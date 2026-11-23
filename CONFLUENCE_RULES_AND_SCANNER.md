# Confluence Strategy — Rules & Scanner Reference

Grympha Trading Desk · the five-factor Confluence agent.
Source of truth: `confluence_strategy.py` (rules) and `confluence_scanner.py` (scan loop).
This is an **independent** engine — it shares no inputs or votes with BBMA or ORB.

---

## 1. What Confluence is

A multi-timeframe (H4 → H1 → M15) **confluence scoring** engine. Each cycle it grades a symbol
on five independent factors (0–5). When a direction is set and the score clears the minimum, it
publishes a **reference** trade idea with ATR-based levels.

**It is reference-only.** The output is tagged `order_idea = QUOTE_REFERENCE`,
`status = REFERENCE_ONLY_NO_ENTRY_TRIGGER`. Unlike BBMA/ORB it does **not** emit a pending
stop-entry trigger; it scores context and shows levels for review. (If a Confluence position is
opened/managed, the shared Trade Monitor and the Confluence early-exit toggle still apply to it.)

---

## 2. Direction gate (H4) — required first

Direction comes only from the latest closed **H4** candle:

- **BUY** if `H4 close > EMA50 > EMA200`
- **SELL** if `H4 close < EMA50 < EMA200`
- otherwise **HOLD** (no side → cannot qualify, score still reported)

---

## 3. The five factors (score 0–5)

One point each. Factors 2–5 are evaluated in the H4 direction; with no H4 direction they fail.

| # | Factor | BUY passes when | SELL passes when |
|---|--------|-----------------|------------------|
| 1 | **H4 trend** | `close > EMA50 > EMA200` | `close < EMA50 < EMA200` |
| 2 | **H1 EMA alignment** | `EMA9 > EMA21 > EMA50` | `EMA9 < EMA21 < EMA50` |
| 3 | **M15 RSI(14)** | `40 < RSI < 65` | `35 < RSI < 60` |
| 4 | **M15 MACD** | `MACD line > signal` | `MACD line < signal` |
| 5 | **Near H1 level** | price within `sr_near_pct` of the H1 rolling-50-bar **low** | price within `sr_near_pct` of the H1 rolling-50-bar **high** |

- `score = number of factors passed` (0–5).
- **Qualified** when a direction is set **and** `score >= min_score` (default **4**).
- `sr_near_pct` default **0.004 = 0.4%**; S/R lookback = **H1 rolling 50 bars** (`sr_lookback_h1`).

---

## 4. Reference levels (only when qualified)

Computed from **M15 ATR(14)** at the current quote (`entry = ask` for BUY, `bid` for SELL):

| Level | Formula | Default |
|-------|---------|---------|
| Stop loss | `entry − sign × sl_atr × ATR` | `sl_atr = 1.5` |
| TP1 | `entry + sign × tp1_atr × ATR` | `tp1_atr = 2.0` |
| TP2 | `entry + sign × tp2_atr × ATR` | `tp2_atr = 3.5` |
| RR1 / RR2 | `tp1_atr / sl_atr` · `tp2_atr / sl_atr` | **1.33R / 2.33R** |

(`sign = +1` BUY, `−1` SELL.) Also emitted: `confirmation_utc` = M15 close + 15 min,
`expires_utc` = M15 close + 30 min — this is the **next-M15-close review time, not an order expiry**.

---

## 5. Indicators (per timeframe, via the `ta` library, default params)

`EMA 9 / 21 / 50 / 200`, `RSI(14)`, `MACD(12,26,9)`, `ATR(14)`, `BollingerBands(20,2)` on Close.
Computed independently on M15, H1 and H4. Warm-up / non-finite values or `ATR ≤ 0` → the symbol is
blocked for that cycle (never scored on bad data).

---

## 6. The scanner (`confluence_scanner.py`)

Runs on its **own cadence**, separate from BBMA:

