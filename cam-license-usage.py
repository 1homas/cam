#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27.0",
#     "python-dotenv>=1.0.0",
#     "click>=8.0.0",
# ]
# ///
"""
Generate the Cisco Access Manager license-usage chart from live usage data.

Queries the Meraki Dashboard nac/license/usage API for the organization's
daily peak concurrent sessions over a lookback window (30 days by default)
and renders daily peak concurrent sessions vs. the rolling 10-day 90th
percentile as either a self-contained interactive HTML page or a static SVG.
The artifact is written into --output, filename prefixed with the
generation date (e.g. 2026-10-09-cam-license-usage.html).

Usage:
    cam-license-usage.py
    cam-license-usage.py --days 90
    cam-license-usage.py --org 123456 --days 365
    cam-license-usage.py -f svg -o ~/Downloads --width 1920 --height 1080
"""

import asyncio
import logging
import math
import os
import sys
from datetime import date, timedelta
from typing import Optional

import click
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("cam-license-usage")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(handler)

BASE_URL = "https://api.meraki.com/api/v1"
DEFAULT_DAYS = 30
DEFAULT_OUTPUT_DIR = "."
ARTIFACT_NAME = "cam-license-usage"
DEFAULT_FORMAT = "html"
DEFAULT_SVG_WIDTH = 1920
DEFAULT_SVG_HEIGHT = 1080

THEMES = {
    # Cisco Brand Guidelines: navy #07182D, Accent 1 Cyan #02C8FF, Accent 4 Orange #FF9000
    "light": {
        "series_1": "#02C8FF",  # Accent 1 (Cyan) — daily peak
        "series_2": "#FF9000",  # Accent 4 (Orange) — rolling 10-day 90th percentile
        "surface": "#ffffff",
        "page_plane": "#f4f5f6",
        "text_primary": "#07182D",
        "text_secondary": "#525E6C",
        "text_muted": "#B4B9C0",
        "gridline": "#e4e6e9",
        "baseline": "#D6D6D6",  # Accent 5
        "border": "rgba(7,24,45,0.10)",
        "window_highlight": "#D6D6D6",  # Accent 5 — neutral grey hover window
        "first_window_marker": "#9A9A9A",  # medium gray, first full 90th-percentile window
        "license_line": "#22C55E",  # reference line for license count
    },
    "dark": {
        "series_1": "#02C8FF",
        "series_2": "#FF9000",
        "surface": "#0e2033",
        "page_plane": "#07182D",
        "text_primary": "#ffffff",
        "text_secondary": "#B4B9C0",
        "text_muted": "#525E6C",
        "gridline": "#1c3350",
        "baseline": "#2a3f56",
        "border": "rgba(255,255,255,0.10)",
        "window_highlight": "#D6D6D6",  # Accent 5 — neutral grey hover window
        "first_window_marker": "#9A9A9A",  # medium gray, first full 90th-percentile window
        "license_line": "#22C55E",  # reference line for license count
    },
}
DEFAULT_THEME = "light"

# Backward-compatible module-level light-theme constants (used as generate_svg() defaults)
SERIES_1 = THEMES["light"]["series_1"]
SERIES_2 = THEMES["light"]["series_2"]
SURFACE = THEMES["light"]["surface"]
TEXT_PRIMARY = THEMES["light"]["text_primary"]
TEXT_SECONDARY = THEMES["light"]["text_secondary"]
TEXT_MUTED = THEMES["light"]["text_muted"]
GRIDLINE = THEMES["light"]["gridline"]
BASELINE = THEMES["light"]["baseline"]


def build_date_range(days: int, today: Optional[str] = None) -> tuple[str, str]:
    """Return (startDate, endDate) ISO strings covering the trailing `days` days, inclusive of today."""
    end = date.fromisoformat(today) if today else date.today()
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def format_date_label(iso_date: str) -> str:
    """Format an ISO date string ("2026-09-10") as a short display label ("Sep 10")."""
    d = date.fromisoformat(iso_date)
    return f"{d.strftime('%b')} {d.day}"


def compute_nice_ticks(data_min: float, data_max: float, tick_count: int = 5) -> list[float]:
    """Return human-friendly, evenly-stepped tick values (1/2/5 x 10^n) spanning [data_min, data_max].

    Session counts can't be negative, so ticks are floored at 0.
    """
    if data_max <= data_min:
        data_max = data_min + 1
    rough_step = (data_max - data_min) / tick_count
    magnitude = 10 ** math.floor(math.log10(rough_step))
    normalized = rough_step / magnitude
    nice = 1 if normalized <= 1 else 2 if normalized <= 2 else 5 if normalized <= 5 else 10
    step = nice * magnitude
    nice_min = math.floor(data_min / step) * step
    steps_needed = math.ceil((data_max - nice_min) / step)
    return [max(0, nice_min + i * step) for i in range(steps_needed + 1)]


