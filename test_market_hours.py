import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as N
from unittest.mock import patch

import launcher_checks as lc
import market_hours as mh


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class MarketHours(unittest.TestCase):
    def test_weekend_and_rollover_boundaries_winter_and_summer(self):
        cases = {
            '2026-11-20T21:59': 'open', '2026-11-20T22:01': 'weekend',      # Fri close 17:00 EST
            '2026-11-22T10:00': 'weekend', '2026-11-22T22:01': 'rollover',  # Sun reopen, sparse first hour
            '2026-11-24T23:06': 'open',
            '2026-07-17T20:59': 'open', '2026-07-17T21:01': 'weekend',      # Fri close 17:00 EDT
            '2026-07-21T21:30': 'rollover', '2026-07-21T22:10': 'open',
        }
        for when, expected in cases.items():
            self.assertEqual(mh.status(at(when))[0], expected, when)

    def test_reopen_time_is_sunday_17_new_york(self):
        state, reopen = mh.status(at('2026-11-21T12:00'))
        self.assertEqual(state, 'weekend')
        self.assertEqual(datetime.fromtimestamp(reopen, timezone.utc).isoformat(), '2026-11-22T22:00:00+00:00')

    def test_stale_quote_is_market_closed_only_when_closed(self):
        with self.assertRaises(mh.MarketClosed):
            mh.check_stale(130000, 60, at('2026-11-22T10:00'))
        mh.check_stale(130000, 60, at('2026-11-24T12:00'))  # open market: caller raises its own stale error
        mh.check_stale(5, 60, at('2026-11-22T10:00'))       # fresh quote: nothing to explain
        self.assertTrue(issubclass(mh.MarketClosed, ValueError))

    def test_crypto_trades_24_7(self):
        self.assertTrue(mh.trades_24_7('BTCUSD')); self.assertTrue(mh.trades_24_7('ethusd.m'))
        self.assertFalse(mh.trades_24_7('XAUUSD')); self.assertFalse(mh.trades_24_7('XAGUSD')); self.assertFalse(mh.trades_24_7('EURUSD')); self.assertFalse(mh.trades_24_7(None))
        mh.check_stale(130000, 60, at('2026-11-22T10:00'), 'BTCUSD')  # weekend: crypto is NOT excused
        with self.assertRaises(mh.MarketClosed):
            mh.check_stale(130000, 60, at('2026-11-22T10:00'), 'EURUSD')

    def test_crypto_news_mapping(self):
        import json
        from pathlib import Path
        from news_filter import currencies
        here = Path(__file__).parent
        src = here / 'config.json' if (here / 'config.json').exists() else here / 'config.example.json'
        cfg = json.loads(src.read_text(encoding='utf-8-sig')).get('news_filter', {})
        for sym in ('BTCUSD', 'ETHUSD', 'XAGUSD'):
            if sym in cfg.get('symbol_currencies', {}):
                self.assertEqual(currencies(sym, cfg), {'USD'})
        self.assertEqual(currencies('BTCUSD', {}), set())  # without mapping it would be UNMAPPED (blocked)

    def test_scanner_health_status(self):
        self.assertEqual(mh.health_status(12, 0, 10), 'HEALTHY')        # crypto still scanning normally
        self.assertEqual(mh.health_status(10, 0, 10), 'MARKET CLOSED')  # everything closed
        self.assertEqual(mh.health_status(12, 1, 10), 'DEGRADED')       # a real block still shows
        self.assertEqual(mh.health_status(0, 0, 0), 'IDLE')

    def test_closure_logged_once(self):
        mh.reset_closed_log()
        with patch('builtins.print') as printed:
            for _ in range(9):
                mh.log_closed_once('Market closed for the weekend · reopens Mon · last quote 36.1 h ago')
        self.assertEqual(printed.call_count, 1)

    def test_launcher_shows_closed_not_fail(self):
        m = N(initialize=lambda **kw: True, shutdown=lambda: None,
              terminal_info=lambda: N(connected=True, data_path='C:/t', trade_allowed=True, tradeapi_disabled=False),
              account_info=lambda: N(trade_mode=0, margin_mode=2, trade_allowed=True, trade_expert=True, login=1, server='S'),
              symbol_select=lambda *a: True, symbol_info_tick=lambda *a: N(time=int(time.time()) - 130000, bid=1.0, ask=1.0001),
              copy_rates_from_pos=lambda *a: [0] * 400, TIMEFRAME_H4=1, TIMEFRAME_H1=2, TIMEFRAME_M15=3, TIMEFRAME_M5=4)
        cfg = dict(symbols=['AUDUSD'], confluence=dict(symbols=['AUDUSD']), news_filter=dict(enabled=False))
        with patch('market_hours.describe', return_value='Market closed for the weekend · reopens Mon 06:00'):
            rows, _ = lc.inspect_mt5(cfg, m)
        quote = next(r for r in rows if r['name'] == 'AUDUSD quote')
        self.assertEqual(quote['status'], 'CLOSED')
        self.assertFalse(any(r['status'] == 'FAIL' for r in rows))
        with patch('market_hours.describe', return_value=None):  # market open: a stale quote is still a FAIL
            rows, _ = lc.inspect_mt5(cfg, m)
        self.assertEqual(next(r for r in rows if r['name'] == 'AUDUSD quote')['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
