# Changelog

## 1.0.0 — 2026-09-07

First tagged release.

- Fixed a header text collision between the section title and the filing-date quote (`fit_text` now sizes the secondary headline to stay inside its column instead of overlapping the market-cap figure).
- Added `METHODOLOGY.md`, the implementation contract for what the engine calculates, what it refuses to calculate, and how to interpret the output — enforced by a test that checks the code's stated boundaries actually match the document.
- Added a live, weekly-refreshed example (HDFC Bank) to the README via `.github/workflows/refresh-example.yml`, so the shown output is never a stale screenshot.
- No changes to the evidence contract, refusal rules, or CLI interface.
