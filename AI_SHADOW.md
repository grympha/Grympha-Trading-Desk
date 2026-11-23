# AI Entry Scoring — v1.11 SHADOW

This adds a local machine-learning research component to Module 5 (Trade Review),
inside the existing application/launcher. No API key, paid AI service, GPU or new
Python dependency is required. It uses NumPy ridge regression (alpha=10), not an
LLM. All model suggestions are informational and have no execution authority.

## What starts immediately

- Before journal persistence/executor handoff, capture first-seen, healthy,
  uncached setup candidates from both strategies and all configured pairs.
- Freeze each candidate's entry-time feature vector and model prediction under
  its verified demo-account scope and exact tracked setup key.
- Record a bounded audit (latest 10,000 account decision records) of scanner
  decisions, including no-plan, blocked and cached analyses. A strategy rejection
  before a plan exists is audited, not turned into a fictional trade.
- Preserve original planned levels. Model failure is isolated from the executor.
  A model suggestion of MODEL SKIP does not prevent a real rule-qualified order.
- Every 30 seconds, link candidates to their own executor-ledger attempt status.
  Only CLOSED trades with finite broker net P/L and a positive recorded initial
  monetary risk estimate get a target: net P/L / estimated initial risk.
- Rejected, cancelled, unexecuted, uncertain or open candidates keep UNKNOWN
  outcomes. No synthetic label, paper P/L or guessed losing trade is introduced.

Features: direction, strategy, Confluence score, spread/original stop distance,
planned TP distances in initial price R, cyclic UTC capture hour, per-timeframe
recent closed-bar range/slope/presence indicators, and exact-symbol categories.
Missing timeframes have explicit presence indicators. Input features never include
actual fill, exit, profit, MFE/MAE, management after entry or future candles.
Original stop/TP distances are predictors, not model-edited order levels.

Captures begin with NEW setups after this update. Old reviewed trades cannot be
reconstructed into a trustworthy entry-time dataset from the few historical
snapshots available, so they are not silently used to train this model.

## Automatic training and chronological validation

A temporary child process performs training so the main bot does not perform
matrix fitting in its trading loop. It imports no MT5 terminal connection and
has no order submission API. Windows gives it below-normal priority; numerical
threads are limited to one. The same launcher starts everything. Stop/Ctrl+C
terminates a running training child during normal application shutdown.

Collection gates: at least 300 captured AND labelled outcomes over at least
20 distinct UTC capture dates. These are engineering minimums, not proof that
the sample is statistically sufficient, diverse, independent or profitable.
The normal 20-trade Trade Review threshold does NOT qualify the AI model.

The scheduler checks after collection refresh, attempts at most once per hour,
and requires at least 50 additional labelled rows before another trained version.
If labels are insufficient it stays NOT TRAINED and invents no score. A worker
can still decline training if chronological purging leaves too few usable folds.

Training procedure:
1. Sort first-seen candidates by capture time, with only labels available by
   the training run's cutoff. Keep account scopes separate.
2. Reserve the latest 20% of rows as a final holdout.
3. On the earlier 80%, use three expanding chronological validation folds.
   Each fold excludes training rows whose capture OR known-label timestamp is
   not strictly earlier than the validation start minus 300 seconds. This purges
   overlapping outcomes and creates a gap between train and validation periods.
4. Fit scaling using only each training subset; fit ridge regression with fixed
   alpha=10. No random train/test shuffle and no parameter search is performed.
5. Fit the research artifact on the purged development subset only, then report
   final holdout error versus a training-mean constant predictor, number of
   model-taken trades, cumulative R, entry-ordered drawdown R and original vs
   shadow outcomes. The final holdout is NOT added to that artifact's fit.
6. Freeze JSON model versions, feature schema, NumPy version, data fingerprint,
   fold boundaries and metrics. No pickle or generated strategy code is executed.

Model estimates represent broker net / recorded initial risk estimate, not a win
probability or calibrated confidence. MODEL TAKE means estimated R > 0; MODEL
SKIP means <= 0. Those suggestions do not affect actual execution.

A descriptive research screen requires at least 20 holdout takes, greater summed
R, no worse entry-ordered drawdown R, and lower prediction error than its constant
baseline. Both pass and fail remain SHADOW ONLY. The latest completed research
artifact is used for prospective shadow scoring; no model is selected by repeatedly
optimising the holdout. Earlier predictions are not overwritten or back-scored
using a later trained model.

## Critical interpretation limits

Only actually executed and closed opportunities currently have credible monetary
outcome labels. This causes EXECUTED-ONLY SELECTION BIAS. Capturing rejected
opportunities does not tell us what would have happened if they had been entered.
An independent counterfactual replay with adequate bid/ask tick history, execution
rules, costs and ambiguity handling is still needed to evaluate that policy.

Holdout cumulative R and drawdown R are descriptive in candidate-entry order, not
an equity/margin backtest. A shadow skip could change capacity and later available
opportunities; the recorded sample does not simulate those counterfactual changes.
Multiple overlapping trades and correlated pairs are not independent evidence.

No causal improvement, statistical significance, calibrated prediction,
profitability or prevention of repeated losses is claimed. A research-screen pass
is not approval. There is NO promotion API, executor integration, automatic risk
increase, automatic SL/TP modification or deployment to orders in this release.
Further stages require independent replay, untouched forward data, uncertainty
analysis and separately reviewed DEMO comparisons against the unchanged strategy.

## Dashboard and operation

Open Trade Review > AI ENTRY SCORING / SHADOW ONLY.
The AI section covers all pairs and both strategies independently of the Trade
Review filters. It shows unique candidates, known labels/days, collection gates,
model version/holdout results, frozen candidate predictions, actual execution
status and known outcome R (UNKNOWN where absent). Latest 100 candidates shown;
counts use the complete account dataset. Existing Trade Review remains available.

Continue starting the bot with your existing launcher/command. Trading still
requires the existing explicit --demo-execution option and DEMO/hedging guards.
The research component alone never enables execution.

Optional config.json section (defaults shown):
  "ai_shadow": {"enabled": true, "auto_train": true}

To capture/score without automatic fitting, set auto_train to false.
To disable the component, set enabled to false and restart. Existing research data
is retained; it is historical, not proof that capture is currently running.

Optional manual fit, after the updated bot has captured its intended demo scope:
  .\.venv\Scripts\python.exe ai_shadow.py --train --db dashboard.sqlite3
It still refuses insufficient data and cannot send orders. Avoid simultaneous
manual fitting and scheduled fitting; normal automatic scheduling is sufficient.

Training data, audit, immutable models and run reports live in additive tables in
the existing dashboard.sqlite3. No credentials or config.json are stored in feature
vectors. Keep that database in backups and stop the application before migration.

## Validation

177 Python tests passed, including 19 shadow-model tests: no fabricated score,
frozen candidate features/predictions, no outcome-feature leakage, unknown rejected
outcomes, correct net-risk labels, account isolation, data/day minimums, train-only
scaling, overlap purging, immutable model versions, unavailable future labels,
background dispatch and model-failure/MODEL SKIP isolation from execution.
Existing execution and strategy parity tests remain green. JavaScript syntax,
AI and Trade Review UI rendering/escaping/error states and 17 chart-clearing
regressions passed. Model-training tests use synthetic temporary data only.

No actual broker dataset was trained or evaluated here. No real-browser rendering,
Windows MT5 execution or Windows child-process priority was exercised here.
