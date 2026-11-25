"""Live Test acceptance tests: simulation correctness, agent isolation, pause/restart,
dedup, stale/gap, session/DST gating, CSV, and the read-only (no-executor) guarantee.

All tests run on scripted quotes/candles (a replay/mock feed); none require a live MT5
terminal. The read-only guard proves the Live Test path cannot reach the executor.
"""
import re
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as N

from dashboard_store import Store
from market_data import ReadOnlyMarketData, Bar, Quote
from live_test import (LiveTestEngine, PrevCandleBreakoutH1, CmsSupportResistanceFlip,
                       CmsFakeoutV1, CmsSupplyDemand, CmsSsr, build_agent, ensure_default_agents,
                       Simulator, MYT, in_session_first_hour, session_first_hour, swing_points,
                       compute_stats, VALID, UNCERTAIN, DATA_GAP)

POINT = 0.01
DIGITS = 2


class FakeMT5:
    """Scriptable, read-only. Exposes only data methods a market feed would."""
    TIMEFRAME_H1 = 'H1'
    TIMEFRAME_H4 = 'H4'
    TIMEFRAME_M5 = 'M5'
    TIMEFRAME_M15 = 'M15'

    def __init__(self, bars):
        self._bars = bars
        self._tick = None
        self.connected = True
        self.names = ['XAUUSD']

    def set_quote(self, bid, ask, t):
        self._tick = N(bid=bid, ask=ask, time=t)

    def terminal_info(self):
        return N(connected=self.connected)

    def symbol_select(self, name, enable=True):
        return name in self.names

    def symbol_info(self, name):
        if name not in self.names:
            return None
        return N(point=POINT, digits=DIGITS, trade_tick_size=POINT)

    def symbol_info_tick(self, name):
        return self._tick

    def symbols_get(self):
        return [N(name=n) for n in self.names]

    def copy_rates_from_pos(self, name, tf, start, count):
        return list(self._bars)


