# Swing Momentum Scanner — Project Status & Roadmap Reconciliation
**审计日期：2026-11-27**  
**Repository:** `tradewithedge/swing-momentum-scanner`  
**审计基准：** `main` branch；当前 `app.py`；仓库内项目文档；可取得的 GitHub commit history。

> 本文件是基于仓库证据的状态重整稿。GitHub 写入权限被集成端拒绝（HTTP 403），因此本次没有修改 `main`，也没有创建分支或 PR。此文件可作为待提交的文档版本。

---

## 1. Executive status

**当前代码版本：**
- App build: `v7.11.0-P2F2-TRIGGER-FOUNDATION`
- Engine / persisted scanner compatibility key: `v7.4.5-P2C-FREEZE`
- Latest feature commit identified: `76c7f2a272bcba5eebfd2eb59f051a812dd46a54` — Phase 2F.2 Trigger State Foundation (2026-09-11).

**结论：项目已从单纯的扫描与评分工具，发展到具备数据可靠性控制、候选质量/入场决策分层，以及可持久化候选生命周期和基础触发确认的决策支持系统。**

但它还不是经过统计验证的交易系统，也还没有完成完整的候选生命周期、历史回测、前向验证、校准、组合风险及自动化闭环。

### 当前里程碑判断

| Workstream | Repository evidence | Status |
|---|---|---|
| Data retrieval / recovery / confidence controls | `app.py` provider, recovery, freshness and data-confidence flows; `DATA_PROVIDER_PLAN.md` | Implemented baseline; ongoing reliability validation |
| Decision-first scanner UX (Phase 2E.3) | Candidate Quality, Entry Status, Action, Main Reason; separate candidate and price-entry gates; filter semantics in `build_ranked_master()` | Implemented in code; formal acceptance/regression evidence not found |
| Candidate Lifecycle (Phase 2F.1) | SQLite + GitHub durable lifecycle store; WATCH / DEVELOPING / READY / INVALIDATED; history and session controls | Foundation implemented |
| Trigger State (Phase 2F.2) | Prior READY 20D reference; subsequent close confirmation; volume ratio threshold 1.50x; trigger metadata/history/UI | Foundation implemented |
| ACTIVE / portfolio confirmation | Code explicitly reserves ACTIVE for later portfolio/position evidence | Not implemented |
| Historical validation / backtesting (Phase 2G) | `VALIDATION_PLAN.md` defines requirements; no in-repository validation engine or test suite identified | Planned |
| Forward Test Lab | Requirements described in `VALIDATION_PLAN.md`; no operating lab identified | Planned |
| Calibration (Phase 2H) | Research and validation principles documented | Planned |
| Portfolio / risk / IBKR integration (Phase 2I) | Architecture and roadmap intent documented | Planned |
| Workflow automation (Phase 2J) | Roadmap item | Planned |
| Production freeze (v3.0) | Roadmap item | Not reached |

---

## 2. Current system architecture

### A. Data acquisition and reliability layer
1. **Yahoo / yfinance** — primary historical daily OHLCV route.
2. **Yahoo `Ticker.history`** — individual-symbol recovery route.
3. **Stooq** — recovery route.
4. **GitHub durable scanner snapshots** — last-good recovery, not an arbitrary individual-ticker OHLCV provider.
5. **Alpaca** — SHADOW / VALIDATION only; not allowed to influence production scoring until benchmark evidence supports promotion.
6. **IBKR** — intended portfolio, risk and execution layer, not the primary large-universe historical data feed.

The code includes freshness, continuity, completed-session, provider health, recovery and data-confidence controls. The documented Alpaca SIP benchmark and provider-promotion criteria remain research/validation work, not completed evidence.

### B. Market regime and scanner decision layer
The scanner produces ranked records and applies the frozen scoring/data compatibility key. The decision hierarchy is:

`Candidate Quality → Entry Quality / Price Entry Gate → Scanner Action → Ticker-level Event + Stop/R:R Verification`

Important distinction:
- `Candidate Quality Gate Pass` is a quality/tradeability gate.
- `Price Entry Gate Pass` is a price-location/entry gate.
- `VERIFY EVENT + STOP` means the price setup is ready for final ticker-level verification; it does **not** mean fully actionable.
- The scanner does not fetch full earnings/event metadata for every symbol as part of the market-wide scan. The final ticker diagnostic remains necessary.

Phase 2E.3 fields are present in `build_ranked_master()` and are surfaced in the scanner UI. The current code labels Phase 2E.3 as presentation/decision hierarchy and states that it does not change scoring, thresholds, ranking, provider roles or actionability.

### C. Candidate lifecycle layer (Phase 2F)
Lifecycle is a separate decision-history layer. The code states that it does not change scanner scores, thresholds, provider roles or the frozen `ENGINE_VERSION`.

