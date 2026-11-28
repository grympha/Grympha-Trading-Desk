"""Signal calibration: do the Signal Agent's grades actually predict how setups turn out?

Every Signal Agent row is graded (A/B/C/X) and frozen when the setup appears, together with the
agent's record at that moment (backtest and Live Test trades and win rates). Once the simulated
trade closes, this module scores the grades against what really happened:

  * per grade        closed trades, win rate, average R with a 95 % range, net R
  * order check      with enough trades, A should beat B, B beat C, and C beat X on average R
  * Brier score      the record's win rate is the predicted chance of a win; Brier is the average
                     squared gap between that chance and the result (1 = win, 0 = loss). Lower is
                     better. It is compared with a baseline that always predicts the overall win
                     rate of the same trades: skill > 0 means the records add information.
  * reliability      predicted-chance buckets against the real win rate in each bucket
  * splits           by market regime at entry (Market Scout) and by strategy

Windows: last 7 days, last 30 days and all time (by entry time). One compact snapshot per
Malaysia day is saved so the trend shows over weeks. REPORT ONLY: it reads the Signal Agent feed
and the Live Test trades, writes its own snapshot table, never changes a grade rule, and never
touches orders, positions or the executor.
"""
from __future__ import annotations

import json
import math
import time

from live_test import VALID

SCHEMA = 'CREATE TABLE IF NOT EXISTS signal_calibration(day TEXT PRIMARY KEY, epoch REAL, payload TEXT)'
GRADES = ('A', 'B', 'C', 'X')
GRADE_NAMES = dict(A='Proven', B='Promising', C='Unproven', X='Losing')
WINDOWS = (('7d', 'Last 7 days', 7 * 86400), ('30d', 'Last 30 days', 30 * 86400), ('all', 'All time', None))
MIN_GRADE = 20          # closed trades a grade needs before it counts in the order check
MIN_BRIER = 50          # scored trades before the Brier verdict is shown
MIN_RECORD = 5          # trades an agent's record needs before its win rate counts as a prediction
KEEP_DAYS = 120
MYT = 8 * 3600


def myt_day(epoch):
    return time.strftime('%Y-%m-%d', time.gmtime(epoch + MYT))


# --------------------------------------------------------------------------- maths (pure)
def predicted(feed):
    """Chance of a win the agent's record implied when the setup was graded: the trade-weighted
    win rate of its backtest and Live Test record. None when the record had fewer than MIN_RECORD
    trades (one lucky win would otherwise 'predict' 100 %)."""
    n = p = 0.0
    for part in (feed.get('backtest'), feed.get('forward')):
        if part and part.get('trades') and part.get('win_rate') is not None:
            n += part['trades']; p += part['trades'] * part['win_rate']
    return p / n if n >= MIN_RECORD else None


def grade_stats(rs):
    """rs: list of R results. Count, win rate, average R with a 95 % range (normal approximation)."""
    n = len(rs)
    if not n:
        return dict(closed=0, wins=0, win_rate=None, avg_r=None, net_r=0.0, low=None, high=None)
    avg = sum(rs) / n
    half = None
    if n >= 2:
        sd = math.sqrt(sum((r - avg) ** 2 for r in rs) / (n - 1))
        half = 1.96 * sd / math.sqrt(n)
    wins = sum(r > 0 for r in rs)
    return dict(closed=n, wins=wins, win_rate=round(wins / n, 4), avg_r=round(avg, 3), net_r=round(sum(rs), 2),
                low=None if half is None else round(avg - half, 3), high=None if half is None else round(avg + half, 3))


def brier(pairs):
    """pairs: [(predicted chance, won 0/1)]. Brier, the baseline's Brier and the skill score."""
    if not pairs:
        return dict(scored=0, brier=None, baseline=None, skill=None, base_rate=None)
    n = len(pairs)
    base = sum(y for _, y in pairs) / n
    b = sum((p - y) ** 2 for p, y in pairs) / n
    b0 = sum((base - y) ** 2 for _, y in pairs) / n
    return dict(scored=n, brier=round(b, 4), baseline=round(b0, 4), base_rate=round(base, 4),
                skill=round(1 - b / b0, 3) if b0 > 0 else None)


