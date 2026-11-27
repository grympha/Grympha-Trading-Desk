"""Quant Agent — historical backtests of Live Test agents on MT5 history. READ-ONLY, simulated.

Runs any Live Test agent (the same agent code) through the SAME Live Test engine logic
(`LiveTestEngine._run_agent`: monitoring, freshness gate, position cap, Simulator fills/exits),
replacing only two things:
  * the market: `HistoricalMarket` replays MT5 history and serves ONLY candles already closed at
    the simulated moment (no lookahead). Each M1 candle becomes four quotes — open, the two
    extremes (low first on an up candle, high first on a down candle), close — so stops and
    targets are hit inside the minute like a live 15 s poll would see them;
  * persistence: trades and signal-log records are collected in memory, nothing is written to
    the dashboard database.
Results are therefore the same R-based records as Live Test, available in minutes instead of weeks.

Limits (shown in every report): fills come from M1 candles, not ticks; the spread is the M1
candle's recorded spread (often the minimum of that minute), plus optional `extra_spread_points`;
history depth is whatever the terminal holds (Tools > Options > Charts > Max bars in chart).

It never imports the executor and never calls an order API; a guard test enforces it.

Usage:
  python quant_agent.py --strategy LIVE_BBMA --setup MOMENTUM_REENTRY --symbol GBPAUD --days 30
  python quant_agent.py --live --days 30          # every live-strategy agent on every pair
  python quant_agent.py --all --days 14           # every Live Test agent
Reports go to reports/quant_<time>.csv and .html.
"""
from __future__ import annotations
import argparse
import bisect
import csv
import html
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from live_test import LiveAgent, LiveTestEngine, VALID, build_agent
from market_data import Bar, Quote

