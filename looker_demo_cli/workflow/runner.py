from __future__ import annotations

from pathlib import Path
from typing import Optional
from looker_demo_cli.generators.lookml_generator import LookMLTableSpec
from looker_demo_cli.precheck.gcp_auth import select_gcp_credentials
from looker_demo_cli.utils.bigquery_client import BigQueryHelper
from looker_demo_cli.utils.console import console, print_banner, print_error, print_info, print_success
from looker_demo_cli.workflow.state import FlowState
from looker_demo_cli.workflow.steps import (
    run_bigquery_upload_step,
    run_dataset_decision_step,
    run_embed_scaffold_step,
    run_looker_deploy_step,
    run_lookml_generation_step,
    run_schema_synthesis_step,
)


class FlowRunner:
    """Coordinates execution of the 6 deterministic demo creation steps."""

    def __init__(self, state: FlowState, scratch_dir: Path, target_base_dir: Path):
        self.state = state
        self.scratch_dir = scratch_dir
        self.target_base_dir = target_base_dir

    def run(self) -> FlowState:
        print_banner(
            "LOOKER DEMO CREATION PIPELINE",
            f"Project: {self.state.looker_project_name} | Scope: {self.state.demo_scope} | BQ Dataset: {self.state.bq_dataset_id}",
        )

        creds, chosen_project = select_gcp_credentials(
            preferred_account=self.state.gcp_account,
            preferred_project=self.state.gcp_project_id,
        )
        self.state.gcp_project_id = chosen_project

        bq_helper = BigQueryHelper(
            project_id=self.state.gcp_project_id,
            credentials=creds,
            location=self.state.gcp_location,
        )

        # 1. Dataset Decision
        self.state = run_dataset_decision_step(self.state, bq_helper)

        # 2. Schema Synthesis
        self.state = run_schema_synthesis_step(self.state, self.scratch_dir)

        # 3. BigQuery Upload
        self.state = run_bigquery_upload_step(self.state, bq_helper)

        # 4. LookML Generation
        table_specs = []
        tables_to_model = self.state.generated_tables or self.state.existing_tables
        for t in tables_to_model:
            table_specs.append(LookMLTableSpec(table_name=t, table_type="fact" if "fct" in t or "order" in t or "event" in t else "dimension"))

        self.state = run_lookml_generation_step(self.state, self.scratch_dir, table_specs)

        # 5. Looker Deploy (delegates directly to lkr tools lookml push ... --deploy)
        self.state = run_looker_deploy_step(self.state)

        # 6. Embed Scaffold
        self.state = run_embed_scaffold_step(self.state, self.target_base_dir)

        self.state.status = "completed"
        print_success("Demo creation flow completed successfully!")
        return self.state
