"""The vectorised bbma_bot.indicators must equal the original pandas implementation exactly
(to floating-point noise), so live BBMA signals cannot change."""
import unittest

import numpy as np
import pandas as pd

from bbma_bot import indicators


def reference(raw):
    """The original implementation (rolling.apply WMA + iloc EMA loop), kept as the oracle."""
    d = raw.copy().sort_values('time').reset_index(drop=True)
    d['mid'] = d.close.rolling(20).mean()
    sd = d.close.rolling(20).std(ddof=0)
    d['upper'], d['lower'] = d.mid + 2 * sd, d.mid - 2 * sd
    for n in (5, 10):
        weights = np.arange(1, n + 1)
        for col in ('high', 'low'):
            d[f'ma{n}_{col}'] = d[col].rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)
    ema = np.full(len(d), np.nan)
    ema[49] = d.close.iloc[:50].mean()
    for i in range(50, len(d)):
        ema[i] = ema[i - 1] + (2 / 51) * (d.close.iloc[i] - ema[i - 1])
    d['ema50'] = ema
    tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(), (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    d['atr'] = tr.rolling(14).mean()
    return d


def market(n, seed, scale=1.0, base=2000.0):
    rng = np.random.default_rng(seed)
    close = base + np.cumsum(rng.normal(0, scale, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0, scale, n)
    low = np.minimum(open_, close) - rng.uniform(0, scale, n)
    return pd.DataFrame(dict(time=np.arange(n) * 300, open=open_, high=high, low=low, close=close))


class FastIndicatorsMatchTheOriginal(unittest.TestCase):
    COLS = ['mid', 'upper', 'lower', 'ma5_high', 'ma5_low', 'ma10_high', 'ma10_low', 'ema50', 'atr']

    def check(self, raw):
        fast, ref = indicators(raw), reference(raw)
        for col in self.COLS:
            np.testing.assert_allclose(fast[col].to_numpy(), ref[col].to_numpy(), rtol=1e-12, atol=1e-9,
                                       equal_nan=True, err_msg=col)

    def test_gold_like_prices(self):
        self.check(market(400, 1, scale=2.0, base=2000))

    def test_forex_like_prices(self):
        self.check(market(400, 2, scale=0.0004, base=1.1))

    def test_index_like_prices(self):
        self.check(market(500, 3, scale=25, base=25000))

    def test_unsorted_input_is_sorted_first(self):
        raw = market(300, 4)
        self.check(raw.sample(frac=1, random_state=4))


if __name__ == '__main__':
    unittest.main()
