import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

import strategy_settings as ss
from bbma_bot import find_setup
from dashboard_server import build_server
from dashboard_store import Store
from test_bbma import CFG

GOOD = {'bbma': {'csa_reentry': True, 'momentum_reentry': False, 'mhv': True}, 'confluence': {'min_score': 5}, 'orb': {'trade': False, 'skip_weekdays': [2, 3], 'skip_months': [12], 'signals_per_day': 1, 'sl_multiplier': 0.9, 'buy_sl_buffer_points': 60, 'sell_sl_buffer_points': 100, 'tp_mode': 'dynamic', 'fixed_rr': 2.0}}


class StrategySettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = Store(Path(self.tmp.name) / 'd.sqlite3')

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_follow_config_until_saved(self):
        cfg = {'enable_mhv': True, 'confluence': {'min_score': 3}}
        self.assertEqual(ss.load(self.store, cfg), {'bbma': {'csa_reentry': True, 'momentum_reentry': True, 'mhv': True}, 'confluence': {'min_score': 3}, 'orb': {'trade': False, 'skip_weekdays': [2, 3], 'skip_months': [12], 'signals_per_day': 1, 'sl_multiplier': 0.9, 'buy_sl_buffer_points': 60, 'sell_sl_buffer_points': 100, 'tp_mode': 'dynamic', 'fixed_rr': 2.0}})
        ss.save(self.store, GOOD)
        self.assertEqual(ss.load(self.store, cfg), GOOD)

    def test_validation(self):
        bad = [
            {'bbma': GOOD['bbma']},
            {**GOOD, 'confluence': {'min_score': 2}},
            {**GOOD, 'confluence': {'min_score': '5'}},
            {**GOOD, 'bbma': {**GOOD['bbma'], 'mhv': 'yes'}},
            {**GOOD, 'bbma': {'csa_reentry': False, 'momentum_reentry': False, 'mhv': False}},
            {**GOOD, 'bbma': {**GOOD['bbma'], 'extra': True}},
            {**GOOD, 'orb': {'trade': 'yes'}},
            {**GOOD, 'orb': {**GOOD['orb'], 'trade': 'yes'}},
            {**GOOD, 'orb': {**GOOD['orb'], 'skip_weekdays': [0, 1, 2, 3, 4]}},
            {**GOOD, 'orb': {**GOOD['orb'], 'skip_weekdays': [5]}},
            {**GOOD, 'orb': {**GOOD['orb'], 'skip_months': list(range(1, 13))}},
            {**GOOD, 'orb': {**GOOD['orb'], 'signals_per_day': 4}},
            {**GOOD, 'orb': {**GOOD['orb'], 'sl_multiplier': 1.5}},
            {**GOOD, 'orb': {**GOOD['orb'], 'tp_mode': 'trailing'}},
            {**GOOD, 'orb': {**GOOD['orb'], 'fixed_rr': 9}},
        ]
        for value in bad:
            with self.assertRaises(ValueError):
                ss.validate(value)

    def test_settings_saved_before_orb_keep_their_values(self):
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('strategy_settings',?)", (json.dumps({'bbma': GOOD['bbma'], 'confluence': {'min_score': 5}}),))
        s = ss.load(self.store, {})
        self.assertEqual((s['confluence']['min_score'], s['bbma']['mhv'], s['orb']['trade'], s['orb']['skip_months']), (5, True, False, [12]))
        with self.store.connect() as db:  # saved when ORB only had the trade switch
            db.execute("INSERT OR REPLACE INTO meta VALUES ('strategy_settings',?)", (json.dumps({'bbma': GOOD['bbma'], 'confluence': {'min_score': 5}, 'orb': {'trade': True}}),))
        s = ss.load(self.store, {})
        self.assertEqual((s['orb']['trade'], s['orb']['signals_per_day'], s['orb']['tp_mode']), (True, 1, 'dynamic'))

    def test_damaged_value_falls_back_to_config(self):
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('strategy_settings','{\"oops\":1}')")
        self.assertEqual(ss.load(self.store, {})['confluence']['min_score'], 4)

    def test_bbma_respects_allowed_setups(self):
        r = dict(time=0, open=104., high=108., low=102., close=107., mid=104., ma5_low=103., ma10_low=102.,
                 ma5_high=106., ma10_high=105., upper=112., lower=98., atr=2.)
        s = pd.DataFrame([dict(r, time=i * 900) for i in range(50)])
        self.assertEqual(find_setup(s, 'BUY', CFG)['type'], 'CSA_REENTRY')            # unchanged default
        on = dict(CFG, **ss.bbma_overrides(ss.defaults()))
        self.assertEqual(find_setup(s, 'BUY', on)['type'], 'CSA_REENTRY')
        off = dict(CFG, **ss.bbma_overrides({'bbma': {'csa_reentry': False, 'momentum_reentry': True, 'mhv': False}, 'confluence': {'min_score': 4}, 'orb': {'trade': False, 'skip_weekdays': [2, 3], 'skip_months': [12], 'signals_per_day': 1, 'sl_multiplier': 0.9, 'buy_sl_buffer_points': 60, 'sell_sl_buffer_points': 100, 'tp_mode': 'dynamic', 'fixed_rr': 2.0}}))
        self.assertIsNone(find_setup(s, 'BUY', off))
        self.assertEqual(ss.bbma_overrides(GOOD), {'enable_mhv': True, '_allowed_setups': ['CSA_REENTRY', 'MHV_REVERSAL']})

    def test_dashboard_endpoint_saves_and_rejects(self):
        server = build_server(self.store, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]; base = f'http://127.0.0.1:{port}'
        def post(body, headers=None):
            h = {'Content-Type': 'application/json', 'X-Dashboard-Request': 'controls', 'Origin': base, 'Host': f'127.0.0.1:{port}'}
            h.update(headers or {})
            return urllib.request.urlopen(urllib.request.Request(base + '/api/strategy-settings', json.dumps(body).encode(), h, method='POST'))
        try:
            with post(GOOD) as r:
                self.assertEqual(json.load(r)['settings'], GOOD)
            self.assertEqual(ss.load(self.store), GOOD)
            with self.assertRaises(urllib.error.HTTPError) as bad:
                post({**GOOD, 'confluence': {'min_score': 1}})
            self.assertEqual(bad.exception.code, 400)
            with self.assertRaises(urllib.error.HTTPError) as forbidden:
                post(GOOD, {'Origin': 'http://evil.example'})
            self.assertEqual(forbidden.exception.code, 403)
            with urllib.request.urlopen(base + '/api/status') as r:
                self.assertEqual(json.load(r)['meta']['strategy_settings'], GOOD)
        finally:
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