def build_output_path(output_dir: str, fmt: str, today: Optional[str] = None) -> str:
    """Return the output path in `output_dir` for `fmt` ("html" or "svg"), filename prefixed with the generation date."""
    stamp = today or date.today().isoformat()
    return os.path.join(output_dir, f"{stamp}-{ARTIFACT_NAME}.{fmt}")


async def fetch_license_usage(
    client: httpx.AsyncClient, org_id: str, start_date: str, end_date: Optional[str] = None
) -> dict:
    """GET nac/license/usage for the organization, retrying once on HTTP 429."""
    params = {"startDate": start_date}
    if end_date:
        params["endDate"] = end_date

    response = await client.get(f"{BASE_URL}/organizations/{org_id}/nac/license/usage", params=params)

    if response.status_code == 429:
        retry_after = float(response.headers.get("Retry-After", 1))
        logger.warning(f"Rate limited, sleeping {retry_after}s")
        await asyncio.sleep(retry_after)
        response = await client.get(f"{BASE_URL}/organizations/{org_id}/nac/license/usage", params=params)

    response.raise_for_status()
    return response.json()


async def fetch_organization(client: httpx.AsyncClient, org_id: str) -> dict:
    """GET the organization's details (used for its display name)."""
    response = await client.get(f"{BASE_URL}/organizations/{org_id}")
    response.raise_for_status()
    return response.json()


def parse_usage_data(payload: dict) -> tuple[list[str], list[int]]:
    """Extract (dates, peakConcurrentSessions) from a license/usage response, sorted by date ascending."""
    usage = sorted(payload.get("usageData", []), key=lambda d: d["date"])
    dates = [d["date"] for d in usage]
    values = [d["peakConcurrentSessions"] for d in usage]
    return dates, values


def rolling_90th_percentile(values: list[int], window: int = 10) -> dict[int, int]:
    """Rolling (current day + previous window-1 days) 90th percentile, nearest-rank method.

    Keys are 1-indexed day numbers; only days with a full window are included.
    """
    result: dict[int, int] = {}
    for day in range(window, len(values) + 1):
        ordered = sorted(values[day - window : day])
        rank = round(0.9 * window)  # nearest-rank: rank 9 of 10
        result[day] = ordered[rank - 1]
    return result


