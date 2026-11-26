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


def _last_sunday(year, month):
    d = datetime(year, month + 1, 1, tzinfo=timezone.utc) if month < 12 else datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    d -= timedelta(days=1)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def frankfurt_offset(utc: datetime) -> int:
    """+2 (CEST) between the last Sunday of March 01:00 UTC and the last Sunday of October 01:00 UTC, else +1."""
    start = _last_sunday(utc.year, 3) + timedelta(hours=1)
    end = _last_sunday(utc.year, 10) + timedelta(hours=1)
    return 2 if start <= utc < end else 1


# Index CFDs keep exchange-linked hours (measured from RoboForex M5 history, Nov 2026):
#   German index  : trades 08:00-21:55 Frankfurt, Monday-Friday
#   US indices    : closed 16:15-20:00 New York every day, weekend Fri 16:15 -> Sun 20:00
#   Japan 225, metals, forex: the New York 17:00 schedule below.
INDEX_PREFIXES = {'DE40': 'DE', 'GER40': 'DE', 'DAX': 'DE',
                  'US30': 'US', 'US500': 'US', 'USTECH': 'US', 'NAS100': 'US', 'SPX500': 'US', 'US100': 'US'}


def index_market(symbol: str | None) -> str | None:
    """'DE' / 'US' for index CFDs with their own session hours, else None (broker prefix '.' ignored)."""
    if not symbol:
        return None
    name = str(symbol).upper().lstrip('.#')
    return next((m for p, m in INDEX_PREFIXES.items() if name.startswith(p)), None)


def _index_status(utc: datetime, market: str) -> str:
    if market == 'DE':
        local = utc + timedelta(hours=frankfurt_offset(utc))
        minutes, wd = local.hour * 60 + local.minute, local.weekday()
        if wd < 5 and 8 * 60 <= minutes < 21 * 60 + 55:
            return 'open'
        return 'weekend' if (wd == 4 and minutes >= 21 * 60 + 55) or wd >= 5 or (wd == 0 and minutes < 8 * 60) else 'session'
    local = utc + timedelta(hours=new_york_offset(utc))
    minutes, wd = local.hour * 60 + local.minute, local.weekday()
    if (wd == 4 and minutes >= 16 * 60 + 15) or wd == 5 or (wd == 6 and minutes < 20 * 60):
        return 'weekend'
    return 'session' if 16 * 60 + 15 <= minutes < 20 * 60 else 'open'


def _next_open(now: float, symbol: str | None) -> float | None:
    """First 5-minute step within 4 days when the market is open again."""
    for k in range(1, 4 * 288 + 1):
        t = now + k * 300
        if status(t, symbol)[0] == 'open':
            return t - (t % 300)
    return None


def status(now: float | None = None, symbol: str | None = None) -> tuple[str, float | None]:
    """('open' | 'weekend' | 'rollover' | 'session', epoch when quotes are expected again or None).
    Without a symbol (or for forex/metals/Japan 225) the New York 17:00 forex schedule applies;
    German and US index CFDs use their own session hours ('session' = outside today's session)."""
    now = time.time() if now is None else now
    utc = datetime.fromtimestamp(now, timezone.utc)
    market = index_market(symbol)
    if market:
        return _index_status(utc, market), None
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


def unexplained_gap_seconds(start: float, end: float, symbol: str | None = None, step: float = 60) -> float:
    """Seconds between two price samples during which the market was OPEN.

    A gap that falls inside the daily rollover break or the weekend is a scheduled pause, not
    missing data. Crypto trades 24/7, so its whole gap counts. Gaps longer than 8 days are
    returned whole (the bot was simply off)."""
    if end <= start:
        return 0.0
    if trades_24_7(symbol) or end - start > 8 * 86400:
        return float(end - start)
    missing, t = 0.0, float(start)
    while t < end:
        seg = min(step, end - t)
        if status(t + seg / 2, symbol)[0] == 'open':
            missing += seg
        t += seg
    return missing


def describe(now: float | None = None, symbol: str | None = None) -> str | None:
    """Human message for a closed market, with the reopening time in the PC's local time."""
    now = time.time() if now is None else now
    state, reopen = status(now, symbol)
    if state == 'open':
        return None
    if reopen is None:
        reopen = _next_open(now, symbol)
    when = time.strftime('%a %d %b %H:%M', time.localtime(reopen)) if reopen else 'later'
    if state == 'weekend':
        return f'Market closed for the weekend · reopens {when} (your time)'
    if state == 'session':
        return f'Index outside its trading session · reopens {when} (your time)'
    return f'Daily rollover break · quotes resume about {when} (your time)'


def check_stale(age_seconds: float, limit_seconds: float, now: float | None = None, symbol: str | None = None) -> None:
    """Raise MarketClosed if a stale quote is explained by market hours; otherwise do nothing."""
    if age_seconds > limit_seconds and not trades_24_7(symbol):
        message = describe(now, symbol)
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
