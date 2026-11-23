import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import patch
import test_demo_execution as fixture
from dashboard_store import Store
from demo_execution import DemoExecutor
from portfolio_guard import snapshot,evaluate,validate,save_settings,check_entry
from execution_audit import summary as audit_summary
from health_watchdog import inspect
from runtime_health import RuntimeLock

class Portfolio(unittest.TestCase):
    def setUp(self):
        self.m=fixture.FakeMT5();self.m.account.equity=10000
        self.m.order_calc_profit=lambda side,symbol,volume,start,end:(end-start)*(1 if side==0 else -1)*100000*volume
        self.m.symbol_info_tick=lambda *_:N(time=int(time.time()),bid=1.1,ask=1.1001)
        self.cfg=dict(symbols=['AUDUSD'],poll_seconds=15)
    def position(self,**changes):
        d=dict(symbol='AUDUSD',type=0,volume=.1,sl=1.099,magic=0,ticket=1,identifier=1,price_open=1.095,comment='manual')
        d.update(changes);return N(**d)
    def limits(self,**changes):return validate(dict(enabled=True,total_percent=3,pair_percent=1.5,currency_percent=2,**changes)) if not changes else validate({**dict(enabled=True,total_percent=3,pair_percent=1.5,currency_percent=2),**changes})
    def test_positions_pending_candidate_added_from_current_equity_basis(self):
        self.m.positions=[self.position()]
        self.m.orders=[N(symbol='AUDUSD',type=4,volume_current=.1,price_open=1.102,sl=1.1,magic=816001,ticket=2)]
        candidate=dict(symbol='AUDUSD',type=0,volume=.1,price=1.101,sl=1.1,magic=816002)
        s=snapshot(self.m,self.cfg,candidate)
        self.assertEqual(s['status'],'KNOWN');self.assertAlmostEqual(s['total_risk'],40)
        self.assertAlmostEqual(s['by_pair']['AUDUSD'],40)
        self.assertAlmostEqual(s['by_strategy']['MANUAL / OTHER'],10)
    def test_locked_profit_still_has_current_equity_giveback_risk(self):
        self.m.positions=[self.position(price_open=1.09,sl=1.099)]
        self.assertAlmostEqual(snapshot(self.m,self.cfg)['total_risk'],10)
    def test_unknown_stop_never_becomes_zero(self):
        self.m.positions=[self.position(sl=0)]
        s=snapshot(self.m,self.cfg);self.assertIsNone(s['total_risk']);self.assertTrue(s['unknown'])
        self.assertTrue(evaluate(s,self.limits())['blocked'])
        self.assertFalse(evaluate(s,validate({}))['blocked'])
    def test_hedged_currency_legs_are_not_netted(self):
        self.m.positions=[self.position(),self.position(symbol='EURUSD',type=1,sl=1.1011,ticket=2,identifier=2)]
        s=snapshot(self.m,self.cfg)
        self.assertAlmostEqual(s['by_currency']['USD']['gross'],20)
        self.assertAlmostEqual(s['by_currency']['USD']['long'],10)
        self.assertAlmostEqual(s['by_currency']['USD']['short'],10)
    def test_total_pair_and_currency_caps_independent(self):
        self.m.positions=[self.position(volume=1)]
        s=snapshot(self.m,self.cfg)
        for k in ('total_percent','pair_percent','currency_percent'):
            d={key:100 for key in ('total_percent','pair_percent','currency_percent')};d[k]=.5
            self.assertTrue(evaluate(s,self.limits(**d))['blocked'])
    def test_missing_equity_and_broker_calculation_fail_closed(self):
        self.m.account.equity=None;s=snapshot(self.m,self.cfg)
        self.assertTrue(evaluate(s,self.limits())['blocked'])
        self.m.account.equity=10000;self.m.positions=[self.position()];self.m.order_calc_profit=lambda *_:None
        self.assertEqual(snapshot(self.m,self.cfg)['status'],'UNKNOWN')
    def test_unsupported_asset_concentration_unknown(self):
        self.m.positions=[self.position(symbol='US500')]
        self.assertEqual(snapshot(self.m,self.cfg)['status'],'UNKNOWN')
    def test_stale_quotes_unknown(self):
        self.m.positions=[self.position()];self.m.symbol_info_tick=lambda *_:N(time=int(time.time())-100,bid=1.1,ask=1.1001)
        self.assertEqual(snapshot(self.m,self.cfg,strict=True)['status'],'UNKNOWN')
    def test_unresolved_attempt_blocks_and_self_reservation_excluded(self):
        r=dict(key='x',symbol='AUDUSD',payload=json.dumps(dict(side='BUY',volume=.1,reference=1.1,sl=1.099)))
        self.assertEqual(snapshot(self.m,self.cfg,reservations=[r])['status'],'UNKNOWN')
        self.assertEqual(snapshot(self.m,self.cfg,reservations=[r],exclude_key='x')['status'],'KNOWN')
    def test_known_broker_ticket_not_double_counted(self):
        self.m.positions=[self.position()]
        r=dict(key='x',symbol='AUDUSD',payload=json.dumps(dict(position_id=1,side='BUY',volume=.1,reference=1.1,sl=1.099)))
        self.assertEqual(len(snapshot(self.m,self.cfg,reservations=[r])['rows']),1)
    def test_invalid_limits_rejected(self):
        for d in ({'enabled':'yes'},{'total_percent':float('nan')},{'pair_percent':0},{'currency_percent':101},{'bad':1}):
            with self.assertRaises(ValueError):validate(d)