def h1_bars(now, high, low):
    """A set of closed H1 bars ending with a prev candle of given high/low."""
    base = int(now // 3600) * 3600 - 10 * 3600
    bars = []
    for i in range(8):
        t = base + i * 3600
        bars.append(dict(time=t, open=low + 1, high=high - 5, low=low + 0.5, close=high - 6))
    bars[-1].update(high=high, low=low)  # most recent CLOSED H1 = the breakout reference
    return bars


def in_session_now():
    """An epoch inside the first hour of a session whose open hour = current MYT hour,
    chosen so the FX market is open (not weekend / not NY rollover)."""
    now = 1_700_007_200.0  # 2023-11-14 ~19:13 New York (Tue, open), deterministic
    hour = datetime.fromtimestamp(now, MYT).replace(minute=20, second=0, microsecond=0)
    return hour.timestamp(), datetime.fromtimestamp(hour.timestamp(), MYT).hour


class ReadOnlyGuard(unittest.TestCase):
    # Forbidden in CODE (not docstrings/comments): order-sending or executor APIs.
    FORBIDDEN = ['order_send', 'order_check', 'TRADE_ACTION', 'broker_send', 'broker_check',
                 'positions_get', 'demo_execution', 'risk_check', 'trade_management']

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

    def test_live_test_sources_have_no_order_api(self):
        for name in ('live_test.py', 'market_data.py', 'live_test_h4_liq.py', 'live_test_crt.py',
                     'live_test_orb.py', 'live_test_h4_swing.py', 'live_test_ny_london.py',
                     'live_test_ifvg.py', 'live_test_momentum.py', 'live_test_advocate.py'):
            code = self._code_only(name)
            for tok in self.FORBIDDEN:
                self.assertNotIn(tok, code, f'{name} code must not reference {tok!r} (read-only Live Test)')

    def test_adapter_exposes_no_order_methods(self):
        api = dir(ReadOnlyMarketData)
        for bad in ('order_send', 'order_check', 'buy', 'sell', 'send', 'close_position', 'modify'):
            self.assertNotIn(bad, api)


class SessionGating(unittest.TestCase):
    def test_first_hour_window_is_dst_aware(self):
        now, hour = in_session_now()
        start, end = session_first_hour(now, hour, 'Asia/Kuala_Lumpur', 1)
        self.assertAlmostEqual(end - start, 3600, delta=1)
        self.assertTrue(in_session_first_hour(now, hour, 'Asia/Kuala_Lumpur'))
        # one hour later is outside the first-hour window
        self.assertFalse(in_session_first_hour(now + 3700, hour, 'Asia/Kuala_Lumpur'))

    def test_us_dst_boundary_shifts_utc(self):
        # A New York session: the same local open hour maps to different UTC in summer vs winter.
        jan = datetime(2026, 1, 15, 12, 0, tzinfo=MYT).timestamp()
        jul = datetime(2026, 7, 15, 12, 0, tzinfo=MYT).timestamp()
        s1, _ = session_first_hour(jan, 9, 'America/New_York')
        s2, _ = session_first_hour(jul, 9, 'America/New_York')
        # 09:00 New York is 14:00 UTC (EST) vs 13:00 UTC (EDT): offsets differ by DST.
        off1 = datetime.utcfromtimestamp(s1).hour
        off2 = datetime.utcfromtimestamp(s2).hour
        self.assertNotEqual(off1, off2)


class Simulation(unittest.TestCase):
    def _engine(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        store = Store(Path(self.tmp.name) / 'db.sqlite3')
        return LiveTestEngine(store), store

    def _market(self, now, high=2000.0, low=1998.0):
        mt5 = FakeMT5(h1_bars(now, high, low))
        return mt5, ReadOnlyMarketData(mt5, {'max_tick_age_seconds': 60})

    def test_buy_breakout_to_tp(self):
        eng, _ = self._engine()
        now, hour = in_session_now()
        mt5, md = self._market(now)
        la = eng.register(PrevCandleBreakoutH1({'session_open_hour': hour, 'tp_points': 600,
                                                'sl_buffer_points': 50, 'max_sl_points': 800}), 'XAUUSD', 'RUNNING')
        # 1) baseline below the prev high -> no entry
        mt5.set_quote(1999.0, 1999.2, now); eng.run(md, now)
        self.assertEqual(len(la.open_positions), 0)
        # 2) cross above prev high -> BUY entry at ASK
        mt5.set_quote(2000.5, 2000.7, now + 1); eng.run(md, now + 1)
        self.assertEqual(len(la.open_positions), 1)
        pos = la.open_positions[0]
        self.assertEqual(pos['side'], 'BUY')
        self.assertAlmostEqual(pos['entry_price'], 2000.7)         # fill on ASK
        self.assertAlmostEqual(pos['entry_spread'], 0.2, places=6)  # spread via bid/ask
        self.assertAlmostEqual(pos['initial_sl'], 1998.0 - 0.5)     # prev low - 50 points
        self.assertAlmostEqual(pos['initial_tp'], 2000.7 + 6.0)     # +600 points
        # 3) price reaches TP -> exit on the TP level, reason TP
        tp = pos['initial_tp']
        mt5.set_quote(tp + 0.1, tp + 0.3, now + 2); eng.run(md, now + 2)
        self.assertEqual(len(la.open_positions), 0)
        trade = eng.trades(la.id)[0]
        self.assertEqual(trade['exit_reason'], 'TP')
        self.assertAlmostEqual(trade['exit_price'], tp)
        self.assertAlmostEqual(trade['price_pnl'], 6.0)
        self.assertAlmostEqual(trade['pnl_r'], 6.0 / (2000.7 - 1997.5))
        self.assertEqual(trade['data_quality_status'], VALID)

    def test_sell_breakout_to_sl(self):
        eng, _ = self._engine()
        now, hour = in_session_now()
        mt5, md = self._market(now)
        la = eng.register(PrevCandleBreakoutH1({'session_open_hour': hour, 'tp_points': 600,
                                                'sl_buffer_points': 50, 'max_sl_points': 2000}), 'XAUUSD', 'RUNNING')
        mt5.set_quote(1998.6, 1998.8, now); eng.run(md, now)                 # baseline above prev low
        mt5.set_quote(1997.3, 1997.5, now + 1); eng.run(md, now + 1)         # cross below prev low -> SELL at BID
        pos = la.open_positions[0]
        self.assertEqual(pos['side'], 'SELL')
        self.assertAlmostEqual(pos['entry_price'], 1997.3)                   # fill on BID
        self.assertAlmostEqual(pos['initial_sl'], 2000.0 + 0.5)             # prev high + 50 points
        sl = pos['initial_sl']
        mt5.set_quote(sl - 0.1, sl + 0.1, now + 2); eng.run(md, now + 2)     # ASK >= SL -> stop
        trade = eng.trades(la.id)[0]
        self.assertEqual(trade['exit_reason'], 'SL')
        self.assertAlmostEqual(trade['exit_price'], sl)
        self.assertAlmostEqual(trade['price_pnl'], 1997.3 - sl)             # negative

    def test_no_entry_when_price_already_beyond_level_at_start(self):
        eng, _ = self._engine()
        now, hour = in_session_now()
        mt5, md = self._market(now)
        la = eng.register(PrevCandleBreakoutH1({'session_open_hour': hour}), 'XAUUSD', 'RUNNING')
        # First observed tick is ALREADY above the prev high -> must not enter (no observed crossing).
        mt5.set_quote(2005.0, 2005.2, now); eng.run(md, now)
        mt5.set_quote(2006.0, 2006.2, now + 1); eng.run(md, now + 1)
        self.assertEqual(len(la.open_positions), 0)

    def test_two_agents_same_symbol_isolated(self):
        eng, _ = self._engine()
        now, hour = in_session_now()
        mt5, md = self._market(now)
        a = eng.register(PrevCandleBreakoutH1({'session_open_hour': hour, 'tp_points': 600}), 'XAUUSD', 'RUNNING')
        b = eng.register(PrevCandleBreakoutH1({'session_open_hour': hour, 'tp_points': 300}), 'XAUUSD', 'RUNNING')
        self.assertNotEqual(a.id, b.id)
        mt5.set_quote(1999.0, 1999.2, now); eng.run(md, now)
        mt5.set_quote(2000.5, 2000.7, now + 1); eng.run(md, now + 1)
        self.assertEqual(len(a.open_positions), 1)
        self.assertEqual(len(b.open_positions), 1)
        self.assertNotEqual(a.open_positions[0]['initial_tp'], b.open_positions[0]['initial_tp'])
        self.assertNotEqual(a.open_positions[0]['trade_id'], b.open_positions[0]['trade_id'])

    def test_pause_restart_and_duplicate_tick(self):
        eng, store = self._engine()
        now, hour = in_session_now()
        mt5, md = self._market(now)
        la = eng.register(PrevCandleBreakoutH1({'session_open_hour': hour, 'tp_points': 600}), 'XAUUSD', 'RUNNING')
        mt5.set_quote(1999.0, 1999.2, now); eng.run(md, now)
        mt5.set_quote(2000.5, 2000.7, now + 1); eng.run(md, now + 1)
        self.assertEqual(len(la.open_positions), 1)
        # Pause: no new entries; a repeated identical tick does not open a second position.
        eng.set_status(la.id, 'PAUSED')
        mt5.set_quote(2001.0, 2001.2, now + 2); eng.run(md, now + 2)
        self.assertEqual(len(la.open_positions), 1)
        # Restart from storage: state + open position restored, no duplicate.
        eng2 = LiveTestEngine(store)
        la2 = eng2.agents[la.id]
        self.assertEqual(len(la2.open_positions), 1)
        self.assertEqual(la2.open_positions[0]['trade_id'], la.open_positions[0]['trade_id'])
        eng2.set_status(la.id, 'RUNNING')
        mt5.set_quote(2001.0, 2001.2, now + 3); eng2.run(md, now + 3)   # duplicate-ish tick
        self.assertEqual(len(eng2.agents[la.id].open_positions), 1)      # still one position

    def test_stale_feed_blocks_entry_and_flags_gap(self):
        eng, _ = self._engine()
        now, hour = in_session_now()
        mt5, md = self._market(now)
        la = eng.register(PrevCandleBreakoutH1({'session_open_hour': hour, 'tp_points': 600}), 'XAUUSD', 'RUNNING')
        mt5.set_quote(1999.0, 1999.2, now); eng.run(md, now)
        mt5.set_quote(2000.5, 2000.7, now + 1); eng.run(md, now + 1)
        self.assertEqual(len(la.open_positions), 1)
        # Now feed goes stale (tick timestamp far behind 'now'): no new entries; open position flagged.
        mt5.set_quote(2000.8, 2001.0, now + 1)   # tick.time stays old
        eng.run(md, now + 400)
        self.assertIn(la.feed_status, ('STALE', 'STALE_OR_CLOSED'))
        self.assertEqual(la.open_positions[0]['data_quality_status'], DATA_GAP)

    def test_candle_end_close(self):
        now, hour = in_session_now()
        _, md = self._market(now)
        agent = PrevCandleBreakoutH1({'session_open_hour': hour, 'tp_points': 600,
                                      'sl_buffer_points': 50, 'max_sl_points': 800})
        la = N(id='x', agent=agent, symbol='XAUUSD')
        q = __import__('market_data').Quote('XAUUSD', 2000.5, 2000.7, now, 0.0, POINT, DIGITS)
        sig = agent.evaluate(now, q, [Bar(now - 3600, 1999, 2000.0, 1998.0, 1999)], POINT, {'ref_price': 1999.0})
        self.assertIsNotNone(sig)
        pos = Simulator.open(la, sig, q, now)
        end = pos['entry_candle_end']
        # Not at TP/SL, but the H1 entry candle has ended -> close at the exit-basis quote.
        q2 = __import__('market_data').Quote('XAUUSD', 2001.0, 2001.2, end, 0.0, POINT, DIGITS)
        pos, closed = Simulator.monitor(pos, q2, end, 120)
        self.assertTrue(closed)
        self.assertEqual(pos['exit_reason'], 'CANDLE_END')
        self.assertAlmostEqual(pos['exit_price'], 2001.0)   # BUY exits on BID

    def test_level_hit_after_data_gap_is_uncertain_and_excluded(self):
        # A TP level is observed right after a sampling gap: the path cannot be verified,
        # so the trade is marked UNCERTAIN and excluded from valid statistics.
        pos = dict(side='BUY', entry_time=0.0, entry_price=2000.0, initial_sl=1999.0, initial_tp=2001.0,
                   risk=1.0, data_quality_status=VALID, last_sample_epoch=0.0, trade_id='t', agent_id='a')
        q = __import__('market_data').Quote('XAUUSD', 2001.5, 2001.7, 100, 0.0, POINT, DIGITS)  # TP hit
        pos, closed = Simulator.monitor(pos, q, 100, 5)   # now-last=100 > max_gap 5 -> gap
        self.assertTrue(closed)
        self.assertEqual(pos['exit_reason'], 'TP')
        self.assertEqual(pos['data_quality_status'], UNCERTAIN)
        stats = compute_stats([pos])
        self.assertEqual(stats['valid_trades'], 0)
        self.assertEqual(stats['affected_by_data'], 1)

    def test_csv_rows_stable_fields_and_iso_utc(self):
        eng, _ = self._engine()
        now, hour = in_session_now()
        mt5, md = self._market(now)
        la = eng.register(PrevCandleBreakoutH1({'session_open_hour': hour, 'tp_points': 600}), 'XAUUSD', 'RUNNING')
        mt5.set_quote(1999.0, 1999.2, now); eng.run(md, now)
        mt5.set_quote(2000.5, 2000.7, now + 1); eng.run(md, now + 1)
        tp = la.open_positions[0]['initial_tp']
        mt5.set_quote(tp + 0.1, tp + 0.3, now + 2); eng.run(md, now + 2)
        rows = eng.csv_rows(la.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0].keys()), LiveTestEngine.CSV_FIELDS)
        self.assertRegex(rows[0]['entry_time'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        # same trade_id across trades() and csv
        self.assertEqual(rows[0]['trade_id'], eng.trades(la.id)[0]['trade_id'])
        # filtered export (by agent) returns only its rows
        self.assertEqual(eng.csv_rows('nonexistent'), [])


try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo('America/New_York')
except Exception:
    NY = None


def ny_epoch(hour, minute=0):
    # 2024-06-12 is a Wednesday in EDT; chosen so the chosen hours are market-open.
    from datetime import datetime as _dt
    return _dt(2024, 6, 12, hour, minute, tzinfo=NY).timestamp()


def ny_bars(now, overrides):
    """Hourly bars for 'today' up to now. `overrides` maps an OPEN hour -> (open, close)."""
    from datetime import datetime as _dt
    d = _dt.fromtimestamp(now, NY)
    bars = []
    for h in range(8, d.hour + 1):
        t = int(_dt(d.year, d.month, d.day, h, 0, tzinfo=NY).timestamp())
        if t + 3600 > now:
            continue
        o, c = overrides.get(h, (1990.0, 1991.0))
        bars.append(dict(time=t, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c))
    return bars


@unittest.skipIf(NY is None, 'zoneinfo unavailable')
class CounterTrend(unittest.TestCase):
    def _engine(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        from live_test import LiveTestEngine
        return LiveTestEngine(Store(Path(self.tmp.name) / 'db.sqlite3'))

    def _md(self, mt5):
        return ReadOnlyMarketData(mt5, {'max_tick_age_seconds': 60})

    def test_watermark_high_mark_fades_short_to_tp(self):
        from live_test import CounterTrendScalper
        eng = self._engine(); now = ny_epoch(16)
        # The 3pm-close candle (opens 14:00) is BULLISH -> close 2000 is a HIGH water mark.
        mt5 = FakeMT5(ny_bars(now, {14: (1998.0, 2000.0)})); md = self._md(mt5)
        la = eng.register(CounterTrendScalper({'target_points': 300, 'tolerance_points': 50}), 'XAUUSD', 'RUNNING')
        mt5.set_quote(1994.9, 1995.1, now); eng.run(md, now)                 # baseline below the mark
        mt5.set_quote(1999.5, 1999.7, now + 1); eng.run(md, now + 1)         # rose to the mark -> SHORT at BID
        self.assertEqual(len(la.open_positions), 1)
        pos = la.open_positions[0]
        self.assertEqual(pos['side'], 'SELL')                                # counter-trend: fade the rise
        self.assertAlmostEqual(pos['entry_price'], 1999.5)
        self.assertAlmostEqual(pos['initial_tp'], 1999.5 - 3.0)             # 1:1, target 300 pts
        self.assertAlmostEqual(pos['initial_sl'], 1999.5 + 3.0)
        tp = pos['initial_tp']
        mt5.set_quote(tp - 0.1, tp, now + 2); eng.run(md, now + 2)           # ASK <= TP -> take profit
        trade = eng.trades(la.id)[0]
        self.assertEqual(trade['exit_reason'], 'TP')
        self.assertAlmostEqual(trade['price_pnl'], 3.0)                      # SELL profit = entry - exit

    def test_watermark_low_mark_fades_long(self):
        from live_test import CounterTrendScalper
        eng = self._engine(); now = ny_epoch(16)
        # BEARISH 3pm candle -> close 2000 is a LOW water mark.
        mt5 = FakeMT5(ny_bars(now, {14: (2002.0, 2000.0)})); md = self._md(mt5)
        la = eng.register(CounterTrendScalper({'target_points': 300, 'tolerance_points': 50}), 'XAUUSD', 'RUNNING')
        mt5.set_quote(2004.9, 2005.1, now); eng.run(md, now)                 # baseline above the mark
        mt5.set_quote(2000.4, 2000.6, now + 1); eng.run(md, now + 1)         # fell to the mark -> LONG at ASK
        pos = la.open_positions[0]
        self.assertEqual(pos['side'], 'BUY')
        self.assertAlmostEqual(pos['entry_price'], 2000.6)
        self.assertAlmostEqual(abs(pos['entry_price'] - pos['initial_sl']),
                               abs(pos['initial_tp'] - pos['entry_price']))  # 1:1 RR

    def test_midpoint_mode_long_below_mid(self):
        from live_test import CounterTrendScalper
        eng = self._engine(); now = ny_epoch(18, 30)                         # after the 17:00 bar has closed
        mt5 = FakeMT5(ny_bars(now, {17: (1999.0, 2001.0)})); md = self._md(mt5)  # 17:00 bar mid ~2000
        la = eng.register(CounterTrendScalper({'mode': 'midpoint', 'mid_hour': 17, 'session_end_hour': 19,
                                               'target_points': 300}), 'XAUUSD', 'RUNNING')
        mt5.set_quote(2000.5, 2000.7, now); eng.run(md, now)                 # baseline above mid
        mt5.set_quote(1999.4, 1999.6, now + 1); eng.run(md, now + 1)         # crossed below mid -> LONG
        self.assertEqual(la.open_positions[0]['side'], 'BUY')

    def test_no_entry_outside_offhours_window(self):
        from live_test import CounterTrendScalper
        eng = self._engine(); now = ny_epoch(11)                            # 11:00 NY -> outside 15-19 window
        mt5 = FakeMT5(ny_bars(now, {14: (1998.0, 2000.0)})); md = self._md(mt5)
        la = eng.register(CounterTrendScalper(), 'XAUUSD', 'RUNNING')
        mt5.set_quote(1994.9, 1995.1, now); eng.run(md, now)
        mt5.set_quote(1999.6, 1999.8, now + 1); eng.run(md, now + 1)
        self.assertEqual(len(la.open_positions), 0)


def cms_bars(now, lows, highs, closes):
    """Closed H1 bars from parallel low/high/close lists (open = first close-ish)."""
    base = int(now // 3600) * 3600 - (len(lows) + 2) * 3600
    return [Bar(base + i * 3600, closes[i], highs[i], lows[i], closes[i]) for i in range(len(lows))]


class CmsFlip(unittest.TestCase):
    NOW = 1_700_007_200.0

    def q(self, bid, ask):
        return Quote('XAUUSD', bid, ask, self.NOW, 0.0, POINT, DIGITS)

    def test_swing_points_detects_pivots(self):
        bars = cms_bars(self.NOW, [1995, 1994, 1993, 1992, 1991, 1990, 1992, 1993, 1991, 1988],
                        [1998, 1997, 1996, 1995, 1994, 1993, 1995, 1996, 1994, 1990],
                        [1996] * 10)
        _, lows = swing_points(bars, 2, 2)
        self.assertEqual(lows[-1][1], 1990)

    def test_sbr_sell_flip_on_retest_up(self):
        agent = CmsSupportResistanceFlip({'break_buffer_points': 80, 'sl_buffer_points': 60,
                                          'min_sl_points': 80, 'max_sl_points': 1500})
        bars = cms_bars(self.NOW, [1995, 1994, 1993, 1992, 1991, 1990, 1992, 1993, 1991, 1988],
                        [1998, 1997, 1996, 1995, 1994, 1993, 1995, 1996, 1994, 1990],
                        [1996, 1995, 1994, 1993, 1992, 1991, 1993, 1994, 1992, 1988.5])
        state = {}
        self.assertIsNone(agent.evaluate(self.NOW, self.q(1988.0, 1988.2), bars, POINT, state))  # below, baseline
        sig = agent.evaluate(self.NOW + 1, self.q(1989.9, 1990.1), bars, POINT, state)            # retest up to 1990
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'SELL')
        self.assertIn('SBR', sig['signal_reason'])
        self.assertAlmostEqual(sig['initial_sl'], 1996.6, places=2)   # protective high 1996 + 60 pts
        self.assertAlmostEqual(sig['initial_tp'], 1976.5, places=2)   # entry(bid 1989.9) - 2R
        self.assertTrue(sig['initial_tp'] < 1989.9 < sig['initial_sl'])

    def test_rbs_buy_flip_on_retest_down(self):
        agent = CmsSupportResistanceFlip()
        bars = cms_bars(self.NOW, [2002, 2003, 2004, 2005, 2006, 2007, 2005, 2004, 2006, 2010],
                        [2005, 2006, 2007, 2008, 2009, 2010, 2008, 2007, 2009, 2012],
                        [2004, 2005, 2006, 2007, 2008, 2009, 2007, 2006, 2008, 2011.5])
        state = {}
        self.assertIsNone(agent.evaluate(self.NOW, self.q(2012.0, 2012.2), bars, POINT, state))   # above, baseline
        sig = agent.evaluate(self.NOW + 1, self.q(2009.9, 2010.1), bars, POINT, state)            # retest down to 2010
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'BUY')
        self.assertIn('RBS', sig['signal_reason'])
        self.assertTrue(sig['initial_sl'] < 2010.1 < sig['initial_tp'])

    def test_no_entry_when_level_already_beyond_at_start(self):
        agent = CmsSupportResistanceFlip()
        bars = cms_bars(self.NOW, [1995, 1994, 1993, 1992, 1991, 1990, 1992, 1993, 1991, 1988],
                        [1998, 1997, 1996, 1995, 1994, 1993, 1995, 1996, 1994, 1990],
                        [1996, 1995, 1994, 1993, 1992, 1991, 1993, 1994, 1992, 1988.5])
        state = {}
        # First observed tick is already above the flipped level and stays above: no up-crossing -> no entry.
        self.assertIsNone(agent.evaluate(self.NOW, self.q(1991.0, 1991.2), bars, POINT, state))
        self.assertIsNone(agent.evaluate(self.NOW + 1, self.q(1992.0, 1992.2), bars, POINT, state))

    def test_engine_registers_and_opens_sell(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        eng = LiveTestEngine(Store(Path(tmp.name) / 'db.sqlite3'))
        now = self.NOW
        raw = cms_bars(now, [1995, 1994, 1993, 1992, 1991, 1990, 1992, 1993, 1991, 1988],
                       [1998, 1997, 1996, 1995, 1994, 1993, 1995, 1996, 1994, 1990],
                       [1996, 1995, 1994, 1993, 1992, 1991, 1993, 1994, 1992, 1988.5])
        bars = [dict(time=b.time, open=b.open, high=b.high, low=b.low, close=b.close) for b in raw]
        mt5 = FakeMT5(bars); md = ReadOnlyMarketData(mt5, {'max_tick_age_seconds': 60})
        la = eng.register(CmsSupportResistanceFlip(), 'XAUUSD', 'RUNNING')
        mt5.set_quote(1988.0, 1988.2, now); eng.run(md, now)
        self.assertEqual(len(la.open_positions), 0)
        mt5.set_quote(1989.9, 1990.1, now + 1); eng.run(md, now + 1)
        self.assertEqual(len(la.open_positions), 1)
        self.assertEqual(la.open_positions[0]['side'], 'SELL')


class CmsFakeout(unittest.TestCase):
    NOW = 1_700_007_200.0

    def q(self, bid, ask):
        return Quote('XAUUSD', bid, ask, self.NOW, 0.0, POINT, DIGITS)

    def test_sell_fakeout_pierces_and_closes_back(self):
        agent = CmsFakeoutV1({'pierce_buffer_points': 50, 'sl_buffer_points': 60,
                              'min_sl_points': 80, 'max_sl_points': 1500})
        bars = cms_bars(self.NOW,
                        [1993, 1994, 1995, 1996, 1997, 1998, 1996, 1995, 1997, 2000],
                        [1998, 1999, 2000, 2001, 2002, 2003, 2001, 2000, 2002, 2005],
                        [1996, 1997, 1998, 1999, 2000, 2001, 1999, 1998, 2000, 2001])
        # last candle high 2005 pierced swing-high 2003 but closed 2001 back below -> fade SELL
        sig = agent.evaluate(self.NOW, self.q(2001.0, 2001.2), bars, POINT, {})
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'SELL')
        self.assertIn('Fakeout V1', sig['signal_reason'])
        self.assertAlmostEqual(sig['initial_sl'], 2005.6, places=2)   # fakeout high + 60 pts
        self.assertTrue(sig['initial_tp'] < 2001.0 < sig['initial_sl'])

    def test_no_fakeout_when_closes_beyond_level(self):
        agent = CmsFakeoutV1()
        # same pierce but the candle CLOSES above the level (a real breakout) -> no fade
        bars = cms_bars(self.NOW,
                        [1993, 1994, 1995, 1996, 1997, 1998, 1996, 1995, 1997, 2000],
                        [1998, 1999, 2000, 2001, 2002, 2003, 2001, 2000, 2002, 2005],
                        [1996, 1997, 1998, 1999, 2000, 2001, 1999, 1998, 2000, 2004])
        self.assertIsNone(agent.evaluate(self.NOW, self.q(2004.0, 2004.2), bars, POINT, {}))


class CmsSupplyDemandTests(unittest.TestCase):
    NOW = 1_700_007_200.0

    def q(self, bid, ask):
        return Quote('XAUUSD', bid, ask, self.NOW, 0.0, POINT, DIGITS)

    def _bars(self):
        base = int(self.NOW // 3600) * 3600 - 12 * 3600
        b = []
        for i in range(6):
            b.append(Bar(base + i * 3600, 1990 + i, 1991 + i, 1989 + i, 1990 + i))  # filler, wide range
        b.append(Bar(base + 6 * 3600, 2000.5, 2001.0, 2000.0, 2000.5))  # base
        b.append(Bar(base + 7 * 3600, 2000.4, 2001.0, 2000.0, 2000.6))  # base
        b.append(Bar(base + 8 * 3600, 2000.5, 2001.0, 2000.0, 2000.5))  # base
        b.append(Bar(base + 9 * 3600, 2001.0, 2005.0, 2000.9, 2004.0))  # bullish impulse -> demand zone 2000-2001
        return b

    def test_demand_zone_buy_on_return(self):
        agent = CmsSupplyDemand({'impulse_body_points': 150, 'base_range_points': 120,
                                 'sl_buffer_points': 60, 'min_sl_points': 80, 'max_sl_points': 2000})
        bars = self._bars()
        state = {}
        self.assertIsNone(agent.evaluate(self.NOW, self.q(2003.0, 2003.2), bars, POINT, state))   # above zone, baseline
        sig = agent.evaluate(self.NOW + 1, self.q(2000.9, 2001.1), bars, POINT, state)            # returns to zone top
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'BUY')
        self.assertIn('Demand', sig['signal_reason'])
        self.assertAlmostEqual(sig['initial_sl'], 1999.4, places=2)   # zone low 2000 - 60 pts
        self.assertTrue(sig['initial_sl'] < 2001.1 < sig['initial_tp'])


class CmsSsrTests(unittest.TestCase):
    NOW = 1_700_007_200.0

    def q(self, bid, ask):
        return Quote('XAUUSD', bid, ask, self.NOW, 0.0, POINT, DIGITS)

    def test_significant_resistance_fades_short(self):
        agent = CmsSsr({'cluster_points': 120, 'sl_buffer_points': 80,
                        'min_sl_points': 80, 'max_sl_points': 1200})
        highs = [2005, 2008, 2007, 2010, 2007, 2008, 2007, 2010.5, 2007, 2008]
        bars = cms_bars(self.NOW, [h - 10 for h in highs], highs, [h - 2 for h in highs])
        state = {}
        self.assertIsNone(agent.evaluate(self.NOW, self.q(2008.0, 2008.2), bars, POINT, state))    # below, baseline
        sig = agent.evaluate(self.NOW + 1, self.q(2010.2, 2010.4), bars, POINT, state)             # rises into ~2010.25
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'SELL')
        self.assertIn('SSR', sig['signal_reason'])
        self.assertTrue(sig['initial_tp'] < 2010.2 < sig['initial_sl'])


class CmsRegistration(unittest.TestCase):
    def test_build_agent_and_defaults(self):
        for sid in ('CMS_SR_FLIP', 'CMS_FAKEOUT_V1', 'CMS_SUPPLY_DEMAND', 'CMS_SSR'):
            self.assertIsNotNone(build_agent(sid, {}))
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        eng = LiveTestEngine(Store(Path(tmp.name) / 'db.sqlite3'))
        ensure_default_agents(eng, {})
        sids = {la.agent.strategy_id for la in eng.agents.values()}
        for sid in ('CMS_SR_FLIP', 'CMS_FAKEOUT_V1', 'CMS_SUPPLY_DEMAND', 'CMS_SSR'):
            self.assertIn(sid, sids)
        self.assertTrue(all(la.status == 'PAUSED' for la in eng.agents.values()))

    def test_default_agents_cover_all_configured_pairs(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        eng = LiveTestEngine(Store(Path(tmp.name) / 'db.sqlite3'))
        ensure_default_agents(eng, {'symbols': ['XAUUSD', 'EURUSD', 'GBPJPY']})
        pairs = {la.base_symbol for la in eng.agents.values()}
        self.assertEqual(pairs, {'XAUUSD', 'EURUSD', 'GBPJPY'})
        # six core (H1) agents + five Live Test structure agents on each pair
        # (ORB is XAUUSD-only). H4 Liquidity Sweep is the M5 baseline on every pair.
        for base in ('XAUUSD', 'EURUSD', 'GBPJPY'):
            core = {la.agent.strategy_id for la in eng.agents.values()
                    if la.base_symbol == base and la.agent.strategy_id
                    not in ('ORB_BREAKOUT_RETEST', 'H4_LIQ_SWEEP_FVG', 'CRT_H1_M5',
                            'H4_SWING_SWEEP_FVG_ENGULF', 'NY_SWEEP_LONDON', 'INVERSE_FVG_RETEST',
                            'MOMENTUM_M5_M1', 'ADVOCATE_EMA_PULLBACK')}
            self.assertEqual(len(core), 6)
            per_pair = {la.agent.strategy_id for la in eng.agents.values()
                        if la.base_symbol == base}
            for sid in ('H4_LIQ_SWEEP_FVG', 'CRT_H1_M5', 'H4_SWING_SWEEP_FVG_ENGULF',
                        'NY_SWEEP_LONDON', 'INVERSE_FVG_RETEST'):
                self.assertIn(sid, per_pair)
        # ORB and the two ported gold desks are XAUUSD-only
        for sid in ('ORB_BREAKOUT_RETEST', 'MOMENTUM_M5_M1', 'ADVOCATE_EMA_PULLBACK'):
            bases = {la.base_symbol for la in eng.agents.values()
                     if la.agent.strategy_id == sid}
            self.assertEqual(bases, {'XAUUSD'}, sid)


class AtrSizing(unittest.TestCase):
    NOW = 1_700_007_200.0

    def test_atr_true_range(self):
        from live_test import atr
        bars = [Bar(0, 10, 12, 8, 11), Bar(3600, 11, 15, 10, 14), Bar(7200, 14, 16, 13, 15)]
        # TR1 = max(15-10, |15-11|, |10-11|) = 5 ; TR2 = max(16-13, |16-14|, |13-14|) = 3 ; ATR = 4
        self.assertAlmostEqual(atr(bars, 14), 4.0)
        self.assertIsNone(atr([Bar(0, 1, 1, 1, 1)]))   # < 2 bars

    def test_dist_points_override_else_atr(self):
        from live_test import dist
        self.assertAlmostEqual(dist(50, 0.1, 4.0, 0.01), 0.5)    # explicit points win (50 * 0.01)
        self.assertAlmostEqual(dist(None, 0.1, 4.0, 0.01), 0.4)  # else ATR-based (0.1 * 4.0)
        self.assertIsNone(dist(None, 0.1, None, 0.01))           # neither available

    def test_fakeout_sl_buffer_is_atr_based_by_default(self):
        from live_test import atr
        agent = CmsFakeoutV1()   # ATR defaults, no point overrides
        bars = cms_bars(self.NOW,
                        [1993, 1994, 1995, 1996, 1997, 1998, 1996, 1995, 1997, 2000],
                        [1998, 1999, 2000, 2001, 2002, 2003, 2001, 2000, 2002, 2005],
                        [1996, 1997, 1998, 1999, 2000, 2001, 1999, 1998, 2000, 2001])
        q = Quote('XAUUSD', 2001.0, 2001.2, self.NOW, 0.0, POINT, DIGITS)
        sig = agent.evaluate(self.NOW, q, bars, POINT, {})
        self.assertIsNotNone(sig)
        a = atr(bars, 14)
        self.assertGreater(a, 0)
        # SL sits sl_buffer_atr (0.15) * ATR above the fakeout high (2005) -> scales per instrument
        self.assertAlmostEqual(sig['initial_sl'] - 2005.0, 0.15 * a, places=2)


if __name__ == '__main__':
    unittest.main()
