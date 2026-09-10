---
name: looker-vis-advanced-config
description: Comprehensive guide to Looker's Highcharts Advanced Vis Config (advanced_vis_config). Use when authoring, customizing, or debugging advanced Highcharts option overrides in LookML dashboards, including axes, plotOptions, tooltips, titles, series styles, declarative formatters, and dynamic annotations.
---

# Looker Highcharts Advanced Vis Config (`looker-vis-advanced-config`)

Looker's native visualization engine renders Cartesian and several specialty chart types using **Highcharts**. The `advanced_vis_config` parameter allows developers to inject raw Highcharts options directly into the chart configuration from LookML dashboards.

This skill documents the rules, validation constraints, native Looker extensions, and Highcharts API options available through `advanced_vis_config`.

---

## 1. Architectural Pipeline & Merge Precedence

When a tile renders in Looker, configurations are evaluated and merged in this exact order:

```
1. Highcharts Core Defaults (Base styles, fonts, margins)
                     ↓
2. Looker Theme & Admin Defaults (Modern 2026 tokens, company palette)
                     ↓
3. Looker Native Vis Config (LookML parameters: series_colors, stacking, x_axis_gridlines, etc.)
                     ↓
4. advanced_vis_config Overrides (Deep-merged on top of the generated Highcharts config)
```

> [!IMPORTANT]
> `advanced_vis_config` overrides almost all visual options. However, if a conflicting native LookML property exists (such as `series_colors`), Looker's UI editor or adapter may re-assert its colors unless overridden specifically in `plotOptions.series` or `series[i].color` within `advanced_vis_config`.

---

## 2. Supported vs. Unsupported Chart Types

Looker enforces an explicit allowlist of visualizations that support `advanced_vis_config`:

### Supported Chart Types (14 types)
- `looker_column`
- `looker_bar`
- `looker_line`
- `looker_area`
- `looker_scatter`
- `looker_pie`
- `looker_funnel`
- `looker_timeline`
- `looker_waterfall`
- `looker_boxplot`
- `looker_wordcloud`
- `looker_histogram`
- `looker_bullet` / `bullet`
- `looker_sankey` / `sankey`

### Unsupported Chart Types
- ❌ `looker_grid` / `table` (AG Grid / custom DOM table engine)
- ❌ `single_value` (React KPI card component)
- ❌ `looker_single_record` (React record inspector)
- ❌ `looker_google_map` / `looker_geo_choropleth` (Google Maps API / Leaflet / TopoJSON engine)

---

## 3. Strict Syntax & Safety Guardrails

### Guardrail 1: Strictly Valid JSON (NO JavaScript Functions)
Looker serializes `advanced_vis_config` into a JSON string. **JavaScript code, function callbacks, and arrow functions are strictly prohibited** to prevent Cross-Site Scripting (XSS):
- ❌ **INVALID**: `formatter: function() { return this.y + "%"; }` (Will fail to parse or be rejected by schema validation)
- ✅ **VALID**: Use Highcharts string templates like `format: "{value}%"` or `format: "${point.y:,.2f}"`.
- ✅ **VALID**: Use Looker's declarative conditional `formatters` array.

### Guardrail 2: `formatters` vs `formatter` Spelling Error
Looker enforces an explicit JSON schema validation rule against invalid formatter spelling:
- If you accidentally include a key named `formatter` anywhere in your JSON, Looker's validator rejects the config.
- Always use Highcharts `format` strings for simple templates (e.g. `labels: { format: "{value}%" }`), or Looker's `formatters` array for conditional styling.

### Guardrail 3: LookML Escaping Syntax
In LookML dashboard YAML files, `advanced_vis_config` can be written as either:
1. **Single-quoted JSON string**:
   ```yaml
   advanced_vis_config: '{"legend": {"enabled": false}, "yAxis": [{"title": {"text": "Custom Revenue"}}]}'
   ```
2. **YAML Block Scalar (`|`)**:
   ```yaml
   advanced_vis_config: |
     {
       "chart": {
         "backgroundColor": "transparent"
       },
       "plotOptions": {
         "column": {
           "borderRadius": 6,
           "pointWidth": 28
         }
       }
     }
   ```

---

## 4. Native Looker Extensions: Declarative `formatters`

Looker built a powerful declarative conditional formatting engine for Highcharts series that does not require JavaScript functions.

You attach a `formatters` array to any series definition:

```json
{
  "series": [
    {
      "formatters": [
        {
          "select": "value > 1000",
          "style": {
            "color": "#34A853"
          }
        },
        {
          "select": "min",
          "style": {
            "color": "#EA4335",
            "marker": { "symbol": "diamond", "radius": 7 }
          }
        }
      ]
    }
  ]
}
```

### Supported Selector Syntax (`select`)
- Extremes: `min`, `max`
- Value comparisons: `value > 100`, `value <= 50`, `value != 0`
- Field comparisons: `value > orders.average_sales`
- Statistical order / quantiles: `percent_rank > 0.8`, `percent_rank <= 0.25`
- String matching: `name = "California" OR name = "New York"`
- Logical grouping: Combined with `AND`, `OR`

---

## 5. Reference Guides

Explore the detailed references for comprehensive catalogs and copy-paste recipes:

1. [**Highcharts API Reference**](references/highcharts_api_reference.md): Exhaustive breakdown of supported Highcharts options (`chart`, `title`, `xAxis`, `yAxis`, `plotOptions`, `series`, `legend`, `tooltip`, `colors`).
2. [**Looker Extensions & Formatters Reference**](references/looker_extensions_reference.md): Syntax specification for declarative `formatters`, dynamic annotations, and schema validation error codes.
3. [**Recipes & LookML Examples**](references/recipes_and_lookml_examples.md): 10+ battle-tested LookML dashboard recipes (dual axes, SLA plotBands, dashed target lines, rounded column caps, threshold alerts).
