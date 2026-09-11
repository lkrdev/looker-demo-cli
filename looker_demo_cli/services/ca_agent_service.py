"""Conversational Analytics agent grounding: golden queries and GE publishing.

Promoted out of the deleted ``workflow/steps`` package. Only the three helpers
below survived; the ``run_ca_agent_step`` orchestrator went with the monolithic
``demo-create run`` it existed to serve.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests
import yaml

from looker_demo_cli.utils.console import print_error, print_info, print_success, print_warning


def extract_golden_queries_from_dashboards(
    lookml_dir: Path,
    default_model: str,
    default_explore: str,
    dashboard_file: Path | None = None,
) -> list[dict[str, Any]]:
    """Extract query specifications from LookML dashboard files and convert to golden queries."""
    golden_queries: list[dict[str, Any]] = []

    if dashboard_file and Path(dashboard_file).is_file():
        dash_files = [Path(dashboard_file)]
    else:
        dash_dir = lookml_dir / "dashboards" if lookml_dir.name != "dashboards" else lookml_dir
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
                    f.get("name"): f.get("default_value") for f in dash_obj.get("filters", []) if f.get("default_value")
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

                        golden_queries.append(
                            {
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
                            }
                        )
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


def publish_agent_to_ge(instance_url: str, agent_id: str, headers: dict[str, str], max_attempts: int = 3) -> bool:
    """Publish CA Agent to Gemini Enterprise with verification and automatic retry up to max_attempts."""
    clean_url = instance_url.rstrip("/")
    print_info("Gemini Enterprise (GE) Prerequisites Checklist:")
    print_info("  1. Active Gemini Enterprise instance/app in Google Cloud Console")
    print_info("  2. Looker Admin -> Gemini Settings configured (Instance ID, Region, Project Number)")
    print_info("  3. Looker Service Account granted 'roles/discoveryengine.admin' role")
    print_info("  4. Looker Service Account explicitly assigned a Gemini Enterprise license")

    if not headers:
        print_warning("No Looker authentication headers available to publish to Gemini Enterprise.")
        return False

    for attempt in range(1, max_attempts + 1):
        print_info(f"Publishing CA Agent `{agent_id}` to Gemini Enterprise (GE) (Attempt {attempt}/{max_attempts})...")
        try:
            r_pub = requests.post(
                f"{clean_url}/api/4.0/internal/agents/{agent_id}/publish",
                json={},
                headers=headers,
                timeout=15,
            )
            if r_pub.status_code in (200, 201):
                # Verify agent publication state
                try:
                    r_verify = requests.get(
                        f"{clean_url}/api/4.0/agents/{agent_id}",
                        headers=headers,
                        timeout=10,
                    )
                    if r_verify.status_code == 200:
                        agent_data = r_verify.json()
                        pub_status = agent_data.get("publish_status") or agent_data.get("status") or "published"
                        print_info(f"Verified agent publication state: `{pub_status}`.")
                except Exception:
                    pass

                print_success(f"Agent `{agent_id}` successfully published to Gemini Enterprise (GE)!")
                return True
            else:
                print_warning(
                    f"GE publish attempt {attempt} returned HTTP {r_pub.status_code}: {r_pub.text[:300]}\n"
                    "Please verify: 1) Admin > Gemini is configured, 2) Looker SA has Discovery Engine Admin, and 3) Looker SA has a GE license."
                )
        except Exception as pub_err:
            print_warning(f"GE publish attempt {attempt} error: {pub_err}")

        if attempt < max_attempts:
            import time

            time.sleep(2)

    print_error(f"Failed to publish CA Agent `{agent_id}` to Gemini Enterprise after {max_attempts} attempts.")
    return False
