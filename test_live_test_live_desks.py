"""Live BBMA / Live Confluence Live Test agents: registration, live-matching execution, partial close.

The strategy analysis itself is the live code (bbma_bot.assess, confluence_strategy.evaluate);
these tests pin how the agents call it and how its plans are executed in the simulator."""
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import patch

import live_test_bbma
import live_test_confluence
from dashboard_store import Store
from live_test import LiveTestEngine, Simulator, ensure_default_agents, VALID
from live_test_bbma import LiveBBMA, SETUP_LABELS
from live_test_confluence import LiveConfluence
from lt_testkit import MultiTFMT5
from market_data import Bar, Quote, ReadOnlyMarketData

POINT, DIGITS = 0.01, 2
NOW = datetime(2026, 11, 25, 10, 2, 30, tzinfo=timezone.utc).timestamp()   # Wednesday, market open


def q(bid, ask, t=NOW):
    return Quote('XAUUSD', bid, ask, t, 0.0, POINT, DIGITS)


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


M5 = [Bar(NOW - 330, 1, 1, 1, 1)]          # latest closed M5 candle (only .time is read here)


def bbma_plan(side='BUY', entry=2000.0, sl=1990.0, tp1=2010.0, tp2=2025.0, single=False, expires=NOW + 600):
    return {'entry_plan': dict(id=f'MOMENTUM_REENTRY:1:2:{side}', side=side, setup='MOMENTUM_REENTRY',
                               entry=entry, stop_loss=sl, tp1=tp1, tp2=tp2, expires_utc=iso(expires),
                               structural_tp=dict(single_target=single))}


class Registration(unittest.TestCase):
    def engine(self, cfg):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        eng = LiveTestEngine(Store(Path(tmp.name) / 'db.sqlite3'))
        ensure_default_agents(eng, cfg)
        return eng

    def test_one_bbma_agent_per_setup_and_pair_one_confluence_per_pair(self):
        cfg = {'symbols': ['XAUUSD', 'EURUSD', 'GBPJPY'], 'min_bb_width_atr': 2.5,
               'confluence': {'symbols': ['XAUUSD', 'EURUSD'], 'min_score': 5}}
        eng = self.engine(cfg)
        bbma = [la for la in eng.agents.values() if la.agent.strategy_id == 'LIVE_BBMA']
        conf = [la for la in eng.agents.values() if la.agent.strategy_id == 'LIVE_CONFLUENCE']
        self.assertEqual({(la.base_symbol, la.agent.config['setup']) for la in bbma},
                         {(s, k) for s in ('XAUUSD', 'EURUSD', 'GBPJPY') for k in SETUP_LABELS})
        self.assertEqual({la.base_symbol for la in conf}, {'XAUUSD', 'EURUSD'})   # Confluence's own pair list
        self.assertTrue(all(la.agent.config['min_bb_width_atr'] == 2.5 for la in bbma))   # copied from config
        self.assertTrue(all(la.agent.config['min_score'] == 5 for la in conf))
        self.assertTrue(all(la.status == 'PAUSED' for la in bbma + conf))

    def test_registration_is_idempotent(self):
        cfg = {'symbols': ['XAUUSD'], 'confluence': {'symbols': ['XAUUSD']}}
        eng = self.engine(cfg)
        n = len(eng.agents)
        ensure_default_agents(eng, cfg)
        self.assertEqual(len(eng.agents), n)

    def test_setup_filter_matches_the_dashboard_switches(self):
        csa = LiveBBMA({'setup': 'CSA_REENTRY'}).analysis_config(POINT)
        mhv = LiveBBMA({'setup': 'MHV_REVERSAL'}).analysis_config(POINT)
        self.assertEqual((csa['_allowed_setups'], csa['enable_mhv']), (['CSA_REENTRY'], False))
        self.assertEqual((mhv['_allowed_setups'], mhv['enable_mhv']), ([], True))
        self.assertEqual((csa['_bbma_tp_mode'], csa['_structural_partial']), ('structural', True))

    def test_unknown_setup_is_rejected(self):
        with self.assertRaises(ValueError):
            LiveBBMA({'setup': 'NOPE'})


