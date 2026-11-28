"""Elliott Wave Live Test agent: zigzag, the three hard rules, Fibonacci depth, entries and registration."""
import unittest

import live_test_elliott as ew
from live_test import build_agent, elliott_agent_configs
from market_data import Bar, Quote

H1D = 3600
T0 = 1_790_000_000 - 1_790_000_000 % H1D


def path(waypoints, step=1.0):
    out = [float(waypoints[0])]
    for w in waypoints[1:]:
        while abs(out[-1] - w) > 1e-9:
            out.append(out[-1] + (step if w > out[-1] else -step))
    return out


def h1_bars(closes, wick=0.3, lead=20):
    """`lead` flat-ish bars first (ATR warm-up), then the path; ATR ~1.6, zigzag size ~4."""
    first = closes[0]
    series = [first + (0.5 if i % 2 else -0.5) for i in range(lead)] + closes
    bars, prev = [], series[0]
    for i, c in enumerate(series):
        bars.append(Bar(T0 + i * H1D, prev, max(prev, c) + wick, min(prev, c) - wick, c))
        prev = c
    return bars


def mirror(bars):
    return [Bar(b.time, 200 - b.open, 200 - b.low, 200 - b.high, 200 - b.close) for b in bars]


class FakeMarket:
    def __init__(self, h1):
        self.h1 = h1

    def candles(self, symbol, tf, count, now=None):
        if tf != 'H1':
            raise ValueError('only H1 in this test')
        return [b for b in self.h1 if b.time + H1D <= now][-count:]


def run(agent, bars, state=None, spread=0.02, delay=10):
    now = bars[-1].time + H1D + delay
    mid = bars[-1].close
    state = {} if state is None else state
    sig = agent.evaluate(now, Quote('XAUUSD', mid, mid + spread, now, 0.0, 0.01, 2), [], 0.01, state,
                         market=FakeMarket(bars), symbol='XAUUSD')
    return sig, state


def agent(**cfg):
    a = ew.ElliottImpulse(cfg)
    a._regime = lambda *x, **k: 'TRENDING'
    return a


class Zigzag(unittest.TestCase):
    def test_swings_and_the_open_leg(self):
        piv, leg = ew.zigzag(h1_bars(path([105, 90, 100, 95, 97])))
        self.assertEqual([(p['kind'], round(p['price'], 1)) for p in piv][-3:], [('H', 105.8), ('L', 89.7), ('H', 100.3)])
        self.assertEqual((leg['dir'], round(leg['price'], 1)), ('down', 94.7))     # wave 2 low, not confirmed yet
        self.assertTrue(all(p['confirm'] > p['time'] for p in piv))

    def test_no_peeking_ahead(self):
        bars = h1_bars(path([105, 90, 100, 95, 112, 106, 108]))
        full, _ = ew.zigzag(bars)
        for k in range(30, len(bars)):
            part, _ = ew.zigzag(bars[:k])
            known = [p for p in full if p['confirm'] <= bars[k - 1].time + H1D]
            self.assertEqual(part, known, k)


