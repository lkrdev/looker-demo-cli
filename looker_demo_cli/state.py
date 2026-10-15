"""Persisted execution state shared across ``demo-create`` invocations.

Each gated subcommand reads the state left by the previous one and writes its
own results back, which is what lets an agent run the gates as separate calls
without re-supplying every flag. :class:`~looker_demo_cli.context.AppContext`
owns loading and saving; commands should not call these functions directly.

Three properties matter here, and each is enforced rather than assumed:

**Versioning.** The file carries a :data:`STATE_SCHEMA_VERSION`. A file written
by a *newer* CLI is rejected outright rather than silently misread, because a
partially-understood state file produces a demo built against the wrong project.

**Atomicity.** Writes go to a temporary file in the same directory and are then
``os.replace``d into position. Several subagents may run gated commands
concurrently, and the previous implementation's plain ``write_text`` could be
interrupted mid-write, leaving truncated JSON that the loader then silently
discarded.

**Loud failure.** A corrupt state file raises :class:`StateError`. It used to be
swallowed by a bare ``except: pass`` that returned a default state, so a
corrupted file looked exactly like a fresh start -- and the run continued
against default values nobody chose.
"""

from __future__ import annotations

import json
import os
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from looker_demo_cli.config import DEFAULT_GCP_PROJECT, DEFAULT_LOOKER_INSTANCE_URL
from looker_demo_cli.errors import StateError, missing_option

#: Bumped whenever a change to :class:`FlowState` cannot be absorbed by
#: pydantic's defaults -- i.e. a field is renamed, or its meaning changes. Adding
#: an optional field does not require a bump, since older files simply take the
#: new default.
STATE_SCHEMA_VERSION: Final[int] = 1

STATE_FILE_NAME: Final[str] = ".demo-state.json"


