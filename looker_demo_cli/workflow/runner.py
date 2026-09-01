from __future__ import annotations

from pathlib import Path
from typing import Optional
from looker_demo_cli.generators.lookml_generator import LookMLTableSpec
from looker_demo_cli.precheck.gcp_auth import select_gcp_credentials
from looker_demo_cli.utils.bigquery_client import BigQueryHelper
from looker_demo_cli.utils.console import print_banner, print_success
from looker_demo_cli.workflow.state import FlowState
from looker_demo_cli.workflow.steps import (
    run_bigquery_upload_step,
    run_ca_agent_step,
    run_dataset_decision_step,
    run_embed_scaffold_step,
    run_looker_deploy_step,
    run_lookml_generation_step,
    run_schema_synthesis_step,
)


def extract_table_specs_from_parquet_dir(parquet_dir: Path) -> list[LookMLTableSpec]:
    import pyarrow.parquet as pq
    specs = []
    parquet_files = sorted(list(parquet_dir.glob("*.parquet")))
    all_table_names = [f.stem for f in parquet_files]

    for p_file in parquet_files:
        t_name = p_file.stem
        table = pq.read_table(p_file)
        schema_fields = {}
        for col_name, col_type in zip(table.schema.names, table.schema.types):
            type_str = str(col_type).lower()
            if "int" in type_str:
                schema_fields[col_name] = "INT64"
            elif "float" in type_str or "double" in type_str:
                schema_fields[col_name] = "FLOAT64"
            elif "bool" in type_str:
                schema_fields[col_name] = "BOOL"
            elif "timestamp" in type_str or "time" in type_str or "date" in type_str:
                schema_fields[col_name] = "TIMESTAMP"
            else:
                schema_fields[col_name] = "STRING"

        is_fact = t_name.startswith("fct_") or "transaction" in t_name or "alert" in t_name or "order" in t_name
        pk = None
        for col in schema_fields:
            if col == f"{t_name.replace('dim_', '').replace('fct_', '')[:-1]}_id" or col == f"{t_name.replace('dim_', '').replace('fct_', '')}_id" or col == "id":
                pk = col
                break
        if not pk:
            for col in schema_fields:
                if col.endswith("_id") and (col.startswith(t_name.split("_")[-1][:-1]) or col.startswith(t_name.split("_")[-1])):
                    pk = col
                    break
        if not pk and any(col.endswith("_id") for col in schema_fields):
            pk = [col for col in schema_fields if col.endswith("_id")][0]

        # Foreign keys
        fks = {}
        for col in schema_fields:
            if col.endswith("_id") and col != pk:
                ref = col[:-3]
                parent_table = None
                for cand in all_table_names:
                    if cand == t_name:
                        continue
                    cand_norm = cand.replace("dim_", "").replace("fct_", "")
                    if cand_norm == ref or cand_norm == f"{ref}s" or cand_norm == f"{ref}es" or (ref.endswith("y") and cand_norm == f"{ref[:-1]}ies"):
                        parent_table = cand
                        break
                if parent_table:
                    fks[col] = f"{parent_table}.{col}"

        specs.append(
            LookMLTableSpec(
                table_name=t_name,
                table_type="fact" if is_fact else "dimension",
                schema_fields=schema_fields,
                primary_key=pk,
                foreign_keys=fks,
            )
        )
    return specs


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
        if self.state.generated_parquet_dir and any(self.state.generated_parquet_dir.glob("*.parquet")):
            table_specs = extract_table_specs_from_parquet_dir(self.state.generated_parquet_dir)
        else:
            table_specs = []
            tables_to_model = self.state.generated_tables or self.state.existing_tables
            for t in tables_to_model:
                table_specs.append(LookMLTableSpec(table_name=t, table_type="fact" if "fct" in t or "order" in t or "event" in t else "dimension"))

        self.state = run_lookml_generation_step(self.state, self.scratch_dir, table_specs)

        # 5. Looker Deploy (delegates directly to lkr tools lookml push ... --deploy)
        self.state = run_looker_deploy_step(self.state)

        # 6. Conversational Analytics Agent & Gemini Enterprise
        self.state = run_ca_agent_step(self.state)

        # 7. Embed Scaffold
        self.state = run_embed_scaffold_step(self.state, self.target_base_dir)

        self.state.status = "completed"
        print_success("Demo creation flow completed successfully!")
        return self.state
