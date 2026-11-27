"""Live Test agents trading live: an opt-in switch per agent x pair (Live Test page, listed in Settings).

Live Test itself stays simulation-only: it never imports this module or the executor. This bridge
reads the SIMULATED positions of the agents the user switched on and hands each NEW simulated entry
to the shared executor (demo_execution.DemoExecutor.submit_agent), so an agent trade gets the same
checks as BBMA / Confluence / ORB: REAL-account check, risk sizing, position cap, news filter,
weekend rule, duplicate-bot guard, portfolio guard, broker pre-check and the durable ledger.

After entry the live trade MIRRORS the simulation (`mirror`, called from the executor's refresh):
  * broker SL follows the simulated stop when it tightens (break-even / trailing), never loosens;
  * a simulated partial close is copied once with the same fraction;
  * when the simulated trade closes (its own SL/TP/trail/session/flip rule) the live position is
    closed at market. The broker SL/TP stay as a safety net if the bot stops: the agent's initial
    levels, with TP capped at 20R for agents that have no take-profit.
Only an entry seen within FRESH_SECONDS of the simulated fill is copied (never a late chase), and
the executor refuses it if price already moved more than 0.25R from the simulated fill.
Switching an agent off stops NEW entries; an open live trade keeps mirroring until it closes.
"""
from __future__ import annotations
import hashlib
import json
import math
import time
from datetime import datetime, timezone

KEY = 'agent_execution'
PREFIX = 'AGENT:'
FRESH_SECONDS = 90
NO_TP_R = 20.0
# These already trade live as BBMA / Confluence / ORB; a second copy would double the position.
EXCLUDED = ('LIVE_BBMA', 'LIVE_CONFLUENCE', 'LIVE_ORB')


def strategy_of(agent_id: str) -> str:
    """Ledger strategy name for one agent: one active live trade per agent and pair."""
    return PREFIX + hashlib.sha1(agent_id.encode()).hexdigest()[:8]


def is_agent(strategy) -> bool:
    return isinstance(strategy, str) and strategy.startswith(PREFIX)


def load(store) -> dict:
    """{agent_id: dict(label, symbol, strategy_id, ledger, enabled_epoch)}; {} if none or unreadable."""
    if store is None:
        return {}
    try:
        with store.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()
        value = json.loads(row['value']) if row else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def set_enabled(store, agent_id, on) -> dict:
    """Switch one Live Test agent (agent x pair) on or off for live execution. Returns the new list."""
    if not isinstance(agent_id, str) or not agent_id or len(agent_id) > 300 or not isinstance(on, bool):
        raise ValueError('Expected agent_id and on/off')
    from pair_controls import entry_lock
    with entry_lock, store.connect() as db:
        row = db.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()
        current = json.loads(row['value']) if row else {}
        if on:
            agent = db.execute('SELECT strategy_id, symbol, config, status FROM live_test_agents WHERE id=?',
                               (agent_id,)).fetchone()
            if agent is None or agent['status'] == 'REMOVED':
                raise ValueError('Unknown Live Test agent')
            if agent['strategy_id'] in EXCLUDED:
                raise ValueError('This agent copies a live strategy that already trades live')
            current[agent_id] = dict(label=_label(agent['strategy_id'], agent['config']), symbol=agent['symbol'],
                                     strategy_id=agent['strategy_id'], ledger=strategy_of(agent_id),
                                     enabled_epoch=time.time())
        else:
            current.pop(agent_id, None)
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (KEY, json.dumps(current)))
    return current


def _label(strategy_id, config):
    try:
        from live_test import build_agent
        agent = build_agent(strategy_id, json.loads(config or '{}'))
        return agent.label() if agent is not None else strategy_id
    except Exception:
        return strategy_id


def view(store) -> list[dict]:
    """Settings list: enabled agents with their Live Test status (a paused agent opens no trades)."""
    on = load(store)
    if not on:
        return []
    with store.connect() as db:
        status = {r['id']: r['status'] for r in db.execute('SELECT id, status FROM live_test_agents')}
    return [dict(agent_id=aid, label=v.get('label'), symbol=v.get('symbol'), strategy_id=v.get('strategy_id'),
                 enabled_epoch=v.get('enabled_epoch'), live_test_status=status.get(aid, 'REMOVED'))
            for aid, v in sorted(on.items(), key=lambda kv: (kv[1].get('symbol') or '', kv[1].get('label') or ''))]


