#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27.0",
#     "python-dotenv>=1.0.0",
#     "click>=8.0.0",
#     "pytest>=8.0.0",
#     "pytest-asyncio>=0.23.0",
# ]
# ///
"""Tests for cam-license-usage.py"""

import importlib.util
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

spec = importlib.util.spec_from_file_location(
    "cam_license_usage", Path(__file__).parent / "cam-license-usage.py"
)
mod = importlib.util.module_from_spec(spec)
sys.modules["cam_license_usage"] = mod
spec.loader.exec_module(mod)

SAMPLE_PAYLOAD = {
    "organizationId": "123456",
    "type": "CoTerm",
    "timeRange": {"startDate": "2026-09-01", "endDate": "2026-09-05"},
    "licenseLimit": 1500,
    "usageData": [
        {"date": "2026-09-03", "peakConcurrentSessions": 950},
        {"date": "2026-09-01", "peakConcurrentSessions": 800},
        {"date": "2026-09-02", "peakConcurrentSessions": 900},
        {"date": "2026-09-05", "peakConcurrentSessions": 1000},
        {"date": "2026-09-04", "peakConcurrentSessions": 980},
    ],
    "summary": {
        "daysInPeriod": 5,
        "highestPeakConcurrentSessions": 1000,
        "p90PeakConcurrentSessions": 980.0,
        "p95PeakConcurrentSessions": 1000.0,
    },
}


def test_rolling_90th_percentile_matches_worked_example():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)

    assert rolling[10] == 1200
    assert rolling[11] == 1300
    assert rolling[12] == 1300
    assert rolling[19] == 1300
    assert rolling[20] == 1200


def test_rolling_90th_percentile_omits_days_before_full_window():
    peak = list(range(800, 800 + 15 * 10, 10))  # 15 values
    rolling = mod.rolling_90th_percentile(peak, window=10)
    assert 9 not in rolling
    assert 10 in rolling


def test_rolling_90th_percentile_uses_nearest_rank():
    rolling = mod.rolling_90th_percentile(list(range(1, 11)), window=10)
    assert rolling[10] == 9


def test_parse_usage_data_sorts_by_date_ascending():
    dates, values = mod.parse_usage_data(SAMPLE_PAYLOAD)
    assert dates == ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05"]
    assert values == [800, 900, 950, 980, 1000]


def test_build_date_range_365_days():
    start, end = mod.build_date_range(days=365, today="2026-10-09")
    assert end == "2026-10-09"
    assert start == "2025-10-10"


def test_build_date_range_custom_days():
    start, end = mod.build_date_range(days=7, today="2026-10-09")
    assert start == "2026-10-03"
    assert end == "2026-10-09"


def test_build_output_path_prefixes_filename_with_todays_date_html():
    path = mod.build_output_path("/tmp/out", "html", today="2026-10-09")
    assert path == "/tmp/out/2026-10-09-cam-license-usage.html"


def test_build_output_path_prefixes_filename_with_todays_date_svg():
    path = mod.build_output_path("/tmp/out", "svg", today="2026-10-09")
    assert path == "/tmp/out/2026-10-09-cam-license-usage.svg"


def test_build_output_path_defaults_to_current_directory():
    path = mod.build_output_path(".", "html", today="2026-10-09")
    assert path == "./2026-10-09-cam-license-usage.html"


@pytest.mark.asyncio
async def test_fetch_license_usage_requests_expected_url_and_params():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = SAMPLE_PAYLOAD
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    payload = await mod.fetch_license_usage(mock_client, "123456", "2026-09-01", "2026-09-05")

    mock_client.get.assert_awaited_once_with(
        "https://api.meraki.com/api/v1/organizations/123456/nac/license/usage",
        params={"startDate": "2026-09-01", "endDate": "2026-09-05"},
    )
    assert payload == SAMPLE_PAYLOAD


@pytest.mark.asyncio
async def test_fetch_license_usage_retries_on_429():
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {"Retry-After": "0"}

    ok = MagicMock()
    ok.status_code = 200
    ok.json.return_value = SAMPLE_PAYLOAD
    ok.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=[rate_limited, ok])

    payload = await mod.fetch_license_usage(mock_client, "123456", "2026-09-01")

    assert payload == SAMPLE_PAYLOAD
    assert mock_client.get.await_count == 2


def test_generate_html_is_self_contained_and_embeds_data():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert html.startswith("<!DOCTYPE html>")
    assert "<script>" in html
    assert "800" in html and "1300" in html


