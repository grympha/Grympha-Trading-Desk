"""Live Test agents trading live: the switch, entry hand-over and exit mirroring. Fake MT5 only."""
import json
import time
import unittest
from types import SimpleNamespace as N

import agent_execution as ae
import test_demo_execution as demo_tests          # also switches the weekend rule off (real-time fake clock)
from demo_execution import DemoExecutor, MAGIC
from live_test import LiveTestEngine, build_agent
from trade_labels import entry_comment


class Broker(demo_tests.FakeMT5):
    """Fake MT5 that can modify SL and close (part of) a position."""
    TRADE_ACTION_SLTP = 6

    def positions_get(self, ticket=None):
        return tuple(p for p in self.positions if ticket is None or p.ticket == ticket)

    def order_send(self, r):
        if r['action'] == self.TRADE_ACTION_SLTP:
            self.sent.append(dict(r))
            for p in self.positions:
                p.sl = r['sl']
            return N(retcode=10009)
        if 'position' not in r:
            return super().order_send(r)
        self.sent.append(dict(r))
        p = next(p for p in self.positions if p.ticket == r['position'])
        if r['volume'] < p.volume - 1e-9:
            p.volume = round(p.volume - r['volume'], 8)
        else:
            self.positions = [x for x in self.positions if x.ticket != r['position']]
            self.deals.append(N(ticket=1002, order=501, position_id=700, symbol=r['symbol'], magic=r['magic'], entry=1,
                                profit=1.0, commission=-.02, swap=0., fee=0., reason=3, price=r['price'], time=int(time.time())))
        return N(retcode=10009, order=501, deal=1002, volume=r['volume'], price=r['price'])


class Base(unittest.TestCase):
    setUp_demo = demo_tests.Demo.setUp
    tearDown = demo_tests.Demo.tearDown

    def setUp(self):
        self.setUp_demo()
        self.mt5 = Broker()
        LiveTestEngine(self.store)                                     # creates the Live Test tables
        self.agent_id = self.add_agent('CMS_SSR')
        self.live_bbma = self.add_agent('LIVE_BBMA', {'setup': 'CSA_REENTRY'})

    def add_agent(self, sid, config=None, status='RUNNING'):
        a = build_agent(sid, config or {})
        aid = f'{sid}|AUDUSD|{a.config_id}'
        with self.store.connect() as db:
            db.execute('INSERT INTO live_test_agents VALUES (?,?,?,?,?,?,?,?)',
                       (aid, sid, 'AUDUSD', a.config_id, json.dumps(a.config), status, '{}', 0))
        return aid

    def sim(self, trade_id='t1', **kw):
        pos = dict(trade_id=trade_id, agent_id=self.agent_id, strategy_id='CMS_SSR', symbol='AUDUSD', side='BUY',
                   entry_price=1.1, initial_sl=1.099, initial_tp=1.102, stop=1.099, entry_time=time.time(), status='OPEN')
        pos.update(kw)
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO live_test_positions VALUES (?,?,?,?,?)',
                       (pos['trade_id'], pos['agent_id'], 'OPEN', json.dumps(pos), time.time()))
        return pos

    def close_sim(self, pos, reason='TRAIL_STOP'):
        with self.store.connect() as db:
            db.execute('DELETE FROM live_test_positions WHERE trade_id=?', (pos['trade_id'],))
            db.execute('INSERT INTO live_test_trades VALUES (?,?,?,?)',
                       (pos['trade_id'], pos['agent_id'], time.time(), json.dumps(dict(pos, status='CLOSED', exit_reason=reason))))

    def rows(self):
        return self.store.snapshot()['demo_orders']


