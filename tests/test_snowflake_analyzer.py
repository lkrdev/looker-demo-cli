import sys
from pathlib import Path

# Add project and scripts directory to sys.path
project_root = Path(__file__).resolve().parent.parent
scripts_dir = project_root / "skills" / "lookml-snowflake-modeler" / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import schema_graph_analyzer  # type: ignore[import-not-found]

SchemaGraphAnalyzer = schema_graph_analyzer.SchemaGraphAnalyzer
TableSchemaSpec = schema_graph_analyzer.TableSchemaSpec


def test_linear_snowflake_schema_analysis():
    # Construct a realistic 3NF Linear-style schema
    linear_tables = [
        TableSchemaSpec(
            table_name="dim_workspaces",
            table_type="dimension",
            primary_key="workspace_id",
            schema_fields={"workspace_id": "STRING", "workspace_name": "STRING", "tier": "STRING"},
        ),
        TableSchemaSpec(
            table_name="dim_teams",
            table_type="dimension",
            primary_key="team_id",
            foreign_keys={"workspace_id": "dim_workspaces.workspace_id"},
            schema_fields={"team_id": "STRING", "workspace_id": "STRING", "team_name": "STRING", "key": "STRING"},
        ),
        TableSchemaSpec(
            table_name="dim_users",
            table_type="dimension",
            primary_key="user_id",
            foreign_keys={"workspace_id": "dim_workspaces.workspace_id"},
            schema_fields={"user_id": "STRING", "workspace_id": "STRING", "email": "STRING", "name": "STRING", "role": "STRING"},
        ),
        TableSchemaSpec(
            table_name="dim_projects",
            table_type="dimension",
            primary_key="project_id",
            foreign_keys={"team_id": "dim_teams.team_id", "lead_user_id": "dim_users.user_id"},
            schema_fields={"project_id": "STRING", "team_id": "STRING", "lead_user_id": "STRING", "project_name": "STRING", "state": "STRING"},
        ),
        TableSchemaSpec(
            table_name="dim_cycles",
            table_type="dimension",
            primary_key="cycle_id",
            foreign_keys={"team_id": "dim_teams.team_id"},
            schema_fields={"cycle_id": "STRING", "team_id": "STRING", "number": "INT64", "start_date": "DATE", "end_date": "DATE"},
        ),
        TableSchemaSpec(
            table_name="dim_workflow_states",
            table_type="dimension",
            primary_key="state_id",
            foreign_keys={"team_id": "dim_teams.team_id"},
            schema_fields={"state_id": "STRING", "team_id": "STRING", "name": "STRING", "type": "STRING"},
        ),
        TableSchemaSpec(
            table_name="fct_issues",
            table_type="fact",
            primary_key="issue_id",
            foreign_keys={
                "project_id": "dim_projects.project_id",
                "cycle_id": "dim_cycles.cycle_id",
                "state_id": "dim_workflow_states.state_id",
                "assignee_id": "dim_users.user_id",
                "creator_id": "dim_users.user_id",
                "parent_issue_id": "fct_issues.issue_id",
            },
            schema_fields={
                "issue_id": "STRING",
                "project_id": "STRING",
                "cycle_id": "STRING",
                "state_id": "STRING",
                "assignee_id": "STRING",
                "creator_id": "STRING",
                "parent_issue_id": "STRING",
                "title": "STRING",
                "priority": "INT64",
                "estimate_points": "INT64",
                "created_at": "TIMESTAMP",
                "completed_at": "TIMESTAMP",
            },
        ),
        TableSchemaSpec(
            table_name="fct_issue_activities",
            table_type="event",
            primary_key="activity_id",
            foreign_keys={
                "issue_id": "fct_issues.issue_id",
                "actor_id": "dim_users.user_id",
            },
            schema_fields={
                "activity_id": "STRING",
                "issue_id": "STRING",
                "actor_id": "STRING",
                "action_type": "STRING",
                "created_at": "TIMESTAMP",
            },
        ),
        TableSchemaSpec(
            table_name="fct_issue_comments",
            table_type="event",
            primary_key="comment_id",
            foreign_keys={
                "issue_id": "fct_issues.issue_id",
                "author_id": "dim_users.user_id",
            },
            schema_fields={
                "comment_id": "STRING",
                "issue_id": "STRING",
                "author_id": "STRING",
                "body": "STRING",
                "created_at": "TIMESTAMP",
            },
        ),
    ]

    analyzer = SchemaGraphAnalyzer(
        tables=linear_tables,
        project_id="looker-demo-392616",
        dataset_id="linear_product_ops",
        connection_name="default_bigquery_connection",
    )

    # 1. Test Table Classification
    classifications = analyzer.classify_tables()
    assert classifications["dim_workspaces"]["role"] == "Sink / Hierarchy Dimension"
    assert classifications["dim_workspaces"]["is_base_candidate"] is False

    assert classifications["fct_issues"]["role"] == "Accumulating Stateful Fact / Core Entity"
    assert classifications["fct_issues"]["is_base_candidate"] is True

    assert classifications["fct_issue_activities"]["role"] == "Atomic Event Leaf Fact"
    assert classifications["fct_issue_activities"]["is_base_candidate"] is True

    # 2. Test Explore Spec for fct_issues
    explore_spec = analyzer.build_explore_spec("fct_issues")
    assert explore_spec.explore_name == "fct_issues"
    assert explore_spec.base_view == "fct_issues"

    join_names = [j.join_name for j in explore_spec.joins]
    assert "dim_projects" in join_names
    assert "dim_teams" in join_names
    assert "dim_workspaces" in join_names
    assert "parent_issues" in join_names
    assert "assignee" in join_names
    assert "creator" in join_names
    assert "Assignee" in [j.view_label for j in explore_spec.joins]
    assert "Creator" in [j.view_label for j in explore_spec.joins]

    # 3. Test Chasm Trap Elimination via NDT Rollups
    ndt_joins = [j for j in explore_spec.joins if j.is_ndt]
    assert len(ndt_joins) >= 1
    assert any("issue_activities" in j.join_name for j in ndt_joins)
    assert any("issue_comments" in j.join_name for j in ndt_joins)
    for j in ndt_joins:
        assert j.relationship == "one_to_one"

    # 4. Test LookML Model Generation
    model_lkml = analyzer.generate_lookml_model()
    assert 'connection: "default_bigquery_connection"' in model_lkml
    assert "explore: fct_issues {" in model_lkml
    assert "explore: fct_issue_activities {" in model_lkml
    assert "relationship: many_to_one" in model_lkml
    assert "relationship: one_to_one" in model_lkml

    # 5. Test View Generation
    issues_view = analyzer.generate_view_lkml(linear_tables[6])
    assert "view: fct_issues {" in issues_view
    assert "primary_key: yes" in issues_view
    assert "dimension_group: created" in issues_view
    print("✓ All 5 Linear Snowflake Analysis test suites passed successfully!")


if __name__ == "__main__":
    test_linear_snowflake_schema_analysis()
