# Regime-Aware Swing Momentum Scanner — Changelog

This changelog records major accepted checkpoints and architecture lessons.

## `v7.8.1-RETRIEVAL-RECOVERY-HOTFIX` — ACCEPTED & FROZEN

Purpose: strengthen ticker-level history recovery after VRT exposed a valid-public-data / Yahoo-route retrieval failure.

Key outcomes:
- Yahoo/yfinance remains primary
- strengthened `Ticker.history` recovery
- retained Stooq fallback
- protected durable last-good snapshots
- failed/empty fresh retrieval must not overwrite valid durable state
- fail closed when reliable OHLCV cannot be recovered

Validation included VRT, AMZN and SMTC.

Scoring/data compatibility remains `v7.4.5-P2C-FREEZE`.

## `v7.8.0-P2E2-DECISION-FIRST-UX` — ACCEPTED

- Candidate Quality first
- Entry Quality separated visibly
- Action surfaced prominently
- Price Data Confidence visible
- technical evidence below decision layer

## Phase 2E.1 — Responsive UX — ACCEPTED

- responsive cards
- desktop/tablet/mobile layouts
- larger readable labels
- no truncation of key trading values

## Phase 2D — Reliability / Provider Health

- retries/backoff for app-owned HTTP
- route-scoped circuit breakers
- provider telemetry
- CORE / RECOVERY / UNIVERSE / ANCILLARY roles
- HEALTHY / DEGRADED / FAILED states
- latency alone cannot trigger FAILED
- ancillary failure isolated from scanner-price health

## Phase 2C — Scoring / Event / Fundamental Freeze

- scoring compatibility frozen at `v7.4.5-P2C-FREEZE`
- Candidate Quality separated from Entry Quality
- actionable minimum grades: A+, A, B+
- earnings hard block 3 days
- earnings caution 14 days
- stop hard cap 10%
- missing earnings date must not imply no earnings risk

## Trade Plan R:R transparency

Accepted requirement:
- retain midpoint R:R
- label midpoint R:R
- show R:R across entry zone
- show T1 and T2 R multiples

## Completed-session integrity

Accepted:
- XNYS calendar
- completed-session only
- 120-minute post-close publication buffer
- stale/in-progress/ambiguous data fails closed

## Durable recovery

Accepted:
- GitHub durable store
- last-good persistence
- failed/empty fresh scans never overwrite good state

## CF Case Study #001 — Execution Capture Lesson

The scanner found CF early, passed ACTIONABLE and identified resistance/trigger around $133, but the opportunity was missed.

Lesson:

> **Find Edge → Execute Edge → Prove Edge → Improve Edge**

Future research:
- ACTIONABLE-A Starter
- ACTIONABLE-B Add
- ACTIONABLE-C Full Trigger
- conditional breakout planning
- maximum chase/fill limits
- fixed total portfolio risk
- Signal Capture Rate
- Missed Opportunity R

## Cross-engine research ledger

### AMZN
Aggregate RS can hide deteriorating recent leadership.

### EXPD
Low volume can be constructive depending on setup.

### BDX
Short-term tactical weakness may contaminate Candidate Quality.

### MSI
Absolute momentum and relative momentum are not the same.

### PLTR
A/A+ Candidate can still be WAIT when extended.

## 2026-09 — Alpaca benchmark added

Decision: no immediate provider replacement.

Current:
- Yahoo = PRIMARY
- Stooq = RECOVERY
- GitHub = LAST-GOOD
- Alpaca = SHADOW / VALIDATION

Historical SIP is the intended benchmark feed for completed-session daily OHLCV/volume.

## 2026-09 — IBKR role clarified

IBKR connectivity tested.

Preferred long-term role:

> **Portfolio + Risk + Execution**

Potential functions: NAV, cash, positions, P&L, exposure, sizing and order workflow.

## Current NEXT

> **Phase 2E.3 — Final Market Scanner Decision UX**
