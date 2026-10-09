"""Shared pytest fixtures and in-memory fakes for the ``looker-demo-cli`` test suite."""

from __future__ import annotations

import importlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

# Strip credential env vars at collection time before `looker_demo_cli.config`
# freezes module-level defaults at import time.
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
# Shared test helpers
# ---------------------------------------------------------------------------


def envelope(result: Any) -> dict[str, Any]:
    """Parse the JSON envelope written to ``result.stdout`` under ``--json``."""
    return json.loads(result.stdout)


def read_state(cwd: Path) -> dict[str, Any]:
    """Read the ``.demo-state.json`` file persisted into ``cwd``."""
    from looker_demo_cli.state import STATE_FILE_NAME

    path = cwd / STATE_FILE_NAME
    assert path.exists(), f"Expected {path} to exist in {cwd}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Environment & state isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_json_mode():
    """Reset the ``--json`` ContextVar before and after each test."""
    from looker_demo_cli.output import set_json_mode

    set_json_mode(False)
    yield
    set_json_mode(False)


@pytest.fixture(autouse=True)
def _stable_console_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin Rich rendering width and disable ANSI color for deterministic output."""
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run every test in an isolated temporary working directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _no_ambient_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-strip credential env vars and isolate gcloud ADC / GCE metadata for every test."""
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "nonexistent-gcloud"))
    monkeypatch.setenv("NO_GCE_CHECK", "true")