def reliability(pairs, width=0.1):
    """Predicted-chance buckets (0-10 %, 10-20 % ...) with the real win rate in each."""
    buckets = {}
    for p, y in pairs:
        k = min(int(p / width), int(1 / width) - 1)
        buckets.setdefault(k, []).append((p, y))
    return [dict(low=round(k * width, 2), high=round((k + 1) * width, 2), n=len(v),
                 predicted=round(sum(p for p, _ in v) / len(v), 3), actual=round(sum(y for _, y in v) / len(v), 3))
            for k, v in sorted(buckets.items())]


def grade_ranking(by_grade):
    """Do grades with enough trades line up A > B > C > X on average R?"""
    ready = [g for g in by_grade if g['closed'] >= MIN_GRADE]
    if len(ready) < 2:
        return dict(status='TOO_FEW', ready=[g['grade'] for g in ready],
                    text=f'Too few closed trades to judge: each grade needs {MIN_GRADE}.')
    wrong = [f"{a['grade']} ({a['avg_r']:+.2f}R) is not above {b['grade']} ({b['avg_r']:+.2f}R)"
             for a, b in zip(ready, ready[1:]) if a['avg_r'] <= b['avg_r']]
    names = ' > '.join(g['grade'] for g in ready)
    if wrong:
        return dict(status='OUT_OF_ORDER', ready=[g['grade'] for g in ready],
                    text='Grades are not separating outcomes: ' + '; '.join(wrong) + '.')
    return dict(status='IN_ORDER', ready=[g['grade'] for g in ready],
                text=f'Grades are separating winners from losers ({names} on average R).')


def summarise(rows):
    """rows: [(feed, r)] for closed, valid trades. The full scorecard for one set of trades."""
    by = {g: [] for g in GRADES}
    pairs = []
    for f, r in rows:
        if f.get('grade') in by:
            by[f['grade']].append(r)
        p = predicted(f)
        if p is not None:
            pairs.append((p, 1 if r > 0 else 0))
    by_grade = [dict(grade=g, name=GRADE_NAMES[g], **grade_stats(by[g])) for g in GRADES]
    return dict(closed=len(rows), by_grade=by_grade, order=grade_ranking(by_grade),
                brier=brier(pairs), reliability=reliability(pairs))


def split(rows, key, min_closed=1):
    """Per-group grade table (e.g. by regime or strategy), biggest groups first."""
    groups = {}
    for f, r in rows:
        groups.setdefault(key(f) or 'UNKNOWN', []).append((f, r))
    out = []
    for name, rs in groups.items():
        if len(rs) < min_closed:
            continue
        s = grade_stats([r for _, r in rs])
        grades = {}
        for f, r in rs:
            grades.setdefault(f.get('grade'), []).append(r)
        s['grades'] = {g: grade_stats(grades[g]) for g in GRADES if g in grades}
        out.append(dict(name=name, **s))
    return sorted(out, key=lambda g: -g['closed'])


def suggestions(card):
    """Plain test ideas from the scorecard. Suggestions only: no rule is ever changed automatically."""
    out = []
    g = {x['grade']: x for x in card['by_grade']}
    if g['A']['closed'] and g['A']['closed'] < MIN_GRADE:
        out.append(f"Only {g['A']['closed']} closed A trades so far: wait for {MIN_GRADE} before trusting the A grade.")
    for k in ('A', 'B'):
        x = g[k]
        if x['closed'] >= MIN_GRADE and x['high'] is not None and x['high'] < 0:
            out.append(f"{k} grades have averaged {x['avg_r']:+.2f}R over {x['closed']} trades, and even the top of the "
                       f"95 % range is below zero: TEST IDEA: tighten the {k} rule.")
    x = g['X']
    if x['closed'] >= MIN_GRADE and x['low'] is not None and x['low'] > 0:
        out.append(f"X grades have averaged {x['avg_r']:+.2f}R over {x['closed']} trades: the X rule may be marking "
                   'working agents as losing.')
    b = card['brier']
    if b['scored'] >= MIN_BRIER and b['skill'] is not None and b['skill'] <= 0:
        out.append('The records\' win rates predict no better than the overall win rate (Brier skill '
                   f"{b['skill']:+.2f}): TEST IDEA: grade on more than the track record, e.g. regime or spread.")
    for bucket in card['reliability']:
        if bucket['n'] >= 30 and abs(bucket['predicted'] - bucket['actual']) >= 0.15:
            out.append(f"Records showing {bucket['low'] * 100:.0f}-{bucket['high'] * 100:.0f} % win rates actually won "
                       f"{bucket['actual'] * 100:.0f} % of {bucket['n']} trades: past win rates are "
                       + ('too optimistic.' if bucket['predicted'] > bucket['actual'] else 'too pessimistic.'))
    return out


