"""Contract tests for the ``ge``, ``agent``, and ``embed`` command groups.

This file began as pure characterization -- pinning **current observable
behavior** of the most network-dependent commands so that Phase 3 refactors
(collapsing the duplicated auth preamble into a shared context object,
de-aliasing ``ge publish``, and extracting service ports) can be proven
non-breaking.

Phase 2 converted the majority of it. Most tests now assert the *intended*
contract: the ``CommandResult`` envelope on stdout, Rich narration on stderr,
and the dedicated exit code of each ``DemoCreateError`` subclass. Tests still
pinning behavior known to be wrong are marked ``@pytest.mark.characterization``
and annotated with a ``# BUG:`` comment naming the phase that owns the fix.

Hermeticity
-----------
Every command under test reaches for the Looker REST API. Two seams are used to
keep the suite offline:

1. ``install_looker_auth(...)`` (conftest) replaces ``get_looker_auth_context``
   at both import sites, supplying the ``(headers, base_url)`` tuple.
2. ``patch_cli(...)`` (local) replaces the *service functions* that ``cli.py``
   imported by value -- ``get_looker_ge_config``,
   ``ensure_gemini_enterprise_configured``, ``provision_ca_agent``,
   ``register_and_link_golden_queries``,
   ``extract_golden_queries_from_dashboard_id``,
   ``extract_golden_queries_from_dashboards``, ``publish_agent_to_ge``, and
   ``EmbedScaffolder``.

The service-function seam is preferred over intercepting HTTP with ``responses``
because these commands make multi-request, order-dependent call sequences whose
exact wire shape is *not* the behavior under test here -- the CLI's branching,
exit codes, and JSON contract are.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from looker_demo_cli.errors import (
    AuthError,
    ConfigError,
    RemoteApiError,
    looker_not_authenticated,
    missing_option,
    no_looker_instance,
)
from looker_demo_cli.output import ENVELOPE_SCHEMA_VERSION
from looker_demo_cli.state import STATE_FILE_NAME, FlowState

# ---------------------------------------------------------------------------
# Local fixtures and helpers
#
# NOTE: none of these are in tests/conftest.py today. See the report at the end
# of this module's docstring block for which ones arguably belong there.
# ---------------------------------------------------------------------------


# `patch_cli` is provided by tests/conftest.py. It searches every command
# module for the symbol, so it keeps working as commands move between files.


@pytest.fixture
def patched_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect :meth:`Path.home` to a throwaway directory.

    ``embed scaffold`` defaults ``--target-dir`` to ``Path.home() / ...``. This
    fixture guarantees that even a mistake in the test cannot touch the real
    home directory.
    """
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def fake_scaffolder(patch_cli):
    """Replace ``EmbedScaffolder`` with a recorder that writes nothing.

    The real scaffolder clones a GitHub repository and copies a template tree,
    which is neither hermetic nor fast.
    """

    class _FakeScaffolder:
        def __init__(self) -> None:
            self.captured: list[Any] = []

        def scaffold_demo_workspace(self, opts: Any) -> Path:
            self.captured.append(opts)
            return opts.target_dir

    fake = _FakeScaffolder()
    patch_cli("EmbedScaffolder", fake)
    return fake


class Recorder:
    """Callable that records every invocation and returns a canned value."""

    def __init__(self, return_value: Any = None) -> None:
        self.return_value = return_value
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return self.return_value

    @property
    def called(self) -> bool:
        return bool(self.calls)


def make_ensure_ge(*, configured: bool = True, instance_id: str = "ge-app-123") -> Any:
    """Build a stand-in for ``ensure_gemini_enterprise_configured``.

    The real function performs a GET, renders a table, may prompt, PATCHes
    Looker, and shells out to ``gcloud``. It returns the (mutated) FlowState.
    """
    calls: list[dict[str, Any]] = []

    def _ensure(state: FlowState, headers: dict, interactive: bool = True, allow_reconfigure: bool = True):
        calls.append(
            {
                "state": state,
                # The command hands over a live object that this stub then
                # mutates, so keep a pre-mutation copy for assertions.
                "state_in": state.model_copy(deep=True),
                "headers": headers,
                "interactive": interactive,
                "allow_reconfigure": allow_reconfigure,
            }
        )
        state.ge_configured = configured
        state.ge_instance_id = instance_id if configured else None
        state.ge_project_id = "ge-project" if configured else None
        return state

    _ensure.calls = calls  # type: ignore[attr-defined]
    return _ensure


def ge_config(**overrides: Any) -> dict[str, Any]:
    """A fully configured ``/api/4.0/gemini_enablement`` payload."""
    cfg = {
        "ai_ge_publish_enabled": True,
        "ai_ge_project_id": "ge-project",
        "ai_ge_location": "global",
        "ai_ge_instance_id": "ge-app-123",
        "ai_ge_service_account_email": "looker-sa@example.iam.gserviceaccount.com",
        "ai_ca_enabled": True,
    }
    cfg.update(overrides)
    return cfg


def read_state(cwd: Path) -> dict[str, Any]:
    """Parse the ``.demo-state.json`` the command under test just wrote."""
    return json.loads((cwd / STATE_FILE_NAME).read_text(encoding="utf-8"))


def envelope(result: Any) -> dict[str, Any]:
    """Parse the JSON envelope a ``--json`` invocation wrote to stdout.

    Deliberately parses ``result.stdout`` rather than ``result.output``: the
    Phase 2 contract is that stdout carries the envelope *and nothing else*,
    so a bare ``json.loads`` succeeding is itself the assertion that no Rich
    output leaked onto the machine-readable stream.
    """
    return json.loads(result.stdout)


# ===========================================================================
# ge status
# ===========================================================================


@pytest.mark.characterization
@pytest.mark.unit
def test_ge_status_happy_path_fully_configured(invoke, fake_looker, install_looker_auth, patch_cli):
    """A fully configured instance renders the table and reports success."""
    install_looker_auth(fake_looker)
    patch_cli("get_looker_ge_config", Recorder(ge_config()))

    result = invoke(["ge", "status"])

    assert result.exit_code == 0
    assert "Looker Gemini Enterprise Configuration" in result.output
    assert "Gemini Enterprise is fully configured in Looker." in result.output


@pytest.mark.characterization
@pytest.mark.unit
def test_ge_status_partial_config_warns_but_exits_zero(invoke, fake_looker, install_looker_auth, patch_cli):
    """A partially configured instance is a *warning*, not an error: exit 0."""
    install_looker_auth(fake_looker)
    patch_cli("get_looker_ge_config", Recorder(ge_config(ai_ge_instance_id="")))

    result = invoke(["ge", "status"])

    # A non-empty-but-unconfigured response still exits 0.
    assert result.exit_code == 0
    assert "NOT fully configured" in result.output


