"""Signal Agent: one ranked feed of every new Live Test setup, graded by that agent's own record.

After each Live Test cycle `SignalAgent.update()` picks up the entries the agents opened since the
last look and grades each one from the same agent's (same strategy, pair and config) track record:

  * backtest: the newest Quant Agent result for that agent id (`quant_results`), and
  * forward:  the agent's own closed, valid Live Test trades.

Grades (plain rules, shown with their reason on every row):
  A  proven      backtest verdict EDGE and Live Test not contradicting it (fewer than 10 trades,
                 or average R >= 0), or Live Test alone earns EDGE (>= 30 trades)
  B  promising   at least 10 trades in total, combined average above +0.05R
  X  losing      a LOSING verdict in either record, or >= 20 Live Test trades averaging below -0.15R
  C  unproven    everything else (too few trades, or no clear edge)

The grade is frozen at the moment the setup appears, so the feed's outcome column shows, over time,
whether the grades themselves hold up. SIMULATION ONLY: reads the Live Test tables, writes its own
feed, and never touches orders, positions or the executor.
"""
from __future__ import annotations

import json
import time

from live_test import VALID, build_agent, myt_iso
from quant_agent import load_results, summarise, verdict
import market_scout

SCHEMA = '''CREATE TABLE IF NOT EXISTS signal_feed(
 trade_id TEXT PRIMARY KEY, epoch REAL, agent_id TEXT, grade TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS signal_feed_epoch ON signal_feed(epoch DESC);'''
WATERMARK_KEY = 'signal_agent_watermark'
# Always-in-the-market tick strategies flip hundreds of times a week; grading each flip would flood
# the feed and the alerts. Their record stays in Live Test and the Quant Agent tab.
UNGRADED = frozenset({'TRAILFLIP'})
GRADES = ('A', 'B', 'C', 'X')
GRADE_NAMES = dict(A='Proven', B='Promising', C='Unproven', X='Losing')
KEEP_ROWS = 5000
ALERT_MAX_AGE = 600          # only announce setups younger than this (never a backlog after a restart)
FIRST_LOOKBACK = 86400       # the very first run fills the feed with the last day's entries


def _avg(s):
    return s.get('avg_r') or 0.0


def _record_text(name, s, verdict_text=None):
    if not s or not s.get('trades'):
        return f'{name}: no trades yet'
    wr = s.get('win_rate')
    out = f"{name}: {s['trades']} trades, {_avg(s):+.2f}R avg" + (f', {wr * 100:.0f}% win' if wr is not None else '')
    if verdict_text and not verdict_text.startswith('TOO FEW'):
        out += f" ({verdict_text.split(' (')[0].lower()})"
    return out


def market_context(scout_row, pos):
    """What Market Scout knew about this market when the setup opened, or None without a sweep."""
    if not scout_row:
        return None
    usual = market_scout.usual_spread(scout_row, pos['entry_time'])
    spread = pos.get('entry_spread')
    x = round(spread / usual, 2) if usual and spread else None
    return dict(spread_x=x, wide_spread=bool(x and x >= market_scout.WIDE_SPREAD_X),
                volatility=scout_row['volatility']['regime'], trend=scout_row['trend']['label'],
                cost_pct=scout_row.get('cost_pct'), scout_epoch=scout_row.get('_epoch'))


def grade(back, fwd):
    """(grade, reason). back: Quant Agent result dict or None; fwd: quant_agent.summarise() of the
    agent's Live Test trades."""
    bv = (back or {}).get('verdict') or ''
    fv = verdict(fwd)
    bn, fn = (back or {}).get('trades') or 0, fwd['trades']
    parts = [_record_text('Backtest', back, bv) if back else 'Backtest: not run yet',
             _record_text('Live Test', fwd, fv)]
    reason = ' · '.join(parts)
    if bv.startswith('LOSING') or fv.startswith('LOSING') or (fn >= 20 and _avg(fwd) < -0.15):
        return 'X', reason
    if bv.startswith('EDGE') and (fn < 10 or _avg(fwd) >= 0):
        return 'A', reason
    if fv.startswith('EDGE') and (not back or _avg(back) > 0):
        return 'A', reason
    n = bn + fn
    if n >= 10 and (bn * _avg(back or {}) + fn * _avg(fwd)) / n > 0.05:
        return 'B', reason
    return 'C', reason