class BBMAExecution(unittest.TestCase):
    def setUp(self):
        self.agent = LiveBBMA()
        self.state = {}
        p = patch.object(live_test_bbma, 'live_frames', lambda *a, **k: (None, None, None))
        p.start(); self.addCleanup(p.stop)

    def run_eval(self, quote, plan=None, candles=M5):
        with patch('bbma_bot.assess', return_value=plan or {'entry_plan': None}) as assess:
            sig = self.agent.evaluate(quote.time, quote, candles, POINT, self.state, market=N(), symbol='XAUUSD')
        return sig, assess

    def test_plan_becomes_a_stop_order_that_fills_only_when_crossed(self):
        sig, _ = self.run_eval(q(1999.0, 1999.2), bbma_plan())
        self.assertIsNone(sig)
        self.assertEqual(self.state['pending']['entry'], 2000.0)
        sig, assess = self.run_eval(q(1999.5, 1999.7, NOW + 30))       # not crossed yet
        self.assertIsNone(sig)
        assess.assert_not_called()                                    # no new analysis while the order waits
        sig, _ = self.run_eval(q(2000.1, 2000.3, NOW + 60))            # Ask crosses the BUY STOP
        self.assertEqual(sig['side'], 'BUY')
        self.assertEqual((sig['initial_sl'], sig['initial_tp']), (1990.0, 2025.0))   # broker TP = TP2
        ex = sig['extra']
        self.assertEqual((ex['partial_price'], ex['partial_fraction'], ex['be_offset_points']), (2010.0, 0.5, 50))
        self.assertEqual((ex['trail_activation_r'], ex['trail_distance_r']), (1.0, 1.0))
        self.assertIsNone(self.state['pending'])

    def test_unfilled_stop_order_expires(self):
        self.run_eval(q(1999.0, 1999.2), bbma_plan(expires=NOW + 600))
        sig, _ = self.run_eval(q(1999.0, 1999.2, NOW + 601))
        self.assertIsNone(sig)
        self.assertEqual(self.state['_emit'][-1]['reason'], 'STOP_ORDER_EXPIRED')

    def test_single_target_has_no_partial_or_break_even(self):
        self.run_eval(q(1999.0, 1999.2), bbma_plan(single=True, tp2=2010.0))
        sig, _ = self.run_eval(q(2000.1, 2000.3, NOW + 60))
        self.assertNotIn('partial_price', sig['extra'])
        self.assertIn('trail_activation_r', sig['extra'])

    def test_wide_spread_is_rejected_like_live(self):
        sig, _ = self.run_eval(q(1997.0, 1999.8), bbma_plan())          # spread 2.8 > 25% of 10 risk
        self.assertIsNone(self.state.get('pending'))
        self.assertEqual(self.state['_emit'][-1]['reason'], 'SPREAD_OVER_25PCT_OF_RISK')

    def test_one_analysis_per_closed_m5_and_no_repeat_of_the_same_plan(self):
        self.run_eval(q(1999.0, 1999.2), bbma_plan(expires=NOW + 10))
        self.run_eval(q(1999.0, 1999.2, NOW + 20))                     # expires
        _, assess = self.run_eval(q(1999.0, 1999.2, NOW + 25), bbma_plan())
        assess.assert_not_called()                                    # same M5 candle
        newer = [Bar(NOW - 30, 1, 1, 1, 1)]
        _, assess = self.run_eval(q(1999.0, 1999.2, NOW + 300), bbma_plan(), candles=newer)
        assess.assert_called_once()
        self.assertIsNone(self.state.get('pending'))                  # same plan id -> not re-armed

    def test_sell_stop(self):
        self.run_eval(q(2000.0, 2000.2), bbma_plan('SELL', entry=1999.0, sl=2009.0, tp1=1989.0, tp2=1975.0))
        sig, _ = self.run_eval(q(1998.9, 1999.1, NOW + 60))
        self.assertEqual(sig['side'], 'SELL')


class ConfluenceExecution(unittest.TestCase):
    def setUp(self):
        self.agent = LiveConfluence()
        self.state = {}
        p = patch.object(live_test_confluence, 'live_frames', lambda *a, **k: (None, None, None))
        p.start(); self.addCleanup(p.stop)
        self.m15 = [Bar(NOW - 1000, 1, 1, 1, 1)]

    def result(self, side='BUY', entry=2000.2, sl=1995.0, tp1=2007.0, tp2=2012.0, score=4):
        plan = dict(id=f'1:{side}', side=side, entry=entry, stop_loss=sl, tp1=tp1, tp2=tp2)
        return dict(entry_plan=plan, score=score,
                    factors=[dict(name='H4 trend', passed=True), dict(name='M15 RSI', passed=False)])

    def run_eval(self, quote, result, candles=None):
        with patch('confluence_strategy.evaluate', return_value=result) as ev:
            sig = self.agent.evaluate(quote.time, quote, candles or self.m15, POINT, self.state,
                                      market=N(), symbol='XAUUSD')
        return sig, ev

    def test_qualified_score_enters_at_market_with_live_management(self):
        sig, ev = self.run_eval(q(2000.0, 2000.2), self.result())
        self.assertEqual((sig['side'], sig['initial_sl'], sig['initial_tp']), ('BUY', 1995.0, 2012.0))
        self.assertEqual(sig['extra']['partial_price'], 2007.0)
        self.assertEqual(sig['extra']['score'], 4)
        cfg = ev.call_args[0][7]
        self.assertEqual(cfg['confluence']['min_score'], 4)

    def test_hold_does_nothing_and_analysis_runs_once_per_m15(self):
        sig, _ = self.run_eval(q(2000.0, 2000.2), dict(entry_plan=None, score=2, factors=[]))
        self.assertIsNone(sig)
        sig, ev = self.run_eval(q(2000.0, 2000.2, NOW + 15), self.result())
        ev.assert_not_called()
        self.assertIsNone(sig)

    def test_wide_spread_rejected(self):
        sig, _ = self.run_eval(q(1998.0, 2000.2), self.result())
        self.assertIsNone(sig)
        self.assertEqual(self.state['_emit'][-1]['reason'], 'SPREAD_OVER_25PCT_OF_RISK')


