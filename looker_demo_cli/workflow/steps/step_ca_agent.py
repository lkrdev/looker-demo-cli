from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests
import yaml

from looker_demo_cli.config import (
    DEFAULT_LOOKER_CLIENT_ID,
    DEFAULT_LOOKER_CLIENT_SECRET,
    DEFAULT_LOOKER_INSTANCE_URL,
)
from looker_demo_cli.precheck.looker_auth import get_authenticated_oauth_instances
from looker_demo_cli.utils.console import print_error, print_info, print_step_header, print_success, print_warning
from looker_demo_cli.workflow.state import FlowState


def extract_golden_queries_from_dashboards(lookml_dir: Path, default_model: str, default_explore: str) -> List[Dict[str, Any]]:
    """Extract query specifications from LookML dashboard files and convert to golden queries."""
    dash_dir = lookml_dir / "dashboards"
    golden_queries: List[Dict[str, Any]] = []

    if not dash_dir.exists():
        return golden_queries

    dash_files = list(dash_dir.glob("*.dashboard.lookml"))
    for df in dash_files:
        try:
            content = df.read_text(encoding="utf-8")
            parsed = yaml.safe_load(content)
            dash_list = parsed if isinstance(parsed, list) else [parsed]
            for dash_obj in dash_list:
                dash_filters = {
                    f.get("name"): f.get("default_value")
                    for f in dash_obj.get("filters", [])
                    if f.get("default_value")
                }
                elements = dash_obj.get("elements", [])
                for el in elements:
                    title = el.get("title") or el.get("name") or "Key Metric"
                    model = el.get("model") or default_model
                    explore = el.get("explore") or default_explore
                    fields = el.get("fields", [])
                    pivots = el.get("pivots", [])
                    filters = dict(el.get("filters", {}))
                    listen_map = el.get("listen", {})

                    for filter_name, target_field in listen_map.items():
                        if filter_name in dash_filters and target_field not in filters:
                            filters[target_field] = dash_filters[filter_name]

                    if model and explore and fields:
                        # Derive natural language question prompt from title
                        prompt = f"What is the {title.lower()}?"
                        if "trajectory" in title.lower() or "monthly" in title.lower():
                            prompt = f"Show the monthly breakdown and trajectory for {title.lower()}."
                        elif "distribution" in title.lower() or "breakdown" in title.lower():
                            prompt = f"Show the breakdown of {title.lower()}."

                        golden_queries.append({
                            "prompt": prompt,
                            "query": {
                                "model": model,
                                "view": explore,
                                "fields": fields,
                                "pivots": pivots,
                                "filters": filters,
                                "sorts": el.get("sorts", []),
                                "limit": str(el.get("limit", "500")),
                            },
                        })
        except Exception as e:
            print_warning(f"Notice while extracting golden queries from {df.name}: {e}")

    return golden_queries


def generate_default_ca_instructions(project_name: str, model_name: str, primary_explore: str) -> str:
    """Generate domain-specific system instructions focusing on persona, query patterns, and formatting."""
    formatted_name = project_name.replace("_", " ").title()
    return f"""You are an expert Senior Data Analyst specializing in {formatted_name}.
Your job is to answer business questions by querying the `{model_name}` LookML model on the `{primary_explore}` explore.

Business Rules & Query Patterns:
- When users ask about revenue, financial performance, or core transaction volume, use `{primary_explore}` metrics.
- For timeline and trend questions, default to `{primary_explore}.created_date` grouped by month or week.
- Exclude cancelled, deleted, or test records unless specifically requested by the user.

Styling & Response Guidelines:
- Provide direct, executive-ready answers without conversational filler or speculative assumptions.
- Always lead with the top-line takeaway number before displaying supporting data tables or dimensional breakdowns.
- Format currency, percentages, and numerical quantities cleanly with standard symbols and delimiters.
"""


