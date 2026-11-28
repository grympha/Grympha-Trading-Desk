"""Elliott Wave impulse entries (wave 3 and wave 5) from an ATR zigzag on closed H1 candles.
Live Test agent, fully SIMULATED.

Only the OBJECTIVE core of Elliott Wave is used: swings come from a zigzag (a swing is confirmed once
price reverses from it by `zz_atr` x H1 ATR), and a count must pass the three hard rules plus
Fibonacci depth checks. The wave in progress is the zigzag's current, unconfirmed leg; the trade is
taken when that leg turns back by `confirm_atr` x ATR (a bullish/bearish H1 close).

  WAVE3 (BUY; SELL mirrors): confirmed swings P0 (low, wave 1 start), P1 (high, wave 1 end); the
        current leg falls from P1 to its low P2 (wave 2) and turns up.
        Rule 1: P2 must stay above P0. Wave 2 depth (P1-P2)/(P1-P0) within [w2_min, w2_max].
        SL below P0 (where rule 1 says the count is wrong) - buffer; TP = P2 + w3_target x wave 1.
  WAVE5 (BUY; SELL mirrors): confirmed swings P0 L, P1 H, P2 L, P3 H; the current leg falls from P3
        to its low P4 (wave 4) and turns up.
        Rule 1: P2 > P0. Rule 2: wave 3 longer than wave 1 (so it can never end up the shortest).
        Rule 3: P4 above P1 (wave 4 never enters wave 1's range). Wave 4 depth (P3-P4)/(P3-P2)
        within [w4_min, w4_max]. SL below P4 - buffer; TP = P4 + w5_target x wave 1.

Shared: minimum reward `min_rr`; stop between max(`min_sl_atr` x ATR, `min_sl_spreads` x spread) and
`max_sl_atr` x ATR; one trade per wave count; time exit `time_exit_hours`; the market regime at entry
is recorded (optional `regime_filter` skips CRISIS and MEAN_REVERTING). Every rejected count is
logged with the rule it failed.

READ-ONLY: a ReadOnlyMarketData (or Quant HistoricalMarket) adapter only; never an order API.
These settings are a first, testable model of common Elliott Wave teaching, not a proven rule set.
"""
from __future__ import annotations
import hashlib

from live_test import atr, config_id, dist

H1, H4 = 'H1', 'H4'
H1_DUR = 3600
MODES = ('WAVE3', 'WAVE5')
REGIME_BLOCKED = ('CRISIS', 'MEAN_REVERTING')


# --------------------------------------------------------------------------- zigzag (pure)
def zigzag(bars, zz_atr=2.5, atr_period=14):
    """Causal ATR zigzag over CLOSED bars. Returns (pivots, leg):
    pivots = [{kind 'H'|'L', price, time, confirm}] (confirm = close time of the bar that confirmed it);
    leg = {dir 'up'|'down', price, time} = the current, unconfirmed extreme (None before the first pivot)."""
    pivots, leg = [], None
    if len(bars) < atr_period + 2:
        return pivots, leg
    hi = lo = None
    for i in range(atr_period, len(bars)):
        b = bars[i]
        a = atr(bars[i - atr_period:i + 1], atr_period)
        if not a:
            continue
        th = zz_atr * a
        close_t = b.time + (bars[i].time - bars[i - 1].time if i else H1_DUR)
        if leg is None:                                   # find the first swing
            if hi is None or b.high > hi[0]:
                hi = (b.high, b.time)
            if lo is None or b.low < lo[0]:
                lo = (b.low, b.time)
            if b.high - lo[0] >= th and lo[1] < b.time:
                pivots.append(dict(kind='L', price=lo[0], time=lo[1], confirm=close_t))
                leg = dict(dir='up', price=b.high, time=b.time)
            elif hi[0] - b.low >= th and hi[1] < b.time:
                pivots.append(dict(kind='H', price=hi[0], time=hi[1], confirm=close_t))
                leg = dict(dir='down', price=b.low, time=b.time)
            continue
        if leg['dir'] == 'up':
            if b.high > leg['price']:
                leg = dict(dir='up', price=b.high, time=b.time)
            elif leg['price'] - b.low >= th:
                pivots.append(dict(kind='H', price=leg['price'], time=leg['time'], confirm=close_t))
                leg = dict(dir='down', price=b.low, time=b.time)
        else:
            if b.low < leg['price']:
                leg = dict(dir='down', price=b.low, time=b.time)
            elif b.high - leg['price'] >= th:
                pivots.append(dict(kind='L', price=leg['price'], time=leg['time'], confirm=close_t))
                leg = dict(dir='up', price=b.high, time=b.time)
    return pivots, leg


