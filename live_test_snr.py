"""S&R Zones: support and resistance zones from clustered H4 swing points, traded on M15
closes. Live Test agent, fully SIMULATED.

Zones (rebuilt on each new closed H4 candle):
  * confirmed H4 swing highs AND lows over `h4_lookback` candles (left/right `pivot_left/right`)
    are grouped when they sit within `merge_atr` x H4 ATR of each other; a group with at least
    `min_touches` swing points is a zone, [lowest point, highest point], widened to at least
    `min_width_atr` x H4 ATR around its centre;
  * a zone below price is support, above price is resistance (role reversal is automatic);
  * `visits` = separate H4 runs that traded into the zone after it formed; a zone visited more
    than `max_visits` times is treated as used up.

Three modes (one agent per mode, `mode` in the config):
  BOUNCE   price trades into a zone and an M15 candle closes back out of it with a rejection
           wick (>= `wick_ratio` of the candle) or an engulfing body -> trade away from the zone.
  RETEST   an H4 candle CLOSES through a zone by >= `break_atr` x H4 ATR (previous H4 close on
           the other side) -> within `retest_hours`, an M15 candle dips back into the zone and
           closes back out in the breakout direction -> trade the breakout direction.
  FAKEOUT  an M15 candle pokes through a zone's far edge and closes back inside/through it ->
           trade back the other way.

Stops and targets: SL beyond the zone's far edge (or the trigger candle's extreme, whichever is
further) plus `sl_buffer_atr` x M15 ATR. TP = the near edge of the next opposing zone; no zone
there -> skipped (NO_TARGET). Skipped when reward < `min_rr`, or when the stop is smaller than
max(`min_sl_atr` x M15 ATR, `min_sl_spreads` x current spread) or larger than `max_sl_atr`.
One trade per zone and direction until price moves `reset_atr` x H4 ATR away from that zone.
Optional regime filter (Market Scout's regime() from closed H1/H4 candles, daily ranges built
from H4): BOUNCE only in MEAN_REVERTING / MIXED, RETEST only in TRENDING / HIGH_VOL, FAKEOUT in
anything but TRENDING; never in CRISIS. The regime is recorded on every entry either way.
Time exit `time_exit_bars` M15 candles after the fill.

READ-ONLY: a ReadOnlyMarketData (or Quant HistoricalMarket) adapter only; never an order API.
Returns a signal dict and appends SKIP / NO_FILL / CANCELLED records to state['_emit'].
These parameters are a first, testable model, not a proven rule set.
"""
from __future__ import annotations
import hashlib

from live_test import atr, config_id, dist, engulfs, swing_points

H1, H4 = 'H1', 'H4'
M15_DUR, H4_DUR = 900, 14400
MODES = ('BOUNCE', 'RETEST', 'FAKEOUT')
REGIME_OK = dict(BOUNCE=('MEAN_REVERTING', 'MIXED'), RETEST=('TRENDING', 'HIGH_VOL'),
                 FAKEOUT=('MEAN_REVERTING', 'MIXED', 'HIGH_VOL'))
MODE_NAMES = dict(BOUNCE='Bounce', RETEST='Break & retest', FAKEOUT='Fakeout')


# --------------------------------------------------------------------------- zones (pure)
def build_zones(h4, left=3, right=3, merge_atr=0.35, min_touches=2, min_width_atr=0.25, atr_period=14):
    """Zones from CLOSED H4 candles: [{lo, hi, mid, touches, first, last, visits, key}], low to high."""
    a = atr(h4, atr_period)
    if not a or len(h4) < left + right + 2:
        return [], a
    highs, lows = swing_points(h4, left, right)

    def distinct(piv):                    # a flat top/bottom (equal extremes side by side) is ONE touch
        out = []
        for i, p, t in piv:
            if out and i - out[-1][0] <= max(left, right):
                continue
            out.append((i, p, t))
        return out
    pivots = sorted([(p, t) for _i, p, t in distinct(highs) + distinct(lows)])
    groups, cur = [], []
    for p, t in pivots:
        if cur and p - (sum(x for x, _ in cur) / len(cur)) > merge_atr * a:
            groups.append(cur)
            cur = []
        cur.append((p, t))
    if cur:
        groups.append(cur)
    zones = []
    for g in groups:
        if len(g) < min_touches:
            continue
        lo, hi = min(p for p, _ in g), max(p for p, _ in g)
        mid = (lo + hi) / 2
        half = max((hi - lo) / 2, min_width_atr * a / 2)
        lo, hi = mid - half, mid + half
        first, last = min(t for _, t in g), max(t for _, t in g)
        # separate H4 runs that traded into the zone after it formed (its last swing confirmed)
        visits, inside = 0, False
        for b in h4:
            if b.time <= last + right * H4_DUR:
                continue
            hit = b.low <= hi and b.high >= lo
            if hit and not inside:
                visits += 1
            inside = hit
        zones.append(dict(lo=lo, hi=hi, mid=mid, touches=len(g), first=first, last=last, visits=visits,
                          key=f'{int(first)}:{len(g)}'))
    return sorted(zones, key=lambda z: z['lo']), a


