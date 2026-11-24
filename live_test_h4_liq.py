"""H4 Liquidity Sweep + FVG — a Live Test forward-test agent (fully SIMULATED).

Direction & target come from H4 structure; the sweep, the Fair Value Gap (FVG)
and the entry come from a lower timeframe (LTF: M5 baseline, M15/M1 optional).

  * Equal Highs (EQH) above price, still un-swept  -> BUY bias; target = EQH.
  * Equal Lows (EQL) below price, still un-swept   -> SELL bias; target = EQL.
  * Both eligible -> SKIP (AMBIGUOUS_BIAS). Several same-side targets -> nearest.

BUY sequence (SELL mirrors):
  1. eligible EQH above price,
  2. an LTF candle sweeps a confirmed LTF swing low (pierces below, closes back above),
  3. after the sweep, the first bullish FVG (low[c3] > high[c1]),
  4. a retrace to the 50% FVG midpoint fills the simulated entry (BUY on Ask),
  5. SL below the sweep low (buffer), TP at the frozen EQH target.

This module is READ-ONLY: it receives a ReadOnlyMarketData adapter and never calls
order_send / order_check / positions_get or any order API (a guard test enforces it).
It only RETURNS a signal dict (the engine's Simulator opens the simulated position) and
appends NO_FILL / cancellation / skip records to state['_emit'] for the signal log.

Target-price choice (documented): the group target is the MIDPOINT of the two swing
prices that form the equal-highs / equal-lows pair. This is an initial, testable model.
"""
from __future__ import annotations

from live_test import atr, config_id, swing_points, dist