# --------------------------------------------------------------------------- entries
def _grid(value, size):
    return round(round(value / size) * size, 10) if size > 0 else value


def signal_from(pos, meta, agent_id, now, tick_size=0.0):
    """Executor signal for one simulated entry. Market entry ('REFERENCE') at the live quote."""
    buy = pos['side'] == 'BUY'
    sign = 1 if buy else -1
    entry, sl = float(pos['entry_price']), float(pos['initial_sl'])
    risk = abs(entry - sl)
    tp = pos.get('initial_tp')
    capped = tp is None
    if capped:
        tp = _grid(entry + sign * NO_TP_R * risk, tick_size)
    tp = float(tp)
    first = pos.get('partial_price')
    single = first is None or not (sign * (float(first) - entry) > 0 and sign * (tp - float(first)) > 0)
    tp1 = tp if single else float(first)
    return dict(key='AGENT|' + pos['trade_id'], strategy=strategy_of(agent_id), symbol=pos['symbol'],
                side=pos['side'], entry=entry, stop_loss=sl, tp1=tp1, tp2=tp, signal_kind='REFERENCE',
                structural_tp={'single_target': True} if single else None, setup=meta.get('strategy_id'),
                score=None, tracking={'created_epoch': now}, agent_id=agent_id, trade_id=pos['trade_id'],
                agent_label=meta.get('label'), tp_capped=capped, sim_entry_epoch=pos.get('entry_time'),
                expires_utc=datetime.fromtimestamp(now + 60, timezone.utc).isoformat())


def step(ex, now=None) -> int:
    """Hand every fresh simulated entry of an enabled agent to the executor. Returns entries handed over."""
    now = time.time() if now is None else now
    on = load(ex.store)
    if not on:
        return 0
    with ex.store.connect() as db:
        rows = [r for r in db.execute("SELECT agent_id, payload FROM live_test_positions WHERE status='OPEN'")
                if r['agent_id'] in on]
        done = {r['setup_key'] for r in db.execute('SELECT setup_key FROM demo_orders WHERE setup_key LIKE ?',
                                                     ('AGENT|%',))} if rows else set()
    handed = 0
    for r in rows:
        pos = json.loads(r['payload'])
        if 'AGENT|' + pos['trade_id'] in done or not 0 <= now - float(pos.get('entry_time') or 0) <= FRESH_SECONDS:
            continue
        try:
            info = ex.mt5.symbol_info(pos['symbol'])
            size = float(getattr(info, 'trade_tick_size', 0) or getattr(info, 'point', 0) or 0)
        except Exception:
            size = 0.0
        ex.submit_agent(signal_from(pos, on[r['agent_id']], r['agent_id'], now, size))
        handed += 1
    return handed


# --------------------------------------------------------------------------- mirror after entry
def _sim(store, trade_id):
    """('OPEN', pos) | ('CLOSED', trade) | (None, None) for one simulated trade."""
    with store.connect() as db:
        row = db.execute('SELECT payload FROM live_test_positions WHERE trade_id=?', (trade_id,)).fetchone()
        if row:
            return 'OPEN', json.loads(row['payload'])
        row = db.execute('SELECT payload FROM live_test_trades WHERE trade_id=?', (trade_id,)).fetchone()
    return ('CLOSED', json.loads(row['payload'])) if row else (None, None)


