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
        sl_buffer_points=50, max_sl_points=800, tp_points=600,
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
        buf = c['sl_buffer_points'] * point
        if side == 'BUY':
            sl = prev.low - buf
            tp = entry + c['tp_points'] * point
        else:
            sl = prev.high + buf
            tp = entry - c['tp_points'] * point
        # Validate SL/TP direction and distance (points). Config values are not copied from a PDF pip example.
        risk = abs(entry - sl)
        max_sl = c['max_sl_points'] * point
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
        tolerance_points=50, target_points=300,
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
        tol = c['tolerance_points'] * point
        tgt = c['target_points'] * point
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
        swing_left=2, swing_right=2, break_buffer_points=80, sl_buffer_points=60,
        protect_lookback=6, reward_rr=2.0, min_sl_points=80, max_sl_points=1500,
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
        highs, lows = swing_points(candles, c['swing_left'], c['swing_right'])
        last = candles[-1]
        bbuf = c['break_buffer_points'] * point
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
        buf = c['sl_buffer_points'] * point
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
        min_sl, max_sl = c['min_sl_points'] * point, c['max_sl_points'] * point
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
        swing_left=2, swing_right=2, pierce_buffer_points=50, sl_buffer_points=60,
        reward_rr=2.0, min_sl_points=80, max_sl_points=1500,
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
        pbuf = c['pierce_buffer_points'] * point
        side = level = None
        if highs and last.high > highs[-1][1] + pbuf and last.close < highs[-1][1] and last.time > highs[-1][2]:
            side, level = 'SELL', highs[-1][1]
        elif lows and last.low < lows[-1][1] - pbuf and last.close > lows[-1][1] and last.time > lows[-1][2]:
            side, level = 'BUY', lows[-1][1]
        if not side:
            return None
        buf = c['sl_buffer_points'] * point
        entry = quote.ask if side == 'BUY' else quote.bid
        if side == 'SELL':
            sl = last.high + buf
            risk = sl - entry
            tp = entry - c['reward_rr'] * risk
        else:
            sl = last.low - buf
            risk = entry - sl
            tp = entry + c['reward_rr'] * risk
        min_sl, max_sl = c['min_sl_points'] * point, c['max_sl_points'] * point
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
        impulse_body_points=150, base_max=3, base_range_points=120, scan=20,
        sl_buffer_points=60, reward_rr=2.0, min_sl_points=80, max_sl_points=2000,
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
        imp_body = c['impulse_body_points'] * point
        base_rng = c['base_range_points'] * point
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
        buf = c['sl_buffer_points'] * point
        entry = quote.ask if side == 'BUY' else quote.bid
        if side == 'BUY':
            sl = zone['low'] - buf
            risk = entry - sl
            tp = entry + c['reward_rr'] * risk
        else:
            sl = zone['high'] + buf
            risk = sl - entry
            tp = entry - c['reward_rr'] * risk
        min_sl, max_sl = c['min_sl_points'] * point, c['max_sl_points'] * point
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
        swing_left=2, swing_right=2, min_touches=2, cluster_points=120, sl_buffer_points=80,
        reward_rr=2.0, min_sl_points=80, max_sl_points=1200, session_enabled=False,
        session_start_hour=0, session_end_hour=24, session_tz='America/New_York',
        max_gap_seconds=120, max_positions=1)

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
        highs, lows = swing_points(candles, c['swing_left'], c['swing_right'])
        tol = c['cluster_points'] * point
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
        buf = c['sl_buffer_points'] * point
        entry = quote.ask if side == 'BUY' else quote.bid
        if side == 'SELL':
            sl = level + buf
            risk = sl - entry
            tp = entry - c['reward_rr'] * risk
        else:
            sl = level - buf
            risk = entry - sl
            tp = entry + c['reward_rr'] * risk
        min_sl, max_sl = c['min_sl_points'] * point, c['max_sl_points'] * point
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
            data_quality_status=VALID, last_sample_epoch=now, last_price=entry,
            current_price=entry, unrealized_pnl=0.0, unrealized_r=0.0,
            config_snapshot=signal.get('config_snapshot', {}))

    @staticmethod
    def _close(pos, now, exit_price, exit_spread, reason, quality):
        buy = pos['side'] == 'BUY'
        price_pnl = (exit_price - pos['entry_price']) if buy else (pos['entry_price'] - exit_price)
        pos.update(status='CLOSED', exit_time=now, exit_price=exit_price, exit_spread=exit_spread,
                   exit_reason=reason, duration_seconds=round(now - pos['entry_time']),
                   price_pnl=price_pnl, pnl_r=(price_pnl / pos['risk']) if pos['risk'] else None,
                   data_quality_status=quality if quality != VALID else pos['data_quality_status'],
                   current_price=exit_price, unrealized_pnl=price_pnl)
        if pos['data_quality_status'] == VALID and quality != VALID:
            pos['data_quality_status'] = quality
        return pos

    @classmethod
    def monitor(cls, pos, quote, now, max_gap):
        """Advance one observed sample. Returns (pos, closed_bool)."""
        buy = pos['side'] == 'BUY'
        gap = (now - pos.get('last_sample_epoch', now)) > max_gap
        if gap and pos['data_quality_status'] == VALID:
            pos['data_quality_status'] = DATA_GAP
        exit_basis = quote.bid if buy else quote.ask
        spread = round(quote.ask - quote.bid, quote.digits)
        pos['last_sample_epoch'] = now
        pos['last_price'] = exit_basis
        pos['current_price'] = exit_basis
        pnl = (exit_basis - pos['entry_price']) if buy else (pos['entry_price'] - exit_basis)
        pos['unrealized_pnl'] = pnl
        pos['unrealized_r'] = (pnl / pos['risk']) if pos['risk'] else None
        sl, tp = pos['initial_sl'], pos['initial_tp']
        sl_hit = (exit_basis <= sl) if buy else (exit_basis >= sl)
        tp_hit = (exit_basis >= tp) if buy else (exit_basis <= tp)
        if sl_hit and tp_hit:
            # Both levels crossed within one sample -> path cannot be verified.
            return cls._close(pos, now, sl, spread, 'AMBIGUOUS_SL_TP', UNCERTAIN), True
        if sl_hit:
            q = UNCERTAIN if gap else VALID
            return cls._close(pos, now, sl, spread, 'SL', q), True
        if tp_hit:
            q = UNCERTAIN if gap else VALID
            return cls._close(pos, now, tp, spread, 'TP', q), True
        end = pos.get('entry_candle_end')
        if end and now >= end:
            return cls._close(pos, now, exit_basis, spread, 'CANDLE_END', pos['data_quality_status']), True
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
                signal = la.agent.evaluate(now, q, candles, q.point, la.state)
            except Exception as exc:
                signal = None
                la.state['_eval_note'] = str(exc)
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
        return dict(status='OK', server_epoch=now, simulation=True,
                    note='All entries, positions, exits and P/L are SIMULATED on live market data. '
                         'Read-only feed; no orders are sent to MT5.',
                    agents=agents, recent_trades=recent)

    @staticmethod
    def _public_pos(p, now):
        return dict(p, signal_time_myt=myt_iso(p.get('signal_time')), entry_time_myt=myt_iso(p.get('entry_time')),
                    age_seconds=round(now - p.get('entry_time', now)))

    # ---- CSV ----
    CSV_FIELDS = ['trade_id', 'strategy_id', 'strategy_version', 'config_id', 'symbol', 'timeframe',
                  'session', 'side', 'signal_reason', 'signal_time', 'trigger_price', 'entry_time',
                  'entry_price', 'initial_sl', 'initial_tp', 'entry_spread', 'exit_time', 'exit_price',
                  'exit_spread', 'exit_reason', 'duration_seconds', 'price_pnl', 'pnl_r',
                  'data_quality_status']

    def csv_rows(self, agent_id=None):
        rows = []
        for t in self.trades(agent_id):
            r = dict(t)
            for k in ('signal_time', 'entry_time', 'exit_time'):
                r[k] = iso_utc(r.get(k))
            rows.append({k: r.get(k, '') for k in self.CSV_FIELDS})
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
    if strategy_id == 'ORB_BREAKOUT_RETEST':
        try:
            from live_test_orb import ORBAgent
            return ORBAgent(config)
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


def ensure_default_agents(engine, cfg=None):
    """Register the starting agents (paused) if not already present. The six core agents run on
    every configured pair; ORB (if available) stays on XAUUSD, its configured instrument."""
    cfg = cfg or {}
    for base in live_test_symbols(cfg):
        for cls in CORE_AGENTS:
            agent = cls()
            aid = f"{agent.strategy_id}|{base}|{agent.config_id}"
            if aid not in engine.agents:
                engine.register(agent, base, 'PAUSED')
    orb = build_agent('ORB_BREAKOUT_RETEST', cfg.get('orb') or {})
    if orb is not None:
        aid = f"{orb.strategy_id}|XAUUSD|{orb.config_id}"
        if aid not in engine.agents:
            engine.register(orb, 'XAUUSD', 'PAUSED')