ROOT = Path(__file__).resolve().parent
TF_SECONDS = {'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800, 'H1': 3600, 'H4': 14400, 'D1': 86400}
SAMPLE_OFFSETS = (1, 20, 40, 59)          # seconds into each M1 candle: open, extreme, extreme, close
WARMUP_BARS = {'M1': 600, 'M5': 2100, 'M15': 600, 'M30': 400, 'H1': 600, 'H4': 600, 'D1': 200}


# --------------------------------------------------------------------------- market
class HistoricalMarket:
    """Replays bars. Same read-only interface the Live Test engine and agents use
    (resolve_symbol / spec / quote / is_fresh / candles); nothing else exists here."""

    def __init__(self, symbol, bars: dict, point, digits, tick_size=None, extra_spread_points=0.0):
        self.symbol, self.point, self.digits = symbol, float(point), int(digits)
        self.tick_size = float(tick_size or point)
        self.extra = float(extra_spread_points) * self.point
        self.bars = {tf: sorted(b, key=lambda x: x[0]) for tf, b in bars.items()}   # tf -> [(t,o,h,l,c,spread_pts)]
        self.times = {tf: [b[0] for b in bs] for tf, bs in self.bars.items()}
        self._quote = None
        self._memo = {}

    # -- the replay loop sets the current quote --
    def set_quote(self, bid, ask, t):
        self._quote = Quote(self.symbol, bid, ask, t, 0.0, self.point, self.digits)

    def resolve_symbol(self, base):
        return self.symbol if base == self.symbol else None

    def spec(self, symbol):
        return dict(point=self.point, digits=self.digits, tick_size=self.tick_size)

    def quote(self, symbol, now=None, max_age=None):
        if self._quote is None:
            raise ValueError('No quote yet')
        q = self._quote
        return q._replace(age=max(0.0, (now or q.time) - q.time))

    def is_fresh(self, q, max_age=None):
        return q.age <= 60

    def candles(self, symbol, timeframe, count, now=None):
        """The last `count` candles CLOSED at `now` (open time + length <= now)."""
        if timeframe not in self.bars:
            raise ValueError(f'No {timeframe} history for {symbol}')
        dur = TF_SECONDS[timeframe]
        end = bisect.bisect_right(self.times[timeframe], now - dur)
        key = (timeframe, count, end)              # unchanged until a new candle closes
        if key not in self._memo:
            if len(self._memo) > 64:
                self._memo.clear()
            rows = self.bars[timeframe][max(0, end - count):end]
            self._memo[key] = [Bar(int(r[0]), r[1], r[2], r[3], r[4]) for r in rows]
        return list(self._memo[key])


def m1_samples(bar, point, extra):
    """Four (time, bid, ask) quotes inside one M1 candle; ask = bid + recorded spread (+ extra)."""
    t, o, h, l, c, spread = bar
    s = max(float(spread), 0.0) * point + extra
    path = (o, l, h, c) if c >= o else (o, h, l, c)
    return [(t + off, round(p, 10), round(p + s, 10)) for off, p in zip(SAMPLE_OFFSETS, path)]


# --------------------------------------------------------------------------- engine
class ReplayEngine(LiveTestEngine):
    """LiveTestEngine without a database: the per-agent cycle logic is inherited unchanged."""

    def __init__(self, max_gap_default=120):
        self.store = None
        self.max_gap_default = max_gap_default
        self.agents = {}
        self._batch = None
        self.closed = []
        self.signal_log = []

    def _save_agent(self, la):
        pass

    def _save_position(self, pos):
        if pos.get('status') == 'CLOSED' and pos['trade_id'] not in self._closed_ids:
            self._closed_ids.add(pos['trade_id'])
            self.closed.append(dict(pos))
            self.closed_by_agent.setdefault(pos['agent_id'], []).append(dict(pos))

    def _drain_signal_log(self, la, symbol, now):
        for ev in la.state.pop('_emit', None) or []:
            self.signal_log.append(dict(ev, agent_id=la.id, epoch=ev.get('epoch', now)))

    def replay(self, agent, market: HistoricalMarket, start, end, progress=None):
        """Run one agent from `start` to `end` (epochs). Returns (closed trades, open positions)."""
        (trades, still_open), = self.replay_many([agent], market, start, end, progress).values()
        return trades, still_open

    def replay_many(self, agents, market: HistoricalMarket, start, end, progress=None):
        """Run several agents on ONE pair in lockstep, sample by sample, so they share every candle
        window and indicator calculation. Returns {agent_id: (closed trades, open positions)}."""
        self._closed_ids, self.closed_by_agent = set(), {}
        las = [LiveAgent(a, market.symbol, 'RUNNING') for a in agents]
        self.agents = {la.id: la for la in las}
        m1 = market.bars['M1']
        i0 = bisect.bisect_left(market.times['M1'], start)
        i1 = bisect.bisect_left(market.times['M1'], end)
        last_report = time.time()
        for k in range(i0, i1):
            for t, bid, ask in m1_samples(m1[k], market.point, market.extra):
                market.set_quote(bid, ask, t)
                for la in las:
                    self._run_agent(la, market, t)
            if progress and time.time() - last_report > 5:
                progress((k - i0) / max(1, i1 - i0))
                last_report = time.time()
        return {la.id: (self.closed_by_agent.get(la.id, []), la.open_positions) for la in las}


# --------------------------------------------------------------------------- stats
SESSIONS = (('Asia', 0, 7), ('London', 7, 13), ('New York', 13, 21), ('Late', 21, 24))


def session_of(epoch):
    h = datetime.fromtimestamp(epoch, timezone.utc).hour
    return next(name for name, a, b in SESSIONS if a <= h < b)


def summarise(trades, split=0.7):
    """Valid-trade statistics plus month / session breakdowns and an in- / out-of-sample split."""
    valid = sorted((t for t in trades if t.get('data_quality_status') == VALID), key=lambda t: t['entry_time'])

    def block(ts):
        rs = [t.get('pnl_r') or 0.0 for t in ts]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        equity = peak = dd = 0.0
        for r in rs:
            equity += r
            peak = max(peak, equity)
            dd = min(dd, equity - peak)
        pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else (math.inf if wins else None)
        return dict(trades=len(rs), win_rate=(len(wins) / len(rs)) if rs else None, net_r=round(sum(rs), 3),
                    avg_r=round(sum(rs) / len(rs), 4) if rs else None, profit_factor=pf, max_dd_r=round(dd, 3),
                    best_r=round(max(rs), 2) if rs else None)

    by_month, by_session = defaultdict(list), defaultdict(list)
    for t in valid:
        by_month[datetime.fromtimestamp(t['entry_time'], timezone.utc).strftime('%Y-%m')].append(t)
        by_session[session_of(t['entry_time'])].append(t)
    cut = int(len(valid) * split)
    out = block(valid)
    out.update(excluded=len(trades) - len(valid),
               in_sample=block(valid[:cut]), out_of_sample=block(valid[cut:]),
               months={k: block(v) for k, v in sorted(by_month.items())},
               sessions={k: block(by_session[k]) for k, _, _ in SESSIONS if by_session.get(k)})
    # one huge winner can carry a whole result (e.g. a tiny stop); show the result without it
    if len(valid) >= 5:
        rs = sorted(t.get('pnl_r') or 0.0 for t in valid)
        out['net_r_without_best'] = round(sum(rs[:-1]), 3)
    return out


def verdict(s, min_trades=30):
    """Plain-language label. Needs enough trades AND a positive out-of-sample part."""
    if s['trades'] < min_trades:
        return 'TOO FEW TRADES'
    oos = s['out_of_sample']
    robust = (s.get('net_r_without_best', s['net_r']) > 0 and (oos['net_r'] or 0) > 0)
    if s['avg_r'] and s['avg_r'] > 0.05 and robust:
        return 'EDGE (keep testing live)'
    if s['avg_r'] is not None and s['avg_r'] < -0.15:
        return 'LOSING (switch off)'
    return 'NO CLEAR EDGE'


# --------------------------------------------------------------------------- MT5 history
def load_history(mt5, symbol, start, end, timeframes, cfg):
    """Closed + forming bars from MT5 for [start - warm-up, end], converted to UTC with the verified
    bar offset. Returns (bars per tf, point, digits, tick_size)."""
    if not mt5.symbol_select(symbol, True):
        raise ValueError(f'{symbol}: not available at the broker')
    info = mt5.symbol_info(symbol)
    off = int(float(cfg.get('bar_timestamp_offset_hours', 0)) * 3600)
    out = {}
    for tf in timeframes:
        dur = TF_SECONDS[tf]
        a = datetime.fromtimestamp(start - WARMUP_BARS.get(tf, 600) * dur + off, timezone.utc)
        b = datetime.fromtimestamp(end + off, timezone.utc)
        rows = mt5.copy_rates_range(symbol, getattr(mt5, 'TIMEFRAME_' + tf), a, b)
        if rows is None or len(rows) == 0:
            raise ValueError(f'{symbol}: no {tf} history for this period')
        out[tf] = [(int(r['time']) - off, float(r['open']), float(r['high']), float(r['low']),
                    float(r['close']), float(r['spread'])) for r in rows]
    return out, float(info.point), int(info.digits), float(getattr(info, 'trade_tick_size', 0) or info.point)


# --------------------------------------------------------------------------- runs
def registered_jobs(store, strategies=None, symbols=None):
    """[(agent, symbol)] for the agents registered in Live Test right now (not removed), rebuilt from
    their saved config so each result lands on the same agent id the dashboard and Signal Agent use."""
    with store.connect() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = db.execute('SELECT id, strategy_id, symbol, config, status FROM live_test_agents').fetchall()             if 'live_test_agents' in tables else []
    jobs = []
    for r in rows:
        if r['status'] == 'REMOVED' or (strategies and r['strategy_id'] not in strategies)                 or (symbols and r['symbol'] not in symbols):
            continue
        try:
            agent = build_agent(r['strategy_id'], json.loads(r['config'] or '{}'))
        except Exception:
            agent = None
        if agent is not None and f"{agent.strategy_id}|{r['symbol']}|{agent.config_id}" == r['id']:
            jobs.append((agent, r['symbol']))
    return jobs


def jobs_for(scope, cfg, store, strategy=None, setup=None, symbol=None):
    """[(agent, symbol)] to backtest. scope: 'one' | 'live' | 'all'."""
    from live_test import LiveTestEngine as _E, ensure_default_agents, live_strategy_agents
    if scope == 'live':
        return live_strategy_agents(cfg, store)
    if scope == 'all':
        tmp = _E.__new__(_E)
        tmp.store, tmp.agents, tmp._batch = store, {}, None
        tmp.register = lambda agent, base, status='PAUSED': tmp.agents.setdefault(f'{agent.strategy_id}|{base}|{agent.config_id}', LiveAgent(agent, base, status))
        tmp._sync = lambda: None
        tmp.set_status = lambda *a, **k: None
        ensure_default_agents(tmp, cfg)
        return [(la.agent, la.base_symbol) for la in tmp.agents.values()]
    for agent, base in live_strategy_agents(cfg, store) if strategy and strategy.startswith('LIVE_') else []:
        if agent.strategy_id == strategy and base == symbol and (setup is None or agent.config.get('setup') == setup):
            return [(agent, symbol)]
    config = {'setup': setup} if setup else {}
    agent = build_agent(strategy, config)
    if agent is None:
        raise SystemExit(f'Unknown strategy {strategy}')
    return [(agent, symbol)]


def run(jobs, start, end, cfg, mt5, extra_spread_points=0.0, log=print):
    """Backtest every (agent, symbol). History is loaded once per symbol and shared."""
    by_symbol = defaultdict(list)
    for agent, symbol in jobs:
        by_symbol[symbol].append(agent)
    results, done, total = [], 0, len(jobs)
    log(f'PROGRESS 0/{total}')
    for symbol, agents in by_symbol.items():
        done += len(agents)
        try:
            bars, point, digits, tick = load_history(mt5, symbol, start, end,
                                                     ('M1', 'M5', 'M15', 'M30', 'H1', 'H4'), cfg)
        except Exception as exc:
            log(f'{symbol}: skipped ({exc})')
            log(f'PROGRESS {done}/{total}')
            continue
        first = bars['M1'][0][0] if bars['M1'] else end
        t0 = time.time()
        market = HistoricalMarket(symbol, bars, point, digits, tick, extra_spread_points)
        outcome = ReplayEngine().replay_many(agents, market, max(start, first), end)
        seconds = round((time.time() - t0) / max(1, len(agents)), 1)
        for agent in agents:
            trades, still_open = outcome[f"{agent.strategy_id}|{symbol}|{agent.config_id}"]
            s = summarise(trades)
            row = dict(strategy_id=agent.strategy_id, label=agent.label(), symbol=symbol,
                       config_id=agent.config_id, setup=agent.config.get('setup', ''),
                       start=max(start, first), end=end, open_at_end=len(still_open), stats=s,
                       verdict=verdict(s), seconds=seconds, trades=trades)
            results.append(row)
            log(f"{symbol:11s} {agent.label()[:46]:46s} {s['trades']:4d} trades  "
                f"net {s['net_r']:+7.2f}R  avg {s['avg_r'] if s['avg_r'] is not None else 0:+.3f}R  "
                f"{row['verdict']}  ({row['seconds']}s)")
        log(f'PROGRESS {done}/{total}')
    return results


# --------------------------------------------------------------------------- results store
# The newest backtest per agent id, read by the Signal Agent to grade live setups. Compact stats
# only (no trade list). Agent ids match Live Test ids, so a result applies to the same config.
RESULTS_SCHEMA = '''CREATE TABLE IF NOT EXISTS quant_results(
 agent_id TEXT PRIMARY KEY, strategy_id TEXT, symbol TEXT, config_id TEXT, setup TEXT,
 start REAL, end REAL, created REAL, verdict TEXT, stats TEXT)'''


def compact(stats):
    keep = ('trades', 'win_rate', 'net_r', 'avg_r', 'max_dd_r', 'net_r_without_best', 'excluded')
    out = {k: stats.get(k) for k in keep}
    pf = stats.get('profit_factor')
    out['profit_factor'] = None if pf is None or math.isinf(pf) else pf
    oos = stats.get('out_of_sample') or {}
    out['oos_trades'], out['oos_net_r'] = oos.get('trades'), oos.get('net_r')
    return out


def save_results(store, results, now=None):
    now = time.time() if now is None else now
    with store.connect() as db:
        db.execute(RESULTS_SCHEMA)
        for r in results:
            db.execute('INSERT OR REPLACE INTO quant_results VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (f"{r['strategy_id']}|{r['symbol']}|{r['config_id']}", r['strategy_id'], r['symbol'],
                        r['config_id'], r.get('setup') or '', r['start'], r['end'], now, r['verdict'],
                        json.dumps(compact(r['stats']), allow_nan=False)))


