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
| Candidate Quality | Implemented | Decision-layer separation implemented; feature-level separation remains a P1 research item |
| Entry Quality | Implemented | Full ticker-level decision engine; scanner-level entry readiness is intentionally partial |
| Actionability | Implemented | Full ticker-level LONG / WAIT / blocked logic |
| Scanner actionability | Partial by design | Price-ready scanner rows require **VERIFY EVENT + STOP** before full actionability |
| Trade Plan | Implemented | Entry, stop, targets, R multiples |
| Completed-session protection | Strong | XNYS calendar normally used; documented fallback exists if calendar resolution is unavailable |
| Price Data Confidence | Strong | User-visible decision is fail-closed |
| Diagnostic encapsulation | Architecture seam | `compute_search_diagnostic()` is not itself fully data-confidence-gated; UI applies final confidence block |
| Fundamental/Event Confidence | Good | UNKNOWN distinct from safe |
| Provider health | Implemented | CORE / RECOVERY / UNIVERSE / ANCILLARY |
| Circuit breakers | Implemented | Route-scoped containment |
| Durable recovery | Implemented | GitHub last-good **scanner snapshot** recovery |
| Responsive UX | Completed | Phase 2E.1 |
| Decision-first ticker UX | Completed | Phase 2E.2 |
| Final scanner UX | **NEXT** | Phase 2E.3 |
| Candidate lifecycle | Planned | Phase 2F |
| Alpaca shadow benchmark | Planned | External connection tested |
| IBKR portfolio/execution layer | Planned | External connection tested |
| Historical validation | Major work remaining | Phase 2G |
| Automated regression suite | Not yet present | Add during Phase 2G / production hardening |
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
- Completed-session signals are the operating rule
- XNYS calendar is the normal session source
- 120-minute post-close publication buffer
- Scan reuse up to 15 minutes
- Stale/in-progress/ambiguous price data fails closed

### Session-control implementation seam

The normal implementation uses `exchange_calendars` / XNYS. If the calendar library is unavailable or errors, current code contains a weekday / approximately 18:00 ET fallback. This is a **resilience fallback**, not the preferred signal-source rule.

Before v3.0, explicitly decide whether production should validate/retain this fallback or fail closed when XNYS calendar resolution is unavailable.

## Reliability architecture

### Individual ticker fresh OHLCV recovery

1. Yahoo/yfinance primary
2. Yahoo `Ticker.history` recovery
3. Stooq individual-ticker fallback / repair
4. Fail closed if reliable fresh ticker history cannot be obtained

### System-level scanner recovery

GitHub durable storage is **not an arbitrary individual-ticker OHLCV fallback**.

Its role is:

> **LAST-GOOD SCANNER SNAPSHOT RECOVERY**

It preserves/restores durable scanner-universe state when a fresh scan is unusable or the Streamlit container is replaced.

Failed/empty fresh scans must never overwrite good durable scanner state.

## Candidate / Entry / Action scope clarification

### Ticker decision engine

The ticker workflow performs the fullest current decision sequence:

> Event → Candidate Quality → Directional Structure → Location → Stop/R:R → Data Confidence override

### Scanner

The universe scanner intentionally avoids expensive full event/stop verification for every ticker.

A scanner row that is price-ready may therefore show:

> **VERIFY EVENT + STOP**

This means:

> **price-ready candidate, not yet fully ACTIONABLE**

Phase 2E.3 must make this distinction visually explicit.

## Data-confidence encapsulation seam

Current user-visible ticker behavior is correctly fail-closed: low data confidence suppresses actionable entry/stop/targets.

However, the final data-confidence override is applied outside `compute_search_diagnostic()` in the UI flow.

Future Phase 2F lifecycle, APIs, backtests or automation must not reuse the diagnostic function without reproducing or centralizing this block.

## Candidate vs Entry feature-separation seam

The decision/UI architecture already distinguishes Candidate Quality from Entry Quality.

However, the frozen Candidate scoring still contains some tactical features such as entry location, volume, short-term momentum/acceleration, ATR/risk and regime fit.

Therefore:

> **Decision-layer separation = implemented**  
> **Feature-level separation = not yet complete**

Feature migration/reweighting remains a **P1 research hypothesis** and must not be changed before Phase 2G evidence.

## Cold-start continuity audit — PASS

On 2026-09-04, a fresh ChatGPT session with no prior project context reconstructed the project using only the GitHub repository documentation and code.

It correctly identified the frozen baseline, compatibility key, Candidate/Entry/Action architecture, current provider roles, Alpaca and IBKR roles, exact NEXT phase, roadmap through v3.0, and the distinction between a frozen baseline and validated edge.

**Continuity status: PASS / FREEZE.**

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
