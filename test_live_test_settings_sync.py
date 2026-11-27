"""Live-strategy Live Test agents follow the LIVE dashboard settings, with a new agent version per change."""
import json
import tempfile
import unittest
from pathlib import Path

from dashboard_store import Store
from live_test import (LiveTestEngine, Simulator, ensure_default_agents, live_strategy_agents,
                       sync_live_strategy_agents, VALID)
from live_test_bbma import dashboard_settings, management_extra
from market_data import Quote

CFG = {'symbols': ['XAUUSD', 'EURUSD'], 'confluence': {'symbols': ['XAUUSD', 'EURUSD'], 'min_score': 4},
       'orb': {'symbols': ['XAUUSD']}, 'bar_timestamp_offset_hours': 2}
LIVE_TODAY = dict(partial_enabled=True, partial_percent=50, breakeven_enabled=True, breakeven_offset_points=50,
                  trailing_enabled=True, trailing_start_r=1, trailing_distance_r=1)


def set_meta(store, key, value):
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'db.sqlite3')
        self.eng = LiveTestEngine(self.store)

    def live(self, status=None):
        return [la for la in self.eng.agents.values() if la.agent.strategy_id.startswith('LIVE_')
                and (status is None or la.status == status)]


class SettingsMapping(Base):
    def test_live_settings_today(self):
        set_meta(self.store, 'trade_controls', LIVE_TODAY)
        set_meta(self.store, 'bbma_tp_mode', 'structural')
        s = dashboard_settings(self.store, CFG)
        self.assertEqual(s['management'], dict(partial_fraction=0.5, be_offset_points=50,
                                               trail_activation_r=1.0, trail_distance_r=1.0))
        self.assertEqual(s['bbma'], dict(tp_mode='structural', structural_runner=True))
        self.assertEqual(s['confluence'], dict(min_score=4))

    def test_switched_off_management(self):
        set_meta(self.store, 'trade_controls', dict(LIVE_TODAY, partial_enabled=False, trailing_enabled=False))
        s = dashboard_settings(self.store, CFG)
        self.assertEqual(s['management'], dict(partial_fraction=0.0, be_offset_points=50,
                                               trail_activation_r=None, trail_distance_r=None))
        self.assertFalse(s['bbma']['structural_runner'])        # live: no runner without partials

    def test_fresh_install_has_live_defaults(self):
        s = dashboard_settings(self.store, CFG)                  # nothing saved: live management is off
        self.assertEqual(s['management']['partial_fraction'], 0.0)
        self.assertIsNone(s['management']['be_offset_points'])
        self.assertEqual(s['bbma']['tp_mode'], 'existing')

    def test_strategy_settings_reach_the_agents(self):
        set_meta(self.store, 'strategy_settings', {
            'bbma': {'csa_reentry': True, 'momentum_reentry': True, 'mhv': False},
            'confluence': {'min_score': 5},
            'orb': {'trade': True, 'skip_weekdays': [4], 'skip_months': [], 'signals_per_day': 2,
                    'sl_multiplier': 0.8, 'buy_sl_buffer_points': 40, 'sell_sl_buffer_points': 90,
                    'tp_mode': 'dynamic', 'fixed_rr': 2.0}})
        agents = {(a.strategy_id, b, a.config.get('setup')): a for a, b in live_strategy_agents(CFG, self.store)}
        self.assertEqual(agents[('LIVE_CONFLUENCE', 'EURUSD', None)].config['min_score'], 5)
        orb = agents[('LIVE_ORB', 'XAUUSD', None)]
        self.assertEqual((orb.config['skip_weekdays'], orb.config['signals_per_day'], orb.config['tp_mode']),
                         ([4], 2, 'dynamic'))
        self.assertEqual(orb.core_config('XAUUSD')['sl_multiplier'], 0.8)
        # BBMA keeps testing all three setups even when one is switched off live
        self.assertIn(('LIVE_BBMA', 'XAUUSD', 'MHV_REVERSAL'), agents)

    def test_a_pair_score_changes_only_that_pairs_agent(self):
        base = lambda pairs: {'bbma': {'csa_reentry': True, 'momentum_reentry': True, 'mhv': False},
                              'confluence': {'min_score': 4, 'pair_scores': pairs}, 'orb': {'trade': False}}
        set_meta(self.store, 'strategy_settings', base({}))
        before = {b: a.config_id for a, b in live_strategy_agents(CFG, self.store) if a.strategy_id == 'LIVE_CONFLUENCE'}
        set_meta(self.store, 'strategy_settings', base({'XAUUSD': 3}))
        after = {b: a for a, b in live_strategy_agents(CFG, self.store) if a.strategy_id == 'LIVE_CONFLUENCE'}
        self.assertEqual((after['XAUUSD'].config['min_score'], after['EURUSD'].config['min_score']), (3, 4))
        self.assertNotEqual(after['XAUUSD'].config_id, before['XAUUSD'])
        self.assertEqual(after['EURUSD'].config_id, before['EURUSD'])     # untouched pair keeps its agent


