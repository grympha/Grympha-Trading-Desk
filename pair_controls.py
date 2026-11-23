"""Persistent scan and DEMO entry controls keyed by strategy and broker symbol."""
import json
from threading import Lock

entry_lock = Lock()
STRATEGIES = ('BBMA', 'CONFLUENCE', 'ORB')


def disabled_pairs(store):
    if store is None:
        return {}
    with store.connect() as db:
        row = db.execute("SELECT value FROM meta WHERE key='disabled_pairs'").fetchone()
    return json.loads(row['value']) if row else {}


def enabled(disabled, strategy, symbol):
    return symbol not in disabled.get(strategy, ())


def save(store, settings):
    if not isinstance(settings, dict) or set(settings) != set(STRATEGIES):
        raise ValueError('Expected a list for every strategy')
    with entry_lock, store.connect() as db:
        row = db.execute("SELECT value FROM meta WHERE key='strategies'").fetchone()
        configured = json.loads(row['value']) if row else {}
        for strategy, symbols in settings.items():
            if not isinstance(symbols, list) or any(not isinstance(s, str) for s in symbols):
                raise ValueError('Expected a list of disabled pair names')
            if len(set(symbols)) != len(symbols) or not set(symbols) <= set(configured.get(strategy, {}).get('symbols', [])):
                raise ValueError('Invalid configured pair')
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('disabled_pairs', json.dumps(settings)))
