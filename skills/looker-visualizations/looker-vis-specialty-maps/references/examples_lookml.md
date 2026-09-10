# Specialty & Geospatial Visualizations - LookML Dashboard Examples

This document contains copy-paste ready `.dashboard.lookml` element blocks for Pie/Donut, Sankey, Funnel, Timeline, Google Map, and Geo Choropleth tiles.

---

## 1. Donut Share Chart (`looker_pie`)

Renders a modern donut chart displaying percentage share of top marketing acquisition channels:

```yaml
- title: Marketing Channel Attribution
  name: channel_attribution_donut
  model: thelook
  explore: order_items
  type: looker_pie
  fields: [users.traffic_source, order_items.order_count]
  sorts: [order_items.order_count desc]
  limit: 6
  value_labels: legend
  label_type: labPer
  show_donut: true
  inner_radius: 55
  series_colors:
    Search: "#4285F4"
    Organic: "#34A853"
    Email: "#FBBC04"
    Display: "#EA4335"
    Social: "#AD7FE6"
  row: 0
  col: 0
  width: 12
  height: 8
```

---

## 2. Multi-Stage Conversion Funnel (`looker_funnel`)

Visualizes drop-off across e-commerce checkout funnel stages:

```yaml
- title: Checkout Funnel Drop-off
  name: checkout_funnel
  model: thelook
  explore: web_events
  type: looker_funnel
  fields: [web_events.funnel_step, web_events.unique_visitors]
  sorts: [web_events.step_order asc]
  limit: 10
  show_dropoff: true
  left_labels: true
  series_colors:
    web_events.unique_visitors: "#1A73E8"
  row: 0
  col: 12
  width: 12
  height: 8
```

---

## 3. Sankey User Pathway Diagram (`looker_sankey`)

Maps customer navigation transitions between touchpoints:

```yaml
- title: User Transition Pathways
  name: user_transition_sankey
  model: thelook
  explore: web_events
  type: looker_sankey
  fields: [web_events.previous_page, web_events.current_page, web_events.event_count]
  sorts: [web_events.event_count desc]
  limit: 50
  node_opacity: 0.75
  link_opacity: 0.35
  rounded_corners: true
  row: 8
  col: 0
  width: 12
  height: 8
```

---

## 4. Interactive Google Map with Proportional Bubbles (`looker_google_map`)

Plots user order volume by geographic coordinates using proportional circles and a custom dark theme:

```yaml
- title: Regional Order Concentration
  name: regional_order_gmap
  model: thelook
  explore: order_items
  type: looker_google_map
  fields: [users.location, order_items.order_count, order_items.total_sale_price]
  sorts: [order_items.order_count desc]
  limit: 1000
  map_plot_mode: points
  map_tile_provider: dark
  map_position: fit_data
  map_marker_type: circle
  map_marker_radius_mode: proportional_value
  map_marker_radius_min: 4
  map_marker_radius_max: 22
  map_marker_color_mode: value
  map_value_colors: ["#34A853", "#FBBC04", "#EA4335"]
  map_pannable: true
  map_zoomable: true
  row: 8
  col: 12
  width: 12
  height: 8
```

---

## 5. State-Level Geo Choropleth Heatmap (`looker_geo_choropleth`)

Shades US states based on sales volume:

```yaml
- title: United States Sales Distribution
  name: us_sales_choropleth
  model: thelook
  explore: order_items
  type: looker_geo_choropleth
  fields: [users.state, order_items.total_sale_price]
  sorts: [order_items.total_sale_price desc]
  limit: 50
  map: usa
  colors: ["#E8F0FE", "#1A73E8", "#174EA6"]
  quantize_colors: false
  show_view_names: false
  row: 16
  col: 0
  width: 12
  height: 8
```
