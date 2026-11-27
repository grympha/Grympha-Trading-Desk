"""Home page (command center) data: one read-only roll-up of what the desk already stores.

Reads dashboard.sqlite3 only (bot snapshot, Live Test, Signal Agent feed, Market Scout). Nothing here
can place, change or close an order.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

# Short display names for the holographic strategy cards; unknown strategies fall back to their id.
SHORT = {
    'LIVE_BBMA': 'BBMA LIVE', 'LIVE_CONFLUENCE': 'CONFLUENCE 4/5+', 'LIVE_ORB': 'LIVE ORB',
    'H4_LIQ_SWEEP_FVG': 'H4 SWEEP + FVG', 'H4_SWING_SWEEP_FVG_ENGULF': 'H4 SWING ENGULF',
    'CMS_SSR': 'CMS SSR', 'CMS_SR_FLIP': 'CMS S/R FLIP', 'CMS_FAKEOUT_V1': 'CMS FAKEOUT',
    'CMS_SUPPLY_DEMAND': 'CMS SUPPLY/DEMAND', 'CRT_H1_M5': 'CRT H1 · M5', 'NY_SWEEP_LONDON': 'NY SWEEP LONDON',
    'INVERSE_FVG_RETEST': 'INVERSE FVG', 'PREV_CANDLE_BREAKOUT_H1': 'PREV CANDLE BRK',
    'COUNTER_TREND_SCALP': 'COUNTER SCALP', 'AMD_PO3': 'AMD · PO3', 'ORB_BREAKOUT_RETEST': 'GOLD ORB RETEST',
    'MOMENTUM_M5_M1': 'MOMENTUM M5/M1', 'ADVOCATE_EMA_PULLBACK': 'ADVOCATE EMA', 'TRAILFLIP': 'TRAILFLIP · TICK',
}
BINS = [('-1R', -9e9, -.75), ('-.5', -.75, -.25), ('0', -.25, .25), ('+.5', .25, .75), ('+1', .75, 1.25),
        ('+1.5', 1.25, 1.75), ('+2', 1.75, 2.5), ('+3', 2.5, 3.5), ('+4+', 3.5, 9e9)]
DAY = 86400
WINDOW_DAYS = 30
HEAT_DAYS = 14
from live_test import VALID  # noqa: E402  (trade data-quality flag for a clean, countable trade)


def short_name(strategy_id):
    return SHORT.get(strategy_id, strategy_id.replace('_', ' '))


def _r(x, n=2):
    return None if x is None else round(x, n)


def build(store, now=None):
    now = time.time() if now is None else now
    since = now - WINDOW_DAYS * DAY
    snap = store.snapshot()
    meta = snap.get('meta') or {}
    acct = meta.get('mt5_account') or {}
    positions = (meta.get('mt5_positions') or {}).get('positions') or []
    floating = sum(float(p.get('profit') or 0) for p in positions)

    with store.connect() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        agents = [dict(r) for r in db.execute('SELECT id, strategy_id, symbol, status FROM live_test_agents')] \
            if 'live_test_agents' in tables else []
        trades = [json.loads(r['payload']) for r in db.execute(
            'SELECT payload FROM live_test_trades WHERE closed_epoch >= ?', (since,))] \
            if 'live_test_trades' in tables else []
        feed = [json.loads(r['payload']) for r in db.execute(
            'SELECT payload FROM signal_feed ORDER BY epoch DESC LIMIT 400')] if 'signal_feed' in tables else []
    try:
        from market_scout import report_view
        scout_rows = report_view(store).get('rows') or []
    except Exception:
        scout_rows = []

    # ---- strategies (one holographic card each) and pairs ----
    live = [a for a in agents if a['status'] != 'REMOVED']
    by_strat = defaultdict(lambda: dict(agents=0, running=0, pairs=set(), trades=0, wins=0, net_r=0.0))
    for a in live:
        s = by_strat[a['strategy_id']]
        s['agents'] += 1
        s['running'] += a['status'] == 'RUNNING'
        s['pairs'].add(a['symbol'])
    valid = [t for t in trades if t.get('data_quality_status') == VALID and t.get('pnl_r') is not None]
    by_agent = defaultdict(lambda: dict(trades=0, net_r=0.0))
    for t in valid:
        r = float(t['pnl_r'])
        s = by_strat[t.get('strategy_id') or t.get('agent_id', '').split('|')[0]]
        s['trades'] += 1; s['wins'] += r > 0; s['net_r'] += r
        a = by_agent[(t.get('strategy_id'), t.get('symbol'))]
        a['trades'] += 1; a['net_r'] += r
    strategies = []
    for sid, s in by_strat.items():
        if not s['agents']:
            continue
        strategies.append(dict(id=sid, name=short_name(sid), agents=s['agents'], running=s['running'],
                               pairs=sorted(s['pairs']), trades=s['trades'],
                               win_rate=_r(s['wins'] / s['trades'], 3) if s['trades'] else None,
                               net_r=_r(s['net_r']), live=sid.startswith('LIVE_')))
    strategies.sort(key=lambda s: (not s['live'], -s['agents'], s['id']))
    pairs = sorted({a['symbol'] for a in live})
    pair_acc = {p: dict(symbol=p, agents=0, trades=0, net_r=0.0) for p in pairs}
    for a in live:
        pair_acc[a['symbol']]['agents'] += 1
    for t in valid:
        p = pair_acc.get(t.get('symbol'))
        if p:
            p['trades'] += 1; p['net_r'] += float(t['pnl_r'])
    pair_stats = [dict(p, net_r=_r(p['net_r'])) for p in pair_acc.values()]

    # ---- prices: bot quotes (with H1 history) first, Market Scout price for the rest ----
    quotes = {}
    for x in snap.get('symbols') or []:
        q, sym = x.get('quote') or {}, x.get('symbol')
        if not sym or q.get('bid') is None or sym in quotes:
            continue
        h1 = ((x.get('charts') or {}).get('H1') or [])[-30:]
        spark = [c['close'] for c in h1 if c.get('close') is not None]
        ref = h1[-24]['open'] if len(h1) >= 24 else (h1[0]['open'] if h1 else None)
        quotes[sym] = dict(symbol=sym, bid=q['bid'], digits=q.get('digits'), spark=spark,
                           change_pct=_r((q['bid'] - ref) / ref * 100, 3) if ref else None)
    for x in scout_rows:
        sym = x.get('symbol')
        if sym in pairs and sym not in quotes and x.get('price') is not None:
            quotes[sym] = dict(symbol=sym, bid=x['price'], digits=None, spark=[], change_pct=None)
    candles = []
    for x in snap.get('symbols') or []:
        if x.get('symbol') == 'XAUUSD':
            src = (x.get('charts') or {}).get('M15') or x.get('chart') or []
            candles = [dict(t=c['time'], o=c['open'], h=c['high'], l=c['low'], c=c['close']) for c in src[-64:]
                       if None not in (c.get('open'), c.get('high'), c.get('low'), c.get('close'))]
            if candles:
                break

    # ---- Live Test roll-ups ----
    dist = [dict(label=b[0], n=sum(1 for t in valid if b[1] <= float(t['pnl_r']) < b[2])) for b in BINS]
    day0 = int(now // DAY) - (HEAT_DAYS - 1)
    heat_acc = defaultdict(lambda: [0.0] * HEAT_DAYS)
    for t in valid:
        d = int((t.get('exit_time') or t.get('entry_time') or 0) // DAY) - day0
        if 0 <= d < HEAT_DAYS:
            heat_acc[t.get('strategy_id')][d] += float(t['pnl_r'])
    busiest = sorted(heat_acc, key=lambda k: -by_strat[k]['trades'])[:9]
    heat = [dict(name=short_name(k), cells=[round(v, 2) for v in heat_acc[k]]) for k in busiest]
    leaders = sorted(({'strategy_id': k[0], 'symbol': k[1], 'net_r': _r(v['net_r']), 'trades': v['trades']}
                      for k, v in by_agent.items()), key=lambda x: -x['net_r'])
    leaders = leaders[:6] + [x for x in leaders[-2:] if x['net_r'] < 0 and x not in leaders[:6]]

    sig = [dict(epoch=f.get('entry_time'), grade=f.get('grade'), strategy_id=f.get('strategy_id'),
                symbol=f.get('symbol'), side=f.get('side')) for f in feed[:14]]
    try:
        from desk_identity import desk_name
        desk = desk_name(meta)
    except Exception:
        desk = ''
    return dict(
        status='OK', server_epoch=now, bot_online=bool(snap.get('bot_online')), desk=desk,
        account=dict(mode=(acct.get('account_type') or 'ANALYSIS').upper() if acct.get('status') else 'ANALYSIS',
                     equity=acct.get('equity'), balance=acct.get('balance'), currency=acct.get('currency'),
                     floating=round(floating, 2), open_positions=len(positions)),
        execution=(meta.get('demo_execution') or {}).get('status'),
        totals=dict(agents=len(live), running=sum(a['status'] == 'RUNNING' for a in live), pairs=len(pairs),
                    strategies=len(strategies), trades_30d=len(valid), net_r_30d=_r(sum(float(t['pnl_r']) for t in valid)),
                    signals_24h=sum(1 for f in feed if (f.get('entry_time') or 0) >= now - DAY)),
        strategies=strategies, pairs=pairs, pair_stats=pair_stats, quotes=[quotes[s] for s in sorted(quotes)], candles=candles,
        dist=dist, heat=heat, heat_days=HEAT_DAYS, leaders=leaders, feed=sig,
        note='Read-only summary. Live Test figures are SIMULATED; nothing on this page can trade.')