class ManagementFields(unittest.TestCase):
    BASE = dict(partial_fraction=0.5, be_offset_points=50, trail_activation_r=1.0, trail_distance_r=1.0)

    def test_all_on(self):
        ex = management_extra(self.BASE, 101.0, single_target=False)
        self.assertEqual((ex['partial_price'], ex['partial_fraction'], ex['be_offset_points']), (101.0, 0.5, 50))
        self.assertTrue(ex['be_on_partial'])
        self.assertEqual(ex['trail_activation_r'], 1.0)

    def test_break_even_without_partial(self):
        ex = management_extra(dict(self.BASE, partial_fraction=0.0), 101.0, single_target=False)
        self.assertEqual((ex['partial_price'], ex['partial_fraction']), (101.0, 0.0))
        self.assertTrue(ex['be_on_partial'])

    def test_everything_off(self):
        ex = management_extra(dict(partial_fraction=0.0, be_offset_points=None, trail_activation_r=None,
                                   trail_distance_r=None), 101.0, single_target=False)
        self.assertEqual(ex, {})

    def test_single_target_never_partials(self):
        ex = management_extra(self.BASE, 101.0, single_target=True)
        self.assertNotIn('partial_price', ex)

    def test_break_even_only_trade_keeps_the_full_position(self):
        pos = dict(side='BUY', symbol='XAUUSD', entry_time=0, entry_price=100.0, initial_sl=99.0, initial_tp=103.0,
                   stop=99.0, risk=1.0, data_quality_status=VALID, last_sample_epoch=1.8e9, trade_id='t', agent_id='a',
                   **management_extra(dict(partial_fraction=0.0, be_offset_points=0), 101.0, False))
        q = lambda bid, t: Quote('XAUUSD', bid, bid + 0.01, t, 0.0, 0.01, 2)
        pos, _ = Simulator.monitor(pos, q(101.0, 1.8e9 + 10), 1.8e9 + 10, 120)
        self.assertAlmostEqual(pos['stop'], 100.0)              # break-even at TP1
        pos, closed = Simulator.monitor(pos, q(100.0, 1.8e9 + 20), 1.8e9 + 20, 120)
        self.assertTrue(closed)
        self.assertAlmostEqual(pos['pnl_r'], 0.0)               # no partial was banked


