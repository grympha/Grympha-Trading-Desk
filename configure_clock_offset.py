"""Measure the broker's clock offset from LIVE quotes and save it to config.json.

MT5 stamps ticks and bars in broker server time. The Trading Desk subtracts
tick_timestamp_offset_hours / bar_timestamp_offset_hours to get back to UTC.
A new installation has no offset (0), so with a UTC+2 broker every quote looks
two hours in the future: all quote rows FAIL and DEMO/REAL execution cannot start.

Measured, not guessed: a symbol counts only if its quote MOVES while we watch
(so a stale or closed-market price is never used), its offset must be a whole
hour (within 6 minutes), and at least two live symbols must agree. Read-only
towards MT5: no order is sent. config.json is backed up before it is changed.
Re-run after a DST change (RoboForex-Pro: UTC+2 in winter, UTC+3 in summer)."""
from __future__ import annotations
import json
import os
import shutil
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KEYS = ('tick_timestamp_offset_hours', 'bar_timestamp_offset_hours')
MIN_AGREE = 2


class OffsetError(RuntimeError):
    pass


def measure(mt5, symbols, watch_seconds=20, clock=time.time, sleep=time.sleep):
    """Return (offset_hours, {symbol: raw_offset_hours}) from symbols whose quote advanced."""
    first, live = {}, {}
    for s in symbols:
        if mt5.symbol_select(s, True):
            t = mt5.symbol_info_tick(s)
            if t and getattr(t, 'time', 0) > 0:
                first[s] = t.time_msc if getattr(t, 'time_msc', 0) else t.time * 1000
    deadline = clock() + watch_seconds
    while first.keys() - live.keys() and clock() < deadline:
        sleep(1)
        for s in first.keys() - live.keys():
            t = mt5.symbol_info_tick(s)
            msc = (t.time_msc if getattr(t, 'time_msc', 0) else t.time * 1000) if t else 0
            if msc > first[s]:
                live[s] = (msc / 1000 - clock()) / 3600       # broker time minus true UTC, in hours
        if len(live) >= max(MIN_AGREE, 3):
            break
    if len(live) < MIN_AGREE:
        raise OffsetError(f'Only {len(live)} symbol(s) had moving quotes in {watch_seconds}s. '
                          'Run this while the market is open and MT5 is connected (crypto trades at weekends).')
    whole = {s: round(h) for s, h in live.items() if abs(h - round(h)) <= 0.1 and -12 <= round(h) <= 14}
    if len(whole) < MIN_AGREE:
        raise OffsetError('Quote times are not a whole number of hours from UTC. Check that Windows time is '
                          'synchronised (Settings > Time & language > Sync now), then try again.')
    offset, votes = Counter(whole.values()).most_common(1)[0]
    if votes < MIN_AGREE or votes < len(whole):
        raise OffsetError(f'Symbols disagree about the broker clock: {whole}. Nothing was changed.')
    return offset, live


def save(path: Path, offset: int):
    """Write both offsets; returns the backup file name, or None if nothing changed."""
    cfg = json.loads(path.read_text(encoding='utf-8-sig'))
    if all(cfg.get(k) == offset for k in KEYS):
        return None
    backup = path.with_name('config.before_clock_offset.' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.json')
    shutil.copy2(path, backup)
    for k in KEYS:
        cfg[k] = offset
    tmp = path.with_suffix('.offset.tmp')
    tmp.write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)
    return backup.name


def main():
    path = ROOT / 'config.json'
    if not path.exists():
        raise SystemExit('config.json missing. Click Setup / Install requirements first.')
    cfg = json.loads(path.read_text(encoding='utf-8-sig'))
    import MetaTrader5 as mt5
    kw = {'path': cfg['terminal_path']} if cfg.get('terminal_path') else {}
    if not mt5.initialize(**kw):
        raise SystemExit('Cannot connect to MT5. Open and log in to the selected terminal, then try again.')
    try:
        symbols = list(dict.fromkeys(cfg.get('symbols', []) + cfg.get('confluence', {}).get('symbols', [])))
        print(f'Watching {len(symbols)} symbols for moving quotes (up to 20 s)...', flush=True)
        try:
            offset, live = measure(mt5, symbols)
        except OffsetError as exc:
            raise SystemExit('Clock offset NOT changed: ' + str(exc))
    finally:
        mt5.shutdown()
    print('Live quotes: ' + ', '.join(f'{s} {h:+.2f} h' for s, h in sorted(live.items())))
    old = [cfg.get(k) for k in KEYS]
    backup = save(path, offset)
    if backup is None:
        print(f'Broker clock is UTC{offset:+d}. Offsets already {offset}; nothing changed.')
    else:
        print(f'Broker clock is UTC{offset:+d}. Saved tick/bar offset {offset} h (was {old[0]}/{old[1]}). Backup: {backup}')
        print('Click Check requirements. Restart the bot if it is running so it uses the new offset.')
    print('No order sent. Re-run after a daylight-saving change.')


if __name__ == '__main__':
    sys.stdout.reconfigure(errors='replace')
    main()
