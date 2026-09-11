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
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from looker_demo_cli.config import DEFAULT_GCP_PROJECT, DEFAULT_LOOKER_INSTANCE_URL
from looker_demo_cli.errors import StateError

#: Bumped whenever a change to :class:`FlowState` cannot be absorbed by
#: pydantic's defaults -- i.e. a field is renamed, or its meaning changes. Adding
#: an optional field does not require a bump, since older files simply take the
#: new default.
STATE_SCHEMA_VERSION: Final[int] = 1

STATE_FILE_NAME: Final[str] = ".demo-state.json"


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

    # Dataset & Intent
    dataset_exists: bool = False
    bq_dataset_id: str | None = None
    existing_tables: list[str] = Field(default_factory=list)
    demo_scope: Literal["internal_looker", "external_embed"] = "internal_looker"

    # Domain & Synthesis
    domain_name: str | None = None
    generated_parquet_dir: Path | None = None
    generated_tables: list[str] = Field(default_factory=list)

    # Looker & LookML
    looker_project_name: str | None = None
    lookml_model_name: str | None = None
    lookml_output_dir: Path | None = None
    deployed_dashboard_id: str | None = None
    deployed_dashboard_url: str | None = None

    # Embed Demo
    embed_workspace_dir: Path | None = None
    embed_portal_url: str | None = None

    # Conversational Analytics Agent & Gemini Enterprise
    ca_agent_id: str | None = None
    ca_agent_name: str | None = None
    published_to_ge: bool = False
    golden_queries_count: int = 0
    ge_configured: bool = False
    ge_project_id: str | None = None
    ge_instance_id: str | None = None
    ge_location: str | None = None
    ge_service_account_email: str | None = None

    # Outcome of the most recent command
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    error_message: str | None = None


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
