"""Module 2: read-only entry checks and request construction. Never sends orders."""
from datetime import datetime
import hashlib
import json
import math
import time

def preview(mt5,store,cfg,result,fixed_lot=.01):
    from trade_controls import controls,estimate
    plan=result['entry_plan']
    return estimate(mt5,result['symbol'],plan['side'],plan['entry'],plan['stop_loss'],plan['tp1'],plan['tp2'],controls(store,cfg),fixed_lot)

class RiskCheck:
    def __init__(self,mt5,store,cfg,lot,magic,*,busy=()):
        self.mt5,self.store,self.cfg,self.lot,self.magic=mt5,store,cfg,lot,magic
        self.last_risk=None
        self.busy=frozenset(busy)

    def quote(self,symbol):
        m=self.mt5
        if not m.symbol_select(symbol,True):raise RuntimeError('Symbol cannot be selected')
        info,tick=m.symbol_info(symbol),m.symbol_info_tick(symbol)
        if info is None or tick is None:raise RuntimeError('Missing symbol/quote')
        age=time.time()-(tick.time-float(self.cfg.get('tick_timestamp_offset_hours',0))*3600)
        if not -5<=age<=10:raise RuntimeError('DEMO entry quote is stale; requires <=10 seconds')
        if not all(math.isfinite(float(x)) and x>0 for x in (tick.bid,tick.ask,info.point)) or tick.ask<tick.bid:raise RuntimeError('Invalid executable demo quote')
        return info,tick

    def request(self,s,account_hash,quote=None):
        m=self.mt5;p=s;t=p['tracking'];info,tick=(quote or self.quote)(s['symbol'])
        if getattr(info,'trade_mode',0) not in (4,1 if s['side']=='BUY' else 2):raise RuntimeError('Symbol does not allow this entry direction')
        volume=self.lot
        minimum,maximum,step=float(info.volume_min),float(info.volume_max),float(info.volume_step)
        reference=s.get('signal_kind')=='REFERENCE'
        buy=s['side']=='BUY';price=float(tick.ask if buy else tick.bid) if reference else float(p['entry'])
        sl,tp1,tp2=map(float,(p['stop_loss'],p['tp1'],p['tp2']))
        risk=abs(float(p['entry'])-sl)
        if risk>0 and tick.ask-tick.bid>.25*risk:raise RuntimeError('Demo spread exceeds 25% of initial stop distance')
        if risk<=0 or (reference and abs(price-p['entry'])>.25*risk):raise RuntimeError('Executable quote moved >0.25 initial R from reference; no chase')
        if not all(math.isfinite(x) and x>0 for x in (price,sl,tp1,tp2)):raise RuntimeError('Invalid positive finite demo price/SL/TP')
        from structural_tp import targets_ordered
        if not targets_ordered(s['side'],price,sl,tp1,tp2,s.get('structural_tp')):
            raise RuntimeError('SL/TP levels no longer bracket a usable entry')
        tick_size=float(getattr(info,'trade_tick_size',0) or info.point)
        for x in (price if not reference else sl,sl,tp2):
            if abs(x/tick_size-round(x/tick_size))>1e-5:raise RuntimeError('Source levels do not match broker price tick grid')
        distance=float(getattr(info,'trade_stops_level',0))*info.point
        exit_basis=(tick.bid if buy else tick.ask) if reference else price
        if buy:
            if exit_basis-sl<distance or tp2-exit_basis<distance:raise RuntimeError('Broker minimum SL/TP stop distance not met')
            if not reference and price-tick.ask<max(distance,info.point):raise RuntimeError('BUY STOP entry crossed/too close; not converted to market')
        else:
            if sl-exit_basis<distance or exit_basis-tp2<distance:raise RuntimeError('Broker minimum SL/TP stop distance not met')
            if not reference and tick.bid-price<max(distance,info.point):raise RuntimeError('SELL STOP entry crossed/too close; not converted to market')
        from trade_controls import controls,estimate,entry_sizing
        settings,volume=entry_sizing(controls(self.store,self.cfg),s['symbol'],self.lot)
        self.last_risk=None
        try:
            self.last_risk=estimate(m,s['symbol'],s['side'],price,sl,tp1,tp2,settings,volume)
            volume=self.last_risk['volume']
        except Exception:
            if settings['risk_enabled']:raise
        if step<=0 or not minimum<=volume<=maximum or abs((volume-minimum)/step-round((volume-minimum)/step))>1e-6:
            raise RuntimeError(f'Demo lot {volume:g} invalid for symbol min={minimum:g}, max={maximum:g}, step={step:g}; no automatic increase')
        from trade_labels import entry_comment
        # Readable comment that is also the tracking tag, e.g. 'BBMA Reentry CSA #a1b2c3' / 'Confluence 4/5 #a1b2c3'
        tag=entry_comment(s['strategy'],s.get('setup'),s.get('score'),account_hash,s['key'])
        req=dict(action=m.TRADE_ACTION_DEAL if reference else m.TRADE_ACTION_PENDING,
                 symbol=s['symbol'],volume=volume,type=(m.ORDER_TYPE_BUY if buy else m.ORDER_TYPE_SELL) if reference else (m.ORDER_TYPE_BUY_STOP if buy else m.ORDER_TYPE_SELL_STOP),
                 price=round(price,info.digits),sl=round(sl,info.digits),tp=round(tp2,info.digits),
                 deviation=20,magic=self.magic[s['strategy']],comment=tag,type_time=m.ORDER_TIME_GTC)
        if reference:
            flags=int(getattr(info,'filling_mode',0))
            if flags&1:req['type_filling']=m.ORDER_FILLING_FOK
            elif flags&2:req['type_filling']=m.ORDER_FILLING_IOC
            elif getattr(info,'trade_exemode',2)!=2:req['type_filling']=m.ORDER_FILLING_RETURN
            else:raise RuntimeError('No supported market filling policy; request not sent')
        else:
            if not int(getattr(info,'expiration_mode',0))&4:raise RuntimeError('Pending demo orders require broker-side SPECIFIED expiry support')
            expiry=datetime.fromisoformat(s['expires_utc']).timestamp()
            remaining=expiry-time.time()
            if remaining<15:raise RuntimeError('Pending entry window nearly expired')
            req.update(type_filling=m.ORDER_FILLING_RETURN,type_time=m.ORDER_TIME_SPECIFIED,
                       expiration=int(tick.time+remaining))
        return req


    def capacity_exceeded(self,all_rows,max_positions):
        # Include manual positions, pending orders and unresolved broker attempts.
        positions=self.mt5.positions_get();orders=self.mt5.orders_get()
        if positions is None or orders is None:raise RuntimeError('Cannot verify demo position/order capacity')
        uncertain=0
        for pending_row in all_rows:
            if pending_row['status'] not in self.busy:continue
            pending_payload=json.loads(pending_row['payload'])
            known=any(getattr(o,'ticket',None)==pending_payload.get('order_ticket') for o in orders) or any(getattr(v,'identifier',None)==pending_payload.get('position_id') or pending_payload.get('tag') and getattr(v,'comment',None)==pending_payload['tag'] for v in positions)
            if not known:uncertain+=1
        return len(positions)+len(orders)+uncertain>=max_positions
