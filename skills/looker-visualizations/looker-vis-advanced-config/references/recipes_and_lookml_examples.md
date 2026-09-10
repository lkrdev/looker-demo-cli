# Highcharts Advanced Vis Config - Recipes & LookML Examples

This document contains 10 production-grade, copy-paste ready LookML dashboard element blocks demonstrating advanced Highcharts configurations via `advanced_vis_config`.

---

## Recipe 1: Dual Independent Y-Axes with Custom Scale Formatting

Splits high-volume revenue currency on the left axis and order count units on the right axis, with custom unit prefixes and dashed gridlines:

```yaml
- title: Revenue vs Order Volume Dual Axis
  name: revenue_vs_volume_dual_axis
  model: thelook
  explore: order_items
  type: looker_column
  fields: [order_items.created_month, order_items.total_sale_price, order_items.order_count]
  sorts: [order_items.created_month asc]
  limit: 12
  x_axis_gridlines: false
  y_axis_gridlines: true
  show_view_names: false
  advanced_vis_config: |
    {
      "chart": {
        "backgroundColor": "transparent"
      },
      "series": [
        {
          "type": "column",
          "color": "#4285F4",
          "yAxis": 0
        },
        {
          "type": "spline",
          "color": "#EA4335",
          "lineWidth": 3,
          "yAxis": 1
        }
      ],
      "yAxis": [
        {
          "title": { "text": "Gross Sales ($)" },
          "labels": { "format": "${value:,.0f}" },
          "gridLineColor": "#EEEEEE"
        },
        {
          "title": { "text": "Total Orders" },
          "labels": { "format": "{value} orders" },
          "opposite": true,
          "gridLineWidth": 0
        }
      ]
    }
  row: 0
  col: 0
  width: 12
  height: 8
```

---

## Recipe 2: Conditional Threshold Highlighting via Declarative `formatters`

Automatically highlights bars above average in green and flags the minimum point in red:

```yaml
- title: Monthly Performance with Dynamic Outlier Highlighting
  name: dynamic_threshold_columns
  model: thelook
  explore: order_items
  type: looker_column
  fields: [order_items.created_month, order_items.total_sale_price]
  sorts: [order_items.created_month asc]
  limit: 12
  x_axis_gridlines: false
  y_axis_gridlines: true
  show_view_names: false
  advanced_vis_config: |
    {
      "chart": {
        "backgroundColor": "transparent"
      },
      "plotOptions": {
        "column": {
          "borderRadius": 4,
          "pointWidth": 26
        }
      },
      "series": [
        {
          "color": "#9AA0A6",
          "formatters": [
            {
              "select": "value > mean",
              "style": {
                "color": "#34A853"
              }
            },
            {
              "select": "min",
              "style": {
                "color": "#EA4335"
              }
            }
          ]
        }
      ]
    }
  row: 0
  col: 12
  width: 12
  height: 8
```

---

## Recipe 3: Target SLA PlotBands & Benchmark PlotLine

Embeds color-coded background status zones (Underperforming vs Goal) and a dashed quota line:

```yaml
- title: Daily Ticket Closures vs SLA Bands
  name: ticket_closure_sla_bands
  model: thelook
  explore: support_tickets
  type: looker_line
  fields: [support_tickets.created_date, support_tickets.closed_count]
  sorts: [support_tickets.created_date asc]
  limit: 30
  x_axis_gridlines: false
  y_axis_gridlines: true
  show_view_names: false
  advanced_vis_config: |
    {
      "yAxis": {
        "title": { "text": "Resolved Tickets" },
        "plotLines": [
          {
            "value": 150,
            "color": "#188038",
            "dashStyle": "Dash",
            "width": 2,
            "zIndex": 5,
            "label": {
              "text": "Daily Quota (150)",
              "align": "right",
              "style": { "color": "#188038", "fontWeight": "bold" }
            }
          }
        ],
        "plotBands": [
          {
            "from": 0,
            "to": 100,
            "color": "rgba(234, 67, 53, 0.08)",
            "label": { "text": "At Risk Zone", "style": { "color": "#D93025" } }
          },
          {
            "from": 100,
            "to": 200,
            "color": "rgba(52, 168, 83, 0.08)",
            "label": { "text": "Healthy Velocity", "style": { "color": "#188038" } }
          }
        ]
      }
    }
  row: 8
  col: 0
  width: 12
  height: 8
```

