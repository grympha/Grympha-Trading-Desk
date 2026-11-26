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

## Other project references

- **README.md** — install, run modes, decision sequence overview.
- **PROJECT_PROMPTS.md** — rebuild / continue-dev / assistant / strategy-only prompt pack.
- **DASHBOARD_SETUP.md**, **DEMO_EXECUTION.md**, **PORTFOLIO_EXECUTION_HEALTH.md**,
  **TRADE_REVIEW.md**, **COUNTERFACTUAL_REPLAY_AND_VERSIONS.md** — subsystem guides.
