"""Module 5: local, read-only trade research. No MT5 order or AI API calls.
Only confirmed closed bot-ledger trades enter outcome statistics. Observed MAE/MFE
are sampled lower bounds, never tick-complete reconstructions or causal findings.
"""
from collections import defaultdict
from datetime import date, datetime, time as midnight_time, timedelta, timezone
import json
import math
import time

SCHEMA='''CREATE TABLE IF NOT EXISTS trade_reviews (
 key TEXT PRIMARY KEY,account_scope TEXT NOT NULL,strategy TEXT NOT NULL,symbol TEXT NOT NULL,
 source_updated REAL NOT NULL,closed_epoch REAL NOT NULL,payload TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS trade_review_samples (
 key TEXT PRIMARY KEY,account_scope TEXT NOT NULL,payload TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS review_account ON trade_reviews(account_scope,closed_epoch);
'''
REASONS={0:'Desktop/manual',1:'Mobile',2:'Web',3:'Expert / algorithm',4:'Stop loss',5:'Take profit',6:'Stop-out'}

def number(value):
    return isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value)

def market_snapshot(frames):
    return {name:[{k:float(v) for k,v in row.items() if number(v)} for row in frame.tail(8).to_dict('records')]
            for name,frame in frames.items()}

def capture(result,cfg,settings):
    epoch=time.time()
    return dict(version=1,captured_epoch=epoch,utc_hour=datetime.fromtimestamp(epoch,timezone.utc).hour,
                condition=result.get('condition'),setup=result.get('entry_plan',{}).get('setup'),
                score=result.get('score'),min_score=result.get('min_score'),factors=result.get('factors'),
                quote=result.get('quote'),timeframes=result.get('review_market',{}),
                controls=settings,poll_seconds=cfg.get('poll_seconds'),
                tick_offset=cfg.get('tick_timestamp_offset_hours',0),bar_offset=cfg.get('bar_timestamp_offset_hours',0))

