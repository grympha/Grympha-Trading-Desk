"""BBMA analysis and paper tracking by default; opt-in guarded DEMO execution. Python 3.11+."""
from __future__ import annotations
import sys
import argparse
import json
import logging
import math
import os
from pathlib import Path
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent


def indicators(raw):
    d = raw.copy().sort_values('time').reset_index(drop=True)
    if len(d) < 100 or d.time.duplicated().any():
        raise ValueError('Need >=100 distinct chronological bars')
    for col in ('open', 'high', 'low', 'close'):
        if not np.isfinite(d[col]).all():
            raise ValueError('Non-finite OHLC')
    if ((d.high < d[['open', 'close', 'low']].max(axis=1)) |
            (d.low > d[['open', 'close', 'high']].min(axis=1))).any():
        raise ValueError('Invalid OHLC')
    d['mid'] = d.close.rolling(20).mean()
    sd = d.close.rolling(20).std(ddof=0)
    d['upper'], d['lower'] = d.mid + 2 * sd, d.mid - 2 * sd
    for n in (5, 10):
        # Linear-weighted MA (weights 1..n, newest heaviest): same values as
        # rolling(n).apply(dot(x, 1..n) / sum), computed with one vectorised dot per window.
        weights = np.arange(1, n + 1, dtype=float)
        for col in ('high', 'low'):
            values = d[col].to_numpy(dtype=float)
            out = np.full(len(values), np.nan)
            if len(values) >= n:
                windows = np.lib.stride_tricks.sliding_window_view(values, n)
                out[n - 1:] = windows @ weights / weights.sum()
            d[f'ma{n}_{col}'] = out
    # SMA-seeded EMA, rather than a first-close seed (plain numpy loop; same recursion as before).
    close = d.close.to_numpy(dtype=float)
    ema = np.full(len(d), np.nan)
    ema[49] = close[:50].mean()
    alpha = 2 / 51
    for i in range(50, len(d)):
        ema[i] = ema[i-1] + alpha * (close[i] - ema[i-1])
    d['ema50'] = ema
    tr = pd.concat([d.high-d.low, (d.high-d.close.shift()).abs(),
                    (d.low-d.close.shift()).abs()], axis=1).max(axis=1)
    d['atr'] = tr.rolling(14).mean()  # Explicit simple ATR, not Wilder ATR.
    return d


def directional(r, side):
    if side == 'BUY':
        return r.close > max(r.ma5_high, r.ma10_high, r.mid)
    return r.close < min(r.ma5_low, r.ma10_low, r.mid)


def momentum(r, side):
    return bool(r.close > r.upper if side == 'BUY' else r.close < r.lower)


def condition(h, cfg):
    r, p = h.iloc[-1], h.iloc[-4]
    width = (r.upper-r.lower) / r.atr if r.atr > 0 else 0
    if width < cfg['min_bb_width_atr']:
        return {'name': 'RANGE_OR_SQUEEZE', 'side': None}
    if r.close > r.ema50 and r.mid > r.ema50 and r.ema50 > p.ema50:
        side = 'BUY'
    elif r.close < r.ema50 and r.mid < r.ema50 and r.ema50 < p.ema50:
        side = 'SELL'
    else:
        return {'name': 'TRANSITION', 'side': None}
    return {'name': 'BULLISH' if side == 'BUY' else 'BEARISH', 'side': side,
            'momentum': momentum(r, side), 'closed_bar_utc': stamp(r.time)}


def rejection(r, side, tolerance):
    if side == 'BUY':
        lo, hi = sorted([r.ma5_low, r.ma10_low])
        return (r.low <= hi+tolerance and r.high >= lo-tolerance
                and r.close > hi and r.close > r.open and r.close > r.mid)
    lo, hi = sorted([r.ma5_high, r.ma10_high])
    return (r.high >= lo-tolerance and r.low <= hi+tolerance
            and r.close < lo and r.close < r.open and r.close < r.mid)


