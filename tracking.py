"""Sampled paper monitoring, not fills or automatic execution."""
import json
from datetime import datetime, timezone

ACTIVE={'PENDING','PAPER_RUNNING','PAPER_TP1'}
CLOSED={'PAPER_CLOSED_SL','PAPER_CLOSED_TP2','PAPER_CLOSED_RULE','EXPIRED','INVALIDATED','MISSED_ENTRY'}


def init(db):
    db.execute('CREATE TABLE IF NOT EXISTS setup_candidates(strategy TEXT,symbol TEXT,candidate TEXT,PRIMARY KEY(strategy,symbol,candidate))')
    db.execute('CREATE TABLE IF NOT EXISTS telegram_updates(key TEXT PRIMARY KEY,message_id INTEGER,chat_hash TEXT,last_edit REAL,text_hash TEXT)')


def config(cfg):
    result=dict(enabled=True,telegram_update_seconds=60,close_on_tp1=False)
    result.update(cfg.get('tracking',{}))
    if result['telegram_update_seconds']<15:raise ValueError('Tracking Telegram update interval must be >=15 seconds')
    return result


def read(row):return json.loads(row['payload'])


def write(db,row,p,status,now,note):
    db.execute('UPDATE strategy_signals SET payload=?,status=?,updated=?,note=? WHERE id=?',
               (json.dumps(p,allow_nan=False),status,now,note,row['id']))


def analyse_row(db,row,quote,analysis,now,settings):
    p=read(row);t=p.get('tracking')
    if not t or row['status'] not in ACTIVE:return
    side=p['side'];sign=1 if side=='BUY' else -1
    bid,ask=float(quote['bid']),float(quote['ask'])
    mark=bid if side=='BUY' else ask
    status=row['status'];note=row['note']
    t.update(last_quote_epoch=now,mark_price=mark,monitor='LIVE',decision='WAIT' if status=='PENDING' else 'HOLD')
    if status=='PENDING':
        if now>=row['expires']:
            status='EXPIRED';t.update(decision='CANCEL',close_reason='Entry window expired. No sampled paper entry.')
        elif analysis and analysis.get('health')=='OK' and analysis.get('_fresh',True) and analysis.get('condition',{}).get('side')!=side:
            status='INVALIDATED';t.update(decision='CANCEL',close_reason='Strategy direction no longer agrees before entry.')
        else:
            crossed=ask>=p['entry'] if side=='BUY' else bid<=p['entry']
            if crossed:
                fill=ask if side=='BUY' else bid
                # A gap beyond target/stop is not a fill at the old trigger.
                if (sign*(fill-p['tp2'])>=0 or sign*(mark-p['stop_loss'])<=0):
                    status='MISSED_ENTRY';t.update(decision='CANCEL',close_reason='Sampled quote skipped usable entry range; no paper fill assumed.')
                else:
                    status='PAPER_RUNNING'
                    t.update(paper_entry=fill,entry_epoch=now,initial_risk=abs(fill-p['stop_loss']),
                             entry_basis='FIRST_SAMPLED_CROSSING_QUOTE',decision='HOLD')
    if status in {'PAPER_RUNNING','PAPER_TP1'}:
        entry=t['paper_entry'];risk=t['initial_risk'];delta=sign*(mark-entry)
        t.update(price_pnl=delta,points_pnl=delta/float(quote['point']) if quote.get('point',0)>0 else None,
                 pnl_r=delta/risk if risk else None,pnl_state='PROFIT' if delta>0 else 'LOSS' if delta<0 else 'FLAT')
        close_reason=None
        if sign*(mark-p['stop_loss'])<=0:status='PAPER_CLOSED_SL';close_reason='SL observed in a sampled executable exit quote.'
        elif sign*(mark-p['tp2'])>=0:status='PAPER_CLOSED_TP2';close_reason='TP2 observed in a sampled executable exit quote.'
        elif sign*(mark-p['tp1'])>=0:
            if settings.get('close_on_tp1'):
                status='PAPER_CLOSED_RULE';close_reason='TP1 observed; configured paper policy closes the full example.'
            else:
                status='PAPER_TP1';t['tp1_observed']=True
        if not close_reason and analysis and analysis.get('health')=='OK' and analysis.get('_fresh',True):
            if analysis.get('condition',{}).get('side')!=side:
                status='PAPER_CLOSED_RULE';close_reason='Strategy direction changed or no longer qualifies.'
            elif row['strategy']=='CONFLUENCE' and not analysis.get('qualified'):
                status='PAPER_CLOSED_RULE';close_reason=f"Confluence score fell below {analysis.get('min_score',4)}/5."
        if close_reason:
            t.update(decision='CLOSE',closed_epoch=now,close_price=mark,close_reason=close_reason)
            note=close_reason+' PAPER simulation only; no broker order closed.'
        elif not analysis or analysis.get('health')!='OK' or not analysis.get('_fresh',True):
            t.update(decision='REVIEW_DATA',monitor='ANALYSIS_STALE',close_reason='Analysis unavailable/stale; HOLD/CLOSE cannot be assessed.')
        else:
            t['close_reason']='TP1 observed; HOLD toward TP2. Original SL unchanged.' if t.get('tp1_observed') else 'HOLD: strategy still qualifies; SL/TP2 not observed.'
        # No SL trailing, partial fills, commissions, swaps or money P/L are inferred.
    if status in {'EXPIRED','INVALIDATED','MISSED_ENTRY'}:note=t.get('close_reason','')
    p['tracking']=t;write(db,row,p,status,now,note)


