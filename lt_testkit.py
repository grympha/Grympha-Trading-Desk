"""Shared deterministic replay harness for the Live Test agent acceptance tests.

No live MT5 terminal: a scriptable read-only MT5 returns different bars per timeframe and
per symbol, and tick-level Bid/Ask quotes drive fills, SL/TP, R and the signal log.
"""
from __future__ import annotations
import tempfile
from pathlib import Path
from types import SimpleNamespace as N

from dashboard_store import Store
from market_data import ReadOnlyMarketData, Bar, Quote
from live_test import LiveTestEngine

POINT = 0.01
DIGITS = 2


def q(bid, ask, t, symbol='XAUUSD'):
    return Quote(symbol, bid, ask, t, 0.0, POINT, DIGITS)


class MultiTFMT5:
    """Read-only MT5 returning scripted bars per timeframe. `bars` maps TF -> [Bar...].
    `names` controls which symbols resolve (to test offline / missing symbols)."""
    TIMEFRAME_M1 = 'M1'
    TIMEFRAME_M5 = 'M5'
    TIMEFRAME_M15 = 'M15'
    TIMEFRAME_M30 = 'M30'
    TIMEFRAME_H1 = 'H1'
    TIMEFRAME_H4 = 'H4'
    TIMEFRAME_D1 = 'D1'

    def __init__(self, bars: dict, names=('XAUUSD',)):
        self._bars = dict(bars)
        self._tick = None
        self.names = list(names)

    def set_quote(self, bid, ask, t):
        self._tick = N(bid=bid, ask=ask, time=t)

    def terminal_info(self):
        return N(connected=True)

    def symbol_select(self, name, enable=True):
        return name in self.names

    def symbol_info(self, name):
        return N(point=POINT, digits=DIGITS, trade_tick_size=POINT) if name in self.names else None

    def symbol_info_tick(self, name):
        return self._tick

    def symbols_get(self):
        return [N(name=n) for n in self.names]

    def copy_rates_from_pos(self, name, tf, start, count):
        return [dict(time=b.time, open=b.open, high=b.high, low=b.low, close=b.close)
                for b in self._bars.get(tf, [])]


def new_engine(testcase):
    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    store = Store(Path(tmp.name) / 'db.sqlite3')
    return LiveTestEngine(store), store


def md(mt5):
    return ReadOnlyMarketData(mt5, {'max_tick_age_seconds': 60})


def bars(series, base, dur):
    """[Bar...] from a list of (o,h,l,c) tuples starting at `base`, `dur` seconds apart."""
    return [Bar(int(base + i * dur), o, h, l, c) for i, (o, h, l, c) in enumerate(series)]
