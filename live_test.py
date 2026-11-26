"""Live Test — forward-test strategies on live MT5 market data, fully simulated.

Every entry, position, exit and P/L here is a SIMULATION inside the Trading Desk.
This module receives ONLY a ReadOnlyMarketData adapter (market_data.py); it never
imports or calls the executor, order_send, or any order/position-mutating API.
A guard test (test_live_test) scans this file for forbidden tokens.

Price basis: a consistent mid price is used to build/observe breakouts; fills use
Ask (BUY entry) / Bid (BUY exit) and Bid (SELL entry) / Ask (SELL exit). R is the
entry-to-initial-SL distance. Spread is embedded once via the bid/ask fill basis.
"""
from __future__ import annotations
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from market_hours import MarketClosed, unexplained_gap_seconds

MYT = timezone(timedelta(hours=8))  # Malaysia time, no DST

VALID = 'OK'
DATA_GAP = 'DATA_GAP'
UNCERTAIN = 'UNCERTAIN'


# --------------------------------------------------------------------------- helpers
def config_id(config: dict) -> str:
    return hashlib.sha1(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]


def myt_iso(epoch) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), MYT).strftime('%Y-%m-%d %H:%M:%S MYT')


def iso_utc(epoch) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def session_first_hour(now: float, open_hour: int, tz_name: str, hours: int = 1):
    """[start, end] epoch of the first `hours` of today's session in tz `tz_name`
    (DST-aware via zoneinfo). Falls back to a fixed MYT if zoneinfo is missing."""
    tz = ZoneInfo(tz_name) if ZoneInfo else MYT
    local = datetime.fromtimestamp(now, tz)
    start_local = local.replace(hour=int(open_hour), minute=0, second=0, microsecond=0)
    start = start_local.timestamp()
    end = (start_local + timedelta(hours=hours)).timestamp()
    return start, end


def in_session_first_hour(now, open_hour, tz_name, hours=1) -> bool:
    start, end = session_first_hour(now, open_hour, tz_name, hours)
    return start <= now < end


def in_window(now, start_hour, end_hour, tz_name) -> bool:
    """True if `now` is within [start_hour, end_hour) local time today (DST-aware)."""
    tz = ZoneInfo(tz_name) if ZoneInfo else MYT
    h = datetime.fromtimestamp(now, tz)
    start = h.replace(hour=int(start_hour), minute=0, second=0, microsecond=0).timestamp()
    end = h.replace(hour=int(end_hour), minute=0, second=0, microsecond=0).timestamp()
    return start <= now < end


def candle_at_hour(candles, hour, tz_name, now, by='close'):
    """The CLOSED H1 candle whose open ('open') or close ('close') lands at `hour` local today."""
    tz = ZoneInfo(tz_name) if ZoneInfo else MYT
    today = datetime.fromtimestamp(now, tz).date()
    for b in candles:
        ref = b.time + (3600 if by == 'close' else 0)
        lt = datetime.fromtimestamp(ref, tz)
        if lt.hour == int(hour) and lt.date() == today:
            return b
    return None


