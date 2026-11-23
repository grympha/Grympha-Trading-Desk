# Counterfactual Replay + Strategy Version Comparison — v1.13

Research Lab is part of the existing Research module, application and launcher.
It does not add an execution agent or authority to place, change or close orders.
The baseline and candidate versions in this release are STATIC CAPTURED-PLAN
EXECUTION POLICIES, not a historical re-run of the entire BBMA/Confluence scanner.

## Initial supported scope

- Replay new or previously captured ai_candidates with their original entry,
  side, SL, TP1/TP2, validity and FIRST entry-time features/prediction.
- Study plans rejected by news, portfolio, capacity or other admission gates as
  explicitly hypothetical FORCED SETUP EXECUTION. The historical rejection reason
  is retained. A hypothetical winner does not prove that a guard was wrong.
- A rejection before a strategy generates a usable plan cannot be turned into an
  invented entry. Those no-plan decisions remain in the scanner audit only.
- Compare full-volume exit TP1 versus TP2, SL/TP distance multipliers, an admission
  spread filter, and an optional minimum FROZEN entry-time model estimate.
- Default versions: 'Plan baseline / static TP2' and 'Candidate / full exit TP1'.
- No partial close, break-even, trailing, news timeline, broker stop/freeze-distance
  replay, full margin/portfolio-capacity simulation, new indicator rule generation,
  or arbitrary strategy-code execution is included in this first release.

Actual broker execution, risk controls, rules and management stay unchanged.
Simulated results use their own tables and NEVER replace ai_candidates.label_r,
broker P/L or AI training outcomes. No version can be promoted to live execution
through the registry/API/UI. No approval or profitability is inferred.

## Data source and clock setup

Bid/ask ticks with millisecond epochs are required. OHLC touches are not used to
invent fills or to decide which of SL/TP happened first.

The worker can fetch MT5 tick history on the user's PC or use explicitly imported
bid/ask CSV data. It connects only to the expected DEMO account/terminal, checks
account identity before and after historical fetches, and never calls login,
order_check or order_send. Its shutdown closes its own Python terminal connection.

History uses timezone-aware UTC datetime requests. MetaQuotes documents UTC
history timestamps. Nevertheless, verify the actual history domain on your broker
independently: the previously configured live-quote/bar offsets are NOT silently
copied to historical replay.

Two fields deliberately remain separate:
- tick_offset_hours: subtract this from RAW HISTORY timestamps to normalise to UTC.
  Default unknown until the user explicitly supplies and verifies a value.
- query_offset_hours: shift the history REQUEST datetime domain. Normally 0 (UTC).
  It need not equal the raw timestamp correction on a nonstandard broker.

Do not infer either correction from a stale stationary quote or merely from the
Windows local timezone. A wrong correction can align the wrong price path.
Compare moving history/current quotes and a known time/event using read-only
source diagnostics before confirming. No automatic offset inference occurs.

Read-only diagnostic, with the main bot allowed to continue running:
  .\.venv\Scripts\python.exe replay_worker.py --diagnose --config config.json

To inspect an already-investigated nonstandard mapping, supply the proposed raw
and query corrections explicitly; these flags DO NOT save replay settings:
  .\.venv\Scripts\python.exe replay_worker.py --diagnose --config config.json --tick-offset-hours 2 --query-offset-hours 0
The numbers above are an example, not a recommendation for this broker. The
command prints raw history time and a corrected time/age when a raw correction
is supplied. Confirm the correct values on the actual PC/terminal.

## Activate research collection/replay

1. Install the cumulative update, restart using the normal launcher/command and
   press Ctrl+F5. Open the new Research Lab tab.
2. Keep ai_shadow.enabled=true to capture setup candidates. A trained ML model is
   NOT required; auto_train may be false if you only want recording/replay.
3. Open Frozen replay assumptions / data setup.
4. Enter the independently verified raw HISTORY offset and query offset. Confirm
   the verification checkbox only after verifying the actual source timestamps.
5. Enter an explicit assumed ROUND-TRIP non-spread cost in ORIGINAL plan R, plus
   adverse slippage per fill and admission latency. No hidden zero-cost assumption
   is activated when costs are blank. Zero is permitted only as an explicit
   declared assumption. Costs are not automatically verified broker commissions.
6. Choose horizon (default 120 minutes) and maximum quote gap (default 10 seconds).
7. Save frozen assumptions. Initial status NEEDS VERIFIED TICK OFFSET / EXPLICIT
   COSTS is intentional until the required settings exist.
8. Let the full horizon mature after a setup is captured. The worker processes
   up to three mature candidates per batch and schedules about once per minute.
   It never fetches an immature future window or invents its remaining ticks.

