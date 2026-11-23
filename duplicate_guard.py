"""Detect another copy of this bot trading the same MT5 account.

Every bot order carries the strategy's magic number and a unique tag that this bot records in
its ledger BEFORE sending. A position or pending order with our magic number whose tag/ticket is
not in this bot's ledger was therefore placed by another bot copy (another PC, VPS or folder).
While such trades are unacknowledged, NEW entries are paused. Nothing is ever closed or changed.
"""
from __future__ import annotations
import json
import time

META_KEY = 'duplicate_bot'
ACK_KEY = 'duplicate_bot_ack'


def own_ids(rows) -> tuple[set, set]:
    tags, tickets = set(), set()
    for row in rows:
        try:
            p = json.loads(row['payload'])
        except Exception:
            continue
        if p.get('tag'):
            tags.add(str(p['tag']))
        for k in ('order_ticket', 'position_ticket', 'position_id'):
            if p.get(k):
                tickets.add(int(p[k]))
    return tags, tickets


def foreign_trades(positions, orders, rows, magics: dict, acknowledged=()) -> list[dict]:
    by_magic = {int(v): k for k, v in magics.items()}
    tags, tickets = own_ids(rows)
    ack = {int(t) for t in acknowledged}
    out = []
    for kind, items in (('POSITION', positions or ()), ('ORDER', orders or ())):
        for x in items:
            magic = int(getattr(x, 'magic', 0) or 0)
            if magic not in by_magic:
                continue  # manual trades and other EAs are not bot trades
            ticket = int(getattr(x, 'ticket', 0) or 0)
            ident = int(getattr(x, 'identifier', ticket) or ticket)
            tag = str(getattr(x, 'comment', '') or '')
            if (tag and tag in tags) or ticket in tickets or ident in tickets or ticket in ack:
                continue
            out.append(dict(kind=kind, ticket=ticket, symbol=getattr(x, 'symbol', ''), strategy=by_magic[magic],
                            side='BUY' if int(getattr(x, 'type', 0)) % 2 == 0 else 'SELL',
                            volume=float(getattr(x, 'volume', getattr(x, 'volume_current', 0)) or 0),
                            opened_epoch=int(getattr(x, 'time', getattr(x, 'time_setup', 0)) or 0), tag=tag))
    return out


def acknowledged(store) -> list[int]:
    with store.connect() as db:
        row = db.execute('SELECT value FROM meta WHERE key=?', (ACK_KEY,)).fetchone()
    return json.loads(row['value']) if row else []


def acknowledge(store, tickets) -> list[int]:
    if not isinstance(tickets, list) or not 0 < len(tickets) <= 200 or not all(type(t) is int and t > 0 for t in tickets):
        raise ValueError('Expected a list of ticket numbers')
    merged = sorted(set(acknowledged(store)) | set(tickets))[-500:]
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (ACK_KEY, json.dumps(merged)))
        row = db.execute('SELECT value FROM meta WHERE key=?', (META_KEY,)).fetchone()
        if row:  # clear the warning immediately; the next reconciliation confirms
            state = json.loads(row['value'])
            state['trades'] = [t for t in state.get('trades', []) if t['ticket'] not in merged]
            state['status'] = 'WARNING' if state['trades'] else 'CLEAR'
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (META_KEY, json.dumps(state)))
    return merged


def publish(store, trades: list[dict]) -> None:
    state = dict(status='WARNING' if trades else 'CLEAR', trades=trades, updated_epoch=time.time())
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (META_KEY, json.dumps(state)))


def describe(trades: list[dict]) -> str:
    items = ', '.join(f"{t['symbol']} {t['side']} {t['volume']:g} ({t['strategy']}, #{t['ticket']})" for t in trades[:6])
    return (f'ANOTHER BOT is trading this account: {len(trades)} bot-tagged trade(s) not placed by this copy: {items}. '
            'New entries are paused until you acknowledge it in the dashboard. Existing trades are not touched.')
