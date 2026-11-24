"""Acceptance tests for the Inverse FVG Retest Live Test agent (deterministic replay)."""
import unittest

from market_data import Bar
from live_test_ifvg import InverseFvgRetest
from lt_testkit import POINT, q

M5_DUR, M15_DUR = 300, 900
BASE = 1_700_000_000


# M5: bearish FVG i1/i2/i3 (gap 1996-1998), flip candle i4 closes above the whole gap -> BUY iFVG.
M5 = [
    Bar(BASE + 0 * M5_DUR, 2000, 2001, 1999, 2000),
    Bar(BASE + 1 * M5_DUR, 2000, 2000, 1998, 1998.5),      # c1 (low 1998)
    Bar(BASE + 2 * M5_DUR, 1998, 1998, 1994, 1995),        # c2 displacement down
    Bar(BASE + 3 * M5_DUR, 1995, 1996, 1993, 1994),        # c3 (high 1996) -> bearish FVG [1996,1998]
    Bar(BASE + 4 * M5_DUR, 1995, 1999, 1994, 1998.5),      # flip: close 1998.5 > whole gap
    Bar(BASE + 5 * M5_DUR, 1997, 1997.5, 1995, 1995.5),    # (used for invalidation test)
]


def m15_fixture():
    """Confirmed un-touched M15 swing high 2010 (BUY target above the zone)."""
    spec = [(2004, 1998), (2006, 2000), (2010, 2003), (2008, 2004), (2007, 2003)]
    return [Bar(int(BASE - (5 - i) * M15_DUR), (h + l) / 2, h, l, (h + l) / 2)
            for i, (h, l) in enumerate(spec)]


class FakeM15:
    def __init__(self, m15):
        self._m15 = m15

    def candles(self, symbol, tf, count, now=None):
        assert tf == 'M15'
        return list(self._m15)

    def resolve_symbol(self, b):
        return b


class DirectAgent(unittest.TestCase):
    def _a(self, cfg=None):
        return InverseFvgRetest({'sl_buffer_points': 1, **(cfg or {})})

    def _arm(self, a, st, mkt):
        a.evaluate(BASE + 100, q(1998.5, 1998.7, BASE + 100), M5[:4], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(BASE + 101, q(1998.5, 1998.7, BASE + 101), M5[:5], POINT, st, market=mkt, symbol='XAUUSD')

    def test_flip_then_retest_fills(self):
        a = self._a()
        mkt = FakeM15(m15_fixture())
        st = {}
        self._arm(a, st, mkt)
        self.assertIsNotNone(st.get('pending'))
        self.assertEqual(st['pending']['side'], 'BUY')
        self.assertAlmostEqual(st['pending']['upper'], 1998.0)
        self.assertAlmostEqual(st['pending']['lower'], 1996.0)
        self.assertAlmostEqual(st['pending']['target_level'], 2010.0)
        # retest: Ask re-enters the zone through the upper edge -> fill on Ask
        sig = a.evaluate(BASE + 102, q(1997.8, 1998.0, BASE + 102), M5[:5], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'BUY')
        self.assertAlmostEqual(sig['extra']['fill_price'], 1998.0)
        self.assertAlmostEqual(sig['initial_tp'], 2010.0)
        self.assertAlmostEqual(sig['initial_sl'], 1995.99)

    def test_jumped_past_zone_no_fill(self):
        a = self._a()
        mkt = FakeM15(m15_fixture())
        st = {}
        self._arm(a, st, mkt)
        sig = a.evaluate(BASE + 102, q(1994.0, 1994.2, BASE + 102), M5[:5], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNone(sig)
        self.assertTrue(any(e['reason'] == 'JUMPED_PAST_ZONE' for e in st.get('_emit', [])))

    def test_flip_invalidated_by_close_back_through(self):
        a = self._a()
        mkt = FakeM15(m15_fixture())
        st = {}
        self._arm(a, st, mkt)
        # a new M5 closes clean back below the whole zone -> flip invalidated
        inval = M5[:5] + [Bar(BASE + 6 * M5_DUR, 1996, 1997, 1993, 1994)]
        sig = a.evaluate(BASE + 103, q(1994.0, 1994.2, BASE + 103), inval, POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNone(sig)
        self.assertTrue(any(e['reason'] in ('FLIP_INVALIDATED', 'JUMPED_PAST_ZONE') for e in st.get('_emit', [])))

    def test_sweep_required_variant_skips_without_prior_sweep(self):
        a = self._a({'variant': 'SWEEP_REQUIRED'})
        mkt = FakeM15(m15_fixture())
        st = {}
        self._arm(a, st, mkt)
        self.assertIsNone(st.get('pending'))
        self.assertTrue(any(e['reason'] == 'NO_PRIOR_SWEEP' for e in st.get('_emit', [])))

    def test_min_width_rejects_thin_gap(self):
        a = self._a({'min_width_ticks': 1000})      # require an impossibly wide gap
        mkt = FakeM15(m15_fixture())
        st = {}
        self._arm(a, st, mkt)
        self.assertIsNone(st.get('pending'))


if __name__ == '__main__':
    unittest.main()