def find_setup(s, side, cfg):
    """Only latest closed M15 can create a setup; prior context cannot be future data."""
    r = s.iloc[-1]
    recent = s.iloc[-1-cfg['signal_lookback']:-1]
    tol = r.atr * cfg['touch_tolerance_atr']
    if rejection(r, side, tol):
        prior = [row for _, row in recent.iterrows() if directional(row, side)]
        if prior:
            kind = ('MOMENTUM_REENTRY' if any(momentum(x, side) for x in prior)
                    else 'CSA_REENTRY')
            allowed = cfg.get('_allowed_setups')  # chosen in dashboard Settings → Strategy setups
            if allowed is None or kind in allowed:
                return {'type': kind, 'side': side, 'high': float(r.high),
                        'low': float(r.low), 'bar_time': int(r.time)}
    if not cfg['enable_mhv']:
        return None
    # Optional strict MHV approximation: extreme -> reaction -> failed retest -> reversal.
    hist = s.iloc[-cfg['mhv_lookback']:-2]
    for idx in reversed(hist.index.tolist()):
        e = s.loc[idx]
        if side == 'SELL':
            extreme = e.ma5_high > e.upper and not momentum(e, 'BUY')
        else:
            extreme = e.ma5_low < e.lower and not momentum(e, 'SELL')
        if not extreme:
            continue
        after = s.loc[idx+1:s.index[-2]]
        if len(after) < 2:
            continue
        retest = after.iloc[-1]
        if side == 'SELL':
            valid = (after.low.min() < e.low and retest.high >= e.high-tol
                     and retest.close <= retest.upper and retest.high <= e.high+tol
                     and r.close < r.open and r.close < retest.low)
        else:
            valid = (after.high.max() > e.high and retest.low <= e.low+tol
                     and retest.close >= retest.lower and retest.low >= e.low-tol
                     and r.close > r.open and r.close > retest.high)
        if valid:
            return {'type': 'MHV_REVERSAL', 'side': side,
                    'high': float(max(after.high.max(), e.high, r.high)),
                    'low': float(min(after.low.min(), e.low, r.low)),
                    'bar_time': int(r.time)}
    return None


def stamp(epoch):
    return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat()


def make_plan(setup, entry, bid, ask, point, digits, cfg):
    # M15 setup is known only at its close. M5 confirmation must OPEN at/after that close.
    closed_at = setup['bar_time'] + 900
    eligible = entry[(entry.time >= closed_at) &
                     (entry.time < closed_at + cfg['confirmation_bars'] * 300)]
    if eligible.empty:
        return None, 'Waiting for M5 confirmation after the M15 setup closes'
    side = setup['side']
    # First qualifying confirmation only; do not refresh an old plan on later bars.
    qualifying = eligible[(eligible.close > setup['high']) & (eligible.close > eligible.open)] if side == 'BUY' else eligible[(eligible.close < setup['low']) & (eligible.close < eligible.open)]
    if qualifying.empty:
        return None, 'Waiting for M5 close beyond setup boundary'
    r = qualifying.iloc[0]
    if int(r.time) != int(entry.iloc[-1].time):
        return None, 'Earlier confirmation already passed; no fresh entry'
    atr = float(r.atr)
    spread = ask-bid
    if not atr > 0 or spread < 0 or spread > cfg['max_spread_atr'] * atr:
        return None, 'Spread/volatility filter blocked entry'
    buffer = max(point*cfg['buffer_points'], cfg['buffer_atr']*atr)
    ticksize = cfg.get('_tick_size', point)
    def up(x): return round(math.ceil(x/ticksize)*ticksize, digits)
    def down(x): return round(math.floor(x/ticksize)*ticksize, digits)
    if side == 'BUY':
        trigger = up(r.high+spread+buffer)
        stop = down(setup['low']-buffer)
        if ask >= trigger:
            return None, 'Entry already crossed: do not chase'
        risk = trigger-stop
    else:
        trigger = down(r.low-buffer)
        stop = up(setup['high']+spread+buffer)
        if bid <= trigger:
            return None, 'Entry already crossed: do not chase'
        risk = stop-trigger
    if risk <= 0 or risk > cfg['max_stop_atr']*atr:
        return None, 'Stop distance filter blocked entry'
    sign = 1 if side == 'BUY' else -1
    key = f"{setup['type']}:{setup['bar_time']}:{int(r.time)}:{side}"
    return {'id': key, 'side': side, 'order_idea': side+'_STOP',
            'entry': trigger, 'stop_loss': stop,
            'tp1': round(trigger+sign*risk, digits),
            'tp2': round(trigger+sign*2*risk, digits),
            'risk_price_distance': round(risk, digits),
            'setup': setup['type'], 'confirmation_utc': stamp(r.time+300),
            'expires_utc': stamp(r.time+300+cfg['entry_expiry_minutes']*60),
            'status': 'PENDING_ENTRY_IDEA_NOT_EXECUTED'}, 'Confirmed'


