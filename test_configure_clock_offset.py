"""configure_clock_offset: measures the broker clock only from MOVING quotes and saves it with a backup."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as N

import configure_clock_offset as cco


class FakeClock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


class FakeMT5:
    """offsets: {symbol: broker hours ahead of UTC}; frozen: symbols whose quote never moves."""
    def __init__(self, clock, offsets, frozen=()):
        self.clock, self.offsets, self.frozen = clock, offsets, set(frozen)
        self.start = clock.now

    def symbol_select(self, s, _on):
        return s in self.offsets

    def symbol_info_tick(self, s):
        t = self.start if s in self.frozen else self.clock.now
        broker = t + self.offsets[s] * 3600
        return N(time=int(broker), time_msc=int(broker * 1000), bid=1, ask=1.001)


class Measure(unittest.TestCase):
    def run_measure(self, offsets, frozen=()):
        c = FakeClock()
        return cco.measure(FakeMT5(c, offsets, frozen), list(offsets), clock=c.time, sleep=c.sleep)

    def test_utc_plus_two_broker(self):
        offset, live = self.run_measure({'EURUSD': 2, 'XAUUSD': 2, 'BTCUSD': 2})
        self.assertEqual(offset, 2)
        self.assertEqual(len(live), 3)

    def test_frozen_quotes_are_ignored(self):
        # a stale XAUUSD quote from 5 h ago must not vote
        offset, live = self.run_measure({'EURUSD': 3, 'GBPUSD': 3, 'XAUUSD': -2}, frozen={'XAUUSD'})
        self.assertEqual(offset, 3)
        self.assertNotIn('XAUUSD', live)

    def test_market_closed_refuses(self):
        with self.assertRaises(cco.OffsetError):
            self.run_measure({'EURUSD': 2, 'GBPUSD': 2}, frozen={'EURUSD', 'GBPUSD'})

    def test_single_live_symbol_is_not_enough(self):
        with self.assertRaises(cco.OffsetError):
            self.run_measure({'EURUSD': 2, 'GBPUSD': 2}, frozen={'GBPUSD'})

    def test_disagreeing_symbols_refuse(self):
        with self.assertRaises(cco.OffsetError):
            self.run_measure({'EURUSD': 2, 'GBPUSD': 2, 'XAUUSD': 3})

    def test_fractional_offset_refuses(self):
        # Windows clock 20 minutes wrong: never round that into an offset
        with self.assertRaises(cco.OffsetError):
            self.run_measure({'EURUSD': 2.33, 'GBPUSD': 2.33})


class Save(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = self.dir / 'config.json'
        self.cfg.write_text(json.dumps({'symbols': ['EURUSD'], 'terminal_path': 'x'}), encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_writes_both_keys_keeps_the_rest_and_backs_up(self):
        backup = cco.save(self.cfg, 2)
        data = json.loads(self.cfg.read_text(encoding='utf-8'))
        self.assertEqual((data['tick_timestamp_offset_hours'], data['bar_timestamp_offset_hours']), (2, 2))
        self.assertEqual(data['symbols'], ['EURUSD'])
        self.assertNotIn('tick_timestamp_offset_hours', json.loads((self.dir / backup).read_text(encoding='utf-8')))

    def test_unchanged_offset_writes_nothing(self):
        cco.save(self.cfg, 2)
        self.assertIsNone(cco.save(self.cfg, 2))
        self.assertEqual(len(list(self.dir.glob('config.before_clock_offset.*'))), 1)


if __name__ == '__main__':
    unittest.main()
