# Grympha Trading Desk — Master Prompt Pack

Four ready-to-paste prompts for this project. Copy the whole section you need.
Project: **BBMA + Confluence + ORB signal/trading desk for MetaTrader 5 on Windows**,
Python 3.11, optional Telegram alerts, web dashboard, DEMO/REAL execution with safety gates.

---

## 1. Rebuild Spec — regenerate the whole project from scratch

```
You are building "Grympha Trading Desk", a standalone Python 3.11 (64-bit, Windows) signal and
trading application for MetaTrader 5 (RoboForex-style brokers). Build it to this specification.

GOAL
A desktop trading desk that reads MT5 market data, runs three independent strategies, shows a
local web dashboard, optionally sends Telegram alerts, and can place DEMO or REAL orders behind
strict safety gates. Default run mode sends NO orders (analysis only).

TECH
- Python 3.11 64-bit, Windows. Packages: MetaTrader5, numpy, pandas, python-dotenv, ta.
- No external paid APIs required. News uses MT5's own economic calendar via a bundled MQL5 EA bridge.
- Config in config.json (beside the code). Secrets (Telegram token/chat id) in .env, never committed.
- Trade ledger/history/reviews in dashboard.sqlite3 (SQLite, WAL). Decisions appended to decisions.jsonl
  (rotated + gzip at 20 MB). State/dedup in state.json (atomic writes).
- A Tkinter launcher (launcher.py) with: requirement checks, setup/install, MT5 terminal picker,
  calendar-EA install, Analysis/DEMO/REAL mode radios, REAL account save+match gate, Telegram config,
  Windows startup + tray + auto-restart options, and a readiness check before start.

TIMEBASE (critical)
- Broker feeds often stamp ticks/bars in UTC+2 (UTC+3 in summer). Config keys tick_timestamp_offset_hours
  and bar_timestamp_offset_hours (default 0) subtract that offset so ages compute against PC UTC.
- Quote freshness gate: good when bid>0, ask>=bid, and -5 <= age <= max_tick_age_seconds (default 60),
  where age = now - (tick.time - tick_offset*3600). Closed-market symbols report CLOSED, not FAIL.
- Timestamps and alert expiry are UTC. Fetch ~400 CLOSED bars/timeframe; exclude MT5 bar zero.

STRATEGY 1 — BBMA (Oma Ali), main engine, H1->M15->M5:
- Condition (closed H1): BUY needs H1 close > EMA50, Mid BB > EMA50, EMA50 rising vs 3 bars ago;
  SELL mirrored. Bollinger width >= 2x simple ATR(14), else RANGE_OR_SQUEEZE/TRANSITION (no trade).
  H1 context must hold both now and at the time the M15 setup formed (using only then-closed H1 bars).
- Setup (closed M15): CSA/Momentum re-entry. BUY: one of previous 12 M15 candles closed above MA5High,
  MA10High and Mid BB; the setup candle overlaps MA5/MA10 Low zone (0.10 ATR touch tolerance), closes
  above both low MAs and Mid BB, and closes bullish. SELL mirrored. If the prior direction candle also
  closed outside the outer BB -> MOMENTUM_REENTRY else CSA_REENTRY. Newest setup wins.
- Entry (closed M5): only M5 bars opening at/after the M15 setup close; within next 4 bars, first bullish
  close above setup high confirms BUY (mirror SELL); must be the most recently closed M5 bar.
- Pending stop idea: BUY entry = confirmation high + spread + buffer (round up to tick); SELL = conf low -
  buffer (round down). Buffer = max(2 points, 0.05*M5 ATR). BUY SL = setup low - buffer; SELL SL = setup
  high + spread + buffer. TP1=1R, TP2=2R (R = entry-to-SL distance). Expiry 10 min after confirmation close.
  Reject if spread > 0.15*M5 ATR, stop distance > 5*M5 ATR, or entry already crossed by current quote.
- Optional MHV reversal (enable_mhv, off by default): within 30 M15 bars MA5 extends outside band with no
  opposing-momentum close, then >=2 intervening bars (reaction + retest near Extreme with close inside band),
  latest candle closes beyond retest; same H1 condition + M5 confirmation gates apply.

STRATEGY 2 — CONFLUENCE (five-factor, H4/H1/M15), reference-only (no entry trigger):
- Direction from H4: BUY if H4 close > EMA50 > EMA200; SELL if close < EMA50 < EMA200; else HOLD.
- Five tests (score 0-5): (1) H4 trend aligned; (2) H1 EMA9>EMA21>EMA50 (BUY) / reversed (SELL);
  (3) M15 RSI in 40-65 (BUY) / 35-60 (SELL); (4) M15 MACD vs signal; (5) price within sr_near_pct (0.4%)
  of H1 rolling 50-bar low (BUY)/high (SELL). Qualifies when side set AND score >= min_score (default 4).
- Levels (reference): entry = ask/bid; SL = 1.5*ATR(14 M15); TP1 = 2*ATR; TP2 = 3.5*ATR. Emits QUOTE_REFERENCE
  only, status REFERENCE_ONLY_NO_ENTRY_TRIGGER; review due next M15 close.

STRATEGY 3 — ORB (opening-range breakout, XAUUSD M5), analysis-only until enabled:
- Range 01:00-06:00 server time; breakout + later retest; EMA200 M5 filter; H4 EMA20 trend; dynamic RR
  1.5-3R; one signal/day (configurable 1-3); skip Wed/Thu and December (configurable); magic 816003,
  comments like "ORB Retest #abcdef". TP1 at 1R, ORB target as TP2. Server offset defaults to
  bar_timestamp_offset_hours. Starts in analysis only; trades only when explicitly enabled.

INDICATORS/DATA
- BB(20,2) population stdev on Close, shift 0. MA5/MA10 linear-weighted (newest heaviest), applied
  separately to High and Low. EMA50 seeded with SMA50. ATR = simple 14-bar average TR (NOT Wilder).
  Reject quotes older than 60s; last closed bar age must be within 1-2 bar durations + 120s grace.

EXECUTION + SAFETY GATES (launcher)
- Analysis mode: starts even if symbol quotes are stale (it waits); blocks only on software/config FAILs
  (Python, Configuration, Folder permissions, Telegram, MetaTrader5, numpy, pandas, python-dotenv, ta).
- DEMO/REAL mode: blocks on ANY FAIL, including every "<symbol> quote" row. DEMO requires MT5 logged into
  a DEMO account; REAL requires MT5 logged into the exact saved real_execution account+server, a hedging
  account (margin_mode==2), and Algo permissions (terminal trade_allowed, tradeapi enabled, account
  trade_allowed + trade_expert). REAL auto-start requires an explicit saved opt-in.
- Risk sizing: risk_percent of equity (default 0.5%), fixed-lot 0.01 fallback; portfolio guard caps total/
  per-pair/per-currency exposure; news blackout (before/after minutes) via MT5 calendar, fail-closed when
  the feed is unavailable; position cap (max_positions). Optional partial/breakeven/trailing management.

DASHBOARD
- Local web dashboard (dashboard_server.py + dashboard.html/.css) with tabs for overview, trade review,
  today-history (date filters, P/L/count), system health (requirements, backups, "Back up now"), and a
  replay/AI-shadow lab. Read-only to broker; it does not place orders itself.

BACKUPS
- Every 24h (and shortly after start) copy dashboard.sqlite3 (+ config.json, launcher_settings.json) via
  SQLite online backup into backups/<date_time>/, verify each copy, keep newest 14. .env is NOT copied.

TESTS
- Provide a unittest suite (337+ tests) covering indicators, condition/setup/entry gates, geometry,
  rejection cases (wide spread, chased entry, expiry, old confirmation), risk/news, trade review, launcher
  checks, backups, telegram, ORB, and the quote/timestamp-offset logic. `py -3.11 -m unittest discover -v`.

DELIVERABLES
- All source files, config.example.json, .env.example, START_APP.cmd launcher, README and setup guides,
  and the MQL5 calendar-bridge EA. No profitability claims; ship as signal/observation first, DEMO before REAL.
```

