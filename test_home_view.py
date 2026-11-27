"""Home page (command center): read-only roll-up of Live Test, Signal Agent and account data, and its routes."""
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import home_view
from dashboard_server import build_server
from dashboard_store import Store
from live_test import LiveTestEngine, VALID, DATA_GAP

NOW = 1_795_000_000.0
DAY = 86400


class HomeView(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'db.sqlite3')

    def test_empty_desk_still_answers(self):
        v = home_view.build(self.store, now=NOW)
        json.dumps(v, allow_nan=False)
        self.assertEqual((v['status'], v['totals']['agents'], v['strategies'], v['leaders']), ('OK', 0, [], []))
        self.assertEqual(sum(b['n'] for b in v['dist']), 0)

    def seed(self):
        LiveTestEngine(self.store)                                  # creates the Live Test tables
        agents = [('LIVE_BBMA|EURUSD|a', 'LIVE_BBMA', 'EURUSD', 'RUNNING'), ('LIVE_BBMA|XAUUSD|a', 'LIVE_BBMA', 'XAUUSD', 'PAUSED'),
                  ('CMS_SSR|EURUSD|b', 'CMS_SSR', 'EURUSD', 'RUNNING'), ('GONE|GBPUSD|c', 'GONE', 'GBPUSD', 'REMOVED')]
        trades = [('LIVE_BBMA', 'EURUSD', 2.0, VALID, 1), ('LIVE_BBMA', 'EURUSD', -1.0, VALID, 1), ('CMS_SSR', 'EURUSD', -1.0, VALID, 0),
                  ('CMS_SSR', 'EURUSD', 9.0, DATA_GAP, 0), ('CMS_SSR', 'EURUSD', 5.0, VALID, 40)]       # gap and 40-day-old trade excluded
        with self.store.connect() as db:
            for a in agents:
                db.execute('INSERT INTO live_test_agents VALUES (?,?,?,?,?,?,?,?)', (a[0], a[1], a[2], 'x', '{}', a[3], '{}', NOW))
            for i, (sid, sym, r, q, age) in enumerate(trades):
                t = NOW - age * DAY - 60
                db.execute('INSERT INTO live_test_trades VALUES (?,?,?,?)', (f't{i}', f'{sid}|{sym}|x', t, json.dumps(dict(
                    strategy_id=sid, symbol=sym, pnl_r=r, data_quality_status=q, entry_time=t - 600, exit_time=t))))

    def test_rollup_counts_only_clean_recent_trades(self):
        self.seed()
        v = home_view.build(self.store, now=NOW)
        json.dumps(v, allow_nan=False)                               # what the page receives
        T = v['totals']
        self.assertEqual((T['agents'], T['running'], T['pairs'], T['strategies'], T['trades_30d'], T['net_r_30d']), (3, 2, 2, 2, 3, 0.0))
        bbma = next(s for s in v['strategies'] if s['id'] == 'LIVE_BBMA')
        self.assertEqual((bbma['name'], bbma['agents'], bbma['running'], bbma['trades'], bbma['net_r'], bbma['live']), ('BBMA LIVE', 2, 1, 2, 1.0, True))
        self.assertEqual(v['strategies'][0]['id'], 'LIVE_BBMA')      # live strategies lead
        self.assertEqual({b['label']: b['n'] for b in v['dist'] if b['n']}, {'-1R': 2, '+2': 1})
        self.assertEqual({p['symbol']: (p['agents'], p['trades']) for p in v['pair_stats']}, {'EURUSD': (2, 3), 'XAUUSD': (1, 0)})
        self.assertEqual(v['leaders'][0], dict(strategy_id='LIVE_BBMA', symbol='EURUSD', net_r=1.0, trades=2))
        heat = {h['name']: h['cells'] for h in v['heat']}
        self.assertEqual((heat['BBMA LIVE'][-2], heat['CMS SSR'][-1]), (1.0, -1.0))
        self.assertNotIn('GONE', json.dumps(v['strategies']))

    def test_unknown_strategy_gets_a_readable_name(self):
        self.assertEqual(home_view.short_name('NEW_THING_V2'), 'NEW THING V2')


class Routes(unittest.TestCase):
    def test_home_page_and_api_are_served(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        server = build_server(Store(Path(tmp.name) / 'db.sqlite3'), 0)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(base + '/home') as r:
                page = r.read().decode('utf-8')
                self.assertIn('js/home.js', page)
                self.assertNotIn('https://', page.split('<body>')[1])   # everything loads from the dashboard itself
            for path in ['/js/home.js', '/js/three-r128.js', '/js/three-bloom.js']:
                with urllib.request.urlopen(base + path) as r: self.assertEqual(r.status, 200)
            with urllib.request.urlopen(base + '/api/home') as r:
                self.assertEqual(json.load(r)['status'], 'OK')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
