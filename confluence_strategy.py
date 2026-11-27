"""Independent port of uploaded xauusd_bot_v4: H4/H1/M15 five-factor analysis.
No BBMA inputs or votes are used. No entry trigger or order execution is added.
"""
from __future__ import annotations
import logging
import json
import math
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from market_hours import check_stale

STRATEGY='CONFLUENCE'


def stamp(t): return datetime.fromtimestamp(int(t),timezone.utc).isoformat()


def settings(cfg):
    c=dict(enabled=True,check_seconds=300,min_score=4,sr_lookback_h1=50,
           sr_near_pct=.004,sl_atr=1.5,tp1_atr=2.,tp2_atr=3.5,
           max_spread_points=0,max_tick_age_seconds=180)
    c.update(cfg.get('confluence',{}))
    if 'symbols' not in c:
        c['symbols']=[s for s in cfg['symbols'] if 'XAU' in s.upper() or 'GOLD' in s.upper()]
    if c['check_seconds']<1 or not 1<=c['min_score']<=5 or c['sr_lookback_h1']<1:
        raise ValueError('Invalid confluence interval, score or S/R lookback')
    return c


def indicators(df):
    # Use the same ta library and default parameters as the supplied script.
    from ta.momentum import RSIIndicator
    from ta.trend import EMAIndicator, MACD
    from ta.volatility import AverageTrueRange, BollingerBands
    d=df.copy();close=d['close']
    for window in (9,21,50,200):
        d[f'ema{window}']=EMAIndicator(close=close,window=window).ema_indicator()
    d['rsi']=RSIIndicator(close=close,window=14).rsi()
    macd=MACD(close=close)
    d['macd']=macd.macd();d['macd_signal']=macd.macd_signal();d['macd_hist']=macd.macd_diff()
    d['atr']=AverageTrueRange(high=d.high,low=d.low,close=close,window=14).average_true_range()
    bb=BollingerBands(close=close,window=20)
    d['upper']=bb.bollinger_hband();d['mid']=bb.bollinger_mavg();d['lower']=bb.bollinger_lband()
    return d


