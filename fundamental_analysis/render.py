#!/usr/bin/env python3
"""Render a data-dense Stockcentric fundamental-analysis infographic."""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path

import fitz
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "outputs" / "fundamental-analysis-brief.pdf"
DEFAULT_PNG = ROOT / "outputs" / "fundamental-analysis-brief.png"

PAGE_W = 900
PAGE_H = 1125
EXPORT_W = 1600
EXPORT_H = 2000
MARGIN = 36
HEADER_COMPANY_X = 195
HEADER_QUOTE_GAP = 72
HEADER_QUOTE_MAX_WIDTH = 245
HEADER_SECONDARY_MAX_WIDTH = 410

BG = HexColor("#EAF4E8")
SURFACE = HexColor("#F8FBF4")
NAVY = HexColor("#15263E")
INK = HexColor("#172132")
TEAL = HexColor("#167B77")
MINT = HexColor("#79B8A5")
BLUE = HexColor("#3F6FB5")
PLUM = HexColor("#76538C")
GOLD = HexColor("#B98018")
CORAL = HexColor("#C9593A")
MUTED = HexColor("#5B6878")
LINE = HexColor("#BFD5BF")
PALE = HexColor("#E0EBDE")

_CURRENCY = "USD"
_SYMBOL = "$"
_UNIT_DIVISOR = 1_000_000_000.0
_UNIT_LABEL = "$B"


def configure_market(data: dict[str, object]) -> None:
    global _CURRENCY, _SYMBOL, _UNIT_DIVISOR, _UNIT_LABEL
    _CURRENCY = str(data.get("currency") or "USD").upper()
    _SYMBOL = "₹" if _CURRENCY == "INR" else "$"
    _UNIT_DIVISOR = safe_float(data.get("statement_unit_divisor"), 10_000_000.0 if _CURRENCY == "INR" else 1_000_000_000.0)
    _UNIT_LABEL = str(data.get("statement_unit_label") or ("₹ cr" if _CURRENCY == "INR" else "$B"))


def register_fonts() -> None:
    font_root = Path(__file__).resolve().parent / "fonts"
    fonts = (
        ("Display", font_root / "DejaVuSans-Bold.ttf"),
        ("Body", font_root / "DejaVuSans.ttf"),
        ("BodyBold", font_root / "DejaVuSans-Bold.ttf"),
        ("Mono", font_root / "DejaVuSansMono.ttf"),
        ("Currency", font_root / "DejaVuSans.ttf"),
        ("CurrencyBold", font_root / "DejaVuSans-Bold.ttf"),
        ("CurrencyMono", font_root / "DejaVuSansMono.ttf"),
    )
    for alias, path in fonts:
        if alias in pdfmetrics.getRegisteredFontNames():
            continue
        if not path.is_file():
            raise FileNotFoundError(f"Bundled font is unavailable: {path}")
        pdfmetrics.registerFont(TTFont(alias, str(path)))


def text_font(preferred: str, text: str) -> str:
    if "₹" not in text:
        return preferred
    if preferred in {"Display", "BodyBold"}:
        return "CurrencyBold"
    if preferred == "Mono":
        return "CurrencyMono"
    return "Currency"


def money_b(value: float) -> str:
    scaled = value / _UNIT_DIVISOR
    return f"{_SYMBOL}{scaled:,.1f}{' cr' if _CURRENCY == 'INR' else 'B'}"


def money_t(value: float) -> str:
    if _CURRENCY == "INR":
        return f"{_SYMBOL}{value / 1_000_000_000_000:.2f} lakh cr" if abs(value) >= 1_000_000_000_000 else money_b(value)
    return f"{_SYMBOL}{value / 1_000_000_000_000:.2f}T" if abs(value) >= 1_000_000_000_000 else money_b(value)


def signed_money_b(value: float) -> str:
    prefix = "-" if value < 0 else ""
    suffix = " cr" if _CURRENCY == "INR" else "B"
    return f"{prefix}{_SYMBOL}{abs(value):,.1f}{suffix}"


def money_per_share(value: float) -> str:
    prefix = "-" if value < 0 else ""
    return f"{prefix}{_SYMBOL}{abs(value):,.0f}"


def per_share_range(low: float, high: float) -> str:
    if low < 0 or high < 0:
        return f"{money_per_share(low)} to {money_per_share(high)}"
    if _CURRENCY == "INR":
        return f"₹{low:,.0f}–{high:,.0f}"
    return f"{money_per_share(low)}-{money_per_share(high)}"


def axis_per_share(value: float) -> str:
    if _CURRENCY == "INR":
        prefix = "-" if value < 0 else ""
        return f"{prefix}{abs(value):,.0f}"
    return money_per_share(value)


def pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def fiscal_label(year: int | str) -> str:
    return f"FY{str(year)[-2:]}"


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def optional_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def optional_pct(value: object) -> str:
    number = optional_float(value)
    return pct(number) if number is not None else "N/A"


def optional_multiple(value: object) -> str:
    number = optional_float(value)
    return f"{number:.2f}x" if number is not None else "N/A"


def yoy_label(current: float, previous: float) -> str:
    if previous < 0 <= current:
        return "TO PROFIT"
    if previous >= 0 > current:
        return "TO LOSS"
    if previous > 0:
        return pct(current / previous - 1)
    if previous < 0 and current < 0:
        change = (abs(current) / abs(previous) - 1) if previous else 0.0
        return f"LOSS {change:+.0%}"
    return "N/A"


def is_financial_issuer(data: dict[str, object]) -> bool:
    name = " ".join(
        str(data.get(key, "")) for key in ("company_name", "sector", "industry")
    ).upper()
    markers = (
        "BANK",
        "BANCORP",
        "JPMORGAN",
        "CITIGROUP",
        "GOLDMAN SACHS",
        "MORGAN STANLEY",
        "WELLS FARGO",
        "FINANCIAL SERVICES",
        "INSURANCE",
        "CAPITAL MARKETS",
        "CREDIT SERVICES",
    )
    return any(marker in name for marker in markers)


def valuation_is_comparable(data: dict[str, object]) -> tuple[bool, str]:
    if is_financial_issuer(data):
        return False, "Bank cash flows and leverage are not comparable to an industrial-company DCF."
    latest = data.get("historical_statements", [{}])[-1]
    if safe_float(latest.get("shares")) <= 0:
        return False, "Per-share valuation is suppressed because diluted-share data is unavailable."
    market = safe_float(data.get("current_price"))
    scenario = data.get("scenarios", {})
    values = [
        optional_float(scenario.get("range_low")),
        optional_float(scenario.get("base", {}).get("per_share")),
        optional_float(scenario.get("range_high")),
    ]
    if market <= 0 or any(value is None for value in values):
        return False, "Per-share valuation inputs are incomplete."
    finite_values = [float(value) for value in values if value is not None]
    if max(abs(value) for value in finite_values) > max(market * 100, 1_000_000):
        return False, "Per-share valuation is suppressed because the model output is not economically comparable."
    return True, ""


def nice_axis(values: list[float], target_intervals: int = 3) -> tuple[float, float, list[float]]:
    """Return a readable shared scale that includes all supplied values."""
    clean = [value for value in values if math.isfinite(value)] or [0.0, 1.0]
    raw_low, raw_high = min(clean), max(clean)
    if raw_low == raw_high:
        padding = max(abs(raw_low) * 0.20, 0.05)
        raw_low -= padding
        raw_high += padding
    raw_step = max((raw_high - raw_low) / target_intervals, 1e-6)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    fraction = raw_step / magnitude
    nice_fraction = 1 if fraction <= 1 else 2 if fraction <= 2 else 5 if fraction <= 5 else 10
    step = nice_fraction * magnitude
    axis_low = math.floor(raw_low / step) * step
    axis_high = math.ceil(raw_high / step) * step
    if raw_low >= 0 and axis_low < step:
        axis_low = 0.0
    ticks: list[float] = []
    value = axis_low
    while value <= axis_high + step * 0.01 and len(ticks) < 8:
        ticks.append(value)
        value += step
    return axis_low, axis_high, ticks


def panel(c: canvas.Canvas, x: float, y: float, w: float, h: float) -> None:
    c.setFillColor(HexColor("#D8E6D7"))
    c.roundRect(x + 3, y - 3, w, h, 9, stroke=0, fill=1)
    c.setFillColor(SURFACE)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.8)
    c.roundRect(x, y, w, h, 9, stroke=1, fill=1)


def section_title(
    c: canvas.Canvas,
    x: float,
    y: float,
    available_width: float,
    number: str,
    title: str,
    source: str,
) -> None:
    c.setFillColor(TEAL)
    c.circle(x + 7, y - 1, 7, stroke=0, fill=1)
    c.setFillColor(SURFACE)
    c.setFont("Mono", 6.6)
    c.drawCentredString(x + 7, y - 3.3, number)
    c.setFillColor(INK)
    c.setFont("BodyBold", 12.2)
    c.drawString(x + 21, y - 4, title)
    c.setFillColor(MUTED)
    c.setFont("Mono", 6.6)
    c.drawRightString(x + available_width, y - 4, source.upper())