---

## Recipe 4: Dashed Benchmark Trend Series

Renders actual performance as a solid thick line and a target projection as a dashed line:

```yaml
- title: Actual Revenue vs Budget Projection
  name: actual_vs_budget_line
  model: thelook
  explore: order_items
  type: looker_line
  fields: [order_items.created_month, order_items.total_sale_price, order_items.projected_budget]
  sorts: [order_items.created_month asc]
  limit: 12
  advanced_vis_config: |
    {
      "series": [
        {
          "name": "Actual Revenue",
          "color": "#1A73E8",
          "lineWidth": 3,
          "marker": { "enabled": true, "radius": 4 }
        },
        {
          "name": "Budget Target",
          "color": "#5F6368",
          "dashStyle": "ShortDash",
          "lineWidth": 2,
          "marker": { "enabled": false }
        }
      ]
    }
  row: 8
  col: 12
  width: 12
  height: 8
```

---

## Recipe 5: Semi-Donut KPI Gauge with Angle Arc

Transforms a pie chart into a modern semi-circular speedometer gauge:

```yaml
- title: Customer Satisfaction Score Gauge
  name: csat_semi_donut_gauge
  model: thelook
  explore: survey_responses
  type: looker_pie
  fields: [survey_responses.sentiment_rating, survey_responses.response_count]
  sorts: [survey_responses.response_count desc]
  limit: 3
  advanced_vis_config: |
    {
      "chart": {
        "backgroundColor": "transparent"
      },
      "plotOptions": {
        "pie": {
          "startAngle": -90,
          "endAngle": 90,
          "center": ["50%", "75%"],
          "size": "110%",
          "innerSize": "70%",
          "dataLabels": {
            "enabled": true,
            "format": "{point.name}: {point.percentage:.1f}%"
          }
        }
      }
    }
  row: 16
  col: 0
  width: 12
  height: 8
```

---

## Recipe 6: HTML-Formatted Tile Title with Direct Metric Badges

Replaces the default plain title with rich HTML including color-coded indicator dots:

```yaml
- title: Server Health & Event Latency
  name: server_health_html_title
  model: thelook
  explore: server_logs
  type: looker_column
  fields: [server_logs.log_date, server_logs.error_count, server_logs.success_count]
  sorts: [server_logs.log_date asc]
  limit: 30
  advanced_vis_config: |
    {
      "title": {
        "text": "<span style='color: #34A853;'>●</span> Normal Requests &nbsp;&nbsp;&nbsp; <span style='color: #EA4335;'>●</span> Exceptions",
        "useHTML": true,
        "align": "left",
        "style": {
          "fontSize": "13px",
          "fontFamily": "Roboto, sans-serif"
        }
      },
      "legend": {
        "enabled": false
      }
    }
  row: 16
  col: 12
  width: 12
  height: 8
```

---

## Recipe 7: Logarithmic Y-Axis for Skewed Metrics

Accommodates datasets where metric magnitudes span several orders of magnitude:

```yaml
- title: API Invocations Distribution by Route
  name: api_invocations_log_scale
  model: thelook
  explore: api_metrics
  type: looker_column
  fields: [api_metrics.endpoint_name, api_metrics.call_count]
  sorts: [api_metrics.call_count desc]
  limit: 25
  advanced_vis_config: |
    {
      "yAxis": {
        "type": "logarithmic",
        "title": { "text": "Invocations (Log Scale)" },
        "minorTickInterval": "auto"
      }
    }
  row: 24
  col: 0
  width: 12
  height: 8
```
