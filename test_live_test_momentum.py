"""Deterministic rule tests for the Momentum M5/M1 Live Test agent and the Simulator's
break-even / trailing / no-take-profit exit management. Proves the ported rules, not profit."""
from __future__ import annotations
import unittest
from types import SimpleNamespace as N

from market_data import Bar, Quote
from live_test import Simulator, ema_last, atr
from live_test_momentum import MomentumM5M1

POINT, DIGITS = 0.01, 2
BASE = 1_700_000_000


def q(bid, ask, t):
    return Quote('XAUUSD', bid, ask, t, 0.0, POINT, DIGITS)


class FakeMarket:
    """Returns scripted closed bars per timeframe; no closed-bar filtering (rules under test)."""
    def __init__(self, tf_bars):
        self.tf_bars = tf_bars

    def candles(self, symbol, tf, count, now=None):
        return list(self.tf_bars.get(tf, []))[-count:]


def ramp(n, start, step, dur):
    """n bars whose closes march by `step` (up if >0); each bar brackets its move."""
    out = []
    for i in range(n):
        close = start + i * step
        openp = close - step
        hi = max(openp, close) + abs(step) * 0.5
        lo = min(openp, close) - abs(step) * 0.5
        out.append(Bar(BASE + i * dur, openp, hi, lo, close))
    return out


def with_pullback(series, d, step, dur):
    """Replace the last bar with a pullback-to-EMA20 confirmation candle in direction d."""
    prev_ema20 = ema_last([b.close for b in series[:-1]], 20)
    i = len(series) - 1
    t = BASE + i * dur
    if d == 1:
        openp = prev_ema20 + 0.01
        close = prev_ema20 + 3 * step
        lo = prev_ema20 - 0.02
        hi = close + 0.02
    else:
        openp = prev_ema20 - 0.01
        close = prev_ema20 - 3 * step
        hi = prev_ema20 + 0.02
        lo = close - 0.02
    series = list(series[:-1]) + [Bar(t, openp, hi, lo, close)]
    return series


class MomentumRules(unittest.TestCase):
    def _agent(self):
        return MomentumM5M1()

    def _market(self, d):
        step = 0.10 * d
        m5 = ramp(80, 2000.0, step, 300)
        m1 = with_pullback(ramp(80, 2000.0, step, 60), d, abs(step), 60)
        return FakeMarket({'M5': m5, 'M1': m1}), m1[-1]

    def test_buy_arms_then_fills_no_tp_with_trail_fields(self):
        ag = self._agent()
        fm, sig_bar = self._market(1)
        state = {}
        # scan: a quote below the trigger arms a pending (not already broken out)
        r1 = ag.evaluate(BASE + 80 * 60, q(sig_bar.close - 0.02, sig_bar.close, BASE + 80 * 60),
                         [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNone(r1)
        self.assertIsNotNone(state.get('pending'))
        trig = state['pending']['trigger']
        self.assertEqual(state['pending']['side'], 'BUY')
        # fill: a quote that reaches the trigger
        sig = ag.evaluate(BASE + 80 * 60 + 1, q(trig, trig + 0.01, BASE + 80 * 60 + 1),
                          [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'BUY')
        self.assertIsNone(sig['initial_tp'])                 # Momentum has no take-profit
        self.assertLess(sig['initial_sl'], trig)             # SL below entry
        self.assertEqual(sig['extra']['be_trigger_r'], 1.0)
        self.assertEqual(sig['extra']['trail_activation_r'], 1.5)
        self.assertIsNone(state.get('pending'))              # consumed

    def test_sell_arms_then_fills(self):
        ag = self._agent()
        fm, sig_bar = self._market(-1)
        state = {}
        ag.evaluate(BASE + 80 * 60, q(sig_bar.close, sig_bar.close + 0.02, BASE + 80 * 60),
                    [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNotNone(state.get('pending'))
        trig = state['pending']['trigger']
        self.assertEqual(state['pending']['side'], 'SELL')
        sig = ag.evaluate(BASE + 80 * 60 + 1, q(trig - 0.01, trig, BASE + 80 * 60 + 1),
                          [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNotNone(sig)
        self.assertEqual(sig['side'], 'SELL')
        self.assertGreater(sig['initial_sl'], trig)

    def test_pending_expires(self):
        ag = self._agent()
        fm, sig_bar = self._market(1)
        state = {}
        ag.evaluate(BASE, q(sig_bar.close - 0.02, sig_bar.close, BASE), [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNotNone(state.get('pending'))
        # a much later tick (past pending_expiry_seconds) cancels with EXPIRED, no fill
        trig = state['pending']['trigger']
        sig = ag.evaluate(BASE + 10_000, q(trig, trig + 0.01, BASE + 10_000),
                          [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNone(sig)
        self.assertIsNone(state.get('pending'))
        self.assertTrue(any(e['event'] == 'CANCELLED' and e['reason'] == 'EXPIRED'
                            for e in state.get('_emit', [])))


class SimulatorExitManagement(unittest.TestCase):
    """break-even -> trailing -> stop-out, with no take-profit, scored in R off the initial SL."""
    def _pos(self, extra):
        la = N(id='MOMENTUM_M5_M1|XAUUSD|c', symbol='XAUUSD',
                agent=N(strategy_id='MOMENTUM_M5_M1', version='v', config_id='c'))
        sig = dict(side='BUY', signal_reason='r', signal_time=BASE, trigger_price=100.0,
                   initial_sl=99.0, initial_tp=None, timeframe='M1', session=None,
                   entry_candle_end=None, config_snapshot={}, extra=extra)
        return Simulator.open(la, sig, q(100.0, 100.0, BASE), BASE)

    def test_break_even_then_trail_then_stop(self):
        pos = self._pos(dict(be_trigger_r=1.0, be_offset_points=0.0,
                             trail_activation_r=1.5, trail_distance_r=0.75))
        self.assertEqual(pos['risk'], 1.0)
        # +0.5R: nothing moves yet
        pos, closed = Simulator.monitor(pos, q(100.5, 100.5, BASE + 1), BASE + 1, 120)
        self.assertFalse(closed)
        self.assertEqual(pos['stop'], 99.0)
        # +1.0R: break-even locks the stop at entry
        pos, closed = Simulator.monitor(pos, q(101.0, 101.0, BASE + 2), BASE + 2, 120)
        self.assertEqual(pos['stop'], 100.0)
        # +1.5R: trailing tightens to price - 0.75R
        pos, closed = Simulator.monitor(pos, q(101.5, 101.5, BASE + 3), BASE + 3, 120)
        self.assertEqual(pos['stop'], 100.75)
        # pull back through the trailed stop -> SL exit at 100.75, +0.75R (no TP ever hit)
        pos, closed = Simulator.monitor(pos, q(100.70, 100.70, BASE + 4), BASE + 4, 120)
        self.assertTrue(closed)
        self.assertEqual(pos['exit_reason'], 'SL')
        self.assertEqual(pos['exit_price'], 100.75)
        self.assertAlmostEqual(pos['pnl_r'], 0.75, places=4)

    def test_no_tp_field_never_closes_on_tp(self):
        pos = self._pos(dict(trail_activation_r=2.0, trail_distance_r=1.0))
        # a big favorable move with no TP set must NOT close the position
        pos, closed = Simulator.monitor(pos, q(120.0, 120.0, BASE + 1), BASE + 1, 120)
        self.assertFalse(closed)


if __name__ == '__main__':
    unittest.main()
