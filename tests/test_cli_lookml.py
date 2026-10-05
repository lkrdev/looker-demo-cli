"""Tests for the ``demo-create lookml`` command group."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import responses
from conftest import envelope, read_state

from looker_demo_cli.errors import (
    AuthError,
    ConfigError,
    RemoteApiError,
    StateError,
    ValidationError,
    no_looker_instance,
)

pytestmark = [pytest.mark.unit]

PROJECT_FILES_URL = "https://fake.cloud.looker.com/api/4.0/projects/retail_demo/files"


def lkml_tree(root: Path) -> set[str]:
    """POSIX-relative paths of every generated LookML artifact under ``root``."""
    return {
        str(p.relative_to(root).as_posix())
        for p in root.rglob("*")
        if p.is_file() and (p.name.endswith(".lkml") or p.name.endswith(".lookml"))
    }


@pytest.fixture
def spec_factory():
    """Build ``LookMLTableSpec`` objects."""
    from looker_demo_cli.generators.lookml_generator import LookMLTableSpec

    def _make(table_name: str, **overrides: Any) -> LookMLTableSpec:
        payload: dict[str, Any] = {
            "table_name": table_name,
            "table_type": "dimension",
            "schema_fields": {f"{table_name}_id": "INT64", "name": "STRING"},
            "primary_key": f"{table_name}_id",
            "foreign_keys": {},
        }
        payload.update(overrides)
        return LookMLTableSpec(**payload)

    return _make


@pytest.fixture
def stub_introspection(patch_cli):
    """Replace ``introspect_bq_table_specs`` and record its keyword args."""
    calls: list[dict[str, Any]] = []

    def _install(return_value: list[Any]):
        def _fake(**kwargs: Any) -> list[Any]:
            calls.append(kwargs)
            return list(return_value)

        patch_cli("introspect_bq_table_specs", _fake)
        return calls

    return _install


@pytest.fixture
def stub_deploy_step(patch_cli):
    """Replace ``deploy_lookml_project`` with a recording double."""
    seen: dict[str, Any] = {}

    def _install(**mutations: Any) -> dict[str, Any]:
        def _fake(state):
            seen["state_in"] = state.model_copy(deep=True)
            for key, value in mutations.items():
                setattr(state, key, value)
            return state

        patch_cli("deploy_lookml_project", _fake)
        return seen

    return _install


@pytest.fixture
def stub_oauth_instances(monkeypatch: pytest.MonkeyPatch):
    """Point the deploy step's OAuth lookup at a single fake instance."""
    base_url = "https://fake.cloud.looker.com"
    monkeypatch.setattr(
        "looker_demo_cli.services.deploy_service.get_authenticated_oauth_instances",
        lambda: [
            {
                "instance_name": "demo-instance",
                "base_url": base_url,
                "is_current": True,
                "access_token": "fake-token",
            }
        ],
    )
    return base_url


# ---------------------------------------------------------------------------
# lookml optimize & restore
# ---------------------------------------------------------------------------


def test_optimize_and_restore_round_trip(invoke, sample_lookml_dir: Path):
    """Optimize patches LookML, creates snapshot, and restore rolls back byte-identically."""
    view = sample_lookml_dir / "views" / "users.view.lkml"
    model = sample_lookml_dir / "models" / "demo.model.lkml"
    view_before = view.read_bytes()
    model_before = model.read_bytes()

    opt = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--json"])
    assert opt.exit_code == 0, opt.output
    opt_payload = envelope(opt)
    assert opt_payload["status"] == "SUCCESS"
    assert opt_payload["data"]["backup_created"] is True
    assert sorted(opt_payload["data"]["files_patched"]) == ["models/demo.model.lkml", "views/users.view.lkml"]
    assert opt_payload["next_actions"][0]["command"] == f"demo-create lookml restore --lookml-dir {sample_lookml_dir}"
    assert view.read_bytes() != view_before

    restore = invoke(["lookml", "restore", "--lookml-dir", str(sample_lookml_dir), "--json"])
    assert restore.exit_code == 0, restore.output
    res_payload = envelope(restore)
    assert sorted(res_payload["data"]["files_restored"]) == ["models/demo.model.lkml", "views/users.view.lkml"]
    assert not (sample_lookml_dir / ".backup_pre_opt").exists()
    assert view.read_bytes() == view_before
    assert model.read_bytes() == model_before


