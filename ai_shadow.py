"""Research-only ridge entry scoring. Features are captured before executor handoff.
No MT5 order calls, strategy edits, API keys, probability claims or promotion path.
"""
from datetime import datetime,timezone
import hashlib,json,math,os,subprocess,sys,time,uuid
from pathlib import Path
import numpy as np

SYMBOLS=('XAUUSD','GBPJPY','EURUSD','GBPUSD','USDJPY','AUDUSD','USDCAD','USDCHF','NZDUSD')
FEATURES=('buy','confluence','score','spread_r','tp1_r','tp2_r','hour_sin','hour_cos')+tuple(f'{tf}_{k}' for tf in ('H4','H1','M15','M5') for k in ('present','range_r','slope_r'))+tuple('pair_'+s for s in SYMBOLS)+('pair_other',)
VERSION=1
MIN_ROWS=300
MIN_DAYS=20
SCHEMA='''CREATE TABLE IF NOT EXISTS ai_candidates (
 key TEXT PRIMARY KEY,account_scope TEXT NOT NULL,setup_key TEXT NOT NULL,strategy TEXT NOT NULL,symbol TEXT NOT NULL,
 observed REAL NOT NULL,features TEXT NOT NULL,plan TEXT NOT NULL,prediction TEXT NOT NULL,
 execution_status TEXT NOT NULL DEFAULT 'NOT_SUBMITTED',label_r REAL,label_available REAL,label_source TEXT);
 CREATE INDEX IF NOT EXISTS ai_scope ON ai_candidates(account_scope,observed);
 CREATE TABLE IF NOT EXISTS ai_decisions (
 key TEXT PRIMARY KEY,account_scope TEXT NOT NULL,strategy TEXT NOT NULL,symbol TEXT NOT NULL,
 first_seen REAL NOT NULL,last_seen REAL NOT NULL,payload TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS ai_models (
 id TEXT PRIMARY KEY,account_scope TEXT NOT NULL,created REAL NOT NULL,payload TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS ai_training_runs (
 id TEXT PRIMARY KEY,account_scope TEXT NOT NULL,created REAL NOT NULL,payload TEXT NOT NULL);
'''

