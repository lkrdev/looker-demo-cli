---
name: looker-vis-tabular-kpi
description: Guidance, query requirements, and LookML dashboard parameters for Looker tabular and KPI visualizations including Looker Grid (modern table), Legacy Table, Single Value (KPI cards with comparisons and sparklines), and Single Record. Use when building data grids, executive scorecard KPIs, or detail inspector tiles in LookML dashboards.
---

# Looker Tabular & Single Value/KPI Visualizations (`looker-vis-tabular-kpi`)

This skill covers the setup, query constraints, and LookML dashboard parameters for all Looker tabular, scorecard, and record inspector visual types:
- **`looker_grid`**: The modern, high-performance table visualization in Looker (supporting themes, inline cell bar visualizations, conditional formatting, row grouping, column grouping, and embedded sparkline trends).
- **`table`**: The legacy Looker table engine (primarily used for backwards-compatibility).
- **`single_value`**: Executive KPI scorecard tile displaying a headline metric, secondary comparison value (percentage change, progress bar, or delta), and sparkline trend charts.
- **`looker_single_record`**: Transposed two-column key-value inspector displaying all attributes of a single selected record/row.

---

## 1. Query Layout Rules & Heuristics

| Vis Type | Required Dimensions | Required Measures | Pivots Allowed? | Row & Column Limits | Recommended Usage |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`looker_grid`** | 0 to 50 | 0 to 50 | Yes (up to 3) | Up to 5,000 rows | Complex multi-metric reporting, audit trails, and financial statements. |
| **`table`** | 0 to 50 | 0 to 50 | Yes | Up to 500 rows | Legacy table layouts. Prefer `looker_grid` for all new tiles. |
| **`single_value`** | 0 to 1 (optional timeframe) | 1 to 2 | Optional (1 pivot) | 1 row (or 2 rows for period-over-period comparison) | Headline summary numbers at the top of a dashboard. |
| **`looker_single_record`** | 1 to 50 | 0 to 50 | No | Strictly 1 row (limit: 1) | User/order/account detail cards. |

### Query Heuristics
1. **Single Measure, No Dimensions**: When a query produces exactly 1 measure and 0 dimensions, Looker automatically defaults to `single_value`.
2. **Single Row with ID/Primary Key**: When the query result contains 1 row and includes an ID or primary key field, Looker defaults to `looker_single_record` or `looker_grid`.
3. **Period-Over-Period KPI Comparison**:
   - Query layout: 1 timeframe dimension (e.g. Month) sorted descending + 1 measure + limit: 2 (Row 1 = Current Period, Row 2 = Prior Period).
   - In `vis_config`: `show_comparison: true`, `comparison_type: change_percentage`, `comparison_row: first`.

---

## 2. Quick Parameter Cheatsheet

### Modern Table (`looker_grid`)
```yaml
- title: Top Customers Overview
  name: customer_grid
  model: thelook
  explore: order_items
  type: looker_grid
  fields: [users.id, users.name, users.city, order_items.order_count, order_items.total_sale_price]
  sorts: [order_items.total_sale_price desc]
  limit: 100

  # --- Table Theme & Appearance ---
  table_theme: modern           # "modern", "white", "gray", "transparent", "unstyled", "classic"
  show_row_numbers: true
  truncate_text: true
  truncate_header: false
  size_to_fit: true
  show_view_names: false

  # --- Totals & Footer ---
  show_totals: true
  show_row_totals: true
  table_show_footer: true

  # --- Pagination ---
  table_enable_pagination: true
  table_page_size_options: "25, 50, 100"

  # --- Column Cell Bars & Widths ---
  series_column_widths:
    users.id: 80
    users.name: 180
  series_cell_visualizations:
    order_items.total_sale_price:
      is_active: true
      value_display: true
```

### Executive KPI Card (`single_value`)
```yaml
- title: Monthly Gross Revenue
  name: monthly_gross_revenue
  model: thelook
  explore: order_items
  type: single_value
  fields: [order_items.created_month, order_items.total_sale_price]
  sorts: [order_items.created_month desc]
  limit: 2                      # Row 1 is current month, Row 2 is prior month

  # --- Comparison Settings ---
  show_comparison: true
  comparison_type: change_percentage  # "value", "change", "change_percentage", "progress", "progress_percentage"
  comparison_row: first
  comparison_label: "vs Prior Month"
  comparison_reverse_colors: false   # True turns negative numbers green and positive red

  # --- Card Titles & Labels ---
  single_value_title: "Total Revenue"
  show_single_value_title: true
  show_comparison_label: true

  # --- Sparkline Trends & Progress Bar ---
  show_chart_component: true    # Renders trend sparkline or progress bar below metric
  show_sparkline_tooltip: true
  show_x_axis: false
  progress_bar_width: full      # "full" or "compact"
  progress_color: "#34A853"
```

---

## 3. Deep-Dive References

- [**Parameters Reference**](references/parameters_reference.md): Full reference for table styling, sparklines, row groups, column groups, and KPI comparisons.
- [**LookML Examples**](references/examples_lookml.md): Ready-to-use modern LookML dashboard element blocks.
