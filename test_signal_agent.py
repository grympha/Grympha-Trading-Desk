"""Signal Agent: grading rules, new-entry pickup, frozen grades, outcomes, alerts."""
import json
import tempfile
import unittest
from pathlib import Path

import quant_agent as qa
import signal_agent as sa
from dashboard_store import Store
from live_test import SCHEMA, VALID

NOW = 1_795_000_000.0
AID = 'LIVE_CONFLUENCE|XAUUSD|abc123'


def fwd(rs):
    return qa.summarise([dict(entry_time=NOW - 1000 * (len(rs) - i), pnl_r=r, data_quality_status=VALID)
                         for i, r in enumerate(rs)])


def back(verdict, trades=60, avg=0.2):
    return dict(verdict=verdict, trades=trades, avg_r=avg, net_r=trades * avg, win_rate=0.5)


class Grading(unittest.TestCase):
    def test_backtest_edge_with_quiet_live_test_is_a(self):
        self.assertEqual(sa.grade(back('EDGE (keep testing live)'), fwd([1, -1, 0.5]))[0], 'A')

    def test_live_test_contradicting_the_backtest_is_not_a(self):
        g, _ = sa.grade(back('EDGE (keep testing live)'), fwd([-1, -1, 0.5] * 4))
        self.assertNotEqual(g, 'A')

    def test_live_test_edge_alone_is_a(self):
        self.assertEqual(sa.grade(None, fwd([1, -0.5] * 20))[0], 'A')

    def test_losing_either_way_is_x(self):
        self.assertEqual(sa.grade(back('LOSING (switch off)', avg=-0.3), fwd([1, 1]))[0], 'X')
        self.assertEqual(sa.grade(back('EDGE (keep testing live)'), fwd([-1, 0.5] * 15))[0], 'X')

    def test_promising_and_unproven(self):
        self.assertEqual(sa.grade(back('TOO FEW TRADES', trades=12, avg=0.3), fwd([]))[0], 'B')
        self.assertEqual(sa.grade(back('NO CLEAR EDGE', trades=50, avg=0.01), fwd([]))[0], 'C')
        g, reason = sa.grade(None, fwd([1]))
        self.assertEqual(g, 'C')
        self.assertIn('Backtest: not run yet', reason)
        self.assertIn('Live Test: 1 trades', reason)


