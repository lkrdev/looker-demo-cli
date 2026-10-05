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
    """Replace ``EmbedScaffolder`` and ``provision_embed_instance`` with in-memory stubs."""
    from looker_demo_cli.generators.embed_scaffolder import EmbedProvisioningResult

    class _FakeScaffolder:
        def __init__(self) -> None:
            self.captured: list[Any] = []

        def scaffold_demo_workspace(self, opts: Any) -> Path:
            self.captured.append(opts)
            return opts.target_dir

    fake = _FakeScaffolder()
    patch_cli("EmbedScaffolder", fake)
    patch_cli(
        "provision_embed_instance",
        lambda opts, headers=None: EmbedProvisioningResult(
            group_id=opts.group_id,
            folder_id=opts.folder_id,
            sa_credentials_configured=bool(opts.client_id and opts.client_secret),
        ),
    )
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
    invoke,
    fake_scaffolder,
    patched_home: Path,
    state_file,
    isolated_cwd: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Covers embed scaffold CLI flags/state, all 6 Looker 4.0 provisioning steps, and workspace hydration."""
    from looker_demo_cli.generators import embed_scaffolder
    from looker_demo_cli.generators.embed_scaffolder import (
        EmbedConfigOptions,
        EmbedScaffolder,
        provision_embed_instance,
    )

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
        "group_id": "8",
        "folder_id": "1",
        "themes_created": [],
        "dashboard_moved": False,
        "agent_shared": False,
        "allowlist_configured": False,
        "sa_credentials_configured": False,
        "state_file": str(isolated_cwd / STATE_FILE_NAME),
    }

    default_res = invoke(
        [
            "embed",
            "scaffold",
            "--brand-name",
            "Flag Brand",
            "--agent-id",
            "1042",
            "--client-id",
            "sa-id-123",
            "--client-secret",
            "sa-secret-456",
        ]
    )
    assert default_res.exit_code == 0
    assert "External Embed Portal configured at" in default_res.output
    opts = fake_scaffolder.captured[-1]
    assert opts.target_dir == patched_home / "looker-embed-retail_analytics"
    assert opts.brand_name == "Flag Brand"
    assert opts.agent_id == "1042"
    assert opts.client_id == "sa-id-123"
    assert opts.client_secret == "sa-secret-456"

    # Verify all 6 Looker 4.0 provisioning steps & workspace file hydration
    class _FakeSDK:
        def __init__(self) -> None:
            self.cm_updates: list[tuple[str, Any]] = []
            self.cm_access_creations: list[Any] = []

        def all_user_attributes(self) -> list[Any]:
            return []

        def create_user_attribute(self, body: Any) -> Any:
            return {"id": "attr-brand", "name": "brand"}

        def get_setting(self) -> dict[str, Any]:
            return {"embed_config": {"domain_allowlist": []}}

        def set_setting(self, body: Any) -> dict[str, Any]:
            return body

        def all_groups(self) -> list[Any]:
            return []

        def create_group(self, body: Any) -> dict[str, Any]:
            return {"id": "42", "name": body.name}

        def all_content_metadata_accesses(self, content_metadata_id: str) -> list[Any]:
            return []

        def create_content_metadata_access(self, body: Any) -> dict[str, Any]:
            self.cm_access_creations.append(body)
            return {"id": f"cma-{len(self.cm_access_creations)}"}

        def all_folders(self) -> list[Any]:
            return []

        def search_folders(self, name: str, parent_id: str) -> list[Any]:
            return []

        def create_folder(self, body: Any) -> dict[str, Any]:
            return {"id": "99", "name": body.name, "content_metadata_id": "199"}

        def content_metadata(self, content_metadata_id: str) -> dict[str, Any]:
            return {"id": content_metadata_id, "inherits": True}

        def update_content_metadata(self, content_metadata_id: str, body: Any) -> dict[str, Any]:
            self.cm_updates.append((content_metadata_id, body))
            return {"id": content_metadata_id, "inherits": False}

        def all_themes(self) -> list[Any]:
            return []

        def create_theme(self, body: Any) -> dict[str, Any]:
            return {"id": "theme-1", "name": getattr(body, "name", "")}

        def get_agent(self, agent_id: str) -> dict[str, Any]:
            return {"id": agent_id, "content_metadata_id": "299"}

    fake_sdk = _FakeSDK()
    monkeypatch.setattr(embed_scaffolder, "get_looker_sdk", lambda **_kw: fake_sdk)
    http_calls: list[tuple[str, str, Any]] = []

    class _FakeResp:
        def __init__(self, status_code: int = 200, data: Any = None) -> None:
            self.status_code = status_code
            self._data = data if data is not None else {}
            self.text = "OK"

        def json(self) -> Any:
            return self._data

    monkeypatch.setattr(
        embed_scaffolder.requests,
        "put",
        lambda url, json=None, **_kw: (http_calls.append(("PUT", url, json)), _FakeResp(200))[1],
    )
    monkeypatch.setattr(
        embed_scaffolder.requests,
        "get",
        lambda url, **_kw: _FakeResp(
            200,
            {"id": "1042", "content_metadata_id": "299"} if "/conversational_agents/" in url else [],
        ),
    )
    monkeypatch.setattr(
        embed_scaffolder.requests,
        "post",
        lambda url, json=None, **_kw: (http_calls.append(("POST", url, json)), _FakeResp(200))[1],
    )

    fake_cache = tmp_path / "cached_embed_repo"
    (fake_cache / "lookml").mkdir(parents=True)
    (fake_cache / "lookml" / "dummy.model.lkml").write_text("connection: 'dummy'\n", encoding="utf-8")
    (fake_cache / ".agent" / "skills" / "setup-embed-demo" / "scripts").mkdir(parents=True)
    (fake_cache / ".agent" / "skills" / "setup-embed-demo" / "scripts" / "2_project_setup.md").write_text(
        "dummy\n", encoding="utf-8"
    )
    (fake_cache / "backend" / "app").mkdir(parents=True)
    (fake_cache / "backend" / "app" / "models.py").write_text('group_ids: list[str] = ["8"]\n', encoding="utf-8")
    (fake_cache / "frontend" / "src" / "config").mkdir(parents=True)
    (fake_cache / "frontend" / "src" / "config" / "constants.ts").write_text(
        'dashboardId: "embed_demo::brand_overview",\nexploreId: "embed_demo/order_items",\n'
        'agentId: "ea1262d262ab43b1a9bb23152f25c236",\nfolderId: "12542",\ngroupIds: [\'8\'],\n',
        encoding="utf-8",
    )
    (fake_cache / "frontend" / "src" / "components" / "dialogs").mkdir(parents=True)
    (fake_cache / "frontend" / "src" / "components" / "dialogs" / "UserDetailsDialog.tsx").write_text(
        "const g = ['8'];\n", encoding="utf-8"
    )
    (fake_cache / "frontend" / "src" / "pages").mkdir(parents=True)
    (fake_cache / "frontend" / "src" / "pages" / "LoginPage.tsx").write_text(
        'const b = "Looker Embed (Levi\'s)";\n', encoding="utf-8"
    )
    monkeypatch.setattr(EmbedScaffolder, "_resolve_template_repo", classmethod(lambda _cls: fake_cache))
    monkeypatch.setattr(embed_scaffolder.shutil, "which", lambda _cmd: None)

    hydrated_dir = tmp_path / "hydrated_portal"
    full_opts = EmbedConfigOptions(
        demo_name="retail_analytics",
        target_dir=hydrated_dir,
        brand_name="Flag Brand",
        brand_title="Flag Brand Intelligence Portal",
        looker_instance_url="https://fake.cloud.looker.com",
        looker_project_name="retail_analytics",
        lookml_model_name="retail_model",
        dashboard_id="retail_model::dash-77",
        agent_id="1042",
        explore_path="retail_model/orders",
        client_id="sa-id-123",
        client_secret="sa-secret-456",
    )
    prov = provision_embed_instance(full_opts, headers={"Authorization": "Bearer tok"})
    EmbedScaffolder.scaffold_demo_workspace(full_opts)

    assert prov.brand_attribute_ensured and prov.allowlist_configured and prov.cookieless_enabled
    assert prov.group_id == "42" and prov.folder_id == "99"
    assert prov.dashboard_moved and prov.agent_shared
    assert prov.themes_created == ["Flag_Brand_Light", "Flag_Brand_Dark"]
    assert not (hydrated_dir / "lookml").exists()
    assert not (hydrated_dir / ".agent" / "skills" / "setup-embed-demo" / "scripts" / "2_project_setup.md").exists()
    assert 'group_ids: list[str] = ["42"]' in (hydrated_dir / "backend" / "app" / "models.py").read_text(
        encoding="utf-8"
    )
    constants_out = (hydrated_dir / "frontend" / "src" / "config" / "constants.ts").read_text(encoding="utf-8")
    assert 'folderId: "99"' in constants_out and 'groupIds: ["42"]' in constants_out


# ---------------------------------------------------------------------------
# --skip terminal branches & Gate 3C critique guard
# ---------------------------------------------------------------------------


def test_agent_publish_and_embed_skip_branches_and_critique_guard(invoke, state_file, isolated_cwd: Path):
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
