import time
import unittest
from types import SimpleNamespace as N

from confluence_scanner import ConfluenceScanner
from runtime_health import HealthMonitor
from trade_monitor import auto_enabled, monitor, set_auto
import test_demo_execution as fixture


class StrategyAgents(unittest.TestCase):
    def setUp(self):
        self.demo = fixture.Demo()
        self.demo.setUp()
        self.addCleanup(self.demo.tearDown)
        self.store = self.demo.store

    def test_legacy_global_mode_and_independent_switches(self):
        self.assertFalse(auto_enabled(self.store,'BBMA'))
        self.assertFalse(auto_enabled(self.store,'CONFLUENCE'))
        with self.store.connect() as db:
            db.execute("INSERT INTO meta VALUES ('auto_early_exit','true')")
        self.assertTrue(auto_enabled(self.store,'BBMA'))
        self.assertTrue(auto_enabled(self.store,'CONFLUENCE'))
        set_auto(self.store, {'BBMA':False,'CONFLUENCE':True})
        self.assertFalse(auto_enabled(self.store,'BBMA'))
        self.assertTrue(auto_enabled(self.store,'CONFLUENCE'))

    def test_orb_auto_exit_switch_available_in_demo(self):
        self.assertFalse(auto_enabled(self.store,'ORB'))
        set_auto(self.store, {'BBMA':False,'CONFLUENCE':False,'ORB':True})
        self.assertTrue(auto_enabled(self.store,'ORB'))
        self.assertFalse(auto_enabled(self.store,'BBMA'))
        self.assertFalse(auto_enabled(self.store,'CONFLUENCE'))
        with self.assertRaises(ValueError):
            set_auto(self.store, {'BBMA':True,'UNKNOWN':True})

    def test_confluence_can_auto_exit_with_bbma_in_review(self):
        set_auto(self.store, {'BBMA':False,'CONFLUENCE':True})
        start=int(time.time())-6350
        bars=[dict(time=start+i*900,high=110,low=100,close=105) for i in range(7)]
        for candle in bars[-2:]:
            candle.update(low=97,close=98)
        pos=N(ticket=9,identifier=10,magic=816002,symbol='AUDUSD',type=0,volume=.04,sl=95,tp=120,time=start)
        sent=[]
        ex=N(store=self.store,cfg={},mt5=N(TIMEFRAME_M15=15,copy_rates_from_pos=lambda *args:bars,
             positions_get=lambda **kwargs:[pos],ORDER_FILLING_FOK=0,TRADE_ACTION_DEAL=1,ORDER_TYPE_SELL=1),
             quote=lambda symbol:(N(filling_mode=1,digits=2),N(bid=98,ask=98.1)),guard=lambda:None,
             broker_check=lambda *args,**kwargs:N(retcode=0),save=lambda *args:None,
             broker_send=lambda req,*args,**kwargs:sent.append(req))
        payload=dict(side='BUY',actual_entry=105,sl=95,tp2=120,actual_tp=120,quote_fresh=True,
                     context={'setup_structure':{'low':100}})
        row=dict(key='test',status='OPEN',strategy='BBMA',symbol='AUDUSD')
        bbma=dict(payload)
        self.assertFalse(monitor(ex,row,bbma,pos))
        self.assertEqual(bbma['trade_monitor']['status'],'EXIT_CONFIRMED')
        self.assertFalse(sent)
        self.assertTrue(monitor(ex,dict(row,strategy='CONFLUENCE'),dict(payload),pos))
        self.assertEqual(sent[0]['position'],9)

    def test_confluence_cadence_and_disabled_pair(self):
        calls=[]
        runner=N(c={'enabled':True,'symbols':['AUDUSD','XAUUSD'],'check_seconds':60},next_due=0,
                 check=lambda mt5,symbol,now:calls.append(symbol) or dict(score=4,reason='ok',health='OK'))
        scanner=ConfluenceScanner(None,self.store.path.parent,runner,str)
        self.assertEqual(scanner.scan(lambda result:None,{'CONFLUENCE':['AUDUSD']})['checked'],1)
        self.assertEqual(calls,['XAUUSD'])
        self.assertIsNone(scanner.scan(lambda result:None,{}))

    def test_six_strategy_health_cards_and_three_shared(self):
        health=HealthMonitor(self.store, self.demo.cfg)
        self.assertEqual(len(health.states),12)  # 3 strategies x 3 agents + 3 shared
        for strategy in ('BBMA','CONFLUENCE','ORB'):
            for stage in ('Scanner','Executor','Trade Monitor'):
                self.assertIn(f'{strategy} {stage}',health.states)
