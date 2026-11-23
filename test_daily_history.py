import unittest,tempfile,json,time
from pathlib import Path
from datetime import datetime,timezone
from types import SimpleNamespace as N
from dashboard_store import Store
from daily_history import collect,refresh,today
class Daily(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'test.db');self.cfg=dict(symbols=['AUDUSD'],poll_seconds=15)
        self.store.start(self.cfg);self.account=N(login=1,server='Demo',currency='USD');self.deals=[]
        self.mt5=N(terminal_info=lambda:N(connected=True),account_info=lambda:self.account,history_deals_get=lambda *a:self.deals)
    def tearDown(self):self.tmp.cleanup()
    def deal(self,ticket,stamp,entry=1,position=1,profit=10,commission=-1,type=0):return N(ticket=ticket,time=stamp,time_msc=stamp*1000,entry=entry,position_id=position,profit=profit,commission=commission,swap=0,fee=-.1,type=type,magic=816001,symbol='AUDUSD',volume=.1,price=1.1,reason=3)
    def test_myt_day_boundary_and_costs(self):
        start=datetime(2026,11,17,16,tzinfo=timezone.utc).timestamp()
        self.deals=[self.deal(1,start-1),self.deal(2,start),self.deal(3,start+86399),self.deal(4,start+86400)]
        result=collect(self.mt5,self.cfg,'2026-11-18');self.assertEqual([r['ticket'] for r in result['deals']],[2,3]);self.assertAlmostEqual(sum(r['net'] for r in result['deals']),17.8)
    def test_duplicate_deal_and_balance_exclusion(self):
        t=datetime(2026,11,18,tzinfo=timezone.utc).timestamp();d=self.deal(1,t)
        self.deals=[d,d,self.deal(2,t,type=2,profit=1000)]
        self.assertEqual(len(collect(self.mt5,self.cfg,'2026-11-18')['deals']),1)
    def test_empty_vs_unavailable(self):
        self.assertEqual(collect(self.mt5,self.cfg,'2026-11-18')['status'],'READY')
        self.mt5.history_deals_get=lambda *a:None
        with self.assertRaises(ValueError):collect(self.mt5,self.cfg,'2026-11-18')
    def test_account_switch_invalidates(self):
        def changed(*a):self.account=N(login=2,server='Demo',currency='USD');return []
        self.mt5.history_deals_get=changed
        with self.assertRaises(ValueError):collect(self.mt5,self.cfg,'2026-11-18')
    def test_history_offset_independent(self):
        stamp=datetime(2026,11,18,17,tzinfo=timezone.utc).timestamp();self.deals=[self.deal(1,stamp)]
        self.assertEqual(len(collect(self.mt5,self.cfg,'2026-11-18')['deals']),0)
        self.cfg['history_timestamp_offset_hours']=2
        self.assertEqual(len(collect(self.mt5,self.cfg,'2026-11-18')['deals']),1)
    def test_cache_queue_account_scope_and_date(self):
        refresh(self.mt5,self.store,self.cfg);r=self.store.daily();self.assertEqual(r['status'],'READY')
        self.assertNotIn('daily_scope',self.store.snapshot()['meta'])
        self.assertEqual(self.store.daily('2025-01-01')['status'],'LOADING');refresh(self.mt5,self.store,self.cfg);self.assertEqual(self.store.daily('2025-01-01')['status'],'READY')
        self.account=N(login=2,server='Demo',currency='USD');refresh(self.mt5,self.store,self.cfg);self.assertEqual(self.store.daily('2025-01-01')['status'],'LOADING')
        with self.assertRaises(ValueError):self.store.daily('9999-01-01')

    def test_http_date_filter_and_private_scope(self):
        import threading,urllib.request,urllib.error
        from dashboard_server import build_server
        refresh(self.mt5,self.store,self.cfg)
        server=build_server(self.store,0);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            base='http://127.0.0.1:'+str(server.server_port)
            with urllib.request.urlopen(base+'/api/daily') as r:
                d=json.load(r);self.assertEqual(d['status'],'READY');self.assertNotIn('account_scope',d)
            with urllib.request.urlopen(base+'/api/daily?date=2025-01-01') as r:self.assertEqual(json.load(r)['status'],'LOADING')
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(base+'/api/daily?date=9999-01-01')
            self.assertEqual(e.exception.code,400)
        finally:server.shutdown();server.server_close()