def analyse(row,p,samples):
    entry,sl,reference=p.get('actual_entry'),p.get('sl'),p.get('reference')
    sign=1 if p.get('side')=='BUY' else -1
    unit_r=abs(entry-sl) if number(entry) and number(sl) else None
    unit_r=unit_r if unit_r and unit_r>0 else None
    realised=p.get('realised');context=p.get('context') or {};before=context.get('review') or {}
    currency=p.get('currency') or 'UNKNOWN'
    complete=bool(before.get('timeframes'))
    observations=samples or {};count=observations.get('count',0)
    mfe=observations.get('mfe_price') if count else None
    mae=observations.get('mae_price') if count else None
    slippage=sign*(entry-reference) if number(entry) and number(reference) else None
    risk=(p.get('risk') or {}).get('estimated_loss')
    outcome='UNKNOWN' if not number(realised) else 'WIN' if realised>0 else 'LOSS' if realised<0 else 'FLAT'
    tp1_r=sign*(p['tp1']-entry)/unit_r if unit_r and number(p.get('tp1')) else None
    flags=[]
    if not before:flags.append('LEGACY: no pre-entry research capture')
    elif not complete:flags.append('Multi-timeframe capture unavailable')
    if not count:flags.append('No live excursion samples; MAE/MFE unavailable')
    else:flags.append('MAE/MFE are sampled lower bounds; ticks between polls are unknown')
    if observations.get('max_gap_seconds',0)>max(60,(before.get('poll_seconds') or 15)*3):flags.append('Sampling gap detected')
    if not number(realised):flags.append('Realised P/L unavailable; excluded from outcome totals')
    # Limited audit: never claim full strategy compliance from an outcome alone.
    from structural_tp import targets_ordered
    bracket=targets_ordered(p['side'],entry,sl,p['tp1'],p['tp2'],p.get('structural_tp')) if all(number(v) for v in [sl,entry,p.get('tp1'),p.get('tp2')]) else None
    news=p.get('news')
    audit=dict(original_levels_bracket_fill=bracket,entry_news_gate_clear=(not news['blocked']) if isinstance(news,dict) and 'blocked' in news else None,
               broker_precheck_accepted=True if p.get('request') and p.get('retcode') in (10008,10009,10010) else None)
    tags=[]
    if p.get('close_reason')==4:tags.append('SL_EXIT')
    if outcome=='LOSS' and unit_r and mfe is not None and tp1_r is not None and mfe/unit_r>=tp1_r:tags.append('SAMPLED_TP1_THEN_NET_LOSS')
    if slippage is not None and unit_r and slippage/unit_r>=.1:tags.append('ADVERSE_FILL_GE_0_1R')
    if any(v is False for v in audit.values()):tags.append('LIMITED_AUDIT_REVIEW')
    rec=dict(key=row['key'],strategy=row['strategy'],symbol=row['symbol'],setup=context.get('setup') or before.get('setup') or 'UNKNOWN',
        side=p.get('side'),created=row['created'],closed_epoch=p.get('exit_epoch') or row['updated'],
        entry_epoch=p.get('entry_epoch'),exit_epoch=p.get('exit_epoch'),
        position_ticket=p.get('position_ticket'),outcome=outcome,realised=realised if number(realised) else None,currency=currency,
        entry=entry,reference=reference,sl=sl,tp1=p.get('tp1'),tp2=p.get('tp2'),final_sl=p.get('actual_sl'),final_tp=p.get('actual_tp'),exit_price=p.get('exit_price'),
        exit_reason=REASONS.get(p.get('close_reason'),'Broker reason '+str(p.get('close_reason'))),
        slippage_price=slippage,slippage_r=slippage/unit_r if unit_r and slippage is not None else None,
        realised_r_estimate=realised/risk if number(realised) and number(risk) and risk>0 else None,
        sampled_mfe_r=mfe/unit_r if unit_r and mfe is not None else None,sampled_mae_r=mae/unit_r if unit_r and mae is not None else None,
        samples=count,max_sample_gap_seconds=observations.get('max_gap_seconds'),sample_started=observations.get('first_epoch'),
        utc_hour=before.get('utc_hour'),tags=tags,limited_audit=audit,data_quality=flags,before=before,
        protection_samples=observations.get('protection_samples',[]),management=p.get('management') or {},tp1_observed=bool(p.get('tp1_observed')),
        review_status='UNDER REVIEW' if tags else 'COLLECTING DATA')
    rec['be']=is_breakeven(rec)
    return rec

