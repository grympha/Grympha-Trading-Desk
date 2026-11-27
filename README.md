Strategy rules: read **STRATEGIES.md** for the index of all three engines (BBMA, Confluence, ORB) and how they share the journal, executor, monitor, news filter and pair controls.

Current release: **v1.9 with Today History**. Read **DAILY_HISTORY.md** for date filters, P/L/count definitions and upgrade steps. Windows launcher and all four tabs are included.

Windows click-to-run launcher included: double-click **START_APP.cmd**. Read **LAUNCHER_GUIDE.md** for first-PC setup and missing-requirement guidance. Bot/backend remains v1.8.

For the current complete upgrade, read **UPGRADE_v1_8.md** first. It includes the MT5 Calendar adapter and four-tab dashboard; no Trading Economics key is needed for the MT5 provider.

# BBMA + Confluence Signal Desk v1.8

For v1.7 broker-only dashboard, risk sizing, news-filter credentials, management controls and upgrade commands, read **DASHBOARD_SETUP.md** first. The BBMA rules below describe its independent engine; the uploaded five-factor strategy is documented separately there.

A standalone Python 3.11+ signal bot for Windows MT5. It reads market data and optionally sends Telegram alerts. Default mode sends no orders. Optional DEMO execution is described in DEMO_EXECUTION.md. Example symbols: XAUUSD, GBPJPY, USDJPY, EURUSD, GBPUSD, AUDUSD. Use exact Market Watch names if your broker adds suffixes.

## Start on your Windows PC

1. Extract the ZIP into a new folder. This is a separate bot, not a replacement for your existing multi-pair bot.
2. Open MT5 and log in normally. Load H1, M15 and M5 history for each symbol. No broker password is stored by this bot.
3. Open PowerShell in the extracted `bbma_bot` folder:

