"""TrailFlip — trailing stop-and-reverse on XAUUSD, simulated tick by tick. Fully SIMULATED.

From "TrailFlip — Strategy & User Guide" (Nov 2026). One rule: the open trade's stop-loss and the
opposite entry are the same price, and that price trails D behind the market.

  * Flat: a BUY STOP D above the ask and a SELL STOP D below the bid; each only moves toward price.
  * Long: level = bid - D, moves up only; short: level = ask + D, moves down only; a move needs at
    least one trail step (12% of D).
  * Price back at the level: the trade closes there and the opposite trade opens at the same price
    (the "flip"). Always in the market unless a limit says otherwise.
  * D = 0.4 x ATR(M1), clamped to [max(2 x median spread, 0.25 x ATR, stops level), 1.0 x ATR],
    refreshed every minute from closed M1 candles.
  * Limits: new entries / reversals pause while the spread is above max(median x 1.5, p90 x 1.1);
    more flips in the last hour than clamp(30 x ATR / D, 10, 90) -> 15 minutes of exits only; a day
    (UTC) at -daily_loss_r R (realised + open; guide default 2% / 0.25% = 8 R) closes everything
    and stops until the next day.
  * Video style trades in chop too. The trend-filter variant only enters when the M1 efficiency
    ratio (20 bars) is above 0.30 or price made a fast straight burst (last 30 s: net move >= D at
    >= 60% efficiency).

Fills: a stop fills at the first tick that reaches it, at that tick's price if it gapped past the
level, and `slippage` (price units, $0.05 on gold as in the guide's replay test) worse. R is the
fill-to-initial-level distance, so every trade is comparable with the other Live Test agents.

The guide's AI supervisor, local model and nightly learning are NOT simulated: their answers change
from run to run, while a forward test needs fixed rules.

Tick mode: the Live Test engine hands this agent every MT5 tick since its previous cycle (read-only
`copy_ticks_range`), so the 15 s bot cycle does not hide flips; the Quant Agent replays MT5 tick
history through the same `run_ticks`. READ-ONLY: never calls any order API.
"""
from __future__ import annotations

import bisect
import hashlib
from collections import deque

from live_test import VALID, DATA_GAP, atr, config_id
from market_hours import unexplained_gap_seconds

DAY = 86400


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return None if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


