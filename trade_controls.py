"""Validated controls and broker-currency risk calculations."""
import math
import re
from decimal import Decimal, ROUND_FLOOR
DEFAULTS=dict(pair_lots={},risk_enabled=True,risk_percent=.5,partial_enabled=False,partial_percent=50.,breakeven_enabled=False,breakeven_offset_points=0.,trailing_enabled=False,trailing_start_r=1.,trailing_distance_r=1.,max_positions=6)

def validate(values):
    if not isinstance(values,dict) or set(values)-set(DEFAULTS):raise ValueError('Unknown trade control')
    d={**DEFAULTS,**values}
    for k in ('risk_enabled','partial_enabled','breakeven_enabled','trailing_enabled'):
        if not isinstance(d[k],bool):raise ValueError(k+' must be boolean')
    for k,lo,hi in [('risk_percent',.01,2),('partial_percent',1,99),('breakeven_offset_points',0,1000),('trailing_start_r',.1,10),('trailing_distance_r',.1,10)]:
        v=d[k]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:raise ValueError(k+' outside permitted range')
    cap=d['max_positions']
    if isinstance(cap,float) and cap.is_integer():cap=int(cap)
    if isinstance(cap,bool) or not isinstance(cap,int) or not 1<=cap<=30:raise ValueError('max_positions must be a whole number from 1 to 30')
    d['max_positions']=cap
    lots=d['pair_lots']
    if not isinstance(lots,dict) or len(lots)>50:raise ValueError('Invalid pair lot map')
    clean={}
    for symbol,volume in lots.items():
        if not isinstance(symbol,str) or not re.fullmatch(r'[A-Za-z0-9_.#-]{1,64}',symbol):raise ValueError('Invalid exact broker symbol')
        if isinstance(volume,bool) or not isinstance(volume,(int,float)) or not math.isfinite(volume) or not 0<volume<=100:raise ValueError('Pair lot must be positive, finite and <=100')
        clean[symbol]=float(volume)
    d['pair_lots']=clean
    return d

def entry_sizing(settings,symbol,fallback=.01):
    """Exact broker symbol overrides are shared across strategies; no suffix guessing."""
    fixed=settings.get('pair_lots',{}).get(symbol)
    return ({**settings,'risk_enabled':False},fixed) if fixed is not None else (settings,fallback)

def controls(store,cfg):
    import json
    d={**DEFAULTS,**cfg.get('trade_controls',{})}
    # Older configurations retain fixed-lot execution until explicitly upgraded.
    if 'trade_controls' not in cfg:d['risk_enabled']=False
    # Until saved in the dashboard, the cap comes from config.json (real_execution / demo_execution max_positions).
    d['max_positions']=int((cfg.get('real_execution') or cfg.get('demo_execution') or {}).get('max_positions',d['max_positions']))
    with store.connect() as db:
        r=db.execute("SELECT value FROM meta WHERE key='trade_controls'").fetchone()
    return validate({**d,**(json.loads(r['value']) if r else {})})

def floor_volume(value,step):
    return float((Decimal(str(value))/Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR)*Decimal(str(step)))

def estimate(mt5,symbol,side,entry,sl,tp1,tp2,settings,fixed_lot=.01):
    settings,fixed_lot=entry_sizing(settings,symbol,fixed_lot)
    a=mt5.account_info();info=mt5.symbol_info(symbol)
    if a is None or info is None:raise ValueError('Risk sizing: account/symbol unavailable')
    equity=float(a.equity)
    if not math.isfinite(equity) or equity<=0:raise ValueError('Risk sizing requires positive equity')
    action=mt5.ORDER_TYPE_BUY if side=='BUY' else mt5.ORDER_TYPE_SELL
    minimum,maximum,step=map(float,(info.volume_min,info.volume_max,info.volume_step))
    if not (step>0 and minimum>0 and maximum>=minimum):raise ValueError('Invalid broker volume limits')
    def pnl(volume,price):
        p=mt5.order_calc_profit(action,symbol,volume,entry,price)
        if p is None or not math.isfinite(float(p)):raise ValueError('Broker profit calculation unavailable')
        return float(p)
    unit=pnl(minimum,sl)
    if unit>=0:raise ValueError('SL must imply a loss')
    budget=equity*settings['risk_percent']/100
    volume=floor_volume(min(maximum,budget/(-unit/minimum)),step) if settings['risk_enabled'] else fixed_lot
    if volume<minimum or volume>maximum or abs(volume/step-round(volume/step))>1e-6:raise ValueError('Risk lot below minimum or off volume grid; no upward rounding')
    loss=-pnl(volume,sl);gain1=pnl(volume,tp1);gain2=pnl(volume,tp2)
    if gain1<=0 or gain2<=0:raise ValueError('TP must imply a profit')
    if settings['risk_enabled'] and loss>budget+1e-7:raise ValueError('Rounded volume exceeds risk budget')
    return dict(volume=volume,equity=equity,risk_percent=settings['risk_percent'] if settings['risk_enabled'] else loss/equity*100,budget=budget if settings['risk_enabled'] else loss,estimated_loss=loss,estimated_tp1=gain1,estimated_tp2=gain2,rr1=gain1/loss,rr2=gain2/loss,currency=a.currency,basis='Broker order_calc_profit; before commission, swap, gaps and slippage. Full-volume TP estimates; partial/SL management changes outcomes.')
