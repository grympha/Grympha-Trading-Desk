# Trade Review & Research — v1.10

Module 5 runs inside the existing application and launcher. This release is a
local deterministic observer and descriptive research journal. It uses no LLM,
AI API, paid service or automatic strategy-learning system. No order authority
is added; execution settings and strategy rules are unchanged.

## Data collected

For new bot submissions, the ledger stores entry-time condition, setup,
Confluence score/factors, bid/ask/spread, risk/management settings and the last
eight available closed bars of each strategy timeframe. BBMA captures H1/M15/M5;
Confluence captures its existing H4/H1/M15 inputs. No future candles are used.
Existing Confluence caches may lack this capture until a new analysis occurs.

The reviewer runs at most once every 30 seconds after the normal journal refresh.
While the ledger confirms OPEN/OPEN_PARTIAL with a fresh quote, it records observed
favourable/adverse price excursions and sampled broker SL/TP/remaining-volume
changes. Only a new broker snapshot timestamp is counted. The latest 200 sampled
protection changes per trade are retained; management records remain in the ledger.

Once the executor confirms CLOSED, the review includes original levels, actual
fill, last observed broker SL/TP, exit, broker-reported net realised result,
adverse fill displacement, sampled MAE/MFE in initial price-risk units and a
limited rule audit. Partial exits are not counted as separate closed trades.

## What the numbers mean

- Only confirmed closed bot execution-ledger trades for the verified current
  demo account enter statistics. Manual trades and standalone account history
  are not imported or assigned a guessed strategy.
- Existing closed ledger trades are backfilled idempotently. Missing pre-entry
  captures and live samples are explicitly marked unavailable, never invented.
- MAE/MFE are observed lower bounds from periodic position snapshots, not exact
  tick extrema. Positions that open and close between observations have no live
  excursion samples. Downtime, missed prices and post-exit price paths are unknown.
- Samples begin when the observer sees a broker-confirmed open position; they do
  not guarantee full coverage from the exact fill time. Data gaps are labelled.
- BUY observations use the broker exit-side current price; SELL uses its opposite
  directional price movement. Excursion R uses actual fill versus original SL.
- Positive fill displacement means adverse slippage; negative means favourable.
- Net/estimated-risk R uses the recorded initial broker risk estimate, when
  available; it is an estimate, not an exact replay of changing volume and costs.
- Net P/L includes costs already reported by the existing executor. Currency
  totals and groups are kept separate. Missing P/L is not treated as zero.
- The limited audit checks original levels against fill, recorded news-gate
  evidence and accepted broker execution evidence. It is not a full independent
  strategy replay, proof of causal error, or guarantee of complete compliance.

## Patterns and suggestions

Groups currently use strategy, pair, setup, side and account currency. Entry UTC
hour and other raw context can be inspected in each review for later research.
All wins, losses and flat outcomes with known P/L are included. Outcomes are not
filtered to losses only. Initial descriptive tags include SL exit, sampled TP1
followed by net loss, adverse fill of at least 0.1 initial R, and limited-audit flags.

At least 20 known outcomes and 5 losses in a group changes its collection status
to UNDER REVIEW. This is a practical review threshold, not statistical evidence.
At least three repeated tagged observations may produce a clearly labelled TEST
IDEA. Candidate ideas compare entry timing/volatility-based stops, TP1/BE policies
or fill constraints across winners AND losers. They never change production rules.

The current workflow exposes COLLECTING DATA and UNDER REVIEW. TESTING,
APPROVED/REJECTED and deployment are not automated or fabricated. Backtests,
separate validation periods, costs and shadow/demo comparisons must be performed
on a concrete candidate before approving a rule change. Wider stops are never
applied automatically. No prevention of all future or repeated losses is promised.

## Dashboard

The sixth tab, Trade Review, provides strategy/pair filters, per-currency totals,
pattern counts, test ideas and closed-trade detail. Open a row for capture quality,
limited audit, raw entry context and sampled management records. The details list
is capped at the latest 200 matching reviews; totals and patterns use all reviewed
closed trades for the selected account/filter. Live observations survive restart.
Research data uses two additive SQLite tables in the existing database.

No background AI subscription, additional launcher or independent process is
required. The bot and MT5 must be running for new captures. If the reviewer fails,
its exception is isolated from trading and the dashboard shows last-reported or
unavailable review data. No automatic retry of orders is introduced.

## Validation and limits

158 Python tests passed, including 19 research tests covering backfill/dedup,
BUY/SELL price direction, stale samples, restart gaps, SL/TP/volume changes,
account switching, unknown P/L, currency separation, descriptive thresholds,
filters, more than 200 reviews, bounded snapshots and the read-only HTTP API.
Existing execution, strategy and four-module parity tests remain green.
JavaScript syntax, Trade Review rendering/escaping/stale state/dialog/tab wiring,
and all 17 Overview level-clearing regressions passed.

No real browser rendering session, Windows launcher, live MT5 execution, tick
reconstruction, strategy performance backtest or autonomous learning was tested.