class TrailFlip:
    strategy_id = 'TRAILFLIP'
    version = 'trailflip@1'
    tick_mode = True             # the engine feeds every tick (run_ticks), not one quote per cycle
    needs_market = False
    timeframe = 'M1'

    DEFAULTS = dict(
        video_style=True,
        trail_atr=0.4, atr_period=14, min_spread_mult=2.0, min_atr_frac=0.25, max_atr_frac=1.0,
        stops_level=0.0, step_frac=0.12,
        spread_median_mult=1.5, spread_p90_mult=1.1, spread_warmup=30,
        flips_per_atr=30.0, min_flips_hour=10, max_flips_hour=90, cooldown_seconds=900,
        daily_loss_r=8.0, slippage=0.05,
        er_bars=20, er_threshold=0.30, burst_seconds=30, burst_efficiency=0.6,
        max_gap_seconds=120, max_catchup_seconds=86400, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)
        # runtime caches (not persisted; rebuilt within a minute after a restart)
        self._spreads = deque(maxlen=400)
        self._spread_t = 0.0
        self._mids = deque()

    def label(self):
        return 'TrailFlip · video style (tick)' if self.config['video_style'] else 'TrailFlip · trend filter (tick)'

    def evaluate(self, *args, **kwargs):        # never used: tick-mode agents run through run_ticks()
        return None

    # ------------------------------------------------------------------ parameters
    def _params(self, st, t, m1, m1_times):
        c = self.config
        end = bisect.bisect_right(m1_times, t - 60)           # M1 candles closed at t
        bars = m1[max(0, end - max(c['atr_period'], c['er_bars']) - 1):end]
        a = atr(bars, c['atr_period'])
        med = _median(self._spreads) if len(self._spreads) >= c['spread_warmup'] else None
        st['next_param'] = t - t % 60 + 60
        if not a or med is None:
            st['ready'] = False
            return
        lo = max(c['min_spread_mult'] * med, c['min_atr_frac'] * a, c['stops_level'])
        hi = max(lo, c['max_atr_frac'] * a)
        d = min(hi, max(lo, c['trail_atr'] * a))
        p90 = sorted(self._spreads)[int(len(self._spreads) * .9)]
        closes = [b.close for b in bars[-(c['er_bars'] + 1):]]
        path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
        st.update(ready=True, atr=a, d=d, step=c['step_frac'] * d,
                  max_spread=max(med * c['spread_median_mult'], p90 * c['spread_p90_mult']),
                  max_flips=int(min(c['max_flips_hour'], max(c['min_flips_hour'], round(c['flips_per_atr'] * a / d)))),
                  er=(abs(closes[-1] - closes[0]) / path) if path else 0.0)

    def _burst(self, st):
        if len(self._mids) < 3:
            return False
        net = self._mids[-1][1] - self._mids[0][1]
        path = sum(abs(self._mids[i][1] - self._mids[i - 1][1]) for i in range(1, len(self._mids)))
        return abs(net) >= st['d'] and path > 0 and abs(net) / path >= self.config['burst_efficiency']

    def _why_blocked(self, st, t, spread):
        if not st.get('ready'):
            return 'WARMING_UP'
        if st.get('halted_day') == int(t // DAY):
            return 'DAILY_LIMIT'
        if t < st.get('cooldown_until', 0):
            return 'FLIP_COOLDOWN'
        if spread > st['max_spread']:
            return 'WIDE_SPREAD'
        if not self.config['video_style'] and st.get('er', 0) < self.config['er_threshold'] and not self._burst(st):
            return 'NO_TREND'
        return None

    # ------------------------------------------------------------------ trades
    def _open(self, st, ctx, side, fill, level, t, spread, reason):
        st['seq'] = st.get('seq', 0) + 1
        tid = hashlib.sha1(f"{ctx['agent_id']}:{t:.3f}:{fill}:{st['seq']}".encode()).hexdigest()[:16]
        level = round(level, ctx['digits'])
        risk = abs(fill - level)
        st.setdefault('entries', []).append(t)
        return dict(
            trade_id=tid, agent_id=ctx['agent_id'], strategy_id=self.strategy_id, strategy_version=self.version,
            config_id=self.config_id, symbol=ctx['symbol'], timeframe='TICK', session=None, side=side,
            signal_reason=reason, signal_time=t, trigger_price=level, entry_time=t, entry_price=round(fill, ctx['digits']),
            initial_sl=level, initial_tp=None, entry_spread=round(spread, ctx['digits']), risk=risk,
            entry_candle_end=None, status='OPEN', stop=level, data_quality_status=VALID, last_sample_epoch=t,
            last_price=fill, current_price=fill, unrealized_pnl=0.0, unrealized_r=0.0, mfe_r=0.0, mae_r=0.0,
            trail_distance=round(st['d'], ctx['digits']),
            config_snapshot=dict(d=round(st['d'], 4), step=round(st['step'], 4), max_spread=round(st['max_spread'], 4),
                                 atr_m1=round(st['atr'], 4), video_style=self.config['video_style']))

    def _close(self, st, pos, exit_price, t, spread, reason, reversed_):
        buy = pos['side'] == 'BUY'
        pnl = (exit_price - pos['entry_price']) if buy else (pos['entry_price'] - exit_price)
        moved = abs(pos['stop'] - pos['initial_sl']) > 1e-9
        pos.update(status='CLOSED', exit_time=t, exit_price=exit_price, exit_spread=round(spread, 6),
                   exit_reason=reason or ('TRAIL_STOP' if moved else 'SL'), stop_moved_by='TRAIL' if moved else None,
                   duration_seconds=round(t - pos['entry_time']), price_pnl=pnl,
                   pnl_r=(pnl / pos['risk']) if pos['risk'] else None, current_price=exit_price,
                   unrealized_pnl=pnl, reversed=reversed_)
        day = int(t // DAY)
        if st.get('day') != day:
            st['day'], st['day_r'] = day, 0.0
        if pos['data_quality_status'] == VALID and pos['pnl_r'] is not None:
            st['day_r'] += pos['pnl_r']
        return pos

    # ------------------------------------------------------------------ the tick loop
    def run_ticks(self, st, pos, ticks, m1, point, digits, symbol, agent_id):
        """Advance through `ticks` [(epoch, bid, ask)] in order. `st` is the persistent state dict,
        `pos` the open position (or None), `m1` closed M1 Bars covering the window (plus warm-up).
        Returns (closed trades, open position or None)."""
        c, ctx = self.config, dict(agent_id=agent_id, symbol=symbol, digits=digits)
        m1_times = [b.time for b in m1]
        slip, closed = c['slippage'], []
        for t, bid, ask in ticks:
            if t <= st.get('tick_t', 0):
                continue
            prev, st['tick_t'] = st.get('tick_t'), t
            spread = ask - bid
            if t - self._spread_t >= 1:                       # sample the spread about once a second
                self._spreads.append(spread)
                self._spread_t = t
            self._mids.append((t, (bid + ask) / 2))
            while self._mids and self._mids[0][0] < t - c['burst_seconds']:
                self._mids.popleft()
            if t >= st.get('next_param', 0):
                self._params(st, t, m1, m1_times)
            day = int(t // DAY)
            if st.get('day') != day:
                st['day'], st['day_r'] = day, 0.0
            if pos is not None and prev and t - prev > c['max_gap_seconds'] \
                    and unexplained_gap_seconds(prev, t, symbol) > c['max_gap_seconds']:
                pos['data_quality_status'] = DATA_GAP            # broker feed had no prices while open
            st['entries'] = [x for x in st.get('entries', []) if x > t - 3600]

            if pos is not None:
                buy = pos['side'] == 'BUY'
                basis = bid if buy else ask
                level = pos['stop']
                pnl = (basis - pos['entry_price']) if buy else (pos['entry_price'] - basis)
                r = pnl / pos['risk'] if pos['risk'] else 0.0
                pos.update(last_sample_epoch=t, last_price=basis, current_price=basis, unrealized_pnl=pnl,
                           unrealized_r=r, mfe_r=round(max(pos['mfe_r'], r), 4), mae_r=round(min(pos['mae_r'], r), 4))
                hit = (bid <= level) if buy else (ask >= level)
                if hit:
                    fill = (min(level, bid) - slip) if buy else (max(level, ask) + slip)
                    fill = round(fill, digits)
                    after = st.get('day_r', 0.0) + (((fill - pos['entry_price']) if buy else (pos['entry_price'] - fill))
                                                    / pos['risk'] if pos['risk'] and pos['data_quality_status'] == VALID else 0.0)
                    if after <= -c['daily_loss_r']:
                        st['halted_day'] = day                   # this loss reaches the daily limit: no reversal
                    blocked = self._why_blocked(st, t, spread)
                    if blocked is None and len(st['entries']) >= st['max_flips']:
                        st['cooldown_until'] = t + c['cooldown_seconds']
                        blocked = 'FLIP_COOLDOWN'
                    closed.append(self._close(st, pos, fill, t, spread, None, blocked is None))
                    pos = None
                    if blocked is None:                      # the opposite stop at the same level fills too
                        side = 'SELL' if buy else 'BUY'
                        new_level = level + st['d'] if side == 'SELL' else level - st['d']
                        pos = self._open(st, ctx, side, fill, new_level, t, spread, 'FLIP')
                    st['buy_stop'] = st['sell_stop'] = None
                    st['blocked'] = blocked
                    continue
                if st.get('ready'):
                    cand = (bid - st['d']) if buy else (ask + st['d'])
                    if (cand - level if buy else level - cand) >= st['step']:
                        pos['stop'] = round(cand, digits)
                # daily loss limit counts the open trade too
                if pos['data_quality_status'] == VALID and st.get('day_r', 0.0) + r <= -c['daily_loss_r']:
                    closed.append(self._close(st, pos, round(basis - slip if buy else basis + slip, digits),
                                              t, spread, 'DAILY_LIMIT', False))
                    pos, st['halted_day'] = None, day
                    st['buy_stop'] = st['sell_stop'] = None
                continue

            # flat: two entry stops that only move toward price
            blocked = self._why_blocked(st, t, spread)
            if blocked is None and len(st['entries']) >= st['max_flips']:
                st['cooldown_until'] = t + c['cooldown_seconds']
                blocked = 'FLIP_COOLDOWN'
            st['blocked'] = blocked
            if blocked is not None:
                st['buy_stop'] = st['sell_stop'] = None
                continue
            d = st['d']
            if st.get('buy_stop') is None:
                st['buy_stop'], st['sell_stop'] = ask + d, bid - d
                continue
            st['buy_stop'] = min(st['buy_stop'], ask + d)
            st['sell_stop'] = max(st['sell_stop'], bid - d)
            if ask >= st['buy_stop']:
                trig = st['buy_stop']
                pos = self._open(st, ctx, 'BUY', round(max(trig, ask) + slip, digits), trig - d, t, spread, 'BUY_STOP')
            elif bid <= st['sell_stop']:
                trig = st['sell_stop']
                pos = self._open(st, ctx, 'SELL', round(min(trig, bid) - slip, digits), trig + d, t, spread, 'SELL_STOP')
            if pos is not None:
                st['buy_stop'] = st['sell_stop'] = None
        return closed, pos

    def abandon(self, st, pos, t, bid, ask, digits):
        """The agent was away longer than it can catch up on: close the open trade at the current price
        and flag it, then start flat."""
        st.update(buy_stop=None, sell_stop=None, tick_t=t)
        if pos is None:
            return None
        basis = bid if pos['side'] == 'BUY' else ask
        pos['data_quality_status'] = DATA_GAP
        return self._close(st, pos, round(basis, digits), t, ask - bid, 'RESUMED_AFTER_GAP', False)

    @staticmethod
    def status_view(st, pos):
        """Compact live state for the dashboard."""
        mode = pos['side'] if pos else ('FLAT' if not st.get('blocked') else st['blocked'])
        return dict(mode=mode, trail=st.get('d'), atr_m1=st.get('atr'), level=pos['stop'] if pos else None,
                    buy_stop=st.get('buy_stop'), sell_stop=st.get('sell_stop'), max_spread=st.get('max_spread'),
                    flips_last_hour=len(st.get('entries', [])), max_flips=st.get('max_flips'),
                    today_r=round(st.get('day_r', 0.0), 2), efficiency=st.get('er'), last_tick=st.get('tick_t'))