def handle(db,result,now,settings):
    strategy=result.get('strategy','BBMA');symbol=result['symbol']
    analysis=dict(result,_fresh=True)
    # Each currently active record is updated; another candle never resets its levels.
    active=db.execute("SELECT * FROM strategy_signals WHERE strategy=? AND symbol=? AND status IN ('PENDING','PAPER_RUNNING','PAPER_TP1')",(strategy,symbol)).fetchall()
    quote=result.get('quote')
    if quote and not -5<=quote.get('age_seconds',0)<=60:
        return # Analysis may accept a 180s tick; paper tracking requires a <=60s quote.
    for row in active:
        if read(row).get('tracking') and quote and result.get('health')=='OK':
            analyse_row(db,row,quote,analysis,now,settings)
    p=result.get('entry_plan') if result.get('health')=='OK' else None
    if not p:return
    existed=db.execute('SELECT 1 FROM setup_candidates WHERE strategy=? AND symbol=? AND candidate=?',(strategy,symbol,p['id'])).fetchone()
    db.execute('INSERT OR IGNORE INTO setup_candidates VALUES (?,?,?)',(strategy,symbol,p['id']))
    key=strategy+'|'+symbol+':'+p['id']
    existing=db.execute('SELECT * FROM strategy_signals WHERE id=?',(key,)).fetchone()
    resumed=bool(existing and strategy=='CONFLUENCE' and existing['status']=='REFERENCE' and not read(existing).get('tracking'))
    if existed or (existing and not resumed):return
    # A verified demo ticket also owns this setup slot, even if paper rules already closed.
    mode=db.execute("SELECT value FROM meta WHERE key='demo_execution'").fetchone()
    scope=db.execute("SELECT value FROM meta WHERE key='demo_scope'").fetchone()
    if mode and scope and json.loads(mode['value']).get('enabled') and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_orders'").fetchone():
        active_demo=db.execute("SELECT 1 FROM demo_orders WHERE account_hash=? AND strategy=? AND symbol=? AND status IN ('PRECHECK','SENDING','UNCERTAIN','SUBMITTED','PENDING','OPEN','OPEN_PARTIAL','CANCEL_UNCERTAIN','REVIEW_UNKNOWN')",(json.loads(scope['value']),strategy,symbol)).fetchone()
        if active_demo:return
    # Candidates seen while a setup runs are consumed, never queued for re-entry later.
    if db.execute("SELECT 1 FROM strategy_signals WHERE strategy=? AND symbol=? AND status IN ('PENDING','PAPER_RUNNING','PAPER_TP1')",(strategy,symbol)).fetchone():return
    expires=datetime.fromisoformat(p['expires_utc']).timestamp()
    if expires<=now:return
    p=json.loads(json.dumps(p));reference=p.get('signal_kind')=='REFERENCE'
    # Do not retroactively paper-fill a cached reference made before this upgrade.
    if reference and result.get('new_analysis') is False and not resumed:return
    status='PAPER_RUNNING' if reference else 'PENDING'
    t=dict(mode='PAPER',decision='HOLD' if reference else 'WAIT',monitor='LIVE',
           last_quote_epoch=now,created_epoch=now,entry_basis='REFERENCE_SIMULATION' if reference else 'AWAITING_SAMPLED_TRIGGER',
           paper_entry=float(p['entry']) if reference else None,entry_epoch=now if reference else None,
           initial_risk=abs(p['entry']-p['stop_loss']) if reference else None,
           mark_price=None,price_pnl=None,pnl_r=None,pnl_state='WAITING',tp1_observed=False,
           close_reason='Paper scenario only. No broker position is assumed.')
    if reference and t['initial_risk']<=0:return
    if resumed:
        t.update(continuity_gap=True,entry_basis='RESUMED_REFERENCE_BASELINE_UNVERIFIED_PATH',entry_epoch=None)
    p['tracking']=t
    p['digits']=int((quote or {}).get('digits',5))
    note='PAPER scenario; Confluence reference assumed as paper entry, not a broker fill.' if reference else 'Pending paper trigger; no broker order placed.'
    if resumed:
        db.execute('UPDATE strategy_signals SET payload=?,status=?,updated=?,note=? WHERE id=?',(json.dumps(p),status,now,'Resumed reference baseline; prior price path and fills are unverified. '+note,key))
    else:
        db.execute('INSERT INTO strategy_signals VALUES (?,?,?,?,?,?,?,?,?)',(key,strategy,symbol,now,expires,status,now,note,json.dumps(p)))
    if quote:
        row=db.execute('SELECT * FROM strategy_signals WHERE id=?',(key,)).fetchone()
        analyse_row(db,row,quote,analysis,now,settings)


