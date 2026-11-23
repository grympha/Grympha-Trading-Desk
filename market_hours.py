"""Forex/metals market hours (New York 17:00 convention), stdlib only.

Used to tell "the market is closed" apart from "the price feed is broken": during the weekend
(Fri 17:00 -> Sun 17:00 New York) and the daily rollover break (17:00-18:05 New York) quotes
stop or thin out, so a stale quote is expected and is reported as MARKET CLOSED, not a fault.
US daylight-saving rules are computed here, so no time-zone database is needed.
"""
from __future__ import annotations
import time
from datetime import datetime, timedelta, timezone

ROLLOVER_MINUTES = 65  # 17:00-18:05 New York: metals pause ~1 h, FX ticks are sparse


CRYPTO_PREFIXES = ('BTC', 'ETH', 'XRP', 'LTC', 'BCH', 'SOL', 'ADA', 'DOGE', 'DOT', 'BNB', 'XLM', 'LINK', 'AVAX', 'TRX')


def trades_24_7(symbol: str | None) -> bool:
    """Crypto CFDs trade through weekends and the FX rollover; forex/metals do not."""
    return bool(symbol) and str(symbol).upper().startswith(CRYPTO_PREFIXES)


class MarketClosed(ValueError):
    """Raised instead of a stale-quote error while the market is closed."""


def _nth_sunday(year, month, n):
    d = datetime(year, month, 1, tzinfo=timezone.utc)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def new_york_offset(utc: datetime) -> int:
    """-4 (EDT) between the 2nd Sunday of March 02:00 and the 1st Sunday of November 02:00 local, else -5."""
    start = _nth_sunday(utc.year, 3, 2) + timedelta(hours=7)   # 02:00 EST = 07:00 UTC
    end = _nth_sunday(utc.year, 11, 1) + timedelta(hours=6)    # 02:00 EDT = 06:00 UTC
    return -4 if start <= utc < end else -5


def status(now: float | None = None) -> tuple[str, float | None]:
    """('open' | 'weekend' | 'rollover', epoch when quotes are expected again or None)."""
    utc = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
    ny = utc + timedelta(hours=new_york_offset(utc))
    minutes = ny.hour * 60 + ny.minute
    wd = ny.weekday()  # Mon=0 .. Sun=6

    def at(day_offset, hour, minute=0):
        local = (ny + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        naive_utc = local - timedelta(hours=new_york_offset(local.replace(tzinfo=timezone.utc)))
        return naive_utc.replace(tzinfo=timezone.utc).timestamp()

    if (wd == 4 and minutes >= 17 * 60) or wd == 5 or (wd == 6 and minutes < 17 * 60):
        return 'weekend', at((6 - wd) % 7, 17)
    if 17 * 60 <= minutes < 17 * 60 + ROLLOVER_MINUTES:
        return 'rollover', at(0, 18, 5)
    return 'open', None


def describe(now: float | None = None) -> str | None:
    """Human message for a closed market, with the reopening time in the PC's local time."""
    state, reopen = status(now)
    if state == 'open':
        return None
    when = time.strftime('%a %d %b %H:%M', time.localtime(reopen))
    if state == 'weekend':
        return f'Market closed for the weekend · reopens {when} (your time)'
    return f'Daily rollover break · quotes resume about {when} (your time)'


def check_stale(age_seconds: float, limit_seconds: float, now: float | None = None, symbol: str | None = None) -> None:
    """Raise MarketClosed if a stale quote is explained by market hours; otherwise do nothing."""
    if age_seconds > limit_seconds and not trades_24_7(symbol):
        message = describe(now)
        if message:
            raise MarketClosed(f'{message} · last quote {age_seconds / 3600:.1f} h ago')


_last_logged = None


def log_closed_once(message: str) -> None:
    """Log/print the closure once per closure period (not once per pair per cycle)."""
    global _last_logged
    key = message.split(' · last quote')[0]
    if key != _last_logged:
        _last_logged = key
        import logging
        logging.info('%s · analysis paused; checks continue quietly', key)
        text = key + ' · analysis paused; checks continue quietly'
        try:
            print(text, flush=True)
        except UnicodeEncodeError:
            print(text.encode('ascii', 'replace').decode(), flush=True)


def reset_closed_log() -> None:
    global _last_logged
    _last_logged = None


def health_status(checked: int, blocked: int, closed: int) -> str:
    """Scanner card status: a closed market is not a fault."""
    if blocked:
        return 'DEGRADED'
    if closed and closed >= checked:
        return 'MARKET CLOSED'
    return 'HEALTHY' if checked else 'IDLE'
