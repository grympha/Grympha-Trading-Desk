import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import pandas as pd
from confluence_strategy import evaluate, Runner

CFG=dict(symbols=['XAUUSD'],poll_seconds=15,confluence={'min_score':3})


def frames():
    m15=pd.DataFrame([dict(time=900,close=100.,open=99.,high=101.,low=99.,ema9=100.,ema21=99.,ema50=98.,rsi=50.,macd=2.,macd_signal=1.,atr=2.,mid=100.,upper=104.,lower=96.)])
    h1=pd.DataFrame([dict(low=99.8,high=101.,ema9=103.,ema21=102.,ema50=101.)])
    h4=pd.DataFrame([dict(close=110.,ema50=105.,ema200=100.)])
    return m15,h1,h4


class Confluence(unittest.TestCase):
    def test_five_factors_and_original_atr_targets(self):
        r=evaluate(*frames(),100.,100.2,.01,2,CFG,1850)
        self.assertEqual(r['score'],5)
        self.assertTrue(r['qualified'])
        p=r['entry_plan'];self.assertEqual(p['entry'],100.2)
        self.assertEqual(p['stop_loss'],97.2)
        self.assertEqual(p['tp1'],104.2)
        self.assertEqual(p['tp2'],107.2)
        self.assertEqual(p['order_idea'],'QUOTE_REFERENCE')
        self.assertEqual(p['signal_kind'],'REFERENCE')
        json.dumps(r,allow_nan=False)

    def test_h4_direction_is_mandatory_even_when_other_indicators_align(self):
        m,h1,h4=frames();h4.loc[0,'close']=102.
        r=evaluate(m,h1,h4,100.,100.2,.01,2,CFG,1850)
        self.assertIsNone(r['condition']['side'])
        self.assertEqual(r['score'],0)
        self.assertIsNone(r['entry_plan'])

    def test_minimum_three_includes_h4_as_one_factor(self):
        m,h1,h4=frames();m.loc[0,'rsi']=80.;m.loc[0,'macd']=0.
        self.assertEqual(evaluate(m,h1,h4,100.,100.2,.01,2,CFG,1850)['score'],3)
        h1.loc[0,'low']=90.
        r=evaluate(m,h1,h4,100.,100.2,.01,2,CFG,1850)
        self.assertEqual(r['score'],2);self.assertIsNone(r['entry_plan'])

    def test_rsi_boundaries_are_strict(self):
        for value in (40.,65.):
            m,h1,h4=frames();m.loc[0,'rsi']=value
            self.assertFalse(evaluate(m,h1,h4,100.,100.2,.01,2,CFG,1850)['factors'][2]['passed'])

    def test_short_uses_bid_and_mirrored_levels(self):
        m,h1,h4=frames();h4.loc[0,['close','ema50','ema200']]=[90.,95.,100.]
        h1.loc[0,['ema9','ema21','ema50','high']]=[97.,98.,99.,100.2]
        m.loc[0,['macd','macd_signal']]=[0.,1.]
        r=evaluate(m,h1,h4,100.,100.2,.01,2,CFG,1850)
        self.assertEqual(r['score'],5)
        self.assertEqual(r['entry_plan']['entry'],100.)
        self.assertEqual(r['entry_plan']['stop_loss'],103.)
        self.assertEqual(r['entry_plan']['tp1'],96.)

    def test_timestamp_normalization_and_once_per_m15_cache(self):
        now=20000
        def rates(sym,secs,start,count):
            end=(now//secs)*secs-(secs if start==1 else 0)
            n=300 if start else 1
            return [dict(time=end-(n-1-i)*secs+7200,open=100+i*.1,high=102+i*.1,
                         low=99+i*.1,close=101+i*.1) for i in range(n)]
        mt5=SimpleNamespace(terminal_info=lambda:SimpleNamespace(connected=True),symbol_select=lambda *_:True,
            symbol_info_tick=lambda _:SimpleNamespace(time=now+7200,bid=130.,ask=130.1),
            symbol_info=lambda _:SimpleNamespace(point=.01,digits=2),copy_rates_from_pos=rates,
            TIMEFRAME_H4=14400,TIMEFRAME_H1=3600,TIMEFRAME_M15=900)
        with tempfile.TemporaryDirectory() as td:
            cfg=dict(CFG,tick_timestamp_offset_hours=2,bar_timestamp_offset_hours=2)
            runner=Runner(cfg,Path(td)/'state.json')
            a=runner.check(mt5,'XAUUSD',now)
            b=runner.check(mt5,'XAUUSD',now+1)
            self.assertTrue(a['new_analysis']);self.assertFalse(b['new_analysis'])
            self.assertEqual(a['analysis_utc'],b['analysis_utc'])
            self.assertEqual(a['analysis_id'],b['analysis_id'])
            self.assertEqual(a['entry_plan'],b['entry_plan'])
            # Cache survives restart without turning a historical quote into a new entry.
            self.assertFalse(Runner(cfg,Path(td)/'state.json').check(mt5,'XAUUSD',now+2)['new_analysis'])

if __name__=='__main__':unittest.main()