def observe(db,symbol,quote,now,settings,analyses):
    rows=db.execute("SELECT * FROM strategy_signals WHERE symbol=? AND status IN ('PENDING','PAPER_RUNNING','PAPER_TP1')",(symbol,)).fetchall()
    for row in rows:analyse_row(db,row,quote,analyses.get(row['strategy']),now,settings)


def notification_text(row):
    p=row; t=p.get('tracking',{});digits=p.get('digits',5)
    if p.get('demo_execution'):return demo_notification(p,p['demo_execution'],digits)
    number=lambda x:'—' if x is None else f'{x:.{digits}f}'
    pnl='Waiting for paper trigger'
    if t.get('price_pnl') is not None:
        pnl=f"{t['pnl_state']} | price change {t['price_pnl']:+.{digits}f} | {t['pnl_r']:+.2f}R"
    return (f"{p['strategy']} {p['symbol']} — {p['side']} (PAPER SIMULATION)\n"
            f"Setup: {p['key']}\nStatus: {p['lifecycle']}\n"
            f"Paper entry: {number(t.get('paper_entry'))} | original reference/trigger: {number(p['entry'])}\n"
            f"Latest exit quote: {number(t.get('mark_price'))}\n{pnl}\n"
            f"SL {number(p['stop_loss'])} | TP1 {number(p['tp1'])} | TP2 {number(p['tp2'])}\n"
            f"Decision: {t.get('decision','REVIEW_DATA')} | monitor: {t.get('monitor','UNKNOWN')}\n{t.get('close_reason','')}\n"
            f"Last sampled quote UTC: {datetime.fromtimestamp(t.get('last_quote_epoch',0),timezone.utc).isoformat()}\n"
            +("Monitoring gap: prior price path/fills are unverified.\n" if t.get("continuity_gap") else "")+
            "Estimated paper status only. No actual fill, cash P/L or broker close is implied.")


def demo_notification(p,d,digits):
    num=lambda value:'—' if value is None else f'{value:.{digits}f}'
    status=d['status'];profit=d.get('profit');currency=d.get('currency','')
    pl=('Broker floating P/L: '+('—' if profit is None else f'{profit:+.2f} {currency}'))
    if status=='CLOSED':pl=f"Broker realised P/L: {d.get('realised',0):+.2f} {currency} (reported costs included)"
    if status not in ('OPEN','OPEN_PARTIAL','CLOSED'):pl='No confirmed open position P/L for this request.'
    return (f"{p['strategy']} {p['symbol']} — {p['side']} (MT5 DEMO)\n"
            f"Setup: {p['key']}\nBroker status: {status}\n"
            f"Order {d.get('order_ticket') or '—'} | Position {d.get('position_ticket') or '—'}\n"
            f"Actual entry {num(d.get('actual_entry'))} | current {num(d.get('current'))} | {d.get('volume')} lots\n"
            f"{pl}\nSL {num(d.get('actual_sl',d.get('sl')))} | TP2 {num(d.get('actual_tp',d.get('tp2')))}\n"
            f"TP1 observed: {bool(d.get('tp1_observed'))}\n"
            f"Decision: {d.get('decision','REVIEW_DATA')}\n{d.get('note','')}\n"
            "DEMO account only. Signal-based CLOSE_REVIEW does not send a market close.")
