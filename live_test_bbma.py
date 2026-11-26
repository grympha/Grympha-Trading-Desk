"""Live BBMA (the strategy Live/DEMO execution trades) as a Live Test agent — fully SIMULATED.

One agent per BBMA setup and pair, so each combination gets its own R-based record:
  CSA_REENTRY, MOMENTUM_REENTRY, MHV_REVERSAL  (dashboard: Settings -> Strategy setups).

The analysis is NOT re-implemented: the agent builds the same H1/M15/M5 frames as the live
scanner (scanner.py) and calls the live `bbma_bot.indicators` and `bbma_bot.assess`, with the
setup filter and the structural-TP mode set exactly as the bot sets them. Execution mirrors the
live executor (risk_check.py / trade_management.py):
  * entry = a STOP order at the plan's trigger, valid until the plan's expiry (10 min default);
    it fills on the first quote that crosses the trigger, at that quote (Ask for BUY);
  * rejected if the spread exceeds 25% of the initial stop distance;
  * broker TP = TP2 (structural: the runner target when one exists);
  * at TP1: 50% partial close at the observed price and stop to break-even +50 points
    (only when there is a separate runner target — as live);
  * trailing stop 1R behind price once +1R is reached.
Not modelled: news blackouts, trade windows, the shared position cap and the 15 s live poll.

READ-ONLY: it receives only the ReadOnlyMarketData adapter and never an order API.
"""
from __future__ import annotations
from datetime import datetime, timezone

import pandas as pd

from live_test import config_id, MYT

SETUP_LABELS = {'CSA_REENTRY': 'CSA re-entry', 'MOMENTUM_REENTRY': 'Momentum re-entry',
                'MHV_REVERSAL': 'MHV reversal'}

# BBMA analysis keys copied from config.json (same names the live scanner reads).
LIVE_KEYS = ('min_bb_width_atr', 'signal_lookback', 'touch_tolerance_atr', 'mhv_lookback',
             'confirmation_bars', 'buffer_points', 'buffer_atr', 'max_spread_atr', 'max_stop_atr',
             'entry_expiry_minutes', 'bar_grace_seconds')

FRAMES = (('H1', 3600), ('M15', 900), ('M5', 300))
_CACHE: dict = {}


def frame_from_bars(bars) -> pd.DataFrame:
    return pd.DataFrame([dict(time=int(b.time), open=b.open, high=b.high, low=b.low, close=b.close)
                         for b in bars])


def cached(kind, symbol, tf, bars, build):
    """Indicator frames are shared by the agents of one pair (3 BBMA setups) until a new bar closes."""
    key = (kind, symbol, tf, int(bars[0].time), int(bars[-1].time), len(bars))
    if key not in _CACHE:
        if len(_CACHE) > 256:
            _CACHE.clear()
        _CACHE[key] = build(frame_from_bars(bars))
    return _CACHE[key]


def live_frames(market, symbol, now, frames, count, minimum, grace, kind, build):
    """Closed, fresh frames with indicators — the live scanners' freshness rule:
    the last closed bar opened between 1 and 2 bar lengths (+ grace) ago."""
    out = []
    for name, seconds in frames:
        bars = market.candles(symbol, name, count + 1, now=now)[-count:]
        if len(bars) < minimum:
            raise ValueError(f'Insufficient {name} history ({len(bars)} bars)')
        age = now - int(bars[-1].time)
        if not seconds <= age <= 2 * seconds + grace:
            raise ValueError(f'Stale or unclosed {name} bar (open age {age:.0f}s)')
        out.append(cached(kind, symbol, name, bars, build))
    return out


def iso_epoch(text):
    return datetime.fromisoformat(text).timestamp()


def management_extra(c, tp1, single_target):
    """Live position management as Simulator fields (see trade_management.py)."""
    extra = dict(trail_activation_r=c['trail_activation_r'], trail_distance_r=c['trail_distance_r'])
    if not single_target and c['partial_fraction'] > 0:
        extra.update(partial_price=tp1, partial_fraction=c['partial_fraction'],
                     be_on_partial=True, be_offset_points=c['be_offset_points'])
    return extra


