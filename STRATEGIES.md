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

## Other project references

- **README.md** — install, run modes, decision sequence overview.
- **PROJECT_PROMPTS.md** — rebuild / continue-dev / assistant / strategy-only prompt pack.
- **DASHBOARD_SETUP.md**, **DEMO_EXECUTION.md**, **PORTFOLIO_EXECUTION_HEALTH.md**,
  **TRADE_REVIEW.md**, **COUNTERFACTUAL_REPLAY_AND_VERSIONS.md** — subsystem guides.
