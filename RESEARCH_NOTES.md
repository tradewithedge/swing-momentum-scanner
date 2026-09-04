# Regime-Aware Swing Momentum Scanner — Research Notes

**Status:** Active hypotheses. Nothing here is an approved production scoring change unless explicitly stated.

## Research governance

> Case Study → Hypothesis → Historical Validation → Forward Validation → Evidence → Calibration

One stock can reveal a weakness. One stock cannot determine a production parameter.

## P1 — Relative Strength Quality

Problem: aggregate RS Edge can hide near-term leadership change.

Research model:

> **RS Quality = RS Level + RS Direction + Stress Resilience**

### RS Level
Magnitude of outperformance vs SPY across horizons.

### RS Direction
Classify improving / stable / deteriorating.

Possible methods:
- RS-line slope
- short-vs-long horizon differential
- recent RS acceleration/deceleration

### Stress Resilience
Evaluate behavior on actual weak/stress SPY sessions.

Candidate metrics:
- Beat Rate
- Downside Capture
- relative drawdown
- tail resilience

## Stress-window research

A manual PLTR analysis used 6 stress sessions. That was a quick sample, not a frozen rule.

Preferred starting hypothesis:

> **10 most recent actual stress sessions**

Phase 2G should compare 6 vs 10 vs 12 and select based on predictive/expectancy evidence.

## P1 — Contextual Volume Quality

Simple `high volume = good / low volume = bad` rules are structurally wrong.

Research model:

> **Volume Quality = f(Setup Type, Price Structure, Volume Behavior)**

- Breakout: prefer expansion
- Pullback: contraction can be constructive
- Tight consolidation: dry-up can be constructive
- Reversal/repair: expansion preferred
- Breakdown: expansion can confirm distribution

## P1 — Candidate vs Entry Feature Separation

Candidate features should emphasize persistent traits:
- structural trend
- leadership
- resilience
- durability
- liquidity

Entry features should emphasize tactical traits:
- short-term momentum
- momentum change
- setup maturity
- contextual volume
- extension
- stop geometry
- R:R
- trigger quality

BDX was a useful case where structural quality and tactical entry quality diverged.

## P1 — Absolute vs Relative Momentum

> **Absolute Momentum ≠ Relative Momentum**

A stock can decelerate in absolute terms while outperforming SPY.

Research separate features before blending.

## P1 — Data Provider Reliability / Volume Data Confidence

Different providers can show nearly identical OHLC but materially different volume totals.

Future volume-derived features should consider:

> **Volume Data Confidence**

Potential states:
- HIGH
- MEDIUM
- LOW
- UNKNOWN

Possible inputs:
- feed type
- missing bars
- provider disagreement
- session completeness
- fallback route
- corporate-action consistency

## Case-study ledger

| Ticker | Lesson |
|---|---|
| AMZN | Aggregate RS can hide deteriorating leadership |
| EXPD | Low volume can be constructive by setup |
| BDX | Tactical weakness may contaminate Candidate Quality |
| MSI | Absolute momentum ≠ relative momentum |
| PLTR | Strong candidate can still be WAIT when extended |
| CF | Good signal can still fail at execution capture |

## CF execution lesson

The problem was not discovery. It was execution capture.

Future research:
- staged execution
- starter position before full trigger
- add-on rules
- max chase/fill limits
- conditional breakout planning
- fixed total portfolio risk

Required metrics:
- Signal Capture Rate
- Missed Opportunity R
- slippage
- missed trigger count
- invalidated-before-fill rate

## Research priority

1. Data-provider reliability
2. Candidate lifecycle
3. RS Quality
4. Contextual Volume Quality
5. Candidate vs Entry separation
6. Absolute vs Relative Momentum
7. Historical validation
8. Forward validation
9. Calibration

The objective is not more indicators. The objective is measurable improvement in expectancy, risk and execution quality.
