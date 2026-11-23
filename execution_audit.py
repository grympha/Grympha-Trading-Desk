"""Append-only execution events; no broker credentials or arbitrary response dumps."""
import json,math,time
SCHEMA='''CREATE TABLE IF NOT EXISTS execution_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT,account_scope TEXT NOT NULL,setup_key TEXT,strategy TEXT,symbol TEXT,
 epoch REAL NOT NULL,event TEXT NOT NULL,payload TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS audit_scope ON execution_audit(account_scope,id);
'''
def clean(v):
    if isinstance(v,float) and not math.isfinite(v):return None
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)):return [clean(x) for x in v]
    return v
class Audit:
    def __init__(self,store,scope):
        self.store,self.scope=store,scope
        with store.connect() as db:db.executescript(SCHEMA)
    def emit(self,event,signal=None,**payload):
        signal=signal or {}
        with self.store.connect() as db:db.execute('INSERT INTO execution_audit (account_scope,setup_key,strategy,symbol,epoch,event,payload) VALUES (?,?,?,?,?,?,?)',
             (self.scope,signal.get('setup_key') or signal.get('key'),signal.get('strategy'),signal.get('symbol'),time.time(),event,json.dumps(clean(payload),allow_nan=False)))

def summary(store,scope):
    with store.connect() as db:
        if not scope or not db.execute("SELECT 1 FROM sqlite_master WHERE name='execution_audit'").fetchone():return dict(total=0,events=[],checks=0,sends=0,rejections=0,uncertain=0,average_send_ms=None)
        counts={r['event']:r['n'] for r in db.execute('SELECT event,count(*) n FROM execution_audit WHERE account_scope=? GROUP BY event',(scope,))}
        recent=[dict(id=r['id'],setup_key=r['setup_key'],epoch=r['epoch'],event=r['event'],strategy=r['strategy'],symbol=r['symbol'],**json.loads(r['payload'])) for r in db.execute('SELECT * FROM execution_audit WHERE account_scope=? ORDER BY id DESC LIMIT 200',(scope,))]
        # Aggregates use all send events, independent of the UI's 200-event window.
        sends=[json.loads(r['payload']) for r in db.execute("SELECT payload FROM execution_audit WHERE account_scope=? AND event='ORDER_SEND'",(scope,))]
    durations=[d['latency_ms'] for d in sends if isinstance(d.get('latency_ms'),(int,float))]
    return dict(total=sum(counts.values()),events=recent,checks=counts.get('ORDER_CHECK',0),sends=len(sends),
                rejections=sum(d.get('retcode') not in (None,10008,10009,10010,10012,10031) for d in sends),
                uncertain=sum(d.get('retcode') in (None,10012,10031) for d in sends),average_send_ms=sum(durations)/len(durations) if durations else None)
