"""H4 Swing Sweep + M15 FVG + Engulfing — Live Test agent, fully SIMULATED.

The swept level is a confirmed H4 swing pivot (the nearest un-broken one within the
H4 lookback). A lower-timeframe (M15) sweep + reclaim, then a Fair Value Gap, then an
engulfing confirmation inside that gap triggers the simulated entry:

  BUY  (SELL mirrors):
    1. nearest confirmed H4 swing LOW, un-broken since it confirmed,
    2. an M15 candle sweeps it (low below, close back above),
    3. the first complete bullish M15 FVG within `max_reclaim_to_fvg` bars (C1 after the
       reclaim candle closes),
    4. within `max_fvg_to_engulf` M15 bars, a candle that TOUCHES the gap and whose bullish
       body ENGULFS the prior bearish body,
    5. entry on the quote after that engulfing closes; SL beyond the gap's back boundary;
       TP frozen at FVG-confirm = nearest confirmed H4 swing above, un-touched.
  Time exit 4 h after the fill.

The source post shows BUY; SELL is the Grympha mirror configuration.
READ-ONLY: a ReadOnlyMarketData adapter only; never an order API. Returns a signal dict
and appends NO_FILL / cancel records to state['_emit'].
"""
from __future__ import annotations
import hashlib

from live_test import atr, config_id, dist, swing_points, fvg, engulfs

