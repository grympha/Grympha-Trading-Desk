"""Historical bid/ask collector and replay worker. Never calls order_send/login.
Tick-history timestamps need a separately verified explicit offset.
"""
import argparse,csv,json,os,subprocess,sys,time,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from replay_lab import *

class ResearchLab:
    def __init__(self,store,cfg,mt5,config_path):
        self.store,self.cfg,self.mt5,self.config_path=store,cfg,mt5,str(Path(config_path).resolve())
        self.worker=None;self.started=0;self.last=0
        initialise(store,cfg)
        with store.connect() as db:db.execute("INSERT OR REPLACE INTO meta VALUES ('replay_scope','null')")
    def enrol(self,key):
        a=settings(self.store)
        if not ready(a):return
        with self.store.connect() as db:
            row=db.execute('SELECT account_scope,observed FROM ai_candidates WHERE key=?',(key,)).fetchone()
            if not row or not 0<=time.time()-row['observed']<=5:return
            versions=[r['id'] for r in db.execute('SELECT id FROM strategy_versions')]
            db.execute('INSERT OR IGNORE INTO replay_enrolments VALUES (?,?,?,?,?)',(key,row['account_scope'],profile_id(a),canonical(versions),time.time()))
    def refresh(self):
        if time.time()-self.last<60:return
        self.last=time.time()
        from ai_shadow import verified_scope
        scope=verified_scope(self.mt5)
        with self.store.connect() as db:db.execute("INSERT OR REPLACE INTO meta VALUES ('replay_scope',?)",(json.dumps(scope),))
        if self.worker:
            if self.worker.poll() is None:
                if time.time()-self.started>180:
                    self.worker.terminate();health(self.store,'WORKER TIMEOUT',scope)
                return
            if self.worker.returncode:health(self.store,'WORKER FAILED',scope)
            self.worker=None
        a=settings(self.store)
        if not scope or not ready(a):health(self.store,'WAITING FOR VERIFIED SOURCE / COSTS',scope);return
        kwargs=dict(stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
        if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0)
        self.worker=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--run','--db',str(self.store.path.resolve()),'--scope',scope,'--config',self.config_path],**kwargs)
        self.started=time.time()
    def close(self):
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()
            try:self.worker.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.worker.kill();self.worker.wait(timeout=2)

def health(store,status,scope=None,**details):
    with store.connect() as db:db.execute("INSERT OR REPLACE INTO meta VALUES ('replay_health',?)",(canonical(dict(status=status,updated_epoch=time.time(),scope=scope,**details)),))

def import_csv(store,scope,symbol,path,offset,verified):
    if not verified or not finite(offset) or not -14<=offset<=14:raise ValueError('Explicit verified tick history offset required')
    ticks=[]
    with Path(path).open(newline='',encoding='utf-8-sig') as stream:
        for row in csv.DictReader(stream):
            ticks.append([float(row['time_msc'])-offset*3600000,float(row['bid']),float(row['ask'])])
            if len(ticks)>250000:raise ValueError('Import limit 250000 ticks; split into smaller windows')
    if not ticks:raise ValueError('CSV has no ticks')
    # Validate without sorting or forward-filling: timestamps and data quality are evidence.
    issues=tick_quality(ticks,ticks[0][0]/1000,ticks[-1][0]/1000,{**DEFAULTS,'max_gap_seconds':60})
    if issues:raise ValueError('; '.join(issues))
    key=digest([scope,symbol,ticks]);metadata=dict(source='IMPORTED_BID_ASK_CSV',offset_hours=offset,offset_verified=True,content_hash=digest(ticks))
    with store.connect() as db:db.execute('INSERT OR IGNORE INTO replay_imports VALUES (?,?,?,?,?,?,?,?)',(key,scope,symbol,ticks[0][0]/1000,ticks[-1][0]/1000,time.time(),pack(ticks),canonical(metadata)))
    return key