class SignalAgent:
    def __init__(self, store, alert=None):
        self.store = store
        self.alert = alert           # callable(row) for new A-grade setups, or None
        self._labels = {}
        with store.connect() as db:
            db.executescript(SCHEMA)

    # ---- helpers ----
    def _label(self, db, agent_id, strategy_id):
        if agent_id not in self._labels:
            row = db.execute('SELECT config FROM live_test_agents WHERE id=?', (agent_id,)).fetchone()
            label = strategy_id
            try:
                agent = build_agent(strategy_id, json.loads(row['config'])) if row else None
                label = agent.label() if agent else strategy_id
            except Exception:
                pass
            self._labels[agent_id] = label
        return self._labels[agent_id]

    @staticmethod
    def _forward(db, agent_id, before):
        """The agent's record as it stood when the setup appeared: only trades closed before then."""
        rows = db.execute('SELECT payload FROM live_test_trades WHERE agent_id=? AND closed_epoch<?',
                          (agent_id, before)).fetchall()
        return summarise([json.loads(r['payload']) for r in rows])

    # ---- per cycle ----
    def update(self, now=None):
        """Grade the entries opened since the last call. Returns the new feed rows."""
        now = time.time() if now is None else now
        with self.store.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (WATERMARK_KEY,)).fetchone()
            mark = float(json.loads(row['value'])) if row else now - FIRST_LOOKBACK
            entries = {}
            for r in db.execute('SELECT payload FROM live_test_positions').fetchall():
                p = json.loads(r['payload'])
                if (p.get('entry_time') or 0) > mark and p.get('strategy_id') not in UNGRADED:
                    entries[p['trade_id']] = p
            for r in db.execute('SELECT payload FROM live_test_trades WHERE closed_epoch>?', (mark,)).fetchall():
                p = json.loads(r['payload'])
                if (p.get('entry_time') or 0) > mark and p.get('strategy_id') not in UNGRADED:
                    entries.setdefault(p['trade_id'], p)
            known = {r['trade_id'] for r in db.execute(
                f"SELECT trade_id FROM signal_feed WHERE trade_id IN ({','.join('?' * len(entries))})",
                list(entries)).fetchall()} if entries else set()
            backtests = load_results(self.store) if entries else {}
            scout = {}
            if entries:
                try:
                    rep = market_scout.load(self.store) or {}
                    scout = {r['symbol']: dict(r, _epoch=rep.get('generated_epoch')) for r in rep.get('rows', [])}
                except Exception:
                    scout = {}
            new = []
            for p in sorted(entries.values(), key=lambda p: p['entry_time']):
                if p['trade_id'] in known:
                    continue
                aid = p['agent_id']
                back, fwd = backtests.get(aid), self._forward(db, aid, p['entry_time'])
                g, reason = grade(back, fwd)
                market = market_context(scout.get(p.get('symbol')), p)
                if market and market['wide_spread']:
                    reason += f" · Spread at entry {market['spread_x']:.1f}× usual (Market Scout)"
                    if g == 'A':
                        g, reason = 'B', reason + ': downgraded from A'
                feed = dict(trade_id=p['trade_id'], agent_id=aid, strategy_id=p.get('strategy_id'),
                            label=self._label(db, aid, p.get('strategy_id')), symbol=p.get('symbol'),
                            side=p.get('side'), timeframe=p.get('timeframe'), setup=p.get('setup'),
                            entry_time=p['entry_time'], entry_price=p.get('entry_price'),
                            initial_sl=p.get('initial_sl'), initial_tp=p.get('initial_tp'),
                            grade=g, reason=reason, market=market,
                            backtest=None if not back else {k: back.get(k) for k in
                                                            ('trades', 'avg_r', 'net_r', 'win_rate', 'verdict', 'start', 'end')},
                            forward={k: fwd.get(k) for k in ('trades', 'avg_r', 'net_r', 'win_rate')})
                db.execute('INSERT OR IGNORE INTO signal_feed VALUES (?,?,?,?,?)',
                           (feed['trade_id'], feed['entry_time'], aid, g, json.dumps(feed, allow_nan=False)))
                new.append(feed)
            # small overlap so an entry written at the very end of a cycle is never missed (dedup by id)
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (WATERMARK_KEY, json.dumps(now - 120)))
            db.execute('DELETE FROM signal_feed WHERE trade_id NOT IN '
                       '(SELECT trade_id FROM signal_feed ORDER BY epoch DESC LIMIT ?)', (KEEP_ROWS,))
        if self.alert:
            for feed in new:
                if feed['grade'] == 'A' and now - feed['entry_time'] <= ALERT_MAX_AGE:
                    try:
                        self.alert(feed)
                    except Exception:
                        pass
        return new


