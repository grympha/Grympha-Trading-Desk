"""Dashboard-started Quant Agent backtests: validation, one run at a time, progress and outcome."""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import quant_agent as qa
import quant_runner as qr
from dashboard_server import build_server
from dashboard_store import Store
from live_test import LiveTestEngine, build_agent

FAKE_OK = "import time; print('PROGRESS 0/2', flush=True); time.sleep(.3); print('XAUUSD  Advocate  3 trades'); print('PROGRESS 2/2'); print('Saved 2 results for the Signal Agent.')"
FAKE_FAIL = "print('Cannot connect to MT5.'); raise SystemExit(1)"
FAKE_SLOW = "import time; print('PROGRESS 0/1', flush=True); time.sleep(30)"


class Runner(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'db.sqlite3')
        LiveTestEngine(self.store)
        rows = []
        for sid, sym, status in (('ADVOCATE_EMA_PULLBACK', 'XAUUSD', 'RUNNING'), ('CMS_SSR', 'EURUSD', 'RUNNING'),
                                 ('CMS_SSR', 'GBPUSD', 'PAUSED'), ('CMS_SSR', 'USDJPY', 'REMOVED')):
            a = build_agent(sid, {})
            rows.append((f'{sid}|{sym}|{a.config_id}', sid, sym, a.config_id, json.dumps(a.config), status, '{}', 0))
        with self.store.connect() as db:
            db.executemany('INSERT INTO live_test_agents VALUES (?,?,?,?,?,?,?,?)', rows)
        p = mock.patch.object(qr, 'LOG', Path(tmp.name) / 'run.log'); p.start(); self.addCleanup(p.stop)
        self.addCleanup(self.kill)

    def kill(self):
        if qr._proc is not None and qr._proc.poll() is None:
            qr._proc.kill(); qr._proc.wait()
        qr._proc = None

    def fake(self, code):
        return mock.patch.object(qr, '_command', lambda *a: [sys.executable, '-c', code])

    def wait(self):
        for _ in range(100):
            j = qr.status(self.store)
            if j['state'] != 'RUNNING':
                return j
            time.sleep(.1)
        self.fail('backtest did not finish')

    def test_options_list_registered_strategies_and_pairs(self):
        opts = {o['id']: o for o in qr.options(self.store)}
        self.assertEqual(opts['CMS_SSR']['symbols'], ['EURUSD', 'GBPUSD'])          # removed agent left out
        self.assertEqual((opts['ADVOCATE_EMA_PULLBACK']['name'], opts['ADVOCATE_EMA_PULLBACK']['agents']), ('ADVOCATE EMA', 1))

    def test_command_targets_the_registered_agents(self):
        cmd = qr._command('CMS_SSR', 'EURUSD', 30)
        self.assertEqual(cmd[2:], [str(qr.ROOT / 'quant_agent.py'), '--registered', '--strategies', 'CMS_SSR',
                                   '--days', '30', '--symbols', 'EURUSD'])
        self.assertNotIn('--symbols', qr._command('CMS_SSR', 'ALL', 7))

    def test_rejects_unknown_choices(self):
        for args in (('NOPE', 'ALL', 30), ('CMS_SSR', 'USDJPY', 30), ('CMS_SSR', 'ALL', 45)):
            with self.assertRaises(ValueError):
                qr.start(self.store, *args)
        self.assertIsNone(qr.status(self.store))

    def test_run_reports_progress_then_saved_results(self):
        with self.fake(FAKE_OK):
            job = qr.start(self.store, 'ADVOCATE_EMA_PULLBACK', 'XAUUSD', 30)
        self.assertEqual((job['state'], job['name']), ('RUNNING', 'ADVOCATE EMA'))
        j = self.wait()
        self.assertEqual((j['state'], j['saved'], j['progress_done'], j['progress_total']), ('DONE', 2, 2, 2))
        self.assertNotIn('PROGRESS 2/2', j['log'])

    def test_failure_shows_the_output(self):
        with self.fake(FAKE_FAIL):
            qr.start(self.store, 'CMS_SSR', 'ALL', 7)
        j = self.wait()
        self.assertEqual((j['state'], j['exit_code']), ('FAILED', 1))
        self.assertIn('Cannot connect to MT5.', j['log'])

    def test_one_run_at_a_time_and_stop(self):
        with self.fake(FAKE_SLOW):
            qr.start(self.store, 'CMS_SSR', 'ALL', 7)
            with self.assertRaises(RuntimeError):
                qr.start(self.store, 'ADVOCATE_EMA_PULLBACK', 'XAUUSD', 30)
        self.assertEqual(qr.stop(self.store)['state'], 'STOPPED')
        self.assertEqual(qr.status(self.store)['state'], 'STOPPED')
        with self.assertRaises(RuntimeError):
            qr.stop(self.store)

    def test_run_left_by_an_earlier_dashboard_is_marked_interrupted(self):
        with self.fake(FAKE_SLOW):
            qr.start(self.store, 'CMS_SSR', 'ALL', 7)
        self.kill()                                              # as if the bot (and dashboard) restarted
        self.assertEqual(qr.status(self.store)['state'], 'INTERRUPTED')

    def test_registered_jobs_rebuild_the_same_agent_ids(self):
        jobs = qa.registered_jobs(self.store, {'CMS_SSR'})
        self.assertEqual(sorted(s for _, s in jobs), ['EURUSD', 'GBPUSD'])
        self.assertEqual([s for _, s in qa.registered_jobs(self.store, None, {'XAUUSD'})], ['XAUUSD'])

    def test_http_routes(self):
        server = build_server(self.store, 0)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        post = lambda body, origin=base: urllib.request.urlopen(urllib.request.Request(
            base + '/api/quant-run', data=json.dumps(body).encode(), method='POST',
            headers={'Content-Type': 'application/json', 'X-Dashboard-Request': 'controls', 'Origin': origin}))
        try:
            with urllib.request.urlopen(base + '/api/quant-run') as r:
                v = json.load(r)
            self.assertEqual((v['job'], v['days']), (None, [7, 14, 30, 60, 90, 180]))
            with self.assertRaises(urllib.error.HTTPError) as e:
                post(dict(action='start', strategy='CMS_SSR', symbol='ALL', days=7), origin='http://evil.example')
            self.assertEqual(e.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as e:
                post(dict(action='start', strategy='CMS_SSR', symbol='ALL', days='7'))
            self.assertEqual(e.exception.code, 400)
            with self.fake(FAKE_OK), post(dict(action='start', strategy='CMS_SSR', symbol='ALL', days=7)) as r:
                self.assertEqual(json.load(r)['job']['state'], 'RUNNING')
            self.assertEqual(self.wait()['state'], 'DONE')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
