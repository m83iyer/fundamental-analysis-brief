from __future__ import annotations

from dataclasses import dataclass


DISCLAIMER = "Research output - not a recommendation. The reader decides whether to act."


@dataclass(frozen=True)
class MarketProfile:
    code: str
    label: str
    currency: str
    currency_symbol: str
    statement_unit_divisor: float
    statement_unit_label: str
    risk_free_rate: float
    equity_risk_premium: float
    default_tax_rate: float
    terminal_growth: float
    debt_spread: float
    default_exchange: str
    filing_label: str


@dataclass(frozen=True)
class Security:
    requested_ticker: str
    canonical_ticker: str
    display_ticker: str
    exchange: str
    profile: MarketProfile


MARKETS = {
    "us": MarketProfile(
        code="us",
        label="United States",
        currency="USD",
        currency_symbol="$",
        statement_unit_divisor=1_000_000_000.0,
        statement_unit_label="$B",
        risk_free_rate=0.045,
        equity_risk_premium=0.0475,
        default_tax_rate=0.21,
        terminal_growth=0.025,
        debt_spread=0.015,
        default_exchange="US",
        filing_label="Annual report / SEC filing",
    ),
    "in": MarketProfile(
        code="in",
        label="India",
        currency="INR",
        currency_symbol="₹",
        statement_unit_divisor=10_000_000.0,
        statement_unit_label="₹ cr",
        risk_free_rate=0.0675,
        equity_risk_premium=0.06,
        default_tax_rate=0.25,
        terminal_growth=0.05,
        debt_spread=0.02,
        default_exchange="NSE",
        filing_label="Annual report / NSE-BSE filing / Ind AS",
    ),
}


def normalize_market(value: str | None, ticker: str) -> str:
    aliases = {
        "usa": "us",
        "united-states": "us",
        "india": "in",
        "nse": "in",
        "bse": "in",
    }
    if value:
        market = aliases.get(value.strip().lower(), value.strip().lower())
        if market not in MARKETS:
            raise ValueError("market must be 'us' or 'in'")
        return market
    upper = ticker.strip().upper()
    if upper.endswith((".NS", ".BO")) or (upper.isdigit() and len(upper) == 6):
        return "in"
    return "us"


def resolve_security(ticker: str, market: str | None = None) -> Security:
    requested = ticker.strip().upper()
    if not requested:
        raise ValueError("ticker is required")
    market_code = normalize_market(market, requested)
    profile = MARKETS[market_code]

    if market_code == "in":
        if requested.endswith(".NS"):
            canonical = requested
            exchange = "NSE"
            display = requested[:-3]
        elif requested.endswith(".BO"):
            canonical = requested
            exchange = "BSE"
            display = requested[:-3]
        elif requested.isdigit() and len(requested) == 6:
            canonical = requested + ".BO"
            exchange = "BSE"
            display = requested
        elif "." in requested:
            raise ValueError("Indian tickers must use .NS, .BO, a six-digit BSE code, or a plain NSE symbol")
        else:
            canonical = requested + ".NS"
            exchange = "NSE"
            display = requested
    else:
        if requested.endswith((".NS", ".BO")):
            raise ValueError("Indian exchange suffix cannot be analyzed with market='us'")
        canonical = requested.replace(".", "-")
        exchange = "US"
        display = requested

    return Security(
        requested_ticker=requested,
        canonical_ticker=canonical,
        display_ticker=display,
        exchange=exchange,
        profile=profile,
    )


def compact_money(value: float, currency: str) -> str:
    symbol = "₹" if currency == "INR" else "$"
    absolute = abs(value)
    sign = "-" if value < 0 else ""
    if currency == "INR":
        if absolute >= 1_000_000_000_000:
            return f"{sign}{symbol}{absolute / 1_000_000_000_000:.2f} lakh cr"
        if absolute >= 10_000_000:
            return f"{sign}{symbol}{absolute / 10_000_000:,.0f} cr"
        return f"{sign}{symbol}{absolute:,.0f}"
    if absolute >= 1_000_000_000_000:
        return f"{sign}{symbol}{absolute / 1_000_000_000_000:.2f}T"
    if absolute >= 1_000_000_000:
        return f"{sign}{symbol}{absolute / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"{sign}{symbol}{absolute / 1_000_000:.1f}M"
    return f"{sign}{symbol}{absolute:,.0f}"


def per_share(value: float, currency: str, digits: int = 0) -> str:
    symbol = "₹" if currency == "INR" else "$"
    return f"-{symbol}{abs(value):,.{digits}f}" if value < 0 else f"{symbol}{value:,.{digits}f}"
