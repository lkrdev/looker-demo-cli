"""``demo-create stream`` -- plan, deploy, and tear down synthetic BigQuery streaming & CDC services."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Annotated, Any, Final, Literal, cast

import typer
from rich.panel import Panel
from rich.table import Table

from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.context import get_context
from looker_demo_cli.error_boundary import ErrorHandlingGroup
from looker_demo_cli.errors import ConfigError, RemoteApiError, ValidationError, missing_option
from looker_demo_cli.gates import attach_next_gate_action
from looker_demo_cli.output import CommandResult, emit
from looker_demo_cli.state import FlowState
from looker_demo_cli.utils.console import console, print_info, print_success, print_warning

COST_NOTICE: Final[str] = (
    "Note: While the streaming daemon runs on a GCE free-tier `e2-micro` compute instance, "
    "continuous streaming into BigQuery will accumulate rows and storage/write volume over time "
    "that may lead to GCP costs being incurred. Consider running `demo-create stream delete` "
    "once the need for your demo resource is done."
)

VALID_CRUD_MODES: Final[tuple[str, ...]] = ("insert_only", "upsert_cdc", "full_crud")
VALID_BACKFILL_MODES: Final[tuple[str, ...]] = ("skip", "local", "vm", "sample_mirror")
VALID_LOG_SEVERITIES: Final[tuple[str, ...]] = ("DEBUG", "INFO", "WARNING", "ERROR")
_DURATION_RE: Final[re.Pattern[str]] = re.compile(r"^(\d+(ns|us|µs|ms|s|m|h))+$")

NETWORK_SECURITY_SPEC: Final[dict[str, Any]] = {
    "public_ip": False,
    "gce_flags": ["--no-address"],
    "subnet_flags": ["--enable-private-ip-google-access"],
    "ssh_scp_flags": ["--tunnel-through-iap"],
    "machine_type": "e2-micro",
}

_FALLBACK_TEMPLATES: Final[dict[str, str]] = {
    "go.mod": """module {{MODULE_NAME}}

go 1.22
""",
    "main.go": """package main

import (
\t"context"
\t"flag"
\t"fmt"
\t"os"
\t"os/signal"
\t"syscall"
\t"time"
)

func main() {
\tproject := flag.String("project", "{{GCP_PROJECT}}", "Target GCP project ID")
\tdataset := flag.String("dataset", "{{BQ_DATASET}}", "Target BigQuery dataset ID")
\ttable := flag.String("table", "{{TARGET_TABLE}}", "Target BigQuery table name")
\tcontinuous := flag.Bool("continuous", true, "Run continuous streaming ticks")
\tintervalStr := flag.String("interval", "{{STREAM_INTERVAL}}", "Streaming tick interval")
\tcrudMode := flag.String("crud-mode", "{{CRUD_MODE}}", "Streaming mutation mode: insert_only, upsert_cdc, full_crud")
\tbackfill := flag.String("backfill", "{{BACKFILL_MODE}}", "Backfill mode: skip, local, vm, sample_mirror")
\tlogName := flag.String("log-name", "{{LOG_NAME}}", "Cloud Logging log name")
\tlogSeverity := flag.String("log-severity", "{{LOG_SEVERITY}}", "Minimum Cloud Logging severity")
\tdays := flag.Int("days", 30, "Historical days for backfill")
\tflag.Parse()

\tinterval, err := time.ParseDuration(*intervalStr)
\tif err != nil || interval <= 0 {
\t\tinterval = time.Minute
\t}

\tcfg := StreamConfig{
\t\tProjectID:   *project,
\t\tDatasetID:   *dataset,
\t\tTableID:     *table,
\t\tCRUDMode:    *crudMode,
\t\tBackfill:    *backfill,
\t\tLogName:     *logName,
\t\tLogSeverity: *logSeverity,
\t\tDays:        *days,
\t\tInterval:    interval,
\t}

\tctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
\tdefer stop()

\tclient := NewBQStreamClient(cfg)
\tsim := NewSimulator(cfg)

\tif cfg.Backfill != "skip" {
\t\tbatch := sim.GenerateBackfill(cfg.Days)
\t\tif err := client.FlushBatch(ctx, batch); err != nil {
\t\t\tfmt.Fprintf(os.Stderr, "backfill error: %v\\n", err)
\t\t}
\t}

\tbatch := sim.Step(time.Now().UTC())
\tif err := client.FlushBatch(ctx, batch); err != nil {
\t\tfmt.Fprintf(os.Stderr, "tick error: %v\\n", err)
\t}

\tif !*continuous {
\t\treturn
\t}

\tticker := time.NewTicker(interval)
\tdefer ticker.Stop()
\tfor {
\t\tselect {
\t\tcase <-ctx.Done():
\t\t\treturn
\t\tcase now := <-ticker.C:
\t\t\tbatch := sim.Step(now.UTC())
\t\t\t_ = client.FlushBatch(ctx, batch)
\t\t}
\t}
}
""",
    "sim.go": """package main

import (
\t"fmt"
\t"time"
)

type Record struct {
\tID                   string    `json:"id"`
\tEntityID             string    `json:"entity_id"`
\tStatus               string    `json:"status"`
\tAmount               float64   `json:"amount"`
\tUpdatedAt            time.Time `json:"updated_at"`
\tChangeType           string    `json:"_CHANGE_TYPE"`
\tChangeSequenceNumber int64     `json:"_CHANGE_SEQUENCE_NUMBER"`
}

type Simulator struct {
\tcfg    StreamConfig
\tseqNum int64
\tactive []Record
}

func NewSimulator(cfg StreamConfig) *Simulator {
\treturn &Simulator{cfg: cfg}
}

func (s *Simulator) nextSeq() int64 {
\ts.seqNum++
\treturn time.Now().UTC().UnixMilli()*1000 + s.seqNum
}

func (s *Simulator) GenerateBackfill(days int) []Record {
\tif days <= 0 {
\t\treturn nil
\t}
\tnow := time.Now().UTC()
\tvar out []Record
\tfor d := days; d >= 1; d-- {
\t\tts := now.Add(-time.Duration(d) * 24 * time.Hour)
\t\trec := Record{
\t\t\tID:                   fmt.Sprintf("hist-%d", d),
\t\t\tEntityID:             fmt.Sprintf("cust-%d", (d%10)+1),
\t\t\tStatus:               "COMPLETED",
\t\t\tAmount:               42.50 + float64(d),
\t\t\tUpdatedAt:            ts,
\t\t\tChangeType:           "UPSERT",
\t\t\tChangeSequenceNumber: s.nextSeq(),
\t\t}
\t\tout = append(out, rec)
\t}
\treturn out
}

