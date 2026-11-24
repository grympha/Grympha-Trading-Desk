"""Acceptance tests for the New York Sweep London Live Test agent (deterministic replay).

Summer date (2024-06-12): London 08:00-12:00 BST = 07:00-11:00 UTC; the NY window
08:00-11:00 EDT = 12:00-15:00 UTC, so the London range freezes before NY opens.
"""
import unittest
from datetime import datetime, timezone

from market_data import Bar
from live_test_ny_london import NYSweepLondon
from lt_testkit import POINT, q

M5_DUR = 300


def utc(h, mi):
    return datetime(2024, 6, 12, h, mi, tzinfo=timezone.utc).timestamp()


# London 07:00-11:00 UTC: range high 2010, low 1985.
LONDON = [
    Bar(int(utc(7, 0)), 2005, 2010, 2000, 2008),
    Bar(int(utc(8, 30)), 2008, 2009, 1985, 1990),
    Bar(int(utc(10, 55)), 1995, 2005, 1994, 2004),
]
# NY session M5 (12:00 UTC on): confirmed swing low 2002 (n2), high sweep (n5), CHoCH (n7).
NY = [
    Bar(int(utc(12, 0)), 2005, 2008, 2003, 2006),
    Bar(int(utc(12, 5)), 2006, 2009, 2004, 2005),
    Bar(int(utc(12, 10)), 2005, 2007, 2002, 2004),     # swing low 2002 (CHoCH reference)
    Bar(int(utc(12, 15)), 2004, 2006, 2003, 2005),
    Bar(int(utc(12, 20)), 2005, 2008, 2004, 2007),
    Bar(int(utc(12, 25)), 2007, 2010.5, 2006, 2008),   # sweeps London high 2010, closes inside
    Bar(int(utc(12, 30)), 2008, 2009, 2003, 2004),
    Bar(int(utc(12, 35)), 2004, 2005, 2000, 2001),     # CHoCH: close 2001 < 2002
]
ALL = LONDON + NY


class FakeM5:
    def __init__(self, bars):
        self._bars = sorted(bars, key=lambda b: b.time)

    def candles(self, symbol, tf, count, now=None):
        assert tf == 'M5'
        return [b for b in self._bars if b.time + M5_DUR <= now]

    def resolve_symbol(self, b):
        return b


class DirectAgent(unittest.TestCase):
    def _a(self, cfg=None):
        return NYSweepLondon({'sl_buffer_points': 1, **(cfg or {})})

    def test_sell_setup_sweep_then_choch_fills(self):
        a = self._a()
        mkt = FakeM5(ALL)
        st = {}
        t_sweep = int(utc(12, 30)) + 1                 # n5 closed -> sweep detected
        self.assertIsNone(a.evaluate(t_sweep, q(2008, 2008.2, t_sweep), [], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertEqual(st['pending']['side'], 'SELL')
        self.assertAlmostEqual(st['pending']['sweep_extreme'], 2010.5)
        self.assertAlmostEqual(st['pending']['ref_pivot'], 2002.0)
        self.assertAlmostEqual(st['pending']['target_level'], 1985.0)
        # waiting candle n6 (no CHoCH yet)
        t6 = int(utc(12, 35)) + 1
        self.assertIsNone(a.evaluate(t6, q(2004, 2004.2, t6), [], POINT, st, market=mkt, symbol='XAUUSD'))
        # n7 closes below the reference swing low -> CHoCH -> fill on Bid
        t7 = int(utc(12, 40)) + 1
        sig = a.evaluate(t7, q(2001.0, 2001.2, t7), [], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'SELL')
        self.assertAlmostEqual(sig['extra']['fill_price'], 2001.0)
        self.assertAlmostEqual(sig['initial_tp'], 1985.0)
        self.assertAlmostEqual(sig['initial_sl'], 2010.51)
        self.assertTrue(sig['initial_tp'] < 2001.0 < sig['initial_sl'])

    def test_closed_back_outside_cancels(self):
        a = self._a()
        mkt = FakeM5(ALL)
        st = {}
        t_sweep = int(utc(12, 30)) + 1
        a.evaluate(t_sweep, q(2008, 2008.2, t_sweep), [], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['pending']['side'], 'SELL')
        # price closes back above the London high -> invalidate
        t = int(utc(12, 35)) + 1
        self.assertIsNone(a.evaluate(t, q(2011.0, 2011.2, t), [], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertIsNone(st['pending'])
        self.assertTrue(any(e['reason'] == 'CLOSED_BACK_OUTSIDE' for e in st.get('_emit', [])))

    def test_target_hit_before_fill(self):
        a = self._a()
        mkt = FakeM5(ALL)
        st = {}
        t_sweep = int(utc(12, 30)) + 1
        a.evaluate(t_sweep, q(2008, 2008.2, t_sweep), [], POINT, st, market=mkt, symbol='XAUUSD')
        t = int(utc(12, 35)) + 1
        self.assertIsNone(a.evaluate(t, q(1984.0, 1984.2, t), [], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertTrue(any(e['reason'] == 'TARGET_HIT_NO_FILL' for e in st.get('_emit', [])))

    def test_no_setup_before_london_range_frozen(self):
        a = self._a()
        mkt = FakeM5(ALL)
        st = {}
        early = int(utc(10, 0))        # London window not finished yet
        self.assertIsNone(a.evaluate(early, q(2005, 2005.2, early), [], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertIsNone(st.get('pending'))


if __name__ == '__main__':
    unittest.main()
