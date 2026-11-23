"""Read-only daily MT5 trade cashflows. A partial exit counts one position/day."""
from datetime import datetime,date,time as dt_time,timedelta,timezone
import json,math,time
from demo_execution import identity,MAGIC

def settings(cfg):
    offset=int(cfg.get('history_timezone_offset_minutes',480))
    correction=float(cfg.get('history_timestamp_offset_hours',0))
    if not -720<=offset<=840 or not math.isfinite(correction) or abs(correction)>14:raise ValueError('Invalid history timezone/correction')
    return timezone(timedelta(minutes=offset)),correction

def today(cfg,now=None):return datetime.fromtimestamp(time.time() if now is None else now,settings(cfg)[0]).date().isoformat()

def collect(mt5,cfg,selected):
    tz,correction=settings(cfg);day=date.fromisoformat(selected)
    start=datetime.combine(day,dt_time(),tz).astimezone(timezone.utc);end=start+timedelta(days=1)
    terminal=mt5.terminal_info();account=mt5.account_info()
    if terminal is None or not terminal.connected or account is None:raise ValueError('MT5 history unavailable/disconnected')
    scope=identity(account)
    deals=mt5.history_deals_get(start-timedelta(days=2),end+timedelta(days=2))
    if deals is None:raise ValueError('Broker deal history unavailable; zero trades is not inferred')
    events=[];seen=set()
    for d in deals:
        if int(d.type) not in (0,1):continue # Exclude deposits, withdrawals, credit and balance operations.
        stamp=float(getattr(d,'time_msc',0) or 0)/1000 if getattr(d,'time_msc',0) else float(d.time)
        stamp-=correction*3600
        if not start.timestamp()<=stamp<end.timestamp() or int(d.ticket) in seen:continue
        values=[float(getattr(d,k,0)) for k in ('profit','commission','swap','fee')]
        if not all(math.isfinite(v) for v in values):raise ValueError('Non-finite broker cashflow')
        seen.add(int(d.ticket));magic=int(d.magic);strategy=next((k for k,v in MAGIC.items() if magic==v),'OTHER')
        events.append(dict(ticket=int(d.ticket),position_id=int(d.position_id),symbol=str(d.symbol),strategy=strategy,side='BUY' if d.type==0 else 'SELL',entry=int(d.entry),epoch=stamp,volume=float(d.volume),price=float(d.price),profit=values[0],commission=values[1],swap=values[2],fee=values[3],net=sum(values),reason=int(getattr(d,'reason',0))))
    # Account changes during history fetch invalidate the entire result.
    after=mt5.account_info()
    if after is None or identity(after)!=scope:raise ValueError('Account changed during history read; retry')
    return dict(status='READY',date=selected,currency=str(account.currency),timezone_offset_minutes=int(tz.utcoffset(None).total_seconds()/60),updated_epoch=time.time(),deals=sorted(events,key=lambda e:(e['epoch'],e['ticket'])),note='Net BUY/SELL deal cashflows booked on selected day, including costs attached to those deals; excludes deposits/withdrawals and separate balance/commission adjustments. Trade count = distinct positions with exit/reversal deals (partial exits counted once per position/day). Opening commission can appear on a day with zero exits. Floating P/L excluded.')

def refresh(mt5,store,cfg):
    account=mt5.account_info();terminal=mt5.terminal_info()
    scope=identity(account) if account and terminal and terminal.connected else None
    now=time.time();day=today(cfg)
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('daily_scope',json.dumps(scope)))
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('daily_today',json.dumps(day)))
        if not scope:return
        db.execute('INSERT OR IGNORE INTO daily_history VALUES (?,?,?,0,NULL)',(scope,day,now))
        # A selected missing/stale date is serviced before refreshing today's already-loaded result.
        rows=list(db.execute('SELECT * FROM daily_history WHERE account_scope=? ORDER BY CASE WHEN payload IS NULL THEN 0 ELSE 1 END, updated ASC',(scope,)))
    for r in rows:
        active=r['day']==day or now-r['requested']<60
        ttl=30 if r['day']==day else 300
        if not active or now-r['updated']<ttl:continue
        try:result=collect(mt5,cfg,r['day'])
        except Exception as exc:result=dict(status='UNAVAILABLE',date=r['day'],updated_epoch=time.time(),deals=[],error=str(exc) if isinstance(exc,ValueError) else 'MT5 history read failed; retry while connected')
        with store.connect() as db:
            db.execute('UPDATE daily_history SET updated=?,payload=? WHERE account_scope=? AND day=?',(time.time(),json.dumps(result,allow_nan=False),scope,r['day']))
        break