def load_results(store):
    """{agent_id: dict(verdict, start, end, created, **compact stats)}; empty before the first run."""
    with store.connect() as db:
        db.execute(RESULTS_SCHEMA)
        rows = db.execute('SELECT * FROM quant_results').fetchall()
    return {r['agent_id']: dict(json.loads(r['stats']), verdict=r['verdict'], start=r['start'], end=r['end'],
                                created=r['created']) for r in rows}


def report_view(store):
    """Dashboard view: every saved backtest beside the same agent's Live Test record and status."""
    results = load_results(store)
    if not results:
        return dict(status='WAITING', rows=[], note='No Quant Agent backtest yet. Run it from the project folder, '
                    'e.g. "python quant_agent.py --live --days 30" (needs MT5 open; takes a few minutes).')
    with store.connect() as db:
        agents = {r['id']: r for r in db.execute('SELECT id, strategy_id, config, status FROM live_test_agents')}
        trades = defaultdict(list)
        for r in db.execute('SELECT agent_id, payload FROM live_test_trades'):
            if r['agent_id'] in results:
                trades[r['agent_id']].append(json.loads(r['payload']))
    rows = []
    for aid, b in results.items():
        strategy_id, symbol = aid.split('|')[:2]
        a = agents.get(aid)
        label = strategy_id
        if a is not None:
            try:
                label = build_agent(a['strategy_id'], json.loads(a['config'])).label()
            except Exception:
                pass
        fwd = summarise(trades.get(aid, [])) if trades.get(aid) else None
        rows.append(dict(agent_id=aid, strategy_id=strategy_id, symbol=symbol, label=label,
                         agent_status=a['status'] if a is not None else 'REMOVED', backtest=b,
                         forward=None if fwd is None else dict(trades=fwd['trades'], win_rate=fwd['win_rate'],
                                                               net_r=fwd['net_r'], avg_r=fwd['avg_r'])))
    rows.sort(key=lambda r: (-(r['backtest'].get('net_r') or 0), r['symbol']))
    created = [b['created'] for b in results.values()]
    counts = defaultdict(int)
    for b in results.values():
        counts[b['verdict']] += 1
    return dict(status='OK', rows=rows, verdicts=dict(counts), last_run_epoch=max(created),
                window_start=min(b['start'] for b in results.values()), window_end=max(b['end'] for b in results.values()))