**States/events**
- Tracked states: `WATCH`, `DEVELOPING`, `READY`, `TRIGGER`, `ACTIVE`.
- Display also includes `INVALIDATED`.
- `DISCOVERED` is a history event, not a persistent lifecycle state.
- `UNTRACKED` can be returned for symbols outside the tracked lifecycle.

**Persistence**
- Local SQLite table: `candidate_lifecycle`.
- Optional durable GitHub JSON store under the configured durable path's `candidate_lifecycle/` subdirectory.
- Durable branch default: `scanner-state`.
- Lifecycle schema: `1`; history retained up to 40 events.
- When both local and durable stores are available, the code chooses the store with the latest `last_processed_session`.

**Advance / hold controls**
- Lifecycle advances only when the signal session is valid, provider health is `HEALTHY`, and the session is newer than the last processed session.
- A price-data block produces `DATA HOLD` and preserves the previous lifecycle state.
- Degraded/recovered data does not advance or invalidate lifecycle state.
- Duplicate or older sessions do not create duplicate transitions.

### D. Trigger confirmation (Phase 2F.2)
For a prior `READY` candidate:
1. Freeze the completed READY session's 20-day reference: `High20` for LONG or `Low20` for SHORT.
2. On a later completed session, require the close to be strictly beyond that stored reference.
3. Require `Volume Ratio >= 1.50x`.
4. Only then transition `READY → TRIGGER`.

The current bar cannot define and confirm its own reference on the same session. Trigger metadata records the reference level/session, confirmed session, close and volume ratio.

**Critical guardrail:** `TRIGGER` still means **VERIFY EVENT + STOP**. It is not a buy/sell instruction or fully actionable trade. `ACTIVE` remains reserved for future portfolio/position verification.

---

## 3. What has been completed

### Implemented in current code
- Data retrieval and recovery architecture, including primary/recovery/durable snapshot roles.
- Market-data confidence and fail-closed user-facing controls.
- Candidate Quality / Entry Quality / Action separation.
- Phase 2E.3 decision-first fields and scanner views.
- Phase 2F.1 candidate lifecycle foundation and persistence.
- Phase 2F.2 prior-session reference and volume-confirmed trigger state.
- Lifecycle history and UI visibility.

### Documented but not yet demonstrated as operational/validated
- Full Alpaca SIP vs Yahoo/Stooq benchmark and provider promotion.
- Comprehensive automated regression suite.
- Historical backtest and point-in-time universe/event validation.
- Forward-test sample collection and reporting.
- Statistically justified calibration.
- Portfolio risk sizing, exposure constraints and IBKR integration.
- End-to-end workflow automation and production freeze.

**Implementation is not equivalent to validation.** A feature appearing in `app.py` establishes implementation evidence, not correctness across edge cases, production stability, or a profitable statistical edge.

---

## 4. Roadmap reconciliation

| Phase | Scope | Reconciled status | Exit evidence still required |
|---|---|---|---|
| 2A–2D | Session integrity, recovery, persistence, provider diagnostics and data reliability | Core implementation present; reliability benchmark remains open | Automated tests, provider comparison, recovery/failure scenarios |
| 2E.1–2E.2 | Candidate / Entry / Action decision UX | Implemented | Regression/acceptance evidence |
| 2E.3 | Final market scanner decision UX and filter semantics | Implemented in current code; old status docs incorrectly say NEXT | Formal acceptance checklist and regression coverage |
| 2F.1 | Candidate lifecycle foundation | Implemented | Transition/persistence/restore test coverage |
| 2F.2 | Trigger state foundation | Implemented | Trigger edge-case tests and forward observation |
| Remaining 2F | Full lifecycle, ACTIVE and portfolio/position verification | In progress / not complete | Portfolio evidence, complete state semantics and operational verification |
| 2G | Historical validation / backtesting | Not started as an operational engine | PIT universe/events, no-lookahead data, reproducible backtest, segmented results |
| Forward Test Lab | Prospective signal capture and outcome tracking | Planned | Daily capture, MAE/MFE, target/stop outcomes, execution capture |
| 2H | Calibration | Planned | Adequate validated sample; measurable hypotheses; holdout discipline |
| 2I | Portfolio / risk / IBKR | Planned | Position sizing, exposure, correlation, portfolio constraints and verified IBKR data/workflows |
| 2J | Automation | Planned | Reliable scheduled runs, alerts, monitoring and failure handling |
| v3.0 | Production freeze | Not reached | All critical validation, operational and governance gates passed |

### Correct next milestone
Do **not** jump directly to scoring calibration or declare the system statistically validated.

