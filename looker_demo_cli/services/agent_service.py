from __future__ import annotations

from typing import Any

from looker_sdk import models40

from looker_demo_cli.sdk import get_looker_sdk
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
    """Extract query tiles from a deployed Looker dashboard via Looker SDK."""
    golden_queries: list[dict[str, Any]] = []

    try:
        sdk = get_looker_sdk(instance_url, headers=headers)
        dash = sdk.dashboard(dashboard_id)
        elements = dash.dashboard_elements or []
        for el in elements:
            title = el.title or "Key Metric"
            q = el.query
            rm = el.result_maker
            if not q and rm is not None:
                q = rm.query
                rm_qid = getattr(rm, "query_id", None)
                if not q and isinstance(rm_qid, str) and rm_qid:
                    try:
                        q = sdk.query(rm_qid)
                    except Exception:
                        pass
            if not q and el.query_id:
                try:
                    q = sdk.query(str(el.query_id))
                except Exception:
                    pass

            if q and q.model and q.view and q.fields:
                prompt = f"What is the {title.lower()}?"
                if "monthly" in title.lower() or "trend" in title.lower():
                    prompt = f"Show the monthly breakdown and trajectory for {title.lower()}."
                elif "breakdown" in title.lower() or "distribution" in title.lower():
                    prompt = f"Show the breakdown of {title.lower()}."

                golden_queries.append(
                    {
                        "prompt": prompt,
                        "query": {
                            "model": q.model,
                            "view": q.view,
                            "fields": list(q.fields),
                            "pivots": list(q.pivots or []),
                            "filters": dict(q.filters or {}),
                            "sorts": list(q.sorts or []),
                            "limit": str(q.limit or "500"),
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
    """Register golden queries and link them to the Looker CA agent via Looker SDK."""
    created_gq_ids: list[str] = []
    sdk = get_looker_sdk(instance_url, headers=headers)

    for idx, gq in enumerate(golden_queries, 1):
        try:
            # 1. Create base query to obtain expanded_share_url
            q_spec = gq["query"]
            q_data = sdk.create_query(
                body=models40.WriteQuery(
                    model=q_spec["model"],
                    view=q_spec["view"],
                    fields=q_spec.get("fields"),
                    pivots=q_spec.get("pivots"),
                    filters=q_spec.get("filters"),
                    sorts=q_spec.get("sorts"),
                    limit=str(q_spec.get("limit", "500")),
                )
            )
            answer_url = q_data.expanded_share_url or q_data.share_url
            if not answer_url:
                continue

            # 2. Create Golden Query
            gq_obj = sdk.create_golden_query(
                body=models40.WriteGoldenQuery(
                    questions=[gq["prompt"]],
                    answer=answer_url,
                    is_active=True,
                )
            )
            if gq_obj.id is not None:
                created_gq_ids.append(str(gq_obj.id))
        except Exception as e:
            print_warning(f"Notice while registering golden query {idx}: {e}")

    if created_gq_ids:
        try:
            existing_ids: list[int] = []
            try:
                existing_agent = sdk.get_agent(agent_id)
                if existing_agent and existing_agent.golden_query_ids:
                    existing_ids = [int(x) for x in existing_agent.golden_query_ids]
            except Exception:
                pass
            merged_ids = list(dict.fromkeys(existing_ids + [int(x) for x in created_gq_ids]))
            sdk.update_agent(
                agent_id=agent_id,
                body=models40.WriteAgent(golden_query_ids=merged_ids),
            )
            print_success(f"Linked {len(created_gq_ids)} Golden Queries to CA Agent `{agent_id}`.")
            return len(created_gq_ids)
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
    """Create a Conversational Analytics Agent via Looker SDK."""
    name = agent_name or f"{model_name.replace('_', ' ').title()} Assistant"
    instructions = custom_instructions or generate_default_ca_instructions(
        project_name=model_name,
        model_name=model_name,
        primary_explore=explore_name,
    )

    try:
        sdk = get_looker_sdk(instance_url, headers=headers)
        agent = sdk.create_agent(
            body=models40.WriteAgent(
                name=name,
                description=f"AI Conversational Analytics Assistant for {model_name}",
                sources=[models40.Source(model=model_name, explore=explore_name)],
                context=models40.Context(instructions=instructions),
                code_interpreter=True,
            )
        )
        if agent.id:
            print_success(f"Successfully created Conversational Analytics Agent `{name}` (ID: `{agent.id}`).")
            return str(agent.id)
    except Exception as e:
        print_error(f"Error creating CA Agent: {e}")
    return None