def mirror(ex, row, p, pos):
    """Copy the simulated trade's exit, partial close and stop moves onto the live position.
    One broker action per refresh; each action is sent once and never automatically resent."""
    from weekend_close import close_position
    trade_id = p.get('trade_id')
    if not trade_id or not p.get('quote_fresh'):
        return
    state, sim = _sim(ex.store, trade_id)
    info, tick = ex.quote(row['symbol'])
    for k in ('mirror_close_order', 'mirror_partial_order'):
        # a broker pre-check refusal (e.g. market just closed) may be retried after a minute;
        # a SENT close is never resent
        s = p.get(k) or {}
        if s.get('status') in ('CHECK_REJECTED', 'NO_FILLING_POLICY') and time.time() - s.get('epoch', 0) > 60:
            p.pop(k)
    if state == 'CLOSED':
        p['mirror'] = dict(sim_status='CLOSED', sim_exit_reason=sim.get('exit_reason'), sim_pnl_r=sim.get('pnl_r'))
        close_position(ex, row, p, pos, tick, info, state_key='mirror_close_order', label='mirror close',
                       operation='MIRROR_CLOSE',
                       note=f"Live Test closed its trade ({sim.get('exit_reason')}); live position closed at market.")
        return
    if state is None:
        p['mirror'] = dict(sim_status='MISSING', note='Simulated trade not found; broker SL/TP keep protecting.')
        return
    p['mirror'] = dict(sim_status='OPEN', sim_stop=sim.get('stop'), sim_partial=bool(sim.get('partial_done')))
    if sim.get('partial_done') and not p.get('mirror_partial_order'):
        frac = float(sim.get('partial_fraction') or 0)
        step_ = float(info.volume_step)
        part = math.floor(float(pos.volume) * frac / step_ + 1e-9) * step_ if step_ > 0 else 0.0
        part = round(part, 8)
        if part < float(info.volume_min) or float(pos.volume) - part < float(info.volume_min):
            p['mirror_partial_order'] = dict(status='SKIPPED', epoch=time.time(),
                                             note='Partial and remainder must each meet the broker minimum lot.')
        else:
            close_position(ex, row, p, pos, tick, info, state_key='mirror_partial_order', label='mirror partial',
                           operation='MIRROR_PARTIAL', volume=part,
                           note=f'Live Test took a {frac:.0%} partial; copied on the live position.')
            return
    _sync_stop(ex, row, p, pos, sim, info, tick)


def _sync_stop(ex, row, p, pos, sim, info, tick):
    """Move the broker SL to the simulated stop when it is tighter (break-even / trail)."""
    from trade_labels import action_comment
    m = ex.mt5
    stop = sim.get('stop')
    if stop is None:
        return
    buy = p['side'] == 'BUY'
    grid = float(getattr(info, 'trade_tick_size', 0) or info.point)
    stop = (math.floor(stop / grid + 1e-8) if buy else math.ceil(stop / grid - 1e-8)) * grid
    stop = round(stop, info.digits)
    if pos.sl and ((stop <= pos.sl + grid * .5) if buy else (stop >= pos.sl - grid * .5)):
        return                                           # not tighter than the broker SL
    current = tick.bid if buy else tick.ask
    gap = max(float(getattr(info, 'trade_stops_level', 0)), float(getattr(info, 'trade_freeze_level', 0))) * info.point
    if (current - stop if buy else stop - current) < max(gap, info.point):
        p['mirror_note'] = 'SL copy waiting: broker stop/freeze distance.'
        return
    log = p.setdefault('mirror_sl', {})
    if any(v.get('status') in ('SENDING', 'UNCERTAIN') for v in log.values()):
        p['mirror_note'] = 'SL copy response uncertain; no resend.'
        return
    key = str(stop)
    if key in log:
        return
    req = dict(action=getattr(m, 'TRADE_ACTION_SLTP', 6), position=int(pos.ticket), symbol=row['symbol'], sl=stop,
               tp=float(pos.tp), magic=int(pos.magic), comment=action_comment(row['strategy'], 'mirror SL'))
    ex.guard()
    check = ex.broker_check(req, row, operation='MIRROR_SL')
    if check is None or getattr(check, 'retcode', None) != 0:
        log[key] = dict(status='REJECTED', epoch=time.time(), retcode=getattr(check, 'retcode', None))
        return
    log[key] = dict(status='SENDING', epoch=time.time())
    ex.save(row['key'], row['status'], p)
    try:
        reply = ex.broker_send(req, row, operation='MIRROR_SL')
    except Exception:
        reply = None
    code = getattr(reply, 'retcode', None)
    log[key].update(status='ACCEPTED' if code == 10009 else 'UNCERTAIN' if code in (None, 10008, 10010, 10012, 10031)
                    else 'REJECTED', retcode=code)
