# Looker Visualization Skills Suite

A specialized suite of agent skills designed to guide Large Language Models (LLMs) and Looker developers in selecting, configuring, and authoring **all Looker visualization options** in LookML dashboards (`.dashboard.lookml`), including deep-dive support for Looker's Highcharts **Advanced Vis Config** (`advanced_vis_config`).

---

## Skills Architecture

The suite is organized into 4 modular domain skills and reference guides located under [`looker-visualizations/`](.):

```
looker-visualizations/
├── README.md                                  # Top-level index, decision tree & query matrix
├── SKILL.md                                   # Central hub skill definition
├── looker-vis-cartesian/                      # Column, Bar, Line, Area, Scatter, Waterfall, Boxplot, Histogram
│   ├── SKILL.md                               # Cartesians overview, query shapes & parameter cheatsheet
│   └── references/
│       ├── parameters_reference.md            # Exhaustive LookML parameter dictionary
│       └── examples_lookml.md                 # Production LookML dashboard YAML element templates
├── looker-vis-tabular-kpi/                    # Modern Grid, Legacy Table, Single Value (KPI), Single Record
│   ├── SKILL.md                               # Tabular & KPI overview, query shapes & cheatsheet
│   └── references/
│       ├── parameters_reference.md            # Exhaustive LookML parameter dictionary
│       └── examples_lookml.md                 # Production LookML dashboard YAML element templates
├── looker-vis-specialty-maps/                 # Pie, Funnel, Timeline, Sankey, Treemap, Bullet, Maps, etc.
│   ├── SKILL.md                               # Specialty & Maps overview, query shapes & cheatsheet
│   └── references/
│       ├── parameters_reference.md            # Exhaustive LookML parameter dictionary
│       └── examples_lookml.md                 # Production LookML dashboard YAML element templates
└── looker-vis-advanced-config/                # Highcharts Advanced Vis Config (advanced_vis_config)
    ├── SKILL.md                               # Architecture, JSON rules, formatters engine & validation
    └── references/
        ├── highcharts_api_reference.md        # Comprehensive Highcharts API option overrides catalog
        ├── looker_extensions_reference.md     # Looker declarative formatters & dynamic annotations
        └── recipes_and_lookml_examples.md     # 10+ real-world LookML recipes with advanced_vis_config
```

---

## Visualization Decision Flowchart

When given a data intent or user query, follow this decision tree to identify the ideal visualization category:

```mermaid
graph TD
    Start["What is the primary analytical goal?"] --> Q1{"Comparison, Composition, Distribution, Relationship, or Raw Data?"}

    Q1 -->|Single Headline KPI| SV["Single Value / KPI Tile<br/>(looker-vis-tabular-kpi)"]
    Q1 -->|Detailed Rows / Audit| TAB["Table / Looker Grid<br/>(looker-vis-tabular-kpi)"]
    Q1 -->|Single Entity Inspection| SR["Single Record<br/>(looker-vis-tabular-kpi)"]

    Q1 -->|Trend over Time| TIME{"Continuous time dimension?"}
    TIME -->|Yes, 1-3 measures| LINE["Line Chart (looker_line)<br/>(looker-vis-cartesian)"]
    TIME -->|Cumulative / Volume| AREA["Area Chart (looker_area)<br/>(looker-vis-cartesian)"]
    TIME -->|Discrete stages / durations| TLINE["Timeline (looker_timeline)<br/>(looker-vis-specialty-maps)"]

    Q1 -->|Category Comparison| CAT{"Dimension type & series size?"}
    CAT -->|Few items <= 15, short labels| COL["Column Chart (looker_column)<br/>(looker-vis-cartesian)"]
    CAT -->|Many items > 15 or long names| BAR["Bar Chart (looker_bar)<br/>(looker-vis-cartesian)"]
    CAT -->|Actual vs Target Goal| BULLET["Bullet Chart (looker_bullet)<br/>(looker-vis-specialty-maps)"]

    Q1 -->|Composition / Part-to-Whole| COMP{"Pivoted or hierarchical?"}
    COMP -->|Simple share, <= 7 items, no pivots| PIE["Pie / Donut (looker_pie)<br/>(looker-vis-specialty-maps)"]
    COMP -->|Hierarchical tree| TREE["Treemap / Sunburst<br/>(looker-vis-specialty-maps)"]
    COMP -->|Cumulative variance steps| WF["Waterfall (looker_waterfall)<br/>(looker-vis-cartesian)"]
    COMP -->|Stacked categories| STACK["Stacked Column / Bar<br/>(looker-vis-cartesian)"]

    Q1 -->|Flow & Multi-step Conversion| FLOW{"Process or Funnel?"}
    FLOW -->|Step-by-step conversion| FUNNEL["Funnel (looker_funnel)<br/>(looker-vis-specialty-maps)"]
    FLOW -->|Source-to-target paths| SANKEY["Sankey (looker_sankey)<br/>(looker-vis-specialty-maps)"]

    Q1 -->|Distribution & Outliers| DIST{"Statistical format?"}
    DIST -->|Continuous frequency / bins| HIST["Histogram (looker_histogram)<br/>(looker-vis-cartesian)"]
    DIST -->|Quartiles, median & outliers| BOX["Boxplot (looker_boxplot)<br/>(looker-vis-cartesian)"]
    DIST -->|Two continuous metrics (correlation)| SCAT["Scatterplot (looker_scatter)<br/>(looker-vis-cartesian)"]

    Q1 -->|Geographic / Location| GEO{"Location format?"}
    GEO -->|Lat / Long or Zipcode points| GMAP["Google Map (looker_google_map)<br/>(looker-vis-specialty-maps)"]
    GEO -->|State / Country boundaries| CHORO["Geo Choropleth (looker_geo_choropleth)<br/>(looker-vis-specialty-maps)"]
```

