"""Unit tests for Knowledge Catalog to LookML synchronization service."""

from __future__ import annotations

from pathlib import Path

import pytest

from looker_demo_cli.catalog.models import (
    CatalogSnapshot,
    ColumnMeta,
    CoverageReport,
    TableMeta,
)
from looker_demo_cli.generators.lookml_generator import LookMLGenerator, LookMLTableSpec
from looker_demo_cli.services.catalog_sync_service import (
    diff_catalog_against_lookml,
    parse_existing_lookml_project,
    sync_catalog_to_lookml,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def sample_snapshot() -> CatalogSnapshot:
    """Fixture providing a rich CatalogSnapshot for testing."""
    return CatalogSnapshot(
        dataset_id="analytics_dw",
        project_id="test-project",
        location="us",
        tables={
            "dim_users": TableMeta(
                name="dim_users",
                role="dimension",
                num_rows=5000,
                business_label="Platform Users",
                description="Core user entity profiles.",
                primary_key=["user_id"],
                columns={
                    "user_id": ColumnMeta(
                        name="user_id",
                        data_type="STRING",
                        business_label="User Identifier",
                        description="Unique customer ID",
                    ),
                    "email": ColumnMeta(
                        name="email",
                        data_type="STRING",
                        business_label="Email Address",
                        description="Customer email address",
                        synonyms=["electronic mail", "login"],
                    ),
                    "status": ColumnMeta(
                        name="status",
                        data_type="STRING",
                        business_label="Account Status",
                        description="User active status",
                        allowed_values=["active", "pending", "suspended"],
                    ),
                },
            ),
            "fct_transactions": TableMeta(
                name="fct_transactions",
                role="fact",
                num_rows=250000,
                business_label="Transaction Ledger",
                description="Financial transaction ledger.",
                primary_key=["transaction_id"],
                foreign_keys={"user_id": "dim_users.user_id"},
                columns={
                    "transaction_id": ColumnMeta(
                        name="transaction_id",
                        data_type="STRING",
                        business_label="Transaction ID",
                        description="Unique transaction record identifier",
                    ),
                    "user_id": ColumnMeta(
                        name="user_id",
                        data_type="STRING",
                        description="Foreign key to dim_users",
                    ),
                    "amount_usd": ColumnMeta(
                        name="amount_usd",
                        data_type="FLOAT64",
                        business_label="Transaction Amount",
                        description="Total charged in USD",
                        format_pattern="usd_0",
                    ),
                },
            ),
        },
        coverage=CoverageReport(
            total_columns=6,
            columns_with_descriptions=6,
            columns_with_labels=5,
            columns_with_glossary=0,
            columns_with_formats=1,
            coverage_percentage=85.0,
            recommended_profile="rich",
        ),
    )


def test_generator_layered_base_and_refinement(tmp_path: Path):
    """Test LookMLGenerator base and refinement view generation."""
    gen = LookMLGenerator(project_id="test-proj", dataset_id="test_ds", connection_name="test_conn")
    spec = LookMLTableSpec(
        table_name="dim_users",
        table_type="dimension",
        business_label="Registered Users",
        description="Users table",
        primary_key="user_id",
        schema_fields={
            "user_id": "STRING",
            "email": "STRING",
            "signup_date": "DATE",
            "balance": "FLOAT64",
        },
        column_descriptions={
            "user_id": "Unique primary key",
            "email": "User contact email",
            "signup_date": "Date account registered",
            "balance": "Current wallet balance",
        },
        column_labels={
            "user_id": "User ID",
            "email": "Email Address",
            "signup_date": "Signup Date",
            "balance": "Balance Amount",
        },
        column_synonyms={
            "email": ["email_address", "login_id"],
        },
        column_formats={
            "balance": "usd_0",
        },
    )

    # 1. Base View
    base_content = gen.generate_base_view_lkml(spec)
    assert "view: dim_users {" in base_content
    assert "sql_table_name: `test-proj.test_ds.dim_users` ;;" in base_content
    assert "dimension: user_id {" in base_content
    assert "primary_key: yes" in base_content
    assert "dimension_group: signup {" in base_content
    assert "measure: count {" in base_content
    # Base view should NOT have business labels or tags
    assert 'tags: ["email_address"' not in base_content
    assert 'label: "Email Address"' not in base_content

    # 2. Refinement View
    ref_content = gen.generate_refinement_view_lkml(spec)
    assert 'include: "/views/base/dim_users.view.lkml"' in ref_content
    assert "view: +dim_users {" in ref_content
    assert 'label: "Registered Users"' in ref_content
    assert 'label: "Email Address"' in ref_content
    assert 'description: "User contact email"' in ref_content
    assert 'tags: ["email_address", "login_id"]' in ref_content
    assert "value_format_name: usd_0" in ref_content
    assert 'label: "Total Users Count"' in ref_content
    assert "measure: total_balance {" in ref_content
    assert "measure: average_balance {" in ref_content

    # 3. Model File with Layered includes
    model_content = gen.generate_model_lkml("test_model", [spec], layered=True)
    assert 'include: "/views/base/**/*.view.lkml"' in model_content
    assert 'include: "/views/refinements/**/*.refinement.lkml"' in model_content

    # 4. Write Project Files Layered
    out_dir = tmp_path / "lookml_layered"
    written = gen.write_lookml_project_files(out_dir, "test_model", [spec], layered=True)
    assert (out_dir / "views" / "base" / "dim_users.view.lkml").exists()
    assert (out_dir / "views" / "refinements" / "dim_users.refinement.lkml").exists()
    assert (out_dir / "models" / "test_model.model.lkml").exists()
    assert len(written) == 4


def test_parse_existing_lookml_project(tmp_path: Path):
    """Test parsing of both layered and monolithic LookML directories."""
    lookml_dir = tmp_path / "lookml"
    base_dir = lookml_dir / "views" / "base"
    ref_dir = lookml_dir / "views" / "refinements"
    base_dir.mkdir(parents=True)
    ref_dir.mkdir(parents=True)

    base_file = base_dir / "customers.view.lkml"
    base_file.write_text(
        """
view: customers {
  sql_table_name: `p.d.customers` ;;
  dimension: id {
    primary_key: yes
    type: string
    sql: ${TABLE}.id ;;
  }
  dimension: name {
    type: string
    sql: ${TABLE}.name ;;
  }
  measure: count {
    type: count
  }
}
""",
        encoding="utf-8",
    )

    ref_file = ref_dir / "customers.refinement.lkml"
    ref_file.write_text(
        """
include: "/views/base/customers.view.lkml"

view: +customers {
  label: "Platform Customers"
  # description: "Customer accounts"

  dimension: name {
    label: "Customer Legal Name"
    description: "Full registered name"
    tags: ["full_name", "legal_name"]
  }

  dimension: custom_tier {
    type: string
    sql: 'TIER_1' ;;
  }

  measure: custom_metric {
    type: number
    sql: 100 ;;
  }
}
""",
        encoding="utf-8",
    )

    tables = parse_existing_lookml_project(lookml_dir)
    assert "customers" in tables
    cust = tables["customers"]
    assert cust.is_layered is True
    assert cust.business_label == "Platform Customers"
    assert cust.description == "Customer accounts"
    assert "id" in cust.columns
    assert "name" in cust.columns
    assert cust.columns["name"].label == "Customer Legal Name"
    assert cust.columns["name"].description == "Full registered name"
    assert cust.columns["name"].tags == ["full_name", "legal_name"]
    # Check custom blocks preserved
    assert any("custom_metric" in b for b in cust.custom_blocks)


def test_diff_catalog_against_lookml_detects_changes(tmp_path: Path, sample_snapshot: CatalogSnapshot):
    """Test diff detection between snapshot and existing LookML files."""
    lookml_dir = tmp_path / "lookml"
    views_dir = lookml_dir / "views"
    views_dir.mkdir(parents=True)

    # Initial view with old label and old description
    view_file = views_dir / "dim_users.view.lkml"
    view_file.write_text(
        """
view: dim_users {
  sql_table_name: `test-project.analytics_dw.dim_users` ;;
  label: "Old User Label"

  dimension: user_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.user_id ;;
  }
  dimension: email {
    label: "Old Email"
    description: "Old description"
    type: string
    sql: ${TABLE}.email ;;
  }
  measure: count {
    type: count
  }
}
""",
        encoding="utf-8",
    )

    # Spec from snapshot has dim_users (modified: new status col, updated email label/desc, updated table label)
    # and fct_transactions (added)
    report = diff_catalog_against_lookml(
        lookml_dir=lookml_dir,
        specs=[
            LookMLTableSpec(
                table_name="dim_users",
                table_type="dimension",
                business_label="Platform Users",
                description="Core user entity profiles.",
                primary_key="user_id",
                schema_fields={"user_id": "STRING", "email": "STRING", "status": "STRING"},
                column_labels={"user_id": "User Identifier", "email": "Email Address", "status": "Account Status"},
                column_descriptions={
                    "user_id": "Unique ID",
                    "email": "Customer email address",
                    "status": "Active status",
                },
                column_synonyms={"email": ["login"]},
                column_allowed_values={"status": ["active", "pending"]},
            ),
            LookMLTableSpec(
                table_name="fct_transactions",
                table_type="fact",
                business_label="Transaction Ledger",
                primary_key="transaction_id",
                schema_fields={"transaction_id": "STRING", "amount_usd": "FLOAT64"},
            ),
        ],
        profile="rich",
        dataset_id="analytics_dw",
        gcp_project="test-project",
    )

    assert "fct_transactions" in report.tables_added
    assert "dim_users" in report.tables_modified
    assert report.total_changes > 0

    user_diff = next(d for d in report.table_diffs if d.table_name == "dim_users")
    assert user_diff.business_label_changed is True
    assert user_diff.old_business_label == "Old User Label"
    assert user_diff.new_business_label == "Platform Users"

    diff_pairs = {(cd.column_name, cd.change_type) for cd in user_diff.column_diffs}
    assert ("status", "added") in diff_pairs
    assert (
        ("email", "label_updated") in diff_pairs
        or ("email", "description_updated") in diff_pairs
        or ("email", "tags_updated") in diff_pairs
    )


def test_sync_catalog_to_lookml_dry_run(tmp_path: Path, sample_snapshot: CatalogSnapshot):
    """Test sync in dry-run mode returns report without altering disk."""
    lookml_dir = tmp_path / "lookml"
    views_dir = lookml_dir / "views"
    views_dir.mkdir(parents=True)
    v_file = views_dir / "dim_users.view.lkml"
    original_text = "view: dim_users {\n  dimension: user_id {}\n}"
    v_file.write_text(original_text, encoding="utf-8")

    report = sync_catalog_to_lookml(
        lookml_dir=lookml_dir,
        snapshot=sample_snapshot,
        profile="rich",
        dry_run=True,
        layered=False,
    )

    assert report.dry_run is True
    assert len(report.files_created) == 0
    assert len(report.files_updated) == 0
    # Original file unmodified
    assert v_file.read_text(encoding="utf-8") == original_text


def test_sync_catalog_to_lookml_layered_preserves_custom(tmp_path: Path, sample_snapshot: CatalogSnapshot):
    """Test sync applying layered LookML updates while preserving custom fields."""
    lookml_dir = tmp_path / "lookml"
    base_dir = lookml_dir / "views" / "base"
    ref_dir = lookml_dir / "views" / "refinements"
    models_dir = lookml_dir / "models"
    base_dir.mkdir(parents=True)
    ref_dir.mkdir(parents=True)
    models_dir.mkdir(parents=True)

    m_file = models_dir / "analytics.model.lkml"
    m_file.write_text('connection: "bq_conn"\ninclude: "/views/**/*.view.lkml"\n', encoding="utf-8")

    b_file = base_dir / "dim_users.view.lkml"
    b_file.write_text(
        """
view: dim_users {
  sql_table_name: `test-project.analytics_dw.dim_users` ;;
  dimension: user_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.user_id ;;
  }
  dimension: email {
    type: string
    sql: ${TABLE}.email ;;
  }
  measure: count {
    type: count
  }
}
""",
        encoding="utf-8",
    )

    r_file = ref_dir / "dim_users.refinement.lkml"
    r_file.write_text(
        """
include: "/views/base/dim_users.view.lkml"

view: +dim_users {
  label: "Old Label"

  dimension: email {
    label: "Old Email"
  }

  dimension: custom_user_calc {
    type: string
    sql: UPPER(${email}) ;;
  }

  measure: custom_kpi {
    type: sum
    sql: 1 ;;
  }
}
""",
        encoding="utf-8",
    )

    report = sync_catalog_to_lookml(
        lookml_dir=lookml_dir,
        snapshot=sample_snapshot,
        profile="rich",
        dry_run=False,
        layered=True,
    )

    assert report.dry_run is False
    assert len(report.files_updated) > 0

    # 1. Refinement file updated with new Dataplex metadata
    ref_updated = r_file.read_text(encoding="utf-8")
    assert 'label: "Platform Users"' in ref_updated
    assert 'label: "Email Address"' in ref_updated
    assert 'tags: ["electronic mail", "login"]' in ref_updated
    # 2. Custom fields preserved
    assert "custom_user_calc" in ref_updated
    assert "custom_kpi" in ref_updated
    assert "Preserved by catalog sync" in ref_updated

    # 3. Newly added table fct_transactions created
    assert (base_dir / "fct_transactions.view.lkml").exists()
    assert (ref_dir / "fct_transactions.refinement.lkml").exists()

    # 4. Model file updated with refinement includes
    m_updated = m_file.read_text(encoding="utf-8")
    assert 'include: "/views/refinements/**/*.refinement.lkml"' in m_updated