def assess(h, s, e, bid, ask, point, digits, cfg):
    c = condition(h, cfg)
    result = {'condition': c, 'setup': None, 'entry_plan': None}
    if not c['side']:
        result['reason'] = 'No directional market condition; no entry'
        return result
    # Use only M15 setups with a still-open confirmation window, newest first.
    latest_close = int(e.iloc[-1].time)+300
    earliest = latest_close-cfg['confirmation_bars']*300-900
    for idx in reversed(s.index[s.time >= earliest].tolist()):
        candidate = s.loc[:idx]
        # H1 context must have existed when this M15 setup closed.
        historical_h = h[h.time+3600 <= int(candidate.iloc[-1].time)+900]
        if len(historical_h) < 55:
            continue
        historical_c = condition(historical_h, cfg)
        if historical_c['side'] != c['side']:
            continue
        setup = find_setup(candidate, c['side'], cfg)
        if setup:
            result['setup'] = setup
            result['entry_plan'], result['reason'] = make_plan(
                setup, e, bid, ask, point, digits, cfg)
            if result['entry_plan'] and cfg.get('_bbma_tp_mode') == 'structural':
                from structural_tp import target_plan
                result['entry_plan'] = target_plan(result['entry_plan'],s.iloc[-1],cfg,digits,point)
                if not result['entry_plan']:
                    result['reason'] = 'Structural TP unavailable: no valid M15 target beyond entry.'
            return result
    result['reason'] = 'Directional condition found; waiting for M15 setup'
    return result


def telegram(message):
    import telegram_config
    creds = telegram_config.active()  # launcher settings in .env (enable switch + token + chat id)
    if not creds:
        return False
    token, chat = creds
    body = json.dumps({'chat_id': chat, 'text': message}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage',
                                 body, {'Content-Type': 'application/json'})
    # Never print exception URLs: they contain the token.
    with urllib.request.urlopen(req, timeout=15) as response:
        if not json.load(response).get('ok'):
            raise RuntimeError('Telegram rejected message')
    return True


def load_state(path):
    return json.loads(path.read_text()) if path.exists() else {'delivered': []}


def save_state(path, state):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def quote_age(now, raw_time, offset_hours=0):
    """Offsets are explicit corrections for verified non-UTC feeds, never inferred."""
    return now - (raw_time - float(offset_hours)*3600)


def diagnose_time(mt5, cfg):
    terminal = mt5.terminal_info()
    account = mt5.account_info()
    print('PC local:', datetime.now().astimezone().isoformat())
    print('PC UTC:', stamp(time.time()))
    print('Terminal connected:', bool(terminal and terminal.connected))
    print('Terminal path:', getattr(terminal, 'path', 'unknown'))
    print('Broker server:', getattr(account, 'server', 'unknown'))
    print('Offsets (tick/bar hours):', cfg.get('tick_timestamp_offset_hours', 0),
          cfg.get('bar_timestamp_offset_hours', 0))
    print('Raw timestamps below are displayed AS UTC for comparison; no offset is assumed.')
    for symbol in cfg['symbols']:
        if not mt5.symbol_select(symbol, True):
            print(symbol, 'SYMBOL UNAVAILABLE', mt5.last_error())
            continue
        previous = None
        for sample in range(3):
            tick = mt5.symbol_info_tick(symbol)
            now = time.time()
            if tick is None:
                print(symbol, 'NO TICK', mt5.last_error())
                break
            identity = getattr(tick, 'time_msc', tick.time*1000)
            print(f'{symbol} sample={sample+1} pc_utc={stamp(now)} '
                  f'raw_tick_as_utc={stamp(tick.time)} '
                  f'raw_age_s={now-tick.time:.1f} '
                  f'adjusted_age_s={quote_age(now,tick.time,cfg.get("tick_timestamp_offset_hours",0)):.1f} '
                  f'bid={tick.bid} ask={tick.ask} '
                  f'timestamp_advanced={None if previous is None else identity > previous}')
            previous = identity
            if sample < 2:
                time.sleep(2)
        for name in ['H1', 'M15', 'M5']:
            raw = mt5.copy_rates_from_pos(symbol, getattr(mt5, 'TIMEFRAME_'+name), 1, 1)
            if raw is not None and len(raw):
                ts = int(raw[-1]['time'])
                print(f'{symbol} {name} last_closed_raw_as_utc={stamp(ts)} '
                      f'adjusted_open_age_s={quote_age(time.time(),ts,cfg.get("bar_timestamp_offset_hours",0)):.1f}')
    print('Do not infer an offset from an old stationary quote. Verify Windows time, '
          'terminal connection, moving quotes and broker timestamps before configuring offsets.')


