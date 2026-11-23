"""Local strategy-separated dashboard persistence; no execution or credentials."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import json
import sqlite3
import time

SCHEMA='''
CREATE TABLE IF NOT EXISTS daily_history (
 account_scope TEXT NOT NULL,day TEXT NOT NULL,requested REAL NOT NULL,updated REAL NOT NULL,payload TEXT,
 PRIMARY KEY(account_scope,day));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strategy_symbols (
 strategy TEXT NOT NULL,symbol TEXT NOT NULL,updated REAL NOT NULL,payload TEXT NOT NULL,
 PRIMARY KEY(strategy,symbol));
CREATE TABLE IF NOT EXISTS strategy_signals (
 id TEXT PRIMARY KEY,strategy TEXT NOT NULL,symbol TEXT NOT NULL,created REAL NOT NULL,
 expires REAL NOT NULL,status TEXT NOT NULL,updated REAL NOT NULL,note TEXT NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS strategy_signal_created ON strategy_signals(created DESC);
'''


def epoch(s):return datetime.fromisoformat(s).timestamp()


def orb_meta(cfg):
    try:
        from orb_strategy import settings
        o=settings(cfg)
        return dict(label='ORB Breakout-Retest',symbols=o['symbols'],check_seconds=cfg['poll_seconds'],freshness_seconds=max(60,cfg['poll_seconds']*3),
                    enabled=bool(o['enabled']),session_utc_offset_hours=o['session_utc_offset_hours'],range_hours=[o['range_start_hour'],o['range_end_hour']],
                    trade_hours=[o['trade_start_hour'],o['trade_end_hour']],skip_weekdays=o['skip_weekdays'],skip_months=o['skip_months'])
    except Exception as exc:
        return dict(label='ORB Breakout-Retest',symbols=[],check_seconds=cfg['poll_seconds'],freshness_seconds=60,enabled=False,error=str(exc))


def comparison(symbols,signals):
    grouped={}
    for row in symbols:grouped.setdefault(row['symbol'],{})[row['strategy']]=row
    result=[]
    for symbol,group in grouped.items():
        b,c=group.get('BBMA'),group.get('CONFLUENCE')
        valid=all(r and r.get('fresh') and r.get('health')=='OK' for r in (b,c))
        bs=b.get('condition',{}).get('side') if b else None
        cs=c.get('condition',{}).get('side') if c else None
        status=('ALIGNED_BIAS' if bs==cs and bs else 'CONFLICTING_BIAS' if bs and cs else 'NO_SHARED_BIAS') if valid else 'UNAVAILABLE'
        result.append(dict(symbol=symbol,status=status,bbma_side=bs,confluence_side=cs,
                           confluence_score=c.get('score') if c else None,
                           confluence_qualified=bool(valid and c.get('qualified')),
                           bbma_entry_ready=any(p['strategy']=='BBMA' and p['symbol']==symbol and p['actionable'] for p in signals),
                           note='Direction comparison only; no combined score, entry or performance claim.'))
    return result


class Store:
    def __init__(self,path):
        self.path=Path(path)
        with self.connect() as db:
            db.executescript(SCHEMA)
            from tracking import init
            init(db)
            # Additive v1.3 migration: leave the original tables untouched and copy once.
            if not db.execute("SELECT 1 FROM meta WHERE key='strategy_migration_v2'").fetchone():
                tables={r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'symbols' in tables:
                    db.execute("INSERT OR IGNORE INTO strategy_symbols SELECT 'BBMA',symbol,updated,payload FROM symbols")
                if 'signals' in tables:
                    db.execute("INSERT OR IGNORE INTO strategy_signals SELECT 'BBMA|'||id,'BBMA',symbol,created,expires,status,updated,note,payload FROM signals")
                db.execute("INSERT OR REPLACE INTO meta VALUES ('strategy_migration_v2','true')")

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            # WAL + NORMAL: no fsync on every commit (only at checkpoints). The database cannot be corrupted,
            # even on a crash; a power cut may lose only the last moments of dashboard records. On a hard
            # disk this cut a commit from ~4.3 s to ~0.06 ms (bot cycle ~3 min -> seconds).
            db.execute('PRAGMA synchronous=NORMAL')
            with db:yield db
        finally:db.close()

    def start(self,cfg,now=None):
        from confluence_strategy import settings
        now=time.time() if now is None else now
        c=settings(cfg)
        from tracking import config
        tracking_config=config(cfg)
        meta=dict(daily_scope=None,started=now,heartbeat=now,running=True,symbols=cfg['symbols'],poll_seconds=cfg['poll_seconds'],
                  mhv_enabled=cfg.get('enable_mhv',False),tick_offset=cfg.get('tick_timestamp_offset_hours',0),
                  bar_offset=cfg.get('bar_timestamp_offset_hours',0),tracking=tracking_config,
                  mt5_account=dict(status='UNAVAILABLE',updated_epoch=now),
                  demo_execution=dict(enabled=False,status='DISABLED',account_type='NOT_VERIFIED',lot=None),
                  strategies={'BBMA':dict(label='BBMA',symbols=cfg['symbols'],check_seconds=cfg['poll_seconds'],
                                          freshness_seconds=max(60,cfg['poll_seconds']*3),enabled=True),
                              'CONFLUENCE':dict(label='Five-factor Confluence',symbols=c['symbols'],check_seconds=c['check_seconds'],
                                                freshness_seconds=c['check_seconds']+60,enabled=c['enabled']),
                              'ORB':orb_meta(cfg)})
        meta.update(replay_scope=None,replay_health=None,portfolio_scope=None,portfolio_snapshot=None,portfolio_last_check=None,health_watchdog=None,ai_scope=None,ai_health=dict(status='WAITING' if cfg.get('ai_shadow',{}).get('enabled',True) else 'DISABLED',mode='SHADOW',updated_epoch=now,orders_affected=False))
        with self.connect() as db:
            from trade_controls import validate,DEFAULTS
            initial={**DEFAULTS,**cfg.get('trade_controls',{})}
            if 'trade_controls' not in cfg:initial['risk_enabled']=False
            db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)',('trade_controls',json.dumps(validate(initial))))
            db.execute("UPDATE strategy_signals SET status='UNVERIFIED',note='Monitoring restarted; pending entry continuity cannot be verified.' WHERE status='PENDING'")
            for row in db.execute("SELECT * FROM strategy_signals WHERE status IN ('PAPER_RUNNING','PAPER_TP1')").fetchall():
                p=json.loads(row['payload']);p['tracking']['continuity_gap']=True
                db.execute('UPDATE strategy_signals SET payload=? WHERE id=?',(json.dumps(p),row['id']))
            for k,v in meta.items():db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(k,json.dumps(v)))

    def stop(self):
        with self.connect() as db:db.execute("INSERT OR REPLACE INTO meta VALUES ('running','false')")

    def record(self,result,now=None):
        now=time.time() if now is None else now
        symbol=result['symbol'];strategy=result.get('strategy','BBMA')
        if strategy not in ('BBMA','CONFLUENCE','ORB'):raise ValueError('Unknown strategy')
        result=dict(result,strategy=strategy)
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('heartbeat',json.dumps(now)))
            db.execute('INSERT OR REPLACE INTO strategy_symbols VALUES (?,?,?,?)',
                       (strategy,symbol,now,json.dumps(result,allow_nan=False)))
            meta_row=db.execute("SELECT value FROM meta WHERE key='tracking'").fetchone()
            track=json.loads(meta_row['value']) if meta_row else {'enabled':True}
            if track.get('enabled',True):
                from tracking import handle
                handle(db,result,now,track)
                return
            rows=db.execute("SELECT * FROM strategy_signals WHERE strategy=? AND symbol=? AND status='PENDING'",(strategy,symbol)).fetchall()
            for row in rows:
                p=json.loads(row['payload']);status,note='PENDING',row['note']
                if now>=row['expires']:status,note='EXPIRED','Entry window ended; no fill or outcome is inferred.'
                elif result.get('health')=='OK':
                    side=result.get('condition',{}).get('side');quote=result.get('quote',{})
                    if side!=p['side']:status,note='INVALIDATED','H1 direction no longer agrees with the plan.'
                    elif quote:
                        mark=quote['ask'] if p['side']=='BUY' else quote['bid']
                        invalid=quote['bid']<=p['stop_loss'] if p['side']=='BUY' else quote['ask']>=p['stop_loss']
                        crossed=mark>=p['entry'] if p['side']=='BUY' else mark<=p['entry']
                        if invalid:status,note='INVALIDATED','Quote observed beyond the planned stop before a confirmed fill.'
                        elif crossed:status,note='LEVEL_OBSERVED','Entry level crossed in a sampled quote. This is not a broker fill.'
                db.execute('UPDATE strategy_signals SET status=?,note=?,updated=? WHERE id=?',(status,note,now,row['id']))
            p=result.get('entry_plan') if result.get('health')=='OK' else None
            if p:
                key=strategy+'|'+symbol+':'+p['id'];expires=epoch(p['expires_utc'])
                reference=p.get('signal_kind')=='REFERENCE'
                status='REFERENCE' if reference else 'PENDING' if expires>now else 'EXPIRED'
                note='Executable quote reference only. No entry trigger, order or fill.' if reference else 'Pending entry idea only. No order has been placed.'
                if not db.execute('SELECT id FROM strategy_signals WHERE id=?',(key,)).fetchone():
                    db.execute("UPDATE strategy_signals SET status='SUPERSEDED',note='Replaced by a newer idea from the same strategy.' WHERE strategy=? AND symbol=? AND status IN ('PENDING','REFERENCE')",(strategy,symbol))
                db.execute('INSERT OR IGNORE INTO strategy_signals VALUES (?,?,?,?,?,?,?,?,?)',
                           (key,strategy,symbol,now,expires,status,now,note,json.dumps(p,allow_nan=False)))
            db.execute('DELETE FROM strategy_signals WHERE id IN (SELECT id FROM strategy_signals ORDER BY created DESC LIMIT -1 OFFSET 10000)')

    def delivery(self,key,status,strategy='BBMA'):
        if '|' not in key:key=strategy+'|'+key
        with self.connect() as db:
            row=db.execute('SELECT payload FROM strategy_signals WHERE id=?',(key,)).fetchone()
            if row:
                p=json.loads(row['payload']);p['telegram']=status
                db.execute('UPDATE strategy_signals SET payload=? WHERE id=?',(json.dumps(p),key))

    def observe(self,symbol,quote,now=None):
        now=time.time() if now is None else now
        with self.connect() as db:
            m={r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM meta')}
            track=m.get('tracking',{'enabled':True})
            if not track.get('enabled',True):return
            analyses={}
            for row in db.execute('SELECT * FROM strategy_symbols WHERE symbol=?',(symbol,)):
                a=json.loads(row['payload'])
                ttl=m.get('strategies',{}).get(row['strategy'],{}).get('freshness_seconds',60)
                a['_fresh']=now-row['updated']<=ttl and row['updated']>=m.get('started',0)
                analyses[row['strategy']]=a
            from tracking import observe
            observe(db,symbol,quote,now,track,analyses)

    def positions(self,positions,now=None,error=None):
        now=time.time() if now is None else now
        with self.connect() as db:
            payload=dict(updated_epoch=now,positions=positions,error=error)
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('mt5_positions',json.dumps(payload,allow_nan=False)))

    def daily(self,selected=None):
        from datetime import date
        with self.connect() as db:
            scope_row=db.execute("SELECT value FROM meta WHERE key='daily_scope'").fetchone()
            day_row=db.execute("SELECT value FROM meta WHERE key='daily_today'").fetchone()
            scope=json.loads(scope_row['value']) if scope_row else None
            current=json.loads(day_row['value']) if day_row else datetime.now().date().isoformat()
            selected=selected or current
            parsed=date.fromisoformat(selected)
            if parsed.isoformat()!=selected or parsed.year<2000 or selected>current:raise ValueError('Choose a valid date from 2000 through today')
            if not scope:return dict(status='UNAVAILABLE',date=selected,today=current,error='Start/connect the bot to load account history',deals=[])
            db.execute('INSERT INTO daily_history VALUES (?,?,?,0,NULL) ON CONFLICT(account_scope,day) DO UPDATE SET requested=excluded.requested',(scope,selected,time.time()))
            r=db.execute('SELECT payload FROM daily_history WHERE account_scope=? AND day=?',(scope,selected)).fetchone()
            result=json.loads(r['payload']) if r and r['payload'] else dict(status='LOADING',date=selected,deals=[])
            return dict(result,today=current)

    def snapshot(self,now=None):
        now=time.time() if now is None else now
        with self.connect() as db:
            meta={r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM meta')}
            rows=list(db.execute('SELECT * FROM strategy_symbols'))
            # Keep separate quotas so one strategy cannot crowd the other out of the API/history.
            signals=[]
            for strategy in ('BBMA','CONFLUENCE','ORB'):
                recent=list(db.execute('SELECT * FROM strategy_signals WHERE strategy=? ORDER BY created DESC LIMIT 200',(strategy,)))
                active=list(db.execute("SELECT * FROM strategy_signals WHERE strategy=? AND status IN ('PENDING','PAPER_RUNNING','PAPER_TP1')",(strategy,)))
                merged={r['id']:r for r in recent+active}
                signals+=list(merged.values())
        meta.pop('daily_scope',None)
        demo_rows=[]
        scope=meta.pop('demo_scope',None)
        with self.connect() as db:
            if scope and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_orders'").fetchone():
                from demo_execution import public,BUSY
                drows=list(db.execute('SELECT * FROM demo_orders WHERE account_hash=? ORDER BY created DESC LIMIT 200',(scope,)))
                placeholders=','.join('?' for _ in BUSY)
                drows+=list(db.execute(f'SELECT * FROM demo_orders WHERE account_hash=? AND status IN ({placeholders})',(scope,*BUSY)))
                demo_rows=[public(r) for r in {r['key']:r for r in drows}.values()]
                current_keys={r['id'] for r in signals}
                for d in demo_rows:
                    if d['status'] in BUSY and d['setup_key'] not in current_keys:
                        linked=db.execute('SELECT * FROM strategy_signals WHERE id=?',(d['setup_key'],)).fetchone()
                        if linked:signals.append(linked);current_keys.add(d['setup_key'])
        ttl=max(60,meta.get('poll_seconds',15)*3);started=meta.get('started',0)
        configs=meta.get('strategies',{})
        symbol_rows=[]
        for row in rows:
            strategy=row['strategy'];conf=configs.get(strategy,{})
            if conf and (not conf.get('enabled',True) or row['symbol'] not in conf['symbols']):continue
            p=json.loads(row['payload']);p.update(strategy=strategy,updated_epoch=row['updated'],
                                                  freshness_seconds=conf.get('freshness_seconds',ttl))
            p['fresh']=bool(now-row['updated']<=p['freshness_seconds'] and row['updated']>=started and meta.get('running',False))
            symbol_rows.append(p)
        index={(p['strategy'],p['symbol']):p for p in symbol_rows};rendered=[]
        for row in sorted(signals,key=lambda r:r['created'],reverse=True):
            p=json.loads(row['payload']);status,note=row['status'],row['note']
            current=index.get((row['strategy'],row['symbol']),{})
            fresh=bool(current.get('fresh')) and current.get('health')=='OK'
            reference=p.get('signal_kind')=='REFERENCE'
            tracking=p.get('tracking')
            if tracking:
                p['tracking']=dict(tracking)
                live=bool(fresh and now-tracking.get('last_quote_epoch',0)<=60)
                p['tracking']['fresh']=live
                if status in ('PENDING','PAPER_RUNNING','PAPER_TP1') and not live:
                    p['tracking'].update(decision='REVIEW_DATA',monitor='STALE',close_reason='Quote or strategy analysis is stale; current HOLD/CLOSE unavailable.')
                reference=False
            if status=='PENDING' and now>=row['expires']:status,note='EXPIRED','Entry window ended; no fill or outcome is inferred.'
            if not tracking and status=='REFERENCE' and (now>=row['expires'] or current.get('analysis_id')!=p['id'] or not fresh):status='STALE_REFERENCE'
            rendered.append(dict(p,key=row['id'],strategy=row['strategy'],symbol=row['symbol'],created=row['created'],expires=row['expires'],
                                 lifecycle=status,note=note,actionable=bool(not reference and status=='PENDING' and fresh),
                                 reference_current=bool(reference and status=='REFERENCE' and fresh)))
        alive=meta.get('running',False) and now-meta.get('heartbeat',0)<=ttl
        return dict(version='1.13',server_epoch=now,meta=meta,bot_online=bool(alive),freshness_seconds=ttl,
                    symbols=symbol_rows,signals=rendered,demo_orders=demo_rows,comparison=comparison(symbol_rows,rendered),
                    disclaimer='Independent analyses. References are not entry triggers. Paper tracking is simulated; broker P/L is shown separately. Order submission requires explicit DEMO execution mode.')
