"""Module 1: independent BBMA and Confluence scanning. No order submission API."""
import json
import logging
import time
import pandas as pd
from market_hours import MarketClosed, check_stale, log_closed_once, reset_closed_log

class BBMAScanner:
    def __init__(self,mt5,cfg,root,*,assess,indicators,quote_age,stamp):
        self.mt5,self.cfg,self.root=mt5,cfg,root
        self.assess,self.indicators,self.quote_age,self.stamp=assess,indicators,quote_age,stamp

    def scan(self,publish,disabled=None,tp_mode='existing',partial=False,overrides=None):
        from pair_controls import enabled
        disabled = disabled or {}
        target=publish;stats=dict(checked=0,blocked=0,closed=0)
        def publish(result):
            stats['checked']+=1
            if result.get('health')=='MARKET_CLOSED':stats['closed']+=1
            elif result.get('health')!='OK':stats['blocked']+=1
            return target(result)
        mt5,cfg,ROOT=self.mt5,self.cfg,self.root
        assess,indicators,quote_age,stamp=self.assess,self.indicators,self.quote_age,self.stamp
        for symbol in cfg['symbols']:
            if not enabled(disabled, 'BBMA', symbol):
                continue
            try:
                terminal = mt5.terminal_info()
                if terminal is None or not terminal.connected:
                    raise RuntimeError('MT5 disconnected; waiting for terminal reconnection')
                if not mt5.symbol_select(symbol, True):
                    raise ValueError('Symbol unavailable: use exact broker name')
                tick, info = mt5.symbol_info_tick(symbol), mt5.symbol_info(symbol)
                now = time.time()
                if tick is None or info is None or tick.bid <= 0 or tick.ask < tick.bid:
                    raise ValueError('Invalid quote')
                age = quote_age(now, tick.time, cfg.get('tick_timestamp_offset_hours', 0))
                if age < -5:
                    raise ValueError(f'Quote timestamp ahead of PC by {-age:.1f}s; '
                                     'check Windows time / run --diagnose-time')
                check_stale(age, cfg['max_tick_age_seconds'], now, symbol)  # weekend / rollover -> MarketClosed
                if age > cfg['max_tick_age_seconds']:
                    raise ValueError(f'Quote age {age:.1f}s exceeds '
                                     f'{cfg["max_tick_age_seconds"]}s; check feed/clock; '
                                     'run --diagnose-time')
                frames = []
                for name, seconds in [('H1', 3600), ('M15', 900), ('M5', 300)]:
                    raw = mt5.copy_rates_from_pos(symbol, getattr(mt5, 'TIMEFRAME_'+name), 1, 400)
                    if raw is None or len(raw) < 150:
                        raise ValueError('Insufficient '+name+' history; open chart in MT5')
                    data = pd.DataFrame(raw)
                    data['time'] = data['time'] - int(float(cfg.get('bar_timestamp_offset_hours', 0))*3600)
                    frame = indicators(data)
                    last = int(frame.iloc[-1].time)
                    if not seconds <= now-last <= 2*seconds+cfg['bar_grace_seconds']:
                        raise ValueError(f'Stale or unclosed {name} bar: open_age={now-last:.1f}s; run --diagnose-time')
                    frames.append(frame)
                local_cfg = dict(cfg, **(overrides or {}), _tick_size=info.trade_tick_size or info.point,
                                 _bbma_tp_mode=tp_mode,_structural_partial=partial,
                                 _minimum_stop_distance=float(getattr(info,'trade_stops_level',0))*info.point)
                result = assess(*frames, tick.bid, tick.ask, info.point, info.digits, local_cfg)
                result.update(symbol=symbol, strategy='BBMA', chart_timeframe='M5', checked_utc=stamp(now), health='OK',
                              quote=dict(bid=float(tick.bid), ask=float(tick.ask),
                                         spread=float(tick.ask-tick.bid), age_seconds=float(age),
                                         digits=int(info.digits)),
                              chart=[dict(time=int(r.time), open=float(r.open), high=float(r.high),
                                          low=float(r.low), close=float(r.close), mid=float(r.mid),
                                          upper=float(r.upper), lower=float(r.lower))
                                     for r in frames[-1].tail(80).itertuples()],
                              h1=dict(close=float(frames[0].iloc[-1].close),
                                      ema50=float(frames[0].iloc[-1].ema50),
                                      mid=float(frames[0].iloc[-1].mid)))
                result['charts'] = {name: chart_bars(frame) for name, frame in zip(('H1', 'M15', 'M5'), frames)}
                try:
                    from trade_review import market_snapshot
                    result['review_market']=market_snapshot(dict(zip(('H1','M15','M5'),frames)))
                except Exception:
                    logging.warning('%s research timeframe capture unavailable',symbol)
                reset_closed_log()
                publish(result)
                from decision_log import append as log_decision
                log_decision(ROOT/'decisions.jsonl', result)  # compact line; candles omitted, auto-rotates at 20 MB
                print(symbol, result['condition']['name'], result.get('reason'), flush=True)
            except MarketClosed as exc:
                log_closed_once(str(exc))
                publish(dict(symbol=symbol, checked_utc=stamp(time.time()), health='MARKET_CLOSED',
                             reason=str(exc), condition={'name': 'MARKET CLOSED', 'side': None},
                             setup=None, entry_plan=None))
            except Exception as exc:
                # MT5 errors contain no Telegram credentials; do not log network exception detail.
                logging.warning('%s analysis blocked: %s', symbol, str(exc))
                publish(dict(symbol=symbol, checked_utc=stamp(time.time()), health='BLOCKED',
                             reason=str(exc), condition={'name': 'UNAVAILABLE', 'side': None},
                             setup=None, entry_plan=None))
        return stats


def chart_bars(frame, count=80):
    """Closed-candle OHLC + Bollinger snapshot for the dashboard chart (non-finite values become None)."""
    import math
    def num(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None
    try:
        return [dict(time=int(r.time), open=float(r.open), high=float(r.high), low=float(r.low), close=float(r.close),
                     mid=num(getattr(r, 'mid', None)), upper=num(getattr(r, 'upper', None)),
                     lower=num(getattr(r, 'lower', None))) for r in frame.tail(count).itertuples()]
    except Exception:
        return []  # chart display must never block an analysis
