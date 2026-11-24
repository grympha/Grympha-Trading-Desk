"""Acceptance tests for the H4 Liquidity Sweep + FVG Live Test agent.

Deterministic replay only — no live MT5 terminal. H4 structure (equal highs/lows) and a
lower-timeframe (M5) sweep + FVG are scripted; tick-level Bid/Ask quotes drive the fill,
SL/TP, R and the NO_FILL / cancellation signal log. A guard proves the agent is read-only
and can never reach the executor.
"""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as N

from dashboard_store import Store
from market_data import ReadOnlyMarketData, Bar, Quote
from live_test import LiveTestEngine, build_agent, ensure_default_agents, compute_stats, VALID, DATA_GAP
from live_test_h4_liq import H4LiquiditySweepFVG, _eq_pair

POINT = 0.01
DIGITS = 2
NOW = 1_700_000_000.0            # Tue 2023-11-14, FX open (deterministic)
H4_DUR = 14400
M5_DUR = 300

# Fixed-tolerance config so EQ detection does not depend on ATR; tight entry windows off.
BUY_CFG = dict(entry_timeframe='M5', eq_tolerance_points=50, h4_pivot_left=2, h4_pivot_right=2,
               ltf_pivot_left=2, ltf_pivot_right=2, sl_buffer_points=20,
               min_sl_points=10, max_sl_points=2000,
               max_sweep_to_fvg_bars=6, max_fvg_to_entry_bars=12)


def q(bid, ask, t=NOW):
    return Quote('XAUUSD', bid, ask, t, 0.0, POINT, DIGITS)


# --------------------------------------------------------------------------- H4 fixtures
def h4_eqh(level=2010.0):
    """12 closed H4 bars: equal HIGHS at `level` (idx2 & idx8), below is unremarkable, and the
    level is un-touched after confirmation. One pivot low only -> no EQL pair (BUY bias only)."""
    H = [2004, 2005, level, 2005, 2004, 2003, 2004, 2005, level, 2005, 2004, 2003]
    L = [1988, 1986, 1991, 1984, 1982, 1980.5, 1979, 1985, 1992, 1983, 1981, 1976]
    base = NOW - H4_DUR * 20
    return [Bar(base + i * H4_DUR, (H[i] + L[i]) / 2, H[i], L[i], (H[i] + L[i]) / 2) for i in range(12)]


def h4_eql(level=1990.0):
    """Mirror: equal LOWS at `level` (idx2 & idx8), un-touched after -> SELL bias only."""
    L = [1996, 1995, level, 1995, 1996, 1997, 1996, 1995, level, 1995, 1996, 1997]
    H = [2012, 2014, 2009, 2016, 2018, 2020, 2021, 2015, 2008, 2017, 2019, 2024]
    base = NOW - H4_DUR * 20
    return [Bar(base + i * H4_DUR, (H[i] + L[i]) / 2, H[i], L[i], (H[i] + L[i]) / 2) for i in range(12)]


def h4_both(eqh=2010.0, eql=1990.0):
    """Equal highs above AND equal lows below the ~2000 price -> AMBIGUOUS_BIAS."""
    H = [2004, 2005, eqh, 2005, 2004, 2003, 2004, 2005, eqh, 2005, 2004, 2003]
    L = [1996, 1995, eql, 1995, 1996, 1997, 1996, 1995, eql, 1995, 1996, 1997]
    base = NOW - H4_DUR * 20
    return [Bar(base + i * H4_DUR, (H[i] + L[i]) / 2, H[i], L[i], (H[i] + L[i]) / 2) for i in range(12)]


# --------------------------------------------------------------------------- M5 (LTF) fixtures
def m5_buy():
    """12 closed M5 bars. Swing low 1998 at idx2; sweep candle idx7 (low 1997, close 1999 back
    above 1998); bullish FVG idx8/9/10 (low[idx10]=2002 > high[idx8]=2001 -> midpoint 2001.5)."""
    O = [2000, 2000, 1999.5, 1999.5, 2000, 2000.5, 2000.5, 2000.0, 2000.0, 2001, 2003.0, 2003.5]
    H = [2001, 2001, 2000.0, 2000.5, 2001, 2001.0, 2001.0, 1999.5, 2001.0, 2003.5, 2004, 2004.0]
    L = [1999, 1999, 1998.0, 1999.0, 1999.5, 2000, 2000.0, 1997.0, 2000.0, 2000.9, 2002, 2003.0]
    C = [2000, 2000, 1999.5, 2000.0, 2000.5, 2000.5, 2000.5, 1999.0, 2000.8, 2003, 2003.5, 2003.5]
    base = int(NOW)
    return [Bar(base + i * M5_DUR, O[i], H[i], L[i], C[i]) for i in range(12)]


