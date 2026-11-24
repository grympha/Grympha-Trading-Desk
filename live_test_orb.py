"""Gold ORB 09:30 New York — Live Test agent, fully SIMULATED. Baseline XAUUSD SELL.

Opening range = the 09:30-09:45 America/New_York M15 candle (frozen after it closes).
A SELL setup, gated by an H4 bearish bias, then plays out on M5:

  1. H4 bias bearish: the last two confirmed swing highs decline AND the last two
     confirmed swing lows decline. Frozen when the opening range is ready.
  2. an M5 candle CLOSES below the range low (breakout),
  3. within `max_break_to_retest` M5 candles, a later M5 candle whose HIGH touches the
     range low and whose CLOSE is back below it (the retest); any M5 that closes back
     inside/above the range before that cancels the setup,
  4. SELL on the first valid Bid after the retest candle closes (<= 11:00 NY, 1/day),
  5. SL above the range high; TP = the nearest confirmed M15 swing low below entry,
     un-touched since it confirmed (a Grympha demand proxy). Skip if none.
  6. time exit at 12:00 New York.

The long (BUY) side is the MIRROR and is DISABLED on the baseline (`enable_long=False`).
READ-ONLY: a ReadOnlyMarketData adapter only; never an order API. Returns a signal dict
and appends NO_FILL / cancel / skip records to state['_emit'].
"""
from __future__ import annotations
import hashlib
from datetime import datetime

from live_test import atr, config_id, dist, swing_points

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

H4, M15 = 'H4', 'M15'
M5_DUR, M15_DUR = 300, 900