def generate_html(
    peak: list[int],
    rolling: dict[int, int],
    theme: str = DEFAULT_THEME,
    dates: Optional[list[str]] = None,
    licenses: Optional[int] = None,
    org_name: Optional[str] = None,
) -> str:
    """Build the self-contained interactive HTML chart in the given theme ("light" or "dark").

    `dates` are ISO date strings aligned 1:1 with `peak`, used to label the x-axis
    with real dates (e.g. "Sep 10"). Falls back to generic "D#" day labels when
    `dates` is omitted or its length doesn't match `peak`.

    `licenses`, when given, draws a horizontal reference line at that session count.

    `org_name`, when given, is prefixed to the page title and heading.
    """
    days = list(range(1, len(peak) + 1))
    window = len(peak) - len(rolling) + 1 if rolling else 10
    chart_title = "10-Day Rolling Window — Daily Peak Concurrent Sessions &amp; 90th Percentile"
    page_title = f"{org_name} — {chart_title}" if org_name else chart_title
    dates_js = dates if (dates is not None and len(dates) == len(peak)) else []
    licenses_js = licenses if licenses is not None else "null"
    license_line_js = (
        """
  if (licenses !== null) {
    const ly = yScale(licenses);
    svg.appendChild(el('line', { class: 'license-line', x1: marginL, x2: W - marginR, y1: ly, y2: ly }));
    const lt = el('text', { class: 'license-label', x: marginL, y: ly + 14, 'text-anchor': 'start' });
    lt.textContent = 'License limit: ' + licenses.toLocaleString();
    svg.appendChild(lt);
  }
"""
        if licenses is not None
        else ""
    )

    def theme_vars(name: str) -> str:
        t = THEMES[name]
        return f"""html[data-theme="{name}"] {{
    color-scheme: {name};
    --surface-1:      {t["surface"]};
    --page-plane:     {t["page_plane"]};
    --text-primary:   {t["text_primary"]};
    --text-secondary: {t["text_secondary"]};
    --text-muted:     {t["text_muted"]};
    --gridline:       {t["gridline"]};
    --baseline:       {t["baseline"]};
    --border:         {t["border"]};
    --series-1:       {t["series_1"]};
    --series-2:       {t["series_2"]};
    --window-highlight: {t["window_highlight"]};
    --first-window-marker: {t["first_window_marker"]};
    --license-line: {t["license_line"]};
    --svg-scale: 1;
  }}"""

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="{theme}">
<head>
<meta charset="UTF-8">
<title>{page_title}</title>
<style>
  {theme_vars("light")}
  {theme_vars("dark")}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0em;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    background: var(--page-plane);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    color: var(--text-primary);
    padding: 0.2em;
  }}
  .wrap {{ display: flex; flex-direction: column; }}
  .card {{
    display: flex;
    flex-direction: column;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 0.5em;
  }}
  h1 {{ font-size: 19px; margin: 0 0 4px; color: var(--text-primary); }}
  p.sub {{ font-size: 13px; color: var(--text-secondary); margin: 0 0 20px; max-width: 640px; }}
  .legend {{ display: flex; gap: 20px; flex-wrap: wrap; margin: 0 0 8px; padding: 0; font-size: 13px; color: var(--text-secondary); }}
  .legend-item {{ display: flex; align-items: center; gap: 7px; }}
  .swatch {{ width: 14px; border-radius: 2px; display: inline-block; }}
  svg {{ display: block; width: 100%; aspect-ratio: 880 / 400; overflow: visible; }}
  .gridline {{ stroke: var(--gridline); stroke-width: calc(1px / var(--svg-scale)); }}
  .baseline {{ stroke: var(--baseline); stroke-width: calc(1px / var(--svg-scale)); }}
  .x-tick {{ stroke: var(--baseline); stroke-width: calc(1px / var(--svg-scale)); }}
  .first-window-line {{ stroke: var(--first-window-marker); stroke-width: calc(1.5px / var(--svg-scale)); stroke-dasharray: 4 3; }}
  .license-line {{ stroke: var(--license-line); stroke-width: calc(2px / var(--svg-scale)); }}
  .license-label {{ fill: var(--license-line); font-size: calc(11px / var(--svg-scale)); }}
  .axis-label {{ fill: var(--text-muted); font-size: calc(11px / var(--svg-scale)); }}
  .line {{ fill: none; stroke-width: calc(2px / var(--svg-scale)); }}
  .line-1 {{ stroke: var(--series-1); }}
  .line-2 {{ stroke: var(--series-2); stroke-width: calc(3px / var(--svg-scale)); }}
  .dot {{ stroke: var(--surface-1); stroke-width: calc(1.5px / var(--svg-scale)); }}
  .dot-1 {{ fill: var(--series-1); }}
  .dot-2 {{ fill: var(--series-2); }}
  .callout {{ font-size: calc(12px / var(--svg-scale)); fill: var(--text-secondary); }}
  .callout-strong {{ font-size: calc(13px / var(--svg-scale)); fill: var(--text-primary); font-weight: 600; }}
  .callout-plain {{ font-size: calc(13px / var(--svg-scale)); font-weight: 600; fill: var(--text-secondary); }}
  #tooltip {{
    position: absolute;
    pointer-events: none;
    background: var(--text-primary);
    color: var(--surface-1);
    font-size: 12px;
    line-height: 1.5;
    padding: 8px 10px;
    border-radius: 6px;
    opacity: 0;
    transition: opacity 0.08s;
    white-space: nowrap;
    z-index: 10;
  }}
  .crosshair {{ stroke: var(--text-muted); stroke-width: calc(1px / var(--svg-scale)); stroke-dasharray: 2 2; opacity: 0; }}
  .hit {{ fill: transparent; }}
  .note {{
    font-size: 12px;
    color: var(--text-secondary);
    margin-top: 16px;
    padding-top: 14px;
    border-top: 1px solid var(--gridline);
  }}
  .note b {{ color: var(--text-primary); }}
  .theme-toggle {{
    position: absolute;
    top: 20px;
    right: 20px;
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--page-plane);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 4px 6px;
    cursor: pointer;
    font-size: 12px;
    color: var(--text-secondary);
    user-select: none;
    z-index: 5;
  }}
  .theme-toggle button {{
    border: none;
    background: transparent;
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 12px;
    color: var(--text-secondary);
    cursor: pointer;
  }}
  .theme-toggle button.active {{
    background: var(--surface-1);
    color: var(--text-primary);
    font-weight: 600;
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card viz-root" id="chartCard" style="position: relative;">
    <div class="theme-toggle" id="themeToggle">
      <button type="button" data-theme-choice="light">Light</button>
      <button type="button" data-theme-choice="dark">Dark</button>
    </div>
    <h1>{page_title}</h1>
    <p class="sub">{len(days)}-day lookback. Each point on the 90th-percentile line is computed from that day plus the {window - 1} days before it — the shaded band shows the most recent {window}-day window. Hover any day to see its own {window}-day window.</p>
    <div class="legend">
      <span class="legend-item"><span class="swatch" style="background:var(--series-1); height:2px;"></span>Daily peak concurrent sessions</span>
      <span class="legend-item"><span class="swatch" style="background:var(--series-2); height:3px;"></span>Rolling {window}-day 90th percentile</span>
      {f'<span class="legend-item"><span class="swatch" style="background:var(--license-line); height:2px;"></span>License limit ({licenses:,})</span>' if licenses is not None else ''}
    </div>
    <svg id="chart" viewBox="0 0 880 400" preserveAspectRatio="xMidYMid meet"></svg>
    <div class="note">
      <b>Rolling 90th percentile:</b> current day + previous {window - 1} days, nearest-rank method (rank {round(0.9 * window)} of {window}).
    </div>
  </div>
</div>
<div id="tooltip"></div>
<script>
(function () {{
  const days = {days!r};
  const peak = {peak!r};
  const dates = {dates_js!r};
  const hasDates = dates.length === days.length;
  const rolling = {{{", ".join(f"{k}: {v}" for k, v in rolling.items())}}};
  const window = {window};
  const licenses = {licenses_js};

  const themeToggle = document.getElementById('themeToggle');
  function setActiveThemeButton(name) {{
    themeToggle.querySelectorAll('button').forEach(b => {{
      b.classList.toggle('active', b.dataset.themeChoice === name);
    }});
  }}
  setActiveThemeButton(document.documentElement.getAttribute('data-theme'));
  themeToggle.addEventListener('click', (e) => {{
    const btn = e.target.closest('button[data-theme-choice]');
    if (!btn) return;
    const choice = btn.dataset.themeChoice;
    document.documentElement.setAttribute('data-theme', choice);
    setActiveThemeButton(choice);
    try {{ localStorage.setItem('cam-chart-theme', choice); }} catch (err) {{}}
  }});
  try {{
    const saved = localStorage.getItem('cam-chart-theme');
    if (saved === 'light' || saved === 'dark') {{
      document.documentElement.setAttribute('data-theme', saved);
      setActiveThemeButton(saved);
    }}
  }} catch (err) {{}}

  function formatDate(iso, opts) {{
    return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', opts);
  }}
  function axisLabel(d) {{
    return hasDates ? formatDate(dates[d - 1], {{ month: 'short', day: 'numeric' }}) : 'D' + d;
  }}
  function tooltipLabel(d) {{
    return hasDates ? formatDate(dates[d - 1], {{ month: 'short', day: 'numeric', year: 'numeric' }}) : 'Day ' + d;
  }}

  const svg = document.getElementById('chart');
  const W = 880, H = 400;
  const marginL = 54, marginR = 20, marginT = 16, marginB = 36;
  const plotW = W - marginL - marginR;
  const plotH = H - marginT - marginB;

  function svgScale() {{
    const rect = svg.getBoundingClientRect();
    return rect.width > 0 ? Math.min(rect.width / W, rect.height / H) : 1;
  }}
  function syncSvgScale() {{
    document.documentElement.style.setProperty('--svg-scale', svgScale() || 1);
  }}

  function niceTicks(dataMin, dataMaxRaw, tickCount) {{
    const dataMax = dataMaxRaw > dataMin ? dataMaxRaw : dataMin + 1;
    const roughStep = (dataMax - dataMin) / tickCount;
    const magnitude = Math.pow(10, Math.floor(Math.log10(roughStep)));
    const normalized = roughStep / magnitude;
    const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    const step = nice * magnitude;
    const niceMin = Math.floor(dataMin / step) * step;
    const stepsNeeded = Math.ceil((dataMax - niceMin) / step);
    const ticks = [];
    for (let i = 0; i <= stepsNeeded; i++) ticks.push(Math.max(0, niceMin + i * step));
    return ticks;
  }}

  const dataMin = Math.min(...peak);
  const dataMax = licenses !== null ? Math.max(...peak, licenses) : Math.max(...peak);
  const ticks = niceTicks(dataMin, dataMax, 5);
  const yMin = ticks[0], yMax = ticks[ticks.length - 1];
  const xScale = d => marginL + ((d - 1) / (days.length - 1)) * plotW;
  const yScale = v => marginT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

  const ns = 'http://www.w3.org/2000/svg';
  function el(tag, attrs) {{
    const e = document.createElementNS(ns, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }}

  ticks.forEach(v => {{
    const y = yScale(v);
    svg.appendChild(el('line', {{ class: 'gridline', x1: marginL, x2: W - marginR, y1: y, y2: y }}));
    const t = el('text', {{ class: 'axis-label', x: marginL - 10, y: y + 4, 'text-anchor': 'end' }});
    t.textContent = Math.round(v).toLocaleString();
    svg.appendChild(t);
  }});

  const labelStride = Math.max(1, Math.floor(days.length / 12));
  days.forEach(d => {{
    const x = xScale(d);
    svg.appendChild(el('line', {{ class: 'x-tick', x1: x, x2: x, y1: H - marginB, y2: H - marginB + 4 }}));
    if ((d - 1) % labelStride === 0 || d === days.length) {{
      const t = el('text', {{ class: 'axis-label', x: x, y: H - marginB + 18, 'text-anchor': 'middle' }});
      t.textContent = axisLabel(d);
      svg.appendChild(t);
    }}
  }});
  svg.appendChild(el('line', {{ class: 'baseline', x1: marginL, x2: W - marginR, y1: H - marginB, y2: H - marginB }}));
  {license_line_js}
  const rollDaysInit = Object.keys(rolling).map(Number);
  const defaultWindowEnd = rollDaysInit.length ? Math.max(...rollDaysInit) : days.length;
  const defaultWindowStart = Math.max(1, defaultWindowEnd - window + 1);
  const spanX1 = xScale(defaultWindowStart), spanX2 = xScale(defaultWindowEnd);
  const windowRect = el('rect', {{
    x: spanX1, y: marginT, width: spanX2 - spanX1, height: plotH, rx: 4,
    fill: 'var(--window-highlight)', opacity: 0.3,
    stroke: 'var(--window-highlight)', 'stroke-width': 2, 'stroke-opacity': 1
  }});
  svg.appendChild(windowRect);
  const spanLabel = el('text', {{ class: 'callout-plain', x: (spanX1 + spanX2) / 2, y: marginT + 16, 'text-anchor': 'middle' }});
  svg.appendChild(spanLabel);
  function setSpanLabel(day, x) {{
    spanLabel.setAttribute('x', x);
    spanLabel.textContent = '';
    const line1 = el('tspan', {{ x: x, dy: '0' }});
    line1.textContent = 'Day ' + day;
    const line2 = el('tspan', {{ x: x, dy: '1.1em' }});
    line2.textContent = window + '-day window';
    spanLabel.appendChild(line1);
    spanLabel.appendChild(line2);
  }}
  setSpanLabel(defaultWindowEnd, (spanX1 + spanX2) / 2);

  const firstRollDay = rollDaysInit.length ? Math.min(...rollDaysInit) : null;
  if (firstRollDay !== null) {{
    const fx = xScale(firstRollDay);
    svg.appendChild(el('line', {{ class: 'first-window-line', x1: fx, x2: fx, y1: marginT, y2: H - marginB }}));
  }}

  const rollDays = Object.keys(rolling).map(Number).sort((a, b) => a - b);
  let rollPath = '';
  rollDays.forEach((n, i) => {{
    const x = xScale(n), y = yScale(rolling[n]);
    rollPath += (i === 0 ? 'M' : 'L') + x + ',' + y + ' ';
  }});
  svg.appendChild(el('path', {{ class: 'line line-2', d: rollPath.trim() }}));
  rollDays.forEach(n => {{
    svg.appendChild(el('circle', {{ class: 'dot dot-2', cx: xScale(n), cy: yScale(rolling[n]), r: 4 }}));
  }});

  let peakPath = '';
  days.forEach((d, i) => {{
    const x = xScale(d), y = yScale(peak[i]);
    peakPath += (i === 0 ? 'M' : 'L') + x + ',' + y + ' ';
  }});
  svg.appendChild(el('path', {{ class: 'line line-1', d: peakPath.trim() }}));
  days.forEach((d, i) => {{
    svg.appendChild(el('circle', {{ class: 'dot dot-1', cx: xScale(d), cy: yScale(peak[i]), r: 4 }}));
  }});

  function callout(day, value, text, dy) {{
    const nearRight = day >= days.length - Math.max(1, Math.round(days.length * 0.1));
    const nearLeft = day <= 1 + Math.max(1, Math.round(days.length * 0.1));
    const anchor = nearRight ? 'end' : nearLeft ? 'start' : 'middle';
    const dx = anchor === 'end' ? -8 : anchor === 'start' ? 8 : 0;
    const t = el('text', {{ class: 'callout-strong', x: xScale(day) + dx, y: yScale(value) + dy, 'text-anchor': anchor }});
    t.textContent = text;
    svg.appendChild(t);
  }}
  const peakValue = Math.max(...peak);
  const peakDay = peak.indexOf(peakValue) + 1;
  const rollDaysForCallout = Object.keys(rolling).map(Number);
  const lastRollDay = rollDaysForCallout.length ? Math.max(...rollDaysForCallout) : null;
  // pixel-space (not day-index) overlap check, so it holds regardless of day count:
  // estimated label half-width + a safe vertical line-gap, both in the fixed 880x400 user space.
  const LABEL_HALF_WIDTH = 70;
  const LABEL_LINE_GAP = 18;
  let peakDy = -14, p90Dy = -14;
  if (lastRollDay !== null) {{
    const dxGap = Math.abs(xScale(peakDay) - xScale(lastRollDay));
    const dyGap = Math.abs((yScale(peakValue) + peakDy) - (yScale(rolling[lastRollDay]) + p90Dy));
    if (dxGap < LABEL_HALF_WIDTH * 2 && dyGap < LABEL_LINE_GAP) {{
      peakDy -= LABEL_LINE_GAP;
    }}
  }}
  callout(peakDay, peakValue, 'peak: ' + peakValue.toLocaleString(), peakDy);
  if (lastRollDay !== null) {{
    callout(lastRollDay, rolling[lastRollDay], 'p90: ' + rolling[lastRollDay].toLocaleString(), p90Dy);
  }}

  const crosshair = el('line', {{ class: 'crosshair', x1: 0, x2: 0, y1: marginT, y2: H - marginB }});
  svg.appendChild(crosshair);
  const hit = el('rect', {{ class: 'hit', x: marginL, y: marginT, width: plotW, height: plotH }});
  svg.appendChild(hit);

  const tooltip = document.getElementById('tooltip');
  const card = document.getElementById('chartCard');

  hit.addEventListener('mousemove', (e) => {{
    const rect = svg.getBoundingClientRect();
    const scale = svgScale();
    const offsetX = (rect.width - W * scale) / 2;
    const mx = (e.clientX - rect.left - offsetX) / scale;
    const day = Math.max(1, Math.min(days.length, Math.round(((mx - marginL) / plotW) * (days.length - 1) + 1)));
    const x = xScale(day);
    crosshair.setAttribute('x1', x);
    crosshair.setAttribute('x2', x);
    crosshair.setAttribute('opacity', 1);

    let html = `<div style="font-weight:600;margin-bottom:4px;">${{tooltipLabel(day)}}</div>`;
    html += `<div>Daily peak: ${{peak[day - 1].toLocaleString()}}</div>`;
    if (rolling[day] !== undefined) {{
      html += `<div>Rolling 90th pct: ${{rolling[day].toLocaleString()}}</div>`;
      const winStart = day - (window - 1);
      windowRect.setAttribute('x', xScale(winStart));
      windowRect.setAttribute('width', xScale(day) - xScale(winStart));
      windowRect.setAttribute('opacity', 0.5);
      setSpanLabel(day, (xScale(winStart) + xScale(day)) / 2);
    }} else {{
      html += `<div style="opacity:0.6">Rolling 90th pct: n/a (needs ${{window}} days)</div>`;
      windowRect.setAttribute('opacity', 0);
    }}
    tooltip.innerHTML = html;
    tooltip.style.opacity = 1;

    const cardRect = card.getBoundingClientRect();
    tooltip.style.left = (e.clientX - cardRect.left + 16) + 'px';
    tooltip.style.top = (e.clientY - cardRect.top - 10) + 'px';
  }});
  hit.addEventListener('mouseleave', () => {{
    tooltip.style.opacity = 0;
    crosshair.setAttribute('opacity', 0);
    windowRect.setAttribute('x', spanX1);
    windowRect.setAttribute('width', spanX2 - spanX1);
    windowRect.setAttribute('opacity', 0.3);
    setSpanLabel(defaultWindowEnd, (spanX1 + spanX2) / 2);
  }});

  syncSvgScale();
  new ResizeObserver(syncSvgScale).observe(svg);
}})();
</script>
</body>
</html>
"""


def generate_svg(
    peak: list[int],
    rolling: dict[int, int],
    width: int = DEFAULT_SVG_WIDTH,
    height: int = DEFAULT_SVG_HEIGHT,
    theme: str = DEFAULT_THEME,
    dates: Optional[list[str]] = None,
    licenses: Optional[int] = None,
    org_name: Optional[str] = None,
) -> str:
    """Build a static SVG rendering of the chart at the given pixel dimensions and theme.

    `dates` are ISO date strings aligned 1:1 with `peak`, used to label the x-axis
    with real dates (e.g. "Sep 10"). Falls back to generic "D#" day labels when
    `dates` is omitted or its length doesn't match `peak`.

    `licenses`, when given, draws a horizontal reference line at that session count.

    `org_name`, when given, is prefixed to the chart heading.

    Static image only, no window-overlay annotation — that's an interactive-hover
    feature reserved for generate_html().
    """
    days = list(range(1, len(peak) + 1))
    window = len(peak) - len(rolling) + 1 if rolling else 10
    use_real_dates = dates is not None and len(dates) == len(days)
    t = THEMES[theme]
    surface, text_primary, text_secondary, text_muted, gridline, baseline, series_1, series_2, first_window_marker, license_line_color = (
        t["surface"], t["text_primary"], t["text_secondary"], t["text_muted"],
        t["gridline"], t["baseline"], t["series_1"], t["series_2"], t["first_window_marker"], t["license_line"],
    )

    margin_l, margin_r, margin_t, margin_b = 110, 60, 128, 90
    plot_w = width - margin_l - margin_r
    plot_h = height - margin_t - margin_b
    data_min = min(peak)
    data_max = max(max(peak), licenses) if licenses is not None else max(peak)
    ticks = compute_nice_ticks(data_min, data_max, tick_count=5)
    y_min, y_max = ticks[0], ticks[-1]

    def x_scale(d: int) -> float:
        return margin_l + ((d - 1) / (len(days) - 1)) * plot_w if len(days) > 1 else margin_l

    def y_scale(v: float) -> float:
        return margin_t + plot_h - ((v - y_min) / (y_max - y_min)) * plot_h

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, -apple-system, Segoe UI, sans-serif">'
    )
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="{surface}"/>')

    heading = "CAM License Usage — Daily Peak Concurrent Sessions &amp; Rolling 90th Percentile"
    if org_name:
        heading = f"{org_name} — {heading}"
    parts.append(
        f'<text x="{margin_l}" y="40" font-size="30" font-weight="600" fill="{text_primary}">{heading}</text>'
    )
    parts.append(
        f'<text x="{margin_l}" y="68" font-size="16" fill="{text_secondary}">'
        f"{len(days)}-day lookback. Each point on the 90th-percentile line is computed from that day "
        f"plus the {window - 1} days before it.</text>"
    )

    # legend, directly under the chart description
    legend_y = 100
    parts.append(f'<rect x="{margin_l}" y="{legend_y - 10}" width="30" height="3" fill="{series_1}"/>')
    parts.append(f'<text x="{margin_l + 40}" y="{legend_y}" font-size="16" fill="{text_secondary}">Daily peak concurrent sessions</text>')
    parts.append(f'<rect x="{margin_l + 400}" y="{legend_y - 10}" width="30" height="5" fill="{series_2}"/>')
    parts.append(f'<text x="{margin_l + 440}" y="{legend_y}" font-size="16" fill="{text_secondary}">Rolling {window}-day 90th percentile</text>')
    if licenses is not None:
        parts.append(f'<rect x="{margin_l + 820}" y="{legend_y - 10}" width="30" height="3" fill="{license_line_color}"/>')
        parts.append(f'<text x="{margin_l + 860}" y="{legend_y}" font-size="16" fill="{text_secondary}">License limit ({licenses:,})</text>')

    # gridlines + y-axis labels (nice round values spanning the data range)
    for v in ticks:
        y = y_scale(v)
        parts.append(f'<line x1="{margin_l}" x2="{width - margin_r}" y1="{y}" y2="{y}" stroke="{gridline}" stroke-width="1"/>')
        parts.append(f'<text x="{margin_l - 16}" y="{y + 6}" font-size="18" fill="{text_muted}" text-anchor="end">{round(v):,}</text>')

    # x-axis tick marks for every day, labels only every ~12th (evenly spaced regardless of day count)
    tick_stride = max(1, len(days) // 12)
    for d in days:
        x = x_scale(d)
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{height - margin_b}" y2="{height - margin_b + 8}" stroke="{baseline}" stroke-width="1"/>')
        if (d - 1) % tick_stride == 0 or d == len(days):
            label = format_date_label(dates[d - 1]) if use_real_dates else f"D{d}"
            parts.append(f'<text x="{x}" y="{height - margin_b + 32}" font-size="16" fill="{text_muted}" text-anchor="middle">{label}</text>')
    parts.append(
        f'<line x1="{margin_l}" x2="{width - margin_r}" y1="{height - margin_b}" y2="{height - margin_b}" '
        f'stroke="{baseline}" stroke-width="1"/>'
    )

    if licenses is not None:
        ly = y_scale(licenses)
        parts.append(
            f'<line x1="{margin_l}" x2="{width - margin_r}" y1="{ly:.1f}" y2="{ly:.1f}" '
            f'stroke="{license_line_color}" stroke-width="3"/>'
        )
        parts.append(
            f'<text x="{margin_l}" y="{ly + 24:.1f}" font-size="16" fill="{license_line_color}" '
            f'text-anchor="start">License limit: {licenses:,}</text>'
        )

    # rolling line + dots
    roll_days = sorted(rolling)

    # dark gray marker for the first day with a full 10-day 90th-percentile window
    if roll_days:
        fx = x_scale(roll_days[0])
        parts.append(
            f'<line x1="{fx:.1f}" x2="{fx:.1f}" y1="{margin_t}" y2="{height - margin_b}" '
            f'stroke="{first_window_marker}" stroke-width="2" stroke-dasharray="6 4"/>'
        )

    if roll_days:
        roll_pts = " ".join(f"{x_scale(d):.1f},{y_scale(rolling[d]):.1f}" for d in roll_days)
        parts.append(f'<polyline points="{roll_pts}" fill="none" stroke="{series_2}" stroke-width="5"/>')
        for d in roll_days:
            parts.append(f'<circle cx="{x_scale(d):.1f}" cy="{y_scale(rolling[d]):.1f}" r="7" fill="{series_2}" stroke="{surface}" stroke-width="2"/>')

    # daily peak line + dots
    peak_pts = " ".join(f"{x_scale(d):.1f},{y_scale(v):.1f}" for d, v in zip(days, peak))
    parts.append(f'<polyline points="{peak_pts}" fill="none" stroke="{series_1}" stroke-width="3"/>')
    for d, v in zip(days, peak):
        parts.append(f'<circle cx="{x_scale(d):.1f}" cy="{y_scale(v):.1f}" r="6" fill="{series_1}" stroke="{surface}" stroke-width="2"/>')

    # callouts — data-driven, not tied to any fixed day index
    def callout(day: int, value: int, text: str, dy: int) -> str:
        edge_band = max(1, round(len(days) * 0.1))
        near_right = day >= len(days) - edge_band
        near_left = day <= 1 + edge_band
        anchor = "end" if near_right else "start" if near_left else "middle"
        dx = -10 if anchor == "end" else 10 if anchor == "start" else 0
        return (
            f'<text x="{x_scale(day) + dx:.1f}" y="{y_scale(value) + dy:.1f}" font-size="17" font-weight="600" '
            f'fill="{text_primary}" text-anchor="{anchor}">{text}</text>'
        )

    peak_day = max(range(len(peak)), key=lambda i: peak[i]) + 1
    last_day = roll_days[-1] if roll_days else None
    # pixel-space (not day-index) overlap check, so it holds regardless of day count or canvas size:
    # estimated label half-width + a safe vertical line-gap for the font-size="17" callouts.
    label_half_width = 90
    label_line_gap = 24
    peak_dy, p90_dy = -20, -20
    if last_day is not None:
        dx_gap = abs(x_scale(peak_day) - x_scale(last_day))
        dy_gap = abs((y_scale(peak[peak_day - 1]) + peak_dy) - (y_scale(rolling[last_day]) + p90_dy))
        if dx_gap < label_half_width * 2 and dy_gap < label_line_gap:
            peak_dy -= label_line_gap
    parts.append(callout(peak_day, peak[peak_day - 1], f"peak: {peak[peak_day - 1]:,}", peak_dy))
    if last_day is not None:
        parts.append(callout(last_day, rolling[last_day], f"p90: {rolling[last_day]:,}", p90_dy))

    parts.append("</svg>")
    return "\n".join(parts)


async def run(
    org_id: Optional[str],
    days: int,
    output_dir: str,
    width: int,
    height: int,
    theme: str = DEFAULT_THEME,
    licenses: Optional[int] = None,
    fmt: str = "html",
) -> None:
    """Fetch license usage from the Meraki Dashboard API and write the dated HTML or SVG chart."""
    api_key = os.getenv("MERAKI_DASHBOARD_API_KEY")

    if not api_key:
        logger.error("MERAKI_DASHBOARD_API_KEY not set")
        sys.exit(1)
    if not org_id:
        logger.error("Organization ID required (--org or MERAKI_ORG_ID)")
        sys.exit(1)

    start_date, end_date = build_date_range(days)
    logger.info(f"Fetching license usage for org {org_id}: {start_date} to {end_date}")

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=httpx.Timeout(30.0),
    ) as client:
        payload = await fetch_license_usage(client, org_id, start_date, end_date)
        org = await fetch_organization(client, org_id)

    org_name = org.get("name")
    dates, peak = parse_usage_data(payload)
    logger.info(f"Fetched {len(peak)} day(s) of usage data")

    rolling = rolling_90th_percentile(peak, window=10)

    os.makedirs(output_dir, exist_ok=True)
    out_path = build_output_path(output_dir, fmt)

    if fmt == "html":
        html = generate_html(peak, rolling, theme=theme, dates=dates, licenses=licenses, org_name=org_name)
        with open(out_path, "w") as f:
            f.write(html)
        logger.info(f"Wrote HTML chart to {out_path}")
    else:
        svg = generate_svg(
            peak, rolling, width=width, height=height, theme=theme, dates=dates, licenses=licenses, org_name=org_name
        )
        with open(out_path, "w") as f:
            f.write(svg)
        logger.info(f"Wrote {width}x{height} SVG chart to {out_path}")


@click.command()
@click.option("--org", "org_id", envvar="MERAKI_ORG_ID", help="Organization ID (or set MERAKI_ORG_ID)")
@click.option("--days", "-d", default=DEFAULT_DAYS, type=int, help=f"Number of days to look back (default: {DEFAULT_DAYS})")
@click.option(
    "--output",
    "-o",
    "output_dir",
    default=DEFAULT_OUTPUT_DIR,
    type=str,
    help=f"Directory to write the dated chart into (default: {DEFAULT_OUTPUT_DIR})",
)
@click.option(
    "--format",
    "-f",
    "fmt",
    default=DEFAULT_FORMAT,
    type=click.Choice(["html", "svg"]),
    help=f"Output format: interactive HTML or static SVG (default: {DEFAULT_FORMAT})",
)
@click.option("--width", default=DEFAULT_SVG_WIDTH, type=int, help=f"SVG width in pixels (default: {DEFAULT_SVG_WIDTH})")
@click.option("--height", default=DEFAULT_SVG_HEIGHT, type=int, help=f"SVG height in pixels (default: {DEFAULT_SVG_HEIGHT})")
@click.option(
    "--theme",
    "-t",
    default=DEFAULT_THEME,
    type=click.Choice(["light", "dark"]),
    help=f"Color theme for the generated charts (default: {DEFAULT_THEME})",
)
@click.option(
    "--licenses",
    "-l",
    "licenses",
    default=None,
    type=int,
    help="Include a license count horizontal reference line",
)
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging")
def main(
    org_id: Optional[str],
    days: int,
    output_dir: str,
    fmt: str,
    width: int,
    height: int,
    theme: str,
    licenses: Optional[int],
    verbose: bool,
) -> None:
    """Generate the CAM license-usage rolling-window chart from live usage data."""
    if verbose:
        logger.setLevel(logging.INFO)

    asyncio.run(
        run(
            org_id=org_id,
            days=days,
            output_dir=output_dir,
            width=width,
            height=height,
            theme=theme,
            licenses=licenses,
            fmt=fmt,
        )
    )


if __name__ == "__main__":
    main()
