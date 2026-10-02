"""Contract tests for the ``ge``, ``agent``, and ``embed`` command groups."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import envelope, read_state

from looker_demo_cli.errors import (
    AuthError,
    ConfigError,
    RemoteApiError,
    StateError,
    looker_not_authenticated,
)
from looker_demo_cli.state import STATE_FILE_NAME, FlowState

pytestmark = [pytest.mark.unit]


@pytest.fixture
def patched_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect :meth:`Path.home` to a throwaway directory."""
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def fake_scaffolder(patch_cli):
    """Replace ``EmbedScaffolder`` with a recorder that writes nothing."""

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
    """Build a stand-in for ``ensure_gemini_enterprise_configured``."""
    calls: list[dict[str, Any]] = []

    def _ensure(state: FlowState, headers: dict, interactive: bool = True, allow_reconfigure: bool = True):
        calls.append(
            {
                "state": state,
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


@pytest.fixture
def agent_create_stubs(patch_cli):
    """Install recorders for every service call ``agent create`` makes."""
    return {
        "provision_ca_agent": patch_cli("provision_ca_agent", Recorder("42")),
        "extract_golden_queries_from_dashboard_id": patch_cli("extract_golden_queries_from_dashboard_id", Recorder([])),
        "extract_golden_queries_from_dashboards": patch_cli("extract_golden_queries_from_dashboards", Recorder([])),
        "register_and_link_golden_queries": patch_cli("register_and_link_golden_queries", Recorder(0)),
        "publish_agent_to_ge": patch_cli("publish_agent_to_ge", Recorder(True)),
        "ensure_gemini_enterprise_configured": patch_cli(
            "ensure_gemini_enterprise_configured", make_ensure_ge(configured=True)
        ),
    }


@pytest.fixture
def golden_queries_stubs(patch_cli):
    """Install recorders for the two extractors and the linker."""
    return {
        "extract_golden_queries_from_dashboard_id": patch_cli("extract_golden_queries_from_dashboard_id", Recorder([])),
        "extract_golden_queries_from_dashboards": patch_cli("extract_golden_queries_from_dashboards", Recorder([])),
        "register_and_link_golden_queries": patch_cli("register_and_link_golden_queries", Recorder(0)),
    }


# ---------------------------------------------------------------------------
# ge status & ge configure
# ---------------------------------------------------------------------------


def test_ge_status_configured_unconfigured_and_errors(
    invoke, fake_looker, unauthenticated_looker, install_looker_auth, patch_cli
):
    """Covers ge status configured (human + JSON), unconfigured next_actions, empty config, and unauth."""
    install_looker_auth(fake_looker)
    patch_cli("get_looker_ge_config", Recorder(ge_config()))
    human = invoke(["ge", "status"])
    assert human.exit_code == 0
    assert "Gemini Enterprise is fully configured in Looker." in human.output

    patch_cli("get_looker_ge_config", Recorder(ge_config(ai_ge_instance_id="")))
    unconf = invoke(["ge", "status", "--json"])
    assert unconf.exit_code == 0
    payload = envelope(unconf)
    assert payload["data"]["configured"] is False
    assert payload["next_actions"][0]["gate"] == 11

    patch_cli("get_looker_ge_config", Recorder({}))
    empty = invoke(["ge", "status", "--json"])
    assert empty.exit_code == RemoteApiError.exit_code

    install_looker_auth(unauthenticated_looker)
    unauth = invoke(["ge", "status", "--json"])
    assert unauth.exit_code == AuthError.exit_code


def test_ge_configure_happy_path_and_failure(invoke, fake_looker, install_looker_auth, patch_cli):
    """Covers ge configure success (human + JSON) and failure RemoteApiError."""
    install_looker_auth(fake_looker)
    ensure = patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))

    human = invoke(["ge", "configure", "--gcp-project", "my-proj", "--app-id", "app-9"])
    assert human.exit_code == 0
    assert "Gemini Enterprise configured" in human.output

    json_res = invoke(["ge", "configure", "--gcp-project", "my-proj", "--json"])
    assert json_res.exit_code == 0
    assert ensure.calls[-1]["interactive"] is False  # type: ignore[attr-defined]
    assert envelope(json_res)["data"]["configured"] is True

    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=False))
    fail = invoke(["ge", "configure", "--gcp-project", "my-proj"])
    assert fail.exit_code == RemoteApiError.exit_code


# ---------------------------------------------------------------------------
# agent publish & ge publish alias
# ---------------------------------------------------------------------------