@pytest.mark.unit
def test_ge_status_json_wraps_config_in_the_standard_envelope(invoke, fake_looker, install_looker_auth, patch_cli):
    """``--json`` emits the envelope on stdout, with the raw config under ``data``."""
    install_looker_auth(fake_looker)
    config = ge_config()
    patch_cli("get_looker_ge_config", Recorder(config))

    result = invoke(["ge", "status", "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["status"] == "SUCCESS"
    assert payload["command"] == "ge status"
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["data"]["configured"] is True
    # The upstream payload is passed through untouched, so callers that were
    # reading the raw config only need to reach one level deeper.
    assert payload["data"]["config"] == config
    # The Rich table is suppressed entirely in JSON mode.
    assert "Looker Gemini Enterprise Configuration" not in result.output


@pytest.mark.unit
def test_ge_status_json_unconfigured_suggests_the_next_command(invoke, fake_looker, install_looker_auth, patch_cli):
    """An unconfigured instance still exits 0 but tells the orchestrator what to run."""
    install_looker_auth(fake_looker)
    patch_cli("get_looker_ge_config", Recorder(ge_config(ai_ge_instance_id="")))

    result = invoke(["ge", "status", "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["data"]["configured"] is False
    assert payload["next_actions"][0]["command"].startswith("demo-create ge configure")
    assert payload["next_actions"][0]["gate"] == 5


@pytest.mark.unit
def test_ge_status_empty_config_is_a_remote_api_error(invoke, fake_looker, install_looker_auth, patch_cli):
    """An empty dict from Looker is a remote failure: exit 5, not a bare exit 1."""
    install_looker_auth(fake_looker)
    patch_cli("get_looker_ge_config", Recorder({}))

    result = invoke(["ge", "status"])

    assert result.exit_code == RemoteApiError.exit_code
    assert "Failed to retrieve Gemini enablement configuration from Looker." in result.output


@pytest.mark.unit
def test_ge_status_empty_config_json_uses_one_spelling(invoke, fake_looker, install_looker_auth, patch_cli):
    """The JSON and human messages are now the same string, from one source."""
    install_looker_auth(fake_looker)
    patch_cli("get_looker_ge_config", Recorder({}))

    result = invoke(["ge", "status", "--json"])

    assert result.exit_code == RemoteApiError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    assert payload["errors"][0]["code"] == "REMOTE_API_ERROR"
    assert payload["errors"][0]["message"] == "Failed to retrieve Gemini enablement configuration from Looker."
    assert payload["errors"][0]["remediation"]


@pytest.mark.unit
def test_ge_status_unauthenticated_exits_with_the_auth_code(
    invoke, unauthenticated_looker, install_looker_auth, patch_cli
):
    """The shared auth guard: no base URL -> AuthError's dedicated exit code."""
    install_looker_auth(unauthenticated_looker)
    ge_cfg = patch_cli("get_looker_ge_config", Recorder(ge_config()))

    result = invoke(["ge", "status"])

    assert result.exit_code == AuthError.exit_code
    assert "No Looker instance URL configured" in result.output
    # The auth guard short-circuits before any API call is attempted.
    assert not ge_cfg.called


@pytest.mark.unit
def test_ge_status_unauthenticated_json_shape(invoke, unauthenticated_looker, install_looker_auth, patch_cli):
    """The canonical auth envelope, which every Looker-backed command now shares."""
    install_looker_auth(unauthenticated_looker)
    patch_cli("get_looker_ge_config", Recorder(ge_config()))

    result = invoke(["ge", "status", "--json"])

    assert result.exit_code == AuthError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    assert payload["errors"] == [
        {
            "code": "AUTH_ERROR",
            "message": no_looker_instance().message,
            "remediation": no_looker_instance().remediation,
            "details": {},
        }
    ]


@pytest.mark.characterization
@pytest.mark.unit
def test_ge_status_never_writes_state_file(invoke, fake_looker, install_looker_auth, patch_cli, isolated_cwd):
    """``ge status`` is read-only: it must not create ``.demo-state.json``."""
    install_looker_auth(fake_looker)
    patch_cli("get_looker_ge_config", Recorder(ge_config()))

    result = invoke(["ge", "status"])

    assert result.exit_code == 0
    assert not (isolated_cwd / STATE_FILE_NAME).exists()


# ===========================================================================
# ge configure
# ===========================================================================


@pytest.mark.unit
def test_ge_configure_happy_path(invoke, fake_looker, install_looker_auth, patch_cli):
    """A configured result prints the app/project success line and exits 0."""
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))

    result = invoke(["ge", "configure", "--gcp-project", "my-proj", "--app-id", "app-9"])

    assert result.exit_code == 0
    assert "Gemini Enterprise configured" in result.output
    assert "ge-app-123" in result.output
    assert "my-proj" in result.output


@pytest.mark.characterization
@pytest.mark.unit
def test_ge_configure_forwards_options_into_flow_state(invoke, fake_looker, install_looker_auth, patch_cli):
    """CLI options are marshalled into a *fresh* FlowState, never a loaded one."""
    install_looker_auth(fake_looker)
    ensure = patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))

    result = invoke(
        [
            "ge",
            "configure",
            "--gcp-project",
            "proj-a",
            "--app-id",
            "app-a",
            "--location",
            "us-central1",
            "--looker-account",
            "acct-a",
        ]
    )

    assert result.exit_code == 0
    call = ensure.calls[0]  # type: ignore[attr-defined]
    state: FlowState = call["state_in"]
    assert state.gcp_project_id == "proj-a"
    assert state.ge_instance_id == "app-a"
    assert state.ge_location == "us-central1"
    assert state.looker_account == "acct-a"
    assert state.looker_instance_url == fake_looker.base_url
    assert call["interactive"] is True
    assert call["allow_reconfigure"] is True


@pytest.mark.unit
def test_ge_configure_json_implies_non_interactive(invoke, fake_looker, install_looker_auth, patch_cli):
    """A machine-readable caller can never answer a prompt, so never raise one."""
    install_looker_auth(fake_looker)
    ensure = patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))

    result = invoke(["ge", "configure", "--gcp-project", "proj-a", "--json"])

    assert result.exit_code == 0
    assert ensure.calls[0]["interactive"] is False  # type: ignore[attr-defined]


@pytest.mark.characterization
@pytest.mark.unit
def test_ge_configure_discards_state_instead_of_saving(
    invoke, fake_looker, install_looker_auth, patch_cli, isolated_cwd
):
    """``ge configure`` throws away everything it learned.

    BUG: `ge configure` builds a FlowState, has it populated with
    ge_configured/ge_instance_id/ge_project_id/ge_service_account_email, and
    then drops it on the floor: save_flow_state() is never called. A subsequent
    `agent publish` therefore re-discovers the GE config from scratch. Every
    other mutating command in this group persists state. Fixed in Phase 5,
    which owns state handling.
    """
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))

    result = invoke(["ge", "configure", "--gcp-project", "proj-a"])

    assert result.exit_code == 0
    assert not (isolated_cwd / STATE_FILE_NAME).exists()


@pytest.mark.characterization
@pytest.mark.unit
def test_ge_configure_ignores_existing_state_file(invoke, fake_looker, install_looker_auth, patch_cli, state_file):
    """A pre-existing ``.demo-state.json`` is not loaded by ``ge configure``.

    BUG: unlike `agent create` / `agent publish` / `embed scaffold`, this
    command constructs FlowState() directly instead of calling
    load_flow_state(), so prior pipeline context (ca_agent_id, dataset, etc.)
    is silently invisible to it. Fixed in Phase 5.
    """
    install_looker_auth(fake_looker)
    state_file(gcp_project_id="from-disk", bq_dataset_id="from_disk_ds", ca_agent_id="777")
    ensure = patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))

    result = invoke(["ge", "configure", "--gcp-project", "from-flag"])

    assert result.exit_code == 0
    state: FlowState = ensure.calls[0]["state_in"]  # type: ignore[attr-defined]
    assert state.gcp_project_id == "from-flag"
    assert state.ca_agent_id is None
    assert state.bq_dataset_id == FlowState().bq_dataset_id


@pytest.mark.unit
def test_ge_configure_failure_is_a_remote_api_error(invoke, fake_looker, install_looker_auth, patch_cli):
    """An unconfigured result after the service call is a hard failure."""
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=False))

    result = invoke(["ge", "configure", "--gcp-project", "proj-a"])

    assert result.exit_code == RemoteApiError.exit_code
    assert "Failed to complete Gemini Enterprise configuration." in result.output


@pytest.mark.unit
def test_ge_configure_unauthenticated_exits_with_the_auth_code(
    invoke, unauthenticated_looker, install_looker_auth, patch_cli
):
    """The shared auth guard, ``ge configure`` copy -- now literally shared."""
    install_looker_auth(unauthenticated_looker)
    ensure = patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))

    result = invoke(["ge", "configure"])

    assert result.exit_code == AuthError.exit_code
    assert "No Looker instance URL configured" in result.output
    assert not ensure.calls  # type: ignore[attr-defined]


@pytest.mark.unit
def test_ge_configure_json_emits_the_envelope(invoke, fake_looker, install_looker_auth, patch_cli):
    """``ge configure`` gained ``--json`` in Phase 2; it previously had none."""
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))

    result = invoke(["ge", "configure", "--gcp-project", "proj-a", "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["command"] == "ge configure"
    assert payload["status"] == "SUCCESS"
    assert payload["data"] == {
        "gcp_project": "proj-a",
        "app_id": "ge-app-123",
        "location": "global",
        "instance_url": fake_looker.base_url,
        "configured": True,
    }


# ===========================================================================
# ge publish  <->  agent publish  (aliasing)
# ===========================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="human-output"),
        pytest.param(["--json"], id="json-output"),
    ],
)
def test_ge_publish_is_a_pure_alias_of_agent_publish(
    invoke, fake_looker, install_looker_auth, patch_cli, state_file, isolated_cwd, extra_args
):
    """``ge publish`` and ``agent publish`` behave identically, bar their name.

    ``ge_publish`` used to be implemented by *directly calling the Python
    function* ``agent_publish(...)``. That worked only because both commands
    happened to declare the same five options in the same order; adding an
    option to one would have silently diverged them. Phase 3 replaced the call
    with a shared :func:`~looker_demo_cli.commands.agent.publish_agent`
    implementation that both wrap.

    The one field that now legitimately differs is ``command``. Previously
    ``ge publish`` reported ``"agent publish"`` on success while its *failure*
    envelope -- produced by the error boundary from the real invocation path --
    said ``"ge publish"``. One command with two identities depending on how it
    failed is precisely the inconsistency Phase 2 fixed for nested groups, so
    the label is now parameterised and each spelling reports itself.

    Everything else is compared byte for byte.
    """
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    patch_cli("publish_agent_to_ge", Recorder(True))

    outputs = []
    exit_codes = []
    for command in (["ge", "publish"], ["agent", "publish"]):
        # Reset state between the two runs: publishing persists state, and a
        # stale file would make the second invocation non-comparable.
        state_file(ca_agent_id="1042", looker_instance_url=fake_looker.base_url)
        result = invoke([*command, *extra_args])
        # Normalise only the label, so any *other* divergence still fails.
        outputs.append(result.output.replace("ge publish", "<publish>").replace("agent publish", "<publish>"))
        exit_codes.append(result.exit_code)

    assert exit_codes == [0, 0]
    assert outputs[0] == outputs[1]


