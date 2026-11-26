"""Market Scout: a regular, read-only report on which markets are worth watching right now.

Every sweep (default hourly) it looks at every symbol in MT5's Market Watch plus the configured live
and Live-Test-only pairs and measures, per symbol:

  * cost        typical spread as a share of a typical hourly move (H1 ATR), and today's spread
                against that typical spread
  * volatility  the last 5 days' daily range against the previous 60 days (quiet / normal / active)
  * trend       EMA 20/50 alignment on H1 and H4 plus how directly price has moved (efficiency
                ratio); strong when both timeframes agree
  * news        high-impact events in the next 24 h for the symbol's currencies (the bot's own
                MT5 calendar feed; nothing is fetched from the internet)

It turns those into a 0-100 "worth watching" score and plain recommendations ("consider adding to
Live Test", "spread unusually high", "trending strongly", "USD news in 40 min"). The Signal Agent
uses the typical spread to flag setups that opened while spreads were unusually wide.

The sweep is spread over several bot cycles (a few symbols per cycle) so it never delays the live
scanners. READ-ONLY: symbol list, symbol info, last tick and candles. It has no order API (guard test)
and changes nothing in MT5, including Market Watch (hidden symbols are skipped, never added).
"""
from __future__ import annotations

import json
import math
import statistics
import time

SCHEMA = 'CREATE TABLE IF NOT EXISTS market_scout(id INTEGER PRIMARY KEY CHECK (id=1), updated REAL, payload TEXT)'
FIAT = {'USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD', 'CHF', 'NZD'}
ROLE_ORDER = {'LIVE': 0, 'LIVE_TEST': 1, 'CANDIDATE': 2}
WIDE_SPREAD_X = 2.5     # MT5 bars store the bar's lowest spread, so a live quote is often ~1.5x it
DEFAULTS = dict(enabled=True, interval_minutes=60, symbols_per_cycle=5, cycle_budget_seconds=1.5,
                news_hours=24, extra_symbols=[])


def settings(cfg):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg.get('market_scout') or {}).items() if k in DEFAULTS})
    return out


# --------------------------------------------------------------------------- maths (pure)
def _tr(bars):
    """True ranges of (t, o, h, l, c, spread) bars, from the second bar on."""
    return [max(b[2], p[4]) - min(b[3], p[4]) for p, b in zip(bars, bars[1:])]


def atr(bars, n=14):
    tr = _tr(bars[-(n + 1):])
    return sum(tr) / len(tr) if tr else None


def ema_series(values, n):
    if len(values) < n:
        return []
    out = [sum(values[:n]) / n]
    a = 2 / (n + 1)
    for v in values[n:]:
        out.append(out[-1] + a * (v - out[-1]))
    return out


def efficiency(closes, n):
    """Kaufman efficiency ratio: net move / path length over n bars (0 = chop, 1 = straight line)."""
    c = closes[-(n + 1):]
    path = sum(abs(b - a) for a, b in zip(c, c[1:]))
    return abs(c[-1] - c[0]) / path if len(c) > 1 and path > 0 else 0.0


def direction(closes):
    """+1 / -1 when close > EMA20 > EMA50 with EMA50 rising (or the mirror), else 0."""
    e20, e50 = ema_series(closes, 20), ema_series(closes, 50)
    if len(e50) < 11:
        return 0
    c, a, b, b_old = closes[-1], e20[-1], e50[-1], e50[-11]
    if c > a > b and b > b_old:
        return 1
    if c < a < b and b < b_old:
        return -1
    return 0


def trend(h1, h4):
    c1, c4 = [b[4] for b in h1], [b[4] for b in h4]
    d1, d4 = direction(c1), direction(c4)
    er = round((efficiency(c1, 48) + efficiency(c4, 30)) / 2, 3)
    if er < 0.10:                       # price going nowhere, whatever the averages say
        label = 'RANGING'
    elif d1 and d1 == d4:
        label = ('STRONG ' if er >= 0.3 else '') + ('UP' if d1 > 0 else 'DOWN')
    else:
        label = 'MIXED'
    return dict(label=label, h1=d1, h4=d4, efficiency=er)


def volatility(d1):
    """Average of the last 5 completed daily ranges vs the median of the 60 before them."""
    done = d1[:-1]                                  # the last D1 bar is still forming
    tr = _tr(done)
    if len(tr) < 20:
        return dict(regime='UNKNOWN', ratio=None)
    recent, base = sum(tr[-5:]) / 5, statistics.median(tr[-65:-5])
    ratio = round(recent / base, 2) if base else None
    regime = ('UNKNOWN' if ratio is None else 'EXTREME' if ratio >= 2.0 else 'ACTIVE' if ratio >= 1.4
              else 'QUIET' if ratio < 0.7 else 'NORMAL')
    return dict(regime=regime, ratio=ratio)