class MT5Ticks:
    def __init__(self,cfg,scope,offset,query_offset=0.):
        import MetaTrader5 as mt5
        self.mt5,self.offset,self.scope,self.query_offset=mt5,offset,scope,query_offset
        if not mt5.initialize(**({'path':cfg['terminal_path']} if cfg.get('terminal_path') else {})):raise ValueError('Historical MT5 connection unavailable')
        from ai_shadow import verified_scope
        if verified_scope(mt5)!=scope:mt5.shutdown();raise ValueError('Historical source account mismatch or not verified DEMO')
    def fetch(self,symbol,start,end):
        from ai_shadow import verified_scope
        if verified_scope(self.mt5)!=self.scope:raise ValueError('Historical source account changed')
        ticks=[];cursor=start
        # Chunk the source request to bound memory before the total-window cap.
        while cursor<end:
            until=min(end,cursor+300)
            raw=self.mt5.copy_ticks_range(symbol,datetime.fromtimestamp(cursor+self.query_offset*3600,timezone.utc),datetime.fromtimestamp(until+self.query_offset*3600,timezone.utc),self.mt5.COPY_TICKS_ALL)
            if verified_scope(self.mt5)!=self.scope:raise ValueError('Historical source account changed during fetch')
            if raw is None:raise ValueError('MT5 tick history unavailable')
            if len(raw)+len(ticks)>250000:raise ValueError('Tick window exceeds limit; no truncation')
            for r in raw:
                tick=[float(r['time_msc'])-self.offset*3600000,float(r['bid']),float(r['ask'])]
                if start*1000<=tick[0]<=end*1000 and (not ticks or tick!=ticks[-1]):ticks.append(tick)
            cursor=until
        return ticks
    def close(self):self.mt5.shutdown()

def run_batch(store,scope,cfg,source_factory=MT5Ticks,now=None,retry_inconclusive=False):
    initialise(store,cfg);a=settings(store);now=time.time() if now is None else now
    if not ready(a):health(store,'NEEDS VERIFIED TICK OFFSET / COSTS',scope);return 0
    aid=profile_id(a);source=None;processed=0;failed=0;cooldown=0;last_error=None
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_candidates'").fetchone():return 0
        # Newly frozen versions can reuse historical immutable ticks, never new model back-scores.
        candidates=[dict(r) for r in db.execute('SELECT * FROM ai_candidates WHERE account_scope=? AND observed<=? ORDER BY observed,key',(scope,now-a['horizon_minutes']*60-2))]
        versions=[dict(r) for r in db.execute('SELECT * FROM strategy_versions ORDER BY created,id')]
    try:
        for candidate in candidates:
            if processed>=3:break
            start=candidate['observed'];end=start+a['horizon_minutes']*60
            with store.connect() as db:
                cached=db.execute('SELECT * FROM replay_jobs WHERE account_scope=? AND candidate_key=? AND assumptions_id=? ORDER BY created DESC LIMIT 1',(scope,candidate['key'],aid)).fetchone()
                imported=db.execute('SELECT * FROM replay_imports WHERE account_scope=? AND symbol=? AND start<=? AND end>=? ORDER BY created DESC LIMIT 1',(scope,candidate['symbol'],start+5,end-5)).fetchone()
                count=db.execute('SELECT count(*) FROM replay_results WHERE job_id=?',(cached['id'],)).fetchone()[0] if cached else 0
                attempt=db.execute('SELECT epoch,payload FROM replay_attempts WHERE account_scope=? AND candidate_key=? ORDER BY epoch DESC LIMIT 1',(scope,candidate['key'])).fetchone()
            if cached and retry_inconclusive:
                with store.connect() as db:
                    values=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM replay_results WHERE job_id=?',(cached['id'],))]
                if any(r.get('status')=='INCONCLUSIVE' for r in values):cached=None
            if cached and count==len(versions) and (not imported or imported['created']<=cached['created']):continue
            if not cached and attempt and now-attempt['epoch']<900 and not retry_inconclusive:
                cooldown+=1;last_error=json.loads(attempt['payload']).get('reason');continue
            try:
                if imported and (not cached or imported['created']>cached['created']):
                    ticks=[t for t in unpack(imported['ticks']) if start*1000<=t[0]<=end*1000]
                    name='IMPORTED_BID_ASK_CSV';metadata=json.loads(imported['metadata'])
                elif cached:
                    ticks=unpack(cached['ticks']);name=cached['source'];metadata=json.loads(cached['metadata'])
                else:
                    if source is None:source=source_factory(cfg,scope,a['tick_offset_hours'],query_offset=a['query_offset_hours'])
                    health(store,'FETCHING HISTORICAL TICKS',scope,symbol=candidate['symbol'])
                    ticks=source.fetch(candidate['symbol'],start,end);name='MT5_BID_ASK_HISTORY'
                    metadata=dict(offset_hours=a['tick_offset_hours'],query_offset_hours=a['query_offset_hours'],offset_verified=a['timestamp_verified'],source=name,start=start,end=end)
                store_job(store,scope,candidate,aid,ticks,name,metadata,versions)
                processed+=1
            except Exception as exc:
                with store.connect() as db:db.execute('INSERT INTO replay_attempts VALUES (?,?,?,?,?)',(uuid.uuid4().hex,scope,candidate['key'],now,canonical(dict(status='DATA UNAVAILABLE',reason=str(exc)))))
                last_error=str(exc);health(store,'DATA UNAVAILABLE',scope,reason=last_error);processed+=1;failed+=1
        health(store,'DATA UNAVAILABLE' if failed else 'BATCH COMPLETE' if processed else 'DATA RETRY COOLDOWN' if cooldown else 'WAITING / UP TO DATE',scope,processed=processed,failed=failed,last_error=last_error)
        return processed
    finally:
        if source:source.close()

