import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from dashboard_store import Store
from dashboard_server import build_server


def stamp(t): return datetime.fromtimestamp(t,timezone.utc).isoformat()


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'dashboard.sqlite3')
        self.cfg=dict(symbols=['XAUUSD'],poll_seconds=15,telegram_token='NEVER_EXPOSE_ME',tracking={'enabled':False})
        self.store.start(self.cfg,now=1000)

    def tearDown(self): self.tmp.cleanup()

    def decision(self,t=1010,key='a',side='BUY',price=100,plan=True):
        p=dict(id=key,side=side,order_idea=side+'_STOP',entry=105,stop_loss=95,tp1=115,tp2=125,
               setup='CSA_REENTRY',confirmation_utc=stamp(1000),expires_utc=stamp(1600))
        return dict(symbol='XAUUSD',health='OK',checked_utc=stamp(t),condition=dict(name='BULLISH',side=side),
                    setup={'type':'CSA_REENTRY'},entry_plan=p if plan else None,
                    quote=dict(bid=price,ask=price+.1,digits=2),chart=[],reason='Confirmed')

    def test_signal_is_deduplicated_and_original_levels_preserved(self):
        self.store.record(self.decision(),now=1010)
        changed=self.decision();changed['entry_plan']['entry']=106
        self.store.record(changed,now=1020)
        s=self.store.snapshot(now=1020)
        self.assertEqual(len(s['signals']),1)
        self.assertEqual(s['signals'][0]['entry'],105)
        self.assertTrue(s['signals'][0]['actionable'])
        self.assertNotIn('NEVER_EXPOSE_ME',json.dumps(s))

    def test_crossing_is_observation_not_fill(self):
        self.store.record(self.decision(),now=1010)
        self.store.record(self.decision(price=106,plan=False),now=1020)
        p=self.store.snapshot(now=1020)['signals'][0]
        self.assertEqual(p['lifecycle'],'LEVEL_OBSERVED')
        self.assertFalse(p['actionable'])
        self.assertIn('not a broker fill',p['note'])

    def test_offline_and_expired_signals_withheld(self):
        self.store.record(self.decision(),now=1010)
        s=self.store.snapshot(now=1100)
        self.assertFalse(s['bot_online'])
        self.assertFalse(s['signals'][0]['actionable'])
        self.assertEqual(self.store.snapshot(now=1700)['signals'][0]['lifecycle'],'EXPIRED')

    def test_blocked_feed_withholds_pending(self):
        self.store.record(self.decision(),now=1010)
        self.store.record(dict(symbol='XAUUSD',health='BLOCKED',reason='Stale quote'),now=1020)
        s=self.store.snapshot(now=1020)
        self.assertTrue(s['bot_online'])
        self.assertFalse(s['signals'][0]['actionable'])

    def test_opposing_condition_invalidates(self):
        self.store.record(self.decision(),now=1010)
        self.store.record(self.decision(side='SELL',plan=False),now=1020)
        self.assertEqual(self.store.snapshot(now=1020)['signals'][0]['lifecycle'],'INVALIDATED')

    def test_restart_keeps_history_but_does_not_revive_pending(self):
        self.store.record(self.decision(),now=1010)
        self.store.start(self.cfg,now=1030)
        self.store.record(self.decision(),now=1040)
        s=self.store.snapshot(now=1040)
        self.assertEqual(s['signals'][0]['lifecycle'],'UNVERIFIED')
        self.assertFalse(s['signals'][0]['actionable'])

    def test_new_signal_supersedes_old(self):
        self.store.record(self.decision(),now=1010)
        self.store.record(self.decision(key='b'),now=1020)
        self.assertEqual({p['lifecycle'] for p in self.store.snapshot(now=1020)['signals']},{'PENDING','SUPERSEDED'})

    def test_http_api_static_csv_and_path_isolation(self):
        self.store.record(self.decision(),now=1010)
        server=build_server(self.store,0)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(base+'/api/status') as r:
                self.assertEqual(r.status,200)
                self.assertIn('no-store',r.headers['Cache-Control'])
                s=json.load(r);self.assertEqual(len(s['signals']),1)
                self.assertNotIn('NEVER_EXPOSE_ME',json.dumps(s))
            for path in ['/', '/js/core.js','/js/app.js','/dashboard.css','/demo.json','/favicon.svg']:
                with urllib.request.urlopen(base+path) as r: self.assertEqual(r.status,200)
            for path in ['/js/../dashboard_server.py','/js/missing.js','/dashboard.js']:
                with self.assertRaises(urllib.error.HTTPError) as caught: urllib.request.urlopen(base+path)
                self.assertEqual(caught.exception.code,404)
            with urllib.request.urlopen(base+'/api/export.csv') as r:
                rows=list(csv.DictReader(io.StringIO(r.read().decode('utf-8-sig'))))
                self.assertEqual(rows[0]['symbol'],'XAUUSD')
            for path in ['/.env','/config.json','/dashboard.sqlite3','/../bbma_bot.py']:
                with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(base+path)
                self.assertEqual(e.exception.code,404)
            request=urllib.request.Request(base+'/',headers={'Host':'evil.example'})
            with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(request)
            self.assertEqual(e.exception.code,403)
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__': unittest.main()

