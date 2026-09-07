from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import fitz
import pandas as pd
import pytest

from fundamental_analysis.analysis import _compute_hindsight, analyze_fundamentals
from fundamental_analysis.cli import _parse_as_of, main, run
from fundamental_analysis.market import resolve_security
from fundamental_analysis.provider import YahooResearchProvider
from fundamental_analysis.render import render

# Shared synthetic fixtures already exist in test_dual_market.py -- reused
# here rather than re-defined, so there is one source of truth for what a
# "synthetic statement row" looks like.
from test_dual_market import synthetic_history, synthetic_statements


class _FakeTicker:
    """Minimal yf.Ticker stand-in: .history(**kwargs) returns a fixed frame
    regardless of the requested window, so tests control exactly what a
    real API call would have returned for that window."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def history(self, **_kwargs: object) -> pd.DataFrame:
        return self._frame


COMMON_KWARGS = dict(
    company_name="Fixture Industries Limited",
    sector="Industrials",
    industry="Conglomerates",
    current_price=190.0,
    beta=1.05,
    fetched_at="2026-07-22T00:00:00+00:00",
    source="frozen fixture",
    source_url="https://example.invalid/frozen-fixture",
)


def _dated_evidence(security, history, *, as_of_requested="2021-04-09", quote_date="2021-04-09"):
    return analyze_fundamentals(
        security,
        statements=synthetic_statements("USD"),
        market_history=history,
        price_mode="dated",
        as_of_requested=as_of_requested,
        quote_date=quote_date,
        price_basis="split_adjusted_close",
        **COMMON_KWARGS,
    )


# ── 1. --as-of parsing: malformed / future dates rejected, no network ──────

def test_as_of_rejects_malformed_date() -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        _parse_as_of("not-a-date")


def test_as_of_rejects_future_date() -> None:
    with pytest.raises(ValueError, match="future"):
        _parse_as_of("2099-01-01")


def test_as_of_accepts_past_date() -> None:
    assert _parse_as_of("2021-04-09") == date(2021, 4, 9)


def test_as_of_none_is_a_noop() -> None:
    assert _parse_as_of(None) is None
    assert _parse_as_of("") is None


# ── 2. Dated quote uses Close, not Adj Close ────────────────────────────────

def test_dated_price_uses_close_not_adj_close() -> None:
    idx = pd.date_range("2021-04-01", periods=9, freq="D")
    frame = pd.DataFrame(
        {"Close": [100.0] * 9, "Adj Close": [96.0] * 9},  # dividend-adjusted, deliberately lower
        index=idx,
    )
    price, session = YahooResearchProvider()._resolve_dated_price(
        _FakeTicker(frame), "TEST", date(2021, 4, 9)
    )
    assert price == 100.0
    assert session == date(2021, 4, 9)


# ── 3. Session resolution: holiday gap resolves back; >7 days fails closed ─

def test_dated_price_resolves_to_prior_session_across_a_gap() -> None:
    idx = pd.to_datetime(["2021-04-06", "2021-04-07", "2021-04-08"])  # no bar on requested 04-09
    frame = pd.DataFrame({"Close": [10.0, 11.0, 12.0]}, index=idx)
    price, session = YahooResearchProvider()._resolve_dated_price(
        _FakeTicker(frame), "TEST", date(2021, 4, 9)
    )
    assert session == date(2021, 4, 8)
    assert price == 12.0


def test_dated_price_fails_closed_beyond_seven_day_gap() -> None:
    idx = pd.to_datetime(["2021-03-25"])  # 15 days before the requested as_of
    frame = pd.DataFrame({"Close": [10.0]}, index=idx)
    with pytest.raises(ValueError, match="No trading session"):
        YahooResearchProvider()._resolve_dated_price(_FakeTicker(frame), "TEST", date(2021, 4, 9))


def test_dated_price_fails_closed_when_window_is_empty() -> None:
    frame = pd.DataFrame({"Close": []}, index=pd.DatetimeIndex([]))
    with pytest.raises(ValueError, match="No trading session"):
        YahooResearchProvider()._resolve_dated_price(_FakeTicker(frame), "TEST", date(2021, 4, 9))


# ── 4. Hindsight count follows period_end + filing_lag_days > quote_date ───

def test_hindsight_counts_periods_the_filing_lag_says_were_not_yet_public() -> None:
    statements = [
        {"year": 2022, "period_end": "2022-09-30"},
        {"year": 2023, "period_end": "2023-09-30"},
        {"year": 2024, "period_end": "2024-09-30"},
    ]
    result = _compute_hindsight(statements, "2023-10-15", filing_lag_days=90)
    # 2022-09-30 + 90d = 2022-12-29, NOT after 2023-10-15 -> not counted.
    # 2023-09-30 + 90d = 2023-12-29, after 2023-10-15 -> counted.
    # 2024-09-30 + 90d = 2024-12-29, after 2023-10-15 -> counted.
    assert result["statement_periods_after_quote"] == 2
    assert result["latest_statement_period_end"] == "2024-09-30"
    assert result["period_end_source"] == "provider"


def test_hindsight_falls_back_to_calendar_year_end_without_period_end() -> None:
    statements = [{"year": 2023}, {"year": 2024}]
    # 2023-12-31 + 90d = 2024-03-30 (before quote); 2024-12-31 + 90d =
    # 2025-03-31 (after quote) -- verified via direct date arithmetic.
    result = _compute_hindsight(statements, "2024-06-01", filing_lag_days=90)
    assert result["period_end_source"] == "assumed_calendar_year_end"
    assert result["statement_periods_after_quote"] == 1


# ── 5. Same statements/price, different mode -> different input_sha256 ─────

def test_input_hash_differs_between_live_and_dated_with_identical_price() -> None:
    security = resolve_security("AAPL", "us")
    history = synthetic_history(security.canonical_ticker, "USD")
    statements = synthetic_statements("USD")
    live = analyze_fundamentals(security, statements=statements, market_history=history, **COMMON_KWARGS)
    dated = _dated_evidence(security, history)
    assert live["current_price"] == dated["current_price"]
    assert live["input_sha256"] != dated["input_sha256"]


# ── 6/7. Rendered dated page carries the dated markers; live page doesn't ──

def test_dated_render_shows_dated_markers_and_is_deterministic(tmp_path: Path) -> None:
    security = resolve_security("AAPL", "us")
    history = synthetic_history(security.canonical_ticker, "USD")
    data = _dated_evidence(security, history)
    sidecar = tmp_path / "evidence.json"
    history_path = tmp_path / "history.json"
    sidecar.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    history_path.write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")
    pdf1, png1 = tmp_path / "first.pdf", tmp_path / "first.png"
    pdf2, png2 = tmp_path / "second.pdf", tmp_path / "second.png"
    render(sidecar, history_path, pdf1, png1, "2026-07-22 00:00 UTC")
    render(sidecar, history_path, pdf2, png2, "2026-07-22 00:00 UTC")
    assert hashlib.sha256(pdf1.read_bytes()).digest() == hashlib.sha256(pdf2.read_bytes()).digest()

    document = fitz.open(pdf1)
    assert document.page_count == 1
    text = document[0].get_text("text")
    document.close()

    assert "DATED PRICE 2021-04-09" in text
    assert "CLOSE 2021-04-09" in text
    assert "PRICE IMPLIED" in text
    # The old undifferentiated labels must be gone, not merely supplemented.
    assert "market quote" not in text.lower()
    assert "current quote" not in text.lower()
    assert "MARKET QUOTE 20" not in text
    for banned in ("buy", "sell", "undervalued", "overvalued", "hold rating"):
        assert banned not in text.lower()


def test_live_render_never_shows_dated_markers(tmp_path: Path) -> None:
    security = resolve_security("AAPL", "us")
    history = synthetic_history(security.canonical_ticker, "USD")
    data = analyze_fundamentals(
        security, statements=synthetic_statements("USD"), market_history=history, **COMMON_KWARGS
    )
    assert data["price_mode"] == "live"
    assert data["hindsight"] is None
    sidecar = tmp_path / "evidence.json"
    history_path = tmp_path / "history.json"
    sidecar.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    history_path.write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")
    pdf_path, png_path = tmp_path / "live.pdf", tmp_path / "live.png"
    render(sidecar, history_path, pdf_path, png_path, "2026-07-22 00:00 UTC")
    document = fitz.open(pdf_path)
    text = document[0].get_text("text")
    document.close()
    assert "DATED PRICE" not in text
    assert "PRICE IMPLIES" in text
    assert "PRICE IMPLIED" not in text


# ── 8. Bundle replay reproduces dated mode with zero network access ────────

def test_bundle_replay_of_a_dated_run_reproduces_dated_mode(tmp_path: Path) -> None:
    security = resolve_security("AAPL", "us")
    history = synthetic_history(security.canonical_ticker, "USD")
    snapshot = {
        "statements": synthetic_statements("USD"),
        "market_history": history,
        "price_mode": "dated",
        "as_of_requested": "2021-04-09",
        "quote_date": "2021-04-09",
        "price_basis": "split_adjusted_close",
        **COMMON_KWARGS,
    }
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(snapshot), encoding="utf-8")
    result = run(ticker="AAPL", market="us", output_dir=tmp_path / "outputs", input_bundle=bundle_path)
    assert result["price_mode"] == "dated"
    assert result["quote_date"] == "2021-04-09"
    assert result["pdf"].endswith("AAPL-fundamental-brief-2021-04-09.pdf")


def test_old_bundle_without_price_mode_key_replays_as_live(tmp_path: Path) -> None:
    security = resolve_security("AAPL", "us")
    history = synthetic_history(security.canonical_ticker, "USD")
    snapshot = {
        "statements": synthetic_statements("USD"),
        "market_history": history,
        **COMMON_KWARGS,
    }
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(snapshot), encoding="utf-8")
    result = run(ticker="AAPL", market="us", output_dir=tmp_path / "outputs", input_bundle=bundle_path)
    assert result["price_mode"] == "live"
    assert result["pdf"].endswith("AAPL-fundamental-brief.pdf")


# ── 9. --as-of and --input-bundle are mutually exclusive at the CLI ────────

def test_as_of_and_input_bundle_are_mutually_exclusive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["fab", "AAPL", "--as-of", "2021-04-09", "--input-bundle", str(bundle_path)]
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0
