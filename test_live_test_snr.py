"""S&R Zones Live Test agent: zones, the three triggers, stops/targets and the safety rules."""
import unittest

import live_test_snr as snr
from live_test import build_agent, snr_agent_configs
from market_data import Bar, Quote

H4D, M15D = 14400, 900
T0 = 1_790_000_000 - 1_790_000_000 % H4D


def path(waypoints, step=1.0):
    """Closes walking between waypoints one `step` per bar."""
    out = [float(waypoints[0])]
    for w in waypoints[1:]:
        while abs(out[-1] - w) > 1e-9:
            out.append(out[-1] + (step if w > out[-1] else -step))
    return out


def h4_bars(closes, wick=0.3):
    bars, prev = [], closes[0]
    for i, c in enumerate(closes):
        bars.append(Bar(T0 + i * H4D, prev, max(prev, c) + wick, min(prev, c) - wick, c))
        prev = c
    return bars


# 100 = support (3 swing lows), 110 = middle zone (2 highs), 120 = resistance (2 highs)
BASE = [105, 100, 110, 100, 110, 100, 120, 113, 120, 113]


class FakeMarket:
    def __init__(self, h4, m15, h1=None):
        self.h4, self.m15, self.h1 = h4, m15, h1 or []

    def candles(self, symbol, tf, count, now=None):
        src = dict(H4=self.h4, M15=self.m15, H1=self.h1)[tf]
        return [b for b in src if b.time + {'H4': H4D, 'M15': M15D, 'H1': 3600}[tf] <= now][-count:]


def m15_after(t, rows):
    """M15 bars (o, h, l, c) starting at t."""
    return [Bar(t + i * M15D, *r) for i, r in enumerate(rows)]


def quote(bid, t, spread=0.02):
    return Quote('XAUUSD', bid, bid + spread, t, 0.0, 0.01, 2)


class Zones(unittest.TestCase):
    def test_clustered_swing_points_make_zones(self):
        zones, a = snr.build_zones(h4_bars(path(BASE + [106])))
        mids = [round(z['mid']) for z in zones]
        self.assertIn(100, mids); self.assertIn(110, mids); self.assertIn(120, mids)
        z100 = next(z for z in zones if round(z['mid']) == 100)
        self.assertEqual(z100['touches'], 3)                                  # 3 swing lows, flat bottoms count once
        self.assertGreaterEqual(z100['hi'] - z100['lo'], 0.25 * a - 1e-9)      # minimum width
        self.assertNotIn(113, mids)                                           # one real swing low at 113: no zone

    def test_single_swing_point_is_not_a_zone(self):
        zones, _ = snr.build_zones(h4_bars(path([105, 100, 110, 104, 112, 106])))
        self.assertFalse([z for z in zones if round(z['mid']) in (104, 112)])

    def test_flat_bottom_is_one_touch(self):
        h4 = h4_bars(path([105, 100, 106]))
        flat = h4[:5] + [h4[5]._replace(low=h4[4].low)] + h4[6:]                # two candles share the low
        zones, _ = snr.build_zones(flat)
        self.assertFalse(zones)

    def test_next_target_skips_the_traded_zone(self):
        zones, _ = snr.build_zones(h4_bars(path(BASE + [106])))
        z100 = next(z for z in zones if round(z['mid']) == 100)
        self.assertEqual(round(snr.next_target(zones, 'BUY', z100['hi'], exclude=z100)), 110)
        self.assertEqual(round(snr.next_target(zones, 'SELL', 119.0)), 110)        # top of the 110 zone


class Rejection(unittest.TestCase):
    Z = dict(lo=99.8, hi=100.2)

    def test_wick_rejection_off_support(self):
        prev, c = Bar(0, 101.5, 101.6, 100.9, 101.0), Bar(1, 100.9, 100.95, 99.9, 100.8)
        self.assertTrue(snr.rejection(c, prev, 'BUY', self.Z, 0.5))

    def test_breakout_from_below_is_not_a_bounce(self):
        prev, c = Bar(0, 99.0, 99.4, 98.8, 99.2), Bar(1, 99.2, 100.9, 99.1, 100.8)
        self.assertFalse(snr.rejection(c, prev, 'BUY', self.Z, 0.5))

    def test_close_inside_the_zone_or_no_wick_is_not_a_rejection(self):
        prev = Bar(0, 101.5, 101.6, 100.9, 101.0)
        self.assertFalse(snr.rejection(Bar(1, 100.9, 100.95, 99.9, 100.1), prev, 'BUY', self.Z, 0.5))
        self.assertFalse(snr.rejection(Bar(1, 100.2, 101.0, 99.9, 100.95), Bar(0, 100.3, 100.6, 100.2, 100.5),
                                       'BUY', self.Z, 0.9))                   # small wick, no engulfing

    def test_sell_mirror(self):
        z = dict(lo=119.8, hi=120.2)
        prev, c = Bar(0, 118.5, 119.1, 118.4, 119.0), Bar(1, 119.1, 120.1, 119.05, 119.2)
        self.assertTrue(snr.rejection(c, prev, 'SELL', z, 0.5))


