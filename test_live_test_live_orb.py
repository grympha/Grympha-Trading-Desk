"""Live ORB Live Test agent: the REAL orb_core.scan on a scripted XAUUSD session, then the live
plan conversion (fixed 2R), the one-signal-per-session limit and live-style management."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dashboard_store import Store
from live_test import LiveTestEngine, ensure_default_agents
from live_test_live_orb import LiveORB
from lt_testkit import MultiTFMT5
from market_data import Bar, ReadOnlyMarketData

# Monday 2026-11-23, broker server = UTC+2. Range 01:00-06:00 server = 23:00-04:00 UTC,
# trading from 07:00 server = 05:00 UTC.
DAY0 = datetime(2026, 11, 22, 22, 0, tzinfo=timezone.utc).timestamp()   # 00:00 server time Monday
START, END, TRADE = DAY0 + 3600, DAY0 + 6 * 3600, DAY0 + 7 * 3600
HI, LO = 2010.0, 1990.0


def session_bars(retest_low=2010.5, retest_close=2011.5):
    m5, t = [], START - 700 * 300
    for i in range(700):                                   # steady rise into the session (EMA200 below)
        p = 1900 + i * 95 / 700
        m5.append(Bar(t, p, p + 1, p - 1, p + 0.5)); t += 300
    for i in range(int((END - START) / 300)):              # the opening range 1990-2010
        p = 2000 + (8 if i % 2 else -8)
        hi = HI if i == 10 else p + 1
        lo = LO if i == 20 else p - 1
        m5.append(Bar(t, p, hi, lo, p)); t += 300
    while t < TRADE:                                       # quiet hour inside the range
        m5.append(Bar(t, 2003, 2004, 2002, 2003.5)); t += 300
    m5.append(Bar(t, 2005, 2013, 2004, 2012)); t += 300    # breakout close above 2010 + 3%
    m5.append(Bar(t, 2012, 2013, retest_low, retest_close))   # retest that holds
    h4 = [Bar(DAY0 + 7200 - (99 - i) * 14400, 1800 + i * 2, 1802 + i * 2, 1799 + i * 2, 1801 + i * 2)
          for i in range(100)]                             # H4 uptrend, last closes 04:00 UTC
    return {'M5': m5, 'H4': h4}


class RealScan(unittest.TestCase):
    def setUp(self):
        self.now = TRADE + 600 + 10                        # 10 s after the retest candle closed
        self.mt5 = MultiTFMT5(session_bars())
        self.mt5.set_quote(2011.4, 2011.6, self.now)
        self.md = ReadOnlyMarketData(self.mt5, {'max_tick_age_seconds': 60})
        self.agent = LiveORB()
        self.state = {}

    def evaluate(self, now=None):
        now = now or self.now
        q = self.md.quote('XAUUSD', now=now)
        candles = self.md.candles('XAUUSD', 'M5', 50, now=now)
        return self.agent.evaluate(now, q, candles, q.point, self.state, market=self.md, symbol='XAUUSD')

    def test_breakout_retest_becomes_a_live_orb_trade(self):
        sig = self.evaluate()
        self.assertIsNotNone(sig, self.state)
        self.assertEqual(sig['side'], 'BUY')
        risk = sig['trigger_price'] - sig['initial_sl']
        # live sizing: SL = 90% of the way to range low - 60 pts; fixed 2R target; TP1 at 1R
        self.assertAlmostEqual(sig['initial_sl'], 2011.6 - (2011.6 - (LO - 0.6)) * 0.9, delta=0.011)
        self.assertAlmostEqual(sig['initial_tp'], sig['trigger_price'] + 2 * risk, delta=0.02)
        ex = sig['extra']
        self.assertAlmostEqual(ex['partial_price'], sig['trigger_price'] + risk, delta=0.02)
        self.assertEqual((ex['partial_fraction'], ex['be_offset_points'], ex['ref_high'], ex['ref_low']),
                         (0.5, 50, HI, LO))

    def test_one_signal_per_session(self):
        self.assertIsNotNone(self.evaluate())
        self.state.pop('done_m5')                          # even if the candle were analysed again
        self.assertIsNone(self.evaluate(self.now + 5))

    def test_signal_window_passed(self):
        self.assertIsNone(self.evaluate(TRADE + 600 + 90))    # > 60 s after the retest close
        self.assertIsNotNone(self.state.get('done_m5'))

    def test_failed_retest_gives_no_trade(self):
        self.mt5 = MultiTFMT5(session_bars(retest_low=2008.0, retest_close=2009.0))   # closed back inside
        self.mt5.set_quote(2009.0, 2009.2, self.now)
        self.md = ReadOnlyMarketData(self.mt5, {'max_tick_age_seconds': 60})
        self.assertIsNone(self.evaluate())

    def test_wide_spread_is_retried_not_consumed(self):
        self.mt5.set_quote(2010.6, 2011.6, self.now)       # 100 pts > orb max 80
        self.assertIsNone(self.evaluate())
        self.assertIsNone(self.state.get('done_m5'))       # live retries on its next poll
        self.mt5.set_quote(2011.4, 2011.6, self.now + 15)
        self.assertIsNotNone(self.evaluate(self.now + 15))

    def test_same_plan_as_the_live_orb_runner(self):
        from orb_strategy import ORBRunner
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        runner = ORBRunner({'orb': {'session_utc_offset_hours': 2}}, Path(tmp.name) / 'orb_state.json')
        runner.apply(dict(skip_weekdays=[2, 3], skip_months=[12], signals_per_day=1, sl_multiplier=0.9,
                          buy_sl_buffer_points=60, sell_sl_buffer_points=100, tp_mode='fixed', fixed_rr=2.0))
        live = runner.check(self.mt5, 'XAUUSD', self.now)['entry_plan']
        sig = self.evaluate()
        self.assertEqual((sig['side'], sig['trigger_price'], sig['initial_sl'], sig['initial_tp'],
                          sig['extra']['partial_price']),
                         (live['side'], live['entry'], live['stop_loss'], live['tp2'], live['tp1']))

    def test_wednesday_is_skipped_like_live(self):
        self.assertIn(2, self.agent.core_config('XAUUSD')['skip_weekdays'])


class Registration(unittest.TestCase):
    def test_registered_once_on_xauusd_with_the_live_offset(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        eng = LiveTestEngine(Store(Path(tmp.name) / 'db.sqlite3'))
        cfg = {'symbols': ['XAUUSD', 'EURUSD'], 'bar_timestamp_offset_hours': 3,
               'orb': {'symbols': ['XAUUSD'], 'range_start_hour': 1, 'range_end_hour': 6}}
        ensure_default_agents(eng, cfg)
        ensure_default_agents(eng, cfg)
        orb = [la for la in eng.agents.values() if la.agent.strategy_id == 'LIVE_ORB']
        self.assertEqual(len(orb), 1)
        self.assertEqual(orb[0].base_symbol, 'XAUUSD')
        self.assertEqual(orb[0].agent.config['session_utc_offset_hours'], 3)
        self.assertEqual(orb[0].status, 'PAUSED')
        self.assertIn('01-06', orb[0].agent.label())


if __name__ == '__main__':
    unittest.main()
