"""AMD / Power-of-Three (accumulation - manipulation - distribution) — Live Test agent,
fully SIMULATED. Baseline XAUUSD.

This is the "Market Cycle Trading Guide" study model C1 (plus its A1 / B1 comparison
triggers) ported into the Live Test tab so its setups can be forward-tested. Every rule
below is the guide's fixed practice specification (page 15); no edge is asserted by the
source, so this is a forward-test candidate, not a validated strategy.

All times are UTC (the guide freezes a UTC window, so there is no DST handling in the
rules themselves). The engine's market_data adapter already normalizes the broker feed to
a true UTC epoch, so `datetime.fromtimestamp(epoch, utc)` gives the guide's clock directly.

Model C1 (default):
  * Range  : the twelve M5 bars opening 06:00 .. 06:55 UTC. H = max high, L = min low.
  * Context: frozen at 07:00 UTC from H1 2-bar pivots KNOWN by 07:00. Last two pivot highs
             AND last two pivot lows both rising = bullish (expect an upside reversal after
             a downside sweep); both falling = bearish; otherwise skip the day.
  * Sweep  : first directional sweep after 07:00 (bullish: an M5 low < L - tick; bearish:
             an M5 high > H + tick). One candidate/day. Opposite boundary swept first, or a
             bar that sweeps both sides, skips the day.
  * Origin : the most recent SAME-recovery-colour completed candle in the five M5 bars
             before the sweep bar (bullish selects a bullish candle; bearish a bearish one),
             full wick span. None -> skip.
  * Reclaim: the sweep bar or the next two bars close strictly inside (L, H).
  * Confirm: by the close of the third bar after the reclaim, a close beyond the origin far
             edge (bullish: above origin high; bearish: below origin low) while inside [L, H].
  * Retest : within the next six bars, a bar whose range overlaps the origin zone and closes
             beyond the same confirming edge. First qualifying bar only. A new sweep extreme,
             an opposite-boundary sweep, or the target being reached first cancels.
  * Entry  : the next observed quote after the trigger bar closes (approximates "next bar
             open"). Stop = one tick beyond the sweep-to-trigger extreme; target = one tick
             inside the opposite boundary; net reward / risk must be >= min_rr (1.50). Skip if
             the H1 pivot known at 07:00 lies strictly between entry and target. No entry at
             or after 10:00 UTC.
  * Exit   : stop, target, or the close of the twelfth M5 bar from entry, or 10:00 UTC,
             whichever is first (Simulator CANDLE_END). No partials / break-even / trailing.

Variants (same context / range / sweep / stop model, only the trigger changes):
  * A1 : enter on the next quote after the RECLAIM bar (no origin confirmation, no retest).
  * B1 : enter on the next quote after the CONFIRM bar (no retest).

Costs: like the other Live Test agents, fills are at Ask/Bid (spread embedded once) and no
commission / slippage is modelled; the >= 1.50 reward gate therefore uses the spread-embedded
fill, not the guide's full round-trip cost. R is scored from fill -> initial SL.

READ-ONLY: receives a ReadOnlyMarketData adapter; never calls any order API. It only RETURNS
a signal dict and appends SKIP / NO_FILL / CANCELLED records to state['_emit']. One candidate
per UTC day (state resets on a new day); a reload cannot re-enter a past day's candidate.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timedelta, timezone

from live_test import atr, config_id, dist, swing_points

UTC = timezone.utc
H1, M5 = 'H1', 'M5'
M5_DUR = 300


class AmdPowerOfThree:
    strategy_id = 'AMD_PO3'
    version = 'amd_po3_c1@1'
    needs_market = True

    DEFAULTS = dict(
        model='C1',                       # C1 (confirm+retest) | A1 (reclaim) | B1 (confirm)
        entry_timeframe='M5',
        range_start_hour=6, range_end_hour=7,     # 06:00 .. 06:55 UTC inclusive (12 M5 bars)
        entry_start_hour=7, entry_end_hour=10,    # setup/entry window 07:00 .. 10:00 UTC
        range_min_bars=10,
        h1_lookback=120, h1_pivot_left=2, h1_pivot_right=2,
        m5_lookback=160,
        pierce_ticks=1,                   # minimum sweep/stop/target penetration, in points
        origin_lookback=5,
        reclaim_deadline=2,               # sweep bar + next N bars may reclaim
        confirm_deadline=3,               # bars after reclaim to confirm (reclaim bar may confirm)
        retest_deadline=6,                # bars after confirm to retest (C1 only)
        time_exit_bars=12,                # close of the Nth M5 bar from entry
        min_rr=1.5, atr_period=14,
        max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    def label(self):
        return f"AMD Power of Three ({self.config['model']})"

    # ---- helpers -----------------------------------------------------------
    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    @staticmethod
    def _utc(epoch):
        return datetime.fromtimestamp(epoch, UTC)

    def _hour_epoch(self, now, hour):
        """Epoch of today's UTC `hour`:00 for the UTC date of `now`."""
        d = self._utc(now).replace(hour=0, minute=0, second=0, microsecond=0)
        return (d + timedelta(hours=int(hour))).timestamp()

    @staticmethod
    def _sid(symbol, day, rng_time, model):
        return hashlib.sha1(f"{symbol}:AMD{model}:{day}:{int(rng_time)}".encode()).hexdigest()[:16]

    def _log(self, pend, symbol, now):
        return dict(signal_id=pend['signal_id'], symbol=symbol, side=pend['side'], epoch=now,
                    setup='AMD_PO3', config_variant=self.config['model'],
                    ref_high=pend.get('H'), ref_low=pend.get('L'),
                    sweep_time=pend.get('sweep_time'), target_level=pend.get('target_level'))

    def _context(self, h1, seven):
        """(d, pivot_high, pivot_low) from H1 pivots KNOWN by 07:00; d=0 when the structure
        is mixed. 'Known by 07:00' means the pivot's right-side confirmation bars have closed
        on or before 07:00, so no future information leaks into the daily bias."""
        c = self.config
        closed = [b for b in h1 if b.time + 3600 <= seven]
        highs, lows = swing_points(closed, c['h1_pivot_left'], c['h1_pivot_right'])
        if len(highs) < 2 or len(lows) < 2:
            return 0, None, None
        up = highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]
        dn = highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]
        d = 1 if up else (-1 if dn else 0)
        return d, highs[-1][1], lows[-1][1]

    def _range(self, series, now):
        """(L, H, range_bar_times) over the M5 bars opening in [range_start, range_end) UTC
        today. None if too few bars traded in the window."""
        c = self.config
        start = self._hour_epoch(now, c['range_start_hour'])
        end = self._hour_epoch(now, c['range_end_hour'])
        win = [b for b in series if start <= b.time < end]
        if len(win) < c['range_min_bars']:
            return None
        return min(b.low for b in win), max(b.high for b in win)

    def _resolve(self, series, L, H, d, tick, now):
        """Deterministic walk of today's entry-window M5 bars (<= now, all CLOSED). Returns a
        dict describing the single daily candidate's current state. Keys: stage in
        {WAIT, SKIP, EXPIRE, NOFILL, TRIGGER}; reason; bar_time (the decisive bar); and, for a
        TRIGGER, sweep_time/origin_high/origin_low/extreme for sizing the stop."""
        c = self.config
        model = c['model']
        e_start = self._hour_epoch(now, c['entry_start_hour'])
        e_end = self._hour_epoch(now, c['entry_end_hour'])
        scan = [b for b in series if e_start <= b.time < e_end]
        if not scan:
            return dict(stage='WAIT')
        up = d == 1

        # --- first directional sweep; opposite-first or double sweep skips the day ----
        sweep_i = None
        for i, b in enumerate(scan):
            dbl = b.low < L - tick and b.high > H + tick
            opp = (b.high > H + tick) if up else (b.low < L - tick)
            dir_ = (b.low < L - tick) if up else (b.high > H + tick)
            if dbl:
                return dict(stage='SKIP', reason='DOUBLE_SWEEP', bar_time=b.time)
            if dir_:
                sweep_i = i
                break
            if opp:
                return dict(stage='SKIP', reason='OPPOSITE_SWEPT_FIRST', bar_time=b.time)
        if sweep_i is None:
            return dict(stage='WAIT')
        sweep_bar = scan[sweep_i]

        # --- origin zone: most recent same-recovery-colour candle in the 5 prior bars -----
        full_i = series.index(sweep_bar)
        prior = series[max(0, full_i - c['origin_lookback']):full_i]
        origin = None
        for b in reversed(prior):
            bull = b.close > b.open
            if (bull if up else (not bull)):
                origin = (b.low, b.high)
                break
        if origin is None:
            return dict(stage='SKIP', reason='NO_ORIGIN', bar_time=sweep_bar.time)
        o_lo, o_hi = origin

        # running sweep-to-here extreme (for the stop) ---------------------------------
        extreme = sweep_bar.low if up else sweep_bar.high

        def ext(b):
            nonlocal extreme
            extreme = min(extreme, b.low) if up else max(extreme, b.high)

        # --- reclaim: sweep bar or next N bars close strictly inside (L, H) ------------
        reclaim_i = None
        rwin = scan[sweep_i:sweep_i + 1 + c['reclaim_deadline']]
        for j, b in enumerate(rwin):
            ext(b)
            if b.low < L - tick and b.high > H + tick:
                return dict(stage='SKIP', reason='DOUBLE_SWEEP', bar_time=b.time)
            if L < b.close < H:
                reclaim_i = sweep_i + j
                break
        if reclaim_i is None:
            last = rwin[-1]
            if last is scan[-1] and len(rwin) < 1 + c['reclaim_deadline']:
                return dict(stage='WAIT')           # deadline not yet elapsed
            return dict(stage='EXPIRE', reason='NO_RECLAIM', bar_time=rwin[-1].time)

        if model == 'A1':                            # trigger on the reclaim bar
            return self._trigger(scan, reclaim_i, sweep_bar, extreme, o_lo, o_hi, now)

        # --- confirm: close beyond the origin far edge, inside [L, H] ------------------
        confirm_i = None
        cwin = scan[reclaim_i:reclaim_i + 1 + c['confirm_deadline']]
        for k, b in enumerate(cwin):
            ext(b)
            beyond = (b.close > o_hi) if up else (b.close < o_lo)
            if beyond and L <= b.close <= H:
                confirm_i = reclaim_i + k
                break
        if confirm_i is None:
            if cwin[-1] is scan[-1] and len(cwin) < 1 + c['confirm_deadline']:
                return dict(stage='WAIT')
            return dict(stage='EXPIRE', reason='NO_CONFIRM', bar_time=cwin[-1].time)

        if model == 'B1':                            # trigger on the confirm bar
            return self._trigger(scan, confirm_i, sweep_bar, extreme, o_lo, o_hi, now)

        # --- C1 retest: overlaps the origin zone and closes beyond the confirming edge --
        rt_window = scan[confirm_i + 1:confirm_i + 1 + c['retest_deadline']]
        for b in rt_window:
            # pre-entry cancellations, tested BEFORE this bar is folded into the extreme
            if (b.high > H + tick) if up else (b.low < L - tick):       # opposite boundary sweep
                return dict(stage='NOFILL', reason='OPPOSITE_SWEEP_PRE_ENTRY', bar_time=b.time)
            makes_new_extreme = (b.low < extreme - tick) if up else (b.high > extreme + tick)
            if makes_new_extreme:                                        # fresh sweep extreme
                return dict(stage='NOFILL', reason='NEW_SWEEP_EXTREME_PRE_ENTRY', bar_time=b.time)
            if (b.high >= H - tick) if up else (b.low <= L + tick):      # target reached first
                return dict(stage='NOFILL', reason='TARGET_REACHED_PRE_ENTRY', bar_time=b.time)
            ext(b)
            overlaps = b.low <= o_hi and b.high >= o_lo
            closes_beyond = (b.close > o_hi) if up else (b.close < o_lo)
            if overlaps and closes_beyond:
                return self._trigger(scan, confirm_i + 1 + rt_window.index(b),
                                     sweep_bar, extreme, o_lo, o_hi, now)
        if rt_window and rt_window[-1] is scan[-1] and len(rt_window) < c['retest_deadline']:
            return dict(stage='WAIT')
        if len(rt_window) >= c['retest_deadline']:
            return dict(stage='NOFILL', reason='RETEST_TIMEOUT',
                        bar_time=rt_window[-1].time if rt_window else scan[confirm_i].time)
        return dict(stage='WAIT')

    def _trigger(self, scan, trig_i, sweep_bar, extreme, o_lo, o_hi, now):
        return dict(stage='TRIGGER', bar_time=scan[trig_i].time, sweep_time=sweep_bar.time,
                    origin_high=o_hi, origin_low=o_lo, extreme=extreme)

    # ---- main --------------------------------------------------------------
    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        if market is None:
            return None
        try:
            m5 = market.candles(symbol, M5, c['m5_lookback'], now=now)
            h1 = market.candles(symbol, H1, c['h1_lookback'], now=now)
        except Exception as exc:
            state['_feed_note'] = f'M5/H1 data unavailable: {exc}'
            return None
        if len(m5) < 20 or len(h1) < 10:
            return None

        day = self._utc(now).strftime('%Y-%m-%d')
        if state.get('utc_day') != day:               # new UTC session -> reset the daily candidate
            state['utc_day'] = day
            state['phase'] = 'WAIT'
            state['bias'] = None
            state['pending'] = None

        e_start = self._hour_epoch(now, c['entry_start_hour'])
        e_end = self._hour_epoch(now, c['entry_end_hour'])

        # Freeze range + H1 context once, at/after 07:00 UTC -----------------------------
        if state.get('bias') is None and now >= e_start:
            rng = self._range(m5, now)
            if rng is None:
                return None                            # range window had too little data; wait/skip quietly
            L, H = rng
            d, piv_hi, piv_lo = self._context(h1, e_start)
            sid = self._sid(symbol, day, int(e_start), c['model'])
            if d == 0:
                self._emit(state, 'SKIP', reason='CONTEXT_MIXED', signal_id=sid, symbol=symbol,
                           epoch=now, setup='AMD_PO3', config_variant=c['model'])
                state['phase'] = 'DONE'
                state['bias'] = 0
                return None
            state['bias'] = d
            state['pending'] = dict(signal_id=sid, side=('BUY' if d == 1 else 'SELL'),
                                    L=round(L, quote.digits), H=round(H, quote.digits),
                                    pivot_high=piv_hi, pivot_low=piv_lo)
            state['phase'] = 'WAIT'

        phase = state.get('phase', 'WAIT')
        if phase in ('DONE',) or not state.get('pending'):
            return None
        d = state['bias']
        pend = state['pending']

        # ARMED: fill at the first quote after the trigger bar closed (next-open proxy) ----
        if phase == 'ARMED':
            return self._fill(now, quote, point, state, pend, d, symbol)

        if now >= e_end:                               # 10:00 UTC cutoff, nothing armed
            self._emit(state, 'NO_FILL', reason='ENTRY_CUTOFF_1000', **self._log(pend, symbol, now))
            state['phase'] = 'DONE'
            return None

        new_m5 = m5[-1].time != state.get('last_m5')
        if not new_m5:
            return None
        state['last_m5'] = m5[-1].time

        tick = max(c['pierce_ticks'], 0) * point
        res = self._resolve(m5, pend['L'], pend['H'], d, tick, now)
        stage = res.get('stage')
        # Only act when the decisive bar is the one that just closed (avoids re-logging history).
        decisive = res.get('bar_time') == m5[-1].time

        if stage == 'TRIGGER' and decisive:
            up = d == 1
            extreme = res['extreme']
            sl = extreme - tick if up else extreme + tick
            tp = (pend['H'] - tick) if up else (pend['L'] + tick)
            pend.update(sl=round(sl, quote.digits), target_level=round(tp, quote.digits),
                        origin_high=res['origin_high'], origin_low=res['origin_low'],
                        sweep_time=res['sweep_time'])
            state['pending'] = pend
            state['phase'] = 'ARMED'
            return None
        if stage in ('SKIP', 'EXPIRE', 'NOFILL') and decisive:
            ev = 'SKIP' if stage == 'SKIP' else 'NO_FILL'
            self._emit(state, ev, reason=res.get('reason'), **self._log(pend, symbol, now))
            state['phase'] = 'DONE'
            return None
        return None

    def _fill(self, now, quote, point, state, pend, d, symbol):
        c = self.config
        up = d == 1
        if now >= self._hour_epoch(now, c['entry_end_hour']):
            self._emit(state, 'NO_FILL', reason='ENTRY_CUTOFF_1000', **self._log(pend, symbol, now))
            state['phase'] = 'DONE'
            return None
        entry = quote.ask if up else quote.bid
        sl, tp = pend['sl'], pend['target_level']
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        ok_dir = (sl < entry < tp) if up else (tp < entry < sl)
        if risk <= 0 or not ok_dir:
            self._emit(state, 'NO_FILL', reason='INVALID_LEVELS', **self._log(pend, symbol, now))
            state['phase'] = 'DONE'
            return None
        if reward / risk < c['min_rr']:
            self._emit(state, 'NO_FILL', reason='RR_TOO_LOW', **self._log(pend, symbol, now))
            state['phase'] = 'DONE'
            return None
        piv = pend.get('pivot_high') if up else pend.get('pivot_low')
        if piv is not None and ((entry < piv < tp) if up else (tp < piv < entry)):
            self._emit(state, 'NO_FILL', reason='PIVOT_BLOCKS_TARGET', **self._log(pend, symbol, now))
            state['phase'] = 'DONE'
            return None
        bar_open = (int(now) // M5_DUR) * M5_DUR
        horizon = bar_open + c['time_exit_bars'] * M5_DUR
        exit_end = min(horizon, self._hour_epoch(now, c['entry_end_hour']))
        extra = self._log(pend, symbol, now)
        extra['fill_price'] = round(entry, quote.digits)
        extra['config_variant'] = c['model']
        side = 'BUY' if up else 'SELL'
        state['phase'] = 'DONE'
        state['pending'] = None
        return dict(side=side, signal_reason=f"AMD {c['model']} {side} failed-breakout reversal",
                    signal_time=pend.get('sweep_time', now), trigger_price=entry,
                    initial_sl=pend['sl'], initial_tp=pend['target_level'],
                    session=f"{self._utc(now).strftime('%Y-%m-%d')}@AMD-UTC",
                    timeframe=self.timeframe, entry_candle_end=float(exit_end),
                    config_snapshot=dict(self.config), extra=extra)
