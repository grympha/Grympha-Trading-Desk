"""ReadOnlyMarketData shares one MT5 read per pair within an engine sample (same `now`)."""
import unittest
from types import SimpleNamespace as N

from market_data import ReadOnlyMarketData


class CountingMT5:
    TIMEFRAME_M5 = 'M5'

    def __init__(self):
        self.ticks = self.rates = 0
        self.bid = 100.0

    def symbol_select(self, s, on=True):
        return True

    def symbol_info(self, s):
        return N(point=0.01, digits=2, trade_tick_size=0.01)

    def symbol_info_tick(self, s):
        self.ticks += 1
        return N(bid=self.bid, ask=self.bid + 0.1, time=1_800_000_000)

    def copy_rates_from_pos(self, s, tf, start, count):
        self.rates += 1
        return [dict(time=1_800_000_000 - 300 * (i + 2), open=1, high=2, low=0.5, close=1.5) for i in range(count)]


class Memo(unittest.TestCase):
    def setUp(self):
        self.mt5 = CountingMT5()
        self.md = ReadOnlyMarketData(self.mt5, {})
        self.now = 1_800_000_010

    def test_one_read_per_pair_per_sample(self):
        for _ in range(15):                                  # 15 agents on one pair
            self.md.quote('EURUSD', now=self.now)
            self.md.candles('EURUSD', 'M5', 50, now=self.now)
        self.assertEqual((self.mt5.ticks, self.mt5.rates), (1, 1))

    def test_next_sample_reads_fresh_prices(self):
        self.assertEqual(self.md.quote('EURUSD', now=self.now).bid, 100.0)
        self.mt5.bid = 101.0
        self.assertEqual(self.md.quote('EURUSD', now=self.now + 5).bid, 101.0)

    def test_no_now_means_no_caching(self):
        self.md.quote('EURUSD'); self.md.quote('EURUSD')
        self.assertEqual(self.mt5.ticks, 2)

    def test_callers_get_their_own_candle_list(self):
        a = self.md.candles('EURUSD', 'M5', 10, now=self.now)
        a.clear()
        self.assertEqual(len(self.md.candles('EURUSD', 'M5', 10, now=self.now)), 10)

    def test_errors_are_shared_too(self):
        self.mt5.symbol_info_tick = lambda s: None
        for _ in range(3):
            with self.assertRaises(ValueError):
                self.md.quote('EURUSD', now=self.now)


if __name__ == '__main__':
    unittest.main()
