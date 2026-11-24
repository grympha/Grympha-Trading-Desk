"""Acceptance tests for the H4 Swing Sweep + M15 FVG + Engulfing Live Test agent."""
import unittest

from market_data import Bar
from live_test_h4_swing import H4SwingSweepFVGEngulf
from lt_testkit import POINT, q

H4_DUR, M15_DUR = 14400, 900
BASE = 1_700_000_000


def h4_fixture():
    """Confirmed un-broken H4 swing LOW 1990 and swing HIGH 2020 (TP target)."""
    spec = [(2015, 2005), (2018, 2008), (2020, 2010), (2012, 2000), (2005, 1990),
            (2008, 1995), (2010, 1998), (2012, 2000), (2014, 2002)]
    base = BASE - 30 * H4_DUR
    return [Bar(int(base + i * H4_DUR), (h + l) / 2, h, l, (h + l) / 2) for i, (h, l) in enumerate(spec)]


# M15 OHLC: sweep of 1990 (i3), bullish FVG i4/i5/i6 (gap 1993-1996), engulf at i8.
M15 = [
    Bar(BASE + 0 * M15_DUR, 1995, 1996, 1993, 1994),
    Bar(BASE + 1 * M15_DUR, 1994, 1995, 1992, 1993),
    Bar(BASE + 2 * M15_DUR, 1993, 1994, 1991, 1992),
    Bar(BASE + 3 * M15_DUR, 1991, 1992.5, 1989, 1992),     # sweep: low 1989 < 1990, close back above
    Bar(BASE + 4 * M15_DUR, 1992, 1993, 1991.5, 1992.5),   # c1 (high 1993)
    Bar(BASE + 5 * M15_DUR, 1993, 1997, 1992.5, 1996.5),   # c2 displacement
    Bar(BASE + 6 * M15_DUR, 1996.5, 1998, 1996, 1997),     # c3 (low 1996) -> FVG [1993,1996]
    Bar(BASE + 7 * M15_DUR, 1997, 1998, 1994.5, 1995),     # bearish candle touching the gap
    Bar(BASE + 8 * M15_DUR, 1994.5, 1998, 1994, 1997.5),   # bullish engulfing -> entry
]


class FakeH4:
    def __init__(self, h4):
        self._h4 = h4

    def candles(self, symbol, tf, count, now=None):
        assert tf == 'H4'
        return list(self._h4)

    def resolve_symbol(self, b):
        return b


class DirectAgent(unittest.TestCase):
    def _a(self, cfg=None):
        return H4SwingSweepFVGEngulf(cfg or {})

    def _to_fvg(self, a, st, mkt):
        now = BASE
        a.evaluate(now, q(1992, 1992.2, now), M15[:3], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(now + 1, q(1992, 1992.2, now + 1), M15[:4], POINT, st, market=mkt, symbol='XAUUSD')  # sweep
        self.assertEqual(st['pending']['phase'], 'SWEPT')
        a.evaluate(now + 2, q(1992.5, 1992.7, now + 2), M15[:5], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(now + 3, q(1996.0, 1996.2, now + 3), M15[:6], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(now + 4, q(1997.0, 1997.2, now + 4), M15[:7], POINT, st, market=mkt, symbol='XAUUSD')  # FVG
        self.assertEqual(st['pending']['phase'], 'FVG')
        self.assertAlmostEqual(st['pending']['fvg_midpoint'], 1994.5)

    def test_full_buy_setup_reaches_fill(self):
        a = self._a()
        mkt = FakeH4(h4_fixture())
        st = {}
        self._to_fvg(a, st, mkt)
        self.assertAlmostEqual(st['pending']['sweep_extreme'], 1989.0)
        self.assertAlmostEqual(st['pending']['target_level'], 2020.0)
        # bearish candle (no engulf yet) -> no fill
        self.assertIsNone(a.evaluate(BASE + 5, q(1995.0, 1995.2, BASE + 5), M15[:8], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertEqual(st['pending']['phase'], 'FVG')
        # bullish engulfing candle closes -> fill on Ask
        sig = a.evaluate(BASE + 6, q(1997.5, 1997.7, BASE + 6), M15[:9], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'BUY')
        self.assertAlmostEqual(sig['extra']['fill_price'], 1997.7)
        self.assertAlmostEqual(sig['initial_tp'], 2020.0)
        self.assertTrue(sig['initial_sl'] < 1993.0)       # beyond the gap back boundary
        self.assertIsNone(st['pending'])

    def test_sweep_rebroken_cancels(self):
        a = self._a()
        mkt = FakeH4(h4_fixture())
        st = {}
        self._to_fvg(a, st, mkt)
        # price collapses below the sweep extreme -> invalidate
        self.assertIsNone(a.evaluate(BASE + 5, q(1988.0, 1988.2, BASE + 5), M15[:8], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertIsNone(st['pending'])
        self.assertTrue(any(e['reason'] == 'SWEEP_REBROKEN' for e in st.get('_emit', [])))

    def test_target_hit_before_fill_no_fill(self):
        a = self._a()
        mkt = FakeH4(h4_fixture())
        st = {}
        self._to_fvg(a, st, mkt)
        self.assertIsNone(a.evaluate(BASE + 5, q(2020.1, 2020.3, BASE + 5), M15[:8], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertIsNone(st['pending'])
        self.assertTrue(any(e['reason'] == 'TARGET_HIT_NO_FILL' for e in st.get('_emit', [])))

    def test_fvg_requires_c1_after_sweep(self):
        a = self._a()
        # a gap whose c1 opens before the sweep candle closes must be ignored
        f = a._find_fvg(M15, after_time=M15[6].time, side='BUY')
        self.assertTrue(f is None or f['c1_time'] >= M15[6].time + M15_DUR)


if __name__ == '__main__':
    unittest.main()
