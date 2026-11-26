"""Live ORB (the strategy Live/DEMO execution trades) as a Live Test agent — fully SIMULATED.

This is the live Opening-Range Breakout + retest from orb_strategy.py / orb_core.py
(range 01:00-06:00 broker server time, trades 07:00-17:00, XAUUSD). It is NOT the
"Gold ORB 09:30 New York" agent (`ORB_BREAKOUT_RETEST`, live_test_orb.py), which is a
different strategy.

The analysis is NOT re-implemented: the agent calls the live `orb_core.scan` with the same
settings as orb_strategy.settings + the dashboard ORB settings, keeps one signal per session
like the runner, and converts it with the live `ORBRunner.plan` (fixed 2R target today).
Execution mirrors the live executor: market entry within the 60 s signal window; rejected if
the spread exceeds 25% of the stop distance or the quote moved > 0.25R from the reference;
TP = TP2; 50% partial + break-even +50 points at TP1 (1R); trail 1R from +1R.
Not modelled: news blackouts, trade windows and the shared position cap.

READ-ONLY: it receives only the ReadOnlyMarketData adapter and never an order API.
"""
from __future__ import annotations
from types import SimpleNamespace

from live_test import config_id
from live_test_bbma import management_extra

# Strategy settings from config.json's "orb" block (names as orb_strategy.DEFAULTS).
LIVE_KEYS = ('range_start_hour', 'range_end_hour', 'trade_start_hour', 'trade_end_hour',
             'ema_period', 'h4_ema_period', 'atr_period', 'dynamic_buffer_pct', 'retest_tolerance_points',
             'max_retest_penetration_fraction', 'max_bars_after_breakout', 'min_range_points',
             'max_range_points', 'max_spread_points', 'max_entry_drift_atr', 'max_signal_age_seconds',
             'max_tick_age_seconds')
# Dashboard ORB settings (Settings -> Strategy setups -> ORB), values on 2026-11-26.
DASHBOARD = dict(skip_weekdays=[2, 3], skip_months=[12], signals_per_day=1, sl_multiplier=0.9,
                 buy_sl_buffer_points=60, sell_sl_buffer_points=100, tp_mode='fixed', fixed_rr=2.0)
# scan() statuses that the live runner would retry on its next 15 s poll within the signal window
RETRY = {'STALE_OR_FUTURE_QUOTE', 'INVALID_QUOTE', 'SPREAD_BLOCKED', 'LEVEL_LOST_BEFORE_EMISSION',
         'ENTRY_DRIFT_BLOCKED'}


class LiveORB:
    strategy_id = 'LIVE_ORB'
    version = 'orb_live@1'
    needs_market = True

    DEFAULTS = dict(entry_timeframe='M5', session_utc_offset_hours=2, m5_bars=2000, h4_bars=400,
                    max_spread_risk=0.25, max_reference_drift_r=0.25,
                    partial_fraction=0.5, be_offset_points=50, trail_activation_r=1.0,
                    trail_distance_r=1.0, max_gap_seconds=120, max_positions=1, **DASHBOARD)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    @classmethod
    def from_live_config(cls, cfg: dict) -> 'LiveORB':
        orb = (cfg or {}).get('orb') or {}
        extra = {k: orb[k] for k in LIVE_KEYS if k in orb}
        offset = orb.get('session_utc_offset_hours')
        extra['session_utc_offset_hours'] = offset if offset is not None else (cfg or {}).get('bar_timestamp_offset_hours', 0)
        return cls(extra)

    def label(self):
        c = self.core_config('XAUUSD')
        return f"ORB live · {c['range_start_hour']:02d}-{c['range_end_hour']:02d} server range, retest"

    def core_config(self, symbol):
        """orb_strategy.settings() with this agent's values, as the live runner passes to scan()."""
        from orb_strategy import DEFAULTS as ORB_DEFAULTS
        c = dict(ORB_DEFAULTS)
        c.update({k: v for k, v in self.config.items() if k in ORB_DEFAULTS})
        c.update(symbol=symbol, symbols=[symbol], symbol_base='XAUUSD', news_mode='off')
        return c

    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        if not candles or market is None:
            return None
        last = candles[-1].time
        if state.get('done_m5') == last:
            return None
        if now - (last + 300) > self.core_config(symbol)['max_signal_age_seconds']:
            state['done_m5'] = last               # the live 60 s signal window for this candle has passed
            return None
        import orb_core
        from orb_strategy import ORBRunner
        to_core = lambda bars: [orb_core.Bar(int(b.time), b.open, b.high, b.low, b.close) for b in bars]
        m5 = to_core(market.candles(symbol, 'M5', c['m5_bars'], now=now))
        h4 = to_core(market.candles(symbol, 'H4', c['h4_bars'], now=now))
        try:
            tick = float(market.spec(symbol)['tick_size']) or point
        except Exception:
            tick = point
        spec = dict(point=point, tick_size=tick, digits=quote.digits)
        r = orb_core.scan(self.core_config(symbol), m5, h4, now,
                          dict(bid=quote.bid, ask=quote.ask, time=quote.time), spec, None)
        status, sig = r['status'], r.get('signal')
        if status not in RETRY:
            state['done_m5'] = last
        if not sig:
            return None
        session = sig['session_date']
        day = state.get('emitted') or {}
        if day.get('session') != session:
            day = {'session': session, 'ids': []}
        if sig['signal_id'] in day['ids'] or len(day['ids']) >= c['signals_per_day']:
            return None
        day['ids'].append(sig['signal_id'])
        state['emitted'] = day
        plan = ORBRunner.plan(SimpleNamespace(tp_mode=c['tp_mode'], fixed_rr=c['fixed_rr']), sig, spec)
        side = plan['side']
        log = dict(signal_id=plan['id'], symbol=symbol, side=side, epoch=now, setup='ORB_RETEST',
                   ref_high=plan['range_high'], ref_low=plan['range_low'])
        fill = quote.ask if side == 'BUY' else quote.bid
        risk = abs(plan['entry'] - plan['stop_loss'])
        if quote.ask - quote.bid > c['max_spread_risk'] * risk:
            self._emit(state, 'NO_FILL', reason='SPREAD_OVER_25PCT_OF_RISK', **log)
            return None
        if abs(fill - plan['entry']) > c['max_reference_drift_r'] * risk:
            self._emit(state, 'NO_FILL', reason='QUOTE_MOVED_OVER_025R', **log)
            return None
        extra = dict(log, target_level=plan['tp2'], tp1_level=plan['tp1'], fill_price=round(fill, quote.digits),
                     range_points=plan['range_points'], config_variant=f"{c['tp_mode']}{c['fixed_rr'] if c['tp_mode'] == 'fixed' else ''}")
        for k in ('symbol', 'side', 'epoch'):
            extra.pop(k)
        extra.update(management_extra(c, plan['tp1'], single_target=False))
        return dict(side=side, signal_reason=f"ORB live {side} retest of {plan['range_high']}/{plan['range_low']}",
                    signal_time=now, trigger_price=plan['entry'], initial_sl=plan['stop_loss'],
                    initial_tp=plan['tp2'], session=f'{session}@orb', timeframe=self.timeframe,
                    entry_candle_end=None, config_snapshot=dict(c), extra=extra)
