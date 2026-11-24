"""Acceptance tests for the Gold ORB 09:30 New York Live Test agent (deterministic replay).

SELL baseline runs; BUY stays disabled. Deterministic NY-session fixtures: a 09:30 M15
opening range, an H4 bearish bias, an M5 break + retest, and an M15 demand target.
"""
import unittest
from datetime import datetime

from market_data import Bar
from live_test_orb import ORBAgent
from lt_testkit import POINT, q

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo('America/New_York')
except Exception:  # pragma: no cover
    NY = None

H4_DUR, M15_DUR, M5_DUR = 14400, 900, 300


def ny_epoch(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=NY).timestamp()


T = ny_epoch(2024, 6, 12, 9, 30)      # 09:30 EDT opening


def h4_bearish():
    """Declining swing highs (2060,2050,2035) and lows (2025,2012,2000) -> bearish bias."""
    spec = [(2050, 2040), (2055, 2045), (2060, 2050), (2048, 2038), (2035, 2025), (2045, 2030),
            (2050, 2040), (2038, 2028), (2022, 2012), (2030, 2018), (2035, 2025), (2025, 2015),
            (2010, 2000), (2015, 2005), (2020, 2010)]
    base = T - 20 * H4_DUR
    return [Bar(int(base + i * H4_DUR), (h + l) / 2, h, l, (h + l) / 2) for i, (h, l) in enumerate(spec)]


def m15_series():
    """Confirmed M15 swing low at 1984 (demand target), then the 09:30 opening range 2000-2010."""
    pre = [(1991, 1990), (1990, 1988), (1987, 1984), (1989, 1987), (1994, 1992), (2000, 1998)]
    bars = [Bar(int(T - (6 - i) * M15_DUR), (h + l) / 2, h, l, (h + l) / 2) for i, (h, l) in enumerate(pre)]
    bars.append(Bar(int(T), 2005, 2010, 2000, 2005))            # 09:30 opening M15 range
    bars.append(Bar(int(T + M15_DUR), 2002, 2006, 1999, 2001))
    return bars


M5 = [
    Bar(int(T + 900), 2004, 2006, 2003, 2005),
    Bar(int(T + 1200), 2003, 2005, 2002, 2004),
    Bar(int(T + 1500), 2002, 2003, 1997, 1998),    # breakout: close 1998 < range low 2000
    Bar(int(T + 1800), 1999, 2000.5, 1997, 1998),  # retest: high 2000.5 >= 2000, close back below
    Bar(int(T + 2100), 1998, 1999, 1996, 1997),
]


class FakeHM:
    """Read-only market returning scripted H4/M15 bars, closed-only like the real adapter."""
    _DUR = {'H4': H4_DUR, 'M15': M15_DUR, 'M5': M5_DUR}

    def __init__(self, h4, m15):
        self._b = {'H4': h4, 'M15': m15}

    def candles(self, symbol, tf, count, now=None):
        dur = self._DUR[tf]
        return [b for b in self._b.get(tf, []) if b.time + dur <= now]

    def resolve_symbol(self, b):
        return b


class ORBDirect(unittest.TestCase):
    def _a(self, cfg=None):
        return ORBAgent({'sl_buffer_points': 1, **(cfg or {})})

    def _run_to_entry(self, a, st, mkt):
        # 1) freeze range + bias at 09:46
        self.assertIsNone(a.evaluate(T + 960, q(2005, 2005.2, T + 960), M5[:2], POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertEqual(st['phase'], 'WAIT_BREAK')
        self.assertAlmostEqual(st['pending']['range_low'], 2000.0)
        # 2) breakout M5 close below range low
        a.evaluate(T + 1501, q(1998, 1998.2, T + 1501), M5[:3], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['phase'], 'WAIT_RETEST')
        # 3) retest candle closes
        a.evaluate(T + 1801, q(1998, 1998.2, T + 1801), M5[:4], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['phase'], 'ARMED')

    def test_sell_setup_runs_through_to_fill(self):
        a = self._a()
        mkt = FakeHM(h4_bearish(), m15_series())
        st = {}
        self._run_to_entry(a, st, mkt)
        sig = a.evaluate(T + 1802, q(1998.0, 1998.2, T + 1802), M5[:5], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'SELL')
        self.assertAlmostEqual(sig['extra']['fill_price'], 1998.0)       # SELL fills Bid
        self.assertAlmostEqual(sig['initial_sl'], 2010.01)              # above range high + 1 tick
        self.assertAlmostEqual(sig['initial_tp'], 1984.0)              # nearest M15 demand below entry
        self.assertTrue(sig['initial_tp'] < 1998.0 < sig['initial_sl'])

    def test_buy_stays_disabled_on_baseline(self):
        a = self._a()
        mkt = FakeHM(h4_bearish(), m15_series())
        # Even with a bullish break the SELL-only baseline never emits a BUY.
        st = {}
        for now, c in [(T + 960, M5[:2]), (T + 1501, M5[:3]), (T + 1801, M5[:4]), (T + 1802, M5[:5])]:
            sig = a.evaluate(now, q(2005, 2005.2, now), c, POINT, st, market=mkt, symbol='XAUUSD')
            self.assertFalse(sig and sig.get('side') == 'BUY')

    def test_bias_not_bearish_skips(self):
        a = self._a()
        flat = [Bar(int(T - 20 * H4_DUR + i * H4_DUR), 2000, 2005, 1995, 2000) for i in range(15)]
        st = {}
        a.evaluate(T + 960, q(2005, 2005.2, T + 960), M5[:2], POINT, st, market=FakeHM(flat, m15_series()), symbol='XAUUSD')
        self.assertNotEqual(st.get('phase'), 'WAIT_BREAK')
        self.assertTrue(any(e['reason'] == 'H4_BIAS_NOT_BEARISH' for e in st.get('_emit', [])))

    def test_retest_failure_closes_back_inside(self):
        a = self._a()
        mkt = FakeHM(h4_bearish(), m15_series())
        st = {}
        a.evaluate(T + 960, q(2005, 2005.2, T + 960), M5[:2], POINT, st, market=mkt, symbol='XAUUSD')
        a.evaluate(T + 1501, q(1998, 1998.2, T + 1501), M5[:3], POINT, st, market=mkt, symbol='XAUUSD')
        # next M5 closes back above the range low -> setup fails
        back_inside = M5[:3] + [Bar(int(T + 1800), 1998, 2003, 1997, 2002)]
        a.evaluate(T + 1801, q(2002, 2002.2, T + 1801), back_inside, POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['phase'], 'DONE')
        self.assertTrue(any(e['reason'] == 'CLOSED_BACK_INSIDE' for e in st.get('_emit', [])))

    def test_no_entry_after_1100_cutoff(self):
        a = self._a()
        mkt = FakeHM(h4_bearish(), m15_series())
        st = {}
        a.evaluate(T + 960, q(2005, 2005.2, T + 960), M5[:2], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['phase'], 'WAIT_BREAK')
        cutoff = ny_epoch(2024, 6, 12, 11, 1)        # past 11:00 NY
        a.evaluate(cutoff, q(1998, 1998.2, cutoff), M5[:3], POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['phase'], 'DONE')
        self.assertTrue(any(e['reason'] == 'ENTRY_CUTOFF_1100' for e in st.get('_emit', [])))


if __name__ == '__main__':
    unittest.main()
