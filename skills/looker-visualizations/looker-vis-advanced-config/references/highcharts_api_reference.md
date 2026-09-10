# Highcharts API Options Reference for Looker (`advanced_vis_config`)

This document details the standard Highcharts configuration options supported and commonly overridden via Looker's `advanced_vis_config`.

> [!IMPORTANT]
> All options must be written in standard JSON format. Never pass JavaScript functions or callbacks. Use Highcharts string format templates (e.g. `{value}` or `{point.y:,.2f}`) instead of function formatters.

---

## 1. `chart` Options

Controls top-level canvas dimensions, layout mode, and canvas styling.

| Property | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `type` | string | `"column"`, `"line"`, `"spline"`, `"areaspline"`, `"scatter"`, `"pie"` | Overrides the base chart rendering type across all series. |
| `backgroundColor` | string | `"transparent"`, `"#FFFFFF"`, `"#F8F9FA"` | Canvas background color. Set to `"transparent"` to match dashboard card themes. |
| `inverted` | boolean | `true`, `false` | Inverts horizontal and vertical axes (turns columns into bars or vice-versa). |
| `polar` | boolean | `true`, `false` | Transforms cartesian coordinates into radial / radar polar coordinates. |
| `spacing` | array of 4 numbers | `[10, 10, 15, 10]` | Outer canvas padding `[top, right, bottom, left]` in pixels. |
| `spacingTop` / `spacingBottom` | number | `15` | Individual outer edge margin spacing. |
| `margin` | array of 4 numbers | `[40, 20, 50, 40]` | Explicit plot area margin `[top, right, bottom, left]` inside the canvas. |

```json
{
  "chart": {
    "backgroundColor": "transparent",
    "spacingTop": 20,
    "spacingBottom": 15
  }
}
```

---

## 2. `title` & `subtitle` Options

Overrides Looker's default title placement and styling inside the visualization canvas.

| Property | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `text` | string | `"Global Q3 Performance"`, `null` | Title text string. Set to `null` or `""` to remove canvas title. |
| `align` | string | `"left"`, `"center"`, `"right"` | Horizontal alignment. |
| `verticalAlign` | string | `"top"`, `"bottom"` | Vertical placement on the canvas. |
| `useHTML` | boolean | `true`, `false` | Enables rich HTML formatting (`<span>`, `<b>`, `<style>`) in the title text. |
| `style` | object | `{"color": "#202124", "fontSize": "16px", "fontWeight": "600"}` | CSS style properties for the text. |
| `margin` | number | `15` | Margin in pixels between title and plot area. |

```json
{
  "title": {
    "text": "<span style='color: #1A73E8;'>●</span> Actual vs <span style='color: #EA4335;'>●</span> Target",
    "useHTML": true,
    "align": "left",
    "style": {
      "fontSize": "14px",
      "fontWeight": "bold"
    }
  }
}
```

---

## 3. `xAxis` & `yAxis` Options

Configures axis scales, tick intervals, labels, grid lines, and reference bands. For multiple axes, provide an array of axis objects.

### Core Axis Properties
| Property | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `title` | object | `{"text": "Revenue (USD)", "style": {"color": "#5F6368"}}` | Axis title text and typography. Set `"text": null` to hide title. |
| `labels` | object | See below | Formatting, rotation, and typography of scale values. |
| `min` / `max` | number | `min: 0`, `max: 1000000` | Pins explicit minimum and maximum scale bounds. |
| `tickInterval` | number | `1000`, `25` | Explicit numerical interval between ticks on the axis. |
| `type` | string | `"linear"`, `"logarithmic"`, `"datetime"`, `"category"` | Scale type. Set to `"logarithmic"` for high-variance power-law distributions. |
| `opposite` | boolean | `true`, `false` | Positions the axis on the opposite side (e.g. Right for Y-axis, Top for X-axis). |
| `reversed` | boolean | `true`, `false` | Inverts axis direction. |
| `gridLineWidth` | number | `0`, `1`, `2` | Width of gridlines across the plot area. Set to `0` to remove gridlines. |
| `gridLineColor` | string | `"#E0E0E0"`, `"transparent"` | Color of axis gridlines. |
| `gridLineDashStyle` | string | `"Solid"`, `"Dash"`, `"Dot"`, `"ShortDash"` | Stroke pattern for gridlines. |
| `lineColor` / `lineWidth` | string / number | `"#BDC1C6"`, `1` | Appearance of the baseline axis rule. |

### Axis `labels` Format
Use Highcharts replacement variables in string formats:
- `{value}`: The raw numeric or string tick value.
- `{value:,.0f}`: Formatted with thousands commas and 0 decimals (e.g. `1,250,000`).
- `{value:,.2f}%`: Number formatted with 2 decimal places and percent symbol.

```json
{
  "yAxis": {
    "title": { "text": "Net Margin" },
    "labels": {
      "format": "{value}%",
      "style": { "color": "#5F6368", "fontSize": "11px" }
    },
    "gridLineDashStyle": "ShortDash",
    "gridLineColor": "#EEEEEE"
  }
}
```

### Reference Bands & Lines (`plotLines`, `plotBands`)

Highlight target zones, thresholds, or service level agreements (SLAs):

```json
{
  "yAxis": {
    "plotLines": [
      {
        "value": 75000,
        "color": "#EA4335",
        "dashStyle": "Dash",
        "width": 2,
        "zIndex": 5,
        "label": {
          "text": "Quota Target ($75k)",
          "align": "right",
          "style": { "color": "#EA4335", "fontWeight": "bold" }
        }
      }
    ],
    "plotBands": [
      {
        "from": 0,
        "to": 50000,
        "color": "rgba(234, 67, 53, 0.08)",
        "label": { "text": "Underperforming Zone" }
      },
      {
        "from": 50000,
        "to": 100000,
        "color": "rgba(52, 168, 83, 0.08)",
        "label": { "text": "Target Range" }
      }
    ]
  }
}
```

