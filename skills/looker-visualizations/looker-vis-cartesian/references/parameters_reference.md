# Cartesian Visualizations - LookML Parameters Reference

This document provides a comprehensive dictionary of all configuration parameters supported on Looker Cartesian chart elements (`looker_column`, `looker_bar`, `looker_line`, `looker_area`, `looker_scatter`, `looker_waterfall`, `looker_boxplot`, `looker_histogram`) within LookML dashboards.

---

## 1. Plotting & Stacking Parameters

| Parameter | Type | Default | Valid Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `stacking` | string | `""` | `""` (grouped / overlay), `"normal"` (stacked), `"percent"` (100% stacked) | Determines how multiple series or pivoted dimensions are grouped or stacked against each other. |
| `show_value_labels` | boolean | `false` | `true`, `false` | If enabled, renders value labels directly on top of or inside data points / bars. |
| `label_density` | number | `25` | `1` to `50` | Controls the threshold density for dropping overlapping data labels when data points are crowded. |
| `font_size` | string / number | `"12px"` | e.g. `"10px"`, `"12px"`, `"14pt"` | Font size for axis labels and value text. |
| `label_rotation` | number | `0` | `-360` to `360` (common: `0`, `-45`, `-90`) | Degrees to rotate value labels for better legibility when space is constrained. |
| `discontinuous_nulls`| boolean | `false` | `true`, `false` | In line and area charts, treats null data points as line breaks instead of connecting them across gaps. |
| `show_null_points` | boolean | `true` | `true`, `false` | Whether null data points are visibly plotted or omitted from the canvas. |
| `hide_legend` | boolean | `false` | `true`, `false` | Suppresses rendering of the chart legend. |
| `legend_position` | string | `"center"` | `"left"`, `"center"`, `"right"` | Placement of the series legend relative to the chart canvas. |

---

## 2. Series Customization & Mixing

| Parameter | Type | Structure | Description |
| :--- | :--- | :--- | :--- |
| `series_types` | key-value map | `<field_name>: <chart_type>` | Allows mixing different chart types on the same canvas. Supported types: `line`, `column`, `bar`, `area`, `scatter`, `boxplot`. |
| `series_colors` | key-value map | `<field_name_or_pivot>: "<hex_color>"` | Custom hex or rgb color assigned to a specific measure or pivot subseries (e.g. `orders.total_revenue: "#4285F4"`). |
| `series_labels` | key-value map | `<field_name_or_pivot>: "<display_label>"`| Overrides the default field label shown in tooltips and legends. |
| `series_point_styles` | key-value map | `<field_name>: <marker_shape>` | Marker symbol shape: `"circle"`, `"square"`, `"diamond"`, `"triangle"`, `"triangle-down"`. |
| `series_value_format` | key-value map | `<field_name>: "<format_string>"` | Explicit number format pattern applied to data labels and tooltips (e.g. `"$#,##0.00"`). |
| `point_style` | string | `"none"` | `"none"`, `"circle"`, `"outline"` | Global point marker style for line and area series when individual points are highlighted. |

---

## 3. Axes Configuration

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `x_axis_gridlines` | boolean | `false` | Whether vertical gridlines are drawn along the X-axis. |
| `y_axis_gridlines` | boolean | `true` | Whether horizontal gridlines are drawn along the Y-axis. |
| `show_y_axis_labels`| boolean | `true` | Displays or hides numerical values along the Y-axis scale. |
| `show_y_axis_ticks` | boolean | `true` | Renders tick markers along the Y-axis line. |
| `show_x_axis_label` | boolean | `true` | Displays or hides the descriptive title for the X-axis. |
| `show_x_axis_ticks` | boolean | `true` | Renders tick markers along the X-axis line. |
| `x_axis_label` | string | auto | Overrides the X-axis title text. |
| `y_axes` | array of objects | auto | Explicit configuration array for multiple or split Y-axes (see schema below). |
| `y_axis_combined` | boolean | `true` | When `true`, measures share a single Y-axis scale. When `false`, splits each measure onto a separate axis. |
| `y_axis_reversed` | boolean | `false` | Inverts the Y-axis orientation (maximum at bottom, minimum at top). |
| `x_axis_reversed` | boolean | `false` | Inverts the X-axis orientation (left-to-right to right-to-left). |
| `swap_axes` | boolean | `false` | Transposes the X and Y axes (e.g., swapping horizontal and vertical orientation). |
| `x_axis_zoom` | boolean | `true` | Enables interactive horizontal box/drag zooming on dashboards and explores. |
| `y_axis_zoom` | boolean | `true` | Enables interactive vertical box/drag zooming. |

### Schema for Explicit `y_axes` Blocks

When fine-tuning dual or multi-axis layouts:

```yaml
y_axes:
  - label: "Revenue ($)"
    orientation: left
    series: [orders.total_revenue]
    showLabels: true
    showValues: true
    unpinAxis: false
    tickDensity: default
    tickDensityCustom: 5
    type: linear          # linear, log
    minValue: 0
    maxValue: 100000
  - label: "Order Count"
    orientation: right
    series: [orders.count]
    showLabels: true
    showValues: true
    unpinAxis: true
    type: linear
```

---

## 4. Reference Lines

Looker provides built-in statistical and static reference lines:

```yaml
reference_lines:
  - reference_type: line       # line, range, margins
    line_value: mean           # mean, median, max, min, or explicit number e.g. "50000"
    label: "Industry Average"
    label_position: right      # left, center, right
    color: "#EA4335"
    value_format: "$#,##0"
```

Margin / range bands:
```yaml
reference_lines:
  - reference_type: margins
    line_value: mean
    margin_top: deviation      # deviation, variance, or percent
    margin_value: mean
    margin_bottom: deviation
    color: "#34A853"
```

---

## 5. Row Display Limits & Filtering

| Parameter | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `limit_displayed_rows` | boolean | `true` | Restricts how many rows are rendered in the visual without modifying the underlying SQL `LIMIT`. |
| `limit_displayed_rows_values` | object | `{num_rows: 10, position: "first"}` | Position can be `"first"` or `"last"`. Useful for "Top 10" or "Bottom 5" charts. |

---

## 6. Modern 2026+ Visual Attributes

These properties activate Looker's modern styling tokens:

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `modern2026` | boolean | `true` | Activates modern rounded styling, updated typography, and responsive spacing. |
| `border_radius` | number | `2` | Corner border radius in pixels for column and bar series caps. |
| `hover_halo_size` | number | `10` | Size in pixels of the interactive highlight halo on mouse hover. |