---

## Query Shape to Visualization Mapping Matrix

Looker matches and validates query structure before rendering a tile. Use this matrix to select the visualization that matches your query fields:

| Visualization Type | LookML `type` | Dimensions | Measures | Pivots | Maximum Recommended Rows | Supported by `advanced_vis_config`? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Table / Grid** | `looker_grid` / `table` | 0 - 50 | 0 - 50 | 0 - 3 | 5,000 (Grid) / 500 (Legacy) | ❌ No |
| **Single Value / KPI** | `single_value` | 0 - 1 | 1 - 2 | 0 - 1 | 1 - 2 (or comparison row) | ❌ No |
| **Single Record** | `looker_single_record` | 1 - 50 | 0 - 50 | 0 | 1 row | ❌ No |
| **Column Chart** | `looker_column` | 1 (categorical/time) | 1 - 10 | 0 - 1 | 500 | ✅ Yes |
| **Bar Chart** | `looker_bar` | 1 (categorical) | 1 - 10 | 0 - 1 | 500 | ✅ Yes |
| **Line Chart** | `looker_line` | 1 (timeframe/numeric) | 1 - 10 | 0 - 1 | 5,000 | ✅ Yes |
| **Area Chart** | `looker_area` | 1 (timeframe) | 1 - 10 | 0 - 1 | 500 | ✅ Yes |
| **Scatterplot** | `looker_scatter` | 0 - 2 | 1 - 2 (or 2 metrics) | 0 - 1 | 5,000 | ✅ Yes |
| **Waterfall** | `looker_waterfall` | 1 | 1 - 2 | 0 | 50 | ✅ Yes |
| **Boxplot** | `looker_boxplot` | 1 | 1 (or 5 percentile measures) | 0 | 100 | ✅ Yes |
| **Histogram** | `looker_histogram` | 0 - 1 | 1 (continuous) | 0 - 1 | 5,000 | ✅ Yes |
| **Pie / Donut** | `looker_pie` | 1 | 1 | 0 (strictly prohibited) | 50 (strictly <= 50) | ✅ Yes |
| **Funnel** | `looker_funnel` | 1 | 1 | 0 | 20 | ✅ Yes |
| **Timeline** | `looker_timeline` | 1 - 2 | 2 dates or 1 date + duration | 0 | 200 | ✅ Yes |
| **Sankey** | `looker_sankey` | 2 (Source, Target) | 1 (Weight) | 0 | 200 | ✅ Yes |
| **Treemap** | `treemap` | 1 - 2 (Hierarchy) | 1 (Size) | 0 | 100 | ❌ No (custom renderer) |
| **Sunburst** | `sunburst` | 2 - 4 (Hierarchy) | 1 (Size) | 0 | 100 | ❌ No |
| **Word Cloud** | `looker_wordcloud` | 1 (Text tokens) | 1 (Count/Weight) | 0 | 100 | ✅ Yes |
| **Bullet Chart** | `looker_bullet` | 1 | 2 - 3 (Target, Actual, Good) | 0 | 50 | ✅ Yes |
| **Google Map** | `looker_google_map` | 1 (`location`/`zipcode`) | 1 - 2 | 0 | 5,000 | ❌ No |
| **Geo Choropleth** | `looker_geo_choropleth` | 1 (`state`/`country`) | 1 | 0 | 500 | ❌ No |
| **Geo Coordinates** | `looker_geo_coordinates` | 1 (`location`) | 1 | 0 | 5,000 | ❌ No |

---

## Domain Skills Index & Quick Links

1. [**Cartesian Visualizations (`looker-vis-cartesian`)**](looker-vis-cartesian/SKILL.md)
   - Deep-dive into Column, Bar, Line, Area, Scatter, Waterfall, Boxplot, and Histogram.
   - [Parameters Reference](looker-vis-cartesian/references/parameters_reference.md)
   - [LookML Examples](looker-vis-cartesian/references/examples_lookml.md)

2. [**Tabular & Single Value/KPI Visualizations (`looker-vis-tabular-kpi`)**](looker-vis-tabular-kpi/SKILL.md)
   - Deep-dive into modern Looker Grid, Legacy Table, Single Value / KPI cards, and Single Record.
   - [Parameters Reference](looker-vis-tabular-kpi/references/parameters_reference.md)
   - [LookML Examples](looker-vis-tabular-kpi/references/examples_lookml.md)

3. [**Specialty & Geospatial Visualizations (`looker-vis-specialty-maps`)**](looker-vis-specialty-maps/SKILL.md)
   - Deep-dive into Pie/Donut, Funnels, Timelines, Sankey, Wordcloud, Google Maps, and Choropleth.
   - [Parameters Reference](looker-vis-specialty-maps/references/parameters_reference.md)
   - [LookML Examples](looker-vis-specialty-maps/references/examples_lookml.md)

4. [**Highcharts Advanced Vis Config (`looker-vis-advanced-config`)**](looker-vis-advanced-config/SKILL.md)
   - Deep-dive into Looker's Highcharts override engine (`advanced_vis_config`), JSON constraints, declarative `formatters`, dynamic annotations, and production recipes.
   - [Highcharts API Reference](looker-vis-advanced-config/references/highcharts_api_reference.md)
   - [Looker Extensions & Formatters Engine](looker-vis-advanced-config/references/looker_extensions_reference.md)
   - [Production Recipes & LookML Templates](looker-vis-advanced-config/references/recipes_and_lookml_examples.md)