class Wave3(unittest.TestCase):
    def test_buy_after_wave_2(self):
        sig, st = run(agent(), h1_bars(path([105, 90, 100, 95, 97])))
        self.assertIsNotNone(sig, st.get('_emit'))
        self.assertEqual((sig['side'], sig['extra']['setup']), ('BUY', 'EW_WAVE3'))
        self.assertAlmostEqual(sig['initial_sl'], 89.54, delta=0.05)              # below wave 1 start (P0)
        self.assertAlmostEqual(sig['initial_tp'], 94.7 + 1.618 * 10.6, delta=0.05)
        self.assertAlmostEqual(sig['extra']['w2_depth'], 0.528, delta=0.01)
        self.assertEqual(sig['extra']['regime'], 'TRENDING')

    def test_sell_mirror(self):
        sig, _ = run(agent(), mirror(h1_bars(path([105, 90, 100, 95, 97]))))
        self.assertEqual(sig['side'], 'SELL')
        self.assertAlmostEqual(sig['initial_sl'], 200 - 89.54, delta=0.05)

    def test_rule1_wave2_below_wave1_start(self):
        sig, st = run(agent(), h1_bars(path([105, 90, 100, 88, 90])))
        self.assertIsNone(sig)
        self.assertEqual(st['_emit'][-1]['reason'], 'RULE1_BROKEN')

    def test_depth_limits(self):
        sig, st = run(agent(), h1_bars(path([105, 90, 100, 91, 93])))
        self.assertEqual((sig, st['_emit'][-1]['reason']), (None, 'W2_TOO_DEEP'))
        sig, st = run(agent(), h1_bars(path([105, 80, 100, 95, 97])))
        self.assertEqual((sig, st['_emit'][-1]['reason']), (None, 'W2_TOO_SHALLOW'))
        self.assertNotIn('WAVE3', ' '.join(st['done']))                    # shallow: may deepen and count later

    def test_wave_2_still_running_is_not_a_trade(self):
        sig, st = run(agent(), h1_bars(path([105, 90, 100, 95, 96])))     # turned up < 1 ATR
        self.assertIsNone(sig)
        self.assertNotIn('_emit', st)

    def test_reward_and_stop_limits(self):
        sig, st = run(agent(min_rr=10), h1_bars(path([105, 90, 100, 95, 97])))
        self.assertEqual((sig, st['_emit'][-1]['reason']), (None, 'RR_TOO_LOW'))
        sig, st = run(agent(min_sl_spreads=3), h1_bars(path([105, 90, 100, 95, 97])), spread=4.0)
        self.assertEqual((sig, st['_emit'][-1]['reason']), (None, 'RISK_OUT_OF_BOUNDS'))

    def test_one_decision_per_count_and_no_late_entry(self):
        bars = h1_bars(path([105, 90, 100, 95, 97]))
        sig, st = run(agent(), bars)
        self.assertIsNotNone(sig)
        st.pop('last_h1_time')
        self.assertIsNone(run(agent(), bars, st)[0])
        self.assertIsNone(run(agent(), bars, delay=900)[0])                 # 15 min after the close: too late

    def test_regime_filter(self):
        a = agent(regime_filter=True)
        a._regime = lambda *x, **k: 'MEAN_REVERTING'
        sig, st = run(a, h1_bars(path([105, 90, 100, 95, 97])))
        self.assertEqual((sig, st['_emit'][-1]['reason']), (None, 'REGIME_FILTERED'))


class Wave5(unittest.TestCase):
    def test_buy_after_wave_4(self):
        sig, st = run(agent(mode='WAVE5'), h1_bars(path([105, 90, 100, 95, 112, 106, 108])))
        self.assertIsNotNone(sig, st.get('_emit'))
        self.assertEqual((sig['side'], sig['extra']['setup']), ('BUY', 'EW_WAVE5'))
        self.assertAlmostEqual(sig['initial_sl'], 105.54, delta=0.05)              # below the wave 4 low
        self.assertAlmostEqual(sig['initial_tp'], 105.7 + 10.6, delta=0.05)        # wave 5 = wave 1
        self.assertAlmostEqual(sig['extra']['w4_depth'], 0.375, delta=0.01)

    def test_rule3_wave4_overlap(self):
        sig, st = run(agent(mode='WAVE5'), h1_bars(path([105, 90, 100, 95, 112, 99, 101])))
        self.assertEqual((sig, st['_emit'][-1]['reason']), (None, 'RULE3_W4_OVERLAP'))

    def test_rule2_wave3_shorter_than_wave1(self):
        sig, st = run(agent(mode='WAVE5'), h1_bars(path([105, 90, 100, 95, 104, 100, 102])))
        self.assertEqual((sig, st['_emit'][-1]['reason']), (None, 'RULE2_W3_SHORTER'))


class Registration(unittest.TestCase):
    def test_two_agents_per_pair_by_default(self):
        cfgs = elliott_agent_configs({}, 'XAUUSD')
        self.assertEqual(cfgs, [{'mode': 'WAVE3', 'regime_filter': False}, {'mode': 'WAVE5', 'regime_filter': False}])
        a = build_agent('EW_IMPULSE', cfgs[1])
        self.assertEqual(a.label(), 'Elliott Wave · wave 5')
        cfgs = elliott_agent_configs({'elliott': {'modes': ['WAVE3'], 'regime_filter_variants': [False, True]}}, 'EURUSD')
        self.assertEqual(len(cfgs), 2)
        with self.assertRaises(ValueError):
            ew.ElliottImpulse({'mode': 'WAVEC'})


if __name__ == '__main__':
    unittest.main()