def test_agent_and_ge_publish_happy_path_and_failures(
    invoke, fake_looker, install_looker_auth, patch_cli, state_file, isolated_cwd
):
    """Covers agent/ge publish alias parity, state persistence, missing --agent-id, and remote failure."""
    install_looker_auth(fake_looker)
    patch_cli("ensure_gemini_enterprise_configured", make_ensure_ge(configured=True))
    patch_cli("publish_agent_to_ge", Recorder(True))

    no_id = invoke(["agent", "publish", "--json"])
    assert no_id.exit_code == ConfigError.exit_code
    assert envelope(no_id)["errors"][0]["details"] == {"option": "--agent-id"}

    state_file(ca_agent_id="1042", looker_instance_url=fake_looker.base_url)
    human = invoke(["ge", "publish"])
    assert human.exit_code == 0
    assert "published to Gemini Enterprise app" in human.output
    assert read_state(isolated_cwd)["published_to_ge"] is True

    json_res = invoke(["agent", "publish", "--agent-id", "9001", "--json"])
    assert json_res.exit_code == 0
    assert envelope(json_res)["data"]["agent_id"] == "9001"

    patch_cli("publish_agent_to_ge", Recorder(False))
    fail = invoke(["agent", "publish", "--agent-id", "1042", "--json"])
    assert fail.exit_code == RemoteApiError.exit_code
    assert envelope(fail)["errors"][0]["code"] == "REMOTE_API_ERROR"


# ---------------------------------------------------------------------------
# agent create
# ---------------------------------------------------------------------------


def test_agent_create_happy_path_and_golden_queries(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd, tmp_path: Path
):
    """Covers agent create human + JSON, dashboard-id/file golden query extraction, and next_actions."""
    install_looker_auth(fake_looker)
    agent_create_stubs["extract_golden_queries_from_dashboard_id"].return_value = [
        {"prompt": "q1", "query": {}},
        {"prompt": "q2", "query": {}},
    ]
    agent_create_stubs["register_and_link_golden_queries"].return_value = 2
    state_file(lookml_model_name="retail_analytics")

    res = invoke(["agent", "create", "--dashboard-id", "retail::overview", "--json"])
    assert res.exit_code == 0
    payload = envelope(res)
    assert payload["data"]["agent_id"] == "42"
    assert payload["data"]["golden_queries_linked"] == 2
    assert payload["next_actions"][0]["gate"] == 11
    assert read_state(isolated_cwd)["ca_agent_id"] == "42"
    assert read_state(isolated_cwd)["golden_queries_count"] == 2

    dash_dir = tmp_path / "lookml" / "dashboards"
    dash_dir.mkdir(parents=True)
    dash_file = dash_dir / "overview.dashboard.lookml"
    dash_file.write_text("dashboard: overview\n", encoding="utf-8")
    agent_create_stubs["extract_golden_queries_from_dashboards"].return_value = [{"prompt": "q", "query": {}}]
    agent_create_stubs["register_and_link_golden_queries"].return_value = 1

    human = invoke(["agent", "create", "--dashboard-file", str(dash_file)])
    assert human.exit_code == 0
    assert f"{fake_looker.base_url}/conversational-analytics/agents/42" in human.output


def test_agent_create_publish_ge_and_error_paths(
    invoke, fake_looker, install_looker_auth, agent_create_stubs, state_file, isolated_cwd
):
    """Covers --publish-ge success, partial failure, provision failure, and authless headers."""
    install_looker_auth(fake_looker)
    state_file(lookml_model_name="retail_analytics")

    ok = invoke(["agent", "create", "--publish-ge", "--non-interactive", "--json"])
    assert ok.exit_code == 0
    assert envelope(ok)["data"]["published_to_ge"] is True
    assert envelope(ok)["next_actions"] == []

    agent_create_stubs["publish_agent_to_ge"].return_value = False
    partial = invoke(["agent", "create", "--publish-ge", "--non-interactive", "--json"])
    assert partial.exit_code == RemoteApiError.exit_code
    assert envelope(partial)["status"] == "PARTIAL"
    assert read_state(isolated_cwd)["ca_agent_id"] == "42"
    assert read_state(isolated_cwd)["published_to_ge"] is False

    agent_create_stubs["provision_ca_agent"].return_value = None
    fail = invoke(["agent", "create", "--explore", "fct_orders", "--json"])
    assert fail.exit_code == RemoteApiError.exit_code
    assert envelope(fail)["errors"][0]["details"] == {"model": "retail_analytics", "explore": "fct_orders"}

    class _AuthlessLooker:
        base_url = "https://fake.cloud.looker.com"
        headers = {"Content-Type": "application/json"}

        def auth_context(self, *_args, **_kwargs):
            return self.headers, self.base_url

    install_looker_auth(_AuthlessLooker())
    authless = invoke(["agent", "create"])
    assert authless.exit_code == AuthError.exit_code
    assert looker_not_authenticated().message in authless.output


# ---------------------------------------------------------------------------
# agent golden-queries
# ---------------------------------------------------------------------------