@pytest.mark.characterization
@pytest.mark.unit
def test_ge_publish_alias_matches_on_error_paths(invoke, fake_looker, install_looker_auth, patch_cli, isolated_cwd):
    """The alias reproduces the missing-agent-id failure identically.

    Everything an orchestrator branches on -- exit code, ``status``, and the
    structured ``errors`` list -- is the same through either spelling, which is
    what Phase 3 must preserve when the direct call is replaced by a shared
    helper.

    The one field that legitimately differs is ``command``: the exception
    boundary lives on the top-level group, so it can only name the group that
    was invoked ("ge" vs "agent"), not the full subcommand path. It is
    excluded from the comparison rather than asserted, since it is a label
    rather than part of the contract.
    """
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    patch_cli("publish_agent_to_ge", Recorder(True))

    ge_result = invoke(["ge", "publish", "--json"])
    agent_result = invoke(["agent", "publish", "--json"])

    assert ge_result.exit_code == agent_result.exit_code == ConfigError.exit_code
    ge_payload = envelope(ge_result)
    agent_payload = envelope(agent_result)
    assert ge_payload["status"] == agent_payload["status"] == "FAILED"
    assert ge_payload["errors"] == agent_payload["errors"]
    assert {k: v for k, v in ge_payload.items() if k != "command"} == {
        k: v for k, v in agent_payload.items() if k != "command"
    }


@pytest.mark.characterization
@pytest.mark.unit
def test_ge_publish_forwards_agent_id_option(invoke, fake_looker, install_looker_auth, patch_cli):
    """``ge publish --agent-id`` reaches ``publish_agent_to_ge`` unchanged."""
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    publish = patch_cli("publish_agent_to_ge", Recorder(True))

    result = invoke(["ge", "publish", "--agent-id", "9001"])

    assert result.exit_code == 0
    args, _ = publish.calls[0]
    assert args == (fake_looker.base_url, "9001", fake_looker.headers)


# ===========================================================================
# agent publish
# ===========================================================================


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_publish_happy_path(invoke, fake_looker, install_looker_auth, patch_cli, state_file, isolated_cwd):
    """Successful publish reports the GE app and persists ``published_to_ge``."""
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    patch_cli("publish_agent_to_ge", Recorder(True))
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "publish"])

    assert result.exit_code == 0
    assert "published to Gemini Enterprise app" in result.output
    saved = read_state(isolated_cwd)
    assert saved["published_to_ge"] is True
    assert saved["ge_configured"] is True
    assert saved["ge_instance_id"] == "ge-app-123"


@pytest.mark.unit
def test_agent_publish_json_emits_the_standard_envelope(
    invoke, fake_looker, install_looker_auth, patch_cli, state_file, isolated_cwd
):
    """The bespoke five-key report is gone; the publish facts moved under ``data``.

    Phase 1 had this command emitting ``{status, agent_id, ge_configured, ...}``
    at the top level while ``ge status`` emitted a bare ``{error: ...}``. A
    caller needed one parser per command. Everything now lives where the rest
    of the CLI puts it, and ``status`` means the same thing everywhere.
    """
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    patch_cli("publish_agent_to_ge", Recorder(True))
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "publish", "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["command"] == "agent publish"
    assert payload["status"] == "SUCCESS"
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["errors"] == []
    assert payload["data"] == {
        "agent_id": "1042",
        "ge_configured": True,
        "ge_instance_id": "ge-app-123",
        "published_to_ge": True,
        "state_file": str(isolated_cwd / STATE_FILE_NAME),
    }


@pytest.mark.unit
def test_agent_publish_json_keeps_rich_output_off_stdout(
    invoke, fake_looker, install_looker_auth, patch_cli, state_file
):
    """Human narration is suppressed in JSON mode, so stdout parses cleanly."""
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    patch_cli("publish_agent_to_ge", Recorder(True))
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "publish", "--json"])

    assert result.exit_code == 0
    assert "Updated state saved to" not in result.output
    assert "Updated state saved to" not in result.stdout


@pytest.mark.unit
def test_agent_publish_failure_reports_the_error_and_its_remediation(
    invoke, fake_looker, install_looker_auth, patch_cli, state_file
):
    """A human failure must end with the next step, not just the bad news.

    The old failure branch printed an error and exited, while the "Updated
    state saved to" notice sat after the ``if/else`` as dead code. Advice that
    used to be glued into the error string is now a separate ``remediation``
    field, which means it is both printed for a human *and* readable by a
    machine from the same source.
    """
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    patch_cli("publish_agent_to_ge", Recorder(False))
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "publish"])

    assert result.exit_code == RemoteApiError.exit_code
    assert "Failed to publish CA Agent `1042` to Gemini Enterprise." in result.output
    assert "roles/discoveryengine.admin" in result.output
    assert "Updated state saved to" not in result.output


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_publish_failure_still_persists_state(
    invoke, fake_looker, install_looker_auth, patch_cli, state_file, isolated_cwd
):
    """A failed publish is recorded to disk before the non-zero exit.

    Pinned rather than asserted as a contract: writing ``published_to_ge:
    false`` on the way out is arguably right (a later ``demo-create status``
    can see the attempt happened), but nothing yet declares that ordering, so
    Phase 5's state-handling work should be free to revisit it deliberately.
    """
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    patch_cli("publish_agent_to_ge", Recorder(False))
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "publish"])

    assert result.exit_code == RemoteApiError.exit_code
    assert read_state(isolated_cwd)["published_to_ge"] is False


@pytest.mark.unit
def test_agent_publish_failure_json_carries_the_ge_diagnosis(
    invoke, fake_looker, install_looker_auth, patch_cli, state_file
):
    """The failure details say *why* GE refused, not merely that it did.

    ``ge_configured`` and ``ge_instance_id`` are the two facts that decide
    whether the fix is `ge configure` or an IAM grant, so they travel with the
    error instead of requiring a follow-up ``ge status`` call.
    """
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    patch_cli("publish_agent_to_ge", Recorder(False))
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "publish", "--json"])

    assert result.exit_code == RemoteApiError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    error = payload["errors"][0]
    assert error["code"] == "REMOTE_API_ERROR"
    assert error["message"] == "Failed to publish CA Agent `1042` to Gemini Enterprise."
    assert error["details"] == {
        "agent_id": "1042",
        "ge_configured": True,
        "ge_instance_id": "ge-app-123",
    }
    assert error["remediation"]


@pytest.mark.unit
def test_agent_publish_without_agent_id_is_a_config_error(invoke, fake_looker, install_looker_auth, patch_cli):
    """Neither ``--agent-id`` nor state -> exit 4, before auth is touched.

    Exit 4 (bad configuration) rather than the old catch-all exit 1: the
    caller supplied nothing to publish, which is repairable by passing an
    argument and is categorically different from a credentials or API failure.
    """
    install_looker_auth(fake_looker)
    ensure = patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    publish = patch_cli("publish_agent_to_ge", Recorder(True))

    result = invoke(["agent", "publish"])

    assert result.exit_code == ConfigError.exit_code
    assert "--agent-id" in result.output
    assert "demo-create agent create" in result.output
    assert not ensure.calls  # type: ignore[attr-defined]
    assert not publish.called