class VersionSync(Base):
    def start_all(self):
        for la in self.live():
            self.eng.set_status(la.id, 'RUNNING')

    def test_first_registration_is_paused_and_complete(self):
        set_meta(self.store, 'trade_controls', LIVE_TODAY)
        ensure_default_agents(self.eng, CFG)
        live = self.live()
        self.assertEqual(len(live), 2 * 3 + 2 + 1)              # BBMA 3 setups x 2 pairs, Confluence x 2, ORB
        self.assertTrue(all(la.status == 'PAUSED' for la in live))

    def test_unchanged_settings_change_nothing(self):
        set_meta(self.store, 'trade_controls', LIVE_TODAY)
        ensure_default_agents(self.eng, CFG)
        self.start_all()
        self.assertEqual(sync_live_strategy_agents(self.eng, CFG), [])
        self.assertEqual(len(self.live()), 9)

    def test_a_settings_change_hands_over_to_a_new_version(self):
        set_meta(self.store, 'trade_controls', LIVE_TODAY)
        ensure_default_agents(self.eng, CFG)
        self.start_all()
        old = {la.id for la in self.live()}
        set_meta(self.store, 'trade_controls', dict(LIVE_TODAY, breakeven_offset_points=20))
        changed = sync_live_strategy_agents(self.eng, CFG)
        self.assertEqual(len(changed), 18)                      # 9 new versions + 9 paused
        running = self.live('RUNNING')
        self.assertEqual(len(running), 9)
        self.assertTrue(all(la.id not in old for la in running))
        self.assertTrue(all(la.agent.config['be_offset_points'] == 20 for la in running))
        self.assertTrue(all(self.eng.agents[i].status == 'PAUSED' for i in old))   # kept, with their trades
        # the new versions survive an engine reload from the database
        self.assertEqual(len([la for la in LiveTestEngine(self.store).agents.values()
                              if la.agent.strategy_id.startswith('LIVE_') and la.status == 'RUNNING']), 9)

    def test_only_affected_strategies_get_new_versions(self):
        settings = lambda score: {
            'bbma': {'csa_reentry': True, 'momentum_reentry': True, 'mhv': True},
            'confluence': {'min_score': score},
            'orb': {'trade': True, 'skip_weekdays': [2, 3], 'skip_months': [12], 'signals_per_day': 1,
                    'sl_multiplier': 0.9, 'buy_sl_buffer_points': 60, 'sell_sl_buffer_points': 100,
                    'tp_mode': 'fixed', 'fixed_rr': 2.0}}
        set_meta(self.store, 'trade_controls', LIVE_TODAY)
        set_meta(self.store, 'strategy_settings', settings(4))
        ensure_default_agents(self.eng, CFG)
        self.start_all()
        set_meta(self.store, 'strategy_settings', settings(5))       # only the Confluence score changes
        changed = sync_live_strategy_agents(self.eng, CFG)
        self.assertEqual({cid.split('|')[0] for cid in changed}, {'LIVE_CONFLUENCE'})
        self.assertEqual(len(changed), 4)                       # 2 new Confluence versions + 2 paused

    def test_a_paused_slot_stays_paused(self):
        set_meta(self.store, 'trade_controls', LIVE_TODAY)
        ensure_default_agents(self.eng, CFG)                    # all paused
        set_meta(self.store, 'trade_controls', dict(LIVE_TODAY, trailing_enabled=False))
        sync_live_strategy_agents(self.eng, CFG)
        self.assertEqual(self.live('RUNNING'), [])

    def test_restarting_an_old_version_is_undone(self):
        set_meta(self.store, 'trade_controls', LIVE_TODAY)
        ensure_default_agents(self.eng, CFG)
        old = [la.id for la in self.live()]
        set_meta(self.store, 'trade_controls', dict(LIVE_TODAY, partial_percent=30))
        sync_live_strategy_agents(self.eng, CFG)
        for la in self.live():                                  # dashboard "Start all"
            self.eng.set_status(la.id, 'RUNNING')
        sync_live_strategy_agents(self.eng, CFG)
        self.assertTrue(all(self.eng.agents[i].status == 'PAUSED' for i in old))

    def test_a_new_pair_follows_its_other_agents(self):
        from unittest import mock
        set_meta(self.store, 'trade_controls', LIVE_TODAY)
        cfg = dict(CFG, live_test_extra_symbols=['GBPAUD', 'EURNZD'])
        with mock.patch('live_test.sync_live_strategy_agents', lambda *a: []):
            ensure_default_agents(self.eng, cfg)                # the pairs' other agents only
        for la in list(self.eng.agents.values()):
            if la.base_symbol == 'GBPAUD':
                self.eng.set_status(la.id, 'RUNNING')           # started; EURNZD's stay paused
        sync_live_strategy_agents(self.eng, cfg)
        status = lambda sym: {la.status for la in self.live() if la.base_symbol == sym}
        self.assertEqual(status('GBPAUD'), {'RUNNING'})
        self.assertEqual(status('EURNZD'), {'PAUSED'})
        self.assertEqual(status('XAUUSD'), {'PAUSED'})          # existing behaviour unchanged

    def test_a_broken_settings_read_never_raises(self):
        class Broken:
            def connect(self):
                raise OSError('database locked')
        eng = LiveTestEngine.__new__(LiveTestEngine)
        eng.store, eng.agents = Broken(), {}
        self.assertEqual(sync_live_strategy_agents(eng, CFG), [])


if __name__ == '__main__':
    unittest.main()
