from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .market import DISCLAIMER, Security


ENGINE_VERSION = "0.1.0"
MIN_STATEMENT_PERIODS = 4
MAX_STATEMENT_PERIODS = 5


INCOME_FIELDS = {
    "revenue": ("TotalRevenue", "OperatingRevenue"),
    "cogs": ("CostOfRevenue", "ReconciledCostOfRevenue"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncome", "TotalOperatingIncomeAsReported", "EBIT"),
    "pretax_income": ("PretaxIncome",),
    "net_income": ("NetIncome", "NetIncomeCommonStockholders", "DilutedNIAvailtoComStockholders"),
    "tax": ("TaxProvision",),
    "interest_expense": ("InterestExpense", "InterestExpenseNonOperating"),
    "ebitda": ("EBITDA", "NormalizedEBITDA"),
    "eps_diluted": ("DilutedEPS", "BasicEPS"),
    "shares": ("DilutedAverageShares", "BasicAverageShares"),
    "da": ("ReconciledDepreciation", "DepreciationAndAmortizationInIncomeStatement"),
}

BALANCE_FIELDS = {
    "cash": ("CashCashEquivalentsAndShortTermInvestments", "CashAndCashEquivalents", "CashFinancial"),
    "current_assets": ("CurrentAssets",),
    "current_liabilities": ("CurrentLiabilities",),
    "total_assets": ("TotalAssets",),
    "total_liabilities": ("TotalLiabilitiesNetMinorityInterest",),
    "equity": ("StockholdersEquity", "CommonStockEquity", "TotalEquityGrossMinorityInterest"),
    "total_debt": ("TotalDebt", "LongTermDebtAndCapitalLeaseObligation"),
    "lt_debt": ("LongTermDebt", "LongTermDebtAndCapitalLeaseObligation"),
    "lt_debt_current": ("CurrentDebtAndCapitalLeaseObligation", "CurrentDebt"),
    "st_borrowings": ("CurrentDebt", "OtherCurrentBorrowings"),
    "shares_balance": ("OrdinarySharesNumber", "ShareIssued"),
}

CASH_FIELDS = {
    "cfo": ("OperatingCashFlow", "CashFlowFromContinuingOperatingActivities"),
    "capex": ("CapitalExpenditure", "PurchaseOfPPE", "CapitalExpenditureReported"),
    "da_cash": ("DepreciationAndAmortization", "DepreciationAmortizationDepletion", "Depreciation"),
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _value(frame: pd.DataFrame, column: Any, candidates: tuple[str, ...]) -> float | None:
    for key in candidates:
        if key not in frame.index or column not in frame.columns:
            continue
        value = _finite(frame.at[key, column])
        if value is not None:
            return value
    return None


def _year(column: Any) -> int:
    return int(pd.Timestamp(column).year)


def normalize_statements(
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    *,
    currency: str,
) -> list[dict[str, Any]]:
    columns = sorted(
        set(income.columns).union(balance.columns).union(cashflow.columns),
        key=lambda value: pd.Timestamp(value),
    )
    by_year: dict[int, dict[str, Any]] = {}
    for column in columns:
        year = _year(column)
        row: dict[str, Any] = {"year": year, "_currency": currency}
        for key, candidates in INCOME_FIELDS.items():
            row[key] = _value(income, column, candidates)
        for key, candidates in BALANCE_FIELDS.items():
            row[key] = _value(balance, column, candidates)
        for key, candidates in CASH_FIELDS.items():
            row[key] = _value(cashflow, column, candidates)
        if row.get("shares") is None:
            row["shares"] = row.get("shares_balance")
        if row.get("da") is None:
            row["da"] = row.get("da_cash")
        row.pop("shares_balance", None)
        row.pop("da_cash", None)
        revenue = _finite(row.get("revenue"))
        net_income = _finite(row.get("net_income"))
        if revenue is not None and net_income is not None:
            by_year[year] = row
    rows = [by_year[year] for year in sorted(by_year)]
    return rows[-MAX_STATEMENT_PERIODS:]


def _safe(value: Any, default: float = 0.0) -> float:
    resolved = _finite(value)
    return default if resolved is None else resolved


def _ratio(numerator: Any, denominator: Any) -> float | None:
    top, bottom = _finite(numerator), _finite(denominator)
    if top is None or bottom is None or bottom == 0:
        return None
    return top / bottom


def _cagr(first: Any, last: Any, periods: int) -> float | None:
    start, end = _finite(first), _finite(last)
    if start is None or end is None or start <= 0 or end <= 0 or periods <= 0:
        return None
    return (end / start) ** (1.0 / periods) - 1.0


def is_financial_issuer(company_name: str, sector: str, industry: str) -> bool:
    haystack = " ".join((company_name, sector, industry)).upper()
    markers = (
        "BANK",
        "FINANCIAL SERVICES",
        "INSURANCE",
        "CAPITAL MARKETS",
        "CREDIT SERVICES",
        "ASSET MANAGEMENT",
        "BROKERAGE",
    )
    return any(marker in haystack for marker in markers)


def _tax_rate(latest: dict[str, Any], default: float) -> float:
    ratio = _ratio(latest.get("tax"), latest.get("pretax_income"))
    if ratio is None or ratio < 0.05 or ratio > 0.40:
        return default
    return ratio


def _dcf_per_share(
    *,
    starting_fcf: float,
    growth: float,
    wacc: float,
    terminal_growth: float,
    cash: float,
    debt: float,
    shares: float,
    years: int = 10,
) -> dict[str, float]:
    if starting_fcf <= 0 or shares <= 0 or wacc <= terminal_growth:
        raise ValueError("DCF inputs are not economically comparable")
    fcf = starting_fcf
    present_values: list[float] = []
    for year in range(1, years + 1):
        fade = year / years
        year_growth = growth * (1.0 - fade) + terminal_growth * fade
        fcf *= 1.0 + year_growth
        present_values.append(fcf / ((1.0 + wacc) ** year))
    terminal_value = fcf * (1.0 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal_value / ((1.0 + wacc) ** years)
    enterprise_value = sum(present_values) + pv_terminal
    equity_value = enterprise_value + cash - debt
    return {
        "per_share": equity_value / shares,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "sum_pv_fcff": sum(present_values),
        "pv_tv": pv_terminal,
    }


def _market_wacc(
    security: Security,
    *,
    beta: float | None,
    market_cap: float,
    debt: float,
    latest: dict[str, Any],
) -> dict[str, Any]:
    profile = security.profile
    resolved_beta = min(max(beta or 1.0, 0.35), 2.5)
    tax_rate = _tax_rate(latest, profile.default_tax_rate)
    cost_of_equity = profile.risk_free_rate + resolved_beta * profile.equity_risk_premium
    pretax_cost_of_debt = profile.risk_free_rate + profile.debt_spread
    cost_of_debt = pretax_cost_of_debt * (1.0 - tax_rate)
    total_capital = max(market_cap, 0.0) + max(debt, 0.0)
    weight_equity = max(market_cap, 0.0) / total_capital if total_capital else 0.9
    weight_debt = max(debt, 0.0) / total_capital if total_capital else 0.1
    raw_wacc = weight_equity * cost_of_equity + weight_debt * cost_of_debt
    floor = profile.terminal_growth + 0.02
    wacc = min(max(raw_wacc, floor), 0.25)
    return {
        "wacc": wacc,
        "cost_of_equity": cost_of_equity,
        "cost_of_debt": cost_of_debt,
        "beta": resolved_beta,
        "risk_free": profile.risk_free_rate,
        "erp": profile.equity_risk_premium,
        "weight_equity": weight_equity,
        "weight_debt": weight_debt,
        "tax_rate": tax_rate,
        "risk_free_source": f"declared_{profile.code}_market_profile_assumption",
        "debt_source": "risk_free_plus_declared_spread",
        "market_cap": market_cap,
        "total_debt": debt,
        "note": "Declared research assumptions; override before decision use.",
    }


def _empty_valuation(wacc: dict[str, Any], reason: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assumptions = {
        "rev_growth": 0.0,
        "rev_growth_start": 0.0,
        "rev_growth_source": "unavailable",
        "op_margin": 0.0,
        "op_margin_start": 0.0,
        "op_margin_target": 0.0,
        "tax_rate": wacc["tax_rate"],
        "da_pct_rev": 0.0,
        "capex_pct_rev": 0.0,
        "nwc_pct_rev": 0.0,
        "wacc": wacc["wacc"],
        "terminal_growth": 0.0,
        "n_years": 10,
        "limitation": reason,
    }
    return (
        {"per_share": None, "assumptions": assumptions, "sensitivity_grid": {}},
        {"bear": {}, "base": {}, "bull": {}, "range_low": None, "range_high": None, "weights": [0.25, 0.5, 0.25]},
        {"implied_growth": None, "converged": False, "verdict": "SUPPRESSED", "verdict_detail": reason},
    )


def _valuation(
    security: Security,
    statements: list[dict[str, Any]],
    ratios: dict[str, Any],
    wacc: dict[str, Any],
    *,
    current_price: float,
    financial: bool,
) -> tuple[str, str | None, dict[str, Any], dict[str, Any], dict[str, Any]]:
    latest = statements[-1]
    if financial:
        reason = "Bank and financial-company cash flows are not comparable to an industrial-company DCF."
        dcf, scenarios, reverse = _empty_valuation(wacc, reason)
        return "limited_fundamental_view", reason, dcf, scenarios, reverse
    shares = _safe(latest.get("shares"))
    cfo = _safe(latest.get("cfo"))
    capex_value = _finite(latest.get("capex"))
    starting_fcf = cfo - abs(capex_value or 0.0)
    if shares <= 0 or capex_value is None or starting_fcf <= 0:
        reason = "Positive normalized free cash flow and diluted-share data are required for comparable per-share valuation."
        dcf, scenarios, reverse = _empty_valuation(wacc, reason)
        return "limited_fundamental_view", reason, dcf, scenarios, reverse

    history_growth = ratios.get("revenue_cagr")
    base_growth = min(max(float(history_growth or 0.05), -0.02), 0.15)
    terminal_growth = security.profile.terminal_growth
    base_wacc = float(wacc["wacc"])
    cash = _safe(latest.get("cash"))
    debt = _safe(latest.get("total_debt"))
    revenue = _safe(latest.get("revenue"))
    op_margin = _ratio(latest.get("operating_income"), revenue) or 0.0
    tax_rate = float(wacc["tax_rate"])

    def value(growth: float, discount: float, terminal: float = terminal_growth) -> dict[str, float]:
        return _dcf_per_share(
            starting_fcf=starting_fcf,
            growth=growth,
            wacc=max(discount, terminal + 0.01),
            terminal_growth=terminal,
            cash=cash,
            debt=debt,
            shares=shares,
        )

    bear = value(max(base_growth - 0.03, -0.05), base_wacc + 0.01)
    base = value(base_growth, base_wacc)
    bull = value(min(base_growth + 0.03, 0.25), max(base_wacc - 0.01, terminal_growth + 0.02))
    scenario_values = [bear["per_share"], base["per_share"], bull["per_share"]]
    scenarios = {
        "bear": {"per_share": bear["per_share"], "growth_rate": max(base_growth - 0.03, -0.05), "op_margin": op_margin},
        "base": {"per_share": base["per_share"], "growth_rate": base_growth, "op_margin": op_margin},
        "bull": {"per_share": bull["per_share"], "growth_rate": min(base_growth + 0.03, 0.25), "op_margin": op_margin},
        "expected_value": sum(v * weight for v, weight in zip(scenario_values, (0.25, 0.5, 0.25))),
        "range_low": min(scenario_values),
        "range_high": max(scenario_values),
        "weights": [0.25, 0.5, 0.25],
    }

    wacc_axis = [max(terminal_growth + 0.01, base_wacc + offset) for offset in (-0.015, -0.01, -0.005, 0.0, 0.005, 0.01, 0.015)]
    tg_axis = [max(0.0, terminal_growth + offset) for offset in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    sensitivity: dict[str, dict[str, float]] = {}
    for discount in wacc_axis:
        row: dict[str, float] = {}
        for terminal in tg_axis:
            if discount <= terminal + 0.005:
                terminal = discount - 0.005
            row[f"{terminal:.4f}"] = value(base_growth, discount, terminal)["per_share"]
        sensitivity[f"{discount:.4f}"] = row

    assumptions = {
        "rev_growth": base_growth,
        "rev_growth_start": base_growth,
        "rev_growth_source": "capped historical revenue CAGR",
        "op_margin": op_margin,
        "op_margin_start": op_margin,
        "op_margin_target": op_margin,
        "tax_rate": tax_rate,
        "da_pct_rev": _ratio(latest.get("da"), revenue) or 0.0,
        "capex_pct_rev": abs(_safe(latest.get("capex"))) / revenue if revenue else 0.0,
        "nwc_pct_rev": 0.0,
        "wacc": base_wacc,
        "terminal_growth": terminal_growth,
        "n_years": 10,
    }
    dcf = {**base, "assumptions": assumptions, "sensitivity_grid": sensitivity}

    low, high = -0.10, 0.50
    converged = False
    implied = base_growth
    for _ in range(60):
        implied = (low + high) / 2.0
        trial = value(implied, base_wacc)["per_share"]
        if abs(trial - current_price) <= max(0.01, current_price * 0.0001):
            converged = True
            break
        if trial < current_price:
            low = implied
        else:
            high = implied
    reverse = {
        "implied_growth": implied,
        "converged": converged,
        "horizon": 10,
        "vs_historical_cagr": implied - float(history_growth or 0.0),
        "verdict": "DEMANDING" if implied > base_growth + 0.05 else "BALANCED" if implied >= base_growth - 0.02 else "CONSERVATIVE",
        "verdict_detail": "Reverse DCF is an assumption translation, not a forecast.",
        "dcf_at_implied": value(implied, base_wacc)["per_share"],
    }
    return "complete", None, dcf, scenarios, reverse


def analyze_fundamentals(
    security: Security,
    *,
    company_name: str,
    sector: str,
    industry: str,
    current_price: float,
    beta: float | None,
    statements: list[dict[str, Any]],
    market_history: dict[str, Any],
    fetched_at: str,
    source: str,
    source_url: str,
) -> dict[str, Any]:
    if len(statements) < MIN_STATEMENT_PERIODS:
        raise ValueError(f"Insufficient comparable annual statements: {len(statements)}; need {MIN_STATEMENT_PERIODS}")
    years = [int(row["year"]) for row in statements]
    if years != sorted(set(years)):
        raise ValueError("Annual statement years must be unique and chronological")
    if current_price <= 0 or not math.isfinite(current_price):
        raise ValueError("A positive timestamped market quote is required")
    if market_history.get("currency") != security.profile.currency:
        raise ValueError("Price-history currency does not match the selected market")
    if market_history.get("ticker") != security.canonical_ticker:
        raise ValueError("Price-history ticker does not match the resolved security")

    latest, first = statements[-1], statements[0]
    periods = max(1, int(latest["year"]) - int(first["year"]))
    revenue = _safe(latest.get("revenue"))
    net_income = _safe(latest.get("net_income"))
    cfo = _safe(latest.get("cfo"))
    capex = abs(_safe(latest.get("capex")))
    equity = _safe(latest.get("equity"))
    assets = _safe(latest.get("total_assets"))
    debt = _safe(latest.get("total_debt"))
    cash = _safe(latest.get("cash"))
    shares = _safe(latest.get("shares"))
    eps = _safe(latest.get("eps_diluted"), net_income / shares if shares else 0.0)
    ratios = {
        "current_ratio": _ratio(latest.get("current_assets"), latest.get("current_liabilities")),
        "gross_margin": _ratio(latest.get("gross_profit"), revenue),
        "operating_margin": _ratio(latest.get("operating_income"), revenue),
        "net_margin": _ratio(net_income, revenue),
        "fcf_margin": (cfo - capex) / revenue if revenue else None,
        "roe": net_income / equity if equity else None,
        "roa": net_income / assets if assets else None,
        "debt_to_equity": debt / equity if equity else None,
        "net_debt_to_equity": (debt - cash) / equity if equity else None,
        "asset_turnover": revenue / assets if assets else None,
        "eps": eps,
        "fcf_per_share": (cfo - capex) / shares if shares else None,
        "revenue_cagr": _cagr(first.get("revenue"), latest.get("revenue"), periods),
        "eps_cagr": _cagr(first.get("eps_diluted"), latest.get("eps_diluted"), periods),
        "asset_cagr": _cagr(first.get("total_assets"), latest.get("total_assets"), periods),
        "shares_cagr": _cagr(first.get("shares"), latest.get("shares"), periods),
        "equity_to_assets": equity / assets if assets else None,
    }
    market_cap = current_price * shares if shares > 0 else 0.0
    wacc = _market_wacc(security, beta=beta, market_cap=market_cap, debt=debt, latest=latest)
    financial = is_financial_issuer(company_name, sector, industry)
    status, limitation, dcf, scenarios, reverse = _valuation(
        security,
        statements,
        ratios,
        wacc,
        current_price=current_price,
        financial=financial,
    )
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    fingerprint = {
        "security": security.canonical_ticker,
        "statements": statements,
        "market_history": market_history,
        "fetched_at": fetched_at,
    }
    input_hash = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": "fundamental-analysis-brief-v1",
        "engine_version": ENGINE_VERSION,
        "status": status,
        "limitation": limitation,
        "generated_at": generated_at,
        "as_of": fetched_at,
        "input_sha256": input_hash,
        "ticker": security.canonical_ticker,
        "display_ticker": security.display_ticker,
        "requested_ticker": security.requested_ticker,
        "market": security.profile.code,
        "market_label": security.profile.label,
        "exchange": security.exchange,
        "currency": security.profile.currency,
        "currency_symbol": security.profile.currency_symbol,
        "statement_unit_divisor": security.profile.statement_unit_divisor,
        "statement_unit_label": security.profile.statement_unit_label,
        "filing_label": security.profile.filing_label,
        "company_name": company_name,
        "sector": sector,
        "industry": industry,
        "current_price": current_price,
        "source": source,
        "source_url": source_url,
        "data_limitations": [
            "Convenient research data; not an exchange-grade point-in-time feed.",
            "Reported periods may be restated by the issuer or provider.",
            "Valuation assumptions are declared model inputs, not forecasts.",
        ],
        "historical_statements": statements,
        "ratios": ratios,
        "wacc": wacc,
        "dcf": dcf,
        "scenarios": scenarios,
        "reverse_dcf": reverse,
        "reconciliation": {"rows": [], "diagnosis": limitation or "Model facts and assumptions are separated."},
        "disclaimer": DISCLAIMER,
    }