# ---- dashboard view ----
def report(store, limit=300, now=None):
    """Feed rows (newest first) with their simulated outcome, and how each grade has done so far."""
    now = time.time() if now is None else now
    with store.connect() as db:
        db.executescript(SCHEMA)
        rows = [json.loads(r['payload']) for r in db.execute(
            'SELECT payload FROM signal_feed ORDER BY epoch DESC').fetchall()]
        ids = [r['trade_id'] for r in rows]
        closed, open_ = {}, {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ','.join('?' * len(chunk))
            for r in db.execute(f'SELECT trade_id, payload FROM live_test_trades WHERE trade_id IN ({marks})', chunk):
                closed[r['trade_id']] = json.loads(r['payload'])
            for r in db.execute(f'SELECT trade_id, payload FROM live_test_positions WHERE trade_id IN ({marks})', chunk):
                open_[r['trade_id']] = json.loads(r['payload'])
    by_grade = {g: dict(grade=g, name=GRADE_NAMES[g], signals=0, closed=0, wins=0, net_r=0.0, open=0) for g in GRADES}
    out = []
    for i, f in enumerate(rows):
        g = by_grade.get(f.get('grade'))
        t, p = closed.get(f['trade_id']), open_.get(f['trade_id'])
        if t:
            f['outcome'] = dict(status='CLOSED', r=t.get('pnl_r'), exit_reason=t.get('exit_reason'),
                                valid=t.get('data_quality_status') == VALID)
        elif p:
            f['outcome'] = dict(status='OPEN', r=p.get('unrealized_r'))
        else:
            f['outcome'] = dict(status='UNKNOWN', r=None)
        if g:
            g['signals'] += 1
            if t and t.get('data_quality_status') == VALID:
                r = t.get('pnl_r') or 0.0
                g['closed'] += 1; g['net_r'] += r; g['wins'] += r > 0
            elif p:
                g['open'] += 1
        if i < limit:
            out.append(dict(f, entry_myt=myt_iso(f.get('entry_time'))))
    for g in by_grade.values():
        g['net_r'] = round(g['net_r'], 2)
        g['avg_r'] = round(g['net_r'] / g['closed'], 3) if g['closed'] else None
        g['win_rate'] = (g['wins'] / g['closed']) if g['closed'] else None
    backtests = load_results(store)
    return dict(status='OK', server_epoch=now, simulation=True, feed=out, by_grade=list(by_grade.values()),
                backtested_agents=len(backtests),
                last_backtest_epoch=max((b['created'] for b in backtests.values()), default=None),
                note='Every setup here is a SIMULATED Live Test entry, graded by the same agent\'s backtest '
                     'and Live Test record. Nothing is traded.')


def telegram_text(feed):
    def px(v):
        return '—' if v is None else (f'{v:,.5f}'.rstrip('0').rstrip('.') if abs(v) < 100 else f'{v:,.2f}')
    return (f"⭐ Signal Agent · grade A (simulated Live Test setup)\n"
            f"{feed['symbol']} {feed['side']} · {feed['label']}\n"
            f"Entry {px(feed['entry_price'])} · SL {px(feed['initial_sl'])} · TP {px(feed['initial_tp'])}\n"
            f"{feed['reason']}\nNo order placed.")