def next_target(zones, side, beyond, exclude=None):
    """Near edge of the closest zone fully beyond `beyond` in the trade direction (None if none)."""
    if side == 'BUY':
        cands = [z['lo'] for z in zones if z['lo'] > beyond and z is not exclude]
        return min(cands) if cands else None
    cands = [z['hi'] for z in zones if z['hi'] < beyond and z is not exclude]
    return max(cands) if cands else None


def rejection(c, prev, side, zone, wick_ratio):
    """BOUNCE trigger: the closed candle traded into the zone and closed back out of it, with a
    rejection wick or an engulfing body. BUY = off support (closes above the zone), SELL mirrors."""
    rng = c.high - c.low
    if rng <= 0:
        return False
    came_from = min(c.open, prev.close) if prev is not None else c.open
    if side == 'BUY':                     # price came down into support (not a breakout from below)
        if not (came_from >= zone['lo'] and c.low <= zone['hi'] and c.low >= zone['lo'] - (zone['hi'] - zone['lo'])
                and c.close > zone['hi']):
            return False
        wick = min(c.open, c.close) - c.low
    else:
        came_from = max(c.open, prev.close) if prev is not None else c.open
        if not (came_from <= zone['hi'] and c.high >= zone['lo'] and c.high <= zone['hi'] + (zone['hi'] - zone['lo'])
                and c.close < zone['lo']):
            return False
        wick = c.high - max(c.open, c.close)
    return wick >= wick_ratio * rng or (prev is not None and engulfs(c, prev, side))