class ORBAgent:
    strategy_id = 'ORB_BREAKOUT_RETEST'
    version = 'gold_orb_0930ny@1'
    needs_market = True

    DEFAULTS = dict(
        entry_timeframe='M5', symbol_hint='XAUUSD',
        tz='America/New_York', open_hour=9, open_minute=30, range_minutes=15,
        entry_cutoff_hour=11, time_exit_hour=12,
        enable_short=True, enable_long=False,            # baseline: SELL only
        h4_lookback=120, h4_pivot_left=2, h4_pivot_right=2,
        m15_lookback=120, m15_pivot_left=2, m15_pivot_right=2,
        pierce_ticks=1, max_break_to_retest=6,
        sl_buffer_points=1, sl_buffer_atr=None, atr_period=14,
        min_sl_atr=0.1, max_sl_atr=20.0, min_sl_points=None, max_sl_points=None,
        min_rr=1.0, max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    def label(self):
        return 'Gold ORB 09:30 New York (SELL)'

    # ---- helpers -----------------------------------------------------------
    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    def _tz(self):
        return ZoneInfo(self.config['tz']) if ZoneInfo else None

    def _local(self, epoch):
        return datetime.fromtimestamp(epoch, self._tz())

    def _after_local(self, now, hour, minute=0):
        tz = self._tz()
        base = self._local(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return now >= base.timestamp()

    def _opening_bar(self, m15, now):
        """The CLOSED 09:30 M15 bar for today's NY date, or None."""
        c = self.config
        day = self._local(now).date()
        for b in m15:
            lt = self._local(b.time)
            if (lt.hour == c['open_hour'] and lt.minute == c['open_minute'] and lt.date() == day
                    and b.time + c['range_minutes'] * 60 <= now):
                return b
        return None

    def _h4_bearish(self, h4):
        c = self.config
        highs, lows = swing_points(h4, c['h4_pivot_left'], c['h4_pivot_right'])
        if len(highs) < 2 or len(lows) < 2:
            return False
        return highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]

    def _nearest_m15_demand(self, m15, entry, now):
        """Nearest confirmed M15 swing low below `entry`, un-touched since it confirmed."""
        c = self.config
        _, lows = swing_points(m15, c['m15_pivot_left'], c['m15_pivot_right'])
        best = None
        for _idx, lvl, t in lows:
            if lvl >= entry:
                continue
            if any(b.low <= lvl and b.time > t for b in m15):     # already touched since confirmed
                continue
            if best is None or lvl > best[0]:                     # nearest below entry = highest such low
                best = (lvl, t)
        return best

    def _signal_id(self, symbol, day, rng_time):
        return hashlib.sha1(f"{symbol}:ORB:{day}:{int(rng_time)}".encode()).hexdigest()[:16]

    def _log_fields(self, pend, symbol, now):
        return dict(signal_id=pend['signal_id'], symbol=symbol, side=pend['side'], epoch=now,
                    setup='ORB_0930NY', ref_high=pend['range_high'], ref_low=pend['range_low'],
                    sweep_time=pend.get('break_time'), target_level=pend.get('target_level'))

    # ---- main --------------------------------------------------------------
    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        try:
            h4 = market.candles(symbol, H4, c['h4_lookback'], now=now) if market else []
            m15 = market.candles(symbol, M15, c['m15_lookback'], now=now) if market else []
        except Exception as exc:
            state['_feed_note'] = f'H4/M15 data unavailable: {exc}'
            return None
        day = self._local(now).strftime('%Y-%m-%d')
        if state.get('ny_day') != day:                    # new NY session -> reset the daily setup
            state['ny_day'] = day
            state['phase'] = 'WAIT_RANGE'
            state['pending'] = None
        if not candles:
            return None
        new_m5 = candles[-1].time != state.get('last_m5')
        pend = state.get('pending') or {}
        phase = state.get('phase', 'WAIT_RANGE')
        after_cutoff = self._after_local(now, c['entry_cutoff_hour'])

        # 1) freeze the opening range once it has closed, with the H4 bias gate ----
        if phase == 'WAIT_RANGE':
            bar = self._opening_bar(m15, now)
            if not bar or after_cutoff:
                return None
            if not (c['enable_short'] and self._h4_bearish(h4)):
                if state.get('_bias_logged') != day:
                    state['_bias_logged'] = day
                    self._emit(state, 'SKIP', reason='H4_BIAS_NOT_BEARISH', symbol=symbol, epoch=now)
                return None
            state['pending'] = dict(
                side='SELL', signal_id=self._signal_id(symbol, day, bar.time),
                range_high=round(bar.high, quote.digits), range_low=round(bar.low, quote.digits),
                range_time=bar.time)
            state['phase'] = 'WAIT_BREAK'
            return None

        # entries are done for the day once one was taken
        if phase == 'DONE':
            return None
        if after_cutoff and phase in ('WAIT_BREAK', 'WAIT_RETEST'):
            self._emit(state, 'NO_FILL', reason='ENTRY_CUTOFF_1100', **self._log_fields(pend, symbol, now))
            state['phase'] = 'DONE'
            state['pending'] = None
            return None

        rng_low, rng_high = pend['range_low'], pend['range_high']
        tick = max(c['pierce_ticks'], 0) * point

        # 2) breakout: an M5 CLOSE below the range low ---------------------------
        if phase == 'WAIT_BREAK':
            if new_m5:
                state['last_m5'] = candles[-1].time
                lc = candles[-1]
                if lc.close < rng_low - tick:
                    pend['break_time'] = lc.time
                    state['pending'] = pend
                    state['phase'] = 'WAIT_RETEST'
            return None

        # 3) retest within the window; cancel if price closes back inside --------
        if phase == 'WAIT_RETEST':
            if new_m5:
                state['last_m5'] = candles[-1].time
                lc = candles[-1]
                bars_since = sum(1 for b in candles if b.time > pend['break_time'])
                if lc.close >= rng_low:                    # back inside/above -> failed
                    self._emit(state, 'CANCELLED', reason='CLOSED_BACK_INSIDE', **self._log_fields(pend, symbol, now))
                    state['phase'] = 'DONE'
                    state['pending'] = None
                    return None
                if lc.high >= rng_low and lc.close < rng_low:   # retest: touched the edge, rejected
                    pend['retest_time'] = lc.time
                    state['pending'] = pend
                    state['phase'] = 'ARMED'
                elif bars_since > c['max_break_to_retest']:
                    self._emit(state, 'NO_FILL', reason='RETEST_TIMEOUT', **self._log_fields(pend, symbol, now))
                    state['phase'] = 'DONE'
                    state['pending'] = None
            return None

        # 4) ARMED: SELL on the first valid Bid after the retest candle closed ---
        if phase == 'ARMED':
            entry = quote.bid
            tgt = self._nearest_m15_demand(m15, entry, now)
            if tgt is None:
                self._emit(state, 'NO_FILL', reason='NO_TARGET', **self._log_fields(pend, symbol, now))
                state['phase'] = 'DONE'
                state['pending'] = None
                return None
            a = atr(candles, c['atr_period'])
            buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
            min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
            max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
            if buf is None or min_sl is None or max_sl is None:
                return None
            sl = rng_high + buf
            tp = round(tgt[0], quote.digits)
            risk = sl - entry
            pend['target_level'] = tp
            if not (min_sl <= risk <= max_sl) or not (tp < entry < sl):
                self._emit(state, 'NO_FILL', reason='INVALID_LEVELS', **self._log_fields(pend, symbol, now))
                state['phase'] = 'DONE'
                state['pending'] = None
                return None
            if risk > 0 and ((entry - tp) / risk) < c.get('min_rr', 1.0):
                self._emit(state, 'NO_FILL', reason='RR_TOO_LOW', **self._log_fields(pend, symbol, now))
                state['phase'] = 'DONE'
                state['pending'] = None
                return None
            tz = self._tz()
            exit_at = self._local(now).replace(hour=c['time_exit_hour'], minute=0, second=0, microsecond=0)
            extra = self._log_fields(pend, symbol, now)
            extra['fill_price'] = round(entry, quote.digits)
            extra['m15_demand_time'] = tgt[1]
            extra['config_variant'] = 'SELL'
            state['phase'] = 'DONE'
            state['pending'] = None
            return dict(side='SELL', signal_reason='Gold ORB 09:30 NY break+retest SELL',
                        signal_time=pend.get('retest_time', now), trigger_price=entry,
                        initial_sl=round(sl, quote.digits), initial_tp=tp,
                        session=f"{day}@0930NY", timeframe=self.timeframe,
                        entry_candle_end=float(exit_at.timestamp()),
                        config_snapshot=dict(self.config), extra=extra)
        return None
