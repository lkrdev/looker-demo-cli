# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class FlowState(BaseModel):
    """Execution state for the demo-create workflow."""

    # Project & Environment
    gcp_project_id: str = "looker-demo-392616"
    gcp_account: Optional[str] = None
    gcp_location: str = "US"
    looker_instance_url: str = "https://looker.lukapuka.co"
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

    # Step lifecycle
    current_step: int = 1
    total_steps: int = 6
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    error_message: Optional[str] = None