@pytest.mark.unit
def test_agent_publish_without_agent_id_json_shape(invoke, fake_looker, install_looker_auth, patch_cli):
    """One error envelope for the whole CLI, produced by one factory.

    The two incompatible JSON error shapes in this group (``{error}`` versus
    ``{status, error}``) are both gone. Asserting against ``missing_option``
    keeps this command's wording welded to its siblings'.
    """
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    patch_cli("publish_agent_to_ge", Recorder(True))
    expected = missing_option(
        "--agent-id",
        purpose="the CA agent to publish",
        hint="Or run `demo-create agent create` first.",
    )

    result = invoke(["agent", "publish", "--json"])

    assert result.exit_code == ConfigError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    assert payload["data"] == {}
    assert payload["errors"] == [
        {
            "code": "CONFIG_ERROR",
            "message": expected.message,
            "remediation": expected.remediation,
            "details": {"option": "--agent-id"},
        }
    ]


@pytest.mark.unit
def test_agent_publish_unauthenticated_exits_with_the_auth_code(
    invoke, unauthenticated_looker, install_looker_auth, patch_cli, state_file
):
    """The shared auth guard, ``agent publish`` copy -- now literally shared.

    This was the third of three different strings for one condition ("Looker
    instance URL not configured."). It is now :func:`no_looker_instance`, the
    same object ``ge status`` and ``agent golden-queries`` raise.
    """
    install_looker_auth(unauthenticated_looker)
    ensure = patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "publish"])

    assert result.exit_code == AuthError.exit_code
    assert no_looker_instance().message in result.output
    assert not ensure.calls  # type: ignore[attr-defined]


@pytest.mark.unit
def test_agent_publish_unauthenticated_json_shape(
    invoke, unauthenticated_looker, install_looker_auth, patch_cli, state_file
):
    """Byte-identical to the ``ge status`` auth envelope, by construction."""
    install_looker_auth(unauthenticated_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "publish", "--json"])

    assert result.exit_code == AuthError.exit_code
    assert envelope(result)["errors"] == [
        {
            "code": "AUTH_ERROR",
            "message": no_looker_instance().message,
            "remediation": no_looker_instance().remediation,
            "details": {},
        }
    ]


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_publish_agent_id_option_overrides_state(invoke, fake_looker, install_looker_auth, patch_cli, state_file):
    """``--agent-id`` wins over ``state.ca_agent_id``."""
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    publish = patch_cli("publish_agent_to_ge", Recorder(True))
    state_file(ca_agent_id="from-state")

    result = invoke(["agent", "publish", "--agent-id", "from-flag", "--json"])

    assert result.exit_code == 0
    assert envelope(result)["data"]["agent_id"] == "from-flag"
    assert publish.calls[0][0][1] == "from-flag"


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_publish_non_interactive_flag_inverts_into_interactive(
    invoke, fake_looker, install_looker_auth, patch_cli, state_file
):
    """``--non-interactive`` is forwarded as ``interactive=False``."""
    install_looker_auth(fake_looker)
    ensure = patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    patch_cli("publish_agent_to_ge", Recorder(True))
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "publish", "--non-interactive"])

    assert result.exit_code == 0
    call = ensure.calls[0]  # type: ignore[attr-defined]
    assert call["interactive"] is False
    # allow_reconfigure is left at its default here, unlike `ge configure`
    # which passes it explicitly.
    assert call["allow_reconfigure"] is True


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_publish_proceeds_even_when_ge_is_not_configured(
    invoke, fake_looker, install_looker_auth, patch_cli, state_file
):
    """An unconfigured GE does not abort the publish attempt."""
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=False))
    publish = patch_cli("publish_agent_to_ge", Recorder(True))
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "publish", "--json"])

    # BUG: the return value of ensure_gemini_enterprise_configured() is still
    # never checked for `ge_configured`; the command publishes regardless and
    # can report SUCCESS with `ge_configured: false`. Phase 2 fixed the
    # *shape* of this report, not the missing precondition check -- so the
    # contradiction is now merely one level deeper, under `data`.
    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["status"] == "SUCCESS"
    assert payload["data"]["ge_configured"] is False
    assert payload["data"]["ge_instance_id"] is None
    assert publish.called


# ===========================================================================
# agent create
# ===========================================================================


@pytest.fixture
def agent_create_stubs(patch_cli):
    """Install recorders for every service call ``agent create`` makes."""
    stubs = {
        "provision_ca_agent": patch_cli("provision_ca_agent", Recorder("42")),
        "extract_golden_queries_from_dashboard_id": patch_cli("extract_golden_queries_from_dashboard_id", Recorder([])),
        "extract_golden_queries_from_dashboards": patch_cli("extract_golden_queries_from_dashboards", Recorder([])),
        "register_and_link_golden_queries": patch_cli("register_and_link_golden_queries", Recorder(0)),
        "publish_agent_to_ge": patch_cli("publish_agent_to_ge", Recorder(True)),
    }
    stubs["ensure_gemini_enterprise_configured"] = patch_cli(
        "ensure_gemini_enterprise_configured", make_ensure_ge(configured=True)
    )
    return stubs


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_happy_path(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd
):
    """Minimal successful provisioning prints the chat URL and saves state."""
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create"])

    assert result.exit_code == 0
    assert f"{fake_looker.base_url}/conversational-analytics/agents/42" in result.output
    saved = read_state(isolated_cwd)
    assert saved["ca_agent_id"] == "42"
    assert saved["looker_instance_url"] == fake_looker.base_url


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_derives_default_agent_name_from_model(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd
):
    """Without ``--name`` the stored name is ``Title Cased Model + " Assistant"``."""
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create"])

    assert result.exit_code == 0
    # BUG: commands/agent.py -- the CLI recomputes the display name locally instead of
    # reading back whatever provision_ca_agent() actually named the agent, so
    # state.ca_agent_name can drift from the real Looker agent name.
    assert read_state(isolated_cwd)["ca_agent_name"] == "Retail Analytics Assistant"


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_explore_defaults_to_first_generated_table(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file
):
    """``--explore`` falls back to ``generated_tables[0]``, else the model name."""
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics", generated_tables=["fct_orders", "dim_users"])

    result = invoke(["agent", "create"])

    assert result.exit_code == 0
    _, kwargs = agent_create_stubs["provision_ca_agent"].calls[0]
    assert kwargs["model_name"] == "retail_analytics"
    assert kwargs["explore_name"] == "fct_orders"


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_explore_falls_back_to_model_name(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file
):
    """With no generated tables the explore name *is* the model name."""
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics", generated_tables=[])

    result = invoke(["agent", "create"])

    assert result.exit_code == 0
    _, kwargs = agent_create_stubs["provision_ca_agent"].calls[0]
    assert kwargs["explore_name"] == "retail_analytics"


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_explicit_options_win_over_state(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file
):
    """``--model``/``--explore``/``--name``/``--instructions`` are passed through verbatim."""
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="from_state", generated_tables=["from_state_table"])

    result = invoke(
        [
            "agent",
            "create",
            "--model",
            "flag_model",
            "--explore",
            "flag_explore",
            "--name",
            "Custom Bot",
            "--instructions",
            "Be terse.",
        ]
    )

    assert result.exit_code == 0
    _, kwargs = agent_create_stubs["provision_ca_agent"].calls[0]
    assert kwargs["model_name"] == "flag_model"
    assert kwargs["explore_name"] == "flag_explore"
    assert kwargs["agent_name"] == "Custom Bot"
    assert kwargs["custom_instructions"] == "Be terse."


@pytest.mark.unit
def test_agent_create_provision_failure_is_a_remote_api_error(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd
):
    """A ``None`` agent id is a remote failure (exit 5), not an anonymous exit 1.

    The distinction matters to a caller deciding what to do next: exit 5 means
    the request reached Looker and was refused, so retrying or checking that
    the explore is deployed is sensible. Exit 1 meant "something happened".
    """
    install_looker_auth(fake_looker)
    agent_create_stubs["provision_ca_agent"].return_value = None
    state_file(lookml_model_name="retail_analytics", ca_agent_id=None)

    result = invoke(["agent", "create"])

    assert result.exit_code == RemoteApiError.exit_code
    assert "Failed to provision Conversational Analytics Agent." in result.output
    assert not agent_create_stubs["register_and_link_golden_queries"].called
    # The state file is the one the fixture wrote; no new agent id was recorded.
    assert read_state(isolated_cwd)["ca_agent_id"] is None


@pytest.mark.unit
def test_agent_create_provision_failure_json_names_the_model_and_explore(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file
):
    """The failure carries the inputs that caused it, so the caller can fix them.

    Provisioning almost always fails because the model or explore is not
    actually deployed to production. Putting both in ``details`` means the
    diagnosis does not require re-reading the command line that was run.
    """
    install_looker_auth(fake_looker)
    agent_create_stubs["provision_ca_agent"].return_value = None
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--explore", "fct_orders", "--json"])

    assert result.exit_code == RemoteApiError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    assert payload["errors"][0]["code"] == "REMOTE_API_ERROR"
    assert payload["errors"][0]["details"] == {"model": "retail_analytics", "explore": "fct_orders"}


