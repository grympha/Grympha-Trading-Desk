"""Read-only MT5 calendar diagnostic. Never imports execution modules or sends orders."""
import json
from pathlib import Path
from mt5_calendar_filter import MT5CalendarFilter

def main():
    root=Path(__file__).resolve().parent
    cfg=json.loads((root/'config.json').read_text(encoding='utf-8-sig'))
    import MetaTrader5 as mt5
    kw={'path':cfg['terminal_path']} if cfg.get('terminal_path') else {}
    if not mt5.initialize(**kw):raise SystemExit('Cannot initialize configured MT5 terminal')
    try:
        t=mt5.terminal_info()
        if t is not None:print('Expected calendar file:',Path(t.data_path)/'MQL5'/'Files'/'bbma_mt5_calendar.json')
        f=MT5CalendarFilter(cfg,mt5);status=f.status(cfg['symbols'])
        print('Configured provider:',cfg.get('news_filter',{}).get('provider','mt5'))
        print(json.dumps(status,indent=2,ensure_ascii=True))
        print('Read-only diagnostic complete. No order sent.')
    finally:mt5.shutdown()
if __name__=='__main__':main()
