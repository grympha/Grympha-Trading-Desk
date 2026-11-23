"""Enable MT5 calendar provider, preserving watchlists, offsets and saved controls."""
import json,shutil,os
from pathlib import Path
from datetime import datetime

def main():
    root=Path(__file__).resolve().parent;p=root/'config.json'
    if not p.exists():raise SystemExit('config.json missing. Copy config.example.json only for a new installation.')
    cfg=json.loads(p.read_text(encoding='utf-8-sig'))
    backup=p.with_name('config.before_v1_8.'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json');shutil.copy2(p,backup)
    cfg.setdefault('news_filter',{}).update(provider='mt5',enabled=True)
    cfg['news_filter'].setdefault('before_minutes',30);cfg['news_filter'].setdefault('after_minutes',30)
    temp=p.with_suffix('.v18.tmp');temp.write_text(json.dumps(cfg,indent=2)+'\n',encoding='utf-8');os.replace(temp,p)
    print('MT5 Calendar enabled. Backup:',backup.name)
    print('Watchlists, offsets, risk, database and saved controls retained. No order sent.')
    print('Install Calendar Bridge in THIS terminal. Run: py -3.11 diagnose_mt5_calendar.py')
if __name__=='__main__':main()
