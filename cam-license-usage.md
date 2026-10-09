# CAM License Usage Chart — Rebuild Specification

Complete behavioral spec for `cam-license-usage.py`, detailed enough to
reimplement the script from scratch without reading the source.

## Purpose

Fetch an organization's daily peak concurrent NAC sessions from the Meraki
Dashboard API and render daily peak vs. the rolling 10-day 90th percentile
as either a self-contained interactive HTML page or a static SVG. Exactly
one artifact is written per run.

## Script setup

- `#!/usr/bin/env -S uv run` shebang with inline PEP 723 metadata:
  `requires-python = ">=3.11"`, deps `httpx>=0.27.0`, `python-dotenv>=1.0.0`,
  `click>=8.0.0`.
- Loads `.env` via `python-dotenv` at import time.
- `logging.getLogger("cam-license-usage")`, default level `WARNING`
  (raised to `INFO` by `-v/--verbose`), formatter
  `"%(asctime)s %(levelname)s %(message)s"` with `datefmt="%Y-%m-%dT%H:%M:%S"`.

## Constants

| Name | Value |
| --- | --- |
| `BASE_URL` | `https://api.meraki.com/api/v1` |
| `DEFAULT_DAYS` | `30` |
| `DEFAULT_OUTPUT_DIR` | `.` |
| `ARTIFACT_NAME` | `cam-license-usage` |
| `DEFAULT_FORMAT` | `html` |
| `DEFAULT_SVG_WIDTH` | `1920` |
| `DEFAULT_SVG_HEIGHT` | `1080` |
| `DEFAULT_THEME` | `light` |

## CLI (click)

| Flag | Short | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `--org` | | string | `MERAKI_ORG_ID` env var | Organization ID |
| `--days` | `-d` | int | `30` | Lookback window length |
| `--output` | `-o` | string | `.` | Output directory |
| `--format` | `-f` | choice `html`\|`svg` | `html` | Which artifact to write |
| `--width` | | int | `1920` | SVG width in px |
| `--height` | | int | `1080` | SVG height in px |
| `--theme` | `-t` | choice `light`\|`dark` | `light` | Color theme |
| `--licenses` | `-l` | int | none | Include a license count horizontal reference line |
| `--verbose` | `-v` | flag | false | Raise log level to INFO |

Exits with error (no API call) if `MERAKI_DASHBOARD_API_KEY` is unset or no
`--org`/`MERAKI_ORG_ID` is resolvable.

## Data flow (`run()`)

1. Resolve `MERAKI_DASHBOARD_API_KEY` from env; error+exit(1) if missing.
2. Resolve `org_id` from `--org` or `MERAKI_ORG_ID`; error+exit(1) if missing.
3. `build_date_range(days)` → `(startDate, endDate)` ISO strings, trailing
   `days` days inclusive of today.
4. Open one `httpx.AsyncClient` with `Authorization: Bearer <key>` and
   `Content-Type: application/json` headers, 30s timeout.
5. `fetch_license_usage(client, org_id, start_date, end_date)` →
   `GET /organizations/{orgId}/nac/license/usage?startDate=&endDate=`.
   On HTTP 429, sleep `Retry-After` (default 1s) and retry once, then
   `raise_for_status()`.
6. `fetch_organization(client, org_id)` → `GET /organizations/{orgId}`,
   used only for its `name` field (prefixed into the chart title/heading).
7. `parse_usage_data(payload)`: sort `payload["usageData"]` by `date`
   ascending, return `(dates, peakConcurrentSessions values)` as parallel
   lists.
