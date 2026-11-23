import ast,json,tempfile,time,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch,Mock
from dashboard_store import Store
from demo_execution import DemoExecutor
import test_demo_execution as fixture
from ai_shadow import ShadowScorer
from replay_lab import *
from replay_worker import ResearchLab,run_batch,import_csv

NOW=1700000000.
A=dict(enabled=True,tick_offset_hours=0,timestamp_verified=True,cost_r=.02,slippage_r=.01,latency_ms=0,horizon_minutes=5,max_gap_seconds=10)
def candidate(key='x',side='BUY',strategy='CONFLUENCE',observed=NOW):
    sign=1 if side=='BUY' else -1
    plan=dict(id=key,side=side,entry=100.,stop_loss=100-sign,tp1=100+sign,tp2=100+2*sign,expires_utc=datetime.fromtimestamp(observed+60,timezone.utc).isoformat())
    if strategy=='CONFLUENCE':plan['signal_kind']='REFERENCE'
    return dict(key=key,strategy=strategy,observed=observed,plan=canonical(plan),prediction=canonical(dict(estimated_r=None)))
def ticks(side='BUY',sl=False,pending=False,start=NOW):
    out=[]
    for i in range(301):
        bid,ask=(99.99,100.01) if not pending or i>=5 else (99.78,99.8)
        if i>=10:
            if sl:bid,ask=((98.5,98.52) if side=='BUY' else (101.48,101.5))
            elif side=='BUY':bid,ask=(101.1,101.12) if i<20 else (102.1,102.12)
            else:bid,ask=(98.88,98.9) if i<20 else (97.88,97.9)
        out.append([(start+i)*1000,bid,ask])
    return out

class ReplayEngine(unittest.TestCase):
    def test_buy_and_sell_bid_ask_cost_slippage(self):
        for side in ('BUY','SELL'):
            r=replay(candidate(side=side),ticks(side),{},A)
            self.assertEqual(r['status'],'CLOSED');self.assertEqual(r['reason'],'TP2');self.assertAlmostEqual(r['net_r'],1.95)
    def test_full_tp1_policy_on_same_ticks(self):
        r=replay(candidate(),ticks(),{'exit_target':'TP1'},A)
        self.assertEqual(r['reason'],'TP1');self.assertAlmostEqual(r['net_r'],.95)
    def test_stop_gap_uses_worse_quote_not_ideal_stop_price(self):
        r=replay(candidate(),ticks(sl=True),{},A);self.assertAlmostEqual(r['exit'],98.49);self.assertAlmostEqual(r['net_r'],-1.55)
    def test_pending_trigger_and_unfilled_are_distinct(self):
        c=candidate(strategy='BBMA');r=replay(c,ticks(pending=True),{},A)
        self.assertEqual(r['status'],'CLOSED');self.assertEqual(r['entry_epoch'],NOW+5)
        path=[[(NOW+i)*1000,99.78,99.8] for i in range(301)]
        r=replay(c,path,{},A);self.assertEqual(r['status'],'UNFILLED');self.assertEqual(r['net_r'],0)
    def test_no_pending_admission_after_stop_already_crossed(self):
        r=replay(candidate(strategy='BBMA'),ticks(),{},A)
        self.assertEqual(r['status'],'NOT_ADMITTED')
    def test_horizon_marks_executable_side_with_costs(self):
        path=[[(NOW+i)*1000,99.99,100.01] for i in range(301)]
        r=replay(candidate(),path,{},A);self.assertEqual(r['reason'],'HORIZON_EXIT');self.assertAlmostEqual(r['net_r'],-.06)
    def test_gap_and_missing_boundaries_do_not_fabricate_results(self):
        for path in ([],ticks()[10:],ticks()[:-10],ticks()[:5]+ticks()[25:]):
            r=replay(candidate(),path,{},A);self.assertEqual(r['status'],'INCONCLUSIVE');self.assertIsNone(r['net_r'])
    def test_conflicting_millisecond_order_invalid_prices_and_ordering(self):
        path=ticks()
        for bad in ([path[0],[path[0][0],99.5,99.6]]+path[1:],[path[1],path[0]]+path[2:],[[NOW*1000,-1,0]]+path[1:],[5]+path[1:]):
            self.assertEqual(replay(candidate(),bad,{},A)['status'],'INCONCLUSIVE')
    def test_verified_time_and_explicit_cost_required(self):
        for d in ({'timestamp_verified':False},{'cost_r':None},{'tick_offset_hours':None,'timestamp_verified':False}):
            self.assertEqual(replay(candidate(),ticks(),{},{**A,**d})['status'],'INCONCLUSIVE')
    def test_model_filter_only_uses_frozen_capture(self):
        c=candidate();self.assertEqual(replay(c,ticks(),{'min_model_r':0},A)['status'],'INCONCLUSIVE')
        c['prediction']=canonical({'estimated_r':-.1});r=replay(c,ticks(),{'min_model_r':0},A)
        self.assertEqual(r['status'],'SKIPPED');self.assertEqual(r['net_r'],0)
    def test_version_stop_change_can_increase_loss_above_one_r(self):
        path=[[(NOW+i)*1000,99.99,100.01] for i in range(301)]
        path[10:]=[[(NOW+i)*1000,98.99,99.01] for i in range(10,301)]
        r=replay(candidate(),path,{'sl_multiplier':2},A)
        self.assertEqual(r['reason'],'HORIZON_EXIT');self.assertLess(r['net_r'],-1)
    def test_latency_and_market_drift_not_future_ideal_entry(self):
        r=replay(candidate(),ticks(),{},{**A,'latency_ms':10000})
        self.assertEqual(r['status'],'NOT_ADMITTED')
    def test_invalid_controls_and_policy_rejected(self):
        for d in ({'cost_r':-1},{'max_gap_seconds':100},{'timestamp_verified':'true'},{'unknown':1}):
            with self.assertRaises(ValueError):validate_settings(d)
        for p in ({'sl_multiplier':0},{'min_model_r':True},{'exit_target':'TP3'},{'apply_live':True}):
            with self.assertRaises(ValueError):validate_policy(p)