@pytest.mark.unit
def test_agent_create_unauthenticated_exits_with_the_auth_code(
    invoke, unauthenticated_looker, install_looker_auth, agent_create_stubs
):
    """The shared auth guard, ``agent create`` copy -- now genuinely shared.

    This command used to hand-write a fourth spelling of "you are not logged
    in". Phase 3 moved the guard into :meth:`AppContext.looker_auth`, so all
    six Looker-touching commands now produce the same error for the same
    condition.

    The message here is :func:`no_looker_instance`, not
    :func:`looker_not_authenticated`, and the distinction is the point: the
    fake reports *no instance URL at all*, which means "tell me which Looker"
    rather than "your token expired". ``agent create`` previously collapsed
    both conditions into the latter, sending a user who had simply never run
    ``lkr auth login`` -- or who had a token but no configured instance -- to
    the same unhelpful remediation.
    """
    install_looker_auth(unauthenticated_looker)

    result = invoke(["agent", "create"])

    assert result.exit_code == AuthError.exit_code
    assert no_looker_instance().message in result.output
    # The remediation is machine-addressable rather than baked into prose.
    assert "lkr auth login" in result.output
    assert not agent_create_stubs["provision_ca_agent"].called


@pytest.mark.unit
def test_agent_create_rejects_authless_headers_even_with_base_url(invoke, install_looker_auth, agent_create_stubs):
    """A reachable instance with no bearer token is still unauthenticated.

    ``agent create`` is the only command in the group that checks the header
    as well as the URL, because provisioning is a write: failing here is much
    cheaper than a 401 halfway through creating the agent.
    """

    class _AuthlessLooker:
        """Reachable instance, but no Authorization header was negotiated."""

        base_url = "https://fake.cloud.looker.com"
        headers = {"Content-Type": "application/json"}

        def auth_context(self, *_args, **_kwargs):
            return self.headers, self.base_url

    install_looker_auth(_AuthlessLooker())

    result = invoke(["agent", "create"])

    assert result.exit_code == AuthError.exit_code
    assert looker_not_authenticated().message in result.output
    assert not agent_create_stubs["provision_ca_agent"].called


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_extracts_golden_queries_from_dashboard_id(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd
):
    """``--dashboard-id`` routes through the REST extractor and links the results."""
    install_looker_auth(fake_looker)
    agent_create_stubs["extract_golden_queries_from_dashboard_id"].return_value = [
        {"prompt": "q1", "query": {}},
        {"prompt": "q2", "query": {}},
    ]
    agent_create_stubs["register_and_link_golden_queries"].return_value = 2
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--dashboard-id", "retail::overview"])

    assert result.exit_code == 0
    args, _ = agent_create_stubs["extract_golden_queries_from_dashboard_id"].calls[0]
    assert args == (fake_looker.base_url, fake_looker.headers, "retail::overview")
    reg_args, _ = agent_create_stubs["register_and_link_golden_queries"].calls[0]
    assert reg_args[2] == "42"
    assert len(reg_args[3]) == 2
    assert read_state(isolated_cwd)["golden_queries_count"] == 2


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_extracts_from_local_dashboard_file(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd, tmp_path
):
    """``--dashboard-file`` walks up to the parent when the dir is named ``dashboards``."""
    install_looker_auth(fake_looker)
    dash_dir = tmp_path / "lookml" / "dashboards"
    dash_dir.mkdir(parents=True)
    dash_file = dash_dir / "overview.dashboard.lookml"
    dash_file.write_text("dashboard: overview\n", encoding="utf-8")
    agent_create_stubs["extract_golden_queries_from_dashboards"].return_value = [{"prompt": "q", "query": {}}]
    agent_create_stubs["register_and_link_golden_queries"].return_value = 1
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--dashboard-file", str(dash_file)])

    assert result.exit_code == 0
    _, kwargs = agent_create_stubs["extract_golden_queries_from_dashboards"].calls[0]
    # A file inside a directory literally named "dashboards" is rewritten to its
    # grandparent, because the extractor re-appends "dashboards" itself.
    assert kwargs["lookml_dir"] == dash_dir.parent
    assert kwargs["default_model"] == "retail_analytics"
    assert kwargs["dashboard_file"] == dash_file
    assert read_state(isolated_cwd)["golden_queries_count"] == 1


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_dashboards_dir_not_named_dashboards_is_used_as_is(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, tmp_path
):
    """A directory *not* named ``dashboards`` is passed through unmodified."""
    install_looker_auth(fake_looker)
    other = tmp_path / "tiles"
    other.mkdir()
    agent_create_stubs["extract_golden_queries_from_dashboards"].return_value = []
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--dashboards-dir", str(other)])

    assert result.exit_code == 0
    _, kwargs = agent_create_stubs["extract_golden_queries_from_dashboards"].calls[0]
    assert kwargs["lookml_dir"] == other
    assert kwargs["dashboard_file"] is None


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_missing_dashboard_path_is_silently_skipped(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, tmp_path
):
    """A nonexistent ``--dashboards-dir`` produces no warning and no failure."""
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--dashboards-dir", str(tmp_path / "nope")])

    # BUG: commands/agent.py -- `if dash_path and dash_path.exists()` swallows a
    # user-supplied path that does not exist. The command exits 0 having
    # created an agent with zero golden queries and never tells the user their
    # --dashboards-dir was wrong.
    assert result.exit_code == 0
    assert not agent_create_stubs["extract_golden_queries_from_dashboards"].called
    assert "nope" not in result.output


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_skips_registration_when_no_queries_found(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd
):
    """Zero extracted queries means ``register_and_link`` is never called."""
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics", golden_queries_count=7)

    result = invoke(["agent", "create", "--dashboard-id", "empty::dash"])

    assert result.exit_code == 0
    assert not agent_create_stubs["register_and_link_golden_queries"].called
    # The stale count from the prior run is preserved, not reset to 0.
    assert read_state(isolated_cwd)["golden_queries_count"] == 7


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_without_publish_ge_skips_ge_entirely(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd
):
    """GE work is opt-in via ``--publish-ge``."""
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create"])

    assert result.exit_code == 0
    assert not agent_create_stubs["ensure_gemini_enterprise_configured"].calls
    assert not agent_create_stubs["publish_agent_to_ge"].called
    assert read_state(isolated_cwd)["published_to_ge"] is False


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_create_publish_ge_flag_runs_ge_flow(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd
):
    """``--publish-ge`` configures GE then publishes, recording the outcome."""
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--publish-ge", "--non-interactive"])

    assert result.exit_code == 0
    ensure_call = agent_create_stubs["ensure_gemini_enterprise_configured"].calls[0]
    assert ensure_call["interactive"] is False
    pub_args, _ = agent_create_stubs["publish_agent_to_ge"].calls[0]
    assert pub_args == (fake_looker.base_url, "42", fake_looker.headers)
    saved = read_state(isolated_cwd)
    assert saved["published_to_ge"] is True
    assert saved["ge_configured"] is True


@pytest.mark.unit
def test_agent_create_publish_ge_failure_is_partial_and_exits_non_zero(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd
):
    """A half-created agent must not be reported as a success.

    This was the most consequential defect in the group: the boolean returned
    by ``publish_agent_to_ge`` was stored and never checked, so
    ``agent create --publish-ge`` printed a chat URL and exited 0 while the
    agent was unreachable from Gemini Enterprise -- the one thing the flag was
    asked to guarantee. ``agent publish`` failed loudly for the identical
    underlying failure. The outcome is now PARTIAL, which exits non-zero, so a
    caller chaining on ``&&`` stops here instead of declaring the demo done.
    """
    install_looker_auth(fake_looker)
    agent_create_stubs["publish_agent_to_ge"].return_value = False
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--publish-ge", "--non-interactive"])

    assert result.exit_code == RemoteApiError.exit_code
    assert "could not be published to Gemini Enterprise" in result.output
    # The agent itself really was created, so it is still recorded: the point
    # of PARTIAL is that the work done is kept and only the gap is reported.
    saved = read_state(isolated_cwd)
    assert saved["ca_agent_id"] == "42"
    assert saved["published_to_ge"] is False


