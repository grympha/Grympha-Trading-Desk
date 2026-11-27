# Grympha Trading Desk — Strategies Index

Three **independent** strategy engines run side by side on the same MT5 account, each with its own
rules, cadence, decision log and broker magic — but all share one journal, executor, monitor, news
filter and pair-control layer. This is the map; follow the links for full rules.

| Strategy | Reference | Timeframes | Core idea | Magic | Decision log |
|----------|-----------|------------|-----------|-------|--------------|
| **BBMA** | [BBMA_RULES_AND_SCANNER.md](BBMA_RULES_AND_SCANNER.md) | H1 → M15 → M5 | Trend re-entry (CSA / Momentum, optional MHV); pending stop-entry | 816001 | `decisions.jsonl` |
| **Confluence** | [CONFLUENCE_RULES_AND_SCANNER.md](CONFLUENCE_RULES_AND_SCANNER.md) | H4 → H1 → M15 | Five-factor scoring; reference levels (no entry trigger) | 816002 | `confluence_decisions.jsonl` |
| **ORB** | [ORB_RULES_AND_SCANNER.md](ORB_RULES_AND_SCANNER.md) | M5 (+ H4 trend) | XAUUSD opening-range breakout + retest; 1/session | 816003 | `orb_decisions.jsonl` |

Each engine is self-contained: **no strategy reads another's inputs, signals or votes.**

---

## How they run together

- **Cadence.** BBMA scans every bot cycle (`poll_seconds`, default 15 s). Confluence has its own timer
  (`check_seconds`, default 300 s). ORB runs each cycle but emits at most 1 signal/symbol/session.
- **Scan loop.** `bbma_bot.py` drives the cycle; `scans = {BBMA, CONFLUENCE, ORB}` each publish into the
  shared **journal** (`dashboard_journal.py`), which the dashboard reads per strategy.
- **Dedup / state.** BBMA alert dedup → `state.json`; Confluence per-M15-candle cache → `confluence_state.json`;
  ORB one-per-session → `orb_state.json`. All survive restarts.

## Shared infrastructure (same for all three)

- **Timebase.** `tick_timestamp_offset_hours` / `bar_timestamp_offset_hours` normalize the broker feed
  (RoboForex = UTC+2 winter / +3 summer) before every freshness check. Stale/unclosed data blocks that
  symbol's analysis — it never scores on bad data.
- **Pair controls.** *Settings → Pairs on/off* enables/disables each symbol **per strategy**
  (`pair_controls.enabled`).
- **Strategy setups.** *Settings → Strategy setups* holds live per-strategy options
  (`strategy_settings.py`): BBMA setup toggles, Confluence min-score, ORB trading/days/months/TP, etc.
  Read every cycle; config.json values are the defaults until first saved.
- **News filter.** The shared **MT5-calendar** blackout (`news_filter.py`) is enforced **at execution**
  for all three, fail-closed when the feed is unavailable.
- **Execution.** Analysis-only by default. Opt-in DEMO/REAL executor (`demo_execution.py`) places orders
  only for enabled strategies; REAL requires the exact saved account, hedging and algo permissions.
  (ORB additionally requires `orb.trade`.)
- **Trade Monitor & early exit.** `trade_monitor.py` reviews bot-owned positions on closed M15 candles for
  **all three** (invalidation: BBMA = setup low/high · Confluence = preceding five-bar structure ·
  ORB = broken-out range edge). Auto early-exit toggles: BBMA/Confluence/ORB in REAL (config
  `real_execution.auto_early_exit`) and in DEMO (dashboard *Early exit · Trade Monitor*).
- **Risk, management, portfolio guard.** Shared risk sizing, optional partial/breakeven/trailing
  management, and the portfolio exposure guard (total / per-pair / per-currency) apply across strategies.
- **Review & backups.** Closed trades feed the shared Trade Review (TP / BE / SL / Other per pair) and the
  SQLite ledger (`dashboard.sqlite3`), backed up every 24 h.

---

## Live Test (forward-test, fully simulated — separate from the three live engines)