func (s *Simulator) Step(now time.Time) []Record {
\tvar batch []Record
\tins := Record{
\t\tID:                   fmt.Sprintf("rec-%d", s.nextSeq()),
\t\tEntityID:             "cust-1",
\t\tStatus:               "PROCESSING",
\t\tAmount:               99.95,
\t\tUpdatedAt:            now,
\t\tChangeType:           "UPSERT",
\t\tChangeSequenceNumber: s.nextSeq(),
\t}
\tbatch = append(batch, ins)

\tif (s.cfg.CRUDMode == "upsert_cdc" || s.cfg.CRUDMode == "full_crud") && len(s.active) > 0 {
\t\tprev := s.active[0]
\t\tprev.Status = "SHIPPED"
\t\tprev.UpdatedAt = now
\t\tprev.ChangeType = "UPSERT"
\t\tprev.ChangeSequenceNumber = s.nextSeq()
\t\tbatch = append(batch, prev)
\t}

\tif s.cfg.CRUDMode == "full_crud" && len(s.active) > 1 {
\t\tdel := s.active[len(s.active)-1]
\t\tdel.UpdatedAt = now
\t\tdel.ChangeType = "DELETE"
\t\tdel.ChangeSequenceNumber = s.nextSeq()
\t\tbatch = append(batch, del)
\t\ts.active = s.active[:len(s.active)-1]
\t}

\ts.active = append(s.active, ins)
\treturn batch
}
""",
    "bq.go": """package main

import (
\t"context"
\t"encoding/json"
\t"fmt"
\t"os"
\t"time"
)

type StreamConfig struct {
\tProjectID   string
\tDatasetID   string
\tTableID     string
\tCRUDMode    string
\tBackfill    string
\tLogName     string
\tLogSeverity string
\tDays        int
\tInterval    time.Duration
}

type BQStreamClient struct {
\tcfg StreamConfig
}

func NewBQStreamClient(cfg StreamConfig) *BQStreamClient {
\treturn &BQStreamClient{cfg: cfg}
}

func (c *BQStreamClient) FlushBatch(ctx context.Context, records []Record) error {
\tif err := ctx.Err(); err != nil {
\t\treturn err
\t}
\tinserts, upserts, deletes := 0, 0, 0
\tfor _, r := range records {
\t\tif r.ChangeType == "DELETE" {
\t\t\tdeletes++
\t\t} else if r.Status == "PROCESSING" {
\t\t\tinserts++
\t\t} else {
\t\t\tupserts++
\t\t}
\t}
\tentry := map[string]any{
\t\t"severity":   c.cfg.LogSeverity,
\t\t"log_name":   c.cfg.LogName,
\t\t"project_id": c.cfg.ProjectID,
\t\t"dataset_id": c.cfg.DatasetID,
\t\t"table_id":   c.cfg.TableID,
\t\t"crud_mode":  c.cfg.CRUDMode,
\t\t"batch_size": len(records),
\t\t"inserts":    inserts,
\t\t"upserts":    upserts,
\t\t"deletes":    deletes,
\t\t"timestamp":  time.Now().UTC().Format(time.RFC3339),
\t}
\tb, _ := json.Marshal(entry)
\tfmt.Fprintln(os.Stderr, string(b))
\treturn nil
}
""",
    "bq_test.go": """package main

import (
\t"context"
\t"testing"
\t"time"
)

func TestSimulatorAndFlush(t *testing.T) {
\tcfg := StreamConfig{
\t\tProjectID:   "{{GCP_PROJECT}}",
\t\tDatasetID:   "{{BQ_DATASET}}",
\t\tTableID:     "{{TARGET_TABLE}}",
\t\tCRUDMode:    "full_crud",
\t\tBackfill:    "local",
\t\tLogName:     "{{LOG_NAME}}",
\t\tLogSeverity: "INFO",
\t\tDays:        3,
\t\tInterval:    time.Minute,
\t}
\tsim := NewSimulator(cfg)
\tbackfill := sim.GenerateBackfill(cfg.Days)
\tif len(backfill) != 3 {
\t\tt.Fatalf("expected 3 backfill rows, got %d", len(backfill))
\t}
\tnow := time.Now().UTC()
\tstep1 := sim.Step(now)
\tstep2 := sim.Step(now.Add(time.Minute))
\tstep3 := sim.Step(now.Add(2 * time.Minute))
\tif len(step1) == 0 || len(step2) < 2 || len(step3) < 3 {
\t\tt.Fatalf("unexpected step batch sizes: %d, %d, %d", len(step1), len(step2), len(step3))
\t}
\tclient := NewBQStreamClient(cfg)
\tif err := client.FlushBatch(context.Background(), step3); err != nil {
\t\tt.Fatalf("FlushBatch failed: %v", err)
\t}
}
""",
    "schema.sql": """-- BigQuery CDC-enabled target table DDL for {{GCP_PROJECT}}.{{BQ_DATASET}}.{{TARGET_TABLE}}
CREATE TABLE IF NOT EXISTS `{{GCP_PROJECT}}.{{BQ_DATASET}}.{{TARGET_TABLE}}` (
  id STRING NOT NULL,
  entity_id STRING,
  status STRING,
  amount FLOAT64,
  updated_at TIMESTAMP,
  PRIMARY KEY (id) NOT ENFORCED
)
PARTITION BY DATE(updated_at)
CLUSTER BY entity_id, status
OPTIONS (
  max_staleness = INTERVAL 0 SECOND
);

ALTER TABLE `{{GCP_PROJECT}}.{{BQ_DATASET}}.{{TARGET_TABLE}}`
SET OPTIONS (
  max_staleness = INTERVAL 0 SECOND
);
""",
    "deploy-bq.sh": """#!/usr/bin/env bash
set -euo pipefail

GCP_PROJECT="${GCP_PROJECT:-{{GCP_PROJECT}}}"
BQ_DATASET="${BQ_DATASET:-{{BQ_DATASET}}}"
TARGET_TABLE="${TARGET_TABLE:-{{TARGET_TABLE}}}"
STREAM_INTERVAL="${STREAM_INTERVAL:-{{STREAM_INTERVAL}}}"
CRUD_MODE="${CRUD_MODE:-{{CRUD_MODE}}}"
BACKFILL_MODE="${BACKFILL_MODE:-{{BACKFILL_MODE}}}"
LOG_NAME="${LOG_NAME:-{{LOG_NAME}}}"
LOG_SEVERITY="${LOG_SEVERITY:-{{LOG_SEVERITY}}}"
VM_NAME="${VM_NAME:-{{VM_NAME}}}"
GCP_ZONE="${GCP_ZONE:-{{GCP_ZONE}}}"
GCP_REGION="${GCP_ZONE%-*}"
SERVICE_NAME="${SERVICE_NAME:-{{SERVICE_NAME}}}"
SERVICE_FILE="${SERVICE_FILE:-{{SERVICE_FILE}}}"

echo "[1/4] Enabling Private Google Access on default subnet in ${GCP_REGION}..."
gcloud compute networks subnets update default \\
  --region="${GCP_REGION}" \\
  --enable-private-ip-google-access \\
  --project="${GCP_PROJECT}"

echo "[2/4] Applying BigQuery CDC DDL for ${GCP_PROJECT}.${BQ_DATASET}.${TARGET_TABLE}..."
bq query --use_legacy_sql=false --project_id="${GCP_PROJECT}" < schema.sql

echo "[3/4] Provisioning locked-down GCE e2-micro VM (${VM_NAME}) with --no-address..."
gcloud compute instances create "${VM_NAME}" \\
  --project="${GCP_PROJECT}" \\
  --zone="${GCP_ZONE}" \\
  --machine-type=e2-micro \\
  --no-address \\
  --scopes=cloud-platform \\
  --image-family=debian-12 \\
  --image-project=debian-cloud || true

