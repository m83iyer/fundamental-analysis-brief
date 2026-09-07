# Changelog

## 1.1.0 — 2026-09-07

Added the dated-price view: `--as-of YYYY-MM-DD` prices today's filings at the split-adjusted close of a past trading session, instead of only today's live quote.

- New CLI flag `--as-of`, mutually exclusive with `--input-bundle`; rejects malformed or future dates before any network access.
- Provider fetches the split-adjusted (not dividend-adjusted) close for the requested date — verified live that using the dividend-adjusted close instead would have silently understated market cap and every downstream multiple by several percent. Resolves to the nearest prior trading session within a 7-day window; fails closed beyond that.
- New evidence fields: `price_mode`, `as_of_requested`, `quote_date`, `price_basis`, `statement_basis`, `hindsight` (how many statement periods postdate the quote under a declared 90-day filing-lag rule). The input fingerprint now varies with price mode, so a live and a dated run of the same statements never share a hash.
- The rendered page marks a dated brief unmissably: a gold accent and header banner, relabeled quote/market-cap/P-E fields, and an explicit fiscal-year-after-quote count — never a footnote.
- Bright line, enforced by the new test suite: one date per brief, no cross-date arithmetic, no comparison language. This is a dated PRICE against today's fundamentals, not a backtest of the model's own past output — see `METHODOLOGY.md`.
- `ENGINE_VERSION` bumped to `0.2.0`.

## 1.0.0 — 2026-09-07

First tagged release.

- Fixed a header text collision between the section title and the filing-date quote (`fit_text` now sizes the secondary headline to stay inside its column instead of overlapping the market-cap figure).
- Added `METHODOLOGY.md`, the implementation contract for what the engine calculates, what it refuses to calculate, and how to interpret the output — enforced by a test that checks the code's stated boundaries actually match the document.
- Added a live, weekly-refreshed example (HDFC Bank) to the README via `.github/workflows/refresh-example.yml`, so the shown output is never a stale screenshot.
- No changes to the evidence contract, refusal rules, or CLI interface.