class TradeReviewer:
    def __init__(self,store,cfg,mt5=None):
        self.store,self.cfg,self.mt5=store,cfg,mt5;self.last_run=0
        with store.connect() as db:
            db.executescript(SCHEMA)
            db.execute("INSERT OR REPLACE INTO meta VALUES ('trade_review_scope','null')")
            db.execute("INSERT OR REPLACE INTO meta VALUES ('trade_review_health',?)",(json.dumps(dict(status='WAITING',updated_epoch=None,automatic_changes=False)),))

    def refresh(self,force=False,now=None):
        now=time.time() if now is None else now
        if not force and now-self.last_run<30:return
        self.last_run=now
        with self.store.connect() as db:
            scope_row=db.execute("SELECT value FROM meta WHERE key='demo_scope'").fetchone()
            scope=json.loads(scope_row['value']) if scope_row else None
            if self.mt5 is not None:
                from demo_execution import identity
                account=self.mt5.account_info();terminal=self.mt5.terminal_info()
                if account is None or terminal is None or not getattr(terminal,'connected',False) or getattr(account,'trade_mode',None) not in (getattr(self.mt5,'ACCOUNT_TRADE_MODE_DEMO',0),getattr(self.mt5,'ACCOUNT_TRADE_MODE_REAL',2)):scope=None
                elif identity(account)!=scope:scope=None
            db.execute("INSERT OR REPLACE INTO meta VALUES ('trade_review_scope',?)",(json.dumps(scope),))
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='demo_orders'").fetchone()
            if not scope or not exists:
                db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('trade_review_health',json.dumps(dict(updated_epoch=now,status='WAITING FOR VERIFIED EXECUTION LEDGER',automatic_changes=False))))
                return
            rows=list(db.execute("SELECT * FROM demo_orders WHERE account_hash=? AND status IN ('OPEN','OPEN_PARTIAL','CLOSED')",(scope,)))
            for row in rows:
                p=json.loads(row['payload'])
                old=db.execute('SELECT payload FROM trade_review_samples WHERE key=?',(row['key'],)).fetchone()
                samples=json.loads(old['payload']) if old else {}
                if row['status'] in ('OPEN','OPEN_PARTIAL'):
                    epoch=p.get('last_broker_epoch',0)
                    if p.get('quote_fresh') is not True or not number(epoch) or not 0<=now-epoch<=60:continue
                    if not number(p.get('current')) or not number(p.get('actual_entry')):continue
                    if epoch<=samples.get('last_epoch',0):continue
                    move=(p['current']-p['actual_entry'])*(1 if p.get('side')=='BUY' else -1)
                    prior=samples.get('last_epoch',epoch)
                    protection=samples.get('protection_samples',[])
                    state=dict(sl=p.get('actual_sl'),tp=p.get('actual_tp'),volume=p.get('volume'))
                    if not protection or any(protection[-1].get(k)!=v for k,v in state.items()):
                        protection=(protection+[dict(epoch=epoch,**state)])[-200:]
                    samples=dict(protection_samples=protection,count=samples.get('count',0)+1,first_epoch=samples.get('first_epoch',epoch),last_epoch=epoch,
                                 max_gap_seconds=max(samples.get('max_gap_seconds',0),epoch-prior),
                                 mfe_price=max(samples.get('mfe_price',0),move,0),mae_price=max(samples.get('mae_price',0),-move,0))
                    db.execute('INSERT OR REPLACE INTO trade_review_samples VALUES (?,?,?)',(row['key'],scope,json.dumps(samples,allow_nan=False)))
                else:
                    reviewed=db.execute('SELECT source_updated FROM trade_reviews WHERE key=?',(row['key'],)).fetchone()
                    if reviewed and reviewed['source_updated']==row['updated']:continue
                    report=analyse(row,p,samples)
                    db.execute('INSERT OR REPLACE INTO trade_reviews VALUES (?,?,?,?,?,?,?)',(row['key'],scope,row['strategy'],row['symbol'],row['updated'],report['closed_epoch'],json.dumps(report,allow_nan=False)))
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('trade_review_health',json.dumps(dict(updated_epoch=now,status='OBSERVING',automatic_changes=False))))


def date_boundary(value, next_day=False):
    day = date.fromisoformat(value)
    if day.isoformat() != value:
        raise ValueError('Use YYYY-MM-DD')
    if next_day:
        day += timedelta(days=1)
    return datetime.combine(day, midnight_time.min, timezone(timedelta(hours=8))).timestamp()


def is_breakeven(r):
    """A stop-loss exit whose final stop had been moved to entry or better (break-even / protected)."""
    if r.get('exit_reason') != 'Stop loss':
        return False
    entry, final_sl, side = r.get('entry'), r.get('final_sl'), r.get('side')
    if not (number(entry) and number(final_sl) and side in ('BUY', 'SELL')):
        return False
    sign = 1 if side == 'BUY' else -1
    return sign * (final_sl - entry) >= 0


def pair_summary(reports):
    groups = defaultdict(list)
    for report in reports:
        groups[(report['symbol'], report['currency'])].append(report)
    return [
        dict(symbol=symbol, currency=currency, total=len(group),
             tp=sum(r['exit_reason'] == 'Take profit' for r in group),
             be=sum(is_breakeven(r) for r in group),
             sl=sum(r['exit_reason'] == 'Stop loss' and not is_breakeven(r) for r in group),
             other=sum(r['exit_reason'] not in ('Take profit', 'Stop loss') for r in group),
             wins=sum(r['outcome'] == 'WIN' for r in group),
             losses=sum(r['outcome'] == 'LOSS' for r in group),
             flat=sum(r['outcome'] == 'FLAT' for r in group),
             unknown=sum(r['outcome'] == 'UNKNOWN' for r in group),
             net=sum(known) if (known := [r['realised'] for r in group if r['realised'] is not None]) else None)
        for (symbol, currency), group in sorted(groups.items())
    ]


