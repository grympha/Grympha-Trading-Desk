"""Momentum M5/M1 — EMA-pullback momentum breakout Live Test agent, fully SIMULATED.

Ported from the standalone Grympha Momentum Desk (paper bot) so its setups can be
forward-tested side by side in the Live Test tab. Decision rules are reproduced exactly;
money management (lots, commission, broker P/L) is NOT — Live Test measures R, so the
shared Simulator fills at Ask/Bid and scores from fill -> initial SL.

Rules (XAUUSD-tuned):
  * Bias from M5: EMA20 > EMA50 -> BUY, else SELL; |EMA20-EMA50| >= min_trend_separation_atr * ATR(M5).
  * Setup on the last CLOSED M1 bar: it touches the PRIOR M1 EMA20, closes on the trend side of
    the CURRENT M1 EMA20, and has a directional body >= min_body_atr * ATR(M1).
  * A pending STOP entry is armed: BUY trigger = signal high + buffer + spread; SELL trigger =
    signal low - buffer (buffer = entry_buffer_atr * ATR(M1)). SL = the far side of
    [signal extreme -/+ buffer] and [trigger -/+ stop_atr * ATR], whichever is wider. NO fixed TP.
  * Vetoes at creation: spread > max_spread_atr*ATR, already beyond the trigger, SL wider than
    max_stop_atr*ATR. While pending: expiry (pending_expiry_seconds), trend flip / bias lost,
    stop touched before fill, spread blow-out. Fill when price reaches the trigger; a gap more than
    max_fill_gap_atr*ATR past the trigger cancels (TRIGGER_GAP_TOO_LARGE).
  * Exit management (Simulator): break-even at break_even_r, trailing from trail_activation_r at
    trail_distance_r, and a max-hold time stop (max_hold_seconds). No take-profit.

READ-ONLY: receives a ReadOnlyMarketData adapter; never calls any order API. It only RETURNS a
signal dict and appends cancel / NO_FILL / VETO records to state['_emit']. A pending is only
filled while `now` is at/after the trigger touch, so a mid-history reload cannot back-date a fill.
"""
from __future__ import annotations
import hashlib

from live_test import atr, ema_last, config_id, dist

M1, M5 = 'M1', 'M5'