Recommended sequence:
1. **P2F.3 — Lifecycle hardening:** test all state transitions, data holds, session idempotency, rediscovery, durable-store failures and restore precedence.
2. **P2G.1 — Validation data foundation:** define reproducible signal snapshots, point-in-time universe/event inputs, completed-session rules and no-lookahead checks.
3. **P2G.2 — Historical outcome engine:** measure forward returns, stop/target outcomes, MAE/MFE, expectancy, drawdown and segmentation.
4. **Forward Test Lab:** run prospective capture before any production scoring changes.
5. **P2H calibration:** only change features/weights when evidence supports a change and holdout/forward results remain acceptable.
6. **P2I/P2J:** portfolio risk and automation after signal validity is established.

---

## 5. Key documentation discrepancies to correct

1. `PROJECT_STATUS.md`, `ROADMAP.md`, `ARCHITECTURE.md`, `VALIDATION_PLAN.md`, `RESEARCH_NOTES.md`, `DATA_PROVIDER_PLAN.md` and `CHANGELOG.md` carry a **2026-09-04** status date, while the current app header is v7.11.0 and Phase 2F.2.
2. `PROJECT_STATUS.md` and `ROADMAP.md` still mark Phase 2E.3 as NEXT. Current code contains the Phase 2E.3 decision-first fields, filter semantics and UI.
3. Phase 2F is described as planned in the old roadmap, but Phase 2F.1 and 2F.2 code has since been added.
4. `CHANGELOG.md` ends with the 2026-09-04 Phase 2E.3 NEXT statement and omits the subsequent v7.10.0, v7.10.1 and v7.11.0 milestones.
5. The current code's app build version and compatibility key are different by design. Do not replace `ENGINE_VERSION = v7.4.5-P2C-FREEZE` with the app build version.
6. The root repository inventory contains no dedicated `tests/` directory or obvious repository-contained regression harness. This is a visible evidence gap, not proof that no manual testing has ever occurred.
7. Research notes must retain all five contextual-volume cases:
   - breakout expansion
   - pullback contraction
   - consolidation dry-up
   - reversal/repair expansion
   - breakdown expansion
   These are research hypotheses, not approved production scoring changes.

---

## 6. Estimated project completion

### Overall estimate: **approximately 45%**
This is a planning estimate, not a measured software metric or a claim of statistical readiness.

| Workstream | Indicative completion |
|---|---:|
| Core data / retrieval / recovery foundation | 80% |
| Decision engine and scanner UX through Phase 2E.3 | 90% |
| Candidate lifecycle and trigger foundation (Phase 2F) | 55% |
| Historical validation / backtesting (Phase 2G) | 0–5% |
| Forward testing and calibration (2H) | 0% |
| Portfolio risk / IBKR integration (2I) | 0–5% |
| Workflow automation and production freeze (2J / v3.0) | 0–5% |

The 45% estimate reflects that the scanner's core implementation is substantial, but the validation, portfolio and operational layers represent a large share of the remaining roadmap. It should be revised once the roadmap has explicit weighted acceptance criteria.

**Do not interpret 45% as “45% of a profitable trading edge.”** No such edge has been established by the repository evidence reviewed.

---

## 7. Audit scope and evidence limitations

Reviewed:
- Root repository inventory on `main`.
- `app.py` current full content (7,932 lines).
- `PROJECT_STATUS.md`, `ROADMAP.md`, `ARCHITECTURE.md`, `VALIDATION_PLAN.md`, `RESEARCH_NOTES.md`.
- `CHANGELOG.md`, `DATA_PROVIDER_PLAN.md`, `README.md`, `requirements.txt`.
- Recent visible commit history and diffs for the Phase 2F.1 and Phase 2F.2 changes.

Latest feature commit evidence:
- `76c7f2a272bcba5eebfd2eb59f051a812dd46a54` — Phase 2F.2 Trigger State Foundation.
- `3e6c07e6ba69eac0110720ae02950f409969996c` — Phase 2F.1 Candidate Lifecycle Foundation.
- `c07cfb8d0b590e1d3b68feed6b65e0f7c066acd2` — insufficient-history eligibility UX hotfix.

Limitations:
- GitHub connector could read the repository but branch creation returned HTTP 403 `Resource not accessible by integration`; no repository files were changed.
- The repository's visible root contains no test suite; this audit did not execute the Streamlit app against live market data or run a formal automated test harness.
- GitHub commit search returned the latest feature commit in September 2026 even though repository metadata showed a later push timestamp; the precise later push contents/branch history should be reconciled separately if the repository has additional non-main activity.

---

## 8. Bottom line

**Where are we now?** The project is at **Phase 2F.2 — Trigger State Foundation implemented**, with Phase 2E.3 already present in code. The next correct engineering step is lifecycle hardening and test coverage, followed by Phase 2G's historical validation foundation.

**The largest remaining gap is no longer the scanner UI. It is proving that the signals are reliable, reproducible and statistically useful before calibration or portfolio deployment.**