def test_optimize_and_restore_human_output_and_state_fallback(invoke, sample_lookml_dir: Path, state_file):
    """Human mode renders summary tables and falls back to state lookml_output_dir."""
    state_file(lookml_output_dir=str(sample_lookml_dir))

    opt = invoke(["lookml", "optimize"])
    assert opt.exit_code == 0
    assert "Optimization Summary" in opt.output

    restore = invoke(["lookml", "restore"])
    assert restore.exit_code == 0
    assert "Successfully restored 2 LookML file(s)" in restore.output


def test_optimize_no_backup_and_error_paths(invoke, sample_lookml_dir: Path, tmp_path: Path):
    """Covers --no-backup, missing directory ConfigError, and missing snapshot StateError."""
    no_bak = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--no-backup", "--json"])
    assert no_bak.exit_code == 0
    assert envelope(no_bak)["data"]["backup_created"] is False
    assert envelope(no_bak)["next_actions"] == []

    missing_res = invoke(["lookml", "restore", "--lookml-dir", str(sample_lookml_dir), "--json"])
    assert missing_res.exit_code == StateError.exit_code
    assert envelope(missing_res)["errors"][0]["code"] == "STATE_ERROR"

    missing_dir = invoke(["lookml", "optimize", "--lookml-dir", str(tmp_path / "nope"), "--json"])
    assert missing_dir.exit_code == ConfigError.exit_code
    assert envelope(missing_dir)["errors"][0]["code"] == "CONFIG_ERROR"


# ---------------------------------------------------------------------------
# lookml model
# ---------------------------------------------------------------------------


def test_model_from_parquet_writes_expected_tree_and_state(
    invoke, sample_parquet_dir: Path, tmp_path: Path, isolated_cwd: Path
):
    """Generates views, model, and dashboard from Parquet and persists state."""
    out = tmp_path / "generated"
    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--dataset",
            "retail_raw",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )

    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["data"]["source"] == "parquet"
    assert sorted(payload["data"]["tables"]) == ["dim_products", "dim_users", "fct_orders"]
    assert payload["next_actions"][0]["gate"] == 6
    assert lkml_tree(out) == {
        "views/dim_products.view.lkml",
        "views/dim_users.view.lkml",
        "views/fct_orders.view.lkml",
        "models/retail_demo.model.lkml",
        "dashboards/retail_demo_overview.dashboard.lookml",
    }
    state = read_state(isolated_cwd)
    assert state["looker_project_name"] == "retail_demo"
    assert state["primary_explore_name"] == "fct_orders"
    assert state["bq_dataset_id"] == "retail_raw"
    assert state["looker_connection_name"] == "default_bigquery_connection"


def test_model_human_output_table_filter_and_dataset_warning(invoke, sample_parquet_dir: Path, tmp_path: Path):
    """Human mode renders summary, warns when --dataset is omitted, and respects --tables."""
    out = tmp_path / "generated"
    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
            "--tables",
            "dim_users, fct_orders",
        ]
    )

    assert result.exit_code == 0, result.output
    assert "No --dataset given" in result.output
    assert "Generated 4 LookML files" in result.output
    assert {p for p in lkml_tree(out) if p.startswith("views/")} == {
        "views/dim_users.view.lkml",
        "views/fct_orders.view.lkml",
    }


