# Regime-Aware Swing Momentum Scanner — Validation Plan

**Primary phase:** 2G Historical Validation / Backtesting  
**Status:** Planned

## Objective

Determine whether the engine has measurable trading edge.

The goal is not to prove current rules correct. The goal is to discover what works, in which regimes/setups, with what expectancy, risk and execution-capture rate.

## Validation principles

1. No look-ahead bias
2. Completed-session data only
3. Point-in-time universe where practical
4. Point-in-time event data where practical
5. Separate signal date from outcome window
6. Validate Candidate and Entry Quality separately
7. Do not tune on the final holdout sample
8. Keep validation and calibration separate
9. Record data-provider confidence
10. Preserve UNKNOWN/failed states rather than silently filling them

## Validation layers

### A — Data Reliability
- bar completeness
- session accuracy
- timestamps
- corporate actions
- provider agreement
- volume consistency

### B — Signal Quality
- Candidate Quality
- Entry Quality
- Actionability
- setup classification

### C — Trade Outcome
- R return
- MAE
- MFE
- stop-out rate
- T1/T2 hit
- time-to-target

### D — Execution Capture
- signal capturability
- slippage
- missed triggers
- Missed Opportunity R

## Variables to test

### Candidate features
- structural trend
- RS level
- RS direction
- stress resilience
- liquidity
- durability

### Entry features
- absolute momentum
- momentum change
- setup type
- contextual volume
- extension
- ATR
- stop geometry
- R:R
- trigger quality

### Context features
- market regime
- event proximity
- earnings confidence
- price-data confidence
- volume-data confidence

## Primary metrics

### Return / expectancy
- Average R
- Median R
- Expectancy
- Win rate
- Profit factor

### Risk
- Max drawdown
- Average loss R
- MAE
- tail-loss distribution

### Opportunity
- T1 hit rate
- T2 hit rate
- MFE
- time-to-target

Segment by:
- Candidate grade
- Entry grade
- Action
- regime
- setup type
- extension bucket
- RS bucket
- stress resilience
- volume quality
- stop width
- event risk

## Candidate vs Entry validation

Test four quadrants:

1. High Candidate + High Entry
2. High Candidate + Low Entry
3. Low Candidate + High Entry
4. Low Candidate + Low Entry

Expected hypothesis: High Candidate + High Entry should produce the strongest actionable cohort; High Candidate + Low Entry should often improve after repair rather than be chased.

## RS Quality test

Compare current aggregate RS Edge against:

> `RS Level + RS Direction + Stress Resilience`

Stress windows:
- 6 sessions
- 10 sessions
- 12 sessions

Leading hypothesis: **10 actual stress sessions**.

Stress metrics:
- Beat Rate
- Downside Capture
- relative drawdown
- tail resilience

## Contextual Volume test

Compare simple volume ratio against setup-aware interpretation:
- breakout expansion
- pullback contraction
- consolidation dry-up
- reversal/repair expansion
- breakdown expansion

## Absolute vs Relative Momentum test

Compare:
- absolute momentum only
- relative momentum only
- separated features
- current blended architecture

## Extension / No-Chase test

Current frozen rule: >8% above EMA20 or RSI >=75.

Test extension buckets:
- <=2%
- 2–4%
- 4–6%
- 6–8%
- 8–10%
- >10%

Measure forward expectancy, MAE, MFE and stop-out rate.

## Stop geometry test

Current hard cap: **10%**.

Test:
- stop %
- stop distance in ATR
- setup-adjusted stop width

## Forward Test Lab

Required fields:
- signal date/time
- candidate grade
- entry grade
- action
- trigger
- planned entry zone
- actual fill/no fill
- stop
- targets
- outcome
- missed-opportunity reason

Required metrics:
- Signal Capture Rate
- Missed Opportunity R
- fill slippage
- trigger latency
- invalidated-before-entry rate

## CF Case Study #001 integration

CF proved:
- signal found
- actionability passed
- trigger identified
- opportunity missed

Therefore:

> **Signal Quality ≠ Execution Capture**

Do not reduce standards to improve capture. Improve execution architecture.

## Staged execution research

> ACTIONABLE-A — Starter  
> ACTIONABLE-B — Add  
> ACTIONABLE-C — Full Trigger

Test:
- starter usefulness
- add timing
- max chase/fill limits
- fixed total portfolio risk

## Sample discipline

Suggested structure:
- development sample
- validation sample
- final holdout sample

Avoid repeated tuning on the same full history.

## Calibration gate

Phase 2H begins only when:
- data reliability acceptable
- enough historical observations exist
- forward-test process running
- P1 hypotheses have measurable results
- parameter changes are statistically/economically justified

## Final Phase 2G outputs

1. feature-performance tables
2. regime-performance tables
3. setup-performance tables
4. Candidate vs Entry matrix
5. RS-quality study
6. Contextual Volume study
7. extension/no-chase study
8. stop-geometry study
9. data-confidence study
10. execution-capture study
11. evidence-backed calibration candidates for Phase 2H

Every conclusion should be classified as:
- validated
- inconclusive
- rejected
- future research
