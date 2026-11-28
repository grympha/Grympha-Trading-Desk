"""Live-Test-only Confluence agents at fixed minimum scores (3/5 and 5/5) beside the live ones."""
import tempfile
import unittest
from pathlib import Path

from dashboard_store import Store
from live_test import LiveTestEngine, build_agent, confluence_score_agents, ensure_default_agents
from live_test_confluence import ConfluenceScore, LiveConfluence


class Agents(unittest.TestCase):
    def test_fixed_scores_with_the_live_analysis(self):
        cfg = {'confluence': {'sl_atr': 1.7, 'min_score': 4}}
        three, five = confluence_score_agents(cfg)
        self.assertEqual((three.strategy_id, three.config['min_score'], five.config['min_score']), ('CONFLUENCE_SCORE', 3, 5))
        self.assertEqual(three.label(), 'Confluence test · score 3/5+')
        self.assertEqual(three.config['sl_atr'], 1.7)                                 # config.json analysis values
        live = LiveConfluence.from_live_config(cfg)
        self.assertEqual({k: v for k, v in three.config.items() if k != 'min_score'},
                         {k: v for k, v in live.config.items() if k != 'min_score'})  # same analysis + management
        self.assertIs(type(build_agent('CONFLUENCE_SCORE', three.config)), ConfluenceScore)
        self.assertEqual(build_agent('CONFLUENCE_SCORE', three.config).config_id, three.config_id)
        self.assertEqual([a.config['min_score'] for a in confluence_score_agents({'confluence_test': {'scores': [2, 9]}})], [2])

    def test_registered_on_every_pair_following_the_live_agent(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        eng = LiveTestEngine(Store(Path(tmp.name) / 'db.sqlite3'))
        cfg = {'symbols': ['XAUUSD', 'EURUSD']}
        eng.register(LiveConfluence.from_live_config(cfg), 'XAUUSD', 'RUNNING')       # live Confluence runs on XAUUSD
        ensure_default_agents(eng, cfg)
        cs = {(la.base_symbol, la.agent.config['min_score']): la.status for la in eng.agents.values()
              if la.agent.strategy_id == 'CONFLUENCE_SCORE'}
        self.assertEqual(cs, {('XAUUSD', 3): 'RUNNING', ('XAUUSD', 5): 'RUNNING',
                              ('EURUSD', 3): 'PAUSED', ('EURUSD', 5): 'PAUSED'})
        live = [la for la in eng.agents.values() if la.agent.strategy_id == 'LIVE_CONFLUENCE' and la.status == 'RUNNING']
        self.assertTrue(live)                                                         # the live agents are untouched
        ensure_default_agents(eng, cfg)                                               # idempotent
        self.assertEqual(len([1 for la in eng.agents.values() if la.agent.strategy_id == 'CONFLUENCE_SCORE']), 4)


if __name__ == '__main__':
    unittest.main()