class MultiStrategy(unittest.TestCase):
    setUp=Dashboard.setUp
    tearDown=Dashboard.tearDown
    decision=Dashboard.decision
    def reference(self,t=1010):
        row=self.decision(t=t);row.update(strategy='CONFLUENCE',analysis_id='a',score=4,qualified=True)
        row['entry_plan'].update(signal_kind='REFERENCE',order_idea='QUOTE_REFERENCE')
        return row

    def test_other_strategy_cannot_invalidate_or_supersede_bbma(self):
        self.store.record(self.decision(),now=1010)
        self.store.record(self.reference(),now=1011)
        opposite=self.reference();opposite.update(condition=dict(name='BEARISH',side='SELL'));opposite['entry_plan']=None
        self.store.record(opposite,now=1020)
        s=self.store.snapshot(now=1020)
        self.assertEqual(len(s['symbols']),2)
        bbma=[x for x in s['signals'] if x['strategy']=='BBMA'][0]
        self.assertEqual(bbma['lifecycle'],'PENDING')
        self.assertTrue(bbma['actionable'])
        ref=[x for x in s['signals'] if x['strategy']=='CONFLUENCE'][0]
        self.assertFalse(ref['actionable'])
        self.assertEqual(len({x['key'] for x in s['signals']}),2)

    def test_reference_never_becomes_pending_or_observed_fill(self):
        self.store.record(self.reference(),now=1010)
        row=self.reference();row['quote'].update(bid=110,ask=110.1)
        self.store.record(row,now=1020)
        s=self.store.snapshot(now=1020)
        self.assertEqual(s['signals'][0]['lifecycle'],'REFERENCE')
        self.assertFalse(s['signals'][0]['actionable'])
        self.assertTrue(s['signals'][0]['reference_current'])
        self.assertEqual(self.store.snapshot(now=1700)['signals'][0]['lifecycle'],'STALE_REFERENCE')

    def test_confluence_block_does_not_hide_fresh_bbma(self):
        self.store.record(self.decision(),now=1010)
        self.store.record(dict(symbol='XAUUSD',strategy='CONFLUENCE',health='BLOCKED',reason='No H4 history'),now=1020)
        s=self.store.snapshot(now=1020)
        self.assertTrue([p for p in s['signals'] if p['strategy']=='BBMA'][0]['actionable'])
        self.assertEqual(s['comparison'][0]['status'],'UNAVAILABLE')

    def test_comparison_is_direction_only_not_a_combined_entry(self):
        self.store.record(self.decision(plan=False),now=1010)
        self.store.record(self.reference(),now=1010)
        row=self.store.snapshot(now=1020)['comparison'][0]
        self.assertEqual(row['status'],'ALIGNED_BIAS')
        self.assertFalse(row['bbma_entry_ready'])
        self.assertTrue(row['confluence_qualified'])
        self.assertNotIn('combined_entry',row)

    def test_legacy_history_additive_migration_is_idempotent(self):
        import sqlite3
        path=Path(self.tmp.name)/'old.sqlite3'
        db=sqlite3.connect(path)
        db.executescript('CREATE TABLE symbols(symbol TEXT PRIMARY KEY,updated REAL,payload TEXT); CREATE TABLE signals(id TEXT PRIMARY KEY,symbol TEXT,created REAL,expires REAL,status TEXT,updated REAL,note TEXT,payload TEXT);')
        row=self.decision()
        db.execute('INSERT INTO symbols VALUES (?,?,?)',('XAUUSD',1010,json.dumps(row)))
        db.execute('INSERT INTO signals VALUES (?,?,?,?,?,?,?,?)',('XAUUSD:a','XAUUSD',1010,1600,'PENDING',1010,'original',json.dumps(row['entry_plan'])))
        db.commit();db.close()
        Store(path);store=Store(path);s=store.snapshot(now=1020)
        self.assertEqual(len(s['signals']),1)
        self.assertEqual(s['signals'][0]['strategy'],'BBMA')
        # contextlib.closing: a bare `with sqlite3.connect()` only commits and leaves the file open,
        # which makes Windows refuse to delete the temporary folder in tearDown.
        import contextlib
        with contextlib.closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM signals').fetchone()[0],1)