class Agent(unittest.TestCase):
    def setup_bounce(self, cfg=None, spread=0.02):
        h4 = h4_bars(path(BASE + [104]))                  # last H4 heads back down towards 100
        t = h4[-1].time + H4D
        m15 = m15_after(t, [(103.0, 103.2, 102.4, 102.5)] * 6 + [(101.5, 101.6, 100.9, 101.0),
                                                                  (100.9, 100.95, 99.9, 100.8)])
        now = m15[-1].time + M15D + 5
        agent = snr.SnrZones(dict(cfg or {}))
        agent._regime = lambda *a, **k: 'MIXED'
        return agent, FakeMarket(h4, m15), now, m15, spread

    def go(self, agent, market, now, m15, spread, state):
        return agent.evaluate(now, quote(100.8, now, spread), [b for b in m15 if b.time + M15D <= now], 0.01,
                              state, market=market, symbol='XAUUSD')

    def test_bounce_buys_off_support_with_stop_below_and_target_at_next_zone(self):
        agent, market, now, m15, sp = self.setup_bounce()
        st = {}
        sig = self.go(agent, market, now, m15, sp, st)
        self.assertIsNotNone(sig, st.get('_emit'))
        self.assertEqual(sig['side'], 'BUY')
        self.assertLess(sig['initial_sl'], 99.9)                              # beyond zone and wick
        self.assertAlmostEqual(sig['initial_tp'], 110.1, delta=0.3)           # near edge of the 110 zone
        self.assertEqual(sig['extra']['setup'], 'SNR_BOUNCE')
        self.assertEqual(sig['extra']['regime'], 'MIXED')
        self.assertGreaterEqual(sig['extra']['planned_rr'], 1.5)
        self.assertEqual(sig['entry_candle_end'], now + 12 * M15D)

    def test_one_trade_per_zone_until_price_moves_away(self):
        agent, market, now, m15, sp = self.setup_bounce()
        st = {}
        self.assertIsNotNone(self.go(agent, market, now, m15, sp, st))
        st.pop('last_candle_time')                                            # the same trigger again
        self.assertIsNone(self.go(agent, market, now, m15, sp, st))
        self.assertEqual(st['_emit'][-1]['reason'], 'ZONE_USED')
        far = quote(130.0, now)                                               # price far away -> zone free again
        agent.evaluate(now, far, m15, 0.01, st, market=market, symbol='XAUUSD')
        self.assertEqual(st['used'], {})

    def test_stop_smaller_than_three_spreads_is_skipped(self):
        agent, market, now, m15, _ = self.setup_bounce()
        st = {}
        self.assertIsNone(self.go(agent, market, now, m15, 0.8, st))         # spread 0.8 -> min stop 2.4
        self.assertEqual(st['_emit'][-1]['reason'], 'RISK_OUT_OF_BOUNDS')

    def test_low_reward_is_skipped(self):
        agent, market, now, m15, sp = self.setup_bounce({'min_rr': 50})
        st = {}
        self.assertIsNone(self.go(agent, market, now, m15, sp, st))
        self.assertEqual(st['_emit'][-1]['reason'], 'RR_TOO_LOW')

    def test_regime_filter(self):
        agent, market, now, m15, sp = self.setup_bounce({'regime_filter': True})
        agent._regime = lambda *a, **k: 'TRENDING'                            # bounce not allowed when trending
        st = {}
        self.assertIsNone(self.go(agent, market, now, m15, sp, st))
        self.assertEqual(st['_emit'][-1]['reason'], 'REGIME_FILTERED')
        agent._regime = lambda *a, **k: 'MEAN_REVERTING'
        st = {}
        self.assertIsNotNone(self.go(agent, market, now, m15, sp, st))

    def test_retest_of_a_broken_zone(self):
        h4 = h4_bars(path(BASE + [108, 111]))             # last H4 closes 111: through the 110 zone
        t = h4[-1].time + H4D
        m15 = m15_after(t, [(111.0, 111.4, 110.8, 111.2)] * 3 + [(110.9, 111.3, 110.1, 111.1)])
        agent = snr.SnrZones({'mode': 'RETEST'})
        agent._regime = lambda *a, **k: 'TRENDING'
        market, st = FakeMarket(h4, m15), {}
        agent.evaluate(t + 5, quote(111.0, t + 5), [], 0.01, st, market=market, symbol='XAUUSD')   # too few M15: no-op
        now = m15[2].time + M15D + 5
        self.assertIsNone(agent.evaluate(now, quote(111.1, now), m15[:3], 0.01, st, market=market, symbol='XAUUSD'))
        self.assertEqual(st['retest']['side'], 'BUY')                         # armed on the H4 close
        now = m15[3].time + M15D + 5
        sig = agent.evaluate(now, quote(111.1, now), m15, 0.01, st, market=market, symbol='XAUUSD')
        self.assertIsNotNone(sig, st.get('_emit'))
        self.assertEqual((sig['side'], sig['extra']['setup']), ('BUY', 'SNR_RETEST'))
        self.assertLess(sig['initial_sl'], 110.1)                             # below the broken zone
        self.assertAlmostEqual(sig['initial_tp'], 120.1, delta=0.3)           # next zone up: 120
        self.assertIsNone(st['retest'])                                       # one entry per break

    def test_retest_cancelled_when_price_closes_back_through(self):
        h4 = h4_bars(path(BASE + [108, 111]))
        t = h4[-1].time + H4D
        m15 = m15_after(t, [(111.0, 111.4, 110.8, 111.2)] * 3 + [(110.0, 110.1, 108.9, 109.0)])
        agent = snr.SnrZones({'mode': 'RETEST'})
        market, st = FakeMarket(h4, m15), {}
        agent.evaluate(m15[2].time + M15D + 5, quote(111, m15[2].time + M15D), m15[:3], 0.01, st, market=market, symbol='XAUUSD')
        now = m15[3].time + M15D + 5
        self.assertIsNone(agent.evaluate(now, quote(109, now), m15, 0.01, st, market=market, symbol='XAUUSD'))
        self.assertEqual(st['_emit'][-1]['reason'], 'ZONE_REBROKEN')
        self.assertIsNone(st['retest'])

    def test_fakeout_mode(self):
        h4 = h4_bars(path(BASE + [104]))
        t = h4[-1].time + H4D
        m15 = m15_after(t, [(103.0, 103.2, 102.4, 102.5)] * 6 + [(101.5, 101.6, 100.9, 101.0),
                                                                  (100.6, 100.7, 99.0, 100.4)])  # pokes below, closes back
        agent = snr.SnrZones({'mode': 'FAKEOUT'})
        agent._regime = lambda *a, **k: 'MIXED'
        now = m15[-1].time + M15D + 5
        sig = agent.evaluate(now, quote(100.4, now), m15, 0.01, {}, market=FakeMarket(h4, m15), symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual((sig['side'], sig['extra']['setup']), ('BUY', 'SNR_FAKEOUT'))
        self.assertLess(sig['initial_sl'], 99.0)


class Causality(unittest.TestCase):
    def test_zones_come_from_closed_h4_candles_only(self):
        h4 = h4_bars(path(BASE + [104]))
        m = FakeMarket(h4, [])
        agent = snr.SnrZones()
        now = h4[-1].time + H4D - 60                                          # the last H4 is still forming
        zones, _, seen = agent._zones(m, 'XAUUSD', now)
        self.assertEqual(seen[-1].time, h4[-2].time)
        self.assertEqual(zones, snr.build_zones(h4[:-1][-120:])[0])

    def test_daily_bars_from_h4(self):
        d = snr.daily_from_h4([Bar(0, 1, 2, 0.5, 1.5), Bar(14400, 1.5, 3, 1, 2.5), Bar(86400, 2.5, 2.6, 2, 2.2)])
        self.assertEqual(d, [(0, 1, 3, 0.5, 2.5), (86400, 2.5, 2.6, 2, 2.2)])


class Registration(unittest.TestCase):
    def test_four_agents_per_pair_by_default(self):
        cfgs = snr_agent_configs({}, 'XAUUSD')
        self.assertEqual([(c['mode'], c['regime_filter']) for c in cfgs],
                         [('BOUNCE', False), ('BOUNCE', True), ('RETEST', False), ('RETEST', True)])
        agents = [build_agent('SNR_ZONES', c) for c in cfgs]
        self.assertEqual(len({a.config_id for a in agents}), 4)
        self.assertEqual(agents[1].label(), 'S&R Zones · Bounce · regime filter')

    def test_modes_and_overrides_from_config(self):
        cfgs = snr_agent_configs({'snr': {'modes': ['FAKEOUT', 'NOPE'], 'regime_filter_variants': [False],
                                          'symbol_overrides': {'XAUUSD': {'min_rr': 2.0}}}}, 'XAUUSD')
        self.assertEqual(cfgs, [{'mode': 'FAKEOUT', 'regime_filter': False, 'min_rr': 2.0}])
        with self.assertRaises(ValueError):
            snr.SnrZones({'mode': 'NOPE'})


if __name__ == '__main__':
    unittest.main()
