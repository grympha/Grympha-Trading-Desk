# Elliott Wave: rules (Live Test agent `EW_IMPULSE`)

Wave 3 and wave 5 entries built only from the **objective** part of Elliott Wave: a mechanical swing
detector, the three hard rules and Fibonacci depth checks. Live Test only: every entry, exit and
result is **simulated** on live MT5 data. Nothing is traded unless you switch an agent on with
**Trade live** yourself. Code: `live_test_elliott.py`.

Elliott counting is subjective; two analysts often label the same chart differently, and no
independent study shows automated counts predict prices. This agent tests whether the objective
core has any edge. The settings below are starting values from common teaching, not proven ones.

## 1. Swings: ATR zigzag on closed H1 candles

- A swing high (low) is **confirmed** once price has fallen (risen) from it by **2.5 × H1 ATR(14)**.
- The zigzag's last leg is the **wave in progress** (not confirmed yet): wave 2 or wave 4.
- Each swing records when it was confirmed; the agent only ever uses swings that were confirmed at
  that moment (the same in Live Test and in Quant Agent backtests). No re-labelling afterwards.

## 2. The three hard rules (a count that breaks one is rejected and logged)

1. Wave 2 never goes past the start of wave 1 (`RULE1_BROKEN`).
2. Wave 3 is never the shortest of waves 1, 3, 5. Checked as **wave 3 longer than wave 1**, which
   keeps the rule true whatever wave 5 does (`RULE2_W3_SHORTER`).
3. Wave 4 never enters wave 1's price range (`RULE3_W4_OVERLAP`). Diagonals (the exception) are
   not traded.

## 3. Setups (one agent per setup)

Checked at each **H1 close**; the entry is the quote within 5 minutes of that close (never later).

| | Wave 3 (BUY; SELL mirrors) | Wave 5 (BUY; SELL mirrors) |
|---|---|---|
| Count | Confirmed swings P0 (low, wave 1 start) and P1 (high, wave 1 end); the current leg falls to P2 (wave 2) | Confirmed P0 low, P1 high, P2 low, P3 high; the current leg falls to P4 (wave 4) |
| Rules | Rule 1 | Rules 1, 2, 3 |
| Depth | Wave 2 = **38.2 – 78.6 %** of wave 1 | Wave 4 = **23.6 – 50 %** of wave 3 |
| Trigger | The pullback **turns**: an H1 candle closes bullish at least **1 × ATR** above the wave 2 low | Same, above the wave 4 low |
| Stop | Below **P0** (wave 1 start: where rule 1 says the count is wrong) − 0.1 × ATR | Below **P4** (wave 4 low) − 0.1 × ATR |
| Target | P2 + **1.618 × wave 1** | P4 + **1.0 × wave 1** (wave 5 = wave 1) |

A pullback that is still too shallow is logged once (`W2_TOO_SHALLOW` / `W4_TOO_SHALLOW`) and checked
again if it deepens; too deep (`W2_TOO_DEEP` / `W4_TOO_DEEP`) ends that count.

Why the wave 5 stop is below wave 4 and not below wave 1's high: with the entry 1 × ATR above the wave
4 low, a stop under wave 1's high usually leaves less than 1R to a "wave 5 = wave 1" target.

## 4. Shared safety rules

- **Minimum reward 1.5R** (`RR_TOO_LOW`).
- **Stop size** at least the larger of **0.5 × H1 ATR** and **3 × the current spread**, at most
  **8 × H1 ATR** (`RISK_OUT_OF_BOUNDS`).
- **One decision per wave count**: the same wave 1 is never traded twice.
- **Time exit after 48 hours**; the usual Live Test weekend, market-closed and data-gap rules apply.
- The **market regime at entry** (Market Scout's label) is recorded on every trade for the Grade
  check. Optional filter (`regime_filter`) skips Crisis and Mean reverting (`REGIME_FILTERED`).

## 5. Agents registered

On every Live Test pair: **Elliott Wave · wave 3** and **Elliott Wave · wave 5** (2 per pair), starting
**paused**. Config `elliott` in `config.json`:

```json
"elliott": {
  "modes": ["WAVE3", "WAVE5"],
  "regime_filter_variants": [false, true],
  "symbol_overrides": {"XAUUSD": {"zz_atr": 3.0}}
}
```

Changing a setting creates a new agent version (a new id); the old one keeps its history.

## Not included (on purpose)

- **Wave C / A-B-C corrections**: telling a zigzag from a flat or a triangle needs judgement the
  rules cannot make objectively.
- Diagonals, extensions and wave-degree labelling.

## Sources

Common Elliott Wave teaching (StockCharts ChartSchool, Babypips, Elliott Wave Forecast, Elliott Wave
Insight, JournalPlus, and others) for the rules and ratios; open-source zigzag-based detectors for the
"confirmed swings only" approach. None publish independent backtests.
