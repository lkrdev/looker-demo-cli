# Tabular & Single Value/KPI Visualizations - Parameters Reference

This document provides a comprehensive dictionary of all configuration parameters for Looker Grid, Legacy Table, Single Value / KPI cards, and Single Record tiles.

---

## 1. Table & Looker Grid Parameters (`looker_grid`, `table`)

### General Layout & Theme

| Parameter | Type | Default | Valid Options | Description |
| :--- | :--- | :--- | :--- | :--- |
| `table_theme` | string | `"modern"` | `"modern"`, `"white"`, `"gray"`, `"transparent"`, `"unstyled"`, `"classic"`, `"high-contrast"` | Preset visual theme controlling cell padding, borders, and zebra striping. |
| `show_row_numbers` | boolean | `true` | `true`, `false` | Prepends an index column with 1-based sequential row numbers. |
| `truncate_text` | boolean | `true` | `true`, `false` | Truncates cell content exceeding column width with an ellipsis (`...`). |
| `truncate_header` | boolean | `false` | `true`, `false` | Truncates header titles exceeding column width. |
| `size_to_fit` | boolean | `true` | `true`, `false` | Automatically scales all column widths proportionally to fill 100% of the container width. |
| `show_view_names` | boolean | `false` | `true`, `false` | Prepends the LookML view name to column headers (e.g. `Orders Total Sale Price` vs `Total Sale Price`). |
| `rows_font_size` | number / string | `12` | e.g. `12`, `"11px"` | Base font size in pixels for body table cells. |
| `minimum_column_width` | number | `100` | pixels | Minimum allowable column width during auto-sizing or manual drag resize. |

### Totals & Pagination

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `show_totals` | boolean | `true` | Displays the column totals row at the bottom of the table (requires `total: true` in the query). |
| `show_row_totals` | boolean | `true` | Displays row total columns on pivoted queries (requires `row_total: true` in the query). |
| `table_show_footer` | boolean | `true` | Displays the bottom summary bar showing row counts and pagination controls. |
| `table_enable_pagination` | boolean | `true` | Enables pagination controls for multi-page datasets. |
| `table_page_size_options` | string | `"25, 50, 100"` | Comma-separated list of rows-per-page options available to viewers. |

### Column Formatting & Cell Bars

| Parameter | Type | Structure | Description |
| :--- | :--- | :--- | :--- |
| `series_column_widths` | key-value map | `<field_name>: <width_px>` | Explicit pixel width for individual columns (e.g. `users.id: 70`). |
| `series_labels` | key-value map | `<field_name>: "<custom_header>"` | Custom header label overriding the field's LookML label. |
| `series_cell_visualizations` | nested object map | See schema below | Renders an inline horizontal progress bar inside numeric measure cells. |
| `series_text_format` | nested object map | See schema below | Custom typography (font color, background color, bold, alignment) per column. |

#### Cell Visualizations Schema (`series_cell_visualizations`)
```yaml
series_cell_visualizations:
  order_items.total_sale_price:
    is_active: true             # Activates the horizontal bar chart inside each cell
    value_display: true         # Displays the numeric value next to or over the bar
    palette:
      collection_id: "google"
      palette_id: "google-categorical-0"
```

#### Series Text Format Schema (`series_text_format`)
```yaml
series_text_format:
  order_items.total_sale_price:
    align: right                # "left", "center", "right"
    bold: true
    italic: false
    underline: false
    strikethrough: false
    fg_color: "#1A73E8"
    bg_color: "#E8F0FE"
    font_size: 13
```

---

## 2. Advanced Grid Features: Row Groups, Sparklines & Column Groups

### Row Grouping (`row_groups`)

Enables collapsible hierarchy and subtotals across multiple dimensions:

```yaml
row_groups:
  enabled: true
  row_grouping_fields: [users.country, users.state]
  default_display_level: collapsed  # "collapsed", "top_expanded", "all_expanded"
  configurable_subtotals: true
  subtotal_location: bottom         # "top", "bottom"
  group_column_header: "Geographic Hierarchy"
  show_grouped_columns: false
  show_group_counts: true          # Shows (N) child rows in group header
```

### Table Sparklines (`table_sparklines`)

Embeds inline Highcharts mini-charts directly inside table rows:

```yaml
table_sparklines:
  enabled: true
  trend_dimension: order_items.created_month
  sparkline_measures: [order_items.total_sale_price]
  total_measures: [order_items.order_count]
  chart_type: line                 # "line", "area", "column"
  color: "#4285F4"
  show_null_points: false
  sparkline_row_height: 48         # 24 to 120 pixels
  show_x_axis: false
  show_sparkline_tooltip: true
```

### Column Grouping (`table_column_groups`)

Creates multi-tier parent header cells spanning multiple child columns:

```yaml
table_column_groups:
  enabled: true
  defaultDisplayState: expanded    # "expanded", "collapsed"
  namespaceCsvHeaders: true        # Prepends group title to CSV export headers
  groups:
    - id: sales_metrics
      title: "Sales & Fulfillment Performance"
      columns: [order_items.total_sale_price, order_items.order_count]
      headerBackgroundColor: "#F1F3F4"
      headerTextColor: "#202124"
```

---

## 3. Single Value & KPI Parameters (`single_value`)

| Parameter | Type | Default | Valid Options | Description |
| :--- | :--- | :--- | :--- | :--- |
| `show_comparison` | boolean | `false` | `true`, `false` | Enables display of the comparison metric below the headline value. |
| `comparison_type` | string | `"value"` | `"value"`, `"change"`, `"change_percentage"`, `"progress"`, `"progress_percentage"` | Format of the comparison delta. |
| `comparison_row` | string | `"first"` | `"first"`, `"second"`, `"last"`, `"total"` | Identifies which row from the query result represents the baseline for comparison. |
| `comparison_series` | string | auto | Measure field name | Explicit field to use for comparison if multiple measures exist. |
| `comparison_label` | string | `""` | e.g. `"vs Last Year"` | Explanatory sublabel shown next to or below the delta value. |
| `comparison_reverse_colors`| boolean| `false`| `true`, `false` | Inverts default traffic-light colors: negative values render green, positive render red (useful for Costs, Bounces, Errors). |
| `show_comparison_label`| boolean | `true` | `true`, `false` | Toggles display of the comparison subtitle string. |
| `single_value_title` | string | auto | e.g. `"Total Active Users"`| Custom title replacing the default field name. |
| `show_single_value_title`| boolean | `true`| `true`, `false` | Displays or suppresses the title above the KPI number. |
| `show_chart_component` | boolean | `false` | `true`, `false` | Renders an inline sparkline trend or progress bar inside the card. |
| `show_sparkline_tooltip`| boolean| `true` | `true`, `false` | Shows numeric tooltip when hovering over sparkline points. |
| `show_x_axis` | boolean | `false` | `true`, `false` | Renders date/time tick labels under the sparkline trend. |
| `progress_bar_width` | string | `"full"` | `"full"`, `"compact"` | Width variant for progress bar comparison mode. |
| `progress_color` | string | `"#34A853"` | Hex color string | Custom color fill for the progress bar. |
