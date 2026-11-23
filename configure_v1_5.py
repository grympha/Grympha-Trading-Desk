"""Apply the requested 4/5 threshold and same-pair tracking policy, preserving other settings."""
from datetime import datetime
import json
from pathlib import Path
import shutil


def main():
    root=Path(__file__).resolve().parent;p=root/'config.json'
    if not p.exists():raise SystemExit('config.json missing; create it from config.example.json first.')
    c=json.loads(p.read_text())
    if not isinstance(c.get('symbols'),list) or not c['symbols']:raise SystemExit('config.json needs a nonempty symbols list.')
    backup=root/('config.before_v1_5_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
    shutil.copy2(p,backup)
    c.setdefault('confluence',{}).update(enabled=True,min_score=4,symbols=list(c['symbols']))
    c.setdefault('tracking',{}).update(enabled=True,telegram_update_seconds=60,close_on_tp1=False)
    tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(c,indent=2)+'\n');tmp.replace(p)
    print('Both strategies:',', '.join(c['symbols']))
    print('Confluence: >=4/5. One active setup per strategy/pair. Telegram edits: 60s.')
    print('Backup:',backup.name)

if __name__=='__main__':main()