The **Live Test** tab forward-tests strategy agents on live MT5 data with a **read-only** feed
(`market_data.py`): every entry, SL, TP, exit and P/L is a **simulation** — no `order_send`, no
executor (a guard test scans every `live_test*` source for forbidden order tokens). The simulator
fills BUY on Ask / SELL on Bid, exits on the opposite side, computes R from fill→initial-SL, flags
stale-feed gaps as `DATA_GAP` (but a position held through the scheduled forex/metals pause — the
daily rollover break 17:00–18:05 New York, ≈06:00 MYT, or the weekend — stays valid; a stop crossed
when prices return fills at the reopening price, marked `gap_fill: MARKET_PAUSE`), tracks MFE/MAE, and supports **break-even / trailing / no-take-profit / partial close at a first target**
exit management (optional per-agent). Agents: six **core** H1 agents + the **nine structure / ported
agents** below (the AMD Power-of-Three agent registers three entry models — C1 / A1 / B1 — as
separate agents). Skipped / NO_FILL / cancelled setups go to an exportable **Signal Log**; closed
trades and a **per-symbol R summary** export to CSV. Start/Pause per agent or **Start all / Pause
all**. The tab auto-renders any registered agent, so all of them appear automatically.

| # | Agent (`strategy_id`) | TF | Symbols | Core idea |
|---|-----------------------|-----|---------|-----------|
| 1 | **H4 Liquidity Sweep + FVG** (`H4_LIQ_SWEEP_FVG`) | M5 (M15/M1 opt) | all 12 | EQH/EQL target → LTF sweep → first FVG → TOUCH_50 / **RECLAIM_50** fill |
| 2 | **CRT H1/M5** (`CRT_H1_M5`) | M5 | all 12 | prior-H1 range swept then closed back inside; enter ≤60 s after close; TP = opposite edge; 3-H1 time exit |
| 3 | **Gold ORB 09:30 NY** (`ORB_BREAKOUT_RETEST`) | M5 | XAUUSD | SELL only; H4-bearish gate; 09:30-09:45 range; M5 break+retest; TP = nearest M15 demand; 12:00 NY exit |
| 4 | **H4 Swing Sweep + M15 FVG + Engulfing** (`H4_SWING_SWEEP_FVG_ENGULF`) | M15 | all 12 | un-broken H4 pivot swept → M15 FVG → engulfing confirm; TP = nearest H4 swing; 4 h exit |
| 5 | **New York Sweep London** (`NY_SWEEP_LONDON`) | M5 | all 12 | London 08-12 range; NY-session sweep of an edge + CHoCH; TP = opposite London edge; 12:00 NY exit |
| 6 | **Inverse FVG Retest** (`INVERSE_FVG_RETEST`) | M5 | all 12 | FVG flipped by a close through the whole gap → retest fills; **SWEEP_REQUIRED** variant; TP = M15 swing; 2 h exit |
| 7 | **Momentum M5/M1** (`MOMENTUM_M5_M1`) | M1 (M5 bias) | XAUUSD | ported from the Momentum Desk — M5 EMA20/50 bias + M1 EMA20-pullback pending breakout; break-even + trailing, no TP, 15-min time stop |
| 8 | **Advocate EMA Pullback** (`ADVOCATE_EMA_PULLBACK`) | M5 (M15 bias) | XAUUSD | ported from the XAU Advocate Desk — M15 EMA20/50 bias + M5 EMA20 pullback gated by a 3-objection debate (cost/extension/structure); trailing only, no TP |
| 9 | **AMD Power of Three** (`AMD_PO3`, models C1/A1/B1) | M5 (H1 context) | XAUUSD | Market Cycle Guide model C1 — 06:00-06:55 UTC range, H1 pivot context frozen at 07:00, failed-breakout sweep → reclaim → origin confirm → retest; SL beyond the sweep extreme, TP inside the opposite boundary, ≥1.5 R:R, 10:00 UTC cutoff. A1 enters on the reclaim, B1 on the confirm (no retest) |
| 10 | **BBMA live** (`LIVE_BBMA`, one agent per setup: CSA re-entry / Momentum re-entry / MHV reversal) | M5 (H1, M15) | BBMA pairs | the **live BBMA strategy**, simulated: calls `bbma_bot.assess` with the same frames and settings as the scanner (structural TP with runner); STOP entry at the trigger until expiry; TP = TP2; 50 % partial + break-even +50 pts at TP1; trail 1R from +1R |
| 11 | **Confluence live** (`LIVE_CONFLUENCE`) | M15 (H1, H4) | Confluence pairs | the **live Five-factor Confluence strategy**, simulated: calls `confluence_strategy.evaluate`; score ≥ live minimum (4/5) enters at market once per M15 close; SL 1.5 ATR, TP = TP2 3.5 ATR; 50 % partial + break-even at TP1 (2 ATR); trail 1R from +1R |
| 12 | **ORB live** (`LIVE_ORB`) | M5 (H4 trend) | XAUUSD | the **live ORB strategy**, simulated: calls `orb_core.scan` (01:00–06:00 server range, breakout + retest, trades 07:00–17:00, Wed/Thu/December skipped) and the live `ORBRunner.plan` (fixed 2R); 1 signal per session; market entry within the 60 s signal window; 50 % partial + break-even at TP1 (1R); trail 1R from +1R |