H4 = 'H4'
_TF_DUR = {'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800, 'H1': 3600, 'H4': 14400}


def _eq_pair(pivots, tol):
    """Most recent pair of confirmed pivots within `tol` of each other.
    `pivots` = [(idx, price, time), ...] sorted by time. Returns (p_old, p_new) or None."""
    for i in range(len(pivots) - 1, 0, -1):
        for j in range(i - 1, -1, -1):
            if abs(pivots[i][1] - pivots[j][1]) <= tol:
                return pivots[j], pivots[i]
    return None


class H4LiquiditySweepFVG:
    strategy_id = 'H4_LIQ_SWEEP_FVG'
    version = 'h4_liq_sweep_fvg@1'
    needs_market = True  # engine passes the ReadOnlyMarketData adapter + resolved symbol

    DEFAULTS = dict(
        entry_timeframe='M5',            # LTF for sweep + FVG + entry (M5 baseline)
        h4_lookback=120, h4_pivot_left=2, h4_pivot_right=2, h4_atr_period=14,
        eq_tolerance_points=None, eq_tolerance_atr=0.10,   # EQH/EQL cluster tolerance
        untouched_eps_atr=0.02,          # slack when testing "untouched since confirmed"
        ltf_pivot_left=2, ltf_pivot_right=2, ltf_atr_period=14,
        sl_buffer_points=None, sl_buffer_atr=0.20,         # SL beyond the sweep extreme
        min_sl_atr=0.20, max_sl_atr=8.0,                   # risk sanity (LTF ATR based)
        min_sl_points=None, max_sl_points=None,
        min_rr=1.0,                      # reject if TP is nearer than 1R from the fill
        entry_model='TOUCH_50',          # TOUCH_50 (tick to midpoint) | RECLAIM_50 (wick+close back)
        max_sweep_to_fvg_bars=6,         # LTF bars sweep-close -> FVG complete
        max_fvg_to_entry_bars=6,         # LTF bars FVG-confirm -> fill (spec: up to 6 more M5)
        max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']   # engine fetches LTF candles with this

    def label(self):
        model = self.config.get('entry_model', 'TOUCH_50')
        return f"H4 Liquidity Sweep + FVG ({self.timeframe} · {model})"

    # ---- helpers -----------------------------------------------------------
    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    def _ltf_dur(self):
        return _TF_DUR.get(self.timeframe, 300)

    def _eligible_targets(self, h4, ref, point):
        """(buy_target, sell_target) where each is a dict(type, level, target_price, p1, p2)
        or None. Eligible = equal-highs above price (BUY) / equal-lows below price (SELL),
        un-swept since confirmation."""
        c = self.config
        if not h4 or len(h4) < c['h4_pivot_left'] + c['h4_pivot_right'] + 2:
            return None, None
        a = atr(h4, c['h4_atr_period'])
        tol = dist(c['eq_tolerance_points'], c['eq_tolerance_atr'], a, point)
        if tol is None:
            return None, None
        eps = (c['untouched_eps_atr'] * a) if a else 0.0
        highs, lows = swing_points(h4, c['h4_pivot_left'], c['h4_pivot_right'])

        def build(pair, kind):
            if not pair:
                return None
            p_old, p_new = pair
            level = (p_old[1] + p_new[1]) / 2.0
            target_price = (p_old[1] + p_new[1]) / 2.0   # midpoint of the two swings
            # un-swept since the later pivot confirmed: no later H4 bar breached the level
            after = [b for b in h4 if b.time > p_new[2]]
            if kind == 'EQH':
                if ref >= level:                      # price already at/above it
                    return None
                if any(b.high >= level - eps for b in after):
                    return None
            else:  # EQL
                if ref <= level:
                    return None
                if any(b.low <= level + eps for b in after):
                    return None
            return dict(type=kind, level=level, target_price=target_price,
                        p1=(p_old[1], p_old[2]), p2=(p_new[1], p_new[2]))

        return build(_eq_pair(highs, tol), 'EQH'), build(_eq_pair(lows, tol), 'EQL')

    def _detect_sweep(self, candles, side):
        """The latest closed LTF candle sweeps a confirmed LTF swing (pierce + reclaim).
        BUY: pierces below a swing low and closes back above it.
        SELL: pierces above a swing high and closes back below it.
        Returns (swing_level, swing_time, sweep_extreme) or None."""
        c = self.config
        if len(candles) < c['ltf_pivot_left'] + c['ltf_pivot_right'] + 2:
            return None
        highs, lows = swing_points(candles, c['ltf_pivot_left'], c['ltf_pivot_right'])
        last = candles[-1]
        if side == 'BUY':
            for idx, lvl, t in reversed(lows):
                if t < last.time and last.low < lvl <= last.close:
                    return lvl, t, last.low
        else:
            for idx, lvl, t in reversed(highs):
                if t < last.time and last.high > lvl >= last.close:
                    return lvl, t, last.high
        return None

    def _find_fvg(self, candles, sweep_time, side):
        """First valid FVG whose candle-1 opens at/after the sweep candle closes.
        Bullish: low[c3] > high[c1]; bearish: high[c3] < low[c1]. Returns dict or None."""
        dur = self._ltf_dur()
        start_open = sweep_time + dur     # c1 must open at/after the sweep candle's close
        for k in range(2, len(candles)):
            c1, c2, c3 = candles[k - 2], candles[k - 1], candles[k]
            if c1.time < start_open:
                continue
            if side == 'BUY' and c3.low > c1.high:
                hi, lo = c1.high, c3.low
                return dict(upper=lo, lower=hi, midpoint=(hi + lo) / 2.0,
                            confirm_time=c3.time, c1_time=c1.time)
            if side == 'SELL' and c3.high < c1.low:
                hi, lo = c3.high, c1.low
                return dict(upper=lo, lower=hi, midpoint=(hi + lo) / 2.0,
                            confirm_time=c3.time, c1_time=c1.time)
        return None

    def _bars_after(self, candles, t):
        return sum(1 for b in candles if b.time > t)

    def _signal_id(self, symbol, kind, level, sweep_time, digits):
        import hashlib
        key = f"{symbol}:{kind}:{round(level, digits)}:{int(sweep_time)}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    # ---- main --------------------------------------------------------------
    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        ref = (quote.bid + quote.ask) / 2.0
        try:
            h4 = market.candles(symbol, H4, c['h4_lookback'], now=now) if market else []
        except Exception as exc:
            state['_feed_note'] = f'H4 data unavailable: {exc}'
            return None
        if not candles:
            return None
        new_candle = candles[-1].time != state.get('last_candle_time')

        pend = state.get('pending')

        # ============================ no active setup: SCAN ===================
        if not pend:
            buy_t, sell_t = self._eligible_targets(h4, ref, point)
            if buy_t and sell_t:
                key = f"{round(buy_t['level'], quote.digits)}/{round(sell_t['level'], quote.digits)}"
                if state.get('_ambig_key') != key:
                    state['_ambig_key'] = key
                    self._emit(state, 'SKIP', reason='AMBIGUOUS_BIAS',
                               detail=f"EQH {round(buy_t['level'], quote.digits)} & "
                                      f"EQL {round(sell_t['level'], quote.digits)} both eligible",
                               epoch=now)
                return None
            state['_ambig_key'] = None
            side = 'BUY' if buy_t else ('SELL' if sell_t else None)
            if not side:
                return None
            if not new_candle:
                return None
            state['last_candle_time'] = candles[-1].time
            swept = self._detect_sweep(candles, side)
            if not swept:
                return None
            target = buy_t if side == 'BUY' else sell_t
            swing_level, swing_time, extreme = swept
            sig_id = self._signal_id(symbol, target['type'], target['level'], candles[-1].time, quote.digits)
            state['pending'] = dict(
                phase='SWEPT', side=side, signal_id=sig_id,
                liq_type=target['type'], target_price=round(target['target_price'], quote.digits),
                h4_level=round(target['level'], quote.digits),
                selection_reason=f"nearest {target['type']} {'above' if side == 'BUY' else 'below'} price",
                ltf_swing_time=swing_time, ltf_swing_level=round(swing_level, quote.digits),
                sweep_time=candles[-1].time, sweep_extreme=round(extreme, quote.digits))
            return None

        side = pend['side']
        target_price = pend['target_price']
        sweep_extreme = pend['sweep_extreme']

        # Tick-based invalidations (apply in both SWEPT and FVG phases) ---------
        if side == 'BUY':
            target_touched = ref >= target_price
            sweep_rebroken = ref < sweep_extreme
        else:
            target_touched = ref <= target_price
            sweep_rebroken = ref > sweep_extreme

        def cancel(event, reason):
            self._emit(state, event, reason=reason, **self._log_fields(pend, symbol, now))
            state['pending'] = None

        if sweep_rebroken:
            cancel('CANCELLED', 'SWEEP_REBROKEN')
            return None

        # ============================ SWEPT: wait for FVG =====================
        if pend['phase'] == 'SWEPT':
            if target_touched:
                cancel('CANCELLED', 'TARGET_TOUCHED_BEFORE_FVG')
                return None
            if new_candle:
                state['last_candle_time'] = candles[-1].time
                fvg = self._find_fvg(candles, pend['sweep_time'], side)
                if fvg:
                    pend.update(phase='FVG', fvg_upper=round(fvg['upper'], quote.digits),
                                fvg_lower=round(fvg['lower'], quote.digits),
                                fvg_midpoint=round(fvg['midpoint'], quote.digits),
                                fvg_confirm_time=fvg['confirm_time'])
                    state['pending'] = pend
                elif self._bars_after(candles, pend['sweep_time']) > c['max_sweep_to_fvg_bars']:
                    cancel('CANCELLED', 'FVG_TIMEOUT')
            return None

        # ============================ FVG: wait for retrace fill ==============
        midpoint = pend['fvg_midpoint']
        model = c.get('entry_model', 'TOUCH_50')
        if target_touched:                       # price ran to target without a retest
            cancel('NO_FILL', 'TARGET_HIT_NO_RETEST')
            return None
        if new_candle:
            state['last_candle_time'] = candles[-1].time
            if self._bars_after(candles, pend['fvg_confirm_time']) > c['max_fvg_to_entry_bars']:
                # RECLAIM_50 logs that the confirmation never happened within the window.
                cancel('NO_FILL', 'WINDOW_EXPIRED' if model == 'TOUCH_50' else 'RECLAIM_NOT_CONFIRMED')
                return None
            if model == 'RECLAIM_50':            # a CLOSED candle must wick the midpoint and close back
                lc = candles[-1]
                reclaimed = (lc.low <= midpoint < lc.close) if side == 'BUY' \
                    else (lc.high >= midpoint > lc.close)
                if reclaimed:
                    pend['reclaimed'] = True
                    state['pending'] = pend

        if model == 'RECLAIM_50':
            if not pend.get('reclaimed'):
                return None
            filled = True                        # confirmed by the closed reclaim candle
        else:                                    # TOUCH_50: BUY on Ask <= midpoint, SELL on Bid >= midpoint
            filled = (quote.ask <= midpoint) if side == 'BUY' else (quote.bid >= midpoint)
        if not filled:
            return None
        entry = quote.ask if side == 'BUY' else quote.bid
        a = atr(candles, c['ltf_atr_period'])
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or min_sl is None or max_sl is None:
            return None
        if side == 'BUY':
            sl, tp = sweep_extreme - buf, target_price
        else:
            sl, tp = sweep_extreme + buf, target_price
        risk = abs(entry - sl)
        if not (min_sl <= risk <= max_sl):
            cancel('NO_FILL', 'RISK_OUT_OF_BOUNDS')
            return None
        if side == 'BUY' and not (sl < entry < tp):
            cancel('NO_FILL', 'INVALID_LEVELS')
            return None
        if side == 'SELL' and not (tp < entry < sl):
            cancel('NO_FILL', 'INVALID_LEVELS')
            return None
        reward = abs(tp - entry)
        if risk > 0 and (reward / risk) < c.get('min_rr', 1.0):     # target too close
            cancel('NO_FILL', 'RR_TOO_LOW')
            return None

        extra = self._log_fields(pend, symbol, now)
        extra['fill_price'] = round(entry, quote.digits)
        extra['config_variant'] = f"{self.timeframe}/{model}"
        extra['target_level'] = pend['target_price']
        state['pending'] = None
        return dict(side=side, signal_reason=f"{pend['liq_type']} sweep+FVG {model} -> {side}",
                    signal_time=pend['sweep_time'], trigger_price=midpoint,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=None, timeframe=self.timeframe, entry_candle_end=None,
                    config_snapshot=dict(self.config), extra=extra)

    @staticmethod
    def _log_fields(pend, symbol, now):
        """The setup fields carried onto the position (and CSV) or the signal-log record."""
        return dict(
            signal_id=pend['signal_id'], symbol=symbol, side=pend['side'], epoch=now,
            setup=pend.get('liq_type'), target_level=pend.get('target_price'),
            h4_liquidity_type=pend['liq_type'], h4_target_price=pend['target_price'],
            selection_reason=pend.get('selection_reason'),
            ltf_swing_time=pend.get('ltf_swing_time'), ltf_swing_level=pend.get('ltf_swing_level'),
            sweep_time=pend.get('sweep_time'), sweep_extreme=pend.get('sweep_extreme'),
            fvg_high=pend.get('fvg_upper'), fvg_low=pend.get('fvg_lower'),
            fvg_midpoint=pend.get('fvg_midpoint'), fvg_confirm_time=pend.get('fvg_confirm_time'))
