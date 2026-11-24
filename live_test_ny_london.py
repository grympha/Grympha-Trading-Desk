"""New York Sweep London — Live Test agent, fully SIMULATED.

London morning range = 08:00-12:00 Europe/London (frozen after the window ends). During
the New York morning (08:00-11:00 America/New_York, DST handled independently) price
sweeps one London edge and closes back INSIDE, then a market-structure shift (CHoCH)
confirms the reversal on M5:

  SELL: M5 sweeps the London HIGH and closes back inside; freeze the most recent confirmed
        M5 swing LOW; a later M5 CLOSE below that swing low is the CHoCH -> enter.
  BUY : mirror on the London LOW with a confirmed M5 swing HIGH.

Entry on the first valid quote after the CHoCH closes. SL beyond the sweep extreme;
TP at the OPPOSITE side of the London range. Time exit 12:00 New York.

READ-ONLY: a ReadOnlyMarketData adapter only; never an order API. Returns a signal dict
and appends NO_FILL / cancel records to state['_emit'].
"""
from __future__ import annotations
import hashlib
from datetime import datetime

from live_test import atr, config_id, dist, swing_points, in_window

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

M5 = 'M5'
M5_DUR = 300


class NYSweepLondon:
    strategy_id = 'NY_SWEEP_LONDON'
    version = 'ny_sweep_london@1'
    needs_market = True

    DEFAULTS = dict(
        entry_timeframe='M5',
        london_tz='Europe/London', london_start=8, london_end=12,
        ny_tz='America/New_York', ny_start=8, ny_end=11, time_exit_hour=12,
        m5_lookback=300, ltf_pivot_left=2, ltf_pivot_right=2, atr_period=14,
        pierce_ticks=1, max_sweep_to_choch=6,
        sl_buffer_points=1, sl_buffer_atr=None,
        min_sl_atr=0.05, max_sl_atr=20.0, min_sl_points=None, max_sl_points=None,
        min_rr=1.0, max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    def label(self):
        return f"New York Sweep London ({self.timeframe})"

    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    def _tz(self, name):
        return ZoneInfo(name) if ZoneInfo else None

    def _london_range(self, m5, now):
        """(high, low, first_time) of today's 08:00-12:00 London M5 bars, or None until frozen."""
        c = self.config
        tz = self._tz(c['london_tz'])
        day = datetime.fromtimestamp(now, tz).date()
        bars = []
        for b in m5:
            lt = datetime.fromtimestamp(b.time, tz)
            if lt.date() == day and c['london_start'] <= lt.hour < c['london_end']:
                bars.append(b)
        if not bars:
            return None
        # frozen only once the window has ended (now past 12:00 London today)
        end = datetime.fromtimestamp(now, tz).replace(hour=c['london_end'], minute=0, second=0, microsecond=0)
        if now < end.timestamp():
            return None
        return max(b.high for b in bars), min(b.low for b in bars), bars[0].time

    def _signal_id(self, symbol, day, side, rng_hi):
        return hashlib.sha1(f"{symbol}:NYL:{day}:{side}:{round(rng_hi, 5)}".encode()).hexdigest()[:16]

    def _log_fields(self, pend, symbol, now):
        return dict(signal_id=pend['signal_id'], symbol=symbol, side=pend['side'], epoch=now,
                    setup='NY_SWEEP_LONDON', ref_high=pend['london_high'], ref_low=pend['london_low'],
                    sweep_time=pend.get('sweep_time'), sweep_extreme=pend.get('sweep_extreme'),
                    choch_time=pend.get('choch_time'), choch_level=pend.get('ref_pivot'),
                    target_level=pend.get('target_level'))

    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        ref = (quote.bid + quote.ask) / 2.0
        try:
            m5 = market.candles(symbol, M5, c['m5_lookback'], now=now) if market else list(candles)
        except Exception as exc:
            state['_feed_note'] = f'M5 data unavailable: {exc}'
            return None
        if not m5:
            return None
        day = datetime.fromtimestamp(now, self._tz(c['ny_tz'])).strftime('%Y-%m-%d')
        if state.get('day') != day:
            state['day'] = day
            state['pending'] = None
            state['phase'] = 'SCAN'
        in_ny = in_window(now, c['ny_start'], c['ny_end'], c['ny_tz'])
        new_candle = m5[-1].time != state.get('last_candle_time')
        pend = state.get('pending')
        tick = max(c['pierce_ticks'], 0) * point

        # time-exit boundary (12:00 NY) carried onto the fill
        def ny_exit_epoch():
            tz = self._tz(c['ny_tz'])
            return datetime.fromtimestamp(now, tz).replace(
                hour=c['time_exit_hour'], minute=0, second=0, microsecond=0).timestamp()

        # -------------------------------- SCAN --------------------------------
        if not pend:
            rng = self._london_range(m5, now)
            if not rng or not in_ny:
                return None
            if not new_candle:
                return None
            state['last_candle_time'] = m5[-1].time
            hi, lo, rng_time = rng
            last = m5[-1]
            highs, lows = swing_points(m5, c['ltf_pivot_left'], c['ltf_pivot_right'])
            side = extreme = ref_pivot = None
            if last.high >= hi + tick and last.close < hi:           # swept London high, closed inside
                if lows:
                    side, extreme, ref_pivot = 'SELL', last.high, lows[-1][1]
            elif last.low <= lo - tick and last.close > lo:          # swept London low, closed inside
                if highs:
                    side, extreme, ref_pivot = 'BUY', last.low, highs[-1][1]
            if not side:
                return None
            state['pending'] = dict(
                phase='SWEPT', side=side, signal_id=self._signal_id(symbol, day, side, hi),
                london_high=round(hi, quote.digits), london_low=round(lo, quote.digits),
                sweep_time=last.time, sweep_extreme=round(extreme, quote.digits),
                ref_pivot=round(ref_pivot, quote.digits),
                target_level=round(lo if side == 'SELL' else hi, quote.digits))
            return None

        side = pend['side']
        buy = side == 'BUY'
        tp = pend['target_level']
        sweep_extreme = pend['sweep_extreme']

        def cancel(event, reason):
            self._emit(state, event, reason=reason, **self._log_fields(pend, symbol, now))
            state['pending'] = None

        # invalidations (tick-based)
        if (ref >= tp) if buy else (ref <= tp):
            cancel('NO_FILL', 'TARGET_HIT_NO_FILL')
            return None
        if (ref > pend['london_high']) if side == 'SELL' else (ref < pend['london_low']):
            cancel('CANCELLED', 'CLOSED_BACK_OUTSIDE')
            return None

        # -------------------------------- wait for CHoCH ----------------------
        if new_candle:
            state['last_candle_time'] = m5[-1].time
            last = m5[-1]
            bars_since = sum(1 for b in m5 if b.time > pend['sweep_time'])
            choch = (last.close < pend['ref_pivot']) if side == 'SELL' else (last.close > pend['ref_pivot'])
            if choch:
                pend['choch_time'] = last.time
                state['pending'] = pend
                state['phase'] = 'ARMED'
            elif bars_since > c['max_sweep_to_choch'] or not in_ny:
                cancel('NO_FILL', 'NO_CHOCH')
                return None

        if state.get('phase') != 'ARMED' and not pend.get('choch_time'):
            return None

        # -------------------------------- ARMED: fill on the next valid quote --
        entry = quote.ask if buy else quote.bid
        a = atr(m5, c['atr_period'])
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or min_sl is None or max_sl is None:
            return None
        sl = sweep_extreme + buf if side == 'SELL' else sweep_extreme - buf
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
        state['phase'] = 'SCAN'
        return dict(side=side, signal_reason=f"NY sweep of London {'high' if side == 'SELL' else 'low'} + CHoCH -> {side}",
                    signal_time=pend['sweep_time'], trigger_price=ref,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=f"{day}@NY", timeframe=self.timeframe,
                    entry_candle_end=float(ny_exit_epoch()),
                    config_snapshot=dict(self.config), extra=extra)
