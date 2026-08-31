# LookML Snowflake Modeling Reference Patterns

This guide provides tested, copy-pasteable LookML architectural patterns for modeling normalized (3NF) and Snowflake database schemas.

---

## Pattern 1: Multi-Hop Snowflake Joins

### Use Case
A normalized hierarchy where a transaction or state fact links to an entity, which links to a parent grouping, which links to an organization (e.g. `issues` $\to$ `projects` $\to$ `teams` $\to$ `workspaces`).

### Architecture Rules
1. Order matters: Define joins in topological dependency order (parents before grandparents).
2. Use `view_label:` to organize the field picker cleanly in the UI.
3. Keep joins strictly `left_outer` and `many_to_one`.

```lookml
explore: issues {
  label: "Issues & Engineering Productivity"
  view_label: "Issues (Base)"
  description: "Core explore for issue lifecycle, lead time, and team throughput."

  # Hop 1: Issue -> Project (many_to_one)
  join: projects {
    view_label: "Project"
    type: left_outer
    relationship: many_to_one
    sql_on: ${issues.project_id} = ${projects.project_id} ;;
  }

  # Hop 2: Project -> Team (many_to_one)
  join: teams {
    view_label: "Project Team"
    type: left_outer
    relationship: many_to_one
    sql_on: ${projects.team_id} = ${teams.team_id} ;;
  }

  # Hop 3: Team -> Workspace / Tenant (many_to_one)
  join: workspaces {
    view_label: "Workspace (Organization)"
    type: left_outer
    relationship: many_to_one
    sql_on: ${teams.workspace_id} = ${workspaces.workspace_id} ;;
  }
}
```

---

## Pattern 2: Role-Playing Dimensions (Diamond Path Resolution)

### Use Case
An explore has multiple foreign keys pointing to the same physical dimension table (e.g. `assignee_user_id`, `creator_user_id`, `lead_user_id` pointing to `users`).

### Architecture Rules
1. Use `from:` to specify the underlying view.
2. Give each join a unique semantic name (`assignee`, `creator`, `team_lead`).
3. Set distinct `view_label:` headers so dimensions appear in their own section in the Explore field picker.

```lookml
explore: issues {
  # Assignee User Role
  join: assignee {
    from: users
    view_label: "Assignee"
    type: left_outer
    relationship: many_to_one
    sql_on: ${issues.assignee_id} = ${assignee.user_id} ;;
  }

  # Creator / Reporter User Role
  join: creator {
    from: users
    view_label: "Creator (Reporter)"
    type: left_outer
    relationship: many_to_one
    sql_on: ${issues.creator_id} = ${creator.user_id} ;;
  }

  # Project Lead User Role (Snowflake hop from projects)
  join: project_lead {
    from: users
    view_label: "Project Lead"
    type: left_outer
    relationship: many_to_one
    sql_on: ${projects.lead_user_id} = ${project_lead.user_id} ;;
  }
}
```

---

## Pattern 3: Native Derived Table (NDT) Rollup for Chasm Trap Elimination

### Use Case
An explore on `issues` needs aggregate metrics from child tables (e.g., total comments count, last comment timestamp, total activities count) without joining the child tables directly and causing Cartesian row explosion.

### Architecture Rules
1. Define a base explore for the child entity.
2. Create an NDT view that selects the parent PK and metric measures.
3. Join the NDT view to the parent Explore as `relationship: one_to_one`.

```lookml
# Child Base Explore
explore: issue_comments_base {
  hidden: yes
  from: issue_comments
}

# NDT View
view: issue_comments_rollup {
  derived_table: {
    explore_source: issue_comments_base {
      column: issue_id {}
      column: total_comments_count { field: issue_comments.count }
      column: last_comment_time { field: issue_comments.max_created_time }
      column: unique_commenters_count { field: issue_comments.count_distinct_users }
    }
  }

  dimension: issue_id {
    primary_key: yes
    hidden: yes
    type: string
  }

  dimension: total_comments_count {
    label: "Total Comments Count"
    description: "Total number of comments posted on this issue."
    type: number
    value_format_name: decimal_0
  }

  dimension: unique_commenters_count {
    label: "Unique Commenters Count"
    description: "Number of distinct users who have commented on this issue."
    type: number
    value_format_name: decimal_0
  }

  dimension_group: last_comment {
    label: "Last Comment"
    type: time
    timeframes: [raw, time, date, days_ago]
    sql: ${TABLE}.last_comment_time ;;
  }
}

# Parent Explore
explore: issues {
  join: issue_comments_rollup {
    view_label: "Issue Metrics"
    type: left_outer
    relationship: one_to_one
    sql_on: ${issues.issue_id} = ${issue_comments_rollup.issue_id} ;;
  }
}
```

---

## Pattern 4: Bridge / $M:N$ Table Collapse via Array Aggregation (BigQuery)

### Use Case
Issues have multiple labels via `issue_label_mappings` ($M:N$). Joining `labels` directly multiplies issue rows. Instead, collapse labels into a single comma-separated string dimension per issue.

```lookml
view: issue_labels_summary {
  derived_table: {
    sql:
      SELECT
        ilm.issue_id,
        ARRAY_TO_STRING(ARRAY_AGG(DISTINCT l.label_name ORDER BY l.label_name), ', ') AS label_names_list,
        COUNT(DISTINCT ilm.label_id) AS total_labels_count
      FROM `${project_id}.${dataset_id}.issue_label_mappings` AS ilm
      LEFT JOIN `${project_id}.${dataset_id}.dim_labels` AS l ON ilm.label_id = l.label_id
      GROUP BY 1
    ;;
  }

  dimension: issue_id {
    primary_key: yes
    hidden: yes
    type: string
  }

  dimension: labels_list {
    label: "Labels (List)"
    description: "Comma-separated list of all labels assigned to this issue."
    type: string
    sql: ${TABLE}.label_names_list ;;
  }

  dimension: total_labels_count {
    label: "Total Labels Count"
    description: "Number of distinct labels assigned to this issue."
    type: number
    sql: ${TABLE}.total_labels_count ;;
  }
}
```

---

## Pattern 5: Self-Referential / Recursive Hierarchy

### Use Case
An issue can have a parent issue (epic/sub-issue relationship), or an employee has a manager in `dim_users`.

```lookml
explore: issues {
  join: parent_issue {
    from: issues
    view_label: "Parent Issue (Epic)"
    type: left_outer
    relationship: many_to_one
    sql_on: ${issues.parent_issue_id} = ${parent_issue.issue_id} ;;
  }
}
```
