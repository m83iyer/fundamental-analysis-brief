from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .analysis import analyze_fundamentals
from .market import resolve_security
from .provider import YahooResearchProvider
from .render import render


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _clear_previous_failure(output_dir: Path) -> None:
    receipt = output_dir / "failure-receipt.json"
    if receipt.is_file():
        receipt.unlink()


def _clear_previous_success(output_dir: Path) -> None:
    for name in ("source-input.json", "fundamental-evidence.json", "market-history.json"):
        path = output_dir / name
        if path.is_file():
            path.unlink()
    for pattern in ("*-fundamental-brief.pdf", "*-fundamental-brief.png"):
        for path in output_dir.glob(pattern):
            if path.is_file():
                path.unlink()


def run(
    *,
    ticker: str,
    market: str | None,
    output_dir: Path,
    input_bundle: Path | None = None,
) -> dict[str, Any]:
    security = resolve_security(ticker, market)
    if input_bundle:
        snapshot = json.loads(input_bundle.read_text(encoding="utf-8"))
    else:
        snapshot = YahooResearchProvider().fetch(security)
    evidence = analyze_fundamentals(security, **snapshot)

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_ticker = security.canonical_ticker.replace(".", "-")
    source_path = output_dir / "source-input.json"
    evidence_path = output_dir / "fundamental-evidence.json"
    history_path = output_dir / "market-history.json"
    pdf_path = output_dir / f"{safe_ticker}-fundamental-brief.pdf"
    png_path = output_dir / f"{safe_ticker}-fundamental-brief.png"
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
    parser.add_argument("--input-bundle", type=Path)
    args = parser.parse_args()
    try:
        result = run(
            ticker=args.ticker,
            market=args.market,
            output_dir=args.out.resolve(),
            input_bundle=args.input_bundle.resolve() if args.input_bundle else None,
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