def score(cost_pct, tr, vol):
    """0-100 'worth watching': cheap to trade (45 %), trending cleanly (35 %), normal-to-active (20 %)."""
    cost_s = 0.0 if cost_pct is None else min(1.0, max(0.0, (0.25 - cost_pct) / 0.20))
    agree = 1.0 if tr['h1'] and tr['h1'] == tr['h4'] else 0.5 if (tr['h1'] or tr['h4']) else 0.25
    trend_s = min(1.0, tr['efficiency'] / 0.4) * agree
    r = vol.get('ratio')
    act_s = 0.3 if r is None else 1.0 if 0.8 <= r <= 1.8 else 0.6 if 0.6 <= r <= 2.5 else 0.3
    return round(100 * (0.45 * cost_s + 0.35 * trend_s + 0.20 * act_s))


def typical_by_hour(h1, min_samples=3):
    """Median recorded spread (points) per UTC hour of day: spreads widen in thin sessions."""
    hours = {}
    for b in h1:
        if b[5] and b[5] > 0:
            hours.setdefault(str(int(b[0] // 3600) % 24), []).append(b[5])
    return {h: statistics.median(v) for h, v in hours.items() if len(v) >= min_samples}


def usual_spread(row, epoch):
    """The usual spread (price units) for this symbol at that hour of day, from a scout row."""
    pts = (row.get('typical_spread_by_hour') or {}).get(str(int(epoch // 3600) % 24)) or row.get('typical_spread_points')
    return pts * row['point'] if pts and row.get('point') else None


def analyse(symbol, role, h1, h4, d1, point, spread_now_points=None, currencies=(), events=(), now=None,
            news_hours=24):
    """One symbol's row. Bars are (t, o, h, l, c, spread_points) tuples, oldest first; events are
    the bot's calendar events (currency, title, epoch)."""
    now = time.time() if now is None else now
    a = atr(h1)
    spreads = [b[5] for b in h1[-120:] if b[5] and b[5] > 0]
    typical = statistics.median(spreads) if spreads else None
    by_hour = typical_by_hour(h1)
    cost_pct = round(typical * point / a, 4) if typical and a else None
    usual_now = by_hour.get(str(int(now // 3600) % 24), typical)
    spread_x = round(spread_now_points / usual_now, 2) if usual_now and spread_now_points else None
    tr, vol = trend(h1, h4), volatility(d1)
    cur = sorted(set(currencies) & FIAT)
    news = sorted(({'currency': e['currency'], 'title': e.get('title', ''), 'epoch': e['epoch'],
                    'minutes': round((e['epoch'] - now) / 60)} for e in events
                   if e.get('currency') in cur and now <= e.get('epoch', 0) <= now + news_hours * 3600),
                  key=lambda e: e['epoch'])
    return dict(symbol=symbol, role=role, price=h1[-1][4] if h1 else None, point=point,
                atr_h1=a, typical_spread_points=typical,
                typical_spread=None if typical is None else typical * point,
                typical_spread_by_hour=by_hour,
                spread_now_points=spread_now_points, spread_x=spread_x, cost_pct=cost_pct,
                volatility=vol, trend=tr, currencies=cur, news=news[:5],
                score=score(cost_pct, tr, vol))


def recommendations(rows, now=None):
    """Plain-language notes, most urgent first: news, cost, volatility, trend, candidates."""
    now = time.time() if now is None else now
    out, watched = [], [r for r in rows if r['role'] != 'CANDIDATE']
    by_event = {}
    for r in watched:
        for e in r['news']:
            if e['minutes'] <= 240:
                by_event.setdefault((e['currency'], e['title'], e['epoch']), []).append(r['symbol'])
    for (cur, title, epoch), syms in sorted(by_event.items(), key=lambda kv: kv[0][2]):
        m = round((epoch - now) / 60)
        when = f'in {m} min' if m < 120 else f'in {m / 60:.1f} h'
        out.append(dict(kind='NEWS', symbol=','.join(syms),
                        text=f'{cur} high-impact news {when}: {title}. Affects {", ".join(syms)}.'))
    for r in watched:
        if r['spread_x'] and r['spread_x'] >= WIDE_SPREAD_X:
            out.append(dict(kind='COST', symbol=r['symbol'],
                            text=f"Spread on {r['symbol']} is unusually high: {r['spread_x']:.1f}× its usual level at this hour."))
        elif r['role'] == 'LIVE' and r['cost_pct'] is not None and r['cost_pct'] >= 0.20:
            out.append(dict(kind='COST', symbol=r['symbol'],
                            text=f"{r['symbol']} is expensive to trade: the spread is {r['cost_pct'] * 100:.0f}% of a typical hourly move."))
    for r in rows:
        ratio, reg = r['volatility']['ratio'], r['volatility']['regime']
        if r['role'] != 'CANDIDATE' and reg in ('ACTIVE', 'EXTREME') and ratio >= 1.8:
            out.append(dict(kind='VOLATILITY', symbol=r['symbol'],
                            text=f"{r['symbol']} is unusually active: daily range {ratio:.1f}× normal."))
        elif r['role'] == 'LIVE' and reg == 'QUIET':
            out.append(dict(kind='VOLATILITY', symbol=r['symbol'],
                            text=f"{r['symbol']} is quiet: daily range {ratio:.1f}× normal (fewer, smaller moves)."))
    for r in rows:
        if r['trend']['label'].startswith('STRONG') and (r['role'] != 'CANDIDATE' or r['score'] >= 65):
            way = 'up' if r['trend']['h1'] > 0 else 'down'
            out.append(dict(kind='TREND', symbol=r['symbol'],
                            text=f"{r['symbol']} is trending strongly {way} (H1 and H4 agree, efficiency {r['trend']['efficiency']:.2f})."))
    for r in sorted(rows, key=lambda r: -r['score']):
        if r['role'] == 'CANDIDATE' and r['score'] >= 65 and r['cost_pct'] is not None and r['cost_pct'] <= 0.08:
            out.append(dict(kind='ADD', symbol=r['symbol'],
                            text=f"{r['symbol']}: consider adding to Live Test. Score {r['score']}, spread {r['cost_pct'] * 100:.1f}% "
                                 f"of an hourly move, trend {r['trend']['label'].lower()}, volatility {r['volatility']['regime'].lower()}."))
    return out


# --------------------------------------------------------------------------- MT5 reading (read-only)
def _bars(mt5, symbol, tf, count, off):
    rows = mt5.copy_rates_from_pos(symbol, getattr(mt5, 'TIMEFRAME_' + tf), 0, count)
    if rows is None or len(rows) == 0:
        return []
    return [(int(r['time']) - off, float(r['open']), float(r['high']), float(r['low']), float(r['close']),
             float(r['spread'])) for r in rows]


def universe(mt5, cfg, s):
    """[(symbol, role)]: live pairs, Live-Test-only pairs, then everything visible in Market Watch."""
    from live_test import live_test_extra_symbols
    roles = {}
    for sym in cfg.get('symbols', []):
        roles.setdefault(sym, 'LIVE')
    for sym in live_test_extra_symbols(cfg):
        roles.setdefault(sym, 'LIVE_TEST')
    for sym in s.get('extra_symbols') or []:
        roles.setdefault(sym, 'CANDIDATE')
    for info in mt5.symbols_get() or []:
        if getattr(info, 'visible', False):
            roles.setdefault(info.name, 'CANDIDATE')
    return list(roles.items())


def scan_symbol(mt5, cfg, symbol, role, events, now, news_hours=24):
    info = mt5.symbol_info(symbol)
    if info is None or not getattr(info, 'visible', False):
        return dict(symbol=symbol, role=role, error='not in Market Watch')
    off = int(float(cfg.get('bar_timestamp_offset_hours', 0)) * 3600)
    h1, h4, d1 = (_bars(mt5, symbol, 'H1', 300, off), _bars(mt5, symbol, 'H4', 150, off),
                  _bars(mt5, symbol, 'D1', 70, off))
    if len(h1) < 60 or len(h4) < 60:
        return dict(symbol=symbol, role=role, error='not enough history')
    tick = mt5.symbol_info_tick(symbol)
    tick_off = float(cfg.get('tick_timestamp_offset_hours', 0)) * 3600
    spread_now = None
    if tick and tick.bid and tick.ask and now - (tick.time - tick_off) <= 300:     # live quote only
        spread_now = round((tick.ask - tick.bid) / info.point, 1)
    cur = {getattr(info, 'currency_base', ''), getattr(info, 'currency_profit', '')}
    return analyse(symbol, role, h1, h4, d1, float(info.point), spread_now, cur, events, now, news_hours)


# --------------------------------------------------------------------------- incremental scout
class MarketScout:
    """Call step() once per bot cycle. A sweep is split over cycles; the finished report is saved."""

    def __init__(self, store, cfg, mt5, clock=time.time):
        self.store, self.cfg, self.mt5, self.clock = store, cfg, mt5, clock
        self.s = settings(cfg)
        self.queue, self.rows, self.started = [], [], None
        self.next_sweep = 0.0
        with store.connect() as db:
            db.execute(SCHEMA)
            row = db.execute('SELECT updated FROM market_scout WHERE id=1').fetchone()
        if row:                           # resume the hourly rhythm after a restart
            self.next_sweep = row['updated'] + self.s['interval_minutes'] * 60

    def _events(self):
        try:
            with self.store.connect() as db:
                row = db.execute("SELECT value FROM meta WHERE key='news_filter'").fetchone()
            data = json.loads(row['value']) if row else {}
            return data.get('events') or [], data.get('error')
        except Exception:
            return [], 'news status unavailable'

    def step(self, force=False):
        if not self.s['enabled']:
            return None
        now = self.clock()
        if not self.queue and self.started is None:
            if not force and now < self.next_sweep:
                return None
            self.queue, self.rows, self.started = universe(self.mt5, self.cfg, self.s), [], now
            self.events, self.news_error = self._events()
        t0, done = time.monotonic(), 0
        while self.queue and (force or (done < self.s['symbols_per_cycle']
                                        and time.monotonic() - t0 < self.s['cycle_budget_seconds'])):
            sym, role = self.queue.pop(0)
            try:
                self.rows.append(scan_symbol(self.mt5, self.cfg, sym, role, self.events, now, self.s['news_hours']))
            except Exception as exc:
                self.rows.append(dict(symbol=sym, role=role, error=str(exc)[:120]))
            done += 1
        if self.queue:
            return None
        report = self.publish(self.clock())
        self.started, self.next_sweep = None, report['started_epoch'] + self.s['interval_minutes'] * 60
        return report

    def publish(self, now):
        ok = [r for r in self.rows if not r.get('error')]
        ok.sort(key=lambda r: (-r['score'], ROLE_ORDER.get(r['role'], 9), r['symbol']))
        report = dict(status='OK', generated_epoch=now, started_epoch=self.started,
                      sweep_seconds=round(now - self.started, 1), interval_minutes=self.s['interval_minutes'],
                      rows=ok, skipped=[dict(symbol=r['symbol'], role=r['role'], reason=r['error'])
                                        for r in self.rows if r.get('error')],
                      recommendations=recommendations(ok, now), news_error=self.news_error,
                      note='Read-only market report. It never trades or changes Market Watch.')
        save(self.store, report)
        return report


def save(store, report):
    with store.connect() as db:
        db.execute(SCHEMA)
        db.execute('INSERT OR REPLACE INTO market_scout VALUES (1,?,?)',
                   (report['generated_epoch'], json.dumps(report, allow_nan=False)))


def load(store):
    with store.connect() as db:
        db.execute(SCHEMA)
        row = db.execute('SELECT payload FROM market_scout WHERE id=1').fetchone()
    return json.loads(row['payload']) if row else None


def report_view(store):
    r = load(store)
    if r is None:
        return dict(status='WAITING', rows=[], recommendations=[], skipped=[],
                    note='No Market Scout sweep yet. It runs while the bot is running (first sweep within a few minutes).')
    return r


def main(argv=None):
    import argparse
    import sys
    from pathlib import Path
    p = argparse.ArgumentParser(description='Market Scout: one full read-only sweep now, printed and saved for the dashboard.')
    p.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass
    root = Path(__file__).resolve().parent
    cfg = json.loads((root / 'config.json').read_text(encoding='utf-8-sig'))
    from dashboard_store import Store
    import MetaTrader5 as mt5
    if not mt5.initialize(**({'path': cfg['terminal_path']} if cfg.get('terminal_path') else {})):
        raise SystemExit('Cannot connect to MT5.')
    try:
        scout = MarketScout(Store(root / 'dashboard.sqlite3'), cfg, mt5)
        r = scout.step(force=True)
    finally:
        mt5.shutdown()
    print(f"Market Scout: {len(r['rows'])} symbols in {r['sweep_seconds']}s · read-only, nothing traded")
    for x in r['rows']:
        cost = '—' if x['cost_pct'] is None else f"{x['cost_pct'] * 100:5.1f}%"
        print(f"{x['symbol']:12s} {x['role']:10s} score {x['score']:3d}  cost {cost}  "
              f"spread×{x['spread_x'] if x['spread_x'] is not None else '—':<5}  vol {x['volatility']['regime']:8s}  "
              f"trend {x['trend']['label']:12s} news {len(x['news'])}")
    print('\nRecommendations:')
    for rec in r['recommendations']:
        print(f"  [{rec['kind']}] {rec['text']}")
    if r['skipped']:
        print('Skipped:', ', '.join(f"{s['symbol']} ({s['reason']})" for s in r['skipped']))


if __name__ == '__main__':
    main()