class MomentumM5M1:
    strategy_id = 'MOMENTUM_M5_M1'
    version = 'momentum_m5m1@1'
    needs_market = True

    DEFAULTS = dict(
        entry_timeframe='M1',
        m1_lookback=220, m5_lookback=220, atr_period=14,
        stop_atr=1.2, max_stop_atr=3.0,
        break_even_r=1.0, break_even_offset_points=0.0,
        trail_activation_r=1.5, trail_distance_r=0.75,
        max_spread_atr=0.15, max_fill_gap_atr=0.25,
        entry_buffer_atr=0.1, min_body_atr=0.2, min_trend_separation_atr=0.1,
        pending_expiry_seconds=180, max_hold_seconds=900,
        max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    def label(self):
        return "Momentum M5/M1 EMA pullback"

    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    @staticmethod
    def _sid(symbol, bar_time, d):
        return hashlib.sha1(f"{symbol}:MOM:{int(bar_time)}:{d}".encode()).hexdigest()[:16]

    def _log_fields(self, pend, symbol, now):
        return dict(signal_id=pend['signal_id'], symbol=symbol, side=pend['side'], epoch=now,
                    setup='MOMENTUM', target_level=None)

    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        if market is None:
            return None
        try:
            m1 = market.candles(symbol, M1, c['m1_lookback'], now=now)
            m5 = market.candles(symbol, M5, c['m5_lookback'], now=now)
        except Exception as exc:
            state['_feed_note'] = f'M1/M5 data unavailable: {exc}'
            return None
        if len(m1) < 60 or len(m5) < 60:
            return None
        atr1, atr5 = atr(m1, c['atr_period']), atr(m5, c['atr_period'])
        if not atr1 or not atr5 or atr1 <= 0 or atr5 <= 0:
            return None
        closes5 = [b.close for b in m5]
        ema20_5, ema50_5 = ema_last(closes5, 20), ema_last(closes5, 50)
        d = 1 if ema20_5 > ema50_5 else -1
        trend = abs(ema20_5 - ema50_5) / atr5 >= c['min_trend_separation_atr']
        spread = quote.ask - quote.bid

        # ---------------------------- manage an armed pending --------------------
        pend = state.get('pending')
        if pend:
            dirn = pend['direction']
            invalid = (quote.bid <= pend['stop']) if dirn == 1 else (quote.ask >= pend['stop'])
            if now >= pend['expires']:
                self._emit(state, 'CANCELLED', reason='EXPIRED', **self._log_fields(pend, symbol, now))
                state['pending'] = None
                return None
            if dirn != d or not trend:
                self._emit(state, 'CANCELLED', reason='TREND_CHANGED', **self._log_fields(pend, symbol, now))
                state['pending'] = None
                return None
            if invalid:
                self._emit(state, 'CANCELLED', reason='SETUP_INVALIDATED', **self._log_fields(pend, symbol, now))
                state['pending'] = None
                return None
            if spread > c['max_spread_atr'] * pend['atr']:
                self._emit(state, 'CANCELLED', reason='SPREAD', **self._log_fields(pend, symbol, now))
                state['pending'] = None
                return None
            return self._try_fill(pend, quote, point, state, symbol, now)

        # ---------------------------- scan for a new setup -----------------------
        b = m1[-1]
        prev_ema20 = ema_last([x.close for x in m1[:-1]], 20)
        ema20_1 = ema_last([x.close for x in m1], 20)
        touch = b.low <= prev_ema20 if d == 1 else b.high >= prev_ema20
        body = d * (b.close - b.open) / atr1
        confirm = body >= c['min_body_atr'] and d * (b.close - ema20_1) > 0
        sid = self._sid(symbol, b.time, d)
        if not (trend and touch and confirm) or sid in state.get('seen', []):
            return None
        state.setdefault('seen', []).append(sid)
        buffer = c['entry_buffer_atr'] * atr1
        if d == 1:
            trigger = b.high + buffer + spread
            stop = min(b.low - buffer, trigger - c['stop_atr'] * atr1)
        else:
            trigger = b.low - buffer
            stop = max(b.high + buffer + spread, trigger + c['stop_atr'] * atr1)
        side_px = quote.ask if d == 1 else quote.bid
        veto = ('SPREAD' if spread > c['max_spread_atr'] * atr1 else
                'ALREADY_BROKEN_OUT' if d * (side_px - trigger) >= 0 else
                'STOP_TOO_WIDE' if abs(trigger - stop) > c['max_stop_atr'] * atr1 else None)
        if veto:
            self._emit(state, 'VETO', reason=veto, signal_id=sid, symbol=symbol,
                       side='BUY' if d == 1 else 'SELL', epoch=now, setup='MOMENTUM')
            return None
        state['pending'] = dict(
            direction=d, side='BUY' if d == 1 else 'SELL', signal_id=sid,
            trigger=round(trigger, quote.digits), stop=round(stop, quote.digits),
            atr=atr1, signal_time=b.time + 60, expires=now + c['pending_expiry_seconds'])
        return None

    def _try_fill(self, pend, quote, point, state, symbol, now):
        c = self.config
        d = pend['direction']
        side_px = quote.ask if d == 1 else quote.bid
        if d * (side_px - pend['trigger']) < 0:
            return None                                   # trigger not reached yet
        if d * (side_px - pend['trigger']) > c['max_fill_gap_atr'] * pend['atr']:
            self._emit(state, 'CANCELLED', reason='TRIGGER_GAP_TOO_LARGE', **self._log_fields(pend, symbol, now))
            state['pending'] = None
            return None
        entry = side_px
        distance = d * (entry - pend['stop'])
        if distance <= 0 or distance > c['max_stop_atr'] * pend['atr']:
            self._emit(state, 'NO_FILL', reason='INVALID_FILL_STOP_DISTANCE', **self._log_fields(pend, symbol, now))
            state['pending'] = None
            return None
        extra = self._log_fields(pend, symbol, now)
        extra['fill_price'] = round(entry, quote.digits)
        extra['config_variant'] = 'BUY trend pullback' if d == 1 else 'SELL trend pullback'
        extra['be_trigger_r'] = c['break_even_r']
        extra['be_offset_points'] = c['break_even_offset_points']
        extra['trail_activation_r'] = c['trail_activation_r']
        extra['trail_distance_r'] = c['trail_distance_r']
        state['pending'] = None
        return dict(side=pend['side'],
                    signal_reason=f"Momentum {pend['side']} pullback breakout",
                    signal_time=pend['signal_time'], trigger_price=pend['trigger'],
                    initial_sl=pend['stop'], initial_tp=None,
                    session=None, timeframe=self.timeframe,
                    entry_candle_end=float(now + c['max_hold_seconds']),
                    config_snapshot=dict(self.config), extra=extra)