```powershell
py -3.11 -m pip install -r requirements.txt
Copy-Item config.example.json config.json
Copy-Item .env.example .env
notepad .env
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. Keep them private. Leave both blank for console-only mode. The bot loads `.env` beside the script, independent of the current folder.

4. Edit `config.json` for broker symbol names. If you have multiple MT5 terminals, set `terminal_path` to the intended terminal executable, using escaped backslashes in JSON, e.g. `"C:\\Program Files\\RoboForex MT5 Terminal\\terminal64.exe"`.
5. Run:

```powershell
py -3.11 bbma_bot.py --test-telegram
py -3.11 bbma_bot.py --once
py -3.11 bbma_bot.py
```

Ctrl+C stops it. Run one instance per extracted folder. If PowerShell starts elsewhere, use `cd` to this folder first. Internet access is required for Telegram. MT5 must remain open and connected; the bot blocks analysis while disconnected and resumes after terminal reconnection. If initialization fails, correct the path/login and restart the script.

## Exact decision sequence

### 1. Identify market condition: closed H1 bars

BUY bias requires H1 close > EMA50, Mid BB > EMA50, and EMA50 higher than three closed bars earlier. SELL requires the mirrored conditions. Bollinger width must be at least 2 simple ATR(14). Otherwise classify as RANGE_OR_SQUEEZE or TRANSITION and provide no setup or entry.

These trend, width and slope filters are engineering choices, not exact instructions from the PDF. This bot does not trade ranges. H1 context must agree both now and at the time the M15 setup became known, using only H1 candles already closed at that time.

### 2. Recognise setup: closed M15 bars

Default: CSA Re-entry or Momentum Re-entry.

BUY: one of the previous 12 M15 candles established strong direction by closing above MA5 High, MA10 High and Mid BB. The setup candle overlaps the MA5/MA10 Low zone (0.10 ATR touch tolerance), closes above both low MAs and Mid BB, and closes bullish.

SELL: mirror the rules using a rebound into MA5/MA10 High, followed by a bearish close below those averages and Mid BB.

If a qualifying prior direction candle also closed outside the appropriate outer BB, classify as MOMENTUM_REENTRY; otherwise CSA_REENTRY. A CSA or Momentum candle alone does not qualify without a pullback/rejection setup. Only a recently completed setup within the confirmation window can be considered. Newer setups take priority.

### 3. Provide entry point: closed M5 confirmation

Only M5 candles that OPEN at or after the M15 setup CLOSE are eligible. Within the next four M5 bars, the first bullish M5 close above the setup high confirms BUY; the first bearish M5 close below the setup low confirms SELL. The confirmation must be the most recently closed M5 candle. No earlier confirmation is republished on later candles.

After confirmation, the bot proposes a pending stop-entry idea:

- BUY entry: confirmation high + current spread + buffer, rounded upward to the symbol tick size.
- SELL entry: confirmation low - buffer, rounded downward to the symbol tick size.
- Buffer: greater of two symbol points or 0.05 M5 ATR.
- BUY SL: setup low - buffer. SELL SL: setup high + current spread + buffer.
- TP1 = 1R and TP2 = 2R, where R is entry-to-stop price distance.
- Expiry: 10 minutes after the confirmation candle closes. Invalidate manually if the setup or H1 condition fails sooner.

Spread must be <=0.15 M5 ATR and stop distance <=5 M5 ATR. An entry already crossed by the current quote is rejected. These SL/TP, expiry and buffer rules are explicit engineering additions; they are not PDF claims. Targets exclude commissions, slippage and subsequent spread changes. Analysis does not size positions; the optional DEMO adapter defaults to a fixed 0.01 lot.

A signal alone is not a broker order or fill. v1.7 shows broker orders, positions and verified exits, with condition/setup context, actual fills and chart levels. Optional risk sizing, news filtering and independent partial/SL management are documented in DASHBOARD_SETUP.md.

## Optional MHV reversal approximation

Set `enable_mhv` to `true` to enable this experimental setup alongside Re-entry; the same H1 condition and M5 confirmation gates still apply.

Within 30 M15 bars, MA5 must have extended outside the relevant band without a close showing opposing Momentum. There must then be at least two intervening bars: a reaction and a retest near the Extreme high/low, with the retest close inside the band. The latest reversal candle must close beyond the retest low/high. The setup boundary spans the Extreme and subsequent reaction/retest. This is a deliberately strict interpretation of the slides, not a complete encoding of all MHV shapes. It is disabled by default.

Raw Extreme signals are never standalone entries. The countertrend Extreme/TP Wajib scalp is not implemented. The PDF's 'Zero Loss' label is not used as a promise or a separate entry strategy. Higher-timeframe alignment is used instead of the custom scalper indicator (whose source was not supplied).

## Indicators and data

BB(20,2), population standard deviation, Close, shift zero. MA5 and MA10 use linear weights increasing toward the newest bar, separately applied to High and Low. EMA50 uses an initial SMA50 seed. ATR is the simple 14-bar average of true range, explicitly not Wilder ATR. Fetch 400 CLOSED bars per timeframe; MT5 bar zero is excluded. Broker sessions and gaps affect bar counts. Timestamps and alert expiry are UTC.

Quotes older than 60 seconds are rejected. Most recently closed bars must have an age between one and two bar durations plus a 120-second grace period. Missing history, invalid quotes/OHLC and data failures block that symbol. Decisions are appended to `decisions.jsonl` as compact lines (candle arrays are not copied; `chart_last_bar` records which closed candle was used). At 20 MB the log moves to `logs/` and is gzip-compressed automatically; nothing is deleted. To archive an existing log by hand: `py -3.11 decision_log.py --archive decisions.jsonl`.

## Alerts and state

Every entry alert follows CONDITION -> SETUP -> ENTRY IDEA, then SL/TP and expiry. Waiting/block reasons appear in the console and JSON log, without Telegram spam. Delivered IDs persist in `state.json`, including across restarts. Failed Telegram deliveries retry while the confirmation remains current. Telegram network ambiguity can still cause duplicates (e.g. Telegram accepts a message but the response times out); exactly-once delivery is not guaranteed. State writes are atomic. Do not run two copies in the same folder. Keep `state.json` to retain deduplication.

## Verification and limits

Run engine tests with:

```powershell
py -3.11 -m unittest discover -v
```

Ten synthetic tests cover indicator calculations, condition filtering, no setup on an unpulled-back CSA, prior direction requirement, optional MHV sequencing, no premature M5 entry, BUY/SELL price geometry, and rejection of wide spreads, chased entries, expired windows and old confirmations.

The engine was syntax-checked and these tests passed in the build environment. A live Windows MT5 connection and your Telegram account were not available there; integration remains to be checked on your PC. No profitability backtest, broker-specific calibration, walk-forward study or forward-test results are included. Begin with signal observation/demo validation before considering manual execution.

Sources: supplied BBMA Oma Ali PDF, especially pages 1, 9-20, 26 and 30-36. Official MT5 integration references:
https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py
https://www.mql5.com/en/docs/python_metatrader5/mt5symbolinfotick_py


## v1.1 time diagnostics

If all symbols show stale quotes, first run:

```powershell
py -3.11 bbma_bot.py --diagnose-time
```

This prints PC UTC/local time, terminal connection/path, broker server (no account number or credentials), three quote samples per symbol, raw/adjusted quote ages, whether timestamps advanced, and last closed bar times. It sends no alerts. A negative raw age means the quote timestamp is ahead of the PC. A positive age over 60 seconds means it appears old. Either can result from incorrect clock/feed timestamp assumptions; this alone does not prove the market is closed.

Check Windows Settings > Time & language > Date & time > Sync now. Verify MT5 is logged into the intended server and Market Watch quotes are updating. Do not increase the stale threshold to hours or disable it. Official MT5 Python docs describe timestamps as UTC; therefore the default correction remains zero. Your local timezone requires no correction.

If diagnostics and independent broker/time verification establish that a feed actually encodes UTC+2, add `"tick_timestamp_offset_hours": 2` to config.json. The normalization subtracts two hours from the raw timestamp. A matching bar correction must be independently verified and set with `"bar_timestamp_offset_hours": 2`; tick and bar corrections are separate, and both default to zero when omitted. A UTC-2 feed uses -2. Do not set these merely because MT5's chart clock differs from your local clock. Fixed offsets need review if the broker changes its clock seasonally. Strategy rules are unchanged. Eleven synthetic tests now pass; live integration still requires the user's terminal.

## v1.2 JSON logging fix

Momentum comparison now returns a native Python bool rather than numpy.bool_. This fixes JSON decision logging in directional conditions. The trading rules and timestamp configuration are unchanged. Twelve tests pass, including bullish/bearish momentum true/false serialization. When upgrading, replace bbma_bot.py and optionally test_bbma.py; retain your .env, config.json and state.json, including verified +2 tick/bar timestamp offsets.

## ORB agent (third strategy, v1.26)

`orb_core.py` is the unchanged engine from *Grympha ORB Scanner v1* (opening-range breakout + later retest, XAUUSD M5, EMA200 M5, H4 EMA20 trend, dynamic RR 1.5–3R, one signal per day, skips Wednesday/Thursday and December). `orb_strategy.py` adapts it to this bot (UTC conversion, one-signal-per-day state in `orb_state.json`, plan with TP1 at 1R and ORB's target as TP2) and `orb_scanner.py` runs it each cycle. Trades use magic 816003 and comments like `ORB Retest #a1b2c3`.

