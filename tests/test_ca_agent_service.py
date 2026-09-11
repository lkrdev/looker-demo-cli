"""Unit tests for the Conversational Analytics agent service.

Covers golden-query extraction from dashboard files and the Gemini Enterprise
publish retry loop. Converted from ``unittest.TestCase`` to pytest so the suite
has a single idiom and can use fixtures and parametrization.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from looker_demo_cli.services.ca_agent_service import (
    extract_golden_queries_from_dashboards,
    generate_default_ca_instructions,
    publish_agent_to_ge,
)

pytestmark = pytest.mark.unit


DASHBOARD = {
    "dashboard": "logistics_overview",
    "title": "Logistics Overview",
    "elements": [
        {
            "title": "Total Shipment Volume",
            "model": "logistics_analytics",
            "explore": "fct_shipments",
            "type": "single_value",
            "fields": ["fct_shipments.count"],
        },
        {
            "title": "Monthly Trajectory of Revenue",
            "model": "logistics_analytics",
            "explore": "fct_shipments",
            "type": "looker_area",
            "fields": ["fct_shipments.created_month", "fct_shipments.total_revenue"],
            "sorts": ["fct_shipments.created_month asc"],
        },
        {
            "title": "Distribution by Status",
            "model": "logistics_analytics",
            "explore": "fct_shipments",
            "type": "looker_donut_multiples",
            "fields": ["fct_shipments.status", "fct_shipments.count"],
        },
    ],
}

PUBLISH_KWARGS = {
    "instance_url": "https://demo.looker.com",
    "agent_id": "42",
    "headers": {"Authorization": "Bearer fake_token"},
}


@pytest.fixture
def dashboard_dir(tmp_path: Path) -> Path:
    """A LookML project root containing one dashboard with three query tiles."""
    dash_dir = tmp_path / "dashboards"
    dash_dir.mkdir(parents=True)
    (dash_dir / "logistics.dashboard.lookml").write_text(yaml.dump(DASHBOARD), encoding="utf-8")
    return tmp_path


def test_default_ca_instructions_include_domain_and_section_headers() -> None:
    """The generated persona names the domain and keeps its required sections."""
    instructions = generate_default_ca_instructions(
        project_name="logistics_analytics",
        model_name="logistics_analytics",
        primary_explore="fct_shipments",
    )

    assert "Logistics Analytics" in instructions
    assert "logistics_analytics" in instructions
    assert "fct_shipments" in instructions
    assert "Business Rules & Query Patterns:" in instructions
    assert "Styling & Response Guidelines:" in instructions


def test_extract_golden_queries_maps_every_tile(dashboard_dir: Path) -> None:
    """Each dashboard tile becomes exactly one golden query, 1:1."""
    queries = extract_golden_queries_from_dashboards(
        lookml_dir=dashboard_dir,
        default_model="logistics_analytics",
        default_explore="fct_shipments",
    )

    assert len(queries) == len(DASHBOARD["elements"])


def test_extract_golden_queries_preserves_tile_query_definition(dashboard_dir: Path) -> None:
    """A tile's model, explore, and field list survive into the golden query."""
    queries = extract_golden_queries_from_dashboards(
        lookml_dir=dashboard_dir,
        default_model="logistics_analytics",
        default_explore="fct_shipments",
    )

    first = queries[0]["query"]
    assert first["model"] == "logistics_analytics"
    assert first["view"] == "fct_shipments"
    assert first["fields"] == ["fct_shipments.count"]


@pytest.mark.parametrize(
    ("index", "expected_fragment"),
    [
        (0, "total shipment volume"),
        (1, "monthly breakdown"),
        (2, "breakdown of distribution by status"),
    ],
)
def test_golden_query_prompt_is_derived_from_tile(dashboard_dir: Path, index: int, expected_fragment: str) -> None:
    """Tile titles and visualization types drive the natural-language prompt.

    The prompt is what grounds the CA agent, so a regression here degrades
    answer quality without failing any deployment check.
    """
    queries = extract_golden_queries_from_dashboards(
        lookml_dir=dashboard_dir,
        default_model="logistics_analytics",
        default_explore="fct_shipments",
    )

    assert expected_fragment in queries[index]["prompt"]


def test_publish_to_ge_succeeds_on_first_attempt() -> None:
    """A 200 from the publish endpoint plus a verified status returns True."""
    ok = MagicMock(status_code=200)
    verified = MagicMock(status_code=200, json=lambda: {"publish_status": "published"})

    with patch("requests.post", return_value=ok) as mock_post, patch("requests.get", return_value=verified):
        result = publish_agent_to_ge(**PUBLISH_KWARGS, max_attempts=1)

    assert result is True
    mock_post.assert_called_once()


def test_publish_to_ge_retries_after_server_error() -> None:
    """A 500 is retried, and a subsequent success still reports True.

    ``time.sleep`` is patched out so the backoff does not slow the suite.
    """
    failure = MagicMock(status_code=500, text="Internal Error")
    success = MagicMock(status_code=200)
    verified = MagicMock(status_code=200, json=lambda: {"publish_status": "published"})

    with (
        patch("requests.post", side_effect=[failure, success]) as mock_post,
        patch("requests.get", return_value=verified),
        patch("time.sleep"),
    ):
        result = publish_agent_to_ge(**PUBLISH_KWARGS, max_attempts=2)

    assert result is True
    assert mock_post.call_count == 2
