"""Inverse FVG Retest — Live Test agent, fully SIMULATED. Baseline M5.

A Fair Value Gap that is later "flipped" by a candle CLOSING clean through the whole gap
becomes an inverse-FVG zone that should now act as support/resistance:

  BUY : a bearish FVG, then an M5 CLOSE above the ENTIRE gap (wick alone does not flip);
        on the retest the zone is support -> fill when Ask re-enters via the upper edge.
  SELL: a bullish FVG, then an M5 CLOSE below the entire gap; fill when Bid re-enters via
        the lower edge.

The gap must be confirmed within `fvg_lookback` M5 candles before the flip, at least
`min_width_ticks` wide and not already fully filled before the flip candle. Retest within
`max_flip_to_retest` M5 candles; if a quote jumps clean past the whole zone -> NO_FILL.
SL beyond the opposite side of the zone; TP frozen at flip time = nearest confirmed M15
swing in the trade direction, beyond the zone and un-touched. Time exit 2 h after fill.

Variant SWEEP_REQUIRED: the flip must be preceded by an EQL sweep+reclaim (BUY) / EQH
(SELL) within `sweep_lookback`. `sl_from_sweep` is a separate experimental SL option.

READ-ONLY: a ReadOnlyMarketData adapter only; never an order API. Returns a signal dict
and appends NO_FILL / cancel records to state['_emit'].
"""
from __future__ import annotations
import hashlib

from live_test import atr, config_id, dist, swing_points, fvg

M15 = 'M15'
M5_DUR = 300