Differences from the standalone package: the range is **01:00–06:00 server time** (RoboForex XAUUSD opens at 01:00, so 00:00–06:00 would never be complete); the server offset defaults to `bar_timestamp_offset_hours` (update at DST changes); news uses this bot's shared MT5 calendar filter with the same window as BBMA and Confluence.

**ORB starts in analysis only.** It scans and shows setups but sends no orders until *Settings → Strategy setups → Allow ORB to place trades* is ticked. It has no backtest or live track record yet; run it on DEMO first. **ORB rules in the dashboard (v1.27):** *Settings → Strategy setups → ORB* sets trading days (default skips Wed/Thu), skipped months (default December), signals per day (1–3), the stop-loss buffer beyond the range (BUY/SELL points) and distance factor (default 60/100 pts ×0.9), and take-profit (dynamic 1.5–3R or a fixed 1–5R). Changes apply from the bot's next check. Range/session hours and symbols stay in `config.json` under `"orb"`.

## Automatic database backup (v1.29)

Every 24 hours (and shortly after the first start) the bot saves a verified copy of `dashboard.sqlite3` (trade ledger, history, reviews and dashboard settings) plus `config.json` and `launcher_settings.json` into `backups/<date_time>/`, keeping the newest 14. It uses SQLite's online backup, so the bot keeps running, and checks each copy before keeping it. `.env` (Telegram token) is not copied. Status and a **Back up now** button are on the dashboard's System Health page. Change the schedule in `config.json`: `"backup": {"enabled": true, "every_hours": 24, "keep": 14, "folder": "backups"}` (an absolute folder such as `C:/GrymphaBackups` is allowed, e.g. to keep copies on another drive).

