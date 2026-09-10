# Cartesian Visualizations - LookML Dashboard Examples

This document contains copy-paste ready, production-grade `.dashboard.lookml` element blocks for Cartesian visualization types.

---

## 1. Grouped & Mixed Column + Line Chart (`looker_column` + `series_types`)

Combines monthly revenue bars with an order count trend line and a benchmark reference line:

```yaml
- title: Monthly Sales Performance
  name: monthly_sales_performance
  model: thelook
  explore: order_items
  type: looker_column
  fields: [order_items.created_month, order_items.total_sale_price, order_items.order_count]
  sorts: [order_items.created_month asc]
  limit: 24
  x_axis_gridlines: false
  y_axis_gridlines: true
  show_view_names: false
  show_y_axis_labels: true
  show_y_axis_ticks: true
  y_axis_combined: false
  show_value_labels: false
  label_density: 25
  x_axis_zoom: true
  y_axis_zoom: true
  series_types:
    order_items.order_count: line
  series_colors:
    order_items.total_sale_price: "#4285F4"
    order_items.order_count: "#EA4335"
  series_labels:
    order_items.total_sale_price: "Gross Revenue ($)"
    order_items.order_count: "Completed Orders"
  series_point_styles:
    order_items.order_count: circle
  reference_lines:
    - reference_type: line
      line_value: mean
      color: "#34A853"
      label: "Average Monthly Sales"
      label_position: right
  y_axes:
    - label: "Gross Revenue ($)"
      orientation: left
      series: [order_items.total_sale_price]
      showLabels: true
      showValues: true
      type: linear
    - label: "Completed Orders"
      orientation: right
      series: [order_items.order_count]
      showLabels: true
      showValues: true
      type: linear
  row: 0
  col: 0
  width: 12
  height: 8
```

---

## 2. Ranked Horizontal Bar Chart with Limit (`looker_bar`)

Presents top 10 categories ranked by gross revenue:

```yaml
- title: Top 10 Product Categories by Revenue
  name: top_10_categories
  model: thelook
  explore: order_items
  type: looker_bar
  fields: [products.category, order_items.total_sale_price]
  sorts: [order_items.total_sale_price desc]
  limit: 50
  limit_displayed_rows: true
  limit_displayed_rows_values:
    num_rows: 10
    position: first
  x_axis_gridlines: false
  y_axis_gridlines: true
  show_view_names: false
  show_value_labels: true
  label_density: 25
  legend_position: center
  hide_legend: true
  series_colors:
    order_items.total_sale_price: "#1A73E8"
  series_labels:
    order_items.total_sale_price: "Revenue"
  modern2026: true
  border_radius: 4
  row: 0
  col: 12
  width: 12
  height: 8
```

---

## 3. Stacked Area Chart (100% Percent Share) (`looker_area`)

Displays market share percentage of product departments over time:

```yaml
- title: Department Revenue Share Over Time
  name: dept_rev_share_area
  model: thelook
  explore: order_items
  type: looker_area
  fields: [order_items.created_month, products.department, order_items.total_sale_price]
  pivots: [products.department]
  sorts: [order_items.created_month asc]
  limit: 500
  stacking: percent
  show_value_labels: false
  x_axis_gridlines: false
  y_axis_gridlines: true
  show_y_axis_labels: true
  show_y_axis_ticks: true
  y_axis_combined: true
  legend_position: right
  discontinuous_nulls: false
  series_colors:
    Men - order_items.total_sale_price: "#4285F4"
    Women - order_items.total_sale_price: "#FBBC04"
  row: 8
  col: 0
  width: 12
  height: 8
```

---

## 4. Scatterplot of User Margin vs Frequency (`looker_scatter`)

Correlates customer purchase count with customer average order margin:

```yaml
- title: Customer Order Frequency vs Margin
  name: customer_scatter
  model: thelook
  explore: order_items
  type: looker_scatter
  fields: [order_items.order_count, order_items.average_margin]
  sorts: [order_items.order_count desc]
  limit: 1000
  x_axis_gridlines: true
  y_axis_gridlines: true
  show_view_names: false
  show_y_axis_labels: true
  show_x_axis_label: true
  x_axis_label: "Lifetime Orders"
  series_colors:
    order_items.average_margin: "#34A853"
  point_style: circle
  row: 8
  col: 12
  width: 12
  height: 8
```

---

## 5. Waterfall Bridge Chart (`looker_waterfall`)

Visualizes net profit progression starting from gross revenue through discounts, refunds, and costs:

```yaml
- title: Net Revenue Bridge
  name: net_revenue_bridge
  model: thelook
  explore: financial_summary
  type: looker_waterfall
  fields: [financial_summary.flow_stage, financial_summary.amount]
  sorts: [financial_summary.stage_order asc]
  limit: 20
  show_value_labels: true
  label_density: 25
  x_axis_gridlines: false
  y_axis_gridlines: true
  series_colors:
    financial_summary.amount: "#4285F4"
  row: 16
  col: 0
  width: 12
  height: 8
```
