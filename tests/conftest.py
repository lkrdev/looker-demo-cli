"""Shared pytest fixtures for the ``looker-demo-cli`` test suite.

This module provides the in-memory fakes and isolation fixtures that let every
CLI command be exercised via :class:`typer.testing.CliRunner` with **zero
network access, zero subprocess spawning, and zero host dependencies**.

Three external boundaries are faked here. They are the same three boundaries
formalized as ``Protocol`` ports in :mod:`looker_demo_cli.ports`:

===================  ==========================================================
Boundary             Fake
===================  ==========================================================
Looker REST API      :class:`FakeLookerApi`   (patches ``get_looker_auth_context``)
BigQuery             :class:`FakeBigQueryHelper` (patches ``BigQueryHelper``)
Shell / subprocess   :class:`FakeShellRunner` (patches ``subprocess.run``)
===================  ==========================================================

The fakes are installed via the :func:`patch_cli` fixture, which replaces a
symbol in *every* command module that holds it. Tests therefore never name the
file a command currently lives in -- the coupling that broke every one of them
when the commands moved out of ``cli.py``. The fake classes are real classes
rather than ``MagicMock`` so that a signature change fails loudly.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# Collection-time credential isolation
# ---------------------------------------------------------------------------
#
# This MUST run at conftest import time, before any `looker_demo_cli` module is
# imported anywhere in the session.
#
# `looker_demo_cli/config.py` freezes module-level constants from the
# environment at *import* time::
#
#     DEFAULT_GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
#
# Those constants are then baked into Typer option defaults, which are
# themselves evaluated at decoration time. By the time a function-scoped
# fixture could call `monkeypatch.delenv`, the value is already captured and
# unchangeable. A developer with `GOOGLE_CLOUD_PROJECT` exported would
# therefore get different CLI defaults than CI, silently.
#
# Stripping here -- at collection, before the first import -- makes the frozen
# constants deterministic for every test in the session.
#
# Still required: `config.py` reads the environment at import time, so the
# stripping must happen before any `looker_demo_cli` import, not in a fixture.
_CREDENTIAL_ENV_VARS = (
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "LOOKERSDK_BASE_URL",
    "LOOKERSDK_CLIENT_ID",
    "LOOKERSDK_CLIENT_SECRET",
    "BIGQUERY_LOCATION",
)

for _var in _CREDENTIAL_ENV_VARS:
    os.environ.pop(_var, None)

# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stable_console_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin Rich's rendering so captured output is deterministic.

    ``looker_demo_cli.utils.console.console`` is a module-level ``Console()``
    singleton constructed at import time. Rich auto-detects terminal width and
    colour support, which makes assertions on captured output flaky across
    machines and CI runners. Forcing a wide, dumb, colourless terminal keeps
    output stable and prevents mid-word line wrapping in assertions.
    """
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run every test in a throwaway working directory.

    Nearly every command calls ``load_flow_state()`` / ``save_flow_state()``,
    which resolve ``.demo-state.json`` relative to :func:`Path.cwd`. Without
    this fixture the test suite would read and clobber the state file of the
    repository it is running in.

    Returns:
        The temporary directory that is now the process working directory.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-strip credential env vars for every test.

    The module-level strip above handles import-time constants. This fixture
    additionally protects against a test (or imported library) re-setting a
    variable mid-session and leaking it into a later test.
    """
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def patched_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect :func:`Path.home` to an empty temporary directory.

    Several commands derive destructive default paths from the real home
    directory -- ``embed scaffold`` defaults ``--target-dir`` to
    ``~/looker-embed-<project>``, and ``data generate`` / ``lookml model``
    default their output under ``~/scratch/demo_create/``. A test that exercises
    those defaults without this fixture writes into the developer's actual home.

    Note this does **not** retroactively affect ``looker_demo_cli.config``,
    whose ``HOME_DIR``-derived constants are frozen at import time; it only
    covers runtime ``Path.home()`` calls.

    Returns:
        The temporary directory now standing in for ``$HOME``. Assert it is
        still empty at the end of a test to prove nothing escaped.
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: fake_home))
    return fake_home


# ---------------------------------------------------------------------------
# Patching command-module symbols
# ---------------------------------------------------------------------------

#: Every module that pulls service functions into its namespace at import time.
#: Order is irrelevant -- ``patch_cli`` patches *all* modules that hold the
#: name, which is what makes it correct when two command groups import the same
#: service.
_COMMAND_MODULES = (
    "looker_demo_cli.cli",
    "looker_demo_cli.context",
    "looker_demo_cli.commands.agent",
    "looker_demo_cli.commands.data",
    "looker_demo_cli.commands.embed",
    "looker_demo_cli.commands.env",
    "looker_demo_cli.commands.ge",
    "looker_demo_cli.commands.lookml",
    "looker_demo_cli.commands.precheck",
    "looker_demo_cli.commands.status",
)


@pytest.fixture
def patch_cli(monkeypatch: pytest.MonkeyPatch):
    """Return a callable that replaces a symbol in every command module holding it.

    Command modules import service functions **by value** at import time, so
    patching the defining module has no effect -- the name the command body
    resolves is the local one.

    Searching :data:`_COMMAND_MODULES` rather than naming a single module is
    deliberate. It keeps tests from encoding *which file* a command currently
    lives in, which is exactly the coupling that broke every one of these tests
    when the commands moved out of ``cli.py``. It also handles the case where
    two groups import the same service, where patching one site silently leaves
    the other live.

    Raises:
        AttributeError: No command module defines the name. This preserves the
            protection of ``monkeypatch.setattr(..., raising=True)``: a typo in
            a symbol name fails loudly instead of silently patching nothing.
    """

    def _patch(name: str, value: Any) -> Any:
        patched = []
        for module_name in _COMMAND_MODULES:
            try:
                module = importlib.import_module(module_name)
            except ModuleNotFoundError:
                continue
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
                patched.append(module_name)
        if not patched:
            raise AttributeError(f"No command module defines {name!r}; checked: {', '.join(_COMMAND_MODULES)}")
        return value

    return _patch


# ---------------------------------------------------------------------------
# CLI invocation
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    """A Typer ``CliRunner`` used to drive commands in-process."""
    return CliRunner()


@pytest.fixture
def invoke(runner: CliRunner):
    """Return a callable that invokes the ``demo-create`` app with ``args``.

    Example:
        >>> result = invoke(["data", "inspect", "--json"])
        >>> assert result.exit_code == 1
    """
    from looker_demo_cli.cli import app

    def _invoke(args: list[str], **kwargs: Any):
        return runner.invoke(app, args, **kwargs)

    return _invoke


# ---------------------------------------------------------------------------
# Fake: Looker REST API
# ---------------------------------------------------------------------------


class FakeLookerApi:
    """In-memory stand-in for the Looker REST API.

    Commands obtain credentials through ``get_looker_auth_context()``, which
    returns an ``(headers, base_url)`` tuple. Installing this fake makes that
    call succeed without touching ``~/.lkr/auth.db`` or the network.

    Attributes:
        base_url: The instance URL reported to the command under test.
        headers: The auth headers reported to the command under test.
        responses: Maps ``(method, url_suffix)`` to a canned JSON payload.
        calls: Ordered log of every request the code under test attempted.
    """

    def __init__(self, base_url: str = "https://fake.cloud.looker.com", authenticated: bool = True):
        self.base_url = base_url if authenticated else ""
        self.headers: dict[str, str] = {"Authorization": "Bearer fake-token"} if authenticated else {}
        self.responses: dict[tuple[str, str], Any] = {}
        self.calls: list[tuple[str, str]] = []

    def auth_context(self, *_args: Any, **_kwargs: Any) -> tuple[dict[str, str], str]:
        """Drop-in replacement for ``get_looker_auth_context``."""
        return self.headers, self.base_url

    def stub(self, method: str, url_suffix: str, payload: Any) -> None:
        """Register a canned response for a request the command will make."""
        self.responses[(method.upper(), url_suffix)] = payload

    def record(self, method: str, url: str) -> Any:
        """Log a request and return its stubbed payload, if any."""
        self.calls.append((method.upper(), url))
        for (stub_method, suffix), payload in self.responses.items():
            if stub_method == method.upper() and url.endswith(suffix):
                return payload
        return None


@pytest.fixture
def fake_looker() -> FakeLookerApi:
    """An authenticated :class:`FakeLookerApi` (not yet installed)."""
    return FakeLookerApi()


@pytest.fixture
def unauthenticated_looker() -> FakeLookerApi:
    """A :class:`FakeLookerApi` reporting no configured instance.

    Drives the ``No Looker instance URL configured`` error path that seven
    separate commands duplicate today.
    """
    return FakeLookerApi(authenticated=False)


@pytest.fixture
def install_looker_auth(monkeypatch: pytest.MonkeyPatch):
    """Return a callable that patches ``get_looker_auth_context`` everywhere.

    The symbol is imported into several modules by value, so patching the
    definition alone is not enough -- each import site must be patched too.

    ``looker_demo_cli.context`` is the important one: it is where every command
    now resolves credentials. ``ge_service`` retains its own import because
    ``ensure_gemini_enterprise_configured`` re-resolves internally.
    """

    def _install(fake: FakeLookerApi) -> FakeLookerApi:
        targets = [
            "looker_demo_cli.context",
            "looker_demo_cli.services.ge_service",
        ]
        patched = 0
        for target in targets:
            module = importlib.import_module(target)
            if hasattr(module, "get_looker_auth_context"):
                monkeypatch.setattr(module, "get_looker_auth_context", fake.auth_context)
                patched += 1
        if not patched:
            raise AttributeError(
                "get_looker_auth_context was not found at any known import site; "
                "the real resolver would run and hit the network."
            )
        return fake

    return _install


# ---------------------------------------------------------------------------
# Fake: BigQuery
# ---------------------------------------------------------------------------


class FakeTableSchemaField:
    """Minimal stand-in for ``google.cloud.bigquery.SchemaField``."""

    def __init__(self, name: str, field_type: str = "STRING", mode: str = "NULLABLE"):
        self.name = name
        self.field_type = field_type
        self.mode = mode


class FakeTable:
    """Minimal stand-in for ``google.cloud.bigquery.Table``."""

    def __init__(self, table_id: str, columns: list[FakeTableSchemaField], num_rows: int = 0):
        self.table_id = table_id
        self.schema = columns
        self.num_rows = num_rows
        self.table_type = "TABLE"


class FakeBigQueryClient:
    """Stand-in for ``bigquery.Client`` exposing only what the CLI touches."""

    def __init__(self, tables: dict[str, FakeTable]):
        self._tables = tables

    def dataset(self, dataset_id: str) -> Any:
        class _DatasetRef:
            def __init__(self, ds_id: str):
                self.dataset_id = ds_id

            def table(self, table_id: str) -> str:
                return table_id

        return _DatasetRef(dataset_id)

    def get_table(self, table_ref: Any) -> FakeTable:
        key = table_ref if isinstance(table_ref, str) else str(table_ref)
        if key not in self._tables:
            raise KeyError(f"Fake table {key!r} not registered")
        return self._tables[key]


class FakeBigQueryHelper:
    """In-memory replacement for :class:`~looker_demo_cli.utils.bigquery_client.BigQueryHelper`.

    The real helper calls ``google.auth.default()`` and constructs a live
    ``bigquery.Client`` in ``__init__``, so it cannot be instantiated at all in
    a hermetic environment. This fake records every mutation so tests can assert
    on *intent* (which datasets/tables the command tried to create) rather than
    on side effects.

    Behavior is shaped through class attributes because the CLI constructs the
    helper itself and tests never get a reference to the instance.
    """

    #: Dataset IDs that exist, mapped to the table IDs they contain.
    datasets: dict[str, list[str]] = {}
    #: Table ID -> :class:`FakeTable`, consulted by ``client.get_table``.
    tables: dict[str, FakeTable] = {}
    #: Row count reported by every ``load_parquet_table`` call.
    rows_per_load: int = 42

    def __init__(self, project_id: str = "fake-project", credentials: Any = None, location: str = "US"):
        self.project_id = project_id
        self.location = location
        self.credentials = credentials
        self.loaded: list[tuple[str, str, Path, int]] = []
        self.created_datasets: list[str] = []
        self.client = FakeBigQueryClient(type(self).tables)

    def dataset_exists(self, dataset_id: str) -> bool:
        return dataset_id in type(self).datasets

    def list_tables(self, dataset_id: str) -> list[str]:
        return list(type(self).datasets.get(dataset_id, []))

    def ensure_dataset(self, dataset_id: str, description: str = "") -> Any:
        if dataset_id not in type(self).datasets:
            type(self).datasets[dataset_id] = []
            self.created_datasets.append(dataset_id)
        return object()

    def load_parquet_table(
        self,
        dataset_id: str,
        table_name: str,
        parquet_file: Path,
        clustering_fields: list[str] | None = None,
        partition_field: str | None = None,
    ) -> int:
        if not Path(parquet_file).exists():
            raise FileNotFoundError(f"Parquet file {parquet_file} does not exist.")
        self.ensure_dataset(dataset_id)
        type(self).datasets[dataset_id].append(table_name)
        rows = type(self).rows_per_load
        self.loaded.append((dataset_id, table_name, Path(parquet_file), rows))
        return rows


#: Modules holding a ``BigQueryHelper`` reference that must be swapped for the
#: fake. ``looker_demo_cli.context`` is the important one: ``AppContext.bigquery``
#: defaults to a factory that resolves ``BigQueryHelper`` from that module's
#: globals, so it is what every command now constructs through.
_BIGQUERY_PATCH_TARGETS = (
    "looker_demo_cli.context",
    "looker_demo_cli.services.schema_service",
)


@pytest.fixture
def fake_bigquery(monkeypatch: pytest.MonkeyPatch):
    """Install :class:`FakeBigQueryHelper` at every import site.

    Yields the fake *class* (not an instance) because the CLI constructs the
    helper itself. Tests shape behavior via the class attributes::

        fake_bigquery.datasets = {"retail": ["orders"]}

    State is reset before and after each test so class-level config cannot leak
    between tests.

    Raises:
        AttributeError: No target module holds a ``BigQueryHelper``. This guard
            replaces an earlier ``raising=False``, which turned a stale target
            into *silence*: the patch would apply nowhere, the real helper would
            be constructed, and it calls ``google.auth.default()`` in its
            constructor. A hermetic test would have reached for real GCP
            credentials and failed with something unrecognisable.
    """
    FakeBigQueryHelper.datasets = {}
    FakeBigQueryHelper.tables = {}
    FakeBigQueryHelper.rows_per_load = 42

    patched = []
    for target in _BIGQUERY_PATCH_TARGETS:
        module = importlib.import_module(target)
        if hasattr(module, "BigQueryHelper"):
            monkeypatch.setattr(module, "BigQueryHelper", FakeBigQueryHelper)
            patched.append(target)
    if not patched:
        raise AttributeError(
            f"BigQueryHelper was not found in any of {_BIGQUERY_PATCH_TARGETS}; "
            "the real client would be constructed and would try to authenticate."
        )

    yield FakeBigQueryHelper

    FakeBigQueryHelper.datasets = {}
    FakeBigQueryHelper.tables = {}


# ---------------------------------------------------------------------------
# Fake: shell / subprocess
# ---------------------------------------------------------------------------


class FakeCompletedProcess:
    """Stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, args: Any, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeShellRunner:
    """Records shell invocations instead of executing them.

    The CLI shells out to ``gcloud``, ``lkr``, and ``uv``. Executing those for
    real makes tests slow, non-hermetic, and capable of mutating the
    developer's actual cloud resources.

    Attributes:
        calls: Ordered log of every command list/string passed to ``run``.
        results: Maps a substring of the command to a canned result.
        default_result: Returned when no ``results`` entry matches.
    """

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.results: dict[str, FakeCompletedProcess] = {}
        self.default_result = FakeCompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def stub(self, contains: str, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        """Register a canned result for any command containing ``contains``."""
        self.results[contains] = FakeCompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)

    def run(self, cmd: Any, *_args: Any, **_kwargs: Any) -> FakeCompletedProcess:
        """Drop-in replacement for ``subprocess.run``."""
        self.calls.append(cmd)
        haystack = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        for needle, result in self.results.items():
            if needle in haystack:
                return result
        return self.default_result

    def assert_called_with_substring(self, needle: str) -> None:
        """Raise ``AssertionError`` unless some recorded call contains ``needle``."""
        joined = [" ".join(c) if isinstance(c, (list, tuple)) else str(c) for c in self.calls]
        assert any(needle in c for c in joined), f"No shell call containing {needle!r}. Calls: {joined}"