class Feed(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'db.sqlite3')
        with self.store.connect() as db:
            db.executescript(SCHEMA)
            db.execute('INSERT INTO live_test_agents VALUES (?,?,?,?,?,?,?,?)',
                       (AID, 'LIVE_CONFLUENCE', 'XAUUSD', 'abc123', json.dumps({}), 'RUNNING', '{}', NOW))
        self.alerts = []
        self.agent = sa.SignalAgent(self.store, alert=self.alerts.append)

    def pos(self, tid, entry_time, **kw):
        return dict(dict(trade_id=tid, agent_id=AID, strategy_id='LIVE_CONFLUENCE', symbol='XAUUSD', side='BUY',
                         timeframe='M15', entry_time=entry_time, entry_price=2000.0, initial_sl=1990.0,
                         initial_tp=2020.0, status='OPEN', unrealized_r=0.4, data_quality_status=VALID), **kw)

    def open_position(self, p):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO live_test_positions VALUES (?,?,?,?,?)',
                       (p['trade_id'], AID, 'OPEN', json.dumps(p), NOW))

    def close(self, p, r, at):
        p = dict(p, status='CLOSED', exit_time=at, exit_reason='TP' if r > 0 else 'SL', pnl_r=r)
        with self.store.connect() as db:
            db.execute('DELETE FROM live_test_positions WHERE trade_id=?', (p['trade_id'],))
            db.execute('INSERT OR REPLACE INTO live_test_trades VALUES (?,?,?,?)', (p['trade_id'], AID, at, json.dumps(p)))

    def history(self, rs):
        for i, r in enumerate(rs):
            self.close(self.pos(f'old{i}', NOW - 400_000 + i * 1000), r, NOW - 399_000 + i * 1000)

    def test_new_entry_is_graded_once_and_alerted_when_a(self):
        qa.save_results(self.store, [dict(strategy_id='LIVE_CONFLUENCE', symbol='XAUUSD', config_id='abc123',
                                          setup='', start=NOW - 30 * 86400, end=NOW - 3600, verdict='EDGE (keep testing live)',
                                          stats=qa.summarise([dict(entry_time=NOW - 9e5 + i, pnl_r=r, data_quality_status=VALID)
                                                              for i, r in enumerate([1, -0.5] * 20)]))], now=NOW - 3600)
        self.agent.update(now=NOW - 60)                      # first run: nothing yet
        self.open_position(self.pos('t1', NOW - 30))
        new = self.agent.update(now=NOW)
        self.assertEqual([(f['trade_id'], f['grade']) for f in new], [('t1', 'A')])
        self.assertIn('Backtest: 40 trades', new[0]['reason'])
        self.assertEqual(len(self.alerts), 1)
        self.assertIn('No order placed', sa.telegram_text(self.alerts[0]))
        self.assertEqual(self.agent.update(now=NOW + 10), [])        # never twice
        self.assertEqual(len(self.alerts), 1)

    def test_wide_spread_at_entry_downgrades_a_and_skips_the_alert(self):
        import market_scout
        qa.save_results(self.store, [dict(strategy_id='LIVE_CONFLUENCE', symbol='XAUUSD', config_id='abc123', setup='',
                                          start=0, end=1, verdict='EDGE (keep testing live)',
                                          stats=qa.summarise([dict(entry_time=i, pnl_r=r, data_quality_status=VALID)
                                                              for i, r in enumerate([1, -0.5] * 20)]))])
        market_scout.save(self.store, dict(generated_epoch=NOW - 600, rows=[dict(
            symbol='XAUUSD', point=0.01, typical_spread_points=20, typical_spread_by_hour={},
            volatility=dict(regime='ACTIVE'), trend=dict(label='MIXED'), cost_pct=0.01)]))
        self.agent.update(now=NOW - 60)
        self.open_position(self.pos('wide', NOW - 30, entry_spread=0.8))      # 4x the usual 0.20
        [row] = self.agent.update(now=NOW)
        self.assertEqual(row['grade'], 'B')
        self.assertIn('downgraded from A', row['reason'])
        self.assertEqual(row['market']['spread_x'], 4.0)
        self.assertEqual(self.alerts, [])

    def test_grade_is_frozen_and_outcome_follows_the_trade(self):
        self.history([-1] * 25)                                        # a losing Live Test record
        self.open_position(self.pos('t1', NOW - 30))
        [row] = self.agent.update(now=NOW)
        self.assertEqual(row['grade'], 'X')
        self.assertEqual(self.alerts, [])                              # only grade A alerts
        rep = sa.report(self.store, now=NOW)
        self.assertEqual(rep['feed'][0]['outcome']['status'], 'OPEN')
        self.close(self.pos('t1', NOW - 30), 2.0, NOW + 100)
        self.history([1] * 25)                                          # record improves later...
        rep = sa.report(self.store, now=NOW + 200)
        f = rep['feed'][0]
        self.assertEqual((f['grade'], f['outcome']['status'], f['outcome']['r']), ('X', 'CLOSED', 2.0))   # ...grade stays
        x = next(g for g in rep['by_grade'] if g['grade'] == 'X')
        self.assertEqual((x['signals'], x['closed'], x['avg_r']), (1, 1, 2.0))

    def test_fast_close_between_cycles_is_still_picked_up(self):
        self.agent.update(now=NOW - 60)
        p = self.pos('quick', NOW - 20)
        self.close(p, -1.0, NOW - 5)
        [row] = self.agent.update(now=NOW)
        self.assertEqual(row['trade_id'], 'quick')
        self.assertEqual(row['forward']['trades'], 0)                  # its own result is not its record

    def test_first_run_backfills_a_day_without_alerting(self):
        qa.save_results(self.store, [dict(strategy_id='LIVE_CONFLUENCE', symbol='XAUUSD', config_id='abc123', setup='',
                                          start=0, end=1, verdict='EDGE (keep testing live)',
                                          stats=qa.summarise([dict(entry_time=i, pnl_r=r, data_quality_status=VALID)
                                                              for i, r in enumerate([1, -0.5] * 20)]))])
        self.open_position(self.pos('old', NOW - 3 * 3600))
        self.open_position(self.pos('ancient', NOW - 3 * 86400))
        new = self.agent.update(now=NOW)
        self.assertEqual([f['trade_id'] for f in new], ['old'])
        self.assertEqual(self.alerts, [])                              # too old to announce

    def test_report_without_any_backtest(self):
        rep = sa.report(self.store, now=NOW)
        self.assertEqual((rep['feed'], rep['backtested_agents'], rep['last_backtest_epoch']), ([], 0, None))
        json.dumps(rep, allow_nan=False)


class QuantResults(unittest.TestCase):
    def test_round_trip_and_infinite_profit_factor(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'db.sqlite3')
        s = qa.summarise([dict(entry_time=i, pnl_r=1, data_quality_status=VALID) for i in range(3)])
        qa.save_results(store, [dict(strategy_id='S', symbol='X', config_id='c', setup='', start=0, end=1,
                                     verdict='TOO FEW TRADES', stats=s)], now=5)
        r = qa.load_results(store)['S|X|c']
        self.assertEqual((r['trades'], r['profit_factor'], r['verdict'], r['created']), (3, None, 'TOO FEW TRADES', 5))


if __name__ == '__main__':
    unittest.main()
