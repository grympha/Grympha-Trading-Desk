"""Live Test — forward-test strategies on live MT5 market data, fully simulated.

Every entry, position, exit and P/L here is a SIMULATION inside the Trading Desk.
This module receives ONLY a ReadOnlyMarketData adapter (market_data.py); it never
imports or calls the executor, order_send, or any order/position-mutating API.
A guard test (test_live_test) scans this file for forbidden tokens.

Price basis: a consistent mid price is used to build/observe breakouts; fills use
Ask (BUY entry) / Bid (BUY exit) and Bid (SELL entry) / Ask (SELL exit). R is the
entry-to-initial-SL distance. Spread is embedded once via the bid/ask fill basis.
"""
from __future__ import annotations
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

MYT = timezone(timedelta(hours=8))  # Malaysia time, no DST

VALID = 'OK'
DATA_GAP = 'DATA_GAP'
UNCERTAIN = 'UNCERTAIN'


# --------------------------------------------------------------------------- helpers
def config_id(config: dict) -> str:
    return hashlib.sha1(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]


def myt_iso(epoch) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), MYT).strftime('%Y-%m-%d %H:%M:%S MYT')


def iso_utc(epoch) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def session_first_hour(now: float, open_hour: int, tz_name: str, hours: int = 1):
    """[start, end] epoch of the first `hours` of today's session in tz `tz_name`
    (DST-aware via zoneinfo). Falls back to a fixed MYT if zoneinfo is missing."""
    tz = ZoneInfo(tz_name) if ZoneInfo else MYT
    local = datetime.fromtimestamp(now, tz)
    start_local = local.replace(hour=int(open_hour), minute=0, second=0, microsecond=0)
    start = start_local.timestamp()
    end = (start_local + timedelta(hours=hours)).timestamp()
    return start, end


def in_session_first_hour(now, open_hour, tz_name, hours=1) -> bool:
    start, end = session_first_hour(now, open_hour, tz_name, hours)
    return start <= now < end


