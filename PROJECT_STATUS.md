# Regime-Aware Swing Momentum Scanner — Project Status

**Status date:** 2026-09-04  
**Repository:** `tradewithedge/swing-momentum-scanner`  
**Formal live baseline:** `v7.8.1-RETRIEVAL-RECOVERY-HOTFIX`  
**Scoring/data compatibility key:** `v7.4.5-P2C-FREEZE`

## Executive status

The project has largely completed the **reliable scanner** stage and is advanced in the **decision-engine** stage. It is **not yet a statistically validated trading system**.

> Reliable Data → Reliable Signals → Candidate Lifecycle → Execution Capture → Historical Validation → Forward Validation → Calibration → Portfolio Integration → Production

## Frozen philosophy

- **QUALITY COMES FIRST**
- **TRADE WITH EDGE**
- **NO CHASE**
- **NO TRADE is a valid decision**
- **Candidate Quality ≠ Entry Quality ≠ Action**
- An A/A+ stock is not automatically an A/A+ actionable entry
- Entry quality is a hard gate
- Low-confidence or ambiguous data must fail closed

## Current functional status

| Area | Status | Notes |
|---|---|---|
| Core scanner | Mature | Large-universe scanner working |
| Market regime | Implemented | Regime-first architecture |
| Candidate Quality | Implemented | Structural stock quality |
| Entry Quality | Implemented | Tactical entry quality |
| Actionability | Implemented | LONG / WAIT / blocked logic |
| Trade Plan | Implemented | Entry, stop, targets, R multiples |
| Completed-session protection | Strong | No in-progress daily bars |
| Price Data Confidence | Strong | Fail-closed design |
| Fundamental/Event Confidence | Good | UNKNOWN distinct from safe |
| Provider health | Implemented | CORE / RECOVERY / UNIVERSE / ANCILLARY |
| Circuit breakers | Implemented | Route-scoped containment |
| Durable recovery | Implemented | GitHub last-good snapshots |
| Responsive UX | Completed | Phase 2E.1 |
| Decision-first ticker UX | Completed | Phase 2E.2 |
| Final scanner UX | **NEXT** | Phase 2E.3 |
| Candidate lifecycle | Planned | Phase 2F |
| Alpaca shadow benchmark | Planned | External connection tested |
| IBKR portfolio/execution layer | Planned | External connection tested |
| Historical validation | Major work remaining | Phase 2G |
| Calibration | Planned | Phase 2H |
| Portfolio risk | Planned | Phase 2I |
| Workflow automation | Planned | Phase 2J |
| Production freeze | Future | v3.0 |

## Frozen rules at current checkpoint

- `MIN_ACTIONABLE_CANDIDATE_GRADES = {"A+", "A", "B+"}`
- Extended if >8% above EMA20 **or** RSI >= 75
- Oversold if >8% below EMA20 **or** RSI <= 25
- Earnings hard block: 3 days
- Earnings caution: 14 days
- Stop hard cap: 10%
- Completed XNYS sessions only
- 120-minute post-close publication buffer
- Scan reuse up to 15 minutes
- Stale/in-progress/ambiguous price data fails closed

## Reliability architecture

Current price-history chain:

1. Yahoo/yfinance primary
2. Yahoo `Ticker.history` recovery
3. Stooq individual-ticker fallback
4. GitHub durable last-good recovery
5. Fail closed if reliable OHLCV cannot be obtained

The VRT retrieval failure led directly to the `v7.8.1` recovery hotfix.

## Event/fundamental reliability

The engine separates **Price Data Confidence** from **Fundamental/Event Data Confidence**. A missing earnings date must never be treated as `Earnings Risk=False`; use **UNKNOWN** when timing cannot be verified.

## Scoring semantics

### Momentum Score
- 40% Daily (1 trading day)
- 35% Weekly (5 trading days)
- 25% Monthly (20 trading days)
- each component capped at -100…+100

### RS Edge
- 20% RS1M
- 35% RS3M
- 45% RS6M

### Trade-plan R:R transparency
Retain midpoint-based R:R, but also show:
- R:R at midpoint
- R:R across full entry zone
- T1 R
- T2 R

## Current research hypotheses

These are **not production scoring changes**:

1. Relative Strength Quality = RS Level + RS Direction + Stress Resilience
2. Contextual Volume Quality
3. Candidate vs Entry Feature Separation
4. Absolute vs Relative Momentum Separation
5. Data Provider Reliability + Volume Data Confidence

## External connections

### Alpaca
Free paper account available. Historical stock access tested successfully. Initial approved role: **SHADOW / VALIDATION**, not primary. Not yet integrated into `app.py`.

### IBKR
Connectivity tested successfully. Preferred long-term role: **Portfolio + Risk + Execution**. Not yet integrated into `app.py`.

## Immediate sequence

1. Phase 2E.3 — Final Scanner UX
2. Phase 2F — Candidate Lifecycle
3. Alpaca Data Reliability Benchmark
4. Phase 2G — Historical Validation / Backtesting + Forward Test Lab
5. Phase 2H — Calibration
6. Phase 2I — Portfolio / Risk Integration
7. Phase 2J — Workflow Automation
8. v3.0 — Production freeze

## Development rule

Do not change weights, thresholds or gates because of one ticker.

> Observation → Hypothesis → Historical Test → Forward Test → Evidence → Calibration
