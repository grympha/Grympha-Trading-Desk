import gzip
import json
import tempfile
import time
import unittest
from pathlib import Path

import decision_log as dl

RESULT = dict(symbol='XAUUSD', health='OK', reason='waiting', condition={'name': 'BULLISH'},
              chart=[{'time': t, 'close': 1.0} for t in range(80)], charts={'M5': [1] * 80},
              review_market={'H1': [1] * 8})


class DecisionLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def wait_for_gzip(self, pattern, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            if list((self.dir / 'logs').glob(pattern)) and not list((self.dir / 'logs').glob('*.jsonl')):
                return
            time.sleep(.05)
        self.fail('background compression did not finish')

    def test_compact_line_keeps_decision_and_drops_candles(self):
        log = self.dir / 'decisions.jsonl'
        dl.append(log, RESULT)
        line = json.loads(log.read_text())
        for k in ('chart', 'charts', 'review_market'):
            self.assertNotIn(k, line)
        self.assertEqual(line['reason'], 'waiting'); self.assertEqual(line['chart_last_bar'], 79)
        self.assertIn('chart', RESULT)  # the live result passed to the dashboard is not modified

    def test_rotates_and_compresses_without_losing_lines(self):
        log = self.dir / 'decisions.jsonl'
        for i in range(30):
            dl.append(log, dict(RESULT, reason=f'r{i}'), max_bytes=1000)
        self.wait_for_gzip('decisions-*.jsonl.gz')
        lines = []
        for gz in sorted((self.dir / 'logs').glob('*.gz')):
            with gzip.open(gz, 'rt') as f:
                lines += [json.loads(x)['reason'] for x in f]
        lines += [json.loads(x)['reason'] for x in log.read_text().splitlines()]
        self.assertEqual(sorted(lines, key=lambda r: int(r[1:])), [f'r{i}' for i in range(30)])

    def test_compress_is_verified_lossless(self):
        p = self.dir / 'x.jsonl'; data = ''.join(json.dumps({'i': i}) + '\n' for i in range(5000))
        p.write_text(data)
        gz = dl.compress(p)
        self.assertFalse(p.exists())
        with gzip.open(gz, 'rt') as f:
            self.assertEqual(f.read(), data)

    def test_nan_rejected_when_strict(self):
        with self.assertRaises(ValueError):
            dl.append(self.dir / 'c.jsonl', dict(RESULT, score=float('nan')), allow_nan=False)


if __name__ == '__main__':
    unittest.main()
