"""Index CFD sessions (German and US indices) and Live-Test-only pairs."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import market_hours as mh
from dashboard_store import Store
from live_test import LiveTestEngine, ensure_default_agents, live_test_symbols


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc).timestamp()


class GermanIndex(unittest.TestCase):
    SYM = '.DE40Cash'

    def test_winter_session_is_0800_to_2155_frankfurt(self):
        # 26 Nov 2026 (CET = UTC+1): 08:00-21:55 Frankfurt = 07:00-20:55 UTC
        self.assertEqual(mh.status(utc(2026, 11, 26, 6, 59), self.SYM)[0], 'session')
        self.assertEqual(mh.status(utc(2026, 11, 26, 7, 0), self.SYM)[0], 'open')
        self.assertEqual(mh.status(utc(2026, 11, 26, 20, 54), self.SYM)[0], 'open')
        self.assertEqual(mh.status(utc(2026, 11, 26, 20, 55), self.SYM)[0], 'session')

    def test_summer_session_moves_with_frankfurt_dst(self):
        # 15 Jul 2026 (CEST = UTC+2): opens 06:00 UTC
        self.assertEqual(mh.status(utc(2026, 7, 15, 5, 59), self.SYM)[0], 'session')
        self.assertEqual(mh.status(utc(2026, 7, 15, 6, 0), self.SYM)[0], 'open')

    def test_weekend(self):
        self.assertEqual(mh.status(utc(2026, 11, 27, 21, 0), self.SYM)[0], 'weekend')   # Fri after close
        self.assertEqual(mh.status(utc(2026, 11, 29, 12, 0), self.SYM)[0], 'weekend')   # Sunday
        self.assertEqual(mh.status(utc(2026, 11, 30, 6, 30), self.SYM)[0], 'weekend')  # Mon before open

    def test_overnight_gap_is_not_missing_data(self):
        self.assertEqual(mh.unexplained_gap_seconds(utc(2026, 11, 25, 20, 55), utc(2026, 11, 26, 7, 0), self.SYM), 0)

    def test_stale_quote_after_close_is_market_closed(self):
        with self.assertRaises(mh.MarketClosed) as e:
            mh.check_stale(3600, 60, utc(2026, 11, 26, 22, 0), self.SYM)
        self.assertIn('Index outside its trading session', str(e.exception))


class USIndices(unittest.TestCase):
    def test_daily_break_1615_to_2000_new_york(self):
        for sym in ('.US30Cash', '.US500Cash', '.USTECHCash'):
            self.assertEqual(mh.status(utc(2026, 11, 25, 21, 14), sym)[0], 'open', sym)      # 16:14 EST
            self.assertEqual(mh.status(utc(2026, 11, 25, 21, 15), sym)[0], 'session', sym)   # 16:15 EST
            self.assertEqual(mh.status(utc(2026, 11, 26, 0, 59), sym)[0], 'session', sym)    # 19:59 EST
            self.assertEqual(mh.status(utc(2026, 11, 26, 1, 0), sym)[0], 'open', sym)        # 20:00 EST

    def test_weekend_friday_1615_to_sunday_2000(self):
        self.assertEqual(mh.status(utc(2026, 11, 27, 21, 15), '.US500Cash')[0], 'weekend')
        self.assertEqual(mh.status(utc(2026, 11, 30, 0, 59), '.US500Cash')[0], 'weekend')
        self.assertEqual(mh.status(utc(2026, 11, 30, 1, 0), '.US500Cash')[0], 'open')

    def test_a_real_gap_inside_the_session_still_counts(self):
        a = utc(2026, 11, 25, 15, 0)
        self.assertEqual(mh.unexplained_gap_seconds(a, a + 600, '.US30Cash'), 600)


class OtherSymbolsUnchanged(unittest.TestCase):
    def test_forex_metals_and_japan_keep_the_new_york_schedule(self):
        t = utc(2026, 11, 25, 22, 30)                     # 17:30 New York: forex rollover
        for sym in (None, 'EURUSD', 'XAUEUR', '.JP225Cash', 'GBPAUD'):
            self.assertEqual(mh.status(t, sym)[0], 'rollover', sym)
        self.assertEqual(mh.status(utc(2026, 11, 25, 22, 30), '.US30Cash')[0], 'session')

    def test_index_detection(self):
        self.assertEqual(mh.index_market('.DE40Cash'), 'DE')
        self.assertEqual(mh.index_market('.USTECHCash'), 'US')
        self.assertIsNone(mh.index_market('.JP225Cash'))
        self.assertIsNone(mh.index_market('EURUSD'))


class LiveTestOnlyPairs(unittest.TestCase):
    CFG = {'symbols': ['XAUUSD', 'EURUSD'], 'confluence': {'symbols': ['XAUUSD']},
           'live_test_extra_symbols': ['.DE40Cash', 'GBPAUD', 'EURUSD']}

    def test_extra_pairs_are_appended_once(self):
        self.assertEqual(live_test_symbols(self.CFG), ['XAUUSD', 'EURUSD', '.DE40Cash', 'GBPAUD'])

    def test_every_agent_family_runs_on_the_extra_pairs(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        eng = LiveTestEngine(Store(Path(tmp.name) / 'db.sqlite3'))
        ensure_default_agents(eng, self.CFG)
        on = lambda sid: {la.base_symbol for la in eng.agents.values() if la.agent.strategy_id == sid}
        for sid in ('CMS_SSR', 'INVERSE_FVG_RETEST', 'LIVE_BBMA', 'LIVE_CONFLUENCE'):
            self.assertTrue({'.DE40Cash', 'GBPAUD'} <= on(sid), sid)
        self.assertEqual(on('LIVE_ORB'), {'XAUUSD'})        # ORB stays gold-only
        self.assertEqual(on('LIVE_CONFLUENCE'), {'XAUUSD', '.DE40Cash', 'GBPAUD', 'EURUSD'})

    def test_extra_pairs_never_reach_live_trading_lists(self):
        cfg = dict(self.CFG)
        live_test_symbols(cfg)
        self.assertEqual(cfg['symbols'], ['XAUUSD', 'EURUSD'])
        self.assertEqual(cfg['confluence']['symbols'], ['XAUUSD'])

    def test_bad_values_are_ignored(self):
        self.assertEqual(live_test_symbols({'symbols': ['XAUUSD'], 'live_test_extra_symbols': 'GBPAUD'}), ['XAUUSD'])
        self.assertEqual(live_test_symbols({'symbols': ['XAUUSD'], 'live_test_extra_symbols': [None, '', 5]}), ['XAUUSD'])


if __name__ == '__main__':
    unittest.main()