def fit_text(c: canvas.Canvas | None, text: str, font: str, size: float, max_width: float) -> float:
    while size > 5 and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 0.25
    return size


def bounded_single_line(
    text: str,
    font: str,
    preferred_size: float,
    max_width: float,
    *,
    minimum_size: float = 10,
) -> tuple[str, float]:
    """Fit one headline without allowing its visual bounds to escape."""
    size = preferred_size
    while size > minimum_size and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 0.25
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text, size
    suffix = "..."
    shortened = text.strip()
    while shortened and pdfmetrics.stringWidth(shortened + suffix, font, minimum_size) > max_width:
        shortened = shortened[:-1].rstrip()
    return (shortened + suffix if shortened else suffix), minimum_size


def header_company_layout(company_name: str, quote_text: str) -> tuple[str, float, str, float, float]:
    """Return collision-free company and quote typography for the navy header."""
    quote_face = text_font("Display", quote_text)
    quote_size = fit_text(None, quote_text, quote_face, 40, HEADER_QUOTE_MAX_WIDTH)
    # Reserve the full quote column even when today's quote is short. This keeps
    # company-name typography stable across price changes and prevents crowding.
    quote_left = PAGE_W - MARGIN - HEADER_QUOTE_MAX_WIDTH
    company_max_width = max(120, quote_left - HEADER_COMPANY_X - HEADER_QUOTE_GAP)
    company_label, company_size = bounded_single_line(
        company_name,
        "BodyBold",
        19,
        company_max_width,
    )
    return company_label, company_size, quote_face, quote_size, quote_left


def wrapped_lines(text: str, font: str, size: float, max_width: float, max_lines: int = 2) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or pdfmetrics.stringWidth(candidate, font, size) <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines - 1:
            break
    if current and len(lines) < max_lines:
        remaining_start = sum(len(line.split()) for line in lines)
        remaining = " ".join(words[remaining_start:])
        while remaining and pdfmetrics.stringWidth(remaining, font, size) > max_width:
            remaining = remaining[:-1]
        if remaining != " ".join(words[remaining_start:]):
            remaining = remaining.rstrip(" .,;") + "…"
        lines.append(remaining)
    return lines


def insight_band(c: canvas.Canvas, x: float, y: float, w: float, text: str) -> None:
    c.setFillColor(PALE)
    c.roundRect(x, y, w, 34, 5, stroke=0, fill=1)
    c.setFillColor(TEAL)
    c.setFont("BodyBold", 7.2)
    c.drawString(x + 9, y + 21.5, "KEY INSIGHT")
    c.setFillColor(INK)
    value_font = text_font("BodyBold", text)
    c.setFont(value_font, 8.3)
    for index, line in enumerate(wrapped_lines(text, value_font, 8.3, w - 91, 2)):
        c.drawString(x + 82, y + 21.5 - index * 10.5, line)


def mini_insight(c: canvas.Canvas, x: float, y: float, w: float, text: str) -> None:
    c.setFillColor(PALE)
    c.roundRect(x, y, w, 40, 5, stroke=0, fill=1)
    c.setFillColor(TEAL)
    c.setFont("BodyBold", 6.8)
    c.drawString(x + 8, y + 28.5, "KEY INSIGHT")
    c.setFillColor(INK)
    value_font = text_font("BodyBold", text)
    c.setFont(value_font, 7.2)
    for index, line in enumerate(wrapped_lines(text, value_font, 7.2, w - 16, 2)):
        c.drawString(x + 8, y + 16.5 - index * 9.5, line)


def data_label(c: canvas.Canvas, x: float, baseline_y: float, text: str, color: HexColor) -> None:
    """Draw a direct label on an opaque plate so the chart mark never crosses the text."""
    font_size = 7.8
    value_font = text_font("BodyBold", text)
    label_width = pdfmetrics.stringWidth(text, value_font, font_size) + 8
    c.setFillColor(SURFACE)
    c.roundRect(x - label_width / 2, baseline_y - 2.5, label_width, 11.5, 3, stroke=0, fill=1)
    c.setFillColor(color)
    c.setFont(value_font, font_size)
    c.drawCentredString(x, baseline_y, text)


def tapered_band(
    c: canvas.Canvas,
    x1: float,
    x2: float,
    center_y: float,
    start_height: float,
    end_height: float,
    color: HexColor,
) -> None:
    """Draw a retained-value ribbon between two financial stages."""
    path = c.beginPath()
    mid_x = (x1 + x2) / 2
    path.moveTo(x1, center_y + start_height / 2)
    path.curveTo(mid_x, center_y + start_height / 2, mid_x, center_y + end_height / 2, x2, center_y + end_height / 2)
    path.lineTo(x2, center_y - end_height / 2)
    path.curveTo(mid_x, center_y - end_height / 2, mid_x, center_y - start_height / 2, x1, center_y - start_height / 2)
    path.close()
    c.setFillColor(color)
    c.drawPath(path, stroke=0, fill=1)


def closest_fiscal_prices(history: dict[str, object], years: list[int]) -> list[float]:
    points = [
        (date.fromisoformat(str(point["date"])), float(point["adjusted_close"]))
        for point in history["points"]
    ]
    values: list[float] = []
    for year in years:
        # The sidecar does not currently expose each issuer's fiscal-end date.
        # A consistent year-end reference is more honest than assuming Sep 30.
        target = date(year, 12, 31)
        values.append(min(points, key=lambda item: abs((item[0] - target).days))[1])
    return values


def line_chart(
    c: canvas.Canvas,
    data: dict[str, object],
    market_history: dict[str, object],
    x: float,
    y: float,
    w: float,
    h: float,
) -> None:
    panel(c, x, y, w, h)
    period_count = len(data["historical_statements"][-5:])
    period_word = {4: "Four", 5: "Five"}.get(period_count, str(period_count))
    section_title(
        c,
        x + 18,
        y + h - 21,
        w - 36,
        "01",
        f"{period_word}-year trajectory in actual units",
        f"Market + {data.get('filing_label', 'annual filing')}",
    )
    rows = data["historical_statements"][-5:]
    years = [int(row["year"]) for row in rows]
    prices = closest_fiscal_prices(market_history, years)
    revenue = [float(row["revenue"]) / _UNIT_DIVISOR for row in rows]
    profit = [float(row["net_income"]) / _UNIT_DIVISOR for row in rows]
    series = [
        ("STOCK PRICE", f"{_SYMBOL}/SH", prices, BLUE, lambda value: money_per_share(value)),
        ("REVENUE", _UNIT_LABEL, revenue, TEAL, lambda value: signed_money_b(value)),
        ("NET INCOME", _UNIT_LABEL, profit, GOLD, lambda value: signed_money_b(value)),
    ]

    chart_x = x + 124
    chart_w = w - 218
    lane_top = y + h - 65
    lane_gap = 43
    lane_h = 25
    for index, (name, unit, values, color, formatter) in enumerate(series):
        lane_y = lane_top - index * lane_gap
        value_low = min(values)
        value_high = max(values)
        padding = max((value_high - value_low) * 0.18, value_high * 0.02)
        scale_low = value_low - padding
        scale_high = value_high + padding
        c.setFillColor(INK)
        c.setFont("BodyBold", 8.2)
        c.drawString(x + 18, lane_y + 6, name)
        c.setFillColor(MUTED)
        c.setFont(text_font("Mono", unit), 6.2)
        c.drawString(x + 18, lane_y - 5, unit)
        c.setStrokeColor(PALE)
        c.setLineWidth(1)
        c.line(chart_x, lane_y, chart_x + chart_w, lane_y)
        points: list[tuple[float, float]] = []
        for position, value in enumerate(values):
            px = chart_x + chart_w * position / (len(values) - 1)
            py = lane_y + lane_h * (value - scale_low) / max(scale_high - scale_low, 1e-9) - lane_h / 2
            points.append((px, py))
        c.setStrokeColor(color)
        c.setLineWidth(2.4)
        for start, end in zip(points, points[1:]):
            c.line(start[0], start[1], end[0], end[1])
        c.setFillColor(color)
        for px, py in points:
            c.circle(px, py, 3.1, stroke=0, fill=1)
        for point_index, (px, py) in enumerate(points):
            data_label(c, px, py + 8, formatter(values[point_index]), color)
        change = values[-1] / values[0] - 1 if values[0] else 0.0
        if name == "NET INCOME" and values[0] < 0 <= values[-1]:
            badge_label = "TO +"
        elif name == "NET INCOME" and values[0] >= 0 > values[-1]:
            badge_label = "TO -"
        else:
            badge_label = f"{change:+.0%}"
        badge_x = x + w - 74
        c.setFillColor(PALE)
        c.roundRect(badge_x, lane_y - 10, 55, 21, 5, stroke=0, fill=1)
        c.setFillColor(color)
        c.setFont("BodyBold", 8.5)
        c.drawCentredString(badge_x + 27.5, lane_y - 2.5, badge_label)

    for position, year in enumerate(years):
        px = chart_x + chart_w * position / (len(years) - 1)
        c.setFillColor(MUTED)
        c.setFont("Mono", 6.7)
        c.drawCentredString(px, y + 48, f"FY{str(year)[-2:]}")
    price_change = prices[-1] / prices[0] - 1 if prices[0] else 0.0
    revenue_change = revenue[-1] / revenue[0] - 1 if revenue[0] else 0.0
    profit_change = profit[-1] / profit[0] - 1 if profit[0] else 0.0
    insight_band(
        c,
        x + 14,
        y + 9,
        w - 28,
        f"Annual reference points show price {price_change:+.0%}, revenue {revenue_change:+.0%} and net income moving from {signed_money_b(profit[0])} to {signed_money_b(profit[-1])}. Separate scales preserve each series' real units.",
    )


