"""Read-only counterfactual tick replay and immutable execution-policy research.
Simulated results are isolated from broker labels and the live executor.
"""
import hashlib,json,math,time,uuid,zlib
from collections import defaultdict
from functools import lru_cache
from datetime import datetime,timezone
import numpy as np

ENGINE='bidask-static-v1'
DEFAULTS=dict(enabled=True,tick_offset_hours=None,query_offset_hours=0.,timestamp_verified=False,cost_r=None,slippage_r=0.,latency_ms=200,horizon_minutes=120,max_gap_seconds=10)
SCHEMA='''CREATE TABLE IF NOT EXISTS replay_assumptions (id TEXT PRIMARY KEY,created REAL NOT NULL,payload TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS strategy_versions (id TEXT PRIMARY KEY,created REAL NOT NULL,name TEXT NOT NULL,payload TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS replay_jobs (id TEXT PRIMARY KEY,account_scope TEXT NOT NULL,candidate_key TEXT NOT NULL,assumptions_id TEXT NOT NULL,tick_hash TEXT NOT NULL,created REAL NOT NULL,source TEXT NOT NULL,ticks BLOB NOT NULL,metadata TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS replay_results (job_id TEXT NOT NULL,version_id TEXT NOT NULL,created REAL NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(job_id,version_id));
 CREATE TABLE IF NOT EXISTS replay_imports (id TEXT PRIMARY KEY,account_scope TEXT NOT NULL,symbol TEXT NOT NULL,start REAL NOT NULL,end REAL NOT NULL,created REAL NOT NULL,ticks BLOB NOT NULL,metadata TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS replay_enrolments (candidate_key TEXT PRIMARY KEY,account_scope TEXT NOT NULL,assumptions_id TEXT NOT NULL,version_ids TEXT NOT NULL,created REAL NOT NULL);
 CREATE TABLE IF NOT EXISTS replay_attempts (id TEXT PRIMARY KEY,account_scope TEXT NOT NULL,candidate_key TEXT NOT NULL,epoch REAL NOT NULL,payload TEXT NOT NULL);
'''