def daily_from_h4(h4):
    """(t, o, h, l, c) daily bars grouped by UTC day; the last one is the (possibly forming) day."""
    days = {}
    for b in h4:
        d = int(b.time // 86400)
        if d not in days:
            days[d] = [d * 86400, b.open, b.high, b.low, b.close]
        else:
            x = days[d]
            x[2], x[3], x[4] = max(x[2], b.high), min(x[3], b.low), b.close
    return [tuple(v) for _, v in sorted(days.items())]


# --------------------------------------------------------------------------- agent
class SnrZones:
    strategy_id = 'SNR_ZONES'
    version = 'snr_zones@1'
    needs_market = True

    DEFAULTS = dict(
        mode='BOUNCE', regime_filter=False, entry_timeframe='M15',
        h4_lookback=420, pivot_left=3, pivot_right=3, merge_atr=0.35, min_touches=2,
        min_width_atr=0.25, max_visits=4, zone_window=120,          # zones from the last 120 H4 candles
        wick_ratio=0.5, break_atr=0.25, retest_hours=24, fake_pierce_atr=0.05,
        atr_period=14, sl_buffer_points=None, sl_buffer_atr=0.10,
        min_sl_atr=0.5, max_sl_atr=6.0, min_sl_points=None, max_sl_points=None, min_sl_spreads=3.0,
        min_rr=1.5, reset_atr=1.0, time_exit_bars=12, max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        if self.config['mode'] not in MODES:
            raise ValueError(f"S&R Zones: unknown mode {self.config['mode']!r}")
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']
        self._cache = None                     # (symbol, last H4 time) -> zones; rebuilt after a restart

    def label(self):
        return f"S&R Zones · {MODE_NAMES[self.config['mode']]}" + (' · regime filter' if self.config['regime_filter'] else '')

    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    # ---- market reads ----
    def _zones(self, market, symbol, now):
        c = self.config
        h4 = market.candles(symbol, H4, c['h4_lookback'], now=now)
        if not h4:
            return [], None, h4
        key = (symbol, h4[-1].time)
        if not self._cache or self._cache[0] != key:
            zones, a = build_zones(h4[-c['zone_window']:], c['pivot_left'], c['pivot_right'], c['merge_atr'],
                                   c['min_touches'], c['min_width_atr'], c['atr_period'])
            self._cache = (key, zones, a)
        return self._cache[1], self._cache[2], h4

    def _regime(self, market, symbol, now, h4):
        try:
            import market_scout
            h1 = market.candles(symbol, H1, 120, now=now)
            return market_scout.regime(h1, h4[-150:], daily_from_h4(h4), closed=True)['label']
        except Exception:
            return None

    # ---- main ----
    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        if market is None or not candles or len(candles) < 3:
            return None
        try:
            zones, a4, h4 = self._zones(market, symbol, now)
        except Exception as exc:
            state['_feed_note'] = f'H4 data unavailable: {exc}'
            return None
        if not a4:
            return None
        ref = (quote.bid + quote.ask) / 2.0
        # forget used zones once price has moved well away from them
        used = state.setdefault('used', {})
        for k in [k for k, v in used.items() if abs(ref - v['mid']) > c['reset_atr'] * a4]:
            used.pop(k)
        if c['mode'] == 'RETEST':
            self._arm_retest(state, zones, a4, h4, symbol, now, quote.digits)
        if candles[-1].time == state.get('last_candle_time'):
            return None
        state['last_candle_time'] = candles[-1].time
        last, prev = candles[-1], candles[-2]
        trig = self._trigger(state, zones, a4, last, prev, symbol, now, quote.digits)
        if not trig:
            return None
        return self._fill(trig, zones, a4, h4, quote, candles, point, state, market, symbol, now)

    def _arm_retest(self, state, zones, a4, h4, symbol, now, digits):
        """On each new closed H4: a close through a zone arms a retest (one at a time, newest wins)."""
        c = self.config
        arm = state.get('retest')
        if arm and now > arm['expires']:
            self._emit(state, 'NO_FILL', reason='RETEST_TIMEOUT', symbol=symbol, side=arm['side'], epoch=now,
                       setup='SNR_RETEST', zone_lo=arm['lo'], zone_hi=arm['hi'])
            state['retest'] = arm = None
        if len(h4) < 2 or h4[-1].time == state.get('last_h4_time'):
            return
        state['last_h4_time'] = h4[-1].time
        b, pb = h4[-1], h4[-2]
        for z in zones:
            up = b.close > z['hi'] + c['break_atr'] * a4 and pb.close <= z['hi']
            down = b.close < z['lo'] - c['break_atr'] * a4 and pb.close >= z['lo']
            if up or down:
                state['retest'] = dict(side='BUY' if up else 'SELL', lo=round(z['lo'], digits), hi=round(z['hi'], digits),
                                       mid=z['mid'], key=z['key'], touches=z['touches'], visits=z['visits'],
                                       break_time=b.time + H4_DUR, expires=b.time + H4_DUR + c['retest_hours'] * 3600)

    def _trigger(self, state, zones, a4, last, prev, symbol, now, digits):
        """(side, zone, extreme, setup) when the closed M15 candle triggers this agent's mode."""
        c = self.config
        mode = c['mode']
        if mode == 'RETEST':
            arm = state.get('retest')
            if not arm:
                return None
            buy = arm['side'] == 'BUY'
            if (last.close < arm['lo']) if buy else (last.close > arm['hi']):
                self._emit(state, 'CANCELLED', reason='ZONE_REBROKEN', symbol=symbol, side=arm['side'], epoch=now,
                           setup='SNR_RETEST', zone_lo=arm['lo'], zone_hi=arm['hi'])
                state['retest'] = None
                return None
            dipped = last.low <= arm['hi'] if buy else last.high >= arm['lo']
            held = (last.close > arm['hi'] and last.close > last.open) if buy else (last.close < arm['lo'] and last.close < last.open)
            if not (dipped and held):
                return None
            state['retest'] = None
            z = dict(lo=arm['lo'], hi=arm['hi'], mid=arm['mid'], key=arm['key'], touches=arm['touches'], visits=arm['visits'])
            return arm['side'], z, (last.low if buy else last.high), 'SNR_RETEST'
        best = None
        for z in zones:
            if z['visits'] > c['max_visits']:
                continue
            for side in ('BUY', 'SELL'):
                if mode == 'BOUNCE':
                    ok = rejection(last, prev, side, z, c['wick_ratio'])
                else:                                    # FAKEOUT: poke through the far edge, close back
                    pierce = c['fake_pierce_atr'] * a4
                    ok = (last.open >= z['lo'] and last.low < z['lo'] - pierce and last.close > z['lo']) if side == 'BUY' else \
                         (last.open <= z['hi'] and last.high > z['hi'] + pierce and last.close < z['hi'])
                if ok and (best is None or z['touches'] > best[1]['touches']):
                    best = (side, z, last.low if side == 'BUY' else last.high, f'SNR_{mode}')
        return best

    def _fill(self, trig, zones, a4, h4, quote, candles, point, state, market, symbol, now):
        c = self.config
        side, z, extreme, setup = trig
        buy = side == 'BUY'
        key = f"{z['key']}:{side}"
        fields = dict(symbol=symbol, side=side, epoch=now, setup=setup, zone_lo=round(z['lo'], quote.digits),
                      zone_hi=round(z['hi'], quote.digits), zone_touches=z['touches'], zone_visits=z['visits'])

        def skip(reason, **kw):
            self._emit(state, 'SKIP', reason=reason, **fields, **kw)
            return None
        if key in state.get('used', {}):
            return skip('ZONE_USED')
        regime = self._regime(market, symbol, now, h4)
        fields['regime'] = regime
        if c['regime_filter'] and (regime == 'CRISIS' or regime not in REGIME_OK[c['mode']]):
            return skip('REGIME_FILTERED')
        entry = quote.ask if buy else quote.bid
        a = atr(candles, c['atr_period'])
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or min_sl is None or max_sl is None:
            return None
        min_sl = max(min_sl, c['min_sl_spreads'] * max(quote.ask - quote.bid, 0.0))
        far = min(z['lo'], extreme) if buy else max(z['hi'], extreme)
        sl = far - buf if buy else far + buf
        risk = abs(entry - sl)
        if not (min_sl <= risk <= max_sl):
            return skip('RISK_OUT_OF_BOUNDS', risk=round(risk, quote.digits), min_risk=round(min_sl, quote.digits))
        own = next((x for x in zones if x['key'] == z['key']), None)
        tp = next_target(zones, side, max(entry, z['hi']) if buy else min(entry, z['lo']), exclude=own)
        if tp is None:
            return skip('NO_TARGET')
        if not ((sl < entry < tp) if buy else (tp < entry < sl)):
            return skip('INVALID_LEVELS')
        rr = abs(tp - entry) / risk if risk > 0 else 0.0
        if rr < c['min_rr']:
            return skip('RR_TOO_LOW', rr=round(rr, 2))
        state.setdefault('used', {})[key] = dict(mid=z['mid'], time=now)
        sid = hashlib.sha1(f"{symbol}:SNR:{c['mode']}:{key}:{int(candles[-1].time)}".encode()).hexdigest()[:16]
        extra = dict(fields, signal_id=sid, fill_price=round(entry, quote.digits), target_level=round(tp, quote.digits),
                     planned_rr=round(rr, 2), config_variant=c['mode'] + ('+REGIME' if c['regime_filter'] else ''))
        why = dict(SNR_BOUNCE='rejection off', SNR_RETEST='retest of broken', SNR_FAKEOUT='fakeout of')[setup]
        return dict(side=side, signal_reason=f"S&R {side}: {why} {'support' if buy else 'resistance'} zone "
                                             f"{fields['zone_lo']}-{fields['zone_hi']} ({z['touches']} touches)",
                    signal_time=candles[-1].time + M15_DUR, trigger_price=(quote.bid + quote.ask) / 2.0,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits), session=None,
                    timeframe=self.timeframe, entry_candle_end=float(now + c['time_exit_bars'] * M15_DUR),
                    config_snapshot=dict(self.config), extra=extra)