def run(args):
    if args.diagnose_time or args.test_telegram or getattr(args,'demo_check',False) or getattr(args,'real_check',False):return _run(args)
    from runtime_health import RuntimeLock
    with RuntimeLock(ROOT/'bot.runtime.lock'):return _run(args)


def _run(args):
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT/'.env')
        import MetaTrader5 as mt5
    except ImportError:
        raise SystemExit('Install requirements.txt using Windows Python 3.11')
    if not Path(args.config).exists():
        raise SystemExit('Missing config.json. Copy config.example.json to config.json and configure your MT5 symbols/time offsets.')
    cfg = json.loads(Path(args.config).read_text())
    if not cfg.get('symbols') or cfg['poll_seconds'] < 1 or cfg['confirmation_bars'] < 1:
        raise SystemExit('Invalid symbols, poll interval, or confirmation window')
    import telegram_config
    _tg = telegram_config.read()
    if _tg['enabled'] and not _tg['configured']:
        logging.warning('Telegram is enabled but the token or chat ID is missing; alerts are off until set in the launcher')
    if args.test_telegram:
        try:
            ok = telegram('BBMA signal bot: Telegram test. No trade placed.')
        except Exception:
            raise SystemExit('Telegram test failed; check credentials/network')
        print('Telegram test delivered' if ok else 'Telegram credentials missing')
        return
    kw = {'path': cfg['terminal_path']} if cfg.get('terminal_path') else {}
    if not mt5.initialize(**kw):
        raise SystemExit(f'MT5 initialization failed: {mt5.last_error()}')
    if getattr(args,'real_check',False):
        from demo_execution import verify_execution_account
        from trade_controls import controls
        from portfolio_guard import settings
        from dashboard_store import Store
        try:
            a=verify_execution_account(mt5,cfg,'REAL')
            local_store=Store(ROOT/'dashboard.sqlite3')
            sizing=controls(local_store,cfg)
            risk=settings(local_store,cfg)
            print(json.dumps(dict(login=a.login,server=a.server,risk_enabled=sizing['risk_enabled'],
                                  risk_percent=sizing['risk_percent'],pair_lots=sizing['pair_lots'],
                                  portfolio=risk,position_cap=sizing['max_positions'])))
        except RuntimeError as exc:
            raise SystemExit(str(exc))
        finally:
            mt5.shutdown()
        return
    dashboard = None
    store = None
    confluence_runner = None
    orb_runner = None
    if not args.diagnose_time:
        from confluence_strategy import Runner
        confluence_runner = Runner(cfg, ROOT/'confluence_state.json')
        try:
            from orb_strategy import ORBRunner
            orb_runner = ORBRunner(cfg, ROOT/'orb_state.json')
        except Exception as exc:
            logging.warning('ORB agent disabled: %s', exc)
    if not args.diagnose_time:
        try:
            from dashboard_store import Store
            store = Store(ROOT/'dashboard.sqlite3')
            store.start(cfg)
            if args.dashboard:
                from dashboard_server import start_dashboard, remote_hosts
                remote = remote_hosts(cfg)
                dashboard = start_dashboard(store, args.dashboard_port, remote)
                print(f'Dashboard: http://127.0.0.1:{dashboard.server_port}', flush=True)
                for h in remote:
                    print(f'Dashboard (view only, via tailscale serve): https://{h}/home', flush=True)
        except Exception as exc:
            logging.warning('Dashboard unavailable: %s (analysis will continue)', exc)
    from news_filter import make_news_filter
    calendar = make_news_filter(cfg,mt5)
    executor = None
    board = None
    notifier = None
    if store:
        from tracking_telegram import Board
        board = Board(store)
    try:
        if args.diagnose_time:
            diagnose_time(mt5, cfg)
            return
        if getattr(args,'demo_execution',False) or getattr(args,'demo_check',False) or getattr(args,'real_execution',False):
            if store is None:raise SystemExit('Demo execution requires the local database; no order can be sent without it.')
            from demo_execution import DemoExecutor
            try:
                execution_mode='REAL' if getattr(args,'real_execution',False) else 'DEMO'
                executor = DemoExecutor(mt5,store,cfg,lot=getattr(args,'demo_lot',.01),execution_mode=execution_mode)
            except Exception as exc:raise SystemExit(str(exc))
            print(f'{executor.execution_mode} account verified | HEDGING | fixed-lot fallback {executor.lot:g} (risk controls may override) | cap {executor.max_positions}. Auto Early Exit follows each strategy setting.',flush=True)
            if getattr(args,'demo_check',False):
                print('Preflight only. No order sent. Start with --demo-execution only when ready for demo orders.',flush=True)
                return
        from scanner import BBMAScanner
        from confluence_scanner import ConfluenceScanner
        from orb_scanner import ORBScanner
        from pair_controls import STRATEGIES, disabled_pairs
        from structural_tp import mode as tp_mode
        from trade_controls import controls
        from dashboard_journal import DashboardJournal
        bbma_scanner=BBMAScanner(mt5,cfg,ROOT,assess=assess,indicators=indicators,quote_age=quote_age,stamp=stamp)
        confluence_scanner=ConfluenceScanner(mt5,ROOT,confluence_runner,stamp)
        orb_scanner=ORBScanner(mt5,ROOT,orb_runner,stamp)
        journal=DashboardJournal(mt5,store,cfg,args,confluence_runner,board,quote_age=quote_age)
        journal.executor=executor
        live_engine=None
        if store:
            try:
                from live_test import LiveTestEngine, ensure_default_agents
                live_engine=LiveTestEngine(store)
                ensure_default_agents(live_engine,cfg)
            except Exception as exc:
                logging.warning('Live Test engine unavailable: %s', exc)
        signal_agent=None
        if not args.once:journal.start_monitor()
        notifier=None
        if store and not args.once:
            try:
                from telegram_notify import Notifier
                notifier=Notifier(store,cfg,mode=executor.execution_mode if executor else 'ANALYSIS')
                notifier.lifecycle(f"▶ Grympha bot started · {notifier.mode} · {len(cfg['symbols'])} pairs · strategies BBMA, Confluence"+(', ORB' if orb_runner else ''))
            except Exception as exc:
                logging.warning('Telegram alerts unavailable: %s', exc)
        if live_engine:
            try:
                from signal_agent import SignalAgent, telegram_text
                from telegram_notify import load_prefs as telegram_prefs
                def signal_alert(feed):
                    if notifier and telegram_prefs(store).get('signal_agent'):notifier.push(telegram_text(feed))
                signal_agent=SignalAgent(store,alert=signal_alert)  # grades Live Test setups · never trades
            except Exception as exc:
                logging.warning('Signal Agent unavailable: %s', exc)
        calibration=None
        if signal_agent and not args.once:
            try:
                from signal_calibration import DailySnapshot
                calibration=DailySnapshot(store)  # one grade-check snapshot per Malaysia day · report only
            except Exception as exc:
                logging.warning('Signal calibration unavailable: %s', exc)
        scout=None
        if store and not args.once:
            try:
                from market_scout import MarketScout
                scout=MarketScout(store,cfg,mt5)  # read-only market report, a few symbols per cycle · never trades
            except Exception as exc:
                logging.warning('Market Scout unavailable: %s', exc)
        while True:
            journal.refresh_sources(executor.news if executor else calendar)
            journal.submission_errors.clear()
            disabled=disabled_pairs(store)
            from strategy_settings import load as load_strategy, bbma_overrides
            strategy_settings=load_strategy(store,cfg)
            if confluence_runner:
                confluence_runner.c['min_score']=strategy_settings['confluence']['min_score']
                confluence_runner.pair_scores=dict(strategy_settings['confluence'].get('pair_scores') or {})
            if orb_runner:orb_runner.apply(strategy_settings['orb'])
            def scan_bbma():
                selected_tp_mode=tp_mode(store)
                partial=bool(selected_tp_mode=='structural' and store and controls(store,cfg)['partial_enabled'])
                return bbma_scanner.scan(journal.publish,disabled,selected_tp_mode,partial,overrides=bbma_overrides(strategy_settings))
            scans={'BBMA':scan_bbma,'CONFLUENCE':lambda: confluence_scanner.scan(journal.publish,disabled),
                   'ORB':lambda: orb_scanner.scan(journal.publish,disabled)}
            for strategy,scan in scans.items():
                try:
                    stats=scan()
                    if stats is not None:
                        from market_hours import health_status
                        closed=stats.get('closed',0)
                        status=stats.get('status') or health_status(stats['checked'],stats['blocked'],closed)
                        journal.health_event(f'{strategy} Scanner',status,f"{stats['checked']} checks · {stats['blocked']} blocked"+(f' · {closed} market closed' if closed else ''))
                except Exception as exc:
                    logging.warning('%s scanner unavailable: %s',strategy,exc)
                    journal.health_event(f'{strategy} Scanner','UNAVAILABLE','Scanner cycle failed')
            if executor:
                try:
                    executor.refresh()
                    for strategy in STRATEGIES:
                        checked,unavailable=executor.monitor_counts.get(strategy,(0,0))
                        ready=executor.runtime_status=='ENABLED'
                        from market_hours import status as market_state
                        closed_now=market_state()[0]!='open'
                        status=('UNAVAILABLE' if not ready else ('MARKET CLOSED' if closed_now else 'DEGRADED') if unavailable else 'HEALTHY' if checked else 'IDLE')
                        journal.health_event(f'{strategy} Trade Monitor',status,
                            (f'{checked} open positions checked · {unavailable} waiting for prices (market closed; broker SL/TP still active)' if unavailable and closed_now
                             else f'{checked} open positions checked · {unavailable} data unavailable') if ready else executor.runtime_error)
                        error=journal.submission_errors.get(strategy)
                        duplicate=getattr(executor,'duplicate_block',False)
                        status=executor.runtime_status if not ready else 'PAUSED' if duplicate else 'DEGRADED' if error or getattr(executor,'audit_unavailable',False) else 'HEALTHY'
                        journal.health_event(f'{strategy} Executor',status,
                            executor.runtime_error or ('Another bot copy is trading this account · new entries paused until acknowledged' if duplicate else None) or error or ('Execution audit unavailable' if getattr(executor,'audit_unavailable',False) else f'{executor.execution_mode} reconciliation completed'))
                except Exception as exc:
                    logging.warning('DEMO broker reconciliation unavailable: %s', exc)
                    executor.publish_meta('UNAVAILABLE','Broker reconciliation unavailable; no duplicate request is sent.')
                    for strategy in STRATEGIES:
                        for stage in ('Executor','Trade Monitor'):
                            journal.health_event(f'{strategy} {stage}','UNAVAILABLE','Broker reconciliation failed')
            if live_engine:
                try:
                    if time.time()>=getattr(live_engine,'_next_settings_sync',0):
                        # live-strategy agents follow the dashboard settings; a change starts a new agent version
                        from live_test import sync_live_strategy_agents
                        live_engine._next_settings_sync=time.time()+30
                        changed=sync_live_strategy_agents(live_engine,cfg)
                        if changed:
                            print(f'Live Test: dashboard settings changed · {len(changed)} live-strategy agent versions registered/paused',flush=True)
                    from market_data import ReadOnlyMarketData
                    live_engine.run(ReadOnlyMarketData(mt5,cfg))  # SIMULATION ONLY · read-only feed, never the executor
                except Exception as exc:
                    logging.warning('Live Test cycle skipped: %s', exc)
                if executor:
                    # Agents switched on for live trading: their fresh simulated entries go to the
                    # executor (all the usual live checks); exits mirror the simulation in refresh().
                    try:
                        import agent_execution
                        n=agent_execution.step(executor)
                        if n:print(f'Live Test agents trading live: {n} new entr{"y" if n==1 else "ies"} handed to the executor',flush=True)
                    except Exception as exc:
                        logging.warning('Live Test agent execution skipped: %s', exc)
                if signal_agent:
                    try:signal_agent.update()
                    except Exception as exc:logging.warning('Signal Agent cycle skipped: %s', exc)
                if calibration:
                    try:calibration.step()
                    except Exception as exc:logging.warning('Signal calibration snapshot skipped: %s', exc)
            if scout:
                try:
                    report=scout.step()
                    if report:print(f"Market Scout: {len(report['rows'])} symbols · {len(report['recommendations'])} notes",flush=True)
                except Exception as exc:logging.warning('Market Scout step skipped: %s', exc)
            journal.refresh()
            if notifier:
                try:notifier.check()
                except Exception as exc:logging.warning('Telegram alert check skipped: %s', exc)
            if store and not args.once:
                try:
                    import db_backup
                    db_backup.start(store,ROOT,cfg)  # daily verified copy in backups/, runs in the background
                except Exception as exc:logging.warning('Database backup check skipped: %s', exc)
            if args.once:
                break
            time.sleep(cfg['poll_seconds'])
    finally:
        if 'journal' in locals():
            error_type=sys.exc_info()[0]
            try:journal.close(error=error_type.__name__ if error_type and error_type is not KeyboardInterrupt else None)
            except Exception:logging.warning('Shadow research worker cleanup unavailable')
        if executor:
            executor.publish_meta('STOPPED','No new order sends; existing demo positions and broker-side stops/expiry remain in MT5.')
        try:
            if notifier:
                notifier.lifecycle('⏹ Grympha bot stopped'+(f' ({error_type.__name__})' if error_type and error_type is not KeyboardInterrupt else '')+'. Open positions keep their broker SL/TP.')
                notifier.flush()
        except Exception:pass
        if store:
            store.stop()
        if dashboard:
            dashboard.shutdown()
            dashboard.server_close()
        mt5.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='BBMA + Confluence Dashboard v1.13 (counterfactual replay + versions)')
    parser.add_argument('--config', default=str(ROOT/'config.json'))
    parser.add_argument('--once', action='store_true')
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument('--real-execution', action='store_true', help='Opt in to orders on the REAL account pinned in config.json')
    execution.add_argument('--real-check', action='store_true', help='Check the pinned REAL account and print effective risk settings without sending orders')
    execution.add_argument('--demo-execution', action='store_true', help='OPT IN: place DEMO-only orders for new setups; rejects real/contest/netting accounts')
    execution.add_argument('--demo-check', action='store_true', help='Verify DEMO/hedging/permissions only; send no order')
    parser.add_argument('--demo-lot', type=float, default=.01, help='Fixed demo order volume; default 0.01; no automatic lot increase')
    parser.add_argument('--dashboard', action='store_true', help='Serve local read-only dashboard')
    parser.add_argument('--dashboard-port', type=int, default=8765)
    parser.add_argument('--diagnose-time', action='store_true', help='Show clock/feed diagnostics and exit; no alerts')
    parser.add_argument('--test-telegram', action='store_true')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    # Launcher requests graceful cleanup via CTRL_BREAK on Windows.
    import signal
    if hasattr(signal,'SIGBREAK'):
        def launcher_interrupt(signum,frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGBREAK,launcher_interrupt)
    try:
        run(parser.parse_args())
    except KeyboardInterrupt:
        print('Stopped')
