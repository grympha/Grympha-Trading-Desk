"""XAUUSD MT5 signal-only bot: closed-bar, five-factor trend confluence.

No orders are ever sent. Uses the XAUUSD symbol from the connected MT5 terminal.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import pandas as pd
import requests
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange, BollingerBands
from dotenv import dotenv_values

# =========================== CONFIGURATION ===========================
# Read settings from the .env alongside this script. Direct parsing avoids
# ambiguity from earlier PowerShell variables or missing load_dotenv calls.
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
FILE_SETTINGS = dotenv_values(ENV_FILE) if ENV_FILE.is_file() else {}

def setting(name: str, default: str = "") -> str:
    file_value = FILE_SETTINGS.get(name)
    if file_value is not None and str(file_value).strip():
        return str(file_value).strip()
    return os.environ.get(name, default).strip()

MT5_PATH = setting("MT5_PATH")  # Optional path to terminal64.exe
SYMBOL = setting("MT5_SYMBOL", "XAUUSD")  # e.g. XAUUSDm
TELEGRAM_TOKEN = setting("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = setting("TELEGRAM_CHAT_ID")
CHECK_INTERVAL_MINUTES = 5
MIN_SCORE = 3                  # 3 out of 5 conditions; NOT a win probability
BARS_PER_TIMEFRAME = 500       # Plenty of history for EMA 200
SR_LOOKBACK_H1 = 50
SR_NEAR_PCT = 0.004            # 0.4% of price
SL_ATR_MULTIPLIER = 1.5
TP1_ATR_MULTIPLIER = 2.0
TP2_ATR_MULTIPLIER = 3.5
MAX_SPREAD_POINTS = 0          # 0 disables; set based on your broker
MAX_TICK_AGE_SECONDS = 180     # Avoid signaling against a stale feed
# The user's observed RoboForex-Pro feed exposed timestamps ~2h ahead.
# This is an observed setting, NOT an established broker rule. Verify it on
# your own MT5 installation; an incorrect offset can hide stale-market data.
BROKER_TIMESTAMP_OFFSET_HOURS = float(setting("MT5_TIMESTAMP_OFFSET_HOURS", "2"))
SEND_TELEGRAM = True
DATA_DIR = BASE_DIR / "data" / "mt5"
LOG_DIR = BASE_DIR / "logs"
STATE_FILE = LOG_DIR / "signal_state.json"
DECISION_CSV = LOG_DIR / "signal_decisions.csv"
# =====================================================================

TIMEFRAMES = {
    "M15": (mt5.TIMEFRAME_M15, 15),
    "H1": (mt5.TIMEFRAME_H1, 60),
    "H4": (mt5.TIMEFRAME_H4, 240),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("xauusd_bot")


def connect_mt5() -> None:
    connected = mt5.initialize(path=MT5_PATH) if MT5_PATH else mt5.initialize()
    if not connected:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    account = mt5.account_info()
    if account is None:
        raise RuntimeError("MT5 connected but account is unavailable. Log in to MT5.")
    if mt5.symbol_info(SYMBOL) is None:
        names = [s.name for s in (mt5.symbols_get() or [])
                 if "XAU" in s.name.upper() or "GOLD" in s.name.upper()]
        raise RuntimeError(f"Symbol {SYMBOL!r} not found. Available gold names: {names[:30]}")
    if not mt5.symbol_select(SYMBOL, True):
        raise RuntimeError(f"Cannot select {SYMBOL}: {mt5.last_error()}")
    terminal = mt5.terminal_info()
    log.info("Connected: broker=%s server=%s symbol=%s terminal=%s",
             account.company, account.server, SYMBOL, getattr(terminal, "path", "unknown"))


def download_closed_bars(name: str) -> pd.DataFrame:
    timeframe, minutes = TIMEFRAMES[name]
    # Bar position 0 is still forming; use historical positions starting at 1.
    raw = mt5.copy_rates_from_pos(SYMBOL, timeframe, 1, BARS_PER_TIMEFRAME)
    if raw is None:
        raise RuntimeError(f"{name}: MT5 download failed: {mt5.last_error()}")
    if len(raw) < 250:
        raise RuntimeError(f"{name}: received only {len(raw)} candles; need at least 250. "
                           "Load more chart history / increase Max. bars in chart.")
    df = pd.DataFrame(raw)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close",
        "tick_volume": "Volume", "real_volume": "RealVolume", "spread": "SpreadPoints",
    }).set_index("time").sort_index()
    if df.index.has_duplicates:
        raise RuntimeError(f"{name}: duplicate candle timestamps")
    # Position 1 is the previous bar, but some broker feeds expose candle
    # timestamps in a server-clock convention that does not align with UTC.
    # Do not determine closure by comparing the label against PC UTC time.
    # Instead ensure MT5's current (position 0) bar is newer than our last bar.
    current = mt5.copy_rates_from_pos(SYMBOL, timeframe, 0, 1)
    if current is None or len(current) != 1:
        raise RuntimeError(f"{name}: cannot verify current bar: {mt5.last_error()}")
    if int(current[0]["time"]) <= int(raw[-1]["time"]):
        raise RuntimeError(f"{name}: last historical bar is not before current bar")
    return df


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["Close"]
    for window in (9, 21, 50, 200):
        df[f"EMA_{window}"] = EMAIndicator(close=close, window=window).ema_indicator()
    df["RSI"] = RSIIndicator(close=close, window=14).rsi()
    macd = MACD(close=close)
    df["MACD"] = macd.macd()
    df["MACD_signal"] = macd.macd_signal()
    df["MACD_hist"] = macd.macd_diff()
    df["ATR"] = AverageTrueRange(high=df["High"], low=df["Low"], close=close,
                                  window=14).average_true_range()
    bb = BollingerBands(close=close, window=20)
    df["BB_upper"] = bb.bollinger_hband()
    df["BB_middle"] = bb.bollinger_mavg()
    df["BB_lower"] = bb.bollinger_lband()
    return df


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except (json.JSONDecodeError, OSError):
            log.warning("Unreadable state file; starting fresh")
    return {}


def save_state(state: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def send_telegram(message: str) -> bool:
    if not SEND_TELEGRAM:
        log.info("Telegram disabled; printing signal only")
        return False
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not configured. Signal only printed locally.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        response = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(result.get("description", "Telegram rejected the message"))
        return True
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        # Requests exceptions can contain the URL (which includes the secret token).
        # Never print that URL or the token in logs.
        if isinstance(exc, requests.RequestException):
            log.error("Telegram delivery failed: network/HTTP error (%s)", type(exc).__name__)
        else:
            log.error("Telegram delivery failed: %s", exc)
        return False


def append_decision(row: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["candle_utc", "checked_utc", "symbol", "direction", "score",
              "price", "atr", "support", "resistance", "spread_points", "reasons"]
    exists = DECISION_CSV.exists()
    with DECISION_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists or DECISION_CSV.stat().st_size == 0:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def analyze(state: dict[str, Any]) -> None:
    # Call per run to recover from terminal disconnects when possible.
    if mt5.terminal_info() is None or mt5.account_info() is None:
        mt5.shutdown()
        connect_mt5()

    frames = {name: indicators(download_closed_bars(name)) for name in TIMEFRAMES}
    m15, h1, h4 = (frames[name] for name in ("M15", "H1", "H4"))
    last15 = m15.iloc[-1]
    last1h = h1.iloc[-1]
    last4h = h4.iloc[-1]
    candle_time = m15.index[-1]
    candle_key = candle_time.isoformat()
    now = datetime.now(timezone.utc)
    # MT5 candle labels may be in the broker's server-clock convention.
    # Use the separate real-time tick timestamp below to detect stale feeds;
    # do not reject a completed bar based on a UTC candle-label comparison.
    if state.get("last_candle_checked") == candle_key and state.get("symbol") == SYMBOL:
        log.info("No new closed M15 candle since %s", candle_key)
        return

    # Validate indicator availability before changing state.
    values = [last15["RSI"], last15["MACD"], last15["MACD_signal"], last15["ATR"],
              last15["EMA_9"], last15["EMA_21"], last15["EMA_50"],
              last1h["EMA_9"], last1h["EMA_21"], last1h["EMA_50"],
              last4h["EMA_50"], last4h["EMA_200"]]
    if not all(pd.notna(x) and pd.api.types.is_number(x) for x in values):
        raise RuntimeError("Indicator warm-up incomplete or invalid market data")
    atr = float(last15["ATR"])
    if atr <= 0:
        raise RuntimeError("ATR is zero or negative; refusing to generate a signal")

    info = mt5.symbol_info(SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)
    if info is None or tick is None or tick.ask <= 0 or tick.bid <= 0:
        raise RuntimeError(f"Missing executable bid/ask for {SYMBOL}: {mt5.last_error()}")
    # Some MT5 brokers encode broker-wall-clock time as if it were UTC.
    # Convert to actual UTC using an explicitly configured, verified offset.
    reported_tick_time = datetime.fromtimestamp(tick.time, tz=timezone.utc)
    corrected_tick_time = reported_tick_time - timedelta(hours=BROKER_TIMESTAMP_OFFSET_HOURS)
    tick_age = (now - corrected_tick_time).total_seconds()
    if tick_age > MAX_TICK_AGE_SECONDS or tick_age < -5:
        log.info("No signal: stale/invalid tick age %.0fs after %.1fh broker offset "
                 "(reported=%s corrected=%s current=%s)",
                 tick_age, BROKER_TIMESTAMP_OFFSET_HOURS,
                 reported_tick_time.isoformat(), corrected_tick_time.isoformat(), now.isoformat())
        return
    spread_points = (tick.ask - tick.bid) / info.point
    if MAX_SPREAD_POINTS and spread_points > MAX_SPREAD_POINTS:
        log.info("No signal: spread %.1f points above limit %d", spread_points, MAX_SPREAD_POINTS)
        return

    # Only past completed H1 candles are used for simple rolling levels.
    recent_h1 = h1.tail(SR_LOOKBACK_H1)
    support = float(recent_h1["Low"].min())
    resistance = float(recent_h1["High"].max())
    reference_price = float(last15["Close"])

    # 1. H4 trend uses H4 close (not M15 close).
    bullish_h4 = last4h["Close"] > last4h["EMA_50"] > last4h["EMA_200"]
    bearish_h4 = last4h["Close"] < last4h["EMA_50"] < last4h["EMA_200"]
    direction = "LONG" if bullish_h4 else "SHORT" if bearish_h4 else None
    score = 0
    reasons = []
    if direction:
        score += 1
        reasons.append(f"H4 {('uptrend' if direction == 'LONG' else 'downtrend')}")

    # 2. H1 EMA alignment uses H1 values.
    if direction == "LONG" and last1h["EMA_9"] > last1h["EMA_21"] > last1h["EMA_50"]:
        score += 1
        reasons.append("H1 EMA 9 > 21 > 50")
    elif direction == "SHORT" and last1h["EMA_9"] < last1h["EMA_21"] < last1h["EMA_50"]:
        score += 1
        reasons.append("H1 EMA 9 < 21 < 50")

    # 3. M15 RSI.
    rsi = float(last15["RSI"])
    if (direction == "LONG" and 40 < rsi < 65) or (direction == "SHORT" and 35 < rsi < 60):
        score += 1
        reasons.append(f"M15 RSI {rsi:.1f}")

    # 4. M15 MACD.
    if (direction == "LONG" and last15["MACD"] > last15["MACD_signal"]) or (
        direction == "SHORT" and last15["MACD"] < last15["MACD_signal"]
    ):
        score += 1
        reasons.append("M15 MACD aligned")

    # 5. Price is on the correct side of H1 support or resistance.
    if direction == "LONG" and support <= reference_price and (reference_price - support) / reference_price < SR_NEAR_PCT:
        score += 1
        reasons.append("M15 close near H1 support")
    elif direction == "SHORT" and resistance >= reference_price and (resistance - reference_price) / reference_price < SR_NEAR_PCT:
        score += 1
        reasons.append("M15 close near H1 resistance")

    entry = float(tick.ask if direction == "LONG" else tick.bid) if direction else reference_price
    log.info("%s M15 close=%s H4=%s score=%d/5 direction=%s bid=%.2f ask=%.2f",
             SYMBOL, candle_key, h4.index[-1].isoformat(), score, direction or "HOLD", tick.bid, tick.ask)
    append_decision({
        "candle_utc": candle_key, "checked_utc": now.isoformat(), "symbol": SYMBOL,
        "direction": direction or "HOLD", "score": score, "price": round(entry, info.digits),
        "atr": round(atr, info.digits), "support": round(support, info.digits),
        "resistance": round(resistance, info.digits), "spread_points": round(spread_points, 1),
        "reasons": "; ".join(reasons),
    })
    state["last_candle_checked"] = candle_key
    state["symbol"] = SYMBOL
    save_state(state)

    if not direction or score < MIN_SCORE:
        log.info("HOLD: insufficient confluence. Factors: %s", ", ".join(reasons) or "none")
        return

    if direction == "LONG":
        sl = entry - SL_ATR_MULTIPLIER * atr
        tp1 = entry + TP1_ATR_MULTIPLIER * atr
        tp2 = entry + TP2_ATR_MULTIPLIER * atr
    else:
        sl = entry + SL_ATR_MULTIPLIER * atr
        tp1 = entry - TP1_ATR_MULTIPLIER * atr
        tp2 = entry - TP2_ATR_MULTIPLIER * atr
    if min(sl, tp1, tp2) <= 0:
        log.warning("Skipping signal: invalid SL/TP")
        return

    fmt = lambda v: f"{v:.{info.digits}f}"
    rr1 = abs(tp1-entry) / abs(entry-sl)
    rr2 = abs(tp2-entry) / abs(entry-sl)
    msg = (
        f"<b>{SYMBOL} — {direction} (ANALYSIS ONLY)</b>\n"
        f"M15 candle (MT5 timestamp): {candle_key}\n"
        f"Confluence: <b>{score}/5</b> (not win probability)\n\n"
        f"<b>Reference executable quote:</b> {fmt(entry)}\n"
        f"Bid / Ask: {fmt(tick.bid)} / {fmt(tick.ask)}\n"
        f"Stop-loss: <code>{fmt(sl)}</code>\n"
        f"TP1: <code>{fmt(tp1)}</code> ({rr1:.2f}R gross)\n"
        f"TP2: <code>{fmt(tp2)}</code> ({rr2:.2f}R gross)\n"
        f"ATR M15: {fmt(atr)}\n"
        f"H1 support / resistance: {fmt(support)} / {fmt(resistance)}\n"
        f"Spread: {spread_points:.1f} points\n\n"
        "<b>Conditions passed:</b>\n• " + "\n• ".join(reasons) + "\n\n"
        "<i>Research signal only. No orders placed. No news filter or entry trigger.</i>"
    )
    log.info("\n%s", msg)
    if send_telegram(msg):
        state["last_alert"] = f"{SYMBOL}:{candle_key}:{direction}"
        save_state(state)
        log.info("Telegram signal delivered")


def main() -> None:
    parser = argparse.ArgumentParser(description="MT5 XAUUSD signal-only bot")
    parser.add_argument("--test-telegram", action="store_true",
                        help="Send one Telegram test message and exit (no MT5 needed)")
    args = parser.parse_args()

    log.info("Configuration: .env found=%s token=%s chat_id=%s",
             ENV_FILE.is_file(), bool(TELEGRAM_TOKEN), bool(TELEGRAM_CHAT_ID))
    if args.test_telegram:
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            log.error("Telegram credentials missing. Set TELEGRAM_BOT_TOKEN and "
                      "TELEGRAM_CHAT_ID in %s", ENV_FILE)
            raise SystemExit(2)
        if send_telegram("XAUUSD MT5 bot: Telegram test successful (signal-only mode)."):
            log.info("Telegram test delivered successfully")
            return
        raise SystemExit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    connect_mt5()
    log.info("Signal-only bot started. Every %d minutes. Telegram configured=%s",
             CHECK_INTERVAL_MINUTES, bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID))
    try:
        while True:
            started = time.monotonic()
            try:
                analyze(state)
            except Exception:
                log.exception("Analysis error (will retry next cycle)")
            delay = max(1.0, CHECK_INTERVAL_MINUTES * 60 - (time.monotonic() - started))
            time.sleep(delay)
    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
