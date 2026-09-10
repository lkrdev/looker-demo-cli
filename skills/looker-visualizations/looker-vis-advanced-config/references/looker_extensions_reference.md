# Looker Extensions & Formatters Engine Reference

Looker provides native extensions on top of standard Highcharts that allow declarative conditional data formatting and annotations safely in JSON without JavaScript functions.

This document details the declarative `formatters` syntax, dynamic annotations, and Looker's validation diagnostic rules.

---

## 1. Declarative `formatters` Array

### Motivation & Architecture
In standard Highcharts, conditional formatting is usually done via JavaScript callback functions (e.g. `point.color = (point.y > 100) ? 'green' : 'red'`). Because Looker strictly prohibits raw JavaScript execution in LookML for security reasons (XSS prevention), Looker provides a **declarative formatters engine**.

The engine evaluates data point conditions during the render cycle, parses data predicates, and injects point-specific style overrides into Highcharts' internal `series[i].data` array.

### Syntax Structure

The `formatters` array is placed inside the target series object within `series`:

```json
{
  "series": [
    {
      "formatters": [
        {
          "select": "<filter_expression>",
          "style": {
            "<style_property>": "<value>"
          }
        }
      ]
    }
  ]
}
```

---

## 2. Selector String (`select`) Grammar & Supported Tokens

The `select` string defines which data points receive the custom style overrides.

### 1. Extremes: `min` and `max`
Identifies the point with the highest or lowest numeric value in the series:
```json
{
  "select": "max",
  "style": {
    "color": "#34A853"
  }
}
```

### 2. Numeric Threshold Comparisons (`value <comparator> <number>`)
Evaluates against static numbers using standard comparators: `>`, `<`, `>=`, `<=`, `=`, `!=`.
- `"value > 1000"`
- `"value <= 0"`
- `"value != 50"`

```json
{
  "select": "value > 50000",
  "style": {
    "color": "#1A73E8"
  }
}
```

### 3. Statistical Aggregate Comparisons (`value <comparator> <aggregate>`)
Compares point values against calculated statistical metrics of the series:
- `"value > mean"` (values above the average)
- `"value < median"` (values below the 50th percentile)
- `"value > orders.average_order_value"` (values exceeding another measure in the query)

```json
{
  "select": "value > mean",
  "style": {
    "color": "#4285F4"
  }
}
```

### 4. Percentile / Ranked Order (`percent_rank <comparator> <decimal>`)
Filters points based on their relative rank in the dataset:
- `"percent_rank > 0.8"` (top 20% of points)
- `"percent_rank <= 0.2"` (bottom 20% of points)

```json
{
  "select": "percent_rank > 0.9",
  "style": {
    "color": "#FBBC04",
    "marker": { "symbol": "diamond", "radius": 8 }
  }
}
```

### 5. String Category Matching (`name <comparator> <value>`)
Matches points where the categorical label equals or differs from a string:
- `"name = California"`
- `"name != Inactive"`

### 6. Logical Combinations (`AND`, `OR`)
Chain multiple conditions together:
- `"value > 1000 AND percent_rank > 0.5"`
- `"name = California OR name = Texas OR name = New York"`

---

## 3. Supported `style` Overrides

When a data point matches the `select` condition, any of the following style properties can be applied:

| Property | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `color` | string | `"#34A853"`, `"#EA4335"` | Point fill color (applies to bar, column, pie slice, or scatter dot). |
| `borderColor` | string | `"#174EA6"` | Point border stroke color. |
| `marker` | object | See below | Custom marker shape, radius, and fill for line/scatter/area points. |
| `dataLabels` | object | `{"enabled": true, "style": {"fontWeight": "bold"}}` | Custom data label appearance for matched points. |

### Matched Marker Styling
```json
{
  "select": "max",
  "style": {
    "color": "#EA4335",
    "marker": {
      "enabled": true,
      "symbol": "diamond",
      "radius": 8,
      "fillColor": "#EA4335",
      "lineColor": "#FFFFFF",
      "lineWidth": 2
    }
  }
}
```

---

## 4. Dynamic Annotations (`annotations`)

Looker supports Highcharts annotations for pinning callout boxes or text labels to specific coordinates:

```json
{
  "annotations": [
    {
      "labels": [
        {
          "point": { "x": 3, "y": 125000 },
          "text": "Campaign Launch Spike",
          "backgroundColor": "rgba(255, 255, 255, 0.9)",
          "borderColor": "#1A73E8",
          "borderRadius": 6,
          "style": {
            "fontSize": "11px",
            "color": "#202124",
            "fontWeight": "bold"
          }
        }
      ]
    }
  ]
}
```

---

## 5. Schema Validation & Error Diagnostic Guide

Looker validates `advanced_vis_config` before passing it to Highcharts. If your config fails validation, inspect this guide:

| Validation Issue | Trigger Condition | How to Fix |
| :--- | :--- | :--- |
| `Formatter Spelling` | Key named `"formatter"` detected anywhere in JSON. | Rename `"formatter"` to `"formatters"` if writing a conditional filter, or use Highcharts string `"format"` template (e.g. `labels: { format: "{value}%" }`). |
| `Root-Level Series Nesting` | `"formatters"` placed at the root level of the JSON. | Nest `"formatters"` inside a specific series object: `{"series": [{"formatters": [...]}]}`. |
| `Array Format Required` | `"series"` or `"annotations"` passed as an object instead of an array. | Ensure `series` is an array: `{"series": [{...}]}`. |
| `Missing Select Expression` | A formatter object is missing the `"select"` key. | Add `"select": "<condition>"` to the formatter object. |
| `Invalid Select Syntax` | Invalid grammar or typo in the `"select"` expression. | Ensure selectors use supported syntax (`min`, `max`, `value > N`, `name = String`). Check that numbers don't contain extraneous characters. |
