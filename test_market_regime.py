"""Market Scout regime labels: one label per market, from closed candles only."""
import unittest

import market_scout as ms


def bars(closes, start=0, step=3600, rng=None):
    """(t, o, h, l, c, spread) bars; each candle's range defaults to 0.4 around its close."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        r = 0.2 if rng is None else rng(i)
        out.append((start + i * step, prev, max(prev, c) + r, min(prev, c) - r, c, 10.0))
        prev = c
    return out


def daily(ranges):
    """D1 bars with the given ranges (the last one is the forming day)."""
    return [(i * 86400, 100.0, 100 + r / 2, 100 - r / 2, 100.0, 10.0) for i, r in enumerate(ranges)]


NORMAL_D1 = daily([2.0] * 70)
UP = [100 + 0.5 * i for i in range(300)]
FLAT = [100 + (0.3 if i % 2 else -0.3) for i in range(300)]


class Labels(unittest.TestCase):
    def test_clean_trend_is_trending(self):
        r = ms.regime(bars(UP), bars(UP, step=4 * 3600), NORMAL_D1)
        self.assertEqual(r['label'], 'TRENDING', r)
        self.assertIn('up', r['reason'])

    def test_choppy_flat_market_is_mean_reverting(self):
        r = ms.regime(bars(FLAT), bars(FLAT, step=4 * 3600), NORMAL_D1)
        self.assertEqual(r['label'], 'MEAN_REVERTING', r)

    def test_wide_daily_ranges_are_crisis(self):
        r = ms.regime(bars(UP), bars(UP, step=4 * 3600), daily([2.0] * 64 + [5.0] * 5 + [2.0]))
        self.assertEqual(r['label'], 'CRISIS', r)
        self.assertEqual(r['ratio'], 2.5)

    def test_one_huge_h1_candle_is_crisis(self):
        h1 = bars(FLAT, rng=lambda i: 6.0 if i == 297 else 0.2)       # a closed candle, 2 bars from the end
        r = ms.regime(h1, bars(FLAT, step=4 * 3600), NORMAL_D1)
        self.assertEqual(r['label'], 'CRISIS', r)
        self.assertGreaterEqual(r['shock_x'], ms.SHOCK_ATR)

    def test_active_without_trend_is_high_vol(self):
        r = ms.regime(bars(FLAT), bars(FLAT, step=4 * 3600), daily([2.0] * 64 + [3.2] * 5 + [2.0]))
        self.assertEqual(r['label'], 'HIGH_VOL', r)

    def test_unclear_market_is_mixed(self):
        wave = [100 + 0.4 * i if i < 250 else 200 - 0.4 * (i - 250) for i in range(300)]   # up, then a turn
        r = ms.regime(bars(wave), bars(UP, step=4 * 3600), NORMAL_D1)
        self.assertEqual(r['label'], 'MIXED', r)

    def test_short_history_is_mixed(self):
        r = ms.regime(bars(UP[:30]), bars(UP[:30]), NORMAL_D1)
        self.assertEqual((r['label'], r['reason']), ('MIXED', 'not enough history'))


class Causality(unittest.TestCase):
    def test_the_forming_candle_never_changes_the_label(self):
        h1, h4 = bars(FLAT), bars(FLAT, step=4 * 3600)
        before = ms.regime(h1, h4, NORMAL_D1)
        t = h1[-1][0]
        wild_h1 = h1[:-1] + [(t, 100.0, 140.0, 60.0, 140.0, 10.0)]      # a still-open candle doing anything
        wild_h4 = h4[:-1] + [(h4[-1][0], 100.0, 140.0, 60.0, 140.0, 10.0)]
        wild_d1 = NORMAL_D1[:-1] + [(NORMAL_D1[-1][0], 100.0, 140.0, 60.0, 100.0, 10.0)]
        self.assertEqual(ms.regime(wild_h1, wild_h4, wild_d1), before)


class Wiring(unittest.TestCase):
    def test_analyse_row_carries_the_regime(self):
        row = ms.analyse('XAUUSD', 'LIVE', bars(UP), bars(UP, step=4 * 3600), NORMAL_D1, 0.01, now=300 * 3600)
        self.assertEqual(row['regime']['label'], 'TRENDING')

    def test_crisis_on_a_watched_pair_is_a_recommendation(self):
        row = dict(symbol='XAUUSD', role='LIVE', news=[], spread_x=None, cost_pct=0.01, score=50,
                   volatility=dict(regime='EXTREME', ratio=2.5), trend=dict(label='MIXED', h1=0, h4=0, efficiency=0.2),
                   regime=dict(label='CRISIS', reason='daily range 2.5x normal'))
        recs = ms.recommendations([row, dict(row, symbol='EURJPY', role='CANDIDATE')])
        crisis = [r for r in recs if r['kind'] == 'REGIME']
        self.assertEqual([r['symbol'] for r in crisis], ['XAUUSD'])
        self.assertIn('daily range 2.5x normal', crisis[0]['text'])


if __name__ == '__main__':
    unittest.main()