def m5_sell():
    """Mirror for SELL: swing high 2002 at idx2; sweep idx7 (high 2003, close 2001 back below);
    bearish FVG idx8/9/10 (high[idx10]=1998 < low[idx8]=1999 -> midpoint 1998.5)."""
    O = [2000, 2000, 2000.5, 2000.5, 2000, 1999.5, 1999.5, 2000.0, 2000.0, 1999, 1997.0, 1996.5]
    H = [2001, 2001, 2002.0, 2001.0, 2000.5, 2000, 2000.0, 2003.0, 2000.0, 1999.1, 1998, 1997.0]
    L = [1999, 1999, 2000.0, 1999.5, 1999, 1999.0, 1999.0, 2000.5, 1999.0, 1996.5, 1996, 1996.0]
    C = [2000, 2000, 2000.5, 2000.0, 1999.5, 1999.5, 1999.5, 2001.0, 1999.2, 1997, 1996.5, 1996.5]
    base = int(NOW)
    return [Bar(base + i * M5_DUR, O[i], H[i], L[i], C[i]) for i in range(12)]


class FakeH4Market:
    """Returns the scripted H4 bars for the agent's own H4 fetch (direct-agent tests)."""
    def __init__(self, h4):
        self._h4 = h4

    def candles(self, symbol, tf, count, now=None):
        assert tf == 'H4', tf
        return list(self._h4)


