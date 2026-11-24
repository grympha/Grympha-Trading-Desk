"""CRT H1/M5 — Candle Range Theory (Turtle-Soup) Live Test agent, fully SIMULATED.

Reference range = the PREVIOUS closed H1 candle's high/low, frozen at the start of the
current H1. The current H1 sweeps one side of that range (a liquidity grab) and then
CLOSES strictly back INSIDE the range: a failed breakout.

  * current H1 sweeps the reference LOW and closes back inside -> BUY candidate,
  * current H1 sweeps the reference HIGH and closes back inside -> SELL candidate,
  * both sides swept in the same H1 -> SKIP (DOUBLE_SWEEP).

Entry: the first valid quote within `entry_window_seconds` (60 s) AFTER the H1 close,
while price is still inside the range and the target has not been touched since the
sweep. SL beyond the sweep extreme; TP at the OPPOSITE side of the reference range.
Time exit: 3 full H1 candles after the entry hour.

READ-ONLY: receives a ReadOnlyMarketData adapter; never calls any order API. It only
RETURNS a signal dict and appends NO_FILL / cancel / skip records to state['_emit'].
Entries are never created retrospectively: a setup only fires while `now` is within the
entry window of the H1 that just closed, so a reload mid-history cannot back-date a fill.
"""
from __future__ import annotations
import hashlib

from live_test import atr, config_id, dist

H1 = 'H1'
H1_DUR = 3600