class GateLifecycle(StrEnum):
    """Standardized lifecycle state for any pipeline gate."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class GateRecord(BaseModel):
    """Per-gate progress record stored in :attr:`FlowState.gates`."""

    status: GateLifecycle = GateLifecycle.PENDING
    substage: str | None = None
    explicit: bool = False

    @property
    def is_satisfied(self) -> bool:
        """Whether this gate is complete or explicitly skipped."""
        return self.status in (GateLifecycle.COMPLETED, GateLifecycle.SKIPPED)


class FlowState(BaseModel):
    """Accumulated results of the demo-create gates.

    Note:
        Fields are additive across gates and every one has a default, so a
        partially completed run always deserialises. Unknown keys are ignored
        (pydantic's default), which is what makes removing a field
        backwards-compatible with state files written by older versions.

    Note:
        Identity fields -- the dataset, Looker project, model, and connection --
        default to ``None``, **not** to a placeholder string. An earlier version
        defaulted three of them to the literal ``"logistics_analytics"``, which
        meant a command run with no arguments silently targeted a project nobody
        had named, and made the final arm of every ``flag or state.field or ...``
        fallback chain unreachable. ``None`` forces the caller to either supply
        a value or receive a clear error.
    """

    #: Version of the on-disk format this state was written with.
    schema_version: int = STATE_SCHEMA_VERSION

    # Project & Environment
    gcp_project_id: str = DEFAULT_GCP_PROJECT
    gcp_account: str | None = None
    gcp_location: str = "US"
    looker_instance_url: str = DEFAULT_LOOKER_INSTANCE_URL
    looker_account: str | None = None
    looker_connection_name: str | None = None

    # Gate 0 outcome. Recorded so that `demo-create status` can distinguish
    # "the environment audit passed" from "it was never run", which is not
    # otherwise observable from any other field.
    precheck_passed: bool = False
    looker_authenticated: bool | None = None
    targets_confirmed: bool = False
    deployment_mode: Literal["full", "bq_only"] = "full"

    # Dataset & Intent
    dataset_exists: bool = False
    bq_dataset_id: str | None = None
    existing_tables: list[str] = Field(default_factory=list)
    demo_scope: Literal["internal_looker", "external_embed"] = "internal_looker"
    data_source_mode: Literal["synthetic", "existing"] = "synthetic"
    catalog_snapshot_path: Path | None = None
    catalog_coverage_pct: float | None = None
    catalog_profile: str | None = None
    catalog_status: Literal["pending", "seeded", "inspected", "synced", "skipped"] = "pending"

    # Domain & Synthesis (Gate 1 Co-Design & Approval)
    domain_name: str | None = None
    schema_file_path: Path | None = None
    schema_proposed: bool = False
    schema_approved: bool = False
    approved_row_count: int | None = None
    generated_parquet_dir: Path | None = None
    generated_tables: list[str] = Field(default_factory=list)

    # Looker & LookML (Gate 2 & Gate 3)
    looker_project_name: str | None = None
    lookml_model_name: str | None = None
    primary_explore_name: str | None = None
    lookml_output_dir: Path | None = None
    lookml_layered: bool = False
    polish_certified: bool = False
    optimizer_status: Literal["pending", "applied", "skipped"] = "pending"
    deployed_dashboard_id: str | None = None
    deployed_dashboard_url: str | None = None
    critique_approved: bool = False

    # Embed Demo (Gate 6)
    embed_workspace_dir: Path | None = None
    embed_portal_url: str | None = None
    embed_status: Literal["pending", "scaffolded", "skipped"] = "pending"
    embed_group_id: str | None = None
    embed_folder_id: str | None = None
    embed_themes_created: list[str] = Field(default_factory=list)
    embed_dashboard_moved: bool = False
    embed_agent_shared: bool = False
    embed_allowlist_configured: bool = False

    # Conversational Analytics Agent & Gemini Enterprise (Gate 4 & Gate 5)
    ca_agent_id: str | None = None
    ca_agent_name: str | None = None
    ca_agent_status: Literal["pending", "created", "skipped"] = "pending"
    published_to_ge: bool = False
    ge_publish_status: Literal["pending", "published", "skipped"] = "pending"
    golden_queries_count: int = 0
    ge_configured: bool = False
    ge_project_id: str | None = None
    ge_instance_id: str | None = None
    ge_location: str | None = None
    ge_service_account_email: str | None = None

    # Live BigQuery Streaming & CDC (Gate 14 / gate_7_stream)
    stream_status: Literal["pending", "planned", "deployed", "skipped"] = "pending"
    stream_planned: bool = False
    stream_deployed: bool = False
    stream_plan_path: Path | None = None
    stream_dir: Path | None = None
    stream_frequency: str = "1m"
    stream_crud_mode: Literal["insert_only", "upsert_cdc", "full_crud"] = "upsert_cdc"
    stream_backfill_mode: Literal["skip", "local", "vm", "sample_mirror"] = "skip"
    stream_target_table: str | None = None
    stream_log_name: str | None = None
    stream_vm_name: str | None = None
    stream_zone: str = "us-central1-a"

    # Outcome of the most recent command
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    error_message: str | None = None

    # Unified per-gate lifecycle tracker (Hybrid state model)
    gates: dict[str, GateRecord] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _post_init_sync_gates(self) -> FlowState:
        """Backfill :attr:`gates` from legacy flat fields on load/construction."""
        self.sync_gates()
        return self

    def sync_gates(self) -> None:
        """Synchronize :attr:`gates` entries from flat state fields when not already overridden."""
        inferred: list[tuple[str, GateLifecycle, str | None]] = [
            ("gate_0a_precheck", GateLifecycle.COMPLETED if self.precheck_passed else GateLifecycle.PENDING, None),
            (
                "gate_0b_confirm_targets",
                GateLifecycle.COMPLETED if self.targets_confirmed else GateLifecycle.PENDING,
                None,
            ),
            (
                "gate_1a_propose_schema",
                GateLifecycle.COMPLETED if (self.schema_proposed or self.dataset_exists) else GateLifecycle.PENDING,
                None,
            ),
            (
                "gate_1b_approve_schema",
                GateLifecycle.COMPLETED if (self.schema_approved or self.dataset_exists) else GateLifecycle.PENDING,
                None,
            ),
            ("gate_1c_generate_data", GateLifecycle.COMPLETED if self.dataset_exists else GateLifecycle.PENDING, None),
            (
                "gate_1d_catalog",
                GateLifecycle.SKIPPED
                if self.catalog_status == "skipped"
                else (
                    GateLifecycle.COMPLETED
                    if (
                        self.catalog_status in ("seeded", "inspected", "synced")
                        or self.catalog_snapshot_path is not None
                    )
                    else GateLifecycle.PENDING
                ),
                self.catalog_status if self.catalog_status not in ("pending", "skipped") else None,
            ),
            (
                "gate_2a_lookml_model",
                GateLifecycle.COMPLETED if self.lookml_output_dir is not None else GateLifecycle.PENDING,
                None,
            ),
            (
                "gate_2b_certify_polish",
                GateLifecycle.COMPLETED if self.polish_certified else GateLifecycle.PENDING,
                None,
            ),
            (
                "gate_3a_optimize",
                GateLifecycle.SKIPPED
                if self.optimizer_status == "skipped"
                else (GateLifecycle.COMPLETED if self.optimizer_status == "applied" else GateLifecycle.PENDING),
                None,
            ),
            (
                "gate_3b_deploy",
                GateLifecycle.COMPLETED if self.deployed_dashboard_url is not None else GateLifecycle.PENDING,
                None,
            ),
            ("gate_3c_critique", GateLifecycle.COMPLETED if self.critique_approved else GateLifecycle.PENDING, None),
            (
                "gate_4_agent",
                GateLifecycle.SKIPPED
                if self.ca_agent_status == "skipped"
                else (
                    GateLifecycle.COMPLETED
                    if (self.ca_agent_id is not None or self.ca_agent_status == "created")
                    else GateLifecycle.PENDING
                ),
                None,
            ),
            (
                "gate_5_publish",
                GateLifecycle.SKIPPED
                if (self.ge_publish_status == "skipped" or self.ca_agent_status == "skipped")
                else (
                    GateLifecycle.COMPLETED
                    if (self.published_to_ge or self.ge_publish_status == "published")
                    else GateLifecycle.PENDING
                ),
                None,
            ),
            (
                "gate_6_embed",
                GateLifecycle.SKIPPED
                if self.embed_status == "skipped"
                else (
                    GateLifecycle.COMPLETED
                    if (self.embed_workspace_dir is not None or self.embed_status == "scaffolded")
                    else GateLifecycle.PENDING
                ),
                None,
            ),
            (
                "gate_7_stream",
                GateLifecycle.SKIPPED
                if self.stream_status == "skipped"
                else (
                    GateLifecycle.COMPLETED
                    if (self.stream_status == "deployed" or self.stream_deployed)
                    else (
                        GateLifecycle.IN_PROGRESS
                        if (self.stream_status == "planned" or self.stream_planned)
                        else GateLifecycle.PENDING
                    )
                ),
                "planned"
                if (self.stream_status == "planned" or (self.stream_planned and not self.stream_deployed))
                else None,
            ),
        ]
        strict_flags_inferred: dict[str, GateLifecycle] = {
            "gate_1d_catalog": (
                GateLifecycle.SKIPPED
                if self.catalog_status == "skipped"
                else (
                    GateLifecycle.COMPLETED
                    if self.catalog_status in ("seeded", "inspected", "synced")
                    else GateLifecycle.PENDING
                )
            ),
            "gate_2a_lookml_model": GateLifecycle.PENDING,
            "gate_3b_deploy": GateLifecycle.PENDING,
            "gate_4_agent": (
                GateLifecycle.SKIPPED
                if self.ca_agent_status == "skipped"
                else (GateLifecycle.COMPLETED if self.ca_agent_status == "created" else GateLifecycle.PENDING)
            ),
            "gate_6_embed": (
                GateLifecycle.SKIPPED
                if self.embed_status == "skipped"
                else (GateLifecycle.COMPLETED if self.embed_status == "scaffolded" else GateLifecycle.PENDING)
            ),
        }
        for gate_id, lifecycle, substage in inferred:
            existing = self.gates.get(gate_id)
            if existing is None:
                if lifecycle != GateLifecycle.PENDING or substage is not None:
                    self.gates[gate_id] = GateRecord(status=lifecycle, substage=substage)
            elif existing.status == GateLifecycle.PENDING:
                effective = strict_flags_inferred.get(gate_id, lifecycle) if existing.explicit else lifecycle
                if effective != GateLifecycle.PENDING:
                    self.gates[gate_id] = GateRecord(status=effective, substage=substage)

    def _reset_legacy_gate_fields(self, gate_id: str) -> None:
        """Reset legacy flat completion flags when a gate is explicitly invalidated to PENDING."""
        if gate_id == "gate_0a_precheck":
            self.precheck_passed = False
        elif gate_id == "gate_0b_confirm_targets":
            self.targets_confirmed = False
        elif gate_id == "gate_1a_propose_schema":
            self.schema_proposed = False
        elif gate_id == "gate_1b_approve_schema":
            self.schema_approved = False
        elif gate_id == "gate_1c_generate_data":
            self.dataset_exists = False
        elif gate_id == "gate_1d_catalog":
            self.catalog_status = "pending"
        elif gate_id == "gate_2b_certify_polish":
            self.polish_certified = False
        elif gate_id == "gate_3a_optimize":
            self.optimizer_status = "pending"
        elif gate_id == "gate_3c_critique":
            self.critique_approved = False
        elif gate_id == "gate_4_agent":
            self.ca_agent_status = "pending"
        elif gate_id == "gate_5_publish":
            self.published_to_ge = False
            self.ge_publish_status = "pending"
        elif gate_id == "gate_6_embed":
            self.embed_status = "pending"
        elif gate_id == "gate_7_stream":
            self.stream_status = "pending"
            self.stream_planned = False
            self.stream_deployed = False

    def gate_status(self, gate_id: str) -> GateLifecycle:
        """Return the current :class:`GateLifecycle` for ``gate_id``."""
        self.sync_gates()
        rec = self.gates.get(gate_id)
        return rec.status if rec is not None else GateLifecycle.PENDING

    def is_gate_satisfied(self, gate_id: str) -> bool:
        """Return True if ``gate_id`` is completed or skipped."""
        self.sync_gates()
        rec = self.gates.get(gate_id)
        return rec.is_satisfied if rec is not None else False

    def mark_gate(
        self,
        gate_id: str,
        status: GateLifecycle = GateLifecycle.COMPLETED,
        *,
        substage: str | None = None,
        invalidate_downstream: tuple[str, ...] = (),
    ) -> None:
        """Record a gate lifecycle transition and optionally reset downstream gates to pending."""
        if status == GateLifecycle.PENDING:
            self._reset_legacy_gate_fields(gate_id)
        self.gates[gate_id] = GateRecord(status=status, substage=substage, explicit=True)
        for downstream_id in invalidate_downstream:
            self._reset_legacy_gate_fields(downstream_id)
            self.gates[downstream_id] = GateRecord(status=GateLifecycle.PENDING, explicit=True)

    def resolve(self, explicit: Any, *state_fields: str, default: Any = None) -> Any:
        """Return ``explicit`` if non-blank, else the first non-blank field in ``state_fields``, else ``default``."""
        for candidate in (explicit, *(getattr(self, field_name) for field_name in state_fields)):
            if candidate is None:
                continue
            if isinstance(candidate, str):
                stripped = candidate.strip()
                if stripped:
                    return stripped
            else:
                return candidate
        return default

    def require(
        self,
        explicit: Any,
        *state_fields: str,
        option: str,
        purpose: str,
        hint: str | None = None,
        write_to: str | None = None,
    ) -> Any:
        """Resolve a required option from CLI flag or state, raising :func:`missing_option` if absent."""
        val = self.resolve(explicit, *state_fields)
        if val is None or val == "":
            raise missing_option(option, purpose=purpose, hint=hint)
        if write_to:
            setattr(self, write_to, val)
        return val


def get_default_state_path(scratch_dir: Path | None = None) -> Path:
    """Find or determine the target state file path.

    Args:
        scratch_dir: Optional secondary location to check when the working
            directory has no state file.

    Returns:
        The existing state file if one is found, otherwise the path one would
        be created at in the working directory.
    """
    cwd_file = Path.cwd() / STATE_FILE_NAME
    if cwd_file.exists():
        return cwd_file
    if scratch_dir and (scratch_dir / STATE_FILE_NAME).exists():
        return scratch_dir / STATE_FILE_NAME
    return cwd_file


def save_flow_state(state: FlowState, path: Path | None = None) -> Path:
    """Atomically persist the flow state as JSON.

    The write goes to a temporary file in the *same directory* as the target and
    is then moved into place with :func:`os.replace`, which is atomic on POSIX.
    Same-directory matters: ``os.replace`` across filesystems is not atomic, and
    a temp file in ``/tmp`` frequently is on a different one.

    Args:
        state: The state to write.
        path: Explicit destination. Defaults to the working directory.

    Returns:
        The path written to.

    Raises:
        StateError: The state could not be written.
    """
    target = path or (Path.cwd() / STATE_FILE_NAME)
    target.parent.mkdir(parents=True, exist_ok=True)

    state.sync_gates()
    payload = state.model_dump_json(indent=2)
    tmp_path: str | None = None
    try:
        # delete=False because we move the file rather than letting it be
        # cleaned up; the finally block covers the failure path.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f"{STATE_FILE_NAME}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = handle.name
            handle.write(payload)
            handle.flush()
            # Without the fsync a crash between replace() and the kernel
            # flushing can leave an empty file where valid JSON is expected.
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
        tmp_path = None
    except OSError as exc:
        raise StateError(
            f"Could not write state file: {target}",
            remediation="Check that the directory exists and is writable.",
            details={"state_file": str(target), "reason": str(exc)},
        ) from exc
    finally:
        if tmp_path is not None:
            Path(tmp_path).unlink(missing_ok=True)

    return target


def _migrate(raw: dict[str, Any], *, source: Path) -> dict[str, Any]:
    """Bring a decoded state payload up to the current schema version.

    Args:
        raw: The decoded JSON object.
        source: Path it came from, for error messages.

    Returns:
        The payload, migrated in place where necessary.

    Raises:
        StateError: The file was written by a newer, unknown schema version.
    """
    # Absent means pre-0.3.0, which predates versioning. Those files are a
    # strict subset of version 1, so they load as-is.
    found = raw.get("schema_version", 0)

    if not isinstance(found, int):
        raise StateError(
            f"State file has a non-numeric schema_version: {found!r}",
            remediation=f"Delete {source} to start from a clean state.",
            details={"state_file": str(source), "schema_version": repr(found)},
        )

    if found > STATE_SCHEMA_VERSION:
        raise StateError(
            f"State file schema version {found} is newer than this CLI supports ({STATE_SCHEMA_VERSION}).",
            remediation=(
                f"Upgrade with `uv tool upgrade looker-demo-cli`, or delete {source} to start from a clean state."
            ),
            details={
                "state_file": str(source),
                "found_version": found,
                "supported_version": STATE_SCHEMA_VERSION,
            },
        )

    # No backwards migrations are needed yet: version 0 -> 1 added only fields
    # with defaults. When one is, branch on `found` here.
    raw["schema_version"] = STATE_SCHEMA_VERSION
    return raw


def load_flow_state(path: Path | None = None, scratch_dir: Path | None = None) -> FlowState:
    """Load the flow state, or return a fresh one when no file exists.

    Args:
        path: Explicit state file location.
        scratch_dir: Secondary location to search when ``path`` is not given.

    Returns:
        The persisted state, or a default :class:`FlowState` when no file exists.

    Raises:
        StateError: The file exists but is unreadable, is not valid JSON, is not
            a JSON object, or was written by a newer schema version. This is
            deliberately *not* silent: a corrupt state file used to be
            indistinguishable from a fresh start, so a run would quietly
            continue against default values nobody chose.
    """
    target = path or get_default_state_path(scratch_dir=scratch_dir)
    if not target.exists():
        return FlowState()

    try:
        content = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateError(
            f"Could not read state file: {target}",
            remediation="Check file permissions, or delete it to start from a clean state.",
            details={"state_file": str(target), "reason": str(exc)},
        ) from exc

    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise StateError(
            f"State file is not valid JSON: {target}",
            remediation=f"Delete {target} to start from a clean state.",
            details={"state_file": str(target), "reason": str(exc)},
        ) from exc

    if not isinstance(raw, dict):
        raise StateError(
            f"State file must contain a JSON object, found {type(raw).__name__}: {target}",
            remediation=f"Delete {target} to start from a clean state.",
            details={"state_file": str(target)},
        )

    raw = _migrate(raw, source=target)

    try:
        return FlowState.model_validate(raw)
    except PydanticValidationError as exc:
        raise StateError(
            f"State file does not match the expected schema: {target}",
            remediation=f"Delete {target} to start from a clean state.",
            details={"state_file": str(target), "reason": str(exc.error_count()) + " validation errors"},
        ) from exc
