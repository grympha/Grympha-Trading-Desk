"""Telegram event alerts (entry, order, fill, TP1, TP, SL, early exit, health) chosen in the dashboard.

Each bot cycle `Notifier.check()` compares the broker-confirmed ledger and new setups with what it has
already announced (persisted, so restarts never resend), formats readable messages and hands them to a
background sender. Sending never blocks or changes trading. Credentials come from .env via
telegram_config (set in the launcher); the dashboard only stores which events to send.
"""
from __future__ import annotations
import json
import logging
import queue
import threading
import time

import telegram_config

PREFS_KEY, STATUS_KEY, SEEN_KEY = 'telegram_prefs', 'telegram_status', 'telegram_seen'
EVENTS = {  # key: (label, default)
    'entry_signal': ('New entry setup', True),
    'order_placed': ('Order placed', True),
    'filled': ('Position opened (filled)', True),
    'tp1': ('TP1 reached (partial / break-even)', True),
    'sl_moved': ('Stop-loss moved (break-even / trailing)', False),
    'tp_hit': ('Closed at take-profit', True),
    'sl_hit': ('Closed at stop-loss', True),
    'closed_other': ('Closed early (bot early exit / manual / stop-out)', True),
    'order_rejected': ('Order rejected / cancelled', True),
    'health': ('Bot started/stopped, another bot on the account', True),
    'setup_board': ('Live setup board (one message per setup, edited as it progresses)', False),
    'signal_agent': ('Signal Agent: grade-A Live Test setup (simulated, never traded)', False),
}
OPEN = ('OPEN', 'OPEN_PARTIAL')
PENDING = ('PENDING', 'SUBMITTED')
REJECTED = ('REJECTED', 'CHECK_REJECTED', 'CANCELLED')
CLOSE_REASON = {0: 'closed manually (desktop)', 1: 'closed manually (mobile)', 2: 'closed manually (web)', 3: 'closed by the bot (early exit)',
                4: 'stop-loss hit', 5: 'take-profit hit', 6: 'stop-out (margin)'}


def defaults() -> dict:
    return {k: d for k, (_, d) in EVENTS.items()}


def validate(prefs) -> dict:
    if not isinstance(prefs, dict) or set(prefs) != set(EVENTS) or not all(isinstance(v, bool) for v in prefs.values()):
        raise ValueError('Telegram preferences must be true/false for: ' + ', '.join(EVENTS))
    return dict(prefs)


def load_prefs(store) -> dict:
    try:
        with store.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (PREFS_KEY,)).fetchone()
        saved = json.loads(row['value']) if row else {}
        return validate({**defaults(), **{k: v for k, v in saved.items() if k in EVENTS}})
    except Exception:
        return defaults()


def save_prefs(store, prefs) -> dict:
    clean = validate(prefs)
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (PREFS_KEY, json.dumps(clean)))
    return clean


def status_view() -> dict:
    """Safe-to-show credential status (never the token or full chat id)."""
    c = telegram_config.read()
    chat = c['chat']
    return dict(enabled=c['enabled'], configured=c['configured'], chat_hint=('…' + chat[-3:]) if len(chat) > 3 else chat)


# ---------------------------------------------------------------- formatting
def _name(row):
    from trade_labels import strategy_name, SETUPS
    setup = ((row.get('context') or {}).get('setup') or '')
    label = SETUPS.get(setup, setup.replace('_', ' ').title()) if setup else ''
    return f"{strategy_name(row.get('strategy'))}{(' · ' + label) if label else ''}"


def _px(v):
    return '—' if v is None else (f'{v:,.5f}'.rstrip('0').rstrip('.') if abs(v) < 100 else f'{v:,.2f}')


def _money(v, cur):
    return '—' if v is None else f"{'+' if v >= 0 else ''}{v:,.2f} {cur or ''}".strip()


