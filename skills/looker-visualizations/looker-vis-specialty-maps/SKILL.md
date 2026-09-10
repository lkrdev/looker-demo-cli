---
name: looker-vis-specialty-maps
description: Guidance, query requirements, and LookML dashboard parameters for Looker specialty charts (Pie/Donut, Funnel, Timeline, Sankey, Treemap, Sunburst, Wordcloud, Bullet) and Geospatial maps (Google Maps, Geo Choropleth, Geo Coordinates). Use when configuring or authoring specialty or map tiles in LookML dashboards.
---

# Looker Specialty & Geospatial Visualizations (`looker-vis-specialty-maps`)

This skill covers the setup, query constraints, and LookML dashboard parameters for all Looker specialty, hierarchical, flow, and geographic visual types:

### Proportional & Hierarchical Charts
- **`looker_pie`**: Circular proportional chart with optional donut hole.
- **`looker_donut_multiples`**: Matrix of donut charts split by a grouping dimension.
- **`treemap`**: Nested rectangular areas illustrating hierarchical breakdown by metric weight.
- **`sunburst`**: Concentric multi-level radial rings illustrating hierarchical branching.

### Flow, Conversion & Sequence Charts
- **`looker_funnel` / `stepped_funnel`**: Sequential stage-by-stage dropoff and conversion rate analysis.
- **`looker_sankey` / `sankey`**: Node-and-link flow diagram visualizing volume pathways between entities.
- **`dependencywheel`**: Circular flow diagram illustrating bidirectional network relationships.
- **`looker_timeline`**: Horizontal Gantt-style duration bars plotted over continuous time.
- **`looker_wordcloud`**: Visual frequency cluster where token size represents measure weight.
- **`looker_bullet` / `bullet`**: Compact target-versus-actual performance indicator with qualitative status bands.

### Geospatial Maps
- **`looker_google_map`**: Interactive vector map plotting points, clusters, or heatmaps on Google Maps tiles.
- **`looker_geo_choropleth`**: Static regional map shading geographic regions (countries, states, zipcodes) based on values.
- **`looker_geo_coordinates`**: Static coordinate plotting map.

---

## 1. Query Layout Rules & Constraints

| Vis Type | Required Dimensions | Required Measures | Pivots Allowed? | Strict Row Limit / Constraint |
| :--- | :--- | :--- | :--- | :--- |
| **`looker_pie`** | 1 (categorical) | 1 | ❌ FORBIDDEN | Strictly `<= 50` rows (Looker refuses to render > 50). Best with `<= 7` slices. |
| **`looker_donut_multiples`**| 1 | 1 - 5 | ❌ No | `<= 20` donuts. Multiple dimensions are NOT supported. |
| **`looker_funnel`** | 1 (stage) | 1 | ❌ No | `<= 20` rows. Must be sorted in stage sequence. |
| **`looker_timeline`** | 1 or 2 (entity name) | 2 date fields (start/end) or 1 date + duration measure | ❌ No | `<= 200` rows. |
| **`looker_sankey`** | 2 dimensions (Source, Target) | 1 measure (Flow Volume/Weight) | ❌ No | `<= 200` paths. |
| **`treemap` / `sunburst`** | 2 to 4 dimensions (Hierarchy) | 1 measure (Size) | ❌ No | `<= 200` nodes. |
| **`looker_wordcloud`** | 1 (Tokens/Words) | 1 (Frequency/Weight) | ❌ No | `<= 100` words. |
| **`looker_bullet`** | 1 (Metric Name) | 2 to 3 (Actual, Target, Benchmark) | ❌ No | `<= 30` bars. |
| **`looker_google_map`** | 1 (`location` or `zipcode` type) | 1 or 2 | ❌ No | Up to 5,000 points. |
| **`looker_geo_choropleth`**| 1 (`state`, `country`, `zipcode`) | 1 | ❌ No | Up to 500 regions. |

---

## 2. Quick Parameter Cheatsheet

### Pie / Donut (`looker_pie`)
```yaml
- title: Traffic Source Breakdown
  name: traffic_source_pie
  model: thelook
  explore: order_items
  type: looker_pie
  fields: [users.traffic_source, order_items.order_count]
  sorts: [order_items.order_count desc]
  limit: 6                      # Keep pie slices under 7 for visual clarity
  value_labels: legend          # "legend", "label", "value", "none"
  label_type: labPer            # "lab" (label), "labVal" (label + value), "labPer" (label + percent), "per" (percent)
  show_donut: true              # Cut out center to create a donut chart
  inner_radius: 50              # Percentage radius of donut hole (e.g. 50%)
  series_colors:
    Search: "#4285F4"
    Organic: "#34A853"
    Email: "#FBBC04"
```

### Flow Diagram (`looker_sankey`)
```yaml
- title: User Navigation Flow
  name: user_nav_sankey
  model: thelook
  explore: web_events
  type: looker_sankey
  fields: [web_events.source_page, web_events.target_page, web_events.event_count]
  sorts: [web_events.event_count desc]
  limit: 50
  node_opacity: 0.8             # Node color opacity (0.0 to 1.0)
  link_opacity: 0.4             # Flow ribbon opacity
  rounded_corners: true
```

### Google Maps (`looker_google_map`)
```yaml
- title: Customer Order Heatmap
  name: customer_order_gmap
  model: thelook
  explore: order_items
  type: looker_google_map
  fields: [users.location, order_items.order_count]
  sorts: [order_items.order_count desc]
  limit: 2000
  map_plot_mode: automagic_heatmap # "points", "automagic_heatmap", "heatmap", "lines"
  map_tile_provider: light         # "light", "light_no_labels", "dark", "satellite", "streets"
  map_position: fit_data           # "fit_data" or "custom"
  map_zoom: 4
  map_pannable: true
  map_zoomable: true
  map_marker_type: circle          # "circle", "icon", "circle_and_icon", "none"
  map_marker_radius_mode: proportional_value
  map_marker_radius_min: 3
  map_marker_radius_max: 25
  map_value_colors: ["#E8F0FE", "#4285F4", "#185ABC", "#B3251E"]
```

---

## 3. Deep-Dive References

- [**Parameters Reference**](references/parameters_reference.md): Detailed parameter dictionary for maps, pie, sankey, funnel, timeline, and bullet.
- [**LookML Examples**](references/examples_lookml.md): Ready-to-use modern LookML dashboard element blocks.
