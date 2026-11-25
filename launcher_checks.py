"""Read-only readiness checks executed in a separate Python process."""
import importlib,importlib.metadata,json,os,platform,re,struct,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PACKAGES={'MetaTrader5':'MetaTrader5','numpy':'numpy','pandas':'pandas','python-dotenv':'dotenv','ta':'ta'}

def package_checks():
    rows=[]
    for package,module in PACKAGES.items():
        try:
            importlib.import_module(module);version=importlib.metadata.version(package)
            parts=tuple(int(v) for v in re.findall(r'\d+',version)[:3]);parts=parts+(0,)*(3-len(parts))
            ok={'MetaTrader5':(5,0,45)<=parts<(6,0,0),'numpy':(1,26,0)<=parts<(3,0,0),'pandas':(2,1,0)<=parts<(3,0,0),'python-dotenv':(1,0,0)<=parts<(2,0,0),'ta':parts==(0,11,0)}[package]
            rows.append(dict(name=package,status='PASS' if ok else 'FAIL',detail=version,guide='' if ok else 'Click Setup / Install requirements to install supported versions.'))
        except Exception:
            rows.append(dict(name=package,status='FAIL',detail='Missing or cannot import',guide='Click Setup / Install requirements. Internet access to PyPI is required.'))
    return rows

def inspect_mt5(cfg,mt5):
    rows=[];terminal_data_path=None
    def add(name,status,detail,guide=''):rows.append(dict(name=name,status=status,detail=detail,guide=guide))
    kw={'path':cfg['terminal_path']} if cfg.get('terminal_path') else {}
    if not mt5.initialize(**kw):
        add('MT5 terminal','FAIL','Cannot initialize selected terminal','Install/open MT5, log in, then use Choose MT5 terminal in this app.');return rows,None
    try:
        t=mt5.terminal_info();a=mt5.account_info()
        terminal_data_path=getattr(t,'data_path',None)
        connected=bool(t and t.connected)
        add('MT5 connection','PASS' if connected else 'FAIL','Connected' if connected else 'Disconnected','Open the intended MT5 terminal, log in and check internet.' if not connected else '')
        if a is None:
            add('Account type','FAIL','Account unavailable','Log into your intended account in MT5.');return rows,terminal_data_path
        mode={0:'DEMO',1:'CONTEST',2:'REAL'}.get(a.trade_mode,'UNKNOWN')
        real=cfg.get('real_execution',{}) or {}
        matches=(getattr(a,'login',None),getattr(a,'server',None))==(real.get('account_login'),real.get('account_server'))
        # Informational: which execution modes this account allows. The Start button enforces the match per mode.
        if mode=='DEMO':
            add('Account type','PASS','DEMO · Analysis and DEMO execution available','REAL execution needs MT5 logged into a REAL account.',)
        elif mode=='REAL' and matches:
            add('Account type','PASS','REAL · matches saved REAL account · Analysis and REAL execution available','DEMO execution needs MT5 logged into a DEMO account.')
        elif mode=='REAL':
            add('Account type','WARN','REAL · not the saved REAL account · Analysis only','Save this account in REAL execution account (Use logged-in MT5 account) to allow REAL execution.')
        else:
            add('Account type','WARN',mode+' · Analysis only','DEMO or REAL execution needs a DEMO or REAL account.')
        rows[-1]['account_mode']=mode;rows[-1]['matches_real']=bool(mode=='REAL' and matches)
        hedging=getattr(a,'margin_mode',None)==2
        add('Hedging','PASS' if hedging else 'FAIL','Hedging' if hedging else 'Netting / unsupported','Use a hedging account so both strategies can hold independent positions.' if not hedging else '')
        permissions=bool(t and getattr(t,'trade_allowed',False) and not getattr(t,'tradeapi_disabled',True) and getattr(a,'trade_allowed',False) and getattr(a,'trade_expert',False))
        add('Algo permissions','PASS' if permissions else 'FAIL','Enabled' if permissions else 'Not enabled','For DEMO/REAL execution enable Algo Trading and permit external Python trading in MT5 settings.' if not permissions else '')
        if not connected:return rows,terminal_data_path
        symbols=list(dict.fromkeys(cfg.get('symbols',[])+cfg.get('confluence',{}).get('symbols',[])))
        for symbol in symbols:
            if not mt5.symbol_select(symbol,True):
                add(symbol,'FAIL','Symbol unavailable','Add exact broker symbol in Market Watch, then use Configure 9 pairs.');continue
            tick=mt5.symbol_info_tick(symbol)
            age=time.time()-(getattr(tick,'time',0)-float(cfg.get('tick_timestamp_offset_hours',0))*3600)
            good=bool(tick and tick.bid>0 and tick.ask>=tick.bid and -5<=age<=cfg.get('max_tick_age_seconds',60))
            closed=None
            if not good and tick and tick.bid>0 and age>cfg.get('max_tick_age_seconds',60):
                from market_hours import describe,trades_24_7
                closed=None if trades_24_7(symbol) else describe()
            if closed:add(symbol+' quote','CLOSED',closed+f' · last quote {age/3600:.1f} h ago','Normal outside market hours. The bot waits and resumes by itself when prices update.')
            elif not good and tick and tick.bid>0 and abs(age)>=1800 and abs(age/3600-round(age/3600))<=0.1:
                # A whole number of hours off: the broker clock offset is not set (new install) or changed (DST).
                offset=float(cfg.get('tick_timestamp_offset_hours',0) or 0);broker=round(offset-age/3600)
                add(symbol+' quote','FAIL',f'Clock offset mismatch: broker clock looks UTC{broker:+d}, offset set to {offset:g} h',
                    'Click Set clock offset (measures the broker clock from live quotes and saves it), then Check requirements again.')
            else:add(symbol+' quote','PASS' if good else 'FAIL','Fresh quote' if good else 'Stale/missing quote or clock mismatch','Check market hours, Windows time and Time diagnostics, or click Set clock offset.' if not good else '')
            missing=[]
            for name in ('H4','H1','M15','M5'):
                rates=mt5.copy_rates_from_pos(symbol,getattr(mt5,'TIMEFRAME_'+name),1,400)
                minimum=150 if name=='M5' else 250
                if rates is None or len(rates)<minimum:missing.append(name)
            add(symbol+' history','PASS' if not missing else 'FAIL','Loaded' if not missing else 'Missing '+', '.join(missing),'Open those timeframes in MT5, load history and recheck.' if missing else '')
        from news_filter import make_news_filter
        news=make_news_filter(cfg,mt5);status=news.status(symbols)
        if not status['enabled']:
            add('News filter','WARN','Disabled','Click Enable MT5 news if you want news blackout protection.')
        else:
            blocked=[(s,g) for s,g in status['symbols'].items() if g['status']=='UNAVAILABLE']
            from market_hours import describe
            closed=describe()
            if blocked and closed:add('News filter','CLOSED','Waiting for the market to open · the calendar clock check needs live prices','Normal outside market hours; it verifies itself once quotes update.')
            elif blocked:add('News filter','FAIL',blocked[0][1]['note'],'Install/attach Calendar Bridge; run Calendar diagnostics. New entries stay blocked while feed unavailable.')
            else:add('News filter','PASS',status['provider']+' ready (scheduled blackouts may still block entries)')
    finally:mt5.shutdown()
    return rows,terminal_data_path