def finite(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def initialise(store):
    with store.connect() as db:db.executescript(SCHEMA)

def verified_scope(mt5):
    if mt5 is None:return None
    a=mt5.account_info();t=mt5.terminal_info()
    if a is None or t is None or not getattr(t,'connected',False) or getattr(a,'trade_mode',None)!=getattr(mt5,'ACCOUNT_TRADE_MODE_DEMO',0):return None
    from demo_execution import identity
    return identity(a)

def vector(result,now):
    p=result['entry_plan'];sign=1 if p.get('side')=='BUY' else -1
    if p.get('side') not in ('BUY','SELL') or not all(finite(p.get(k)) for k in ('entry','stop_loss','tp1','tp2')):raise ValueError('Incomplete plan')
    risk=abs(p['entry']-p['stop_loss'])
    if risk<=0:raise ValueError('Zero stop distance')
    q=result.get('quote') or {};spread=q.get('spread')
    if spread is None and finite(q.get('ask')) and finite(q.get('bid')):spread=q['ask']-q['bid']
    if not finite(spread) or spread<0:raise ValueError('Missing executable spread')
    h=datetime.fromtimestamp(now,timezone.utc);hour=h.hour+h.minute/60
    f=dict(buy=float(sign==1),confluence=float(result.get('strategy')=='CONFLUENCE'),score=float(result.get('score',0) or 0)/5,
           spread_r=spread/risk,tp1_r=sign*(p['tp1']-p['entry'])/risk,tp2_r=sign*(p['tp2']-p['entry'])/risk,
           hour_sin=math.sin(2*math.pi*hour/24),hour_cos=math.cos(2*math.pi*hour/24))
    market=result.get('review_market') or {}
    for tf in ('H4','H1','M15','M5'):
        bars=market.get(tf) or []
        bars=[b for b in bars if all(finite(b.get(k)) for k in ('open','high','low','close'))]
        f[tf+'_present']=float(bool(bars))
        f[tf+'_range_r']=sum(b['high']-b['low'] for b in bars)/len(bars)/risk if bars else 0.
        f[tf+'_slope_r']=sign*(bars[-1]['close']-bars[0]['close'])/risk if bars else 0.
    symbol=result['symbol']
    for s in SYMBOLS:f['pair_'+s]=float(symbol==s)
    f['pair_other']=float(symbol not in SYMBOLS)
    if not all(finite(f[k]) for k in FEATURES):raise ValueError('Non-finite feature')
    return f

def latest_model(db,scope):
    row=db.execute('SELECT payload FROM ai_models WHERE account_scope=? ORDER BY created DESC LIMIT 1',(scope,)).fetchone()
    return json.loads(row['payload']) if row else None

def predict(model,features):
    if not model:return dict(status='NOT TRAINED',estimated_r=None,suggestion='WAIT FOR DATA',model_id=None,mode='SHADOW')
    if model.get('feature_version')!=VERSION or model.get('feature_names')!=list(FEATURES):raise ValueError('Model feature schema mismatch')
    x=np.asarray([features[k] for k in FEATURES],dtype=float)
    value=float(((x-np.asarray(model['mean']))/np.asarray(model['scale']))@np.asarray(model['coef'])+model['intercept'])
    if not math.isfinite(value):raise ValueError('Non-finite model output')
    return dict(status='SHADOW ESTIMATE',estimated_r=value,suggestion='MODEL TAKE' if value>0 else 'MODEL SKIP',
                model_id=model['id'],evaluation=model['evaluation_status'],mode='SHADOW')

class ShadowScorer:
    def __init__(self,store,cfg,mt5):
        self.store,self.cfg,self.mt5=store,cfg,mt5;self.worker=None;self.worker_started=0;self.last_refresh=0;self.last_attempt=0
        initialise(store)
        with store.connect() as db:db.execute("INSERT OR REPLACE INTO meta VALUES ('ai_scope','null')")
    def capture(self,result,now=None):
        now=time.time() if now is None else now
        scope=verified_scope(self.mt5)
        if not scope:return
        strategy=result.get('strategy','BBMA');symbol=result.get('symbol');p=result.get('entry_plan')
        if strategy not in ('BBMA','CONFLUENCE') or not symbol:return
        candle=result.get('analysis_id') or ((result.get('chart') or [{}])[-1].get('time')) or int(now//300)
        decision=hashlib.sha256(f'{scope}|{strategy}|{symbol}|{candle}|{result.get("health")}|{bool(p)}'.encode()).hexdigest()
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('ai_scope',?)",(json.dumps(scope),))
            payload=dict(health=result.get('health'),reason=result.get('reason'),has_plan=bool(p),new_analysis=result.get('new_analysis'),mode='SHADOW')
            db.execute('INSERT INTO ai_decisions VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET last_seen=excluded.last_seen,payload=excluded.payload',
                       (decision,scope,strategy,symbol,now,now,json.dumps(payload)))
            # Bounded audit of non-entry/blocked scans; training candidates have separate retention.
            db.execute('DELETE FROM ai_decisions WHERE account_scope=? AND key NOT IN (SELECT key FROM ai_decisions WHERE account_scope=? ORDER BY last_seen DESC LIMIT 10000)',(scope,scope))
            if not p or result.get('health')!='OK' or result.get('new_analysis') is False:return
            setup_key=strategy+'|'+symbol+':'+str(p['id']);key=scope+'|'+setup_key
            old=db.execute('SELECT prediction FROM ai_candidates WHERE key=?',(key,)).fetchone()
            if old:result['ai_shadow']=json.loads(old['prediction']);return
            features=vector(result,now)
            try:prediction=predict(latest_model(db,scope),features)
            except Exception:prediction=dict(status='MODEL UNAVAILABLE',estimated_r=None,suggestion='WAIT FOR MODEL',model_id=None,mode='SHADOW')
            db.execute('INSERT INTO ai_candidates (key,account_scope,setup_key,strategy,symbol,observed,features,plan,prediction) VALUES (?,?,?,?,?,?,?,?,?)',
                       (key,scope,setup_key,strategy,symbol,now,json.dumps(features),json.dumps(p),json.dumps(prediction)))
            result['ai_shadow']=prediction
            return key
    def refresh(self,now=None):
        now=time.time() if now is None else now
        if now-self.last_refresh<30:return
        self.last_refresh=now;scope=verified_scope(self.mt5)
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('ai_scope',?)",(json.dumps(scope),))
            if not scope:return
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='demo_orders'").fetchone():
                rows=db.execute('SELECT c.key,c.observed,d.status,d.updated,d.payload FROM ai_candidates c JOIN demo_orders d ON c.account_scope=d.account_hash AND c.setup_key=d.setup_key WHERE c.account_scope=?',(scope,)).fetchall()
                for row in rows:
                    d=json.loads(row['payload']);risk=(d.get('risk') or {}).get('estimated_loss');net=d.get('realised')
                    label=net/risk if row['status']=='CLOSED' and finite(net) and finite(risk) and risk>0 and row['updated']>=row['observed'] else None
                    db.execute('UPDATE ai_candidates SET execution_status=?,label_r=?,label_available=?,label_source=? WHERE key=?',
                               (row['status'],label,row['updated'] if label is not None else None,'BROKER_NET_OVER_ESTIMATED_INITIAL_RISK' if label is not None else None,row['key']))
            count=db.execute('SELECT count(*) FROM ai_candidates WHERE account_scope=? AND label_r IS NOT NULL',(scope,)).fetchone()[0]
            model=latest_model(db,scope)
            db.execute("INSERT OR REPLACE INTO meta VALUES ('ai_health',?)",(json.dumps(dict(updated_epoch=now,mode='SHADOW',labelled=count,orders_affected=False)),))
        if self.worker:
            if self.worker.poll() is None:
                if now-self.worker_started>180:self.worker.terminate()
                return
            self.worker=None
        if not self.cfg.get('ai_shadow',{}).get('auto_train',True) or now-self.last_attempt<3600:return
        if count<MIN_ROWS or model and count-model['labelled_at_training']<50:return
        self.last_attempt=now
        kwargs=dict(stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1'})
        if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0)
        self.worker=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--train','--db',str(self.store.path.resolve()),'--scope',scope],**kwargs)
        self.worker_started=now
    def close(self):
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()
            try:self.worker.wait(timeout=2)
            except subprocess.TimeoutExpired:self.worker.kill()


def fit(rows):
    x=np.asarray([[json.loads(r['features'])[k] for k in FEATURES] for r in rows],dtype=float)
    y=np.asarray([r['label_r'] for r in rows],dtype=float)
    mean=x.mean(axis=0);scale=x.std(axis=0);scale[scale<1e-8]=1
    z=(x-mean)/scale;intercept=float(y.mean())
    coef=np.linalg.solve(z.T@z+10*np.eye(len(FEATURES)),z.T@(y-intercept))
    return dict(mean=mean.tolist(),scale=scale.tolist(),coef=coef.tolist(),intercept=intercept)

def evaluate(model,rows):
    predictions=[float(((np.asarray([json.loads(r['features'])[k] for k in FEATURES])-model['mean'])/model['scale'])@model['coef']+model['intercept']) for r in rows]
    targets=[r['label_r'] for r in rows];taken=[y for y,p in zip(targets,predictions) if p>0]
    pnl=[y if p>0 else 0 for y,p in zip(targets,predictions)]
    def dd(values):
        peak=level=maximum=0
        for v in values:level+=v;peak=max(peak,level);maximum=max(maximum,peak-level)
        return maximum
    return dict(count=len(rows),taken=len(taken),mae=float(np.mean(np.abs(np.asarray(predictions)-targets))),
                baseline_mae=float(np.mean(np.abs(np.asarray(targets)-model['intercept']))),
                baseline_sum_r=sum(targets),shadow_sum_r=sum(pnl),baseline_drawdown_r=dd(targets),shadow_drawdown_r=dd(pnl),
                expected_r_per_candidate=sum(pnl)/len(rows),baseline_r_per_candidate=sum(targets)/len(rows),
                note='Executed-only sample; cumulative R is descriptive, not an account equity backtest. Skipped opportunities lack counterfactual labels.')

def train(store,scope,now=None):
    now=time.time() if now is None else now;initialise(store)
    with store.connect() as db:
        rows=[dict(r) for r in db.execute('SELECT * FROM ai_candidates WHERE account_scope=? AND label_r IS NOT NULL AND label_available<=? ORDER BY observed,key',(scope,now))]
    rows=[r for r in rows if finite(r['label_r']) and r['label_available']>=r['observed']]
    days=len({datetime.fromtimestamp(r['observed'],timezone.utc).date() for r in rows})
    run=dict(status='COLLECTING DATA',labels=len(rows),days=days,minimum_labels=MIN_ROWS,minimum_days=MIN_DAYS,mode='SHADOW')
    if len(rows)>=MIN_ROWS and days>=MIN_DAYS:
        dev=rows[:int(len(rows)*.8)];hold=rows[len(dev):];gap=300
        folds=[]
        for lo,hi in ((.4,.6),(.6,.8),(.8,1.)):
            valid=dev[int(len(dev)*lo):int(len(dev)*hi)];start=valid[0]['observed']
            training=[r for r in dev if r['observed']<start-gap and r['label_available']<start-gap]
            if len(training)<40:continue
            model=fit(training);metric=evaluate(model,valid)
            folds.append(dict(train_count=len(training),train_last_label=max(r['label_available'] for r in training),validation_start=start,metrics=metric))
        boundary=hold[0]['observed'];training=[r for r in dev if r['observed']<boundary-gap and r['label_available']<boundary-gap]
        if len(folds)==3 and len(training)>=100 and len(hold)>=50:
            model=fit(training);test=evaluate(model,hold)
            # A research-screen result, not deployment approval or statistical proof.
            passes=test['taken']>=20 and test['shadow_sum_r']>test['baseline_sum_r'] and test['shadow_drawdown_r']<=test['baseline_drawdown_r'] and test['mae']<test['baseline_mae']
            digest=hashlib.sha256(json.dumps([(r['key'],r['observed'],r['features'],r['label_r'],r['label_available']) for r in rows]).encode()).hexdigest()
            model.update(id=hashlib.sha256((scope+digest+str(now)).encode()).hexdigest()[:16],feature_version=VERSION,feature_names=list(FEATURES),
                         created=now,numpy_version=np.__version__,algorithm='Ridge alpha=10, train-only scaling',data_hash=digest,labelled_at_training=len(rows),training_rows=len(training),
                         train_last_label=max(r['label_available'] for r in training),holdout_start=boundary,holdout=test,folds=folds,
                         evaluation_status='RESEARCH SCREEN PASSED' if passes else 'RESEARCH SCREEN FAILED',mode='SHADOW',
                         selection_bias='Only executed, closed trades with a recorded monetary risk estimate are labelled. Rejected and unexecuted candidates remain unlabelled. No causal policy evaluation.',
                         locked_risk_limits=True)
            with store.connect() as db:db.execute('INSERT INTO ai_models VALUES (?,?,?,?)',(model['id'],scope,now,json.dumps(model,allow_nan=False)))
            run.update(status='TRAINED SHADOW ONLY',model_id=model['id'],evaluation=model['evaluation_status'])
        else:run.update(status='INSUFFICIENT PURGED FOLDS')
    with store.connect() as db:db.execute('INSERT INTO ai_training_runs VALUES (?,?,?,?)',(uuid.uuid4().hex,scope,now,json.dumps(run)))
    return run

def summary(store):
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_candidates'").fetchone():return dict(status='NOT TRAINED',mode='SHADOW',candidates=0,labelled=0,recent=[],model=None)
        r=db.execute("SELECT value FROM meta WHERE key='ai_scope'").fetchone();scope=json.loads(r['value']) if r else None
        candidates=db.execute('SELECT * FROM ai_candidates WHERE account_scope=? ORDER BY observed DESC',(scope,)).fetchall() if scope else []
        model=latest_model(db,scope) if scope else None
        last=db.execute('SELECT payload FROM ai_training_runs WHERE account_scope=? ORDER BY created DESC LIMIT 1',(scope,)).fetchone() if scope else None
        decisions=db.execute('SELECT count(*) FROM ai_decisions WHERE account_scope=?',(scope,)).fetchone()[0] if scope else 0
        health=db.execute("SELECT value FROM meta WHERE key='ai_health'").fetchone()
        health=json.loads(health['value']) if health else {}
    return dict(status='DISABLED' if health.get('status')=='DISABLED' else 'TRAINED SHADOW ONLY' if model else 'NOT TRAINED',updated_epoch=health.get('updated_epoch'),mode='SHADOW',candidates=len(candidates),labelled=sum(r['label_r'] is not None for r in candidates),
                decisions=decisions,labelled_days=len({datetime.fromtimestamp(r['observed'],timezone.utc).date() for r in candidates if r['label_r'] is not None}),minimum_labels=MIN_ROWS,minimum_days=MIN_DAYS,last_training=json.loads(last['payload']) if last else None,
                model={k:v for k,v in model.items() if k not in ('coef','mean','scale')} if model else None,
                recent=[dict(strategy=r['strategy'],symbol=r['symbol'],observed=r['observed'],prediction=json.loads(r['prediction']),execution_status=r['execution_status'],label_r=r['label_r']) for r in candidates[:100]],
                note='SHADOW only: model suggestions never alter orders, sizing, SL/TP or risk limits. Targets are broker net / recorded initial risk estimate; executed-only selection bias remains. Unexecuted/rejected outcomes are unknown. Earlier predictions stay frozen.')

if __name__=='__main__':
    import argparse
    from dashboard_store import Store
    parser=argparse.ArgumentParser();parser.add_argument('--train',action='store_true');parser.add_argument('--db',required=True);parser.add_argument('--scope')
    args=parser.parse_args()
    if args.train:
        store=Store(Path(args.db))
        if not args.scope:
            with store.connect() as db:
                row=db.execute("SELECT value FROM meta WHERE key='ai_scope'").fetchone()
                args.scope=json.loads(row['value']) if row else None
            if not args.scope:raise SystemExit('No captured demo scope. Start the updated bot on the intended demo account first.')
        try:train(store,args.scope)
        except Exception as exc:
            initialise(store)
            with store.connect() as db:db.execute('INSERT INTO ai_training_runs VALUES (?,?,?,?)',(uuid.uuid4().hex,args.scope,time.time(),json.dumps(dict(status='TRAINING FAILED',error_type=type(exc).__name__,mode='SHADOW'))))
            raise SystemExit(1)
