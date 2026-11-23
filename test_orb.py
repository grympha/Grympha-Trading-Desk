"""ORB agent tests.

Part 1 is the original Grympha ORB Scanner v1 engine suite (19 tests, unchanged logic; the fixture now
reads the ORB defaults from this project). Part 2 covers the integration as a third strategy.
"""
import ast
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as N

import orb_strategy
from orb_core import Bar, iso, news_check, scan, validate_config

ROOT = Path(__file__).resolve().parent


def fixture():
    c = dict(orb_strategy.DEFAULTS)
    c.update(range_start_hour=0, session_utc_offset_hours=0, news_mode='off', news_file='news.json')
    now = int(datetime(2026, 11, 23, 7, 15, tzinfo=timezone.utc).timestamp()) + 2
    latest = now - 2 - 300
    bars = [Bar(latest - (799 - i) * 300, 3000, 3010, 2990, 3000) for i in range(800)]
    bars[-2] = Bar(latest - 300, 3011, 3016, 3011, 3015)
    bars[-1] = Bar(latest, 3014, 3015, 3010, 3013)
    h_end = (now // 14400) * 14400 - 14400
    h4 = [Bar(h_end - (99 - i) * 14400, 2800 + i, 2802 + i, 2798 + i, 2801 + i) for i in range(100)]
    return c, bars, h4, now, {'bid': 3013, 'ask': 3013.2, 'time': now}, {'point': .01, 'tick_size': .01, 'digits': 2}


class EngineTests(unittest.TestCase):  # original ORB v1 engine suite
    def test_valid_buy(self):
        r = scan(*fixture()); s = r['signal']
        self.assertEqual(r['status'], 'SIGNAL_CANDIDATE'); self.assertEqual(s['direction'], 'BUY')
        self.assertLess(s['breakout_bar_utc'], s['retest_bar_utc'])
        self.assertLess(s['sl_suggested'], s['entry_reference']); self.assertGreater(s['tp_suggested'], s['entry_reference'])
        self.assertFalse(s['execution_authorized'])

    def test_no_same_bar_retest(self):
        c, b, h, n, q, s = fixture()
        b[-2] = replace(b[-2], open=3000, high=3010, low=2990, close=3000)
        b[-1] = Bar(b[-1].time, 3000, 3015, 2998, 3013)
        self.assertIsNone(scan(c, b, h, n, q, s)['signal'])

    def test_range_end_excluded(self):
        c, b, h, n, q, s = fixture()
        t = int(datetime(2026, 11, 23, 6, tzinfo=timezone.utc).timestamp())
        b = [replace(x, high=3050, low=2950) if x.time == t else x for x in b]
        r = scan(c, b, h, n, q, s)
        self.assertEqual(r['signal']['range_high'], 3010); self.assertEqual(r['signal']['range_low'], 2990)

    def test_incomplete_range(self):
        c, b, h, n, q, s = fixture(); del b[-30]
        self.assertEqual(scan(c, b, h, n, q, s)['status'], 'INCOMPLETE_RANGE')

    def test_current_candles_ignored(self):
        c, b, h, n, q, s = fixture()
        base = scan(c, b, h, n, q, s)['signal']['signal_id']
        b.append(Bar(n - 2, 1000, 9999, 1, 1000)); h.append(Bar((n // 14400) * 14400, 1000, 9999, 1, 1000))
        self.assertEqual(scan(c, b, h, n, q, s)['signal']['signal_id'], base)

    def test_stale_signal(self):
        c, b, h, n, q, s = fixture(); n += 100; q['time'] = n
        self.assertEqual(scan(c, b, h, n, q, s)['status'], 'STALE_SIGNAL_SUPPRESSED')

    def test_spread(self):
        c, b, h, n, q, s = fixture(); q['ask'] = 3014
        self.assertEqual(scan(c, b, h, n, q, s)['status'], 'SPREAD_BLOCKED')

    def test_quote_stale(self):
        c, b, h, n, q, s = fixture(); q['time'] -= 31
        self.assertEqual(scan(c, b, h, n, q, s)['status'], 'STALE_OR_FUTURE_QUOTE')

    def test_missing_news_blocks(self):
        c, b, h, n, q, s = fixture(); c['news_mode'] = 'required'
        self.assertEqual(scan(c, b, h, n, q, s)['status'], 'NEWS_DATA_MISSING')

    def test_news_windows_and_coverage(self):
        c, b, h, n, q, s = fixture(); c['news_mode'] = 'required'
        news = {'generated_at_utc': iso(n), 'coverage_from_utc': iso(n - 3600), 'coverage_to_utc': iso(n + 7200),
                'events': [{'time_utc': iso(n + 1800), 'currency': 'USD', 'impact': 'HIGH', 'name': 'Test'}]}
        self.assertFalse(news_check(c, n, news)[0])
        news['events'] = []
        self.assertTrue(news_check(c, n, news)[0]); self.assertFalse(news_check(c, n + 8000, news)[0])

    def test_deep_retest_rejected(self):
        c, b, h, n, q, s = fixture(); b[-1] = replace(b[-1], low=3000)
        self.assertIsNone(scan(c, b, h, n, q, s)['signal'])

    def test_h4_opposite_blocks(self):
        c, b, h, n, q, s = fixture()
        h = [Bar(x.time, 3100 - i, 3102 - i, 3098 - i, 3099 - i) for i, x in enumerate(h)]
        self.assertIsNone(scan(c, b, h, n, q, s)['signal'])

    def test_retest_expired(self):
        c, b, h, n, q, s = fixture(); c['max_bars_after_breakout'] = 1
        b[-3] = replace(b[-2], time=b[-3].time); b[-2] = Bar(b[-2].time, 3014, 3016, 3012, 3014)
        self.assertIsNone(scan(c, b, h, n, q, s)['signal'])

    def test_sell_mirror(self):
        c, b, h, n, q, s = fixture()
        b = [Bar(x.time, 6000 - x.open, 6000 - x.low, 6000 - x.high, 6000 - x.close) for x in b]
        h = [Bar(x.time, 6000 - x.open, 6000 - x.low, 6000 - x.high, 6000 - x.close) for x in h]
        sig = scan(c, b, h, n, {'bid': 2986.8, 'ask': 2987, 'time': n}, s)['signal']
        self.assertEqual(sig['direction'], 'SELL')
        self.assertGreater(sig['sl_suggested'], sig['entry_reference']); self.assertLess(sig['tp_suggested'], sig['entry_reference'])

    def test_unknown_timezone_blocks(self):
        c, *rest = fixture(); c['session_utc_offset_hours'] = None
        with self.assertRaises(ValueError):
            validate_config(c)

    def test_session_gap(self):
        c, b, h, n, q, s = fixture(); del b[-5]
        self.assertEqual(scan(c, b, h, n, q, s)['status'], 'SESSION_DATA_GAP')

    def test_duplicate_bars_rejected(self):
        c, b, h, n, q, s = fixture(); b.insert(1, b[0])
        with self.assertRaises(ValueError):
            scan(c, b, h, n, q, s)

    def test_nonzero_session_offset(self):
        c, b, h, n, q, s = fixture(); c['session_utc_offset_hours'] = 2
        b = [replace(x, time=x.time - 7200) for x in b]; h = [replace(x, time=x.time - 7200) for x in h]
        q['time'] -= 7200
        self.assertIsNotNone(scan(c, b, h, n - 7200, q, s)['signal'])

    def test_skipped_day(self):
        c, b, h, n, q, s = fixture(); c['skip_weekdays'] = [0]
        self.assertEqual(scan(c, b, h, n, q, s)['status'], 'SKIPPED_SESSION')


def fake_mt5(bars, h4, now, quote):
    rows = lambda bs: [dict(time=x.time, open=x.open, high=x.high, low=x.low, close=x.close) for x in bs]
    return N(TIMEFRAME_M5=5, TIMEFRAME_H4=16388, terminal_info=lambda: N(connected=True), symbol_select=lambda *a: True,
             symbol_info=lambda s: N(point=.01, trade_tick_size=.01, digits=2),
             symbol_info_tick=lambda s: N(bid=quote['bid'], ask=quote['ask'], time=quote['time']),
             copy_rates_from_pos=lambda s, tf, start, count: rows(bars if tf == 5 else h4)[-count:])


class AgentIntegration(unittest.TestCase):
    CFG = {'symbols': ['XAUUSD'], 'poll_seconds': 15, 'bar_timestamp_offset_hours': 0, 'tick_timestamp_offset_hours': 0,
           'orb': {'range_start_hour': 0}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True); self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_match_broker_session(self):
        s = orb_strategy.settings({'bar_timestamp_offset_hours': 2})
        self.assertEqual((s['range_start_hour'], s['range_end_hour'], s['session_utc_offset_hours'], s['symbols']), (1, 6, 2, ['XAUUSD']))
        self.assertEqual(s['news_mode'], 'off')  # bot's MT5 calendar filter applies at execution

    def test_runner_maps_signal_to_plan_and_limits_one_per_day(self):
        c, b, h, n, q, s = fixture()
        runner = orb_strategy.ORBRunner(self.CFG, self.dir / 'orb_state.json')
        r = runner.check(fake_mt5(b, h, n, q), 'XAUUSD', n)
        p = r['entry_plan']
        self.assertEqual((r['strategy'], r['health'], r['orb_status'], r['condition']['side']), ('ORB', 'OK', 'SIGNAL_CANDIDATE', 'BUY'))
        self.assertEqual((p['side'], p['signal_kind'], p['setup']), ('BUY', 'REFERENCE', 'ORB_RETEST'))
        self.assertLess(p['stop_loss'], p['entry']); self.assertLess(p['entry'], p['tp1']); self.assertLess(p['tp1'], p['tp2'])
        self.assertAlmostEqual(p['tp1'] - p['entry'], p['entry'] - p['stop_loss'], delta=.011)  # TP1 = 1R
        self.assertEqual(r['setup']['range_high'], 3010)
        again = orb_strategy.ORBRunner(self.CFG, self.dir / 'orb_state.json').check(fake_mt5(b, h, n, q), 'XAUUSD', n + 5)
        self.assertEqual((again['orb_status'], again['entry_plan']), ('ALREADY_ISSUED', None))  # no second order, survives restart
        json.dumps(r, allow_nan=False)

    def test_registered_as_third_strategy(self):
        from demo_execution import MAGIC
        from pair_controls import STRATEGIES
        from strategy_execution import EXECUTORS
        from strategy_monitors import MONITORS
        from trade_labels import action_comment, entry_comment
        self.assertEqual(MAGIC['ORB'], 816003); self.assertIn('ORB', STRATEGIES)
        self.assertEqual(EXECUTORS['ORB'].news_until({}, 1000), 1000)  # same news window as the other strategies
        self.assertEqual(MONITORS['ORB'].boundary({'setup_structure': {'range_high': 3010, 'range_low': 2990}}, [], True), 3010)
        self.assertRegex(entry_comment('ORB', 'ORB_RETEST', None, 'acct', 'k'), r'^ORB Retest #[0-9a-f]{6}$')
        self.assertEqual(action_comment('ORB', 'early exit'), 'ORB early exit')

    def test_store_accepts_orb_and_trading_is_off_by_default(self):
        from dashboard_store import Store
        import strategy_settings as ss
        c, b, h, n, q, s = fixture()
        r = orb_strategy.ORBRunner(self.CFG, self.dir / 'st.json').check(fake_mt5(b, h, n, q), 'XAUUSD', n)
        store = Store(self.dir / 'd.sqlite3'); store.start(dict(self.CFG, symbols=['XAUUSD'], poll_seconds=15))
        store.record(r, now=n)
        snap = store.snapshot(now=n)
        self.assertIn('ORB', snap['meta']['strategies'])
        self.assertTrue(any(x['strategy'] == 'ORB' and x['key'].startswith('ORB|XAUUSD:') for x in snap['signals']))
        self.assertFalse(ss.load(store, {})['orb']['trade'])

    def test_executor_ignores_orb_until_trading_allowed(self):
        from demo_execution import DemoExecutor
        from dashboard_store import Store
        import strategy_settings as ss
        ex = DemoExecutor.__new__(DemoExecutor)
        ex.store = Store(self.dir / 'e.sqlite3'); ex.cfg = {}
        ex.guard = lambda: (_ for _ in ()).throw(AssertionError('must stop before any broker work'))
        ex.submit({'strategy': 'ORB', 'symbol': 'XAUUSD', 'health': 'OK', 'entry_plan': {'id': 'x'}})  # returns silently
        ss.save(ex.store, {**ss.defaults(), 'orb': {**ss.defaults()['orb'], 'trade': True}})
        with self.assertRaises(AssertionError):
            ex.submit({'strategy': 'ORB', 'symbol': 'XAUUSD', 'health': 'OK', 'entry_plan': {'id': 'x'}})

    def settings_(self, **change):
        import strategy_settings as ss
        return ss.validate_orb({**ss.orb_defaults(), **change})

    def test_dashboard_settings_change_the_rules(self):
        c, b, h, n, q, s = fixture()  # fixture day is a Monday (weekday 0)
        r = orb_strategy.ORBRunner(self.CFG, self.dir / 'a.json'); r.apply(self.settings_(skip_weekdays=[0]))
        self.assertEqual(r.check(fake_mt5(b, h, n, q), 'XAUUSD', n)['orb_status'], 'SKIPPED_SESSION')
        r = orb_strategy.ORBRunner(self.CFG, self.dir / 'b.json'); r.apply(self.settings_(skip_months=[11]))
        self.assertEqual(r.check(fake_mt5(b, h, n, q), 'XAUUSD', n)['orb_status'], 'SKIPPED_SESSION')  # November
        r = orb_strategy.ORBRunner(self.CFG, self.dir / 'c.json'); r.apply(self.settings_(skip_months=[]))
        self.assertEqual(r.check(fake_mt5(b, h, n, q), 'XAUUSD', n)['orb_status'], 'SIGNAL_CANDIDATE')

    def test_fixed_rr_and_stop_settings(self):
        c, b, h, n, q, s = fixture()
        dyn = orb_strategy.ORBRunner(self.CFG, self.dir / 'd.json').check(fake_mt5(b, h, n, q), 'XAUUSD', n)['entry_plan']
        r = orb_strategy.ORBRunner(self.CFG, self.dir / 'e.json'); r.apply(self.settings_(tp_mode='fixed', fixed_rr=3.0))
        fx = r.check(fake_mt5(b, h, n, q), 'XAUUSD', n)['entry_plan']
        self.assertAlmostEqual(fx['tp2'] - fx['entry'], 3 * (fx['entry'] - fx['stop_loss']), delta=.011)
        self.assertEqual(fx['tp_mode'], 'fixed'); self.assertNotEqual(fx['tp2'], dyn['tp2'])
        r = orb_strategy.ORBRunner(self.CFG, self.dir / 'f.json'); r.apply(self.settings_(sl_multiplier=0.5))
        tight = r.check(fake_mt5(b, h, n, q), 'XAUUSD', n)['entry_plan']
        self.assertLess(tight['entry'] - tight['stop_loss'], dyn['entry'] - dyn['stop_loss'])

    def test_signals_per_day_and_old_state_format(self):
        c, b, h, n, q, s = fixture()
        state = self.dir / 'g.json'
        r = orb_strategy.ORBRunner(self.CFG, state); r.apply(self.settings_(signals_per_day=2))
        self.assertIsNotNone(r.check(fake_mt5(b, h, n, q), 'XAUUSD', n)['entry_plan'])
        self.assertEqual(r.check(fake_mt5(b, h, n, q), 'XAUUSD', n + 5)['orb_status'], 'ALREADY_ISSUED')  # same retest again
        r.state['emitted']['XAUUSD']['ids'].append('another-retest')
        self.assertEqual(r.check(fake_mt5(b, h, n, q), 'XAUUSD', n + 9)['orb_status'], 'ALREADY_ISSUED')
        r.state['emitted']['XAUUSD']['ids'] = ['x', 'y']
        self.assertEqual(r.check(fake_mt5(b, h, n, q), 'XAUUSD', n + 9)['orb_status'], 'DAILY_SIGNAL_LIMIT')
        old = self.dir / 'h.json'; old.write_text(json.dumps({'emitted': {'XAUUSD': '2026-11-23'}}))  # v1.26 format
        self.assertEqual(orb_strategy.ORBRunner(self.CFG, old).check(fake_mt5(b, h, n, q), 'XAUUSD', n)['orb_status'], 'DAILY_SIGNAL_LIMIT')

    def test_agent_has_no_order_sending(self):
        for name in ('orb_core.py', 'orb_strategy.py', 'orb_scanner.py'):
            tree = ast.parse((ROOT / name).read_text(encoding='utf-8'))
            calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
            self.assertNotIn('order_send', calls, name)


if __name__ == '__main__':
    unittest.main()