class Switch(Base):
    def test_on_off_and_validation(self):
        with self.assertRaises(ValueError):
            ae.set_enabled(self.store, 'NOPE|AUDUSD|x', True)
        with self.assertRaises(ValueError):                            # already trades live as BBMA
            ae.set_enabled(self.store, self.live_bbma, True)
        with self.assertRaises(ValueError):
            ae.set_enabled(self.store, self.agent_id, 'yes')
        ae.set_enabled(self.store, self.agent_id, True)
        v = ae.view(self.store)
        self.assertEqual([(r['agent_id'], r['symbol'], r['live_test_status']) for r in v], [(self.agent_id, 'AUDUSD', 'RUNNING')])
        self.assertTrue(v[0]['label'])
        self.assertIn(self.agent_id, self.store.snapshot()['meta']['agent_execution'])   # Settings reads it here
        ae.set_enabled(self.store, self.agent_id, False)
        self.assertEqual(ae.view(self.store), [])

    def test_ledger_names_and_magic(self):
        s = ae.strategy_of(self.agent_id)
        self.assertTrue(ae.is_agent(s) and s.startswith('AGENT:'))
        self.assertEqual(MAGIC[s], 816004)
        self.assertEqual(MAGIC['CONFLUENCE'], 816002)
        tag = entry_comment(s, 'AMD_PO3_A1_VERY_LONG_NAME_HERE', None, 'acct', 'AGENT|t1')
        self.assertTrue(tag.startswith('LT ') and len(tag) <= 31)


class Entries(Base):
    def executor(self, lot=.01):
        return DemoExecutor(self.mt5, self.store, self.cfg, lot)

    def test_fresh_entry_of_an_enabled_agent_is_traded_once(self):
        ex = self.executor()
        self.sim()
        self.assertEqual(ae.step(ex), 0)                               # switched off: nothing
        self.assertEqual(self.mt5.sent, [])
        ae.set_enabled(self.store, self.agent_id, True)
        self.assertEqual(ae.step(ex), 1)
        req = self.mt5.sent[0]
        self.assertEqual((req['action'], req['type'], req['sl'], req['tp'], req['magic']), (1, 0, 1.099, 1.102, 816004))
        self.assertTrue(req['comment'].startswith('LT '))
        ae.step(ex); ex.refresh(); ae.step(ex)
        self.assertEqual(len(self.mt5.sent), 1)                        # never a second entry
        row = self.rows()[0]
        self.assertTrue(row['strategy'].startswith('LT · '))
        self.assertEqual((row['status'], row['agent_id'], row['trade_id']), ('OPEN', self.agent_id, 't1'))

    def test_late_or_unknown_entries_are_not_chased(self):
        ex = self.executor()
        ae.set_enabled(self.store, self.agent_id, True)
        self.sim(entry_time=time.time() - 300)                         # opened 5 minutes ago
        self.assertEqual(ae.step(ex), 0)
        ex.submit_agent(dict(strategy='BBMA', key='x'))                # not an agent signal: ignored
        self.assertEqual(self.mt5.sent, [])

    def test_price_moved_too_far_is_blocked(self):
        ex = self.executor()
        ae.set_enabled(self.store, self.agent_id, True)
        self.sim(entry_price=1.0990 + .00001, initial_sl=1.0980)       # simulated fill far below the live ask
        ae.step(ex)
        self.assertEqual(self.mt5.sent, [])
        self.assertEqual(self.rows()[0]['status'], 'BLOCKED')

    def test_agent_without_take_profit_gets_a_20r_safety_cap(self):
        ex = self.executor()
        ae.set_enabled(self.store, self.agent_id, True)
        self.sim(initial_tp=None)
        ae.step(ex)
        self.assertAlmostEqual(self.mt5.sent[0]['tp'], 1.12)
        self.assertIn('20R safety cap', self.rows()[0]['note'])

    def test_switched_off_before_send_is_blocked(self):
        ex = self.executor()
        ae.set_enabled(self.store, self.agent_id, True)
        self.mt5.after_check = lambda: ae.set_enabled(self.store, self.agent_id, False)
        self.sim(); ae.step(ex)
        self.assertEqual(self.mt5.sent, [])
        self.assertEqual(self.rows()[0]['status'], 'BLOCKED')