def run_ca_agent_step(state: FlowState, custom_instructions: Optional[str] = None, publish_to_ge: bool = True) -> FlowState:
    """Step 6: Provision Conversational Analytics Agent, register dashboard golden queries, and publish to GE."""
    print_step_header(6, state.total_steps, "Conversational Analytics Agent & Golden Queries Provisioning")

    oauth_instances = get_authenticated_oauth_instances()
    active_oauth = None
    if state.looker_account:
        active_oauth = next((i for i in oauth_instances if i["instance_name"] == state.looker_account), None)
    if not active_oauth:
        active_oauth = next((i for i in oauth_instances if i["is_current"]), None) or (oauth_instances[0] if oauth_instances else None)

    instance_url = (active_oauth["base_url"] if active_oauth else state.looker_instance_url) or DEFAULT_LOOKER_INSTANCE_URL
    state.looker_instance_url = instance_url.rstrip("/")

    headers: Dict[str, str] = {}
    if active_oauth and active_oauth.get("access_token"):
        headers = {"Authorization": f"Bearer {active_oauth['access_token']}"}

    primary_explore = state.generated_tables[0] if state.generated_tables else state.lookml_model_name
    agent_name = f"{state.looker_project_name.replace('_', ' ').title()} Assistant"
    system_instructions = custom_instructions or generate_default_ca_instructions(
        project_name=state.looker_project_name,
        model_name=state.lookml_model_name,
        primary_explore=primary_explore,
    )

    # 1. Extract Golden Queries from generated dashboard files
    golden_queries: List[Dict[str, Any]] = []
    if state.lookml_output_dir and state.lookml_output_dir.exists():
        golden_queries = extract_golden_queries_from_dashboards(
            lookml_dir=state.lookml_output_dir,
            default_model=state.lookml_model_name,
            default_explore=primary_explore,
        )
        print_info(f"Extracted {len(golden_queries)} golden queries from LookML dashboard specifications.")

    # 2. Create Conversational Analytics Agent via Looker Native API
    agent_id: Optional[str] = None
    agent_payload = {
        "name": agent_name,
        "description": f"AI Conversational Analytics Assistant for {state.looker_project_name}",
        "sources": [{"model": state.lookml_model_name, "explore": primary_explore}],
        "context": {"instructions": system_instructions},
        "code_interpreter": True,
    }

    if headers:
        try:
            print_info(f"Creating Looker CA Agent `{agent_name}` via native Looker API...")
            r_agent = requests.post(
                f"{state.looker_instance_url}/api/4.0/agents",
                json=agent_payload,
                headers=headers,
                timeout=15,
            )
            if r_agent.status_code in (200, 201):
                agent_data = r_agent.json()
                agent_id = agent_data.get("id")
                print_success(f"Successfully created Conversational Analytics Agent (ID: `{agent_id}`).")
            else:
                print_warning(f"Could not create agent via REST ({r_agent.status_code}): {r_agent.text[:200]}")
        except Exception as e:
            print_warning(f"Notice while creating CA agent via REST: {e}")

    # Fallback to Code Mode execution if REST was not directly available
    if not agent_id:
        lkr_bin = shutil.which("lkr") or str(Path(sys.executable).parent / "lkr")
        cmd_codemode = [
            lkr_bin,
            "--dev",
        ]
        if active_oauth:
            cmd_codemode.extend(["--oauth-account", active_oauth["instance_name"]])

        py_script = f"""
import json
agent = create_agent(body={json.dumps(agent_payload)})
print(f"AGENT_ID_OUTPUT:{{agent.get('id')}}")
"""
        cmd_codemode.extend(["code-mode", "sandbox", f"--code={py_script}"])
        try:
            res_cm = subprocess.run(cmd_codemode, capture_output=True, text=True, check=True)
            for line in res_cm.stdout.splitlines():
                if "AGENT_ID_OUTPUT:" in line:
                    agent_id = line.split("AGENT_ID_OUTPUT:")[1].strip()
                    print_success(f"Successfully created CA Agent via Code Mode (ID: `{agent_id}`).")
                    break
        except Exception as cm_err:
            print_warning(f"Notice during Code Mode CA Agent creation: {cm_err}")

    if not agent_id:
        print_warning("Could not provision CA Agent automatically. Continuing workflow.")
        return state

    state.ca_agent_id = agent_id
    state.ca_agent_name = agent_name

    # 3. Register Dashboard Golden Queries (3-Step Looker 4.0 Flow)
    created_gq_ids: List[str] = []
    if headers:
        for idx, gq in enumerate(golden_queries, 1):
            try:
                # 3a. Create Base Looker Query to obtain expanded_share_url
                r_q = requests.post(
                    f"{state.looker_instance_url}/api/4.0/queries",
                    json=gq["query"],
                    headers=headers,
                    timeout=10,
                )
                if r_q.status_code not in (200, 201):
                    continue
                q_data = r_q.json()
                answer_url = q_data.get("expanded_share_url") or q_data.get("share_url")
                if not answer_url:
                    continue

                # 3b. Create Golden Query (Looker enforces exactly 1 question per Golden Query)
                r_gq = requests.post(
                    f"{state.looker_instance_url}/api/4.0/golden_queries",
                    json={
                        "questions": [gq["prompt"]],
                        "answer": answer_url,
                        "is_active": True,
                    },
                    headers=headers,
                    timeout=10,
                )
                if r_gq.status_code in (200, 201):
                    gq_id = r_gq.json().get("id")
                    if gq_id is not None:
                        created_gq_ids.append(str(gq_id))
            except Exception as gq_err:
                print_warning(f"Notice while registering golden query {idx}: {gq_err}")

        # 3c. Link Golden Queries to the CA Agent
        if created_gq_ids:
            try:
                r_patch = requests.patch(
                    f"{state.looker_instance_url}/api/4.0/agents/{agent_id}",
                    json={"golden_query_ids": created_gq_ids},
                    headers=headers,
                    timeout=15,
                )
                if r_patch.status_code in (200, 201):
                    state.golden_queries_count = len(created_gq_ids)
                    print_success(f"Registered and linked {len(created_gq_ids)} dashboard Golden Queries to CA Agent `{agent_id}`.")
                else:
                    print_warning(f"Could not link golden queries to agent ({r_patch.status_code}): {r_patch.text[:200]}")
            except Exception as patch_err:
                print_warning(f"Notice while linking golden queries to agent: {patch_err}")

    # 4. Publish Agent to Gemini Enterprise (GE) if requested
    if publish_to_ge:
        print_info(f"Publishing CA Agent `{agent_id}` to Gemini Enterprise (GE)...")
        if headers:
            try:
                r_pub = requests.post(
                    f"{state.looker_instance_url}/api/4.0/internal/agents/{agent_id}/publish",
                    json={},
                    headers=headers,
                    timeout=15,
                )
                if r_pub.status_code in (200, 201):
                    state.published_to_ge = True
                    print_success("Agent successfully published to Gemini Enterprise (GE)!")
                else:
                    print_warning(f"GE publish endpoint returned status {r_pub.status_code} (Ensure GE publishing is enabled in Looker Admin).")
            except Exception as pub_err:
                print_warning(f"Notice while publishing to Gemini Enterprise: {pub_err}")

    return state
