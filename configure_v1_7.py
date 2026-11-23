"""Opt in to risk sizing and fail-closed news filter. Preserve other user settings."""
import json,shutil
from pathlib import Path
from datetime import datetime
from trade_controls import DEFAULTS
from dashboard_store import Store
root=Path(__file__).resolve().parent
p=root/'config.json'
if not p.exists():shutil.copy2(root/'config.example.json',p)
backup=p.with_name('config.backup.'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json');shutil.copy2(p,backup)
cfg=json.loads(p.read_text(encoding='utf-8-sig'))
cfg.setdefault('trade_controls',dict(DEFAULTS));cfg.setdefault('news_filter',dict(enabled=True,before_minutes=30,after_minutes=30))
p.write_text(json.dumps(cfg,indent=2),encoding='utf-8')
store=Store(root/'dashboard.sqlite3')
with store.connect() as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('trade_controls',json.dumps(cfg['trade_controls'])))
print('v1.7 configured. Backup:',backup.name)
print('News filter requires TRADING_ECONOMICS_API_KEY in .env. Missing/stale calendar blocks new entries.')