---

## 2. Continue Dev — hand an AI the current state to keep building

```
You are continuing development of an existing, working project: "Grympha Trading Desk" (v1.11), a
Python 3.11 / Windows MetaTrader 5 signal + trading desk. DO NOT rewrite it; make targeted changes and
keep the existing architecture, file layout, and test suite green.

WHAT IT IS
- Three independent strategies: BBMA (H1->M15->M5 re-entry, main engine), Confluence (five-factor H4/H1/M15,
  reference-only), ORB (XAUUSD opening-range breakout, analysis-only until enabled).
- Entry point bbma_bot.py (flags: --once, --test-telegram, --diagnose-time, --real-check, --demo-check,
  --dashboard --dashboard-port N, --analysis/--demo-execution/--real-execution). Tkinter launcher.py +
  launcher_checks.py + launcher_extras.py wrap it with requirement/readiness checks and run modes.
- Web dashboard: dashboard_server.py + dashboard.html/.css + dashboard_store.py (SQLite dashboard.sqlite3).
- Config: config.json beside code (symbols, offsets, strategy params, trade_controls, news_filter,
  real_execution, orb, backup). Secrets in .env. Decisions -> decisions.jsonl; dedup/state -> state.json.

KEY INVARIANTS (do not break)
- Timebase: launcher_checks.py computes age = now - (tick.time - tick_timestamp_offset_hours*3600); quote
  is "good" only when bid>0, ask>=bid, -5 <= age <= max_tick_age_seconds (60). bar_timestamp_offset_hours
  does the same for candles. RoboForex is UTC+2 winter / UTC+3 summer; offsets are user-set, never guessed.
- Mode gating in launcher.py start(): Analysis ignores symbol-quote FAILs; DEMO/REAL block on ALL FAILs and
  additionally require correct account type, hedging, and algo permissions. REAL requires exact saved
  real_execution account+server match; REAL auto-start needs an explicit saved opt-in.
- Analysis/signal layer never sizes or sends orders. Only DEMO/REAL adapters place orders. News filter is
  fail-closed. Backups never copy .env.

HOW TO WORK
- Run `py -3.11 -m unittest discover -v` before and after changes; add tests for new behavior.
- Keep decision logging JSON-serializable (native bools, no numpy types). Atomic state writes.
- For live diagnostics use `py -3.11 bbma_bot.py --diagnose-time`.
- Ask before any change that could place real orders or weaken a safety gate.

CURRENT KNOWN ISSUE / RECENT CONTEXT
- REAL/DEMO start was failing with "Fix requirements first: <symbol> quote" because config.json lacked
  tick_timestamp_offset_hours while the RoboForex feed is UTC+2 (measured age = -7200s on all symbols).
  Fix: tick_timestamp_offset_hours=2 and bar_timestamp_offset_hours=2 (change to 3 in summer).
- REAL also requires MT5 logged into the saved account (real_execution.account_login/server).

TASK
<describe the change you want here>
```