8. `rolling_90th_percentile(peak, window=10)` (see below).
9. `os.makedirs(output_dir, exist_ok=True)`.
10. `build_output_path(output_dir, fmt)` → single path, filename
    `{YYYY-MM-DD}-cam-license-usage.{html|svg}` (today's date).
11. If `fmt == "html"`: call `generate_html(...)` and write it. Else call
    `generate_svg(width=, height=, ...)` and write it. **Only one file is
    ever written per run** — this is the key difference from the format
    sharing an output pair.

## Core calculation: `rolling_90th_percentile(values, window=10)`

- Nearest-rank 90th percentile over a trailing window: for each 1-indexed
  day `d` from `window` to `len(values)`, sort `values[d-window : d]` and
  take the value at `rank - 1` where `rank = round(0.9 * window)` (rank 9
  of 10 for the default window).
- Returns a `dict[int, int]` keyed by day number; days before the window
  is full (`d < window`) are omitted entirely — not zero-filled.
- `window` is re-derived elsewhere as `len(peak) - len(rolling) + 1` (or
  `10` if `rolling` is empty).

## `compute_nice_ticks(data_min, data_max, tick_count=5)`

Produces human-friendly axis tick values:
1. If `data_max <= data_min`, bump `data_max = data_min + 1`.
2. `rough_step = (data_max - data_min) / tick_count`.
3. `magnitude = 10 ** floor(log10(rough_step))`; normalize
   `rough_step / magnitude`.
4. Snap to nearest "nice" multiplier: `1` if `≤1`, else `2` if `≤2`, else
   `5` if `≤5`, else `10`. `step = nice * magnitude`.
5. `nice_min = floor(data_min / step) * step`; emit
   `nice_min, nice_min+step, ...` up through covering `data_max`, each
   floored at `0` (counts can't be negative).

Both renderers use the equivalent logic (`generate_svg` in Python via this
function; `generate_html`'s embedded JS reimplements the identical
algorithm as `niceTicks()`).

## Theming

Two themes, `light` (default) and `dark`, each a dict of named colors:

| Key | Light | Dark | Used for |
| --- | --- | --- | --- |
| `series_1` | `#02C8FF` | `#02C8FF` | Daily peak line/dots |
| `series_2` | `#FF9000` | `#FF9000` | Rolling 90th-pct line/dots |
| `surface` | `#ffffff` | `#0e2033` | Card/chart background |
| `page_plane` | `#f4f5f6` | `#07182D` | Page background |
| `text_primary` | `#07182D` | `#ffffff` | Headings |
| `text_secondary` | `#525E6C` | `#B4B9C0` | Body/legend text |
| `text_muted` | `#B4B9C0` | `#525E6C` | Axis labels |
| `gridline` | `#e4e6e9` | `#1c3350` | Horizontal gridlines |
| `baseline` | `#D6D6D6` | `#2a3f56` | X-axis baseline/ticks |
| `border` | `rgba(7,24,45,0.10)` | `rgba(255,255,255,0.10)` | Card border |
| `window_highlight` | `#D6D6D6` | `#D6D6D6` | 10-day window hover band |
| `first_window_marker` | `#9A9A9A` | `#9A9A9A` | First full-window dashed line (medium gray, both themes) |
| `license_line` | `#22C55E` | `#22C55E` | License-limit reference line/label (green, both themes) |

Colors are Cisco brand palette: navy `#07182D`, Accent 1 cyan `#02C8FF`,
Accent 4 orange `#FF9000`.

## `generate_html()` — interactive artifact

Signature: `generate_html(peak, rolling, theme="light", dates=None, licenses=None, org_name=None) -> str`.

### Title/heading
- `chart_title = "10-Day Rolling Window — Daily Peak Concurrent Sessions & 90th Percentile"`.
- If `org_name` given, prefix as `"{org_name} — {chart_title}"`. Used for
  both `<title>` and the on-page `<h1>`.

### Document structure
```
<html data-theme="light|dark">
  <head><style>...</style></head>
  <body>
    <div class="wrap">
      <div class="card viz-root" id="chartCard">
        <div class="theme-toggle" id="themeToggle">
          <button data-theme-choice="light">Light</button>
          <button data-theme-choice="dark">Dark</button>
        </div>
        <h1>{page_title}</h1>
        <p class="sub">{N}-day lookback. ... hover any day ...</p>
        <div class="legend">...</div>
        <svg id="chart" viewBox="0 0 880 400" preserveAspectRatio="xMidYMid meet"></svg>
        <div class="note">Rolling 90th percentile: ...</div>
      </div>
    </div>
    <div id="tooltip"></div>
    <script>...</script>
  </body>
</html>
```

### CSS layout
- `body`: `margin: 0em; padding: 0.2em; min-height: 100vh;` flex column,
  background `var(--page-plane)`, font `system-ui, -apple-system, "Segoe UI", sans-serif`.
- `.wrap`: flex column, no explicit width (avoids `width:100%` + margin
  overflow).
- `.card` (`#chartCard`): flex column, `padding: 0.5em`, `border-radius: 12px`,
  1px `var(--border)` border, background `var(--surface-1)`. No explicit
  width/height — sizes to content, not stretched via `flex: 1`.
- `h1`: `font-size: 19px`.
- `p.sub`: `font-size: 13px`, `max-width: 640px`.
- `.legend`: flex row, `gap: 20px`, `margin: 0 0 8px`, `padding: 0`,
  `font-size: 13px`.
- `.swatch`: `width: 14px`; **height set inline per legend item to match
  that series' actual stroke width** (not a fixed class height):
  - series-1 (daily peak) swatch: `height:2px`
  - series-2 (rolling p90) swatch: `height:3px`
  - license-line swatch: `height:2px`
- `svg`: `width: 100%; aspect-ratio: 880 / 400;` (NOT `flex:1` — a flex-grown
  SVG with a fixed viewBox letterboxes and creates large empty gaps above/below
  the chart; `aspect-ratio` makes the card hug the chart's actual content height).
- `.theme-toggle`: absolutely positioned `top: 20px; right: 20px`, pill
  button group, active button gets `background: var(--surface-1)`.

### Scale compensation (`--svg-scale`) — critical, non-obvious

The inline `<svg>` has a fixed `viewBox="0 0 880 400"` but renders at
whatever width the responsive `.card` ends up being. SVG stroke-width and
font-size are specified in **viewBox user units**, which the browser scales
by `renderedWidth / 880` (and `renderedHeight / 400`) when painting. Without
compensation, a "2px" line or "13px" label text would render thicker/larger
than literal 2px/13px once the card is wider than 880px — which it always is
in practice — and would not visually match sibling HTML elements (legend,
subtitle) that use literal CSS pixels.

Fix: every SVG-internal `font-size` and `stroke-width` is written as
`calc(Npx / var(--svg-scale))`, and JS computes/maintains `--svg-scale` as
`min(renderedWidth/880, renderedHeight/400)` on the root `<html>` element:

```js
function svgScale() {
  const rect = svg.getBoundingClientRect();
  return rect.width > 0 ? Math.min(rect.width / W, rect.height / H) : 1;
}
function syncSvgScale() {
  document.documentElement.style.setProperty('--svg-scale', svgScale() || 1);
}
syncSvgScale();
new ResizeObserver(syncSvgScale).observe(svg);
```

Default `--svg-scale: 1` is set per-theme block as a baseline before JS runs.
Every affected CSS class divides by this variable:
`.gridline`, `.baseline`, `.x-tick` (1px), `.first-window-line` /
`.license-line` (1.5–2px), `.license-label` / `.axis-label` (11px),
`.line` (2px), `.line-2` (3px), `.dot` (1.5px), `.callout` (12px),
`.callout-strong` / `.callout-plain` (13px), `.crosshair` (1px).

Daily-peak line (`.line`, 2px) and license-line (`.license-line`, 2px) are
intentionally the same thickness, matching their legend swatches (also 2px);
rolling-p90 line (`.line-2`, 3px) and its swatch are both 3px.

### SVG chart geometry (JS)
- `W = 880, H = 400`.
- `marginL = 54, marginR = 20, marginT = 16, marginB = 36`.
- `plotW = W - marginL - marginR`, `plotH = H - marginT - marginB`.
- `xScale(d) = marginL + ((d-1)/(days.length-1)) * plotW`.
- Y domain: `niceTicks(min(peak), licenses!=null ? max(max(peak), licenses) : max(peak), 5)`;
  `yMin/yMax` = first/last tick; `yScale(v) = marginT + plotH - ((v-yMin)/(yMax-yMin)) * plotH`.

### Draw order (all via a tiny `el(tag, attrs)` SVG-element helper)
1. Y gridlines + `.axis-label` tick text for each nice tick.
2. X-axis day ticks (`.x-tick`, every day) + date/`D{n}` labels at a
   stride of `max(1, floor(days.length/12))` (always labels the last day too).
3. `.baseline` horizontal line at the bottom of the plot.
4. **License line** (only if `licenses != null`): horizontal `.license-line`
   at `yScale(licenses)` spanning the full plot width, plus a `.license-label`
   text `"License limit: {n}"` positioned `x: marginL, y: ly+14, anchor: start`
   — i.e. left-aligned, directly under the line.
5. **10-day window highlight rect** (`windowRect`): defaults to spanning the
   last full rolling window (`[lastRollDay - window + 1, lastRollDay]`),
   `fill: var(--window-highlight)` at `opacity: 0.3`, with a two-line
   `.callout-plain` label ("Day N" / "{window}-day window") centered over it
   at `y: marginT + 16` (anchored to the box's top, not vertically centered).
   On hover (see Interactivity) this rect/label re-targets the hovered day's
   own window and `opacity` bumps to `0.5`.
6. **First-window marker**: dashed `.first-window-line` vertical line at the
   earliest day that has a full rolling window (`min` of rolling's keys).
7. Rolling p90 path (`.line.line-2`) + `.dot.dot-2` circles (r=4) at each
   rolling-data day, sorted ascending.
8. Daily-peak path (`.line.line-1`) + `.dot.dot-1` circles (r=4) at every day.
9. **Peak/p90 callouts** — see anti-overlap algorithm below.
10. Hidden `.crosshair` vertical line + transparent `.hit` rect covering the
    plot area, both used for hover.

### Peak/p90 label anti-overlap (generalized, pixel-space)

The rolling-p90 line's *last* data point is, by construction, always the
chart's final day — so a label anchored there sits at the right edge. If the
all-time peak also falls near the end, its label can collide with the p90
label both horizontally and vertically. The fix must hold for **any**
dataset length or canvas size, so it reasons in rendered pixel space, not
day-index differences:

```js
function callout(day, value, text, dy) {
  const nearRight = day >= days.length - Math.max(1, Math.round(days.length * 0.1));
  const nearLeft  = day <= 1 + Math.max(1, Math.round(days.length * 0.1));
  const anchor = nearRight ? 'end' : nearLeft ? 'start' : 'middle';
  const dx = anchor === 'end' ? -8 : anchor === 'start' ? 8 : 0;
  // draws <text class="callout-strong"> at xScale(day)+dx, yScale(value)+dy, anchor
}

const LABEL_HALF_WIDTH = 70;  // estimated label half-width, viewBox user units
const LABEL_LINE_GAP = 18;    // minimum safe vertical gap between baselines
let peakDy = -14, p90Dy = -14;
if (lastRollDay !== null) {
  const dxGap = Math.abs(xScale(peakDay) - xScale(lastRollDay));
  const dyGap = Math.abs((yScale(peakValue) + peakDy) - (yScale(rolling[lastRollDay]) + p90Dy));
  if (dxGap < LABEL_HALF_WIDTH * 2 && dyGap < LABEL_LINE_GAP) {
    peakDy -= LABEL_LINE_GAP;  // push the peak label further up
  }
}
```

- A label within 10% of either edge switches `text-anchor` to `end`/`start`
  (with an 8px nudge) instead of `middle`, so it never bleeds past the plot
  edge or sits centered on top of the edge-most point.
- Overlap is detected by comparing actual rendered x/y distances (in the
  fixed 880×400 user space, which is scale-independent) against an estimated
  label footprint, not by comparing day indices — this is what makes it
  correct for both a 10-day and a 365-day dataset.
- Peak is pushed further above its point (more negative `dy`) rather than
  p90, because peak is the dataset's global max, so its point is always at
  or above (smaller y pixel than) the p90 point — pushing it up never causes
  it to collide with its own line/dots.

### Interactivity (hover)
- `hit` rect listens for `mousemove`: converts mouse X → nearest `day` via
  inverse of `xScale`, accounting for the SVG's actual render scale/offset
  (`svgScale()`, same scale-compensation value as above).
- Shows a vertical dashed `.crosshair` at that day.
- Tooltip (`#tooltip`, absolutely positioned div, dark background,
  `white-space: nowrap`) shows: formatted date/day label, `Daily peak: N`,
  and either `Rolling 90th pct: N` (re-sizing/repositioning `windowRect` and
  its label to that day's own trailing window) or
  `Rolling 90th pct: n/a (needs {window} days)` with `windowRect` hidden
  (`opacity: 0`) for days before the first full window.
- `mouseleave` resets tooltip opacity to 0, crosshair hidden, and
  `windowRect`/label back to the default (most recent) window.

### Theme toggle
- Two buttons (`Light`/`Dark`) in a pill container; clicking sets
  `document.documentElement.setAttribute('data-theme', choice)`, toggles
  the `.active` class, and persists to `localStorage['cam-chart-theme']`.
- On load, if a saved choice exists in `localStorage`, it overrides the
  server-rendered default theme.
- Both themes' full CSS variable sets are always embedded (scoped to
  `html[data-theme="light"]` / `html[data-theme="dark"]`) so toggling is
  instant with no re-render.

### Date handling
- `dates` (ISO strings) are embedded only if their length matches `peak`;
  otherwise the chart falls back to generic `D1, D2, ...` labels
  (`hasDates` flag in JS).
- `formatDate(iso, opts)`: `new Date(iso + 'T00:00:00').toLocaleDateString('en-US', opts)`
  — appending `T00:00:00` avoids UTC-vs-local-timezone off-by-one-day bugs.
- Axis labels use `{month:'short', day:'numeric'}`; tooltip labels add
  `year:'numeric'`.

## `generate_svg()` — static artifact

Signature: `generate_svg(peak, rolling, width=1920, height=1080, theme="light", dates=None, licenses=None, org_name=None) -> str`.

No hover/JS — pure static markup, same data and `rolling_90th_percentile()`
output as the HTML version so the two never drift apart.

### Layout
- `margin_l, margin_r, margin_t, margin_b = 110, 60, 128, 90`.
  (`margin_t` is taller than you'd expect from the heading/subtitle alone —
  it reserves room for the legend row sitting between the subtitle and the
  plot's gridlines; see below.)
- `plot_w = width - margin_l - margin_r`, `plot_h = height - margin_t - margin_b`.
- `x_scale(d) = margin_l + ((d-1)/(len(days)-1)) * plot_w` (or just `margin_l`
  if only one day).
- Y domain via `compute_nice_ticks(min(peak), licenses is not None ? max(max(peak), licenses) : max(peak), 5)`.

### Draw order
1. `<svg>` root with explicit `width`/`height`/`viewBox="0 0 {width} {height}"`
   (1:1 — no responsive scaling, so no `--svg-scale` compensation needed here).
2. Background `<rect>` filled `surface`.
3. Heading at `(margin_l, 40)`, `font-size:30`, bold:
   `"CAM License Usage — Daily Peak Concurrent Sessions & Rolling 90th Percentile"`,
   prefixed `"{org_name} — "` if given.
4. Subtitle at `(margin_l, 68)`, `font-size:16`: `"{N}-day lookback. Each point ... plus the {window-1} days before it."`
5. **Legend, directly under the description** (`legend_y = 100`, a fixed
   y-offset — NOT `height - 36`/bottom-of-canvas as in earlier revisions):
   - Series-1 swatch: `<rect x=margin_l y=legend_y-10 width=30 height=3>` +
     label "Daily peak concurrent sessions" at `x=margin_l+40`.
   - Series-2 swatch: `<rect x=margin_l+400 ... height=5>` + label "Rolling
     {window}-day 90th percentile" at `x=margin_l+440`.
   - License swatch (only if `licenses` given): `<rect x=margin_l+820 ...
     height=3 fill=license_line_color>` + label `"License limit ({n})"` at
     `x=margin_l+860`.
   - All legend text `font-size:16`, `fill=text_secondary`.
6. Y gridlines (`stroke=gridline, stroke-width=1`) + right-aligned tick
   labels (`font-size:18, fill=text_muted`) for each nice tick.
7. X-axis day ticks for every day (`stroke=baseline, stroke-width=1`),
   date/`D{n}` labels at stride `max(1, len(days)//12)` (always includes the
   last day), `font-size:16, fill=text_muted`.
8. Baseline horizontal line.
9. **License line** (if given): horizontal line at `y_scale(licenses)`,
   `stroke=license_line_color, stroke-width=3` (same thickness as the daily
   peak line, matching its legend swatch), **solid** (no dash). Label
   `"License limit: {n}"` left-aligned at `x=margin_l, y=ly+24`, colored
   `license_line_color` (not the muted gray used for axis labels).
10. First-full-window dashed vertical line at the earliest rolling day,
    `stroke=first_window_marker` (`#9A9A9A`, medium gray — not dark gray),
    `stroke-width=2, stroke-dasharray="6 4"`.
11. Rolling p90 polyline (`stroke=series_2, stroke-width=5`) + circles (r=7,
    `stroke=surface, stroke-width=2`) at each rolling day.
12. Daily-peak polyline (`stroke=series_1, stroke-width=3`) + circles (r=6,
    same stroke style) at every day.
13. **Peak/p90 callouts** (`font-size:17, font-weight:600, fill=text_primary`):
    same edge-anchoring (10% edge band → `end`/`start` with ±10px `dx`) and
    the same generalized pixel-space anti-overlap check as the HTML version,
    but tuned for this renderer's larger absolute pixel scale:
    `label_half_width = 90`, `label_line_gap = 24`, base `dy = -20` for both;
    peak's `dy` is pushed by `-24` more if the pixel-space check trips.

No window-highlight band, no first-window two-line callout box, no
crosshair/tooltip — those are HTML-only interactive features.

## Output

- `build_output_path(output_dir, fmt, today=None)` →
  `{output_dir}/{YYYY-MM-DD}-cam-license-usage.{fmt}`, date defaults to
  `date.today()`.
- Exactly one file is written per invocation (`fmt` picks `html` or `svg`);
  there is no mode that writes both.

## Testing conventions (see `cam-license-usage_test.py`)

- Pure functions (`rolling_90th_percentile`, `compute_nice_ticks`,
  `build_date_range`, `build_output_path`, `parse_usage_data`) get direct
  unit tests against worked examples.
- `generate_html()`/`generate_svg()` are checked for structural/string
  markers (presence of specific CSS rules, class names, color hex values,
  computed `calc()` expressions, legend/line thickness pairings, anchor
  values) — not pixel-perfect rendering. Where feasible, prefer asserting
  on *why* a value is correct (e.g. "swatch height equals the stroke-width
  of its matching line class") over a bare literal.
- `fetch_license_usage`/`fetch_organization` are tested with mocked
  `httpx.AsyncClient` (including a 429-retry case).
- `run()` is tested with monkeypatched fetch functions + `tmp_path`,
  asserting that **only** the file matching `fmt` is written (the other
  must not exist).
- CLI wiring tests use `click.testing.CliRunner` + a monkeypatched `run()`
  to assert each flag/short-flag reaches `run()` with the right kwarg,
  including defaults and invalid-choice rejection.