The numerical defaults for slippage/latency/gaps/horizon are model assumptions,
not measured execution guarantees. Set appropriate research assumptions yourself.
The same profile is used across the selected comparison cohort. Fee-R values are
not an instrument-specific money-cost calculator and can differ from real charges.

Saved settings have immutable content-addressed assumption profiles. Changing
costs, clocks, latency, horizon or gap criteria starts a separate comparison.
Pausing enabled=false stops new scheduling but retains the same past scientific
profile/results. A currently running read-only job may complete.

Optional bootstrap config (saved dashboard controls take precedence):
  "replay": {
    "enabled": true,
    "tick_offset_hours": null,
    "query_offset_hours": 0,
    "timestamp_verified": false,
    "cost_r": null,
    "slippage_r": 0,
    "latency_ms": 200,
    "horizon_minutes": 120,
    "max_gap_seconds": 10
  }

## CSV source and retries

CSV must contain these exact columns, one symbol per file:
  time_msc,bid,ask
  1795046400000,100.00,100.02
  1795046401000,100.01,100.03

The figures above are FORMAT EXAMPLES, not market evidence. time_msc is a numeric
Unix millisecond timestamp in the declared source time domain, not a local date
string. Keep event order; do not sort, interpolate or fill missing quotes to make
an outcome look certain. Import only data whose timestamps you have verified.

After the updated bot has established the intended demo scope:
  .\.venv\Scripts\python.exe replay_worker.py --db dashboard.sqlite3 --import-csv ticks.csv --symbol AUDUSD --tick-offset-hours 0 --verified-offset

CSV normalisation uses its OWN declared raw offset once, then stores UTC ticks.
The MT5 query-offset profile field is not applied a second time to imported UTC
samples. Source normalisation metadata and hashes remain attached to the dataset.
The importer permits at most 250,000 ticks and rejects invalid order/quotes or
large source gaps. Each imported window must cover a complete candidate horizon;
separate files are not stitched or forward-filled automatically. Newer matching
imports can create a new frozen dataset while older results remain preserved.

Optional one-batch historical replay (uses the saved immutable assumptions):
  .\.venv\Scripts\python.exe replay_worker.py --run --db dashboard.sqlite3 --config config.json

After a broker later downloads missing/corrected history, explicitly refetch
inconclusive windows using:
  .\.venv\Scripts\python.exe replay_worker.py --run --retry-inconclusive --db dashboard.sqlite3 --config config.json

Ordinary data-fetch failures use a 15-minute retry cooldown. The explicit retry
command bypasses that cooldown for source repair. Do not run manual and scheduled
jobs simultaneously. Cached successful data is reused when adding new versions.

## Simulation conventions and limits

- The first eligible quote at/after capture + declared latency supplies admission.
  Missing admission coverage (>5 seconds) is inconclusive.
- Market references use executable ask for BUY / bid for SELL, require the declared
  spread bound and no more than 0.25 original R reference drift before entry.
- Pending STOP orders must still be valid and uncrossed at simulated admission.
  Subsequent ask triggers BUY STOP; subsequent bid triggers SELL STOP. A jump fills
  at the worse executable quote, plus adverse assumed entry slippage.
- Invalid protection brackets after a hypothetical fill are INCONCLUSIVE; broker
  acceptance is not guessed. Unsupported pending types are not simulated as stops.
- Long exits use bid, short exits ask. SL fills allow an adverse quote gap. Profit
  exits are capped at their target (no favourable gap bonus). Declared adverse
  slippage applies again at exit. Spread is already embedded and not subtracted
  a second time. Assumed non-spread round-trip cost is deducted once after entry.
- A still-open example exits at the last observed executable quote within the
  horizon's permitted boundary tolerance, labelled HORIZON_EXIT. This is a forced
  research horizon, not a claim that the real bot would close then.
- All versions retain the ORIGINAL plan R and fixed-volume proxy. A wider SL can
  lose more than 1R; volume is not resized to restore a fixed cash-risk budget.
- Untriggered pending orders receive UNFILLED / 0 simulated R only under the
  declared window-coverage criteria. Intentional model filtering is SKIPPED / 0R.
  Inadmissible modelled requests receive NOT_ADMITTED / 0R. These are simulated
  no-entry decisions, not losses or claims about actual broker rejection costs.
- Invalid/non-finite/crossed quotes, out-of-order ticks, conflicting different
  quotes at the same millisecond, insufficient boundaries and excessive gaps
  produce INCONCLUSIVE / unknown R, never a fabricated win, loss or zero.
