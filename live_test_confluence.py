"""Live Five-factor Confluence (the strategy Live/DEMO execution trades) as a Live Test agent —
fully SIMULATED. One agent per pair.

The analysis is NOT re-implemented: the agent builds the same M15/H1/H4 frames as the live
runner (confluence_strategy.Runner) and calls the live `confluence_strategy.indicators` and
`confluence_strategy.evaluate`. A score >= min_score (live: 4/5, H4 trend required) enters at
market, once per closed M15 candle. Execution mirrors the live executor:
  * SL 1.5 ATR, broker TP = TP2 (3.5 ATR);
  * rejected if the spread exceeds 25% of the initial stop distance;
  * at TP1 (2 ATR): 50% partial close at the observed price and stop to break-even +50 points;
  * trailing stop 1R behind price once +1R is reached.
Not modelled: news blackouts, trade windows, the shared position cap, and the live runner's
5-minute schedule (live can enter up to 5 min after the M15 close; this agent enters at once).

READ-ONLY: it receives only the ReadOnlyMarketData adapter and never an order API.
"""
from __future__ import annotations
from datetime import datetime

from live_test import config_id, MYT
from live_test_bbma import live_frames, management_extra

# Confluence keys copied from config.json's "confluence" block.
LIVE_KEYS = ('min_score', 'sr_lookback_h1', 'sr_near_pct', 'sl_atr', 'tp1_atr', 'tp2_atr',
             'max_spread_points')
FRAMES = (('M15', 900), ('H1', 3600), ('H4', 14400))


class LiveConfluence:
    strategy_id = 'LIVE_CONFLUENCE'
    version = 'confluence_live@1'
    needs_market = True

    DEFAULTS = dict(
        entry_timeframe='M15',
        min_score=4, sr_lookback_h1=50, sr_near_pct=0.004, sl_atr=1.5, tp1_atr=2.0, tp2_atr=3.5,
        max_spread_points=0, history_bars=500, bar_grace_seconds=120,
        max_spread_risk=0.25, partial_fraction=0.5, be_offset_points=50,
        trail_activation_r=1.0, trail_distance_r=1.0, max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        self.timeframe = self.config['entry_timeframe']

    @classmethod
    def from_live_config(cls, cfg: dict, settings: dict | None = None) -> 'LiveConfluence':
        """config.json values + (optionally) the live dashboard settings (minimum score, management)."""
        conf = (cfg or {}).get('confluence') or {}
        extra = {k: conf[k] for k in LIVE_KEYS if k in conf}
        if 'bar_grace_seconds' in (cfg or {}):
            extra['bar_grace_seconds'] = cfg['bar_grace_seconds']
        s = settings or {}
        extra.update(s.get('confluence') or {})
        extra.update(s.get('management') or {})
        return cls(extra)

    def label(self):
        return f"Confluence live · score {self.config['min_score']}/5+"

    def _emit(self, state, event, **kw):
        state.setdefault('_emit', []).append(dict(event=event, **kw))

    def evaluate(self, now, quote, candles, point, state, market=None, symbol=None):
        c = self.config
        symbol = symbol or quote.symbol
        if not candles or market is None:
            return None
        if state.get('last_m15') == candles[-1].time:
            return None                           # one analysis per closed M15 candle, like the runner
        from confluence_strategy import evaluate as score, indicators
        m15, h1, h4 = live_frames(market, symbol, now, FRAMES, c['history_bars'], 250,
                                  c['bar_grace_seconds'], 'confluence', indicators)
        state['last_m15'] = candles[-1].time
        conf = {k: c[k] for k in LIVE_KEYS}
        result = score(m15, h1, h4, quote.bid, quote.ask, point, quote.digits,
                       {'symbols': [symbol], 'confluence': dict(conf, symbols=[symbol])}, now)
        plan = result.get('entry_plan')
        if not plan:
            return None
        side = plan['side']
        log = dict(signal_id=f"{symbol}:{plan['id']}", symbol=symbol, side=side, epoch=now,
                   setup=f"CONFLUENCE_{result['score']}of5")
        fill = quote.ask if side == 'BUY' else quote.bid
        sl, tp1, tp2 = plan['stop_loss'], plan['tp1'], plan['tp2']
        risk = abs(fill - sl)
        if quote.ask - quote.bid > c['max_spread_risk'] * risk:
            self._emit(state, 'NO_FILL', reason='SPREAD_OVER_25PCT_OF_RISK', **log)
            return None
        factors = ', '.join(f['name'] for f in result['factors'] if f['passed'])
        extra = dict(signal_id=log['signal_id'], setup=log['setup'], config_variant=f"min{c['min_score']}",
                     target_level=tp2, tp1_level=tp1, fill_price=round(fill, quote.digits),
                     score=result['score'], factors_passed=factors)
        extra.update(management_extra(c, tp1, single_target=False))
        day = datetime.fromtimestamp(now, MYT).strftime('%Y-%m-%d')
        return dict(side=side, signal_reason=f"Confluence {result['score']}/5 {side} ({factors})",
                    signal_time=now, trigger_price=plan['entry'], initial_sl=sl, initial_tp=tp2,
                    session=f'{day}@confluence', timeframe=self.timeframe, entry_candle_end=None,
                    config_snapshot=dict(c), extra=extra)
