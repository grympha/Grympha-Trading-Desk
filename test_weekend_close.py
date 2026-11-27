"""Weekend rule: from Friday 20:00 MT5 time, bot positions on non-crypto pairs close, pending entries
are cancelled and new entries wait for the next week. BTCUSD / ETHUSD keep running. Fake MT5 only."""
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as N
from unittest.mock import patch

import test_demo_execution as demo_tests
import weekend_close as wc
from demo_execution import DemoExecutor

ON = {'weekend_close': {'enabled': True}}


def server(y, mo, d, h, mi=0):
    """An MT5 tick time: the broker's wall clock written as if it were UTC."""
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


FRI_1959, FRI_2000 = server(2026, 11, 27, 19, 59), server(2026, 11, 27, 20, 0)
SAT, MON = server(2026, 11, 28, 10), server(2026, 11, 30, 9)


class Rule(unittest.TestCase):
    def test_friday_2000_server_time_until_the_week_ends(self):
        self.assertEqual([wc.closing_time('XAUUSD', t, ON) for t in (FRI_1959, FRI_2000, SAT, MON)],
                         [False, True, True, False])
        self.assertTrue(wc.closing_time('EURUSD', server(2026, 11, 27, 23, 50), ON))
        self.assertFalse(wc.closing_time('EURUSD', server(2026, 11, 26, 21, 0), ON))      # Thursday evening

    def test_crypto_keeps_running(self):
        for sym in ('BTCUSD', 'ETHUSD', 'btcusd.m'):
            self.assertFalse(wc.closing_time(sym, SAT, ON), sym)
        self.assertTrue(wc.closing_time('XAGUSD', SAT, ON))

    def test_settings(self):
        cfg = {'weekend_close': {'enabled': True, 'friday_time': '21:30', 'keep_open': ['BTCUSD']}}
        self.assertFalse(wc.closing_time('XAUUSD', server(2026, 11, 27, 21, 29), cfg))
        self.assertTrue(wc.closing_time('XAUUSD', server(2026, 11, 27, 21, 30), cfg))
        self.assertTrue(wc.closing_time('ETHUSD', SAT, cfg))                               # no longer kept open
        self.assertFalse(wc.closing_time('XAUUSD', SAT, {'weekend_close': {'enabled': False}}))
        self.assertTrue(wc.settings({})['enabled'] or wc.DEFAULTS['enabled'] is False)     # on unless switched off


class Broker(demo_tests.FakeMT5):
    """Fake MT5 whose clock is set by the test and that can look up a position by ticket."""
    def __init__(self, clock):
        super().__init__()
        self.clock = clock

    def symbol_info_tick(self, *_):
        return N(time=self.clock, bid=1.0999, ask=1.1000)

    def positions_get(self, ticket=None):
        return tuple(p for p in self.positions if ticket is None or p.ticket == ticket)

    def order_send(self, r):
        if 'position' not in r:
            return super().order_send(r)
        self.sent.append(dict(r))                                    # a close: the position goes, an exit deal is booked
        if self.none_reply:
            return None
        self.positions = [p for p in self.positions if p.ticket != r['position']]
        self.deals.append(N(ticket=1002, order=501, position_id=700, symbol=r['symbol'], magic=r['magic'], entry=1,
                            profit=1.0, commission=-.02, swap=0., fee=0., reason=3, price=r['price'], time=self.clock))
        return N(retcode=10009, order=501, deal=1002, volume=r['volume'], price=r['price'])


class Executor(unittest.TestCase):
    setUp = demo_tests.Demo.setUp
    tearDown = demo_tests.Demo.tearDown
    result = demo_tests.Demo.result
    submit = demo_tests.Demo.submit
    latest = demo_tests.Demo.latest

    def executor(self, clock):
        self.mt5 = Broker(clock)
        self.cfg = {**self.cfg, **ON}
        return DemoExecutor(self.mt5, self.store, self.cfg)

    def run_at(self, clock):
        """Pretend 'now' is the given server time (offset 0 in the test config: server = UTC)."""
        return patch('time.time', return_value=float(clock))

    def open_position(self, clock):
        ex = self.executor(clock)
        with self.run_at(clock):
            self.submit(ex, self.result())
        self.assertEqual(self.mt5.sent[0]['action'], 1)                                   # entry filled
        return ex

    def test_open_trade_is_closed_at_friday_2000(self):
        ex = self.open_position(FRI_1959)
        with self.run_at(FRI_1959):
            ex.refresh()
        self.assertEqual(len(self.mt5.sent), 1)                                           # 19:59: still held
        self.mt5.clock = FRI_2000
        with self.run_at(FRI_2000):
            ex.refresh()
        close = self.mt5.sent[-1]
        self.assertEqual((close['action'], close['position'], close['type'], close['volume']), (1, 701, 1, 0.01))
        self.assertIn('weekend close', close['comment'])
        self.assertEqual(self.latest()['weekend_close_order']['status'], 'ACCEPTED')
        with self.run_at(FRI_2000 + 15):
            self.mt5.clock = FRI_2000 + 15
            ex.refresh()                                                                  # never sent twice
        self.assertEqual(sum(1 for r in self.mt5.sent if r.get('position') == 701), 1)

    def test_unconfirmed_close_is_not_resent(self):
        ex = self.open_position(FRI_1959)
        self.mt5.clock, self.mt5.none_reply = FRI_2000, True
        with self.run_at(FRI_2000):
            ex.refresh()
            ex.refresh()
        self.assertEqual(sum(1 for r in self.mt5.sent if r.get('position') == 701), 1)
        self.assertEqual(self.latest()['weekend_close_order']['status'], 'UNCONFIRMED')

    def test_crypto_position_is_kept(self):
        self.cfg = {**self.cfg, 'symbols': ['BTCUSD']}
        ex = self.open_position(FRI_1959)
        with self.store.connect() as db:                                                  # same trade, booked as BTCUSD
            db.execute("UPDATE demo_orders SET symbol='BTCUSD'")
        self.mt5.positions[0].symbol = 'BTCUSD'
        self.mt5.clock = FRI_2000
        with self.run_at(FRI_2000):
            ex.refresh()
        self.assertEqual(len(self.mt5.sent), 1)

    def test_no_new_entry_after_friday_2000(self):
        ex = self.executor(FRI_2000)
        with self.run_at(FRI_2000):
            self.submit(ex, self.result())
        self.assertEqual((self.latest()['status'], self.mt5.sent), ('SKIPPED_WEEKEND', []))
        self.assertIn('Friday 20:00', self.latest()['note'])

    def test_pending_entry_is_cancelled(self):
        ex = self.executor(FRI_1959)
        with self.run_at(FRI_1959):
            self.submit(ex, self.result('BBMA', 'b'))                                     # a BUY STOP pending order
        self.assertEqual(self.mt5.sent[0]['action'], 5)
        self.mt5.clock = FRI_2000
        with self.run_at(FRI_2000):
            ex.refresh()
        self.assertEqual(self.mt5.sent[-1]['action'], 8)                                  # removed
        self.assertEqual(self.latest()['status'], 'CANCELLED')
        self.assertIn('Weekend rule', self.latest()['note'])


if __name__ == '__main__':
    unittest.main()
