"""Per-invocation context: resolved credentials, state, and injected ports.

Before this module existed, every command that talked to Looker opened with the
same five lines::

    set_json_mode(output_json)
    state = load_flow_state()
    headers, base_url = get_looker_auth_context(
        instance_url=instance_url or state.looker_instance_url,
        preferred_account=account or state.looker_account,
    )
    if not base_url or not headers:
        raise looker_not_authenticated()

Seven commands carried a copy. They had drifted: some checked only ``base_url``,
some checked both, and three different error messages were produced for what a
caller experiences as one condition. Worse, the fallback chain
(``flag or state``) was retyped each time, so a command that forgot the
``or state.…`` half silently ignored a value the user had already supplied.

:class:`AppContext` is the single place that resolution happens. Commands
declare what they need and get it, or get a structured error.

Lifecycle
---------
One context per CLI invocation, created lazily on first access and stashed on
``typer.Context.obj``. Laziness is the point: constructing a BigQuery client
authenticates against GCP, so a context built eagerly in the root callback
would make ``demo-create --help`` require credentials.

Test seam
---------
The three ports default to their real adapters but can be replaced wholesale::

    ctx.obj = AppContext(looker_auth=fake.auth_context, bigquery=FakeHelper)

which is what lets a command be driven end to end with no network at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import typer

from looker_demo_cli.errors import StateError, looker_not_authenticated, no_looker_instance
from looker_demo_cli.output import set_json_mode
from looker_demo_cli.ports import (
    BigQueryFactory,
    LookerAuthPort,
    ShellPort,
    SubprocessShell,
)
from looker_demo_cli.services.ge_service import get_looker_auth_context
from looker_demo_cli.state import FlowState, get_default_state_path, load_flow_state, save_flow_state
from looker_demo_cli.utils.bigquery_client import BigQueryHelper


@dataclass(frozen=True)
class LookerAuth:
    """Resolved, non-empty Looker credentials.

    Only ever constructed by :meth:`AppContext.looker_auth`, which raises rather
    than returning an incomplete instance. A command holding one of these does
    not need to null-check it -- that is the whole point of the type.

    Attributes:
        headers: Authorization headers for the Looker REST API.
        base_url: Instance base URL, without a trailing slash.
    """

    headers: dict[str, str]
    base_url: str


@dataclass
class AppContext:
    """Everything a command needs from the outside world.

    Attributes:
        looker_auth_port: Resolves Looker credentials. Defaults to the real
            ``lkr``-backed implementation.
        bigquery: Factory for a BigQuery client. A *factory*, not a client,
            because the target project is a per-command flag and the real
            client authenticates in its constructor.
        shell: Runs external binaries.
        state_path: Explicit ``.demo-state.json`` location. ``None`` means
            "discover it" (cwd, then the scratch directory).
        json_mode: Whether this invocation was asked for machine-readable
            output.
    """

    # NOTE: these are `default_factory` lambdas, not plain defaults, and that is
    # load-bearing. A plain default (`looker_auth_port: … = get_looker_auth_context`)
    # captures the *function object* when the class body executes -- at import
    # time. `monkeypatch.setattr("looker_demo_cli.context.get_looker_auth_context", fake)`
    # would then rebind the module global while every AppContext kept using the
    # original, so a test that installed a fake would silently hit the network.
    # A factory defers the global lookup to instance creation, which happens
    # per-invocation, i.e. after patching.
    looker_auth_port: LookerAuthPort = field(default_factory=lambda: get_looker_auth_context)
    bigquery: BigQueryFactory = field(default_factory=lambda: BigQueryHelper)
    shell: ShellPort = field(default_factory=SubprocessShell)
    state_path: Path | None = None
    json_mode: bool = False

    _state: FlowState | None = field(default=None, repr=False)

    # -- Output mode ------------------------------------------------------

    def set_json_mode(self, enabled: bool) -> None:
        """Record the output mode for this invocation.

        Sets both the context flag and the process-wide flag that the top-level
        error boundary reads. The boundary catches errors raised *before* a
        command body runs, where no context is reachable, so it cannot consult
        this object -- hence the two.

        Args:
            enabled: True when ``--json`` was passed.
        """
        self.json_mode = enabled
        set_json_mode(enabled)

    # -- Flow state -------------------------------------------------------

    def use_state_file(self, path: Path | None) -> None:
        """Point this invocation at an explicit state file.

        Called by every command from its ``--state-file`` option. Passing
        ``None`` leaves the default discovery behaviour alone, so the option is
        genuinely optional rather than needing a sentinel.

        Two agents running gated commands in the same directory otherwise share
        one ``.demo-state.json`` and clobber each other; this is how they get
        separate ones.

        Args:
            path: Explicit state file location, or ``None`` to auto-discover.

        Raises:
            StateError: The state has already been read this invocation.
                Switching files afterwards would mean the command had made
                decisions from one file and then written them to another.
        """
        if path is None:
            return
        if self._state is not None:
            raise StateError(
                "Cannot switch state files after the state has already been read.",
                remediation="Pass --state-file before any option that consults saved state.",
                details={"requested": str(path)},
            )
        self.state_path = path

    @property
    def resolved_state_path(self) -> Path:
        """The state file this invocation reads and writes.

        Resolved through the same discovery rules as :meth:`state`, so a command
        can report the real path even when ``--state-file`` was not passed.
        """
        return self.state_path or get_default_state_path()

    @property
    def state(self) -> FlowState:
        """The persisted flow state, loaded once per invocation.

        Cached because several commands read state, mutate it, and read it
        again; reloading would silently discard the mutation.
        """
        if self._state is None:
            self._state = load_flow_state(path=self.state_path)
        return self._state

    def set_state(self, state: FlowState) -> None:
        """Replace the cached flow state.

        For services that *return* a :class:`FlowState` rather than mutating the
        one they were given. Most mutate in place and returning is a formality,
        but depending on that is depending on an implementation detail: a
        service that switched to ``model_copy`` would silently discard its own
        work at the next :meth:`save_state`.

        Args:
            state: The state to treat as current from here on.
        """
        self._state = state

    def save_state(self) -> Path:
        """Persist the current flow state.

        Returns:
            The path written to, for reporting in the result envelope.
        """
        return save_flow_state(self.state, path=self.state_path)

    # -- Looker -----------------------------------------------------------

    def looker_auth(
        self,
        instance_url: str | None = None,
        account: str | None = None,
    ) -> LookerAuth:
        """Resolve Looker credentials, falling back to saved state.

        Precedence is explicit flag, then recorded state, then whatever the
        ``lkr`` session provides.

        Args:
            instance_url: ``--instance`` value, if the caller supplied one.
            account: ``--looker-account`` value, if the caller supplied one.

        Returns:
            Non-empty credentials.

        Raises:
            AuthError: No instance URL could be determined, or an instance was
                known but no credentials for it were available. These are
                distinguished deliberately: the first means "tell me *which*
                Looker", the second means "log in". Presenting one remediation
                for both sends users down the wrong path roughly half the time.
        """
        state = self.state
        headers, base_url = self.looker_auth_port(
            instance_url=instance_url or state.looker_instance_url,
            preferred_account=account or state.looker_account,
        )
        if not base_url:
            raise no_looker_instance()
        # Specifically the bearer token, not merely a non-empty dict. Only
        # `agent create` checked this before; the other five accepted a header
        # dict carrying no credential and failed later with a bare 401 from
        # Looker, which is far harder to act on.
        if not headers.get("Authorization"):
            raise looker_not_authenticated()
        return LookerAuth(headers=headers, base_url=base_url)


def get_context(ctx: typer.Context | None) -> AppContext:
    """Fetch (or lazily create) the :class:`AppContext` for this invocation.

    Args:
        ctx: The Typer context. ``None`` is tolerated so that command functions
            remain directly callable from Python -- several are, including the
            ``ge publish`` alias -- without fabricating a Click context.

    Returns:
        The context for this invocation, created with real adapters if absent.
    """
    if ctx is None:
        return AppContext()
    if not isinstance(ctx.obj, AppContext):
        ctx.obj = AppContext()
    return ctx.obj
