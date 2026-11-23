"""Fake-MT5-only execution tests. No terminal import or broker call occurs."""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from dashboard_store import Store
from demo_execution import DemoExecutor

stamp=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()

class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO=0;ACCOUNT_MARGIN_MODE_RETAIL_HEDGING=2
    TRADE_ACTION_DEAL=1;TRADE_ACTION_PENDING=5;TRADE_ACTION_REMOVE=8
    ORDER_TYPE_BUY=0;ORDER_TYPE_SELL=1;ORDER_TYPE_BUY_STOP=4;ORDER_TYPE_SELL_STOP=5
    ORDER_TIME_GTC=0;ORDER_TIME_SPECIFIED=2
    ORDER_FILLING_FOK=0;ORDER_FILLING_IOC=1;ORDER_FILLING_RETURN=2
    def __init__(self):
        self.account=N(login=11,server='Fake-Demo',trade_mode=0,margin_mode=2,trade_allowed=True,trade_expert=True,currency='USD')
        self.terminal=N(connected=True,trade_allowed=True,tradeapi_disabled=False)
        self.info=N(point=.00001,digits=5,trade_mode=4,volume_min=.01,volume_max=10.,volume_step=.01,
                    trade_tick_size=.00001,trade_stops_level=5,filling_mode=1,trade_exemode=2,expiration_mode=4)
        self.sent=[];self.positions=[];self.orders=[];self.deals=[];self.old_orders=[]
        self.none_reply=False;self.check_code=0;self.after_check=None
    def account_info(self):return self.account
    def terminal_info(self):return self.terminal
    def symbol_select(self,*_):return True
    def symbol_info(self,*_):return self.info
    def symbol_info_tick(self,*_):return N(time=int(time.time()),bid=1.0999,ask=1.1000)
    def positions_get(self):return tuple(self.positions)
    def orders_get(self):return tuple(self.orders)
    def history_deals_get(self,*args,**kw):
        if 'ticket' in kw:return tuple(d for d in self.deals if d.ticket==kw['ticket'])
        if 'position' in kw:return tuple(d for d in self.deals if d.position_id==kw['position'])
        return tuple(self.deals)
    def history_orders_get(self,**kw):return tuple(self.old_orders)
    def order_check(self,request):
        if self.after_check:self.after_check()
        return N(retcode=self.check_code)
    def order_send(self,r):
        self.sent.append(dict(r))
        if self.none_reply:return None
        if r['action']==self.TRADE_ACTION_REMOVE:
            self.orders=[];self.old_orders=[N(state=2)]
            return N(retcode=10009)
        if r['action']==self.TRADE_ACTION_PENDING:
            self.orders=[N(ticket=500,symbol=r['symbol'],magic=r['magic'],comment=r['comment'])]
            return N(retcode=10008,order=500,deal=0,volume=0.,price=0.)
        self.positions=[N(ticket=701,identifier=700,symbol=r['symbol'],magic=r['magic'],comment=r['comment'],
                          price_open=1.10002,price_current=1.1007,volume=r['volume'],profit=4.56,swap=-.02,sl=r['sl'],tp=r['tp'])]
        self.deals=[N(ticket=1001,order=500,position_id=700,symbol=r['symbol'],magic=r['magic'],
                      entry=0,profit=0.,commission=-.02,swap=0.,fee=0.,reason=3)]
        return N(retcode=10009,order=500,deal=1001,volume=r['volume'],price=1.10002)

