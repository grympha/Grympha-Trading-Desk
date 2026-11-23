"""Account-wide, non-netted stop-risk estimates. No order submission authority."""
import json,math,re,time
from collections import defaultdict

DEFAULTS=dict(enabled=False,total_percent=3.,pair_percent=1.5,currency_percent=2.)
class PortfolioBlocked(RuntimeError):
    def __init__(self,report):self.report=report;super().__init__('Portfolio guard: '+'; '.join(report['reasons']))
def finite(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def validate(values):
    if not isinstance(values,dict) or set(values)-set(DEFAULTS):raise ValueError('Unknown portfolio control')
    d={**DEFAULTS,**values}
    if not isinstance(d['enabled'],bool):raise ValueError('enabled must be boolean')
    for k in ('total_percent','pair_percent','currency_percent'):
        if not finite(d[k]) or not .01<=d[k]<=100:raise ValueError('Portfolio limit outside 0.01..100 percent')
    return d
def initialise(store,cfg):
    values=validate(cfg.get('portfolio_guard',{}))
    with store.connect() as db:db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)',('portfolio_controls',json.dumps(values)))

def settings(store,cfg=None):
    with store.connect() as db:r=db.execute("SELECT value FROM meta WHERE key='portfolio_controls'").fetchone()
    return validate(json.loads(r['value']) if r else (cfg or {}).get('portfolio_guard',{}))
def save_settings(store,values):
    d=validate(values)
    with store.connect() as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('portfolio_controls',json.dumps(d)))
    return d

def legs(symbol,info):
    base=getattr(info,'currency_base',None);quote=getattr(info,'currency_profit',None)
    if base and quote:return (str(base),str(quote)) if base!=quote else None
    m=re.search(r'(XAU|USD|EUR|GBP|JPY|AUD|CAD|CHF|NZD)(USD|EUR|GBP|JPY|AUD|CAD|CHF|NZD)',symbol.upper())
    return m.groups() if m else None

def snapshot(mt5,cfg,candidate=None,reservations=(),exclude_key=None,now=None,strict=False):
    now=time.time() if now is None else now
    rows=[];errors=[];scope=None;equity=None;currency='UNKNOWN'
    try:
        a=mt5.account_info();terminal=mt5.terminal_info()
        if a is None or terminal is None or not terminal.connected:raise ValueError('MT5/account unavailable')
        from demo_execution import identity
        scope=identity(a);currency=getattr(a,'currency','UNKNOWN');equity=getattr(a,'equity',None)
        if not finite(equity) or equity<=0:raise ValueError('Positive finite equity unavailable')
        positions=mt5.positions_get();orders=mt5.orders_get()
        if positions is None or orders is None:raise ValueError('Broker positions/orders unavailable')
    except Exception as e:
        return dict(status='UNKNOWN',updated_epoch=now,equity=equity if finite(equity) else None,currency=currency,known_risk=0.,total_risk=None,total_percent=None,rows=[],unknown=[str(e)],by_pair={},by_currency={},by_strategy={},scope=scope)
    def add(kind,symbol,side,volume,start,sl,magic=0,ticket=None,extra_unknown=None):
        row=dict(kind=kind,symbol=symbol,side=side,volume=volume,ticket=ticket,risk=None,currency_legs=None,
                 strategy={816001:'BBMA',816002:'CONFLUENCE',816003:'ORB'}.get(magic,'MANUAL / OTHER'),unknown=[])
        if extra_unknown:row['unknown'].append(extra_unknown)
        try:
            info=mt5.symbol_info(symbol)
            if info is None:raise ValueError('Symbol metadata unavailable')
            pair=legs(symbol,info)
            if not pair:row['unknown'].append('Currency legs unavailable')
            else:row['currency_legs']=[dict(currency=pair[0],direction='LONG' if side=='BUY' else 'SHORT'),dict(currency=pair[1],direction='SHORT' if side=='BUY' else 'LONG')]
            if side not in ('BUY','SELL') or not all(finite(v) and v>0 for v in (volume,start,sl)):raise ValueError('Volume, price or protective SL unavailable')
            if (sl>=start if side=='BUY' else sl<=start):raise ValueError('SL at/beyond risk basis; protection or closure state uncertain')
            pnl=mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if side=='BUY' else mt5.ORDER_TYPE_SELL,symbol,volume,start,sl)
            if not finite(pnl):raise ValueError('Broker loss calculation unavailable')
            row['risk']=max(0.,-float(pnl))
        except Exception as e:row['unknown'].append(str(e))
        rows.append(row)
    for p in positions:
        side='BUY' if getattr(p,'type',None)==0 else 'SELL' if getattr(p,'type',None)==1 else 'UNKNOWN'
        mark=None;problem=None
        try:
            tick=mt5.symbol_info_tick(p.symbol)
            age=now-(tick.time-float(cfg.get('tick_timestamp_offset_hours',0))*3600)
            if not -5<=age<=(10 if strict else cfg.get('max_tick_age_seconds',180)):raise ValueError('Stale position quote')
            if not all(finite(v) and v>0 for v in (tick.bid,tick.ask)) or tick.ask<tick.bid:raise ValueError('Invalid position quote')
            mark=tick.bid if side=='BUY' else tick.ask
        except Exception as e:problem=str(e)
        add('POSITION',p.symbol,side,getattr(p,'volume',None),mark,getattr(p,'sl',None),getattr(p,'magic',0),getattr(p,'ticket',None),problem)
    for o in orders:
        typ=getattr(o,'type',None);side='BUY' if typ in (2,4,6) else 'SELL' if typ in (3,5,7) else 'UNKNOWN'
        price=getattr(o,'price_stoplimit',None) if typ in (6,7) else getattr(o,'price_open',None)
        add('PENDING',o.symbol,side,getattr(o,'volume_current',None),price,getattr(o,'sl',None),getattr(o,'magic',0),getattr(o,'ticket',None))
    for r in reservations:
        if r['key']==exclude_key:continue
        d=json.loads(r['payload'])
        known=any(getattr(o,'ticket',None)==d.get('order_ticket') or d.get('tag') and getattr(o,'comment',None)==d['tag'] for o in orders) or any(getattr(p,'identifier',None)==d.get('position_id') or d.get('tag') and getattr(p,'comment',None)==d['tag'] for p in positions)
        if known:continue
        req=d.get('request') or {}
        add('UNRESOLVED',r['symbol'],d.get('side'),req.get('volume',d.get('volume')),req.get('price',d.get('reference')),req.get('sl',d.get('sl')),req.get('magic',0),extra_unknown='Unresolved execution reservation; exposure not confirmed')
    if candidate:
        typ=candidate.get('type');side='BUY' if typ in (0,2,4,6) else 'SELL' if typ in (1,3,5,7) else 'UNKNOWN'
        add('CANDIDATE',candidate['symbol'],side,candidate.get('volume'),candidate.get('price'),candidate.get('sl'),candidate.get('magic',0))
    by_pair=defaultdict(float);by_strategy=defaultdict(float);by_currency={};known_risk=0.
    for row in rows:
        if row['unknown']:errors.append(row['symbol']+': '+'; '.join(row['unknown']))
        if row['risk'] is None:continue
        risk=row['risk'];known_risk+=risk;by_pair[row['symbol']]+=risk;by_strategy[row['strategy']]+=risk
        for leg in row['currency_legs'] or []:
            c=by_currency.setdefault(leg['currency'],dict(gross=0.,long=0.,short=0.))
            c['gross']+=risk;c[leg['direction'].lower()]+=risk
    return dict(status='UNKNOWN' if errors else 'KNOWN',updated_epoch=now,equity=equity,currency=currency,
                known_risk=known_risk,total_risk=None if errors else known_risk,total_percent=None if errors else known_risk/equity*100,
                rows=rows,unknown=errors,by_pair=dict(by_pair),by_currency=by_currency,by_strategy=dict(by_strategy),scope=scope,
                basis='Positions: additional equity loss from fresh executable quote to SL. Pending/candidate: entry to SL. Non-netted estimates before fees, gaps and slippage. Currency gross assigns full trade risk to each leg; not notional exposure or correlation.')