def message(event, row, mode, extra=None):
    sym, side, vol = row.get('symbol'), row.get('side'), row.get('volume')
    head = f"{sym} {side} {vol if vol is not None else ''} lots · {_name(row)} · {mode}".replace('  ', ' ')
    entry = row.get('actual_entry') or row.get('reference')
    sl, tp = row.get('actual_sl') or row.get('sl'), row.get('actual_tp') or row.get('tp2')
    levels = f"Entry {_px(entry)} · SL {_px(sl)} · TP {_px(tp)}"
    cur = row.get('currency')
    if event == 'order_placed':
        return f"📝 Order placed\n{head}\n{levels}"
    if event == 'filled':
        return f"🟢 Position opened\n{head}\n{levels}"
    if event == 'tp1':
        return f"🎯 TP1 reached\n{head}\n{'Partial close done · ' if row.get('status') == 'OPEN_PARTIAL' else ''}Running P/L {_money(row.get('profit'), cur)}"
    if event == 'sl_moved':
        return f"🛡 Stop-loss moved\n{head}\nSL {_px(extra)} → {_px(sl)}"
    if event in ('tp_hit', 'sl_hit', 'closed_other'):
        icon = {'tp_hit': '✅', 'sl_hit': '🔴'}.get(event, '⚪')
        v = row.get('realised')
        reason = CLOSE_REASON.get(row.get('close_reason'), 'closed')
        return f"{icon} {reason[:1].upper() + reason[1:]}\n{head}\nEntry {_px(entry)} → Exit {_px(row.get('exit_price'))}\nResult {_money(v, cur)}"
    if event == 'order_rejected':
        return f"⚠ Order {row.get('status', '').lower().replace('_', ' ')}\n{head}\n{(row.get('note') or '')[:200]}"
    return f"{head}"


def signal_message(sig, mode, trading):
    from trade_labels import strategy_name, SETUPS
    setup = SETUPS.get(sig.get('setup'), str(sig.get('setup', '')).replace('_', ' ').title())
    note = '' if trading else '\n(analysis only · this strategy is not allowed to trade)'
    kind = 'quote reference' if sig.get('signal_kind') == 'REFERENCE' else 'pending entry idea'
    return (f"🔔 New setup · {strategy_name(sig.get('strategy'))} · {setup}\n{sig.get('symbol')} {sig.get('side')} · {kind} · {mode}\n"
            f"Entry {_px(sig.get('entry'))} · SL {_px(sig.get('stop_loss'))} · TP1 {_px(sig.get('tp1'))} · TP2 {_px(sig.get('tp2'))}{note}")


