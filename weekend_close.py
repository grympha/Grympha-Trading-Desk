"""Weekend rule: no bot position is held over the weekend, except the crypto pairs.

Every Friday from 20:00 MT5 server time (the broker's own clock, read from the pair's latest tick, so
the broker's summer/winter time is followed automatically):

  * every open bot position on a non-crypto pair is closed at market ("weekend close"),
  * the bot's own pending entry orders on those pairs are cancelled,
  * no new entry is opened on those pairs until the market reopens.

BTCUSD and ETHUSD (and broker variants such as BTCUSD.m) keep trading through the weekend.
Only the bot's own trades are touched (matched by magic number and ledger), never manual trades.

config.json (optional):  "weekend_close": {"enabled": true, "friday_time": "20:00",
                                           "keep_open": ["BTCUSD", "ETHUSD"]}

A close is sent once per position: an unconfirmed or ambiguous reply is never resent automatically
(the dashboard shows it for checking in MT5), like the bot's other trade actions.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

DEFAULTS = dict(enabled=True, friday_time='20:00', keep_open=['BTCUSD', 'ETHUSD'])
FRIDAY = 4


def settings(cfg):
    raw = (cfg or {}).get('weekend_close') or {}
    s = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    m = re.fullmatch(r'([01]\d|2[0-3]):([0-5]\d)', str(s['friday_time']))
    s['minute'] = int(m.group(1)) * 60 + int(m.group(2)) if m else 20 * 60
    s['keep_open'] = [str(x).upper() for x in (s['keep_open'] or []) if x]
    s['enabled'] = s['enabled'] is not False
    return s


def keeps_running(symbol, cfg):
    """True for the pairs that trade on through the weekend (BTCUSD.m counts as BTCUSD)."""
    sym = str(symbol).upper()
    return any(sym == k or sym.startswith(k) for k in settings(cfg)['keep_open'])


def server_clock(server_epoch):
    """The MT5 server wall clock for a tick time (MT5 tick times are the broker's local time)."""
    return datetime.fromtimestamp(int(server_epoch), timezone.utc)


def closing_time(symbol, server_epoch, cfg):
    """True from Friday <friday_time> server time until the week ends, for a pair that must be flat."""
    s = settings(cfg)
    if not s['enabled'] or keeps_running(symbol, cfg):
        return False
    t = server_clock(server_epoch)
    minute = t.hour * 60 + t.minute
    return (t.weekday() == FRIDAY and minute >= s['minute']) or t.weekday() >= 5


def describe(cfg):
    s = settings(cfg)
    return (f"Weekend rule: Friday {s['friday_time']} MT5 time, open trades are closed and no new entries until the "
            f"market reopens (except {', '.join(s['keep_open']) or 'none'}).")


def close_position(ex, row, p, pos, tick, info, *, state_key='weekend_close_order', label='weekend close',
                   operation='WEEKEND_CLOSE', note=None, volume=None):
    """Close one bot position at market (the weekend rule; also used to mirror a Live Test agent's
    exit or partial close with `volume`). Each `state_key` is sent once per position, never resent.
    Returns True when an order was sent."""
    from trade_labels import action_comment
    from trade_monitor import exit_lock
    m = ex.mt5
    state = p.get(state_key) or {}
    if state.get('status') in ('SENDING', 'ACCEPTED', 'UNCONFIRMED', 'CHECK_REJECTED', 'NO_FILLING_POLICY'):
        return False                                    # once per position; never an automatic resend
    buy = p['side'] == 'BUY'
    with exit_lock:
        ex.guard()
        live = next((v for v in (m.positions_get(ticket=int(pos.ticket)) or ())
                     if (v.ticket, v.identifier, v.magic, v.symbol) == (pos.ticket, pos.identifier, pos.magic, row['symbol'])), None)
        if live is None:
            return False
        flags = int(info.filling_mode)
        filling = (m.ORDER_FILLING_FOK if flags & 1 else m.ORDER_FILLING_IOC if flags & 2
                   else m.ORDER_FILLING_RETURN if info.trade_exemode != 2 else None)
        if filling is None:
            p[state_key] = dict(status='NO_FILLING_POLICY', epoch=time.time())
            return False
        price = tick.bid if buy else tick.ask
        req = dict(action=m.TRADE_ACTION_DEAL, position=int(live.ticket), symbol=row['symbol'],
                   volume=float(volume if volume is not None else live.volume),
                   type=m.ORDER_TYPE_SELL if buy else m.ORDER_TYPE_BUY, price=round(price, info.digits), deviation=30,
                   magic=int(live.magic), comment=action_comment(row['strategy'], label), type_filling=filling)
        check = ex.broker_check(req, row, operation=operation)
        if getattr(check, 'retcode', None) != 0:
            p[state_key] = dict(status='CHECK_REJECTED', retcode=getattr(check, 'retcode', None), epoch=time.time())
            return False
        p[state_key] = dict(status='SENDING', epoch=time.time(), quote=price, volume=req['volume'])
        p['note'] = note or describe(ex.cfg)
        ex.save(row['key'], row['status'], p)
        try:
            reply = ex.broker_send(req, row, operation=operation)
        except Exception:
            reply = None
        code = getattr(reply, 'retcode', None)
        p[state_key].update(status='ACCEPTED' if code == 10009 else 'UNCONFIRMED', retcode=code,
                            deal=int(getattr(reply, 'deal', 0) or 0))
        return True
