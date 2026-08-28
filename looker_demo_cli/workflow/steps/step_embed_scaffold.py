from __future__ import annotations

from pathlib import Path
from looker_demo_cli.generators.embed_scaffolder import EmbedConfigOptions, EmbedScaffolder
from looker_demo_cli.utils.console import print_info, print_step_header, print_success
from looker_demo_cli.workflow.state import FlowState


def run_embed_scaffold_step(state: FlowState, target_base_dir: Path) -> FlowState:
    """Step 6: Scaffold fresh embedded portal workspace if external demo is selected."""
    print_step_header(6, state.total_steps, "External Embed Portal Scaffolding")

    if state.demo_scope != "external_embed":
        print_info("Internal Looker Demo chosen. Skipping external embed scaffolding.")
        return state

    target_dir = target_base_dir / f"looker-embed-{state.looker_project_name}"
    opts = EmbedConfigOptions(
        demo_name=state.looker_project_name,
        target_dir=target_dir,
        brand_name=state.looker_project_name.replace("_", " ").title(),
        brand_title=f"{state.looker_project_name.replace('_', ' ').title()} Intelligence Portal",
        looker_instance_url=state.looker_instance_url,
        looker_project_name=state.looker_project_name,
        lookml_model_name=state.lookml_model_name,
        dashboard_id=f"{state.looker_project_name}::{state.lookml_model_name}_overview",
    )

    scaffolded_dir = EmbedScaffolder.scaffold_demo_workspace(opts)
    state.embed_workspace_dir = scaffolded_dir
    state.embed_portal_url = "http://localhost:8008"

    print_success(f"External Embed Portal configured at: `{scaffolded_dir}`")
    return state
