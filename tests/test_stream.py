"""Hermetic unit tests for ``demo-create stream {plan,deploy,delete}``."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from conftest import envelope, read_state

from looker_demo_cli.commands.stream import COST_NOTICE
from looker_demo_cli.errors import ConfigError, RemoteApiError, ValidationError

pytestmark = [pytest.mark.unit]


def test_stream_plan_skip_completes_gate_14_and_includes_cost_notice(invoke, state_file, isolated_cwd: Path) -> None:
    """`demo-create stream plan --skip --json` marks stream_status='skipped', completes Gate 14, and emits COST_NOTICE."""
    state_file(
        precheck_passed=True,
        targets_confirmed=True,
        gcp_project_id="my-gcp-project",
        bq_dataset_id="thelook_demo",
        dataset_exists=True,
        catalog_status="seeded",
        deployment_mode="bq_only",
    )

    res = invoke(["stream", "plan", "--skip", "--json"])
    assert res.exit_code == 0, res.output
    body = envelope(res)
    assert body["command"] == "stream plan"
    assert body["status"] == "SUCCESS"
    assert body["data"]["skipped"] is True
    assert body["data"]["stream_status"] == "skipped"
    assert body["data"]["cost_notice"] == COST_NOTICE
    assert COST_NOTICE in body["warnings"]
    assert body["next_actions"] == []

    persisted = read_state(isolated_cwd)
    assert persisted["stream_status"] == "skipped"
    assert persisted["stream_planned"] is False


def test_stream_plan_full_config_writes_plan_and_updates_spec_section_4b(
    invoke,
    state_file,
    isolated_cwd: Path,
) -> None:
    """`demo-create stream plan` writes `.demo-stream-plan.json`, updates `SPEC.md` Section 4B, and enforces locked-down GCE."""
    state_file(
        precheck_passed=True,
        targets_confirmed=True,
        gcp_project_id="default-proj",
        bq_dataset_id="default_ds",
        dataset_exists=True,
        catalog_status="seeded",
        deployment_mode="bq_only",
    )
    spec_path = isolated_cwd / "SPEC.md"
    spec_path.write_text("# Demo Spec\n\n## Section 1: Overview\nExisting content.\n", encoding="utf-8")

    res = invoke(
        [
            "stream",
            "plan",
            "--gcp-project",
            "acme-prod",
            "--dataset",
            "retail_ops",
            "--frequency",
            "5m",
            "--crud-mode",
            "full_crud",
            "--backfill-mode",
            "local",
            "--target-table",
            "fct_orders",
            "--log-name",
            "custom-log",
            "--log-severity",
            "DEBUG",
            "--zone",
            "us-east1-b",
            "--json",
        ]
    )
    assert res.exit_code == 0, res.output
    body = envelope(res)
    data = body["data"]

    assert data["project_id"] == "acme-prod"
    assert data["dataset_id"] == "retail_ops"
    assert data["target_table"] == "fct_orders"
    assert data["frequency"] == "5m"
    assert data["crud_mode"] == "full_crud"
    assert data["backfill_mode"] == "local"
    assert data["log_name"] == "custom-log"
    assert data["log_severity"] == "DEBUG"
    assert data["zone"] == "us-east1-b"
    assert data["vm_name"] == "retail-ops-bq-streamer"
    assert data["stream_status"] == "planned"
    assert data["cost_notice"] == COST_NOTICE
    assert COST_NOTICE in body["warnings"]

    # Locked-down GCE security assertions
    net_sec = data["network_security"]
    assert net_sec["public_ip"] is False
    assert "--no-address" in net_sec["gce_flags"]
    assert "--enable-private-ip-google-access" in net_sec["subnet_flags"]
    assert "--tunnel-through-iap" in net_sec["ssh_scp_flags"]
    assert net_sec["machine_type"] == "e2-micro"

    # 4-phase plan and 4 subagent roles
    assert len(data["execution_phases"]) == 4
    agent_ids = [a["id"] for a in data["agents"]]
    assert agent_ids == [
        "bq-profiling-researcher",
        "catalog-spec-architect",
        "go-bq-cdc-engineer",
        "go-sim-cli-engineer",
    ]

    # Next action points to stream deploy
    assert len(body["next_actions"]) == 1
    assert body["next_actions"][0]["gate"] == 14
    assert (
        body["next_actions"][0]["command"] == "demo-create stream deploy --gcp-project acme-prod --dataset retail_ops"
    )

    # Plan file written on disk
    plan_file = isolated_cwd / ".demo-stream-plan.json"
    assert plan_file.exists()
    plan_json = json.loads(plan_file.read_text(encoding="utf-8"))
    assert plan_json["crud_mode"] == "full_crud"
    assert plan_json["network_security"]["public_ip"] is False

    # SPEC.md updated with Section 4B and COST_NOTICE while preserving Section 1
    spec_text = spec_path.read_text(encoding="utf-8")
    assert "## Section 1: Overview" in spec_text
    assert "## Section 4B: Real-Time Streaming & CDC Architecture" in spec_text
    assert "acme-prod.retail_ops.fct_orders" in spec_text
    assert "--no-address" in spec_text
    assert "--tunnel-through-iap" in spec_text
    assert COST_NOTICE in spec_text

    # State persisted
    persisted = read_state(isolated_cwd)
    assert persisted["stream_status"] == "planned"
    assert persisted["stream_planned"] is True
    assert persisted["stream_frequency"] == "5m"
    assert persisted["stream_crud_mode"] == "full_crud"
    assert persisted["stream_backfill_mode"] == "local"
    assert persisted["stream_target_table"] == "fct_orders"
    assert persisted["stream_log_name"] == "custom-log"
    assert persisted["stream_zone"] == "us-east1-b"


@pytest.mark.parametrize(
    ("extra_args", "expected_code"),
    [
        (["--gcp-project", "p", "--dataset", "d", "--crud-mode", "invalid_mode"], ConfigError.exit_code),
        (["--gcp-project", "p", "--dataset", "d", "--backfill-mode", "bad_backfill"], ConfigError.exit_code),
        (["--gcp-project", "p", "--dataset", "d", "--log-severity", "TRACE"], ConfigError.exit_code),
        (["--gcp-project", "p", "--dataset", "d", "--frequency", "invalid"], ConfigError.exit_code),
        (["--gcp-project", "", "--dataset", "d"], ConfigError.exit_code),
        (["--gcp-project", "p"], ConfigError.exit_code),
    ],
)
def test_stream_plan_validation_errors(invoke, state_file, extra_args: list[str], expected_code: int) -> None:
    """`demo-create stream plan` raises ConfigError (exit 4) on invalid enum flags or missing project/dataset."""
    state_file(gcp_project_id="", bq_dataset_id=None, domain_name=None)
    res = invoke(["stream", "plan", *extra_args, "--json"])
    assert res.exit_code == expected_code
    body = envelope(res)
    assert body["status"] == "FAILED"
    assert body["errors"][0]["code"] == "CONFIG_ERROR"


def test_stream_deploy_scaffold_only_materializes_templates_and_keeps_planned_state(
    invoke,
    state_file,
    isolated_cwd: Path,
) -> None:
    """`demo-create stream deploy --scaffold-only --json` renders Go files, strips .tmpl, marks deploy-bq.sh executable, and emits 4 agent_briefs."""
    state_file(
        gcp_project_id="acme-prod",
        bq_dataset_id="retail_ops",
        stream_status="planned",
        stream_planned=True,
        stream_frequency="5m",
        stream_crud_mode="upsert_cdc",
        stream_backfill_mode="local",
        stream_target_table="fct_orders",
        stream_log_name="retail-ops-streamer",
        stream_vm_name="retail-ops-bq-streamer",
        stream_zone="us-central1-a",
    )

    out_dir = isolated_cwd / "custom_streamer"
    res = invoke(
        [
            "stream",
            "deploy",
            "--scaffold-only",
            "--output-dir",
            str(out_dir),
            "--json",
        ]
    )
    assert res.exit_code == 0, res.output
    body = envelope(res)
    data = body["data"]

    assert data["scaffold_only"] is True
    assert data["execute_infra"] is False
    assert data["stream_status"] == "planned"
    assert data["stream_deployed"] is False
    assert data["cost_notice"] == COST_NOTICE

    # Check 4 agent briefs
    brief_ids = [b["id"] for b in data["agent_briefs"]]
    assert brief_ids == [
        "bq-profiling-researcher",
        "catalog-spec-architect",
        "go-bq-cdc-engineer",
        "go-sim-cli-engineer",
    ]

    # Check scaffolded files exist, .tmpl stripped, placeholders replaced, and deploy-bq.sh executable
    expected_files = (
        "go.mod",
        "main.go",
        "sim.go",
        "bq.go",
        "bq_test.go",
        "schema.sql",
        "deploy-bq.sh",
        "retail-ops-streamer.service",
    )
    for fname in expected_files:
        fpath = out_dir / fname
        assert fpath.exists(), f"Missing scaffolded file: {fpath}"
        content = fpath.read_text(encoding="utf-8")
        assert "{{GCP_PROJECT}}" not in content
        assert "{{BQ_DATASET}}" not in content
        assert "{{TARGET_TABLE}}" not in content
        assert "{{SERVICE_NAME}}" not in content
        assert "{{SERVICE_FILE}}" not in content
    assert not (out_dir / "thelook.service").exists()
    assert not list(out_dir.glob("*.tmpl"))

    deploy_sh = out_dir / "deploy-bq.sh"
    assert os.access(deploy_sh, os.X_OK)
    deploy_sh_text = deploy_sh.read_text(encoding="utf-8")
    assert "--no-address" in deploy_sh_text
    assert "--enable-private-ip-google-access" in deploy_sh_text
    assert "--tunnel-through-iap" in deploy_sh_text

    # Next action points to --execute-infra
    assert len(body["next_actions"]) == 1
    assert "--execute-infra" in body["next_actions"][0]["command"]

    persisted = read_state(isolated_cwd)
    assert persisted["stream_status"] == "planned"
    assert persisted["stream_deployed"] is False


def test_stream_deploy_execute_infra_dry_run_and_mocked_shell(
    invoke,
    state_file,
    fake_shell,
    isolated_cwd: Path,
) -> None:
    """`demo-create stream deploy --execute-infra` validates locked-down GCE commands, verification SQL, and marks Gate 14 deployed."""
    state_file(
        precheck_passed=True,
        targets_confirmed=True,
        gcp_project_id="acme-prod",
        bq_dataset_id="retail_ops",
        dataset_exists=True,
        catalog_status="seeded",
        deployment_mode="bq_only",
        stream_status="planned",
        stream_planned=True,
        stream_target_table="fct_orders",
    )

    res = invoke(["stream", "deploy", "--execute-infra", "--dry-run", "--json"])
    assert res.exit_code == 0, res.output
    body = envelope(res)
    data = body["data"]

    assert data["execute_infra"] is True
    assert data["dry_run"] is True
    assert data["stream_status"] == "deployed"
    assert data["stream_deployed"] is True
    assert data["cost_notice"] == COST_NOTICE
    assert data["teardown_command"] == "demo-create stream delete --gcp-project acme-prod --dataset retail_ops"

    infra_joined = "\n".join(data["infra_commands"])
    assert "--no-address" in infra_joined
    assert "--enable-private-ip-google-access" in infra_joined
    assert "--tunnel-through-iap" in infra_joined
    assert "--public-ip" not in infra_joined

    sql_joined = "\n".join(data["verification_sql"])
    assert "PRIMARY KEY" in sql_joined
    assert "_CHANGE_TYPE" in sql_joined
    assert "max_staleness" in sql_joined
    assert "COUNT(*)" in sql_joined

    # Gate 14 is now complete so next_actions is empty
    assert body["next_actions"] == []

    # Also test live execution path with fake_shell
    res_live = invoke(["stream", "deploy", "--execute-infra", "--json"])
    assert res_live.exit_code == 0, res_live.output
    fake_shell.assert_called_with_substring("go test ./...")
    fake_shell.assert_called_with_substring("./deploy-bq.sh")


def test_stream_deploy_surfaces_go_test_and_deploy_script_failures(
    invoke,
    state_file,
    fake_shell,
) -> None:
    """`stream deploy --execute-infra` raises ValidationError on `go test` failure and RemoteApiError on `deploy-bq.sh` failure."""
    state_file(gcp_project_id="acme-prod", bq_dataset_id="retail_ops", stream_status="planned", stream_planned=True)

    fake_shell.stub("go test", returncode=1, stderr="FAIL: TestSimulatorAndFlush")
    res_test_fail = invoke(["stream", "deploy", "--execute-infra", "--json"])
    assert res_test_fail.exit_code == ValidationError.exit_code
    assert envelope(res_test_fail)["errors"][0]["code"] == "VALIDATION_ERROR"

    fake_shell.stub("go test", returncode=0)
    fake_shell.stub("deploy-bq.sh", returncode=1, stderr="ERROR: IAP tunnel failed")
    res_deploy_fail = invoke(["stream", "deploy", "--execute-infra", "--json"])
    assert res_deploy_fail.exit_code == RemoteApiError.exit_code
    assert envelope(res_deploy_fail)["errors"][0]["code"] == "REMOTE_API_ERROR"


@pytest.mark.parametrize(
    ("flag", "expected_bq_action", "expected_substr"),
    [
        ("--keep-bq", "kept", None),
        ("--drop-bq-table", "dropped_table", "DROP TABLE IF EXISTS `acme-prod.retail_ops.fct_orders`;"),
        ("--drop-bq-dataset", "dropped_dataset", "bq rm -r -f -d acme-prod:retail_ops"),
    ],
)
def test_stream_delete_retention_modes(
    invoke,
    state_file,
    isolated_cwd: Path,
    flag: str,
    expected_bq_action: str,
    expected_substr: str | None,
) -> None:
    """`demo-create stream delete` supports `--keep-bq`, `--drop-bq-table`, and `--drop-bq-dataset`."""
    state_file(
        gcp_project_id="acme-prod",
        bq_dataset_id="retail_ops",
        dataset_exists=True,
        stream_status="deployed",
        stream_deployed=True,
        stream_target_table="fct_orders",
        stream_vm_name="retail-ops-bq-streamer",
        stream_zone="us-central1-a",
    )

    res = invoke(["stream", "delete", flag, "--dry-run", "--json"])
    assert res.exit_code == 0, res.output
    data = envelope(res)["data"]

    assert data["bq_action"] == expected_bq_action
    cmds_joined = "\n".join(data["teardown_commands"])
    assert "systemctl disable --now retail-ops-streamer.service" in cmds_joined
    assert "gcloud compute instances delete retail-ops-bq-streamer" in cmds_joined
    assert "gcloud secrets delete retail_ops-streamer-secret" in cmds_joined

    if expected_substr is not None:
        assert expected_substr in cmds_joined
    else:
        assert "DROP TABLE" not in cmds_joined
        assert "bq rm" not in cmds_joined

    persisted = read_state(isolated_cwd)
    assert persisted["stream_deployed"] is False
    assert persisted["stream_status"] == "skipped"
    if flag == "--drop-bq-dataset":
        assert persisted["dataset_exists"] is False
    else:
        assert persisted["dataset_exists"] is True


def test_stream_delete_rejects_mutually_exclusive_flags(invoke, state_file) -> None:
    """`demo-create stream delete` raises ConfigError when multiple BigQuery retention flags are passed."""
    state_file(gcp_project_id="acme-prod", bq_dataset_id="retail_ops")
    res = invoke(["stream", "delete", "--keep-bq", "--drop-bq-table", "--dry-run", "--json"])
    assert res.exit_code == ConfigError.exit_code
    body = envelope(res)
    assert body["status"] == "FAILED"
    assert body["errors"][0]["code"] == "CONFIG_ERROR"


def test_materialized_go_template_compiles_and_passes_go_test(tmp_path: Path) -> None:
    """Materializing `looker_demo_cli/templates/go_streamer/` compiles cleanly and passes `go test ./...`."""
    import shutil
    import subprocess

    from looker_demo_cli.commands.stream import _materialize_go_template

    if shutil.which("go") is None:
        pytest.skip("go binary not available")

    out_dir = tmp_path / "streamer"
    _materialize_go_template(
        out_dir,
        tokens={
            "{{MODULE_NAME}}": "demo-streamer",
            "{{GCP_PROJECT}}": "acme-prod",
            "{{BQ_DATASET}}": "retail_ops",
            "{{TARGET_TABLE}}": "order_items",
            "{{STREAM_INTERVAL}}": "1m",
            "{{CRUD_MODE}}": "upsert_cdc",
            "{{BACKFILL_MODE}}": "skip",
            "{{LOG_NAME}}": "retail-ops-streamer",
            "{{LOG_SEVERITY}}": "INFO",
            "{{VM_NAME}}": "retail-ops-bq-streamer",
            "{{GCP_ZONE}}": "us-central1-a",
        },
        overwrite=True,
    )
    proc = subprocess.run(
        ["go", "test", "./..."],
        cwd=out_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"go test failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