def test_model_bigquery_introspection_and_parquet_fallback(
    invoke, stub_introspection, spec_factory, sample_parquet_dir: Path, tmp_path: Path
):
    """--dataset introspects BigQuery first and falls back to Parquet when empty."""
    calls = stub_introspection([spec_factory("orders"), spec_factory("customers")])
    out_bq = tmp_path / "bq_gen"
    res_bq = invoke(
        [
            "lookml",
            "model",
            "--dataset",
            "retail_raw",
            "--output-dir",
            str(out_bq),
            "--looker-project",
            "retail_demo",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )
    assert res_bq.exit_code == 0
    assert len(calls) == 1
    assert envelope(res_bq)["data"]["source"] == "bigquery"

    stub_introspection([])
    out_fb = tmp_path / "fb_gen"
    res_fb = invoke(
        [
            "lookml",
            "model",
            "--dataset",
            "retail_raw",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(out_fb),
            "--looker-project",
            "retail_demo",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )
    assert res_fb.exit_code == 0
    assert envelope(res_fb)["data"]["source"] == "parquet"


def test_model_missing_connection_or_tables_raises_config_error(invoke, sample_parquet_dir: Path, tmp_path: Path):
    """Missing --connection or empty source tables exits with ConfigError."""
    no_conn = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(tmp_path / "out1"),
            "--looker-project",
            "retail_demo",
            "--json",
        ]
    )
    assert no_conn.exit_code == ConfigError.exit_code
    assert envelope(no_conn)["errors"][0]["details"] == {"option": "--connection"}

    no_tables = invoke(
        [
            "lookml",
            "model",
            "--output-dir",
            str(tmp_path / "out2"),
            "--looker-project",
            "empty_demo",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )
    assert no_tables.exit_code == ConfigError.exit_code
    assert envelope(no_tables)["errors"][0]["message"] == "No tables found to model."


# ---------------------------------------------------------------------------
# lookml deploy
# ---------------------------------------------------------------------------


def test_deploy_missing_dir_is_a_config_error(invoke, tmp_path: Path):
    """Missing LookML directory raises ConfigError."""
    result = invoke(["lookml", "deploy", "--lookml-dir", str(tmp_path / "nope"), "--json"])
    assert result.exit_code == ConfigError.exit_code
    assert envelope(result)["errors"][0]["code"] == "CONFIG_ERROR"


def test_deploy_end_to_end_success_and_validator_failure(
    invoke,
    sample_lookml_dir: Path,
    fake_shell,
    isolated_cwd: Path,
    stub_oauth_instances: str,
    state_file,
):
    """End-to-end deploy succeeds when validator is clean and exits ValidationError on validator errors."""
    state_file(looker_connection_name="default_bigquery_connection")
    base_url = stub_oauth_instances

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.PATCH, f"{base_url}/api/4.0/session", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/projects/retail_demo", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/lookml_models/retail_demo", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/projects/retail_demo/files", json=[], status=200)
        rsps.add(responses.POST, f"{base_url}/api/4.0/projects/retail_demo/validate", json={"errors": []}, status=200)

        ok = invoke(
            [
                "lookml",
                "deploy",
                "--lookml-dir",
                str(sample_lookml_dir),
                "--looker-project",
                "retail_demo",
                "--looker-account",
                "demo-instance",
                "--json",
            ]
        )

    assert ok.exit_code == 0, ok.output
    assert envelope(ok)["data"]["dashboard_url"] == f"{base_url}/dashboards/retail_demo::retail_demo_overview"
    fake_shell.assert_called_with_substring("tools lookml push")
    fake_shell.assert_called_with_substring("tools lookml deploy")
    assert read_state(isolated_cwd)["status"] == "completed"

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.PATCH, f"{base_url}/api/4.0/session", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/projects/retail_demo", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/lookml_models/retail_demo", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/projects/retail_demo/files", json=[], status=200)
        rsps.add(
            responses.POST,
            f"{base_url}/api/4.0/projects/retail_demo/validate",
            json={"errors": [{"file_path": "views/users.view.lkml", "line_number": 4, "message": "Unknown field"}]},
            status=200,
        )

        fail = invoke(["lookml", "deploy", "--lookml-dir", str(sample_lookml_dir), "--looker-project", "retail_demo"])

    assert fail.exit_code == ValidationError.exit_code
    assert "LookML validation failed with 1 error(s)" in fail.output
    assert read_state(isolated_cwd)["status"] == "failed"


# ---------------------------------------------------------------------------
# lookml clean-root
# ---------------------------------------------------------------------------


def test_clean_root_auth_and_config_guards(invoke, unauthenticated_looker, fake_looker, install_looker_auth):
    """Unauthenticated fails with AuthError; missing project fails with ConfigError without network call."""
    install_looker_auth(unauthenticated_looker)
    unauth = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])
    assert unauth.exit_code == AuthError.exit_code
    assert envelope(unauth)["errors"][0]["message"] == no_looker_instance().message

    install_looker_auth(fake_looker)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        no_proj = invoke(["lookml", "clean-root"])
        assert len(rsps.calls) == 0
    assert no_proj.exit_code == ConfigError.exit_code


