"""Signal calibration: grade scorecards, Brier score and daily snapshots (report only)."""
import json
import tempfile
import unittest
from pathlib import Path

import signal_calibration as sc
from dashboard_store import Store
from live_test import SCHEMA, VALID

NOW = 1_800_000_000.0
DAY = 86400


def feed(grade, wr=None, n=0, regime=None, entry=NOW - 3600, label='Agent'):
    f = dict(grade=grade, entry_time=entry, label=label, backtest=None,
             forward=dict(trades=n, win_rate=wr) if n else dict(trades=0, win_rate=None))
    if regime:
        f['market'] = dict(regime=regime)
    return f


class Maths(unittest.TestCase):
    def test_predicted_is_the_trade_weighted_record(self):
        f = dict(backtest=dict(trades=30, win_rate=0.5), forward=dict(trades=10, win_rate=0.1))
        self.assertAlmostEqual(sc.predicted(f), 0.4)
        self.assertIsNone(sc.predicted(dict(backtest=None, forward=dict(trades=4, win_rate=1.0))))   # under MIN_RECORD
        self.assertIsNone(sc.predicted(dict(backtest=None, forward=dict(trades=0, win_rate=None))))

    def test_brier_by_hand(self):
        pairs = [(0.8, 1), (0.2, 0), (0.5, 1), (0.5, 0)]
        b = sc.brier(pairs)
        self.assertAlmostEqual(b['brier'], (0.04 + 0.04 + 0.25 + 0.25) / 4)          # 0.145
        self.assertAlmostEqual(b['baseline'], 0.25)                                   # always 50 %
        self.assertAlmostEqual(b['skill'], round(1 - 0.145 / 0.25, 3))
        self.assertEqual(sc.brier([])['brier'], None)

    def test_grade_stats_and_range(self):
        s = sc.grade_stats([1.0, -1.0, 2.0, -1.0])
        self.assertEqual((s['closed'], s['wins'], s['win_rate'], s['avg_r'], s['net_r']), (4, 2, 0.5, 0.25, 1.0))
        self.assertLess(s['low'], 0.25); self.assertGreater(s['high'], 0.25)
        self.assertIsNone(sc.grade_stats([0.5])['low'])
        self.assertIsNone(sc.grade_stats([])['avg_r'])

    def test_reliability_buckets(self):
        rel = sc.reliability([(0.05, 0), (0.07, 1), (0.95, 1), (1.0, 1)])
        self.assertEqual([(b['low'], b['n'], b['actual']) for b in rel], [(0.0, 2, 0.5), (0.9, 2, 1.0)])

    def test_outlier_resistant_stats(self):
        s = sc.grade_stats([-1.0] * 9 + [45.0])                     # one +45R trade with a spread-sized stop
        self.assertEqual(s['avg_r'], 3.6)                            # plain average: looks like a great grade
        self.assertEqual(s['capped_avg_r'], round((-9 + sc.R_CAP) / 10, 3))   # -0.4: still losing
        self.assertEqual(s['median_r'], -1.0)
        self.assertEqual(s['outliers'], 1)
        self.assertEqual(sc.grade_stats([1.0, 2.0])['median_r'], 1.5)

    def test_grade_ranking(self):
        mk = lambda g, n, r, plain=None: dict(grade=g, closed=n, capped_avg_r=r, avg_r=r if plain is None else plain, outliers=0)
        self.assertEqual(sc.grade_ranking([mk('A', 30, .2), mk('B', 30, 0), mk('C', 30, -.3), mk('X', 30, -.6)])['status'], 'IN_ORDER')
        out = sc.grade_ranking([mk('A', 30, -.5), mk('B', 30, 0), mk('C', 5, 1), mk('X', 30, -.6)])
        self.assertEqual(out['status'], 'OUT_OF_ORDER')
        self.assertIn('A (-0.50R) is not above B', out['text'])                    # C ignored: too few
        self.assertEqual(sc.grade_ranking([mk('A', 30, .2), mk('B', 3, 0)])['status'], 'TOO_FEW')

    def test_one_outlier_cannot_flip_the_ranking(self):
        mk = lambda g, capped, plain, n=0: dict(grade=g, closed=30, capped_avg_r=capped, avg_r=plain, outliers=n)
        out = sc.grade_ranking([mk('A', -.05, -.09), mk('B', -.2, .06, 1), mk('C', -.45, -.45), mk('X', -.7, -.7)])
        self.assertEqual((out['status'], out['plain_status']), ('IN_ORDER', 'OUT_OF_ORDER'))
        self.assertIn('capped at ±5R', out['text'])
        self.assertIn('The plain average disagrees: A (-0.09R) is not above B (+0.06R) (1 trade beyond ±5R)', out['text'])

    def test_outliers_are_listed_and_suggested(self):
        rows = ([(dict(feed('B', 0.3, 20), symbol='BTCUSD', label='Inverse FVG'), r) for r in (45.0, 31.7, -1.0)]
                + [(dict(feed('C', 0.3, 20), symbol='XAUUSD', label='Other'), -1.0)])
        card = sc.scorecards(rows, NOW)['all']
        self.assertEqual(card['outliers'], [dict(label='Inverse FVG', symbol='BTCUSD', count=2, largest_r=45.0, grades=['B'])])
        self.assertIn('Inverse FVG · BTCUSD: 2 results beyond ±5R (largest +45.0R)', ' '.join(card['suggestions']))


