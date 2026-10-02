# Executive LookML Dashboard Polish & Highcharts Safety Standards

This shared resource defines the **4 Mandatory Default Polish Rules**, YAML safety rules, LookML dashboard code templates, and the **9-Point Executive UI Quality Checklist** shared across [`looker-demo-orchestrator`](../looker-demo-orchestrator/SKILL.md), [`lookml-dashboard-designer`](../looker-demo-orchestrator/subagents/lookml-dashboard-designer.md), and [`looker-visualizations`](../looker-visualizations/SKILL.md).

---

## 1. Why Dashboard Polish Must Never Be Skipped Between Gate 2 and Gate 3

1. **CLI Scaffolding Is a Raw Draft**: `demo-create lookml model` generates functional baseline `.dashboard.lookml` scaffolding — never treat it as a finished product.
2. **`HTTP 200 OK` Query Validation Does Not Check Highcharts**: Looker's `validate_project` and `run_inline_query` only validate LookML/SQL syntax. For example, `series_types: { ...: looker_column }` passes SQL validation with `HTTP 200 OK` yet **crashes Highcharts in the browser** because Highcharts expects bare `'column'`, `'line'`, `'area'`, `'bar'`, or `'scatter'` inside `series_types`.
3. **YAML Quoting Safety**: Unquoted colons followed by spaces (`title: Daily Spend: Cost vs Tokens`) break PyYAML parsing. **Always wrap `title`, `name`, `tab_name`, `label`, `title_text`, and `subtitle_text` in double quotes**, and never use periods (`.`) in tile `name:` attributes.
4. **Root Cross-Filtering Attribute**: Always use `crossfilter_enabled: true` at the dashboard root level. Never use deprecated `crossfilter: true`.

---

## 2. The 4 Mandatory Default Upgrades for Every LookML Dashboard

1. **Audit Chart Types & `series_types` Against Highcharts Specs ([`looker-vis-cartesian`](../looker-visualizations/looker-vis-cartesian/SKILL.md))**:
   - Element root `type:` uses Looker wrappers (`looker_column`, `looker_bar`, `looker_line`, `looker_area`, `looker_pie`, `looker_grid`, `single_value`).
   - Inside `series_types:` (for mixed/combo Cartesian charts), **ALWAYS use bare Highcharts series names** (`column`, `bar`, `line`, `area`, `scatter`) — **NEVER** `looker_column`, `looker_line`, or `looker_area`.
2. **Inject Modern Geometry Tokens via `advanced_vis_config` ([`looker-vis-advanced-config`](../looker-visualizations/looker-vis-advanced-config/SKILL.md))**:
   - Apply rounded bar corners (`"plotOptions": {"series": {"borderRadius": 4}}`), rounded container & transparent chart surfaces (`"chart": {"backgroundColor": "transparent", "borderRadius": 8}`), shadow tooltips (`"tooltip": {"borderRadius": 8, "shadow": true}`), and centered legends (`"legend": {"align": "center", "verticalAlign": "bottom"}`).
   - Never use JavaScript function callbacks (`formatter: function()`) or attach `advanced_vis_config` to `single_value` or `looker_grid` tiles.
3. **Convert Default Pie Charts to Donuts with Curated Palettes ([`looker-vis-specialty-maps`](../looker-visualizations/looker-vis-specialty-maps/SKILL.md))**:
   - Configure all `looker_pie` tiles with `show_donut: true`, `inner_radius: 50`, `legend_position: center`, `value_labels: legend`, `label_type: labPer`, and curated `series_colors:` / Highcharts `"colors"` palettes grounded in `SELECT DISTINCT` data literals.
4. **Upgrade Tables to `transparent` Theme with In-Cell Data Bars ([`looker-vis-tabular-kpi`](../looker-visualizations/looker-vis-tabular-kpi/SKILL.md))**:
   - Configure all `looker_grid` tiles with `table_theme: transparent`, `show_view_names: false`, `show_row_numbers: true`, `truncate_text: true`, `size_to_fit: true`, and `series_cell_visualizations` data bars on primary numeric measures.

---

## 3. Canonical LookML Dashboard Element Templates

### A. Theme-Inheriting Section Header (`type: text`, Zero Hardcoded HTML Gradients)
Never use HTML `<div>` banners with hardcoded background gradients or hex colors (`#FFFFFF`) that break light/dark Looker themes. Use native `type: text` tiles at `row: 0`:

```lookml
  - name: "executive_pulse_header"
    type: text
    title_text: "Executive Pulse — Real-Time Operations"
    subtitle_text: "Headline KPIs, dual-axis throughput trajectory, and category distribution"
    tab_name: "Executive Pulse"
    row: 0
    col: 0
    width: 24
    height: 2
```

### B. Dual-Axis Combo Chart with Bare Highcharts `series_types` & `advanced_vis_config`
```lookml
  - name: "revenue_and_volume_trend"
    title: "Monthly Revenue vs. Order Volume"
    model: ecommerce
    explore: fct_orders
    type: looker_line
    fields: [fct_orders.order_month, fct_orders.total_sale_price, fct_orders.count]
    y_axis_combined: false
    y_axis_unpinned: true
    legend_position: center
    series_types:
      fct_orders.total_sale_price: area
      fct_orders.count: column
    advanced_vis_config: |
      {
        "chart": { "backgroundColor": "transparent", "borderRadius": 8 },
        "plotOptions": { "series": { "borderRadius": 4 } },
        "tooltip": { "borderRadius": 8, "shadow": true },
        "legend": { "align": "center", "verticalAlign": "bottom" },
        "yAxis": [
          {
            "title": { "text": "Revenue (USD)" },
            "labels": { "format": "${value:,.0f}" }
          },
          {
            "title": { "text": "Order Volume" },
            "opposite": true,
            "labels": { "format": "{value:,.0f}" }
          }
        ]
      }
```

---

## 4. 9-Point Executive UI Quality Checklist (Pass 3 Pre-Push Audit)

- [x] `crossfilter_enabled: true` at root (no deprecated `crossfilter: true`).
- [x] All `series_types:` entries use bare Highcharts names (`column`, `line`, `area`, `bar`, `scatter`) — zero `looker_*` names inside `series_types`.
- [x] All titles/labels double-quoted; 0 periods in tile `name:` attributes.
- [x] Theme-inheriting `type: text` headers at `row: 0` on every tab (0 hardcoded HTML color banners).
- [x] `legend_position: center` and modern `advanced_vis_config` tokens (`borderRadius`, `"backgroundColor": "transparent"`, `tooltip`) on all cartesian and pie charts.
- [x] Default pie charts converted to donuts (`show_donut: true`, `inner_radius: 50`) with curated palettes.
- [x] Independent dual-axis value ranges (`y_axis_combined: false`, `y_axis_unpinned: true`) with explicit numeric label `format` strings.
- [x] `table_theme: transparent` and `series_cell_visualizations` on all `looker_grid` tiles.
- [x] No `advanced_vis_config` on `single_value` or `looker_grid` tiles.
