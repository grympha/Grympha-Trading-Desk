"""Live Test: a position held through the broker's daily rollover break (or the weekend) stays VALID.

The market pause is scheduled, not missing data. Only time the market was OPEN without a price
sample is a data gap. A stop crossed when prices return fills at the reopening price (slippage)."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as N

import market_hours
from live_test import Simulator, LiveTestEngine, compute_stats, VALID, DATA_GAP, UNCERTAIN
from market_data import Quote
from market_hours import MarketClosed

POINT, DIGITS = 0.00001, 5


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc).timestamp()


# Winter (EST): rollover 17:00-18:05 New York = 22:00-23:05 UTC.
BEFORE = utc(2026, 11, 25, 21, 59)   # Wednesday, last FX quote before the break
REOPEN = utc(2026, 11, 25, 22, 5)    # FX prices resume
METALS = utc(2026, 11, 25, 23, 5)    # metals resume about an hour later


def pos(symbol='EURUSD', side='BUY', entry=1.10000, sl=1.09900, tp=1.10100, last=BEFORE):
    return dict(side=side, symbol=symbol, entry_time=last - 600, entry_price=entry, initial_sl=sl,
                initial_tp=tp, risk=abs(entry - sl), data_quality_status=VALID,
                last_sample_epoch=last, trade_id='t', agent_id='a')


def quote(bid, ask, t, symbol='EURUSD'):
    return Quote(symbol, bid, ask, t, 0.0, POINT, DIGITS)


class UnexplainedGap(unittest.TestCase):
    def test_rollover_is_explained(self):
        self.assertLessEqual(market_hours.unexplained_gap_seconds(BEFORE, REOPEN, 'EURUSD'), 120)
        self.assertLessEqual(market_hours.unexplained_gap_seconds(BEFORE, METALS, 'XAUUSD'), 120)

    def test_weekend_is_explained(self):
        fri = utc(2026, 11, 27, 21, 59)       # Friday 16:59 New York
        sun = utc(2026, 11, 29, 22, 5)        # Sunday 17:05 New York
        self.assertLessEqual(market_hours.unexplained_gap_seconds(fri, sun, 'GBPUSD'), 120)

    def test_open_market_gap_counts(self):
        a = utc(2026, 11, 25, 10, 0)
        self.assertEqual(market_hours.unexplained_gap_seconds(a, a + 600, 'EURUSD'), 600)

    def test_gap_partly_outside_the_break_counts_the_open_part(self):
        # Bot was offline 21:00-22:05 UTC: the hour before the break is real missing data.
        self.assertGreaterEqual(market_hours.unexplained_gap_seconds(utc(2026, 11, 25, 21, 0), REOPEN, 'EURUSD'), 3500)

    def test_crypto_never_pauses(self):
        self.assertEqual(market_hours.unexplained_gap_seconds(BEFORE, REOPEN, 'BTCUSD'), REOPEN - BEFORE)

    def test_summer_rollover_moves_with_new_york_dst(self):
        # July (EDT): rollover 21:00-22:05 UTC.
        self.assertLessEqual(market_hours.unexplained_gap_seconds(utc(2026, 7, 15, 20, 59), utc(2026, 7, 15, 21, 5), 'EURUSD'), 120)


class MonitorThroughThePause(unittest.TestCase):
    def test_position_held_through_rollover_stays_valid(self):
        p, closed = Simulator.monitor(pos(), quote(1.10020, 1.10030, REOPEN), REOPEN, 120)
        self.assertFalse(closed)
        self.assertEqual(p['data_quality_status'], VALID)
        p, closed = Simulator.monitor(p, quote(1.10110, 1.10120, REOPEN + 60), REOPEN + 60, 120)
        self.assertTrue(closed)
        self.assertEqual((p['exit_reason'], p['data_quality_status']), ('TP', VALID))
        self.assertEqual(compute_stats([p])['valid_trades'], 1)

    def test_stop_crossed_at_reopen_fills_at_reopen_price(self):
        # Reopens 30 pips below a 10-pip stop: the loss is the real slippage, not exactly -1R.
        p, closed = Simulator.monitor(pos(), quote(1.09700, 1.09720, REOPEN), REOPEN, 120)
        self.assertTrue(closed)
        self.assertEqual((p['exit_reason'], p['data_quality_status']), ('SL', VALID))
        self.assertAlmostEqual(p['exit_price'], 1.09700)
        self.assertAlmostEqual(p['pnl_r'], -3.0, places=3)
        self.assertEqual(p['gap_fill'], 'MARKET_PAUSE')

    def test_sell_stop_crossed_at_reopen(self):
        p = pos(side='SELL', entry=1.10000, sl=1.10100, tp=1.09900)
        p, closed = Simulator.monitor(p, quote(1.10180, 1.10200, REOPEN), REOPEN, 120)
        self.assertTrue(closed)
        self.assertAlmostEqual(p['exit_price'], 1.10200)    # SELL exits on ASK
        self.assertEqual(p['data_quality_status'], VALID)

    def test_real_gap_in_open_market_is_still_excluded(self):
        a = utc(2026, 11, 25, 10, 0)
        p, closed = Simulator.monitor(pos(last=a), quote(1.10020, 1.10030, a + 600), a + 600, 120)
        self.assertFalse(closed)
        self.assertEqual(p['data_quality_status'], DATA_GAP)

    def test_level_after_a_real_gap_stays_uncertain(self):
        a = utc(2026, 11, 25, 10, 0)
        p, closed = Simulator.monitor(pos(last=a), quote(1.09700, 1.09720, a + 600), a + 600, 120)
        self.assertEqual((p['exit_reason'], p['data_quality_status']), ('SL', UNCERTAIN))
        self.assertAlmostEqual(p['exit_price'], 1.09900)     # unchanged rule outside a market pause

    def test_crypto_gap_at_rollover_time_is_still_a_gap(self):
        p = pos(symbol='BTCUSD', entry=90000.0, sl=89000.0, tp=91000.0)
        p, _ = Simulator.monitor(p, quote(90100.0, 90110.0, REOPEN, 'BTCUSD'), REOPEN, 120)
        self.assertEqual(p['data_quality_status'], DATA_GAP)


class EngineMarketClosed(unittest.TestCase):
    """While the feed reports MarketClosed, open positions are NOT flagged."""
    def _engine_with_position(self, error):
        eng = LiveTestEngine.__new__(LiveTestEngine)
        eng.max_gap_default = 120
        saved = []
        eng._save_agent = lambda la: None
        eng._save_position = lambda p: saved.append(p['data_quality_status'])
        p = pos()
        la = N(agent=N(config={}), base_symbol='EURUSD', symbol='EURUSD', state={}, open_positions=[p],
               feed_status='LIVE')

        class Market:
            def resolve_symbol(self, s):
                return s

            def quote(self, s, now=None):
                raise error
        eng._run_agent(la, Market(), REOPEN - 120)
        return la, p, saved

    def test_market_closed_keeps_positions_valid(self):
        la, p, saved = self._engine_with_position(MarketClosed('Daily rollover break'))
        self.assertEqual(la.feed_status, 'STALE_OR_CLOSED')
        self.assertEqual(p['data_quality_status'], VALID)
        self.assertEqual(saved, [])

    def test_broken_feed_still_flags_positions(self):
        _, p, saved = self._engine_with_position(ValueError('Missing symbol/quote'))
        self.assertEqual(p['data_quality_status'], DATA_GAP)
        self.assertEqual(saved, [DATA_GAP])


if __name__ == '__main__':
    unittest.main()
