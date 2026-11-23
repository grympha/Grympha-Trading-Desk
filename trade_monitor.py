"""Closed-M15 review for bot-owned DEMO positions."""
import json
import math
import time
from threading import Lock

exit_lock = Lock()
HORIZON = 120 * 60


def auto_enabled(store, strategy):
    with store.connect() as db:
        row = db.execute("SELECT value FROM meta WHERE key='auto_early_exit'").fetchone()
    settings = json.loads(row['value']) if row else False
    return settings.get(strategy) is True if isinstance(settings, dict) else settings is True


AUTO_EXIT_STRATEGIES = {'BBMA', 'CONFLUENCE', 'ORB'}


def set_auto(store, settings):
    if (not isinstance(settings, dict) or not settings or not set(settings) <= AUTO_EXIT_STRATEGIES
            or any(type(value) is not bool for value in settings.values())):
        raise ValueError('Expected boolean switches for BBMA, Confluence and ORB')
    with exit_lock, store.connect() as db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('auto_early_exit', json.dumps(settings)))


def assess(strategy, payload, bars, now, fill_epoch, price, bar_offset=0):
    missing = dict(status='DATA_UNAVAILABLE', reason='Fresh closed M15 candles and a verified fill time are required.')
    if bars is None or len(bars) < 7 or not fill_epoch:
        return missing
    recent = [[float(bar[field]) for field in ('time','high','low','close')] for bar in bars[-7:]]
    if not all(math.isfinite(v) and v > 0 for bar in recent for v in bar):
        return missing
    times = [bar[0] - bar_offset for bar in recent]
    if not 0 <= now - (times[-1] + 900) <= 960 or any(b-a != 900 for a,b in zip(times,times[1:])):
        return missing
    buy = payload['side'] == 'BUY'
    sign = 1 if buy else -1
    prior, latest = recent[-2][3], recent[-1][3]
    structure = recent[:5]
    context = payload.get('context') or {}
    from strategy_monitors import MONITORS
    policy = MONITORS[strategy]
    boundary = policy.boundary(context, structure, buy)
    if not isinstance(boundary,(int,float)) or not math.isfinite(boundary) or boundary <= 0:
        return dict(status='DATA_UNAVAILABLE', reason='Original setup boundary was not captured.')
    broken = sign*(latest-boundary) < 0
    confirmed = times[-2] >= fill_epoch and broken and sign*(prior-boundary) < 0
    adverse = sign*(latest-recent[-4][3]) < 0
    tp = payload.get('actual_tp') or payload['tp2']
    risk = abs(payload['actual_entry']-payload['sl'])
    obstacle = max(bar[1] for bar in recent) if buy else min(bar[2] for bar in recent)
    path = ('RECENT M15 LEVEL BEFORE TP' if 0 < sign*(obstacle-price) < sign*(tp-price)
            else 'MOMENTUM WEAKENING' if adverse else 'NO BARRIER IN RECENT M15 RANGE')
    if confirmed:
        status, reason = 'EXIT_CONFIRMED', 'Two post-fill M15 closes beyond the '+policy.reason
    elif broken or adverse:
        status, reason = 'WATCH', 'M15 momentum weakened or one close crossed the invalidation level.'
    else:
        status, reason = 'HOLD', 'Closed M15 candles have not confirmed invalidation.'
    return dict(status=status, reason=reason, setup=context.get('setup'), boundary=boundary,
                path=path, obstacle=obstacle, target=tp, remaining_r=max(0,sign*(tp-price))/risk if risk else None,
                confirmed_bar_epoch=times[-1]+900, checked_epoch=now)


def observe_shadow(ex, row, p, now, price=None):
    shadow = p.get('early_exit_review')
    if not shadow or shadow['outcome'] != 'OBSERVING':
        return False
    if now > shadow['epoch'] + HORIZON:
        shadow['outcome'] = 'INCONCLUSIVE' if shadow.get('gap') or now-shadow['last_sample'] > 60 else 'NO_LEVEL_OBSERVED'
        return True
    if price is None:
        try:
            _,tick = ex.quote(row['symbol'])
            price = tick.bid if p['side'] == 'BUY' else tick.ask
        except Exception:
            return False
    shadow['gap'] = shadow.get('gap', False) or now-shadow['last_sample'] > 60
    shadow['last_sample'] = now
    sign = 1 if p['side'] == 'BUY' else -1
    if sign*(price-shadow['sl']) <= 0 or sign*(price-shadow['tp']) >= 0:
        shadow['outcome'] = ('LEVEL OBSERVED AFTER GAP' if shadow['gap'] else
                             'ORIGINAL SL OBSERVED' if sign*(price-shadow['sl']) <= 0 else 'ORIGINAL TP OBSERVED')
        shadow['hit_epoch'] = now
    return True


