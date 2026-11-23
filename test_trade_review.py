import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dashboard_store import Store
from demo_execution import DemoExecutor
import test_demo_execution as fixture
from trade_review import TradeReviewer,summary,analyse,capture,market_snapshot,pair_summary,is_breakeven

class BreakEven(unittest.TestCase):
    def _report(self,**kw):
        base=dict(symbol='EURUSD',currency='USD',exit_reason='Stop loss',side='BUY',
                  entry=1.1000,final_sl=1.1000,outcome='FLAT',realised=0.0)
        base.update(kw);return base

    def test_is_breakeven_detects_stop_at_entry_or_better(self):
        self.assertTrue(is_breakeven(self._report(final_sl=1.1000)))                 # BUY stop at entry
        self.assertTrue(is_breakeven(self._report(final_sl=1.1005)))                 # BUY stop in profit
        self.assertFalse(is_breakeven(self._report(final_sl=1.0990)))                # BUY stop below entry = real loss
        self.assertTrue(is_breakeven(self._report(side='SELL',entry=1.1,final_sl=1.1)))
        self.assertFalse(is_breakeven(self._report(side='SELL',entry=1.1,final_sl=1.1010)))  # SELL stop above entry = loss
        self.assertFalse(is_breakeven(self._report(exit_reason='Take profit',final_sl=1.1000)))
        self.assertFalse(is_breakeven(self._report(final_sl=None)))

    def test_pair_summary_splits_be_out_of_sl_and_preserves_total(self):
        reports=[self._report(final_sl=1.1000),                       # BE
                 self._report(final_sl=1.0990,outcome='LOSS',realised=-10),  # real SL
                 self._report(exit_reason='Take profit',outcome='WIN',realised=15),
                 self._report(exit_reason='Expert / algorithm',outcome='WIN',realised=3)]
        row=pair_summary(reports)[0]
        self.assertEqual(row['total'],4)
        self.assertEqual(row['tp'],1)
        self.assertEqual(row['be'],1)
        self.assertEqual(row['sl'],1)
        self.assertEqual(row['other'],1)
        self.assertEqual(row['tp']+row['be']+row['sl']+row['other'],row['total'])


