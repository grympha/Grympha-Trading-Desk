"""Quant Agent: no-lookahead replay, identical results to the Live Test engine, statistics."""
import math
import tempfile
import unittest
from pathlib import Path

import quant_agent as qa
from dashboard_store import Store
from live_test import LiveTestEngine, VALID, config_id

T0 = 1_795_000_020 - (1_795_000_020 % 3600)        # an hour boundary on a weekday


def m1_bars(n, start=T0, base=100.0, step=0.01, spread=2):
    out, p = [], base
    for i in range(n):
        o = p
        c = p + (step if (i // 30) % 2 == 0 else -step)      # 30 min up, 30 min down
        out.append((start + i * 60, o, max(o, c) + 0.005, min(o, c) - 0.005, c, spread))
        p = c
    return out


def aggregate(m1, seconds):
    out, bucket = [], None
    for t, o, h, l, c, s in m1:
        k = t - t % seconds
        if bucket and bucket[0] == k:
            bucket = (k, bucket[1], max(bucket[2], h), min(bucket[3], l), c, s)
            out[-1] = bucket
        else:
            bucket = (k, o, h, l, c, s)
            out.append(bucket)
    return out


def market(n=600):
    m1 = m1_bars(n)
    bars = {'M1': m1, 'M5': aggregate(m1, 300), 'M15': aggregate(m1, 900), 'H1': aggregate(m1, 3600)}
    return qa.HistoricalMarket('TESTPAIR', bars, point=0.001, digits=3)


class EveryTenMinutes:
    """Minimal deterministic agent: BUY on each new closed M5 candle whose minute is a multiple of
    10, SL 0.05 below, TP 0.05 above. Uses the same contract as the real agents."""
    strategy_id, version, needs_market, timeframe = 'TEST_AGENT', 'test@1', False, 'M5'

    def __init__(self):
        self.config = dict(max_gap_seconds=120, max_positions=1)
        self.config_id = config_id(self.config)

    def label(self):
        return 'Test agent'

    def evaluate(self, now, quote, candles, point, state):
        if not candles or state.get('last') == candles[-1].time:
            return None
        state['last'] = candles[-1].time
        if (candles[-1].time // 60) % 10:
            return None
        return dict(side='BUY', signal_reason='test', signal_time=now, trigger_price=quote.ask,
                    initial_sl=round(quote.ask - 0.05, 3), initial_tp=round(quote.ask + 0.05, 3),
                    session='t', timeframe='M5', entry_candle_end=None, config_snapshot={})


class NoLookahead(unittest.TestCase):
    def test_only_closed_candles_are_served(self):
        m = market()
        now = T0 + 7 * 60 + 30                                  # inside the 7th minute
        m5 = m.candles('TESTPAIR', 'M5', 10, now=now)
        self.assertEqual(m5[-1].time, T0)                      # 00:05 candle is still open
        m1 = m.candles('TESTPAIR', 'M1', 3, now=now)
        self.assertEqual(m1[-1].time, T0 + 6 * 60)             # the 07 candle has not closed

    def test_candle_lists_are_copies(self):
        m = market()
        a = m.candles('TESTPAIR', 'M1', 5, now=T0 + 600)
        a.clear()
        self.assertEqual(len(m.candles('TESTPAIR', 'M1', 5, now=T0 + 600)), 5)


class Samples(unittest.TestCase):
    def test_up_candle_visits_low_before_high_and_adds_spread(self):
        s = qa.m1_samples((T0, 1.0, 1.5, 0.5, 1.2, 3), point=0.01, extra=0.0)
        self.assertEqual([round(b, 3) for _, b, _ in s], [1.0, 0.5, 1.5, 1.2])
        self.assertAlmostEqual(s[0][2] - s[0][1], 0.03)
        self.assertTrue(all(T0 < t < T0 + 60 for t, _, _ in s))

    def test_down_candle_visits_high_first(self):
        s = qa.m1_samples((T0, 1.2, 1.5, 0.5, 1.0, 0), point=0.01, extra=0.02)
        self.assertEqual([round(b, 3) for _, b, _ in s], [1.2, 1.5, 0.5, 1.0])
        self.assertAlmostEqual(s[0][2] - s[0][1], 0.02)


class SameAsLiveTestEngine(unittest.TestCase):
    def test_replay_gives_identical_trades_to_the_live_engine(self):
        start, end = T0 + 600, T0 + 330 * 60
        replay_trades, _ = qa.ReplayEngine().replay(EveryTenMinutes(), market(), start, end)

        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        eng = LiveTestEngine(Store(Path(tmp.name) / 'db.sqlite3'))
        eng.register(EveryTenMinutes(), 'TESTPAIR', 'RUNNING')
        m = market()
        import bisect
        i0, i1 = bisect.bisect_left(m.times['M1'], start), bisect.bisect_left(m.times['M1'], end)
        for k in range(i0, i1):
            for t, bid, ask in qa.m1_samples(m.bars['M1'][k], m.point, m.extra):
                m.set_quote(bid, ask, t)
                eng.run(m, now=t)
        live_trades = sorted(eng.trades(), key=lambda t: t['entry_time'])

        key = lambda t: (t['entry_time'], t['entry_price'], t['exit_time'], t['exit_price'], t['exit_reason'],
                         round(t['pnl_r'], 9), t['data_quality_status'])
        self.assertGreater(len(replay_trades), 10)
        self.assertEqual([key(t) for t in replay_trades], [key(t) for t in live_trades])


class Statistics(unittest.TestCase):
    def trades(self, rs):
        return [dict(entry_time=T0 + i * 3600, pnl_r=r, data_quality_status=VALID) for i, r in enumerate(rs)]

    def test_summary(self):
        s = qa.summarise(self.trades([1, -1, 2, -1, -1, 3]) + [dict(entry_time=T0, pnl_r=5, data_quality_status='DATA_GAP')])
        self.assertEqual((s['trades'], s['net_r'], s['excluded']), (6, 3, 1))
        self.assertAlmostEqual(s['win_rate'], 0.5)
        self.assertAlmostEqual(s['profit_factor'], 6 / 3)
        self.assertEqual(s['max_dd_r'], -2)                     # +2 peak after trade 3, then -1 -1
        self.assertEqual(s['net_r_without_best'], 0)
        self.assertEqual((s['in_sample']['trades'], s['out_of_sample']['trades']), (4, 2))

    def test_verdicts(self):
        self.assertEqual(qa.verdict(qa.summarise(self.trades([1] * 5))), 'TOO FEW TRADES')
        self.assertTrue(qa.verdict(qa.summarise(self.trades([1, -0.5] * 20))).startswith('EDGE'))
        self.assertTrue(qa.verdict(qa.summarise(self.trades([-1, 0.3] * 20))).startswith('LOSING'))
        # one giant winner carrying an otherwise losing record is not an edge
        self.assertEqual(qa.verdict(qa.summarise(self.trades([-1] * 39 + [80]))), 'NO CLEAR EDGE')

    def test_no_losses_gives_infinite_profit_factor(self):
        self.assertTrue(math.isinf(qa.summarise(self.trades([1, 2]))['profit_factor']))


class Reports(unittest.TestCase):
    def test_reports_are_written(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        trades, _ = qa.ReplayEngine().replay(EveryTenMinutes(), market(), T0 + 600, T0 + 500 * 60)
        s = qa.summarise(trades)
        row = dict(strategy_id='TEST_AGENT', label='Test <agent>', symbol='TESTPAIR', config_id='x', setup='',
                   start=T0, end=T0 + 1, open_at_end=0, stats=s, verdict=qa.verdict(s), seconds=0.1, trades=trades)
        csv_path, html_path, trades_path = qa.write_reports([row], Path(tmp.name), T0, T0 + 86400, 0)
        self.assertIn('TEST_AGENT', csv_path.read_text(encoding='utf-8'))
        self.assertIn('Test &lt;agent&gt;', html_path.read_text(encoding='utf-8'))    # escaped
        self.assertGreater(len(trades_path.read_text(encoding='utf-8').splitlines()), 10)


if __name__ == '__main__':
    unittest.main()
