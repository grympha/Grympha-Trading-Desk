# BBMA Strategy — Rules & Scanner Reference

Grympha Trading Desk · the main BBMA (Oma Ali) engine.
Source of truth: `bbma_bot.py` (rules), `scanner.py` (scan loop), `strategy_settings.py` (setup toggles).
Independent engine — shares no inputs or votes with Confluence or ORB. Broker magic **816001**.

---

## 1. What BBMA is

A three-timeframe **H1 → M15 → M5** trend re-entry engine. It identifies a trending H1 condition,
waits for a CSA/Momentum re-entry setup on M15, then a precise M5 confirmation, and proposes a
**pending stop-entry** idea with SL/TP. It does **not** trade ranges.

The decision always follows **CONDITION → SETUP → ENTRY IDEA**.

---

## 2. Step 1 — Condition (closed H1 bars)

- **BUY bias:** `H1 close > EMA50`, `Mid BB > EMA50`, and `EMA50` higher than three closed bars earlier.
- **SELL bias:** the mirrored conditions.
- **Bollinger width ≥ 2 × simple ATR(14)**, else classify as `RANGE_OR_SQUEEZE` / `TRANSITION` → no setup.
- The H1 condition must hold **now and at the time the M15 setup formed** (using only H1 candles
  already closed at that time).

These trend/width/slope filters are engineering choices; the bot does not trade ranges.

---

## 3. Step 2 — Setup (closed M15 bars)

Default setups: **CSA Re-entry** and **Momentum Re-entry** (MHV optional, see §6).

- **BUY:** one of the previous **12** M15 candles (`signal_lookback`) established strong direction by
  closing above **MA5 High, MA10 High and Mid BB**. The **setup candle** overlaps the MA5/MA10 **Low**
  zone (0.10 ATR touch tolerance), closes above both low MAs and Mid BB, and closes **bullish**.
- **SELL:** mirror — a rebound into MA5/MA10 **High**, then a **bearish** close below those averages and Mid BB.
- **Classification:** if the qualifying prior-direction candle also closed **outside the outer BB** →
  `MOMENTUM_REENTRY`, otherwise `CSA_REENTRY`. A CSA/Momentum candle alone does **not** qualify without
  the pullback/rejection setup. **Newest** valid setup takes priority.

---

## 4. Step 3 — Entry confirmation (closed M5 bars)

- Only M5 candles that **OPEN at or after the M15 setup CLOSE** are eligible.
- Within the next **4** M5 bars (`confirmation_bars`): the **first bullish M5 close above the setup high**
  confirms **BUY**; the first bearish M5 close below the setup low confirms **SELL**.
- The confirmation must be the **most recently closed** M5 candle; it is never republished on later candles.

### Pending stop-entry idea (after confirmation)
- **BUY entry** = confirmation high + current spread + buffer, rounded **up** to tick size.
- **SELL entry** = confirmation low − buffer, rounded **down** to tick size.
- **Buffer** = max(2 symbol points, 0.05 × M5 ATR).
- **BUY SL** = setup low − buffer. **SELL SL** = setup high + current spread + buffer.
- **TP1 = 1R**, **TP2 = 2R** (R = entry-to-stop price distance).
- **Expiry** = 10 minutes after the confirmation candle closes.

### Rejections (entry invalid)
- Spread > **0.15 × M5 ATR**; stop distance > **5 × M5 ATR**; entry already crossed by the current quote.
Targets exclude commissions, slippage and later spread changes.

---

## 5. Indicators & data

- **BB(20,2)**, population standard deviation, on Close, shift 0.
- **MA5 / MA10**: linear weights increasing toward the newest bar, applied **separately** to High and Low.
- **EMA50**: seeded with an initial SMA50.
- **ATR**: simple 14-bar average true range (**not** Wilder).
- Fetch ~**400 closed** bars per timeframe; MT5 bar 0 (forming) excluded.
- **Quote freshness:** rejected if older than ~60 s (`max_tick_age_seconds`), applying
  `tick_timestamp_offset_hours`. Last closed bar age must be within 1–2 bar durations + 120 s grace
  (`bar_grace_seconds`), applying `bar_timestamp_offset_hours`.
- Timestamps and alert expiry are UTC.

---

## 6. Optional MHV reversal (`enable_mhv`, off by default)

Experimental, alongside Re-entry, with the **same H1 condition and M5 confirmation gates**:
within 30 M15 bars (`mhv_lookback`), MA5 must extend outside the band with no opposing-momentum close;
then ≥ 2 intervening bars (a reaction and a retest near the Extreme, retest close **inside** the band);
the latest candle closes beyond the retest. Raw Extreme signals are never standalone entries.

---

## 7. The scanner (`scanner.py` → `BBMAScanner`)

- Runs each bot cycle on the shared `poll_seconds` cadence (default **15 s**).
- For each configured symbol (honoring **Pairs on/off** for BBMA), it runs the condition → setup →
  confirmation `assess()` and publishes CONDITION → SETUP → ENTRY IDEA (or a waiting/block reason).
- Waiting/block reasons go to console and the JSON decision log without Telegram spam.
- Setups allowed are read live from **Settings → Strategy setups → BBMA** (`_allowed_setups`:
  CSA / Momentum / MHV; at least one must stay on).
- Decisions appended to `decisions.jsonl` (rotated + gzipped at 20 MB). Delivered alert IDs persist in
  `state.json` for dedup across restarts.

---

## 8. Config keys (`config.json`)

```json
"poll_seconds": 15,
"min_bb_width_atr": 2.0,
"signal_lookback": 12,
"touch_tolerance_atr": 0.1,
"enable_mhv": false,
"mhv_lookback": 30,
"confirmation_bars": 4,
"buffer_points": 2,
"buffer_atr": 0.05,
"max_spread_atr": 0.15,
"max_stop_atr": 5.0,
"max_tick_age_seconds": 60,
"bar_grace_seconds": 120,
"entry_expiry_minutes": 10
```

---

## 9. Signal vs order

A BBMA signal is **not** a broker order or fill. In analysis mode it is observation only. In DEMO/REAL
execution (opt-in) the executor places the pending stop-entry (magic **816001**), and the shared Trade
Monitor can propose/auto early-exit on a confirmed M15 reversal (invalidation = the original setup low/high).