def test_generate_html_chart_card_expands_to_fill_body_width():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "max-width: 1600px" not in html
    # no explicit width: 100% on .wrap/.card — flex stretch sizes them within the margin, avoiding overflow
    assert "width: 100%; flex: 1; display: flex; flex-direction: column;" not in html
    assert ".wrap {{ display: flex; flex-direction: column; }}".replace("{{", "{").replace("}}", "}") in html


def test_generate_html_card_hugs_content_height_via_svg_aspect_ratio():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    # body sizes itself via flex + min-height instead of a hard width/height: 100% (which overflowed with margin)
    assert "html, body { width: 100%; height: 100%; }" not in html
    assert "min-height: 100vh;" in html
    assert "display: flex;\n    flex-direction: column;" in html
    # the svg keeps its viewBox's aspect ratio instead of stretching with flex:1,
    # which previously letterboxed and left large empty gaps above/below the chart
    assert "aspect-ratio: 880 / 400;" in html
    assert "flex: 1; min-height: 400px;" not in html


def test_generate_svg_has_requested_dimensions():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080)

    assert svg.startswith("<svg")
    assert 'width="1920"' in svg
    assert 'height="1080"' in svg
    assert "<polyline" in svg or "<path" in svg


def test_generate_svg_omits_window_overlay():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080)

    assert "window feeding" not in svg
    assert "window-overlay" not in svg


def test_generate_svg_handles_dataset_shorter_than_window():
    peak = [800, 850, 900, 950, 1000]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080)

    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")


def test_generate_svg_uses_real_dates_when_provided():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    dates = [f"2026-09-{10 + i:02d}" for i in range(len(peak))]

    svg = mod.generate_svg(peak, rolling, dates=dates)

    assert ">D1<" not in svg
    assert ">Sep 10<" in svg


def test_generate_svg_falls_back_to_day_labels_without_dates():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)

    svg = mod.generate_svg(peak, rolling)

    assert ">D1<" in svg


def test_generate_svg_falls_back_to_day_labels_when_dates_length_mismatches():
    peak = [800, 850, 900]
    rolling = mod.rolling_90th_percentile(peak, window=10)

    svg = mod.generate_svg(peak, rolling, dates=["2026-01-01"])

    assert ">D1<" in svg


def test_generate_html_embeds_real_dates_when_provided():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    dates = [f"2026-09-{10 + i:02d}" for i in range(len(peak))]

    html = mod.generate_html(peak, rolling, dates=dates)

    assert "'2026-09-10'" in html
    assert "const hasDates = dates.length === days.length;" in html


def test_generate_html_falls_back_to_day_labels_without_dates():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)

    html = mod.generate_html(peak, rolling)

    assert "const dates = [];" in html


def test_generate_svg_y_axis_never_negative():
    peak = [0, 0, 0, 0, 10, 0, 0, 0, 0, 0]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080)

    assert ">-" not in svg


def test_generate_svg_dots_align_with_nice_round_gridlines():
    peak = [800, 900, 1000, 1100, 1200, 1300, 1200, 1100, 1000, 900]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080)

    grid_match = re.search(r'<text[^>]*y="([\d.]+)"[^>]*>1,000</text>', svg)
    assert grid_match, "expected a nice round 1,000 gridline/label"
    grid_y = float(grid_match.group(1)) - 6  # labels are offset +6 from the gridline itself

    dot_ys = [float(y) for y in re.findall(r'<circle cx="[\d.]+" cy="([\d.]+)" r="6"', svg)]
    assert any(abs(y - grid_y) < 0.5 for y in dot_ys), "no dot for a value of 1000 lines up with the 1,000 gridline"


def test_compute_nice_ticks_produces_round_steps():
    ticks = mod.compute_nice_ticks(800, 1300, tick_count=5)

    assert ticks == [800.0, 900.0, 1000.0, 1100.0, 1200.0, 1300.0]


def test_generate_html_y_axis_never_negative():
    peak = [0, 0, 0, 0, 10, 0, 0, 0, 0, 0]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "Math.max(0, niceMin + i * step)" in html


def test_generate_html_hover_window_uses_neutral_grey_not_series_color():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "--window-highlight" in html
    assert mod.THEMES["light"]["window_highlight"] in html
    assert "fill: 'var(--window-highlight)'" in html
    assert "fill: 'var(--series-2)'" not in html


def test_generate_html_rolling_window_label_anchored_to_box_top():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "y: marginT + 16" in html
    assert "y: marginT + plotH / 2" not in html