@pytest.fixture
def patched_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` to an empty temporary directory."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: fake_home))
    return fake_home


# ---------------------------------------------------------------------------
# Command-module symbol patching & CLI invocation
# ---------------------------------------------------------------------------

_COMMAND_MODULES = (
    "looker_demo_cli.cli",
    "looker_demo_cli.context",
    "looker_demo_cli.commands.agent",
    "looker_demo_cli.commands.catalog",
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
    """Replace a symbol across all command modules that imported it by value."""

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


@pytest.fixture
def runner() -> CliRunner:
    """Return a Typer ``CliRunner``."""
    return CliRunner()


@pytest.fixture
def invoke(runner: CliRunner):
    """Return a callable that invokes the ``demo-create`` app with ``args``."""
    from looker_demo_cli.cli import app

    def _invoke(args: list[str], **kwargs: Any):
        return runner.invoke(app, args, **kwargs)

    return _invoke


# ---------------------------------------------------------------------------
# Fake: Looker REST API
# ---------------------------------------------------------------------------


class FakeLookerApi:
    """In-memory stand-in for the Looker REST API."""

    def __init__(self, base_url: str = "https://fake.cloud.looker.com", authenticated: bool = True):
        self.base_url = base_url if authenticated else ""
        self.headers: dict[str, str] = {"Authorization": "Bearer fake-token"} if authenticated else {}
        self.responses: dict[tuple[str, str], Any] = {}
        self.calls: list[tuple[str, str]] = []

    def auth_context(self, *_args: Any, **_kwargs: Any) -> tuple[dict[str, str], str]:
        """Drop-in replacement for ``get_looker_auth_context``."""
        return self.headers, self.base_url

    def stub(self, method: str, url_suffix: str, payload: Any) -> None:
        """Register a canned response for a request."""
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
    """An authenticated :class:`FakeLookerApi`."""
    return FakeLookerApi()


@pytest.fixture
def unauthenticated_looker() -> FakeLookerApi:
    """A :class:`FakeLookerApi` reporting no configured instance."""
    return FakeLookerApi(authenticated=False)


@pytest.fixture
def install_looker_auth(monkeypatch: pytest.MonkeyPatch):
    """Patch ``get_looker_auth_context`` across all import sites."""

    def _install(fake: FakeLookerApi) -> FakeLookerApi:
        targets = (
            "looker_demo_cli.context",
            "looker_demo_cli.services.ge_service",
        )
        patched = 0
        for target in targets:
            module = importlib.import_module(target)
            if hasattr(module, "get_looker_auth_context"):
                monkeypatch.setattr(module, "get_looker_auth_context", fake.auth_context)
                patched += 1
        if not patched:
            raise AttributeError("get_looker_auth_context was not found at any known import site.")
        return fake

    return _install


# ---------------------------------------------------------------------------
# Fake: BigQuery
# ---------------------------------------------------------------------------


class FakeTableSchemaField:
    """Minimal stand-in for ``google.cloud.bigquery.SchemaField``."""

    def __init__(
        self,
        name: str,
        field_type: str = "STRING",
        mode: str = "NULLABLE",
        description: str | None = None,
    ):
        self.name = name
        self.field_type = field_type
        self.mode = mode
        self.description = description


class FakeTable:
    """Minimal stand-in for ``google.cloud.bigquery.Table``."""

    def __init__(
        self,
        table_id: str,
        columns: list[FakeTableSchemaField] | None = None,
        num_rows: int = 0,
        description: str | None = None,
        labels: dict[str, str] | None = None,
        table_constraints: Any = None,
    ):
        self.table_id = table_id
        self.schema = columns or []
        self.num_rows = num_rows
        self.table_type = "TABLE"
        self.description = description
        self.labels = labels or {}
        self.table_constraints = table_constraints
        self.time_partitioning = None
        self.range_partitioning = None
        self.clustering_fields: list[str] | None = None


class FakeBigQueryClient:
    """Stand-in for ``bigquery.Client``."""

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
    """In-memory replacement for ``BigQueryHelper``."""

    datasets: dict[str, list[str]] = {}
    tables: dict[str, FakeTable] = {}
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

    def get_dataset_location(self, dataset_id: str) -> str:
        return self.location

    def get_table_metadata(self, dataset_id: str, table_id: str) -> Any:
        key = f"{dataset_id}.{table_id}"
        if key in type(self).tables:
            return type(self).tables[key]
        if table_id in type(self).tables:
            return type(self).tables[table_id]
        return FakeTable(table_id=table_id, columns=[])

    def preview_rows(self, dataset_id: str, table_id: str, limit: int = 5) -> list[dict[str, Any]]:
        return [{"row_num": i} for i in range(min(limit, 5))]

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

    def load_parquet_table_optimized(
        self,
        dataset_id: str,
        table_name: str,
        parquet_file: Path,
        df_sample: Any = None,
        partition_field: str | None = None,
        clustering_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        from looker_demo_cli.utils.bigquery_client import BigQueryOptimizationAdvisor

        source = df_sample if df_sample is not None else Path(parquet_file)
        part_col = partition_field or BigQueryOptimizationAdvisor.infer_partition_field(table_name, source)
        cluster_cols = clustering_fields or BigQueryOptimizationAdvisor.infer_cluster_fields(
            table_name, source, part_col
        )
        rows = self.load_parquet_table(
            dataset_id,
            table_name,
            parquet_file,
            clustering_fields=cluster_cols,
            partition_field=part_col,
        )
        return {
            "table_name": table_name,
            "rows": rows,
            "partition_field": part_col,
            "clustering_fields": cluster_cols,
        }


_BIGQUERY_PATCH_TARGETS = (
    "looker_demo_cli.context",
    "looker_demo_cli.services.schema_service",
)


@pytest.fixture
def fake_bigquery(monkeypatch: pytest.MonkeyPatch):
    """Install :class:`FakeBigQueryHelper` at every import site."""
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
        raise AttributeError(f"BigQueryHelper was not found in any of {_BIGQUERY_PATCH_TARGETS}.")

    yield FakeBigQueryHelper

    FakeBigQueryHelper.datasets = {}
    FakeBigQueryHelper.tables = {}


# ---------------------------------------------------------------------------
# Fake: Dataplex / Knowledge Catalog
# ---------------------------------------------------------------------------


class FakeCatalogClient:
    """In-memory stand-in for DataplexCatalogClient satisfying CatalogPort."""

    entries: dict[str, dict[str, Any]] = {}
    entry_links: dict[str, list[dict[str, Any]]] = {}
    glossary_terms: dict[str, dict[str, Any]] = {}
    context_joins: dict[str, Any] = {}

    def __init__(self, project_id: str = "fake-project", location: str = "us", credentials: Any = None):
        self.project_id = project_id
        self.location = location
        self.credentials = credentials

    def lookup_entry(self, dataset_id: str, table_id: str, view: str = "ALL") -> dict[str, Any]:
        key = f"{dataset_id}.{table_id}"
        return type(self).entries.get(key, {})

    def lookup_entry_links(
        self,
        entry_name: str,
        entry_link_type: str | None = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        return list(type(self).entry_links.get(entry_name, []))

    def get_glossary_term(self, glossary_id: str, term_id: str) -> dict[str, Any]:
        key = f"{glossary_id}/{term_id}"
        return type(self).glossary_terms.get(key, {})

    def lookup_context(self, resources: list[str], format: str = "JSON") -> dict[str, Any]:
        return {"joins": dict(type(self).context_joins)}


_CATALOG_PATCH_TARGETS = (
    "looker_demo_cli.context",
    "looker_demo_cli.services.catalog_service",
)


@pytest.fixture
def fake_catalog(monkeypatch: pytest.MonkeyPatch):
    """Install :class:`FakeCatalogClient` at import sites."""
    FakeCatalogClient.entries = {}
    FakeCatalogClient.entry_links = {}
    FakeCatalogClient.glossary_terms = {}
    FakeCatalogClient.context_joins = {}

    for target in _CATALOG_PATCH_TARGETS:
        try:
            module = importlib.import_module(target)
            if hasattr(module, "DataplexCatalogClient"):
                monkeypatch.setattr(module, "DataplexCatalogClient", FakeCatalogClient)
        except ModuleNotFoundError:
            pass

    yield FakeCatalogClient

    FakeCatalogClient.entries = {}
    FakeCatalogClient.entry_links = {}
    FakeCatalogClient.glossary_terms = {}
    FakeCatalogClient.context_joins = {}


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
    """Records shell invocations instead of executing them."""

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
        haystack = " ".join(cmd) if isinstance(cmd, list | tuple) else str(cmd)
        for needle, result in self.results.items():
            if needle in haystack:
                return result
        return self.default_result

    def assert_called_with_substring(self, needle: str) -> None:
        """Raise ``AssertionError`` unless some recorded call contains ``needle``."""
        joined = [" ".join(c) if isinstance(c, list | tuple) else str(c) for c in self.calls]
        assert any(needle in c for c in joined), f"No shell call containing {needle!r}. Calls: {joined}"


@pytest.fixture
def fake_shell(monkeypatch: pytest.MonkeyPatch) -> FakeShellRunner:
    """Install a :class:`FakeShellRunner` over ``subprocess.run``."""
    import subprocess

    fake = FakeShellRunner()
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


# ---------------------------------------------------------------------------
# Filesystem sample data & state fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_parquet_dir(tmp_path: Path) -> Path:
    """A directory of small Parquet files forming a star schema."""
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
    """A minimal but structurally valid LookML project tree."""
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
    """Write a ``.demo-state.json`` into the isolated test working directory."""

    def _write(**fields: Any) -> Path:
        from looker_demo_cli.state import STATE_FILE_NAME, FlowState

        state = FlowState(**fields)
        path = isolated_cwd / STATE_FILE_NAME
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        return path

    return _write


# ---------------------------------------------------------------------------
# Host capability detection (for integration tests)
# ---------------------------------------------------------------------------

requires_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="requires the `uv` binary on PATH")
requires_gcloud = pytest.mark.skipif(shutil.which("gcloud") is None, reason="requires the `gcloud` binary on PATH")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def package_env() -> dict[str, str]:
    """Environment for subprocess-based integration tests."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    return env
