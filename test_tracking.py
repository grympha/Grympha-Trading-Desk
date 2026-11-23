import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from dashboard_store import Store
from tracking_telegram import Board

stamp=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()


import contextlib


@contextlib.contextmanager
def board_on(store):
    """Live setup board switched on in the dashboard, with launcher credentials (fake)."""
    import telegram_notify
    telegram_notify.save_prefs(store, {**telegram_notify.defaults(), 'setup_board': True})
    with patch('telegram_config.active', return_value=('fake', 'fakechat')):
        yield

class Tracking(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db.sqlite3')
        self.cfg=dict(symbols=['AUDUSD'],poll_seconds=15,confluence=dict(symbols=['AUDUSD'],min_score=4),tracking=dict(enabled=True,telegram_update_seconds=60))
        self.store.start(self.cfg,now=1000)
    def tearDown(self):self.tmp.cleanup()
    def result(self,key='a',bid=99.9,ask=100.,side='BUY',qualified=True,strategy='CONFLUENCE'):
        p=dict(id=key,side=side,entry=100.,stop_loss=95. if side=='BUY' else 105.,tp1=105. if side=='BUY' else 95.,tp2=110. if side=='BUY' else 90.,
               setup='FIVE_FACTOR_CONFLUENCE' if strategy=='CONFLUENCE' else 'CSA_REENTRY',
               expires_utc=stamp(1600),confirmation_utc=stamp(1000),order_idea='QUOTE_REFERENCE' if strategy=='CONFLUENCE' else side+'_STOP')
        if strategy=='CONFLUENCE':p['signal_kind']='REFERENCE'
        return dict(strategy=strategy,symbol='AUDUSD',health='OK',analysis_id=key,new_analysis=True,
                    condition=dict(name='BULLISH' if side=='BUY' else 'BEARISH',side=side),
                    qualified=qualified,min_score=4,score=4 if qualified else 3,
                    quote=dict(bid=bid,ask=ask,digits=2,point=.01),entry_plan=p if qualified else None)
    def one(self,now=1020):return self.store.snapshot(now=now)['signals'][0]
    def test_bid_ask_pnl_is_price_and_r_not_cash(self):
        self.store.record(self.result(),now=1010)
        self.assertAlmostEqual(self.one()['tracking']['price_pnl'],-.1)
        self.store.observe('AUDUSD',dict(bid=102.,ask=102.1,point=.01),now=1020)
        t=self.one()['tracking'];self.assertEqual(t['pnl_state'],'PROFIT')
        self.assertAlmostEqual(t['price_pnl'],2.);self.assertAlmostEqual(t['pnl_r'],.4)
        self.assertNotIn('cash_profit',t);self.assertEqual(t['decision'],'HOLD')
    def test_new_candle_does_not_duplicate_or_reset_levels(self):
        self.store.record(self.result(),now=1010)
        new=self.result(key='b',bid=102,ask=102.1);new['entry_plan']['entry']=102.1
        self.store.record(new,now=1020)
        rows=self.store.snapshot(now=1020)['signals']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['entry'],100.)
        self.assertEqual(rows[0]['tracking']['paper_entry'],100.)
    def test_seen_while_running_is_not_queued_after_close(self):
        self.store.record(self.result(),now=1010)
        self.store.record(self.result(key='b',bid=102,ask=102.1),now=1020)
        self.store.observe('AUDUSD',dict(bid=110,ask=110.1),now=1030)
        self.assertEqual(self.one(1030)['lifecycle'],'PAPER_CLOSED_TP2')
        self.store.record(self.result(key='b'),now=1040)
        self.assertEqual(len(self.store.snapshot(now=1040)['signals']),1)
    def test_sl_observation_freezes_sampled_pnl(self):
        self.store.record(self.result(),now=1010)
        self.store.observe('AUDUSD',dict(bid=94,ask=94.1),now=1020)
        p=self.one();self.assertEqual(p['lifecycle'],'PAPER_CLOSED_SL')
        self.assertEqual(p['tracking']['decision'],'CLOSE')
        self.assertEqual(p['tracking']['price_pnl'],-6.)
        self.store.observe('AUDUSD',dict(bid=103,ask=103.1),now=1030)
        self.assertEqual(self.one(1030)['tracking']['price_pnl'],-6.)
    def test_tp1_holds_without_moving_stop(self):
        self.store.record(self.result(),now=1010)
        self.store.observe('AUDUSD',dict(bid=105,ask=105.1),now=1020)
        p=self.one();self.assertEqual(p['lifecycle'],'PAPER_TP1')
        self.assertEqual(p['stop_loss'],95.);self.assertEqual(p['tracking']['decision'],'HOLD')
    def test_threshold_drop_closes_paper_only(self):
        self.store.record(self.result(),now=1010)
        self.store.record(self.result(key='b',bid=101,ask=101.1,qualified=False),now=1020)
        p=self.one();self.assertEqual(p['lifecycle'],'PAPER_CLOSED_RULE')
        self.assertIn('below 4/5',p['tracking']['close_reason'])
    def test_short_marks_to_ask(self):
        self.store.record(self.result(side='SELL',bid=100,ask=100.2),now=1010)
        self.assertAlmostEqual(self.one()['tracking']['price_pnl'],-.2)
        self.store.observe('AUDUSD',dict(bid=97.9,ask=98.),now=1020)
        self.assertEqual(self.one()['tracking']['price_pnl'],2.)
    def test_bbma_waits_for_sampled_trigger_and_uses_gap_quote(self):
        self.store.record(self.result(strategy='BBMA',bid=99,ask=99.1),now=1010)
        self.assertEqual(self.one()['lifecycle'],'PENDING')
        self.assertIsNone(self.one()['tracking']['paper_entry'])
        self.store.observe('AUDUSD',dict(bid=100.3,ask=100.4),now=1020)
        p=self.one();self.assertEqual(p['lifecycle'],'PAPER_RUNNING')
        self.assertEqual(p['tracking']['paper_entry'],100.4)
    def test_expired_pending_does_not_fill(self):
        self.store.record(self.result(strategy='BBMA',bid=99,ask=99.1),now=1010)
        self.store.observe('AUDUSD',dict(bid=102,ask=102.1),now=1700)
        self.assertEqual(self.one(1700)['lifecycle'],'EXPIRED')
        self.assertIsNone(self.one(1700)['tracking']['paper_entry'])
    def test_running_reference_continues_past_review_deadline(self):
        self.store.record(self.result(),now=1010)
        r=self.result(key='b',bid=101,ask=101.1);r['entry_plan']['expires_utc']=stamp(2500)
        self.store.record(r,now=1650)
        self.assertEqual(len(self.store.snapshot(now=1650)['signals']),1)
        self.assertEqual(self.one(1650)['lifecycle'],'PAPER_RUNNING')
    def test_stale_analysis_never_gives_hold_or_closes_using_old_direction(self):
        self.store.record(self.result(),now=1010)
        self.store.observe('AUDUSD',dict(bid=101,ask=101.1),now=1400)
        self.assertEqual(self.one(1400)['tracking']['decision'],'REVIEW_DATA')
        self.assertEqual(self.one(1400)['lifecycle'],'PAPER_RUNNING')
    def test_edit_same_telegram_message_instead_of_resending(self):
        self.store.record(self.result(),now=1010);calls=[]
        def send(token,method,body):calls.append((method,body));return {'message_id':7}
        board=Board(self.store,send=send)
        with board_on(self.store):
            board.update(now=1010)
            self.store.observe('AUDUSD',dict(bid=101,ask=101.1),now=1020)
            board.update(now=1020)
            self.assertEqual(len(calls),1)
            board.update(now=1071)
            self.assertEqual(calls[-1][0],'editMessageText')
            self.assertEqual(calls[-1][1]['message_id'],7)
            self.store.record(self.result(key='b',bid=102,ask=102.1),now=1072)
            board.update(now=1072)
            self.assertEqual(sum(m=='sendMessage' for m,_ in calls),1)
    def test_failed_edit_does_not_send_duplicate(self):
        self.store.record(self.result(),now=1010);calls=[]
        def send(token,method,body):
            calls.append(method)
            if method=='editMessageText':raise RuntimeError('failure')
            return {'message_id':7}
        board=Board(self.store,send=send)
        with board_on(self.store):
            board.update(now=1010)
            self.store.observe('AUDUSD',dict(bid=94,ask=94.1),now=1020)
            board.update(now=1020)
        self.assertEqual(calls,['sendMessage','editMessageText'])
    def test_restart_keeps_entry_and_marks_gap(self):
        self.store.record(self.result(),now=1010);self.store.start(self.cfg,now=1020)
        r=self.result(key='b',bid=101,ask=101.1);self.store.record(r,now=1030)
        self.assertEqual(len(self.store.snapshot(now=1030)['signals']),1)
        self.assertEqual(self.one(1030)['tracking']['paper_entry'],100.)
        self.assertTrue(self.one(1030)['tracking']['continuity_gap'])

    def test_current_legacy_reference_resumes_in_place_with_unverified_gap(self):
        old=dict(self.cfg,tracking={'enabled':False})
        self.store.start(old,now=1000)
        self.store.record(self.result(),now=1010)
        self.store.start(self.cfg,now=1020)
        r=self.result(bid=102.,ask=102.1);r['new_analysis']=False
        self.store.record(r,now=1030)
        rows=self.store.snapshot(now=1030)['signals']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['entry'],100.)
        self.assertEqual(rows[0]['tracking']['price_pnl'],2.)
        self.assertTrue(rows[0]['tracking']['continuity_gap'])
        self.assertIsNone(rows[0]['tracking']['entry_epoch'])

    def test_outdated_cached_reference_without_existing_record_is_not_backfilled(self):
        r=self.result();r['new_analysis']=False
        self.store.record(r,now=1010)
        self.assertEqual(self.store.snapshot(now=1020)['signals'],[])

    def test_telegram_id_survives_restart_and_is_edited(self):
        self.store.record(self.result(),now=1010);calls=[]
        def send(token,method,body):calls.append((method,body));return {'message_id':77}
        with board_on(self.store):
            Board(self.store,send=send).update(now=1010)
            self.store.start(self.cfg,now=1060)
            self.store.record(self.result(bid=102.,ask=102.1),now=1075)
            Board(self.store,send=send).update(now=1075)
        self.assertEqual([m for m,_ in calls],['sendMessage','editMessageText'])
        self.assertEqual(calls[1][1]['message_id'],77)

    def test_strategies_track_same_symbol_independently(self):
        self.store.record(self.result(),now=1010)
        self.store.record(self.result(strategy='BBMA',bid=99,ask=99.1),now=1011)
        rows=self.store.snapshot(now=1020)['signals']
        self.assertEqual(len(rows),2)
        self.assertEqual({p['strategy'] for p in rows},{'BBMA','CONFLUENCE'})
        self.assertEqual(len({p['key'] for p in rows}),2)

    def test_stale_confluence_quote_does_not_start_paper_position(self):
        r=self.result();r['quote']['age_seconds']=120
        self.store.record(r,now=1010)
        self.assertEqual(self.store.snapshot(now=1020)['signals'],[])