class Scorecard(unittest.TestCase):
    def test_suggestions_flag_a_losing_b_and_optimistic_records(self):
        rows = [(feed('B', 0.55, 40), -1.0) for _ in range(30)] + [(feed('B', 0.55, 40), 0.1) for _ in range(5)]
        card = sc.summarise(rows)
        tips = ' '.join(sc.suggestions(card))
        self.assertIn('B grades have averaged', tips)
        self.assertIn('too optimistic', tips)

    def test_windows_and_splits(self):
        rows = ([(feed('A', 0.5, 20, 'TRENDING'), 1.0)] * 3
                + [(feed('X', 0.1, 20, None, entry=NOW - 20 * DAY, label='Other'), -1.0)] * 2)
        cards = sc.scorecards(rows, NOW)
        self.assertEqual((cards['7d']['closed'], cards['30d']['closed'], cards['all']['closed']), (3, 5, 5))
        self.assertEqual({g['name']: g['closed'] for g in cards['all']['by_regime']}, {'TRENDING': 3, 'UNKNOWN': 2})
        self.assertEqual(cards['all']['by_strategy'][0]['name'], 'Agent')
        self.assertEqual(cards['all']['by_regime'][0]['grades']['A']['avg_r'], 1.0)


class Store_(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'db.sqlite3')
        with self.store.connect() as db:
            db.executescript(SCHEMA)
            db.execute('CREATE TABLE IF NOT EXISTS signal_feed(trade_id TEXT PRIMARY KEY, epoch REAL, agent_id TEXT, grade TEXT, payload TEXT)')
            for i, (g, r, q) in enumerate([('A', 1.5, VALID), ('X', -1.0, VALID), ('C', 2.0, 'DATA_GAP')]):
                f = dict(feed(g, 0.4, 10), trade_id=f't{i}')
                db.execute('INSERT INTO signal_feed VALUES (?,?,?,?,?)', (f't{i}', NOW - 3600, 'ag', g, json.dumps(f)))
                db.execute('INSERT INTO live_test_trades VALUES (?,?,?,?)',
                           (f't{i}', 'ag', NOW - 60, json.dumps(dict(trade_id=f't{i}', pnl_r=r, data_quality_status=q))))
            f = dict(feed('B'), trade_id='open')                                       # still open: not scored
            db.execute('INSERT INTO signal_feed VALUES (?,?,?,?,?)', ('open', NOW - 60, 'ag', 'B', json.dumps(f)))

    def test_only_closed_valid_trades_are_scored(self):
        rows = sc.load_rows(self.store)
        self.assertEqual(sorted((f['grade'], r) for f, r in rows), [('A', 1.5), ('X', -1.0)])
        rep = sc.report(self.store, now=NOW)
        self.assertTrue(rep['simulation'])
        self.assertEqual(rep['windows']['all']['brier']['scored'], 2)
        json.dumps(rep, allow_nan=False)

    def test_one_snapshot_per_malaysia_day(self):
        snap = sc.DailySnapshot(self.store, clock=lambda: self.now)
        self.now = NOW
        self.assertTrue(snap.step())
        self.assertFalse(snap.step())                                       # same day: cached, no write
        self.assertFalse(sc.snapshot(self.store, NOW + 60))                 # same day after a restart
        self.now = NOW + DAY
        self.assertTrue(snap.step())
        hist = sc.history(self.store)
        self.assertEqual([h['day'] for h in hist], [sc.myt_day(NOW + DAY), sc.myt_day(NOW)])
        self.assertEqual(hist[0]['all']['closed'], 2)

    def test_malaysia_day_boundary(self):
        midnight_myt = 1_799_971_200.0 - 8 * 3600                           # 00:00 MYT
        self.assertNotEqual(sc.myt_day(midnight_myt - 1), sc.myt_day(midnight_myt))


if __name__ == '__main__':
    unittest.main()