def wave3_count(pivots, leg, c):
    """(side, count, reject_reason). count = dict(P0, P1, P2) prices/times when the rules pass."""
    if not leg or len(pivots) < 2:
        return None, None, None
    p0, p1 = pivots[-2], pivots[-1]
    if p1['kind'] == 'H' and leg['dir'] == 'down':
        side, sign = 'BUY', 1
    elif p1['kind'] == 'L' and leg['dir'] == 'up':
        side, sign = 'SELL', -1
    else:
        return None, None, None
    w1 = sign * (p1['price'] - p0['price'])
    p2 = leg['price']
    count = dict(P0=p0['price'], P1=p1['price'], P2=p2, t0=p0['time'], t1=p1['time'], t2=leg['time'], w1=w1)
    if w1 <= 0:
        return side, count, 'NOT_AN_IMPULSE'
    if sign * (p2 - p0['price']) <= 0:
        return side, count, 'RULE1_BROKEN'
    depth = sign * (p1['price'] - p2) / w1
    count['w2_depth'] = round(depth, 3)
    if depth < c['w2_min']:
        return side, count, 'W2_TOO_SHALLOW'
    if depth > c['w2_max']:
        return side, count, 'W2_TOO_DEEP'
    return side, count, None


def wave5_count(pivots, leg, c):
    """(side, count, reject_reason) for a wave-5 entry: four confirmed swings + the wave-4 leg."""
    if not leg or len(pivots) < 4:
        return None, None, None
    p0, p1, p2, p3 = pivots[-4:]
    if p3['kind'] == 'H' and leg['dir'] == 'down' and p0['kind'] == 'L':
        side, sign = 'BUY', 1
    elif p3['kind'] == 'L' and leg['dir'] == 'up' and p0['kind'] == 'H':
        side, sign = 'SELL', -1
    else:
        return None, None, None
    w1 = sign * (p1['price'] - p0['price'])
    w3 = sign * (p3['price'] - p2['price'])
    p4 = leg['price']
    count = dict(P0=p0['price'], P1=p1['price'], P2=p2['price'], P3=p3['price'], P4=p4,
                 t0=p0['time'], t1=p1['time'], t3=p3['time'], t4=leg['time'], w1=w1, w3=w3)
    if w1 <= 0 or w3 <= 0:
        return side, count, 'NOT_AN_IMPULSE'
    if sign * (p2['price'] - p0['price']) <= 0:
        return side, count, 'RULE1_BROKEN'
    if w3 <= w1:
        return side, count, 'RULE2_W3_SHORTER'
    if sign * (p4 - p1['price']) <= 0:
        return side, count, 'RULE3_W4_OVERLAP'
    depth = sign * (p3['price'] - p4) / w3
    count['w4_depth'] = round(depth, 3)
    if depth < c['w4_min']:
        return side, count, 'W4_TOO_SHALLOW'
    if depth > c['w4_max']:
        return side, count, 'W4_TOO_DEEP'
    return side, count, None