def test_generate_html_uses_zero_chart_margin():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    # one margin around the whole chart (body), not a second nested margin on the card
    assert html.count("margin: 0em;") == 1
    assert "margin: 0.2em;" not in html
    assert "margin: 1em;" not in html
    assert "padding: 0.2em;" in html


def test_generate_html_card_uses_half_em_padding():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "padding: 0.5em;" in html


def test_generate_html_draws_daily_x_axis_ticks():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "class: 'x-tick'" in html


def test_generate_html_x_axis_label_stride_scales_with_day_count():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "const labelStride = Math.max(1, Math.floor(days.length / 12));" in html
    assert "d % 2 === 1" not in html


def test_generate_svg_x_axis_label_stride_scales_with_day_count():
    peak_365 = [800 + (i % 50) for i in range(365)]
    rolling_365 = mod.rolling_90th_percentile(peak_365, window=10)
    svg_365 = mod.generate_svg(peak_365, rolling_365, width=1920, height=1080)
    label_count_365 = svg_365.count('font-size="16" fill="#B4B9C0" text-anchor="middle">')

    # a year of days must still render a bounded, readable number of x-axis labels
    assert label_count_365 <= 14


def test_generate_svg_draws_daily_x_axis_ticks():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080)

    tick_lines = re.findall(r'<line x1="[\d.]+" x2="[\d.]+" y1="990" y2="998" stroke="#D6D6D6"', svg)
    assert len(tick_lines) == len(peak)


def test_generate_html_marks_first_full_rolling_window_with_dark_gray_line():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "class: 'first-window-line'" in html
    assert "--first-window-marker" in html
    assert mod.THEMES["light"]["first_window_marker"] in html


def test_generate_svg_marks_first_full_rolling_window_with_dark_gray_line():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080)

    assert f'stroke="{mod.THEMES["light"]["first_window_marker"]}" stroke-width="2" stroke-dasharray="6 4"' in svg


def test_generate_html_dark_theme_uses_dark_surface():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)

    light_html = mod.generate_html(peak, rolling, theme="light")
    dark_html = mod.generate_html(peak, rolling, theme="dark")

    assert 'data-theme="dark"' in dark_html
    assert mod.THEMES["dark"]["surface"] in dark_html
    assert 'data-theme="light"' in light_html
    assert mod.THEMES["light"]["surface"] in light_html


def test_generate_html_window_box_text_matches_legend_font_size():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert ".legend {" in html and "font-size: 13px;" in html
    assert ".callout-plain { font-size: calc(13px / var(--svg-scale));" in html


def test_generate_html_legend_has_reduced_margin_and_padding():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "margin: 0 0 8px; padding: 0;" in html


def test_generate_html_legend_swatches_match_charted_line_thickness():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling, licenses=1500)

    # .line (series-1) and .license-line are both 2px (matched); .line-2 (series-2) is 3px
    assert "background:var(--series-1); height:2px;" in html
    assert "background:var(--series-2); height:3px;" in html
    assert "background:var(--license-line); height:2px;" in html


def test_generate_html_svg_stroke_widths_are_scale_compensated_to_match_legend_px():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling, licenses=1500)

    # a chart line's SVG stroke-width is in viewBox user units, which get magnified when the
    # responsive <svg> renders wider than its 880x400 viewBox — without compensation a "2px"
    # line renders thicker than the legend's literal 2px swatch. calc(/var(--svg-scale)) cancels that.
    assert ".line { fill: none; stroke-width: calc(2px / var(--svg-scale)); }" in html
    assert ".line-2 { stroke: var(--series-2); stroke-width: calc(3px / var(--svg-scale)); }" in html
    # daily peak and license line are the same thickness
    assert ".license-line { stroke: var(--license-line); stroke-width: calc(2px / var(--svg-scale)); }" in html


def test_generate_svg_legend_rects_match_charted_line_thickness():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080, licenses=1500)

    # daily-peak polyline is stroke-width="3", rolling polyline is "5"; license line matches daily peak at "3"
    assert 'height="3" fill="#02C8FF"' in svg
    assert 'height="5" fill="#FF9000"' in svg
    assert 'height="3" fill="#22C55E"' in svg


def test_generate_svg_legend_sits_under_chart_description_not_at_bottom():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080)

    description_pos = svg.index("90th-percentile line is computed")
    legend_pos = svg.index("Daily peak concurrent sessions")
    gridline_pos = svg.index('stroke="#e4e6e9"')

    # legend renders right after the description, well before the plot's gridlines
    assert description_pos < legend_pos < gridline_pos