class Mirror(Base):
    executor = Entries.executor

    def opened(self, lot=.01, **kw):
        ex = self.executor(lot)
        ae.set_enabled(self.store, self.agent_id, True)
        pos = self.sim(**kw)
        ae.step(ex); ex.refresh()
        self.assertEqual(self.rows()[0]['status'], 'OPEN')
        return ex, pos

    def test_stop_move_is_copied_tighter_only(self):
        ex, pos = self.opened()
        self.sim(stop=1.0995)                                          # simulated break-even / trail
        ex.refresh()
        sltp = [r for r in self.mt5.sent if r['action'] == 6]
        self.assertEqual([r['sl'] for r in sltp], [1.0995])
        self.sim(stop=1.0993)                                          # looser than the broker SL: ignored
        ex.refresh(); ex.refresh()
        self.assertEqual(len([r for r in self.mt5.sent if r['action'] == 6]), 1)

    def test_partial_then_close_follow_the_simulation(self):
        ex, pos = self.opened(lot=.02)
        self.sim(partial_done=True, partial_fraction=.5)
        ex.refresh()
        part = self.mt5.sent[-1]
        self.assertEqual((part['position'], part['volume'], part['type']), (701, .01, 1))
        self.assertIn('mirror partial', part['comment'])
        ex.refresh()
        self.assertEqual(sum(1 for r in self.mt5.sent if 'position' in r), 1)     # partial copied once
        self.close_sim(pos)
        ex.refresh()
        close = self.mt5.sent[-1]
        self.assertEqual((close['position'], close['volume']), (701, .01))
        self.assertIn('mirror close', close['comment'])
        ex.refresh()                                                   # position gone: closed from broker deals
        self.assertEqual(self.rows()[0]['status'], 'CLOSED')
        self.assertEqual(sum(1 for r in self.mt5.sent if 'mirror close' in r.get('comment', '')), 1)

    def test_switching_off_keeps_mirroring_the_open_trade(self):
        ex, pos = self.opened()
        ae.set_enabled(self.store, self.agent_id, False)
        self.close_sim(pos, 'SESSION_END')
        ex.refresh()
        self.assertIn('mirror close', self.mt5.sent[-1]['comment'])

    def test_unconfirmed_close_is_never_resent(self):
        ex, pos = self.opened()
        self.close_sim(pos)
        self.mt5.none_reply = True
        ex.refresh(); ex.refresh()
        self.assertEqual(sum(1 for r in self.mt5.sent if 'mirror close' in r.get('comment', '')), 1)


class Endpoints(Base):
    def test_switch_list_and_live_test_flag(self):
        import threading
        import urllib.error
        import urllib.request
        from dashboard_server import build_server
        server = build_server(self.store, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        def post(body):
            h = {'Content-Type': 'application/json', 'X-Dashboard-Request': 'controls', 'Origin': base,
                 'Host': base.split('//')[1]}
            return urllib.request.urlopen(urllib.request.Request(base + '/api/agent-execution', json.dumps(body).encode(), h, method='POST'))
        try:
            with post({'agent_id': self.agent_id, 'on': True}) as r:
                self.assertEqual([a['agent_id'] for a in json.load(r)['agents']], [self.agent_id])
            with urllib.request.urlopen(base + '/api/agent-execution') as r:
                self.assertEqual(json.load(r)['agents'][0]['live_test_status'], 'RUNNING')
            with urllib.request.urlopen(base + '/api/live-test') as r:
                agents = {a['id']: a for a in json.load(r)['agents']}
            self.assertTrue(agents[self.agent_id]['trade_live'])
            self.assertFalse(agents[self.live_bbma]['trade_live_allowed'])
            with self.assertRaises(urllib.error.HTTPError) as bad:
                post({'agent_id': self.live_bbma, 'on': True})
            self.assertEqual(bad.exception.code, 400)
            with post({'agent_id': self.agent_id, 'on': False}) as r:
                self.assertEqual(json.load(r)['agents'], [])
        finally:
            server.shutdown(); server.server_close()


class Guard(unittest.TestCase):
    def test_live_test_modules_never_import_the_bridge(self):
        from pathlib import Path
        root = Path(__file__).parent
        for name in root.glob('live_test*.py'):
            self.assertNotIn('agent_execution', name.read_text(encoding='utf-8'), name.name)


if __name__ == '__main__':
    unittest.main()
