import json,time,unittest,threading,urllib.request,urllib.error
from types import SimpleNamespace as N
from unittest.mock import patch
from trade_controls import DEFAULTS,validate,estimate,controls
from news_filter import NewsFilter,currencies
import test_demo_execution as demo_tests
FakeMT5=demo_tests.FakeMT5
from demo_execution import DemoExecutor
from dashboard_server import build_server

class RiskNews(unittest.TestCase):
    def broker(self):
        m=FakeMT5();m.account.equity=10000
        m.order_calc_profit=lambda side,symbol,volume,entry,exit:(exit-entry)*(1 if side==0 else -1)*100000*volume
        return m
    def test_risk_buy_sell_and_grid(self):
        for side,sl,tp1,tp2 in [('BUY',1.099,1.101,1.102),('SELL',1.101,1.099,1.098)]:
            d=estimate(self.broker(),'AUDUSD',side,1.1,sl,tp1,tp2,DEFAULTS)
            self.assertLessEqual(d['estimated_loss'],50.000001);self.assertAlmostEqual(d['estimated_tp2']/d['estimated_loss'],2)
            self.assertLessEqual(d['volume'],.5)
    def test_risk_too_small_not_rounded_up(self):
        m=self.broker();m.account.equity=1
        with self.assertRaises(ValueError):estimate(m,'AUDUSD','BUY',1.1,1.09,1.11,1.12,DEFAULTS)
    def test_missing_calculation_blocks(self):
        m=self.broker();m.order_calc_profit=lambda *a:None
        with self.assertRaises(ValueError):estimate(m,'AUDUSD','BUY',1.1,1.09,1.11,1.12,DEFAULTS)
    def test_validate(self):
        for v in ({'risk_percent':float('nan')},{'risk_percent':3},{'partial_enabled':'true'},{'other':1}):
            with self.assertRaises(ValueError):validate(v)
    def calendar(self):
        n=NewsFilter({'news_filter':{'enabled':True}});n.error=None;n.fetched=10000;n.coverage=(0,100000);n.events=[dict(currency='USD',title='CPI',epoch=12000,from_epoch=10200,to_epoch=13800)]
        return n
    def test_news_window_and_pending_horizon(self):
        n=self.calendar()
        self.assertFalse(n.check('AUDUSD',now=10000)['blocked'])
        self.assertTrue(n.check('AUDUSD',until=10500,now=10000)['blocked'])
        self.assertTrue(n.check('XAUUSD.a',now=10300)['blocked'])
        self.assertFalse(n.check('GBPJPY',now=10300)['blocked'])
    def test_news_stale_missing_unmapped_fail_closed(self):
        n=self.calendar();self.assertTrue(n.check('AUDUSD',now=20000)['blocked'])
        self.assertTrue(n.check('GOLD',now=10000)['blocked'])
        n.error='offline';self.assertTrue(n.check('AUDUSD',now=10000)['blocked'])
    def test_country_currency_mapping(self):
        self.assertEqual(currencies('m.EURUSD.a',{}),{'EUR','USD'})
        self.assertEqual(currencies('GOLD',{'symbol_currencies':{'GOLD':['USD']}}),{'USD'})
    def test_fetch_missing_key_is_sanitized(self):
        n=NewsFilter({'news_filter':{'enabled':True}})
        with patch.dict('os.environ',{'TRADING_ECONOMICS_API_KEY':''}):n.fetch()
        self.assertTrue(n.check('AUDUSD')['blocked']);self.assertNotIn('Authorization',n.error)

    def test_provider_utc_country_fallback_and_estimated_time(self):
        from datetime import datetime,timezone
        raw=[dict(Importance=3,Country='United States',Currency='',Date='2026-11-18T12:30:00',DateSpan='0',Event='CPI'),dict(Importance=3,Country='Japan',Currency='',Date='2026-11-18T00:00:00Z',DateSpan='1',Event='Policy')]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,*a):return json.dumps(raw).encode()
        n=NewsFilter({'news_filter':{'enabled':True}})
        with patch.dict('os.environ',{'TRADING_ECONOMICS_API_KEY':'private:key'}),patch('news_filter.urllib.request.urlopen',return_value=Response()) as get:
            n.fetch()
        self.assertIsNone(n.error);self.assertEqual(n.events[0]['currency'],'USD')
        self.assertEqual(n.events[0]['epoch'],datetime(2026,11,18,12,30,tzinfo=timezone.utc).timestamp())
        self.assertGreater(n.events[1]['to_epoch']-n.events[1]['from_epoch'],86400)
        self.assertNotIn('private',get.call_args.args[0].full_url)
    def test_provider_malformed_data_fails_closed(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,*a):return b'[{"Importance":3,"Country":"Japan","Date":"bad"}]'
        n=NewsFilter({'news_filter':{'enabled':True}})
        with patch.dict('os.environ',{'TRADING_ECONOMICS_API_KEY':'private:key'}),patch('news_filter.urllib.request.urlopen',return_value=Response()):n.fetch()
        self.assertTrue(n.check('USDJPY')['blocked']);self.assertEqual(n.fetched,0)