# --------------------------------------------------------------------------- data
def load_rows(store):
    """[(feed, r)] for every graded setup whose simulated trade closed with valid data."""
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS signal_feed(trade_id TEXT PRIMARY KEY, epoch REAL, agent_id TEXT, grade TEXT, payload TEXT)')
        feed = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM signal_feed').fetchall()]
        ids = [f['trade_id'] for f in feed]
        closed = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in db.execute(f"SELECT trade_id, payload FROM live_test_trades WHERE trade_id IN ({','.join('?' * len(chunk))})", chunk):
                closed[r['trade_id']] = json.loads(r['payload'])
    out = []
    for f in feed:
        t = closed.get(f['trade_id'])
        if t and t.get('data_quality_status') == VALID and t.get('pnl_r') is not None:
            out.append((f, float(t['pnl_r'])))
    return out


def scorecards(rows, now):
    cards = {}
    for key, label, span in WINDOWS:
        rs = rows if span is None else [(f, r) for f, r in rows if (f.get('entry_time') or 0) >= now - span]
        card = summarise(rs)
        card.update(key=key, label=label, suggestions=suggestions(card),
                    by_regime=split(rs, lambda f: (f.get('market') or {}).get('regime')),
                    by_strategy=split(rs, lambda f: f.get('label') or f.get('strategy_id'))[:12])
        cards[key] = card
    return cards


def _compact(card):
    return dict(closed=card['closed'], order=card['order']['status'], brier=card['brier'],
                by_grade=[{k: g[k] for k in ('grade', 'closed', 'win_rate', 'avg_r', 'net_r')} for g in card['by_grade']])


def snapshot(store, now=None, rows=None):
    """Save today's (Malaysia date) compact scorecard once. Returns True when one was written."""
    now = time.time() if now is None else now
    day = myt_day(now)
    with store.connect() as db:
        db.execute(SCHEMA)
        if db.execute('SELECT 1 FROM signal_calibration WHERE day=?', (day,)).fetchone():
            return False
    cards = scorecards(load_rows(store) if rows is None else rows, now)
    payload = dict(day=day, epoch=now, **{k: _compact(cards[k]) for k in ('7d', 'all')})
    with store.connect() as db:
        db.execute('INSERT OR IGNORE INTO signal_calibration VALUES (?,?,?)', (day, now, json.dumps(payload, allow_nan=False)))
        db.execute('DELETE FROM signal_calibration WHERE day NOT IN (SELECT day FROM signal_calibration ORDER BY day DESC LIMIT ?)', (KEEP_DAYS,))
    return True


class DailySnapshot:
    """Bot-loop helper: writes one snapshot per Malaysia day, cheap to call every cycle."""

    def __init__(self, store, clock=time.time):
        self.store, self.clock, self.done_day = store, clock, None

    def step(self):
        now = self.clock()
        day = myt_day(now)
        if day == self.done_day:
            return False
        wrote = snapshot(self.store, now)
        self.done_day = day
        return wrote


def history(store, limit=60):
    with store.connect() as db:
        db.execute(SCHEMA)
        rows = db.execute('SELECT payload FROM signal_calibration ORDER BY day DESC LIMIT ?', (limit,)).fetchall()
    return [json.loads(r['payload']) for r in rows]


def report(store, now=None):
    now = time.time() if now is None else now
    rows = load_rows(store)
    return dict(status='OK', server_epoch=now, simulation=True, windows=scorecards(rows, now),
                history=history(store), min_grade=MIN_GRADE, min_brier=MIN_BRIER, min_record=MIN_RECORD,
                note='Scores the Signal Agent\'s frozen grades against how the SIMULATED Live Test trades closed. '
                     'Report only: grade rules are never changed automatically and nothing is traded.')
