---
name: looker-vis-cartesian
description: Guidance, query requirements, and LookML dashboard parameters for Looker cartesian visualizations including Column, Bar, Line, Area, Scatter, Waterfall, Boxplot, and Histogram. Use when configuring, generating, or debugging cartesian chart tiles in LookML dashboards.
---

# Looker Cartesian Visualizations (`looker-vis-cartesian`)

This skill covers the setup, query constraints, and LookML dashboard parameters for all Looker Cartesian visualizations:
- **`looker_column`**: Vertical bar chart comparing metrics across categories or discrete time intervals.
- **`looker_bar`**: Horizontal bar chart ideal for long category labels or ranked lists.
- **`looker_line`**: Continuous series tracking trend, velocity, or rate over time.
- **`looker_area`**: Filled line chart highlighting volume and cumulative quantity over time.
- **`looker_scatter`**: Two-dimensional point distribution comparing two metrics or observing correlation.
- **`looker_waterfall`**: Cumulative bridge chart illustrating sequential positive and negative contributions.
- **`looker_boxplot`**: Distribution chart displaying median, quartiles, and statistical whiskers.
- **`looker_histogram`**: Frequency distribution grouping continuous data into automatic or specified bins.

---

## 1. Query Layout Rules & Heuristics

To ensure Looker renders a Cartesian chart properly without errors or fallback to tables, your explore query must satisfy these layout rules:

| Vis Type | Required Dimensions | Required Measures | Pivots Allowed? | Common Sort Pattern |
| :--- | :--- | :--- | :--- | :--- |
| **`looker_column`** | 1 (categorical or time) | 1 to 10 | Yes (1 pivot dimension) | Dimension ascending or Measure descending |
| **`looker_bar`** | 1 (categorical) | 1 to 10 | Yes (1 pivot dimension) | Measure descending (ranked) |
| **`looker_line`** | 1 (timeframe or ordered numeric) | 1 to 10 | Yes (1 pivot dimension) | Date/time ascending |
| **`looker_area`** | 1 (timeframe) | 1 to 10 | Yes (1 pivot dimension) | Date/time ascending |
| **`looker_scatter`** | 0 to 2 | 1 to 2 (or 2 numeric measures) | Yes (1 pivot) | Primary measure descending |
| **`looker_waterfall`** | 1 (categorical or stage) | 1 (or 2: base & change) | No | Step sequence ascending |
| **`looker_boxplot`** | 1 (category) | 1 raw measure or 5 percentile measures | No | Category alphabetical |
| **`looker_histogram`** | 0 or 1 | 1 (continuous measure) | Optional | Measure value ascending |

### Automatic Heuristics
Looker applies internal rules when determining default visualizations:
1. **Trend over Time**: 1 timeframe dimension + 1 measure -> lines (`looker_line`) or areas (`looker_area`) are strongly prioritized.
2. **Categorical Dimension**: 1 non-time dimension + 1 measure -> columns (`looker_column`) or bars (`looker_bar`) are prioritized.
3. **Pivoted Subseries**: 1 dimension + 1 pivot + 1 measure -> grouped or stacked column chart.
4. **Relationship between 2 Numeric Series**: 2 numeric series -> scatterplot (`looker_scatter`).

---

## 2. Quick Parameter Cheatsheet

Here are the most critical LookML parameters used in cartesian dashboard elements:

```yaml
- title: Revenue Trend by Category
  name: revenue_trend
  model: thelook
  explore: order_items
  type: looker_line
  fields: [orders.created_month, orders.total_revenue, orders.order_count]
  sorts: [orders.created_month asc]
  limit: 500

  # --- Stacking & Plotting ---
  stacking: ""                  # "" (grouped), "normal" (stacked), or "percent" (100% stacked)
  show_value_labels: true       # Display metric values directly on data points
  label_density: 25             # Value label density (1 - 50)

  # --- Series Type Mixing & Styling ---
  series_types:
    orders.total_revenue: area  # Render total_revenue as area while others remain line
    orders.order_count: line
  series_colors:
    orders.total_revenue: "#4285F4"
    orders.order_count: "#EA4335"
  series_labels:
    orders.total_revenue: "Gross Revenue"
    orders.order_count: "Total Orders"
  series_point_styles:
    orders.total_revenue: "circle" # "circle", "square", "diamond", "triangle", "triangle-down"

  # --- Axes Configuration ---
  x_axis_gridlines: false
  y_axis_gridlines: true
  show_y_axis_labels: true
  show_y_axis_ticks: true
  y_axis_combined: false        # False splits metrics into separate dual Y-axes
  y_axis_reversed: false
  x_axis_reversed: false
  x_axis_zoom: true
  y_axis_zoom: true

  # --- Legend & Layout ---
  legend_position: center       # "left", "center", "right"
  hide_legend: false

  # --- Reference Lines ---
  reference_lines:
    - reference_type: line
      line_value: mean
      range_start: max
      range_end: min
      margin_top: deviation
      margin_value: mean
      margin_bottom: deviation
      label_position: right
      color: "#EA4335"
      value_format: "$#,##0"
      label: "Average Target"

  # --- Modern Looker Look & Feel (2026+) ---
  modern2026: true
  border_radius: 4              # Pixel border radius for bar/column ends
  hover_halo_size: 12
```

---

## 3. Deep-Dive References

- [**Parameters Reference**](references/parameters_reference.md): Detailed parameter dictionary including types, valid options, default values, and deprecation notices.
- [**LookML Examples**](references/examples_lookml.md): Complete, copy-paste ready `.dashboard.lookml` element definitions for all 8 cartesian types.