# --------------------------------------------------------------------------- reports
def write_reports(results, folder: Path, start, end, extra_spread_points):
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path, html_path, trades_path = (folder / f'quant_{stamp}.csv', folder / f'quant_{stamp}.html',
                                        folder / f'quant_{stamp}_trades.csv')
    cols = ['strategy_id', 'setup', 'symbol', 'label', 'trades', 'win_rate', 'net_r', 'avg_r', 'profit_factor',
            'max_dd_r', 'best_r', 'net_r_without_best', 'oos_trades', 'oos_net_r', 'excluded', 'verdict', 'config_id']
    with open(csv_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in results:
            s = r['stats']
            w.writerow([r['strategy_id'], r['setup'], r['symbol'], r['label'], s['trades'], s['win_rate'], s['net_r'],
                        s['avg_r'], s['profit_factor'], s['max_dd_r'], s['best_r'], s.get('net_r_without_best'),
                        s['out_of_sample']['trades'], s['out_of_sample']['net_r'], s['excluded'], r['verdict'],
                        r['config_id']])
    tcols = ['strategy_id', 'symbol', 'setup', 'side', 'entry_time', 'entry_price', 'initial_sl', 'initial_tp',
             'exit_time', 'exit_price', 'exit_reason', 'pnl_r', 'data_quality_status', 'signal_reason']
    with open(trades_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(tcols)
        for r in results:
            for t in r['trades']:
                w.writerow([r['strategy_id'], r['symbol'], r['setup']] + [
                    datetime.fromtimestamp(t[c], timezone.utc).isoformat() if c in ('entry_time', 'exit_time') and t.get(c)
                    else t.get(c) for c in tcols[3:]])
    html_path.write_text(render_html(results, start, end, extra_spread_points), encoding='utf-8')
    return csv_path, html_path, trades_path


def render_html(results, start, end, extra_spread_points):
    day = lambda e: datetime.fromtimestamp(e, timezone.utc).strftime('%d %b %Y')
    def f(v, d=2, pct=False):
        if v is None:
            return '—'
        if isinstance(v, float) and math.isinf(v):
            return '∞'
        return f'{v * 100:.0f}%' if pct else f'{v:+.{d}f}' if d else str(v)
    rows = []
    for r in sorted(results, key=lambda r: -(r['stats']['avg_r'] or -9)):
        s = r['stats']
        cls = 'good' if r['verdict'].startswith('EDGE') else 'bad' if r['verdict'].startswith('LOSING') else 'muted'
        rows.append(f"<tr><td>{html.escape(r['label'])}</td><td>{html.escape(r['symbol'])}</td><td>{s['trades']}</td>"
                    f"<td>{f(s['win_rate'], pct=True)}</td><td>{f(s['net_r'])}</td><td>{f(s['avg_r'], 3)}</td>"
                    f"<td>{f(s['profit_factor'])}</td><td>{f(s['max_dd_r'])}</td><td>{f(s.get('net_r_without_best'))}</td>"
                    f"<td>{f(s['out_of_sample']['net_r'])}</td><td class='{cls}'>{html.escape(r['verdict'])}</td></tr>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quant Agent report</title><style>
:root{{color-scheme:dark;--bg:#0b1b17;--panel:#112822;--line:#24423b;--text:#e8f5f2;--muted:#89ada4;--gold:#ccae76;--good:#5ee0be;--bad:#f28c99}}
body{{margin:0;padding:24px 16px;background:var(--bg);color:var(--text);font:14px/1.5 ui-sans-serif,"Segoe UI",sans-serif}}
h1{{font-size:20px;font-weight:500;color:var(--gold);margin:0 0 4px}}p{{color:var(--muted);margin:4px 0 16px;max-width:900px}}
.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:900px;background:var(--panel)}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}th{{color:var(--muted);font-weight:500}}
td:first-child,th:first-child,td:nth-child(2),th:nth-child(2),td:last-child,th:last-child{{text-align:left}}
.good{{color:var(--good)}}.bad{{color:var(--bad)}}.muted{{color:var(--muted)}}</style></head><body>
<h1>Quant Agent backtest</h1>
<p>{day(start)} – {day(end)} · {len(results)} agent × pair runs · simulated with the Live Test engine on MT5 M1 history (four quotes per minute),
recorded M1 spread{f' + {extra_spread_points:g} points' if extra_spread_points else ''}. Valid trades only. "Without best" removes the single biggest win;
"Out-of-sample" is the last 30% of trades. Backtests are optimistic versus live — confirm in Live Test before switching anything on.</p>
<div class="wrap"><table><thead><tr><th>Agent</th><th>Pair</th><th>Trades</th><th>Win</th><th>Net R</th><th>Avg R</th><th>PF</th>
<th>Max DD R</th><th>Without best</th><th>Out-of-sample R</th><th>Verdict</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
</body></html>"""


# --------------------------------------------------------------------------- CLI
def main(argv=None):
    p = argparse.ArgumentParser(description='Quant Agent: backtest Live Test agents on MT5 history (simulated).')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--strategy', help='strategy_id, e.g. LIVE_BBMA, LIVE_CONFLUENCE, CMS_SSR')
    g.add_argument('--live', action='store_true', help='every live-strategy agent (BBMA, Confluence, ORB) on its pairs')
    g.add_argument('--all', action='store_true', help='every default Live Test agent')
    g.add_argument('--registered', action='store_true',
                   help='the agents registered in Live Test now (their saved settings); filter with --strategies/--symbols')
    p.add_argument('--strategies', help='comma-separated strategy_id filter for --registered')
    p.add_argument('--setup', help='BBMA setup: CSA_REENTRY, MOMENTUM_REENTRY or MHV_REVERSAL')
    p.add_argument('--symbol', default='XAUUSD')
    p.add_argument('--symbols', help='comma-separated filter for --live/--all')
    p.add_argument('--days', type=float, default=30)
    p.add_argument('--extra-spread-points', type=float, default=0.0, help='added to every quote (stress test)')
    a = p.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass
    cfg = json.loads((ROOT / 'config.json').read_text(encoding='utf-8-sig'))
    from dashboard_store import Store
    store = Store(ROOT / 'dashboard.sqlite3')
    if a.registered:
        jobs = registered_jobs(store, {s.strip() for s in a.strategies.split(',')} if a.strategies else None)
    else:
        scope = 'live' if a.live else 'all' if a.all else 'one'
        jobs = jobs_for(scope, cfg, store, a.strategy, a.setup, a.symbol)
    if a.symbols:
        keep = {s.strip() for s in a.symbols.split(',')}
        jobs = [j for j in jobs if j[1] in keep]
    if not jobs:
        raise SystemExit('Nothing to backtest: no matching agent.')
    end = time.time()
    start = end - a.days * 86400
    import MetaTrader5 as mt5
    if not mt5.initialize(**({'path': cfg['terminal_path']} if cfg.get('terminal_path') else {})):
        raise SystemExit('Cannot connect to MT5.')
    try:
        print(f'Quant Agent: {len(jobs)} runs, {a.days:g} days. Read-only; no order is sent.', flush=True)
        results = run(jobs, start, end, cfg, mt5, a.extra_spread_points, log=lambda m: print(m, flush=True))
    finally:
        mt5.shutdown()
    paths = write_reports(results, ROOT / 'reports', start, end, a.extra_spread_points)
    if not a.extra_spread_points:          # stress tests are reports only; they never replace the grades
        save_results(store, results)
        print(f'Saved {len(results)} results for the Signal Agent.')
    print('Reports:', *[str(x) for x in paths], sep='\n  ')


if __name__ == '__main__':
    main()
