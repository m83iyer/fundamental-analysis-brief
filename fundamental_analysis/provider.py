from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from .analysis import normalize_statements
from .market import Security


class YahooResearchProvider:
    """Default replaceable adapter for reproducible public research inputs."""

    name = "Yahoo Finance via yfinance"

    def fetch(self, security: Security) -> dict[str, Any]:
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

        daily = ticker.history(period="10d", interval="1d", auto_adjust=True, actions=False)
        monthly = ticker.history(period="6y", interval="1mo", auto_adjust=True, actions=False)
        if daily is None or daily.empty or monthly is None or monthly.empty:
            raise ValueError(f"No usable adjusted market history returned for {security.canonical_ticker}")
        current_price = float(pd.to_numeric(daily["Close"], errors="coerce").dropna().iloc[-1])
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
        }