class LiveBBMA:
    strategy_id = 'LIVE_BBMA'
    version = 'bbma_live@1'
    needs_market = True

    DEFAULTS = dict(
        entry_timeframe='M5', setup='MOMENTUM_REENTRY',
        # analysis (config.json values on 2026-11-26)
        min_bb_width_atr=2.0, signal_lookback=12, touch_tolerance_atr=0.1, mhv_lookback=30,
        confirmation_bars=4, buffer_points=2, buffer_atr=0.05, max_spread_atr=0.15,
        max_stop_atr=5.0, entry_expiry_minutes=10, bar_grace_seconds=120, history_bars=400,
        # targets + management (dashboard settings on 2026-11-26)
        tp_mode='structural', structural_runner=True, max_spread_risk=0.25,
        partial_fraction=0.5, be_offset_points=50, trail_activation_r=1.0, trail_distance_r=1.0,
        max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        if self.config['setup'] not in SETUP_LABELS:
            raise ValueError('setup must be one of ' + ', '.join(SETUP_LABELS))
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    @classmethod
    def from_live_config(cls, cfg: dict, setup: str) -> 'LiveBBMA':
        return cls({**{k: cfg[k] for k in LIVE_KEYS if k in (cfg or {})}, 'setup': setup})

    def label(self):
        return f"BBMA live · {SETUP_LABELS[self.config['setup']]}"

    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    def analysis_config(self, tick_size):
        c, setup = self.config, self.config['setup']
        cfg = {k: c[k] for k in LIVE_KEYS}
        cfg.update(enable_mhv=setup == 'MHV_REVERSAL',
                   _allowed_setups=[] if setup == 'MHV_REVERSAL' else [setup],
                   _tick_size=tick_size, _bbma_tp_mode=c['tp_mode'],
                   _structural_partial=bool(c['structural_runner']), _minimum_stop_distance=0.0)
        return cfg

    def _tick_size(self, market, symbol, point):
        try:
            return float(market.spec(symbol)['tick_size']) or point
        except Exception:
            return point

    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        pend = state.get('pending')
        if pend:
            log = dict(signal_id=pend['id'], symbol=symbol, side=pend['side'], epoch=now, setup=pend['setup'])
            if now >= pend['expires']:
                self._emit(state, 'NO_FILL', reason='STOP_ORDER_EXPIRED', **log)
                state['pending'] = None
            elif (quote.ask >= pend['entry']) if pend['side'] == 'BUY' else (quote.bid <= pend['entry']):
                state['pending'] = None
                return self._signal(pend, quote, now, state, log)
            else:
                return None                       # stop order still waiting
        if not candles or market is None:
            return None
        if state.get('last_m5') == candles[-1].time:
            return None                           # analyse once per closed M5 candle, like the scanner
        from bbma_bot import assess, indicators
        h1, m15, m5 = live_frames(market, symbol, now, FRAMES, c['history_bars'], 150,
                                  c['bar_grace_seconds'], 'bbma', indicators)
        state['last_m5'] = candles[-1].time
        result = assess(h1, m15, m5, quote.bid, quote.ask, point, quote.digits,
                        self.analysis_config(self._tick_size(market, symbol, point)))
        plan = result.get('entry_plan')
        if not plan or plan['id'] == state.get('last_plan'):
            return None
        state['last_plan'] = plan['id']
        structural = plan.get('structural_tp') or {}
        pend = dict(id=plan['id'], side=plan['side'], setup=plan['setup'], entry=plan['entry'],
                    stop_loss=plan['stop_loss'], tp1=plan['tp1'], tp2=plan['tp2'],
                    single_target=bool(structural.get('single_target', False)) if structural else False,
                    expires=iso_epoch(plan['expires_utc']), created=now)
        risk = abs(pend['entry'] - pend['stop_loss'])
        if quote.ask - quote.bid > c['max_spread_risk'] * risk:
            self._emit(state, 'NO_FILL', reason='SPREAD_OVER_25PCT_OF_RISK', signal_id=pend['id'],
                       symbol=symbol, side=pend['side'], epoch=now, setup=pend['setup'])
            return None
        state['pending'] = pend
        return None

    def _signal(self, pend, quote, now, state, log):
        c, side = self.config, pend['side']
        fill = quote.ask if side == 'BUY' else quote.bid
        sl, tp1, tp2 = pend['stop_loss'], pend['tp1'], pend['tp2']
        ordered = (sl < fill < tp2) if side == 'BUY' else (tp2 < fill < sl)
        if not ordered:
            self._emit(state, 'NO_FILL', reason='FILL_BEYOND_TARGET_OR_STOP', **log)
            return None
        extra = dict(signal_id=pend['id'], setup=pend['setup'], config_variant=c['setup'],
                     target_level=tp2, tp1_level=tp1, fill_price=round(fill, quote.digits),
                     trigger_level=pend['entry'], single_target=pend['single_target'])
        extra.update(management_extra(c, tp1, pend['single_target']))
        day = datetime.fromtimestamp(now, MYT).strftime('%Y-%m-%d')
        return dict(side=side, signal_reason=f"BBMA {SETUP_LABELS[c['setup']]} {side} stop {pend['entry']}",
                    signal_time=pend['created'], trigger_price=pend['entry'],
                    initial_sl=sl, initial_tp=tp2, session=f'{day}@bbma', timeframe=self.timeframe,
                    entry_candle_end=None, config_snapshot=dict(c), extra=extra)