- Default endpoint tolerance is five seconds and maximum internal gap is the
  declared value. These checks do not certify the broker archive is complete;
  missing source events within those tolerances can still be unknown in reality.
  Do not relax data-quality criteria merely to obtain attractive results.

Research SL/TP multipliers and spread thresholds are explicitly in original-plan
R. They are not a full reproduction of every broker constraint or current bot
management setting. Static TP2 baseline results can differ from actual positions
that were managed, delayed, rejected, partially filled or manually closed.

## Immutable versions and fair comparison

Use Register a research policy version to freeze a named definition (maximum
30 versions including the two initial versions). Existing versions cannot be
edited/deleted or applied live by this UI. A new hypothesis gets a new ID.

Comparisons match the SAME candidates, frozen tick dataset and assumption profile.
A candidate with unknown outcome in either version is excluded from BOTH sides
of that paired metric, and the inconclusive count remains visible. Exclusion can
itself create selection bias; always inspect coverage and individual reasons.
Click a replay row to inspect all version outcomes, original plan, frozen model
prediction, source metadata, assumption ID and tick hash.

An optional model filter uses ONLY the actual frozen entry-time estimate. If the
model was untrained then, the result is INCONCLUSIVE; later models are never used
to back-score it. Replayed returns never enter the ML broker-outcome label table. A model trained
on actually managed trades is not automatically calibrated to a static TP replay
policy; any such filter must be assessed as a new research hypothesis.

Exploratory+forward and enrolled-forward results are separate. Genuine enrolled
forward requires BOTH version IDs and the active ready assumption profile to have
been recorded when the candidate was FIRST captured, with definitions predating
capture. Enrolment is accepted only in the immediate publication window. Creating
or selecting a version/profile later cannot convert old data into forward data.

Reports show matched count, unknown count, sums/means in R, entry-ordered drawdown,
avoided losses and missed winners from model skips. A deterministic exploratory
95% paired interval resamples 5-day blocks (not independent individual trades),
when at least ten observed UTC dates exist. It is not adjusted for testing many
versions, and does not establish causal improvement or account-level performance.
At least 30 matched candidates across ten dates changes collection status to
UNDER REVIEW, not APPROVED. Freeze any selected hypothesis and collect another
untouched period before considering later demo adoption.

No margin, cash balance, daily loss, correlation/capacity feedback, news gate,
queue interaction, future opportunities created by a skip, full order-book fill,
liquidity or exchange/broker matching engine is reconstructed. Summed R/drawdown
is descriptive independent-trade research, not a realistic equity backtest.

## Operation, storage and tests

The same launcher runs everything. Historical processing occurs in a temporary
read-only child with single numerical thread and below-normal Windows priority.
CPU simulation/compression is outside SQLite write transactions, so it does not
hold the execution journal's writer lock while calculating outcomes. MT5 history
requests still share terminal/network resources; verify actual latency in DEMO.
A 180-second process budget, five-minute fetch chunks, three-candidate batches
and 250,000-tick window limit bound work; no silent tick truncation is used.

Ticks are compressed in additive SQLite tables with immutable dataset IDs that
fingerprint scope, candidate, assumptions, exact ticks, source metadata and engine.
This archive can grow materially. Back up the stopped app/database, and plan disk
space. No automatic deletion sacrifices replay reproducibility. No credentials or
actual tick archive is included in this update ZIP.

The dashboard shows latest 100 baseline rows; 'datasets evaluated' includes
inconclusive datasets. Comparison totals use the matching account/filter dataset.
Paired interval calculations are cached by their immutable daily inputs to keep
polling responsive. Actual journal/history remain in their existing tabs.

237 Python tests passed, including 34 replay/registry/source tests. They cover
BUY/SELL bid/ask, costs/slippage, SL gaps, pending validity, no entry, forced horizon,
invalid/gapped/ambiguous data, frozen-model filtering, no fake outcomes, immutable
profiles/versions, paired cohorts, first-capture forward enrolment, source-account
isolation, separate raw/query offsets, chunk boundaries, CSV import, actual local
CSV worker subprocess, retry cooldown, cross-origin rejection and read-only APIs.
Existing execution/strategy/risk/AI tests remain green. UI syntax, eight-tab wiring,
rendering/escaping, unknown outcomes, forward separation, dirty-input preservation,
replay detail and all prior chart-clearing checks passed.

Test prices/data are synthetic and MT5 source tests are mocked. No real broker
history/performance, Windows MT5 connection, Windows process flags, real browser
layout or live order was validated from this environment. Successful software
tests are not evidence that a candidate improves future trading results.

Official source API references:
- [MetaQuotes copy_ticks_range](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py)
- [MetaQuotes shutdown](https://www.mql5.com/en/docs/python_metatrader5/mt5shutdown_py)