Agents 10–12 exist to judge each **live strategy × setup × pair** on R, without money, the shared
position cap or skipped signals, before switching combinations off in **Pairs on / off** and
**Strategy setups**. Their analysis settings come from `config.json`; trade management (partial,
break-even, trailing), the BBMA TP mode, the Confluence minimum score and the ORB settings follow the
**live dashboard settings** (checked every 30 s). When one of those changes, each affected agent gets a
**new version**: it takes over Running from the old version, and the old version is paused with its
trades kept, so results under the old and new settings stay separate. A `config.json` change does the
same at the next bot start.
Not modelled: news blackouts, trade windows, the shared position cap and live polling delays.
Agent 12 (live ORB, `orb_strategy.py`) and agent 3 (Gold ORB 09:30 New York) are different strategies.

**Exit reasons.** `SL` = the original stop (−1R); `BREAK_EVEN` = a stop moved to entry (+offset);
`TRAIL_STOP` = a trailed stop (usually a profit); `TP` = take-profit; `CANDLE_END` = the agent's time
limit (e.g. Momentum's 15-minute max hold); `AMBIGUOUS_SL_TP` = both levels crossed within one sample.
R is always measured from the original stop. Trades closed before 26 Nov 2026 were relabelled from
`SL` by the same rule (`exit_reason_relabelled`).

**Live-Test-only pairs.** `live_test_extra_symbols` in `config.json` adds pairs to every Live Test
agent family (core, structure, BBMA live, Confluence live) **without** adding them to live trading —
the live scanners and executor keep reading `symbols` / `confluence.symbols`. Takes effect at the next
bot start. Each pair adds 15 agents. German and US index CFDs have their own session hours in
`market_hours.py` (DE40 08:00–21:55 Frankfurt; US30/US500/USTECH closed 16:15–20:00 New York), so their
closed hours count as market closed, not data gaps; JP225 and metals follow the forex schedule.
Example (RoboForex names): `["XAUEUR", ".DE40Cash", ".USTECHCash", ".JP225Cash", ".US30Cash",
".US500Cash", "GBPAUD", "EURJPY", "AUDJPY"]`.

All agents use closed candles for structure, a shared min-R:R gate (`RR_TOO_LOW`), per-symbol
tick/point profiles and per agent × symbol × config state. One offline symbol never blocks the
others. See each `live_test_*.py` module and its `test_live_test_*.py` suite, plus
[PROJECT_PROMPTS.md](PROJECT_PROMPTS.md).

## Quant Agent — historical backtests (`quant_agent.py`)

Runs any Live Test agent over MT5 history with the **same agent code and the same Live Test engine
logic** (monitoring, freshness gate, position cap, simulator fills and exits), so its R results mean
the same as Live Test's — in minutes instead of weeks. Read-only and simulated: it gets a replayed
market that serves only candles already closed at the simulated moment (no lookahead); each M1
candle becomes four quotes (open, the two extremes in their likely order, close). The only thing it
writes to the dashboard database is the newest result per agent (`quant_results`, read by the Signal
Agent; `--extra-spread-points` stress runs are not saved). No order API exists in it (guard test).
Dashboard → **Quant Agent** tab (`/api/quant-agent`) shows every saved backtest beside the same
agent's Live Test record and status, filterable by verdict and strategy. **Run a backtest** on that
tab picks a strategy, a pair (or all its pairs) and 7–180 days of history, then starts
`quant_agent.py --registered` as a separate process (`quant_runner.py`, `/api/quant-run`; it opens its
own MT5 connection so the bot's is untouched). One run at a time; the tab shows "Backtest running"
with progress and a Stop button, then the result (or the last output lines if it failed); the table
switches to that strategy. Output goes to `reports/quant_dashboard_run.log`.

```
python quant_agent.py --registered --strategies ADVOCATE_EMA_PULLBACK --days 30   # what the tab runs
python quant_agent.py --live --days 30                          # every live-strategy agent, all pairs
python quant_agent.py --strategy LIVE_BBMA --setup CSA_REENTRY --symbol GBPAUD --days 60
python quant_agent.py --all --days 14 --symbols XAUUSD,EURUSD   # every Live Test agent on two pairs
python quant_agent.py --live --days 30 --extra-spread-points 20 # cost stress test
```

Reports go to `reports/quant_<time>.csv`, `.html` (summary per agent × pair: trades, win rate, net/avg R,
profit factor, max drawdown, result **without the single best trade**, last-30 % out-of-sample R and a
verdict) and `_trades.csv` (every simulated trade). Verdicts: `EDGE` needs ≥ 30 trades, avg > +0.05R,
a positive result without the best trade AND a positive out-of-sample part; `LOSING` is avg < −0.15R.
Live-strategy agents use the CURRENT dashboard settings for the whole period.

Checked against live: on 18–25 Nov 2026 it found 10 of live BBMA's 13 XAUUSD setups with identical
setup ids and entry prices (the rest were traded under the older 1R/2R target mode). Limits: fills
from M1 candles, not ticks; the M1 spread is often the minute's minimum (use `--extra-spread-points`);
news blackouts, trade windows and the shared position cap are not modelled. Treat results as
optimistic and confirm in Live Test before switching anything on live.

## Signal Agent — graded setup feed (`signal_agent.py`)

After every Live Test cycle the bot hands each new simulated entry to the Signal Agent, which grades it
from the SAME agent's (strategy × pair × config) record **as it stood when the setup appeared**: the
newest Quant Agent backtest (`quant_results`) plus the agent's closed, valid Live Test trades.

| Grade | Rule |
|---|---|
| **A · Proven** | backtest verdict EDGE and Live Test not contradicting it (< 10 trades, or avg ≥ 0R); or Live Test alone earns EDGE (≥ 30 trades) |
| **B · Promising** | ≥ 10 trades in total, combined average above +0.05R |
| **X · Losing** | a LOSING verdict in either record, or ≥ 20 Live Test trades averaging below −0.15R |
| **C · Unproven** | everything else |

The grade is frozen, so the feed's outcome column and the per-grade totals show whether the grades
themselves hold up. Shown in the dashboard's **Live Test** tab (Signal Agent table, filter by grade;
`/api/signal-agent`). Optional Telegram alert for grade-A setups (Settings → Telegram, "Signal Agent ·
grade A", off by default; only setups under 10 minutes old, never a backlog). The first run back-fills
the last day without alerting. Simulation only: it reads the Live Test tables, writes `signal_feed`
(newest 5 000 rows) and never reaches orders or the executor (guard test). Re-run the Quant Agent
(`--live`) after changing strategy settings — a new config is a new agent id with no backtest yet.
With a Market Scout report available, a setup that opened while its spread was ≥ 2.5× the usual
spread for that hour is flagged, and an A drops to B.

## Market Scout — hourly market report (`market_scout.py`)

While the bot runs, Market Scout sweeps every symbol visible in MT5's Market Watch plus the live and
Live-Test-only pairs, five symbols per bot cycle (the sweep never delays the scanners), once an hour.
Per symbol:

- **Cost** — usual spread (median of the H1 bars' recorded spread) as a share of the H1 ATR, and the
  live spread against the usual spread *for that hour of day* (MT5 bars record each bar's lowest
  spread, so "unusually high" starts at 2.5×).
- **Volatility** — mean of the last 5 daily ranges against the median of the 60 before:
  QUIET < 0.7×, NORMAL, ACTIVE ≥ 1.4×, EXTREME ≥ 2×.
- **Trend** — close / EMA20 / EMA50 alignment with a rising (falling) EMA50 on H1 and H4, plus the
  efficiency ratio (H1 48 bars, H4 30 bars). RANGING below 0.10, STRONG when both timeframes agree
  and efficiency ≥ 0.30.
- **News** — high-impact events in the next 24 h for the symbol's currencies, from the bot's own MT5
  calendar feed.

Score 0–100 = 45 % cost + 35 % trend + 20 % volatility. Recommendations: news within 4 h for watched
pairs, unusually wide spreads, expensive live pairs (spread ≥ 20 % of an hourly move), unusually
active / quiet markets, strong trends, and candidates worth adding to Live Test (score ≥ 65 and
spread ≤ 8 % of an hourly move). Dashboard → **Market Scout** tab (`/api/market-scout`); `python
market_scout.py` runs one sweep now and prints it. Config (optional) `market_scout`: `enabled`,
`interval_minutes` (60), `symbols_per_cycle` (5), `cycle_budget_seconds` (1.5), `news_hours` (24),
`extra_symbols` ([]). Read-only: symbol list, symbol info, last tick and candles; hidden symbols are
skipped, never added to Market Watch; no order API (guard test).

## TrailFlip — tick-by-tick stop-and-reverse (`live_test_trailflip.py`, `TRAILFLIP`)

From "TrailFlip — Strategy & User Guide" (Nov 2026). XAUUSD, always in the market: the open trade's
stop and the opposite entry are one level that trails D behind price (long: bid − D, up only; short:
ask + D, down only; moves of ≥ 12 % of D); price back at the level closes the trade and opens the
opposite one at the same fill. Flat: a BUY STOP at ask + D and a SELL STOP at bid − D that only move
toward price. D = 0.4 × ATR(M1, 14) clamped to [max(2 × median spread, 0.25 × ATR), 1 × ATR],
refreshed each minute. Limits: no entry / reversal while spread > max(median × 1.5, p90 × 1.1);
flips in the last hour ≥ clamp(30 × ATR / D, 10, 90) → 15 min exits only; a UTC day at −8 R
(realised + open, the guide's 2 % ÷ 0.25 %) closes everything until the next day. Two agents: **video
style** and **trend filter** (enter only when the 20-bar M1 efficiency ratio ≥ 0.30 or a 30 s burst
moved ≥ D at ≥ 60 % efficiency). Stops fill at the first tick that reaches them (at the gap price if
it jumped past) plus `slippage` $0.05; R = fill → initial level. The guide's AI supervisor, local
model and nightly learning are not simulated. Config (optional) `trailflip`: any of the defaults in
`TrailFlip.DEFAULTS`.

**Tick mode:** a 15 s bot cycle cannot follow a $0.50 trail (gold moves ~$1 in a typical 15 s), so the
engine (`_run_tick_agent`) fetches every MT5 tick since the agent's last cycle
(`ReadOnlyMarketData.ticks`, read-only `copy_ticks_range`) and replays them through `run_ticks`; up to
24 h are caught up after a restart or pause, longer gaps close the open trade as
`RESUMED_AFTER_GAP` (DATA_GAP). The Quant Agent replays MT5 tick history through the same
`run_ticks` (`run_tick_agents`, one day of ticks at a time; ~1 min per agent per 30 days). TrailFlip
entries are not graded by the Signal Agent (`UNGRADED`) so hundreds of flips do not flood the feed and
alerts. First 30-day tick backtest (28 Oct – 27 Nov 2026): video style 773 trades, 28 % win, −220 R;
trend filter 632 trades, −210 R; the daily limit hit on ~20 of 22 days; every D tried (0.4–3 × ATR,
slippage 0–0.05) lost.

## Command center — home page (`/home`, `home_view.py`, `js/home.js`)

The launcher opens `http://127.0.0.1:<port>/home`; the dashboard logo also links there and **ENTER
DESK** goes to the dashboard (`/`). A 3D centrepiece with a switch (buttons or keys 1/2/3; the choice is
remembered per browser): **Globe** (one hub per traded pair, agents on orbit rings), **War Table**
(towers at financial centres sized by agents per currency, one arc per pair) and **Galaxy** (one spiral
arm per strategy, one star per agent). Arcs are teal / red / gold for a pair's 30-day Live Test net R
up / down / no trades. Holographic cards = one per strategy (running/total agents, 30-day win rate and
net R). Panels: XAUUSD M15 candles, quotes with 24 h change, Live Test R distribution (30 d,
square-root bar height), strategy × day heatmap (14 d), agent leaderboard (30 d net R) and the Signal
Agent feed. `/api/home` refreshes every 15 s; it only reads the database (clean `OK` trades only).
three.js r128 is bundled (`js/three-r128.js`, `js/three-bloom.js`) because the page CSP allows
same-origin scripts only. `?still` skips the intro; `?scene=table` opens a given scene.

## Other project references

- **README.md** — install, run modes, decision sequence overview.
- **PROJECT_PROMPTS.md** — rebuild / continue-dev / assistant / strategy-only prompt pack.
- **DASHBOARD_SETUP.md**, **DEMO_EXECUTION.md**, **PORTFOLIO_EXECUTION_HEALTH.md**,
  **TRADE_REVIEW.md**, **COUNTERFACTUAL_REPLAY_AND_VERSIONS.md** — subsystem guides.