# --------------------------------------------------------------------------- agent
class ElliottImpulse:
    strategy_id = 'EW_IMPULSE'
    version = 'ew_impulse@1'
    needs_market = True

    DEFAULTS = dict(
        mode='WAVE3', regime_filter=False, entry_timeframe='H1',
        h1_lookback=300, zz_atr=2.5, atr_period=14, confirm_atr=1.0,
        w2_min=0.382, w2_max=0.786, w3_target=1.618,
        w4_min=0.236, w4_max=0.5, w5_target=1.0,
        sl_buffer_points=None, sl_buffer_atr=0.10,
        min_sl_atr=0.5, max_sl_atr=8.0, min_sl_points=None, max_sl_points=None, min_sl_spreads=3.0,
        min_rr=1.5, entry_window_seconds=300, time_exit_hours=48, max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        if self.config['mode'] not in MODES:
            raise ValueError(f"Elliott Wave: unknown mode {self.config['mode']!r}")
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    def label(self):
        return f"Elliott Wave · {'wave 3' if self.config['mode'] == 'WAVE3' else 'wave 5'}" + \
               (' · regime filter' if self.config['regime_filter'] else '')

    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    def _regime(self, market, symbol, now, h1):
        try:
            import market_scout
            from live_test_snr import daily_from_h4
            h4 = market.candles(symbol, H4, 420, now=now)
            return market_scout.regime(h1[-120:], h4[-150:], daily_from_h4(h4), closed=True)['label']
        except Exception:
            return None

    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        if market is None:
            return None
        try:
            h1 = market.candles(symbol, H1, c['h1_lookback'], now=now)
        except Exception as exc:
            state['_feed_note'] = f'H1 data unavailable: {exc}'
            return None
        if len(h1) < c['atr_period'] + 5 or h1[-1].time == state.get('last_h1_time'):
            return None
        state['last_h1_time'] = h1[-1].time
        last = h1[-1]
        if now - (last.time + H1_DUR) > c['entry_window_seconds']:
            return None                                          # stale close: never a late entry
        pivots, leg = zigzag(h1, c['zz_atr'], c['atr_period'])
        side, count, reject = (wave3_count if c['mode'] == 'WAVE3' else wave5_count)(pivots, leg, c)
        if side is None:
            return None
        a = atr(h1, c['atr_period'])
        if not a:
            return None
        buy = side == 'BUY'
        turned = (last.close - leg['price'] >= c['confirm_atr'] * a and last.close > last.open) if buy else \
                 (leg['price'] - last.close >= c['confirm_atr'] * a and last.close < last.open)
        if not turned:
            return None                                          # wave 2 / wave 4 still running
        key = f"{c['mode']}:{int(count['t0'])}:{int(count['t1'])}"
        done = state.setdefault('done', [])
        if key in done:
            return None
        if reject in ('W2_TOO_SHALLOW', 'W4_TOO_SHALLOW'):
            # the pullback may still deepen into the zone and turn again: re-check, log once
            if state.get('shallow_logged') == key:
                return None
            state['shallow_logged'] = key
        else:
            done.append(key)                                     # one decision per wave count
            del done[:-50]
        fields = dict(symbol=symbol, side=side, epoch=now, setup=f"EW_{c['mode']}",
                      **{k: (round(v, quote.digits) if isinstance(v, float) and k.startswith('P') else v)
                         for k, v in count.items() if not k.startswith('t')})

        def skip(reason, **kw):
            self._emit(state, 'SKIP', reason=reason, **fields, **kw)
            return None
        if reject:
            return skip(reject)
        regime = self._regime(market, symbol, now, h1)
        fields['regime'] = regime
        if c['regime_filter'] and regime in REGIME_BLOCKED:
            return skip('REGIME_FILTERED')
        entry = quote.ask if buy else quote.bid
        buf = dist(c['sl_buffer_points'], c['sl_buffer_atr'], a, point)
        min_sl = dist(c['min_sl_points'], c['min_sl_atr'], a, point)
        max_sl = dist(c['max_sl_points'], c['max_sl_atr'], a, point)
        if buf is None or min_sl is None or max_sl is None:
            return None
        min_sl = max(min_sl, c['min_sl_spreads'] * max(quote.ask - quote.bid, 0.0))
        sign = 1 if buy else -1
        if c['mode'] == 'WAVE3':
            sl = count['P0'] - sign * buf
            tp = count['P2'] + sign * c['w3_target'] * count['w1']
        else:
            sl = count['P4'] - sign * buf
            tp = count['P4'] + sign * c['w5_target'] * count['w1']
        risk = abs(entry - sl)
        if not (min_sl <= risk <= max_sl):
            return skip('RISK_OUT_OF_BOUNDS', risk=round(risk, quote.digits), min_risk=round(min_sl, quote.digits))
        if not ((sl < entry < tp) if buy else (tp < entry < sl)):
            return skip('INVALID_LEVELS')
        rr = abs(tp - entry) / risk if risk > 0 else 0.0
        if rr < c['min_rr']:
            return skip('RR_TOO_LOW', rr=round(rr, 2))
        sid = hashlib.sha1(f"{symbol}:EW:{key}:{side}".encode()).hexdigest()[:16]
        extra = dict(fields, signal_id=sid, fill_price=round(entry, quote.digits), target_level=round(tp, quote.digits),
                     planned_rr=round(rr, 2), config_variant=c['mode'] + ('+REGIME' if c['regime_filter'] else ''))
        wave = 'wave 3 after wave 2' if c['mode'] == 'WAVE3' else 'wave 5 after wave 4'
        return dict(side=side, signal_reason=f"Elliott {side}: {wave} "
                                             f"(depth {count.get('w2_depth', count.get('w4_depth'))})",
                    signal_time=last.time + H1_DUR, trigger_price=(quote.bid + quote.ask) / 2.0,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits), session=None,
                    timeframe=self.timeframe, entry_candle_end=float(now + c['time_exit_hours'] * 3600),
                    config_snapshot=dict(self.config), extra=extra)
