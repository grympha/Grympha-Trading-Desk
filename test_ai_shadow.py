import ast,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import numpy as np
from dashboard_store import Store
from demo_execution import DemoExecutor
import test_demo_execution as fixture
from ai_shadow import ShadowScorer,FEATURES,vector,train,summary,predict

class AIShadow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db.sqlite3')
        self.cfg=dict(symbols=['AUDUSD'],poll_seconds=15,confluence=dict(symbols=['AUDUSD']),ai_shadow={'auto_train':False})
        self.store.start(self.cfg);self.mt5=fixture.FakeMT5();self.ex=DemoExecutor(self.mt5,self.store,self.cfg)
        self.scope=self.ex.account_hash;self.scorer=ShadowScorer(self.store,self.cfg,self.mt5)
    def tearDown(self):self.scorer.close();self.tmp.cleanup()
    def candidate(self,key='a',**changes):
        d=dict(strategy='BBMA',symbol='AUDUSD',health='OK',analysis_id=key,new_analysis=True,condition={'name':'BULLISH'},
               quote=dict(bid=1.1,ask=1.1001,spread=.0001),entry_plan=dict(id=key,side='BUY',entry=1.101,stop_loss=1.1,tp1=1.102,tp2=1.103,setup='CSA_REENTRY',order_idea='BUY_STOP',confirmation_utc='2026-11-19T00:00:00+00:00',expires_utc='2099-01-01T00:00:00+00:00'))
        d.update(changes);return d
    def put_label(self,key='a',status='CLOSED',net=12,risk=10,scope=None,updated=300):
        scope=scope or self.scope;setup='BBMA|AUDUSD:'+key
        with self.store.connect() as db:db.execute('INSERT OR REPLACE INTO demo_orders VALUES (?,?,?,?,?,?,?,?,?)',
             (scope+'|'+setup,scope,setup,'BBMA','AUDUSD',110,updated,status,json.dumps(dict(realised=net,risk=dict(estimated_loss=risk)))))
    def seed(self,n=400,days=50):
        rng=np.random.default_rng(9);start=1700000000
        with self.store.connect() as db:
            for i in range(n):
                observed=start+i*days*86400/n
                features={k:0. for k in FEATURES};features['spread_r']=float(rng.uniform(0,1));features['buy']=float(i%2)
                target=1.-3*features['spread_r']+float(rng.normal(0,.15))
                db.execute('INSERT INTO ai_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (f'{self.scope}|seed{i}',self.scope,f'seed{i}','BBMA','AUDUSD',observed,json.dumps(features),'{}','{}','CLOSED',target,observed+600,'BROKER'))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('ai_scope',?)",(json.dumps(self.scope),))
    def test_not_trained_not_fabricated_and_no_order_calls(self):
        r=self.candidate();self.scorer.capture(r,100)
        self.assertIsNone(r['ai_shadow']['estimated_r']);self.assertEqual(r['ai_shadow']['status'],'NOT TRAINED')
        self.assertEqual(self.mt5.sent,[]);self.assertEqual(summary(self.store)['candidates'],1)
    def test_candidate_features_and_prediction_freeze(self):
        self.scorer.capture(self.candidate(),100)
        changed=self.candidate(quote=dict(spread=.5));self.scorer.capture(changed,200)
        with self.store.connect() as db:row=db.execute('SELECT * FROM ai_candidates').fetchone()
        self.assertEqual(row['observed'],100);self.assertAlmostEqual(json.loads(row['features'])['spread_r'],.1)
        self.assertEqual(summary(self.store)['candidates'],1)
    def test_future_outcome_fields_never_enter_feature_vector(self):
        a=self.candidate();b=self.candidate(realised=999,exit_price=99,sampled_mfe_r=50)
        self.assertEqual(vector(a,100),vector(b,100));self.assertEqual(set(vector(a,100)),set(FEATURES))
    def test_missing_spread_fails_without_guessing(self):
        with self.assertRaises(ValueError):vector(self.candidate(quote={}),100)
    def test_rejected_and_pending_are_unlabelled(self):
        self.scorer.capture(self.candidate(),100)
        for status in ('REJECTED','PENDING','UNCERTAIN','BLOCKED_NEWS'):
            self.put_label(status=status);self.scorer.refresh(now=300)
            self.scorer.last_refresh=0
            self.assertEqual(summary(self.store)['labelled'],0)
        self.assertEqual(self.mt5.sent,[])
    def test_closed_net_over_initial_risk_and_unknown_risk(self):
        self.scorer.capture(self.candidate(),100);self.put_label(net=-12)
        self.scorer.refresh(now=300);self.assertAlmostEqual(summary(self.store)['recent'][0]['label_r'],-1.2)
        self.put_label(risk=None);self.scorer.last_refresh=0;self.scorer.refresh(now=330)
        self.assertEqual(summary(self.store)['labelled'],0)
    def test_other_account_not_joined_and_switch_hides_data(self):
        self.scorer.capture(self.candidate(),100);self.put_label(scope='other')
        self.scorer.refresh(now=300);self.assertEqual(summary(self.store)['labelled'],0)
        self.mt5.account.login=999;self.scorer.refresh(now=340)
        self.assertEqual(summary(self.store)['candidates'],0)
    def test_no_setup_and_cached_analysis_audited_not_candidates(self):
        self.scorer.capture(self.candidate(entry_plan=None),100)
        self.scorer.capture(self.candidate(new_analysis=False),110)
        s=summary(self.store);self.assertEqual(s['candidates'],0);self.assertGreater(s['decisions'],0)
    def test_insufficient_data_creates_no_model(self):
        r=train(self.store,self.scope,now=2000000000)
        self.assertEqual(r['status'],'COLLECTING DATA')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM ai_models').fetchone()[0],0)
    def test_days_gate_not_just_trade_count(self):
        self.seed(days=2);r=train(self.store,self.scope,now=2000000000)
        self.assertEqual(r['status'],'COLLECTING DATA')
    def test_time_purged_folds_train_only_scaling_and_immutable_versions(self):
        self.seed();r=train(self.store,self.scope,now=2000000000)
        self.assertEqual(r['status'],'TRAINED SHADOW ONLY')
        with self.store.connect() as db:m=json.loads(db.execute('SELECT payload FROM ai_models').fetchone()['payload'])
        self.assertLess(m['train_last_label'],m['holdout_start']-300)
        self.assertEqual(len(m['folds']),3)
        for f in m['folds']:self.assertLess(f['train_last_label'],f['validation_start']-300)
        self.assertEqual(m['mode'],'SHADOW');self.assertTrue(m['locked_risk_limits'])
        self.assertEqual(m['holdout']['count'],80)
        with self.store.connect() as db:
            rows=list(db.execute('SELECT * FROM ai_candidates ORDER BY observed'))
        training=[r for r in rows[:320] if r['label_available']<m['holdout_start']-300 and r['observed']<m['holdout_start']-300]
        mean=np.mean([json.loads(r['features'])['spread_r'] for r in training])
        self.assertAlmostEqual(m['mean'][FEATURES.index('spread_r')],mean)
        p=predict(m,{k:0. for k in FEATURES});self.assertEqual(p['mode'],'SHADOW');self.assertIsInstance(p['estimated_r'],float)
        train(self.store,self.scope,now=2000000001)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM ai_models').fetchone()[0],2)
    def test_unavailable_future_labels_excluded(self):
        self.seed()
        with self.store.connect() as db:db.execute('UPDATE ai_candidates SET label_available=2100000000')
        self.assertEqual(train(self.store,self.scope,now=2000000000)['labels'],0)
    def test_overlap_purge_refuses_bad_folds(self):
        self.seed()
        with self.store.connect() as db:db.execute('UPDATE ai_candidates SET label_available=1900000000')
        self.assertEqual(train(self.store,self.scope,now=2000000000)['status'],'INSUFFICIENT PURGED FOLDS')
    def test_background_process_only_after_enough_labels(self):
        self.cfg['ai_shadow']['auto_train']=True
        with patch('ai_shadow.subprocess.Popen') as spawn:
            self.scorer.refresh(now=2000000000);spawn.assert_not_called()
            self.seed();self.scorer.last_refresh=0;self.scorer.refresh(now=2000000040)
            spawn.assert_called_once();args=spawn.call_args.args[0]
            self.assertIn('--train',args);self.assertNotIn('bbma_bot.py',args)
            self.scorer.worker=None
    def test_real_cli_training_worker_writes_research_model(self):
        import subprocess,sys,os
        self.seed()
        result=subprocess.run([sys.executable,str(Path(__file__).parent/'ai_shadow.py'),'--train','--db',str(self.store.path),'--scope',self.scope],capture_output=True,text=True,timeout=30,env={**os.environ,'OPENBLAS_NUM_THREADS':'1'})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIsNotNone(summary(self.store)['model']);self.assertEqual(self.mt5.sent,[])
    def test_read_only_shadow_api(self):
        import threading,urllib.request
        from dashboard_server import build_server
        self.scorer.capture(self.candidate(),100)
        server=build_server(self.store,0);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/ai-shadow') as response:r=json.load(response)
            self.assertEqual(r['mode'],'SHADOW');self.assertEqual(r['candidates'],1)
            self.assertIsNone(r['recent'][0]['prediction']['estimated_r'])
        finally:server.shutdown();server.server_close()

    def test_no_execution_or_strategy_mutation_apis(self):
        tree=ast.parse((Path(__file__).parent/'ai_shadow.py').read_text())
        calls=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
        for forbidden in ('order_send','order_check','submit','save_config'):self.assertNotIn(forbidden,calls)
    def test_model_skip_never_blocks_executor_or_changes_entry_plan(self):
        from dashboard_journal import DashboardJournal
        from copy import deepcopy
        journal=DashboardJournal(self.mt5,self.store,self.cfg,fixture.N(),None,None,quote_age=None)
        journal.executor=Mock();result=self.candidate();original=deepcopy(result['entry_plan'])
        model=dict(id='research',feature_version=1,feature_names=list(FEATURES),mean=[0]*len(FEATURES),scale=[1]*len(FEATURES),coef=[0]*len(FEATURES),intercept=-1,evaluation_status='RESEARCH SCREEN FAILED')
        with patch('ai_shadow.latest_model',return_value=model):journal.publish(result)
        self.assertEqual(result['ai_shadow']['suggestion'],'MODEL SKIP')
        self.assertEqual(result['entry_plan'],original);journal.executor.submit.assert_called_once_with(result)
        journal.close()

    def test_prediction_failure_isolated_from_executor_handoff(self):
        from dashboard_journal import DashboardJournal
        journal=DashboardJournal(self.mt5,self.store,self.cfg,fixture.N(),None,None,quote_age=None)
        journal.shadow.capture=Mock(side_effect=RuntimeError('model error'));journal.executor=Mock()
        result=self.candidate()
        with self.assertLogs(level='WARNING'):journal.publish(result)
        journal.executor.submit.assert_called_once_with(result);journal.close()

if __name__=='__main__':unittest.main()
