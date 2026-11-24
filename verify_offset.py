"""Verify the broker timestamp offset for the Research Lab (and the live bot).

READ-ONLY. This script only reads quotes, history ticks and config. It never
imports the executor, never places or changes an order, and never writes anything.

The offset is how many hours the broker's raw timestamps are AHEAD of true UTC.
MT5 stamps ticks/bars in broker server time; the Trading Desk subtracts this
offset everywhere to convert back to UTC. RoboForex-Pro is UTC+2 in winter and
UTC+3 in summer (DST), so re-run this at each DST changeover.

Usage:
    python verify_offset.py            # check the default symbol (XAUUSD)
    python verify_offset.py EURUSD     # check a specific symbol

Enter the reported whole-number offset into:
  - Research Lab -> "Verified raw history timestamp offset (hours)"  (history feed)
  - config.json  -> tick_timestamp_offset_hours / bar_timestamp_offset_hours (live feed)
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time


def _load_config_offsets():
    try:
        with open('config.json', encoding='utf-8') as fh:
            cfg = json.load(fh)
    except Exception:
        return None
    return dict(
        tick=cfg.get('tick_timestamp_offset_hours'),
        bar=cfg.get('bar_timestamp_offset_hours'),
    )


def _fmt(epoch):
    return dt.datetime.utcfromtimestamp(epoch).strftime('%Y-%m-%d %H:%M:%S')


def _verdict(offset_hours):
    """Round to the nearest hour and flag a suspicious fractional part.
    Returns (rounded, clean, note): clean is False when the reading looks stale."""
    rounded = round(offset_hours)
    frac = abs(offset_hours - rounded)
    clean = frac <= 0.1
    note = ''
    if not clean:
        note = (f'  <-- fractional part {frac:.2f}h is large; the tick is STALE or '
                'the market is closed. Ignored in the verdict -- re-run when open.')
    return rounded, clean, note


def main(symbol='XAUUSD'):
    try:
        import MetaTrader5 as mt5
    except Exception:
        print('ERROR: MetaTrader5 package not available in this Python environment.')
        return 2

    if not mt5.initialize():
        print('ERROR: mt5.initialize() failed. Start the MT5 terminal / bot first.')
        print('       last_error:', mt5.last_error())
        return 2

    try:
        if not mt5.symbol_select(symbol, True):
            print(f'ERROR: cannot select symbol {symbol}. Check the exact broker name.')
            return 2

        print('=' * 68)
        print(f'Broker timestamp offset check  |  symbol: {symbol}')
        print('=' * 68)

        results = []

        # ---- 1) LIVE tick (symbol_info_tick) -------------------------------
        tick = mt5.symbol_info_tick(symbol)
        utc_now = time.time()
        if tick and tick.time:
            live_off = (tick.time - utc_now) / 3600.0
            rounded, clean, note = _verdict(live_off)
            if clean:
                results.append(('live', rounded))
            print('\n[LIVE feed  symbol_info_tick]')
            print(f'  raw broker epoch : {int(tick.time)}  ({_fmt(tick.time)} as UTC clock)')
            print(f'  true UTC now     : {int(utc_now)}  ({_fmt(utc_now)})')
            print(f'  offset (hours)   : {live_off:+.2f}  -> use {rounded}{note}')
        else:
            print('\n[LIVE feed] no tick available (market closed?).')

        # ---- 2) HISTORY ticks (copy_ticks_from) ----------------------------
        # This is the feed the Research Lab actually replays.
        try:
            frm = dt.datetime.utcnow() - dt.timedelta(minutes=10)
            ticks = mt5.copy_ticks_from(symbol, frm, 50, mt5.COPY_TICKS_ALL)
        except Exception as exc:
            ticks = None
            print('\n[HISTORY feed] copy_ticks_from raised:', exc)

        if ticks is not None and len(ticks):
            last = ticks[-1]
            # time_msc is milliseconds; fall back to seconds-based time
            raw = float(last['time_msc']) / 1000.0 if last['time_msc'] else float(last['time'])
            utc_now = time.time()
            hist_off = (raw - utc_now) / 3600.0
            rounded, clean, note = _verdict(hist_off)
            if clean:
                results.append(('history', rounded))
            print('\n[HISTORY feed  copy_ticks_from]  <-- Research Lab uses this')
            print(f'  raw broker epoch : {raw:.3f}  ({_fmt(raw)} as UTC clock)')
            print(f'  true UTC now     : {int(utc_now)}  ({_fmt(utc_now)})')
            print(f'  offset (hours)   : {hist_off:+.2f}  -> use {rounded}{note}')
        else:
            print('\n[HISTORY feed] no history ticks returned (market closed, or '
                  'history not loaded for this symbol).')

        # ---- 3) compare with config ----------------------------------------
        cfg = _load_config_offsets()
        if cfg:
            print('\n[config.json current values]')
            print(f'  tick_timestamp_offset_hours : {cfg["tick"]}')
            print(f'  bar_timestamp_offset_hours  : {cfg["bar"]}')

        # ---- 4) verdict -----------------------------------------------------
        print('\n' + '-' * 68)
        measured = {r for _, r in results}
        if not measured:
            print('VERDICT: no CLEAN reading (feeds stale or market closed). '
                  'Re-run while the market is open.')
        elif len(measured) == 1:
            off = measured.pop()
            print(f'VERDICT: enter  {off}  in the Research Lab verified-offset field.')
            if cfg and cfg.get('tick') not in (off, None):
                print(f'         NOTE: config tick offset is {cfg["tick"]}, measured is {off}. '
                      'A DST change may have occurred -- update config.json too.')
        else:
            print(f'VERDICT: live and history offsets DISAGREE {sorted(measured)}. '
                  'Prefer the HISTORY value for the Research Lab; investigate the difference.')
        print('-' * 68)
        return 0
    finally:
        mt5.shutdown()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'XAUUSD'))
