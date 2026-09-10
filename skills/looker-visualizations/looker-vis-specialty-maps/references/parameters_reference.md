# Specialty & Geospatial Visualizations - Parameters Reference

This document provides a comprehensive dictionary of all configuration parameters for Looker Pie/Donut, Funnel, Timeline, Sankey, Treemap, Sunburst, Wordcloud, Bullet, and Geospatial Maps.

---

## 1. Pie & Donut Visualizations (`looker_pie`)

| Parameter | Type | Default | Valid Options | Description |
| :--- | :--- | :--- | :--- | :--- |
| `value_labels` | string | `"legend"` | `"legend"`, `"label"`, `"value"`, `"none"` | Location of labels. `"legend"` places categories in an interactive side legend. |
| `label_type` | string | `"labPer"` | `"lab"` (label only), `"val"` (value only), `"per"` (percent only), `"labVal"` (label + value), `"labPer"` (label + percent) | Content shown inside or beside each slice. |
| `show_donut` | boolean | `false` | `true`, `false` | When `true`, removes the center to convert the pie into a donut chart. |
| `inner_radius` | number | `50` | `10` to `90` (percentage) | Size of the inner donut hole cutout relative to chart radius. |
| `series_colors` | key-value map | auto | `<slice_name>: "<hex_color>"` | Custom color mapping for individual category slices. |
| `start_angle` | number | `0` | `-360` to `360` | Rotation offset in degrees for the first slice. |

---

## 2. Flow & Sequential Visualizations

### Funnel & Stepped Funnel (`looker_funnel`, `stepped_funnel`)
| Parameter | Type | Default | Valid Options | Description |
| :--- | :--- | :--- | :--- | :--- |
| `show_dropoff` | boolean | `true` | `true`, `false` | Displays calculated drop-off percentages between successive funnel stages. |
| `left_labels` | boolean | `false` | `true`, `false` | Renders stage labels along the left side rather than centered over the slices. |

### Sankey & Dependency Wheel (`looker_sankey`, `dependencywheel`)
| Parameter | Type | Default | Valid Options | Description |
| :--- | :--- | :--- | :--- | :--- |
| `node_opacity` | number | `0.66` | `0.0` to `1.0` | Opacity level for categorical stage nodes. |
| `link_opacity` | number | `0.4` | `0.0` to `1.0` | Opacity level for connection flow ribbons. |
| `rounded_corners` | boolean| `true` | `true`, `false` | Applies soft rounded corners to node blocks. |
| `inactive_opacity`| number | `0.25` | `0.0` to `1.0` | Dimming opacity of unrelated paths when hovering over a specific node. |

### Timeline (`looker_timeline`)
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `timeline_layout` | string | `"horizontal"` | Visual orientation of the timeline axis. |
| `opacity` | number | `0.8` | Fill opacity of the horizontal event bars. |
| `color_application`| object | auto | Palette assignment for categories and status levels. |

### Bullet Chart (`looker_bullet`)
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `show_value_labels`| boolean | `true` | Renders actual performance values on the bullet bar. |
| `series_colors` | map | auto | Overrides colors for the actual bar, target marker, and qualitative background range bands. |

---

## 3. Geospatial Map Parameters (`looker_google_map`, `looker_geo_choropleth`, `looker_geo_coordinates`)

### Rendering Mode & Tile Provider

| Parameter | Type | Default | Valid Options | Description |
| :--- | :--- | :--- | :--- | :--- |
| `map_plot_mode` | string | `"points"` | `"points"`, `"automagic_heatmap"`, `"heatmap"`, `"lines"`, `"areas"`, `"3d_heatmap"` | Rendering method for coordinates. `"automagic_heatmap"` dynamically switches from clustered heatmap to individual points based on zoom. |
| `map_tile_provider` | string | `"light"` | `"light"`, `"light_no_labels"`, `"dark"`, `"dark_no_labels"`, `"satellite"`, `"satellite_streets"`, `"streets"`, `"outdoors"`, `"minimal"` | Base basemap tile style. |
| `map_position` | string | `"fit_data"` | `"fit_data"`, `"custom"` | When `"fit_data"`, automatically pans and zooms to frame all returned data points. |
| `map_latitude` | number | auto | `-90` to `90` | Center latitude when `map_position: custom`. |
| `map_longitude` | number | auto | `-180` to `180` | Center longitude when `map_position: custom`. |
| `map_zoom` | number | `4` | `1` (global) to `18` (street level) | Default zoom level when `map_position: custom`. |
| `map_pannable` | boolean | `true` | `true`, `false` | Permits click-and-drag map panning. |
| `map_zoomable` | boolean | `true` | `true`, `false` | Permits scroll-wheel and pinch zooming. |

### Markers & Heatmap Styling

| Parameter | Type | Default | Valid Options | Description |
| :--- | :--- | :--- | :--- | :--- |
| `map_marker_type` | string | `"circle"` | `"circle"`, `"icon"`, `"circle_and_icon"`, `"none"` | Point marker shape. |
| `map_marker_radius_mode` | string | `"proportional_value"` | `"proportional_value"`, `"equal_to_value"`, `"fixed"` | Determines whether marker size scales with measure value. |
| `map_marker_radius_min` | number | `3` | pixels | Minimum marker size for lowest measure value. |
| `map_marker_radius_max` | number | `20` | pixels | Maximum marker size for highest measure value. |
| `map_marker_color_mode` | string | `"value"` | `"value"`, `"fixed"` | Whether marker color is determined by a color palette gradient or static color. |
| `map_value_colors` | array | `["green", "gold", "red"]` | Array of hex strings | Gradient color stops mapped to the measure range. |
| `quantize_map_value_colors` | boolean | `false` | `true`, `false` | Snaps continuous gradient into discrete color buckets. |
| `reverse_map_value_colors` | boolean | `false` | `true`, `false` | Inverts color gradient direction. |
| `heatmap_opacity` | number | `0.75` | `0.0` to `1.0` | Fill opacity of density heatmap contours. |

### Geo Choropleth (`looker_geo_choropleth`)

| Parameter | Type | Default | Valid Options | Description |
| :--- | :--- | :--- | :--- | :--- |
| `map` | string | `"auto"` | `"auto"`, `"usa"`, `"world"`, `"uk"`, or custom map layer | Geographic boundary dictionary to match region names against. |
| `colors` | string / array | auto | Hex strings | Color palette applied across regional values. |
| `quantize_colors` | boolean | `false` | `true`, `false` | Quantizes continuous metric into discrete shaded steps. |
| `map_use_custom_layer` | boolean | `false` | `true`, `false` | Enables external TopoJSON custom layer. |
| `map_url` | string | `""` | HTTPS TopoJSON URL | Source URL for custom TopoJSON boundaries. |