def evaluate(m15,h1,h4,bid,ask,point,digits,cfg,now):
    """Pure score/levels. Inputs must already be validated CLOSED candles."""
    c=settings(cfg);r15,r1,r4=m15.iloc[-1],h1.iloc[-1],h4.iloc[-1]
    values=[r15.rsi,r15.macd,r15.macd_signal,r15.atr,r15.ema9,r15.ema21,r15.ema50,
            r1.ema9,r1.ema21,r1.ema50,r4.ema50,r4.ema200]
    if not all(np.isfinite(float(v)) for v in values) or r15.atr<=0:
        raise ValueError('Confluence indicator warm-up incomplete or invalid ATR')
    if not (math.isfinite(bid) and math.isfinite(ask) and bid>0 and ask>=bid and point>0):
        raise ValueError('Invalid Confluence executable quote')
    spread_points=(ask-bid)/point
    if c['max_spread_points'] and spread_points>c['max_spread_points']:
        raise ValueError('Confluence spread limit exceeded')
    recent=h1.tail(c['sr_lookback_h1'])
    support,resistance=float(recent.low.min()),float(recent.high.max())
    price=float(r15.close)
    side='BUY' if r4.close>r4.ema50>r4.ema200 else 'SELL' if r4.close<r4.ema50<r4.ema200 else None
    tests=[('H4 trend',bool(side),'Close > EMA50 > EMA200' if side=='BUY' else 'Close < EMA50 < EMA200' if side=='SELL' else 'No H4 trend alignment'),
           ('H1 EMA alignment',bool(r1.ema9>r1.ema21>r1.ema50 if side=='BUY' else r1.ema9<r1.ema21<r1.ema50 if side=='SELL' else False),'EMA9 / EMA21 / EMA50'),
           ('M15 RSI',bool(40<r15.rsi<65 if side=='BUY' else 35<r15.rsi<60 if side=='SELL' else False),f'RSI {r15.rsi:.2f}'),
           ('M15 MACD',bool(r15.macd>r15.macd_signal if side=='BUY' else r15.macd<r15.macd_signal if side=='SELL' else False),'MACD versus signal'),
           ('Near H1 level',bool(support<=price and (price-support)/price<c['sr_near_pct'] if side=='BUY' else resistance>=price and (resistance-price)/price<c['sr_near_pct'] if side=='SELL' else False),f'Within {c["sr_near_pct"]*100:g}%; H1 rolling {c["sr_lookback_h1"]}-bar low/high')]
    score=sum(int(ok) for _,ok,_ in tests)
    qualified=bool(side and score>=c['min_score'])
    analysis_id=f'{int(r15.time)}:{side or "HOLD"}'
    factors=[dict(name=name,passed=ok,detail=detail) for name,ok,detail in tests]
    plan=None
    if qualified:
        entry=float(ask if side=='BUY' else bid);sign=1 if side=='BUY' else -1
        atr=float(r15.atr)
        sl=entry-sign*c['sl_atr']*atr;tp1=entry+sign*c['tp1_atr']*atr;tp2=entry+sign*c['tp2_atr']*atr
        if min(sl,tp1,tp2)<=0: raise ValueError('Invalid Confluence SL/TP levels')
        plan=dict(id=analysis_id,side=side,order_idea='QUOTE_REFERENCE',signal_kind='REFERENCE',
                  entry=round(entry,digits),stop_loss=round(sl,digits),tp1=round(tp1,digits),tp2=round(tp2,digits),
                  setup='FIVE_FACTOR_CONFLUENCE',score=score,max_score=5,atr=atr,
                  sl_atr=c['sl_atr'],tp1_atr=c['tp1_atr'],tp2_atr=c['tp2_atr'],
                  rr1=c['tp1_atr']/c['sl_atr'],rr2=c['tp2_atr']/c['sl_atr'],
                  confirmation_utc=stamp(int(r15.time)+900),
                  # Display review due at the NEXT M15 close; not an order expiry/entry trigger.
                  expires_utc=stamp(int(r15.time)+1800),reference_utc=stamp(now),
                  status='REFERENCE_ONLY_NO_ENTRY_TRIGGER')
    return dict(strategy=STRATEGY,health='OK',checked_utc=stamp(now),analysis_utc=stamp(now),
                analysis_id=analysis_id,chart_timeframe='M15',
                condition=dict(name='BULLISH' if side=='BUY' else 'BEARISH' if side=='SELL' else 'HOLD',side=side),
                setup=dict(type='FIVE_FACTOR_CONFLUENCE',score=score) if qualified else None,
                entry_plan=plan,score=score,max_score=5,min_score=c['min_score'],qualified=qualified,
                factors=factors,support=support,resistance=resistance,atr=float(r15.atr),
                reason=f'{score}/5 factors passed. '+('Quote reference only; no entry trigger.' if qualified else 'HOLD: insufficient confluence or no H4 direction.'),
                quote=dict(bid=float(bid),ask=float(ask),spread=float(ask-bid),spread_points=spread_points,digits=int(digits)),
                chart=[dict(time=int(r.time),open=float(r.open),high=float(r.high),low=float(r.low),close=float(r.close),
                            mid=float(r.mid),upper=float(r.upper),lower=float(r.lower)) for r in m15.tail(80).itertuples()],
                charts={name: chart_bars(frame) for name, frame in (('H4', h4), ('H1', h1), ('M15', m15))})