def run():
    report=dict(rows=[],terminal_data_path=None)
    def add(n,s,d,g=''):report['rows'].append(dict(name=n,status=s,detail=d,guide=g))
    correct=sys.version_info[:2]==(3,11) and struct.calcsize('P')==8 and platform.system()=='Windows'
    add('Python','PASS' if correct else 'FAIL',platform.python_version()+' / '+str(struct.calcsize('P')*8)+'-bit / '+platform.system(),'Install Windows Python 3.11 64-bit with pip and Tcl/Tk.' if not correct else '')
    packages=package_checks();report['rows']+=packages
    try:
        cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
        if not cfg.get('symbols') or not isinstance(cfg.get('confluence',{}),dict):raise ValueError()
        add('Configuration','PASS',str(len(cfg['symbols']))+' BBMA symbols / '+str(len(cfg.get('confluence',{}).get('symbols',[])))+' Confluence symbols')
    except Exception:
        add('Configuration','FAIL','Missing/invalid config.json','Click Setup for first installation. Keep existing config/backups; use Open folder to review invalid JSON.');return report
    terminal_path=cfg.get('terminal_path','')
    selected=bool(terminal_path and Path(terminal_path).is_file() and Path(terminal_path).name.lower()=='terminal64.exe')
    add('Selected MT5 terminal','PASS' if selected else 'FAIL','Explicit terminal selected' if selected else 'Terminal path not selected/invalid','Use Choose MT5 terminal. Explicit selection is required before launcher DEMO execution.' if not selected else '')
    if (ROOT/'.env').exists() and all(r['status']=='PASS' for r in packages):
        from dotenv import dotenv_values
        env=dotenv_values(ROOT/'.env')
        token=bool(os.getenv('TELEGRAM_BOT_TOKEN') or env.get('TELEGRAM_BOT_TOKEN'));chat=bool(os.getenv('TELEGRAM_CHAT_ID') or env.get('TELEGRAM_CHAT_ID'))
        flag=str(env.get('TELEGRAM_ENABLED') or '').lower()
        if flag in ('0','false','no','off'):token=chat=False  # switched off in the launcher: optional, not a failure
        add('Telegram','PASS' if token==chat else 'FAIL','Configured' if token and chat else 'Optional alerts disabled' if not token and not chat else 'Only one Telegram field is configured','Set BOTH token/chat ID, or leave BOTH blank. Values are never displayed.' if token!=chat else '')
    else:add('Telegram','WARN','Optional environment file/packages not ready','Setup creates a blank example. Enter Telegram credentials locally if desired.')
    if not os.access(ROOT,os.W_OK):add('Folder permissions','FAIL','Folder is not writable','Extract into Documents or another writable folder, not Program Files.')
    else:add('Folder permissions','PASS','Writable')
    if all(r['status']=='PASS' for r in packages):
        try:
            import MetaTrader5 as mt5
            rows,report['terminal_data_path']=inspect_mt5(cfg,mt5);report['rows']+=rows
        except Exception:add('MT5 checks','FAIL','Readiness check unavailable','Run Calendar/Time diagnostics and review terminal configuration.')
    return report

if __name__=='__main__':print(json.dumps(run(),ensure_ascii=True))
