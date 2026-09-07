from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .analysis import analyze_fundamentals
from .market import resolve_security
from .provider import YahooResearchProvider
from .render import render


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_as_of(value: str | None) -> date | None:
    """Parse and validate --as-of. Pure and network-free (no ticker lookups)
    so it is directly unit-testable: raises ValueError on a malformed date
    or one after today's UTC date, same failure-receipt path as any other
    input error."""
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"--as-of must be YYYY-MM-DD, got {value!r}") from error
    today = datetime.now(timezone.utc).date()
    if parsed > today:
        raise ValueError(f"--as-of {parsed.isoformat()} is in the future (today is {today.isoformat()})")
    return parsed


def _clear_previous_failure(output_dir: Path) -> None:
    receipt = output_dir / "failure-receipt.json"
    if receipt.is_file():
        receipt.unlink()


def _clear_previous_success(output_dir: Path) -> None:
    for name in ("source-input.json", "fundamental-evidence.json", "market-history.json"):
        path = output_dir / name
        if path.is_file():
            path.unlink()
    # The glob is deliberately open-ended (not an exact match): dated-mode
    # runs suffix the filename with the quote date, e.g.
    # "AAPL-fundamental-brief-2021-04-09.pdf", and a stale one from a
    # different --as-of date must be cleared just like a stale live one.
    for pattern in ("*-fundamental-brief*.pdf", "*-fundamental-brief*.png"):
        for path in output_dir.glob(pattern):
            if path.is_file():
                path.unlink()


def run(
    *,
    ticker: str,
    market: str | None,
    output_dir: Path,
    input_bundle: Path | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    security = resolve_security(ticker, market)
    as_of_date = _parse_as_of(as_of)
    if input_bundle:
        snapshot = json.loads(input_bundle.read_text(encoding="utf-8"))
    else:
        snapshot = YahooResearchProvider().fetch(security, as_of=as_of_date)
    evidence = analyze_fundamentals(security, **snapshot)

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_ticker = security.canonical_ticker.replace(".", "-")
    source_path = output_dir / "source-input.json"
    evidence_path = output_dir / "fundamental-evidence.json"
    history_path = output_dir / "market-history.json"
    quote_date = evidence.get("quote_date") or ""
    # Filename uses the SESSION date (the truth), not the requested date --
    # they can differ (a weekend/holiday --as-of resolves to the prior
    # trading session).
    suffix = f"-{quote_date}" if evidence.get("price_mode") == "dated" and quote_date else ""
    pdf_path = output_dir / f"{safe_ticker}-fundamental-brief{suffix}.pdf"
    png_path = output_dir / f"{safe_ticker}-fundamental-brief{suffix}.png"
    _write_json(source_path, snapshot)
    _write_json(evidence_path, evidence)
    _write_json(history_path, snapshot["market_history"])
    render(
        evidence_path,
        history_path,
        pdf_path,
        png_path,
        str(snapshot["fetched_at"]).replace("T", " ")[:16] + " UTC",
    )
    _clear_previous_failure(output_dir)
    return {
        "ticker": security.canonical_ticker,
        "market": security.profile.code,
        "status": evidence["status"],
        "price_mode": evidence.get("price_mode", "live"),
        "quote_date": evidence.get("quote_date"),
        "as_of_requested": evidence.get("as_of_requested"),
        "source_input": str(source_path),
        "evidence": str(evidence_path),
        "pdf": str(pdf_path),
        "png": str(png_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a refusal-first US or Indian fundamental-analysis brief."
    )
    parser.add_argument("ticker")
    parser.add_argument("--market", choices=("us", "in"), default=None)
    parser.add_argument("--out", type=Path, default=Path("outputs"))
    replay_group = parser.add_mutually_exclusive_group()
    replay_group.add_argument("--input-bundle", type=Path)
    replay_group.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="YYYY-MM-DD: dated-price view (split-adjusted close on/before this date; statements as reported today)",
    )
    args = parser.parse_args()
    try:
        result = run(
            ticker=args.ticker,
            market=args.market,
            output_dir=args.out.resolve(),
            input_bundle=args.input_bundle.resolve() if args.input_bundle else None,
            as_of=args.as_of,
        )
    except (ValueError, KeyError, IndexError) as error:
        output_dir = args.out.resolve()
        _clear_previous_success(output_dir)
        receipt = {
            "ticker": args.ticker.upper(),
            "market": args.market,
            "status": "insufficient_evidence",
            "reason": str(error),
        }
        _write_json(output_dir / "failure-receipt.json", receipt)
        print(json.dumps(receipt, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