class Runner:
    """Runs on its own 300s schedule; deduplicates completed M15 candles on disk."""
    def __init__(self,cfg,path):
        self.cfg=cfg;self.c=settings(cfg);self.path=Path(path);self.next_due=0
        try:self.cache=json.loads(self.path.read_text())
        except (OSError,ValueError):self.cache={}
        if not isinstance(self.cache,dict):self.cache={}
        # Clear cache if strategy settings or timestamp basis changed.
        signature=json.dumps(dict(c=self.c,bar_offset=cfg.get('bar_timestamp_offset_hours',0)),sort_keys=True)
        if self.cache.get('signature')!=signature:self.cache={'signature':signature,'symbols':{}}
        self.cache.setdefault('symbols',{})
        self.pair_scores={}   # {symbol: 3|4|5} from Settings -> Strategy setups; others use c['min_score']

    def score_for(self,symbol):
        return int(self.pair_scores.get(symbol,self.c['min_score']))

    def check(self,mt5,symbol,now):
        if not mt5.terminal_info() or not mt5.terminal_info().connected:
            raise ValueError('MT5 disconnected')
        if not mt5.symbol_select(symbol,True):raise ValueError('Confluence symbol unavailable')
        tick,info=mt5.symbol_info_tick(symbol),mt5.symbol_info(symbol)
        if tick is None or info is None:raise ValueError('Confluence quote unavailable')
        age=now-(tick.time-float(self.cfg.get('tick_timestamp_offset_hours',0))*3600)
        if age >= -5:check_stale(age,self.c['max_tick_age_seconds'],now,symbol)  # weekend / rollover -> MarketClosed
        if age < -5 or age > self.c['max_tick_age_seconds']:
            raise ValueError(f'Confluence stale/invalid quote age {age:.1f}s')
        if tick.bid<=0 or tick.ask<tick.bid or info.point<=0:raise ValueError('Confluence invalid bid/ask')
        spread=(tick.ask-tick.bid)/info.point
        if self.c['max_spread_points'] and spread>self.c['max_spread_points']:raise ValueError('Confluence spread limit exceeded')
        frames={}
        for name,seconds in [('M15',900),('H1',3600),('H4',14400)]:
            raw=mt5.copy_rates_from_pos(symbol,getattr(mt5,'TIMEFRAME_'+name),1,500)
            current=mt5.copy_rates_from_pos(symbol,getattr(mt5,'TIMEFRAME_'+name),0,1)
            if raw is None or len(raw)<250 or current is None or len(current)!=1:
                raise ValueError(f'Confluence needs >=250 closed {name} bars; load MT5 history')
            d=pd.DataFrame(raw).sort_values('time').reset_index(drop=True)
            if int(current[0]['time'])<=int(d.iloc[-1].time):raise ValueError(f'Cannot verify closed {name} bar')
            if d.time.duplicated().any() or not np.isfinite(d[['open','high','low','close']].to_numpy()).all():
                raise ValueError(f'Invalid {name} OHLC/timestamps')
            if ((d.high<d[['open','close','low']].max(axis=1))|(d.low>d[['open','close','high']].min(axis=1))).any():
                raise ValueError(f'Invalid {name} OHLC geometry')
            d['time']=d.time-int(float(self.cfg.get('bar_timestamp_offset_hours',0))*3600)
            bar_age=now-int(d.iloc[-1].time)
            if not seconds<=bar_age<=2*seconds+self.cfg.get('bar_grace_seconds',120):
                raise ValueError(f'Confluence stale/unclosed {name} bars; age={bar_age:.0f}s')
            frames[name]=d
        key=int(frames['M15'].iloc[-1].time)
        score=self.score_for(symbol)
        prior=self.cache['symbols'].get(symbol)
        if prior and prior['candle']==key and prior['result'].get('min_score')==score:
            result=json.loads(json.dumps(prior['result']))
            result.update(checked_utc=stamp(now),new_analysis=False)
            result['quote']=dict(bid=float(tick.bid),ask=float(tick.ask),spread=float(tick.ask-tick.bid),spread_points=float(spread),digits=int(info.digits),age_seconds=float(age))
            result['reason']=f'No new closed M15 candle. Last analysis: {result["analysis_utc"]}. '+result['reason']
            return result
        f={k:indicators(v) for k,v in frames.items()}
        # the dashboard score (default or this pair's own) decides qualification, not config.json
        cfg=dict(self.cfg,confluence=dict(self.cfg.get('confluence') or {},min_score=score))
        result=evaluate(f['M15'],f['H1'],f['H4'],tick.bid,tick.ask,info.point,info.digits,cfg,now)
        result.update(symbol=symbol,new_analysis=True)
        try:
            from trade_review import market_snapshot
            result['review_market']=market_snapshot(f)
        except Exception:
            logging.warning('Confluence research timeframe capture unavailable')
        result['quote']['age_seconds']=float(age)
        self.cache['symbols'][symbol]=dict(candle=key,result=result)
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.cache,allow_nan=False));tmp.replace(self.path)
        return result


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