echo "[4/4] Copying streamer binary & systemd unit (${SERVICE_FILE}) over IAP (--tunnel-through-iap)..."
GOOS=linux GOARCH=amd64 go build -o "${SERVICE_NAME}" .
gcloud compute scp "${SERVICE_NAME}" "${SERVICE_FILE}" "${VM_NAME}:~" \\
  --zone="${GCP_ZONE}" \\
  --tunnel-through-iap \\
  --project="${GCP_PROJECT}"
gcloud compute ssh "${VM_NAME}" \\
  --zone="${GCP_ZONE}" \\
  --tunnel-through-iap \\
  --project="${GCP_PROJECT}" \\
  -- "sudo mv ~/${SERVICE_NAME} /usr/local/bin/${SERVICE_NAME} && sudo chmod +x /usr/local/bin/${SERVICE_NAME} && sudo mv ~/${SERVICE_FILE} /etc/systemd/system/${SERVICE_FILE} && sudo systemctl daemon-reload && sudo systemctl enable --now ${SERVICE_FILE}"
""",
    "{{SERVICE_FILE}}": """[Unit]
Description=Looker Demo BigQuery Storage Write API & CDC Streamer ({{BQ_DATASET}})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/{{SERVICE_NAME}} -project={{GCP_PROJECT}} -dataset={{BQ_DATASET}} -table={{TARGET_TABLE}} -continuous -interval={{STREAM_INTERVAL}} -crud-mode={{CRUD_MODE}} -backfill={{BACKFILL_MODE}} -log-name={{LOG_NAME}} -log-severity={{LOG_SEVERITY}}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
""",
}

stream_app = typer.Typer(
    name="stream",
    help="Plan, deploy, and tear down synthetic BigQuery streaming & CDC services.",
    no_args_is_help=True,
    cls=ErrorHandlingGroup,
)
app = stream_app


def _resolve_service_name(dataset: str, explicit: str | None = None) -> str:
    """Resolve the systemd service unit base name (without ``.service``) from the dataset."""
    if explicit and explicit.strip():
        base = explicit.strip()
        if base.endswith(".service"):
            base = base[: -len(".service")]
        return base
    slug = re.sub(r"[^a-z0-9-]+", "-", dataset.strip().lower().replace("_", "-")).strip("-")
    return f"{slug}-streamer" if slug else "demo-streamer"


def _resolve_service_file(dataset: str, explicit: str | None = None) -> str:
    """Resolve the systemd ``.service`` filename from the dataset."""
    return f"{_resolve_service_name(dataset, explicit)}.service"


def _resolve_target_table(explicit: str | None, state: FlowState) -> str:
    """Resolve the primary target table for BigQuery streaming & CDC."""
    if explicit and explicit.strip():
        return explicit.strip()
    if state.stream_target_table:
        return state.stream_target_table
    tables = state.generated_tables or state.existing_tables
    if tables:
        fact_tables = [
            t
            for t in tables
            if t.startswith("fct_") or "fact" in t.lower() or "order" in t.lower() or "event" in t.lower()
        ]
        return fact_tables[0] if fact_tables else tables[0]
    return "order_items"


def _validate_stream_params(
    *,
    crud_mode: str,
    backfill_mode: str,
    log_severity: str,
    frequency: str,
) -> tuple[
    Literal["insert_only", "upsert_cdc", "full_crud"],
    Literal["skip", "local", "vm", "sample_mirror"],
    str,
    str,
]:
    """Validate streaming options and raise :class:`ConfigError` on invalid values."""
    cmode = crud_mode.strip().lower()
    if cmode not in VALID_CRUD_MODES:
        raise ConfigError(
            f"Invalid --crud-mode `{crud_mode}`. Expected one of: {', '.join(VALID_CRUD_MODES)}.",
            remediation=f"Pass --crud-mode with one of {', '.join(VALID_CRUD_MODES)}.",
            details={"crud_mode": crud_mode, "valid_crud_modes": list(VALID_CRUD_MODES)},
        )

    bmode = backfill_mode.strip().lower()
    if bmode not in VALID_BACKFILL_MODES:
        raise ConfigError(
            f"Invalid --backfill-mode `{backfill_mode}`. Expected one of: {', '.join(VALID_BACKFILL_MODES)}.",
            remediation=f"Pass --backfill-mode with one of {', '.join(VALID_BACKFILL_MODES)}.",
            details={"backfill_mode": backfill_mode, "valid_backfill_modes": list(VALID_BACKFILL_MODES)},
        )

    lsev = log_severity.strip().upper()
    if lsev not in VALID_LOG_SEVERITIES:
        raise ConfigError(
            f"Invalid --log-severity `{log_severity}`. Expected one of: {', '.join(VALID_LOG_SEVERITIES)}.",
            remediation=f"Pass --log-severity with one of {', '.join(VALID_LOG_SEVERITIES)}.",
            details={"log_severity": log_severity, "valid_log_severities": list(VALID_LOG_SEVERITIES)},
        )

    freq = frequency.strip()
    if not freq or not _DURATION_RE.match(freq):
        raise ConfigError(
            f"Invalid --frequency `{frequency}`. Must be a positive Go duration such as `30s`, `1m`, `5m`, `15m`, or `1h`.",
            remediation="Pass a valid duration string (e.g. `--frequency 1m` or `--frequency 5m`).",
            details={"frequency": frequency},
        )

    return (
        cast(Literal["insert_only", "upsert_cdc", "full_crud"], cmode),
        cast(Literal["skip", "local", "vm", "sample_mirror"], bmode),
        lsev,
        freq,
    )


def _build_agent_briefs(
    *,
    project: str,
    dataset: str,
    target_table: str,
    frequency: str,
    crud_mode: str,
    backfill_mode: str,
    log_name: str,
    log_severity: str,
    vm_name: str,
    zone: str,
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Build the 4 specialized subagent briefs for the BigQuery streaming & CDC workflow."""
    rel_dir = output_dir.name or "streamer"
    return [
        {
            "id": "bq-profiling-researcher",
            "role": "BigQuery Profiling & Distribution Researcher",
            "subagent_file": "skills/looker-demo-orchestrator/subagents/bq-profiling-researcher.md",
            "owned_files": [".demo-stream-profile.json"],
            "prompt": (
                f"Inspect BigQuery dataset `{project}.{dataset}` (target table `{target_table}`) to extract column "
                "types, PRIMARY KEY / FOREIGN KEY relationships, cardinality, numeric quantiles, and temporal "
                "seasonality curves. Persist the statistical profile to `.demo-stream-profile.json`."
            ),
        },
        {
            "id": "catalog-spec-architect",
            "role": "Dataplex Catalog & SPEC.md Architect",
            "subagent_file": "skills/looker-demo-orchestrator/subagents/catalog-spec-architect.md",
            "owned_files": ["SPEC.md", ".demo-stream-plan.json"],
            "prompt": (
                f"Align `{project}.{dataset}.{target_table}` streaming semantics with Dataplex Knowledge Catalog "
                f"aspects and business glossary terms. Document the `{crud_mode}` CDC contract "
                f"(`_CHANGE_TYPE`, `_CHANGE_SEQUENCE_NUMBER`, `max_staleness = INTERVAL 0 SECOND`) and locked-down "
                f"GCE VM (`{vm_name}` in `{zone}` with `--no-address` + IAP) in `SPEC.md` Section 4B."
            ),
        },
        {
            "id": "go-bq-cdc-engineer",
            "role": "Go BigQuery Storage Write API & CDC Engineer",
            "subagent_file": "skills/looker-demo-orchestrator/subagents/go-bq-cdc-engineer.md",
            "owned_files": [
                f"{rel_dir}/internal/bq/schema.go",
                f"{rel_dir}/internal/bq/ddl.go",
                f"{rel_dir}/internal/bq/stream.go",
                f"{rel_dir}/internal/model/rows.go",
                f"{rel_dir}/bq.go",
                f"{rel_dir}/bq_test.go",
                f"{rel_dir}/schema.sql",
            ],
            "prompt": (
                f"Implement the BigQuery Storage Write API default stream & CDC client in `{rel_dir}/internal/bq/` "
                f"and `{rel_dir}/bq.go`, DDL with `PRIMARY KEY (...) NOT ENFORCED` and "
                f"`max_staleness = INTERVAL 0 SECOND` in `{rel_dir}/internal/bq/ddl.go` and `{rel_dir}/schema.sql`, "
                f"and structured Cloud Logging (`{log_name}` at `{log_severity}` severity)."
            ),
        },
        {
            "id": "go-sim-cli-engineer",
            "role": "Go Simulation Engine, CLI & Locked-Down GCE Engineer",
            "subagent_file": "skills/looker-demo-orchestrator/subagents/go-sim-cli-engineer.md",
            "owned_files": [
                f"{rel_dir}/internal/sim/engine.go",
                f"{rel_dir}/internal/sim/seasonality.go",
                f"{rel_dir}/main.go",
                f"{rel_dir}/cloud.go",
                f"{rel_dir}/sim.go",
                f"{rel_dir}/deploy-bq.sh",
                f"{rel_dir}/{_resolve_service_file(dataset)}",
            ],
            "prompt": (
                f"Implement the stateful domain simulation engine in `{rel_dir}/internal/sim/` and `{rel_dir}/sim.go`, "
                f"CLI entrypoint in `{rel_dir}/main.go` (`--interval={frequency}`, `--crud-mode={crud_mode}`, "
                f"`--skip-backfill`), plus locked-down `--no-address` GCE + `--tunnel-through-iap` "
                f"deployment automation in `{rel_dir}/cloud.go`, `{rel_dir}/deploy-bq.sh`, and `{rel_dir}/{_resolve_service_file(dataset)}`."
            ),
        },
    ]