class LabStorage(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db.sqlite3')
        self.cfg=dict(symbols=['AUDUSD'],poll_seconds=15,confluence=dict(symbols=['AUDUSD']))
        self.store.start(self.cfg);self.mt5=fixture.FakeMT5();self.ex=DemoExecutor(self.mt5,self.store,self.cfg)
        self.scope=self.ex.account_hash;self.scorer=ShadowScorer(self.store,self.cfg,self.mt5)
        self.lab=ResearchLab(self.store,self.cfg,self.mt5,Path(self.tmp.name)/'config.json')
        with self.store.connect() as db:self.aid=register_settings(db,A);db.execute("INSERT OR REPLACE INTO meta VALUES ('replay_scope',?)",(canonical(self.scope),))
    def tearDown(self):self.lab.close();self.scorer.close();self.tmp.cleanup()
    def put(self,key='x',observed=NOW):
        c=candidate(key=key,observed=observed);c.update(account_scope=self.scope,symbol='AUDUSD')
        with self.store.connect() as db:db.execute('INSERT INTO ai_candidates (key,account_scope,setup_key,strategy,symbol,observed,features,plan,prediction,execution_status) VALUES (?,?,?,?,?,?,?,?,?,?)',
              (key,self.scope,key,'CONFLUENCE','AUDUSD',observed,'{}',c['plan'],c['prediction'],'BLOCKED_PORTFOLIO'))
        return c
    def test_jobs_versions_and_results_immutable_no_ai_label_contamination(self):
        c=self.put();job=store_job(self.store,self.scope,c,self.aid,ticks(),'TEST',{})
        self.assertEqual(job,store_job(self.store,self.scope,c,self.aid,ticks(),'TEST',{}))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM replay_jobs').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM replay_results').fetchone()[0],2)
            self.assertIsNone(db.execute('SELECT label_r FROM ai_candidates').fetchone()[0])
            new=add_version(db,'Wider test',{'sl_multiplier':1.5})
        store_job(self.store,self.scope,c,self.aid,ticks(),'TEST',{})
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM replay_results').fetchone()[0],3)
        self.assertEqual(self.mt5.sent,[])
    def test_cross_account_candidate_refused(self):
        c=self.put()
        with self.assertRaises(ValueError):store_job(self.store,'other',c,self.aid,ticks(),'TEST',{})
    def test_existing_data_is_exploratory_without_first_capture_enrolment(self):
        c=self.put();store_job(self.store,self.scope,c,self.aid,ticks(),'TEST',{})
        report=summary(self.store);self.assertEqual(report['comparisons'][0]['all']['matched'],1)
        self.assertEqual(report['comparisons'][0]['forward']['matched'],0)
        self.assertEqual(report['recent'][0]['actual_status'],'BLOCKED_PORTFOLIO')
    def test_forward_requires_enrolled_profile_and_both_frozen_versions(self):
        t=time.time();c=self.put(observed=t)
        self.lab.enrol(c['key']);store_job(self.store,self.scope,c,self.aid,ticks(start=t),'TEST',{})
        r=summary(self.store);self.assertEqual(r['comparisons'][0]['forward']['matched'],1)
        with self.store.connect() as db:add_version(db,'Created after capture',{'tp_multiplier':.8})
        store_job(self.store,self.scope,c,self.aid,ticks(start=t),'TEST',{})
        r=summary(self.store);self.assertEqual(r['comparisons'][-1]['forward']['matched'],0)
    def test_changed_assumptions_start_separate_cohort(self):
        c=self.put();store_job(self.store,self.scope,c,self.aid,ticks(),'TEST',{})
        with self.store.connect() as db:register_settings(db,{**A,'cost_r':.05})
        self.assertEqual(summary(self.store)['replayed'],0)
    def test_import_csv_rejects_unverified_or_invalid_data(self):
        path=Path(self.tmp.name)/'ticks.csv';path.write_text('time_msc,bid,ask\n1700000000000,1,1.1\n1700000001000,1,1.1\n')
        with self.assertRaises(ValueError):import_csv(self.store,self.scope,'AUDUSD',path,0,False)
        key=import_csv(self.store,self.scope,'AUDUSD',path,2,True)
        with self.store.connect() as db:r=db.execute('SELECT * FROM replay_imports WHERE id=?',(key,)).fetchone()
        self.assertEqual(unpack(r['ticks'])[0][0],1700000000000-7200000)
    def test_read_only_batch_fetches_only_mature_candidates(self):
        c=self.put();factory=Mock()
        provider=factory.return_value;provider.fetch.return_value=ticks()
        self.assertEqual(run_batch(self.store,self.scope,self.cfg,factory,now=NOW+100),0)
        factory.assert_not_called()
        self.assertEqual(run_batch(self.store,self.scope,self.cfg,factory,now=NOW+400),1)
        provider.fetch.assert_called_once();provider.close.assert_called_once()
        self.assertEqual(summary(self.store)['replayed'],1);self.assertEqual(self.mt5.sent,[])
    def test_batch_uses_import_without_opening_mt5(self):
        c=self.put();path=Path(self.tmp.name)/'ticks.csv'
        path.write_text('time_msc,bid,ask\n'+'\n'.join(','.join(map(str,t)) for t in ticks()))
        import_csv(self.store,self.scope,'AUDUSD',path,0,True);factory=Mock(side_effect=AssertionError('No MT5 expected'))
        run_batch(self.store,self.scope,self.cfg,factory,now=NOW+400);factory.assert_not_called()
        self.assertEqual(summary(self.store)['recent'][0]['source'],'IMPORTED_BID_ASK_CSV')
    def test_comparison_filters(self):
        c=self.put();store_job(self.store,self.scope,c,self.aid,ticks(),'TEST',{})
        self.assertEqual(summary(self.store,'BBMA')['replayed'],0)
        self.assertEqual(summary(self.store,'ALL','EURUSD')['replayed'],0)
    def test_pause_preserves_assumption_profile_and_past_results(self):
        c=self.put();store_job(self.store,self.scope,c,self.aid,ticks(),'TEST',{})
        with self.store.connect() as db:key=register_settings(db,{**A,'enabled':False})
        self.assertEqual(key,self.aid);r=summary(self.store)
        self.assertEqual(r['status'],'DISABLED');self.assertEqual(r['replayed'],1)
    def test_late_enrolment_cannot_backfill_forward_cohort(self):
        c=self.put()
        self.lab.enrol(c['key'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM replay_enrolments').fetchone()[0],0)
    def test_frozen_dataset_covers_each_policy_once_and_cpu_work_outside_db_write(self):
        c=self.put();real=replay
        def verify(*args,**kwargs):
            # A second writer can commit while simulation is executing.
            with self.store.connect() as db:db.execute("INSERT OR REPLACE INTO meta VALUES ('replay_test_writer','true')")
            return real(*args,**kwargs)
        with patch('replay_lab.replay',side_effect=verify):store_job(self.store,self.scope,c,self.aid,ticks(),'TEST',{})
    def test_real_cli_worker_replays_imported_ticks_without_mt5(self):
        import subprocess,sys,os
        c=self.put();path=Path(self.tmp.name)/'ticks.csv'
        path.write_text('time_msc,bid,ask\n'+'\n'.join(','.join(map(str,t)) for t in ticks()))
        import_csv(self.store,self.scope,'AUDUSD',path,0,True)
        r=subprocess.run([sys.executable,str(Path(__file__).parent/'replay_worker.py'),'--run','--db',str(self.store.path),'--scope',self.scope],capture_output=True,text=True,timeout=20,env={**os.environ,'OPENBLAS_NUM_THREADS':'1'})
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(summary(self.store)['replayed'],1)
    def test_mt5_history_adapter_utc_offset_and_chunk_boundaries(self):
        from replay_worker import MT5Ticks
        import numpy as np
        self.mt5.initialize=Mock(return_value=True);self.mt5.shutdown=Mock();self.mt5.COPY_TICKS_ALL=3
        calls=[]
        def history(symbol,start,end,flags):
            calls.append((start.timestamp(),end.timestamp()))
            return np.array([(t*1000,99.99,100.01) for t in range(int(start.timestamp()),int(end.timestamp())+1)],dtype=[('time_msc','f8'),('bid','f8'),('ask','f8')])
        self.mt5.copy_ticks_range=history
        with patch.dict('sys.modules',{'MetaTrader5':self.mt5}):
            source=MT5Ticks({},self.scope,2,query_offset=2)
            try:path=source.fetch('AUDUSD',NOW,NOW+600)
            finally:source.close()
        self.assertEqual(calls[0][0],NOW+7200);self.assertEqual(len(calls),2)
        self.assertEqual(path[0][0],NOW*1000);self.assertEqual(path[-1][0],(NOW+600)*1000)
        self.assertEqual(len(path),601);self.assertEqual(self.mt5.sent,[])
        # UTC query parameters and nonstandard raw timestamps need not share an offset.
        def utc_query_server_raw(symbol,start,end,flags):
            calls.append((start.timestamp(),end.timestamp()))
            return np.array([((t+7200)*1000,99.99,100.01) for t in range(int(start.timestamp()),int(end.timestamp())+1)],dtype=[('time_msc','f8'),('bid','f8'),('ask','f8')])
        self.mt5.copy_ticks_range=utc_query_server_raw
        with patch.dict('sys.modules',{'MetaTrader5':self.mt5}):
            source=MT5Ticks({},self.scope,2,query_offset=0)
            try:path=source.fetch('AUDUSD',NOW,NOW+300)
            finally:source.close()
        self.assertEqual(calls[-1][0],NOW);self.assertEqual(path[0][0],NOW*1000)
    def test_history_adapter_rejects_other_account_without_login_or_orders(self):
        from replay_worker import MT5Ticks
        self.mt5.initialize=Mock(return_value=True);self.mt5.shutdown=Mock()
        with patch.dict('sys.modules',{'MetaTrader5':self.mt5}):
            with self.assertRaises(ValueError):MT5Ticks({},'other-account',0)
        self.mt5.shutdown.assert_called_once();self.assertEqual(self.mt5.sent,[])
    def test_history_adapter_discards_data_if_account_changes_during_fetch(self):
        from replay_worker import MT5Ticks
        self.mt5.initialize=Mock(return_value=True);self.mt5.shutdown=Mock();self.mt5.COPY_TICKS_ALL=3
        def changed(*args):self.mt5.account.login+=1;return []
        self.mt5.copy_ticks_range=changed
        with patch.dict('sys.modules',{'MetaTrader5':self.mt5}):
            source=MT5Ticks({},self.scope,0)
            try:
                with self.assertRaises(ValueError):source.fetch('AUDUSD',NOW,NOW+300)
            finally:source.close()
    def test_history_failure_stays_unknown_and_retry_cooldown_visible(self):
        self.put();source=Mock(side_effect=ValueError('History unavailable'))
        run_batch(self.store,self.scope,self.cfg,source,now=NOW+400)
        r=summary(self.store);self.assertEqual(r['replayed'],0);self.assertEqual(r['health']['status'],'DATA UNAVAILABLE')
        source.reset_mock();run_batch(self.store,self.scope,self.cfg,source,now=NOW+401)
        source.assert_not_called();self.assertEqual(summary(self.store)['health']['status'],'DATA RETRY COOLDOWN')
        with self.store.connect() as db:self.assertIsNone(db.execute('SELECT label_r FROM ai_candidates').fetchone()[0])

    def test_http_registry_settings_and_same_origin_controls(self):
        import threading,urllib.request,urllib.error
        from dashboard_server import build_server
        server=build_server(self.store,0);threading.Thread(target=server.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(url+'/api/replay') as r:self.assertEqual(len(json.load(r)['versions']),2)
            body=canonical(dict(name='Research only',policy={'exit_target':'TP1'})).encode()
            bad=urllib.request.Request(url+'/api/replay-version',data=body,headers={'Origin':'https://bad.invalid','Content-Type':'application/json','X-Dashboard-Request':'controls'})
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(bad)
            self.assertEqual(e.exception.code,403)
            req=urllib.request.Request(url+'/api/replay-version',data=body,headers={'Origin':url,'Content-Type':'application/json','X-Dashboard-Request':'controls'})
            with urllib.request.urlopen(req) as r:self.assertTrue(json.load(r)['research_only'])
            req=urllib.request.Request(url+'/api/replay-settings',data=canonical({**A,'cost_r':.04}).encode(),headers={'Origin':url,'Content-Type':'application/json','X-Dashboard-Request':'controls'})
            with urllib.request.urlopen(req) as r:self.assertTrue(json.load(r)['ok'])
            self.assertEqual(settings(self.store)['cost_r'],.04)
            with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM strategy_versions').fetchone()[0],3)
        finally:server.shutdown();server.server_close()

    def test_no_live_execution_or_ai_label_update_in_modules(self):
        for name in ('replay_lab.py','replay_worker.py'):
            tree=ast.parse((Path(__file__).parent/name).read_text())
            attrs=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
            for forbidden in ('order_send','order_check','login','submit'):self.assertNotIn(forbidden,attrs)

class PairedStatistics(unittest.TestCase):
    def test_same_candidate_intersection_unknown_excluded_and_missed_winners(self):
        pairs=[dict(observed=NOW,base={'net_r':1},candidate={'net_r':0,'status':'SKIPPED'}),dict(observed=NOW+1,base={'net_r':-1},candidate={'net_r':None,'status':'INCONCLUSIVE'})]
        r=paired_comparison(pairs);self.assertEqual(r['matched'],1);self.assertEqual(r['inconclusive'],1)
        self.assertEqual(r['missed_winners'],1);self.assertEqual(r['delta_mean_r'],-1)
    def test_day_block_interval_reproducible_and_no_approval(self):
        pairs=[dict(observed=NOW+i*86400+j,base={'net_r':-.2},candidate={'net_r':0,'status':'SKIPPED'}) for i in range(15) for j in range(3)]
        a=paired_comparison(pairs);b=paired_comparison(pairs)
        self.assertEqual(a['paired_day_block_ci95'],b['paired_day_block_ci95']);self.assertEqual(a['status'],'UNDER REVIEW')
        self.assertEqual(a['avoided_losses'],45);self.assertIn('No approval',a['note'])

if __name__=='__main__':unittest.main()