class GuardExecution(unittest.TestCase):
    setUp=fixture.Demo.setUp
    tearDown=fixture.Demo.tearDown
    result=fixture.Demo.result
    submit=fixture.Demo.submit
    latest=fixture.Demo.latest
    def broker(self):
        self.mt5.account.equity=10000
        self.mt5.order_calc_profit=lambda side,symbol,volume,start,end:(end-start)*(1 if side==0 else -1)*100000*volume
    def test_final_guard_blocks_new_manual_exposure_after_broker_precheck(self):
        self.broker();save_settings(self.store,dict(enabled=True,total_percent=.5,pair_percent=.5,currency_percent=.5))
        self.mt5.after_check=lambda:self.mt5.positions.append(N(symbol='AUDUSD',type=0,volume=1,sl=1.0989,ticket=99,identifier=99,magic=0))
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result())
        self.assertEqual(self.latest()['status'],'BLOCKED_PORTFOLIO');self.assertEqual(self.mt5.sent,[])
    def test_own_precheck_reservation_not_double_counted(self):
        self.broker();save_settings(self.store,dict(enabled=True,total_percent=.02,pair_percent=.02,currency_percent=.02))
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result())
        self.assertEqual(len(self.mt5.sent),1)
    def test_audit_response_and_confirmed_fill_no_duplicate_send(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result());ex.refresh();self.submit(ex,self.result())
        a=audit_summary(self.store,ex.account_hash)
        self.assertEqual(a['sends'],1);self.assertEqual(a['checks'],1)
        event=next(e for e in a['events'] if e['event']=='ORDER_SEND')
        self.assertGreaterEqual(event['latency_ms'],0);self.assertAlmostEqual(event['slippage_points'],2)
        self.assertTrue(any(e['event']=='CONFIRMED_FILL' for e in a['events']))
    def test_precheck_rejection_logged_without_send(self):
        self.mt5.check_code=10014;ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result())
        a=audit_summary(self.store,ex.account_hash);self.assertEqual(a['checks'],1);self.assertEqual(a['sends'],0)
    def test_ambiguous_response_logged_not_retried(self):
        self.mt5.none_reply=True;ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result());self.submit(ex,self.result())
        a=audit_summary(self.store,ex.account_hash);self.assertEqual(a['sends'],1);self.assertEqual(a['uncertain'],1)
    def test_monitor_only_does_not_require_missing_risk_data(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result())
        self.assertEqual(len(self.mt5.sent),1)
    def test_guard_enable_unknown_equity_blocks_new_entry(self):
        save_settings(self.store,dict(enabled=True))
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result())
        self.assertEqual(self.latest()['status'],'BLOCKED_PORTFOLIO');self.assertEqual(self.mt5.sent,[])
    def test_management_continues_when_new_entry_guard_would_block(self):
        import test_trade_controls as managed
        from trade_controls import validate as trade_settings
        self.mt5=managed.ManagedBroker();self.mt5.bid=1.0999;self.mt5.ask=1.1
        ex=DemoExecutor(self.mt5,self.store,self.cfg,lot=.04);self.submit(ex,self.result());ex.refresh()
        save_settings(self.store,dict(enabled=True,total_percent=.01,pair_percent=.01,currency_percent=.01))
        with self.store.connect() as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('trade_controls',json.dumps(trade_settings({'risk_enabled':False,'partial_enabled':True}))))
        self.mt5.bid=1.1015;self.mt5.ask=1.1016;ex.refresh()
        closes=[r for r in self.mt5.sent if r.get('position') and r['action']==1]
        self.assertEqual(len(closes),1)
        self.assertTrue(any(e.get('operation')=='TP1_PARTIAL' for e in audit_summary(self.store,ex.account_hash)['events']))
    def test_existing_pending_order_not_cancelled_by_portfolio_toggle(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result('BBMA'))
        save_settings(self.store,dict(enabled=True,total_percent=.01,pair_percent=.01,currency_percent=.01))
        ex.refresh();self.assertEqual(len(self.mt5.sent),1)
        self.assertEqual(self.latest()['status'],'PENDING')

    def test_audit_scoped_to_account(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result())
        self.assertEqual(audit_summary(self.store,'other')['total'],0)
    def test_platform_api_and_same_origin_control_persistence(self):
        import threading,urllib.request,urllib.error
        from dashboard_server import build_server
        from portfolio_guard import refresh,initialise,settings
        self.broker();initialise(self.store,self.cfg);refresh(self.mt5,self.store,self.cfg)
        server=build_server(self.store,0);threading.Thread(target=server.serve_forever,daemon=True).start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(url+'/api/platform') as response:data=json.load(response)
            self.assertEqual(data['portfolio']['status'],'KNOWN');self.assertFalse(data['controls']['enabled'])
            body=json.dumps(dict(enabled=True,total_percent=2,pair_percent=1,currency_percent=1.5)).encode()
            bad=urllib.request.Request(url+'/api/portfolio-controls',data=body,headers={'Origin':'https://other.invalid','Content-Type':'application/json','X-Dashboard-Request':'controls'})
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(bad)
            self.assertEqual(e.exception.code,403)
            req=urllib.request.Request(url+'/api/portfolio-controls',data=body,headers={'Origin':url,'Content-Type':'application/json','X-Dashboard-Request':'controls'})
            with urllib.request.urlopen(req) as response:self.assertTrue(json.load(response)['ok'])
            initialise(self.store,{'portfolio_guard':{'enabled':False}})
            self.assertTrue(settings(self.store)['enabled'])
        finally:server.shutdown();server.server_close()

