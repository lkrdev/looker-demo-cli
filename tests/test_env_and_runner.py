from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from looker_demo_cli.precheck.env_checker import (
    check_runtime_environment,
    RuntimeEnvironmentStatus,
)


class TestEnvAndRunner(unittest.TestCase):
    def test_check_runtime_environment(self):
        status = check_runtime_environment()
        self.assertIsInstance(status, RuntimeEnvironmentStatus)
        self.assertTrue(status.python_executable)
        self.assertTrue(status.uv_installed)
        
        # Verify dependency checks
        dep_map = {d.package_name: d for d in status.dependency_checks}
        self.assertIn("mcp", dep_map)
        self.assertIn("pydantic-monty", dep_map)
        self.assertIn("requests", dep_map)
        self.assertIn("pyyaml", dep_map)
        self.assertIn("lkr-dev-cli", dep_map)
        
        # In this synced environment, all pins should be valid
        self.assertTrue(dep_map["mcp"].is_satisfied)
        self.assertTrue(dep_map["pydantic-monty"].is_satisfied)
        self.assertTrue(dep_map["requests"].is_satisfied)
        self.assertTrue(dep_map["pyyaml"].is_satisfied)

    def test_cli_run_script(self):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("""
import sys
import pandas as pd
import pyarrow as pa
from google.cloud import bigquery
import looker_demo_cli

print("RUNNER_TEST_SUCCESS")
sys.exit(0)
""")
            script_path = Path(f.name)

        try:
            res = subprocess.run(
                [sys.executable, "-m", "looker_demo_cli.cli", "run-script", str(script_path)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("RUNNER_TEST_SUCCESS", res.stdout)
        finally:
            script_path.unlink(missing_ok=True)

    def test_cli_python_command(self):
        res = subprocess.run(
            [sys.executable, "-m", "looker_demo_cli.cli", "python", "-c", "import looker_demo_cli; print('PYTHON_CMD_SUCCESS')"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("PYTHON_CMD_SUCCESS", res.stdout)

    def test_cli_env_info(self):
        res = subprocess.run(
            [sys.executable, "-m", "looker_demo_cli.cli", "env", "info"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Python Runtime & Dependency Health", res.stdout)
        self.assertIn("mcp", res.stdout)
        self.assertIn("pydantic-monty", res.stdout)

    def test_lookml_optimize_and_restore(self):
        from looker_demo_cli.services.optimizer_service import (
            optimize_lookml_project,
            restore_lookml_backup,
        )

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            views_dir = tmp_dir / "views"
            views_dir.mkdir()
            view_file = views_dir / "users.view.lkml"
            original_content = """
view: users {
  sql_table_name: `demo.users` ;;
  dimension: id {
    primary_key: yes
    type: string
    sql: ${TABLE}.id ;;
  }
  dimension: status {
    type: string
    sql: ${TABLE}.status ;;
  }
}
"""
            view_file.write_text(original_content, encoding="utf-8")

            # 1. Optimize with backup
            opt_res = optimize_lookml_project(tmp_dir, backup=True)
            self.assertEqual(opt_res["status"], "SUCCESS")
            self.assertTrue(opt_res["backup_created"])
            backup_dir = tmp_dir / ".backup_pre_opt"
            self.assertTrue(backup_dir.exists())

            # File should be modified (suggestable: no added, etc.)
            modified_content = view_file.read_text(encoding="utf-8")
            self.assertNotEqual(original_content, modified_content)
            self.assertIn("suggestable: no", modified_content)

            # 2. Restore backup
            restore_res = restore_lookml_backup(tmp_dir)
            self.assertEqual(restore_res["status"], "SUCCESS")
            self.assertFalse(backup_dir.exists())

            # File should be completely reverted to original
            reverted_content = view_file.read_text(encoding="utf-8")
            self.assertEqual(original_content, reverted_content)

    def test_find_and_clean_root_duplicates(self):
        from unittest.mock import MagicMock, patch
        from looker_demo_cli.services.lookml_cleaner import (
            clean_root_duplicate_files,
            find_root_duplicate_files,
        )

        mock_files = [
            {"path": "views/users.view.lkml"},
            {"path": "models/ecommerce.model.lkml"},
            {"path": "dashboards/overview.dashboard.lookml"},
            {"path": "users.view.lkml"},  # Duplicate root orphan!
            {"path": "ecommerce.model.lkml"},  # Duplicate root orphan!
            {"path": "manifest.lkml"},  # Allowed root file
        ]

        # 1. Test detection
        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: mock_files)
            duplicates = find_root_duplicate_files(
                project_id="ecommerce",
                headers={"Authorization": "Bearer fake"},
                base_url="https://demo.looker.com",
            )
            self.assertEqual(sorted(duplicates), ["ecommerce.model.lkml", "users.view.lkml"])

        # 2. Test dry-run
        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: mock_files)
            res_dry = clean_root_duplicate_files(
                project_id="ecommerce",
                headers={"Authorization": "Bearer fake"},
                base_url="https://demo.looker.com",
                dry_run=True,
            )
            self.assertEqual(res_dry["status"], "DRY_RUN")
            self.assertEqual(len(res_dry["cleaned_files"]), 2)

        # 3. Test deletion
        with patch("requests.get") as mock_get, patch("requests.delete") as mock_del:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: mock_files)
            mock_del.return_value = MagicMock(status_code=204)
            res_clean = clean_root_duplicate_files(
                project_id="ecommerce",
                headers={"Authorization": "Bearer fake"},
                base_url="https://demo.looker.com",
                dry_run=False,
            )
            self.assertEqual(res_clean["status"], "SUCCESS")
            self.assertEqual(len(res_clean["cleaned_files"]), 2)
            self.assertEqual(mock_del.call_count, 2)


if __name__ == "__main__":
    unittest.main()

