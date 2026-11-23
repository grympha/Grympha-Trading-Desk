"""Equity curve history: read-only account balance/equity samples + closed-trade P/L.

Recorded by the dashboard journal once per bot cycle (stored at most once per minute).
Never sends orders and stores no login number: accounts are keyed by the same
SHA-256 identity hash the executor uses (login|server).
"""
from __future__ import annotations
import json
import math
import time

SCHEMA = '''
CREATE TABLE IF NOT EXISTS equity_history (
 account_scope TEXT NOT NULL, epoch INTEGER NOT NULL, balance REAL NOT NULL, equity REAL NOT NULL,
 currency TEXT, account_type TEXT, PRIMARY KEY(account_scope, epoch));
'''
BUCKET_SECONDS = 60
KEEP_SECONDS = 400 * 86400
RANGES = {'1D': 86400, '1W': 7 * 86400, '1M': 31 * 86400, '3M': 92 * 86400, 'ALL': None}
MAX_POINTS = 600


def scope_of(mt5):
    try:
        from demo_execution import identity
        account = mt5.account_info()
        return identity(account) if account is not None else None
    except Exception:
        return None


def ensure(db):
    db.executescript(SCHEMA)


def record(store, mt5, account, now=None):
    """Store one point per minute from a HEALTHY account sample. Failures never affect trading."""
    now = time.time() if now is None else now
    if not account or account.get('status') != 'HEALTHY':
        return False
    balance, equity = account.get('balance'), account.get('equity')
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (balance, equity)):
        return False
    scope = scope_of(mt5)
    if not scope:
        return False
    bucket = int(now // BUCKET_SECONDS * BUCKET_SECONDS)
    with store.connect() as db:
        ensure(db)
        db.execute('INSERT OR REPLACE INTO equity_history VALUES (?,?,?,?,?,?)',
                   (scope, bucket, float(balance), float(equity), account.get('currency'), account.get('account_type')))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('equity_scope',?)", (json.dumps(scope),))
        if bucket % 3600 == 0:  # hourly housekeeping
            db.execute('DELETE FROM equity_history WHERE epoch<?', (bucket - KEEP_SECONDS,))
    return True


def _downsample(rows, limit=MAX_POINTS):
    if len(rows) <= limit:
        return rows
    first, last = rows[0][0], rows[-1][0]
    width = max(1, (last - first) / limit)
    out, current = [], None
    for row in rows:
        b = int((row[0] - first) // width)
        if b != current:
            out.append(row); current = b
        else:
            out[-1] = row  # keep the last value in each bucket
    return out


def series(store, range_key='1W', now=None):
    now = time.time() if now is None else now
    if range_key not in RANGES:
        raise ValueError('Unknown range')
    span = RANGES[range_key]
    start = now - span if span else 0
    with store.connect() as db:
        ensure(db)
        row = db.execute("SELECT value FROM meta WHERE key='equity_scope'").fetchone()
        scope = json.loads(row['value']) if row else None
        review = db.execute("SELECT value FROM meta WHERE key='trade_review_scope'").fetchone()
        review_scope = json.loads(review['value']) if review else None
        points = [tuple(r) for r in db.execute(
            'SELECT epoch,equity,balance FROM equity_history WHERE account_scope=? AND epoch>=? ORDER BY epoch',
            (scope, start)).fetchall()] if scope else []
        meta = db.execute('SELECT currency,account_type FROM equity_history WHERE account_scope=? ORDER BY epoch DESC LIMIT 1',
                          (scope,)).fetchone() if scope else None
        first = db.execute('SELECT MIN(epoch) FROM equity_history WHERE account_scope=?', (scope,)).fetchone()[0] if scope else None
        has_reviews = db.execute("SELECT 1 FROM sqlite_master WHERE name='trade_reviews'").fetchone()
        trades = []
        if has_reviews and review_scope:
            for r in db.execute('SELECT closed_epoch,payload FROM trade_reviews WHERE account_scope=? ORDER BY closed_epoch',
                                (review_scope,)).fetchall():
                p = json.loads(r['payload'])
                v = p.get('realised')
                if isinstance(v, (int, float)) and math.isfinite(v) and r['closed_epoch']:
                    trades.append((int(r['closed_epoch']), float(v), p.get('currency'), p.get('strategy'), p.get('symbol')))
    # cumulative closed P/L: running total starts at 0 at the beginning of the range
    closed, total, currency = [], 0.0, None
    before = sum(v for t, v, *_ in trades if t < start)
    for t, v, c, strategy, symbol in trades:
        if t < start:
            continue
        total += v; currency = currency or c
        closed.append([t, round(total, 2), round(v, 2), strategy, symbol])
    return dict(status='READY', range=range_key, updated_epoch=now, start_epoch=start or (points[0][0] if points else None),
                currency=(meta['currency'] if meta else None) or currency, account_type=meta['account_type'] if meta else None,
                recording_since=first, points=[list(p) for p in _downsample(points)],
                closed=closed[-2000:], closed_before_range=round(before, 2), same_account=(scope == review_scope) if scope and review_scope else None)
