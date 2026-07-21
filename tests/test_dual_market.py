from __future__ import annotations

import hashlib
import json
from pathlib import Path

import fitz
import pandas as pd
import pytest
from PIL import Image

from fundamental_analysis.analysis import analyze_fundamentals, normalize_statements
from fundamental_analysis.market import DISCLAIMER, compact_money, resolve_security
from fundamental_analysis.render import EXPORT_H, EXPORT_W, PAGE_H, PAGE_W, render


def synthetic_statements(currency: str, *, periods: int = 5, financial: bool = False) -> list[dict]:
    scale = 10_000_000.0 if currency == "INR" else 1_000_000_000.0
    shares = 6_700_000_000.0 if currency == "INR" else 1_500_000_000.0
    rows = []
    for index in range(periods):
        revenue = scale * 8_000 * (1.09**index)
        net_income = revenue * (0.21 if not financial else 0.17)
        rows.append(
            {
                "year": 2021 + index,
                "_currency": currency,
                "revenue": revenue,
                "cogs": revenue * 0.58,
                "gross_profit": revenue * 0.42 if not financial else None,
                "operating_income": revenue * 0.27 if not financial else None,
                "pretax_income": net_income / 0.75,
                "net_income": net_income,
                "tax": net_income / 0.75 - net_income,
                "interest_expense": revenue * 0.01,
                "ebitda": revenue * 0.31 if not financial else None,
                "eps_diluted": net_income / shares,
                "shares": shares * (1.0 + 0.002 * index),
                "da": revenue * 0.03,
                "cash": revenue * 0.18,
                "current_assets": None if financial else revenue * 0.45,
                "current_liabilities": None if financial else revenue * 0.31,
                "total_assets": revenue * (8.0 if financial else 1.7),
                "total_liabilities": revenue * (7.0 if financial else 0.9),
                "equity": revenue * 0.8,
                "total_debt": revenue * (1.5 if financial else 0.25),
                "lt_debt": revenue * 0.2,
                "lt_debt_current": revenue * 0.02,
                "st_borrowings": revenue * 0.03,
                "cfo": revenue * 0.25,
                "capex": -revenue * 0.05,
            }
        )
    return rows


def synthetic_history(ticker: str, currency: str) -> dict:
    dates = pd.date_range("2020-01-31", periods=72, freq="ME")
    return {
        "schema_version": 1,
        "ticker": ticker,
        "currency": currency,
        "source": "frozen fixture",
        "source_url": "https://example.invalid/frozen-fixture",
        "fetched_at": "2026-07-22T00:00:00+00:00",
        "points": [
            {"date": value.date().isoformat(), "adjusted_close": 100.0 + index * 2.0}
            for index, value in enumerate(dates)
        ],
    }


def payload(market: str, ticker: str, *, financial: bool = False, periods: int = 5) -> tuple[dict, dict]:
    security = resolve_security(ticker, market)
    history = synthetic_history(security.canonical_ticker, security.profile.currency)
    data = analyze_fundamentals(
        security,
        company_name="Fixture Bank Limited" if financial else "Fixture Industries Limited",
        sector="Financial Services" if financial else "Industrials",
        industry="Banks - Regional" if financial else "Conglomerates",
        current_price=2450.0 if market == "in" else 190.0,
        beta=1.05,
        statements=synthetic_statements(security.profile.currency, periods=periods, financial=financial),
        market_history=history,
        fetched_at="2026-07-22T00:00:00+00:00",
        source="frozen fixture",
        source_url="https://example.invalid/frozen-fixture",
    )
    return data, history


def test_market_resolution_is_explicit_and_unambiguous() -> None:
    assert resolve_security("AAPL", "us").canonical_ticker == "AAPL"
    assert resolve_security("BRK.B", "us").canonical_ticker == "BRK-B"
    assert resolve_security("RELIANCE", "in").canonical_ticker == "RELIANCE.NS"
    assert resolve_security("RELIANCE.NS").exchange == "NSE"
    assert resolve_security("500325", "in").canonical_ticker == "500325.BO"
    assert resolve_security("500325.BO", "in").exchange == "BSE"
    with pytest.raises(ValueError, match="Indian exchange suffix"):
        resolve_security("RELIANCE.NS", "us")