---

## 4. `plotOptions`

Sets default rendering options for specific chart types (`column`, `bar`, `line`, `area`, `pie`, `scatter`) or across all series (`series`).

### Column & Bar (`plotOptions.column`, `plotOptions.bar`)
| Property | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `pointWidth` | number | `24`, `36` | Explicit pixel width of individual columns/bars. |
| `maxPointWidth` | number | `40` | Upper limit for column width when dataset has few items. |
| `pointPadding` | number | `0.1` | Space between bars within the same category group (`0.0` to `0.5`). |
| `groupPadding` | number | `0.15` | Space between distinct category groups (`0.0` to `0.5`). |
| `borderRadius` | number | `4`, `8` | Corner rounding radius for column caps. |
| `stacking` | string | `"normal"`, `"percent"`, `null` | Column stacking behavior. |

```json
{
  "plotOptions": {
    "column": {
      "pointWidth": 28,
      "borderRadius": 6,
      "groupPadding": 0.1
    }
  }
}
```

### Line & Area (`plotOptions.line`, `plotOptions.area`, `plotOptions.spline`)
| Property | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `lineWidth` | number | `2.5`, `3` | Stroke thickness in pixels. |
| `dashStyle` | string | `"Solid"`, `"Dash"`, `"Dot"`, `"ShortDash"`, `"LongDash"` | Line dash style. |
| `step` | string / boolean | `"left"`, `"center"`, `"right"`, `true` | Step-line rendering for stair-step progression. |
| `fillOpacity` | number | `0.25` | Transparency of area fill below the line (`0.0` to `1.0`). |
| `marker` | object | See below | Data point symbol behavior and sizing. |

```json
{
  "plotOptions": {
    "series": {
      "marker": {
        "enabled": true,
        "radius": 4,
        "symbol": "circle"
      }
    }
  }
}
```

### Pie & Donut (`plotOptions.pie`)
| Property | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `innerSize` | string / number | `"60%"`, `120` | Radius of center cutout. Set to `"50%"` or `"60%"` to create a donut. |
| `startAngle` | number | `-90`, `0` | Angle where the first slice begins. Set to `-90` and `endAngle: 90` for semi-circle gauge. |
| `endAngle` | number | `90`, `360` | Ending angle for pie rendering. |
| `size` | string / number | `"80%"` | Outer diameter of the pie chart relative to plot area. |

---

## 5. `series` Array Overrides

Allows targeting a specific series index (`series[0]`, `series[1]`, etc.) to assign distinct types, secondary axes, colors, or dash styles:

| Property | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `type` | string | `"spline"`, `"line"`, `"column"` | Series-specific chart type. |
| `yAxis` | number | `0`, `1` | 0-indexed reference linking series to a specific Y-axis in multi-axis layouts. |
| `color` | string | `"#4285F4"`, `"rgba(66,133,244,0.5)"` | Direct color override. |
| `dashStyle` | string | `"Dash"`, `"ShortDot"` | Custom stroke pattern (e.g. dashed benchmark or target line). |
| `zIndex` | number | `10` | Stacking order layer (higher values render on top). |
| `visible` | boolean | `true`, `false` | Initial visibility state in the chart. |

```json
{
  "series": [
    {
      "type": "column",
      "color": "#4285F4",
      "yAxis": 0
    },
    {
      "type": "spline",
      "color": "#EA4335",
      "dashStyle": "ShortDash",
      "lineWidth": 3,
      "yAxis": 1
    }
  ]
}
```

---

## 6. `tooltip` Options

Customizes hover tooltip behavior, formatting, and responsiveness:

| Property | Type | Default | Example | Description |
| :--- | :--- | :--- | :--- | :--- |
| `enabled` | boolean | `true` | `true`, `false` | Toggles tooltip display on hover. |
| `shared` | boolean | `false` | `true`, `false` | When `true`, shows a single shared tooltip consolidating all series at the hovered X coordinate. |
| `valuePrefix` | string | `""` | `"$"` | Prepends currency symbols or units before the number. |
| `valueSuffix` | string | `""` | `" units"`, `" %"` | Appends suffix text after the number. |
| `valueDecimals` | number | auto | `0`, `2` | Clamps decimal digits. |
| `useHTML` | boolean | `false` | `true` | Permits rich HTML formatting in header and point templates. |
| `backgroundColor` | string | Looker theme | `"#202124"` | Background fill color of the tooltip bubble. |
| `borderRadius` | number | `4` | `8` | Corner radius of the tooltip box. |

```json
{
  "tooltip": {
    "shared": true,
    "valuePrefix": "$",
    "valueDecimals": 2
  }
}
```

---

## 7. `legend` Options

Customizes legend positioning, alignment, and typography:

| Property | Type | Default | Valid Options | Description |
| :--- | :--- | :--- | :--- | :--- |
| `enabled` | boolean | `true` | `true`, `false` | Toggles legend display. |
| `layout` | string | `"horizontal"` | `"horizontal"`, `"vertical"` | Orientation of items in the legend. |
| `align` | string | `"center"` | `"left"`, `"center"`, `"right"` | Horizontal positioning relative to plot area. |
| `verticalAlign`| string | `"bottom"` | `"top"`, `"middle"`, `"bottom"` | Vertical positioning. |
| `itemStyle` | object | auto | `{"fontSize": "11px", "color": "#3C4043"}` | CSS style object for legend item labels. |
| `symbolRadius` | number | `4` | `0` (square), `4` (rounded), `8` (circle) | Corner radius for legend color swatches. |