H4 = 'H4'
_TF_DUR = {'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800, 'H1': 3600, 'H4': 14400}


class H4SwingSweepFVGEngulf:
    strategy_id = 'H4_SWING_SWEEP_FVG_ENGULF'
    version = 'h4_swing_sweep_fvg_engulf@1'
    needs_market = True

    DEFAULTS = dict(
        entry_timeframe='M15',
        h4_lookback=60, h4_pivot_left=2, h4_pivot_right=2, h4_atr_period=14,
        untouched_eps_atr=0.02,
        ltf_pivot_left=2, ltf_pivot_right=2, ltf_atr_period=14,
        sl_buffer_points=None, sl_buffer_atr=0.20,
        min_sl_atr=0.20, max_sl_atr=10.0, min_sl_points=None, max_sl_points=None,
        min_rr=1.0,
        max_reclaim_to_fvg=4, max_fvg_to_engulf=4,
        time_exit_hours=4, max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    def label(self):
        return f"H4 Swing Sweep + M15 FVG + Engulfing ({self.timeframe})"

    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    def _ltf_dur(self):
        return _TF_DUR.get(self.timeframe, 900)

    def _nearest_unbroken(self, h4, point):
        """(swing_low, swing_high) each (level, time) — most recent confirmed H4 pivot not
        broken since it confirmed, or None."""
        c = self.config
        highs, lows = swing_points(h4, c['h4_pivot_left'], c['h4_pivot_right'])
        a = atr(h4, c['h4_atr_period'])
        eps = (c['untouched_eps_atr'] * a) if a else 0.0

        def pick(pivots, is_low):
            for _idx, lvl, t in reversed(pivots):
                after = [b for b in h4 if b.time > t]
                broken = any((b.low < lvl - eps) if is_low else (b.high > lvl + eps) for b in after)
                if not broken:
                    return (lvl, t)
            return None
        return pick(lows, True), pick(highs, False)

    def _nearest_tp(self, h4, side, ref, point):
        """Nearest confirmed H4 swing in the trade direction, un-touched since confirmed."""
        c = self.config
        highs, lows = swing_points(h4, c['h4_pivot_left'], c['h4_pivot_right'])
        a = atr(h4, c['h4_atr_period'])
        eps = (c['untouched_eps_atr'] * a) if a else 0.0
        best = None
        if side == 'BUY':
            for _i, lvl, t in highs:
                if lvl <= ref:
                    continue
                if any(b.high >= lvl - eps and b.time > t for b in h4):
                    continue
                if best is None or lvl < best:
                    best = lvl
        else:
            for _i, lvl, t in lows:
                if lvl >= ref:
                    continue
                if any(b.low <= lvl + eps and b.time > t for b in h4):
                    continue
                if best is None or lvl > best:
                    best = lvl
        return best

    def _detect_sweep(self, candles, level, side):
        last = candles[-1]
        if side == 'BUY' and last.low < level <= last.close:
            return last.low
        if side == 'SELL' and last.high > level >= last.close:
            return last.high
        return None

    def _find_fvg(self, candles, after_time, side):
        dur = self._ltf_dur()
        start = after_time + dur
        for k in range(2, len(candles)):
            c1, c3 = candles[k - 2], candles[k]
            if c1.time < start:
                continue
            g = fvg(c1, c3, side)
            if g:
                return g
        return None

    def _bars_after(self, candles, t):
        return sum(1 for b in candles if b.time > t)

    def _signal_id(self, symbol, side, level, sweep_time, digits):
        key = f"{symbol}:H4SW:{side}:{round(level, digits)}:{int(sweep_time)}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def _log_fields(self, pend, symbol, now):
        return dict(signal_id=pend['signal_id'], symbol=symbol, side=pend['side'], epoch=now,
                    setup='H4_SWING', ltf_swing_level=pend.get('h4_level'),
                    ltf_swing_time=pend.get('h4_time'), sweep_time=pend.get('sweep_time'),
                    sweep_extreme=pend.get('sweep_extreme'), fvg_high=pend.get('fvg_upper'),
                    fvg_low=pend.get('fvg_lower'), fvg_midpoint=pend.get('fvg_midpoint'),
                    fvg_confirm_time=pend.get('fvg_confirm_time'), target_level=pend.get('target_level'))

    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        ref = (quote.bid + quote.ask) / 2.0
        try:
            h4 = market.candles(symbol, H4, c['h4_lookback'], now=now) if market else []
        except Exception as exc:
            state['_feed_note'] = f'H4 data unavailable: {exc}'
            return None
        if not candles or len(candles) < 2:      # sweep needs 1 bar; FVG/engulf gate themselves
            return None
        new_candle = candles[-1].time != state.get('last_candle_time')
        pend = state.get('pending')

        # -------------------------------- SCAN --------------------------------
        if not pend:
            if not new_candle:
                return None
            state['last_candle_time'] = candles[-1].time
            low, high = self._nearest_unbroken(h4, point)
            for side, piv in (('BUY', low), ('SELL', high)):
                if not piv:
                    continue
                extreme = self._detect_sweep(candles, piv[0], side)
                if extreme is None:
                    continue
                tp = self._nearest_tp(h4, side, ref, point)
                if tp is None:
                    self._emit(state, 'SKIP', reason='NO_TP_TARGET', symbol=symbol, side=side, epoch=now)
                    continue
                state['pending'] = dict(
                    phase='SWEPT', side=side,
                    signal_id=self._signal_id(symbol, side, piv[0], candles[-1].time, quote.digits),
                    h4_level=round(piv[0], quote.digits), h4_time=piv[1],
                    sweep_time=candles[-1].time, sweep_extreme=round(extreme, quote.digits),
                    target_level=round(tp, quote.digits))
                return None
            return None

        side = pend['side']
        buy = side == 'BUY'
        sweep_extreme = pend['sweep_extreme']
        tp = pend['target_level']
        target_touched = ref >= tp if buy else ref <= tp
        sweep_rebroken = ref < sweep_extreme if buy else ref > sweep_extreme

        def cancel(event, reason):
            self._emit(state, event, reason=reason, **self._log_fields(pend, symbol, now))
            state['pending'] = None

        if sweep_rebroken:
            cancel('CANCELLED', 'SWEEP_REBROKEN')
            return None
        if target_touched:
            cancel('NO_FILL', 'TARGET_HIT_NO_FILL')
            return None

        # -------------------------------- SWEPT: find FVG ---------------------
        if pend['phase'] == 'SWEPT':
            if new_candle:
                state['last_candle_time'] = candles[-1].time
                g = self._find_fvg(candles, pend['sweep_time'], side)
                if g:
                    pend.update(phase='FVG', fvg_upper=round(g['upper'], quote.digits),
                                fvg_lower=round(g['lower'], quote.digits),
                                fvg_midpoint=round(g['midpoint'], quote.digits),
                                fvg_confirm_time=g['confirm_time'])
                    state['pending'] = pend
                elif self._bars_after(candles, pend['sweep_time']) > c['max_reclaim_to_fvg']:
                    cancel('CANCELLED', 'FVG_TIMEOUT')
            return None

        # -------------------------------- FVG: wait for engulfing -------------
        upper, lower = pend['fvg_upper'], pend['fvg_lower']
        if not new_candle:
            return None
        state['last_candle_time'] = candles[-1].time
        lc, prev = candles[-1], candles[-2]
        if (lc.close < lower) if buy else (lc.close > upper):     # closed beyond the back boundary
            cancel('CANCELLED', 'CLOSED_BEYOND_GAP')
            return None
        if self._bars_after(candles, pend['fvg_confirm_time']) > c['max_fvg_to_engulf']:
            cancel('NO_FILL', 'ENGULF_TIMEOUT')
            return None
        touches = lc.low <= upper and lc.high >= lower                # candle intersects the gap
        if not (touches and engulfs(lc, prev, side)):
            return None
        entry = quote.ask if buy else quote.bid
        a = atr(candles, c['ltf_atr_period'])
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or min_sl is None or max_sl is None:
            return None
        sl = lower - buf if buy else upper + buf
        risk = abs(entry - sl)
        if not (min_sl <= risk <= max_sl):
            cancel('NO_FILL', 'RISK_OUT_OF_BOUNDS')
            return None
        if not ((sl < entry < tp) if buy else (tp < entry < sl)):
            cancel('NO_FILL', 'INVALID_LEVELS')
            return None
        if risk > 0 and (abs(tp - entry) / risk) < c.get('min_rr', 1.0):
            cancel('NO_FILL', 'RR_TOO_LOW')
            return None
        extra = self._log_fields(pend, symbol, now)
        extra['fill_price'] = round(entry, quote.digits)
        extra['config_variant'] = side
        state['pending'] = None
        return dict(side=side, signal_reason=f"H4 swing sweep + M15 FVG + engulf -> {side}",
                    signal_time=pend['sweep_time'], trigger_price=(quote.bid + quote.ask) / 2.0,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=None, timeframe=self.timeframe,
                    entry_candle_end=float(now + c['time_exit_hours'] * 3600),
                    config_snapshot=dict(self.config), extra=extra)
