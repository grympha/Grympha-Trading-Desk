"""Live Test engine writes a whole cycle in one transaction; one failing agent never blocks the rest."""
import tempfile
import unittest
from pathlib import Path

from dashboard_store import Store
from live_test import LiveTestEngine, CmsSsr


class BatchedCycle(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'db.sqlite3')
        self.eng = LiveTestEngine(self.store)
        self.ids = [self.eng.register(CmsSsr(), s, 'RUNNING').id for s in ('XAUUSD', 'EURUSD', 'GBPUSD')]

    def test_one_connection_per_cycle_and_everything_saved(self):
        def fake_run(la, market, now):
            la.state['seen'] = now
            self.eng._save_agent(la)
        self.eng._run_agent = fake_run
        opened = []
        real = self.store.connect
        def counting():
            opened.append(1)
            return real()
        self.store.connect = counting
        self.eng.run(market=None, now=123.0)
        self.assertEqual(len(opened), 2)                     # _sync read + the cycle's write transaction
        self.store.connect = real
        reloaded = LiveTestEngine(self.store)
        self.assertTrue(all(reloaded.agents[i].state.get('seen') == 123.0 for i in self.ids))

    def test_a_failing_agent_does_not_roll_back_the_others(self):
        def fake_run(la, market, now):
            if la.base_symbol == 'EURUSD':
                raise RuntimeError('broken agent')
            la.state['seen'] = now
            self.eng._save_agent(la)
        self.eng._run_agent = fake_run
        self.assertEqual(self.eng.run(market=None, now=7.0), 3)
        reloaded = LiveTestEngine(self.store)
        saved = {la.base_symbol: la.state.get('seen') for la in reloaded.agents.values()}
        self.assertEqual(saved, {'XAUUSD': 7.0, 'EURUSD': None, 'GBPUSD': 7.0})
        self.assertIn('broken agent', self.eng.agents[self.ids[1]].state['_run_note'])

    def test_outside_a_cycle_saves_still_commit_immediately(self):
        self.eng.set_status(self.ids[0], 'PAUSED')
        self.assertEqual(LiveTestEngine(self.store).agents[self.ids[0]].status, 'PAUSED')


if __name__ == '__main__':
    unittest.main()
