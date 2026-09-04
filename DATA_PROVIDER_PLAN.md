# Regime-Aware Swing Momentum Scanner — Data Provider Plan

**Status date:** 2026-09-04

## Data philosophy

Reliable signals require reliable inputs.

Separate:
- transport success
- data completeness
- session correctness
- price-data confidence
- event/fundamental confidence
- volume-data confidence

HTTP success alone does not prove data is fit for a trading decision.

## Current stack

### Yahoo / yfinance — PRIMARY
Used for historical daily OHLCV and most derived indicators.

Strengths:
- broad coverage
- free
- mature existing integration

Weaknesses:
- unofficial wrapper over Yahoo public interfaces
- occasional route/retrieval failures

### Yahoo `Ticker.history` — RECOVERY
Individual-symbol recovery route.

### Stooq — RECOVERY
Individual-ticker fallback.

### GitHub Durable Store — LAST-GOOD RECOVERY
Rules:
- empty scans never overwrite good snapshots
- failed scans never overwrite good snapshots
- recovered stale data must be clearly labeled

## Alpaca — SHADOW / VALIDATION

Free paper account exists. Historical stock connectivity tested successfully.

Initial role:

> **SHADOW / VALIDATION ONLY**

Do not let Alpaca affect production scoring/action until benchmark evidence supports it.

## Alpaca feed rule

For full-market daily volume research:

> **Use historical SIP data**

Do not use IEX volume for full-market scanner volume decisions.

The existing 120-minute post-close buffer fits completed historical SIP usage.

## Benchmark scope

Initial tickers:
- AAPL
- PLTR
- IBKR
- AMZN
- EXPD
- BDX
- MSI
- VRT
- SMTC

Then:
- Nasdaq-100
- S&P 500

Compare:
- Open
- High
- Low
- Close
- Volume
- date/session identity
- timestamps
- corporate-action adjustments

Reliability metrics:
- missing-bar rate
- stale-bar rate
- OHLC discrepancy rate/magnitude
- volume discrepancy
- session/timestamp mismatch
- retrieval failure rate
- retry count
- latency
- batch success rate
- pagination failures
- malformed responses
- corporate-action consistency

## Volume Data Confidence

Recent cross-provider checks showed similar OHLC with materially different volume totals.

Potential future field:

> `volume_data_confidence`

Potential states:
- HIGH
- MEDIUM
- LOW
- UNKNOWN

Confidence reducers:
- non-consolidated feed
- missing session data
- material provider disagreement
- partial-day bar
- fallback route
- corporate-action inconsistency

## Promotion criteria

Promote Alpaca only if evidence shows:
- materially lower missing/failure rates
- stable completed-session timestamps
- reliable SIP volume
- better large-universe batch behavior
- acceptable latency
- consistent corporate-action handling

Possible future stack if validated:

> **Alpaca SIP Historical = PRIMARY EOD OHLCV**  
> **Yahoo = SECONDARY / corroboration**  
> **Stooq = TERTIARY recovery**  
> **GitHub = LAST-GOOD recovery**

Until then, keep v7.8.1 architecture unchanged.

## IBKR role

IBKR is not the preferred primary large-universe historical feed.

Preferred role:

> **Portfolio + Risk + Execution**

Potential uses:
- live/current quote confirmation
- bid/ask
- account NAV
- cash
- positions
- P&L
- portfolio exposure
- position sizing
- future order workflow

## Long-term separation

> **Alpaca = Market Data Layer**  
> **Quality Engine = Decision Layer**  
> **IBKR = Portfolio / Risk / Execution Layer**

## Security

Never commit:
- Alpaca API key/secret
- IBKR credentials
- GitHub PAT

Use **Streamlit Secrets**.

Initial Alpaca integration must be read-only market data. No order execution during benchmarking.
