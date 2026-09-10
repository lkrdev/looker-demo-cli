# Tabular & Single Value/KPI Visualizations - LookML Dashboard Examples

This document contains copy-paste ready `.dashboard.lookml` element blocks for modern table grids, KPI cards with comparisons, embedded sparklines, and single-record details.

---

## 1. Executive Scorecard KPI Card with Sparkline & MoM Delta (`single_value`)

Renders a high-impact KPI tile comparing the current month's revenue with the previous month and displaying an inline sparkline:

```yaml
- title: Monthly Net Revenue
  name: monthly_net_revenue_kpi
  model: thelook
  explore: order_items
  type: single_value
  fields: [order_items.created_month, order_items.total_sale_price]
  sorts: [order_items.created_month desc]
  limit: 2
  show_comparison: true
  comparison_type: change_percentage
  comparison_row: first
  comparison_label: "vs Prior Month"
  comparison_reverse_colors: false
  single_value_title: "Total Net Revenue"
  show_single_value_title: true
  show_comparison_label: true
  show_chart_component: true
  show_sparkline_tooltip: true
  show_x_axis: false
  row: 0
  col: 0
  width: 6
  height: 4
```

---

## 2. Modern Data Grid with Cell Bar Visualizations & Column Widths (`looker_grid`)

Provides an analytical table with inline visual bars for performance metrics:

```yaml
- title: Product Category Sales & Margins
  name: category_sales_grid
  model: thelook
  explore: order_items
  type: looker_grid
  fields: [products.category, products.department, order_items.order_count, order_items.total_sale_price, order_items.average_margin]
  sorts: [order_items.total_sale_price desc]
  limit: 100
  table_theme: modern
  show_row_numbers: true
  truncate_text: true
  size_to_fit: true
  show_view_names: false
  show_totals: true
  table_show_footer: true
  table_enable_pagination: true
  table_page_size_options: "25, 50, 100"
  series_column_widths:
    products.category: 160
    products.department: 120
    order_items.order_count: 100
  series_labels:
    products.category: "Category"
    products.department: "Dept"
    order_items.order_count: "Orders"
    order_items.total_sale_price: "Gross Revenue"
    order_items.average_margin: "Avg Margin %"
  series_cell_visualizations:
    order_items.total_sale_price:
      is_active: true
      value_display: true
  row: 0
  col: 6
  width: 18
  height: 8
```

---

## 3. Advanced Grid with Embedded Trend Sparklines (`looker_grid`)

Embeds high-density timeseries trend lines within each row:

```yaml
- title: Department Trends with Inline Sparklines
  name: dept_sparklines_grid
  model: thelook
  explore: order_items
  type: looker_grid
  fields: [products.department, order_items.created_month, order_items.total_sale_price, order_items.order_count]
  sorts: [products.department asc, order_items.created_month asc]
  limit: 500
  table_theme: modern
  show_row_numbers: false
  size_to_fit: true
  table_sparklines:
    enabled: true
    trend_dimension: order_items.created_month
    sparkline_measures: [order_items.total_sale_price]
    total_measures: [order_items.order_count]
    chart_type: area
    color: "#1A73E8"
    sparkline_row_height: 52
    show_null_points: false
    show_x_axis: false
    show_sparkline_tooltip: true
  row: 8
  col: 0
  width: 12
  height: 8
```

---

## 4. Single Record Inspector (`looker_single_record`)

Presents a clean, two-column attribute inspector card for an individual entity:

```yaml
- title: Customer Profile Details
  name: customer_profile_record
  model: thelook
  explore: users
  type: looker_single_record
  fields: [users.id, users.name, users.email, users.city, users.state, users.created_date, users.traffic_source]
  filters:
    users.id: "1042"
  limit: 1
  show_view_names: false
  row: 8
  col: 12
  width: 12
  height: 8
```
