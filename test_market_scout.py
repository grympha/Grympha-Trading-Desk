"""Market Scout: measurements, score, recommendations, incremental sweep, read-only MT5 use,
and the Signal Agent's spread check."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import market_scout as ms
import signal_agent as sa
from dashboard_store import Store

NOW = 1_795_003_200.0        # an hour boundary
H, D = 3600, 86400


def bars(n, step, start_price=100.0, drift=0.0, wiggle=0.2, rng=1.0, spread=10, end=NOW):
    out, p = [], start_price
    for i in range(n):
        t = end - (n - i) * step
        o = p
        c = p + drift + (wiggle if i % 2 else -wiggle)
        out.append((t, o, max(o, c) + rng / 2, min(o, c) - rng / 2, c, spread))
        p = c
    return out


class Measurements(unittest.TestCase):
    def test_trend_up_down_and_ranging(self):
        up = ms.trend(bars(300, H, drift=0.5), bars(150, 4 * H, drift=2.0))
        self.assertEqual((up['label'], up['h1'], up['h4']), ('STRONG UP', 1, 1))
        down = ms.trend(bars(300, H, drift=-0.5), bars(150, 4 * H, drift=-2.0))
        self.assertEqual(down['label'], 'STRONG DOWN')
        flat = ms.trend(bars(300, H, wiggle=0.5), bars(150, 4 * H, wiggle=0.5))
        self.assertEqual(flat['label'], 'RANGING')

    def test_volatility_regimes(self):
        calm = bars(65, D, rng=1.0)
        hot = calm + bars(6, D, rng=3.0, end=NOW + 6 * D)
        self.assertEqual(ms.volatility(calm)['regime'], 'NORMAL')
        v = ms.volatility(hot)
        self.assertEqual(v['regime'], 'EXTREME')
        self.assertGreater(v['ratio'], 2)
        self.assertEqual(ms.volatility(bars(10, D))['regime'], 'UNKNOWN')

    def test_cost_and_spread_now_use_the_same_hour(self):
        h1 = bars(300, H, rng=1.0, spread=10)
        # this hour of day is usually wider (30 points): a 40-point spread now is only 1.3x usual
        h1 = [b[:5] + (30,) if int(b[0] // H) % 24 == int(NOW // H) % 24 else b for b in h1]
        row = ms.analyse('X', 'LIVE', h1, bars(150, 4 * H), bars(70, D), point=0.001, spread_now_points=40, now=NOW)
        self.assertAlmostEqual(row['spread_x'], 40 / 30, places=2)
        self.assertAlmostEqual(row['cost_pct'], 10 * 0.001 / row['atr_h1'], places=3)
        self.assertAlmostEqual(ms.usual_spread(row, NOW), 30 * 0.001)
        self.assertAlmostEqual(ms.usual_spread(row, NOW + H), 10 * 0.001)

    def test_news_is_matched_by_currency_and_window(self):
        ev = [dict(currency='USD', title='CPI', epoch=NOW + 1800), dict(currency='JPY', title='BoJ', epoch=NOW + 600),
              dict(currency='USD', title='Old', epoch=NOW - 600), dict(currency='USD', title='Far', epoch=NOW + 2 * D)]
        row = ms.analyse('XAUUSD', 'LIVE', bars(300, H), bars(150, 4 * H), bars(70, D), 0.01, None,
                         {'XAU', 'USD'}, ev, NOW)
        self.assertEqual([(n['title'], n['minutes']) for n in row['news']], [('CPI', 30)])
        self.assertEqual(row['currencies'], ['USD'])

    def test_score_prefers_cheap_trending_markets(self):
        cheap_trend = ms.score(0.02, dict(h1=1, h4=1, efficiency=0.5), dict(ratio=1.2))
        costly_chop = ms.score(0.30, dict(h1=0, h4=0, efficiency=0.05), dict(ratio=0.4))
        self.assertEqual(cheap_trend, 100)
        self.assertLess(costly_chop, 20)


class Recommendations(unittest.TestCase):
    def row(self, sym, role, score=50, cost=0.1, sx=1.0, regime='NORMAL', ratio=1.0, label='MIXED', h1=0, news=()):
        return dict(symbol=sym, role=role, score=score, cost_pct=cost, spread_x=sx,
                    volatility=dict(regime=regime, ratio=ratio), trend=dict(label=label, h1=h1, efficiency=0.4),
                    news=list(news))

    def test_kinds_and_order(self):
        rows = [self.row('EURUSD', 'LIVE', news=[dict(currency='USD', title='CPI', epoch=NOW + 1800, minutes=30)]),
                self.row('XAUUSD', 'LIVE', sx=3.0, news=[dict(currency='USD', title='CPI', epoch=NOW + 1800, minutes=30)]),
                self.row('USDCHF', 'LIVE', cost=0.22),
                self.row('.US500Cash', 'LIVE_TEST', regime='EXTREME', ratio=3.1),
                self.row('GBPCAD', 'CANDIDATE', score=70, cost=0.05, label='STRONG DOWN', h1=-1),
                self.row('AUDNZD', 'CANDIDATE', score=80, cost=0.15)]          # too expensive to suggest
        recs = ms.recommendations(rows, NOW)
        self.assertEqual([r['kind'] for r in recs], ['NEWS', 'COST', 'COST', 'VOLATILITY', 'TREND', 'ADD'])
        self.assertIn('Affects EURUSD, XAUUSD', recs[0]['text'])
        self.assertIn('3.0×', recs[1]['text'])
        self.assertIn('GBPCAD: consider adding', recs[-1]['text'])
        self.assertNotIn('AUDNZD', json.dumps(recs))


class FakeMT5:
    """Records every call; exposes only the read functions Market Scout may use."""
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 'H1', 'H4', 'D1'

    def __init__(self, names):
        self.names, self.calls = names, []
        dt = np.dtype([('time', 'i8'), ('open', 'f8'), ('high', 'f8'), ('low', 'f8'), ('close', 'f8'), ('spread', 'i8')])
        self.rates = {tf: np.array([b for b in bars(n, s, drift=0.3)], dtype=dt)
                      for tf, n, s in (('H1', 300, H), ('H4', 150, 4 * H), ('D1', 70, D))}

    def __getattr__(self, name):
        raise AssertionError(f'Market Scout must not call mt5.{name}')

    def symbols_get(self):
        self.calls.append('symbols_get')
        return [SimpleNamespace(name=n, visible=n != 'HIDDEN') for n in self.names]

    def symbol_info(self, s):
        self.calls.append('symbol_info')
        return SimpleNamespace(point=0.01, visible=s != 'HIDDEN', currency_base='XAU', currency_profit='USD')

    def symbol_info_tick(self, s):
        self.calls.append('symbol_info_tick')
        return SimpleNamespace(bid=100.0, ask=100.2, time=NOW - 5)

    def copy_rates_from_pos(self, s, tf, start, count):
        self.calls.append('copy_rates_from_pos')
        return self.rates[tf][-count:]


class Sweep(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'db.sqlite3')
        self.t = NOW
        self.cfg = dict(symbols=['XAUUSD'], live_test_extra_symbols=['GBPAUD'],
                        market_scout=dict(symbols_per_cycle=2, interval_minutes=60))
        self.mt5 = FakeMT5(['XAUUSD', 'EURGBP', 'NEM', 'HIDDEN'])

    def scout(self):
        return ms.MarketScout(self.store, self.cfg, self.mt5, clock=lambda: self.t)

    def test_sweep_is_spread_over_cycles_then_waits_for_the_interval(self):
        s = self.scout()
        self.assertIsNone(s.step())                 # XAUUSD, GBPAUD
        r = s.step()                                # EURGBP, NEM: done (HIDDEN is never asked for)
        self.assertEqual({x['symbol']: x['role'] for x in r['rows']},
                         {'XAUUSD': 'LIVE', 'GBPAUD': 'LIVE_TEST', 'EURGBP': 'CANDIDATE', 'NEM': 'CANDIDATE'})
        self.assertEqual(ms.load(self.store)['generated_epoch'], NOW)
        self.t += 30 * 60
        self.assertIsNone(s.step())                 # not due yet: no MT5 call
        before = len(self.mt5.calls)
        s.step()
        self.assertEqual(len(self.mt5.calls), before)
        self.t += 31 * 60
        self.assertIsNone(s.step())                 # a new sweep has started
        self.assertGreater(len(self.mt5.calls), before)

    def test_restart_keeps_the_hourly_rhythm_and_disabled_does_nothing(self):
        self.scout().step(force=True)
        self.t += 10 * 60
        self.assertIsNone(self.scout().step())
        self.cfg['market_scout']['enabled'] = False
        calls = len(self.mt5.calls)
        self.assertIsNone(self.scout().step(force=True))
        self.assertEqual(len(self.mt5.calls), calls)

    def test_report_view_before_any_sweep(self):
        self.assertEqual(ms.report_view(self.store)['status'], 'WAITING')


class SignalAgentSpreadCheck(unittest.TestCase):
    def test_wide_spread_at_entry_downgrades_a_to_b(self):
        row = dict(point=0.01, typical_spread_points=20, typical_spread_by_hour={}, volatility=dict(regime='ACTIVE'),
                   trend=dict(label='MIXED'), cost_pct=0.02)
        wide = sa.market_context(row, dict(entry_time=NOW, entry_spread=0.6))      # 3x usual
        calm = sa.market_context(row, dict(entry_time=NOW, entry_spread=0.25))
        self.assertTrue(wide['wide_spread'])
        self.assertEqual(wide['spread_x'], 3.0)
        self.assertFalse(calm['wide_spread'])
        self.assertIsNone(sa.market_context(None, dict(entry_time=NOW, entry_spread=0.6)))


if __name__ == '__main__':
    unittest.main()