def test_clean_root_clean_dry_run_and_delete_with_fallback(invoke, fake_looker, install_looker_auth):
    """Covers clean root, --dry-run, and duplicate deletion with URL-path fallback."""
    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(responses.GET, PROJECT_FILES_URL, json=[{"path": "views/users.view.lkml"}], status=200)
        clean = invoke(["lookml", "clean-root", "--looker-project", "retail_demo"])
    assert clean.exit_code == 0
    assert "has a clean root directory structure" in clean.output

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(
            responses.GET,
            PROJECT_FILES_URL,
            json=[{"path": "users.view.lkml"}, {"path": "views/users.view.lkml"}],
            status=200,
        )
        dry = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--dry-run", "--json"])
    assert dry.exit_code == 0
    assert envelope(dry)["status"] == "DRY_RUN"
    assert envelope(dry)["data"]["cleaned_files"] == ["users.view.lkml"]

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(
            responses.GET,
            PROJECT_FILES_URL,
            json=[{"path": "users.view.lkml"}, {"path": "views/users.view.lkml"}],
            status=200,
        )
        rsps.add(responses.DELETE, PROJECT_FILES_URL, json={}, status=422)
        rsps.add(responses.DELETE, f"{PROJECT_FILES_URL}/users.view.lkml", json={}, status=200)
        deleted = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])
    assert deleted.exit_code == 0
    assert envelope(deleted)["data"]["total_deleted"] == 1


def test_clean_root_partial_and_api_failures(invoke, fake_looker, install_looker_auth):
    """Partial deletion and HTTP 500 on file listing both exit with RemoteApiError."""
    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(
            responses.GET,
            PROJECT_FILES_URL,
            json=[{"path": "users.view.lkml"}, {"path": "views/users.view.lkml"}],
            status=200,
        )
        rsps.add(responses.DELETE, PROJECT_FILES_URL, json={}, status=500)
        rsps.add(responses.DELETE, f"{PROJECT_FILES_URL}/users.view.lkml", json={}, status=500)
        partial = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])
    assert partial.exit_code == RemoteApiError.exit_code
    assert envelope(partial)["status"] == "PARTIAL"

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(responses.GET, PROJECT_FILES_URL, body="boom", status=500)
        api_err = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])
    assert api_err.exit_code == RemoteApiError.exit_code
    assert envelope(api_err)["status"] == "FAILED"


# ---------------------------------------------------------------------------
# lookml certify-polish, optimize --skip, deploy guards & approve-critique
# ---------------------------------------------------------------------------


