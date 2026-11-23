"""Module 4: dashboard state, journal persistence and notifications.
Records candidates before handing them to the sole executor; never sends MT5 orders.
"""
import json
import logging
import time
from pathlib import Path

class DashboardJournal:
    def __init__(self,mt5,store,cfg,args,runner,board,*,quote_age):
        self.mt5,self.store,self.cfg,self.args=mt5,store,cfg,args
        self.submission_errors={}
        self.runner,self.board,self.quote_age=runner,board,quote_age
        self.executor=None
        self.lab=None
        if store is not None:
            try:
                from replay_worker import ResearchLab
                self.lab=ResearchLab(store,cfg,mt5,getattr(args,'config',Path(__file__).with_name('config.json')))
            except Exception:logging.warning('Replay lab unavailable; no execution changes')
        self.health=None
        if store is not None:
            try:
                from portfolio_guard import initialise as initialise_portfolio
                initialise_portfolio(store,cfg)
                from runtime_health import HealthMonitor
                self.health=HealthMonitor(store,cfg)
            except Exception:logging.warning('Module health monitor unavailable')
        self.reviewer=None
        self.shadow=None
        if store is not None and cfg.get('ai_shadow',{}).get('enabled',True):
            try:
                from ai_shadow import ShadowScorer
                self.shadow=ShadowScorer(store,cfg,mt5)
            except Exception:
                logging.warning('AI shadow unavailable; order rules unchanged')
        if store is not None:
            try:
                from trade_review import TradeReviewer
                self.reviewer=TradeReviewer(store,cfg,mt5)
            except Exception:
                logging.warning('Trade Review unavailable; execution rules unchanged')

    def publish(self,result):
        from pair_controls import disabled_pairs, enabled
        if not enabled(disabled_pairs(self.store), result.get('strategy','BBMA'), result['symbol']):
            return
        mt5,cfg,store,executor=self.mt5,self.cfg,self.store,self.executor
        args=self.args
        if store is not None:
            try:
                plan=result.get('entry_plan')
                if plan and result.get('health')=='OK':
                    try:
                        from risk_check import preview
                        result['risk_preview']=preview(mt5,store,cfg,result,getattr(args,'demo_lot',.01))
                    except Exception:
                        result['risk_preview']={'error':'Risk estimate unavailable: verify account equity, broker profit calculation and lot limits.'}
                if plan and result.get('health')=='OK':
                    try:
                        from trade_review import capture
                        from trade_controls import controls
                        result['review_capture']=capture(result,cfg,controls(store,cfg))
                    except Exception:
                        logging.warning('Pre-entry research capture unavailable')
                new_candidate=None
                if self.shadow:
                    try:new_candidate=self.shadow.capture(result)
                    except Exception:
                        logging.warning('AI shadow capture unavailable; no trading decision changed')
                if self.lab and new_candidate:
                    try:self.lab.enrol(new_candidate)
                    except Exception:logging.warning('Forward replay enrolment unavailable')
                store.record(result)
            except Exception as exc:
                logging.warning('Dashboard update failed: %s', exc)
                return
            if executor:
                try: executor.submit(result)
                except Exception as exc:
                    logging.warning('DEMO submission blocked: %s', exc)
                    self.submission_errors[result.get('strategy','BBMA')]=str(exc)

    def health_event(self,name,status,note=''):
        if self.health:
            try:self.health.set(name,status,note)
            except Exception:logging.warning('Module health publication unavailable')

    def start_monitor(self):
        if self.health:
            try:self.health.start_watchdog()
            except Exception:logging.warning('External watchdog unavailable; inspect Windows process restrictions')

    def refresh_sources(self,feed):
        cfg,store,mt5=self.cfg,self.store,self.mt5
        self.health_event('Dashboard + Journal','RUNNING','Refreshing calendar/account and account-wide stop risk')
        feed.refresh()
        if store:
            with store.connect() as db:
                db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('news_filter',json.dumps(feed.status(cfg['symbols']))))
        if store:
            from account_status import sample
            account=sample(mt5)
            with store.connect() as db:
                db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('mt5_account',json.dumps(account,allow_nan=False)))
            try:
                from equity_history import record as record_equity
                record_equity(store,mt5,account)
            except Exception:logging.warning('Equity history sample unavailable; trading unaffected')

        if store:
            try:
                from portfolio_guard import refresh,settings,evaluate
                refresh(mt5,store,cfg)
                with store.connect() as db:r=db.execute("SELECT value FROM meta WHERE key='portfolio_snapshot'").fetchone()
                report=json.loads(r['value'])
                from market_hours import status as market_state
                mode=('Enforcing' if settings(store,cfg)['enabled'] else 'Monitor only')
                stale_only=report['status']=='UNKNOWN' and report.get('unknown') and all('Stale position quote' in u for u in report['unknown'])
                if evaluate(report,settings(store,cfg))['blocked']:self.health_event('Risk Check','BLOCKED',mode+'; account-wide stop-risk estimates')
                elif stale_only and market_state()[0]!='open':self.health_event('Risk Check','MARKET CLOSED',mode+'; prices are from before the close, risk is recalculated when the market reopens')
                else:self.health_event('Risk Check','UNKNOWN' if report['status']=='UNKNOWN' else 'HEALTHY',mode+'; account-wide stop-risk estimates')
            except Exception:self.health_event('Risk Check','UNAVAILABLE','Portfolio snapshot unavailable')

    def refresh(self):
        store,cfg,mt5=self.store,self.cfg,self.mt5
        journal_errors=[];research_errors=[]
        confluence_runner,board,quote_age=self.runner,self.board,self.quote_age
        if store:
            # Quotes are sampled each main loop, including between Confluence analyses.
            watch = set(cfg['symbols'])
            if confluence_runner and confluence_runner.c['enabled']:
                watch.update(confluence_runner.c['symbols'])
            for observed_symbol in watch:
                try:
                    tick, info = mt5.symbol_info_tick(observed_symbol), mt5.symbol_info(observed_symbol)
                    qnow = time.time()
                    if tick is None or info is None or tick.bid <= 0 or tick.ask < tick.bid:
                        continue
                    qage = quote_age(qnow, tick.time, cfg.get('tick_timestamp_offset_hours',0))
                    if not -5 <= qage <= cfg['max_tick_age_seconds']: continue
                    store.observe(observed_symbol,dict(bid=float(tick.bid),ask=float(tick.ask),
                                                     point=float(info.point),digits=int(info.digits)),now=qnow)
                except Exception:
                    logging.warning('%s tracking quote update unavailable', observed_symbol)
            # Read-only broker positions; no inferred strategy attribution or order closes.
            try:
                terminal=mt5.terminal_info()
                if terminal is None or not terminal.connected:raise RuntimeError('MT5 disconnected')
                actual = mt5.positions_get()
                if actual is None: raise RuntimeError('MT5 positions unavailable')
                account = mt5.account_info()
                links={int(d['position_ticket']):d for d in store.snapshot().get('demo_orders',[]) if d.get('position_ticket')}
                positions = [dict(ticket=int(p.ticket),symbol=p.symbol,side='BUY' if p.type==0 else 'SELL',
                                  volume=float(p.volume),entry=float(p.price_open),current=float(p.price_current),
                                  stop_loss=float(p.sl),take_profit=float(p.tp),profit=float(p.profit),swap=float(p.swap),
                                  currency=getattr(account,'currency',''),attribution=links.get(int(p.ticket),{}).get('strategy','UNASSIGNED'),
                                  setup_key=links.get(int(p.ticket),{}).get('setup_key'),
                                  decision=links.get(int(p.ticket),{}).get('decision'))
                             for p in actual if p.symbol in watch]
                store.positions(positions)
            except Exception:
                store.positions([],error='Read-only MT5 positions unavailable; no account position state is inferred.')
                journal_errors.append('Positions unavailable')
            try:
                from daily_history import refresh as refresh_daily
                refresh_daily(mt5,store,cfg)
            except Exception:
                logging.warning('Daily broker history unavailable; no P/L inferred')
                journal_errors.append('Daily history unavailable')
                with store.connect() as db:
                    db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('daily_scope','null'))
            if board:
                for key in board.update():
                    logging.warning('Telegram tracked-message update failed for %s; no duplicate sent', key)

            if self.reviewer:
                try:self.reviewer.refresh()
                except Exception:
                    logging.warning('Trade Review refresh unavailable; no execution changes')
                    research_errors.append('Trade Review unavailable')

            if self.shadow:
                try:self.shadow.refresh()
                except Exception:
                    logging.warning('AI shadow refresh unavailable; order rules unchanged')
                    research_errors.append('AI shadow unavailable')

            if self.lab:
                try:self.lab.refresh()
                except Exception:
                    research_errors.append('Replay lab unavailable')
                    logging.warning('Replay lab refresh unavailable; no execution changes')

            self.health_event('Dashboard + Journal','DEGRADED' if journal_errors else 'HEALTHY','; '.join(journal_errors) or 'Journal refresh completed')
            self.health_event('Research','DEGRADED' if research_errors else 'OBSERVING' if self.shadow or self.reviewer else 'DISABLED','; '.join(research_errors) or 'Research has no order authority')

    def close(self,error=None):
        try:
            if self.shadow:self.shadow.close()
        finally:
            try:
                if self.lab:self.lab.close()
            finally:
                if self.health:self.health.close(error=error)
