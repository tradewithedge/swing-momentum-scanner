# Regime-Aware Swing Momentum Scanner — Roadmap

**Status date:** 2026-09-04  
**Current formal baseline:** `v7.8.1-RETRIEVAL-RECOVERY-HOTFIX`

## Roadmap principle

> **Find Edge → Execute Edge → Prove Edge → Improve Edge**

# Phase 2E.3 — Final Market Scanner Decision UX

**Status:** NEXT

## Objective

Turn the large-universe scanner from an indicator-heavy table into a decision-first surface.

Required primary hierarchy:

> **Ticker → Candidate Quality → Entry Status → Action → Main Reason**

## Critical scope rule

Scanner-level “entry readiness” is intentionally not the same as fully verified ticker actionability.

A scanner row that passes price-entry geometry may still require:

> **VERIFY EVENT + STOP**

Therefore Phase 2E.3 must distinguish:

- **Candidate Quality** — stock/setup quality
- **Entry Status** — price readiness / wait condition
- **Action** — scanner action, including VERIFY EVENT + STOP
- **Main Reason** — dominant reason for WAIT / verification / readiness

Do not relabel `VERIFY EVENT + STOP` as fully ACTIONABLE.

## Main Reason examples

- Data issue
- Candidate quality below gate
- Trend / directional structure
- Extended / chase risk
- Momentum not ready
- Stop / location concern
- Regime misalignment
- Price ready — verify event + stop
- Setup developing

## UX rule

The first visible scanner columns should support a decision without requiring interpretation of RS Rating, Momentum Score, RSI, Volume Ratio, ATR or EMA20 distance. Those remain evidence columns later in the table/details.

## Freeze condition

After Phase 2E.3 is accepted:

> **Freeze UX except defects.**

No scoring weights, thresholds, provider roles or frozen gates change during 2E.3.

# Phase 2F — Candidate Lifecycle / State Tracking

Before lifecycle logic reuses ticker diagnostics, centralize the Price Data Confidence hard block so actionability cannot depend on UI-only enforcement.

Proposed state machine:

> `DISCOVERED → WATCH → DEVELOPING → READY → TRIGGER → ACTIVE / INVALIDATED`

# P1 — Alpaca Data Reliability Benchmark

Current roles remain frozen during benchmark:

- Yahoo/yfinance = PRIMARY
- Stooq = RECOVERY
- GitHub = LAST-GOOD SCANNER SNAPSHOT RECOVERY
- Alpaca = SHADOW / VALIDATION

# Phase 2G — Historical Validation / Backtesting

Validate Candidate grade, Entry grade, regime, RS, stress resilience, contextual volume, extension, ATR, setup type, stop geometry, event proximity and data-confidence states.

Also add repository-contained regression tests for frozen gates and reliability behavior.

# Forward Test Lab

> **Signal Quality ≠ Execution Capture**

Track Signal Capture Rate, Missed Opportunity R, slippage and trigger latency.

# Phase 2H — Calibration

Only after evidence.

# Phase 2I — Portfolio / Risk Integration

Preferred broker layer:

> **IBKR**

# Phase 2J — Workflow Automation

Target:

> Market Regime → Scan → Rank → Lifecycle → READY/TRIGGER → Event Verification → Position Sizing → Portfolio Risk → Execution Plan

# v3.0 — Production Freeze

Requires validated data/scoring, calibrated thresholds, lifecycle, forward-test evidence, regression-test coverage, portfolio-risk integration, stable UX and reproducible deployment.