class Review(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db.sqlite3')
        self.cfg=dict(symbols=['AUDUSD'],poll_seconds=15,confluence=dict(symbols=['AUDUSD']))
        self.store.start(self.cfg);self.mt5=fixture.FakeMT5();self.ex=DemoExecutor(self.mt5,self.store,self.cfg)
        self.scope=self.ex.account_hash;self.reviewer=TradeReviewer(self.store,self.cfg,self.mt5)
    def tearDown(self):self.tmp.cleanup()
    def payload(self,**changes):
        d=dict(side='BUY',reference=100.,actual_entry=101.,sl=99.,tp1=103.,tp2=105.,exit_price=99.,exit_epoch=200,
               realised=-20.,currency='USD',close_reason=4,position_ticket=701,quote_fresh=True,last_broker_epoch=100,current=101.,
               context=dict(setup='REENTRY',review=dict(captured_epoch=90,utc_hour=8,poll_seconds=15,timeframes={'M5':[{'close':100}]})),
               risk=dict(estimated_loss=20),news=dict(blocked=False))
        d.update(changes);return d
    def put(self,key='a',status='CLOSED',updated=200,scope=None,strategy='BBMA',p=None):
        scope=scope or self.scope
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO demo_orders VALUES (?,?,?,?,?,?,?,?,?)',(scope+'|'+key,scope,key,strategy,'AUDUSD',90,updated,status,json.dumps(p or self.payload())))
    def run_review(self,now=200):self.reviewer.refresh(force=True,now=now);return summary(self.store)
    def test_backfill_and_dedup_and_no_orders(self):
        self.put();a=self.run_review();b=self.run_review()
        self.assertEqual(a['total'],1);self.assertEqual(b['total'],1)
        self.assertEqual(a['currencies']['USD']['net'],-20)
        self.assertEqual(a['reviews'][0]['samples'],0)
        self.assertIsNone(a['reviews'][0]['sampled_mfe_r']);self.assertFalse(a['automatic_changes'])
        self.assertEqual(self.mt5.sent,[])
    def test_buy_samples_and_sell_sign(self):
        self.put(status='OPEN',updated=100,p=self.payload(current=103.,last_broker_epoch=100))
        self.run_review(100)
        self.put(status='OPEN_PARTIAL',updated=115,p=self.payload(current=100.,last_broker_epoch=115))
        self.run_review(115);self.put();r=self.run_review()['reviews'][0]
        self.assertEqual(r['sampled_mfe_r'],1);self.assertEqual(r['sampled_mae_r'],.5)
        self.assertIn('SAMPLED_TP1_THEN_NET_LOSS',r['tags'])
        self.assertEqual(r['slippage_r'],.5)
        self.put(key='sell',status='OPEN',updated=100,p=self.payload(side='SELL',current=99,sl=103,tp1=99,tp2=97))
        self.run_review(100)
        self.put(key='sell',p=self.payload(side='SELL',sl=103,tp1=99,tp2=97))
        sell=next(x for x in self.run_review()['reviews'] if x['side']=='SELL')
        self.assertEqual(sell['sampled_mfe_r'],1);self.assertEqual(sell['slippage_r'],-.5)
    def test_sl_tp_volume_changes_are_sampled_without_modifying_orders(self):
        self.put(status='OPEN',p=self.payload(last_broker_epoch=100,actual_sl=99,actual_tp=105,volume=.04));self.run_review(100)
        self.put(status='OPEN_PARTIAL',p=self.payload(last_broker_epoch=130,actual_sl=101,actual_tp=105,volume=.02));self.run_review(130)
        self.put();r=self.run_review()['reviews'][0]
        self.assertEqual(len(r['protection_samples']),2)
        self.assertEqual(r['protection_samples'][-1]['sl'],101)
        self.assertEqual(r['protection_samples'][-1]['volume'],.02)
        self.assertEqual(self.mt5.sent,[])

    def test_stale_quotes_not_sampled_or_inferred_zero(self):
        self.put(status='OPEN',p=self.payload(quote_fresh=False))
        self.run_review();self.put();r=self.run_review()['reviews'][0]
        self.assertIsNone(r['sampled_mae_r'])
    def test_repeated_broker_sample_is_not_counted_twice(self):
        self.put(status='OPEN',p=self.payload(last_broker_epoch=200))
        self.run_review();self.run_review();self.put();self.assertEqual(self.run_review()['reviews'][0]['samples'],1)
    def test_sampling_gap_reported_and_restart_dedup(self):
        self.put(status='OPEN',p=self.payload(last_broker_epoch=100));self.run_review(100)
        self.reviewer=TradeReviewer(self.store,self.cfg,self.mt5)
        self.put(status='OPEN',p=self.payload(last_broker_epoch=300));self.run_review(300)
        self.put(updated=301);r=self.run_review(301)['reviews'][0]
        self.assertEqual(r['samples'],2);self.assertIn('Sampling gap detected',r['data_quality'])
    def test_account_switch_hides_previous_account(self):
        self.put();self.assertEqual(self.run_review()['total'],1)
        self.mt5.account.login=99;self.assertEqual(self.run_review()['total'],0)
    def test_unconfirmed_and_cancelled_not_counted(self):
        for i,status in enumerate(['PENDING','OPEN','UNCERTAIN','CANCELLED','REJECTED']):self.put(key=str(i),status=status)
        self.assertEqual(self.run_review()['total'],0)
    def test_manual_history_not_imported(self):
        self.mt5.deals=[fixture.N(profit=-99)]
        self.assertEqual(self.run_review()['total'],0)
    def test_unknown_legacy_quality_and_no_zero_pnl_inference(self):
        self.put(p=self.payload(context={},realised=None))
        r=self.run_review();self.assertEqual(r['currencies'],{})
        self.assertEqual(r['reviews'][0]['outcome'],'UNKNOWN')
        self.assertTrue(any('LEGACY' in f for f in r['reviews'][0]['data_quality']))
    def test_winners_losses_and_currencies_separate(self):
        self.put(key='win',p=self.payload(realised=10));self.put(key='loss')
        self.put(key='eur',p=self.payload(realised=8,currency='EUR'))
        r=self.run_review();self.assertEqual(r['currencies']['USD']['net'],-10)
        self.assertEqual(r['currencies']['EUR']['net'],8)
        self.assertEqual(len(r['patterns']),2)
    def test_pattern_threshold_is_review_only(self):
        for i in range(20):self.put(key=str(i),p=self.payload(realised=-20 if i<5 else 10))
        p=self.run_review()['patterns'][0]
        self.assertEqual(p['status'],'UNDER REVIEW');self.assertEqual(p['losses'],5)
        self.assertIn('TEST IDEA',p['suggestions'][0]);self.assertIn('No rule approved',p['evidence'])
    def test_filters_and_strategy_independence(self):
        self.put();self.put(key='c',strategy='CONFLUENCE');self.run_review()
        self.assertEqual(summary(self.store,'BBMA')['total'],1)
        self.assertEqual(summary(self.store,'ALL','GBPUSD')['total'],0)
        with self.assertRaises(ValueError):summary(self.store,'WRONG')
    def test_closed_result_update_refreshes_review(self):
        self.put();self.run_review();self.put(updated=201,p=self.payload(realised=-22))
        self.assertEqual(self.run_review(201)['currencies']['USD']['net'],-22)
    def test_capture_is_bounded_no_credentials_and_no_future_candles(self):
        import pandas as pd
        f=pd.DataFrame([dict(time=i,close=float(i),bad=float('nan')) for i in range(20)])
        snap=market_snapshot({'H1':f})
        self.assertEqual(len(snap['H1']),8);self.assertNotIn('bad',snap['H1'][0])
        r=capture(dict(entry_plan={'setup':'X'},review_market=snap),dict(poll_seconds=15,telegram_token='SECRET'),{})
        self.assertNotIn('SECRET',json.dumps(r));self.assertEqual(r['timeframes']['H1'][-1]['time'],19)
    def test_read_only_no_execution_or_network(self):
        tree=ast.parse((Path(__file__).parent/'trade_review.py').read_text())
        calls=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
        for name in ['order_send','order_check','urlopen','request']:self.assertNotIn(name,calls)
    def test_all_reviewed_rows_count_beyond_display_limit(self):
        for i in range(205):self.put(key=str(i),p=self.payload(realised=1))
        r=self.run_review();self.assertEqual(r['total'],205);self.assertEqual(len(r['reviews']),200)
        self.assertEqual(r['currencies']['USD']['net'],205)

    def test_read_only_api_and_invalid_filter(self):
        import threading,urllib.request,urllib.error
        from dashboard_server import build_server
        self.put();self.run_review()
        server=build_server(self.store,0);threading.Thread(target=server.serve_forever,daemon=True).start()
        url=f'http://127.0.0.1:{server.server_port}/api/review'
        try:
            with urllib.request.urlopen(url) as response:self.assertEqual(json.load(response)['total'],1)
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(url+'?strategy=INVALID')
            self.assertEqual(e.exception.code,400)
        finally:server.shutdown();server.server_close()

    def test_empty_schema_safe_on_existing_dashboard(self):
        with tempfile.TemporaryDirectory() as td:
            other=Store(Path(td)/'old.sqlite3');self.assertEqual(summary(other)['status'],'WAITING')

if __name__=='__main__':unittest.main()