@pytest.mark.unit
def test_agent_create_publish_ge_failure_json_is_partial_with_a_recovery_command(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file
):
    """PARTIAL carries both halves of the truth: the agent id *and* the failure.

    An orchestrator must not have to choose between "it worked" and "it did
    not". It needs the id to keep going and the remediation to finish the job,
    which is why the data payload is fully populated alongside the error.
    """
    install_looker_auth(fake_looker)
    agent_create_stubs["publish_agent_to_ge"].return_value = False
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--publish-ge", "--non-interactive", "--json"])

    assert result.exit_code == RemoteApiError.exit_code
    payload = envelope(result)
    assert payload["status"] == "PARTIAL"
    assert payload["data"]["agent_id"] == "42"
    assert payload["data"]["published_to_ge"] is False
    assert payload["errors"][0]["code"] == "REMOTE_API_ERROR"
    assert "demo-create agent publish --agent-id 42" in payload["errors"][0]["remediation"]


@pytest.mark.unit
def test_agent_create_json_emits_the_agent_id_an_orchestrator_needs(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd
):
    """``agent create`` gained ``--json``; it was previously unreadable by machine.

    This is the command that mints the agent id every later gate depends on,
    and it was the one command in the group with no ``--json`` at all -- the id
    could only be recovered by parsing ``.demo-state.json`` out of band.
    """
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["command"] == "agent create"
    assert payload["status"] == "SUCCESS"
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["data"] == {
        "agent_id": "42",
        "agent_name": "Retail Analytics Assistant",
        "model": "retail_analytics",
        "explore": "retail_analytics",
        "chat_url": f"{fake_looker.base_url}/conversational-analytics/agents/42",
        "golden_queries_linked": 0,
        "published_to_ge": False,
        "state_file": str(isolated_cwd / STATE_FILE_NAME),
    }


@pytest.mark.unit
def test_agent_create_json_reports_how_many_golden_queries_were_linked(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file
):
    """Grounding coverage is a headline number, so it belongs in the payload.

    A CA agent with zero golden queries is technically provisioned and
    practically useless; surfacing the count lets a caller notice that before
    handing the demo over.
    """
    install_looker_auth(fake_looker)
    agent_create_stubs["extract_golden_queries_from_dashboard_id"].return_value = [
        {"prompt": "q1", "query": {}},
        {"prompt": "q2", "query": {}},
    ]
    agent_create_stubs["register_and_link_golden_queries"].return_value = 2
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--dashboard-id", "retail::overview", "--json"])

    assert result.exit_code == 0
    assert envelope(result)["data"]["golden_queries_linked"] == 2


@pytest.mark.unit
def test_agent_create_without_publish_ge_suggests_the_gate_five_command(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file
):
    """Skipping GE is a choice, so the command hands back the way to resume.

    ``next_actions`` is what lets an orchestrator walk the pipeline without
    re-reading a prompt file, and the pre-substituted ``--agent-id`` means the
    follow-up needs no state lookup. ``requires_human_confirmation`` is set
    because publishing to Gemini Enterprise is a Gate 5 decision.
    """
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--json"])

    assert result.exit_code == 0
    assert envelope(result)["next_actions"] == [
        {
            "description": "Publish the agent to Gemini Enterprise",
            "command": "demo-create agent publish --agent-id 42",
            "gate": 5,
            "requires_human_confirmation": True,
        }
    ]


@pytest.mark.unit
def test_agent_create_with_successful_publish_ge_suggests_nothing_further(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file
):
    """Gate 5 is already done, so proposing it again would loop the orchestrator."""
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--publish-ge", "--non-interactive", "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["data"]["published_to_ge"] is True
    assert payload["next_actions"] == []


@pytest.mark.unit
def test_agent_create_json_keeps_progress_narration_off_stdout(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file
):
    """This command narrates as it works, and that narration used to break ``--json``.

    ``agent create`` prints provisioning and extraction progress lines. With
    the console on stderr they are still visible to a human watching the run,
    while ``stdout`` holds exactly one parseable document.
    """
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics")

    result = invoke(["agent", "create", "--json"])

    assert result.exit_code == 0
    assert "Provisioning Looker CA Agent" in result.output
    assert "Provisioning Looker CA Agent" not in result.stdout
    assert envelope(result)["data"]["agent_id"] == "42"


# ===========================================================================
# agent golden-queries
# ===========================================================================


@pytest.fixture
def golden_queries_stubs(patch_cli):
    """Install recorders for the two extractors and the linker."""
    return {
        "extract_golden_queries_from_dashboard_id": patch_cli("extract_golden_queries_from_dashboard_id", Recorder([])),
        "extract_golden_queries_from_dashboards": patch_cli("extract_golden_queries_from_dashboards", Recorder([])),
        "register_and_link_golden_queries": patch_cli("register_and_link_golden_queries", Recorder(0)),
    }


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_golden_queries_happy_path(
    invoke, fake_looker, install_looker_auth, golden_queries_stubs, state_file, isolated_cwd
):
    """Extracted queries are linked to the agent and the count is persisted."""
    install_looker_auth(fake_looker)
    golden_queries_stubs["extract_golden_queries_from_dashboard_id"].return_value = [
        {"prompt": "q1", "query": {}},
        {"prompt": "q2", "query": {}},
        {"prompt": "q3", "query": {}},
    ]
    golden_queries_stubs["register_and_link_golden_queries"].return_value = 3
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "golden-queries", "--dashboard-id", "d1"])

    assert result.exit_code == 0
    assert read_state(isolated_cwd)["golden_queries_count"] == 3


@pytest.mark.unit
def test_agent_golden_queries_success_reports_what_it_linked(
    invoke, fake_looker, install_looker_auth, golden_queries_stubs, state_file
):
    """The success path used to print literally nothing.

    No ``print_success``, no "Updated state saved to" -- the only feedback a
    user got was whatever the service layer happened to log, which made a
    successful grounding run indistinguishable from a silent no-op. It now
    states the count and the agent it linked to.
    """
    install_looker_auth(fake_looker)
    golden_queries_stubs["extract_golden_queries_from_dashboard_id"].return_value = [{"prompt": "q", "query": {}}]
    golden_queries_stubs["register_and_link_golden_queries"].return_value = 1
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "golden-queries", "--dashboard-id", "d1"])

    assert result.exit_code == 0
    assert "Linked 1 of 1 golden queries to agent `1042`." in result.output
    # The narration is on stderr, so a caller redirecting stdout still gets a
    # clean stream -- here, an empty one, since --json was not passed.
    assert result.stdout.strip() == ""


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_golden_queries_agent_id_option_overrides_state(
    invoke, fake_looker, install_looker_auth, golden_queries_stubs, state_file
):
    """``--agent-id`` takes precedence over ``state.ca_agent_id``."""
    install_looker_auth(fake_looker)
    golden_queries_stubs["extract_golden_queries_from_dashboard_id"].return_value = [{"prompt": "q", "query": {}}]
    golden_queries_stubs["register_and_link_golden_queries"].return_value = 1
    state_file(ca_agent_id="from-state")

    result = invoke(["agent", "golden-queries", "--agent-id", "from-flag", "--dashboard-id", "d1"])

    assert result.exit_code == 0
    args, _ = golden_queries_stubs["register_and_link_golden_queries"].calls[0]
    assert args[2] == "from-flag"


@pytest.mark.unit
def test_agent_golden_queries_without_agent_id_is_a_config_error(
    invoke, fake_looker, install_looker_auth, golden_queries_stubs
):
    """A missing required value exits 4, not the undifferentiated 1.

    Exit 4 tells an orchestrator the invocation is repairable by supplying an
    argument, as opposed to exit 3 (re-authenticate) or 5 (retry the API).
    Previously every one of those conditions exited 1 with a prose string.
    """
    install_looker_auth(fake_looker)

    result = invoke(["agent", "golden-queries"])

    assert result.exit_code == ConfigError.exit_code
    assert "--agent-id" in result.output
    assert "demo-create agent create" in result.output
    assert not golden_queries_stubs["extract_golden_queries_from_dashboard_id"].called


@pytest.mark.unit
def test_agent_golden_queries_without_agent_id_json_shape(
    invoke, fake_looker, install_looker_auth, golden_queries_stubs
):
    """The error is emitted by the canonical factory, not hand-written here.

    Asserting against ``missing_option(...)`` rather than a literal string is
    the point: it is now impossible for this command's wording to drift from
    ``agent publish``'s, which is exactly what happened before Phase 2.
    """
    install_looker_auth(fake_looker)
    expected = missing_option(
        "--agent-id",
        purpose="the CA agent to attach golden queries to",
        hint="Or run `demo-create agent create` first.",
    )

    result = invoke(["agent", "golden-queries", "--json"])

    assert result.exit_code == ConfigError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    assert payload["errors"] == [
        {
            "code": "CONFIG_ERROR",
            "message": expected.message,
            "remediation": expected.remediation,
            "details": {"option": "--agent-id"},
        }
    ]