def revenue_profit_chart(c: canvas.Canvas, data: dict[str, object], x: float, y: float, w: float, h: float) -> None:
    panel(c, x, y, w, h)
    latest = data["historical_statements"][-1]
    latest_year = int(latest["year"])
    section_title(c, x + 15, y + h - 19, w - 30, "02", f"{fiscal_label(latest_year)} income statement flow", str(data.get("filing_label", "Annual filing")))
    revenue = safe_float(latest.get("revenue")) / _UNIT_DIVISOR
    if revenue <= 0:
        raise ValueError("Latest annual revenue must be positive")
    gross = safe_float(latest.get("gross_profit")) / _UNIT_DIVISOR
    operating_raw = optional_float(latest.get("operating_income"))
    operating = safe_float(operating_raw) / _UNIT_DIVISOR
    pretax = safe_float(latest.get("pretax_income")) / _UNIT_DIVISOR
    net = safe_float(latest.get("net_income")) / _UNIT_DIVISOR
    operating_available = operating_raw is not None and not (operating_raw == 0 and net != 0)
    use_gross = 0 < gross <= revenue * 1.25
    second_label = "GROSS PROFIT" if use_gross else "PRE-TAX"
    second_value = gross if use_gross else pretax
    stages = [
        ("REVENUE", revenue, NAVY),
        (second_label, second_value, BLUE),
        ("OPERATING", operating, TEAL),
        ("NET INCOME", net, GOLD),
    ]
    stage_x = [x + 33, x + 137, x + 241, x + 345]
    center_y = y + 91
    flow_compatible = operating_available and all(value >= 0 for _, value, _ in stages) and all(
        stages[index][1] >= stages[index + 1][1] for index in range(3)
    )
    if flow_compatible:
        heights = [52 * value / revenue for _, value, _ in stages]
        for index in range(3):
            tapered_band(
                c,
                stage_x[index] + 5,
                stage_x[index + 1] - 5,
                center_y,
                heights[index],
                heights[index + 1],
                (MINT, TEAL, GOLD)[index],
            )
        for index, (_, _, color) in enumerate(stages):
            c.setFillColor(color)
            c.roundRect(stage_x[index] - 4, center_y - heights[index] / 2, 8, heights[index], 3, stroke=0, fill=1)
        costs = [revenue - second_value, second_value - operating, operating - net]
        cost_labels = ("COGS", "OPERATING COSTS", "TAX + OTHER") if use_gross else ("TO PRE-TAX", "OPERATING GAP", "TAX + OTHER")
        for index, (label, value) in enumerate(zip(cost_labels, costs)):
            cx = (stage_x[index] + stage_x[index + 1]) / 2
            cost_text = signed_money_b(-value)
            c.setFillColor(CORAL if value >= 0 else TEAL)
            c.setFont(text_font("BodyBold", cost_text), 6.5)
            c.drawCentredString(cx, y + 56, cost_text)
            c.setFillColor(MUTED)
            c.setFont("Mono", 5.3)
            c.drawCentredString(cx, y + 46, label)
    else:
        # A signed common-scale view avoids invalid tapered ribbons for losses,
        # financial issuers, or unusual statement taxonomies.
        baseline = y + 82
        chart_h = 43
        max_abs = max(abs(value) for _, value, _ in stages) or 1.0
        c.setStrokeColor(LINE)
        c.setLineWidth(0.8)
        c.line(x + 17, baseline, x + w - 17, baseline)
        for (label, value, color), sx in zip(stages, stage_x):
            if label == "OPERATING" and not operating_available:
                continue
            bar_h = chart_h * abs(value) / max_abs
            bar_y = baseline if value >= 0 else baseline - bar_h
            c.setFillColor(color if value >= 0 else CORAL)
            c.roundRect(sx - 13, bar_y, 26, max(bar_h, 1.5), 3, stroke=0, fill=1)
    for index, (label, value, color) in enumerate(stages):
        sx = stage_x[index]
        c.setFillColor(INK)
        display_value = "N/A" if label == "OPERATING" and not operating_available else signed_money_b(value)
        c.setFont(text_font("BodyBold", display_value), 9.8)
        c.drawCentredString(sx, y + 143, display_value)
        c.setFillColor(MUTED)
        c.setFont("Mono", 5.9)
        c.drawCentredString(sx, y + 132, label)
        c.setFont("BodyBold", 6.7)
        display_margin = "NOT REPORTED" if label == "OPERATING" and not operating_available else f"{value / revenue:.1%} OF SALES"
        c.drawCentredString(sx, y + 121, display_margin)
    net_margin = net / revenue
    if net >= 0:
        narrative = f"{data['ticker']} reported {signed_money_b(net)} of net income on {signed_money_b(revenue)} revenue: a {net_margin:.1%} net margin."
    else:
        narrative = f"{data['ticker']} reported a {signed_money_b(net)} net loss on {signed_money_b(revenue)} revenue: a {net_margin:.1%} net margin."
    insight_band(
        c,
        x + 11,
        y + 9,
        w - 22,
        narrative,
    )


