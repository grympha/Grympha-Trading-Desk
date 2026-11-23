"""Compact, self-rotating decision logs (decisions.jsonl, confluence_decisions.jsonl).

Each line keeps the full decision (condition, setup, entry plan, reason, quote, ...) but not
the candle arrays, which made up ~97% of every line and can be re-read from MT5 history.
When a log passes MAX_BYTES it is moved to logs/ and gzip-compressed; nothing is deleted.

Manual archive of an existing log (lossless, verified):
    py -3.11 decision_log.py --archive decisions.jsonl
"""
from __future__ import annotations
import gzip
import hashlib
import json
import logging
import shutil
import threading
import time
from pathlib import Path

HEAVY = ('chart', 'charts', 'review_market')
MAX_BYTES = 20 * 1024 * 1024
_lock = threading.Lock()


def compact(result: dict) -> dict:
    out = {k: v for k, v in result.items() if k not in HEAVY}
    chart = result.get('chart')
    if isinstance(chart, list) and chart and isinstance(chart[-1], dict) and 'time' in chart[-1]:
        out['chart_last_bar'] = chart[-1]['time']  # which closed candle the decision used
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def compress(path: Path) -> Path:
    """gzip `path` next to itself, verify the round trip, then remove the uncompressed copy."""
    target = path.with_name(path.name + '.gz')
    with open(path, 'rb') as src, gzip.open(target, 'wb', compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    if _sha256(path) != _sha256(target):
        target.unlink(missing_ok=True)
        raise IOError(f'Compressed copy of {path.name} did not verify; original kept')
    path.unlink()
    return target


def rotate(path: Path, now: float | None = None) -> Path | None:
    """Move a full log into logs/ and compress it in the background. Returns the moved path."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(now or time.time()))
    archive_dir = path.parent / 'logs'
    archive_dir.mkdir(exist_ok=True)
    moved = archive_dir / f'{path.stem}-{stamp}.jsonl'
    n = 1
    while moved.exists() or moved.with_name(moved.name + '.gz').exists():
        moved = archive_dir / f'{path.stem}-{stamp}-{n}.jsonl'; n += 1
    path.replace(moved)

    def work():
        try:
            compress(moved)
        except Exception:
            logging.warning('Log archive %s left uncompressed; it is complete and safe to keep', moved.name)
    threading.Thread(target=work, name='decision-log-gzip', daemon=True).start()
    return moved


def append(path: Path, result: dict, *, allow_nan: bool = True, max_bytes: int = MAX_BYTES) -> None:
    """Append one compact decision line; rotate first if the log is over the size limit."""
    line = json.dumps(compact(result), allow_nan=allow_nan) + '\n'
    with _lock:
        try:
            if path.exists() and path.stat().st_size >= max_bytes:
                rotate(path)
        except OSError:
            logging.warning('Decision log rotation skipped (file busy); will retry on the next write')
        with path.open('a') as out:
            out.write(line)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='Archive a decision log into logs/ as a verified .gz (lossless).')
    p.add_argument('--archive', required=True, help='log file to archive, e.g. decisions.jsonl')
    a = p.parse_args()
    src = Path(a.archive).resolve()
    before = src.stat()
    moved = src.parent / 'logs' / f'{src.stem}-archived-{time.strftime("%Y%m%d-%H%M%S")}.jsonl'
    moved.parent.mkdir(exist_ok=True)
    src.replace(moved)  # the bot reopens the log by name on every write, so new lines go to a fresh file
    if moved.stat().st_size != before.st_size:
        print('Note: the log grew while archiving; the archive contains every line written so far.')
    gz = compress(moved)
    print(f'Archived {before.st_size/1e6:.1f} MB -> {gz} ({gz.stat().st_size/1e6:.1f} MB), verified lossless.')