def test_first_window_marker_is_medium_gray():
    assert mod.THEMES["light"]["first_window_marker"] == "#9A9A9A"
    assert mod.THEMES["dark"]["first_window_marker"] == "#9A9A9A"


def test_generate_html_draws_license_reference_line_when_given():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling, licenses=1500)

    assert "class: 'license-line'" in html
    assert "1500" in html
    assert "License limit" in html


def test_generate_html_omits_license_line_when_not_given():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "License limit" not in html


def test_generate_svg_draws_license_reference_line_when_given():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080, licenses=1500)

    assert "1,500" in svg
    assert "License limit" in svg


def test_generate_svg_omits_license_line_when_not_given():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080)

    assert "License limit" not in svg


def test_license_line_theme_color_is_green():
    assert mod.THEMES["light"]["license_line"] == "#22C55E"
    assert mod.THEMES["dark"]["license_line"] == "#22C55E"


def test_generate_html_license_line_is_solid_and_text_is_green():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling, licenses=1500)

    assert ".license-line { stroke: var(--license-line); stroke-width: calc(2px / var(--svg-scale)); }" in html
    assert ".license-label { fill: var(--license-line);" in html
    assert "class: 'license-label'" in html


def test_generate_html_license_label_left_aligned_under_line():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling, licenses=1500)

    assert "x: marginL, y: ly + 14, 'text-anchor': 'start'" in html


def test_generate_svg_license_line_is_solid_and_text_is_green():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080, licenses=1500)

    license_line_tag = re.search(r'<line[^>]*stroke="#22C55E"[^>]*/>', svg)
    assert license_line_tag is not None
    assert "stroke-dasharray" not in license_line_tag.group()
    assert 'fill="#22C55E"' in svg
    assert 'text-anchor="start">License limit:' in svg


class TestCliWiringLicenses:
    def test_licenses_flag_sets_value(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, ["-l", "1500"])

        assert result.exit_code == 0, result.output
        assert captured["licenses"] == 1500

    def test_licenses_defaults_to_none(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, [])

        assert result.exit_code == 0, result.output
        assert captured["licenses"] is None


def test_generate_html_title_includes_org_name():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling, org_name="Acme Corp")

    assert "Acme Corp" in html
    assert "<title>Acme Corp" in html


def test_generate_html_title_falls_back_without_org_name():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "<title>10-Day Rolling Window" in html


def test_generate_svg_title_includes_org_name():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, org_name="Acme Corp")

    assert "Acme Corp" in svg


@pytest.mark.asyncio
async def test_fetch_organization_requests_expected_url():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "123456", "name": "Acme Corp"}
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    org = await mod.fetch_organization(mock_client, "123456")

    mock_client.get.assert_awaited_once_with("https://api.meraki.com/api/v1/organizations/123456")
    assert org["name"] == "Acme Corp"


def test_generate_html_svg_text_sizes_use_scale_compensated_calc():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "--svg-scale: 1;" in html
    assert ".axis-label { fill: var(--text-muted); font-size: calc(11px / var(--svg-scale));" in html
    assert "function syncSvgScale" in html
    assert "ResizeObserver(syncSvgScale)" in html


def test_generate_svg_peak_and_p90_callouts_anchor_away_from_right_edge_when_near_it():
    # peak and the last rolling day are both the final day in a 20-day series
    peak = [800] * 19 + [1300]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080)

    assert 'text-anchor="end">peak:' in svg
    assert 'text-anchor="end">p90:' in svg


def test_generate_svg_peak_and_p90_callouts_stagger_when_colocated():
    peak = [800] * 19 + [1300]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    svg = mod.generate_svg(peak, rolling, width=1920, height=1080)

    peak_y = float(re.search(r'y="([\d.]+)"[^>]*text-anchor="end">peak:', svg).group(1))
    p90_y = float(re.search(r'y="([\d.]+)"[^>]*text-anchor="end">p90:', svg).group(1))
    assert abs(peak_y - p90_y) > 10


def test_generate_html_peak_and_p90_callouts_anchor_away_from_right_edge_when_near_it():
    peak = [800] * 19 + [1300]
    rolling = mod.rolling_90th_percentile(peak, window=10)
    html = mod.generate_html(peak, rolling)

    assert "'text-anchor': anchor" in html
    assert "nearRight ? 'end'" in html