# =========================================================================== direct-agent tests
class DirectAgent(unittest.TestCase):
    def _agent(self, cfg=None):
        return H4LiquiditySweepFVG({**BUY_CFG, **(cfg or {})})

    def test_eq_pair_picks_most_recent_within_tolerance(self):
        pivots = [(0, 2010.0, 10), (1, 1990.0, 20), (2, 2010.2, 30)]
        pair = _eq_pair(pivots, 0.5)
        self.assertEqual((pair[0][1], pair[1][1]), (2010.0, 2010.2))

    def test_eligible_targets_buy_only(self):
        a = self._agent()
        buy, sell = a._eligible_targets(h4_eqh(), ref=2000.0, point=POINT)
        self.assertIsNotNone(buy); self.assertIsNone(sell)
        self.assertEqual(buy['type'], 'EQH')
        self.assertAlmostEqual(buy['target_price'], 2010.0)

    def test_eligible_targets_touched_level_not_eligible(self):
        a = self._agent()
        # Price already above the EQH -> not eligible (liquidity would be behind us).
        buy, _ = a._eligible_targets(h4_eqh(), ref=2011.0, point=POINT)
        self.assertIsNone(buy)

    def test_full_buy_setup_reaches_fill_signal(self):
        a = self._agent()
        mkt = FakeH4Market(h4_eqh())
        bars = m5_buy()
        st = {}
        # SCAN: newest = idx6, no sweep yet
        self.assertIsNone(a.evaluate(NOW, q(2000.0, 2000.2), bars[:7], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertNotIn('pending', [k for k in st if st.get('pending')])
        # SWEEP on idx7 -> pending SWEPT
        self.assertIsNone(a.evaluate(NOW + 1, q(1999.4, 1999.6), bars[:8], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertEqual(st['pending']['phase'], 'SWEPT')
        self.assertAlmostEqual(st['pending']['sweep_extreme'], 1997.0)
        self.assertEqual(st['pending']['liq_type'], 'EQH')
        self.assertAlmostEqual(st['pending']['target_price'], 2010.0)
        # idx8, idx9 close: still waiting for FVG; price rallies above the gap
        a.evaluate(NOW + 2, q(2000.4, 2000.6), bars[:9], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 3, q(2002.4, 2002.6), bars[:10], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['pending']['phase'], 'SWEPT')
        # idx10 closes -> bullish FVG confirmed, midpoint 2001.5, price still above -> no fill
        self.assertIsNone(a.evaluate(NOW + 4, q(2002.9, 2003.1), bars[:11], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertEqual(st['pending']['phase'], 'FVG')
        self.assertAlmostEqual(st['pending']['fvg_midpoint'], 2001.5)
        # retrace: Ask reaches the midpoint -> fill signal
        sig = a.evaluate(NOW + 5, q(2001.4, 2001.5), bars[:11], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'BUY')
        self.assertAlmostEqual(sig['initial_sl'], 1996.8)      # sweep low 1997 - 20 pts
        self.assertAlmostEqual(sig['initial_tp'], 2010.0)      # EQH target
        self.assertEqual(sig['extra']['h4_liquidity_type'], 'EQH')
        self.assertAlmostEqual(sig['extra']['fill_price'], 2001.5)
        self.assertIsNone(st['pending'])                       # setup consumed

    def test_full_sell_setup_reaches_fill_signal(self):
        a = self._agent()
        mkt = FakeH4Market(h4_eql())
        bars = m5_sell()
        st = {}
        a.evaluate(NOW, q(2000.0, 2000.2), bars[:7], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 1, q(2000.4, 2000.6), bars[:8], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['pending']['phase'], 'SWEPT')
        self.assertEqual(st['pending']['side'], 'SELL')
        self.assertAlmostEqual(st['pending']['sweep_extreme'], 2003.0)
        a.evaluate(NOW + 2, q(1999.4, 1999.6), bars[:9], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 3, q(1997.4, 1997.6), bars[:10], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNone(a.evaluate(NOW + 4, q(1996.9, 1997.1), bars[:11], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertEqual(st['pending']['phase'], 'FVG')
        self.assertAlmostEqual(st['pending']['fvg_midpoint'], 1998.5)
        sig = a.evaluate(NOW + 5, q(1998.5, 1998.6), bars[:11], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'SELL')
        self.assertAlmostEqual(sig['initial_sl'], 2003.2)      # sweep high 2003 + 20 pts
        self.assertAlmostEqual(sig['initial_tp'], 1990.0)      # EQL target
        self.assertTrue(sig['initial_tp'] < 1998.6 < sig['initial_sl'])

    def test_ambiguous_bias_skips_and_logs(self):
        a = self._agent()
        mkt = FakeH4Market(h4_both())
        st = {}
        self.assertIsNone(a.evaluate(NOW, q(2000.0, 2000.2), m5_buy()[:8], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertIsNone(st.get('pending'))
        emits = st.get('_emit', [])
        self.assertTrue(any(e['event'] == 'SKIP' and e['reason'] == 'AMBIGUOUS_BIAS' for e in emits))

    def test_fvg_before_sweep_is_ignored(self):
        # An FVG whose first candle opens before the sweep candle closes must not be taken.
        a = self._agent()
        bars = m5_buy()
        # sweep candle is idx7 (time base+7*M5); an FVG at idx2/3/4 (before it) is invalid
        f = a._find_fvg(bars, sweep_time=bars[7].time, side='BUY')
        self.assertTrue(f is None or f['c1_time'] >= bars[7].time + M5_DUR)

    def test_fvg_needs_third_candle_closed(self):
        a = self._agent()
        bars = m5_buy()
        # Only up to idx9 closed -> the idx8/9/10 FVG's third candle is not closed yet.
        self.assertIsNone(a._find_fvg(bars[:10], sweep_time=bars[7].time, side='BUY'))
        self.assertIsNotNone(a._find_fvg(bars[:11], sweep_time=bars[7].time, side='BUY'))

    def test_no_fill_in_swept_phase_before_fvg_confirms(self):
        # Price sitting at the eventual midpoint during SWEPT (no FVG yet) must NOT fill.
        a = self._agent()
        mkt = FakeH4Market(h4_eqh())
        bars = m5_buy()
        st = {}
        a.evaluate(NOW, q(2000.0, 2000.2), bars[:7], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 1, q(1999.4, 1999.6), bars[:8], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['pending']['phase'], 'SWEPT')
        sig = a.evaluate(NOW + 2, q(2001.4, 2001.5), bars[:9], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNone(sig)                                  # no fill while still SWEPT
        self.assertEqual(st['pending']['phase'], 'SWEPT')

    def test_target_hit_before_entry_records_no_fill(self):
        a = self._agent()
        mkt = FakeH4Market(h4_eqh())
        bars = m5_buy()
        st = {}
        a.evaluate(NOW, q(2000.0, 2000.2), bars[:7], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 1, q(1999.4, 1999.6), bars[:8], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 2, q(2002.4, 2002.6), bars[:10], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 3, q(2002.9, 2003.1), bars[:11], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['pending']['phase'], 'FVG')
        # price runs straight to the EQH target (2010) without retracing -> NO_FILL
        sig = a.evaluate(NOW + 4, q(2010.2, 2010.4), bars[:11], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNone(sig)
        self.assertIsNone(st['pending'])
        self.assertTrue(any(e['event'] == 'NO_FILL' and e['reason'] == 'TARGET_HIT_NO_RETEST'
                            for e in st.get('_emit', [])))

    def test_sweep_extreme_rebroken_cancels(self):
        a = self._agent()
        mkt = FakeH4Market(h4_eqh())
        bars = m5_buy()
        st = {}
        a.evaluate(NOW, q(2000.0, 2000.2), bars[:7], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 1, q(1999.4, 1999.6), bars[:8], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['pending']['phase'], 'SWEPT')
        # price collapses back below the sweep low (1997) -> invalidate
        self.assertIsNone(a.evaluate(NOW + 2, q(1996.4, 1996.6), bars[:9], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertIsNone(st['pending'])
        self.assertTrue(any(e['event'] == 'CANCELLED' and e['reason'] == 'SWEEP_REBROKEN'
                            for e in st.get('_emit', [])))

    def test_spread_prevents_fill_even_when_mid_touches(self):
        a = self._agent()
        mkt = FakeH4Market(h4_eqh())
        bars = m5_buy()
        st = {}
        a.evaluate(NOW, q(2000.0, 2000.2), bars[:7], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 1, q(1999.4, 1999.6), bars[:8], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 2, q(2002.4, 2002.6), bars[:10], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 3, q(2002.9, 2003.1), bars[:11], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['pending']['phase'], 'FVG')     # midpoint 2001.5
        # mid = 2001.5 touches the midpoint on the chart, but ASK (2001.9) has not -> NO fill
        sig = a.evaluate(NOW + 4, q(2001.1, 2001.9), bars[:11], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNone(sig)
        self.assertEqual(st['pending']['phase'], 'FVG')     # still waiting
        # Ask finally reaches the midpoint -> fill
        sig = a.evaluate(NOW + 5, q(2001.3, 2001.5), bars[:11], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNotNone(sig)

    def test_reclaim_50_waits_for_closed_reclaim_candle(self):
        # RECLAIM_50: a tick at the midpoint must NOT fill; only a CLOSED candle that wicks the
        # midpoint and closes back above it triggers the entry (filled on the quote after close).
        a = self._agent({'entry_model': 'RECLAIM_50'})
        mkt = FakeH4Market(h4_eqh())
        bars = m5_buy()
        st = {}
        a.evaluate(NOW, q(2000.0, 2000.2), bars[:7], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 1, q(1999.4, 1999.6), bars[:8], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 2, q(2002.4, 2002.6), bars[:10], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(NOW + 3, q(2002.9, 2003.1), bars[:11], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['pending']['phase'], 'FVG')          # midpoint 2001.5
        # a mere tick to the midpoint does NOT fill under RECLAIM_50
        self.assertIsNone(a.evaluate(NOW + 4, q(2001.4, 2001.5), bars[:11], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertEqual(st['pending']['phase'], 'FVG')
        # a CLOSED candle that wicks below the midpoint and closes back above -> confirmed reclaim
        reclaim = bars[:11] + [Bar(int(NOW) + 11 * M5_DUR, 2002.0, 2002.5, 2001.0, 2002.0)]
        sig = a.evaluate(NOW + 5, q(2001.9, 2002.1), reclaim, POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'BUY')
        self.assertAlmostEqual(sig['extra']['fill_price'], 2002.1)   # filled on the Ask after close
        self.assertEqual(sig['extra']['setup'], 'EQH')
        self.assertIn('RECLAIM_50', sig['signal_reason'])
        self.assertAlmostEqual(sig['initial_sl'], 1996.8)
        self.assertAlmostEqual(sig['initial_tp'], 2010.0)

    def test_signal_id_stable_for_same_setup(self):
        a = self._agent()
        one = a._signal_id('XAUUSD', 'EQH', 2010.004, 1700000000, DIGITS)
        two = a._signal_id('XAUUSD', 'EQH', 2010.001, 1700000000, DIGITS)   # same rounded level
        self.assertEqual(one, two)
        self.assertNotEqual(one, a._signal_id('EURUSD', 'EQH', 2010.0, 1700000000, DIGITS))


# =========================================================================== engine integration
class MultiTFMT5:
    """Scriptable read-only MT5 returning different bars per timeframe. One symbol by default;
    `names` controls which symbols resolve (to test offline / missing-data symbols)."""
    TIMEFRAME_H4 = 'H4'
    TIMEFRAME_M5 = 'M5'
    TIMEFRAME_M15 = 'M15'
    TIMEFRAME_M1 = 'M1'

    def __init__(self, h4, m5, names=('XAUUSD',)):
        self._bars = {'H4': h4, 'M5': m5}
        self._tick = None
        self.names = list(names)

    def set_quote(self, bid, ask, t):
        self._tick = N(bid=bid, ask=ask, time=t)

    def terminal_info(self):
        return N(connected=True)

    def symbol_select(self, name, enable=True):
        return name in self.names

    def symbol_info(self, name):
        return N(point=POINT, digits=DIGITS, trade_tick_size=POINT) if name in self.names else None

    def symbol_info_tick(self, name):
        return self._tick

    def symbols_get(self):
        return [N(name=n) for n in self.names]

    def copy_rates_from_pos(self, name, tf, start, count):
        return [dict(time=b.time, open=b.open, high=b.high, low=b.low, close=b.close)
                for b in self._bars.get(tf, [])]


class EngineIntegration(unittest.TestCase):
    def _engine(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        return LiveTestEngine(self.store)

    def _md(self, mt5):
        return ReadOnlyMarketData(mt5, {'max_tick_age_seconds': 60})

    def _t(self, idx):
        """A `now` at which M5 bars[0..idx] are closed (newest = idx)."""
        return int(NOW) + (idx + 1) * M5_DUR + 1

    def test_full_buy_lifecycle_through_engine_to_tp(self):
        eng = self._engine()
        mt5 = MultiTFMT5(h4_eqh(), m5_buy()); md = self._md(mt5)
        la = eng.register(build_agent('H4_LIQ_SWEEP_FVG', BUY_CFG), 'XAUUSD', 'RUNNING')
        steps = [(6, 2000.0, 2000.2), (7, 1999.4, 1999.6), (8, 2000.4, 2000.6),
                 (9, 2002.4, 2002.6), (10, 2002.9, 2003.1)]
        for idx, bid, ask in steps:
            t = self._t(idx); mt5.set_quote(bid, ask, t); eng.run(md, t)
        self.assertEqual(len(la.open_positions), 0)            # not filled yet (price above midpoint)
        # retrace tick (same newest candle idx10): Ask hits midpoint 2001.5 -> fill at ASK
        t = self._t(10) + 2; mt5.set_quote(2001.4, 2001.5, t); eng.run(md, t)
        self.assertEqual(len(la.open_positions), 1)
        pos = la.open_positions[0]
        self.assertEqual(pos['side'], 'BUY')
        self.assertAlmostEqual(pos['entry_price'], 2001.5)     # fill on ASK at the midpoint
        self.assertAlmostEqual(pos['initial_sl'], 1996.8)
        self.assertAlmostEqual(pos['initial_tp'], 2010.0)
        self.assertAlmostEqual(pos['fill_price'], 2001.5)
        self.assertEqual(pos['h4_liquidity_type'], 'EQH')
        self.assertEqual(pos['signal_id'], la.open_positions[0]['signal_id'])
        # price reaches TP on the next tick (close sample, no data gap) -> exit TP,
        # R = (2010-2001.5)/(2001.5-1996.8)
        t = self._t(10) + 4; mt5.set_quote(2010.1, 2010.3, t); eng.run(md, t)
        trade = eng.trades(la.id)[0]
        self.assertEqual(trade['exit_reason'], 'TP')
        self.assertAlmostEqual(trade['exit_price'], 2010.0)
        self.assertAlmostEqual(trade['price_pnl'], 8.5)
        self.assertAlmostEqual(trade['pnl_r'], 8.5 / 4.7, places=4)
        self.assertEqual(trade['data_quality_status'], VALID)
        # same trade_id across trades() and CSV; H4 columns populated
        rows = eng.csv_rows(la.id)
        self.assertEqual(rows[0]['trade_id'], trade['trade_id'])
        self.assertEqual(rows[0]['h4_liquidity_type'], 'EQH')
        self.assertEqual(rows[0]['signal_id'], trade['signal_id'])
        self.assertTrue(rows[0]['sweep_time'].endswith('Z'))

    def test_data_gap_flags_open_position_and_excludes_it(self):
        eng = self._engine()
        mt5 = MultiTFMT5(h4_eqh(), m5_buy()); md = self._md(mt5)
        la = eng.register(build_agent('H4_LIQ_SWEEP_FVG', BUY_CFG), 'XAUUSD', 'RUNNING')
        for idx, bid, ask in [(6, 2000.0, 2000.2), (7, 1999.4, 1999.6), (8, 2000.4, 2000.6),
                              (9, 2002.4, 2002.6), (10, 2002.9, 2003.1)]:
            t = self._t(idx); mt5.set_quote(bid, ask, t); eng.run(md, t)
        t = self._t(10) + 2; mt5.set_quote(2001.4, 2001.5, t); eng.run(md, t)
        self.assertEqual(len(la.open_positions), 1)
        # Feed goes stale: tick timestamp stops advancing while `now` jumps forward.
        mt5.set_quote(2001.6, 2001.8, t); eng.run(md, t + 500)
        self.assertIn(la.feed_status, ('STALE', 'STALE_OR_CLOSED'))
        self.assertEqual(la.open_positions[0]['data_quality_status'], DATA_GAP)

    def test_no_fill_logged_in_signal_table(self):
        eng = self._engine()
        mt5 = MultiTFMT5(h4_eqh(), m5_buy()); md = self._md(mt5)
        la = eng.register(build_agent('H4_LIQ_SWEEP_FVG', BUY_CFG), 'XAUUSD', 'RUNNING')
        for idx, bid, ask in [(6, 2000.0, 2000.2), (7, 1999.4, 1999.6), (8, 2002.4, 2002.6),
                              (10, 2002.9, 2003.1)]:
            t = self._t(idx); mt5.set_quote(bid, ask, t); eng.run(md, t)
        # straight to target without retrace -> NO_FILL into the signal log
        t = self._t(10) + 2; mt5.set_quote(2010.2, 2010.4, t); eng.run(md, t)
        self.assertEqual(len(la.open_positions), 0)
        sigs = eng.signals(la.id)
        self.assertTrue(any(s['event'] == 'NO_FILL' and s['reason'] == 'TARGET_HIT_NO_RETEST' for s in sigs))
        # NO_FILL is exportable with stable columns
        rows = eng.signals_csv_rows(la.id)
        self.assertEqual(list(rows[0].keys()), LiveTestEngine.SIGNAL_CSV_FIELDS)
        # per-symbol roll-up counts the NO_FILL
        by = {d['symbol']: d for d in eng.report()['by_symbol']}
        self.assertGreaterEqual(by['XAUUSD']['no_fill'], 1)

    def test_restart_keeps_pending_setup_no_duplicate(self):
        eng = self._engine()
        mt5 = MultiTFMT5(h4_eqh(), m5_buy()); md = self._md(mt5)
        la = eng.register(build_agent('H4_LIQ_SWEEP_FVG', BUY_CFG), 'XAUUSD', 'RUNNING')
        for idx, bid, ask in [(6, 2000.0, 2000.2), (7, 1999.4, 1999.6)]:
            t = self._t(idx); mt5.set_quote(bid, ask, t); eng.run(md, t)
        self.assertEqual(la.state['pending']['phase'], 'SWEPT')
        # Reload engine from storage: the pending setup survives.
        eng2 = LiveTestEngine(self.store)
        self.assertEqual(eng2.agents[la.id].state['pending']['phase'], 'SWEPT')
        # Continue to a fill on the reloaded engine -> exactly one position, no duplicate.
        for idx, bid, ask in [(8, 2000.4, 2000.6), (9, 2002.4, 2002.6), (10, 2002.9, 2003.1)]:
            t = self._t(idx); mt5.set_quote(bid, ask, t); eng2.run(md, t)
        t = self._t(10) + 2; mt5.set_quote(2001.4, 2001.5, t); eng2.run(md, t)
        self.assertEqual(len(eng2.agents[la.id].open_positions), 1)

    def test_twelve_symbols_registered_and_one_offline_does_not_block(self):
        eng = self._engine()
        symbols = ['XAUUSD', 'GBPJPY', 'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD',
                   'USDCAD', 'USDCHF', 'NZDUSD', 'BTCUSD', 'ETHUSD', 'XAGUSD']
        ensure_default_agents(eng, {'symbols': symbols})
        h4_agents = {la.base_symbol for la in eng.agents.values()
                     if la.agent.strategy_id == 'H4_LIQ_SWEEP_FVG'}
        self.assertEqual(h4_agents, set(symbols))              # all 12 present
        # Start all H4 agents; one symbol (XAGUSD) has no feed -> must not block XAUUSD.
        for la in list(eng.agents.values()):
            if la.agent.strategy_id == 'H4_LIQ_SWEEP_FVG':
                eng.set_status(la.id, 'RUNNING')
        mt5 = MultiTFMT5(h4_eqh(), m5_buy(), names=['XAUUSD', 'GBPJPY'])  # others offline
        md = self._md(mt5)
        t = self._t(7); mt5.set_quote(1999.4, 1999.6, t)
        touched = eng.run(md, t)                              # runs every RUNNING agent
        self.assertEqual(touched, 12)
        feeds = {la.base_symbol: la.feed_status for la in eng.agents.values()
                 if la.agent.strategy_id == 'H4_LIQ_SWEEP_FVG'}
        self.assertIn(feeds['XAUUSD'], ('LIVE', 'STALE'))     # resolved + processed
        self.assertEqual(feeds['XAGUSD'], 'SYMBOL_UNAVAILABLE')  # offline, flagged, did not throw

    def test_optional_m15_timeframe_registers_separately(self):
        eng = self._engine()
        ensure_default_agents(eng, {'symbols': ['XAUUSD'], 'h4_liq': {'timeframes': ['M5', 'M15']}})
        tfs = sorted(la.agent.timeframe for la in eng.agents.values()
                     if la.agent.strategy_id == 'H4_LIQ_SWEEP_FVG')
        self.assertEqual(tfs, ['M15', 'M5'])
        ids = {la.id for la in eng.agents.values() if la.agent.strategy_id == 'H4_LIQ_SWEEP_FVG'}
        self.assertEqual(len(ids), 2)                         # separate state per timeframe


# =========================================================================== read-only guard
class ReadOnlyGuard(unittest.TestCase):
    FORBIDDEN = ['order_send', 'order_check', 'TRADE_ACTION', 'positions_get',
                 'demo_execution', 'risk_check', 'trade_management']

    @staticmethod
    def _code_only(path):
        import tokenize
        names = []
        with tokenize.open(path) as fh:
            for tok in tokenize.generate_tokens(fh.readline):
                if tok.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                if tok.type in (tokenize.NAME, tokenize.OP):
                    names.append(tok.string)
        return ' '.join(names)

    def test_agent_source_has_no_order_api(self):
        code = self._code_only('live_test_h4_liq.py')
        for tok in self.FORBIDDEN:
            self.assertNotIn(tok, code, f'live_test_h4_liq.py must not reference {tok!r}')

    def test_agent_has_no_executor_methods(self):
        for bad in ('order_send', 'order_check', 'buy', 'sell', 'close_position', 'modify'):
            self.assertNotIn(bad, dir(H4LiquiditySweepFVG))


if __name__ == '__main__':
    unittest.main()
