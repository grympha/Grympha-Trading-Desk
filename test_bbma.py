import json
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
from bbma_bot import indicators, condition, make_plan, find_setup, assess, quote_age

CFG = json.loads((Path(__file__).parent/'config.example.json').read_text())

class Rules(unittest.TestCase):
    def test_time_checks(self):
        self.assertEqual(quote_age(10000, 9997), 3)
        self.assertEqual(quote_age(10000, 17197, 2), 3)
        self.assertEqual(quote_age(10000, 2797, -2), 3)
        self.assertLess(quote_age(10000, 17200), -5)
        self.assertGreater(quote_age(10000, 9000), 60)

    def test_weighted_high_and_population_bands(self):
        x = np.arange(120.)+100
        d = indicators(pd.DataFrame(dict(time=np.arange(120)*300, open=x, high=x+2, low=x-2, close=x)))
        self.assertAlmostEqual(d.iloc[-1].ma5_high, np.average(x[-5:]+2, weights=np.arange(1,6)))
        self.assertAlmostEqual(d.iloc[-1].upper, x[-20:].mean()+2*x[-20:].std())
        self.assertAlmostEqual(d.iloc[49].ema50, x[:50].mean())

    def context(self, buy=True):
        rows = [dict(close=110, ema50=100+i, mid=105, upper=115, lower=95, atr=3, time=i*3600) for i in range(4)]
        if not buy:
            for i,r in enumerate(rows): r.update(close=90, ema50=100-i, mid=95)
        return pd.DataFrame(rows)

    def test_directional_decision_is_json_serializable(self):
        # pandas comparisons yield numpy.bool_; logging must accept directional markets.
        for buy in (True, False):
            h = self.context(buy)
            for active in (True, False):
                h.loc[3, 'close'] = (120. if buy else 90.) if active else (110. if buy else 96.)
                c = condition(h, CFG)
                self.assertIsInstance(c['momentum'], bool)
                encoded = json.dumps({'condition': c, 'setup': None, 'entry_plan': None})
                self.assertEqual(json.loads(encoded)['condition']['momentum'], active)

    def test_direction_and_squeeze_block(self):
        self.assertEqual(condition(self.context(), CFG)['side'], 'BUY')
        self.assertEqual(condition(self.context(False), CFG)['side'], 'SELL')
        h=self.context(); h.loc[3,['upper','lower']]=[106,104]
        self.assertIsNone(condition(h,CFG)['side'])

    def setup(self):
        return dict(type='CSA_REENTRY',side='BUY',high=105.,low=100.,bar_time=0)

    def bars(self, times, closes):
        return pd.DataFrame([dict(time=t,open=104.,close=c,high=max(c,106.),low=103.,atr=2.) for t,c in zip(times,closes)])

    def test_no_entry_before_setup_known(self):
        p,_=make_plan(self.setup(), self.bars([600],[106.]),104.,104.1,.01,2,CFG)
        self.assertIsNone(p)

    def test_confirmed_buy_and_targets(self):
        p,_=make_plan(self.setup(),self.bars([900],[106.]),104.,104.1,.01,2,CFG)
        self.assertIsNotNone(p)
        self.assertLess(p['stop_loss'],p['entry'])
        self.assertGreater(p['tp1'],p['entry'])
        self.assertAlmostEqual(p['tp2']-p['entry'],2*(p['entry']-p['stop_loss']),places=2)

    def test_spread_chase_expiry_and_old_confirmation(self):
        for bars,bid,ask in [(self.bars([900],[106.]),104.,105.),
                              (self.bars([900],[106.]),110.,110.1),
                              (self.bars([2100],[106.]),104.,104.1),
                              (self.bars([900,1200],[106.,107.]),104.,104.1)]:
            p,_=make_plan(self.setup(),bars,bid,ask,.01,2,CFG)
            self.assertIsNone(p)

    def test_sell_direction(self):
        setup=dict(type='CSA_REENTRY',side='SELL',high=110.,low=105.,bar_time=0)
        bars=pd.DataFrame([dict(time=900,open=106.,close=104.,high=106.,low=103.,atr=2.)])
        p,_=make_plan(setup,bars,105.,105.1,.01,2,CFG)
        self.assertIsNotNone(p)
        self.assertGreater(p['stop_loss'],p['entry'])
        self.assertLess(p['tp2'],p['tp1'])

    def test_csa_without_pullback_is_not_setup(self):
        r=dict(time=0,open=110.,high=114.,low=109.,close=113.,mid=105.,
               ma5_low=103.,ma10_low=102.,ma5_high=107.,ma10_high=106.,
               upper=112.,lower=98.,atr=2.)
        s=pd.DataFrame([dict(r,time=i*900) for i in range(50)])
        self.assertIsNone(find_setup(s,'BUY',CFG))

    def test_pipeline_blocks_without_direction(self):
        h=self.context(); h.loc[3,['upper','lower']]=[106,104]
        result=assess(h,pd.DataFrame(),pd.DataFrame(),100,100.1,.01,2,CFG)
        self.assertIsNone(result['setup'])
        self.assertIsNone(result['entry_plan'])

    def test_reentry_requires_prior_direction(self):
        r=dict(time=0,open=104.,high=108.,low=102.,close=107.,mid=104.,
               ma5_low=103.,ma10_low=102.,ma5_high=106.,ma10_high=105.,
               upper=112.,lower=98.,atr=2.)
        s=pd.DataFrame([dict(r,time=i*900) for i in range(50)])
        self.assertEqual(find_setup(s,'BUY',CFG)['type'],'CSA_REENTRY')
        s.loc[:48,'close']=104.
        self.assertIsNone(find_setup(s,'BUY',CFG))

    def test_optional_mhv_sequence(self):
        r=dict(time=0,open=117.,high=118.,low=114.,close=115.,mid=116.,
               ma5_low=113.,ma10_low=112.,ma5_high=118.,ma10_high=119.,
               upper=125.,lower=100.,atr=2.)
        s=pd.DataFrame([dict(r,time=i*900) for i in range(50)])
        s.loc[46,['high','low','close','ma5_high','upper']]=[120.,115.,118.,121.,120.]
        s.loc[47,['high','low','close']]=[116.,110.,113.]
        s.loc[48,['high','low','close']]=[120.,116.,118.]
        cfg=dict(CFG,enable_mhv=True)
        self.assertEqual(find_setup(s,'SELL',cfg)['type'],'MHV_REVERSAL')
        self.assertIsNone(find_setup(s,'SELL',CFG))

if __name__=='__main__': unittest.main()