def finite(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def profile_id(values):return digest({k:v for k,v in values.items() if k!='enabled'})
def pack(ticks):return zlib.compress(canonical(ticks).encode())
def unpack(blob):return json.loads(zlib.decompress(blob))

def validate_settings(values):
    if not isinstance(values,dict) or set(values)-set(DEFAULTS):raise ValueError('Unknown replay setting')
    d={**DEFAULTS,**values}
    for k in ('enabled','timestamp_verified'):
        if not isinstance(d[k],bool):raise ValueError(k+' must be boolean')
    for k,lo,hi in [('tick_offset_hours',-14,14),('query_offset_hours',-14,14),('cost_r',0,5),('slippage_r',0,1),('latency_ms',0,10000),('horizon_minutes',5,1440),('max_gap_seconds',1,60)]:
        if d[k] is None and k in ('tick_offset_hours','cost_r'):continue
        if not finite(d[k]) or not lo<=d[k]<=hi:raise ValueError(k+' outside permitted range')
    if d['timestamp_verified'] and d['tick_offset_hours'] is None:raise ValueError('Explicit verified history offset required')
    return d

def ready(d):return d['enabled'] and d['timestamp_verified'] and d['tick_offset_hours'] is not None and d['cost_r'] is not None

def initialise(store,cfg=None):
    with store.connect() as db:
        db.executescript(SCHEMA)
        if not db.execute("SELECT 1 FROM meta WHERE key='replay_settings'").fetchone():
            d=validate_settings((cfg or {}).get('replay',{}));register_settings(db,d)
        if not db.execute('SELECT 1 FROM strategy_versions').fetchone():
            for name,policy in [('Plan baseline / static TP2',{}),('Candidate / full exit TP1',{'exit_target':'TP1'})]:
                add_version(db,name,policy,baseline=name.startswith('Plan baseline'))

def register_settings(db,values):
    d=validate_settings(values);key=profile_id(d)
    db.execute('INSERT OR IGNORE INTO replay_assumptions VALUES (?,?,?)',(key,time.time(),canonical({**d,'enabled':True})))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('replay_settings',?)",(canonical(d),))
    return key

def settings(store):
    with store.connect() as db:r=db.execute("SELECT value FROM meta WHERE key='replay_settings'").fetchone()
    return validate_settings(json.loads(r['value']) if r else {})

POLICY=dict(exit_target='TP2',sl_multiplier=1.,tp_multiplier=1.,max_spread_r=.25,min_model_r=None)
def validate_policy(values):
    if not isinstance(values,dict) or set(values)-set(POLICY):raise ValueError('Unknown policy field')
    d={**POLICY,**values}
    if d['exit_target'] not in ('TP1','TP2'):raise ValueError('Invalid exit target')
    for k,lo,hi in [('sl_multiplier',.25,3),('tp_multiplier',.25,3),('max_spread_r',.01,1),('min_model_r',-10,10)]:
        if k=='min_model_r' and d[k] is None:continue
        if not finite(d[k]) or not lo<=d[k]<=hi:raise ValueError('Invalid '+k)
    return d

def add_version(db,name,policy,baseline=False):
    if not isinstance(name,str) or not 1<=len(name.strip())<=80:raise ValueError('Name must be 1..80 characters')
    policy=validate_policy(policy)
    if baseline and db.execute("SELECT 1 FROM strategy_versions WHERE json_extract(payload,'$.baseline')=1").fetchone():raise ValueError('Baseline already exists')
    key=uuid.uuid4().hex[:16]
    db.execute('INSERT INTO strategy_versions VALUES (?,?,?,?)',(key,time.time(),name.strip(),canonical(dict(policy=policy,baseline=baseline,engine=ENGINE,scope='Static captured-plan execution policy; no scanner rerun or live deployment'))))
    return key

def tick_quality(ticks,start,end,assumptions):
    reasons=[]
    if not ticks:return ['No bid/ask ticks']
    if len(ticks)>250000:return ['Tick window exceeds resource limit; no truncation used']
    previous=None
    for tick in ticks:
        if not isinstance(tick,(list,tuple)) or len(tick)!=3 or not all(finite(v) for v in tick):return ['Invalid tick shape/number']
        ms,bid,ask=tick
        if bid<=0 or ask<bid:return ['Invalid or crossed bid/ask quote']
        if previous:
            if ms<previous[0]:return ['Ticks out of chronological order']
            if ms==previous[0] and tick!=previous:return ['Conflicting quotes at identical millisecond; event order uncertain']
        previous=tick
    if ticks[0][0]<start*1000-1 or ticks[-1][0]>end*1000+1:return ['Tick slice outside requested window']
    if ticks[0][0]/1000-start>5:reasons.append('Missing start boundary coverage (>5s)')
    if end-ticks[-1][0]/1000>5:reasons.append('Missing end boundary coverage (>5s)')
    if any((b[0]-a[0])/1000>assumptions['max_gap_seconds'] for a,b in zip(ticks,ticks[1:])):reasons.append('Quote gap exceeds declared maximum')
    return reasons

def replay(candidate,ticks,policy,assumptions):
    """Each tick is [UTC milliseconds,bid,ask]. No interpolation, bar-touch guessing or actual-label reuse."""
    policy=validate_policy(policy);a=validate_settings(assumptions)
    start=float(candidate['observed']);end=start+a['horizon_minutes']*60
    base=dict(engine=ENGINE,mode='SIMULATED',status='INCONCLUSIVE',net_r=None,reason='',entry=None,exit=None,mae_r=None,mfe_r=None)
    if not ready(a):return dict(base,reason='Verified tick offset and explicit cost assumption required')
    issues=tick_quality(ticks,start,end,a)
    if issues:return dict(base,reason='; '.join(issues))
    plan=json.loads(candidate['plan']) if isinstance(candidate['plan'],str) else candidate['plan']
    side=plan.get('side');sign=1 if side=='BUY' else -1
    if side not in ('BUY','SELL') or not all(finite(plan.get(k)) for k in ('entry','stop_loss','tp1','tp2')):return dict(base,reason='Invalid captured plan')
    ref=plan['entry'];unit=abs(ref-plan['stop_loss'])
    from structural_tp import targets_ordered
    if unit<=0 or not targets_ordered(side,ref,plan['stop_loss'],plan['tp1'],plan['tp2'],plan.get('structural_tp')):
        return dict(base,reason='Unusable original plan levels')
    # Only the model prediction actually frozen at capture is eligible for a version filter.
    pred=json.loads(candidate.get('prediction') or '{}') if isinstance(candidate.get('prediction'),str) else candidate.get('prediction') or {}
    if policy['min_model_r'] is not None:
        if not finite(pred.get('estimated_r')):return dict(base,reason='No frozen entry-time model estimate; cannot back-score with future model')
        if pred['estimated_r']<policy['min_model_r']:return dict(base,status='SKIPPED',net_r=0.,reason='Frozen model filter')
    sl=ref-sign*unit*policy['sl_multiplier']
    target=ref+sign*abs(plan[policy['exit_target'].lower()]-ref)*policy['tp_multiplier']
    eligible=start*1000+a['latency_ms'];path=[t for t in ticks if t[0]>=eligible]
    if not path:return dict(base,reason='No tick after declared latency')
    if path[0][0]-eligible>5000:return dict(base,reason='Missing admission-time tick coverage')
    first=path[0];quote=first[2] if sign==1 else first[1]
    if (first[2]-first[1])/unit>policy['max_spread_r']:return dict(base,status='NOT_ADMITTED',net_r=0.,reason='Declared spread filter at admission')
    market=plan.get('signal_kind')=='REFERENCE' or plan.get('order_idea')=='QUOTE_REFERENCE'
    if not market and plan.get('order_idea') not in (None,side+'_STOP'):return dict(base,reason='Unsupported captured pending-order type')
    entry_tick=None
    if market:
        if abs(quote-ref)>.25*unit:return dict(base,status='NOT_ADMITTED',net_r=0.,reason='Market reference moved beyond 0.25 original R')
        entry_tick=first
    else:
        try:
            expiry_time=datetime.fromisoformat(plan['expires_utc'])
            if expiry_time.tzinfo is None:raise ValueError('Naive expiry timestamp')
            expiry=expiry_time.timestamp()*1000
        except (KeyError,ValueError,TypeError):return dict(base,reason='Pending expiry unavailable')
        if first[0]>=expiry:return dict(base,status='NOT_ADMITTED',net_r=0.,reason='Pending validity expired before simulated admission')
        if expiry>end*1000:return dict(base,reason='Horizon shorter than pending validity; cannot infer unfilled outcome')
        if (quote>=ref if sign==1 else quote<=ref):return dict(base,status='NOT_ADMITTED',net_r=0.,reason='Pending stop already crossed at simulated admission')
        for tick in path[1:]:
            if tick[0]>expiry:break
            if (tick[2]>=ref if sign==1 else tick[1]<=ref):entry_tick=tick;break
        if entry_tick is None:return dict(base,status='UNFILLED',net_r=0.,reason='No pending trigger in fully covered validity window')
    at=entry_tick[0];entry_quote=entry_tick[2] if sign==1 else entry_tick[1]
    fill=(entry_quote if market else max(ref,entry_quote) if sign==1 else min(ref,entry_quote))+sign*a['slippage_r']*unit
    if not (sl<fill<target if sign==1 else sl>fill>target):return dict(base,reason='Fill would invalidate protection bracket; broker acceptance not assumed')
    mae=mfe=0.
    for tick in path:
        if tick[0]<at:continue
        mark=tick[1] if sign==1 else tick[2]
        move=sign*(mark-fill)/unit;mae=max(mae,-move);mfe=max(mfe,move)
        stop=(mark<=sl if sign==1 else mark>=sl);take=(mark>=target if sign==1 else mark<=target)
        if stop or take:
            # Stops allow adverse gaps; profit exits receive no favourable gap bonus.
            price=(mark if stop else target)-sign*a['slippage_r']*unit
            return dict(base,status='CLOSED',net_r=sign*(price-fill)/unit-a['cost_r'],reason='SL' if stop else policy['exit_target'],entry=fill,exit=price,entry_epoch=at/1000,exit_epoch=tick[0]/1000,mae_r=mae,mfe_r=mfe)
    last=path[-1];mark=last[1] if sign==1 else last[2];price=mark-sign*a['slippage_r']*unit
    return dict(base,status='CLOSED',net_r=sign*(price-fill)/unit-a['cost_r'],reason='HORIZON_EXIT',entry=fill,exit=price,entry_epoch=at/1000,exit_epoch=last[0]/1000,mae_r=mae,mfe_r=mfe)

def store_job(store,scope,candidate,assumptions_id,ticks,source,metadata,versions=None):
    if candidate.get('account_scope')!=scope:raise ValueError('Candidate account mismatch')
    tick_hash=digest(ticks);job_id=digest([scope,candidate['key'],assumptions_id,tick_hash,ENGINE,source,metadata])
    with store.connect() as db:
        cfg=db.execute('SELECT payload FROM replay_assumptions WHERE id=?',(assumptions_id,)).fetchone()
        if not cfg:raise ValueError('Unknown frozen assumptions')
        a=json.loads(cfg['payload'])
        versions=versions or [dict(r) for r in db.execute('SELECT * FROM strategy_versions ORDER BY created,id')]
        existing={r['version_id'] for r in db.execute('SELECT version_id FROM replay_results WHERE job_id=?',(job_id,))}
    # CPU work and compression occur outside the SQLite write transaction, so
    # research never holds the execution journal's writer lock while simulating.
    blob=pack(ticks);evaluated=[]
    for row in versions:
        if row['id'] in existing:continue
        definition=json.loads(row['payload']);result=replay(candidate,ticks,definition['policy'],a)
        evaluated.append((job_id,row['id'],time.time(),canonical(result)))
    with store.connect() as db:
        db.execute('INSERT OR IGNORE INTO replay_jobs VALUES (?,?,?,?,?,?,?,?,?)',(job_id,scope,candidate['key'],assumptions_id,tick_hash,time.time(),source,blob,canonical(metadata)))
        db.executemany('INSERT OR IGNORE INTO replay_results VALUES (?,?,?,?)',evaluated)
    return job_id

@lru_cache(maxsize=128)
def block_interval(days):
    if len(days)<10:return None
    rng=np.random.default_rng(0);samples=[];block=min(5,len(days))
    for _ in range(1000):
        picked=[]
        while len(picked)<len(days):
            start=int(rng.integers(0,len(days)-block+1));picked.extend(days[start:start+block])
        picked=picked[:len(days)];samples.append(sum(x[0] for x in picked)/sum(x[1] for x in picked))
    return [float(x) for x in np.quantile(samples,[.025,.975])]

def stats(values):
    if not values:return dict(count=0,sum_r=None,mean_r=None,drawdown_r=None,wins=0,losses=0)
    level=peak=dd=0.
    for v in values:level+=v;peak=max(peak,level);dd=max(dd,peak-level)
    return dict(count=len(values),sum_r=sum(values),mean_r=sum(values)/len(values),drawdown_r=dd,wins=sum(v>0 for v in values),losses=sum(v<0 for v in values))

def paired_comparison(pairs):
    """Paired candidate outcomes, chronological entry order. Days are block-resampled, not individual trades."""
    pairs=sorted(pairs,key=lambda p:p['observed'])
    usable=[p for p in pairs if p['base'].get('net_r') is not None and p['candidate'].get('net_r') is not None]
    baseline=stats([p['base']['net_r'] for p in usable]);challenger=stats([p['candidate']['net_r'] for p in usable])
    by_day=defaultdict(lambda:[0.,0])
    for p in usable:
        day=datetime.fromtimestamp(p['observed'],timezone.utc).date().isoformat()
        by_day[day][0]+=p['candidate']['net_r']-p['base']['net_r'];by_day[day][1]+=1
    days=[by_day[k] for k in sorted(by_day)]
    ci=block_interval(tuple(tuple(x) for x in days))
    return dict(matched=len(usable),inconclusive=len(pairs)-len(usable),days=len(days),baseline=baseline,candidate=challenger,
                delta_mean_r=challenger['mean_r']-baseline['mean_r'] if usable else None,paired_day_block_ci95=ci,
                avoided_losses=sum(p['base']['net_r']<0 and p['candidate']['status']=='SKIPPED' for p in usable),
                missed_winners=sum(p['base']['net_r']>0 and p['candidate']['status']=='SKIPPED' for p in usable),
                status='UNDER REVIEW' if len(usable)>=30 and len(days)>=10 else 'COLLECTING DATA',
                note='Descriptive independent-trade R, not account equity/margin replay. Paired CI is exploratory, uses 5-day moving blocks, and is not adjusted for testing multiple versions. No approval or live promotion.')

def summary(store,strategy='ALL',symbol='ALL'):
    if strategy not in ('ALL','BBMA','CONFLUENCE','ORB'):raise ValueError('Unknown strategy filter')
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='strategy_versions'").fetchone():return dict(status='WAITING',versions=[],comparisons=[],recent=[])
        raw=db.execute("SELECT value FROM meta WHERE key='replay_scope'").fetchone();scope=json.loads(raw['value']) if raw else None
        d=settings(store);aid=profile_id(d)
        config=db.execute('SELECT created FROM replay_assumptions WHERE id=?',(aid,)).fetchone()
        definitions=[dict(r) for r in db.execute('SELECT * FROM strategy_versions ORDER BY created,id')]
        versions=[dict(id=r['id'],name=r['name'],created=r['created'],**json.loads(r['payload'])) for r in definitions]
        health=db.execute("SELECT value FROM meta WHERE key='replay_health'").fetchone()
        rows=list(db.execute('SELECT j.id,j.account_scope,j.candidate_key,j.assumptions_id,j.tick_hash,j.created,j.source,j.metadata,c.observed,c.strategy,c.symbol,c.execution_status,c.plan,c.prediction FROM replay_jobs j JOIN ai_candidates c ON j.candidate_key=c.key AND j.account_scope=c.account_scope WHERE j.account_scope=? AND j.assumptions_id=? ORDER BY j.created DESC',(scope,aid))) if scope and db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_candidates'").fetchone() else []
        # One latest frozen tick dataset per candidate for the current assumption profile.
        rows=[r for r in rows if (strategy=='ALL' or r['strategy']==strategy) and (symbol=='ALL' or r['symbol']==symbol)]
        latest={}
        for row in rows:latest.setdefault(row['candidate_key'],row)
        enrolments={r['candidate_key']:dict(r) for r in db.execute('SELECT * FROM replay_enrolments WHERE account_scope=?',(scope,))} if scope else {}
        result_rows=list(db.execute('SELECT r.* FROM replay_results r JOIN replay_jobs j ON r.job_id=j.id WHERE j.account_scope=? AND j.assumptions_id=?',(scope,aid))) if scope else []
        results={(r['job_id'],r['version_id']):json.loads(r['payload']) for r in result_rows}
        count=db.execute("SELECT count(*) FROM ai_candidates WHERE account_scope=? AND (?='ALL' OR strategy=?) AND (?='ALL' OR symbol=?)",(scope,strategy,strategy,symbol,symbol)).fetchone()[0] if scope and db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_candidates'").fetchone() else 0
    baseline=next((v for v in versions if v['baseline']),None);comparisons=[];recent=[]
    if baseline:
        for version in versions:
            if version['baseline']:continue
            pairs=[]
            for row in latest.values():
                base=results.get((row['id'],baseline['id']));other=results.get((row['id'],version['id']))
                if base is None or other is None:continue
                enrolled=enrolments.get(row['candidate_key']) or {}
                enrolled_versions=json.loads(enrolled.get('version_ids','[]'))
                forward=enrolled.get('assumptions_id')==aid and version['id'] in enrolled_versions and baseline['id'] in enrolled_versions and row['observed']>max(version['created'],baseline['created'],config['created'] if config else float('inf'))
                pairs.append(dict(observed=row['observed'],base=base,candidate=other,forward=forward))
            comparisons.append(dict(version_id=version['id'],name=version['name'],all=paired_comparison(pairs),forward=paired_comparison([p for p in pairs if p['forward']]),research_only=True))
        for row in sorted(latest.values(),key=lambda r:r['observed'],reverse=True)[:100]:
            recent.append(dict(symbol=row['symbol'],strategy=row['strategy'],observed=row['observed'],actual_status=row['execution_status'],source=row['source'],tick_hash=row['tick_hash'],source_metadata=json.loads(row['metadata']),plan=json.loads(row['plan']),prediction=json.loads(row['prediction']),versions=[dict(id=v['id'],name=v['name'],result=results.get((row['id'],v['id']))) for v in versions],baseline=results.get((row['id'],baseline['id']))))
    return dict(status='DISABLED' if not d['enabled'] else 'READY FOR REPLAY' if ready(d) else 'NEEDS VERIFIED TICK OFFSET / EXPLICIT COSTS',settings=d,assumptions_id=aid,
                candidate_count=count,replayed=len(latest),versions=versions,comparisons=comparisons,recent=recent,
                health=json.loads(health['value']) if health and (json.loads(health['value']) or {}).get('scope')==scope else None,
                note='SIMULATED forced setup execution. Historical risk/news/broker admission decisions are not overridden in the live bot. Static SL/full-target policies; no partial, BE/trailing, strategy re-scan or portfolio-capacity simulation. Counterfactual outcomes never enter broker AI labels. No automatic deployment.')