@pytest.mark.unit
def test_agent_golden_queries_unauthenticated_exits_with_the_auth_code(
    invoke, unauthenticated_looker, install_looker_auth, golden_queries_stubs, state_file
):
    """The shared auth guard, ``agent golden-queries`` copy -- now literally shared.

    This command used to say "Looker instance URL not configured." while its
    siblings said two other things for the identical condition. All three are
    now :func:`no_looker_instance`.
    """
    install_looker_auth(unauthenticated_looker)
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "golden-queries", "--dashboard-id", "d1"])

    assert result.exit_code == AuthError.exit_code
    assert no_looker_instance().message in result.output
    assert not golden_queries_stubs["extract_golden_queries_from_dashboard_id"].called


@pytest.mark.unit
def test_agent_golden_queries_empty_result_warns_instead_of_returning_silently(
    invoke, fake_looker, install_looker_auth, golden_queries_stubs, state_file, isolated_cwd
):
    """Finding nothing is a success, but it has to *say* so.

    This branch previously returned in total silence: exit 0, no output, no
    state change. A user who pointed the command at the wrong dashboard got a
    result indistinguishable from a successful grounding run. It now emits a
    warning naming the reason, while still exiting 0 -- an empty dashboard is
    not an error, it is a no-op.
    """
    install_looker_auth(fake_looker)
    state_file(ca_agent_id="1042", golden_queries_count=5)

    result = invoke(["agent", "golden-queries", "--dashboard-id", "d1"])

    assert result.exit_code == 0
    assert "No query tiles found to extract." in result.output
    assert not golden_queries_stubs["register_and_link_golden_queries"].called
    # Deliberately no save: a no-op must not clobber the count from the run
    # that actually did the grounding.
    assert read_state(isolated_cwd)["golden_queries_count"] == 5


