# Regime-Aware Swing Momentum Scanner — Architecture

**Current formal baseline:** `v7.8.1-RETRIEVAL-RECOVERY-HOTFIX`  
**Status date:** 2026-09-04

## 1. Architectural objective

The system is a layered decision engine, not a simple indicator dashboard:

> **Data Reliability → Market Regime → Candidate Quality → Entry Quality → Actionability → Trade Plan → Lifecycle → Portfolio Risk → Execution**

## 2. Decision layers

### Market Regime
Assessed before individual stocks to contextualize momentum and opportunity.

### Candidate Quality
Question: **Is this a high-quality swing candidate?**

Persistent traits should dominate:
- structural trend
- leadership
- resilience/durability
- liquidity
- longer-horizon quality

### Entry Quality
Question: **Is this a good place to enter now?**

Tactical factors:
- current momentum
- momentum change
- setup maturity
- contextual volume
- extension
- stop geometry
- R:R
- trigger quality

### Actionability
Question: **Should the system act now?**

A strong candidate can still be WAIT, NOT READY, event-blocked, extended or poor-R:R.

## 3. Price-data architecture

### Current
- Yahoo/yfinance — PRIMARY
- Yahoo `Ticker.history` — RECOVERY
- Stooq — RECOVERY
- GitHub durable snapshots — LAST-GOOD RECOVERY

### Future candidate
- Alpaca historical SIP — SHADOW / VALIDATION first

## 4. Session integrity

- XNYS calendar
- completed-session only
- 120-minute post-close publication buffer
- stale/in-progress daily bars rejected
- ambiguous timestamps fail closed

This protects close, RSI, ATR, moving averages, momentum, volume ratio and RS from partial-session contamination.

## 5. Provider health

Roles:
- CORE
- RECOVERY
- UNIVERSE
- ANCILLARY

States:
- HEALTHY
- DEGRADED
- FAILED

Rules:
- latency alone must not force FAILED
- ancillary failures must not automatically poison price confidence
- recovery failure may reduce repair capacity without invalidating already-good data

## 6. Fundamental / Event architecture

Price confidence and event/fundamental confidence are separate.

Required behavior:
- negative EPS → P/E N/M
- missing earnings date ≠ safe
- earnings may be confirmed, estimated, conflicted or unknown
- UNKNOWN must be handled explicitly

Current windows:
- hard block: 3 days
- caution: 14 days

## 7. Core indicator semantics

### Momentum Score
- 40% Daily
- 35% Weekly
- 25% Monthly

Daily = 1 trading day, Weekly = 5, Monthly = 20. Components capped -100…+100.

### RS Edge
- 20% RS1M
- 35% RS3M
- 45% RS6M
- benchmark: SPY

### Extension
- Extended if >8% above EMA20 or RSI >=75
- Oversold if >8% below EMA20 or RSI <=25

## 8. Trade Plan

Generated only when:
1. Candidate gate passes
2. Entry gate passes
3. Data confidence acceptable
4. Event risk acceptable

Communicate:
- entry zone
- stop
- stop %
- T1/T2
- R:R at midpoint
- R:R across entry zone
- T1 R
- T2 R

Stop hard cap: **10%**.

## 9. Planned lifecycle architecture

> `DISCOVERED → WATCH → DEVELOPING → READY → TRIGGER → ACTIVE / INVALIDATED`

State transitions must use completed-session data.

## 10. Future RS Quality

Research model:

> **RS Quality = RS Level + RS Direction + Stress Resilience**

Stress metrics may include Beat Rate, Downside Capture, relative drawdown and tail resilience.

## 11. Future Contextual Volume

Research model:

> **Volume Quality = f(Setup Type, Price Structure, Volume Behavior)**

| Setup | Constructive behavior |
|---|---|
| Breakout | Expansion |
| Pullback | Contraction can be positive |
| Tight consolidation | Dry-up can be positive |
| Reversal / repair | Expansion preferred |
| Breakdown | Expansion can confirm distribution |

## 12. Future provider architecture

If Alpaca benchmark passes:

> Alpaca SIP Historical = Primary EOD OHLCV  
> Yahoo = Secondary / corroboration  
> Stooq = Tertiary recovery  
> GitHub = Last-good recovery

IBKR remains:

> **Portfolio + Risk + Execution**

## 13. Security

Never commit or paste into public files:
- Alpaca key/secret
- IBKR credentials
- GitHub PAT

Use **Streamlit Secrets**.

Initial Alpaca integration is read-only market data. No order execution during benchmarking.
