"""Deterministic rule tests for the Advocate EMA-pullback (M15/M5) Live Test agent.
Proves the ported debate gate and entry, not profitability."""
from __future__ import annotations
import unittest

from market_data import Bar, Quote
from live_test import ema_last
from live_test_advocate import AdvocateEmaPullback

POINT, DIGITS = 0.01, 2
BASE = 1_700_000_000


def q(bid, ask, t):
    return Quote('XAUUSD', bid, ask, t, 0.0, POINT, DIGITS)


class FakeMarket:
    def __init__(self, tf_bars):
        self.tf_bars = tf_bars

    def candles(self, symbol, tf, count, now=None):
        return list(self.tf_bars.get(tf, []))[-count:]


def ramp(n, start, step, dur, last_high_wick=0.0):
    out = []
    for i in range(n):
        close = start + i * step
        openp = close - step
        hi = max(openp, close) + abs(step) * 0.5
        lo = min(openp, close) - abs(step) * 0.5
        out.append(Bar(BASE + i * dur, openp, hi, lo, close))
    if last_high_wick:                      # lift the final bar's high to seed a 20-bar extreme
        b = out[-1]
        out[-1] = Bar(b.time, b.open, b.high + last_high_wick, b.low, b.close)
    return out


def m5_pullback(series, d, step, close_offset):
    """Last M5 bar: pullback to prior EMA20, closing `close_offset` past the current EMA20."""
    prev_ema20 = ema_last([b.close for b in series[:-1]], 20)
    b = series[-1]
    if d == 1:
        openp = prev_ema20 - 0.02
        close = prev_ema20 + close_offset
        lo = prev_ema20 - 0.05
        hi = close + 0.02
    else:
        openp = prev_ema20 + 0.02
        close = prev_ema20 - close_offset
        hi = prev_ema20 + 0.05
        lo = close - 0.02
    return list(series[:-1]) + [Bar(b.time, openp, hi, lo, close)]


class AdvocateRules(unittest.TestCase):
    def _mk(self, d=1, close_offset=0.03, m15_wick=6.0):
        step = 0.10 * d
        m15 = ramp(80, 2000.0, step, 900, last_high_wick=(m15_wick if d == 1 else 0.0))
        if d == -1:                         # for SELL, seed a low extreme below price
            b = m15[-1]
            m15[-1] = Bar(b.time, b.open, b.high, b.low - m15_wick, b.close)
        m5 = m5_pullback(ramp(80, 2000.0, step, 300), d, abs(step), close_offset)
        return FakeMarket({'M5': m5, 'M15': m15}), m5[-1]

    def test_buy_debate_passes_and_enters_no_tp(self):
        ag = AdvocateEmaPullback()
        fm, b = self._mk(d=1, close_offset=0.03)
        state = {}
        sig = ag.evaluate(BASE + 80 * 300, q(b.close - 0.01, b.close, BASE + 80 * 300),
                          [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'BUY')
        self.assertIsNone(sig['initial_tp'])
        self.assertLess(sig['initial_sl'], b.close)
        self.assertEqual(sig['extra']['trail_activation_r'], 2.0)

    def test_sell_debate_passes_and_enters(self):
        ag = AdvocateEmaPullback()
        fm, b = self._mk(d=-1, close_offset=0.03)
        state = {}
        sig = ag.evaluate(BASE + 80 * 300, q(b.close, b.close + 0.01, BASE + 80 * 300),
                          [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'SELL')
        self.assertGreater(sig['initial_sl'], b.close)

    def test_extension_too_far_vetoes(self):
        ag = AdvocateEmaPullback()
        # a far-displaced close -> extension > debate_max_extension_atr -> UNREBUTTED
        fm, b = self._mk(d=1, close_offset=5.0)
        state = {}
        sig = ag.evaluate(BASE + 80 * 300, q(b.close - 0.01, b.close, BASE + 80 * 300),
                          [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNone(sig)
        self.assertTrue(any(e['event'] == 'VETO' and e['reason'] == 'UNREBUTTED_OBJECTION'
                            for e in state.get('_emit', [])))

    def test_zero_spread_gives_fewer_than_three_objections(self):
        ag = AdvocateEmaPullback()
        fm, b = self._mk(d=1, close_offset=0.03)
        state = {}
        # bid == ask -> cost objection does not exist -> fewer than three evidenced objections
        sig = ag.evaluate(BASE + 80 * 300, q(b.close, b.close, BASE + 80 * 300),
                          [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNone(sig)
        self.assertTrue(any(e['event'] == 'VETO' and e['reason'] == 'FEWER_THAN_THREE_OBJECTIONS'
                            for e in state.get('_emit', [])))


if __name__ == '__main__':
    unittest.main()