def summary(store,strategy='ALL',symbol='ALL',start_date=None,end_date=None,*,all_reviews=False):
    if strategy not in ('ALL','BBMA','CONFLUENCE','ORB'):raise ValueError('Unknown strategy')
    if start_date and end_date and start_date > end_date:
        raise ValueError('From date must be on or before To date')
    start = date_boundary(start_date) if start_date else None
    end = date_boundary(end_date, next_day=True) if end_date else None
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='trade_reviews'").fetchone():
            return dict(status='WAITING',updated_epoch=None,total=0,reviews=[],patterns=[],currencies={},pairs=[],note='Start the updated bot to enable Trade Review.')
        scope_row=db.execute("SELECT value FROM meta WHERE key='trade_review_scope'").fetchone()
        scope=json.loads(scope_row['value']) if scope_row else None
        filters = ['account_scope=?']
        parameters = [scope]
        for clause, value in [('strategy=?', strategy if strategy != 'ALL' else None),
                              ('symbol=?', symbol if symbol != 'ALL' else None),
                              ('closed_epoch>=?', start), ('closed_epoch<?', end)]:
            if value is not None:
                filters.append(clause)
                parameters.append(value)
        sql = 'SELECT payload FROM trade_reviews WHERE ' + ' AND '.join(filters) + ' ORDER BY closed_epoch DESC'
        rows = db.execute(sql, parameters).fetchall() if scope else []
        health=db.execute("SELECT value FROM meta WHERE key='trade_review_health'").fetchone()
    reports=[json.loads(row['payload']) for row in rows]
    buckets=defaultdict(list);currencies={}
    for r in reports:
        buckets[(r['strategy'],r['symbol'],r['setup'],r['side'],r['currency'])].append(r)
        if r['realised'] is not None:
            c=currencies.setdefault(r['currency'],dict(net=0,wins=0,losses=0,flat=0,count=0))
            c['net']+=r['realised'];c['count']+=1;c[{'WIN':'wins','LOSS':'losses','FLAT':'flat'}[r['outcome']]]+=1
    patterns=[]
    for key,group in buckets.items():
        valid=[r for r in group if r['realised'] is not None];losses=sum(r['outcome']=='LOSS' for r in valid)
        sl_losses=sum(r['outcome']=='LOSS' and 'SL_EXIT' in r['tags'] for r in valid)
        giveback=sum('SAMPLED_TP1_THEN_NET_LOSS' in r['tags'] for r in valid)
        slips=sum('ADVERSE_FILL_GE_0_1R' in r['tags'] for r in valid)
        ready=len(valid)>=20 and losses>=5
        ideas=[]
        if sl_losses>=3:ideas.append('TEST IDEA: compare entry timing and volatility-normalised SL. Include wins and increased loss size; do not widen stops automatically.')
        if giveback>=3:ideas.append('TEST IDEA: compare TP1 partial/break-even policies after costs using a separate validation period.')
        if slips>=3:ideas.append('TEST IDEA: compare spread/slippage constraints and retained opportunity across winners and losers.')
        if not ideas:ideas=['Collect comparable wins and losses before proposing a rule change.']
        patterns.append(dict(strategy=key[0],symbol=key[1],setup=key[2],side=key[3],currency=key[4],count=len(valid),wins=sum(r['outcome']=='WIN' for r in valid),losses=losses,
                             sl_losses=sl_losses,tp1_then_loss=giveback,adverse_fills=slips,net=sum(r['realised'] for r in valid),
                             status='UNDER REVIEW' if ready else 'COLLECTING DATA',suggestions=ideas,
                             evidence='Descriptive grouping only; 20 trades / 5 losses is a review threshold, not statistical proof. No rule approved.'))
    health=json.loads(health['value']) if health else {}
    return dict(status=health.get('status','COLLECTING DATA'),updated_epoch=health.get('updated_epoch'),total=len(reports),currencies=currencies,pairs=pair_summary(reports),
                patterns=sorted(patterns,key=lambda p:(-p['losses'],-p['count'])),reviews=reports if all_reviews else reports[:200],
                automatic_changes=False,note='Confirmed closed bot-ledger trades for the current execution account. Latest 200 details; summaries use all reviewed trades. MAE/MFE are sampled lower bounds. No causal or profitability claim; no automatic changes.')