---

## 3. Trading Assistant — system prompt for an AI that helps operate this bot

```
You are the operations assistant for "Grympha Trading Desk", a Python 3.11 / Windows MetaTrader 5 signal
and trading bot (strategies: BBMA, Confluence, ORB). Help the user set up, run, diagnose, and understand it.
You explain and guide; you NEVER place trades, change broker settings, or hold money, and you never tell the
user to disable safety gates or widen stale-quote thresholds to hours.

WHAT YOU KNOW
- Run modes: Analysis only (no orders, default), DEMO execution, REAL execution. Analysis starts even with
  stale quotes; DEMO/REAL block on any failing check. REAL needs MT5 logged into the exact saved account
  (real_execution.account_login + account_server), a hedging account, and algo permissions enabled.
- Config lives in config.json beside the app; secrets in .env. Telegram alerts are optional.
- Timebase: broker (RoboForex) feeds are UTC+2 in winter, UTC+3 in summer. If quotes read as ~2h stale
  (age ~= -7200s), the fix is config tick_timestamp_offset_hours (and bar_timestamp_offset_hours) = 2
  (or 3 in summer). Confirm with `py -3.11 bbma_bot.py --diagnose-time`. Never guess offsets.

COMMON PROBLEMS -> FIRST CHECKS
- "Fix requirements first: <symbol> quote" in DEMO/REAL but Analysis works -> stale-quote gate; almost
  always the timestamp offset above, OR MT5 not logged in/connected. Check diagnose-time, Market Watch
  live ticks, Windows clock sync.
- REAL won't start / won't trade -> account mismatch (MT5 logged into a different account than saved),
  netting (not hedging) account, or algo trading / external-Python permission off in MT5.
- No signals -> market conditions not met (BBMA needs trending H1, not ranges), news blackout, or bar
  freshness failing (bar offset).

HOW YOU RESPOND
- Diagnose with evidence (quote ages, which check failed, which mode). Give the smallest safe fix.
- For anything that could send real-money orders, state the risk and have the user confirm. Recommend
  observing signals, then DEMO, before REAL. You are not a licensed financial advisor and give no
  personalized investment advice or profitability promises.
```