class ManagedBroker(FakeMT5):
    TRADE_ACTION_SLTP=6
    def __init__(self):
        super().__init__();self.account.equity=10000;self.info.trade_freeze_level=0
        self.bid=1.1015;self.ask=1.1016
    def symbol_info_tick(self,*_):return N(time=int(time.time()),bid=self.bid,ask=self.ask)
    def order_send(self,r):
        if r['action']==6:
            self.sent.append(dict(r));self.positions[0].sl=r['sl'];return N(retcode=10009)
        if r.get('position'):
            self.sent.append(dict(r))
            if self.none_reply:return None
            self.positions[0].volume-=r['volume'];return N(retcode=10009)
        return super().order_send(r)

class Management(unittest.TestCase):
    setUp=demo_tests.Demo.setUp
    tearDown=demo_tests.Demo.tearDown
    result=demo_tests.Demo.result
    submit=demo_tests.Demo.submit
    latest=demo_tests.Demo.latest
    def prepare(self,changes,volume=.04):
        self.mt5=ManagedBroker();self.mt5.bid=1.0999;self.mt5.ask=1.1
        ex=DemoExecutor(self.mt5,self.store,self.cfg,lot=volume);self.submit(ex,self.result());ex.refresh()
        c={**DEFAULTS,**changes}
        with self.store.connect() as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('trade_controls',json.dumps(c)))
        self.mt5.bid=1.1015;self.mt5.ask=1.1016;self.mt5.positions[0].price_current=self.mt5.bid
        return ex
    def test_partial_once_and_keep_remainder(self):
        ex=self.prepare({'partial_enabled':True});ex.refresh();ex.refresh()
        closes=[r for r in self.mt5.sent if r.get('position') and r['action']==1]
        self.assertEqual(len(closes),1);self.assertAlmostEqual(closes[0]['volume'],.02);self.assertAlmostEqual(self.mt5.positions[0].volume,.02)
    def test_tiny_partial_never_full_close(self):
        ex=self.prepare({'partial_enabled':True},.01);ex.refresh()
        self.assertEqual(len(self.mt5.sent),1);self.assertEqual(self.latest()['management']['TP1_PARTIAL']['status'],'SKIPPED')
    def test_be_only_tightens_and_preserves_tp(self):
        ex=self.prepare({'breakeven_enabled':True});ex.refresh();ex.refresh()
        mods=[r for r in self.mt5.sent if r['action']==6]
        self.assertEqual(len(mods),1);self.assertAlmostEqual(mods[0]['sl'],1.10002);self.assertEqual(mods[0]['tp'],self.mt5.positions[0].tp)
    def test_trailing_independent(self):
        ex=self.prepare({'trailing_enabled':True,'trailing_distance_r':.5});ex.refresh()
        self.assertTrue(any(r['action']==6 for r in self.mt5.sent));previous=self.mt5.positions[0].sl
        self.mt5.bid=1.1014;self.mt5.ask=1.1015;ex.refresh();self.assertEqual(self.mt5.positions[0].sl,previous)
    def test_uncertain_partial_no_repeat(self):
        ex=self.prepare({'partial_enabled':True});self.mt5.none_reply=True;ex.refresh();ex.refresh()
        self.assertEqual(len([r for r in self.mt5.sent if r.get('position')]),1)
    def test_news_blocks_submission_without_send(self):
        self.cfg['news_filter']={'enabled':True};ex=DemoExecutor(self.mt5,self.store,self.cfg)
        with patch.object(ex.news,'refresh'):self.submit(ex,self.result())
        self.assertEqual(self.latest()['status'],'BLOCKED_NEWS');self.assertEqual(self.mt5.sent,[])
    def test_controls_api_persistence_and_cross_origin_rejection(self):
        server=build_server(self.store,0);threading.Thread(target=server.serve_forever,daemon=True).start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            body=json.dumps(DEFAULTS).encode()
            bad=urllib.request.Request(url+'/api/controls',data=body,headers={'Origin':'https://evil.example','Content-Type':'application/json','X-Dashboard-Request':'controls'})
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(bad)
            self.assertEqual(e.exception.code,403)
            req=urllib.request.Request(url+'/api/controls',data=body,headers={'Origin':url,'Content-Type':'application/json','X-Dashboard-Request':'controls'})
            with urllib.request.urlopen(req) as r:self.assertTrue(json.load(r)['ok'])
            self.assertTrue(controls(self.store,self.cfg)['risk_enabled'])
        finally:server.shutdown();server.server_close()

    def test_management_account_switch_blocks_mutation(self):
        ex=self.prepare({'partial_enabled':True,'breakeven_enabled':True});self.mt5.account.trade_mode=2;ex.refresh()
        self.assertEqual(len(self.mt5.sent),1)
    def test_management_missing_quote_does_not_mutate(self):
        ex=self.prepare({'partial_enabled':True,'breakeven_enabled':True})
        with patch.object(ex,'quote',side_effect=RuntimeError('stale')):ex.refresh()
        self.assertEqual(len(self.mt5.sent),1)
    def test_rejected_modify_not_called_send(self):
        ex=self.prepare({'breakeven_enabled':True});self.mt5.check_code=10016;ex.refresh()
        self.assertEqual(len(self.mt5.sent),1)
    def test_risk_execution_uses_calculated_volume(self):
        self.mt5.account.equity=10000
        self.mt5.order_calc_profit=lambda side,symbol,volume,entry,exit:(exit-entry)*100000*volume
        with self.store.connect() as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('trade_controls',json.dumps(DEFAULTS)))
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result())
        r=self.mt5.sent[0];self.assertGreater(r['volume'],.01)
        self.assertLessEqual(self.latest()['risk']['estimated_loss'],50.000001)
