# Regime-Aware Swing Momentum Scanner — Changelog

## 2026-09-04 — Cold-Start Continuity Audit — PASS / FREEZE

A fresh ChatGPT session with no prior project context reconstructed the project using only the GitHub repository documentation and current code.

It correctly identified:

- `v7.8.1-RETRIEVAL-RECOVERY-HOTFIX` as formal frozen baseline
- `v7.4.5-P2C-FREEZE` as scoring/data compatibility key
- Candidate Quality ≠ Entry Quality ≠ Action
- Yahoo / Stooq / GitHub current provider roles
- Alpaca = SHADOW / VALIDATION
- IBKR = Portfolio + Risk + Execution
- Phase 2E.3 as exact NEXT
- roadmap through v3.0
- frozen baseline ≠ statistically validated edge

**Continuity result: PASS.**

### Architecture seams identified and documented

1. **Candidate vs Entry separation**
   - decision/UI separation is implemented
   - feature-level separation is not yet complete
   - remains P1 research

2. **GitHub durable recovery scope**
   - GitHub restores last-good scanner snapshots
   - it is not an arbitrary individual-ticker OHLCV fallback

3. **XNYS session fallback**
   - normal path uses XNYS / `exchange_calendars`
   - current code has a weekday / approximately 18:00 ET resilience fallback if calendar resolution fails

4. **Price Data Confidence encapsulation**
   - user-visible ticker flow is fail-closed
   - `compute_search_diagnostic()` itself is not fully self-contained for the final data-confidence hard block

5. **Scanner vs ticker actionability**
   - scanner `VERIFY EVENT + STOP` is price-ready, not fully actionable
   - Phase 2E.3 must preserve this distinction

6. **Regression-suite gap**
   - no comprehensive repository-contained validation/regression harness exists yet
   - add during Phase 2G / production hardening

## `v7.8.1-RETRIEVAL-RECOVERY-HOTFIX` — ACCEPTED & FROZEN

- Yahoo/yfinance remains primary
- strengthened `Ticker.history` recovery
- retained Stooq fallback
- protected durable last-good scanner snapshots
- failed/empty fresh retrieval must not overwrite valid durable state
- scoring/data compatibility remains `v7.4.5-P2C-FREEZE`

## `v7.8.0-P2E2-DECISION-FIRST-UX` — ACCEPTED

- Candidate Quality first
- Entry Quality separated visibly
- Action surfaced prominently
- Price Data Confidence visible
- technical evidence below decision layer

## Current NEXT

> **Phase 2E.3 — Final Market Scanner Decision UX**
