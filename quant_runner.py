"""Start a Quant Agent backtest from the dashboard and report its progress. READ-ONLY, simulated.

The backtest runs as a separate `quant_agent.py --registered` process (it opens and closes its own
MT5 connection, which must not touch the bot's). One run at a time. The process only reads MT5
history and writes reports plus the `quant_results` table; it never imports the executor.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / 'reports' / 'quant_dashboard_run.log'
DAY_CHOICES = (7, 14, 30, 60, 90, 180)
META_KEY = 'quant_dashboard_job'

_lock = threading.Lock()
_proc = None            # the running subprocess.Popen, if this dashboard process started it


_LABELS = {}


def agent_label(strategy_id, config_json):
    """The agent's own label (e.g. 'S&R Zones · Bounce · regime filter'): what a 'variant' is."""
    key = (strategy_id, config_json or '{}')
    if key not in _LABELS:
        if len(_LABELS) > 5000:
            _LABELS.clear()
        try:
            from live_test import build_agent
            agent = build_agent(strategy_id, json.loads(config_json or '{}'))
            _LABELS[key] = agent.label() if agent is not None else strategy_id
        except Exception:
            _LABELS[key] = strategy_id
    return _LABELS[key]


def options(store):
    """Strategies registered in Live Test (not removed) with their pairs and variants, for the run form.
    A variant is one agent label (setup / mode / filter); it may run on many pairs."""
    with store.connect() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = db.execute("SELECT strategy_id, symbol, config FROM live_test_agents WHERE status != 'REMOVED'").fetchall() \
            if 'live_test_agents' in tables else []
    by = {}
    for r in rows:
        s = by.setdefault(r['strategy_id'], dict(id=r['strategy_id'], agents=0, symbols=set(), variants={}))
        s['agents'] += 1
        s['symbols'].add(r['symbol'])
        label = agent_label(r['strategy_id'], r['config'])
        v = s['variants'].setdefault(label, dict(label=label, agents=0, symbols=set()))
        v['agents'] += 1
        v['symbols'].add(r['symbol'])
    try:
        from home_view import short_name
    except Exception:
        def short_name(x):
            return x
    return [dict(s, name=short_name(s['id']), symbols=sorted(s['symbols']),
                 variants=[dict(v, symbols=sorted(v['symbols'])) for v in sorted(s['variants'].values(), key=lambda v: v['label'])])
            for s in sorted(by.values(), key=lambda s: s['id'])]


def _save(store, job):
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (META_KEY, json.dumps(job)))


def _load(store):
    with store.connect() as db:
        row = db.execute('SELECT value FROM meta WHERE key=?', (META_KEY,)).fetchone()
    return json.loads(row[0]) if row else None


def _tail(n=12):
    try:
        lines = LOG.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    return lines[-n:]


def _progress(lines):
    done = total = None
    for line in lines:
        m = re.match(r'PROGRESS (\d+)/(\d+)$', line.strip())
        if m:
            done, total = int(m.group(1)), int(m.group(2))
    return done, total


def _command(strategy, symbol, days, variant=None):
    """Argument list (no shell); values are already validated against the registered agents."""
    return [sys.executable, '-u', str(ROOT / 'quant_agent.py'), '--registered', '--strategies', strategy,
            '--days', str(days)] + ([] if symbol == 'ALL' else ['--symbols', symbol]) \
        + (['--variant', variant] if variant else [])


def start(store, strategy, symbol, days, now=None, variant=None):
    """Validate the request against the registered agents and start the backtest process.
    variant: one agent label of that strategy (None or 'ALL' = every variant)."""
    global _proc
    now = time.time() if now is None else now
    opts = {o['id']: o for o in options(store)}
    if strategy not in opts:
        raise ValueError('Unknown strategy')
    variant = None if variant in (None, '', 'ALL') else variant
    pool = opts[strategy]['symbols']
    if variant is not None:
        v = next((x for x in opts[strategy]['variants'] if x['label'] == variant), None)
        if v is None:
            raise ValueError('Unknown variant for this strategy')
        pool = v['symbols']
    if symbol != 'ALL' and symbol not in pool:
        raise ValueError('That pair has no agent for this strategy')
    if days not in DAY_CHOICES:
        raise ValueError('Choose 7, 14, 30, 60, 90 or 180 days')
    with _lock:
        if _proc is not None and _proc.poll() is None:
            raise RuntimeError('A backtest is already running')
        LOG.parent.mkdir(exist_ok=True)
        cmd = _command(strategy, symbol, days, variant)
        log = open(LOG, 'w', encoding='utf-8')
        try:
            _proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0,
                                     env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        finally:
            log.close()
        job = dict(state='RUNNING', strategy=strategy, name=opts[strategy]['name'], variant=variant, symbol=symbol,
                   days=days, started=now, finished=None, pid=_proc.pid)
        _save(store, job)
        return job


def stop(store):
    global _proc
    with _lock:
        if _proc is None or _proc.poll() is not None:
            raise RuntimeError('No backtest is running')
        _proc.terminate()
        try:
            _proc.wait(10)
        except subprocess.TimeoutExpired:
            _proc.kill()
        job = _load(store) or {}
        job.update(state='STOPPED', finished=time.time())
        _save(store, job)
        _proc = None
        return job


def status(store, now=None):
    """The latest run (running or finished) with progress and the last log lines."""
    global _proc
    now = time.time() if now is None else now
    with _lock:
        job = _load(store)
        if job is None:
            return None
        if job.get('state') == 'RUNNING':
            if _proc is not None and _proc.pid == job.get('pid'):
                rc = _proc.poll()
                if rc is not None:
                    lines = _tail(40)
                    saved = next((int(m.group(1)) for m in (re.search(r'Saved (\d+) results', x) for x in lines) if m), None)
                    job.update(state='DONE' if rc == 0 else 'FAILED', finished=now, exit_code=rc, saved=saved)
                    _save(store, job)
                    _proc = None
            else:
                # Started by an earlier dashboard process (bot restarted): it cannot be followed any more.
                job.update(state='INTERRUPTED', finished=now)
                _save(store, job)
    lines = _tail()
    done, total = _progress(_tail(400))
    shown = [x for x in lines if not x.startswith('PROGRESS ')]
    return dict(job, progress_done=done, progress_total=total, elapsed=round((job.get('finished') or now) - job['started']),
                log=shown[-8:])