---

## 4. Strategy Only — exact trading rules (BBMA + Confluence + ORB)

```
Encode these exact trading rules. All bars are CLOSED; MT5 bar zero excluded; timestamps UTC after applying
the broker timestamp offset. Indicators: BB(20,2) population stdev on Close shift 0; MA5/MA10 linear-weighted
(newest heaviest) applied separately to High and Low; EMA50 seeded with SMA50; ATR = simple 14-bar average
true range (NOT Wilder). Fetch ~400 closed bars per timeframe.

BBMA (H1 -> M15 -> M5), the main engine:
1) Condition (H1): BUY needs close>EMA50 AND MidBB>EMA50 AND EMA50 > EMA50 three closed bars earlier;
   SELL mirrored. Require BB width >= 2 * ATR(14); otherwise RANGE_OR_SQUEEZE/TRANSITION => no setup.
   The H1 condition must hold now AND at the moment the M15 setup formed (using only H1 bars closed then).
2) Setup (M15), CSA/Momentum re-entry. BUY: one of the previous 12 M15 candles closed above MA5High,
   MA10High and MidBB (prior strong direction). The setup candle overlaps the MA5/MA10 Low zone with a
   0.10 ATR touch tolerance, closes above both low MAs and MidBB, and closes bullish. SELL mirrored on the
   MA5/MA10 High side with a bearish close. If the qualifying prior-direction candle also closed outside the
   outer BB => MOMENTUM_REENTRY, else CSA_REENTRY. Newest valid setup takes priority.
3) Entry (M5): consider only M5 bars that OPEN at/after the M15 setup CLOSE. Within the next 4 M5 bars, the
   first bullish M5 close above the setup high confirms BUY (first bearish close below setup low confirms
   SELL). Confirmation must be the most recently closed M5 bar; never republished later.
4) Pending stop order idea: BUY entry = confirmation high + current spread + buffer, rounded UP to tick;
   SELL entry = confirmation low - buffer, rounded DOWN. Buffer = max(2 points, 0.05 * M5 ATR). BUY SL =
   setup low - buffer; SELL SL = setup high + spread + buffer. TP1 = 1R, TP2 = 2R (R = |entry - SL|).
   Expiry = 10 minutes after the confirmation candle closes. Reject if spread > 0.15*M5 ATR, stop distance
   > 5*M5 ATR, or entry already crossed by the current quote. Targets exclude commission/slippage/spread.
5) Optional MHV reversal (off by default): within 30 M15 bars MA5 extends outside the band with no
   opposing-momentum close; then >=2 intervening bars (a reaction and a retest near the Extreme with the
   retest close inside the band); the latest candle closes beyond the retest. Same H1 + M5 gates apply.
   Raw Extreme signals are never standalone entries.

CONFLUENCE (five-factor, reference-only, no entry trigger):
- Direction from H4: BUY if H4 close>EMA50>EMA200; SELL if close<EMA50<EMA200; else HOLD.
- Score 0-5, one point each: (1) H4 trend aligned; (2) H1 EMA9>EMA21>EMA50 (BUY)/reversed (SELL);
  (3) M15 RSI(14) in 40-65 (BUY)/35-60 (SELL); (4) M15 MACD vs signal in trade direction;
  (5) price within 0.4% of H1 rolling 50-bar low (BUY)/high (SELL). Qualify if side set AND score>=4.
- Reference levels: entry=ask(BUY)/bid(SELL); SL=1.5*ATR(14 M15); TP1=2*ATR; TP2=3.5*ATR. Output is a
  quote reference only (REFERENCE_ONLY_NO_ENTRY_TRIGGER), reviewed at the next M15 close.

ORB (XAUUSD M5 opening-range breakout):
- Build range 01:00-06:00 server time. Trade the breakout and a later retest of the range edge.
- Filters: price vs EMA200 (M5); H4 EMA20 trend direction. Dynamic reward 1.5R-3R. One signal/day (allow
  1-3 configurable). Skip Wednesday/Thursday and December (configurable). TP1 at 1R, ORB projected target
  as TP2. Stop beyond the range by a configured buffer/distance factor.

Global guards: reject quotes older than 60s; last closed bar age within 1-2 bar durations + 120s grace;
block on missing history, invalid OHLC, or data failures. News blackout (default +/-30 min) is fail-closed.
```
