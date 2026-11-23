"""ORB agent: Opening-Range Breakout + retest (third strategy), built on the unchanged orb_core.py.

orb_core.py is the original Grympha ORB Scanner v1 engine (read-only, deterministic). This adapter:
  * reads MT5 M5/H4 bars and the quote, converting broker server time to UTC,
  * runs orb_core.scan(), keeps ONE emitted signal per symbol per session day (persisted),
  * returns a result in this bot's strategy format (condition -> setup -> entry_plan) so the shared
    journal, tracking, executor, monitor and dashboard can handle ORB like BBMA and Confluence.
News is enforced by the bot's MT5-calendar filter at execution, exactly as for BBMA and Confluence.
Defaults differ from the standalone package in one place: the range starts 01:00 server time because
RoboForex XAUUSD trades from 01:00 (00:00-06:00 would never be complete there).
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import orb_core
from market_hours import check_stale

STRATEGY = 'ORB'
DEFAULTS = {
    'enabled': True, 'symbols': ['XAUUSD'], 'symbol_base': 'XAUUSD', 'symbol': 'XAUUSD',
    'session_utc_offset_hours': None,  # None -> use bar_timestamp_offset_hours from config.json
    'range_start_hour': 1, 'range_end_hour': 6, 'trade_start_hour': 7, 'trade_end_hour': 17,
    'skip_weekdays': [2, 3], 'skip_months': [12],
    'ema_period': 200, 'h4_ema_period': 20, 'atr_period': 14,
    'dynamic_buffer_pct': 3.0, 'retest_tolerance_points': 80, 'max_retest_penetration_fraction': 0.25,
    'max_bars_after_breakout': 30, 'min_range_points': 1000, 'max_range_points': 7700,
    'buy_sl_buffer_points': 60, 'sell_sl_buffer_points': 100, 'sl_multiplier': 0.9,
    'max_spread_points': 80, 'max_entry_drift_atr': 0.5, 'max_signal_age_seconds': 60,
    'max_tick_age_seconds': 30, 'poll_seconds': 15,
    'news_mode': 'off',  # the bot's MT5 calendar filter is applied at execution instead
    'news_file': '', 'news_before_minutes': 60, 'news_after_minutes': 15, 'news_max_age_hours': 24,
}
STATUS_TEXT = {
    'SKIPPED_SESSION': 'No ORB today (Wednesday/Thursday or December are skipped by the strategy).',
    'BUILDING_RANGE': 'Building today\'s opening range.',
    'OUTSIDE_TRADE_WINDOW': 'Outside the ORB trading window.',
    'INCOMPLETE_RANGE': 'Opening range incomplete (missing M5 candles); no ORB signal today.',
    'INVALID_SYMBOL_SPEC': 'Broker symbol specification invalid.',
    'RANGE_FILTERED': 'Opening range too small or too large for ORB.',
    'INDICATOR_WARMUP_INCOMPLETE': 'Not enough M5/H4 history for EMA200/EMA20; load more history in MT5.',
    'SESSION_DATA_GAP': 'Gap in today\'s M5 data; ORB paused for safety.',
    'WAIT_BREAKOUT': 'Waiting for a breakout close beyond the range.',
    'WAIT_RETEST': 'Breakout seen; waiting for a retest that holds.',
    'STALE_SIGNAL_SUPPRESSED': 'Retest signal too old; not traded.',
    'INVALID_QUOTE': 'Quote invalid.', 'STALE_OR_FUTURE_QUOTE': 'Quote stale or timestamped ahead of the PC clock.',
    'SPREAD_BLOCKED': 'Spread above the ORB limit.', 'LEVEL_LOST_BEFORE_EMISSION': 'Price fell back inside the range before entry.',
    'ENTRY_DRIFT_BLOCKED': 'Price moved too far from the retest close.', 'INVALID_STOP': 'Stop-loss geometry invalid.',
    'SIGNAL_CANDIDATE': 'ORB breakout-retest entry qualified.',
    'DAILY_SIGNAL_LIMIT': 'Today\'s ORB signal limit is reached.',
    'ALREADY_ISSUED': 'This ORB signal was already issued.',
}


def settings(cfg: dict) -> dict:
    c = dict(DEFAULTS)
    c.update(cfg.get('orb') or {})
    if c['session_utc_offset_hours'] is None:
        c['session_utc_offset_hours'] = cfg.get('bar_timestamp_offset_hours', 0)
    c['symbols'] = list(c.get('symbols') or ['XAUUSD'])
    for sym in c['symbols']:
        orb_core.validate_config(dict(c, symbol=sym))
    return c


def stamp(epoch) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat()


def h4_trend(h4, period):
    closed = [b.close for b in h4]
    ema = orb_core.ema(closed, period)
    if not closed or ema[-1] is None:
        return None, None
    return ('BUY' if closed[-1] > ema[-1] else 'SELL'), ema[-1]


def chart_bars(bars, count=80):
    return [dict(time=b.time, open=b.open, high=b.high, low=b.low, close=b.close, mid=None, upper=None, lower=None)
            for b in bars[-count:]]


def tick_round(value, tick, up):
    return (math.ceil(value / tick) if up else math.floor(value / tick)) * tick


class ORBRunner:
    """One instance per bot. State file keeps the one-signal-per-session limit across restarts."""

    def __init__(self, cfg, state_path):
        self.cfg, self.c = cfg, settings(cfg)
        self.path = Path(state_path)
        try:
            self.state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.state = {}
        if not isinstance(self.state, dict):
            self.state = {}
        self.state.setdefault('emitted', {})
        self.signals_per_day, self.tp_mode, self.fixed_rr = 1, 'dynamic', 2.0

    def apply(self, o):
        """Dashboard ORB settings (strategy_settings['orb']); read every bot cycle."""
        self.c.update(skip_weekdays=list(o['skip_weekdays']), skip_months=list(o['skip_months']),
                      sl_multiplier=float(o['sl_multiplier']), buy_sl_buffer_points=int(o['buy_sl_buffer_points']),
                      sell_sl_buffer_points=int(o['sell_sl_buffer_points']))
        self.signals_per_day, self.tp_mode, self.fixed_rr = int(o['signals_per_day']), o['tp_mode'], float(o['fixed_rr'])

    def _save(self):
        tmp = self.path.with_suffix('.tmp'); tmp.write_text(json.dumps(self.state)); tmp.replace(self.path)

    def bars(self, mt5, symbol, tf, count, offset):
        rows = mt5.copy_rates_from_pos(symbol, getattr(mt5, 'TIMEFRAME_' + tf), 0, count)
        if rows is None or len(rows) == 0:
            raise ValueError(f'ORB needs {tf} history; open the chart in MT5')
        return [orb_core.Bar(int(r['time']) - offset, float(r['open']), float(r['high']), float(r['low']), float(r['close'])) for r in rows]

    def check(self, mt5, symbol, now):
        c = dict(self.c, symbol=symbol)
        if not mt5.terminal_info() or not mt5.terminal_info().connected:
            raise ValueError('MT5 disconnected')
        if not mt5.symbol_select(symbol, True):
            raise ValueError('ORB symbol unavailable')
        tick, info = mt5.symbol_info_tick(symbol), mt5.symbol_info(symbol)
        if tick is None or info is None:
            raise ValueError('ORB quote unavailable')
        tick_off = float(self.cfg.get('tick_timestamp_offset_hours', 0)) * 3600
        bar_off = int(float(self.cfg.get('bar_timestamp_offset_hours', 0)) * 3600)
        age = now - (tick.time - tick_off)
        if age >= -5:
            check_stale(age, c['max_tick_age_seconds'], now, symbol)  # weekend / rollover -> MarketClosed
        m5 = self.bars(mt5, symbol, 'M5', 2000, bar_off)
        h4 = self.bars(mt5, symbol, 'H4', 400, bar_off)
        quote = {'bid': float(tick.bid), 'ask': float(tick.ask), 'time': float(tick.time - tick_off)}
        spec = {'point': float(info.point), 'tick_size': float(getattr(info, 'trade_tick_size', 0) or info.point), 'digits': int(info.digits)}
        r = orb_core.scan(c, m5, h4, now, quote, spec, None)
        closed_h4 = [b for b in h4 if b.time + 14400 <= now]
        side, ema20 = h4_trend(closed_h4, c['h4_ema_period'])
        status, sig = r['status'], r.get('signal')
        session = r.get('session') or (sig or {}).get('session_date')
        plan = None
        if sig:
            day = self.state['emitted'].get(symbol)
            if not isinstance(day, dict) or day.get('session') != session:  # also upgrades the old 'session' string
                day = {'session': session, 'ids': []}
                if self.state['emitted'].get(symbol) == session:
                    day['ids'] = ['(earlier signal)']
            if sig['signal_id'] in day['ids']:
                status, sig = 'ALREADY_ISSUED', None
            elif len(day['ids']) >= self.signals_per_day:
                status, sig = 'DAILY_SIGNAL_LIMIT', None
            else:
                plan = self.plan(sig, spec)
                day['ids'].append(sig['signal_id'])
                self.state['emitted'][symbol] = day
                self._save()
        closed_m5 = [b for b in m5 if b.time + 300 <= now]
        result = dict(
            strategy=STRATEGY, symbol=symbol, health='OK', checked_utc=stamp(now), analysis_utc=stamp(now),
            chart_timeframe='M5', orb_status=status, session=session,
            condition=dict(name='BULLISH' if side == 'BUY' else 'BEARISH' if side == 'SELL' else 'WAITING', side=side,
                           h4_ema20=ema20),
            setup=(dict(type='ORB_RETEST', side=sig['direction'], range_high=sig['range_high'], range_low=sig['range_low'],
                        high=sig['range_high'], low=sig['range_low'], breakout_utc=sig['breakout_bar_utc'],
                        retest_utc=sig['retest_bar_utc']) if sig else None),
            entry_plan=plan,
            range=dict(high=r.get('range_high', sig and sig['range_high']), low=r.get('range_low', sig and sig['range_low']),
                       points=r.get('range_points', sig and sig['range_points'])),
            pending={k: bool(v) for k, v in (r.get('pending') or {}).items()},
            reason=STATUS_TEXT.get(status, status.replace('_', ' ').capitalize()) + (f" Range {r['range_points']:.0f} pts." if r.get('range_points') else ''),
            quote=dict(bid=float(tick.bid), ask=float(tick.ask), spread=float(tick.ask - tick.bid),
                       spread_points=float((tick.ask - tick.bid) / info.point), digits=int(info.digits), age_seconds=float(age)),
            chart=chart_bars(closed_m5),
            charts={'H4': chart_bars(closed_h4), 'M5': chart_bars(closed_m5)},
            new_analysis=True)
        return result

    def plan(self, sig, spec):
        """ORB candidate -> this bot's entry plan (market entry at the executable quote, like Confluence)."""
        buy = sig['direction'] == 'BUY'
        entry, sl, tp = float(sig['entry_reference']), float(sig['sl_suggested']), float(sig['tp_suggested'])
        risk = abs(entry - sl)
        tick = spec['tick_size']
        rr = sig['rr_after_rounding']
        if self.tp_mode == 'fixed':  # replace ORB's dynamic 1.5-3R target with a fixed multiple of the risk
            tp = round(tick_round(entry + risk * self.fixed_rr if buy else entry - risk * self.fixed_rr, tick, not buy), spec['digits'])
            rr = abs(tp - entry) / risk
        # TP1 at 1R gives break-even / partial-close management a level; ORB's own target is TP2.
        tp1 = round(tick_round(entry + risk if buy else entry - risk, tick, not buy), spec['digits'])
        return dict(id=sig['signal_id'][:16], side=sig['direction'], order_idea='QUOTE_REFERENCE', signal_kind='REFERENCE',
                    setup='ORB_RETEST', entry=entry, stop_loss=sl, tp1=tp1, tp2=tp, rr1=1.0, rr2=rr, tp_mode=self.tp_mode,
                    confirmation_utc=sig['signal_close_utc'], expires_utc=sig['expires_at_utc'],
                    range_high=sig['range_high'], range_low=sig['range_low'], range_points=sig['range_points'],
                    orb_signal_id=sig['signal_id'], orb_config_hash=sig['config_hash'],
                    management_proposal=sig['management_proposal'])