# --------------------------------------------------------------------------- agents
class PrevCandleBreakoutH1:
    """Previous (closed) H1 candle breakout. BUY when the reference price crosses the
    prior H1 high; SELL when it crosses the prior H1 low. Crossings are detected only
    from observed ticks, so a price already beyond the level at start does not fire."""
    strategy_id = 'PREV_CANDLE_BREAKOUT_H1'
    version = 'prev_candle_h1@1'
    timeframe = 'H1'
    DEFAULTS = dict(
        session_open_hour=9, session_tz='Asia/Kuala_Lumpur', session_hours=1,
        sl_buffer_points=50, max_sl_points=800, tp_points=600,
        max_gap_seconds=120, max_positions=1)

    def __init__(self, config: dict | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.config_id = config_id(self.config)

    def label(self):
        return 'Previous Candle Breakout H1'

    def evaluate(self, now, quote, candles, point, state: dict):
        """Return a signal dict or None. `state` persists per agent (ref price, fired level)."""
        c = self.config
        ref = (quote.bid + quote.ask) / 2.0
        prev_ref = state.get('ref_price')
        state['ref_price'] = ref
        # Only trade inside the first hour of the configured session.
        if not in_session_first_hour(now, c['session_open_hour'], c['session_tz'], c['session_hours']):
            state['session'] = None
            return None
        session = datetime.fromtimestamp(now, MYT).strftime('%Y-%m-%d') + f"@{c['session_open_hour']:02d}MYT"
        state['session'] = session
        if not candles:
            return None
        prev = candles[-1]  # most recent CLOSED H1 candle
        level_key = f"{session}:{int(prev.time)}"
        if state.get('fired') == level_key:
            return None  # one crossing per level/session (avoid repeat signals)
        if prev_ref is None:
            return None  # first observation: establish baseline, never enter on an already-broken level
        side = reason = None
        if prev_ref <= prev.high < ref:
            side, reason = 'BUY', f'Crossed prev H1 high {prev.high}'
        elif prev_ref >= prev.low > ref:
            side, reason = 'SELL', f'Crossed prev H1 low {prev.low}'
        if not side:
            return None
        entry = quote.ask if side == 'BUY' else quote.bid
        buf = c['sl_buffer_points'] * point
        if side == 'BUY':
            sl = prev.low - buf
            tp = entry + c['tp_points'] * point
        else:
            sl = prev.high + buf
            tp = entry - c['tp_points'] * point
        # Validate SL/TP direction and distance (points). Config values are not copied from a PDF pip example.
        risk = abs(entry - sl)
        max_sl = c['max_sl_points'] * point
        if risk <= 0 or risk > max_sl:
            return None
        if side == 'BUY' and not (sl < entry < tp):
            return None
        if side == 'SELL' and not (tp < entry < sl):
            return None
        state['fired'] = level_key
        h1 = 3600
        entry_candle_end = (int(now) // h1 + 1) * h1
        return dict(side=side, signal_reason=reason, signal_time=now, trigger_price=ref,
                    initial_sl=round(sl, quote.digits), initial_tp=round(tp, quote.digits),
                    session=session, timeframe=self.timeframe, entry_candle_end=float(entry_candle_end),
                    config_snapshot=dict(self.config))


# --------------------------------------------------------------------------- simulator
class Simulator:
    """Simulated fills and exits. BUY fills Ask / exits Bid; SELL fills Bid / exits Ask."""

    @staticmethod
    def open(agent, signal, quote, now):
        side = signal['side']
        entry = quote.ask if side == 'BUY' else quote.bid
        risk = abs(entry - signal['initial_sl'])
        tid = hashlib.sha1(f"{agent.id}:{signal['signal_time']}:{entry}".encode()).hexdigest()[:16]
        return dict(
            trade_id=tid, agent_id=agent.id, strategy_id=agent.agent.strategy_id,
            strategy_version=agent.agent.version, config_id=agent.agent.config_id,
            symbol=agent.symbol, timeframe=signal['timeframe'], session=signal.get('session'),
            side=side, signal_reason=signal['signal_reason'], signal_time=signal['signal_time'],
            trigger_price=signal['trigger_price'], entry_time=now, entry_price=entry,
            initial_sl=signal['initial_sl'], initial_tp=signal['initial_tp'],
            entry_spread=round(quote.ask - quote.bid, quote.digits), risk=risk,
            entry_candle_end=signal.get('entry_candle_end'), status='OPEN',
            data_quality_status=VALID, last_sample_epoch=now, last_price=entry,
            current_price=entry, unrealized_pnl=0.0, unrealized_r=0.0,
            config_snapshot=signal.get('config_snapshot', {}))

    @staticmethod
    def _close(pos, now, exit_price, exit_spread, reason, quality):
        buy = pos['side'] == 'BUY'
        price_pnl = (exit_price - pos['entry_price']) if buy else (pos['entry_price'] - exit_price)
        pos.update(status='CLOSED', exit_time=now, exit_price=exit_price, exit_spread=exit_spread,
                   exit_reason=reason, duration_seconds=round(now - pos['entry_time']),
                   price_pnl=price_pnl, pnl_r=(price_pnl / pos['risk']) if pos['risk'] else None,
                   data_quality_status=quality if quality != VALID else pos['data_quality_status'],
                   current_price=exit_price, unrealized_pnl=price_pnl)
        if pos['data_quality_status'] == VALID and quality != VALID:
            pos['data_quality_status'] = quality
        return pos

    @classmethod
    def monitor(cls, pos, quote, now, max_gap):
        """Advance one observed sample. Returns (pos, closed_bool)."""
        buy = pos['side'] == 'BUY'
        gap = (now - pos.get('last_sample_epoch', now)) > max_gap
        if gap and pos['data_quality_status'] == VALID:
            pos['data_quality_status'] = DATA_GAP
        exit_basis = quote.bid if buy else quote.ask
        spread = round(quote.ask - quote.bid, quote.digits)
        pos['last_sample_epoch'] = now
        pos['last_price'] = exit_basis
        pos['current_price'] = exit_basis
        pnl = (exit_basis - pos['entry_price']) if buy else (pos['entry_price'] - exit_basis)
        pos['unrealized_pnl'] = pnl
        pos['unrealized_r'] = (pnl / pos['risk']) if pos['risk'] else None
        sl, tp = pos['initial_sl'], pos['initial_tp']
        sl_hit = (exit_basis <= sl) if buy else (exit_basis >= sl)
        tp_hit = (exit_basis >= tp) if buy else (exit_basis <= tp)
        if sl_hit and tp_hit:
            # Both levels crossed within one sample -> path cannot be verified.
            return cls._close(pos, now, sl, spread, 'AMBIGUOUS_SL_TP', UNCERTAIN), True
        if sl_hit:
            q = UNCERTAIN if gap else VALID
            return cls._close(pos, now, sl, spread, 'SL', q), True
        if tp_hit:
            q = UNCERTAIN if gap else VALID
            return cls._close(pos, now, tp, spread, 'TP', q), True
        end = pos.get('entry_candle_end')
        if end and now >= end:
            return cls._close(pos, now, exit_basis, spread, 'CANDLE_END', pos['data_quality_status']), True
        return pos, False


# --------------------------------------------------------------------------- stats
def compute_stats(trades: list[dict]) -> dict:
    valid = [t for t in trades if t.get('data_quality_status') == VALID]
    wins = sum(1 for t in valid if (t.get('pnl_r') or 0) > 0)
    affected = sum(1 for t in trades if t.get('data_quality_status') != VALID)
    net_r = sum((t.get('pnl_r') or 0) for t in valid)
    return dict(total_trades=len(trades), valid_trades=len(valid), wins=wins,
                losses=len(valid) - wins, win_rate=(wins / len(valid)) if valid else None,
                net_r=round(net_r, 4), affected_by_data=affected)


# --------------------------------------------------------------------------- live agent instance
class LiveAgent:
    """One running instance: strategy + symbol + config, with its own state/positions/stats."""

    def __init__(self, agent, symbol, status='PAUSED'):
        self.agent = agent
        self.symbol = symbol
        self.base_symbol = symbol
        self.status = status  # RUNNING | PAUSED
        self.id = f"{agent.strategy_id}|{symbol}|{agent.config_id}"
        self.state = {}
        self.open_positions = []
        self.last_tick_epoch = None
        self.last_signal = None
        self.feed_status = 'WAITING'


# --------------------------------------------------------------------------- engine
SCHEMA = '''
CREATE TABLE IF NOT EXISTS live_test_agents(
 id TEXT PRIMARY KEY, strategy_id TEXT, symbol TEXT, config_id TEXT,
 config TEXT, status TEXT, state TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS live_test_positions(
 trade_id TEXT PRIMARY KEY, agent_id TEXT, status TEXT, payload TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS live_test_trades(
 trade_id TEXT PRIMARY KEY, agent_id TEXT, closed_epoch REAL, payload TEXT);
CREATE INDEX IF NOT EXISTS live_test_trade_closed ON live_test_trades(closed_epoch DESC);
'''


class LiveTestEngine:
    """Registry + per-cycle runner. Pure simulation over a ReadOnlyMarketData adapter."""

    def __init__(self, store, max_gap_default=120):
        self.store = store
        self.max_gap_default = max_gap_default
        self.agents: dict[str, LiveAgent] = {}
        with store.connect() as db:
            db.executescript(SCHEMA)
        self._load()

    # ---- registry ----
    def register(self, agent, symbol, status='PAUSED') -> LiveAgent:
        la = LiveAgent(agent, symbol, status)
        self.agents[la.id] = la
        self._save_agent(la)
        return la

    def set_status(self, agent_id, status):
        la = self.agents.get(agent_id)
        if not la:
            raise KeyError(agent_id)
        if status not in ('RUNNING', 'PAUSED'):
            raise ValueError('status must be RUNNING or PAUSED')
        la.status = status
        self._save_agent(la)
        return la

    # ---- persistence ----
    def _save_agent(self, la):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO live_test_agents VALUES (?,?,?,?,?,?,?,?)',
                       (la.id, la.agent.strategy_id, la.base_symbol, la.agent.config_id,
                        json.dumps(la.agent.config), la.status,
                        json.dumps(dict(state=la.state, last_tick=la.last_tick_epoch,
                                        last_signal=la.last_signal, feed=la.feed_status)),
                        time.time()))

    def _save_position(self, pos):
        with self.store.connect() as db:
            if pos['status'] == 'CLOSED':
                db.execute('DELETE FROM live_test_positions WHERE trade_id=?', (pos['trade_id'],))
                db.execute('INSERT OR REPLACE INTO live_test_trades VALUES (?,?,?,?)',
                           (pos['trade_id'], pos['agent_id'], pos.get('exit_time'),
                            json.dumps(pos, allow_nan=False)))
            else:
                db.execute('INSERT OR REPLACE INTO live_test_positions VALUES (?,?,?,?,?)',
                           (pos['trade_id'], pos['agent_id'], pos['status'],
                            json.dumps(pos, allow_nan=False), time.time()))

    def _load(self):
        with self.store.connect() as db:
            for r in db.execute('SELECT * FROM live_test_agents'):
                cfg = json.loads(r['config'])
                agent = build_agent(r['strategy_id'], cfg)
                if agent is None:
                    continue
                la = LiveAgent(agent, r['symbol'], r['status'])
                meta = json.loads(r['state'] or '{}')
                la.state = meta.get('state', {})
                la.last_tick_epoch = meta.get('last_tick')
                la.last_signal = meta.get('last_signal')
                la.feed_status = meta.get('feed', 'WAITING')
                self.agents[la.id] = la
            for la in self.agents.values():
                rows = db.execute('SELECT payload FROM live_test_positions WHERE agent_id=?', (la.id,)).fetchall()
                la.open_positions = [json.loads(r['payload']) for r in rows]

    def _load_positions(self, la):
        with self.store.connect() as db:
            rows = db.execute('SELECT payload FROM live_test_positions WHERE agent_id=?', (la.id,)).fetchall()
        la.open_positions = [json.loads(r['payload']) for r in rows]

    def _sync(self):
        """Pick up start/pause and newly registered agents written by the dashboard server."""
        with self.store.connect() as db:
            rows = list(db.execute('SELECT * FROM live_test_agents'))
        seen = set()
        for r in rows:
            seen.add(r['id'])
            if r['id'] in self.agents:
                self.agents[r['id']].status = r['status']
            else:
                agent = build_agent(r['strategy_id'], json.loads(r['config']))
                if agent:
                    la = LiveAgent(agent, r['symbol'], r['status'])
                    self.agents[r['id']] = la
                    self._load_positions(la)
        for aid in list(self.agents):
            if aid not in seen:
                del self.agents[aid]

    # ---- per-cycle run ----
    def run(self, market, now=None):
        """Advance every RUNNING agent one sampled observation. `market` is ReadOnlyMarketData."""
        now = time.time() if now is None else now
        self._sync()
        touched = 0
        for la in list(self.agents.values()):
            if la.status != 'RUNNING':
                continue
            touched += 1
            self._run_agent(la, market, now)
        return touched

    def _run_agent(self, la, market, now):
        cfg = la.agent.config
        max_gap = cfg.get('max_gap_seconds', self.max_gap_default)
        symbol = market.resolve_symbol(la.base_symbol) if hasattr(market, 'resolve_symbol') else la.base_symbol
        if not symbol:
            la.feed_status = 'SYMBOL_UNAVAILABLE'
            self._save_agent(la)
            return
        la.symbol = symbol
        try:
            q = market.quote(symbol, now=now)
        except Exception as exc:
            la.feed_status = 'STALE_OR_CLOSED'
            la.state['_feed_note'] = str(exc)
            # Feed down: no new entries. Flag open positions as having a data gap.
            for pos in la.open_positions:
                if pos['data_quality_status'] == VALID:
                    pos['data_quality_status'] = DATA_GAP
                    self._save_position(pos)
            self._save_agent(la)
            return
        fresh = market.is_fresh(q) if hasattr(market, 'is_fresh') else True
        la.last_tick_epoch = q.time
        la.feed_status = 'LIVE' if fresh else 'STALE'
        # 1) monitor existing open positions
        still_open = []
        for pos in la.open_positions:
            updated, closed = Simulator.monitor(pos, q, now, max_gap)
            self._save_position(updated)
            if not closed:
                still_open.append(updated)
        la.open_positions = still_open
        # 2) look for a new entry (only when fresh and under the position cap)
        if fresh and len(la.open_positions) < cfg.get('max_positions', 1):
            try:
                candles = market.candles(symbol, la.agent.timeframe, 50, now=now)
                signal = la.agent.evaluate(now, q, candles, q.point, la.state)
            except Exception as exc:
                signal = None
                la.state['_eval_note'] = str(exc)
            if signal:
                pos = Simulator.open(la, signal, q, now)
                la.open_positions.append(pos)
                la.last_signal = dict(side=signal['side'], reason=signal['signal_reason'], epoch=now)
                self._save_position(pos)
        self._save_agent(la)

    # ---- reporting ----
    def trades(self, agent_id=None, limit=None):
        with self.store.connect() as db:
            if agent_id:
                rows = db.execute('SELECT payload FROM live_test_trades WHERE agent_id=? ORDER BY closed_epoch DESC',
                                  (agent_id,)).fetchall()
            else:
                rows = db.execute('SELECT payload FROM live_test_trades ORDER BY closed_epoch DESC').fetchall()
        out = [json.loads(r['payload']) for r in rows]
        return out[:limit] if limit else out

    def report(self, now=None):
        now = time.time() if now is None else now
        agents = []
        for la in self.agents.values():
            trades = self.trades(la.id)
            agents.append(dict(
                id=la.id, strategy_id=la.agent.strategy_id, label=la.agent.label(),
                symbol=la.symbol, base_symbol=la.base_symbol, config_id=la.agent.config_id,
                strategy_version=la.agent.version, status=la.status, feed_status=la.feed_status,
                last_tick_epoch=la.last_tick_epoch, last_tick_myt=myt_iso(la.last_tick_epoch),
                last_signal=la.last_signal, config=la.agent.config,
                open_positions=[self._public_pos(p, now) for p in la.open_positions],
                stats=compute_stats(trades)))
        recent = []
        for t in self.trades(limit=1000):
            recent.append(dict(t, closed_myt=myt_iso(t.get('exit_time')), entry_myt=myt_iso(t.get('entry_time'))))
        return dict(status='OK', server_epoch=now, simulation=True,
                    note='All entries, positions, exits and P/L are SIMULATED on live market data. '
                         'Read-only feed; no orders are sent to MT5.',
                    agents=agents, recent_trades=recent)

    @staticmethod
    def _public_pos(p, now):
        return dict(p, signal_time_myt=myt_iso(p.get('signal_time')), entry_time_myt=myt_iso(p.get('entry_time')),
                    age_seconds=round(now - p.get('entry_time', now)))

    # ---- CSV ----
    CSV_FIELDS = ['trade_id', 'strategy_id', 'strategy_version', 'config_id', 'symbol', 'timeframe',
                  'session', 'side', 'signal_reason', 'signal_time', 'trigger_price', 'entry_time',
                  'entry_price', 'initial_sl', 'initial_tp', 'entry_spread', 'exit_time', 'exit_price',
                  'exit_spread', 'exit_reason', 'duration_seconds', 'price_pnl', 'pnl_r',
                  'data_quality_status']

    def csv_rows(self, agent_id=None):
        rows = []
        for t in self.trades(agent_id):
            r = dict(t)
            for k in ('signal_time', 'entry_time', 'exit_time'):
                r[k] = iso_utc(r.get(k))
            rows.append({k: r.get(k, '') for k in self.CSV_FIELDS})
        return rows


# --------------------------------------------------------------------------- agent factory
def build_agent(strategy_id, config):
    if strategy_id == PrevCandleBreakoutH1.strategy_id:
        return PrevCandleBreakoutH1(config)
    if strategy_id == 'ORB_BREAKOUT_RETEST':
        try:
            from live_test_orb import ORBAgent
            return ORBAgent(config)
        except Exception:
            return None
    return None


def ensure_default_agents(engine, cfg=None):
    """Register the starting agents (paused) if they are not already present. XAUUSD first."""
    base = 'XAUUSD'
    defaults = [PrevCandleBreakoutH1()]
    orb = build_agent('ORB_BREAKOUT_RETEST', (cfg or {}).get('orb') or {})
    if orb is not None:
        defaults.append(orb)
    for agent in defaults:
        aid = f"{agent.strategy_id}|{base}|{agent.config_id}"
        if aid not in engine.agents:
            engine.register(agent, base, 'PAUSED')
