import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from dashboard_store import Store
import equity_history as eh


class FakeMT5:
    def __init__(self, login=1, server='Demo'):
        self.login, self.server = login, server

    def account_info(self):
        return SimpleNamespace(login=self.login, server=self.server)


def healthy(equity, balance=10000.0):
    return dict(status='HEALTHY', balance=balance, equity=equity, currency='USD', account_type='DEMO')


class EquityHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = Store(Path(self.tmp.name) / 'd.sqlite3')

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_one_point_per_minute_and_skips_unhealthy(self):
        mt5 = FakeMT5()
        self.assertTrue(eh.record(self.store, mt5, healthy(10000), now=960))
        self.assertTrue(eh.record(self.store, mt5, healthy(10010), now=1010))  # same minute -> replaced
        self.assertFalse(eh.record(self.store, mt5, dict(status='DISCONNECTED'), now=1100))
        self.assertFalse(eh.record(self.store, mt5, healthy(float('nan')), now=1100))
        eh.record(self.store, mt5, healthy(10025), now=1090)
        s = eh.series(self.store, 'ALL', now=2000)
        self.assertEqual([p[1] for p in s['points']], [10010, 10025])
        self.assertEqual(s['currency'], 'USD')

    def test_range_filter_and_account_separation(self):
        eh.record(self.store, FakeMT5(login=1), healthy(1), now=10)
        eh.record(self.store, FakeMT5(login=2), healthy(2), now=200000)
        s = eh.series(self.store, '1D', now=200100)
        self.assertEqual([p[1] for p in s['points']], [2])  # only the current account, inside range

    def test_closed_trade_curve_is_cumulative_within_range(self):
        scope = eh.scope_of(FakeMT5())
        eh.record(self.store, FakeMT5(), healthy(1), now=100)
        with self.store.connect() as db:
            db.execute('CREATE TABLE trade_reviews (key TEXT PRIMARY KEY, account_scope TEXT, strategy TEXT, symbol TEXT, source_updated REAL, closed_epoch REAL, payload TEXT)')
            db.execute("INSERT OR REPLACE INTO meta VALUES ('trade_review_scope',?)", (json.dumps(scope),))
            for i, (t, v) in enumerate([(50, 5.0), (300, 10.0), (400, -4.0)]):
                db.execute('INSERT INTO trade_reviews VALUES (?,?,?,?,?,?,?)',
                           (str(i), scope, 'BBMA', 'XAUUSD', t, t, json.dumps(dict(realised=v, currency='USD', strategy='BBMA', symbol='XAUUSD'))))
        s = eh.series(self.store, '1D', now=200)  # start = negative -> all trades in range
        self.assertEqual([c[1] for c in s['closed']], [5.0, 15.0, 11.0])
        self.assertTrue(s['same_account'])

    def test_downsample_keeps_bounds(self):
        rows = [(i, float(i), 0.0) for i in range(5000)]
        out = eh._downsample(rows, 100)
        self.assertLessEqual(len(out), 101)
        self.assertEqual(out[-1][0], 4999)

    def test_rejects_unknown_range(self):
        with self.assertRaises(ValueError):
            eh.series(self.store, '5Y')


if __name__ == '__main__':
    unittest.main()
