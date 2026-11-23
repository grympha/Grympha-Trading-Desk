"""Read-only MT5 symbol validation, then backed-up watchlist update. Sends no orders."""
import json
from pathlib import Path
from datetime import datetime
import shutil
import os

PAIRS=['XAUUSD','GBPJPY','EURUSD','GBPUSD','USDJPY','AUDUSD','USDCAD','USDCHF','NZDUSD']

def resolve(base,known,available):
    choices={s.name:s for s in available}
    saved=[n for n in known if base in n.upper() and n in choices]
    if len(set(saved))==1:return saved[0]
    visible=[n for n,s in choices.items() if base in n.upper() and s.visible]
    if len(visible)==1:return visible[0]
    if base in choices and choices[base].visible:return base
    if len(visible)>1:raise ValueError(base+': multiple visible symbols: '+', '.join(sorted(visible))+'. Keep the intended symbol in existing config first.')
    raise ValueError(base+': no matching visible symbol. Add it to Market Watch.')

def main():
    root=Path(__file__).resolve().parent;p=root/'config.json'
    if not p.exists():raise SystemExit('Place this script beside your existing config.json. No configuration changed.')
    cfg=json.loads(p.read_text(encoding='utf-8-sig'))
    import MetaTrader5 as mt5
    kw={'path':cfg['terminal_path']} if cfg.get('terminal_path') else {}
    if not mt5.initialize(**kw):raise SystemExit('Cannot initialize intended MT5 terminal. No configuration changed.')
    try:
        t=mt5.terminal_info()
        if t is None or not t.connected:raise RuntimeError('MT5 disconnected. No configuration changed.')
        symbols=mt5.symbols_get()
        if not symbols:raise RuntimeError('MT5 symbol list unavailable. No configuration changed.')
        known=list(cfg.get('symbols',[]))+list(cfg.get('confluence',{}).get('symbols',[]))
        names=[resolve(base,known,symbols) for base in PAIRS]
        if len(set(names))!=9:raise ValueError('Symbol resolution is not unique. No configuration changed.')
        majors=set(names)
        keep=lambda lst:[s for s in lst if s not in majors and not any(b in s.upper() for b in PAIRS)]
        extra_bbma=keep(cfg.get('symbols',[]));extra_conf=keep(cfg.get('confluence',{}).get('symbols',[]))
        cfg['symbols']=names+extra_bbma
        cfg.setdefault('confluence',{}).update(symbols=names+extra_conf,min_score=cfg.get('confluence',{}).get('min_score',4))
        backup=p.with_name('config.before_major_pairs.'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
        shutil.copy2(p,backup)
        temp=p.with_name('config.major_pairs.tmp')
        temp.write_text(json.dumps(cfg,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        os.replace(temp,p)
        print('BBMA and Confluence watchlists updated:')
        print(', '.join(cfg['symbols']))
        if extra_bbma or extra_conf:print('Extra symbols kept:',', '.join(dict.fromkeys(extra_bbma+extra_conf)))
        print('Backup:',backup.name)
        print('Offsets, risk, news configuration, limits, credentials and database retained. No order sent.')
        print('Load H4/H1/M15/M5 history for each symbol. Restart bot and refresh dashboard with Ctrl+F5.')
    finally:mt5.shutdown()

if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit(str(exc))
