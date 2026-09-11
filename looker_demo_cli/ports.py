"""Narrow ``Protocol`` ports for the CLI's three external boundaries.

Every non-hermetic thing this CLI does passes through one of three seams:

===================  =========================================================
Port                 Boundary
===================  =========================================================
:class:`LookerAuth`  Resolving Looker credentials (``lkr`` OAuth or API keys)
:class:`BigQuery`    Reading and writing BigQuery datasets and tables
:class:`Shell`       Spawning subprocesses (``lkr``, ``gcloud``, ``uv``)
===================  =========================================================

Declaring them as :class:`~typing.Protocol` types rather than base classes is
deliberate. The real implementations already exist and already have the right
shapes; a Protocol lets them satisfy the port **without inheriting from
anything**, and lets ``tests/conftest.py`` supply in-memory fakes that were
written before these ports existed. Nothing has to change to opt in.

The ports are intentionally *narrow*: each declares only the members the CLI
actually calls, not everything the underlying object can do. A port that mirrors
its implementation is not an abstraction -- it is a second copy of the
implementation's signature, and it makes the fake as expensive to write as the
real thing.

Why these three and not more
----------------------------
These are the boundaries that make a test slow, flaky, or impossible. Pure
functions (LookML generation, schema inference, the optimizer's AST walk) need
no port: they are already testable by calling them. Adding a port for those
would buy indirection and nothing else.

.. note::
   The ports describe *shape*, not *policy*. Whether a missing credential is a
   hard error or a soft fallback is decided in
   :mod:`looker_demo_cli.context`, not here, so that the same adapter can serve
   a command that requires auth and one that merely prefers it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Looker
# ---------------------------------------------------------------------------


@runtime_checkable
class LookerAuthPort(Protocol):
    """Resolves Looker API credentials.

    Implementations answer one question: *given an optional instance URL and an
    optional saved account alias, what headers and base URL should I use?*

    The port returns a ``(headers, base_url)`` tuple rather than raising on
    failure, because "no credentials" is a legitimate answer for commands that
    degrade gracefully. Turning an empty result into an
    :class:`~looker_demo_cli.errors.AuthError` is the context object's job.
    """

    def __call__(
        self,
        instance_url: str | None = None,
        preferred_account: str | None = None,
    ) -> tuple[dict[str, str], str]:
        """Resolve credentials for a Looker instance.

        Args:
            instance_url: Explicit Looker base URL. When omitted, the
                implementation falls back to the saved ``lkr`` OAuth session.
            preferred_account: Saved ``lkr`` OAuth account alias to prefer when
                several are authenticated.

        Returns:
            A ``(headers, base_url)`` pair. Either or both may be empty when no
            usable credentials were found.
        """
        ...


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------


@runtime_checkable
class BigQueryPort(Protocol):
    """The subset of BigQuery the CLI uses.

    Matches :class:`~looker_demo_cli.utils.bigquery_client.BigQueryHelper` and
    ``tests/conftest.FakeBigQueryHelper``.

    Attributes:
        project_id: GCP project the client is bound to.
        location: Dataset location (e.g. ``"US"``) used when creating datasets.
        client: The underlying vendor client. **Escape hatch, not part of the
            contract** -- it exists because
            :mod:`looker_demo_cli.services.knowledge_service` still reaches for
            the raw client to introspect table schemas. Prefer adding a method
            to this port over reaching through it.
    """

    project_id: str
    location: str
    client: Any

    def dataset_exists(self, dataset_id: str) -> bool:
        """Report whether a dataset exists, without raising if it does not."""
        ...

    def list_tables(self, dataset_id: str) -> list[str]:
        """List table IDs in a dataset, returning ``[]`` if it is unreadable."""
        ...

    def ensure_dataset(self, dataset_id: str, description: str = ...) -> Any:
        """Create the dataset if it is missing and return it."""
        ...

    def load_parquet_table(
        self,
        dataset_id: str,
        table_name: str,
        parquet_file: Path,
        clustering_fields: list[str] | None = None,
        partition_field: str | None = None,
    ) -> int:
        """Load one Parquet file into a table, replacing it.

        Returns:
            The number of rows in the resulting table.
        """
        ...


class BigQueryFactory(Protocol):
    """Constructs a :class:`BigQueryPort`.

    Commands need a *factory* rather than an instance because the target
    project is a per-invocation flag, and the real client authenticates in its
    constructor. Deferring construction keeps a command that never touches
    BigQuery from requiring GCP credentials to run.
    """

    def __call__(
        self,
        project_id: str = ...,
        credentials: Any = None,
        location: str = ...,
    ) -> BigQueryPort:
        """Build a BigQuery client bound to ``project_id``."""
        ...


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------


@runtime_checkable
class CompletedProcessPort(Protocol):
    """The result of a subprocess run.

    Structurally satisfied by :class:`subprocess.CompletedProcess`.
    """

    returncode: int
    stdout: str
    stderr: str


@runtime_checkable
class ShellPort(Protocol):
    """Runs external binaries (``lkr``, ``gcloud``, ``uv``, ``git``).

    Deliberately shaped like :func:`subprocess.run` so the real adapter is that
    function itself, unwrapped.
    """

    def run(self, cmd: Any, *args: Any, **kwargs: Any) -> CompletedProcessPort:
        """Run a command to completion and return its result."""
        ...


class SubprocessShell:
    """The real :class:`ShellPort`, backed by :mod:`subprocess`.

    A thin class rather than passing the :mod:`subprocess` module directly, so
    that the injected object has one method and the fake does not have to
    imitate a module.
    """

    def run(self, cmd: Any, *args: Any, **kwargs: Any) -> CompletedProcessPort:
        """Delegate to :func:`subprocess.run`.

        Defaults are *not* injected here (no implicit ``capture_output`` or
        ``check``): callers vary in what they need, and a silently-applied
        default is the kind of thing that makes a subprocess bug take an hour to
        find.
        """
        import subprocess

        return subprocess.run(cmd, *args, **kwargs)
