"""Opt-in DEMO management. Durable intents; never replay uncertain mutations."""
import json,math,time
from trade_controls import controls,floor_volume

def manage(ex,row,p,pos):
    from trade_labels import action_comment
    c=controls(ex.store,ex.cfg);m=ex.mt5
    if not any(c[k] for k in ('partial_enabled','breakeven_enabled','trailing_enabled')):return
    if not p.get('quote_fresh'):return
    ex.guard();info,tick=ex.quote(row['symbol']);buy=p['side']=='BUY'
    # Always re-read exact ticket + identifier + magic before a mutation.
    live=next((v for v in (m.positions_get() or ()) if v.ticket==pos.ticket and v.identifier==pos.identifier and v.magic==pos.magic and v.symbol==row['symbol']),None)
    if live is None:return
    pos=live;current=tick.bid if buy else tick.ask;entry=float(pos.price_open);risk=abs(entry-float(p['sl']))
    if risk<=0:return
    single_target=(p.get('structural_tp') or {}).get('single_target')
    reached=not single_target and (current>=p['tp1'] if buy else current<=p['tp1'])
    p.setdefault('management',{});log=p['management']
    if any(v.get('status') in ('SENDING','UNCERTAIN') for v in log.values()):
        p['management_note']='Management response uncertain; manual reconciliation required, no resend.';return
    def send(key,req):
        if key in log:return
        ex.guard()
        check=ex.broker_check(req,row,operation=key)
        if check is None or check.retcode!=0:
            log[key]=dict(status='REJECTED',epoch=time.time(),note='Broker management precheck rejected');return
        log[key]=dict(status='SENDING',epoch=time.time(),request=req)
        ex.save(row['key'],row['status'],p)
        ex.guard()
        try:r=ex.broker_send(req,row,operation=key)
        except Exception:r=None
        code=getattr(r,'retcode',None)
        log[key].update(status='ACCEPTED' if code==10009 else 'UNCERTAIN' if code in (None,10008,10010,10012,10031) else 'REJECTED',retcode=code)
        ex.save(row['key'],row['status'],p)
    if c['partial_enabled'] and reached and 'TP1_PARTIAL' not in log:
        part=floor_volume(float(pos.volume)*c['partial_percent']/100,float(info.volume_step));remain=round(float(pos.volume)-part,8)
        if part<float(info.volume_min) or remain<float(info.volume_min):
            log['TP1_PARTIAL']=dict(status='SKIPPED',note='Partial and remainder must each meet broker minimum lot; full close is never substituted')
        else:
            flags=int(info.filling_mode)
            filling=m.ORDER_FILLING_FOK if flags&1 else m.ORDER_FILLING_IOC if flags&2 else m.ORDER_FILLING_RETURN if info.trade_exemode!=2 else None
            if filling is None:log['TP1_PARTIAL']=dict(status='SKIPPED',note='No supported filling policy')
            else:
                send('TP1_PARTIAL',dict(action=m.TRADE_ACTION_DEAL,position=int(pos.ticket),symbol=row['symbol'],volume=part,type=m.ORDER_TYPE_SELL if buy else m.ORDER_TYPE_BUY,price=round(current,info.digits),deviation=20,magic=int(pos.magic),comment=action_comment(row['strategy'],'TP1 partial'),type_filling=filling))
                return # Reconcile actual remaining volume before any next action.
    candidates=[]
    if c['breakeven_enabled'] and reached:candidates.append(entry+(1 if buy else -1)*c['breakeven_offset_points']*info.point)
    gain=(current-entry)*(1 if buy else -1)
    if c['trailing_enabled'] and gain>=c['trailing_start_r']*risk:candidates.append(current-(1 if buy else -1)*c['trailing_distance_r']*risk)
    if not candidates:return
    proposed=max(candidates) if buy else min(candidates)
    grid=float(info.trade_tick_size or info.point)
    proposed=(math.floor(proposed/grid+1e-8) if buy else math.ceil(proposed/grid-1e-8))*grid
    proposed=round(proposed,info.digits)
    gap=max(float(info.trade_stops_level),float(getattr(info,'trade_freeze_level',0)))*info.point
    if (current-proposed if buy else proposed-current)<max(gap,info.point):
        p['management_note']='SL update waiting: broker stop/freeze distance';return
    if pos.sl and ((proposed<=pos.sl+grid*.5) if buy else (proposed>=pos.sl-grid*.5)):return
    if pos.sl and abs(current-pos.sl)<=float(getattr(info,'trade_freeze_level',0))*info.point:return
    send('SL_'+str(proposed),dict(action=getattr(m,'TRADE_ACTION_SLTP',6),position=int(pos.ticket),symbol=row['symbol'],sl=proposed,tp=float(pos.tp),magic=int(pos.magic),comment=action_comment(row['strategy'],'SL protect')))
    p['management_note']='SL modifications tighten protection only; break-even is price-based before costs.'