if __name__=='__main__':
    from dashboard_store import Store
    parser=argparse.ArgumentParser();parser.add_argument('--db',default=str(Path(__file__).with_name('dashboard.sqlite3')));parser.add_argument('--config',default=str(Path(__file__).with_name('config.json')));parser.add_argument('--scope');parser.add_argument('--run',action='store_true');parser.add_argument('--import-csv');parser.add_argument('--symbol');parser.add_argument('--tick-offset-hours',type=float);parser.add_argument('--query-offset-hours',type=float,default=0.);parser.add_argument('--verified-offset',action='store_true');parser.add_argument('--diagnose',action='store_true');parser.add_argument('--retry-inconclusive',action='store_true')
    args=parser.parse_args();cfg=json.loads(Path(args.config).read_text()) if Path(args.config).exists() else {};store=Store(Path(args.db));initialise(store,cfg)
    if not args.scope:
        with store.connect() as db:
            r=db.execute("SELECT value FROM meta WHERE key='replay_scope'").fetchone();args.scope=json.loads(r['value']) if r else None
            if not args.scope:
                with store.connect() as db:
                    r=db.execute("SELECT value FROM meta WHERE key='ai_scope'").fetchone();args.scope=json.loads(r['value']) if r else None
    if args.diagnose:
        import MetaTrader5 as mt5
        if not mt5.initialize(**({'path':cfg['terminal_path']} if cfg.get('terminal_path') else {})):raise SystemExit('MT5 unavailable')
        try:
            print('PC UTC:',datetime.now(timezone.utc).isoformat())
            for symbol in cfg.get('symbols',[])[:9]:
                tick=mt5.symbol_info_tick(symbol);shift=timedelta(hours=args.query_offset_hours);raw=mt5.copy_ticks_range(symbol,datetime.now(timezone.utc)+shift-timedelta(minutes=2),datetime.now(timezone.utc)+shift,mt5.COPY_TICKS_ALL)
                print(symbol,'query offset hours:',args.query_offset_hours,'current raw as UTC:',datetime.fromtimestamp(tick.time,timezone.utc).isoformat() if tick else None,'history ticks in UTC query:',len(raw) if raw is not None else None,'last history raw as UTC:',datetime.fromtimestamp(float(raw[-1]['time_msc'])/1000,timezone.utc).isoformat() if raw is not None and len(raw) else None)
                if args.tick_offset_hours is not None and raw is not None and len(raw):
                    corrected=float(raw[-1]['time_msc'])/1000-args.tick_offset_hours*3600
                    print('  Declared raw offset:',args.tick_offset_hours,'corrected last UTC:',datetime.fromtimestamp(corrected,timezone.utc).isoformat(),'corrected age seconds:',round(time.time()-corrected,3))
            print('Read-only diagnostic. Verify history timestamps independently; do not infer an offset from a stationary quote or reuse quote offsets blindly.')
        finally:mt5.shutdown()
    elif not args.scope:raise SystemExit('No captured demo scope. Start the updated bot on the intended demo account first.')
    elif args.import_csv:
        if not args.symbol:raise SystemExit('--symbol required')
        print(import_csv(store,args.scope,args.symbol,args.import_csv,args.tick_offset_hours,args.verified_offset))
    elif args.run:run_batch(store,args.scope,cfg,retry_inconclusive=args.retry_inconclusive)
