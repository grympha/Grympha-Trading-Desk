"""Advocate EMA Pullback (M15/M5) — six-role debate Live Test agent, fully SIMULATED.

Ported from the standalone Grympha XAU Advocate Desk (paper bot). The strategy is an EMA
pullback gated by a deterministic three-objection debate; it is reproduced here so its setups
can be forward-tested in the Live Test tab. Lots / commission / broker P/L are NOT ported —
Live Test measures R, so the shared Simulator fills at Ask/Bid and scores from fill -> initial SL.

Rules (XAUUSD-tuned):
  * Bias from M15: EMA20 > EMA50 -> BUY, else SELL.
  * Setup on the last CLOSED M5 bar: it touches the prior M5 EMA20, and closes on the trend side
    of both its own body and the current M5 EMA20.
  * Debate (all three objections must exist AND clear their thresholds):
      cost      = spread / ATR(M5)                 <= debate_max_cost_atr
      extension = |entry - EMA20(M5)| / ATR(M5)    <= debate_max_extension_atr
      structure = room to the 20-bar M15 extreme   >= debate_min_room_r   (in R)
    Fewer than three evidenced objections -> VETO (FEWER_THAN_THREE_OBJECTIONS). A spread wider
    than max_spread_atr*ATR vetoes before the debate (SPREAD).
  * Entry is a market fill at the debated quote; SL = entry -/+ stop_atr * ATR(M5). NO take-profit.
  * Exit management (Simulator): trailing only, from trail_activation_r at trail_distance_r.
    No break-even and no time stop (matches the desk: stop / trail / session end).

READ-ONLY: receives a ReadOnlyMarketData adapter; never calls any order API. It only RETURNS a
signal dict and appends VETO / SKIP records to state['_emit']. Each M5 signal candle fires at
most once (dedup via state['seen']); a reload cannot re-enter a past candle.
"""
from __future__ import annotations
import hashlib

from live_test import atr, ema_last, config_id

M5, M15 = 'M5', 'M15'


class AdvocateEmaPullback:
    strategy_id = 'ADVOCATE_EMA_PULLBACK'
    version = 'advocate_ema@1'
    needs_market = True

    DEFAULTS = dict(
        entry_timeframe='M5',
        m5_lookback=220, m15_lookback=220, atr_period=14,
        stop_atr=1.5, trail_activation_r=2.0, trail_distance_r=1.0,
        max_spread_atr=0.15,
        debate_max_cost_atr=0.1, debate_max_extension_atr=0.75, debate_min_room_r=2.0,
        structure_lookback=20, max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    def label(self):
        return "Advocate EMA pullback (M15/M5)"

    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    @staticmethod
    def _sid(symbol, bar_time, d):
        return hashlib.sha1(f"{symbol}:ADV:{int(bar_time)}:{d}".encode()).hexdigest()[:16]

    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        if market is None:
            return None
        try:
            m5 = market.candles(symbol, M5, c['m5_lookback'], now=now)
            m15 = market.candles(symbol, M15, c['m15_lookback'], now=now)
        except Exception as exc:
            state['_feed_note'] = f'M5/M15 data unavailable: {exc}'
            return None
        if len(m5) < 60 or len(m15) < 60:
            return None
        atr5 = atr(m5, c['atr_period'])
        atr15 = atr(m15, c['atr_period'])
        if not atr5 or not atr15 or atr5 <= 0 or atr15 <= 0:
            return None
        closes15 = [b.close for b in m15]
        d = 1 if ema_last(closes15, 20) > ema_last(closes15, 50) else -1
        b = m5[-1]
        prev_ema20 = ema_last([x.close for x in m5[:-1]], 20)
        ema20_5 = ema_last([x.close for x in m5], 20)
        touched = b.low <= prev_ema20 if d == 1 else b.high >= prev_ema20
        confirm = d * (b.close - b.open) > 0 and d * (b.close - ema20_5) > 0
        sid = self._sid(symbol, b.time, d)
        side = 'BUY' if d == 1 else 'SELL'
        if not (touched and confirm) or sid in state.get('seen', []):
            return None
        state.setdefault('seen', []).append(sid)

        spread = quote.ask - quote.bid
        if spread / atr5 > c['max_spread_atr']:
            self._emit(state, 'VETO', reason='SPREAD', signal_id=sid, symbol=symbol,
                       side=side, epoch=now, setup='ADVOCATE')
            return None

        entry = quote.ask if d == 1 else quote.bid
        distance = c['stop_atr'] * atr5
        extension = abs(entry - ema20_5) / atr5
        win = m15[-c['structure_lookback']:]
        opposing = max(x.high for x in win) if d == 1 else min(x.low for x in win)
        room = d * (opposing - entry) / distance
        cost = spread / atr5
        # Three evidenced objections must each EXIST (value > 0) before the debate can resolve.
        n_objections = (1 if cost > 0 else 0) + (1 if extension > 0 else 0) + (1 if room > 0 else 0)
        base = dict(signal_id=sid, symbol=symbol, side=side, epoch=now, setup='ADVOCATE',
                    ref_high=round(opposing, quote.digits) if d == 1 else None,
                    ref_low=round(opposing, quote.digits) if d == -1 else None,
                    target_level=round(opposing, quote.digits))
        if n_objections != 3:
            self._emit(state, 'VETO', reason='FEWER_THAN_THREE_OBJECTIONS', **base)
            return None
        approved = (cost <= c['debate_max_cost_atr'] and extension <= c['debate_max_extension_atr']
                    and room >= c['debate_min_room_r'])
        if not approved:
            self._emit(state, 'VETO', reason='UNREBUTTED_OBJECTION', **base)
            return None

        sl = entry - d * distance
        extra = dict(base)
        extra['fill_price'] = round(entry, quote.digits)
        extra['config_variant'] = 'M15/M5 pullback'
        extra['trail_activation_r'] = c['trail_activation_r']
        extra['trail_distance_r'] = c['trail_distance_r']
        return dict(side=side, signal_reason=f"Advocate {side} EMA pullback (debate passed)",
                    signal_time=b.time + 300, trigger_price=entry,
                    initial_sl=round(sl, quote.digits), initial_tp=None,
                    session=None, timeframe=self.timeframe, entry_candle_end=None,
                    config_snapshot=dict(self.config), extra=extra)
