from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


from looker_demo_cli.config import DEFAULT_GCP_PROJECT, DEFAULT_LOOKER_INSTANCE_URL


class FlowState(BaseModel):
    """Execution state for the demo-create workflow."""

    # Project & Environment
    gcp_project_id: str = DEFAULT_GCP_PROJECT
    gcp_account: Optional[str] = None
    gcp_location: str = "US"
    looker_instance_url: str = DEFAULT_LOOKER_INSTANCE_URL
    looker_account: Optional[str] = None
    looker_connection_name: str = "default_bigquery_connection"

    # Dataset & Intent
    dataset_exists: bool = False
    bq_dataset_id: str = "logistics_analytics"
    existing_tables: List[str] = Field(default_factory=list)
    action_intent: Literal["create_new_dataset", "augment_existing_dataset", "model_existing_only"] = "create_new_dataset"
    demo_scope: Literal["internal_looker", "external_embed"] = "internal_looker"

    # Domain & Synthesis
    domain_name: Optional[str] = None
    domain_description: Optional[str] = None
    generated_parquet_dir: Optional[Path] = None
    generated_tables: List[str] = Field(default_factory=list)

    # Looker & LookML
    looker_project_name: str = "logistics_analytics"
    lookml_model_name: str = "logistics_analytics"
    lookml_output_dir: Optional[Path] = None
    deployed_dashboard_id: Optional[str] = None
    deployed_dashboard_url: Optional[str] = None

    # Embed Demo
    embed_workspace_dir: Optional[Path] = None
    embed_portal_url: Optional[str] = None

    # Conversational Analytics Agent & Gemini Enterprise
    ca_agent_id: Optional[str] = None
    ca_agent_name: Optional[str] = None
    published_to_ge: bool = False
    golden_queries_count: int = 0
    ge_configured: bool = False
    ge_project_id: Optional[str] = None
    ge_instance_id: Optional[str] = None
    ge_location: Optional[str] = None
    ge_service_account_email: Optional[str] = None

    # Step lifecycle
    current_step: int = 1
    total_steps: int = 7
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    error_message: Optional[str] = None


STATE_FILE_NAME = ".demo-state.json"


def get_default_state_path(scratch_dir: Optional[Path] = None) -> Path:
    """Find or determine the target state file path."""
    cwd_file = Path.cwd() / STATE_FILE_NAME
    if cwd_file.exists():
        return cwd_file
    if scratch_dir and (scratch_dir / STATE_FILE_NAME).exists():
        return scratch_dir / STATE_FILE_NAME
    return cwd_file


def save_flow_state(state: FlowState, path: Optional[Path] = None) -> Path:
    """Serialize FlowState to a JSON file."""
    target = path or (Path.cwd() / STATE_FILE_NAME)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return target


def load_flow_state(path: Optional[Path] = None, scratch_dir: Optional[Path] = None) -> FlowState:
    """Load FlowState from file if present, otherwise return fresh default FlowState."""
    target = path or get_default_state_path(scratch_dir=scratch_dir)
    if target.exists():
        try:
            content = target.read_text(encoding="utf-8")
            return FlowState.model_validate_json(content)
        except Exception:
            pass
    return FlowState()

