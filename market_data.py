"""Read-only MT5 market-data adapter for the Live Test tab.

HARD CONSTRAINT: this module exposes ONLY read methods (quotes, candles, symbol
resolution, connection state). It never calls order_send, order_check,
positions_get mutation, TRADE_ACTION_* or any API that places or changes orders
or positions. The Live Test engine receives ONLY this adapter, never a raw mt5
handle, so forward-test simulation can never reach the real executor.

A test (test_live_test) asserts this file and live_test.py contain none of the
forbidden order-sending tokens.
"""
from __future__ import annotations
import time
from collections import namedtuple

from market_hours import check_stale

Bar = namedtuple('Bar', 'time open high low close')
Quote = namedtuple('Quote', 'symbol bid ask time age point digits')

_TF = {'M1': 'TIMEFRAME_M1', 'M5': 'TIMEFRAME_M5', 'M15': 'TIMEFRAME_M15',
       'M30': 'TIMEFRAME_M30', 'H1': 'TIMEFRAME_H1', 'H4': 'TIMEFRAME_H4', 'D1': 'TIMEFRAME_D1'}
_TF_SECONDS = {'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800, 'H1': 3600, 'H4': 14400, 'D1': 86400}


class StaleQuote(ValueError):
    """Quote older than allowed and not explained by market hours."""


class ReadOnlyMarketData:
    """Read-only wrapper over an mt5 handle. No order/position mutation methods exist here."""

    def __init__(self, mt5, cfg=None):
        self._mt5 = mt5
        self.cfg = cfg or {}
        self._resolved = {}
        # Per-sample memo: the engine asks every agent at the SAME `now`, so agents sharing a pair
        # share one MT5 read per quote / candle request. Only explicit `now` values are cached and
        # the bot builds a new adapter every cycle, so data is never older than the cycle.
        self._memo = {}

    def _remember(self, key, read):
        if key[-1] is None:
            return read()
        if key not in self._memo:
            if len(self._memo) > 4000:
                self._memo.clear()
            try:
                self._memo[key] = (True, read())
            except Exception as exc:          # a closed/stale market is answered the same way for all
                self._memo[key] = (False, exc)
        ok, value = self._memo[key]
        if not ok:
            raise value
        return list(value) if isinstance(value, list) else value

    # ---- connection --------------------------------------------------------
    def connected(self) -> bool:
        ti = self._mt5.terminal_info()
        return bool(ti and getattr(ti, 'connected', False))

    # ---- offsets -----------------------------------------------------------
    def _tick_off(self) -> float:
        return float(self.cfg.get('tick_timestamp_offset_hours', 0)) * 3600

    def _bar_off(self) -> int:
        return int(float(self.cfg.get('bar_timestamp_offset_hours', 0)) * 3600)

    # ---- symbol resolution (handles broker suffix, e.g. XAUUSD.m) ----------
    def resolve_symbol(self, base: str) -> str | None:
        if base in self._resolved:
            return self._resolved[base]
        # 1) exact name that selects cleanly
        if self._mt5.symbol_select(base, True) and self._mt5.symbol_info(base) is not None:
            self._resolved[base] = base
            return base
        # 2) scan Market Watch for base or base+suffix (prefer the shortest match)
        try:
            names = [s.name for s in (self._mt5.symbols_get() or [])]
        except Exception:
            names = []
        up = base.upper()
        cands = [n for n in names if n.upper() == up] or \
                sorted((n for n in names if n.upper().startswith(up)), key=len)
        for n in cands:
            if self._mt5.symbol_select(n, True) and self._mt5.symbol_info(n) is not None:
                self._resolved[base] = n
                return n
        self._resolved[base] = None
        return None

    def spec(self, symbol: str) -> dict:
        info = self._mt5.symbol_info(symbol)
        if info is None:
            raise ValueError('Symbol spec unavailable')
        point = float(info.point)
        return dict(point=point, digits=int(info.digits),
                    tick_size=float(getattr(info, 'trade_tick_size', 0) or point))

    # ---- quote (read-only) -------------------------------------------------
    def quote(self, symbol: str, now: float | None = None, max_age: float | None = None) -> Quote:
        return self._remember(('quote', symbol, max_age, now), lambda: self._read_quote(symbol, now, max_age))

    def _read_quote(self, symbol, now, max_age):
        now = time.time() if now is None else now
        if not self._mt5.symbol_select(symbol, True):
            raise ValueError('Symbol cannot be selected')
        info = self._mt5.symbol_info(symbol)
        tick = self._mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            raise ValueError('Missing symbol/quote')
        if not (tick.bid > 0 and tick.ask >= tick.bid and info.point > 0):
            raise ValueError('Invalid bid/ask/point')
        age = now - (tick.time - self._tick_off())
        limit = self.cfg.get('max_tick_age_seconds', 60) if max_age is None else max_age
        if age >= -5:
            check_stale(age, limit, now, symbol)  # weekend/rollover -> MarketClosed
        return Quote(symbol=symbol, bid=float(tick.bid), ask=float(tick.ask),
                     time=float(tick.time) - self._tick_off(), age=float(age),
                     point=float(info.point), digits=int(info.digits))

    def is_fresh(self, q: Quote, max_age: float | None = None) -> bool:
        limit = self.cfg.get('max_tick_age_seconds', 60) if max_age is None else max_age
        return -5 <= q.age <= limit

    # ---- ticks (read-only history, for tick-mode agents such as TrailFlip) ----
    def ticks(self, symbol: str, start: float, end: float) -> list[tuple[float, float, float]]:
        """Every bid/ask change in (start, end], as [(epoch, bid, ask)] in time order (UTC epochs,
        same offset handling as quote())."""
        off = self._tick_off()
        rows = self._mt5.copy_ticks_range(symbol, int(start + off), int(end + off) + 1, self._mt5.COPY_TICKS_INFO)
        if rows is None:
            raise ValueError(f'No ticks for {symbol}')
        out = []
        for r in rows:
            t, bid, ask = float(r['time_msc']) / 1000 - off, float(r['bid']), float(r['ask'])
            if start < t <= end + 1 and bid > 0 and ask >= bid:
                out.append((t, bid, ask))
        return out

    # ---- candles (read-only, closed bars only) -----------------------------
    def candles(self, symbol: str, timeframe: str, count: int, now: float | None = None) -> list[Bar]:
        return self._remember(('candles', symbol, timeframe, count, now),
                              lambda: self._read_candles(symbol, timeframe, count, now))

    def _read_candles(self, symbol, timeframe, count, now):
        now = time.time() if now is None else now
        tf = _TF.get(timeframe)
        if tf is None:
            raise ValueError(f'Unknown timeframe {timeframe}')
        rows = self._mt5.copy_rates_from_pos(symbol, getattr(self._mt5, tf), 0, count)
        if rows is None or len(rows) == 0:
            raise ValueError(f'No {timeframe} history for {symbol}')
        off, dur = self._bar_off(), _TF_SECONDS[timeframe]
        bars = [Bar(int(r['time']) - off, float(r['open']), float(r['high']), float(r['low']), float(r['close']))
                for r in rows]
        bars.sort(key=lambda b: b.time)
        # keep only CLOSED bars (exclude the forming bar 0)
        return [b for b in bars if b.time + dur <= now]