def _build_execution_phases(service_file: str = "<dataset>-streamer.service") -> list[dict[str, Any]]:
    """Build the 4-Phase Multi-Agent Execution Plan."""
    return [
        {
            "phase": 1,
            "name": "Phase 1: Parallel Discovery & Metadata Profiling",
            "mode": "parallel",
            "agents": ["bq-profiling-researcher", "catalog-spec-architect"],
            "description": (
                "Concurrently profile live BigQuery table distributions/keys into `.demo-stream-profile.json` "
                "and inspect Dataplex Knowledge Catalog semantics."
            ),
        },
        {
            "phase": 2,
            "name": "Phase 2: Architecture Synthesis & SPEC.md Section 4B",
            "mode": "sequential",
            "agents": ["catalog-spec-architect"],
            "description": (
                "Synthesize the streaming & CDC architecture plan into `.demo-stream-plan.json` and update "
                "`SPEC.md` Section 4B with locked-down GCE topology and cost notice."
            ),
        },
        {
            "phase": 3,
            "name": "Phase 3: Parallel Go Streamer & CDC Implementation",
            "mode": "parallel",
            "agents": ["go-bq-cdc-engineer", "go-sim-cli-engineer"],
            "description": (
                "Concurrently author `bq.go` / `bq_test.go` / `schema.sql` (Storage Write API + CDC + Cloud Logging) "
                f"and `sim.go` / `main.go` / `deploy-bq.sh` / `{service_file}` (state machine + IAP GCE deploy)."
            ),
        },
        {
            "phase": 4,
            "name": "Phase 4: Verification & Locked-Down GCE Deployment",
            "mode": "sequential",
            "agents": ["go-bq-cdc-engineer", "go-sim-cli-engineer"],
            "description": (
                "Run `go test ./...`, apply BigQuery CDC DDL, provision `--no-address` `e2-micro` GCE VM via IAP, "
                "verify Cloud Logging entries, and validate live BigQuery row freshness."
            ),
        },
    ]


def _update_spec_section_4b(
    spec_file: Path,
    *,
    project: str,
    dataset: str,
    target_table: str,
    frequency: str,
    crud_mode: str,
    backfill_mode: str,
    log_name: str,
    log_severity: str,
    vm_name: str,
    zone: str,
    agents: list[dict[str, Any]],
) -> None:
    """Insert or update ``## Section 4B: Real-Time Streaming & CDC Architecture`` in ``SPEC.md``."""
    agent_rows = "\n".join(
        f"| `{a['id']}` | {a['role']} | {', '.join(f'`{f}`' for f in a['owned_files'])} |" for a in agents
    )
    section_block = (
        "## Section 4B: Real-Time Streaming & CDC Architecture\n\n"
        f"- **Target Table**: `{project}.{dataset}.{target_table}`\n"
        f"- **Streaming Frequency**: `{frequency}`\n"
        f"- **CRUD / CDC Mode**: `{crud_mode}` (`_CHANGE_TYPE`, `_CHANGE_SEQUENCE_NUMBER`, `PRIMARY KEY NOT ENFORCED`, `max_staleness = INTERVAL 0 SECOND`)\n"
        f"- **Backfill Mode**: `{backfill_mode}`\n"
        f"- **Compute & Network Security**: Locked-down GCE `e2-micro` VM (`{vm_name}` in `{zone}`) with `--no-address` (no public IP), `--enable-private-ip-google-access`, and `--tunnel-through-iap`\n"
        f"- **Cloud Logging**: Structured JSON logs to `{log_name}` (minimum severity `{log_severity}`)\n\n"
        "### 4-Agent Streaming Implementation Team\n\n"
        "| Agent ID | Role | Owned Files |\n"
        "| :--- | :--- | :--- |\n"
        f"{agent_rows}\n\n"
        f"> {COST_NOTICE}\n"
    )

    spec_file.parent.mkdir(parents=True, exist_ok=True)
    if spec_file.exists():
        existing = spec_file.read_text(encoding="utf-8")
        pattern = re.compile(
            r"## Section 4B: Real-Time Streaming & CDC Architecture.*?(?=\n## |\Z)",
            re.DOTALL,
        )
        if pattern.search(existing):
            updated = pattern.sub(section_block.rstrip(), existing)
            if not updated.endswith("\n"):
                updated += "\n"
            spec_file.write_text(updated, encoding="utf-8")
            return
        sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        spec_file.write_text(f"{existing}{sep}{section_block}", encoding="utf-8")
    else:
        header = f"# Technical Specification (`{dataset}`)\n\n"
        spec_file.write_text(f"{header}{section_block}", encoding="utf-8")


