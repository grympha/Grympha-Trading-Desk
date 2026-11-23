"""Simulated MT5 only. No real broker connection is opened."""
import unittest
import test_demo_execution as fixture
from demo_execution import DemoExecutor


class RealExecutionTests(unittest.TestCase):
    def setUp(self):
        self.demo=fixture.Demo()
        self.demo.setUp()
        self.addCleanup(self.demo.tearDown)
        self.mt5=self.demo.mt5
        self.mt5.account.trade_mode=2
        self.cfg=dict(self.demo.cfg,real_execution=dict(account_login=self.mt5.account.login,
                                                      account_server=self.mt5.account.server))

    def executor(self):
        return DemoExecutor(self.mt5,self.demo.store,self.cfg,execution_mode='REAL')

    def test_pinned_account_can_submit(self):
        ex=self.executor()
        self.demo.submit(ex,self.demo.result('BBMA'))
        self.assertEqual(len(self.mt5.sent),1)
        self.assertEqual(self.demo.store.snapshot()['meta']['demo_execution']['account_type'],'REAL')

    def test_real_mode_rejects_wrong_login_server_type_and_netting(self):
        for field,value in [('login',999),('server','different'),('trade_mode',0),('margin_mode',0)]:
            previous=getattr(self.mt5.account,field)
            setattr(self.mt5.account,field,value)
            with self.assertRaises(RuntimeError):
                self.executor()
            setattr(self.mt5.account,field,previous)
        self.assertFalse(self.mt5.sent)

    def test_switch_before_send_is_blocked(self):
        ex=self.executor()
        self.mt5.account.login+=1
        for operation in ('ENTRY','SLTP','CANCEL','EARLY_EXIT'):
            with self.assertRaises(RuntimeError):
                ex.broker_send({}, {}, operation)
        self.assertFalse(self.mt5.sent)

    def test_demo_mode_rejects_real_even_with_pin(self):
        with self.assertRaises(RuntimeError):
            DemoExecutor(self.mt5,self.demo.store,self.cfg)
        self.assertFalse(self.mt5.sent)