def atr(candles, period=14):
    """Average True Range (in price) over the last `period` closed bars. None if < 2 bars.
    Lets each agent size SL/TP to an instrument's own volatility instead of fixed points."""
    if not candles or len(candles) < 2:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i].high, candles[i].low, candles[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    window = trs[-period:]
    return (sum(window) / len(window)) if window else None


def ema_last(values, n):
    """Exponential moving average (last value) over `values`, seeded on values[0] with the
    standard 2/(n+1) weight. Matches the Momentum/Advocate desks' EMA so the ported agents
    make the same bias decision. Returns None for an empty series."""
    if not values:
        return None
    e = values[0]
    k = 2.0 / (n + 1)
    for v in values[1:]:
        e += k * (v - e)
    return e


def dist(points_param, atr_mult, atr_value, point):
    """Resolve a distance in price. An explicit *_points value (not None) wins and pins a fixed
    distance; otherwise it is atr_mult * ATR (per-instrument). None when neither is available."""
    if points_param is not None:
        return points_param * point
    if atr_mult is not None and atr_value is not None:
        return atr_mult * atr_value
    return None


def fvg(c1, c3, side):
    """A 3-candle Fair Value Gap between candle-1 and candle-3 (candle-2 is the displacement).
    Bullish: low[c3] > high[c1] (gap = [high c1, low c3]); bearish: high[c3] < low[c1].
    Returns dict(upper, lower, midpoint, confirm_time, c1_time) or None. Boundaries are stored
    low-to-high (lower <= upper) regardless of side. Call once candle-3 has CLOSED."""
    if side == 'BUY' and c3.low > c1.high:
        lo, hi = c1.high, c3.low
    elif side == 'SELL' and c3.high < c1.low:
        lo, hi = c3.high, c1.low
    else:
        return None
    return dict(upper=hi, lower=lo, midpoint=(hi + lo) / 2.0,
                confirm_time=c3.time, c1_time=c1.time)


def engulfs(cur, prev, side):
    """True if `cur` is a `side`-direction candle whose body covers `prev`'s opposite-direction
    body. BUY: cur bullish, prev bearish, cur.close >= prev.open and cur.open <= prev.close.
    SELL mirrors. Used by the M15 FVG + engulfing confirmation agent."""
    if side == 'BUY':
        return (cur.close > cur.open and prev.close < prev.open
                and cur.close >= prev.open and cur.open <= prev.close)
    return (cur.close < cur.open and prev.close > prev.open
            and cur.close <= prev.open and cur.open >= prev.close)


# --------------------------------------------------------------------------- agents
class PrevCandleBreakoutH1:
    """Previous (closed) H1 candle breakout. BUY when the reference price crosses the
    prior H1 high; SELL when it crosses the prior H1 low. Crossings are detected only
    from observed ticks, so a price already beyond the level at start does not fire."""
    strategy_id = 'PREV_CANDLE_BREAKOUT_H1'
    version = 'prev_candle_h1@1'
    timeframe = 'H1'
    DEFAULTS = dict(
        session_open_hour=9, session_tz='Asia/Kuala_Lumpur', session_hours=1,
        atr_period=14, sl_buffer_atr=0.1, tp_atr=1.5, max_sl_atr=3.0,
        sl_buffer_points=None, tp_points=None, max_sl_points=None,
        max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)

    def label(self):
        return 'Previous Candle Breakout H1'

    def evaluate(self, now, quote, candles, point, state: dict):
        """Return a signal dict or None. `state` persists per agent (ref price, fired level)."""
        c = self.config
        ref = (quote.bid + quote.ask) / 2.0
        prev_ref = state.get('ref_price')
        state['ref_price'] = ref
        # Only trade inside the first hour of the configured session.
        if not in_session_first_hour(now, c['session_open_hour'], c['session_tz'], c['session_hours']):
            state['session'] = None
            return None
        session = datetime.fromtimestamp(now, MYT).strftime('%Y-%m-%d') + f"@{c['session_open_hour']:02d}MYT"
        state['session'] = session
        if not candles:
            return None
        prev = candles[-1]  # most recent CLOSED H1 candle
        level_key = f"{session}:{int(prev.time)}"
        if state.get('fired') == level_key:
            return None  # one crossing per level/session (avoid repeat signals)
        if prev_ref is None:
            return None  # first observation: establish baseline, never enter on an already-broken level
        side = reason = None
        if prev_ref <= prev.high < ref:
            side, reason = 'BUY', f'Crossed prev H1 high {prev.high}'
        elif prev_ref >= prev.low > ref:
            side, reason = 'SELL', f'Crossed prev H1 low {prev.low}'
        if not side:
            return None
        entry = quote.ask if side == 'BUY' else quote.bid
        a = atr(candles, c['atr_period'])
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        tp_d = dist(c['tp_points'], c['tp_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or tp_d is None or max_sl is None:
            return None
        if side == 'BUY':
            sl = prev.low - buf
            tp = entry + tp_d
        else:
            sl = prev.high + buf
            tp = entry - tp_d
        # Validate SL/TP direction and distance. Distances are ATR-based per instrument by default.
        risk = abs(entry - sl)
        if risk <= 0 or risk > max_sl:
            return None
        if side == 'BUY' and not (sl < entry < tp):
            return None
        if side == 'SELL' and not (tp < entry < sl):
            return None
        state['fired'] = level_key
        h1 = 3600
        entry_candle_end = (int(now) // h1 + 1) * h1
        return dict(side=side, signal_reason=reason, signal_time=now, trigger_price=ref,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=session, timeframe=self.timeframe, entry_candle_end=float(entry_candle_end),
                    config_snapshot=dict(self.config))


class CounterTrendScalper:
    """Counter-trend 'water-mark' scalp (Scalping Cheat Sheet #1). Fades the off-hours range:
    - watermark mode: the reference-hour (default 3pm EST) H1 CLOSE is a water mark. If that candle
      closed bullish the close is a HIGH water mark -> SHORT when price rises back to it; if bearish it
      is a LOW water mark -> LONG when price falls back to it.
    - midpoint mode: the mid-point of the first H1 bar after 5pm EST (the 'dead-time range'). LONG when
      price crosses below the mid-point, SHORT when it crosses above.
    1:1 risk/reward; target/stop in MT5 points (defaults suit XAUUSD, NOT the PDF's EUR/USD pips).
    Crossings are detected from observed ticks; entries only inside the configured off-hours window."""
    strategy_id = 'COUNTER_TREND_SCALP'
    version = 'counter_trend_scalp@1'
    timeframe = 'H1'
    DEFAULTS = dict(
        mode='watermark', ref_hour=15, mid_hour=17, ref_tz='America/New_York',
        session_start_hour=15, session_end_hour=19,
        atr_period=14, tolerance_atr=0.15, target_atr=0.8,
        tolerance_points=None, target_points=None,
        max_gap_seconds=120, max_positions=1)

    def __init__(self, config=None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)

    def label(self):
        return 'Counter-Trend Water-Mark Scalp'

    def evaluate(self, now, quote, candles, point, state):
        c = self.config
        ref_price = (quote.bid + quote.ask) / 2.0
        prev = state.get('ref_price')
        state['ref_price'] = ref_price
        if not in_window(now, c['session_start_hour'], c['session_end_hour'], c['ref_tz']):
            state['session'] = None
            return None
        day = datetime.fromtimestamp(now, MYT).strftime('%Y-%m-%d')
        state['session'] = f"{day}@offhours"
        if not candles or prev is None:
            return None
        a = atr(candles, c['atr_period'])
        tol = dist(c['tolerance_points'], c['tolerance_atr'], a, point)
        tgt = dist(c['target_points'], c['target_atr'], a, point)
        if tol is None or tgt is None:
            return None
        side = reason = None
        if c['mode'] == 'midpoint':
            bar = candle_at_hour(candles, c['mid_hour'], c['ref_tz'], now, by='open')
            if not bar:
                return None
            mid = (bar.high + bar.low) / 2.0
            level_key = f"mid:{int(bar.time)}"
            if state.get('fired') == level_key:
                return None
            if prev >= mid > ref_price:
                side, reason = 'BUY', f'Below dead-time mid {round(mid, quote.digits)}'
            elif prev <= mid < ref_price:
                side, reason = 'SELL', f'Above dead-time mid {round(mid, quote.digits)}'
        else:  # watermark
            bar = candle_at_hour(candles, c['ref_hour'], c['ref_tz'], now, by='close')
            if not bar:
                return None
            mark = bar.close
            bullish = bar.close >= bar.open
            level_key = f"wm:{int(bar.time)}"
            if state.get('fired') == level_key:
                return None
            if bullish and prev < mark - tol <= ref_price:      # rose to the high water mark -> fade short
                side, reason = 'SELL', f'Hit high water mark {round(mark, quote.digits)}'
            elif (not bullish) and prev > mark + tol >= ref_price:  # fell to the low water mark -> fade long
                side, reason = 'BUY', f'Hit low water mark {round(mark, quote.digits)}'
        if not side:
            return None
        entry = quote.ask if side == 'BUY' else quote.bid
        if side == 'BUY':
            sl, tp = entry - tgt, entry + tgt
        else:
            sl, tp = entry + tgt, entry - tgt
        if tgt <= 0 or not (sl < entry < tp if side == 'BUY' else tp < entry < sl):
            return None
        state['fired'] = level_key
        return dict(side=side, signal_reason=reason, signal_time=now, trigger_price=ref_price,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=state['session'], timeframe=self.timeframe, entry_candle_end=None,
                    config_snapshot=dict(self.config))


def swing_points(candles, left, right):
    """Confirmed pivot highs and lows as [(index, price, time), ...].
    A pivot needs `left` bars before and `right` bars after it, and must be a strict
    extreme versus at least one neighbour (so flat plateaus do not count)."""
    highs, lows = [], []
    n = len(candles)
    for i in range(left, n - right):
        c = candles[i]
        window = range(i - left, i + right + 1)
        if all(c.high >= candles[j].high for j in window) and any(c.high > candles[j].high for j in window if j != i):
            highs.append((i, c.high, c.time))
        if all(c.low <= candles[j].low for j in window) and any(c.low < candles[j].low for j in window if j != i):
            lows.append((i, c.low, c.time))
    return highs, lows


class CmsSupportResistanceFlip:
    """CMS (Mansor Sapari) continuation flip: SBR / RBS.
    - SBR, Support Becomes Resistance: a CLOSE below a prior swing low flips that broken
      support into resistance; SELL when price retests UP into the level.
    - RBS, Resistance Becomes Support: a CLOSE above a prior swing high flips that broken
      resistance into support; BUY when price retests DOWN into the level.
    Swing levels are H1 pivots; the retest crossing is detected from observed ticks, so a
    level already breached at start never enters. SL sits beyond the protective extreme of
    the recent structure; TP is a fixed reward multiple of the measured risk. These SL/TP
    and zone parameters are an initial, testable model and NOT a confirmed CMS rule set."""
    strategy_id = 'CMS_SR_FLIP'
    version = 'cms_sr_flip@1'
    timeframe = 'H1'
    DEFAULTS = dict(
        swing_left=2, swing_right=2, atr_period=14, break_buffer_atr=0.25, sl_buffer_atr=0.15,
        min_sl_atr=0.3, max_sl_atr=4.0, break_buffer_points=None, sl_buffer_points=None,
        min_sl_points=None, max_sl_points=None, protect_lookback=6, reward_rr=2.0,
        flip_ttl_hours=12, session_enabled=False, session_start_hour=0, session_end_hour=24,
        session_tz='America/New_York', max_gap_seconds=120, max_positions=1)

    def __init__(self, config=None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)

    def label(self):
        return 'CMS Support/Resistance Flip (SBR/RBS)'

    def evaluate(self, now, quote, candles, point, state):
        c = self.config
        ref = (quote.bid + quote.ask) / 2.0
        prev = state.get('ref_price')
        state['ref_price'] = ref
        if c['session_enabled'] and not in_window(now, c['session_start_hour'], c['session_end_hour'], c['session_tz']):
            return None
        if not candles or len(candles) < c['swing_left'] + c['swing_right'] + 2:
            return None
        a = atr(candles, c['atr_period'])
        bbuf = dist(c['break_buffer_points'], c['break_buffer_atr'], a, point)
        if bbuf is None:
            return None
        highs, lows = swing_points(candles, c['swing_left'], c['swing_right'])
        last = candles[-1]
        flip = state.get('flip')
        fired = state.get('fired_break')
        # Detect a flip from the latest CLOSED candle breaking a prior pivot (SBR / RBS).
        new_flip = None
        if lows and last.close < lows[-1][1] - bbuf and last.time > lows[-1][2]:
            new_flip = dict(side='SELL', level=lows[-1][1], break_time=last.time, created=now)
        elif highs and last.close > highs[-1][1] + bbuf and last.time > highs[-1][2]:
            new_flip = dict(side='BUY', level=highs[-1][1], break_time=last.time, created=now)
        if new_flip and new_flip['break_time'] != fired and (not flip or flip['break_time'] != new_flip['break_time']):
            flip = new_flip
            state['flip'] = flip
        if flip and (now - flip.get('created', now)) > c['flip_ttl_hours'] * 3600:
            state['flip'] = flip = None
        if not flip or prev is None:
            return None
        level = flip['level']
        side = None
        if flip['side'] == 'SELL' and prev < level <= ref:   # retest up into broken support (now resistance)
            side = 'SELL'
        elif flip['side'] == 'BUY' and prev > level >= ref:   # retest down into broken resistance (now support)
            side = 'BUY'
        if not side:
            return None
        look = candles[-c['protect_lookback']:] if c['protect_lookback'] > 0 else candles[-1:]
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or min_sl is None or max_sl is None:
            return None
        entry = quote.ask if side == 'BUY' else quote.bid
        if side == 'SELL':
            protect = max([b.high for b in look] + [level])
            sl = protect + buf
            risk = sl - entry
            tp = entry - c['reward_rr'] * risk
        else:
            protect = min([b.low for b in look] + [level])
            sl = protect - buf
            risk = entry - sl
            tp = entry + c['reward_rr'] * risk
        if not (min_sl <= risk <= max_sl):
            return None
        if side == 'BUY' and not (sl < entry < tp):
            return None
        if side == 'SELL' and not (tp < entry < sl):
            return None
        state['fired_break'] = flip['break_time']
        state['flip'] = None
        session = datetime.fromtimestamp(now, MYT).strftime('%Y-%m-%d') + '@cms'
        return dict(side=side, signal_reason=f"{'RBS' if side == 'BUY' else 'SBR'} retest of {round(level, quote.digits)}",
                    signal_time=now, trigger_price=ref,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=session, timeframe=self.timeframe, entry_candle_end=None,
                    config_snapshot=dict(self.config))


class CmsFakeoutV1:
    """CMS Fakeout V1: a swing level is pierced then reclaimed inside one H1 candle.
    - SELL: the candle's high pokes above a prior swing high (resistance) but the candle
      CLOSES back below it -> fade the failed breakout.
    - BUY: the candle's low pokes below a prior swing low (support) but CLOSES back above it.
    Enters at market right after the rejection candle closes. SL beyond the fakeout wick;
    TP a fixed reward multiple of risk. Parameters are an initial, testable model."""
    strategy_id = 'CMS_FAKEOUT_V1'
    version = 'cms_fakeout_v1@1'
    timeframe = 'H1'
    DEFAULTS = dict(
        swing_left=2, swing_right=2, atr_period=14, pierce_buffer_atr=0.15, sl_buffer_atr=0.15,
        min_sl_atr=0.3, max_sl_atr=4.0, pierce_buffer_points=None, sl_buffer_points=None,
        min_sl_points=None, max_sl_points=None, reward_rr=2.0,
        session_enabled=False, session_start_hour=0, session_end_hour=24,
        session_tz='America/New_York', max_gap_seconds=120, max_positions=1)

    def __init__(self, config=None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)

    def label(self):
        return 'CMS Fakeout V1 (sweep & reclaim)'

    def evaluate(self, now, quote, candles, point, state):
        c = self.config
        if c['session_enabled'] and not in_window(now, c['session_start_hour'], c['session_end_hour'], c['session_tz']):
            return None
        if not candles or len(candles) < c['swing_left'] + c['swing_right'] + 2:
            return None
        highs, lows = swing_points(candles, c['swing_left'], c['swing_right'])
        last = candles[-1]
        if state.get('fired') == last.time:
            return None
        a = atr(candles, c['atr_period'])
        pbuf = dist(c['pierce_buffer_points'], c['pierce_buffer_atr'], a, point)
        if pbuf is None:
            return None
        side = level = None
        if highs and last.high > highs[-1][1] + pbuf and last.close < highs[-1][1] and last.time > highs[-1][2]:
            side, level = 'SELL', highs[-1][1]
        elif lows and last.low < lows[-1][1] - pbuf and last.close > lows[-1][1] and last.time > lows[-1][2]:
            side, level = 'BUY', lows[-1][1]
        if not side:
            return None
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or min_sl is None or max_sl is None:
            return None
        entry = quote.ask if side == 'BUY' else quote.bid
        if side == 'SELL':
            sl = last.high + buf
            risk = sl - entry
            tp = entry - c['reward_rr'] * risk
        else:
            sl = last.low - buf
            risk = entry - sl
            tp = entry + c['reward_rr'] * risk
        if not (min_sl <= risk <= max_sl):
            return None
        if side == 'BUY' and not (sl < entry < tp):
            return None
        if side == 'SELL' and not (tp < entry < sl):
            return None
        state['fired'] = last.time
        session = datetime.fromtimestamp(now, MYT).strftime('%Y-%m-%d') + '@cms'
        return dict(side=side, signal_reason=f'Fakeout V1 {side} at {round(level, quote.digits)}',
                    signal_time=now, trigger_price=(quote.bid + quote.ask) / 2.0,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=session, timeframe=self.timeframe, entry_candle_end=None,
                    config_snapshot=dict(self.config))


class CmsSupplyDemand:
    """CMS Supply & Demand base (DBR / RBD / RBR / DBD): a tight base between two legs,
    where the leg OUT of the base sets the zone. A bullish departure marks a DEMAND zone
    (BUY on return to the zone); a bearish departure marks a SUPPLY zone (SELL on return).
    Zone = [base low, base high]; the retest is detected from observed ticks. SL beyond the
    far edge of the zone; TP a fixed reward multiple of risk. An initial, testable model."""
    strategy_id = 'CMS_SUPPLY_DEMAND'
    version = 'cms_supply_demand@1'
    timeframe = 'H1'
    DEFAULTS = dict(
        atr_period=14, impulse_body_atr=1.0, base_range_atr=0.6, sl_buffer_atr=0.15,
        min_sl_atr=0.3, max_sl_atr=5.0, impulse_body_points=None, base_range_points=None,
        sl_buffer_points=None, min_sl_points=None, max_sl_points=None,
        base_max=3, scan=20, reward_rr=2.0,
        zone_ttl_hours=24, session_enabled=False, session_start_hour=0, session_end_hour=24,
        session_tz='America/New_York', max_gap_seconds=120, max_positions=1)

    def __init__(self, config=None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)

    def label(self):
        return 'CMS Supply & Demand base'

    def evaluate(self, now, quote, candles, point, state):
        c = self.config
        ref = (quote.bid + quote.ask) / 2.0
        prev = state.get('ref_price')
        state['ref_price'] = ref
        if c['session_enabled'] and not in_window(now, c['session_start_hour'], c['session_end_hour'], c['session_tz']):
            return None
        if not candles or len(candles) < 3:
            return None
        a = atr(candles, c['atr_period'])
        imp_body = dist(c['impulse_body_points'], c['impulse_body_atr'], a, point)
        base_rng = dist(c['base_range_points'], c['base_range_atr'], a, point)
        if imp_body is None or base_rng is None:
            return None
        n = len(candles)
        zone = state.get('zone')
        fired = state.get('fired_zone')
        # most recent base->impulse structure within the scan window
        new = None
        lo = max(1, n - c['scan'])
        for k in range(n - 1, lo - 1, -1):
            imp = candles[k]
            if abs(imp.close - imp.open) < imp_body:
                continue
            base = []
            j = k - 1
            while j >= 0 and (candles[j].high - candles[j].low) <= base_rng and len(base) < c['base_max']:
                base.append(candles[j])
                j -= 1
            if not base:
                continue
            new = dict(side=('BUY' if imp.close > imp.open else 'SELL'),
                       low=min(b.low for b in base), high=max(b.high for b in base),
                       imp_time=imp.time, created=now)
            break
        if new and new['imp_time'] != fired and (not zone or zone['imp_time'] != new['imp_time']):
            zone = new
            state['zone'] = zone
        if zone and (now - zone.get('created', now)) > c['zone_ttl_hours'] * 3600:
            state['zone'] = zone = None
        if not zone or prev is None:
            return None
        side = None
        if zone['side'] == 'BUY' and prev > zone['high'] >= ref:     # fell back into demand zone top
            side = 'BUY'
        elif zone['side'] == 'SELL' and prev < zone['low'] <= ref:   # rose back into supply zone bottom
            side = 'SELL'
        if not side:
            return None
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or min_sl is None or max_sl is None:
            return None
        entry = quote.ask if side == 'BUY' else quote.bid
        if side == 'BUY':
            sl = zone['low'] - buf
            risk = entry - sl
            tp = entry + c['reward_rr'] * risk
        else:
            sl = zone['high'] + buf
            risk = sl - entry
            tp = entry - c['reward_rr'] * risk
        if not (min_sl <= risk <= max_sl):
            return None
        if side == 'BUY' and not (sl < entry < tp):
            return None
        if side == 'SELL' and not (tp < entry < sl):
            return None
        state['fired_zone'] = zone['imp_time']
        state['zone'] = None
        session = datetime.fromtimestamp(now, MYT).strftime('%Y-%m-%d') + '@cms'
        kind = 'Demand' if side == 'BUY' else 'Supply'
        return dict(side=side,
                    signal_reason=f"{kind} zone {round(zone['low'], quote.digits)}-{round(zone['high'], quote.digits)}",
                    signal_time=now, trigger_price=ref,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=session, timeframe=self.timeframe, entry_candle_end=None,
                    config_snapshot=dict(self.config))


class CmsSsr:
    """CMS SSR (Significant Support & Resistance), basic reaction model. A level touched
    at least `min_touches` times (swing pivots clustered within tolerance) is treated as
    significant; price reacting back into it is faded: SELL at significant resistance on a
    retest up, BUY at significant support on a retest down. The 'King' selection in CMS is
    not publicly confirmed, so `min_touches`/`cluster_points` are invented thresholds."""
    strategy_id = 'CMS_SSR'
    version = 'cms_ssr@1'
    timeframe = 'H1'
    DEFAULTS = dict(
        swing_left=2, swing_right=2, min_touches=2, atr_period=14, cluster_atr=0.4,
        sl_buffer_atr=0.2, min_sl_atr=0.3, max_sl_atr=3.0, cluster_points=None,
        sl_buffer_points=None, min_sl_points=None, max_sl_points=None, reward_rr=2.0,
        session_enabled=False, session_start_hour=0, session_end_hour=24,
        session_tz='America/New_York', max_gap_seconds=120, max_positions=1)

    def __init__(self, config=None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)

    def label(self):
        return 'CMS Significant S/R (SSR)'

    @staticmethod
    def _significant(prices, tol, min_touches):
        best, best_n = None, 0
        for p in prices:
            members = [q for q in prices if abs(q - p) <= tol]
            if len(members) >= min_touches and len(members) > best_n:
                best, best_n = sum(members) / len(members), len(members)
        return best

    def evaluate(self, now, quote, candles, point, state):
        c = self.config
        ref = (quote.bid + quote.ask) / 2.0
        prev = state.get('ref_price')
        state['ref_price'] = ref
        if c['session_enabled'] and not in_window(now, c['session_start_hour'], c['session_end_hour'], c['session_tz']):
            return None
        if not candles or len(candles) < c['swing_left'] + c['swing_right'] + 2:
            return None
        a = atr(candles, c['atr_period'])
        tol = dist(c['cluster_points'], c['cluster_atr'], a, point)
        if tol is None:
            return None
        highs, lows = swing_points(candles, c['swing_left'], c['swing_right'])
        res = self._significant([h[1] for h in highs], tol, c['min_touches'])
        sup = self._significant([l[1] for l in lows], tol, c['min_touches'])
        if prev is None:
            return None
        side = level = None
        if res is not None and prev < res <= ref:       # retest up into significant resistance -> fade SELL
            side, level = 'SELL', res
        elif sup is not None and prev > sup >= ref:      # retest down into significant support -> BUY
            side, level = 'BUY', sup
        if not side:
            return None
        key = f'{side}:{round(level, quote.digits)}'
        if state.get('fired') == key:
            return None
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or min_sl is None or max_sl is None:
            return None
        entry = quote.ask if side == 'BUY' else quote.bid
        if side == 'SELL':
            sl = level + buf
            risk = sl - entry
            tp = entry - c['reward_rr'] * risk
        else:
            sl = level - buf
            risk = entry - sl
            tp = entry + c['reward_rr'] * risk
        if not (min_sl <= risk <= max_sl):
            return None
        if side == 'BUY' and not (sl < entry < tp):
            return None
        if side == 'SELL' and not (tp < entry < sl):
            return None
        state['fired'] = key
        session = datetime.fromtimestamp(now, MYT).strftime('%Y-%m-%d') + '@cms'
        return dict(side=side, signal_reason=f'SSR {side} at significant {round(level, quote.digits)}',
                    signal_time=now, trigger_price=ref,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=session, timeframe=self.timeframe, entry_candle_end=None,
                    config_snapshot=dict(self.config))


# --------------------------------------------------------------------------- simulator
class Simulator:
    """Simulated fills and exits. BUY fills Ask / exits Bid; SELL fills Bid / exits Ask."""

    @staticmethod
    def open(agent, signal, quote, now):
        side = signal['side']
        entry = quote.ask if side == 'BUY' else quote.bid
        risk = abs(entry - signal['initial_sl'])
        tid = hashlib.sha1(f"{agent.id}:{signal['signal_time']}:{entry}".encode()).hexdigest()[:16]
        return dict(
            trade_id=tid, agent_id=agent.id, strategy_id=agent.agent.strategy_id,
            strategy_version=agent.agent.version, config_id=agent.agent.config_id,
            symbol=agent.symbol, timeframe=signal['timeframe'], session=signal.get('session'),
            side=side, signal_reason=signal['signal_reason'], signal_time=signal['signal_time'],
            trigger_price=signal['trigger_price'], entry_time=now, entry_price=entry,
            initial_sl=signal['initial_sl'], initial_tp=signal['initial_tp'],
            entry_spread=round(quote.ask - quote.bid, quote.digits), risk=risk,
            entry_candle_end=signal.get('entry_candle_end'), status='OPEN',
            stop=signal['initial_sl'],   # live stop; trails/break-even move it, R stays off initial_sl
            data_quality_status=VALID, last_sample_epoch=now, last_price=entry,
            current_price=entry, unrealized_pnl=0.0, unrealized_r=0.0,
            mfe_r=0.0, mae_r=0.0,
            config_snapshot=signal.get('config_snapshot', {}),
            **{k: v for k, v in (signal.get('extra') or {}).items()
               if k not in ('symbol', 'side', 'epoch')})

    @staticmethod
    def _close(pos, now, exit_price, exit_spread, reason, quality):
        buy = pos['side'] == 'BUY'
        price_pnl = (exit_price - pos['entry_price']) if buy else (pos['entry_price'] - exit_price)
        price_pnl = Simulator._blend(pos, price_pnl)
        pos.update(status='CLOSED', exit_time=now, exit_price=exit_price, exit_spread=exit_spread,
                   exit_reason=reason, duration_seconds=round(now - pos['entry_time']),
                   price_pnl=price_pnl, pnl_r=(price_pnl / pos['risk']) if pos['risk'] else None,
                   data_quality_status=quality if quality != VALID else pos['data_quality_status'],
                   current_price=exit_price, unrealized_pnl=price_pnl)
        if pos['data_quality_status'] == VALID and quality != VALID:
            pos['data_quality_status'] = quality
        return pos

    @staticmethod
    def _blend(pos, remainder_pnl):
        """Whole-position price P/L after an optional partial close (partial_fraction closed at
        partial_exit_price, the rest at the current/exit price). R stays off the initial risk."""
        f = pos.get('partial_fraction') if pos.get('partial_done') else None
        if not f:
            return remainder_pnl
        part = pos['partial_exit_price'] - pos['entry_price']
        if pos['side'] != 'BUY':
            part = -part
        return f * part + (1 - f) * remainder_pnl

    @classmethod
    def monitor(cls, pos, quote, now, max_gap):
        """Advance one observed sample. Returns (pos, closed_bool)."""
        buy = pos['side'] == 'BUY'
        last = pos.get('last_sample_epoch', now)
        gap = paused = False
        if now - last > max_gap:
            # The daily rollover break / weekend is a scheduled pause, not missing data:
            # only the time the market was OPEN without a sample counts as a data gap.
            gap = unexplained_gap_seconds(last, now, pos.get('symbol')) > max_gap
            paused = not gap
        if gap and pos['data_quality_status'] == VALID:
            pos['data_quality_status'] = DATA_GAP
        exit_basis = quote.bid if buy else quote.ask
        spread = round(quote.ask - quote.bid, quote.digits)
        pos['last_sample_epoch'] = now
        pos['last_price'] = exit_basis
        pos['current_price'] = exit_basis
        pnl = (exit_basis - pos['entry_price']) if buy else (pos['entry_price'] - exit_basis)
        pos['unrealized_pnl'] = pnl
        r = (pnl / pos['risk']) if pos['risk'] else None
        pos['unrealized_r'] = r
        if r is not None:                       # maximum favorable / adverse excursion, in R
            pos['mfe_r'] = round(max(pos.get('mfe_r', 0.0) or 0.0, r), 4)
            pos['mae_r'] = round(min(pos.get('mae_r', 0.0) or 0.0, r), 4)
        # --- optional partial close at a first target (partial_price / partial_fraction), as the
        # live executor does at TP1; be_on_partial then moves the stop to break-even. ---
        pp = pos.get('partial_price')
        if (pp is not None and not pos.get('partial_done')
                and ((exit_basis >= pp) if buy else (exit_basis <= pp))):
            pos.update(partial_done=True, partial_exit_price=exit_basis, partial_time=now)
            if pos.get('be_on_partial') and r is not None:
                pos['be_trigger_r'] = r
        if pos.get('partial_done'):
            pos['unrealized_pnl'] = cls._blend(pos, pnl)
            pos['unrealized_r'] = (pos['unrealized_pnl'] / pos['risk']) if pos['risk'] else None
        sl = pos.get('stop', pos['initial_sl'])      # trailed/break-even stop if one has been set
        tp = pos['initial_tp']                        # None for agents that exit on stop/trail only
        sl_hit = (exit_basis <= sl) if buy else (exit_basis >= sl)
        tp_hit = tp is not None and ((exit_basis >= tp) if buy else (exit_basis <= tp))
        if sl_hit and tp_hit:
            # Both levels crossed within one sample -> path cannot be verified.
            return cls._close(pos, now, sl, spread, 'AMBIGUOUS_SL_TP', UNCERTAIN), True
        if sl_hit:
            q = UNCERTAIN if gap else VALID
            if paused:
                # Price reopened beyond the stop: a real stop fills at the reopening price (slippage).
                pos['gap_fill'] = 'MARKET_PAUSE'
                return cls._close(pos, now, exit_basis, spread, 'SL', q), True
            return cls._close(pos, now, sl, spread, 'SL', q), True
        if tp_hit:
            q = UNCERTAIN if gap else VALID
            return cls._close(pos, now, tp, spread, 'TP', q), True
        end = pos.get('entry_candle_end')
        if end and now >= end:
            return cls._close(pos, now, exit_basis, spread, 'CANDLE_END', pos['data_quality_status']), True
        # --- dynamic stop management: break-even, then trailing. Tighten only, never loosen. ---
        # Driven by optional per-position fields (be_trigger_r / be_offset_points /
        # trail_activation_r / trail_distance_r); absent for agents that use a fixed SL.
        if r is not None and pos['risk']:
            d = 1 if buy else -1
            cand = sl
            be_r = pos.get('be_trigger_r')
            if be_r is not None and r >= be_r:
                be = pos['entry_price'] + d * (pos.get('be_offset_points', 0.0) or 0.0) * quote.point
                if d * (be - cand) > 0 and d * (exit_basis - be) > 0:
                    cand = be
            ta, td = pos.get('trail_activation_r'), pos.get('trail_distance_r')
            if ta is not None and td is not None and r >= ta:
                trail = exit_basis - d * td * pos['risk']
                if d * (trail - cand) > 0:
                    cand = trail
            if d * (cand - sl) > 0:
                pos['stop'] = round(cand, quote.digits)
        return pos, False


# --------------------------------------------------------------------------- stats
def compute_stats(trades: list[dict]) -> dict:
    valid = [t for t in trades if t.get('data_quality_status') == VALID]
    wins = sum(1 for t in valid if (t.get('pnl_r') or 0) > 0)
    affected = sum(1 for t in trades if t.get('data_quality_status') != VALID)
    net_r = sum((t.get('pnl_r') or 0) for t in valid)
    return dict(total_trades=len(trades), valid_trades=len(valid), wins=wins,
                losses=len(valid) - wins, win_rate=(wins / len(valid)) if valid else None,
                net_r=round(net_r, 4), affected_by_data=affected)


# --------------------------------------------------------------------------- live agent instance
class LiveAgent:
    """One running instance: strategy + symbol + config, with its own state/positions/stats."""

    def __init__(self, agent, symbol, status='PAUSED'):
        self.agent = agent
        self.symbol = symbol
        self.base_symbol = symbol
        self.status = status  # RUNNING | PAUSED
        self.id = f"{agent.strategy_id}|{symbol}|{agent.config_id}"
        self.state = {}
        self.open_positions = []
        self.last_tick_epoch = None
        self.last_signal = None
        self.feed_status = 'WAITING'


# --------------------------------------------------------------------------- engine
SCHEMA = '''
CREATE TABLE IF NOT EXISTS live_test_agents(
 id TEXT PRIMARY KEY, strategy_id TEXT, symbol TEXT, config_id TEXT,
 config TEXT, status TEXT, state TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS live_test_positions(
 trade_id TEXT PRIMARY KEY, agent_id TEXT, status TEXT, payload TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS live_test_trades(
 trade_id TEXT PRIMARY KEY, agent_id TEXT, closed_epoch REAL, payload TEXT);
CREATE INDEX IF NOT EXISTS live_test_trade_closed ON live_test_trades(closed_epoch DESC);
CREATE TABLE IF NOT EXISTS live_test_signals(
 id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, signal_id TEXT, epoch REAL, payload TEXT);
CREATE INDEX IF NOT EXISTS live_test_signal_epoch ON live_test_signals(epoch DESC);
'''


class LiveTestEngine:
    """Registry + per-cycle runner. Pure simulation over a ReadOnlyMarketData adapter."""

    def __init__(self, store, max_gap_default=120):
        self.store = store
        self.max_gap_default = max_gap_default
        self.agents: dict[str, LiveAgent] = {}
        with store.connect() as db:
            db.executescript(SCHEMA)
        self._load()

    # ---- registry ----
    def register(self, agent, symbol, status='PAUSED') -> LiveAgent:
        la = LiveAgent(agent, symbol, status)
        self.agents[la.id] = la
        self._save_agent(la)
        return la

    def set_status(self, agent_id, status):
        la = self.agents.get(agent_id)
        if not la:
            raise KeyError(agent_id)
        if status not in ('RUNNING', 'PAUSED'):
            raise ValueError('status must be RUNNING or PAUSED')
        la.status = status
        self._save_agent(la)
        return la

    # ---- persistence ----
    def _save_agent(self, la):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO live_test_agents VALUES (?,?,?,?,?,?,?,?)',
                       (la.id, la.agent.strategy_id, la.base_symbol, la.agent.config_id,
                        json.dumps(la.agent.config), la.status,
                        json.dumps(dict(state=la.state, last_tick=la.last_tick_epoch,
                                        last_signal=la.last_signal, feed=la.feed_status)),
                        time.time()))

    def _save_position(self, pos):
        with self.store.connect() as db:
            if pos['status'] == 'CLOSED':
                db.execute('DELETE FROM live_test_positions WHERE trade_id=?', (pos['trade_id'],))
                db.execute('INSERT OR REPLACE INTO live_test_trades VALUES (?,?,?,?)',
                           (pos['trade_id'], pos['agent_id'], pos.get('exit_time'),
                            json.dumps(pos, allow_nan=False)))
            else:
                db.execute('INSERT OR REPLACE INTO live_test_positions VALUES (?,?,?,?,?)',
                           (pos['trade_id'], pos['agent_id'], pos['status'],
                            json.dumps(pos, allow_nan=False), time.time()))

    def _drain_signal_log(self, la, symbol, now):
        """Persist NO_FILL / cancellation / skip records the agent appended to its state.
        These never become positions but are kept, exportable, in the signal log."""
        events = la.state.pop('_emit', None)
        if not events:
            return
        with self.store.connect() as db:
            for ev in events:
                rec = dict(ev, agent_id=la.id, strategy_id=la.agent.strategy_id,
                           config_id=la.agent.config_id, base_symbol=la.base_symbol)
                rec.setdefault('symbol', symbol)
                rec.setdefault('epoch', now)
                db.execute('INSERT INTO live_test_signals(agent_id,signal_id,epoch,payload) VALUES (?,?,?,?)',
                           (la.id, rec.get('signal_id'), rec['epoch'], json.dumps(rec, allow_nan=False)))

    def signals(self, agent_id=None, limit=None):
        with self.store.connect() as db:
            if agent_id:
                rows = db.execute('SELECT payload FROM live_test_signals WHERE agent_id=? ORDER BY epoch DESC',
                                  (agent_id,)).fetchall()
            else:
                rows = db.execute('SELECT payload FROM live_test_signals ORDER BY epoch DESC').fetchall()
        out = [json.loads(r['payload']) for r in rows]
        return out[:limit] if limit else out

    def _load(self):
        with self.store.connect() as db:
            for r in db.execute('SELECT * FROM live_test_agents'):
                cfg = json.loads(r['config'])
                agent = build_agent(r['strategy_id'], cfg)
                if agent is None:
                    continue
                la = LiveAgent(agent, r['symbol'], r['status'])
                meta = json.loads(r['state'] or '{}')
                la.state = meta.get('state', {})
                la.last_tick_epoch = meta.get('last_tick')
                la.last_signal = meta.get('last_signal')
                la.feed_status = meta.get('feed', 'WAITING')
                self.agents[la.id] = la
            for la in self.agents.values():
                rows = db.execute('SELECT payload FROM live_test_positions WHERE agent_id=?', (la.id,)).fetchall()
                la.open_positions = [json.loads(r['payload']) for r in rows]

    def _load_positions(self, la):
        with self.store.connect() as db:
            rows = db.execute('SELECT payload FROM live_test_positions WHERE agent_id=?', (la.id,)).fetchall()
        la.open_positions = [json.loads(r['payload']) for r in rows]

    def _sync(self):
        """Pick up start/pause and newly registered agents written by the dashboard server."""
        with self.store.connect() as db:
            rows = list(db.execute('SELECT * FROM live_test_agents'))
        seen = set()
        for r in rows:
            seen.add(r['id'])
            if r['id'] in self.agents:
                self.agents[r['id']].status = r['status']
            else:
                agent = build_agent(r['strategy_id'], json.loads(r['config']))
                if agent:
                    la = LiveAgent(agent, r['symbol'], r['status'])
                    self.agents[r['id']] = la
                    self._load_positions(la)
        for aid in list(self.agents):
            if aid not in seen:
                del self.agents[aid]

    # ---- per-cycle run ----
    def run(self, market, now=None):
        """Advance every RUNNING agent one sampled observation. `market` is ReadOnlyMarketData."""
        now = time.time() if now is None else now
        self._sync()
        touched = 0
        for la in list(self.agents.values()):
            if la.status != 'RUNNING':
                continue
            touched += 1
            self._run_agent(la, market, now)
        return touched

    def _run_agent(self, la, market, now):
        cfg = la.agent.config
        max_gap = cfg.get('max_gap_seconds', self.max_gap_default)
        symbol = market.resolve_symbol(la.base_symbol) if hasattr(market, 'resolve_symbol') else la.base_symbol
        if not symbol:
            la.feed_status = 'SYMBOL_UNAVAILABLE'
            self._save_agent(la)
            return
        la.symbol = symbol
        try:
            q = market.quote(symbol, now=now)
        except Exception as exc:
            la.feed_status = 'STALE_OR_CLOSED'
            la.state['_feed_note'] = str(exc)
            if isinstance(exc, MarketClosed):
                # Scheduled pause (daily rollover / weekend): no new entries, positions stay valid.
                # When prices return, monitor() checks that the market was really closed meanwhile.
                self._save_agent(la)
                return
            # Feed down: no new entries. Flag open positions as having a data gap.
            for pos in la.open_positions:
                if pos['data_quality_status'] == VALID:
                    pos['data_quality_status'] = DATA_GAP
                    self._save_position(pos)
            self._save_agent(la)
            return
        fresh = market.is_fresh(q) if hasattr(market, 'is_fresh') else True
        la.last_tick_epoch = q.time
        la.feed_status = 'LIVE' if fresh else 'STALE'
        # 1) monitor existing open positions
        still_open = []
        for pos in la.open_positions:
            updated, closed = Simulator.monitor(pos, q, now, max_gap)
            self._save_position(updated)
            if not closed:
                still_open.append(updated)
        la.open_positions = still_open
        # 2) look for a new entry (only when fresh and under the position cap)
        if fresh and len(la.open_positions) < cfg.get('max_positions', 1):
            try:
                candles = market.candles(symbol, la.agent.timeframe, 50, now=now)
                if getattr(la.agent, 'needs_market', False):
                    signal = la.agent.evaluate(now, q, candles, q.point, la.state, market=market, symbol=symbol)
                else:
                    signal = la.agent.evaluate(now, q, candles, q.point, la.state)
            except Exception as exc:
                signal = None
                la.state['_eval_note'] = str(exc)
            self._drain_signal_log(la, symbol, now)
            if signal:
                pos = Simulator.open(la, signal, q, now)
                la.open_positions.append(pos)
                la.last_signal = dict(side=signal['side'], reason=signal['signal_reason'], epoch=now)
                self._save_position(pos)
        self._save_agent(la)

    # ---- reporting ----
    def trades(self, agent_id=None, limit=None):
        with self.store.connect() as db:
            if agent_id:
                rows = db.execute('SELECT payload FROM live_test_trades WHERE agent_id=? ORDER BY closed_epoch DESC',
                                  (agent_id,)).fetchall()
            else:
                rows = db.execute('SELECT payload FROM live_test_trades ORDER BY closed_epoch DESC').fetchall()
        out = [json.loads(r['payload']) for r in rows]
        return out[:limit] if limit else out

    def report(self, now=None):
        now = time.time() if now is None else now
        agents = []
        for la in self.agents.values():
            trades = self.trades(la.id)
            agents.append(dict(
                id=la.id, strategy_id=la.agent.strategy_id, label=la.agent.label(),
                symbol=la.symbol, base_symbol=la.base_symbol, config_id=la.agent.config_id,
                strategy_version=la.agent.version, status=la.status, feed_status=la.feed_status,
                last_tick_epoch=la.last_tick_epoch, last_tick_myt=myt_iso(la.last_tick_epoch),
                last_signal=la.last_signal, config=la.agent.config,
                open_positions=[self._public_pos(p, now) for p in la.open_positions],
                stats=compute_stats(trades)))
        recent = []
        for t in self.trades(limit=1000):
            recent.append(dict(t, closed_myt=myt_iso(t.get('exit_time')), entry_myt=myt_iso(t.get('entry_time'))))
        recent_signals = []
        for s in self.signals(limit=1000):
            recent_signals.append(dict(s, epoch_myt=myt_iso(s.get('epoch'))))
        return dict(status='OK', server_epoch=now, simulation=True,
                    note='All entries, positions, exits and P/L are SIMULATED on live market data. '
                         'Read-only feed; no orders are sent to MT5.',
                    agents=agents, recent_trades=recent, recent_signals=recent_signals,
                    by_symbol=self._by_symbol())

    def _by_symbol(self):
        """Per-symbol performance roll-up (R-based, comparable across instruments)."""
        acc = {}

        def row(sym):
            return acc.setdefault(sym, dict(symbol=sym, agents=0, running=0, feed='—',
                                            signals=0, no_fill=0, closed=0, valid=0, wins=0,
                                            net_r=0.0, data_gap=0, win_rate=None))
        for la in self.agents.values():
            d = row(la.base_symbol)
            d['agents'] += 1
            if la.status == 'RUNNING':
                d['running'] += 1
                d['feed'] = la.feed_status
            st = compute_stats(self.trades(la.id))
            d['closed'] += st['total_trades']; d['valid'] += st['valid_trades']
            d['wins'] += st['wins']; d['net_r'] += st['net_r']; d['data_gap'] += st['affected_by_data']
        for s in self.signals():
            d = acc.get(s.get('base_symbol') or s.get('symbol'))
            if not d:
                continue
            d['signals'] += 1
            if s.get('event') == 'NO_FILL':
                d['no_fill'] += 1
        for d in acc.values():
            d['net_r'] = round(d['net_r'], 4)
            d['win_rate'] = (d['wins'] / d['valid']) if d['valid'] else None
        return sorted(acc.values(), key=lambda r: r['symbol'])

    @staticmethod
    def _public_pos(p, now):
        return dict(p, signal_time_myt=myt_iso(p.get('signal_time')), entry_time_myt=myt_iso(p.get('entry_time')),
                    age_seconds=round(now - p.get('entry_time', now)))

    # ---- CSV ----
    CSV_FIELDS = ['trade_id', 'strategy_id', 'strategy_version', 'config_id', 'symbol', 'timeframe',
                  'session', 'side', 'signal_reason', 'signal_time', 'trigger_price', 'entry_time',
                  'entry_price', 'initial_sl', 'initial_tp', 'entry_spread', 'exit_time', 'exit_price',
                  'exit_spread', 'exit_reason', 'duration_seconds', 'price_pnl', 'pnl_r',
                  'data_quality_status',
                  # structure columns shared across the Live Test agents (empty where N/A):
                  'signal_id', 'config_variant', 'setup', 'h4_liquidity_type', 'h4_target_price',
                  'ltf_swing_time', 'ltf_swing_level', 'sweep_time', 'sweep_extreme',
                  'fvg_high', 'fvg_low', 'fvg_confirm_time', 'fvg_midpoint', 'fill_price',
                  'ref_high', 'ref_low', 'choch_time', 'choch_level', 'target_level',
                  'mfe_r', 'mae_r']

    _CSV_TIME_FIELDS = ('signal_time', 'entry_time', 'exit_time', 'ltf_swing_time',
                        'sweep_time', 'fvg_confirm_time', 'choch_time')

    def csv_rows(self, agent_id=None):
        rows = []
        for t in self.trades(agent_id):
            r = dict(t)
            for k in self._CSV_TIME_FIELDS:
                if r.get(k) is not None:
                    r[k] = iso_utc(r.get(k))
            rows.append({k: r.get(k, '') for k in self.CSV_FIELDS})
        return rows

    # ---- signal log (NO_FILL / cancelled / skipped setups), exportable ----
    SIGNAL_CSV_FIELDS = ['epoch', 'event', 'reason', 'detail', 'strategy_id', 'config_id', 'symbol',
                         'side', 'signal_id', 'setup', 'h4_liquidity_type', 'h4_target_price',
                         'selection_reason', 'ltf_swing_time', 'ltf_swing_level', 'sweep_time',
                         'sweep_extreme', 'fvg_high', 'fvg_low', 'fvg_confirm_time', 'fvg_midpoint',
                         'ref_high', 'ref_low', 'choch_time', 'choch_level', 'target_level']
    _SIGNAL_TIME_FIELDS = ('ltf_swing_time', 'sweep_time', 'fvg_confirm_time', 'choch_time')

    def signals_csv_rows(self, agent_id=None):
        rows = []
        for s in self.signals(agent_id):
            r = dict(s)
            r['epoch'] = iso_utc(r.get('epoch'))
            for k in self._SIGNAL_TIME_FIELDS:
                if r.get(k) is not None:
                    r[k] = iso_utc(r.get(k))
            rows.append({k: r.get(k, '') for k in self.SIGNAL_CSV_FIELDS})
        return rows


# --------------------------------------------------------------------------- agent factory
def build_agent(strategy_id, config):
    if strategy_id == PrevCandleBreakoutH1.strategy_id:
        return PrevCandleBreakoutH1(config)
    if strategy_id == CounterTrendScalper.strategy_id:
        return CounterTrendScalper(config)
    if strategy_id == CmsSupportResistanceFlip.strategy_id:
        return CmsSupportResistanceFlip(config)
    if strategy_id == CmsFakeoutV1.strategy_id:
        return CmsFakeoutV1(config)
    if strategy_id == CmsSupplyDemand.strategy_id:
        return CmsSupplyDemand(config)
    if strategy_id == CmsSsr.strategy_id:
        return CmsSsr(config)
    if strategy_id == 'H4_LIQ_SWEEP_FVG':
        try:
            from live_test_h4_liq import H4LiquiditySweepFVG
            return H4LiquiditySweepFVG(config)
        except Exception:
            return None
    if strategy_id == 'ORB_BREAKOUT_RETEST':
        try:
            from live_test_orb import ORBAgent
            return ORBAgent(config)
        except Exception:
            return None
    if strategy_id == 'CRT_H1_M5':
        try:
            from live_test_crt import CrtH1M5
            return CrtH1M5(config)
        except Exception:
            return None
    if strategy_id == 'H4_SWING_SWEEP_FVG_ENGULF':
        try:
            from live_test_h4_swing import H4SwingSweepFVGEngulf
            return H4SwingSweepFVGEngulf(config)
        except Exception:
            return None
    if strategy_id == 'NY_SWEEP_LONDON':
        try:
            from live_test_ny_london import NYSweepLondon
            return NYSweepLondon(config)
        except Exception:
            return None
    if strategy_id == 'INVERSE_FVG_RETEST':
        try:
            from live_test_ifvg import InverseFvgRetest
            return InverseFvgRetest(config)
        except Exception:
            return None
    if strategy_id == 'MOMENTUM_M5_M1':
        try:
            from live_test_momentum import MomentumM5M1
            return MomentumM5M1(config)
        except Exception:
            return None
    if strategy_id == 'ADVOCATE_EMA_PULLBACK':
        try:
            from live_test_advocate import AdvocateEmaPullback
            return AdvocateEmaPullback(config)
        except Exception:
            return None
    if strategy_id == 'AMD_PO3':
        try:
            from live_test_amd import AmdPowerOfThree
            return AmdPowerOfThree(config)
        except Exception:
            return None
    if strategy_id == 'LIVE_BBMA':
        try:
            from live_test_bbma import LiveBBMA
            return LiveBBMA(config)
        except Exception:
            return None
    if strategy_id == 'LIVE_CONFLUENCE':
        try:
            from live_test_confluence import LiveConfluence
            return LiveConfluence(config)
        except Exception:
            return None
    if strategy_id == 'LIVE_ORB':
        try:
            from live_test_live_orb import LiveORB
            return LiveORB(config)
        except Exception:
            return None
    return None


CORE_AGENTS = (PrevCandleBreakoutH1, CounterTrendScalper, CmsSupportResistanceFlip,
               CmsFakeoutV1, CmsSupplyDemand, CmsSsr)


def live_test_symbols(cfg):
    """Pairs the default agents run on. `live_test_symbols` in config overrides; otherwise the
    dashboard's configured pairs (`symbols`); otherwise XAUUSD. XAUUSD is listed first, de-duped."""
    cfg = cfg or {}
    raw = cfg.get('live_test_symbols') or cfg.get('symbols') or ['XAUUSD']
    out = []
    for s in (['XAUUSD'] if 'XAUUSD' in raw else []) + list(raw):
        if isinstance(s, str) and s and s not in out:
            out.append(s)
    return out


def h4_liq_timeframes(cfg):
    """Entry timeframes to register the H4 Liquidity Sweep + FVG agent on. Baseline is M5 for
    every symbol; config `h4_liq.timeframes` can add M15 and/or M1 as separate agents."""
    tfs = ((cfg or {}).get('h4_liq') or {}).get('timeframes') or ['M5']
    out = []
    for tf in tfs:
        if tf in ('M1', 'M5', 'M15') and tf not in out:
            out.append(tf)
    return out or ['M5']


def ensure_default_agents(engine, cfg=None):
    """Register the starting agents (paused) if not already present. The core agents run on
    every configured pair. The H4 Liquidity Sweep + FVG agent runs on every pair too, at the
    M5 baseline (plus any extra timeframes in cfg.h4_liq.timeframes). ORB (if available) stays
    on XAUUSD, its configured instrument."""
    cfg = cfg or {}
    h4_overrides = (cfg.get('h4_liq') or {}).get('symbol_overrides') or {}

    def _reg(agent, base):
        if agent is None:
            return
        aid = f"{agent.strategy_id}|{base}|{agent.config_id}"
        if aid not in engine.agents:
            engine.register(agent, base, 'PAUSED')

    # The all-symbol Live Test agents (one baseline config each; per-symbol overrides via
    # cfg[<key>].symbol_overrides). H4 Liquidity Sweep adds its optional timeframes.
    ALL_SYMBOL_AGENTS = ('CRT_H1_M5', 'H4_SWING_SWEEP_FVG_ENGULF', 'NY_SWEEP_LONDON', 'INVERSE_FVG_RETEST')
    _key = {'CRT_H1_M5': 'crt', 'H4_SWING_SWEEP_FVG_ENGULF': 'h4_swing',
            'NY_SWEEP_LONDON': 'ny_london', 'INVERSE_FVG_RETEST': 'inverse_fvg'}
    for base in live_test_symbols(cfg):
        for cls in CORE_AGENTS:
            _reg(cls(), base)
        for tf in h4_liq_timeframes(cfg):
            _reg(build_agent('H4_LIQ_SWEEP_FVG', {'entry_timeframe': tf, **(h4_overrides.get(base) or {})}), base)
        for sid in ALL_SYMBOL_AGENTS:
            ov = (cfg.get(_key[sid]) or {}).get('symbol_overrides') or {}
            _reg(build_agent(sid, ov.get(base) or {}), base)
    # Gold ORB 09:30 NY is XAUUSD-only (SELL baseline).
    _reg(build_agent('ORB_BREAKOUT_RETEST', cfg.get('orb') or {}), 'XAUUSD')
    # Ported XAUUSD paper desks (gold-tuned ATR/sizing), XAUUSD-only like ORB.
    _reg(build_agent('MOMENTUM_M5_M1', cfg.get('momentum') or {}), 'XAUUSD')
    _reg(build_agent('ADVOCATE_EMA_PULLBACK', cfg.get('advocate') or {}), 'XAUUSD')
    # AMD / Power-of-Three (Market Cycle Guide model C1 + A1/B1 comparison triggers),
    # 06:00-10:00 UTC window, XAUUSD-only. One agent per entry model so the three can be
    # compared side by side as the guide intends.
    amd_cfg = cfg.get('amd') or {}
    amd_models = amd_cfg.get('models') or ['C1', 'A1', 'B1']
    for _m in amd_models:
        _reg(build_agent('AMD_PO3', {**{k: v for k, v in amd_cfg.items() if k != 'models'},
                                     'model': _m}), 'XAUUSD')
    # The live-execution strategies, simulated, on the current live dashboard settings.
    sync_live_strategy_agents(engine, cfg)


LIVE_STRATEGY_IDS = ('LIVE_BBMA', 'LIVE_CONFLUENCE', 'LIVE_ORB')


def live_strategy_agents(cfg, store=None):
    """[(agent, base_symbol)] for the live-execution strategies: BBMA as one agent per setup and pair,
    Five-factor Confluence as one agent per pair (each on its own live pair list) and the live ORB on
    XAUUSD. Analysis values come from config.json; management, the BBMA TP mode, the Confluence minimum
    score and the ORB settings come from the live dashboard settings in `store` (when given)."""
    from live_test_bbma import LiveBBMA, SETUP_LABELS, dashboard_settings
    from live_test_confluence import LiveConfluence
    from live_test_live_orb import LiveORB
    cfg = cfg or {}
    settings = dashboard_settings(store, cfg) if store is not None else None
    out = []
    for base in live_test_symbols({'symbols': cfg['symbols']}) if cfg.get('symbols') else []:
        for setup in SETUP_LABELS:
            out.append((LiveBBMA.from_live_config(cfg, setup, settings), base))
    conf_symbols = (cfg.get('confluence') or {}).get('symbols') or cfg.get('symbols') or []
    for base in live_test_symbols({'symbols': conf_symbols}) if conf_symbols else []:
        out.append((LiveConfluence.from_live_config(cfg, settings), base))
    if 'XAUUSD' in ((cfg.get('orb') or {}).get('symbols') or ['XAUUSD']):
        out.append((LiveORB.from_live_config(cfg, settings), 'XAUUSD'))
    return out


def _live_slot(strategy_id, base, config):
    """One slot per live strategy x pair x BBMA setup; settings versions of a slot replace each other."""
    return strategy_id, base, config.get('setup', '')


def sync_live_strategy_agents(engine, cfg):
    """Keep the live-strategy agents on the CURRENT live dashboard settings.

    Unchanged settings give the same configs and ids, so nothing happens. When a setting changes,
    each affected slot gets a new agent version; it starts RUNNING if the version it replaces was
    running, and the replaced version is paused with its trades kept (it shows how the old settings
    did). Returns the ids that were registered or paused. Never raises: a failure here must not stop
    the bot or the other agents."""
    try:
        engine._sync()                               # fresh Start/Pause states from the dashboard
        wanted = {}
        for agent, base in live_strategy_agents(cfg, engine.store):
            wanted[f"{agent.strategy_id}|{base}|{agent.config_id}"] = (agent, base)
    except Exception:
        return []
    current = [la for la in engine.agents.values() if la.agent.strategy_id in LIVE_STRATEGY_IDS]
    changed = []
    for aid, (agent, base) in wanted.items():
        if aid in engine.agents:
            continue
        slot = _live_slot(agent.strategy_id, base, agent.config)
        running = any(la.status == 'RUNNING' for la in current
                      if _live_slot(la.agent.strategy_id, la.base_symbol, la.agent.config) == slot)
        engine.register(agent, base, 'RUNNING' if running else 'PAUSED')
        changed.append(aid)
    for la in current:
        if la.id not in wanted and la.status == 'RUNNING':
            engine.set_status(la.id, 'PAUSED')       # superseded by the current-settings version
            changed.append(la.id)
    return changed
