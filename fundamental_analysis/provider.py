from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from .analysis import normalize_statements
from .market import Security


class YahooResearchProvider:
    """Default replaceable adapter for reproducible public research inputs."""

    name = "Yahoo Finance via yfinance"

    def _resolve_dated_price(self, ticker: yf.Ticker, canonical: str, as_of: date) -> tuple[float, date]:
        """Split-adjusted (NOT dividend-adjusted) close on or before as_of.

        auto_adjust=False + "Close" is deliberate, not a shortcut: the live
        path's auto_adjust=True close is dividend-adjusted, and pairing a
        dividend-adjusted historical price with today's (undiluted-for-
        dividends) share count silently understates market cap and every
        downstream multiple -- verified live: AAPL 2019-06-14 auto_adjust
        Close 48.19 vs Adj Close 46.06 (-4.4%), HDFCBANK 608.78 vs 562.48
        (-7.6%). "Close" under auto_adjust=False IS split/bonus-adjusted to
        today's share basis, which is what pairs correctly with the latest
        diluted share count.
        """
        window = ticker.history(
            start=as_of - timedelta(days=14),
            end=as_of + timedelta(days=1),
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
        if window is not None and not window.empty:
            window = window[window.index.date <= as_of]
        if window is None or window.empty:
            raise ValueError(
                f"No trading session for {canonical} within 7 days on or before {as_of.isoformat()}"
            )
        session = window.index[-1].date()
        if (as_of - session).days > 7:
            raise ValueError(
                f"No trading session for {canonical} within 7 days on or before {as_of.isoformat()}"
            )
        price = float(pd.to_numeric(window["Close"], errors="coerce").dropna().iloc[-1])
        return price, session

    def fetch(self, security: Security, as_of: date | None = None) -> dict[str, Any]:
        ticker = yf.Ticker(security.canonical_ticker)
        income = ticker.get_income_stmt(freq="yearly")
        balance = ticker.get_balance_sheet(freq="yearly")
        cashflow = ticker.get_cash_flow(freq="yearly")
        statements = normalize_statements(
            income,
            balance,
            cashflow,
            currency=security.profile.currency,
        )

        monthly = ticker.history(period="6y", interval="1mo", auto_adjust=True, actions=False)
        if monthly is None or monthly.empty:
            raise ValueError(f"No usable adjusted market history returned for {security.canonical_ticker}")

        price_mode = "live"
        as_of_requested: str | None = None
        if as_of is not None:
            price_mode = "dated"
            as_of_requested = as_of.isoformat()
            current_price, session_date = self._resolve_dated_price(ticker, security.canonical_ticker, as_of)
            quote_date = session_date.isoformat()
            price_basis = "split_adjusted_close"
        else:
            daily = ticker.history(period="10d", interval="1d", auto_adjust=True, actions=False)
            if daily is None or daily.empty:
                raise ValueError(f"No usable adjusted market history returned for {security.canonical_ticker}")
            daily_close = pd.to_numeric(daily["Close"], errors="coerce").dropna()
            current_price = float(daily_close.iloc[-1])
            quote_date = daily_close.index[-1].date().isoformat()
            price_basis = "latest_adjusted_close"
        points = [
            {"date": pd.Timestamp(index).date().isoformat(), "adjusted_close": round(float(value), 6)}
            for index, value in pd.to_numeric(monthly["Close"], errors="coerce").dropna().items()
        ]
        if len(points) < 48:
            raise ValueError(f"Only {len(points)} monthly observations were returned; need 48")

        try:
            info = ticker.get_info() or {}
        except Exception:
            info = {}
        returned_currency = str(info.get("currency") or security.profile.currency).upper()
        if returned_currency != security.profile.currency:
            raise ValueError(
                f"Provider currency {returned_currency} does not match {security.profile.currency} for {security.canonical_ticker}"
            )
        fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        source_url = f"https://finance.yahoo.com/quote/{security.canonical_ticker}"
        market_history = {
            "schema_version": 1,
            "ticker": security.canonical_ticker,
            "display_ticker": security.display_ticker,
            "market": security.profile.code,
            "exchange": security.exchange,
            "currency": security.profile.currency,
            "source": self.name,
            "source_url": source_url,
            "fetched_at": fetched_at,
            "points": points,
        }
        return {
            "company_name": str(info.get("longName") or info.get("shortName") or security.display_ticker),
            "sector": str(info.get("sector") or "Unavailable"),
            "industry": str(info.get("industry") or "Unavailable"),
            "beta": float(info["beta"]) if info.get("beta") is not None else None,
            "current_price": current_price,
            "statements": statements,
            "market_history": market_history,
            "fetched_at": fetched_at,
            "source": self.name,
            "source_url": source_url,
            "price_mode": price_mode,
            "as_of_requested": as_of_requested,
            "quote_date": quote_date,
            "price_basis": price_basis,
        }
