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


def test_optimize_layered_refinement_files(tmp_path: Path) -> None:
    """Optimizer patches suggestable flags in layered refinement files without hiding primary keys or duplicating onto base views."""
    base_dir = tmp_path / "views" / "base"
    ref_dir = tmp_path / "views" / "refinements"
    dash_dir = tmp_path / "dashboards"
    base_dir.mkdir(parents=True)
    ref_dir.mkdir(parents=True)
    dash_dir.mkdir(parents=True)

    base_file = base_dir / "users.view.lkml"
    base_content = """view: users {
  sql_table_name: `p.d.users` ;;
  dimension: user_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.user_id ;;
  }
  dimension: external_ref_id {
    type: string
    sql: ${TABLE}.external_ref_id ;;
  }
}
"""
    base_file.write_text(base_content, encoding="utf-8")

    ref_file = ref_dir / "users.view.lkml"
    ref_file.write_text(
        """include: "/views/base/users.view.lkml"

view: +users {
  dimension: user_id {
    label: "User ID"
  }
  dimension: external_ref_id {
    label: "External Reference ID"
  }
}
""",
        encoding="utf-8",
    )

    dash_file = dash_dir / "overview.dashboard.lookml"
    dash_file.write_text("- dashboard: overview\n  title: Executive Overview\n", encoding="utf-8")

    result = optimize_lookml_project(tmp_path, backup=True)
    assert result["status"] == "SUCCESS"
    updated_ref = ref_file.read_text(encoding="utf-8")
    assert "suggestable: no" in updated_ref
    # Primary key user_id must NOT be marked hidden: yes in refinement view
    user_id_block = updated_ref.split("dimension: external_ref_id")[0]
    assert "hidden: yes" not in user_id_block
    # Foreign/reference ID external_ref_id IS marked hidden: yes in refinement view
    ext_id_block = updated_ref.split("dimension: external_ref_id")[1]
    assert "hidden: yes" in ext_id_block
    # Base view remains untouched because refinement view owns those dimensions
    assert base_file.read_text(encoding="utf-8") == base_content

    # Dashboard (.lookml) is also snapshotted in .backup_pre_opt and restored by restore_lookml_backup
    assert (tmp_path / ".backup_pre_opt" / "dashboards" / "overview.dashboard.lookml").exists()
    restore_res = restore_lookml_backup(tmp_path)
    assert restore_res["status"] == "SUCCESS"
    assert not (tmp_path / ".backup_pre_opt").exists()


def test_find_root_duplicates_detects_layered_base_and_refinement_views() -> None:
    """Root view files shadowing views/base/ or views/refinements/ are flagged as duplicates."""
    layered_files = [
        {"path": "views/base/orders.view.lkml"},
        {"path": "views/refinements/orders.view.lkml"},
        {"path": "orders.view.lkml"},
        {"path": "manifest.lkml"},
    ]
    with patch("requests.get", return_value=MagicMock(status_code=200, json=lambda: layered_files)):
        duplicates = find_root_duplicate_files(**CLEANER_KWARGS)
    assert duplicates == ["orders.view.lkml"]


def test_validate_filtered_measures_across_layered_base_and_refinement(tmp_path: Path) -> None:
    """Filtered measures in views/refinements/*.view.lkml resolve sql_table_name and dimensions from views/base/*.view.lkml."""
    import pandas as pd

    from looker_demo_cli.services.validator_service import validate_filtered_measures_in_lookml

    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    df = pd.DataFrame({"order_id": ["o1", "o2"], "status": ["COMPLETED", "CANCELLED"], "amount": [10.0, 20.0]})
    df.to_parquet(parquet_dir / "fct_orders.parquet")

    lookml_dir = tmp_path / "lookml"
    base_dir = lookml_dir / "views" / "base"
    ref_dir = lookml_dir / "views" / "refinements"
    base_dir.mkdir(parents=True)
    ref_dir.mkdir(parents=True)

    (base_dir / "fct_orders.view.lkml").write_text(
        """view: fct_orders {
  sql_table_name: `proj.ds.fct_orders` ;;
  dimension: order_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.order_id ;;
  }
  dimension: status {
    type: string
    sql: ${TABLE}.status ;;
  }
  dimension: amount {
    type: number
    sql: ${TABLE}.amount ;;
  }
}
""",
        encoding="utf-8",
    )

    (ref_dir / "fct_orders.view.lkml").write_text(
        """include: "/views/base/fct_orders.view.lkml"
view: +fct_orders {
  measure: completed_revenue {
    type: sum
    sql: ${amount} ;;
    filters: [status: "COMPLETED"]
  }
  measure: bogus_revenue {
    type: sum
    sql: ${amount} ;;
    filters: [status: "NON_EXISTENT_STATUS"]
  }
}
""",
        encoding="utf-8",
    )

    diagnostics = validate_filtered_measures_in_lookml(lookml_dir=lookml_dir, parquet_dirs=[parquet_dir])
    assert len(diagnostics) == 1
    assert "bogus_revenue" in diagnostics[0]
    assert "NON_EXISTENT_STATUS" in diagnostics[0]


def test_subagent_definitions_are_client_agnostic() -> None:
    """All subagent markdown definitions must include send_message in tools, omit `model: sonnet`, and omit editor-specific keys."""
    repo_root = Path(__file__).resolve().parent.parent
    subagents_dir = repo_root / "skills" / "looker-demo-orchestrator" / "subagents"
    md_files = list(subagents_dir.glob("*.md"))
    assert len(md_files) >= 9
    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8")
        assert "model: sonnet" not in text, f"{md_file.name} should not hardcode model: sonnet"
        assert "mainAgent:" not in text, f"{md_file.name} should not contain Jetski-specific mainAgent key"
        assert "commandExecutionPolicy:" not in text, f"{md_file.name} should not contain commandExecutionPolicy"
        assert "- send_message" in text, f"{md_file.name} must include send_message in tools list"


def test_install_subagent_definitions(tmp_path: Path) -> None:
    """`install_subagent_definitions` copies subagent markdown definitions into target directory."""
    from looker_demo_cli.precheck.skills_organizer import install_subagent_definitions

    agents_dir = tmp_path / "agents"
    installed = install_subagent_definitions(agents_dir=agents_dir)
    assert len(installed) >= 9
    assert any("lookml-dashboard-designer.md" in p for p in installed)
    dest_file = agents_dir / "lookml-dashboard-designer.md"
    assert dest_file.exists()
    assert "- send_message" in dest_file.read_text(encoding="utf-8")