def test_certify_polish_optimize_skip_deploy_guards_and_approve_critique(
    invoke, sample_lookml_dir: Path, state_file, isolated_cwd: Path, tmp_path: Path
):
    """Covers Gate 2B certify-polish, Gate 3A optimize --skip, Gate 3B deploy StateError guards, and Gate 3C approve-critique."""
    # Missing directory -> ConfigError
    assert (
        invoke(["lookml", "certify-polish", "--lookml-dir", str(tmp_path / "missing"), "--json"]).exit_code
        == ConfigError.exit_code
    )

    # Stateful pipeline: precheck_passed=True requires certify-polish and optimize before deploy
    state_file(precheck_passed=True, lookml_output_dir=str(sample_lookml_dir))
    unpolished = invoke(["lookml", "deploy", "--json"])
    assert unpolished.exit_code == StateError.exit_code
    assert "Gate 2B" in envelope(unpolished)["errors"][0]["message"]

    # Gate 2B: certify-polish
    cert = invoke(["lookml", "certify-polish", "--lookml-dir", str(sample_lookml_dir), "--json"])
    assert cert.exit_code == 0, cert.output
    cert_payload = envelope(cert)
    assert cert_payload["data"]["certified"] is True
    assert cert_payload["next_actions"][0]["gate"] == 7
    assert read_state(isolated_cwd)["polish_certified"] is True

    # Still blocked at deploy until Gate 3A (optimize or optimize --skip) is resolved
    unoptimized = invoke(["lookml", "deploy", "--json"])
    assert unoptimized.exit_code == StateError.exit_code
    assert "Gate 3A" in envelope(unoptimized)["errors"][0]["message"]

    # Gate 3A: optimize --skip
    skip_opt = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--skip", "--json"])
    assert skip_opt.exit_code == 0, skip_opt.output
    skip_payload = envelope(skip_opt)
    assert skip_payload["data"]["skipped"] is True
    assert skip_payload["next_actions"][0]["gate"] == 8
    assert read_state(isolated_cwd)["optimizer_status"] == "skipped"

    # Gate 3C: approve-critique requires deployed_dashboard_url
    no_deploy = invoke(["lookml", "approve-critique", "--json"])
    assert no_deploy.exit_code == StateError.exit_code

    state_file(
        precheck_passed=True,
        looker_project_name="retail_demo",
        deployed_dashboard_url="https://fake.cloud.looker.com/dashboards/42",
    )
    critique = invoke(["lookml", "approve-critique", "--notes", "Looks great", "--json"])
    assert critique.exit_code == 0, critique.output
    critique_payload = envelope(critique)
    assert critique_payload["data"]["critique_approved"] is True
    assert critique_payload["next_actions"][0]["gate"] == 10
    assert read_state(isolated_cwd)["critique_approved"] is True


def test_certify_polish_blocks_raw_scaffold_and_allows_force_or_custom(
    invoke, sample_parquet_dir: Path, tmp_path: Path, isolated_cwd: Path
):
    """Gate 2B certify-polish rejects uncustomized LookMLGenerator scaffolding with FAILED_POLISH_CHECK and agent_guidance."""
    out = tmp_path / "scaffold_lookml"
    gen_res = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(out),
            "--looker-project",
            "gaming_demo",
            "--dataset",
            "gaming_raw",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )
    assert gen_res.exit_code == 0, gen_res.output

    # 1. Running certify-polish immediately on raw LookMLGenerator output fails with FAILED_POLISH_CHECK (exit code 6)
    blocked_json = invoke(["lookml", "certify-polish", "--lookml-dir", str(out), "--json"])
    assert blocked_json.exit_code == ValidationError.exit_code
    payload = envelope(blocked_json)
    assert payload["status"] == "FAILED_POLISH_CHECK"
    assert payload["data"]["certified"] is False
    assert len(payload["data"]["scaffolding_issues"]) > 0
    guidance = payload["data"]["agent_guidance"]
    assert guidance["action_required"] == "CONSULT_VISUALIZATION_SKILLS"
    assert "skills/looker-visualizations/SKILL.md" in guidance["recommended_skills"]
    assert "lookml-dashboard-designer.md" in guidance["subagent_command"]["type"]
    assert read_state(isolated_cwd)["polish_certified"] is False

    # 2. Human mode outputs actionable Rich guidance panel
    blocked_human = invoke(["lookml", "certify-polish", "--lookml-dir", str(out)])
    assert blocked_human.exit_code == ValidationError.exit_code
    assert "ACTION REQUIRED: DASHBOARD CONTAINS UNCUSTOMIZED SCAFFOLDING DRAFT" in blocked_human.output
    assert "lookml-dashboard-designer" in blocked_human.output

    # 3. --force-scaffold overrides scaffolding check when explicitly requested
    forced = invoke(["lookml", "certify-polish", "--lookml-dir", str(out), "--force-scaffold", "--json"])
    assert forced.exit_code == 0, forced.output
    forced_payload = envelope(forced)
    assert forced_payload["status"] == "SUCCESS"
    assert forced_payload["data"]["certified"] is True
    assert forced_payload["data"]["force_scaffold"] is True
    assert read_state(isolated_cwd)["polish_certified"] is True