**Restore:** stop the bot, copy `backups/<date_time>/dashboard.sqlite3` over `dashboard.sqlite3`, delete `dashboard.sqlite3-wal` and `dashboard.sqlite3-shm` if present, then start the bot.

## Weekend rule: no positions over the weekend

Every Friday from **20:00 MT5 server time** (the broker's own clock, read from the pair's latest tick, so summer/winter time follows the broker) the bot closes its open positions on every pair except **BTCUSD and ETHUSD**, cancels its pending entry orders on those pairs, and opens no new entries on them until the market reopens. Crypto keeps trading through the weekend. Only the bot's own trades are touched, never manual ones. Each close is sent once; an unconfirmed reply is shown in the Execution Ledger ("Weekend close · UNCONFIRMED") for checking in MT5 and is never resent automatically. Change it in `config.json`: `"weekend_close": {"enabled": true, "friday_time": "20:00", "keep_open": ["BTCUSD", "ETHUSD"]}`.

## Live Test agents trading live (opt-in per agent × pair)

Each Live Test agent card has a **Trade live** switch (off by default; a confirmation shows the agent's
Live Test record and warns on a REAL account). Switched-on agents are listed in **Settings → Live Test
agents trading live**, where **Stop trading live** removes them; the section is hidden when none are on.

- The agent keeps running as a simulation; `agent_execution.py` hands each NEW simulated entry (seen
  within 90 s of the simulated fill) to the executor, which places it at market with every usual check:
  REAL-account check, risk sizing (Settings → Trade management), position cap, news filter, weekend rule,
  duplicate-bot guard, portfolio guard, broker pre-check. Refused if price moved > 0.25R from the
  simulated fill. Strategy trade windows and pair on/off switches do not apply (the agent's own rules do).
- After entry the live trade **mirrors the simulation**: a tighter simulated stop (break-even / trail)
  moves the broker SL, a simulated partial close is copied once, and when the simulated trade closes the
  live position is closed at market. Broker SL/TP stay as a safety net (initial levels; TP capped at 20R
  for agents with no take-profit). Exits follow up to one bot cycle (~15 s) after the simulation.
- Magic number 816004; comments `LT <Agent> #id`, `LT agent mirror close` and so on. The ledger shows
  `LT · <agent>`. One live trade per agent and pair at a time.
- Switching off stops new entries only; an open live trade keeps mirroring until it closes. A paused
  Live Test agent opens no trades. BBMA / Confluence / ORB live-copy agents cannot be switched on: they
  already trade live as their strategy.
- Live Test modules never import the bridge (tested); only the executor sends orders.

## Two desks on one PC (two MT5 accounts)

Each desk is its own project folder with its own MT5 installation, `config.json`, database and port:
install MT5 a second time in another folder and log in to the second account there; copy the project
folder and delete `dashboard.sqlite3` in the copy (the launcher rebuilds the copied `.venv` on
Install/Repair); in the second launcher pick that MT5's `terminal64.exe` and port 8766. The launcher
lock is per folder, so both run side by side.

- **Desk name** (launcher, top row; saved in that folder's `launcher_settings.json`): shown in the
  launcher title and tray tooltip, the dashboard header and browser tab, the home page header, and in
  front of every Telegram alert as `[name]`. Without a name the dashboard and home page show the MT5
  server and account type and Telegram alerts are unchanged (`desk_identity.py`).
- **Start with Windows** writes one Startup entry per folder, `Grympha Trading Desk (<folder>).cmd`; an
  older shared `Grympha Trading Desk.cmd` is renamed by the desk it starts. A desk switched on while
  another already starts with Windows waits 20 s per other desk, so the two do not open MT5 together.