class Health(unittest.TestCase):
    def test_watchdog_differentiates_stop_dead_stall_and_new_session(self):
        meta=dict(health_session='s',running=True,heartbeat=100)
        self.assertEqual(inspect(meta,True,'s',60,120),'WATCHING')
        self.assertEqual(inspect(meta,True,'s',60,200),'HEARTBEAT STALE')
        self.assertEqual(inspect(meta,False,'s',60,120),'BOT EXITED')
        self.assertEqual(inspect(meta,True,'other',60,120),'SUPERSEDED')
        meta['health_shutdown']={'session':'s'}
        self.assertEqual(inspect(meta,False,'s',60,120),'STOPPED')
        meta['health_shutdown']['error']='RuntimeError'
        self.assertEqual(inspect(meta,False,'s',60,120),'BOT ERROR')
    def test_same_folder_lock_refuses_second_owner_then_releases(self):
        with tempfile.TemporaryDirectory() as td:
            file=Path(td)/'bot.runtime.lock'
            with RuntimeLock(file):
                with self.assertRaises(SystemExit):
                    with RuntimeLock(file):pass
            with RuntimeLock(file):pass
    def test_external_watchdog_records_dead_parent_without_mt5(self):
        import subprocess,sys
        from runtime_health import HealthMonitor
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/'db.sqlite3');store.start(dict(symbols=['AUDUSD'],poll_seconds=15))
            h=HealthMonitor(store,{})
            r=subprocess.run([sys.executable,str(Path(__file__).parent/'health_watchdog.py'),'--db',str(store.path),'--pid','2147483647','--session',h.session],capture_output=True,timeout=10)
            self.assertEqual(r.returncode,0,r.stderr)
            report=json.loads((Path(td)/'health_watchdog.json').read_text())
            self.assertEqual(report['status'],'BOT EXITED')
            self.assertTrue((Path(td)/'health_alerts.jsonl').exists())

    def test_real_external_watchdog_records_clean_shutdown(self):
        import subprocess,sys,os
        from runtime_health import HealthMonitor
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/'db.sqlite3');store.start(dict(symbols=['AUDUSD'],poll_seconds=15))
            h=HealthMonitor(store,dict(poll_seconds=15));h.start_watchdog();h.close()
            self.assertIsNotNone(h.worker.poll())
            report=json.loads((Path(td)/'health_watchdog.json').read_text())
            self.assertEqual(report['status'],'STOPPED')

if __name__=='__main__':unittest.main()
