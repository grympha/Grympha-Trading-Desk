"""TrailFlip: tick-by-tick trailing stop-and-reverse, its Live Test tick mode and its tick backtest."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import quant_agent as qa
from dashboard_store import Store
from live_test import LiveTestEngine, VALID, DATA_GAP, build_agent, ensure_default_agents
from live_test_trailflip import TrailFlip
from market_data import Bar, Quote

T = 1_795_000_020 - (1_795_000_020 % 86400) + 13 * 3600       # 13:00 UTC on a weekday (London-NY hours)
D = 0.6
# The guide's settings: the rule tests below check the guide's mechanics with them.
GUIDE = dict(video_style=True, trail_atr=0.4, max_atr_frac=1.0, session_start_hour=None, daily_loss_r=8.0)


def ready(**kw):
    """State with fixed parameters (D = 0.6, step 0.072) so the rules can be checked by hand."""
    return dict(dict(ready=True, d=D, step=0.072, max_spread=1.0, max_flips=90, atr=1.5, er=0.5,
                     next_param=9e12, tick_t=0), **kw)


def run(agent, st, prices, t0=T, spread=0.0, pos=None):
    ticks = [(t0 + i, p - spread, p) for i, p in enumerate(prices)]      # price = ask
    return agent.run_ticks(st, pos, ticks, [], 0.01, 2, 'XAUUSD', 'TRAILFLIP|XAUUSD|x')


class Rules(unittest.TestCase):
    def test_guide_worked_example(self):
        a = TrailFlip({**GUIDE, 'slippage': 0.0})
        closed, pos = run(a, ready(), [3965.00, 3965.30, 3965.60, 3966.5, 3967.80, 3967.5, 3967.20, 3966, 3964.00, 3964.3, 3964.60])
        self.assertEqual([(t['side'], t['entry_price'], t['exit_price'], round(t['price_pnl'], 2), t['reversed']) for t in closed],
                         [('BUY', 3965.6, 3967.2, 1.6, True), ('SELL', 3967.2, 3964.6, 2.6, True)])
        self.assertEqual((pos['side'], pos['entry_price'], pos['stop'], pos['signal_reason']), ('BUY', 3964.6, 3964.0, 'FLIP'))
        self.assertEqual(closed[0]['exit_reason'], 'TRAIL_STOP')
        self.assertAlmostEqual(closed[0]['pnl_r'], 1.6 / 0.6)                # R = fill to initial level

    def test_unmoved_stop_is_sl_and_slippage_costs_both_sides(self):
        a = TrailFlip({**GUIDE, 'slippage': 0.05})
        closed, pos = run(a, ready(), [3965.00, 3965.60, 3965.20, 3964.90])
        t = closed[0]
        self.assertEqual((t['entry_price'], t['initial_sl'], t['exit_price'], t['exit_reason']), (3965.65, 3965.0, 3964.85, 'SL'))
        self.assertAlmostEqual(t['pnl_r'], -0.8 / 0.65)
        self.assertEqual((pos['side'], pos['entry_price']), ('SELL', 3964.85))     # reversed at the same fill

    def test_level_moves_only_by_a_full_step(self):
        a = TrailFlip({**GUIDE, 'slippage': 0.0})
        _, pos = run(a, ready(), [3965.00, 3965.60, 3965.65])                   # +0.05 < step 0.072
        self.assertEqual(pos['stop'], 3965.0)
        _, pos = run(a, ready(tick_t=0), [3965.00, 3965.60, 3965.70])
        self.assertEqual(pos['stop'], 3965.1)

    def test_gap_through_the_level_fills_at_the_gap_price(self):
        a = TrailFlip({**GUIDE, 'slippage': 0.0})
        closed, _ = run(a, ready(), [3965.00, 3965.60, 3963.00])
        self.assertEqual(closed[0]['exit_price'], 3963.0)

    def test_wide_spread_closes_without_reversing_then_rearms(self):
        a = TrailFlip({**GUIDE, 'slippage': 0.0})
        st = ready(max_spread=0.3)
        closed, pos = run(a, st, [3965.00, 3965.60])
        self.assertEqual(pos['side'], 'BUY')
        closed, pos = a.run_ticks(st, pos, [(T + 5, 3964.5, 3965.5)], [], 0.01, 2, 'XAUUSD', 'x')   # spread 1.0
        self.assertEqual((len(closed), closed[0]['reversed'], pos, st['blocked']), (1, False, None, 'WIDE_SPREAD'))
        a.run_ticks(st, None, [(T + 6, 3965.0, 3965.1)], [], 0.01, 2, 'XAUUSD', 'x')
        self.assertEqual((st['buy_stop'], st['sell_stop'], st['blocked']), (3965.7, 3964.4, None))

    def test_too_many_flips_means_exits_only(self):
        a = TrailFlip({**GUIDE, 'slippage': 0.0})
        st = ready(max_flips=2)
        closed, pos = run(a, st, [3965.00, 3965.60, 3965.00, 3965.60, 3965.00])       # entry, flip, then the limit
        self.assertEqual([t['reversed'] for t in closed], [True, False])
        self.assertIsNone(pos)
        self.assertEqual(st['blocked'], 'FLIP_COOLDOWN')
        self.assertGreater(st['cooldown_until'], T)

    def test_daily_loss_limit_stops_until_the_next_day(self):
        a = TrailFlip({**GUIDE, 'slippage': 0.0, 'daily_loss_r': 3.0})
        st = ready()
        prices = [3965.00] + [3965.60, 3965.00] * 10                              # every flip loses 1R
        closed, pos = run(a, st, prices)
        self.assertEqual(len(closed), 3)                                          # the third loss reaches -3R
        self.assertEqual((closed[-1]['reversed'], st['blocked']), (False, 'DAILY_LIMIT'))
        self.assertIsNone(pos)
        self.assertAlmostEqual(st['day_r'], -3.0)
        self.assertEqual(st['halted_day'], T // 86400)
        n = len(closed)
        closed, pos = run(a, st, [3970.0, 3971.0, 3972.0], t0=T + 3600)            # same day: nothing
        self.assertEqual((closed, pos), ([], None))
        closed, pos = run(a, st, [3970.0, 3970.0, 3970.7], t0=T + 86400)            # next day: trading again
        self.assertEqual(pos['side'], 'BUY')
        self.assertGreater(n, 2)

    def test_trend_filter_waits_for_trend_or_burst(self):
        a = TrailFlip({**GUIDE, 'slippage': 0.0, 'video_style': False})
        st = ready(er=0.1)
        closed, pos = run(a, st, [3965.00, 3965.05, 3965.00, 3965.05])
        self.assertEqual((pos, st['blocked']), (None, 'NO_TREND'))
        _, pos = run(a, st, [3965.0 + 0.15 * i for i in range(12)], t0=T + 100)      # straight burst
        self.assertEqual(pos['side'], 'BUY')

    def test_parameters_come_from_m1_atr_and_spread(self):
        a = TrailFlip({**GUIDE, 'slippage': 0.0})
        m1 = [Bar(T - 3600 + 60 * i, 100.0, 101.5, 100.0, 100.0 + (i % 2)) for i in range(40)]     # TR 1.5
        st = {}
        ticks = [(T + i, 100.0, 100.2) for i in range(40)]                         # spread 0.2, 1 sample/s
        a.run_ticks(st, None, ticks, m1, 0.01, 2, 'XAUUSD', 'x')
        a.run_ticks(st, None, [(T + 61, 100.0, 100.2)], m1, 0.01, 2, 'XAUUSD', 'x')
        self.assertTrue(st['ready'])
        self.assertAlmostEqual(st['atr'], 1.5)
        self.assertAlmostEqual(st['d'], 0.6)                                       # 0.4 x ATR, above 2 x spread
        self.assertAlmostEqual(st['step'], 0.072)
        self.assertAlmostEqual(st['max_spread'], 0.3)
        self.assertEqual(st['max_flips'], 75)
        desk = TrailFlip({'slippage': 0.0}); st = {}                               # desk default: 2 x ATR
        desk.run_ticks(st, None, ticks + [(T + 61, 100.0, 100.2)], m1, 0.01, 2, 'XAUUSD', 'x')
        self.assertAlmostEqual(st['d'], 3.0)


class DeskSettings(unittest.TestCase):
    """Version H: trend filter, 2 x ATR trail, London open to New York close, no daily stop."""
    DAY0 = 1_795_000_020 - (1_795_000_020 % 86400)                            # a November weekday, 00:00 UTC

    def test_defaults(self):
        c = TrailFlip().config
        self.assertEqual((c['video_style'], c['trail_atr'], c['max_atr_frac'], c['daily_loss_r']), (False, 2.0, 2.0, None))
        self.assertEqual(TrailFlip().label(), 'TrailFlip · trend filter 2×ATR · London–NY (tick)')

    def test_trades_only_from_london_open_to_new_york_close(self):
        a, st = TrailFlip(), {}
        at = lambda h, m=0: self.DAY0 + h * 3600 + m * 60                        # November: London = UTC, NY = UTC-5
        self.assertEqual([a._in_session(st, at(h, m)) for h, m in ((7, 59), (8, 0), (21, 59), (22, 0), (23, 30))],
                         [False, True, True, False, False])
        st = ready(er=0.5)
        _, pos = run(TrailFlip({'slippage': 0.0}), st, [3965.0, 3965.6, 3966.2], t0=at(7, 50))  # before London: nothing
        self.assertEqual((pos, st['blocked']), (None, 'OUT_OF_SESSION'))

    def test_summer_time_is_followed(self):
        july = 1_783_000_000 - (1_783_000_000 % 86400)                          # a July day: London UTC+1, NY UTC-4
        a = TrailFlip()
        self.assertEqual([a._in_session({}, july + h * 3600) for h in (6, 7, 20, 21)], [False, True, True, False])

    def test_open_trade_closes_at_new_york_close(self):
        a, st = TrailFlip({'slippage': 0.0}), ready(d=D)
        close = self.DAY0 + 22 * 3600
        _, pos = run(a, st, [3965.0, 3965.6, 3965.7], t0=close - 10)
        self.assertEqual(pos['side'], 'BUY')
        closed, pos = run(a, st, [3966.0], t0=close, pos=pos)
        self.assertEqual((closed[0]['exit_reason'], closed[0]['reversed'], pos), ('SESSION_END', False, None))

    def test_no_daily_stop(self):
        a, st = TrailFlip({'slippage': 0.0}), ready()
        closed, pos = run(a, st, [3965.00] + [3965.60, 3965.00] * 10)         # every flip loses 1R
        self.assertLess(st['day_r'], -15)
        self.assertNotIn('DAILY_LIMIT', [t['exit_reason'] for t in closed])
        self.assertIsNotNone(pos)                                               # still trading


class FakeMarket:
    """Read-only market with a scripted tick tape."""
    def __init__(self, tape):
        self.tape = tape
        self.m1 = [Bar(T - 7200 + 60 * i, 3960.0, 3961.5, 3960.0, 3960.5) for i in range(240)]

    def resolve_symbol(self, base):
        return base

    def quote(self, symbol, now=None, max_age=None):
        t, bid, ask = [x for x in self.tape if x[0] <= now][-1]
        return Quote(symbol, bid, ask, t, now - t, 0.01, 2)

    def is_fresh(self, q, max_age=None):
        return True

    def ticks(self, symbol, start, end):
        return [x for x in self.tape if start < x[0] <= end]

    def candles(self, symbol, tf, count, now=None):
        return [b for b in self.m1 if b.time + 60 <= now][-count:]


class TickMode(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'db.sqlite3')

    def tape(self):
        out, p = [], 3965.0
        for i in range(600):                                     # 10 minutes, a tick a second, waves of $3
            p += 0.25 if (i // 12) % 2 == 0 else -0.25
            out.append((T + i, round(p, 2), round(p + 0.2, 2)))
        return out

    def test_cycles_replay_every_tick_like_one_long_run(self):
        tape = self.tape()
        eng = LiveTestEngine(self.store)
        la = eng.register(build_agent('TRAILFLIP', GUIDE), 'XAUUSD', 'RUNNING')
        la.state['tick_t'] = T - 1
        for now in range(T + 15, T + 600, 15):                   # the bot's 15 s cycle
            eng.run(FakeMarket(tape), now=now)
        cycled = sorted(eng.trades(la.id), key=lambda t: t['entry_time'])
        one = build_agent('TRAILFLIP', GUIDE)
        closed, _ = one.run_ticks({'tick_t': T - 1}, None, [x for x in tape if x[0] <= T + 585], FakeMarket(tape).m1, 0.01, 2, 'XAUUSD', la.id)
        self.assertGreater(len(closed), 5)
        key = lambda t: (t['side'], t['entry_time'], t['entry_price'], t['exit_price'])
        self.assertEqual([key(t) for t in cycled], [key(t) for t in closed])
        rep = LiveTestEngine(self.store).report()
        a = next(x for x in rep['agents'] if x['id'] == la.id)
        self.assertIn(a['tick_state']['mode'], ('BUY', 'SELL'))
        self.assertEqual(len(a['open_positions']), 1)
        json.dumps(rep, allow_nan=False)

    def test_too_long_away_closes_the_trade_and_restarts_flat(self):
        tape = self.tape()
        eng = LiveTestEngine(self.store)
        la = eng.register(build_agent('TRAILFLIP', {**GUIDE, 'max_catchup_seconds': 120}), 'XAUUSD', 'RUNNING')
        la.state['tick_t'] = T + 100
        eng.run(FakeMarket(tape), now=T + 200)
        eng.run(FakeMarket(tape), now=T + 300)
        self.assertEqual(len(la.open_positions), 1)
        eng.run(FakeMarket(tape), now=T + 590)                   # 290 s later > 120 s catch-up
        gone = [t for t in eng.trades(la.id) if t['exit_reason'] == 'RESUMED_AFTER_GAP']
        self.assertEqual((len(gone), gone[0]['data_quality_status']), (1, DATA_GAP))

    def test_registered_next_to_running_gold_agents(self):
        eng = LiveTestEngine(self.store)
        eng.register(build_agent('ADVOCATE_EMA_PULLBACK', {}), 'XAUUSD', 'RUNNING')
        ensure_default_agents(eng, {'symbols': ['XAUUSD']})
        tf = [la for la in eng.agents.values() if la.agent.strategy_id == 'TRAILFLIP']
        self.assertEqual([(la.agent.config['video_style'], la.agent.config['trail_atr'], la.status) for la in tf],
                         [(False, 2.0, 'RUNNING')])

    def test_old_versions_are_retired_and_the_new_one_takes_over(self):
        eng = LiveTestEngine(self.store)
        old = [eng.register(build_agent('TRAILFLIP', dict(GUIDE, video_style=v)), 'XAUUSD', s)
               for v, s in ((True, 'RUNNING'), (False, 'PAUSED'))]
        la = old[0]; la.state['tick_t'] = T - 1
        eng.run(FakeMarket(self.tape()), now=T + 300)
        kept = len(eng.trades(la.id)); self.assertGreater(kept, 0)
        ensure_default_agents(eng, {'symbols': ['XAUUSD']})
        tf = [x for x in eng.agents.values() if x.agent.strategy_id == 'TRAILFLIP']
        self.assertEqual([(x.agent.config['trail_atr'], x.status) for x in tf], [(2.0, 'RUNNING')])   # took over the running one
        with self.store.connect() as db:
            st = dict(db.execute("SELECT id, status FROM live_test_agents WHERE strategy_id='TRAILFLIP'").fetchall())
        self.assertEqual(sorted(st.values()), ['REMOVED', 'REMOVED', 'RUNNING'])
        again = LiveTestEngine(self.store)                                     # a restart does not bring them back
        self.assertEqual([x.id for x in again.agents.values() if x.agent.strategy_id == 'TRAILFLIP'], [tf[0].id])
        self.assertEqual(len(again.trades(la.id)), kept)                        # their trades stay in the history
        ensure_default_agents(again, {'symbols': ['XAUUSD']})                   # idempotent
        self.assertEqual(sum(1 for x in again.agents.values() if x.agent.strategy_id == 'TRAILFLIP'), 1)

    def test_signal_agent_does_not_grade_trailflip(self):
        from signal_agent import SignalAgent
        eng = LiveTestEngine(self.store)
        la = eng.register(build_agent('TRAILFLIP', GUIDE), 'XAUUSD', 'RUNNING')
        la.state['tick_t'] = T - 1
        eng.run(FakeMarket(self.tape()), now=T + 300)
        self.assertTrue(eng.trades(la.id))
        self.assertEqual(SignalAgent(self.store).update(now=T + 301), [])


class FakeMT5:
    COPY_TICKS_INFO = 2
    TIMEFRAME_M1 = 1

    def __init__(self, tape, m1):
        self.tape, self.m1 = tape, m1

    def symbol_select(self, s, on):
        return True

    def symbol_info(self, s):
        return SimpleNamespace(point=0.01, digits=2, trade_tick_size=0.01)

    def copy_ticks_range(self, s, a, b, flags):
        rows = [(int(t * 1000), bid, ask) for t, bid, ask in self.tape if a <= t < b]
        return np.array(rows, dtype=[('time_msc', 'i8'), ('bid', 'f8'), ('ask', 'f8')])

    def copy_rates_range(self, s, tf, a, b):
        rows = [(x.time, x.open, x.high, x.low, x.close, 20) for x in self.m1 if a.timestamp() <= x.time <= b.timestamp()]
        return np.array(rows, dtype=[('time', 'i8'), ('open', 'f8'), ('high', 'f8'), ('low', 'f8'), ('close', 'f8'), ('spread', 'i8')])


class TickBacktest(unittest.TestCase):
    def test_quant_agent_replays_ticks_through_the_same_rules(self):
        tape = TickMode.tape(None)
        m1 = FakeMarket(tape).m1
        agent = build_agent('TRAILFLIP', GUIDE)
        results = qa.run([(agent, 'XAUUSD')], T, T + 600, {}, FakeMT5(tape, m1), log=lambda m: None)
        live, _ = build_agent('TRAILFLIP', GUIDE).run_ticks({}, None, [x for x in tape if x[0] < T + 600], m1, 0.01, 2, 'XAUUSD', 'x')
        self.assertEqual(len(results), 1)
        self.assertEqual([(t['entry_time'], t['exit_price']) for t in results[0]['trades']],
                         [(t['entry_time'], t['exit_price']) for t in live])
        self.assertGreater(results[0]['stats']['trades'], 5)


if __name__ == '__main__':
    unittest.main()