@pytest.fixture
def fake_shell(monkeypatch: pytest.MonkeyPatch) -> FakeShellRunner:
    """Install a :class:`FakeShellRunner` over ``subprocess.run``.

    Patched at the ``subprocess`` module level so it covers every call site,
    including those inside helpers the CLI delegates to.
    """
    import subprocess

    fake = FakeShellRunner()
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


# ---------------------------------------------------------------------------
# Filesystem sample data
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_parquet_dir(tmp_path: Path) -> Path:
    """A directory of small, valid Parquet files forming a realistic star schema.

    Exercises the PK/FK inference heuristics: ``dim_users`` and ``dim_products``
    are dimensions with singular-to-plural foreign key relationships, and
    ``fct_orders`` is a fact table referencing both.
    """
    import pandas as pd

    out = tmp_path / "parquet"
    out.mkdir()

    pd.DataFrame({"user_id": [1, 2, 3], "user_name": ["a", "b", "c"]}).to_parquet(out / "dim_users.parquet")
    pd.DataFrame({"product_id": [10, 11], "product_name": ["x", "y"]}).to_parquet(out / "dim_products.parquet")
    pd.DataFrame(
        {
            "order_id": [100, 101, 102],
            "user_id": [1, 2, 3],
            "product_id": [10, 11, 10],
            "amount": [9.99, 19.99, 5.00],
        }
    ).to_parquet(out / "fct_orders.parquet")

    return out


