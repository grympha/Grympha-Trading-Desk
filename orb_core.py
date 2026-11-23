"""Deterministic, read-only ORB signal engine. All timestamps are UTC epochs."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math

UTC = timezone.utc
VERSION = 'grympha.orb.retest.v1'

@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float


def iso(t):
    return datetime.fromtimestamp(t, UTC).isoformat()


def ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    out[period-1] = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1-alpha) * out[i-1]
    return out


def atr(bars, period):
    # Explicit SMA of true ranges; see README for indicator parity limitations.
    tr = [max(b.high-b.low, abs(b.high-bars[i-1].close), abs(b.low-bars[i-1].close))
          if i else b.high-b.low for i,b in enumerate(bars)]
    return [sum(tr[i-period+1:i+1])/period if i >= period else None
            for i in range(len(bars))]


def validate_bars(bars):
    if any(b.time >= c.time for b,c in zip(bars,bars[1:])):
        raise ValueError('Bars must be unique and strictly increasing')
    for b in bars:
        if not all(math.isfinite(v) and v > 0 for v in (b.open,b.high,b.low,b.close)):
            raise ValueError('Invalid OHLC price')
        if b.low > min(b.open,b.close) or b.high < max(b.open,b.close) or b.high < b.low:
            raise ValueError('Invalid OHLC bounds')


def validate_config(c):
    if c['symbol_base'] != 'XAUUSD':
        raise ValueError('v1 supports only an explicitly mapped XAUUSD symbol')
    off = c['session_utc_offset_hours']
    if off is None or not isinstance(off,(int,float)) or not -12 <= off <= 14:
        raise ValueError('Set session_utc_offset_hours to the VERIFIED broker server offset')
    if not 0 <= c['range_start_hour'] < c['range_end_hour'] <= c['trade_start_hour'] < c['trade_end_hour'] <= 24:
        raise ValueError('Sessions must be same-day, ordered, non-overlapping')
    if c['ema_period'] < 2 or c['atr_period'] < 2 or c['h4_ema_period'] < 2:
        raise ValueError('Indicator periods must be >= 2')
    if not 0 < c['min_range_points'] < c['max_range_points']:
        raise ValueError('Invalid range limits')
    if c['max_bars_after_breakout'] < 1 or c['max_spread_points'] <= 0:
        raise ValueError('Invalid expiry/spread')
    for key in ('sl_multiplier','max_signal_age_seconds','max_tick_age_seconds','poll_seconds'):
        if c[key] <= 0:
            raise ValueError('Invalid ' + key)
    if not 0 <= c['max_retest_penetration_fraction'] <= 1:
        raise ValueError('Invalid retest penetration fraction')
    if c['news_mode'] not in ('required','off'):
        raise ValueError('news_mode must be required or off')


def news_check(c, now, document):
    if c['news_mode'] == 'off':
        return True, 'DISABLED_BY_CONFIG'
    if not document:
        return False, 'NEWS_DATA_MISSING'
    try:
        def parse(s):
            d = datetime.fromisoformat(s.replace('Z','+00:00'))
            if d.tzinfo is None:
                raise ValueError('News timestamps require timezone')
            return d.timestamp()
        # Coverage is an explicit provider/operator attestation, not inferred from event dates.
        if not parse(document['coverage_from_utc']) <= now < parse(document['coverage_to_utc']):
            return False, 'NEWS_COVERAGE_EXPIRED_OR_MISSING'
        age = now - parse(document['generated_at_utc'])
        if age < -5 or age > c['news_max_age_hours'] * 3600:
            return False, 'NEWS_DATA_STALE'
        for ev in document['events']:
            t = parse(ev['time_utc'])
            if ev['currency'].upper() == 'USD' and ev['impact'].upper() == 'HIGH':
                if t-c['news_before_minutes']*60 <= now <= t+c['news_after_minutes']*60:
                    return False, 'NEWS_BLOCK: ' + ev['name']
        return True, 'CLEAR_WITHIN_DECLARED_COVERAGE'
    except (KeyError, ValueError, TypeError, AttributeError):
        return False, 'NEWS_DATA_INVALID'


def scan(c, m5, h4, now, quote, spec, news=None):
    """Reconstruct today's state; return status and at most one NEW closed-bar candidate.

    No trades, no balance sizing. Historical signals are not delivered on restart.
    The runner enforces persistent one-emission-per-session and duplicate protection.
    """
    validate_config(c)
    m5 = [b for b in m5 if b.time+300 <= now]
    h4 = [b for b in h4 if b.time+14400 <= now]
    validate_bars(m5); validate_bars(h4)
    def result(status, **extra):
        return {'status':status, 'asof_utc':iso(now), 'signal':None, **extra}
    offset = timedelta(hours=c['session_utc_offset_hours'])
    local = datetime.fromtimestamp(now,UTC) + offset
    session = local.date().isoformat()
    day = (local.replace(hour=0,minute=0,second=0,microsecond=0)-offset).timestamp()
    if local.month in c['skip_months'] or local.weekday() in c['skip_weekdays']:
        return result('SKIPPED_SESSION', session=session)
    start = day+c['range_start_hour']*3600
    end = day+c['range_end_hour']*3600
    trade_start = day+c['trade_start_hour']*3600
    trade_end = day+c['trade_end_hour']*3600
    if now < end:
        return result('BUILDING_RANGE', session=session)
    if not trade_start <= now < trade_end:
        return result('OUTSIDE_TRADE_WINDOW', session=session)
    rb = [b for b in m5 if start <= b.time < end]
    expected = list(range(int(start),int(end),300))
    if [b.time for b in rb] != expected:
        return result('INCOMPLETE_RANGE', expected_bars=len(expected), actual_bars=len(rb))
    hi,lo = max(b.high for b in rb),min(b.low for b in rb)
    point = spec['point']
    if not math.isfinite(point) or point <= 0 or not math.isfinite(spec['tick_size']) or spec['tick_size'] <= 0:
        return result('INVALID_SYMBOL_SPEC')
    size = hi-lo
    rp = size/point
    if not c['min_range_points'] <= rp <= c['max_range_points']:
        return result('RANGE_FILTERED', range_points=rp)
    if len(m5) < max(c['ema_period']*3,c['atr_period']+1) or len(h4) < c['h4_ema_period']*3:
        return result('INDICATOR_WARMUP_INCOMPLETE')
    ev = ema([b.close for b in m5],c['ema_period'])
    av = atr(m5,c['atr_period'])
    hv = ema([b.close for b in h4],c['h4_ema_period'])
    # Require contiguous M5 data after session start; never invent missing bars.
    today_bars = [b for b in m5 if start <= b.time < trade_end]
    if any(b.time-a.time != 300 for a,b in zip(today_bars,today_bars[1:])):
        return result('SESSION_DATA_GAP')
    pending = {'BUY':None,'SELL':None}
    candidate = None
    for i,b in enumerate(m5):
        close_time = b.time+300
        if not trade_start <= close_time < trade_end:
            continue
        if ev[i] is None or av[i] is None:
            continue
        # Choose H4 candle that was FULLY CLOSED at this M5 decision timestamp.
        eligible = [j for j,h in enumerate(h4) if h.time+14400 <= close_time]
        if not eligible:
            continue
        j = eligible[-1]
        if hv[j] is None or close_time-(h4[j].time+14400) >= 14400:
            continue
        trend = 'BUY' if h4[j].close > hv[j] else 'SELL'
        for side in ('BUY','SELL'):
            buy = side == 'BUY'
            p = pending[side]
            invalid = b.close < lo if buy else b.close > hi
            if p and (i-p['index'] > c['max_bars_after_breakout'] or invalid):
                pending[side] = p = None
            aligned = b.close > ev[i] if buy else b.close < ev[i]
            if p and b.time > p['time']:
                touch = b.low <= hi+c['retest_tolerance_points']*point if buy else b.high >= lo-c['retest_tolerance_points']*point
                held = b.close > hi if buy else b.close < lo
                depth_ok = b.low >= hi-size*c['max_retest_penetration_fraction'] if buy else b.high <= lo+size*c['max_retest_penetration_fraction']
                if touch and held and depth_ok and aligned and trend == side:
                    if i == len(m5)-1:
                        candidate = dict(side=side,breakout_time=p['time'],retest_time=b.time,
                                         close_time=close_time,atr=av[i],ema=ev[i],h4_time=h4[j].time,
                                         h4_ema=hv[j],close=b.close)
                    pending[side] = None
                    continue
            breakout = b.close > hi+size*c['dynamic_buffer_pct']/100 if buy else b.close < lo-size*c['dynamic_buffer_pct']/100
            if pending[side] is None and breakout and aligned:
                pending[side] = {'time':b.time,'index':i}
    if not candidate:
        return result('WAIT_RETEST' if any(pending.values()) else 'WAIT_BREAKOUT',session=session,
                      range_high=hi,range_low=lo,range_points=rp,pending=pending)
    if now-candidate['close_time'] > c['max_signal_age_seconds']:
        return result('STALE_SIGNAL_SUPPRESSED', session=session)
    if not all(math.isfinite(quote[k]) and quote[k] > 0 for k in ('bid','ask','time')):
        return result('INVALID_QUOTE')
    if quote['ask'] < quote['bid']:
        return result('INVALID_QUOTE')
    if not -5 <= now-quote['time'] <= c['max_tick_age_seconds']:
        return result('STALE_OR_FUTURE_QUOTE')
    spread = (quote['ask']-quote['bid'])/point
    if spread > c['max_spread_points']:
        return result('SPREAD_BLOCKED',spread_points=spread)
    ok,news_status = news_check(c,now,news)
    if not ok:
        return result(news_status)
    buy = candidate['side'] == 'BUY'
    price = quote['ask'] if buy else quote['bid']
    if (buy and price <= hi) or (not buy and price >= lo):
        return result('LEVEL_LOST_BEFORE_EMISSION')
    if abs(price-candidate['close']) > c['max_entry_drift_atr']*candidate['atr']:
        return result('ENTRY_DRIFT_BLOCKED')
    raw_sl = lo-c['buy_sl_buffer_points']*point if buy else hi+c['sell_sl_buffer_points']*point
    sl = price-(price-raw_sl)*c['sl_multiplier']
    rr = 2.5 if rp < 2500 else (1.5 if rp > 5000 else 2.0)
    if abs(candidate['close']-candidate['ema'])/candidate['atr'] >= 3:
        rr += .3
    rr = min(rr,3.0)
    ts = spec['tick_size']
    sl = (math.floor(sl/ts) if buy else math.ceil(sl/ts))*ts
    risk = price-sl if buy else sl-price
    if risk <= 0:
        return result('INVALID_STOP')
    tp = price+risk*rr if buy else price-risk*rr
    tp = (math.floor(tp/ts) if buy else math.ceil(tp/ts))*ts
    sl,tp = round(sl,spec['digits']),round(tp,spec['digits'])
    fingerprint = hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest()[:16]
    identity = f"{VERSION}|{c['symbol']}|{session}|{candidate['side']}|{candidate['breakout_time']}|{candidate['retest_time']}"
    sig = {'schema_version':'1.0','strategy_id':VERSION,'signal_id':hashlib.sha256(identity.encode()).hexdigest(),
           'config_hash':fingerprint,'symbol':c['symbol'],'timeframe':'M5','session_date':session,
           'direction':candidate['side'],'status':'CANDIDATE_REQUIRES_RISK_CHECK','execution_authorized':False,
           'generated_at_utc':iso(now),'signal_close_utc':iso(candidate['close_time']),
           'expires_at_utc':iso(candidate['close_time']+c['max_signal_age_seconds']),
           'breakout_bar_utc':iso(candidate['breakout_time']),'retest_bar_utc':iso(candidate['retest_time']),
           'entry_reference':price,'sl_suggested':sl,'tp_suggested':tp,'initial_risk_price':risk,
           'rr_requested':rr,'rr_after_rounding':abs(tp-price)/risk,'spread_points':spread,
           'range_high':hi,'range_low':lo,'range_points':rp,'ema200':candidate['ema'],'atr14':candidate['atr'],
           'h4_closed_bar_utc':iso(candidate['h4_time']),'h4_ema20':candidate['h4_ema'],
           'news_status':news_status,'session_utc_offset_hours':c['session_utc_offset_hours'],
           'required_checks':['expiry','fresh_quote','spread','news','account_exposure','daily_equity_loss',
                              'lot_sizing','margin','broker_stop_distance','idempotency'],
           'management_proposal':{'break_even_at_initial_R':.75,'break_even_lock_points':20,
                                  'max_hold_hours':24,'friday_close_server':'21:00','partial_close':False}}
    return result('SIGNAL_CANDIDATE',session=session) | {'signal':sig}