def evaluate(report,controls):
    reasons=[];equity=report.get('equity')
    if report['status']=='UNKNOWN':reasons.append('Unknown account exposure / '+str(len(report.get('unknown',[])))+' data issue(s)')
    if finite(equity) and equity>0:
        if report['known_risk']>equity*controls['total_percent']/100+1e-7:reasons.append('Total stop-risk cap exceeded')
        for symbol,risk in report['by_pair'].items():
            if risk>equity*controls['pair_percent']/100+1e-7:reasons.append(symbol+' pair risk cap exceeded')
        for currency,d in report['by_currency'].items():
            if d['gross']>equity*controls['currency_percent']/100+1e-7:reasons.append(currency+' gross concentration cap exceeded')
    else:reasons.append('Equity unavailable')
    return dict(enabled=controls['enabled'],blocked=bool(controls['enabled'] and reasons),reasons=reasons,
                status='BLOCKED' if controls['enabled'] and reasons else 'PASS' if controls['enabled'] else 'MONITOR ONLY',
                projected_risk=report.get('total_risk'),known_projected_risk=report.get('known_risk'),currency=report.get('currency'),updated_epoch=report['updated_epoch'],limits=controls)

def check_entry(ex,request,exclude_key=None):
    controls=settings(ex.store,ex.cfg)
    from demo_execution import BUSY
    reservations=[r for r in ex.rows() if r['status'] in BUSY]
    report=snapshot(ex.mt5,ex.cfg,request,reservations,exclude_key,strict=True)
    decision=evaluate(report,controls)
    with ex.store.connect() as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('portfolio_last_check',json.dumps(decision,allow_nan=False)))
    if decision['blocked']:raise PortfolioBlocked(decision)
    return decision

def refresh(mt5,store,cfg):
    from demo_execution import BUSY,identity
    controls=settings(store,cfg);reservations=[]
    a=mt5.account_info()
    with store.connect() as db:
        if a is not None and db.execute("SELECT 1 FROM sqlite_master WHERE name='demo_orders'").fetchone():
            reservations=[r for r in db.execute('SELECT * FROM demo_orders WHERE account_hash=?',(identity(a),)) if r['status'] in BUSY]
    report=snapshot(mt5,cfg,reservations=reservations);report['controls']=controls
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('portfolio_scope',json.dumps(report.pop('scope'))))
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('portfolio_snapshot',json.dumps(report,allow_nan=False)))
