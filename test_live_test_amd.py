"""Deterministic rule tests for the AMD / Power-of-Three (Market Cycle Guide model C1 + A1/B1)
Live Test agent. Proves the failed-breakout sequence and the three entry triggers, not profit.

All bars are placed at real UTC epochs because the agent windows on UTC wall-clock (06:00 range,
07:00-10:00 setup). A bullish candidate is built by hand: a 06:00-06:55 range [2000, 2012], a
bullish H1 context, a downside sweep of L, an origin candle, a reclaim+confirm, then a retest.
"""
from __future__ import annotations
import unittest
from datetime import datetime, timezone

from market_data import Bar, Quote
from live_test_amd import AmdPowerOfThree

POINT, DIGITS = 0.01, 2
DAY = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
SIX = DAY.replace(hour=6).timestamp()
SEVEN = DAY.replace(hour=7).timestamp()


def q(bid, ask, t):
    return Quote('XAUUSD', bid, ask, t, 0.0, POINT, DIGITS)


def bar(t, o, h, l, c):
    return Bar(t, o, h, l, c)


class FakeMarket:
    def __init__(self, m5, h1):
        self.tf = {'M5': m5, 'H1': h1}

    def candles(self, symbol, tf, count, now=None):
        return list(self.tf.get(tf, []))[-count:]


def h1_bullish():
    """17 H1 bars (all closed by 07:00) whose 2-bar pivots give higher highs AND higher lows.
    Peaks at n%4==2, troughs at n%4==0, spaced so each is the extreme of its own +/-2 window."""
    bump = {0: -4.0, 1: 0.0, 2: 4.0, 3: 0.0}
    out = []
    for n in range(17):
        trend = 2100.0 + 0.5 * n + bump[n % 4]
        hi, lo = trend + 1.0, trend - 1.0
        out.append(bar(SEVEN - (17 - n) * 3600, lo + 0.2, hi, lo, hi - 0.2))
    return out


def h1_flat():
    return [bar(SEVEN - (17 - n) * 3600, 2100.0, 2100.0, 2100.0, 2100.0) for n in range(17)]


def m5_prefix():
    """Ten 05:10-05:55 filler bars + the twelve 06:00-06:55 range bars (L=2000, H=2012);
    the 06:55 bar is the bullish origin candle [2001, 2003]."""
    pre = [bar(SIX - (10 - i) * 300, 2005, 2007, 2003, 2005) for i in range(10)]
    rng = [bar(SIX + 0 * 300, 2005, 2006, 2000, 2005),     # L = 2000
           bar(SIX + 1 * 300, 2006, 2012, 2005, 2011)]     # H = 2012
    for i in range(2, 11):
        rng.append(bar(SIX + i * 300, 2005, 2008, 2003, 2006))
    rng.append(bar(SIX + 11 * 300, 2001.5, 2003, 2001, 2002.5))  # 06:55 origin: bullish [2001,2003]
    return pre + rng


# scan bars (07:00+): two bearish inside bars, the sweep(+reclaim+confirm), a filler, the retest
S0 = bar(SEVEN + 0 * 300, 2003, 2004, 2001, 2002)                 # 07:00 bearish inside
S1 = bar(SEVEN + 1 * 300, 2003, 2004, 2001, 2002)                 # 07:05 bearish inside
S2_SWEEP = bar(SEVEN + 2 * 300, 2003, 2004, 1999.5, 2004)         # 07:10 sweep L, reclaim+confirm
S3_FILL = bar(SEVEN + 3 * 300, 2004, 2006, 2004, 2005)            # 07:15 above zone (no retest)
S4_RETEST = bar(SEVEN + 4 * 300, 2004, 2005, 2002, 2004)          # 07:20 retest zone, close>origin hi


class AmdBullish(unittest.TestCase):
    def _run_entry(self, model, m5, trig_epoch):
        ag = AmdPowerOfThree({'model': model})
        fm = FakeMarket(m5, h1_bullish())
        state = {}
        # first call at trigger-bar close: freezes context+range, then ARMS on the trigger bar
        r1 = ag.evaluate(trig_epoch + 1, q(2002.99, 2003.0, trig_epoch + 1),
                         [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNone(r1)
        self.assertEqual(state.get('phase'), 'ARMED', f'{model} should arm')
        self.assertEqual(state['bias'], 1)
        # next quote fills (next-open proxy): entry near the retest, deep stop, far target
        sig = ag.evaluate(trig_epoch + 30, q(2002.99, 2003.0, trig_epoch + 30),
                          [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNotNone(sig, f'{model} should fill')
        self.assertEqual(sig['side'], 'BUY')
        self.assertAlmostEqual(sig['initial_sl'], 1999.49, places=2)   # sweep extreme 1999.5 - 1 tick
        self.assertAlmostEqual(sig['initial_tp'], 2011.99, places=2)   # range high 2012 - 1 tick
        self.assertLess(sig['initial_sl'], sig['trigger_price'])
        self.assertLess(sig['trigger_price'], sig['initial_tp'])
        self.assertEqual(sig['extra']['config_variant'], model)
        self.assertEqual(sig['extra']['setup'], 'AMD_PO3')
        return sig

    def test_c1_confirm_and_retest_enters(self):
        m5 = m5_prefix() + [S0, S1, S2_SWEEP, S3_FILL, S4_RETEST]
        self._run_entry('C1', m5, SEVEN + 4 * 300)

    def test_a1_enters_on_reclaim_bar(self):
        # A1 needs no retest: it arms as soon as the sweep bar reclaims inside the range.
        m5 = m5_prefix() + [S0, S1, S2_SWEEP]
        self._run_entry('A1', m5, SEVEN + 2 * 300)

    def test_b1_enters_on_confirm_bar(self):
        # B1 arms on the origin-confirmation bar (here the sweep bar confirms on reclaim).
        m5 = m5_prefix() + [S0, S1, S2_SWEEP]
        self._run_entry('B1', m5, SEVEN + 2 * 300)


class AmdSkips(unittest.TestCase):
    def test_mixed_context_skips_the_day(self):
        ag = AmdPowerOfThree({'model': 'C1'})
        fm = FakeMarket(m5_prefix() + [S0, S1, S2_SWEEP, S3_FILL, S4_RETEST], h1_flat())
        state = {}
        sig = ag.evaluate(SEVEN + 4 * 300 + 1, q(2002.99, 2003.0, SEVEN + 4 * 300 + 1),
                          [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNone(sig)
        self.assertEqual(state.get('phase'), 'DONE')
        self.assertTrue(any(e['event'] == 'SKIP' and e['reason'] == 'CONTEXT_MIXED'
                            for e in state.get('_emit', [])))

    def test_opposite_boundary_swept_first_skips(self):
        # 07:05 pokes above H before any downside sweep -> not our candidate.
        opp = bar(SEVEN + 1 * 300, 2011, 2013, 2010, 2011)    # high 2013 > H + tick
        ag = AmdPowerOfThree({'model': 'C1'})
        fm = FakeMarket(m5_prefix() + [S0, opp], h1_bullish())
        state = {}
        sig = ag.evaluate(SEVEN + 1 * 300 + 1, q(2010.99, 2011.0, SEVEN + 1 * 300 + 1),
                          [], POINT, state, market=fm, symbol='XAUUSD')
        self.assertIsNone(sig)
        self.assertTrue(any(e['event'] == 'SKIP' and e['reason'] == 'OPPOSITE_SWEPT_FIRST'
                            for e in state.get('_emit', [])))


if __name__ == '__main__':
    unittest.main()