@pytest.mark.unit
def test_agent_golden_queries_json_empty_result_is_success_with_a_warning(
    invoke, fake_looker, install_looker_auth, golden_queries_stubs, state_file
):
    """The no-op is machine-readable too, so an agent can react to it.

    ``queries_found: 0`` plus a populated ``warnings`` list is what lets an
    orchestrator decide to re-run against a different dashboard rather than
    proceeding to publish an unusable, ungrounded agent.
    """
    install_looker_auth(fake_looker)
    state_file(ca_agent_id="1042", golden_queries_count=5)

    result = invoke(["agent", "golden-queries", "--dashboard-id", "d1", "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["status"] == "SUCCESS"
    assert payload["warnings"] == ["No query tiles found to extract."]
    assert payload["data"] == {"agent_id": "1042", "queries_found": 0, "queries_linked": 0}
    assert payload["errors"] == []


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_golden_queries_ignores_dashboard_file_when_extracting_locally(
    invoke, fake_looker, install_looker_auth, golden_queries_stubs, state_file, tmp_path
):
    """``--dashboard-file`` narrows the *directory* but is not forwarded downstream."""
    install_looker_auth(fake_looker)
    dash_dir = tmp_path / "lookml" / "dashboards"
    dash_dir.mkdir(parents=True)
    dash_file = dash_dir / "overview.dashboard.lookml"
    dash_file.write_text("dashboard: overview\n", encoding="utf-8")
    golden_queries_stubs["extract_golden_queries_from_dashboards"].return_value = [{"prompt": "q", "query": {}}]
    golden_queries_stubs["register_and_link_golden_queries"].return_value = 1
    # ``lookml_model_name`` is a real precondition of the local-extraction path:
    # without a model from state or ``--model`` the command now refuses before
    # extracting anything, and this test would never reach its subject.
    state_file(ca_agent_id="1042", lookml_model_name="retail_analytics")

    result = invoke(["agent", "golden-queries", "--dashboard-file", str(dash_file)])

    assert result.exit_code == 0
    _, kwargs = golden_queries_stubs["extract_golden_queries_from_dashboards"].calls[0]
    # BUG: commands/agent.py -- unlike `agent create`, this command
    # never passes `dashboard_file=` to extract_golden_queries_from_dashboards.
    # Asking for one specific dashboard file therefore extracts *every*
    # dashboard in that directory.
    assert "dashboard_file" not in kwargs
    assert kwargs["lookml_dir"] == dash_dir.parent


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_golden_queries_model_and_explore_fall_back_to_state(
    invoke, fake_looker, install_looker_auth, golden_queries_stubs, state_file, tmp_path
):
    """With ``--model``/``--explore`` omitted, both are taken from prior state.

    Phase 5 added those two flags so the required-model guard could name one
    that exists; state remains the source when they are not passed, which is
    what keeps the flags a convenience rather than a new obligation.
    """
    install_looker_auth(fake_looker)
    dash_dir = tmp_path / "tiles"
    dash_dir.mkdir()
    golden_queries_stubs["extract_golden_queries_from_dashboards"].return_value = []
    state_file(ca_agent_id="1042", lookml_model_name="retail_analytics", generated_tables=["fct_orders"])

    result = invoke(["agent", "golden-queries", "--dashboards-dir", str(dash_dir)])

    assert result.exit_code == 0
    _, kwargs = golden_queries_stubs["extract_golden_queries_from_dashboards"].calls[0]
    assert kwargs["default_model"] == "retail_analytics"
    assert kwargs["default_explore"] == "fct_orders"


@pytest.mark.characterization
@pytest.mark.unit
def test_agent_golden_queries_json_reports_found_and_linked_counts(
    invoke, fake_looker, install_looker_auth, golden_queries_stubs, state_file, isolated_cwd
):
    """``agent golden-queries`` gained ``--json``; it previously had none.

    Both counts are reported because they can legitimately differ: Looker can
    reject an individual query while accepting the rest. Grounding quality is
    the whole value of this command, so a caller must be able to see that it
    asked for three and got two.
    """
    install_looker_auth(fake_looker)
    golden_queries_stubs["extract_golden_queries_from_dashboard_id"].return_value = [
        {"prompt": "q1", "query": {}},
        {"prompt": "q2", "query": {}},
        {"prompt": "q3", "query": {}},
    ]
    golden_queries_stubs["register_and_link_golden_queries"].return_value = 2
    state_file(ca_agent_id="1042")

    result = invoke(["agent", "golden-queries", "--dashboard-id", "d1", "--json"])

    payload = envelope(result)
    assert payload["command"] == "agent golden-queries"
    assert payload["data"] == {
        "agent_id": "1042",
        "queries_found": 3,
        "queries_linked": 2,
        "state_file": str(isolated_cwd / STATE_FILE_NAME),
    }
    # BUG: linking 2 of 3 is still reported as an unqualified SUCCESS with exit
    # 0, whereas `agent create --publish-ge` now downgrades an equivalent
    # half-done outcome to PARTIAL. The counts make the shortfall *visible*,
    # but a caller chaining on `&&` still cannot detect it. Left unfixed here
    # because deciding what fraction constitutes a failure is a product
    # question, not an output-contract one.
    assert result.exit_code == 0
    assert payload["status"] == "SUCCESS"


# ===========================================================================
# embed scaffold
# ===========================================================================


@pytest.mark.characterization
@pytest.mark.unit
def test_embed_scaffold_rejects_agent_id_option(invoke, fake_scaffolder, tmp_path):
    """``--agent-id`` is documented but not implemented."""
    result = invoke(
        [
            "embed",
            "scaffold",
            "--looker-project",
            "retail_analytics",
            "--target-dir",
            str(tmp_path / "portal"),
            "--agent-id",
            "1042",
        ]
    )

    # BUG: README.md:318 documents
    #   `demo-create embed scaffold ... --agent-id 1042 ...`
    # but embed_scaffold (commands/embed.py) declares no such option, so the
    # documented invocation fails with a Click usage error.
    assert result.exit_code == 2
    assert not fake_scaffolder.captured


@pytest.mark.characterization
@pytest.mark.unit
def test_embed_scaffold_happy_path(invoke, fake_scaffolder, state_file, isolated_cwd, tmp_path):
    """A successful scaffold records the workspace, portal URL, and demo scope."""
    dest = tmp_path / "portal"
    state_file(looker_project_name="retail_analytics", looker_instance_url="https://fake.cloud.looker.com")

    result = invoke(["embed", "scaffold", "--target-dir", str(dest)])

    assert result.exit_code == 0
    assert "External Embed Portal configured at" in result.output
    saved = read_state(isolated_cwd)
    assert saved["embed_workspace_dir"] == str(dest)
    assert saved["embed_portal_url"] == "http://localhost:8008"
    assert saved["demo_scope"] == "external_embed"


@pytest.mark.characterization
@pytest.mark.unit
def test_embed_scaffold_builds_config_options_from_state(invoke, fake_scaffolder, state_file, tmp_path):
    """State supplies project, instance URL, and model when flags are omitted."""
    dest = tmp_path / "portal"
    state_file(
        looker_project_name="retail_analytics",
        looker_instance_url="https://fake.cloud.looker.com",
        lookml_model_name="retail_model",
        deployed_dashboard_id="dash-77",
    )

    result = invoke(["embed", "scaffold", "--target-dir", str(dest)])

    assert result.exit_code == 0
    opts = fake_scaffolder.captured[0]
    assert opts.demo_name == "retail_analytics"
    assert opts.target_dir == dest
    assert opts.looker_instance_url == "https://fake.cloud.looker.com"
    assert opts.looker_project_name == "retail_analytics"
    assert opts.lookml_model_name == "retail_model"
    assert opts.dashboard_id == "dash-77"


@pytest.mark.characterization
@pytest.mark.unit
def test_embed_scaffold_derives_brand_name_and_title(invoke, fake_scaffolder, state_file, tmp_path):
    """Brand name is the title-cased project; the title appends "Intelligence Portal"."""
    dest = tmp_path / "portal"
    state_file(looker_project_name="retail_analytics")

    result = invoke(["embed", "scaffold", "--target-dir", str(dest)])

    assert result.exit_code == 0
    opts = fake_scaffolder.captured[0]
    assert opts.brand_name == "Retail Analytics"
    assert opts.brand_title == "Retail Analytics Intelligence Portal"


@pytest.mark.characterization
@pytest.mark.unit
def test_embed_scaffold_synthesizes_dashboard_id_when_absent(invoke, fake_scaffolder, state_file, tmp_path):
    """With no deployed dashboard the id is guessed as ``<proj>::<proj>_overview``."""
    dest = tmp_path / "portal"
    state_file(looker_project_name="retail_analytics", deployed_dashboard_id=None)

    result = invoke(["embed", "scaffold", "--target-dir", str(dest)])

    assert result.exit_code == 0
    # BUG: commands/agent.py -- the fallback fabricates a dashboard id by string
    # concatenation without verifying it exists in Looker, so the scaffolded
    # portal can ship a .env pointing at a 404.
    assert fake_scaffolder.captured[0].dashboard_id == "retail_analytics::retail_analytics_overview"


@pytest.mark.characterization
@pytest.mark.unit
def test_embed_scaffold_flags_override_state(invoke, fake_scaffolder, state_file, tmp_path):
    """Explicit flags beat every state-derived default."""
    dest = tmp_path / "portal"
    state_file(
        looker_project_name="from_state",
        looker_instance_url="https://from-state.looker.com",
        deployed_dashboard_id="state-dash",
    )

    result = invoke(
        [
            "embed",
            "scaffold",
            "--looker-project",
            "flag_project",
            "--target-dir",
            str(dest),
            "--dashboard-id",
            "flag-dash",
            "--brand-name",
            "Flag Brand",
            "--instance",
            "https://flag.looker.com",
        ]
    )

    assert result.exit_code == 0
    opts = fake_scaffolder.captured[0]
    assert opts.demo_name == "flag_project"
    assert opts.dashboard_id == "flag-dash"
    assert opts.brand_name == "Flag Brand"
    assert opts.brand_title == "Flag Brand Intelligence Portal"
    assert opts.looker_instance_url == "https://flag.looker.com"


@pytest.mark.characterization
@pytest.mark.unit
def test_embed_scaffold_default_target_dir_is_home_relative(invoke, fake_scaffolder, patched_home, state_file):
    """The default ``--target-dir`` is ``Path.home() / f"looker-embed-{project}"``.

    ``Path.home`` is redirected to a temp directory for this test and the
    scaffolder is faked, so the default is *computed and inspected* without
    anything ever being written to the real home directory.
    """
    state_file(looker_project_name="retail_analytics")

    result = invoke(["embed", "scaffold"])

    assert result.exit_code == 0
    opts = fake_scaffolder.captured[0]
    # BUG: commands/embed.py -- scaffolding defaults to writing into the user's home
    # directory rather than the current working directory or a scratch dir,
    # which makes the command destructive-by-default outside the workspace.
    assert opts.target_dir == patched_home / "looker-embed-retail_analytics"
    # Nothing was actually created, not even in the fake home.
    assert list(patched_home.iterdir()) == []


@pytest.mark.characterization
@pytest.mark.unit
def test_embed_scaffold_never_touches_looker_auth(invoke, fake_scaffolder, patch_cli, state_file, tmp_path):
    """``embed scaffold`` is fully offline: it resolves no Looker credentials."""
    auth = patch_cli("get_looker_auth_context", Recorder(({}, "")))
    state_file(looker_project_name="retail_analytics")

    result = invoke(["embed", "scaffold", "--target-dir", str(tmp_path / "portal")])

    assert result.exit_code == 0
    # BUG: commands/embed.py -- the instance URL baked into the generated .env
    # comes straight from state (or --instance) with no auth check and no
    # validation, so an empty state produces a portal wired to "".
    assert not auth.called


@pytest.mark.unit
def test_embed_scaffold_json_emits_the_envelope(invoke, fake_scaffolder, state_file, isolated_cwd, tmp_path):
    """``embed scaffold`` gained ``--json``; it previously had none at all.

    An orchestrator that scaffolds a portal needs the workspace directory and
    the portal URL to tell the user where to ``npm run dev``. Before Phase 2
    both facts were only recoverable by re-reading ``.demo-state.json`` or by
    scraping a Rich line, so the final delivery report was assembled by hand.
    """
    dest = tmp_path / "portal"
    state_file(
        looker_project_name="retail_analytics",
        looker_instance_url="https://fake.cloud.looker.com",
        lookml_model_name="retail_model",
        deployed_dashboard_id="dash-77",
    )

    result = invoke(["embed", "scaffold", "--target-dir", str(dest), "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["command"] == "embed scaffold"
    assert payload["status"] == "SUCCESS"
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["data"] == {
        "workspace_dir": str(dest),
        "portal_url": "http://localhost:8008",
        "looker_project": "retail_analytics",
        "brand_name": "Retail Analytics",
        "dashboard_id": "dash-77",
        "instance_url": "https://fake.cloud.looker.com",
        "state_file": str(isolated_cwd / STATE_FILE_NAME),
    }


@pytest.mark.unit
def test_embed_scaffold_json_keeps_rich_output_off_stdout(invoke, fake_scaffolder, state_file, tmp_path):
    """The success banner must not corrupt the machine-readable stream.

    This is the whole point of moving the console to stderr: the banner is
    still emitted for a human watching the terminal, but ``stdout`` parses as
    exactly one JSON document.
    """
    dest = tmp_path / "portal"
    state_file(looker_project_name="retail_analytics")

    result = invoke(["embed", "scaffold", "--target-dir", str(dest), "--json"])

    assert result.exit_code == 0
    assert "External Embed Portal configured at" not in result.stdout
    assert envelope(result)["data"]["workspace_dir"] == str(dest)


# ===========================================================================
# Group-level invariants
# ===========================================================================


@pytest.mark.characterization
@pytest.mark.unit
@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["ge"], id="ge"),
        pytest.param(["agent"], id="agent"),
        pytest.param(["embed"], id="embed"),
    ],
)
def test_group_with_no_args_shows_help(invoke, args):
    """Every group is declared ``no_args_is_help=True``."""
    result = invoke(args)

    # Typer exits 0 for a bare group with no_args_is_help since Click 8.2's
    # NoArgsIsHelpError; pin whichever code ships today alongside the banner.
    assert result.exit_code in (0, 2)
    assert "Usage" in result.output


@pytest.mark.characterization
@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        pytest.param(["ge", "status"], {"status", "configure", "publish"}, id="ge"),
        pytest.param(["agent", "create"], {"create", "golden-queries", "publish"}, id="agent"),
        pytest.param(["embed", "scaffold"], {"scaffold"}, id="embed"),
    ],
)
def test_group_subcommand_inventory(invoke, args, expected):
    """Pin the exact set of subcommands each group exposes."""
    result = invoke([args[0], "--help"])

    assert result.exit_code == 0
    for name in expected:
        assert name in result.output