def test_agent_golden_queries_linking_empty_warning_and_missing_id(
    invoke, fake_looker, install_looker_auth, golden_queries_stubs, state_file, isolated_cwd
):
    """Covers golden-queries linking, empty query warning, and missing --agent-id ConfigError."""
    install_looker_auth(fake_looker)

    no_id = invoke(["agent", "golden-queries", "--json"])
    assert no_id.exit_code == ConfigError.exit_code

    state_file(ca_agent_id="1042", golden_queries_count=5)
    empty = invoke(["agent", "golden-queries", "--dashboard-id", "d1", "--json"])
    assert empty.exit_code == 0
    assert envelope(empty)["warnings"] == ["No query tiles found to extract."]
    assert read_state(isolated_cwd)["golden_queries_count"] == 5

    golden_queries_stubs["extract_golden_queries_from_dashboard_id"].return_value = [
        {"prompt": "q1", "query": {}},
        {"prompt": "q2", "query": {}},
    ]
    golden_queries_stubs["register_and_link_golden_queries"].return_value = 2
    linked = invoke(["agent", "golden-queries", "--dashboard-id", "d1"])
    assert linked.exit_code == 0
    assert "Linked 2 of 2 golden queries to agent `1042`." in linked.output
    assert read_state(isolated_cwd)["golden_queries_count"] == 2


# ---------------------------------------------------------------------------
# embed scaffold
# ---------------------------------------------------------------------------


def test_embed_scaffold_state_and_flag_precedence(
    invoke, fake_scaffolder, patched_home: Path, state_file, isolated_cwd: Path, tmp_path: Path
):
    """Covers embed scaffold from state, explicit flag overrides, JSON envelope, and default target dir."""
    dest = tmp_path / "portal"
    state_file(
        looker_project_name="retail_analytics",
        looker_instance_url="https://fake.cloud.looker.com",
        lookml_model_name="retail_model",
        deployed_dashboard_id="dash-77",
    )

    json_res = invoke(["embed", "scaffold", "--target-dir", str(dest), "--json"])
    assert json_res.exit_code == 0
    payload = envelope(json_res)
    assert payload["data"] == {
        "workspace_dir": str(dest),
        "portal_url": "http://localhost:8008",
        "looker_project": "retail_analytics",
        "brand_name": "Retail Analytics",
        "dashboard_id": "dash-77",
        "agent_id": "",
        "instance_url": "https://fake.cloud.looker.com",
        "state_file": str(isolated_cwd / STATE_FILE_NAME),
    }

    default_res = invoke(["embed", "scaffold", "--brand-name", "Flag Brand", "--agent-id", "1042"])
    assert default_res.exit_code == 0
    assert "External Embed Portal configured at" in default_res.output
    opts = fake_scaffolder.captured[-1]
    assert opts.target_dir == patched_home / "looker-embed-retail_analytics"
    assert opts.brand_name == "Flag Brand"
    assert opts.agent_id == "1042"


# ---------------------------------------------------------------------------
# --skip terminal branches & Gate 3C critique guard
# ---------------------------------------------------------------------------


def test_agent_publish_and_embed_skip_branches_and_critique_guard(
    invoke, state_file, isolated_cwd: Path
):
    """Covers Gate 3C critique guard on agent create, and --skip on agent create, ge publish, and embed scaffold."""
    state_file(
        precheck_passed=True,
        deployed_dashboard_url="https://fake.cloud.looker.com/dashboards/42",
        critique_approved=False,
    )
    unapproved = invoke(["agent", "create", "--json"])
    assert unapproved.exit_code == StateError.exit_code
    assert "Gate 3C" in envelope(unapproved)["errors"][0]["message"]

    # Approve critique, then skip agent create -> jumps directly to Gate 12 (embed scaffold)
    state_file(
        precheck_passed=True,
        deployed_dashboard_url="https://fake.cloud.looker.com/dashboards/42",
        critique_approved=True,
    )
    skip_ca = invoke(["agent", "create", "--skip", "--json"])
    assert skip_ca.exit_code == 0, skip_ca.output
    assert envelope(skip_ca)["data"]["skipped"] is True
    assert envelope(skip_ca)["next_actions"][0]["gate"] == 12
    assert read_state(isolated_cwd)["ca_agent_status"] == "skipped"

    # ge publish --skip
    skip_ge = invoke(["ge", "publish", "--skip", "--json"])
    assert skip_ge.exit_code == 0, skip_ge.output
    assert envelope(skip_ge)["data"]["skipped"] is True
    assert read_state(isolated_cwd)["ge_publish_status"] == "skipped"

    # embed scaffold --skip -> finishes pipeline (next_actions == [])
    skip_embed = invoke(["embed", "scaffold", "--skip", "--json"])
    assert skip_embed.exit_code == 0, skip_embed.output
    assert envelope(skip_embed)["data"]["skipped"] is True
    assert envelope(skip_embed)["next_actions"] == []
    assert read_state(isolated_cwd)["embed_status"] == "skipped"

