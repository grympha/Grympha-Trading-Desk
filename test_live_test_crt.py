"""Acceptance tests for the CRT H1/M5 Live Test agent (deterministic replay)."""
import unittest

from market_data import Bar
from live_test import build_agent, ensure_default_agents, VALID
from live_test_crt import CrtH1M5
from lt_testkit import POINT, DIGITS, q, MultiTFMT5, new_engine, md, bars

H1_DUR, M5_DUR = 3600, 300
T0 = 1_700_000_000
CFG = dict(pierce_ticks=1, sl_buffer_points=1, min_sl_points=None, max_sl_points=None,
           min_sl_atr=0.01, max_sl_atr=50.0)


def h1_series(hour_n):
    """[prev-ref (1990-2010), hour_n]."""
    ref = Bar(T0, 2000, 2010, 1990, 2000)
    return [ref, hour_n]


def m5_flat(base):
    return bars([(1998, 1999, 1997, 1998)] * 6, base, M5_DUR)


class FakeH1:
    def __init__(self, h1):
        self._h1 = h1

    def candles(self, symbol, tf, count, now=None):
        assert tf == 'H1'
        return list(self._h1)

    def resolve_symbol(self, b):
        return b


class DirectAgent(unittest.TestCase):
    def _a(self, cfg=None):
        return CrtH1M5({**CFG, **(cfg or {})})

    def _detect_now(self):
        return T0 + 2 * H1_DUR + 1          # just after hour_n closes

    def test_buy_fade_of_low_sweep_fills(self):
        a = self._a()
        hour_n = Bar(T0 + H1_DUR, 1995, 1996, 1989.5, 1991)     # swept 1990 low, closed inside
        mkt = FakeH1(h1_series(hour_n))
        m5 = m5_flat(T0 + 2 * H1_DUR - 5 * M5_DUR)
        st = {}
        now = self._detect_now()
        self.assertIsNone(a.evaluate(now, q(1998, 1998.2, now), m5, POINT, st, market=mkt, symbol='XAUUSD'))
        self.assertEqual(st['pending']['side'], 'BUY')
        self.assertAlmostEqual(st['pending']['sweep_extreme'], 1989.5)
        self.assertAlmostEqual(st['pending']['target_level'], 2010.0)
        sig = a.evaluate(now + 1, q(1991.0, 1991.2, now + 1), m5, POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'BUY')
        self.assertAlmostEqual(sig['extra']['fill_price'], 1991.2)      # BUY fills Ask
        self.assertAlmostEqual(sig['initial_sl'], 1989.49)
        self.assertAlmostEqual(sig['initial_tp'], 2010.0)
        self.assertIsNone(st['pending'])

    def test_sell_fade_of_high_sweep_fills(self):
        a = self._a()
        hour_n = Bar(T0 + H1_DUR, 2005, 2010.5, 2004, 2008)     # swept 2010 high, closed inside
        mkt = FakeH1(h1_series(hour_n))
        m5 = m5_flat(T0 + 2 * H1_DUR - 5 * M5_DUR)
        st = {}
        now = self._detect_now()
        a.evaluate(now, q(2007, 2007.2, now), m5, POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(st['pending']['side'], 'SELL')
        self.assertAlmostEqual(st['pending']['sweep_extreme'], 2010.5)
        sig = a.evaluate(now + 1, q(2008.0, 2008.2, now + 1), m5, POINT, st, market=mkt, symbol='XAUUSD')
        self.assertEqual(sig['side'], 'SELL')
        self.assertAlmostEqual(sig['extra']['fill_price'], 2008.0)      # SELL fills Bid
        self.assertAlmostEqual(sig['initial_sl'], 2010.51)
        self.assertAlmostEqual(sig['initial_tp'], 1990.0)

    def test_double_sweep_skips(self):
        a = self._a()
        hour_n = Bar(T0 + H1_DUR, 2000, 2010.5, 1989.5, 2000)   # swept BOTH edges
        st = {}
        now = self._detect_now()
        a.evaluate(now, q(2000, 2000.2, now), m5_flat(T0), POINT, st, market=FakeH1(h1_series(hour_n)), symbol='XAUUSD')
        self.assertIsNone(st.get('pending'))
        self.assertTrue(any(e['event'] == 'SKIP' and e['reason'] == 'DOUBLE_SWEEP' for e in st.get('_emit', [])))

    def test_close_outside_range_is_not_a_setup(self):
        a = self._a()
        hour_n = Bar(T0 + H1_DUR, 1995, 1996, 1985, 1988)       # swept low but CLOSED below range
        st = {}
        now = self._detect_now()
        a.evaluate(now, q(1988, 1988.2, now), m5_flat(T0), POINT, st, market=FakeH1(h1_series(hour_n)), symbol='XAUUSD')
        self.assertIsNone(st.get('pending'))

    def test_rr_too_low_rejected(self):
        a = self._a()
        hour_n = Bar(T0 + H1_DUR, 1995, 1996, 1980, 1998)       # deep sweep -> SL far, RR < 1
        mkt = FakeH1(h1_series(hour_n))
        m5 = m5_flat(T0)
        st = {}
        now = self._detect_now()
        a.evaluate(now, q(1998, 1998.2, now), m5, POINT, st, market=mkt, symbol='XAUUSD')
        sig = a.evaluate(now + 1, q(1998.0, 1998.2, now + 1), m5, POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNone(sig)
        self.assertTrue(any(e['reason'] == 'RR_TOO_LOW' for e in st.get('_emit', [])))

    def test_no_retrospective_entry_on_stale_close(self):
        a = self._a()
        hour_n = Bar(T0 + H1_DUR, 1995, 1996, 1989.5, 1991)
        st = {}
        stale = T0 + 2 * H1_DUR + 600          # 600 s after close -> outside the 60 s window
        a.evaluate(stale, q(1991, 1991.2, stale), m5_flat(T0), POINT, st, market=FakeH1(h1_series(hour_n)), symbol='XAUUSD')
        self.assertIsNone(st.get('pending'))

    def test_target_before_entry_no_fill(self):
        a = self._a()
        hour_n = Bar(T0 + H1_DUR, 1995, 1996, 1989.5, 1991)
        mkt = FakeH1(h1_series(hour_n))
        m5 = m5_flat(T0)
        st = {}
        now = self._detect_now()
        a.evaluate(now, q(1998, 1998.2, now), m5, POINT, st, market=mkt, symbol='XAUUSD')
        # price jumps to the opposite edge (target) before we enter -> TARGET_BEFORE_ENTRY
        sig = a.evaluate(now + 1, q(2010.1, 2010.3, now + 1), m5, POINT, st, market=mkt, symbol='XAUUSD')
        self.assertIsNone(sig)
        self.assertTrue(any(e['reason'] == 'TARGET_BEFORE_ENTRY' for e in st.get('_emit', [])))


class EngineIntegration(unittest.TestCase):
    def _t_detect(self):
        return T0 + 2 * H1_DUR + 1

    def test_lifecycle_fill_then_tp(self):
        eng, store = new_engine(self)
        hour_n = Bar(T0 + H1_DUR, 1995, 1996, 1989.5, 1991)
        m5 = m5_flat(T0 + 2 * H1_DUR - 5 * M5_DUR)
        mt5 = MultiTFMT5({'H1': h1_series(hour_n), 'M5': m5})
        market = md(mt5)
        la = eng.register(build_agent('CRT_H1_M5', CFG), 'XAUUSD', 'RUNNING')
        now = self._t_detect()
        mt5.set_quote(1998, 1998.2, now); eng.run(market, now)          # arm
        self.assertEqual(len(la.open_positions), 0)
        mt5.set_quote(1991.0, 1991.2, now + 1); eng.run(market, now + 1)  # fill
        self.assertEqual(len(la.open_positions), 1)
        pos = la.open_positions[0]
        self.assertEqual(pos['side'], 'BUY')
        self.assertAlmostEqual(pos['entry_price'], 1991.2)
        # price runs to TP (BUY exits on Bid)
        mt5.set_quote(2010.1, 2010.3, now + 3); eng.run(market, now + 3)
        trade = eng.trades(la.id)[0]
        self.assertEqual(trade['exit_reason'], 'TP')
        self.assertAlmostEqual(trade['exit_price'], 2010.0)
        self.assertEqual(trade['data_quality_status'], VALID)
        self.assertEqual(eng.csv_rows(la.id)[0]['setup'], 'CRT')

    def test_twelve_symbols_registered_one_offline_isolated(self):
        eng, store = new_engine(self)
        symbols = ['XAUUSD', 'GBPJPY', 'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD',
                   'USDCAD', 'USDCHF', 'NZDUSD', 'BTCUSD', 'ETHUSD', 'XAGUSD']
        ensure_default_agents(eng, {'symbols': symbols})
        crt = {la.base_symbol for la in eng.agents.values() if la.agent.strategy_id == 'CRT_H1_M5'}
        self.assertEqual(crt, set(symbols))
        for la in list(eng.agents.values()):
            if la.agent.strategy_id == 'CRT_H1_M5':
                eng.set_status(la.id, 'RUNNING')
        hour_n = Bar(T0 + H1_DUR, 1995, 1996, 1989.5, 1991)
        mt5 = MultiTFMT5({'H1': h1_series(hour_n), 'M5': m5_flat(T0)}, names=['XAUUSD', 'GBPJPY'])
        now = self._t_detect(); mt5.set_quote(1998, 1998.2, now)
        touched = eng.run(md(mt5), now)
        self.assertEqual(touched, 12)
        feeds = {la.base_symbol: la.feed_status for la in eng.agents.values()
                 if la.agent.strategy_id == 'CRT_H1_M5'}
        self.assertIn(feeds['XAUUSD'], ('LIVE', 'STALE'))
        self.assertEqual(feeds['XAGUSD'], 'SYMBOL_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
