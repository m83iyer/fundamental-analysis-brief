# Methodology

This note describes what the Fundamental Analysis Brief calculates, what it refuses to calculate, and how to interpret the output. It is an implementation contract for the current code and tests—not an investment thesis.

## Evidence contract

The engine requires a positive timestamped market quote, matching ticker and currency metadata, and at least four unique chronological annual statement periods. It normalizes revenue, profit, cash flow, capital expenditure, assets, liabilities, equity, debt, cash, diluted shares and diluted EPS before computing ratios. The saved source bundle and its SHA-256 input fingerprint make a run replayable.

The default adapter uses convenient research data from Yahoo Finance through `yfinance`. It is not an exchange-grade point-in-time feed. Reported periods may be restated, and every output retains its source, source URL, fetch time and data limitations.

## Historical evidence

The brief calculates revenue, EPS, asset and share-count CAGR across the available annual period; gross, operating, net and free-cash-flow margins; ROE and ROA; debt and net debt relative to equity; asset turnover; EPS; and free cash flow per share. Missing denominators remain unavailable instead of being replaced with estimates.

## Declared valuation model

Industrial-company valuation starts with normalized free cash flow: operating cash flow minus the absolute value of capital expenditure. It requires positive free cash flow and diluted-share data.

The base growth input is historical revenue CAGR capped between -2% and 15%. Bear, base and bull cases use growth offsets of -3, 0 and +3 percentage points, subject to the implementation caps, with 25% / 50% / 25% scenario weights. The discount rate comes from the market-specific WACC model, while terminal growth comes from the selected US or India market profile. Cash is added, debt is deducted and the result is divided by diluted shares.

The sensitivity grid varies WACC across seven points and terminal growth across five points. If a discount-rate/terminal-growth pair becomes mathematically unsafe, the model maintains a minimum spread before calculating the value.

## Reverse DCF

The reverse DCF does not predict revenue. It searches a -10% to 50% ten-year growth interval for the growth rate that reproduces the timestamped market quote under the same cash-flow, WACC and terminal-growth assumptions. The dashboard compares this model-implied rate with historical revenue CAGR. This is an assumption translation, not a cheap/expensive label, target price or forecast.

## Refusal rules

The engine fails closed when the quote is invalid, ticker/history metadata disagree, currency does not match, annual periods are insufficient or non-chronological, or the required valuation inputs are absent. It suppresses an industrial-company DCF for banks and financial issuers, for missing diluted shares or capital expenditure, and for non-positive normalized free cash flow.

## Verification

The test suite covers US and Indian ticker resolution, NSE/BSE handling, USD/INR isolation, four- and five-period statements, invalid quotes and currencies, bank refusal, deterministic rendering, one-page dimensions, bundled fonts, header collision safety and the exact outward disclaimer.

Research output — not a recommendation. The reader decides whether to act.
