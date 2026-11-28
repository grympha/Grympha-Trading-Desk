# S&R Zones: rules (Live Test agent `SNR_ZONES`)

Support and resistance traded as **zones built from several swing points**, with a candle-close
confirmation on M15. Live Test only: every entry, exit and result is **simulated** on live MT5 data.
Nothing is traded unless you switch an agent on with **Trade live** yourself. Code: `live_test_snr.py`.

These settings are a first, testable model taken from common S&R teaching (see Sources). They are
not a proven edge: Live Test and the Quant Agent decide whether they work.

## 1. Zones (H4, closed candles only)

1. Take the last **120 closed H4 candles**.
2. Find **swing highs and swing lows**: a candle whose high (low) is the highest (lowest) of the
   3 candles on each side. Two side-by-side candles with the same extreme count as **one** touch.
3. Group swing points that sit within **0.35 × H4 ATR(14)** of each other. A group with **at least
   2 swing points** is a zone, from its lowest to its highest point, at least **0.25 × H4 ATR** wide.
   Highs and lows go in the same group: a level that was resistance and later support is one zone.
4. A zone **below** price is support, **above** price is resistance.
5. **Visits:** each separate run of H4 candles that trades into the zone after it formed. A zone
   visited **more than 4 times** is treated as used up (no Bounce or Fakeout trades).

The still-forming H4 candle is never used, so the zones at any moment only use information that
existed then (the same in Live Test and in Quant Agent backtests).

## 2. The three setups (one agent per setup)

All triggers are checked when an **M15 candle closes**.

| Setup | BUY (SELL is the mirror) |
|---|---|
| **Bounce** | Price came down into a **support** zone (the candle opened, and the one before closed, at or above the zone's bottom), traded into it, and the M15 candle **closed above the zone** with a **lower wick ≥ 50 % of the candle** or a **bullish engulfing** body. A candle that rallies up through the zone from below is a breakout, not a bounce. |
| **Break & retest** | An H4 candle **closes above a zone** by at least **0.25 × H4 ATR**, with the H4 candle before it closing at or below the zone's top. This arms a retest for **24 hours**. An M15 candle then dips back into the zone and **closes back above it as a bullish candle** → BUY. An M15 close back below the zone cancels it (`ZONE_REBROKEN`); no retest in 24 h → `RETEST_TIMEOUT`. |
| **Fakeout** (optional, off by default) | An M15 candle that opened at or above a zone pokes **below the zone's bottom** by at least 0.05 × H4 ATR and **closes back above the bottom**. |

## 3. Stop, target and filters

- **Stop:** beyond the zone's far edge or the trigger candle's extreme (whichever is further), plus
  **0.10 × M15 ATR**.
- **Target:** the **near edge of the next opposing zone** (for a BUY, the bottom of the next zone
  above). No zone there → skipped (`NO_TARGET`).
- **Minimum reward 1.5R** to that target (`RR_TOO_LOW`).
- **Stop size** must be at least the larger of **0.5 × M15 ATR** and **3 × the current spread**, and
  at most **6 × M15 ATR** (`RISK_OUT_OF_BOUNDS`). The spread rule stops "tiny stop" trades that turn
  ordinary moves into +40R results.
- **One trade per zone and direction** until price has moved **1 × H4 ATR** away from the zone
  (`ZONE_USED`).
- **Time exit** after **12 M15 candles** (3 hours) if neither stop nor target is hit; the usual Live
  Test weekend, market-closed and data-gap handling applies.

## 4. Regime filter (side-by-side test)

Every agent records the market regime at entry (Market Scout's label from closed H1/H4 candles:
Crisis / Trending / High volatility / Mean reverting / Mixed), so the Grade check can split results by
regime. Each setup also runs a **second copy with the regime filter on**:

| Setup | Allowed regimes with the filter on |
|---|---|
| Bounce | Mean reverting, Mixed |
| Break & retest | Trending, High volatility |
| Fakeout | Mean reverting, Mixed, High volatility |

Never in **Crisis**. A filtered setup is logged as `REGIME_FILTERED`.

## 5. Agents registered

On every Live Test pair: **Bounce**, **Bounce · regime filter**, **Break & retest**,
**Break & retest · regime filter** (4 agents per pair). They start **paused**: start them on the
Live Test page. Config `snr` in `config.json` can change this:

```json
"snr": {
  "modes": ["BOUNCE", "RETEST", "FAKEOUT"],
  "regime_filter_variants": [false, true],
  "symbol_overrides": {"XAUUSD": {"min_rr": 2.0}}
}
```

Changing a setting creates a new agent version (a new id); the old one keeps its history.

## 6. Every skipped setup is logged

Live Test's signal log shows why a trigger did not become a trade: `ZONE_USED`, `REGIME_FILTERED`,
`RISK_OUT_OF_BOUNDS`, `NO_TARGET`, `INVALID_LEVELS`, `RR_TOO_LOW`, `RETEST_TIMEOUT`, `ZONE_REBROKEN`.

## Sources

- C. Osler (2000), *Support for Resistance: Technical Analysis and Intraday Exchange Rates*, Federal
  Reserve Bank of New York Economic Policy Review: published FX support/resistance levels saw more
  trend interruptions than arbitrary levels; the effect varied by currency and firm.
- Trading-education descriptions of zones, touches, rejection and break-and-retest entries
  (Babypips, The Right Trader, TraderFaculty, FXGlory, WePassChallenges, and others). None of them
  publish backtests: these rules have to prove themselves here.