class InverseFvgRetest:
    strategy_id = 'INVERSE_FVG_RETEST'
    version = 'inverse_fvg_retest@1'
    needs_market = True

    DEFAULTS = dict(
        entry_timeframe='M5', variant='BASELINE',          # BASELINE | SWEEP_REQUIRED
        fvg_lookback=12, min_width_ticks=2,
        max_flip_to_retest=6,
        m15_lookback=120, m15_pivot_left=2, m15_pivot_right=2, m15_atr_period=14,
        untouched_eps_atr=0.02,
        ltf_pivot_left=2, ltf_pivot_right=2, atr_period=14,
        sweep_lookback=12, eq_tolerance_points=None, eq_tolerance_atr=0.10,
        sl_from_sweep=False,                               # experimental: SL beyond sweep extreme
        sl_buffer_points=1, sl_buffer_atr=None,
        min_sl_atr=0.05, max_sl_atr=20.0, min_sl_points=None, max_sl_points=None,
        min_rr=1.0, time_exit_hours=2, max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    def label(self):
        return f"Inverse FVG Retest ({self.timeframe} · {self.config.get('variant', 'BASELINE')})"

    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    def _recent_sweep(self, candles, side, point):
        """SWEEP_REQUIRED precondition: an EQL (BUY) / EQH (SELL) swept and reclaimed within
        the lookback. Returns the sweep extreme price, or None."""
        c = self.config
        window = candles[-(c['sweep_lookback'] + c['ltf_pivot_left'] + c['ltf_pivot_right']):]
        highs, lows = swing_points(window, c['ltf_pivot_left'], c['ltf_pivot_right'])
        a = atr(window, c['atr_period'])
        tol = dist(c['eq_tolerance_points'], c['eq_tolerance_atr'], a, point) or (2 * point)
        pivots = lows if side == 'BUY' else highs
        for b in reversed(window):
            for _i, lvl, t in pivots:
                if b.time <= t:
                    continue
                if side == 'BUY' and b.low < lvl <= b.close and abs(b.low - lvl) <= tol * 10:
                    return b.low
                if side == 'SELL' and b.high > lvl >= b.close and abs(b.high - lvl) <= tol * 10:
                    return b.high
        return None

    def _detect_flip(self, candles, point):
        """Find the most recent flip: a bearish FVG then a close above it (BUY), or a bullish
        FVG then a close below it (SELL). Returns dict(side, upper, lower, flip_time, sweep_ext)."""
        c = self.config
        tick = max(c['min_width_ticks'], 0) * point
        n = len(candles)
        # scan recent candles as the flip candle
        for fi in range(n - 1, 1, -1):
            flip = candles[fi]
            lo = max(0, fi - c['fvg_lookback'] - 2)
            for k in range(fi - 1, lo + 1, -1):           # candidate FVG completing at k (c3=candles[k])
                for side, gside in (('BUY', 'SELL'), ('SELL', 'BUY')):
                    g = fvg(candles[k - 2], candles[k], gside)
                    if not g or (g['upper'] - g['lower']) < tick:
                        continue
                    if side == 'BUY' and flip.close <= g['upper']:
                        continue
                    if side == 'SELL' and flip.close >= g['lower']:
                        continue
                    # not fully filled before the flip: no candle strictly between k and fi closed through
                    mids = candles[k + 1:fi]
                    if side == 'BUY' and any(b.close > g['upper'] for b in mids):
                        continue
                    if side == 'SELL' and any(b.close < g['lower'] for b in mids):
                        continue
                    return dict(side=side, upper=g['upper'], lower=g['lower'],
                                flip_time=flip.time, c1_time=g['c1_time'])
        return None

    def _nearest_tp(self, m15, side, zone_upper, zone_lower, point):
        c = self.config
        highs, lows = swing_points(m15, c['m15_pivot_left'], c['m15_pivot_right'])
        a = atr(m15, c['m15_atr_period'])
        eps = (c['untouched_eps_atr'] * a) if a else 0.0
        best = None
        if side == 'BUY':
            for _i, lvl, t in highs:
                if lvl <= zone_upper:
                    continue
                if any(b.high >= lvl - eps and b.time > t for b in m15):
                    continue
                if best is None or lvl < best:
                    best = lvl
        else:
            for _i, lvl, t in lows:
                if lvl >= zone_lower:
                    continue
                if any(b.low <= lvl + eps and b.time > t for b in m15):
                    continue
                if best is None or lvl > best:
                    best = lvl
        return best

    def _signal_id(self, symbol, side, upper, lower, flip_time, digits):
        key = f"{symbol}:IFVG:{side}:{round(upper, digits)}:{round(lower, digits)}:{int(flip_time)}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def _log_fields(self, pend, symbol, now):
        return dict(signal_id=pend['signal_id'], symbol=symbol, side=pend['side'], epoch=now,
                    setup=f"iFVG/{self.config.get('variant', 'BASELINE')}",
                    fvg_high=pend['upper'], fvg_low=pend['lower'],
                    fvg_midpoint=round((pend['upper'] + pend['lower']) / 2.0, 6),
                    fvg_confirm_time=pend.get('flip_time'), sweep_extreme=pend.get('sweep_extreme'),
                    target_level=pend.get('target_level'))

    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        ref = (quote.bid + quote.ask) / 2.0
        try:
            m15 = market.candles(symbol, M15, c['m15_lookback'], now=now) if market else []
        except Exception as exc:
            state['_feed_note'] = f'M15 data unavailable: {exc}'
            return None
        if not candles or len(candles) < 4:
            return None
        new_candle = candles[-1].time != state.get('last_candle_time')
        pend = state.get('pending')

        # -------------------------------- SCAN for a flip ---------------------
        if not pend:
            if not new_candle:
                return None
            state['last_candle_time'] = candles[-1].time
            flip = self._detect_flip(candles, point)
            if not flip:
                return None
            side = flip['side']
            sweep_ext = None
            if c.get('variant') == 'SWEEP_REQUIRED':
                sweep_ext = self._recent_sweep(candles, side, point)
                if sweep_ext is None:
                    self._emit(state, 'SKIP', reason='NO_PRIOR_SWEEP', symbol=symbol, side=side, epoch=now)
                    return None
            tp = self._nearest_tp(m15, side, flip['upper'], flip['lower'], point)
            if tp is None:
                self._emit(state, 'SKIP', reason='NO_TP_TARGET', symbol=symbol, side=side, epoch=now)
                return None
            state['pending'] = dict(
                side=side, upper=round(flip['upper'], quote.digits), lower=round(flip['lower'], quote.digits),
                flip_time=flip['flip_time'], sweep_extreme=(round(sweep_ext, quote.digits) if sweep_ext else None),
                target_level=round(tp, quote.digits),
                signal_id=self._signal_id(symbol, side, flip['upper'], flip['lower'], flip['flip_time'], quote.digits))
            return None

        side = pend['side']
        buy = side == 'BUY'
        upper, lower, tp = pend['upper'], pend['lower'], pend['target_level']

        def cancel(event, reason):
            self._emit(state, event, reason=reason, **self._log_fields(pend, symbol, now))
            state['pending'] = None

        if (ref >= tp) if buy else (ref <= tp):
            cancel('NO_FILL', 'TARGET_HIT_NO_FILL')
            return None
        # a candle that closes clean back through the whole zone against the flip invalidates it
        if new_candle:
            state['last_candle_time'] = candles[-1].time
            lc = candles[-1]
            if (lc.close < lower) if buy else (lc.close > upper):
                cancel('CANCELLED', 'FLIP_INVALIDATED')
                return None
            if sum(1 for b in candles if b.time > pend['flip_time']) > c['max_flip_to_retest']:
                cancel('NO_FILL', 'RETEST_TIMEOUT')
                return None

        # retest fill: re-enter the zone through the flip-side edge
        if buy:
            if quote.ask < lower:                     # gapped clean below the zone
                cancel('NO_FILL', 'JUMPED_PAST_ZONE')
                return None
            filled = lower <= quote.ask <= upper
        else:
            if quote.bid > upper:
                cancel('NO_FILL', 'JUMPED_PAST_ZONE')
                return None
            filled = lower <= quote.bid <= upper
        if not filled:
            return None
        entry = quote.ask if buy else quote.bid
        a = atr(candles, c['atr_period'])
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or min_sl is None or max_sl is None:
            return None
        if c.get('sl_from_sweep') and pend.get('sweep_extreme') is not None:
            sl = pend['sweep_extreme'] - buf if buy else pend['sweep_extreme'] + buf
        else:
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
        extra['config_variant'] = c.get('variant', 'BASELINE')
        state['pending'] = None
        return dict(side=side, signal_reason=f"Inverse FVG retest ({c.get('variant', 'BASELINE')}) -> {side}",
                    signal_time=pend['flip_time'], trigger_price=ref,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=None, timeframe=self.timeframe,
                    entry_candle_end=float(now + c['time_exit_hours'] * 3600),
                    config_snapshot=dict(self.config), extra=extra)