class CrtH1M5:
    strategy_id = 'CRT_H1_M5'
    version = 'crt_h1_m5@1'
    needs_market = True

    DEFAULTS = dict(
        entry_timeframe='M5',            # entry cadence; the reference/sweep come from H1
        h1_lookback=60,
        pierce_ticks=1,                  # min penetration beyond the range edge (in points)
        sl_buffer_points=1, sl_buffer_atr=None,            # spec baseline: 1 tick beyond the extreme
        atr_period=14, min_sl_atr=0.05, max_sl_atr=10.0,
        min_sl_points=None, max_sl_points=None,
        min_rr=1.0,                      # reject if the opposite edge is nearer than 1R
        entry_window_seconds=60,
        time_exit_h1=3,                  # full H1 candles after the entry hour -> force exit
        max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    def label(self):
        return f"CRT H1/M5 ({self.timeframe})"

    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    def _signal_id(self, symbol, ref_time, digits):
        return hashlib.sha1(f"{symbol}:CRT:{int(ref_time)}".encode()).hexdigest()[:16]

    @staticmethod
    def _log_fields(pend, symbol, now):
        return dict(signal_id=pend['signal_id'], symbol=symbol, side=pend['side'], epoch=now,
                    setup='CRT', ref_high=pend['ref_high'], ref_low=pend['ref_low'],
                    sweep_time=pend['sweep_time'], sweep_extreme=pend['sweep_extreme'],
                    target_level=pend['target_level'])

    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        ref_mid = (quote.bid + quote.ask) / 2.0
        try:
            h1 = market.candles(symbol, H1, c['h1_lookback'], now=now) if market else []
        except Exception as exc:
            state['_feed_note'] = f'H1 data unavailable: {exc}'
            return None
        if len(h1) < 2:
            return None
        tick = max(c['pierce_ticks'], 0) * point

        pend = state.get('pending')

        # --------------------------- an armed setup: try to enter --------------
        if pend:
            side = pend['side']
            tp = pend['target_level']
            if now > pend['entry_deadline']:
                self._emit(state, 'NO_FILL', reason='ENTRY_WINDOW_EXPIRED',
                           **self._log_fields(pend, symbol, now))
                state['pending'] = None
                pend = None
            else:
                buy = side == 'BUY'
                target_touched = ref_mid >= tp if buy else ref_mid <= tp
                rebroke = ref_mid <= pend['ref_low'] if buy else ref_mid >= pend['ref_high']
                if target_touched:
                    self._emit(state, 'NO_FILL', reason='TARGET_BEFORE_ENTRY',
                               **self._log_fields(pend, symbol, now))
                    state['pending'] = None
                    return None
                if rebroke:
                    self._emit(state, 'CANCELLED', reason='RANGE_REBROKEN',
                               **self._log_fields(pend, symbol, now))
                    state['pending'] = None
                    return None
                return self._fill(pend, quote, candles, point, state, symbol, now)

        # --------------------------- detect a new setup at H1 close ------------
        hour_n, ref = h1[-1], h1[-2]
        close_n = hour_n.time + H1_DUR
        if state.get('last_h1_time') == hour_n.time:
            return None
        state['last_h1_time'] = hour_n.time
        if now - close_n > c['entry_window_seconds']:
            return None                                   # stale close -> no retrospective entry
        swept_low = hour_n.low <= ref.low - tick
        swept_high = hour_n.high >= ref.high + tick
        inside = ref.low < hour_n.close < ref.high
        if swept_low and swept_high:
            self._emit(state, 'SKIP', reason='DOUBLE_SWEEP', symbol=symbol, epoch=now,
                       ref_high=round(ref.high, quote.digits), ref_low=round(ref.low, quote.digits))
            return None
        if not (swept_low ^ swept_high) or not inside:
            return None
        side = 'BUY' if swept_low else 'SELL'
        extreme = hour_n.low if swept_low else hour_n.high
        target = ref.high if swept_low else ref.low
        state['pending'] = dict(
            side=side, signal_id=self._signal_id(symbol, ref.time, quote.digits),
            ref_high=round(ref.high, quote.digits), ref_low=round(ref.low, quote.digits),
            sweep_time=close_n, sweep_extreme=round(extreme, quote.digits),
            target_level=round(target, quote.digits),
            entry_deadline=close_n + c['entry_window_seconds'],
            entry_hour=int(now // H1_DUR))
        return None

    def _fill(self, pend, quote, candles, point, state, symbol, now):
        c = self.config
        side = pend['side']
        entry = quote.ask if side == 'BUY' else quote.bid
        a = atr(candles, c['atr_period'])
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or min_sl is None or max_sl is None:
            return None
        tp = pend['target_level']
        sl = pend['sweep_extreme'] - buf if side == 'BUY' else pend['sweep_extreme'] + buf
        risk = abs(entry - sl)
        if not (min_sl <= risk <= max_sl):
            self._emit(state, 'NO_FILL', reason='RISK_OUT_OF_BOUNDS', **self._log_fields(pend, symbol, now))
            state['pending'] = None
            return None
        ok = (sl < entry < tp) if side == 'BUY' else (tp < entry < sl)
        if not ok:
            self._emit(state, 'NO_FILL', reason='INVALID_LEVELS', **self._log_fields(pend, symbol, now))
            state['pending'] = None
            return None
        if risk > 0 and (abs(tp - entry) / risk) < c.get('min_rr', 1.0):
            self._emit(state, 'NO_FILL', reason='RR_TOO_LOW', **self._log_fields(pend, symbol, now))
            state['pending'] = None
            return None
        # Force exit 3 full H1 candles after the hour the entry sits in (explicit, replay-tested).
        entry_hour_end = (int(now // H1_DUR) + 1) * H1_DUR
        exit_deadline = entry_hour_end + c['time_exit_h1'] * H1_DUR
        extra = self._log_fields(pend, symbol, now)
        extra['fill_price'] = round(entry, quote.digits)
        extra['setup'] = 'CRT'
        extra['config_variant'] = self.timeframe
        state['pending'] = None
        return dict(side=side, signal_reason=f"CRT {side} fade of {'low' if side == 'BUY' else 'high'} sweep",
                    signal_time=pend['sweep_time'], trigger_price=(quote.bid + quote.ask) / 2.0,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=None, timeframe=self.timeframe, entry_candle_end=float(exit_deadline),
                    config_snapshot=dict(self.config), extra=extra)
