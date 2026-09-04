# Regime-Aware Swing Momentum Scanner — Roadmap

**Status date:** 2026-09-04  
**Current formal baseline:** `v7.8.1-RETRIEVAL-RECOVERY-HOTFIX`

## Guiding sequence

> **Find Edge → Execute Edge → Prove Edge → Improve Edge**

Do not calibrate before validation. Do not automate execution before risk architecture is ready. Do not weaken entry standards to increase trade count.

## Phase 2E.3 — Final Market Scanner Decision UX

**Status:** NEXT

Target hierarchy:

> Ticker → Candidate Quality → Entry Status → Action → Main Reason

Main-reason examples:
- Extended
- RS deteriorating
- Setup not mature
- Wide stop
- Earnings caution
- Volume confirmation missing
- Ready after repair
- Trigger confirmed

**Exit condition:** scanner is decision-readable on desktop/mobile without requiring raw-indicator interpretation.

After completion: **freeze UX except defects**.

## Phase 2F — Candidate Lifecycle / State Tracking

**Status:** Planned

Proposed states:

> `DISCOVERED → WATCH → DEVELOPING → READY → TRIGGER → ACTIVE / INVALIDATED`

Minimum tracked fields:
- ticker
- first discovered date
- latest state
- candidate grade
- entry grade
- main reason
- setup type
- trigger
- invalidation
- extension state
- event-risk state
- state-transition history

**Exit condition:** setup progression can be tracked across completed sessions.

## P1 — Alpaca Data Reliability Benchmark

**Status:** Planned; connection tested

Keep production architecture unchanged during benchmark:
- Yahoo/yfinance = PRIMARY
- Stooq = RECOVERY
- GitHub = LAST-GOOD
- Alpaca = SHADOW / VALIDATION

Initial benchmark tickers:
- AAPL
- PLTR
- IBKR
- AMZN
- EXPD
- BDX
- MSI
- VRT
- SMTC

Then test Nasdaq-100 and S&P 500.

Metrics:
- OHLC discrepancies
- Yahoo volume vs Alpaca SIP volume
- missing/stale bars
- session/timestamp mismatches
- corporate-action consistency
- request failures/retries
- latency
- batch stability
- pagination stability
- coverage

**Rule:** use historical SIP for daily full-market volume; do not use IEX volume for scanner volume decisions.

Possible future architecture if validated:

> Alpaca = Primary EOD OHLCV → Yahoo = Secondary → Stooq = Tertiary Recovery → GitHub = Last-Good

## Phase 2G — Historical Validation / Backtesting

**Status:** Major work remaining

Validate:
- Candidate grade
- Entry grade
- regime
- momentum
- momentum change
- RS
- RS direction
- stress resilience
- extension
- volume/contextual volume
- ATR
- setup type
- stop geometry
- R:R
- event proximity
- data-confidence states

Core metrics:
- expectancy
- average/median R
- win rate
- profit factor
- max drawdown
- MAE
- MFE
- results by grade/regime/setup

### Stress resilience research
Compare 6 vs 10 vs 12 actual stress sessions. Leading hypothesis: **10 most recent actual stress sessions**, not the last 10 trading days.

Potential metrics:
- Beat Rate
- Downside Capture
- relative drawdown
- tail resilience

## Forward Test Lab

Measure both **Signal Quality** and **Execution Capture**.

Required metrics:
- Signal Capture Rate
- Missed Opportunity R
- entry slippage
- trigger-to-fill delay
- invalidated-before-entry rate

### CF Case Study #001
The scanner found CF early, passed ACTIONABLE, and identified resistance/trigger around $133, but execution capture failed.

Do not loosen standards. Improve execution architecture.

Future staged execution research:
- ACTIONABLE-A — Starter
- ACTIONABLE-B — Add
- ACTIONABLE-C — Full Trigger
- conditional breakout planning
- maximum chase/fill limits
- fixed total portfolio risk

## Phase 2H — Calibration

Only after sufficient evidence.

Potential calibration targets:
- Candidate weights
- Entry weights
- grade thresholds
- RS weights
- extension thresholds
- contextual volume rules
- setup penalties/bonuses
- stop geometry
- event-risk penalties

## Phase 2I — Portfolio / Risk Integration

Preferred broker layer: **IBKR**.

Potential functions:
- NAV
- cash
- positions
- sector exposure
- portfolio heat
- correlation
- open risk
- position sizing
- order preparation

## Phase 2J — Workflow Automation

Desired flow:

> Market Regime → Universe Scan → Candidate Ranking → Lifecycle Update → READY/TRIGGER → Event Verification → Position Sizing → Portfolio Risk Check → Execution Plan

Default objective: disciplined, auditable decision support — not autonomous trading.

## v3.0 — Production Freeze

Target characteristics:
- validated data architecture
- validated scoring
- calibrated thresholds
- stateful lifecycle
- forward-test evidence
- portfolio-risk integration
- stable UX
- reproducible deployment
- explicit operating manual
