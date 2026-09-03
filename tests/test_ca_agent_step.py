from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
import yaml

from looker_demo_cli.workflow.state import FlowState
from looker_demo_cli.workflow.steps.step_ca_agent import (
    extract_golden_queries_from_dashboards,
    generate_default_ca_instructions,
)


class TestCAAgentStep(unittest.TestCase):
    def test_generate_default_ca_instructions(self):
        instructions = generate_default_ca_instructions(
            project_name="logistics_analytics",
            model_name="logistics_analytics",
            primary_explore="fct_shipments",
        )
        self.assertIn("Logistics Analytics", instructions)
        self.assertIn("logistics_analytics", instructions)
        self.assertIn("fct_shipments", instructions)
        self.assertIn("Business Rules & Query Patterns:", instructions)
        self.assertIn("Styling & Response Guidelines:", instructions)

    def test_extract_golden_queries_from_dashboards(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            dash_dir = tmp_path / "dashboards"
            dash_dir.mkdir(parents=True, exist_ok=True)

            dash_content = {
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

            dash_file = dash_dir / "logistics.dashboard.lookml"
            dash_file.write_text(yaml.dump(dash_content), encoding="utf-8")

            gqs = extract_golden_queries_from_dashboards(
                lookml_dir=tmp_path,
                default_model="logistics_analytics",
                default_explore="fct_shipments",
            )

            self.assertEqual(len(gqs), 3)
            self.assertEqual(gqs[0]["query"]["model"], "logistics_analytics")
            self.assertEqual(gqs[0]["query"]["view"], "fct_shipments")
            self.assertEqual(gqs[0]["query"]["fields"], ["fct_shipments.count"])
            self.assertIn("total shipment volume", gqs[0]["prompt"])
            self.assertIn("monthly breakdown", gqs[1]["prompt"])
            self.assertIn("breakdown of distribution by status", gqs[2]["prompt"])

    def test_publish_agent_to_ge(self):
        from unittest.mock import MagicMock, patch
        from looker_demo_cli.workflow.steps.step_ca_agent import publish_agent_to_ge

        # 1. Test success on first attempt
        mock_resp_success = MagicMock(status_code=200)
        mock_resp_verify = MagicMock(status_code=200, json=lambda: {"publish_status": "published"})

        with patch("requests.post", return_value=mock_resp_success) as mock_post, \
             patch("requests.get", return_value=mock_resp_verify) as mock_get:
            res = publish_agent_to_ge(
                instance_url="https://demo.looker.com",
                agent_id="42",
                headers={"Authorization": "Bearer fake_token"},
                max_attempts=1,
            )
            self.assertTrue(res)
            mock_post.assert_called_once()

        # 2. Test retry on failure
        mock_resp_fail = MagicMock(status_code=500, text="Internal Error")
        with patch("requests.post", side_effect=[mock_resp_fail, mock_resp_success]) as mock_post_retry, \
             patch("requests.get", return_value=mock_resp_verify), \
             patch("time.sleep"):
            res = publish_agent_to_ge(
                instance_url="https://demo.looker.com",
                agent_id="42",
                headers={"Authorization": "Bearer fake_token"},
                max_attempts=2,
            )
            self.assertTrue(res)
            self.assertEqual(mock_post_retry.call_count, 2)


if __name__ == "__main__":
    unittest.main()