# ---------------------------------------------------------------- notifier
class Notifier:
    def __init__(self, store, cfg, mode='ANALYSIS', send=None):
        self.store, self.cfg, self.mode = store, cfg, mode
        self.send = send or telegram_config.send
        self.q = queue.Queue(maxsize=200)
        self.started = time.time()
        self.seen = self._load_seen()
        threading.Thread(target=self._worker, name='telegram-sender', daemon=True).start()

    # persistence of what was already announced
    def _load_seen(self):
        try:
            with self.store.connect() as db:
                row = db.execute('SELECT value FROM meta WHERE key=?', (SEEN_KEY,)).fetchone()
            data = json.loads(row['value']) if row else None
        except Exception:
            data = None
        return data if isinstance(data, dict) and 'rows' in data else None  # None -> prime on first check

    def _save_seen(self):
        rows = dict(list(self.seen['rows'].items())[-400:]); sigs = self.seen['signals'][-400:]
        self.seen = dict(rows=rows, signals=sigs, duplicates=self.seen.get('duplicates', ''))
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (SEEN_KEY, json.dumps(self.seen)))

    def _status(self, **change):
        try:
            with self.store.connect() as db:
                row = db.execute('SELECT value FROM meta WHERE key=?', (STATUS_KEY,)).fetchone()
                st = json.loads(row['value']) if row else dict(sent=0, failed=0)
                st.update(change, **status_view(), updated_epoch=time.time())
                db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (STATUS_KEY, json.dumps(st)))
        except Exception:
            pass

    def _worker(self):
        while True:
            text = self.q.get()
            creds = telegram_config.active()
            if not creds:
                continue  # disabled or not set up in the launcher: drop quietly
            try:
                self.send(*creds, text)
                self._status(last_sent_epoch=time.time(), last_error=None, sent=self._count('sent'))
            except Exception as exc:  # never log the token/URL
                logging.warning('Telegram alert not sent: %s', str(exc)[:120])
                self._status(last_error=str(exc)[:160], last_error_epoch=time.time(), failed=self._count('failed'))

    def _count(self, key):
        try:
            with self.store.connect() as db:
                row = db.execute('SELECT value FROM meta WHERE key=?', (STATUS_KEY,)).fetchone()
            return int((json.loads(row['value']) if row else {}).get(key, 0)) + 1
        except Exception:
            return 1

    def push(self, text):
        try:
            self.q.put_nowait(text)
        except queue.Full:
            logging.warning('Telegram alert queue full; alert dropped')

    def lifecycle(self, text):
        if load_prefs(self.store)['health']:
            self.push(text)

    # main check, once per bot cycle
    def check(self):
        prefs = load_prefs(self.store)
        snap = self.store.snapshot()
        orders = snap.get('demo_orders') or []
        signals = [s for s in snap.get('signals') or [] if s.get('created', 0) >= self.started - 5]
        current = {o['key']: dict(status=o.get('status'), sl=o.get('actual_sl'), tp1=bool(o.get('tp1_observed'))) for o in orders}
        live_sigs = [s['key'] for s in signals if (s.get('lifecycle') == 'REFERENCE' and s.get('reference_current')) or s.get('actionable')]
        dup = snap['meta'].get('duplicate_bot') or {}
        dup_sig = ','.join(sorted(str(t['ticket']) for t in dup.get('trades', []))) if dup.get('status') == 'WARNING' else ''
        if self.seen is None:  # first run ever: remember the present, announce nothing old
            self.seen = dict(rows=current, signals=live_sigs, duplicates=dup_sig); self._save_seen()
            self._status(primed_epoch=time.time())
            return []
        sent = []
        def emit(event, text):
            if prefs.get(event):
                self.push(text); sent.append(event)
        from strategy_settings import load as load_strategy
        orb_trade = load_strategy(self.store, self.cfg)['orb']['trade']
        for s in signals:
            if s['key'] in live_sigs and s['key'] not in self.seen['signals']:
                trading = self.mode in ('DEMO', 'REAL') and (s.get('strategy') != 'ORB' or orb_trade)
                emit('entry_signal', signal_message(s, self.mode, trading))
                self.seen['signals'].append(s['key'])
        for o in orders:
            key, now, was = o['key'], current[o['key']], self.seen['rows'].get(o['key'])
            st, old = now['status'], (was or {}).get('status')
            if st != old:
                if st in PENDING and old not in PENDING + OPEN:
                    emit('order_placed', message('order_placed', o, self.mode))
                elif st in OPEN and old not in OPEN:
                    emit('filled', message('filled', o, self.mode))
                elif st == 'OPEN_PARTIAL' and old == 'OPEN':
                    emit('tp1', message('tp1', o, self.mode))
                elif st == 'CLOSED' and old is not None:
                    reason = o.get('close_reason')
                    emit('tp_hit' if reason == 5 else 'sl_hit' if reason == 4 else 'closed_other', message('tp_hit' if reason == 5 else 'sl_hit' if reason == 4 else 'closed_other', o, self.mode))
                elif st in REJECTED and old is not None:
                    emit('order_rejected', message('order_rejected', o, self.mode))
            elif st in OPEN and was:
                if now['tp1'] and not was.get('tp1') and st == 'OPEN':
                    emit('tp1', message('tp1', o, self.mode))
                if was.get('sl') and now['sl'] and abs(now['sl'] - was['sl']) > 1e-9:
                    emit('sl_moved', message('sl_moved', o, self.mode, extra=was['sl']))
            self.seen['rows'][key] = now
        if dup_sig and dup_sig != self.seen.get('duplicates'):
            emit('health', f"⚠ Another bot is trading this account ({len(dup.get('trades', []))} unknown bot trade(s)).\nNew entries are PAUSED. Stop the other bot, then acknowledge in the dashboard.")
        self.seen['duplicates'] = dup_sig
        self._save_seen()
        return sent

    def flush(self, timeout=8.0):
        """Give queued messages (e.g. 'bot stopped') a chance to go out before the process exits."""
        end = time.time() + timeout
        while not self.q.empty() and time.time() < end:
            time.sleep(0.2)
        time.sleep(0.5)