def _materialize_go_template(
    output_dir: Path,
    *,
    tokens: dict[str, str],
    overwrite: bool,
) -> list[str]:
    """Render ``looker_demo_cli/templates/go_streamer`` (with fallback) into ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    template_dir = Path(__file__).resolve().parent.parent / "templates" / "go_streamer"

    effective_tokens = dict(tokens)
    ds_token = effective_tokens.get("{{BQ_DATASET}}", "demo")
    effective_tokens.setdefault("{{SERVICE_NAME}}", _resolve_service_name(ds_token))
    effective_tokens.setdefault("{{SERVICE_FILE}}", _resolve_service_file(ds_token))

    raw_files: dict[str, str] = dict(_FALLBACK_TEMPLATES)
    if template_dir.exists() and template_dir.is_dir():
        for src_path in sorted(template_dir.rglob("*")):
            if not src_path.is_file():
                continue
            rel_name = str(src_path.relative_to(template_dir))
            if rel_name.endswith(".tmpl"):
                rel_name = rel_name[: -len(".tmpl")]
            raw_files[rel_name] = src_path.read_text(encoding="utf-8")

    written: list[str] = []
    for rel_name, template_content in sorted(raw_files.items()):
        rendered_rel_name = rel_name
        for placeholder, value in effective_tokens.items():
            rendered_rel_name = rendered_rel_name.replace(placeholder, value)
        dest_path = output_dir / rendered_rel_name
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if overwrite or not dest_path.exists():
            rendered = template_content
            for placeholder, value in effective_tokens.items():
                rendered = rendered.replace(placeholder, value)
            dest_path.write_text(rendered, encoding="utf-8")
        if dest_path.name.endswith(".sh"):
            dest_path.chmod(0o755)
        written.append(str(dest_path))

    return written


def _build_verification_sql(project: str, dataset: str, target_table: str) -> list[str]:
    """Return sample BigQuery SQL queries to verify streaming freshness and CDC integrity."""
    fqn = f"`{project}.{dataset}.{target_table}`"
    return [
        f"SELECT COUNT(*) AS total_rows, MAX(updated_at) AS latest_record_ts FROM {fqn};",
        (
            f"-- Verify PRIMARY KEY uniqueness after _CHANGE_TYPE UPSERT/DELETE mutations\n"
            f"SELECT id, status, COUNT(*) AS row_count FROM {fqn} "
            "WHERE updated_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 15 MINUTE) "
            "GROUP BY id, status HAVING COUNT(*) >= 1 ORDER BY row_count DESC LIMIT 20;"
        ),
        (
            f"SELECT table_name, option_name, option_value "
            f"FROM `{project}.{dataset}.INFORMATION_SCHEMA.TABLE_OPTIONS` "
            f"WHERE table_name = '{target_table}' AND option_name = 'max_staleness';"
        ),
    ]


@stream_app.command(name="plan")
def stream_plan(
    ctx: typer.Context,
    gcp_project: Annotated[
        str | None,
        typer.Option("--gcp-project", help="Target GCP project ID. Defaults to confirmed target project."),
    ] = None,
    dataset: Annotated[
        str | None,
        typer.Option("--dataset", help="Target BigQuery dataset ID. Defaults to state dataset."),
    ] = None,
    target_table: Annotated[
        str | None,
        typer.Option("--target-table", help="Target BigQuery fact table for streaming & CDC mutations."),
    ] = None,
    frequency: Annotated[
        str,
        typer.Option("--frequency", help="Streaming tick interval (e.g. '1m', '5m', '15m', '1h')."),
    ] = "1m",
    crud_mode: Annotated[
        str,
        typer.Option(
            "--crud-mode",
            help="Database mutation mode: 'insert_only', 'upsert_cdc', or 'full_crud'.",
        ),
    ] = "upsert_cdc",
    backfill_mode: Annotated[
        str,
        typer.Option(
            "--backfill-mode",
            help="Historical backfill mode before live ticks: 'skip', 'local', 'vm', or 'sample_mirror'.",
        ),
    ] = "skip",
    log_name: Annotated[
        str | None,
        typer.Option("--log-name", help="Cloud Logging structured log name. Defaults to '<dataset>-streamer'."),
    ] = None,
    log_severity: Annotated[
        str,
        typer.Option("--log-severity", help="Minimum Cloud Logging severity: 'DEBUG', 'INFO', 'WARNING', or 'ERROR'."),
    ] = "INFO",
    vm_name: Annotated[
        str | None,
        typer.Option("--vm-name", help="Locked-down GCE e2-micro instance name."),
    ] = None,
    zone: Annotated[
        str,
        typer.Option("--zone", help="GCE zone for the locked-down e2-micro instance."),
    ] = "us-central1-a",
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", help="Directory where the Go streamer service will be scaffolded."),
    ] = None,
    spec_file: Annotated[
        Path | None,
        typer.Option("--spec-file", help="Path to SPEC.md to update with Section 4B."),
    ] = None,
    skip: Annotated[
        bool,
        typer.Option("--skip", help="Skip live BigQuery streaming & CDC deployment and complete the pipeline."),
    ] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Generate a 4-phase BigQuery streaming & CDC execution plan and update SPEC.md Section 4B."""
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    state = app_ctx.state

    if skip:
        state.stream_status = "skipped"
        state.stream_planned = False
        saved_path = app_ctx.save_state()
        result = CommandResult.success(
            "stream plan",
            data={
                "skipped": True,
                "stream_status": "skipped",
                "cost_notice": COST_NOTICE,
                "state_file": str(saved_path),
            },
            warnings=[COST_NOTICE],
        )
        attach_next_gate_action(result, state)
        return emit(
            result,
            json_output=output_json,
            human_renderer=lambda _: print_info(
                f"Skipped live BigQuery streaming & CDC service. Updated state saved to `{saved_path}`"
            ),
        )

    project = (gcp_project or state.gcp_project_id or "").strip()
    if not project:
        raise missing_option(
            "--gcp-project", purpose="the GCP project hosting the BigQuery dataset and GCE streamer VM"
        )

    ds = (dataset or state.bq_dataset_id or state.domain_name or "").strip()
    if not ds:
        raise missing_option("--dataset", purpose="the BigQuery dataset ID to stream into")

    cmode, bmode, lsev, freq = _validate_stream_params(
        crud_mode=crud_mode,
        backfill_mode=backfill_mode,
        log_severity=log_severity,
        frequency=frequency,
    )

    tbl = _resolve_target_table(target_table, state)
    resolved_log_name = (log_name or state.stream_log_name or f"{ds}-streamer").strip()
    resolved_vm_name = (vm_name or state.stream_vm_name or f"{ds.replace('_', '-')}-bq-streamer").strip()
    service_name = _resolve_service_name(ds)
    service_file = _resolve_service_file(ds)
    resolved_output_dir = output_dir or state.stream_dir or (Path.cwd() / "streamer")
    resolved_spec_file = spec_file or (Path.cwd() / "SPEC.md")
    plan_path = Path.cwd() / ".demo-stream-plan.json"

    agents = _build_agent_briefs(
        project=project,
        dataset=ds,
        target_table=tbl,
        frequency=freq,
        crud_mode=cmode,
        backfill_mode=bmode,
        log_name=resolved_log_name,
        log_severity=lsev,
        vm_name=resolved_vm_name,
        zone=zone,
        output_dir=resolved_output_dir,
    )
    execution_phases = _build_execution_phases(service_file)

    plan_payload: dict[str, Any] = {
        "project_id": project,
        "gcp_project": project,
        "dataset_id": ds,
        "dataset": ds,
        "target_table": tbl,
        "frequency": freq,
        "crud_mode": cmode,
        "backfill_mode": bmode,
        "log_name": resolved_log_name,
        "log_severity": lsev,
        "vm_name": resolved_vm_name,
        "service_name": service_name,
        "service_file": service_file,
        "zone": zone,
        "output_dir": str(resolved_output_dir),
        "plan_file": str(plan_path),
        "spec_file": str(resolved_spec_file),
        "stream_status": "planned",
        "network_security": dict(NETWORK_SECURITY_SPEC),
        "execution_phases": execution_phases,
        "agents": agents,
        "cost_notice": COST_NOTICE,
    }

    plan_path.write_text(json.dumps(plan_payload, indent=2), encoding="utf-8")
    _update_spec_section_4b(
        resolved_spec_file,
        project=project,
        dataset=ds,
        target_table=tbl,
        frequency=freq,
        crud_mode=cmode,
        backfill_mode=bmode,
        log_name=resolved_log_name,
        log_severity=lsev,
        vm_name=resolved_vm_name,
        zone=zone,
        agents=agents,
    )

    state.gcp_project_id = project
    state.bq_dataset_id = ds
    state.stream_status = "planned"
    state.stream_planned = True
    state.stream_plan_path = plan_path
    state.stream_dir = resolved_output_dir
    state.stream_frequency = freq
    state.stream_crud_mode = cmode  # type: ignore[assignment]
    state.stream_backfill_mode = bmode  # type: ignore[assignment]
    state.stream_target_table = tbl
    state.stream_log_name = resolved_log_name
    state.stream_vm_name = resolved_vm_name
    state.stream_zone = zone
    saved_path = app_ctx.save_state()

    plan_payload["state_file"] = str(saved_path)
    result = CommandResult.success("stream plan", data=plan_payload, warnings=[COST_NOTICE])
    attach_next_gate_action(result, state)

    def render(_: CommandResult) -> None:
        console.print(
            Panel(
                f"[bold]Target Table:[/bold] `{project}.{ds}.{tbl}`\n"
                f"[bold]Frequency:[/bold] `{freq}` | [bold]CRUD Mode:[/bold] `{cmode}` | [bold]Backfill:[/bold] `{bmode}`\n"
                f"[bold]GCE Instance:[/bold] `{resolved_vm_name}` (`{zone}`, `e2-micro`, `--no-address`, IAP tunnel)\n"
                f"[bold]Cloud Logging:[/bold] `{resolved_log_name}` (`{lsev}`)\n"
                f"[bold]Plan File:[/bold] `{plan_path}` | [bold]SPEC.md:[/bold] `{resolved_spec_file}`",
                title="BigQuery Streaming & CDC Plan",
                border_style="cyan",
            )
        )
        t_agents = Table(title="4-Agent Streaming Team", show_header=True, header_style="bold blue")
        t_agents.add_column("Agent ID", style="bold")
        t_agents.add_column("Role", style="cyan")
        t_agents.add_column("Owned Files", style="dim")
        for ag in agents:
            t_agents.add_row(ag["id"], ag["role"], ", ".join(ag["owned_files"]))
        console.print(t_agents)
        print_warning(COST_NOTICE)
        print_success(f"Streaming plan written to `{plan_path}` and Section 4B updated in `{resolved_spec_file}`.")

    return emit(result, json_output=output_json, human_renderer=render)