def margin_chart(c: canvas.Canvas, data: dict[str, object], x: float, y: float, w: float, h: float) -> None:
    panel(c, x, y, w, h)
    first = data["historical_statements"][-5:][0]
    latest = data["historical_statements"][-1]
    first_year = int(first["year"])
    latest_year = int(latest["year"])
    # Keep the title compact: the date comparison is repeated in the visual and
    # insight, while the right-aligned filing label must remain unobstructed.
    section_title(c, x + 15, y + h - 19, w - 30, "03", "Margin evolution", str(data.get("filing_label", "Annual filing")))
    first_revenue = safe_float(first.get("revenue"))
    latest_revenue = safe_float(latest.get("revenue"))
    if first_revenue <= 0 or latest_revenue <= 0:
        raise ValueError("Multi-year margin view requires positive revenue")
    first_cfo = safe_float(first.get("cfo"))
    latest_cfo = safe_float(latest.get("cfo"))
    first_capex = optional_float(first.get("capex"))
    latest_capex = optional_float(latest.get("capex"))
    capex_available = bool(first_capex and latest_capex)
    first_fcf = first_cfo - abs(safe_float(first_capex))
    latest_fcf = latest_cfo - abs(safe_float(latest_capex))
    first_gross = optional_float(first.get("gross_profit"))
    latest_gross = optional_float(latest.get("gross_profit"))
    gross_available = bool(first_gross and latest_gross and first_gross > 0 and latest_gross > 0)
    first_primary = (first_gross / first_revenue) if gross_available else safe_float(first.get("pretax_income")) / first_revenue
    latest_primary = (latest_gross / latest_revenue) if gross_available else safe_float(latest.get("pretax_income")) / latest_revenue
    primary_label = "GROSS" if gross_available else "PRE-TAX"
    first_assets = safe_float(first.get("total_assets"))
    latest_assets = safe_float(latest.get("total_assets"))
    first_operating_raw = optional_float(first.get("operating_income"))
    latest_operating_raw = optional_float(latest.get("operating_income"))
    operating_available = (
        first_operating_raw is not None
        and latest_operating_raw is not None
        and not (first_operating_raw == 0 and safe_float(first.get("net_income")) != 0)
        and not (latest_operating_raw == 0 and safe_float(latest.get("net_income")) != 0)
    )
    if is_financial_issuer(data):
        metrics = [
            (primary_label, first_primary, latest_primary, BLUE),
            ("NET", safe_float(first.get("net_income")) / first_revenue, safe_float(latest.get("net_income")) / latest_revenue, PLUM),
            ("RETURN ON ASSETS", safe_float(first.get("net_income")) / first_assets if first_assets else 0.0, safe_float(latest.get("net_income")) / latest_assets if latest_assets else 0.0, TEAL),
            ("ASSET GROWTH", 0.0, latest_assets / first_assets - 1 if first_assets else 0.0, GOLD),
        ]
    else:
        second_metric = (
            "OPERATING",
            safe_float(first_operating_raw) / first_revenue,
            safe_float(latest_operating_raw) / latest_revenue,
            TEAL,
        ) if operating_available else (
            "RETURN ON ASSETS",
            safe_float(first.get("net_income")) / first_assets if first_assets else 0.0,
            safe_float(latest.get("net_income")) / latest_assets if latest_assets else 0.0,
            TEAL,
        )
        cash_metric = (
            "FREE CASH FLOW",
            first_fcf / first_revenue,
            latest_fcf / latest_revenue,
            GOLD,
        ) if capex_available else (
            "OPERATING CASH",
            first_cfo / first_revenue,
            latest_cfo / latest_revenue,
            GOLD,
        )
        metrics = [
            (primary_label, first_primary, latest_primary, BLUE),
            second_metric,
            ("NET", safe_float(first.get("net_income")) / first_revenue, safe_float(latest.get("net_income")) / latest_revenue, PLUM),
            cash_metric,
        ]
    plot_x, plot_w = x + 126, 160
    low, high, ticks = nice_axis([value for _, start, end, _ in metrics for value in (start, end)])
    c.setFillColor(MUTED)
    c.setFont("Mono", 5.9)
    c.setFillColor(SURFACE)
    c.setStrokeColor(MUTED)
    c.setLineWidth(1.4)
    c.circle(x + 78, y + h - 47, 3, stroke=1, fill=1)
    c.setFillColor(MUTED)
    c.drawString(x + 86, y + h - 49, fiscal_label(first_year))
    c.circle(x + 307, y + h - 47, 3.2, stroke=0, fill=1)
    c.drawString(x + 315, y + h - 49, fiscal_label(latest_year))
    for tick in ticks:
        tx = plot_x + plot_w * (tick - low) / (high - low)
        c.drawCentredString(tx, y + h - 49, pct(tick, 0))
        c.setStrokeColor(PALE)
        c.setLineWidth(0.6)
        c.line(tx, y + 58, tx, y + h - 57)
    top = y + h - 72
    for index, (label, start, end, color) in enumerate(metrics):
        row_y = top - index * 22
        start_x = plot_x + plot_w * (start - low) / (high - low)
        end_x = plot_x + plot_w * (end - low) / (high - low)
        c.setFillColor(MUTED)
        c.setFont("BodyBold", 7.2)
        c.drawString(x + 15, row_y - 2, label)
        c.setStrokeColor(color)
        c.setLineWidth(2)
        c.line(start_x, row_y, end_x, row_y)
        c.setFillColor(SURFACE)
        c.setStrokeColor(color)
        c.setLineWidth(2)
        c.circle(start_x, row_y, 4, stroke=1, fill=1)
        c.setFillColor(color)
        c.circle(end_x, row_y, 4.5, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont("BodyBold", 7.2)
        c.drawRightString(x + 113, row_y - 2.5, pct(start))
        c.drawString(x + 296, row_y - 2.5, pct(end))
        delta = (end - start) * 100
        c.setFillColor(TEAL if delta >= 0 else CORAL)
        c.setFont("BodyBold", 6.7)
        c.drawRightString(x + w - 14, row_y - 2.5, f"{delta:+.1f}pp")
    if is_financial_issuer(data):
        margin_insight = f"{primary_label.title()} margin moved {(latest_primary - first_primary) * 100:+.1f} points since {fiscal_label(first_year)}; industrial free-cash-flow margin is intentionally excluded."
    elif not capex_available:
        margin_insight = f"{primary_label.title()} margin moved {(latest_primary - first_primary) * 100:+.1f} points since {fiscal_label(first_year)}; operating-cash margin is shown because capex was not reported."
    else:
        margin_insight = f"{primary_label.title()} margin moved {(latest_primary - first_primary) * 100:+.1f} points since {fiscal_label(first_year)}; free-cash-flow margin moved {(latest_fcf / latest_revenue - first_fcf / first_revenue) * 100:+.1f} points."
    insight_band(
        c,
        x + 11,
        y + 9,
        w - 22,
        margin_insight,
    )


def cash_flow_chart(c: canvas.Canvas, data: dict[str, object], x: float, y: float, w: float, h: float) -> None:
    panel(c, x, y, w, h)
    latest = data["historical_statements"][-1]
    latest_year = int(latest["year"])
    financial = is_financial_issuer(data)
    capex_raw = optional_float(latest.get("capex"))
    capex_available = capex_raw is not None and capex_raw != 0
    chart_title = "Balance-sheet scale" if financial else "Cash conversion bridge" if capex_available else "Cash-flow data gate"
    section_title(c, x + 15, y + h - 19, w - 30, "04", chart_title, f"{fiscal_label(latest_year)} filing")
    if financial:
        values = [
            ("TOTAL ASSETS", optional_float(latest.get("total_assets")), NAVY),
            ("TOTAL EQUITY", optional_float(latest.get("equity")), TEAL),
            ("NET INCOME", optional_float(latest.get("net_income")), GOLD),
        ]
        card_w = 105
        for index, (label, value, color) in enumerate(values):
            bx = x + 24 + index * 124
            c.setFillColor(PALE)
            c.roundRect(bx, y + 63, card_w, 76, 6, stroke=0, fill=1)
            c.setFillColor(color)
            c.rect(bx, y + 63, 6, 76, stroke=0, fill=1)
            meaningful = value is not None and (value != 0 or label == "NET INCOME")
            display = (money_t(value) if abs(value) >= 1e12 else money_b(value)) if meaningful and value is not None else "N/A"
            value_font = text_font("Display", display)
            size = fit_text(c, display, value_font, 22, card_w - 20)
            c.setFont(value_font, size)
            c.drawString(bx + 14, y + 101, display)
            c.setFillColor(MUTED)
            c.setFont("Mono", 6.2)
            c.drawString(bx + 14, y + 82, label)
        insight_band(
            c,
            x + 11,
            y + 9,
            w - 22,
            "Bank balance-sheet scale is shown instead of industrial free cash flow, which is not a comparable decision metric for this issuer.",
        )
        return
    if not capex_available:
        cfo_value = optional_float(latest.get("cfo"))
        values = [
            ("OPERATING CASH", cfo_value, TEAL),
            ("CAPEX", None, CORAL),
            ("FREE CASH FLOW", None, GOLD),
        ]
        card_w = 105
        for index, (label, value, color) in enumerate(values):
            bx = x + 24 + index * 124
            c.setFillColor(PALE)
            c.roundRect(bx, y + 63, card_w, 76, 6, stroke=0, fill=1)
            c.setFillColor(color)
            c.rect(bx, y + 63, 6, 76, stroke=0, fill=1)
            display = money_b(value) if value is not None else "N/A"
            value_font = text_font("Display", display)
            size = fit_text(c, display, value_font, 22, card_w - 20)
            c.setFont(value_font, size)
            c.drawString(bx + 14, y + 101, display)
            c.setFillColor(MUTED)
            c.setFont("Mono", 6.2)
            c.drawString(bx + 14, y + 82, label)
        insight_band(
            c,
            x + 11,
            y + 9,
            w - 22,
            "Operating cash is shown, but capex and free cash flow are suppressed because the filing concept was not available.",
        )
        return
    cfo = safe_float(latest.get("cfo")) / _UNIT_DIVISOR
    capex = abs(safe_float(capex_raw)) / _UNIT_DIVISOR
    fcf = cfo - capex
    baseline = y + 59
    maximum_h = 74
    bar_w = 61
    positions = [x + 54, x + 174, x + 294]
    if cfo > 0 and fcf >= 0:
        cfo_h = maximum_h
        fcf_h = maximum_h * fcf / cfo
        bars = [
            (positions[0], baseline, cfo_h, TEAL, signed_money_b(cfo), "OPERATING CASH"),
            (positions[1], baseline + fcf_h, cfo_h - fcf_h, CORAL, signed_money_b(-capex), "CAPEX"),
            (positions[2], baseline, fcf_h, GOLD, signed_money_b(fcf), "FREE CASH FLOW"),
        ]
        for index, (bx, by, bh, color, value_label, label) in enumerate(bars):
            c.setFillColor(color)
            c.roundRect(bx, by, bar_w, max(bh, 1.5), 4, stroke=0, fill=1)
            c.setFillColor(INK)
            c.setFont(text_font("BodyBold", value_label), 9.2)
            c.drawCentredString(bx + bar_w / 2, by + bh + 8, value_label)
            c.setFillColor(MUTED)
            c.setFont("Mono", 5.8)
            c.drawCentredString(bx + bar_w / 2, baseline - 12, label)
            if index < 2:
                connector_y = baseline + (cfo_h if index == 0 else fcf_h)
                c.setStrokeColor(LINE)
                c.setDash(3, 2)
                c.setLineWidth(1)
                c.line(bx + bar_w, connector_y, positions[index + 1], connector_y)
                c.setDash()
    else:
        zero_y = y + 97
        values = [(cfo, TEAL, "OPERATING CASH"), (-capex, CORAL, "CAPEX"), (fcf, GOLD, "FREE CASH FLOW")]
        max_abs = max(abs(value) for value, _, _ in values) or 1.0
        c.setStrokeColor(LINE)
        c.setLineWidth(0.8)
        c.line(x + 30, zero_y, x + w - 30, zero_y)
        for bx, (value, color, label) in zip(positions, values):
            bar_h = 45 * abs(value) / max_abs
            bar_y = zero_y if value >= 0 else zero_y - bar_h
            c.setFillColor(color if value >= 0 else CORAL)
            c.roundRect(bx, bar_y, bar_w, max(bar_h, 1.5), 4, stroke=0, fill=1)
            c.setFillColor(INK)
            value_label = signed_money_b(value)
            c.setFont(text_font("BodyBold", value_label), 9.2)
            c.drawCentredString(bx + bar_w / 2, y + 151, value_label)
            c.setFillColor(MUTED)
            c.setFont("Mono", 5.8)
            c.drawCentredString(bx + bar_w / 2, y + 48, label)
    if cfo:
        cash_insight = f"{signed_money_b(cfo)} of operating cash less {signed_money_b(capex)} of capex produced {signed_money_b(fcf)} of FCF, a {fcf / cfo * 100:.1f}% conversion rate."
    else:
        cash_insight = f"Operating cash was {signed_money_b(cfo)}; after {signed_money_b(capex)} of capex, free cash flow was {signed_money_b(fcf)}."
    insight_band(
        c,
        x + 11,
        y + 9,
        w - 22,
        cash_insight,
    )


def ratios_panel(c: canvas.Canvas, data: dict[str, object], x: float, y: float, w: float, h: float) -> None:
    panel(c, x, y, w, h)
    latest = data["historical_statements"][-1]
    latest_year = int(latest["year"])
    dated = data.get("price_mode") == "dated"
    quote_date = str(data.get("quote_date") or "")
    pe_label = f"{quote_date} PRICE / {fiscal_label(latest_year)} EPS" if dated else f"PRICE / {fiscal_label(latest_year)} EPS"
    section_title(c, x + 15, y + h - 19, w - 30, "05", "Decision ratios", f"{fiscal_label(latest_year)} / 10Y")
    price = safe_float(data.get("current_price"))
    shares = safe_float(latest.get("shares"), 0.0)
    eps = safe_float(latest.get("eps_diluted"), 0.0)
    if eps == 0 and shares > 0:
        eps = safe_float(latest.get("net_income"), 0.0) / shares
    financial = is_financial_issuer(data)
    if financial:
        groups = [
            (
                "SCALE / RETURNS",
                [
                    ("ASSET CAGR", optional_pct(data["ratios"].get("asset_cagr")), TEAL),
                    ("EPS CAGR", optional_pct(data["ratios"].get("eps_cagr")), GOLD),
                    ("RETURN ON ASSETS", optional_pct(data["ratios"].get("roa")), BLUE),
                ],
            ),
            (
                "CAPITAL / PRICE",
                [
                    ("EQUITY / ASSETS", optional_pct(data["ratios"].get("equity_to_assets")), CORAL),
                    ("SHARE COUNT CAGR", optional_pct(data["ratios"].get("shares_cagr")), PLUM),
                    (pe_label, f"{price / eps:.1f}x" if price > 0 and eps > 0 else "N/A", NAVY),
                ],
            ),
        ]
    else:
        groups = [
            (
                "COMPOUNDING / RETURNS",
                [
                    ("REVENUE CAGR", optional_pct(data["ratios"].get("revenue_cagr")), TEAL),
                    ("EPS CAGR", optional_pct(data["ratios"].get("eps_cagr")), GOLD),
                    ("RETURN ON ASSETS", optional_pct(data["ratios"].get("roa")), BLUE),
                ],
            ),
            (
                "BALANCE / PRICE",
                [
                    ("DEBT / EQUITY", optional_multiple(data["ratios"].get("debt_to_equity")), CORAL),
                    ("CURRENT RATIO", optional_multiple(data["ratios"].get("current_ratio")), PLUM),
                    (pe_label, f"{price / eps:.1f}x" if price > 0 and eps > 0 else "N/A", NAVY),
                ],
            ),
        ]
    group_w = (w - 48) / 2
    for group_index, (group_label, values) in enumerate(groups):
        gx = x + 15 + group_index * (group_w + 18)
        c.setFillColor(TEAL)
        c.setFont("Mono", 6.2)
        c.drawString(gx, y + h - 50, group_label)
        for row_index, (label, value, color) in enumerate(values):
            row_y = y + h - 76 - row_index * 28
            c.setFillColor(color)
            c.circle(gx + 3, row_y + 5, 3, stroke=0, fill=1)
            c.setFillColor(MUTED)
            c.setFont("Mono", 5.8)
            c.drawString(gx + 12, row_y + 2, label)
            c.setFillColor(INK)
            c.setFont("Display", 15.5)
            c.drawRightString(gx + group_w, row_y - 1, value)
            c.setStrokeColor(PALE)
            c.setLineWidth(0.7)
            c.line(gx, row_y - 8, gx + group_w, row_y - 8)
    eps_cagr = optional_pct(data["ratios"].get("eps_cagr"))
    previous_shares = safe_float(data["historical_statements"][-2].get("shares"))
    latest_shares = safe_float(data["historical_statements"][-1].get("shares"))
    shares_change = f"{(latest_shares / previous_shares - 1) * 100:+.1f}%" if previous_shares > 0 else "N/A"
    if financial:
        ratio_insight = (
            f"Asset CAGR is {optional_pct(data['ratios'].get('asset_cagr'))}; EPS CAGR is {eps_cagr}. "
            f"Equity funds {optional_pct(data['ratios'].get('equity_to_assets'))} of assets; diluted shares changed {shares_change} in {fiscal_label(latest_year)}."
        )
    else:
        ratio_insight = (
            f"Revenue CAGR is {optional_pct(data['ratios'].get('revenue_cagr'))}; EPS CAGR is {eps_cagr}. "
            f"Diluted shares changed {shares_change} in {fiscal_label(latest_year)}."
        )
    insight_band(
        c,
        x + 11,
        y + 9,
        w - 22,
        ratio_insight,
    )


def dcf_range_panel(c: canvas.Canvas, data: dict[str, object], x: float, y: float, w: float, h: float) -> None:
    panel(c, x, y, w, h)
    section_title(c, x + 13, y + h - 18, w - 26, "06", "DCF range", "Model")
    scenario = data["scenarios"]
    markers = [
        ("BEAR", float(scenario["bear"]["per_share"]), CORAL),
        ("BASE", float(scenario["base"]["per_share"]), TEAL),
        ("BULL", float(scenario["bull"]["per_share"]), GOLD),
        ("MARKET", float(data["current_price"]), NAVY),
    ]
    maximum = max(value for _, value, _ in markers) * 1.08
    axis_x, axis_y, axis_w = x + 16, y + 105, w - 32
    c.setStrokeColor(LINE)
    c.setLineWidth(5)
    c.line(axis_x, axis_y, axis_x + axis_w, axis_y)
    low = float(scenario["range_low"])
    high = float(scenario["range_high"])
    c.setStrokeColor(MINT)
    c.setLineWidth(10)
    c.line(axis_x + axis_w * low / maximum, axis_y, axis_x + axis_w * high / maximum, axis_y)
    for index, (label, value, color) in enumerate(markers):
        px = axis_x + axis_w * value / maximum
        marker_text = f"{label} {axis_per_share(value)}"
        c.setFillColor(color)
        c.circle(px, axis_y, 5, stroke=0, fill=1)
        c.setFont(text_font("Mono", marker_text), 4.8)
        label_y = axis_y + 17 + (index % 2) * 20
        c.drawCentredString(px, label_y, marker_text)
    c.setFillColor(INK)
    range_text = per_share_range(low, high)
    c.setFont(text_font("Display", range_text), 24)
    c.drawString(x + 14, y + h - 61, range_text)
    c.setFillColor(MUTED)
    c.setFont("Body", 6.2)
    c.drawString(x + 14, y + h - 78, "Bear/base/bull model span")
    assumptions = data["dcf"]["assumptions"]
    c.setFont("Mono", 5.3)
    c.drawString(x + 14, y + 20, f"WACC {pct(float(assumptions['wacc']))}  |  TG {pct(float(assumptions['terminal_growth']))}")
    c.setFont("Body", 5.6)
    c.drawString(x + 14, y + 9, "Model output, not a price target.")


def heatmap_panel(c: canvas.Canvas, data: dict[str, object], x: float, y: float, w: float, h: float) -> None:
    panel(c, x, y, w, h)
    section_title(c, x + 13, y + h - 18, w - 26, "07", "DCF sensitivity", "WACC x TG")
    grid = data["dcf"]["sensitivity_grid"]
    row_keys = sorted(grid, key=float)
    if len(row_keys) > 5:
        center = min(range(len(row_keys)), key=lambda index: abs(float(row_keys[index]) - float(data["dcf"]["assumptions"]["wacc"])))
        start = max(0, min(center - 2, len(row_keys) - 5))
        row_keys = row_keys[start : start + 5]
    col_keys = sorted(next(iter(grid.values())), key=float)
    values = [float(grid[row][column]) for row in row_keys for column in col_keys]
    low, high = min(values), max(values)
    palette = [HexColor(value) for value in ("#F3D8CE", "#F1E3C5", "#DDEAD5", "#B9D9C8", "#79B8A5")]
    cell_w = (w - 58) / len(col_keys)
    cell_h = 25
    origin_x, origin_y = x + 39, y + 51
    for row_index, row in enumerate(reversed(row_keys)):
        for col_index, column in enumerate(col_keys):
            value = float(grid[row][column])
            bucket = min(4, int((value - low) / max(1e-9, high - low) * 4.99))
            cx = origin_x + col_index * cell_w
            cy = origin_y + row_index * cell_h
            c.setFillColor(palette[bucket])
            c.rect(cx, cy, cell_w - 2, cell_h - 2, stroke=0, fill=1)
            c.setFillColor(INK)
            cell_text = axis_per_share(value)
            c.setFont(text_font("BodyBold", cell_text), 5.8)
            c.drawCentredString(cx + (cell_w - 2) / 2, cy + 8, cell_text)
        c.setFillColor(MUTED)
        c.setFont("Mono", 4.8)
        c.drawRightString(origin_x - 5, origin_y + row_index * cell_h + 8, pct(float(row)))
    for col_index, column in enumerate(col_keys):
        c.setFillColor(MUTED)
        c.setFont("Mono", 4.8)
        c.drawCentredString(origin_x + col_index * cell_w + (cell_w - 2) / 2, origin_y - 10, pct(float(column)))
    c.setFillColor(MUTED)
    c.setFont("Body", 5.5)
    c.drawString(x + 13, y + 12, "Rows: WACC. Columns: terminal growth. Values: per share.")


def expectation_panel(c: canvas.Canvas, data: dict[str, object], x: float, y: float, w: float, h: float) -> None:
    panel(c, x, y, w, h)
    section_title(c, x + 13, y + h - 18, w - 26, "08", "Expectation gap", "Reverse DCF")
    values = [
        ("HISTORY", float(data["ratios"]["revenue_cagr"]), TEAL),
        ("BASE", float(data["dcf"]["assumptions"]["rev_growth"]), GOLD),
        ("IMPLIED", float(data["reverse_dcf"]["implied_growth"]), CORAL),
    ]
    maximum = max(value for _, value, _ in values) * 1.15
    baseline = y + 49
    chart_h = h - 105
    positions = [x + 36, x + 94, x + 152]
    for (label, value, color), cx in zip(values, positions):
        height = chart_h * value / maximum
        c.setStrokeColor(color)
        c.setLineWidth(5)
        c.line(cx, baseline, cx, baseline + height)
        c.setFillColor(color)
        c.circle(cx, baseline + height, 6, stroke=0, fill=1)
        c.setFont("BodyBold", 8)
        c.drawCentredString(cx, baseline + height + 12, pct(value))
        c.setFillColor(MUTED)
        c.setFont("Mono", 5)
        c.drawCentredString(cx, baseline - 14, label)
    ratio = float(data["reverse_dcf"]["implied_growth"]) / float(data["ratios"]["revenue_cagr"])
    c.setFillColor(INK)
    c.setFont("Display", 20)
    c.drawString(x + 13, y + h - 61, f"{ratio:.1f}x history")
    c.setFillColor(MUTED)
    c.setFont("Body", 5.6)
    c.drawString(x + 13, y + 12, "The current price requires materially faster sales growth in this model.")


def unavailable_valuation_panel(
    c: canvas.Canvas,
    data: dict[str, object],
    x: float,
    y: float,
    w: float,
    h: float,
    reason: str,
) -> None:
    panel(c, x, y, w, h)
    section_title(
        c,
        x + 15,
        y + h - 20,
        w - 30,
        "06",
        "Valuation: comparability gate",
        "Fail-closed model",
    )
    labels = (
        ("06A / SCENARIO RANGE", "SUPPRESSED", "No per-share range is shown."),
        ("06B / DCF SENSITIVITY", "SUPPRESSED", "No precision is implied."),
        ("06C / REVERSE DCF", "SUPPRESSED", "No growth hurdle is shown."),
    )
    gap = 14
    card_w = (w - 28 - 2 * gap) / 3
    for index, (label, value, note) in enumerate(labels):
        bx = x + 14 + index * (card_w + gap)
        c.setFillColor(PALE)
        c.roundRect(bx, y + 62, card_w, 82, 6, stroke=0, fill=1)
        c.setFillColor(TEAL)
        c.setFont("Mono", 6.2)
        c.drawString(bx + 11, y + 129, label)
        c.setFillColor(INK)
        c.setFont("Display", 21)
        c.drawString(bx + 11, y + 94, value)
        c.setFillColor(MUTED)
        c.setFont("Body", 7)
        c.drawString(bx + 11, y + 76, note)
    insight_band(c, x + 14, y + 10, w - 28, reason + " The dashboard fails closed rather than publishing misleading precision.")


def valuation_panel(c: canvas.Canvas, data: dict[str, object], x: float, y: float, w: float, h: float) -> None:
    """Draw one connected valuation narrative with three fully labelled visuals."""
    comparable, reason = valuation_is_comparable(data)
    if not comparable:
        unavailable_valuation_panel(c, data, x, y, w, h, reason)
        return
    dated = data.get("price_mode") == "dated"
    quote_date = str(data.get("quote_date") or "")
    quote_noun = f"the {quote_date} quote" if dated else "the current quote"
    quote_box_label = f"CLOSE {quote_date}" if dated else "MARKET QUOTE"
    panel(c, x, y, w, h)
    section_title(
        c,
        x + 15,
        y + h - 20,
        w - 30,
        "06",
        "Valuation: model range, sensitivity and expectations in price",
        "DCF + reverse DCF",
    )
    gap = 14
    inner_x = x + 14
    usable_w = w - 28 - 2 * gap
    left_w = 275
    center_w = 280
    right_w = usable_w - left_w - center_w
    center_x = inner_x + left_w + gap
    right_x = center_x + center_w + gap
    dividers = [inner_x + left_w + gap / 2, center_x + center_w + gap / 2]
    for divider in dividers:
        c.setStrokeColor(LINE)
        c.setLineWidth(0.7)
        c.line(divider, y + 57, divider, y + h - 45)

    # 06A — scenario range against the observed market quote.
    left_x = inner_x
    scenario = data["scenarios"]
    low = float(scenario["range_low"])
    base = float(scenario["base"]["per_share"])
    high = float(scenario["range_high"])
    market = float(data["current_price"])
    c.setFillColor(TEAL)
    c.setFont("Mono", 6.2)
    c.drawString(left_x, y + h - 49, "06A / SCENARIO RANGE")
    c.setFillColor(INK)
    range_label = per_share_range(low, high)
    range_face = text_font("Display", range_label)
    range_max = left_w - 88 if _CURRENCY == "INR" else left_w - 10
    range_start_size = 24 if _CURRENCY == "INR" else 30
    range_font = fit_text(c, range_label, range_face, range_start_size, range_max)
    c.setFont(range_face, range_font)
    c.drawString(left_x, y + h - 84, range_label)
    c.setFillColor(MUTED)
    c.setFont("Mono", 5.9)
    c.drawString(left_x, y + h - 97, "DCF SCENARIO RANGE / PER SHARE")
    axis_x, axis_y, axis_w = left_x + 3, y + 91, left_w - 91
    scale_low, scale_high, _ = nice_axis([low, base, high], target_intervals=4)
    c.setStrokeColor(PALE)
    c.setLineWidth(5)
    c.line(axis_x, axis_y, axis_x + axis_w, axis_y)
    c.setStrokeColor(MINT)
    c.setLineWidth(11)
    low_x = axis_x + axis_w * (low - scale_low) / (scale_high - scale_low)
    high_x = axis_x + axis_w * (high - scale_low) / (scale_high - scale_low)
    c.line(low_x, axis_y, high_x, axis_y)
    for label, value, color, offset in (
        ("BEAR", low, CORAL, -17),
        ("BASE", base, TEAL, 12),
        ("BULL", high, GOLD, -17),
    ):
        px = axis_x + axis_w * (value - scale_low) / (scale_high - scale_low)
        marker_text = f"{label} {axis_per_share(value)}"
        c.setFillColor(color)
        c.circle(px, axis_y, 5.2, stroke=0, fill=1)
        c.setFont(text_font("BodyBold", marker_text), 5.9)
        if label == "BULL" and _CURRENCY == "INR":
            c.drawRightString(px - 3, axis_y + offset, marker_text)
        else:
            c.drawCentredString(px, axis_y + offset, marker_text)
    market_x, market_y = left_x + left_w - 76, y + 74
    c.setFillColor(NAVY)
    c.roundRect(market_x, market_y, 72, 39, 6, stroke=0, fill=1)
    c.setFillColor(SURFACE)
    market_text = money_per_share(market)
    c.setFont(text_font("Display", market_text), 18)
    c.drawCentredString(market_x + 36, market_y + 15, market_text)
    c.setFillColor(GOLD if dated else MINT)
    c.setFont("Mono", fit_text(c, quote_box_label, "Mono", 5.2, 66))
    c.drawCentredString(market_x + 36, market_y + 5, quote_box_label)
    c.setFillColor(CORAL)
    c.setFont("BodyBold", 6.2)
    market_to_high = f"{market / high:.1f}x BULL CASE" if high else "BULL CASE N/A"
    c.drawCentredString(market_x + 36, market_y + 46, market_to_high)
    c.setStrokeColor(LINE)
    c.setDash(3, 2)
    c.setLineWidth(1)
    c.line(high_x + 6, axis_y, market_x - 5, market_y + 19)
    c.setDash()
    quote_phrase = f"the {quote_date} quote" if dated else "the market quote"
    quote_phrase_cap = f"The {quote_date} quote" if dated else "The market quote"
    if high < market:
        scenario_insight = f"The {per_share_range(low, high)} model span sits below {quote_phrase} of {money_per_share(market)}. This is an assumption gap, not a verdict."
    elif low > market:
        scenario_insight = f"The {per_share_range(low, high)} model span sits above {quote_phrase} of {money_per_share(market)}. This is an assumption gap, not a verdict."
    else:
        scenario_insight = f"{quote_phrase_cap} of {money_per_share(market)} sits inside the {per_share_range(low, high)} model span. The range reflects assumption sensitivity, not a verdict."
    mini_insight(
        c,
        left_x,
        y + 8,
        left_w,
        scenario_insight,
    )

    # 06B — 25-cell sensitivity grid with complete data labels.
    c.setFillColor(TEAL)
    c.setFont("Mono", 6.2)
    c.drawString(center_x, y + h - 49, "06B / WACC × TERMINAL GROWTH")
    grid = data["dcf"]["sensitivity_grid"]
    row_keys = sorted(grid, key=float)
    if len(row_keys) > 5:
        center_index = min(
            range(len(row_keys)),
            key=lambda index: abs(float(row_keys[index]) - float(data["dcf"]["assumptions"]["wacc"])),
        )
        start = max(0, min(center_index - 2, len(row_keys) - 5))
        row_keys = row_keys[start : start + 5]
    col_keys = sorted(next(iter(grid.values())), key=float)
    grid_values = [float(grid[row][column]) for row in row_keys for column in col_keys]
    grid_low, grid_high = min(grid_values), max(grid_values)
    palette = [HexColor(value) for value in ("#F3D8CE", "#F1E3C5", "#DDEAD5", "#B9D9C8", "#79B8A5")]
    grid_x, grid_y = center_x + 31, y + 72
    cell_w = (center_w - 34) / 5
    cell_h = 15.5
    for row_index, row in enumerate(reversed(row_keys)):
        for column_index, column in enumerate(col_keys):
            value = float(grid[row][column])
            bucket = min(4, int((value - grid_low) / max(1e-9, grid_high - grid_low) * 4.99))
            cx = grid_x + column_index * cell_w
            cy = grid_y + row_index * cell_h
            c.setFillColor(palette[bucket])
            c.rect(cx, cy, cell_w - 2, cell_h - 2, stroke=0, fill=1)
            c.setFillColor(INK)
            cell_text = axis_per_share(value)
            c.setFont(text_font("BodyBold", cell_text), 6.1)
            c.drawCentredString(cx + (cell_w - 2) / 2, cy + 7, cell_text)
        c.setFillColor(MUTED)
        c.setFont("Mono", 5.1)
        c.drawRightString(grid_x - 5, grid_y + row_index * cell_h + 7, pct(float(row)))
    for column_index, column in enumerate(col_keys):
        c.setFillColor(MUTED)
        c.setFont("Mono", 5.1)
        c.drawCentredString(grid_x + column_index * cell_w + (cell_w - 2) / 2, grid_y - 10, pct(float(column)))
    if grid_high < market:
        sensitivity_insight = f"Across all 25 displayed cases, value ranges from {money_per_share(grid_low)} to {money_per_share(grid_high)}; no cell reaches {quote_phrase}."
    elif grid_low > market:
        sensitivity_insight = f"Across all 25 displayed cases, value ranges from {money_per_share(grid_low)} to {money_per_share(grid_high)}; every cell exceeds {quote_phrase}."
    else:
        sensitivity_insight = f"{quote_phrase_cap} of {money_per_share(market)} falls inside the {per_share_range(grid_low, grid_high)} sensitivity span; assumptions drive the result."
    mini_insight(
        c,
        center_x,
        y + 8,
        center_w,
        sensitivity_insight,
    )

    # 06C — historical, base-case and price-implied revenue growth.
    c.setFillColor(TEAL)
    c.setFont("Mono", 6.2)
    c.drawString(right_x, y + h - 49, "06C / REVENUE GROWTH REQUIRED")
    growth_values = [
        ("HISTORY", safe_float(data["ratios"].get("revenue_cagr")), TEAL),
        ("BASE", safe_float(data["dcf"]["assumptions"].get("rev_growth")), GOLD),
        ("IMPLIED", safe_float(data.get("reverse_dcf", {}).get("implied_growth")), CORAL),
    ]
    growth_low, growth_high, _ = nice_axis([0.0] + [value for _, value, _ in growth_values], target_intervals=3)
    chart_h = 68
    chart_bottom = y + 72
    baseline = chart_bottom + chart_h * (0.0 - growth_low) / max(growth_high - growth_low, 1e-9)
    positions = [right_x + right_w * fraction for fraction in (0.18, 0.50, 0.82)]
    for (label, value, color), cx in zip(growth_values, positions):
        value_y = chart_bottom + chart_h * (value - growth_low) / max(growth_high - growth_low, 1e-9)
        c.setStrokeColor(color)
        c.setLineWidth(7)
        c.line(cx, baseline, cx, value_y)
        c.setFillColor(color)
        c.circle(cx, value_y, 7, stroke=0, fill=1)
        c.setFont("BodyBold", 9)
        label_y = value_y + 12 if value >= 0 else value_y - 18
        c.drawCentredString(cx, label_y, pct(value))
        c.setFillColor(MUTED)
        c.setFont("Mono", 5.5)
        c.drawCentredString(cx, baseline - 12, label)
    implied = growth_values[-1][1]
    history = growth_values[0][1]
    base_growth = growth_values[1][1]
    history_multiple = f"{implied / history:.1f}x history" if history else "history comparison N/A"
    base_multiple = f"{implied / base_growth:.1f}x the base case" if base_growth else "base comparison N/A"
    if dated:
        history_part = f"{implied / history:.1f}x history" if history else "history comparison N/A"
        base_part = f"{implied / base_growth:.1f}x base" if base_growth else "base comparison N/A"
        growth_sentence = f"{quote_date} price implied {pct(implied)} growth: {history_part}, {base_part}."
    else:
        growth_sentence = f"Reverse DCF implies {pct(implied)} revenue growth: {history_multiple} and {base_multiple}."
    mini_insight(
        c,
        right_x,
        y + 8,
        right_w,
        growth_sentence,
    )


def render(sidecar: Path, market_history_path: Path, pdf_path: Path, png_path: Path, quote_as_of: str) -> None:
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    market_history = json.loads(market_history_path.read_text(encoding="utf-8"))
    configure_market(data)
    if market_history["ticker"] != data["ticker"]:
        raise ValueError("Sidecar and market-history tickers do not match")
    if len(data.get("historical_statements", [])) < 4:
        raise ValueError("At least four comparable annual statements are required")
    current_price = safe_float(data.get("current_price"))
    if current_price <= 0:
        raise ValueError("A positive timestamped market quote is required")
    latest = data["historical_statements"][-1]
    previous = data["historical_statements"][-2]
    previous_revenue = safe_float(previous.get("revenue"))
    previous_profit = safe_float(previous.get("net_income"))
    previous_shares = safe_float(previous.get("shares"))
    revenue_yoy = safe_float(latest.get("revenue")) / previous_revenue - 1 if previous_revenue else None
    profit_yoy = safe_float(latest.get("net_income")) / previous_profit - 1 if previous_profit else None
    shares_yoy = safe_float(latest.get("shares")) / previous_shares - 1 if previous_shares else None
    cfo = safe_float(latest.get("cfo"))
    latest_capex = optional_float(latest.get("capex"))
    capex_available = latest_capex is not None and latest_capex != 0
    fcf = cfo - abs(safe_float(latest_capex))
    comparable_valuation, _ = valuation_is_comparable(data)
    financial = is_financial_issuer(data)
    fcf_conversion = fcf / cfo if cfo and not financial and capex_available else None
    historical_growth = safe_float(data["ratios"].get("revenue_cagr"))
    implied_growth = safe_float(data.get("reverse_dcf", {}).get("implied_growth"))
    implied_multiple = implied_growth / historical_growth if historical_growth and comparable_valuation else None

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    register_fonts()
    c = canvas.Canvas(str(pdf_path), pagesize=(PAGE_W, PAGE_H), invariant=1)
    c.setTitle(f"{data.get('display_ticker', data['ticker'])} Investor Decision Dashboard")
    c.setAuthor("stockcentric")

    dated = data.get("price_mode") == "dated"
    quote_date = str(data.get("quote_date") or "")
    as_of_requested = data.get("as_of_requested")
    accent = GOLD if dated else MINT

    c.setFillColor(BG)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    c.setFillColor(NAVY)
    c.rect(0, 972, PAGE_W, 153, stroke=0, fill=1)
    c.setFillColor(accent)
    c.rect(0, 967, PAGE_W, 5, stroke=0, fill=1)
    c.setFillColor(MINT)
    c.setFont("Mono", 9)
    c.drawString(MARGIN, 1091, "STOCKCENTRIC / INVESTOR DECISION DASHBOARD")
    if dated:
        pill_text = f"DATED PRICE {quote_date} · NOT A POINT-IN-TIME VIEW"
        pill_font_size = fit_text(c, pill_text, "Mono", 7.0, 245)
        pill_w = pdfmetrics.stringWidth(pill_text, "Mono", pill_font_size) + 16
        pill_x = PAGE_W - MARGIN - pill_w
        c.setFillColor(GOLD)
        c.roundRect(pill_x, 1082, pill_w, 13, 6.5, stroke=0, fill=1)
        c.setFillColor(NAVY)
        c.setFont("Mono", pill_font_size)
        c.drawCentredString(pill_x + pill_w / 2, 1085.7, pill_text)
    c.setFillColor(SURFACE)
    display_ticker = str(data.get("display_ticker") or data["ticker"])
    ticker_font = fit_text(c, display_ticker, "Display", 70, 138)
    c.setFont("Display", ticker_font)
    c.drawString(MARGIN, 1010, display_ticker)
    quote_text = f"{_SYMBOL}{current_price:,.2f}"
    company_label, company_font, quote_face, quote_size, _ = header_company_layout(
        str(data["company_name"]), quote_text
    )
    c.setFont("BodyBold", company_font)
    c.drawString(HEADER_COMPANY_X, 1040, company_label)
    c.setFillColor(MINT)
    c.setFont("BodyBold", 9)
    hindsight = data.get("hindsight") or {}
    periods_after_quote = int(hindsight.get("statement_periods_after_quote") or 0)
    hindsight_clause = f"  ·  {periods_after_quote} FY AFTER QUOTE" if dated and periods_after_quote else ""
    filing_line = (
        f"{data.get('exchange', 'US')} / {data.get('currency', 'USD')}  |  "
        f"FILINGS THROUGH FY{latest['year']}{hindsight_clause}  |  MODEL BUILT {data['generated_at'][:10]}"
    )
    c.setFont("BodyBold", fit_text(c, filing_line, "BodyBold", 9, 415))
    c.drawString(196, 1016, filing_line)
    c.setFillColor(SURFACE)
    profit_label = yoy_label(safe_float(latest.get("net_income")), previous_profit)
    conversion_label = f"{fcf_conversion * 100:.1f}%" if fcf_conversion is not None else "N/A"
    implied_label = f"{implied_multiple:.1f}x" if implied_multiple is not None else "N/A"
    if profit_label == "TO PROFIT":
        earnings_headline = "EARNINGS TURNED POSITIVE"
    elif profit_label == "TO LOSS":
        earnings_headline = "EARNINGS TURNED NEGATIVE"
    elif profit_label.startswith("LOSS"):
        earnings_headline = f"NET {profit_label}"
    else:
        earnings_headline = f"PROFIT {profit_label}"
    price_verb = "IMPLIED" if dated else "IMPLIES"
    secondary_headline = (
        f"{earnings_headline}  -  FCF CONVERSION {conversion_label}  -  "
        f"PRICE {price_verb} {implied_label} HISTORICAL GROWTH"
    )
    c.setFont(
        "BodyBold",
        fit_text(None, secondary_headline, "BodyBold", 8.6, HEADER_SECONDARY_MAX_WIDTH),
    )
    c.drawString(196, 988, secondary_headline)

    c.setFillColor(GOLD if dated else SURFACE)
    c.setFont(quote_face, quote_size)
    c.drawRightString(PAGE_W - MARGIN, 1037, quote_text)
    c.setFillColor(accent)
    if dated and as_of_requested and as_of_requested != quote_date:
        quote_label = f"CLOSE {quote_date} · REQUESTED {as_of_requested}"
    elif dated:
        quote_label = f"CLOSE {quote_date} · DATED PRICE"
    else:
        quote_label = f"MARKET QUOTE {quote_as_of}"
    c.setFont("Mono", fit_text(c, quote_label, "Mono", 7.2, HEADER_QUOTE_MAX_WIDTH))
    c.drawRightString(PAGE_W - MARGIN, 1013, quote_label)
    market_cap = safe_float(data.get("wacc", {}).get("market_cap"))
    market_cap_text = money_t(market_cap) if market_cap > 0 else "N/A"
    c.setFillColor(GOLD if dated else SURFACE)
    c.setFont(text_font("BodyBold", market_cap_text), 12)
    c.drawRightString(PAGE_W - MARGIN, 987, market_cap_text)
    c.setFillColor(accent)
    c.setFont("Mono", 6.3)
    cap_label = f"MARKET CAP · DATED PRICE × {fiscal_label(latest['year'])} SHARES" if dated else "MARKET CAPITALIZATION"
    c.drawRightString(PAGE_W - MARGIN, 976, cap_label)

    ribbon_y = 916
    ribbon_items = [
        ("REVENUE", pct(revenue_yoy) if revenue_yoy is not None else "N/A", TEAL),
        ("NET INCOME", profit_label, GOLD),
        ("DILUTED SHARES", pct(shares_yoy) if shares_yoy is not None else "N/A", PLUM),
        ("FCF MARGIN", "N/A" if financial or not capex_available else optional_pct(data["ratios"].get("fcf_margin")), BLUE),
    ]
    item_w = (PAGE_W - 2 * MARGIN) / len(ribbon_items)
    for index, (label, value, color) in enumerate(ribbon_items):
        rx = MARGIN + index * item_w
        c.setFillColor(color)
        c.rect(rx, ribbon_y, 7, 42, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont("Display", 22)
        c.drawString(rx + 16, ribbon_y + 15, value)
        c.setFillColor(MUTED)
        c.setFont("Mono", 6.8)
        c.drawString(rx + 16, ribbon_y + 2, f"{label} YOY" if label != "FCF MARGIN" else label)

    line_chart(c, data, market_history, 36, 683, 828, 225)

    row_w, row_h, row_gap = 408, 190, 12
    revenue_profit_chart(c, data, 36, 478, row_w, row_h)
    margin_chart(c, data, 36 + row_w + row_gap, 478, row_w, row_h)

    cash_flow_chart(c, data, 36, 273, row_w, row_h)
    ratios_panel(c, data, 36 + row_w + row_gap, 273, row_w, row_h)

    valuation_panel(c, data, 36, 61, 828, 197)

    c.setStrokeColor(LINE)
    c.setLineWidth(0.8)
    c.line(MARGIN, 49, PAGE_W - MARGIN, 49)
    c.setFillColor(MUTED)
    c.setFont("Body", 5.9)
    filing_label = str(data.get("filing_label") or "Annual filing")
    source_label = str(data.get("source") or market_history.get("source") or "declared research source")
    quote_source_phrase = f"split-adjusted close on {quote_date}" if dated else "timestamped quote"
    c.drawString(MARGIN, 36, f"Sources: {data['company_name']} FY{latest['year']} {filing_label}; {source_label} adjusted history and {quote_source_phrase}; deterministic model sidecar.")
    assumptions = data.get("dcf", {}).get("assumptions", {})
    c.drawString(
        MARGIN,
        25,
        f"Facts, market observations and model outputs are separated. Assumptions: {optional_pct(assumptions.get('wacc'))} WACC, {optional_pct(assumptions.get('terminal_growth'))} terminal growth, {int(safe_float(assumptions.get('n_years'), 10))}-year horizon. Annual price references use nearest year-end month.",
    )
    c.setFillColor(INK)
    c.setFont("BodyBold", 6.2)
    c.drawString(MARGIN, 11, "Research output — not a recommendation. The reader decides whether to act.")
    c.setFillColor(TEAL)
    c.setFont("Mono", 5.8)
    c.drawRightString(PAGE_W - MARGIN, 11, "STOCKCENTRIC / STOCK-ANALYSIS-BRIEF / 01")
    c.showPage()
    c.save()

    document = fitz.open(pdf_path)
    page = document[0]
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(EXPORT_W / PAGE_W, EXPORT_H / PAGE_H), alpha=False
    )
    pixmap.save(png_path)
    document.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sidecar", type=Path)
    parser.add_argument("--market-history", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--png", type=Path, default=DEFAULT_PNG)
    parser.add_argument("--quote-as-of", default="2026-07-21 00:15 UTC")
    args = parser.parse_args()
    render(
        args.sidecar.resolve(),
        args.market_history.resolve(),
        args.pdf.resolve(),
        args.png.resolve(),
        args.quote_as_of,
    )
    print(json.dumps({"pdf": str(args.pdf.resolve()), "png": str(args.png.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
