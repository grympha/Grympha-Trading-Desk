"""Automatic backup of the dashboard database (trade ledger, history, settings) and config files.

Uses SQLite's online backup API, which takes a consistent copy while the bot keeps running and writing.
Each backup goes to backups/<YYYY-MM-DD_HHMM>/ with dashboard.sqlite3 (integrity-checked before it is
kept), config.json and launcher_settings.json. Runs in a background thread so it never delays trading.
.env (Telegram token) is deliberately NOT copied. Defaults: every 24 h, keep the newest 14.
Override in config.json: "backup": {"enabled": true, "every_hours": 24, "keep": 14, "folder": "backups"}.

Restore: stop the bot, copy backups/<date>/dashboard.sqlite3 over dashboard.sqlite3
(and delete dashboard.sqlite3-wal / -shm if present), then start the bot.
"""
from __future__ import annotations
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

DEFAULTS = {'enabled': True, 'every_hours': 24, 'keep': 14, 'folder': 'backups'}
META_KEY = 'db_backup'
EXTRA_FILES = ('config.json', 'launcher_settings.json')
_running = threading.Lock()


def settings(cfg: dict | None) -> dict:
    s = {**DEFAULTS, **((cfg or {}).get('backup') or {})}
    s['every_hours'] = max(1.0, float(s['every_hours']))
    s['keep'] = max(1, min(365, int(s['keep'])))
    return s


def _folder(root: Path, s: dict) -> Path:
    f = Path(s['folder'])
    return f if f.is_absolute() else root / f


def list_backups(root: Path, s: dict) -> list[Path]:
    d = _folder(root, s)
    return sorted([p for p in d.iterdir() if p.is_dir() and (p / 'dashboard.sqlite3').exists()], key=lambda p: p.name) if d.exists() else []


def backup_now(db_path: Path, root: Path, s: dict, now: float | None = None) -> Path:
    """Create one verified backup and prune old ones. Returns the backup folder."""
    now = time.time() if now is None else now
    base = _folder(root, s) / time.strftime('%Y-%m-%d_%H%M%S', time.localtime(now))
    dest_dir, n = base, 1
    while dest_dir.exists():  # two backups in the same second never share a folder
        dest_dir = base.with_name(f'{base.name}-{n}'); n += 1
    dest_dir.mkdir(parents=True)
    tmp = dest_dir / 'dashboard.sqlite3.tmp'
    src = sqlite3.connect(str(db_path), timeout=30)
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)  # consistent snapshot even while the bot writes
    finally:
        dst.close(); src.close()
    check = sqlite3.connect(str(tmp))
    try:
        ok = check.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        tables = check.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    finally:
        check.close()
    if not ok or tables == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError('Backup copy failed its integrity check; previous backups are kept')
    os.replace(tmp, dest_dir / 'dashboard.sqlite3')
    for name in EXTRA_FILES:
        if (root / name).exists():
            shutil.copy2(root / name, dest_dir / name)
    for old in list_backups(root, s)[:-s['keep']]:  # only folders this module created (they contain dashboard.sqlite3)
        shutil.rmtree(old, ignore_errors=True)
    return dest_dir


def _meta(store, **change):
    try:
        with store.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (META_KEY,)).fetchone()
            st = json.loads(row['value']) if row else {}
            st.update(change)
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (META_KEY, json.dumps(st)))
    except Exception:
        pass


def last_epoch(store) -> float:
    try:
        with store.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (META_KEY,)).fetchone()
        return float((json.loads(row['value']) if row else {}).get('last_ok_epoch') or 0)
    except Exception:
        return 0.0


def start(store, root: Path, cfg: dict | None, force: bool = False) -> bool:
    """Start a background backup if due (or forced). Returns True if one was started."""
    s = settings(cfg)
    if not s['enabled'] and not force:
        return False
    if not force and time.time() - last_epoch(store) < s['every_hours'] * 3600:
        return False
    if not _running.acquire(blocking=False):
        return False

    def work():
        try:
            _meta(store, running=True, started_epoch=time.time())
            folder = backup_now(Path(store.path), Path(root), s)
            size = (folder / 'dashboard.sqlite3').stat().st_size
            _meta(store, running=False, last_ok_epoch=time.time(), last_folder=folder.name, last_size=size,
                  kept=len(list_backups(Path(root), s)), keep=s['keep'], every_hours=s['every_hours'], last_error=None,
                  folder=str(_folder(Path(root), s)))
            print(f'Database backup saved: {folder.name} ({size / 1e6:.1f} MB)', flush=True)
        except Exception as exc:
            logging.warning('Database backup failed: %s', exc)
            _meta(store, running=False, last_error=str(exc)[:200], last_error_epoch=time.time())
        finally:
            _running.release()
    threading.Thread(target=work, name='db-backup', daemon=True).start()
    return True
