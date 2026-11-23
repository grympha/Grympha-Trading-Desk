"""Shared pair sizing, persistence, broker rejection, and unchanged existing volume."""
import json
import unittest
from trade_controls import validate,entry_sizing,controls,estimate,DEFAULTS
import test_trade_controls as risk_tests
import test_demo_execution as demo_tests
from demo_execution import DemoExecutor

class PairSizing(unittest.TestCase):
    def test_validation(self):
        for lots in ([],{'AUDUSD':True},{'AUDUSD':0},{'AUDUSD':float('nan')},{'AUDUSD':101},{'bad symbol':.02}):
            with self.assertRaises(ValueError):validate({'pair_lots':lots})
    def test_exact_symbol_and_global_fallback(self):
        c=validate({'pair_lots':{'AUDUSD.a':.03}})
        self.assertEqual(entry_sizing(c,'AUDUSD.a')[1],.03)
        self.assertFalse(entry_sizing(c,'AUDUSD.a')[0]['risk_enabled'])
        self.assertTrue(entry_sizing(c,'AUDUSD')[0]['risk_enabled'])
        self.assertTrue(c['risk_enabled'])
    def test_preview_uses_fixed_override(self):
        m=risk_tests.RiskNews().broker()
        d=estimate(m,'AUDUSD','BUY',1.1,1.099,1.101,1.102,validate({'pair_lots':{'AUDUSD':.03}}))
        self.assertEqual(d['volume'],.03)
        self.assertAlmostEqual(d['estimated_loss'],3)

class PairExecution(unittest.TestCase):
    setUp=demo_tests.Demo.setUp
    tearDown=demo_tests.Demo.tearDown
    result=demo_tests.Demo.result
    submit=demo_tests.Demo.submit
    latest=demo_tests.Demo.latest
    def save(self,lots):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('trade_controls',json.dumps(validate({'pair_lots':lots}))))
    def test_both_strategies_share_override_without_profit_api(self):
        for strategy in ('BBMA','CONFLUENCE'):
            self.save({'AUDUSD':.03})
            ex=DemoExecutor(self.mt5,self.store,self.cfg,lot=.01)
            self.submit(ex,self.result(strategy,key=strategy))
            self.assertEqual(self.mt5.sent[-1]['volume'],.03)
    def test_invalid_broker_grid_never_sends(self):
        self.save({'AUDUSD':.015})
        ex=DemoExecutor(self.mt5,self.store,self.cfg)
        self.submit(ex,self.result())
        self.assertEqual(self.mt5.sent,[])
    def test_save_does_not_resize_existing_position(self):
        self.save({'AUDUSD':.03})
        ex=DemoExecutor(self.mt5,self.store,self.cfg)
        self.submit(ex,self.result());ex.refresh()
        self.save({'AUDUSD':.05});ex.refresh()
        self.assertEqual(self.mt5.positions[0].volume,.03)
        self.assertEqual(len(self.mt5.sent),1)
        self.assertEqual(controls(self.store,self.cfg)['pair_lots']['AUDUSD'],.05)

if __name__=='__main__':unittest.main()
