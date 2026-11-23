import unittest

from structural_tp import mode, save_mode, target_plan
from demo_execution import DemoExecutor
import test_demo_execution as fixture


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.bar = dict(time=1800, ma5_high=105, ma10_high=106, mid=104, upper=110,
                        ma5_low=95, ma10_low=94, lower=90)

    def test_buy_reentry_single_target(self):
        plan = target_plan(dict(side='BUY',entry=100,setup='CSA_REENTRY'), self.bar, {}, 2, .01)
        self.assertEqual((plan['tp1'],plan['tp2']), (104,104))
        self.assertTrue(plan['structural_tp']['single_target'])

    def test_sell_reversal_opposite_ma_and_further_runner(self):
        plan = target_plan(dict(side='SELL',entry=100,setup='MHV_REVERSAL'), self.bar,
                           {'_structural_partial':True}, 2, .01)
        self.assertEqual((plan['tp1'],plan['tp2']), (95,94))
        self.assertEqual(plan['structural_tp']['mandatory']['source'], 'MA5 LOW')
        self.assertEqual(plan['structural_tp']['candle_closed_epoch'], 2700)

    def test_fallback_and_no_target(self):
        buy = dict(side='BUY',entry=106,setup='MOMENTUM_REENTRY')
        plan = target_plan(buy, self.bar, {'_minimum_stop_distance':.05}, 2, .01)
        self.assertEqual(plan['structural_tp']['mandatory']['source'], 'Upper BB')
        self.assertIsNone(target_plan(dict(buy,entry=110), self.bar, {}, 2, .01))


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.demo = fixture.Demo()
        self.demo.setUp()
        self.addCleanup(self.demo.tearDown)

    def test_mode_saved_and_single_broker_target_frozen(self):
        store = self.demo.store
        self.assertEqual(mode(store), 'existing')
        save_mode(store, {'mode':'structural'})
        self.assertEqual(mode(store), 'structural')
        ex = DemoExecutor(self.demo.mt5,store,self.demo.cfg)
        result = self.demo.result('BBMA')
        bar = dict(time=1800,ma5_high=1.1011,ma10_high=1.1012,mid=1.1015,upper=1.102)
        result['entry_plan'] = target_plan(result['entry_plan'],bar,{},5,.00001)
        self.demo.submit(ex,result)
        self.assertEqual(self.demo.mt5.sent[0]['tp'],1.1015)
        self.assertEqual(self.demo.latest()['structural_tp']['final']['source'],'Mid BB')
        save_mode(store, {'mode':'existing'})
        ex.refresh()
        self.assertEqual(len(self.demo.mt5.sent),1)

    def test_old_r_target_blocked_in_structural_mode(self):
        save_mode(self.demo.store, {'mode':'structural'})
        ex = DemoExecutor(self.demo.mt5,self.demo.store,self.demo.cfg)
        self.demo.submit(ex,self.demo.result('BBMA'))
        self.assertFalse(self.demo.mt5.sent)
