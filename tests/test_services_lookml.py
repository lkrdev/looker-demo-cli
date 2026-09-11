"""Service-level unit tests for LookML optimization, restore, and root cleanup.

These exercise the service layer directly, below the CLI. The corresponding
command-level behavior (argument parsing, exit codes, ``--json`` envelopes,
``.demo-state.json`` effects) is covered separately in
``tests/test_cli_lookml.py``.

Extracted from the original ``test_env_and_runner.py``, which mixed these
hermetic service tests together with host-dependent environment probes. The
host-dependent half now lives in ``tests/test_host_integration.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from looker_demo_cli.services.lookml_cleaner import (
    clean_root_duplicate_files,
    find_root_duplicate_files,
)
from looker_demo_cli.services.optimizer_service import (
    optimize_lookml_project,
    restore_lookml_backup,
)

pytestmark = pytest.mark.unit


ORIGINAL_VIEW = """
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


@pytest.fixture
def lookml_project(tmp_path: Path) -> tuple[Path, Path]:
    """A minimal LookML project on disk.

    Returns:
        A ``(project_root, view_file)`` tuple.
    """
    views_dir = tmp_path / "views"
    views_dir.mkdir()
    view_file = views_dir / "users.view.lkml"
    view_file.write_text(ORIGINAL_VIEW, encoding="utf-8")
    return tmp_path, view_file


def test_optimize_creates_backup_and_patches_view(lookml_project: tuple[Path, Path]) -> None:
    """Optimizing snapshots the originals and rewrites the view in place."""
    project_root, view_file = lookml_project

    result = optimize_lookml_project(project_root, backup=True)

    assert result["status"] == "SUCCESS"
    assert result["backup_created"] is True
    assert (project_root / ".backup_pre_opt").exists()

    modified = view_file.read_text(encoding="utf-8")
    assert modified != ORIGINAL_VIEW
    # Unique identifiers are marked non-suggestable so Looker does not issue a
    # `SELECT DISTINCT` against a high-cardinality column to populate filters.
    assert "suggestable: no" in modified


def test_restore_reverts_view_byte_for_byte(lookml_project: tuple[Path, Path]) -> None:
    """Restore is a true rollback: content is identical to pre-optimization."""
    project_root, view_file = lookml_project

    optimize_lookml_project(project_root, backup=True)
    assert view_file.read_text(encoding="utf-8") != ORIGINAL_VIEW

    result = restore_lookml_backup(project_root)

    assert result["status"] == "SUCCESS"
    assert not (project_root / ".backup_pre_opt").exists()
    assert view_file.read_text(encoding="utf-8") == ORIGINAL_VIEW


MOCK_PROJECT_FILES = [
    {"path": "views/users.view.lkml"},
    {"path": "models/ecommerce.model.lkml"},
    {"path": "dashboards/overview.dashboard.lookml"},
    {"path": "users.view.lkml"},  # Duplicate root orphan
    {"path": "ecommerce.model.lkml"},  # Duplicate root orphan
    {"path": "manifest.lkml"},  # Legitimately lives at the root
]

CLEANER_KWARGS = {
    "project_id": "ecommerce",
    "headers": {"Authorization": "Bearer fake"},
    "base_url": "https://demo.looker.com",
}


def _mock_list_files() -> MagicMock:
    return MagicMock(status_code=200, json=lambda: MOCK_PROJECT_FILES)


def test_find_root_duplicates_ignores_legitimate_root_files() -> None:
    """Only root files shadowing a subdirectory copy are flagged.

    ``manifest.lkml`` legitimately lives at the project root and must never be
    reported, even though it has no subdirectory counterpart.
    """
    with patch("requests.get", return_value=_mock_list_files()):
        duplicates = find_root_duplicate_files(**CLEANER_KWARGS)

    assert sorted(duplicates) == ["ecommerce.model.lkml", "users.view.lkml"]


def test_clean_root_dry_run_reports_without_deleting() -> None:
    """A dry run reports the same set it would delete, but issues no DELETEs."""
    with patch("requests.get", return_value=_mock_list_files()), patch("requests.delete") as mock_delete:
        result = clean_root_duplicate_files(**CLEANER_KWARGS, dry_run=True)

    assert result["status"] == "DRY_RUN"
    assert len(result["cleaned_files"]) == 2
    mock_delete.assert_not_called()


def test_clean_root_deletes_each_duplicate() -> None:
    """Each detected duplicate results in exactly one DELETE call."""
    with (
        patch("requests.get", return_value=_mock_list_files()),
        patch("requests.delete", return_value=MagicMock(status_code=204)) as mock_delete,
    ):
        result = clean_root_duplicate_files(**CLEANER_KWARGS, dry_run=False)

    assert result["status"] == "SUCCESS"
    assert len(result["cleaned_files"]) == 2
    assert mock_delete.call_count == 2
