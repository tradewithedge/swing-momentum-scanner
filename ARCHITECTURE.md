# Regime-Aware Swing Momentum Scanner — Architecture

**Current formal baseline:** `v7.8.1-RETRIEVAL-RECOVERY-HOTFIX`  
**Status date:** 2026-09-04

## 1. Architectural objective

> **Data Reliability → Market Regime → Candidate Quality → Entry Quality → Actionability → Trade Plan → Lifecycle → Portfolio Risk → Execution**

## 2. Decision layers

### Candidate Quality
Question: **Is this a high-quality swing candidate?**

Persistent traits should dominate.

### Entry Quality
Question: **Is this a good place to enter now?**

Tactical factors include setup maturity, contextual volume, extension, stop geometry and R:R.

### Actionability
Question: **Should the system act now?**

A strong candidate can still be WAIT.

### Current feature-separation status

The **decision layer** already separates Candidate Quality, Entry Quality and Actionability.

The **feature layer** is not yet perfectly separated. Current frozen Candidate scoring still includes some tactical inputs such as entry location, volume, short-term momentum/acceleration, ATR/risk and regime fit.

This remains:

> **P1 — Candidate vs Entry Feature Separation**

Do not move or reweight these features before Phase 2G validation.

## 3. Price-data architecture

### Individual ticker fresh history

- Yahoo/yfinance — PRIMARY
- Yahoo `Ticker.history` — RECOVERY
- Stooq — individual-ticker RECOVERY / repair
- fail closed if reliable fresh OHLCV is unavailable

### Scanner durable recovery

- GitHub durable snapshots — **LAST-GOOD SCANNER SNAPSHOT RECOVERY**

GitHub durable recovery restores persisted scanner-universe snapshots. It is not a fourth arbitrary per-ticker historical-data API.

### Future candidate

- Alpaca historical SIP — SHADOW / VALIDATION first

## 4. Session integrity

Normal path:

- XNYS calendar via `exchange_calendars`
- completed-session signal selection
- 120-minute post-close publication buffer
- stale/in-progress daily bars rejected
- ambiguous timestamps fail closed

### Resilience fallback seam

Current code includes a weekday / approximately 18:00 ET fallback if the XNYS calendar library is unavailable or errors.

Treat this as:

> **resilience behavior, not exact exchange-calendar equivalence**

Before v3.0, explicitly decide whether to validate/retain this fallback or replace it with a strict fail-closed policy.

## 5. Trade Plan and data-confidence enforcement

A fully actionable ticker trade plan requires:

1. Candidate gate passes
2. Entry gate passes
3. Event risk acceptable
4. Stop/R:R acceptable
5. Price Data Confidence acceptable

Stop hard cap: **10%**.

### Current encapsulation seam

`compute_search_diagnostic()` can compute structural/actionable geometry before the final UI-level Price Data Confidence override.

The UI currently corrects this safely by converting low-confidence results into a data issue and clearing actionable levels.

Therefore:

> **Current user-visible behavior = fail closed**  
> **Diagnostic function alone = not yet self-contained fail closed**

Before reuse in Phase 2F lifecycle, APIs, automation or backtesting, centralize the confidence gate.

## 6. Scanner vs ticker actionability

### Ticker workflow
Performs the fullest current verification, including event and stop/R:R checks.

### Scanner workflow
Performs price/candidate readiness without full expensive per-ticker event and stop verification.

Therefore:

> **VERIFY EVENT + STOP = PRICE READY, not yet fully ACTIONABLE**

Phase 2E.3 must preserve this distinction.

## 7. Future RS Quality

> **RS Quality = RS Level + RS Direction + Stress Resilience**

Research only until validated.

## 8. Future Contextual Volume

> **Volume Quality = f(Setup Type, Price Structure, Volume Behavior)**

Research only until validated.

## 9. Future provider architecture

If Alpaca benchmark passes:

> Alpaca SIP Historical = Primary EOD OHLCV  
> Yahoo = Secondary / corroboration  
> Stooq = Tertiary recovery  
> GitHub = Last-good scanner snapshot recovery

IBKR remains:

> **Portfolio + Risk + Execution**

## 10. Validation / regression architecture gap

The repository does not yet contain a formal automated historical-validation engine or comprehensive regression test suite.

Before v3.0, add repeatable regression tests around at least:

- completed-session resolution
- Price Data Confidence
- event UNKNOWN handling
- extension/no-chase gates
- stop hard cap
- scanner/ticker actionability semantics
- recovery-state behavior

## 11. Security

Never commit Alpaca secrets, IBKR credentials or GitHub PATs. Use **Streamlit Secrets**.