class PartialClose(unittest.TestCase):
    def pos(self, **kw):
        p = dict(side='BUY', symbol='XAUUSD', entry_time=NOW, entry_price=100.0, initial_sl=99.0,
                 initial_tp=103.0, stop=99.0, risk=1.0, data_quality_status=VALID,
                 last_sample_epoch=NOW, trade_id='t', agent_id='a',
                 partial_price=101.0, partial_fraction=0.5, be_on_partial=True, be_offset_points=0,
                 trail_activation_r=None, trail_distance_r=None)
        p.update(kw)
        return p

    def step(self, p, bid, t):
        return Simulator.monitor(p, Quote('XAUUSD', bid, bid + 0.01, t, 0.0, POINT, DIGITS), t, 120)

    def test_partial_then_break_even_stop(self):
        p, closed = self.step(self.pos(), 101.0, NOW + 10)
        self.assertFalse(closed)
        self.assertTrue(p['partial_done'])
        self.assertAlmostEqual(p['stop'], 100.0)                       # break-even after TP1
        self.assertAlmostEqual(p['unrealized_r'], 1.0)
        p, closed = self.step(p, 100.0, NOW + 20)
        self.assertTrue(closed)
        self.assertAlmostEqual(p['pnl_r'], 0.5)                        # +1R on half, 0 on the rest

    def test_partial_then_final_target(self):
        p, _ = self.step(self.pos(), 101.0, NOW + 10)
        p, closed = self.step(p, 103.0, NOW + 20)
        self.assertEqual(p['exit_reason'], 'TP')
        self.assertAlmostEqual(p['pnl_r'], 2.0)                        # 0.5*1 + 0.5*3

    def test_stop_before_partial_is_a_full_loss(self):
        p, closed = self.step(self.pos(), 99.0, NOW + 10)
        self.assertAlmostEqual(p['pnl_r'], -1.0)
        self.assertFalse(p.get('partial_done'))

    def test_sell_partial(self):
        p = self.pos(side='SELL', initial_sl=101.0, stop=101.0, initial_tp=97.0, partial_price=99.0)
        p, _ = Simulator.monitor(p, Quote('XAUUSD', 98.99, 99.0, NOW + 10, 0.0, POINT, DIGITS), NOW + 10, 120)
        p, _ = Simulator.monitor(p, Quote('XAUUSD', 96.99, 97.0, NOW + 20, 0.0, POINT, DIGITS), NOW + 20, 120)
        self.assertAlmostEqual(p['pnl_r'], 2.0)

    def test_positions_without_partial_fields_are_unchanged(self):
        p = self.pos(partial_price=None, be_on_partial=None)
        p, _ = self.step(p, 103.0, NOW + 10)
        self.assertAlmostEqual(p['pnl_r'], 3.0)


class RealAnalysisOnSyntheticMarket(unittest.TestCase):
    """End to end through the REAL bbma_bot / confluence_strategy code on scripted MT5 bars:
    frames build, pass the live freshness rule, and the analysis runs without error."""
    def bars(self, seconds, n, last_open):
        out = []
        for i in range(n):
            t = last_open - (n - 1 - i) * seconds
            mid = 2000 + 30 * math.sin(i / 9) + i * 0.4
            out.append(Bar(t, mid - 1, mid + 3, mid - 3, mid + 1))
        return out

    def market(self, now):
        frames = {}
        for tf, s, n in (('M5', 300, 401), ('M15', 900, 501), ('H1', 3600, 501), ('H4', 14400, 501)):
            last_closed_open = (now // s) * s - s
            frames[tf] = self.bars(s, n, last_closed_open) + [Bar(last_closed_open + s, 2000, 2001, 1999, 2000)]
        mt5 = MultiTFMT5(frames)
        mt5.set_quote(2000.0, 2000.2, now)
        return ReadOnlyMarketData(mt5, {'max_tick_age_seconds': 60})

    def test_both_agents_run_the_live_analysis(self):
        live_test_bbma._CACHE.clear()
        now = NOW
        md = self.market(now)
        quote = md.quote('XAUUSD', now=now)
        for agent in [LiveBBMA({'setup': s}) for s in SETUP_LABELS] + [LiveConfluence()]:
            state = {}
            candles = md.candles('XAUUSD', agent.timeframe, 50, now=now)
            agent.evaluate(now, quote, candles, POINT, state, market=md, symbol='XAUUSD')
            self.assertIn('last_m5' if agent.strategy_id == 'LIVE_BBMA' else 'last_m15', state)
        # the three BBMA setups on one pair share indicator frames (computed once per new bar)
        self.assertEqual(len([k for k in live_test_bbma._CACHE if k[0] == 'bbma']), 3)


if __name__ == '__main__':
    unittest.main()