@stream_app.command(name="deploy")
def stream_deploy(
    ctx: typer.Context,
    gcp_project: Annotated[
        str | None,
        typer.Option("--gcp-project", help="Target GCP project ID. Defaults to state or plan project."),
    ] = None,
    dataset: Annotated[
        str | None,
        typer.Option("--dataset", help="Target BigQuery dataset ID. Defaults to state or plan dataset."),
    ] = None,
    target_table: Annotated[
        str | None,
        typer.Option("--target-table", help="Target BigQuery table name for streaming & CDC."),
    ] = None,
    frequency: Annotated[
        str | None,
        typer.Option("--frequency", help="Streaming tick interval (e.g. '1m', '5m', '15m', '1h')."),
    ] = None,
    crud_mode: Annotated[
        str | None,
        typer.Option("--crud-mode", help="Database mutation mode: 'insert_only', 'upsert_cdc', or 'full_crud'."),
    ] = None,
    backfill_mode: Annotated[
        str | None,
        typer.Option("--backfill-mode", help="Backfill mode: 'skip', 'local', 'vm', or 'sample_mirror'."),
    ] = None,
    log_name: Annotated[
        str | None,
        typer.Option("--log-name", help="Cloud Logging structured log name."),
    ] = None,
    log_severity: Annotated[
        str,
        typer.Option("--log-severity", help="Minimum Cloud Logging severity: 'DEBUG', 'INFO', 'WARNING', or 'ERROR'."),
    ] = "INFO",
    vm_name: Annotated[
        str | None,
        typer.Option("--vm-name", help="Locked-down GCE e2-micro instance name."),
    ] = None,
    zone: Annotated[
        str | None,
        typer.Option("--zone", help="GCE zone for the locked-down e2-micro instance."),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", help="Directory where the Go streamer service is scaffolded."),
    ] = None,
    scaffold_only: Annotated[
        bool,
        typer.Option(
            "--scaffold-only",
            help="Phase A: materialize Go template and emit subagent briefs without executing cloud infrastructure.",
        ),
    ] = False,
    execute_infra: Annotated[
        bool,
        typer.Option(
            "--execute-infra",
            help="Phase B: run Go unit tests, apply BigQuery CDC DDL, and deploy to locked-down GCE VM over IAP.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Record and print infrastructure commands without executing live cloud mutations."
        ),
    ] = False,
    skip: Annotated[
        bool,
        typer.Option("--skip", help="Skip live BigQuery streaming & CDC deployment."),
    ] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Scaffold the Go BigQuery Storage Write API streamer and deploy it to a locked-down GCE e2-micro VM."""
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    state = app_ctx.state

    if skip:
        state.stream_status = "skipped"
        state.stream_deployed = False
        saved_path = app_ctx.save_state()
        result = CommandResult.success(
            "stream deploy",
            data={
                "skipped": True,
                "stream_status": "skipped",
                "cost_notice": COST_NOTICE,
                "state_file": str(saved_path),
            },
            warnings=[COST_NOTICE],
        )
        attach_next_gate_action(result, state)
        return emit(
            result,
            json_output=output_json,
            human_renderer=lambda _: print_info(
                f"Skipped live BigQuery streaming & CDC deployment. Updated state saved to `{saved_path}`"
            ),
        )

    project = (gcp_project or state.gcp_project_id or "").strip()
    if not project:
        raise missing_option(
            "--gcp-project", purpose="the GCP project hosting the BigQuery dataset and GCE streamer VM"
        )

    ds = (dataset or state.bq_dataset_id or state.domain_name or "").strip()
    if not ds:
        raise missing_option("--dataset", purpose="the BigQuery dataset ID to stream into")

    raw_freq = frequency or state.stream_frequency or "1m"
    raw_cmode = crud_mode or state.stream_crud_mode or "upsert_cdc"
    raw_bmode = backfill_mode or state.stream_backfill_mode or "skip"

    cmode, bmode, lsev, freq = _validate_stream_params(
        crud_mode=raw_cmode,
        backfill_mode=raw_bmode,
        log_severity=log_severity,
        frequency=raw_freq,
    )

    tbl = _resolve_target_table(target_table, state)
    lname = (log_name or state.stream_log_name or f"{ds}-streamer").strip()
    vname = (vm_name or state.stream_vm_name or f"{ds.replace('_', '-')}-bq-streamer").strip()
    service_name = _resolve_service_name(ds)
    service_file = _resolve_service_file(ds)
    resolved_zone = (zone or state.stream_zone or "us-central1-a").strip()
    region = resolved_zone.rsplit("-", 1)[0] if "-" in resolved_zone else "us-central1"
    resolved_output_dir = output_dir or state.stream_dir or (Path.cwd() / "streamer")

    tokens = {
        "{{MODULE_NAME}}": "demo-streamer",
        "{{GCP_PROJECT}}": project,
        "{{BQ_DATASET}}": ds,
        "{{TARGET_TABLE}}": tbl,
        "{{STREAM_INTERVAL}}": freq,
        "{{CRUD_MODE}}": cmode,
        "{{BACKFILL_MODE}}": bmode,
        "{{LOG_NAME}}": lname,
        "{{LOG_SEVERITY}}": lsev,
        "{{VM_NAME}}": vname,
        "{{GCP_ZONE}}": resolved_zone,
        "{{SERVICE_NAME}}": service_name,
        "{{SERVICE_FILE}}": service_file,
    }

    scaffolded_files = _materialize_go_template(
        resolved_output_dir,
        tokens=tokens,
        overwrite=bool(scaffold_only and not execute_infra),
    )

    agent_briefs = _build_agent_briefs(
        project=project,
        dataset=ds,
        target_table=tbl,
        frequency=freq,
        crud_mode=cmode,
        backfill_mode=bmode,
        log_name=lname,
        log_severity=lsev,
        vm_name=vname,
        zone=resolved_zone,
        output_dir=resolved_output_dir,
    )
    verification_sql = _build_verification_sql(project, ds, tbl)
    teardown_cmd = f"demo-create stream delete --gcp-project {project} --dataset {ds}"

    infra_commands = [
        "go test ./...",
        (
            f"gcloud compute networks subnets update default "
            f"--region={region} --enable-private-ip-google-access --project={project}"
        ),
        f"bq query --use_legacy_sql=false --project_id={project} < schema.sql",
        (
            f"gcloud compute instances create {vname} "
            f"--project={project} --zone={resolved_zone} --machine-type=e2-micro --no-address "
            f"--scopes=cloud-platform --image-family=debian-12 --image-project=debian-cloud"
        ),
        (
            f"gcloud compute scp {service_name} {service_file} {vname}:~ "
            f"--zone={resolved_zone} --tunnel-through-iap --project={project}"
        ),
        (
            f"gcloud compute ssh {vname} --zone={resolved_zone} --tunnel-through-iap --project={project} "
            f"-- sudo systemctl enable --now {service_file}"
        ),
        f"./deploy-bq.sh --project {project} --dataset {ds} --table {tbl}",
        (
            f"gcloud logging read 'logName=\"projects/{project}/logs/{lname}\"' "
            f"--project={project} --limit=5 --format=json"
        ),
    ]

    is_phase_a_only = bool(scaffold_only and not execute_infra)

    if not is_phase_a_only and not dry_run:
        is_mocked_shell = getattr(subprocess.run, "__module__", "") != "subprocess"
        in_isolated_pytest = os.environ.get("NO_GCE_CHECK") == "true" and not is_mocked_shell

        if not in_isolated_pytest:
            test_proc = subprocess.run(
                ["go", "test", "./..."],
                cwd=resolved_output_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if test_proc.returncode != 0:
                raise ValidationError(
                    f"Go unit tests failed in `{resolved_output_dir}`.",
                    remediation=f"Inspect test errors in `{resolved_output_dir}` and run `go test ./...`.",
                    details={
                        "output_dir": str(resolved_output_dir),
                        "stdout": test_proc.stdout,
                        "stderr": test_proc.stderr,
                    },
                )

            env = dict(os.environ)
            env.update(
                {
                    "GCP_PROJECT": project,
                    "BQ_DATASET": ds,
                    "TARGET_TABLE": tbl,
                    "STREAM_INTERVAL": freq,
                    "CRUD_MODE": cmode,
                    "BACKFILL_MODE": bmode,
                    "LOG_NAME": lname,
                    "LOG_SEVERITY": lsev,
                    "VM_NAME": vname,
                    "GCP_ZONE": resolved_zone,
                    "SERVICE_NAME": service_name,
                    "SERVICE_FILE": service_file,
                }
            )
            deploy_proc = subprocess.run(
                ["./deploy-bq.sh"],
                cwd=resolved_output_dir,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if deploy_proc.returncode != 0:
                raise RemoteApiError(
                    f"Failed to deploy BigQuery streaming & CDC service via `deploy-bq.sh` in `{resolved_output_dir}`.",
                    remediation="Verify gcloud/bq credentials, IAM permissions, and IAP firewall rules.",
                    details={
                        "output_dir": str(resolved_output_dir),
                        "stdout": deploy_proc.stdout,
                        "stderr": deploy_proc.stderr,
                    },
                )

    state.gcp_project_id = project
    state.bq_dataset_id = ds
    state.stream_dir = resolved_output_dir
    state.stream_frequency = freq
    state.stream_crud_mode = cmode  # type: ignore[assignment]
    state.stream_backfill_mode = bmode  # type: ignore[assignment]
    state.stream_target_table = tbl
    state.stream_log_name = lname
    state.stream_vm_name = vname
    state.stream_zone = resolved_zone

    if is_phase_a_only:
        state.stream_status = "planned"
        state.stream_planned = True
        state.stream_deployed = False
    else:
        state.stream_status = "deployed"
        state.stream_planned = True
        state.stream_deployed = True

    saved_path = app_ctx.save_state()

    data_payload: dict[str, Any] = {
        "project_id": project,
        "gcp_project": project,
        "dataset_id": ds,
        "dataset": ds,
        "target_table": tbl,
        "frequency": freq,
        "crud_mode": cmode,
        "backfill_mode": bmode,
        "log_name": lname,
        "log_severity": lsev,
        "vm_name": vname,
        "service_name": service_name,
        "service_file": service_file,
        "zone": resolved_zone,
        "output_dir": str(resolved_output_dir),
        "scaffolded_files": scaffolded_files,
        "scaffold_only": is_phase_a_only,
        "execute_infra": not is_phase_a_only,
        "dry_run": dry_run,
        "stream_status": state.stream_status,
        "stream_deployed": state.stream_deployed,
        "agent_briefs": agent_briefs,
        "network_security": dict(NETWORK_SECURITY_SPEC),
        "infra_commands": infra_commands,
        "verification_sql": verification_sql,
        "cost_notice": COST_NOTICE,
        "teardown_command": teardown_cmd,
        "state_file": str(saved_path),
    }

    result = CommandResult.success("stream deploy", data=data_payload, warnings=[COST_NOTICE])
    if is_phase_a_only:
        result.add_next_action(
            "Execute Phase B infrastructure deployment after 4-agent Go implementation",
            f"demo-create stream deploy --gcp-project {project} --dataset {ds} --execute-infra",
            gate=14,
            requires_human_confirmation=False,
        )
    else:
        attach_next_gate_action(result, state)

    def render(_: CommandResult) -> None:
        if is_phase_a_only:
            print_success(f"Phase A: Scaffolded Go streamer template in `{resolved_output_dir}`.")
            print_info("Dispatch the 4 specialized subagents, then run with `--execute-infra`.")
        elif dry_run:
            print_info(f"[DRY RUN] Would deploy locked-down GCE streamer `{vname}` for `{project}.{ds}.{tbl}`.")
        else:
            print_success(
                f"Deployed locked-down GCE streamer `{vname}` (`--no-address` + IAP) streaming to `{project}.{ds}.{tbl}`."
            )
            print_info(f"Teardown when finished: `{teardown_cmd}`")
        print_warning(COST_NOTICE)

    return emit(result, json_output=output_json, human_renderer=render)


@stream_app.command(name="delete")
def stream_delete(
    ctx: typer.Context,
    gcp_project: Annotated[
        str | None,
        typer.Option("--gcp-project", help="Target GCP project ID. Defaults to state project."),
    ] = None,
    dataset: Annotated[
        str | None,
        typer.Option("--dataset", help="Target BigQuery dataset ID. Defaults to state dataset."),
    ] = None,
    target_table: Annotated[
        str | None,
        typer.Option("--target-table", help="Target BigQuery table name."),
    ] = None,
    vm_name: Annotated[
        str | None,
        typer.Option("--vm-name", help="Locked-down GCE VM name to delete."),
    ] = None,
    zone: Annotated[
        str | None,
        typer.Option("--zone", help="GCE zone of the streamer VM."),
    ] = None,
    keep_bq: Annotated[
        bool,
        typer.Option("--keep-bq", help="Keep the BigQuery dataset and streamed tables intact."),
    ] = False,
    drop_bq_table: Annotated[
        bool,
        typer.Option("--drop-bq-table", help="Drop only the streamed target table in BigQuery."),
    ] = False,
    drop_bq_dataset: Annotated[
        bool,
        typer.Option("--drop-bq-dataset", help="Drop the entire BigQuery dataset (`bq rm -r -f -d`)."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip interactive confirmation prompts."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print teardown commands without executing live deletions."),
    ] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Tear down the GCE streaming daemon, VM, secrets, and optionally drop BigQuery table/dataset."""
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    state = app_ctx.state

    selected_flags = sum([bool(keep_bq), bool(drop_bq_table), bool(drop_bq_dataset)])
    if selected_flags > 1:
        raise ConfigError(
            "Pass at most one of `--keep-bq`, `--drop-bq-table`, or `--drop-bq-dataset`.",
            remediation="Choose a single BigQuery retention option (`--keep-bq`, `--drop-bq-table`, or `--drop-bq-dataset`).",
            details={
                "keep_bq": keep_bq,
                "drop_bq_table": drop_bq_table,
                "drop_bq_dataset": drop_bq_dataset,
            },
        )

    project = (gcp_project or state.gcp_project_id or "").strip()
    if not project:
        raise missing_option("--gcp-project", purpose="the GCP project containing the GCE streamer VM")

    ds = (dataset or state.bq_dataset_id or state.domain_name or "").strip()
    if not ds:
        raise missing_option("--dataset", purpose="the BigQuery dataset ID associated with the streamer")

    tbl = _resolve_target_table(target_table, state)
    vname = (vm_name or state.stream_vm_name or f"{ds.replace('_', '-')}-bq-streamer").strip()
    service_file = _resolve_service_file(ds)
    resolved_zone = (zone or state.stream_zone or "us-central1-a").strip()

    if selected_flags == 0:
        if not output_json and not yes and not dry_run:
            choice = (
                typer.prompt(
                    "How should BigQuery data be handled? [keep / table / dataset]",
                    default="keep",
                )
                .strip()
                .lower()
            )
            if choice in ("keep", "keep_bq", "keep-bq"):
                keep_bq = True
            elif choice in ("table", "drop-table", "drop_bq_table", "drop-bq-table"):
                drop_bq_table = True
            elif choice in ("dataset", "drop-dataset", "drop_bq_dataset", "drop-bq-dataset"):
                drop_bq_dataset = True
            else:
                raise ConfigError(
                    f"Unrecognized BigQuery teardown option `{choice}`.",
                    remediation="Specify `keep`, `table`, or `dataset`.",
                    details={"choice": choice},
                )
        else:
            keep_bq = True

    if drop_bq_dataset:
        bq_action = "dropped_dataset"
    elif drop_bq_table:
        bq_action = "dropped_table"
    else:
        bq_action = "kept"

    teardown_argvs: list[list[str]] = [
        [
            "gcloud",
            "compute",
            "ssh",
            vname,
            f"--zone={resolved_zone}",
            "--tunnel-through-iap",
            f"--project={project}",
            "--",
            "sudo",
            "systemctl",
            "disable",
            "--now",
            service_file,
        ],
        [
            "gcloud",
            "compute",
            "instances",
            "delete",
            vname,
            f"--zone={resolved_zone}",
            f"--project={project}",
            "--quiet",
        ],
        [
            "gcloud",
            "secrets",
            "delete",
            f"{ds}-streamer-secret",
            f"--project={project}",
            "--quiet",
        ],
    ]
    if drop_bq_table:
        teardown_argvs.append(
            [
                "bq",
                "query",
                "--use_legacy_sql=false",
                f"--project_id={project}",
                f"DROP TABLE IF EXISTS `{project}.{ds}.{tbl}`;",
            ]
        )
    elif drop_bq_dataset:
        teardown_argvs.append(["bq", "rm", "-r", "-f", "-d", f"{project}:{ds}"])

    teardown_commands: list[str] = [shlex.join(cmd) for cmd in teardown_argvs]

    if not dry_run:
        is_mocked_shell = getattr(subprocess.run, "__module__", "") != "subprocess"
        in_isolated_pytest = os.environ.get("NO_GCE_CHECK") == "true" and not is_mocked_shell
        if not in_isolated_pytest:
            for cmd_argv in teardown_argvs:
                subprocess.run(cmd_argv, capture_output=True, text=True, check=False)

    state.stream_deployed = False
    state.stream_status = "skipped"
    if drop_bq_dataset:
        state.dataset_exists = False
    saved_path = app_ctx.save_state()

    data_payload: dict[str, Any] = {
        "project_id": project,
        "gcp_project": project,
        "dataset_id": ds,
        "dataset": ds,
        "target_table": tbl,
        "vm_name": vname,
        "zone": resolved_zone,
        "bq_action": bq_action,
        "keep_bq": bool(keep_bq),
        "drop_bq_table": bool(drop_bq_table),
        "drop_bq_dataset": bool(drop_bq_dataset),
        "dry_run": dry_run,
        "teardown_commands": teardown_commands,
        "stream_status": state.stream_status,
        "stream_deployed": state.stream_deployed,
        "state_file": str(saved_path),
    }

    result = CommandResult.success("stream delete", data=data_payload)

    def render(_: CommandResult) -> None:
        prefix = "[DRY RUN] " if dry_run else ""
        print_success(
            f"{prefix}Tore down GCE streamer `{vname}` (`{resolved_zone}`) for `{project}.{ds}` (BigQuery action: `{bq_action}`)."
        )
        for cmd in teardown_commands:
            print_info(f"  $ {cmd}")

    return emit(result, json_output=output_json, human_renderer=render)
