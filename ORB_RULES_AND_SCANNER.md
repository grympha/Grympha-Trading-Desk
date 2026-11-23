# ORB Strategy — Rules & Scanner Reference

Grympha Trading Desk · the Opening-Range Breakout + retest agent (third strategy).
Source of truth: `orb_core.py` (unchanged original engine), `orb_strategy.py` (adapter/runner),
`orb_scanner.py` (scan loop). Independent engine. Broker magic **816003**.

> ⚠️ ORB is the newest engine with **no backtest/track record**. It starts in **analysis only** and
> places no orders until *Settings → Strategy setups → ORB → Allow ORB to place trades* is ticked
> (config `orb.trade`). Run it on DEMO first.

---

## 1. What ORB is

For **XAUUSD on M5**: build the day's opening range, wait for a **breakout** close beyond it, then a
**retest** of the broken edge that holds, filtered by trend. Emits **one signal per symbol per session
day** (configurable 1–3). `orb_core.py` is the deterministic original engine; `orb_strategy.py` adapts
it into this bot's `condition → setup → entry_plan` format.

---

## 2. Session & calendar

- **Opening range:** `range_start_hour` **01:00** → `range_end_hour` **06:00** server time.
  (01:00 start because RoboForex XAUUSD opens at 01:00; 00:00 would never complete.)
- **Trade window:** `trade_start_hour` **07:00** → `trade_end_hour` **17:00** server time.
- **Skip days:** `skip_weekdays` default **[2, 3] = Wednesday & Thursday**.
- **Skip months:** `skip_months` default **[12] = December**.
- Server time = UTC + `session_utc_offset_hours`, which defaults to `bar_timestamp_offset_hours`
  (keep it current across DST).

---

## 3. Entry logic (orb_core)

1. **Build range** between range start/end from M5 bars. Range must pass size filters:
   `min_range_points` (1000) ≤ size ≤ `max_range_points` (7700), else `RANGE_FILTERED`.
2. **Breakout:** an M5 **close** beyond the range edge (buffer `dynamic_buffer_pct` 3%), within
   `max_bars_after_breakout` (30) bars.
3. **Retest:** price returns to the broken edge within `retest_tolerance_points` (80) and holds
   (penetration ≤ `max_retest_penetration_fraction` 0.25); the retest close confirms the signal.
4. **Trend filters:** price vs **EMA200 (M5)**; direction aligned with the **H4 EMA20** trend
   (`h4_ema_period` 20).
5. **Dynamic reward:** orb_core suggests a target in a **1.5R–3R** band (unless overridden, §5).

Status values surface in the dashboard: `BUILDING_RANGE`, `WAIT_BREAKOUT`, `WAIT_RETEST`,
`SIGNAL_CANDIDATE`, `RANGE_FILTERED`, `INCOMPLETE_RANGE`, `SESSION_DATA_GAP`,
`OUTSIDE_TRADE_WINDOW`, `SKIPPED_SESSION`, `DAILY_SIGNAL_LIMIT`, `ALREADY_ISSUED`, etc.

---

## 4. Entry plan (adapter → this bot's format)

Market entry at the executable quote (like Confluence), from the signal:
- `entry = entry_reference`, `stop_loss = sl_suggested`, `tp2 = tp_suggested` (ORB's own target).
- **TP1 = 1R** (entry ± risk, rounded to tick) — gives break-even / partial-close management a level.
- `rr1 = 1.0`, `rr2 = ORB's RR after rounding`.
- Stop buffers: `buy_sl_buffer_points` (60), `sell_sl_buffer_points` (100), `sl_multiplier` (0.9).

### Signal guards (adapter/engine)
- Spread ≤ `max_spread_points` (80) else `SPREAD_BLOCKED`.
- Entry drift ≤ `max_entry_drift_atr` (0.5 ATR) from the retest close, else `ENTRY_DRIFT_BLOCKED`.
- Signal age ≤ `max_signal_age_seconds` (60) else `STALE_SIGNAL_SUPPRESSED`.
- Quote age ≤ `max_tick_age_seconds` (30), applying `tick_timestamp_offset_hours`;
  `age ≥ −5` runs the market-closed check (weekend/rollover → MarketClosed).
- Invalid stop geometry → `INVALID_STOP`; price back inside range before entry → `LEVEL_LOST_BEFORE_EMISSION`.

---

## 5. Dashboard settings (Settings → Strategy setups → ORB)

Read live every cycle (`strategy_settings['orb']`, applied via `ORBRunner.apply`):

| Setting | Default | Range |
|---------|---------|-------|
| **trade** (allow orders) | off | on/off |
| **skip_weekdays** | Wed, Thu | any subset of Mon–Fri (not all 5) |
| **skip_months** | December | any subset of 1–12 (not all 12) |
| **signals_per_day** | 1 | 1–3 |
| **sl_multiplier** (distance factor) | 0.9 | 0.5–1.0 |
| **buy_sl_buffer_points / sell_sl_buffer_points** | 60 / 100 | 0–2000 |
| **tp_mode** | dynamic | dynamic (1.5–3R) or fixed |
| **fixed_rr** | 2.0 | 1.0–5.0 (used when tp_mode = fixed) |

Range/session hours and symbols stay in `config.json` under `"orb"`.

---

## 6. Data requirements (`ORBRunner.check`)

- MT5 connected; symbol selectable; valid tick/info.
- **M5:** 2000 bars fetched; **H4:** 400 bars (for EMA200 / EMA20 warm-up). Insufficient →
  `INDICATOR_WARMUP_INCOMPLETE`. Gaps in today's M5 → `SESSION_DATA_GAP` (paused for safety).
- `bar_timestamp_offset_hours` subtracted from bar times; only **closed** bars are used
  (M5: `time + 300 ≤ now`; H4: `time + 14400 ≤ now`).

---

## 7. One-signal-per-session limit & dedup

- State persisted in **`orb_state.json`** (`emitted[symbol] = {session, ids}`), surviving restarts.
- A signal already in today's `ids` → `ALREADY_ISSUED`; reaching `signals_per_day` → `DAILY_SIGNAL_LIMIT`.
- Each emitted signal's id is recorded before the plan is returned.

---

## 8. The scanner (`orb_scanner.py`)

- Runs each bot cycle; for each ORB symbol (honoring **Pairs on/off**) calls `ORBRunner.check`,
  publishes the result, and logs to `orb_decisions.jsonl`.
- **Analysis-only until `orb.trade` is true** — the executor refuses to submit ORB orders otherwise.
- News is enforced by the bot's shared **MT5 calendar filter at execution** (not inside ORB), same
  window as BBMA/Confluence.

---

## 9. Execution & monitoring

When enabled and in DEMO/REAL mode, the executor places ORB orders (magic **816003**, comments like
`ORB Retest #abcdef`). The shared **Trade Monitor** evaluates ORB positions too (invalidation = the
broken-out **range edge**); **ORB auto early-exit** works in REAL (config
`real_execution.auto_early_exit.ORB`) and in DEMO (dashboard toggle) once enabled.
