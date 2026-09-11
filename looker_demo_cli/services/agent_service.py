from __future__ import annotations

from typing import Any

import requests

from looker_demo_cli.services.ca_agent_service import (
    generate_default_ca_instructions,
)
from looker_demo_cli.utils.console import (
    print_error,
    print_success,
    print_warning,
)


def extract_golden_queries_from_dashboard_id(
    instance_url: str,
    headers: dict[str, str],
    dashboard_id: str,
) -> list[dict[str, Any]]:
    """Extract query tiles from a deployed Looker dashboard via REST API."""
    clean_url = instance_url.rstrip("/")
    endpoint = f"{clean_url}/api/4.0/dashboards/{dashboard_id}"
    golden_queries: list[dict[str, Any]] = []

    try:
        resp = requests.get(endpoint, headers=headers, timeout=12)
        if resp.status_code != 200:
            print_warning(f"Could not fetch dashboard `{dashboard_id}` ({resp.status_code}): {resp.text[:200]}")
            return []

        dash = resp.json()
        elements = dash.get("dashboard_elements") or dash.get("elements") or []
        for el in elements:
            title = el.get("title") or "Key Metric"
            q = el.get("query")
            if not q and el.get("query_id"):
                r_q = requests.get(f"{clean_url}/api/4.0/queries/{el['query_id']}", headers=headers, timeout=8)
                if r_q.status_code == 200:
                    q = r_q.json()

            if q and q.get("model") and q.get("view") and q.get("fields"):
                prompt = f"What is the {title.lower()}?"
                if "monthly" in title.lower() or "trend" in title.lower():
                    prompt = f"Show the monthly breakdown and trajectory for {title.lower()}."
                elif "breakdown" in title.lower() or "distribution" in title.lower():
                    prompt = f"Show the breakdown of {title.lower()}."

                golden_queries.append(
                    {
                        "prompt": prompt,
                        "query": {
                            "model": q.get("model"),
                            "view": q.get("view"),
                            "fields": q.get("fields", []),
                            "pivots": q.get("pivots", []),
                            "filters": dict(q.get("filters", {})),
                            "sorts": q.get("sorts", []),
                            "limit": str(q.get("limit", "500")),
                        },
                    }
                )
    except Exception as e:
        print_warning(f"Error extracting queries from dashboard `{dashboard_id}`: {e}")

    return golden_queries


def register_and_link_golden_queries(
    instance_url: str,
    headers: dict[str, str],
    agent_id: str,
    golden_queries: list[dict[str, Any]],
) -> int:
    """Register golden queries and link them to the Looker CA agent."""
    clean_url = instance_url.rstrip("/")
    created_gq_ids: list[str] = []

    for idx, gq in enumerate(golden_queries, 1):
        try:
            # 1. Create base query to obtain expanded_share_url
            r_q = requests.post(
                f"{clean_url}/api/4.0/queries",
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

            # 2. Create Golden Query
            r_gq = requests.post(
                f"{clean_url}/api/4.0/golden_queries",
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
        except Exception as e:
            print_warning(f"Notice while registering golden query {idx}: {e}")

    if created_gq_ids:
        try:
            r_patch = requests.patch(
                f"{clean_url}/api/4.0/agents/{agent_id}",
                json={"golden_query_ids": [int(x) for x in created_gq_ids]},
                headers=headers,
                timeout=15,
            )
            if r_patch.status_code in (200, 201):
                print_success(f"Linked {len(created_gq_ids)} Golden Queries to CA Agent `{agent_id}`.")
                return len(created_gq_ids)
            else:
                print_error(f"Failed to link golden queries to agent ({r_patch.status_code}): {r_patch.text[:300]}")
        except Exception as e:
            print_warning(f"Error linking golden queries to agent: {e}")

    return len(created_gq_ids)


def provision_ca_agent(
    instance_url: str,
    headers: dict[str, str],
    model_name: str,
    explore_name: str,
    agent_name: str | None = None,
    custom_instructions: str | None = None,
) -> str | None:
    """Create a Conversational Analytics Agent via Looker REST API."""
    clean_url = instance_url.rstrip("/")
    name = agent_name or f"{model_name.replace('_', ' ').title()} Assistant"
    instructions = custom_instructions or generate_default_ca_instructions(
        project_name=model_name,
        model_name=model_name,
        primary_explore=explore_name,
    )

    payload = {
        "name": name,
        "description": f"AI Conversational Analytics Assistant for {model_name}",
        "sources": [{"model": model_name, "explore": explore_name}],
        "context": {"instructions": instructions},
        "code_interpreter": True,
    }

    try:
        r = requests.post(f"{clean_url}/api/4.0/agents", json=payload, headers=headers, timeout=15)
        if r.status_code in (200, 201):
            agent_id = r.json().get("id")
            print_success(f"Successfully created Conversational Analytics Agent `{name}` (ID: `{agent_id}`).")
            return str(agent_id)
        print_error(f"Failed to create CA Agent ({r.status_code}): {r.text[:300]}")
    except Exception as e:
        print_error(f"Error creating CA Agent: {e}")
    return None