@pytest.fixture
def sample_lookml_dir(tmp_path: Path) -> Path:
    """A minimal but structurally valid LookML project tree.

    Contains one view with a primary key and a low-cardinality dimension, which
    is exactly the shape the optimizer service looks for when deciding where to
    add ``suggestable: no`` and static ``suggestions``.
    """
    root = tmp_path / "lookml"
    (root / "views").mkdir(parents=True)
    (root / "models").mkdir()

    (root / "views" / "users.view.lkml").write_text(
        """view: users {
  sql_table_name: `demo.users` ;;
  dimension: id {
    primary_key: yes
    type: string
    sql: ${TABLE}.id ;;
  }
  dimension: status {
    type: string
    sql: ${TABLE}.status ;;
  }
  measure: count {
    type: count
  }
}
""",
        encoding="utf-8",
    )
    (root / "models" / "demo.model.lkml").write_text(
        """connection: "default_bigquery_connection"
include: "/views/*.view.lkml"

explore: users {}
""",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def state_file(isolated_cwd: Path):
    """Return a callable that writes a ``.demo-state.json`` into the test CWD.

    Many commands read prior pipeline state to fill in omitted options. This
    fixture makes that precondition explicit and greppable in each test.
    """

    def _write(**fields: Any) -> Path:
        from looker_demo_cli.state import STATE_FILE_NAME, FlowState

        state = FlowState(**fields)
        path = isolated_cwd / STATE_FILE_NAME
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        return path

    return _write


# ---------------------------------------------------------------------------
# Host capability detection (for integration-marked tests)
# ---------------------------------------------------------------------------


def _binary_available(name: str) -> bool:
    import shutil

    return shutil.which(name) is not None


requires_uv = pytest.mark.skipif(not _binary_available("uv"), reason="requires the `uv` binary on PATH")
requires_gcloud = pytest.mark.skipif(not _binary_available("gcloud"), reason="requires the `gcloud` binary on PATH")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def package_env() -> dict[str, str]:
    """Environment for subprocess-based integration tests.

    Ensures the package under test is importable in the child process even when
    the suite runs from a directory other than the repo root.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    return env
