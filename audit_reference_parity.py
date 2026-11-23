import ast,sys,logging,re,json
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone,timedelta
from typing import Any
import numpy as np,pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator,MACD
from ta.volatility import AverageTrueRange,BollingerBands
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
from confluence_strategy import indicators,evaluate
src=(BASE/'reference'/'xauusd_bot_v4_original.py').read_text()
tree=ast.parse(src)
functions=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('indicators','analyze')],type_ignores=[])
compiled=compile(functions,'uploaded_reference','exec')
now=datetime.now(timezone.utc);now_s=int(now.timestamp())
checked=0;qualified=0
for seed in range(48):
 rng=np.random.default_rng(seed);frames={}
 for name,secs in [('M15',900),('H1',3600),('H4',14400)]:
  slope=[-.025,0,.025,.1][seed%4]
  # A range of trends/oscillations exercises LONG/SHORT/HOLD and differing scores.
  close=100+np.arange(500)*slope+np.sin(np.arange(500)*(.03+seed*.002))*1.5+rng.normal(0,.08,500)
  op=close+rng.normal(0,.05,500)
  t=(now_s//secs)*secs-secs-np.arange(499,-1,-1)*secs
  frames[name]=pd.DataFrame(dict(time=t,open=op,high=np.maximum(op,close)+.2,low=np.minimum(op,close)-.2,close=close))
 bid=float(frames['M15'].iloc[-1].close);ask=bid+.03
 rows=[];messages=[]
 namespace=dict(pd=pd,Any=Any,datetime=datetime,timezone=timezone,timedelta=timedelta,RSIIndicator=RSIIndicator,EMAIndicator=EMAIndicator,MACD=MACD,AverageTrueRange=AverageTrueRange,BollingerBands=BollingerBands,
  TIMEFRAMES={'M15':None,'H1':None,'H4':None},SYMBOL='XAUUSD',SR_LOOKBACK_H1=50,SR_NEAR_PCT=.004,SL_ATR_MULTIPLIER=1.5,TP1_ATR_MULTIPLIER=2.,TP2_ATR_MULTIPLIER=3.5,MAX_SPREAD_POINTS=0,MAX_TICK_AGE_SECONDS=180,BROKER_TIMESTAMP_OFFSET_HOURS=2,MIN_SCORE=3,
  log=logging.getLogger('parity'),append_decision=lambda r:rows.append(r),save_state=lambda s:None,send_telegram=lambda msg:messages.append(msg) or True,
  mt5=SimpleNamespace(terminal_info=lambda:object(),account_info=lambda:object(),symbol_info=lambda s:SimpleNamespace(point=.01,digits=2),symbol_info_tick=lambda s:SimpleNamespace(bid=bid,ask=ask,time=now_s+7200)))
 def download(name):
  df=frames[name].rename(columns={k:k.capitalize() for k in ['open','high','low','close']}).copy()
  df.index=pd.to_datetime(df.pop('time')+7200,unit='s',utc=True)
  return df
 namespace['download_closed_bars']=download
 exec(compiled,namespace)
 namespace['analyze']({})
 got=evaluate(*(indicators(frames[n]) for n in ['M15','H1','H4']),bid,ask,.01,2,{'symbols':['XAUUSD'],'confluence':{'min_score':3}},now_s)
 ref=rows[0]
 assert got['score']==ref['score'],(seed,got['score'],ref['score'])
 assert got['condition']['side']=={'LONG':'BUY','SHORT':'SELL','HOLD':None}[ref['direction']]
 assert bool(got['entry_plan'])==bool(messages),(seed,'qualification mismatch')
 if messages:
  vals=[float(v) for v in re.findall(r'<code>([0-9.]+)</code>',messages[0])]
  plan=got['entry_plan']
  assert [plan['stop_loss'],plan['tp1'],plan['tp2']]==vals,(seed,plan,vals)
  assert plan['entry']==ref['price']
  qualified+=1
 checked+=1
print(json.dumps(dict(scenarios=checked,qualified_scenarios=qualified,score_direction_qualification_entry_sl_tp_parity='PASS')))