def test_generate_svg_dark_theme_uses_dark_surface():
    peak = [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1300, 1300, 1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800]
    rolling = mod.rolling_90th_percentile(peak, window=10)

    dark_svg = mod.generate_svg(peak, rolling, theme="dark")
    light_svg = mod.generate_svg(peak, rolling, theme="light")

    assert f'fill="{mod.THEMES["dark"]["surface"]}"' in dark_svg
    assert f'fill="{mod.THEMES["light"]["surface"]}"' in light_svg
    assert dark_svg.split("\n")[1] != light_svg.split("\n")[1]  # background rect differs by theme


class TestCliWiring:
    def test_days_flag_sets_lookback(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, ["--days", "30"])

        assert result.exit_code == 0, result.output
        assert captured["days"] == 30
        assert captured["org_id"] == "env_org"

    def test_days_defaults_to_30(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, [])

        assert result.exit_code == 0, result.output
        assert captured["days"] == 30

    def test_org_flag_overrides_env(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, ["--org", "flag_org"])

        assert result.exit_code == 0, result.output
        assert captured["org_id"] == "flag_org"

    def test_output_defaults_to_current_directory(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, [])

        assert result.exit_code == 0, result.output
        assert captured["output_dir"] == "."

    def test_output_short_flag_sets_output_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, ["-o", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert captured["output_dir"] == str(tmp_path)

    def test_format_defaults_to_html(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, [])

        assert result.exit_code == 0, result.output
        assert captured["fmt"] == "html"

    def test_format_short_flag_sets_svg(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, ["-f", "svg"])

        assert result.exit_code == 0, result.output
        assert captured["fmt"] == "svg"

    def test_format_rejects_invalid_choice(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")
        result = CliRunner().invoke(mod.main, ["--format", "png"])
        assert result.exit_code != 0

    def test_width_and_height_default_to_1920x1080(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, [])

        assert result.exit_code == 0, result.output
        assert captured["width"] == 1920
        assert captured["height"] == 1080

    def test_theme_defaults_to_light(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, [])

        assert result.exit_code == 0, result.output
        assert captured["theme"] == "light"

    def test_theme_short_flag_sets_dark(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")

        captured = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(mod, "run", fake_run)
        result = CliRunner().invoke(mod.main, ["-t", "dark"])

        assert result.exit_code == 0, result.output
        assert captured["theme"] == "dark"

    def test_theme_rejects_invalid_choice(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")
        result = CliRunner().invoke(mod.main, ["--theme", "blue"])
        assert result.exit_code != 0

    def test_errors_without_org_id(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.delenv("MERAKI_ORG_ID", raising=False)
        result = CliRunner().invoke(mod.main, [])
        assert result.exit_code != 0


@pytest.mark.asyncio
async def test_run_writes_only_html_when_format_is_html(tmp_path, monkeypatch):
    monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")

    async def fake_fetch(client, org_id, start_date, end_date=None):
        return SAMPLE_PAYLOAD

    async def fake_fetch_org(client, org_id):
        return {"id": org_id, "name": "Acme Corp"}

    monkeypatch.setattr(mod, "fetch_license_usage", fake_fetch)
    monkeypatch.setattr(mod, "fetch_organization", fake_fetch_org)

    await mod.run(
        org_id="123456",
        days=5,
        output_dir=str(tmp_path),
        width=1920,
        height=1080,
        theme="dark",
        fmt="html",
    )

    today = mod.date.today().isoformat()
    html_out = tmp_path / f"{today}-cam-license-usage.html"
    svg_out = tmp_path / f"{today}-cam-license-usage.svg"

    assert html_out.exists()
    assert not svg_out.exists()
    assert mod.THEMES["dark"]["surface"] in html_out.read_text()


@pytest.mark.asyncio
async def test_run_writes_only_svg_when_format_is_svg(tmp_path, monkeypatch):
    monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")

    async def fake_fetch(client, org_id, start_date, end_date=None):
        return SAMPLE_PAYLOAD

    async def fake_fetch_org(client, org_id):
        return {"id": org_id, "name": "Acme Corp"}

    monkeypatch.setattr(mod, "fetch_license_usage", fake_fetch)
    monkeypatch.setattr(mod, "fetch_organization", fake_fetch_org)

    await mod.run(
        org_id="123456",
        days=5,
        output_dir=str(tmp_path),
        width=1920,
        height=1080,
        theme="dark",
        fmt="svg",
    )

    today = mod.date.today().isoformat()
    html_out = tmp_path / f"{today}-cam-license-usage.html"
    svg_out = tmp_path / f"{today}-cam-license-usage.svg"

    assert svg_out.exists()
    assert not html_out.exists()
    assert 'width="1920"' in svg_out.read_text()
    assert mod.THEMES["dark"]["surface"] in svg_out.read_text()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