1. **Enabled?** Skips entirely if Confluence is disabled in config.
2. **Due?** Only runs when `time.monotonic() >= next_due`; then sets `next_due += check_seconds`
   (default **300 s** = every 5 minutes).
3. **Per symbol** in `confluence.symbols` (default: the XAU/GOLD symbols, else the configured list):
   - Skipped if that pair is turned off for Confluence in **Pairs on/off** (`pair_controls`).
   - Runs `Runner.check(...)`, publishes the result, appends a line to
     `confluence_decisions.jsonl`, and prints `CONFLUENCE <symbol> <score>/5 <reason>`.
4. **Counters returned:** `checked`, `blocked`, `closed`.
5. **MarketClosed** → published as `health = MARKET_CLOSED` (logged once, not spammed).
   Any other error → `health = BLOCKED` with the reason (fail-safe; no scoring on bad data).

---

## 7. Data-quality gates (`Runner.check`) — all must pass

Before any scoring, every cycle verifies:

- **MT5 connected**; symbol selectable; tick & symbol info present.
- **Quote freshness:** `age = now − (tick.time − tick_timestamp_offset_hours × 3600)`.
  Must be `−5 ≤ age ≤ max_tick_age_seconds` (default **180 s** for Confluence).
  `age ≥ −5` runs the market-closed check (weekend/rollover → `MarketClosed`).
- **Valid quote:** `bid > 0`, `ask ≥ bid`, `point > 0`; spread ≤ `max_spread_points` (default 0 = off).
- **Bars per timeframe (M15, H1, H4):** fetch 500 closed bars, require **≥ 250**; confirm bar 0 is
  newer than the last closed bar (closed-bar verification); no duplicate timestamps; finite OHLC;
  valid OHLC geometry.
- **Bar timestamp offset** `bar_timestamp_offset_hours` subtracted; last closed bar age must be within
  `[tf_seconds, 2 × tf_seconds + bar_grace_seconds]` (grace default 120 s), else stale/unclosed.
- **Dedup:** keyed by the last **M15** candle time, cached in `confluence_state.json`. If no new M15
  candle since last run, the previous result is re-served with `new_analysis = false` and
  "No new closed M15 candle" prepended — it never re-scores the same candle. The cache is cleared
  automatically if strategy settings or the bar-offset basis change.

---

## 8. Config keys (`config.json → "confluence"`)

```json
"confluence": {
  "enabled": true,
  "symbols": ["XAUUSD", "..."],
  "check_seconds": 300,        // scan cadence
  "min_score": 4               // 1–5; qualify threshold
}
```

Internal defaults not usually surfaced: `sr_lookback_h1 = 50`, `sr_near_pct = 0.004`,
`sl_atr = 1.5`, `tp1_atr = 2.0`, `tp2_atr = 3.5`, `max_spread_points = 0`,
`max_tick_age_seconds = 180`. In the dashboard, **Settings → Strategy setups → Confluence**
sets the minimum score (3, 4 or 5; H4 direction is always required).

---

## 9. Output shape (per symbol, each cycle)

```
strategy=CONFLUENCE, health=OK|MARKET_CLOSED|BLOCKED,
condition={name: BULLISH|BEARISH|HOLD, side},
score=N, max_score=5, min_score, qualified=bool,
factors=[{name, passed, detail} × 5],
support, resistance, atr,
entry_plan= null | {side, entry, stop_loss, tp1, tp2, score, rr1, rr2,
                    order_idea: 'QUOTE_REFERENCE',
                    status: 'REFERENCE_ONLY_NO_ENTRY_TRIGGER', ...},
reason="N/5 factors passed. ...",
quote={bid, ask, spread, spread_points, digits},
chart=[last 80 M15 bars], charts={H4,H1,M15}
```

Every decision is appended to `confluence_decisions.jsonl`; the dashboard reads the latest per
symbol for the Confluence view.