class Demo(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db.sqlite3')
        self.cfg=dict(symbols=['AUDUSD'],poll_seconds=15,confluence=dict(symbols=['AUDUSD'],min_score=4))
        self.store.start(self.cfg);self.mt5=FakeMT5()
    def tearDown(self):self.tmp.cleanup()
    def result(self,strategy='CONFLUENCE',key='a'):
        now=time.time();reference=strategy=='CONFLUENCE'
        p=dict(id=key,side='BUY',entry=1.1 if reference else 1.1005,stop_loss=1.099,
               tp1=1.1013 if reference else 1.1015,tp2=1.1023 if reference else 1.1025,
               setup='FIVE_FACTOR_CONFLUENCE' if reference else 'CSA_REENTRY',
               expires_utc=stamp(now+600),confirmation_utc=stamp(now-30),order_idea='QUOTE_REFERENCE' if reference else 'BUY_STOP')
        if reference:p['signal_kind']='REFERENCE'
        return dict(strategy=strategy,symbol='AUDUSD',health='OK',analysis_id=key,new_analysis=True,
                    condition=dict(name='BULLISH',side='BUY'),qualified=True,min_score=4,score=4,
                    quote=dict(bid=1.0999,ask=1.1,digits=5,point=.00001,age_seconds=0),entry_plan=p)
    def submit(self,ex,r):self.store.record(r);ex.submit(r)
    def latest(self):return self.store.snapshot()['demo_orders'][0]
    def test_real_contest_and_netting_rejected_without_order(self):
        for mode,margin in [(2,2),(1,2),(0,0)]:
            self.mt5.account.trade_mode=mode;self.mt5.account.margin_mode=margin
            with self.assertRaises(RuntimeError):DemoExecutor(self.mt5,self.store,self.cfg)
            self.assertEqual(self.mt5.sent,[])
    def test_permissions_required(self):
        self.mt5.terminal.tradeapi_disabled=True
        with self.assertRaises(RuntimeError):DemoExecutor(self.mt5,self.store,self.cfg)
    def test_market_request_has_stops_and_independent_tag(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);r=self.result();self.submit(ex,r)
        req=self.mt5.sent[0]
        self.assertEqual(req['action'],1);self.assertEqual(req['volume'],.01)
        self.assertEqual(req['sl'],1.099);self.assertEqual(req['tp'],1.1023)
        self.assertEqual(req['magic'],816002)
        self.assertRegex(req['comment'],r'^Confluence 4/5 #[0-9a-f]{6}$')  # readable label + tracking id
        self.assertLessEqual(len(req['comment']),31)
        ex.submit(r);self.assertEqual(len(self.mt5.sent),1)
    def test_broker_fill_not_reference_is_attributed(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result())
        # Broker may remove the comment: direct deal->position_id still identifies the position.
        self.mt5.positions[0].comment=''
        ex.refresh();d=self.latest()
        self.assertEqual(d['status'],'OPEN');self.assertEqual(d['position_ticket'],701)
        self.assertEqual(d['actual_entry'],1.10002);self.assertEqual(d['profit'],4.56)
        self.assertNotEqual(d['order_ticket'],d['position_ticket'])
    def test_rule_close_is_review_only_no_market_close(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result())
        r=self.result('CONFLUENCE','b');r.update(qualified=False,score=3,entry_plan=None)
        self.store.record(r);ex.refresh()
        self.assertEqual(self.latest()['decision'],'CLOSE_REVIEW')
        self.assertEqual(len(self.mt5.sent),1)
    def test_broker_exit_history_is_required_for_realised_pnl(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result());ex.refresh()
        self.mt5.positions=[]
        ex.refresh();self.assertEqual(self.latest()['status'],'REVIEW_UNKNOWN')
        self.assertIsNone(self.latest()['realised'])
        self.mt5.deals.append(N(ticket=1002,order=501,position_id=700,symbol='AUDUSD',magic=816002,entry=1,
                               profit=8.,commission=-.5,swap=-.1,fee=-.05,reason=5))
        ex.refresh();d=self.latest()
        self.assertEqual(d['status'],'CLOSED');self.assertAlmostEqual(d['realised'],7.33)
        self.assertEqual(len(self.mt5.sent),1)
    def test_pending_entry_and_native_expiry(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);r=self.result('BBMA');self.submit(ex,r)
        req=self.mt5.sent[0];self.assertEqual(req['action'],5);self.assertEqual(req['type'],4)
        self.assertEqual(req['type_time'],2);self.assertEqual(req['type_filling'],2)
        self.assertAlmostEqual(req['expiration'],time.time()+600,delta=3)
        ex.refresh();self.assertEqual(self.latest()['status'],'PENDING')
    def test_pending_stop_distance_uses_order_price(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);r=self.result('BBMA');r['entry_plan']['stop_loss']=1.1002
        self.mt5.symbol_info_tick=lambda *_:N(time=int(time.time()),bid=1.09999,ask=1.10000)
        self.submit(ex,r);self.assertEqual(len(self.mt5.sent),1)
    def test_owned_pending_is_cancelled_after_expiry_not_a_market_close(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result('BBMA'))
        future=time.time()+1200
        with patch('demo_execution.time.time',return_value=future):ex.refresh()
        self.assertEqual(self.mt5.sent[-1],{'action':8,'order':500})
        self.assertEqual(self.latest()['status'],'CANCELLED')
    def test_ambiguous_send_never_retries(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.mt5.none_reply=True;r=self.result()
        self.submit(ex,r);self.assertEqual(self.latest()['status'],'UNCERTAIN')
        ex.submit(r);ex.refresh();ex.submit(r)
        self.assertEqual(len(self.mt5.sent),1)
    def test_precheck_reject_does_not_send(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.mt5.check_code=10014
        self.submit(ex,self.result());self.assertEqual(self.mt5.sent,[])
        self.assertEqual(self.latest()['status'],'CHECK_REJECTED')
    def test_account_change_between_check_and_send_blocks(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg)
        self.mt5.after_check=lambda:setattr(self.mt5.account,'trade_mode',2)
        with self.assertRaises(RuntimeError):self.submit(ex,self.result())
        self.assertEqual(self.mt5.sent,[])
    def test_different_demo_account_also_stops_session(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.mt5.account.login=12
        with self.assertRaises(RuntimeError):self.submit(ex,self.result())
        self.assertEqual(self.mt5.sent,[])
    def test_invalid_volume_never_rounded_up(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.mt5.info.volume_min=.1
        self.submit(ex,self.result());self.assertEqual(self.mt5.sent,[])
        self.assertEqual(self.latest()['status'],'BLOCKED')
    def test_old_paper_scenario_is_not_retroactively_traded(self):
        self.store.record(self.result());ex=DemoExecutor(self.mt5,self.store,self.cfg)
        ex.submit(self.result());self.assertEqual(self.mt5.sent,[])
        self.assertEqual(self.latest()['status'],'SKIPPED_OLD_SETUP')
    def test_unsupported_pending_expiry_is_not_replaced_with_gtc(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.mt5.info.expiration_mode=1
        self.submit(ex,self.result('BBMA'));self.assertEqual(self.mt5.sent,[])
        self.assertEqual(self.latest()['status'],'BLOCKED')
    def test_restart_reconciles_existing_ticket_without_new_entry(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);r=self.result();self.submit(ex,r)
        restart=DemoExecutor(self.mt5,self.store,self.cfg);restart.refresh();restart.submit(r)
        self.assertEqual(len(self.mt5.sent),1);self.assertEqual(self.latest()['position_ticket'],701)
    def test_manual_position_is_not_closed_or_claimed(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);r=self.result();self.submit(ex,r)
        manual=N(ticket=900,identifier=901,symbol='AUDUSD',magic=0,comment='manual',price_open=1.1,price_current=1.099,volume=.1,profit=-1.,swap=0.,sl=0.,tp=0.)
        self.mt5.positions.append(manual);ex.refresh()
        self.assertEqual(self.latest()['position_ticket'],701)
        self.assertEqual(len(self.mt5.sent),1)

    def test_demo_still_open_blocks_replacement_after_paper_close(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.submit(ex,self.result());ex.refresh()
        failed=self.result(key='b');failed.update(qualified=False,score=3,entry_plan=None)
        self.store.record(failed)
        new=self.result(key='c');self.store.record(new);ex.submit(new)
        self.assertEqual(len(self.mt5.sent),1)
        self.assertEqual(len(self.store.snapshot()['signals']),1)

    def test_both_strategies_keep_independent_demo_tickets(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg)
        self.submit(ex,self.result());self.submit(ex,self.result('BBMA'))
        ex.refresh();rows=self.store.snapshot()['demo_orders']
        self.assertEqual({r['strategy'] for r in rows},{'BBMA','CONFLUENCE'})
        self.assertEqual({r['status'] for r in rows},{'OPEN','PENDING'})
        self.assertEqual(len(self.mt5.sent),2)

    def test_capacity_counts_manual_positions_without_managing_them(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.mt5.positions=[N() for _ in range(6)]
        self.submit(ex,self.result());self.assertEqual(self.mt5.sent,[])
        self.assertEqual(self.latest()['status'],'SKIPPED_CAPACITY')

    def test_unsupported_market_fill_policy_is_blocked(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);self.mt5.info.filling_mode=0
        self.submit(ex,self.result());self.assertEqual(self.mt5.sent,[])
        self.assertEqual(self.latest()['status'],'BLOCKED')

    def test_partial_fill_is_recorded_without_resending_remainder(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg);original=self.mt5.order_send
        def partial(request):
            reply=original(request);reply.retcode=10010;reply.volume=.005
            self.mt5.positions[0].volume=.005
            return reply
        self.mt5.order_send=partial
        r=self.result();self.submit(ex,r);ex.refresh();ex.submit(r)
        self.assertEqual(self.latest()['status'],'OPEN_PARTIAL')
        self.assertEqual(self.latest()['volume'],.005)
        self.assertEqual(len(self.mt5.sent),1)

    def test_stale_entry_quote_blocks_send(self):
        ex=DemoExecutor(self.mt5,self.store,self.cfg)
        self.mt5.symbol_info_tick=lambda *_:N(time=int(time.time())-30,bid=1.0999,ask=1.1)
        self.submit(ex,self.result());self.assertEqual(self.mt5.sent,[])
        self.assertEqual(self.latest()['status'],'BLOCKED')

if __name__=='__main__':unittest.main()

class DemoRunner(unittest.TestCase):
    def run_bot(self,check_only):
        import contextlib,io
        from unittest.mock import Mock
        import bbma_bot
        m=FakeMT5();m.initialize=Mock(return_value=True);m.shutdown=Mock()
        m.TIMEFRAME_H1=3600;m.TIMEFRAME_M15=900;m.TIMEFRAME_M5=300
        def rates(symbol,seconds,start,count):
            end=(int(time.time())//seconds)*seconds-seconds
            return [dict(time=end-(159-i)*seconds,open=1.1,high=1.101,low=1.099,close=1.1001) for i in range(160)]
        m.copy_rates_from_pos=rates
        now=time.time()
        p=dict(id='full-run',side='BUY',entry=1.1005,stop_loss=1.099,tp1=1.1015,tp2=1.1025,
               setup='CSA_REENTRY',order_idea='BUY_STOP',expires_utc=stamp(now+600),confirmation_utc=stamp(now-30))
        analysis=dict(condition=dict(name='BULLISH',side='BUY'),setup=dict(type='CSA_REENTRY'),entry_plan=p,reason='Confirmed')
        cfg=dict(symbols=['AUDUSD'],poll_seconds=15,confirmation_bars=4,bar_grace_seconds=120,max_tick_age_seconds=60,confluence=dict(enabled=False))
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'config.json').write_text(json.dumps(cfg))
            args=N(config=str(root/'config.json'),once=True,test_telegram=False,diagnose_time=False,
                   dashboard=False,dashboard_port=8765,demo_check=check_only,demo_execution=not check_only,demo_lot=.01)
            with patch.object(bbma_bot,'ROOT',root),patch.object(bbma_bot,'assess',return_value=analysis),\
                 patch.dict('sys.modules',{'MetaTrader5':m,'dotenv':N(load_dotenv=lambda *_:None)}),\
                 patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'','TELEGRAM_CHAT_ID':''}),contextlib.redirect_stdout(io.StringIO()):
                bbma_bot.run(args)
            snap=Store(root/'dashboard.sqlite3').snapshot()
            m.shutdown.assert_called_once()
            return m,snap
    def test_preflight_run_sends_no_order(self):
        m,s=self.run_bot(True)
        self.assertEqual(m.sent,[])
        self.assertEqual(s['meta']['demo_execution']['status'],'STOPPED')
    def test_opt_in_run_places_and_reconciles_only_demo_pending_order(self):
        m,s=self.run_bot(False)
        self.assertEqual(len(m.sent),1)
        self.assertEqual(m.sent[0]['action'],m.TRADE_ACTION_PENDING)
        self.assertEqual(s['demo_orders'][0]['status'],'PENDING')
        self.assertEqual(s['meta']['demo_execution']['status'],'STOPPED')
        self.assertEqual(len(m.orders),1) # Stopping the client does not pretend to cancel broker orders.