def monitor(ex, row, p, pos):
    m = ex.mt5
    now = time.time()
    if not p['quote_fresh']:
        p['trade_monitor'] = dict(status='DATA_UNAVAILABLE', reason='Fresh executable quote unavailable.')
        return False
    info,tick = ex.quote(row['symbol'])
    buy = p['side'] == 'BUY'
    price = tick.bid if buy else tick.ask
    observe_shadow(ex,row,p,now,price)
    fill = getattr(pos,'time',0)
    bars = m.copy_rates_from_pos(row['symbol'],m.TIMEFRAME_M15,1,7) if fill else None
    result = assess(row['strategy'],p,bars,now,fill-float(ex.cfg.get('tick_timestamp_offset_hours',0))*3600 if fill else 0,
                    price,float(ex.cfg.get('bar_timestamp_offset_hours',0))*3600)
    result['structural_tp'] = p.get('structural_tp')
    p['trade_monitor'] = result
    if result['status'] != 'EXIT_CONFIRMED':
        return False
    original_stop,original_target = p['sl'],p['tp2']
    if 'early_exit_review' not in p and original_stop > 0 and original_target > 0:
        sign = 1 if buy else -1
        initial = 'OBSERVING' if sign*(price-original_stop)>0 and sign*(original_target-price)>0 else 'LEVEL ALREADY REACHED'
        p['early_exit_review'] = dict(epoch=now,last_sample=now,quote=price,sl=original_stop,tp=original_target,
                                      outcome=initial,reason=result['reason'],bar=result['confirmed_bar_epoch'])
    with exit_lock:
        if getattr(ex,'execution_mode','DEMO')=='REAL':
            early_exit=ex.cfg.get('real_execution',{}).get('auto_early_exit',{}).get(row['strategy']) is True
        else:
            early_exit=auto_enabled(ex.store,row['strategy'])
        if not early_exit or p.get('early_exit_order'):
            return False
        ex.guard()
        positions = m.positions_get(ticket=int(pos.ticket))
        live = next((v for v in (positions or ()) if
                     (v.ticket,v.identifier,v.magic,v.symbol,v.type)==(pos.ticket,pos.identifier,pos.magic,row['symbol'],0 if buy else 1)),None)
        if live is None:
            return False
        info,tick = ex.quote(row['symbol'])
        price = tick.bid if buy else tick.ask
        sign = 1 if buy else -1
        if (live.sl and sign*(price-live.sl)<=0) or (live.tp and sign*(live.tp-price)<=0):
            return False
        flags = int(info.filling_mode)
        filling = m.ORDER_FILLING_FOK if flags&1 else m.ORDER_FILLING_IOC if flags&2 else m.ORDER_FILLING_RETURN if info.trade_exemode!=2 else None
        if filling is None:
            p['early_exit_order'] = dict(status='NO_FILLING_POLICY')
            return False
        req = dict(action=m.TRADE_ACTION_DEAL,position=int(live.ticket),symbol=row['symbol'],volume=float(live.volume),
                   type=m.ORDER_TYPE_SELL if buy else m.ORDER_TYPE_BUY,price=round(price,info.digits),
                   deviation=20,magic=int(live.magic),comment=__import__('trade_labels').action_comment(row['strategy'],'early exit'),type_filling=filling)
        operation = 'EARLY_EXIT'
        check = ex.broker_check(req,row,operation=operation)
        if getattr(check,'retcode',None) != 0:
            p['early_exit_order'] = dict(status='CHECK_REJECTED',retcode=getattr(check,'retcode',None))
            return False
        ex.guard()
        current = m.positions_get(ticket=int(live.ticket))
        if not any((v.ticket,v.identifier,v.magic,v.symbol,v.type,v.volume) ==
                   (live.ticket,live.identifier,live.magic,live.symbol,live.type,live.volume)
                   for v in (current or ())):
            return False
        p['early_exit_order'] = dict(status='SENDING',epoch=time.time(),quote=price,volume=req['volume'])
        ex.save(row['key'],row['status'],p)
        try:
            reply = ex.broker_send(req,row,operation=operation)
        except Exception:
            reply = None
        code = getattr(reply,'retcode',None)
        p['early_exit_order'].update(status='ACCEPTED' if code==10009 else 'UNCONFIRMED',
                                     retcode=code,deal=int(getattr(reply,'deal',0) or 0))
        return True