def test_currency_formatting_does_not_leak_markets() -> None:
    assert compact_money(1_500_000_000_000, "INR") == "₹1.50 lakh cr"
    assert compact_money(1_500_000_000_000, "USD") == "$1.50T"


def test_statement_normalization_accepts_four_comparable_years() -> None:
    columns = pd.to_datetime(["2022-03-31", "2023-03-31", "2024-03-31", "2025-03-31"])
    income = pd.DataFrame(
        [
            [100.0, 110.0, 120.0, 130.0],
            [10.0, 11.0, 12.0, 13.0],
            [1.0, 1.1, 1.2, 1.3],
        ],
        index=["TotalRevenue", "NetIncome", "DilutedEPS"],
        columns=columns,
    )
    balance = pd.DataFrame([[10.0] * 4], index=["OrdinarySharesNumber"], columns=columns)
    cashflow = pd.DataFrame(
        [[15.0] * 4, [-3.0] * 4],
        index=["OperatingCashFlow", "CapitalExpenditure"],
        columns=columns,
    )
    rows = normalize_statements(income, balance, cashflow, currency="INR")
    assert [row["year"] for row in rows] == [2022, 2023, 2024, 2025]
    assert all(row["_currency"] == "INR" for row in rows)


@pytest.mark.parametrize(("market", "ticker", "currency"), (("us", "AAPL", "USD"), ("in", "RELIANCE", "INR")))
def test_analysis_contract_for_both_markets(market: str, ticker: str, currency: str) -> None:
    data, _ = payload(market, ticker)
    assert data["status"] == "complete"
    assert data["currency"] == currency
    assert data["disclaimer"] == DISCLAIMER
    assert data["dcf"]["per_share"] > 0
    assert data["scenarios"]["range_low"] <= data["scenarios"]["range_high"]
    assert len(data["dcf"]["sensitivity_grid"]) == 7
    assert data["input_sha256"]


def test_bank_view_suppresses_industrial_dcf() -> None:
    data, _ = payload("in", "HDFCBANK", financial=True, periods=4)
    assert data["status"] == "limited_fundamental_view"
    assert data["dcf"]["per_share"] is None
    assert data["scenarios"]["range_low"] is None
    assert "not comparable" in data["limitation"]


def test_currency_mismatch_fails_closed() -> None:
    security = resolve_security("RELIANCE", "in")
    history = synthetic_history(security.canonical_ticker, "USD")
    with pytest.raises(ValueError, match="currency"):
        analyze_fundamentals(
            security,
            company_name="Fixture Industries Limited",
            sector="Industrials",
            industry="Conglomerates",
            current_price=100.0,
            beta=1.0,
            statements=synthetic_statements("INR"),
            market_history=history,
            fetched_at="2026-07-22T00:00:00+00:00",
            source="fixture",
            source_url="https://example.invalid",
        )


def test_four_period_india_render_is_one_page_and_deterministic(tmp_path: Path) -> None:
    data, history = payload("in", "RELIANCE", periods=4)
    sidecar = tmp_path / "evidence.json"
    history_path = tmp_path / "history.json"
    sidecar.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    history_path.write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")
    first_pdf, first_png = tmp_path / "first.pdf", tmp_path / "first.png"
    second_pdf, second_png = tmp_path / "second.pdf", tmp_path / "second.png"
    render(sidecar, history_path, first_pdf, first_png, "2026-07-22 00:00 UTC")
    render(sidecar, history_path, second_pdf, second_png, "2026-07-22 00:00 UTC")
    assert hashlib.sha256(first_pdf.read_bytes()).digest() == hashlib.sha256(second_pdf.read_bytes()).digest()
    assert hashlib.sha256(first_png.read_bytes()).digest() == hashlib.sha256(second_png.read_bytes()).digest()
    document = fitz.open(first_pdf)
    assert document.page_count == 1
    assert document[0].rect.width == PAGE_W
    assert document[0].rect.height == PAGE_H
    text = document[0].get_text("text")
    document.close()
    assert "NSE / INR" in text
    assert "$" not in text
    with Image.open(first_png) as image:
        assert image.size == (EXPORT_W, EXPORT_H)
