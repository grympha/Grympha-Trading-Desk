"""Opt-in MT5 DEMO order bridge with separately enabled trade management.
Actual requests are made only when the user launches --demo-execution locally.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import logging
import sqlite3
import time
from weekend_close import closing_time, close_position, describe, keeps_running

MAGIC={'BBMA':816001,'CONFLUENCE':816002,'ORB':816003}
BUSY={'PRECHECK','SENDING','UNCERTAIN','SUBMITTED','PENDING','OPEN','OPEN_PARTIAL','CANCEL_UNCERTAIN','REVIEW_UNKNOWN'}
SCHEMA='''CREATE TABLE IF NOT EXISTS demo_orders (
 key TEXT PRIMARY KEY,account_hash TEXT NOT NULL,setup_key TEXT NOT NULL,strategy TEXT NOT NULL,
 symbol TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,status TEXT NOT NULL,
 payload TEXT NOT NULL);
 CREATE UNIQUE INDEX IF NOT EXISTS demo_setup_account ON demo_orders(account_hash,setup_key);
 CREATE UNIQUE INDEX IF NOT EXISTS demo_active_pair ON demo_orders(account_hash,strategy,symbol)
 WHERE status IN ('PRECHECK','SENDING','UNCERTAIN','SUBMITTED','PENDING','OPEN','OPEN_PARTIAL','CANCEL_UNCERTAIN','REVIEW_UNKNOWN');
'''


def identity(account):
    return hashlib.sha256((str(account.login)+'|'+str(account.server)).encode()).hexdigest()


def public(row):
    d=json.loads(row['payload'])
    # No raw account id, token, broker login or unfiltered account info is exposed.
    return dict(d,key=row['key'],setup_key=row['setup_key'],strategy=row['strategy'],symbol=row['symbol'],
                status=row['status'],created=row['created'],updated=row['updated'])


def verify_execution_account(mt5, cfg, execution_mode):
    account, terminal = mt5.account_info(), mt5.terminal_info()
    if account is None or terminal is None or not getattr(terminal,'connected',False):
        raise RuntimeError('MT5 account unavailable or disconnected')
    expected = (getattr(mt5,'ACCOUNT_TRADE_MODE_REAL',2) if execution_mode=='REAL'
                else getattr(mt5,'ACCOUNT_TRADE_MODE_DEMO',0))
    if getattr(account,'trade_mode',None) != expected:
        raise RuntimeError(f'{execution_mode} execution requires a matching account type')
    if execution_mode=='REAL':
        target = cfg.get('real_execution', {})
        login, server = target.get('account_login'), target.get('account_server')
        if type(login) is not int or login <= 0 or not isinstance(server,str) or not server.strip():
            raise RuntimeError('Set real_execution.account_login and account_server in config.json')
        if (account.login,account.server) != (login,server):
            raise RuntimeError('Connected REAL account or broker server differs from config.json')
    if getattr(account,'margin_mode',None) != getattr(mt5,'ACCOUNT_MARGIN_MODE_RETAIL_HEDGING',2):
        raise RuntimeError(f'{execution_mode} execution requires a HEDGING account')
    if not (getattr(account,'trade_allowed',False) and getattr(account,'trade_expert',False)
            and getattr(terminal,'trade_allowed',False) and not getattr(terminal,'tradeapi_disabled',True)):
        raise RuntimeError('MT5 algorithmic trading permissions are unavailable')
    return account


class DemoExecutor:
    def __init__(self,mt5,store,cfg,lot=.01,*,execution_mode='DEMO'):
        if execution_mode not in ('DEMO','REAL'):
            raise ValueError('Invalid execution mode')
        self.execution_mode=execution_mode
        self.mt5,self.store,self.cfg=mt5,store,cfg
        self.lot=float(lot)
        if not math.isfinite(self.lot) or self.lot<=0:raise RuntimeError('Demo lot must be positive and finite')
        self.max_positions=int(cfg.get(execution_mode.lower()+'_execution',{}).get('max_positions',6))
        if not 1<=self.max_positions<=30:raise RuntimeError('max_positions must be between 1 and 30')
        from news_filter import make_news_filter
        self.news=make_news_filter(cfg,mt5)
        self.started=time.time();self.account_hash=None;self.session_block=None
        self.guard()
        from risk_check import RiskCheck
        self.risk_check=RiskCheck(mt5,store,cfg,self.lot,MAGIC,busy=BUSY)
        with store.connect() as db:db.executescript(SCHEMA)
        from execution_audit import Audit
        self.audit=Audit(store,self.account_hash)
        self.last_quote=None
        self.publish_meta('ENABLED')

    def guard(self):
        m=self.mt5;a=m.account_info()
        if self.session_block:raise RuntimeError(self.session_block)
        if a is not None and self.account_hash is not None and identity(a)!=self.account_hash:
            self.session_block='Execution stopped: account/server changed. Restart on the intended account.'
            raise RuntimeError(self.session_block)
        checked=verify_execution_account(m,self.cfg,self.execution_mode)
        account_hash=identity(checked)
        if self.account_hash is not None and account_hash!=self.account_hash:
            self.session_block='Execution stopped: account/server changed. Restart on the intended account.'
            raise RuntimeError(self.session_block)
        self.account_hash=account_hash
        return a

    def publish_meta(self,status,error=None):
        self.runtime_status,self.runtime_error=status,error
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('demo_scope',json.dumps(self.account_hash)))
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('demo_execution',json.dumps(dict(
                enabled=True,status=status,error=error,lot=self.lot,max_positions=self.max_positions,
                account_type=self.execution_mode,updated_epoch=time.time(),signal_closes='REVIEW_ONLY'))))

    def check_duplicates(self,positions,orders):
        """Another bot copy on this account? Pause NEW entries until acknowledged (never touches trades)."""
        try:
            from duplicate_guard import foreign_trades,acknowledged,publish,describe
            foreign=foreign_trades(positions,orders,self.rows(),MAGIC,acknowledged(self.store))
            publish(self.store,foreign)
            signature=tuple(sorted(t['ticket'] for t in foreign))
            if signature!=getattr(self,'_duplicate_signature',()):
                if foreign:print('WARNING '+describe(foreign),flush=True)
                elif getattr(self,'_duplicate_signature',()):print('Duplicate-bot check clear: new entries allowed again.',flush=True)
                self._duplicate_signature=signature
            self.duplicate_block=bool(foreign)
        except Exception as exc:
            print('Duplicate-bot check unavailable: '+str(exc)[:120],flush=True)

    def rows(self):
        with self.store.connect() as db:
            return list(db.execute('SELECT * FROM demo_orders WHERE account_hash=? ORDER BY created DESC',(self.account_hash,)))

    def _audit(self,event,signal=None,**payload):
        try:
            self.audit.emit(event,dict(signal) if signal is not None else None,**payload)
            self.audit_unavailable=False
        except Exception:
            self.audit_unavailable=True
            logging.warning('Execution audit unavailable; verify journal storage')

    def broker_check(self,request,signal,operation='ENTRY'):
        start=time.perf_counter()
        try:
            reply=self.mt5.order_check(request)
        except Exception:
            self._audit('ORDER_CHECK',signal,operation=operation,latency_ms=(time.perf_counter()-start)*1000,retcode=None,error='Broker check exception')
            raise
        self._audit('ORDER_CHECK',signal,operation=operation,latency_ms=(time.perf_counter()-start)*1000,retcode=getattr(reply,'retcode',None),request=request)
        return reply

    def broker_send(self,request,signal,operation='ENTRY'):
        self.guard()
        start=time.perf_counter()
        try:reply=self.mt5.order_send(request)
        except Exception:
            self._audit('ORDER_SEND',signal,operation=operation,latency_ms=(time.perf_counter()-start)*1000,retcode=None,error='Broker send exception',request=request,quote=self.last_quote)
            raise
        price=getattr(reply,'price',None);volume=getattr(reply,'volume',0)
        slip=None;points=None;rr=None
        try:
            if request.get('action')==self.mt5.TRADE_ACTION_DEAL and request.get('type') in (0,1) and price and volume:
                slip=(price-request['price'])*(1 if request['type']==0 else -1)
                info=self.mt5.symbol_info(request['symbol'])
                point=getattr(info,'point',0)
                points=slip/point if point else None
                risk=abs(request['price']-request.get('sl',request['price']))
                rr=slip/risk if risk else None
        except Exception:slip=points=rr=None
        self._audit('ORDER_SEND',signal,operation=operation,latency_ms=(time.perf_counter()-start)*1000,retcode=getattr(reply,'retcode',None),
                    response_price=price,response_volume=volume,slippage_points=points,slippage_r=rr,request=request,quote=self.last_quote)
        return reply

    def save(self,key,status,payload):
        with self.store.connect() as db:
            prior=db.execute('SELECT * FROM demo_orders WHERE key=?',(key,)).fetchone()
            db.execute('UPDATE demo_orders SET status=?,updated=?,payload=? WHERE key=?',
                       (status,time.time(),json.dumps(payload,allow_nan=False),key))
        if prior:
            old=json.loads(prior['payload'])
            if prior['status']!=status:self._audit('STATE',prior,previous=prior['status'],status=status,note=payload.get('note'))
            if payload.get('actual_entry') and payload.get('actual_entry')!=old.get('actual_entry'):
                request=payload.get('request') or {};sign=1 if payload.get('side')=='BUY' else -1
                slip=sign*(payload['actual_entry']-request['price']) if request.get('price') else None
                point=None
                try:point=getattr(self.mt5.symbol_info(prior['symbol']),'point',None)
                except Exception:pass
                risk=abs(request['price']-payload['sl']) if request.get('price') and payload.get('sl') else None
                self._audit('CONFIRMED_FILL',prior,actual_entry=payload['actual_entry'],requested_entry=request.get('price'),signed_price_slippage=slip,
                            slippage_points=slip/point if slip is not None and point else None,slippage_r=slip/risk if slip is not None and risk else None,position_ticket=payload.get('position_ticket'))
            for field,event in [('actual_sl','BROKER_SL_CHANGED'),('actual_tp','BROKER_TP_CHANGED'),('volume','BROKER_VOLUME_CHANGED')]:
                if field in payload and field in old and payload[field]!=old[field]:self._audit(event,prior,previous=old[field],current=payload[field])

    def reserve(self,signal,status,payload):
        key=self.account_hash+'|'+signal['key']
        now=time.time()
        with self.store.connect() as db:
            cursor=db.execute('INSERT OR IGNORE INTO demo_orders VALUES (?,?,?,?,?,?,?,?,?)',
                       (key,self.account_hash,signal['key'],signal['strategy'],signal['symbol'],now,now,status,json.dumps(payload)))
            self._inserted=cursor.rowcount==1
        return key

    def quote(self,symbol):
        info,tick=self.risk_check.quote(symbol)
        self.last_quote=dict(bid=float(tick.bid),ask=float(tick.ask),spread_points=(tick.ask-tick.bid)/info.point,broker_epoch=int(tick.time),captured_epoch=time.time())
        return info,tick

    def server_epoch(self,symbol):
        """MT5 server time now: the pair's latest tick time (the broker's own clock), else the configured offset."""
        try:
            tick=self.mt5.symbol_info_tick(symbol)
            if tick is not None and tick.time and abs(time.time()-(tick.time-float(self.cfg.get('tick_timestamp_offset_hours',0))*3600))<=300:
                return int(tick.time)
        except Exception:pass
        return int(time.time()+float(self.cfg.get('tick_timestamp_offset_hours',0))*3600)

    def request(self,signal):
        request=self.risk_check.request(signal,self.account_hash,quote=self.quote)
        self.last_risk=self.risk_check.last_risk
        return request

    def submit(self,result):
        """Called after analysis persistence; only a NEW tracked setup is eligible."""
        from pair_controls import disabled_pairs, enabled, entry_lock
        if not result.get('entry_plan') or result.get('health')!='OK':return
        if not enabled(disabled_pairs(self.store), result.get('strategy','BBMA'), result['symbol']):return
        if result.get('strategy')=='ORB':
            from strategy_settings import load as load_strategy
            if not load_strategy(self.store,self.cfg)['orb']['trade']:return  # ORB is analysis-only until allowed in Settings
        self.guard();now=time.time()
        snap=self.store.snapshot(now=now)
        signal=next((s for s in snap['signals'] if s['strategy']==result.get('strategy','BBMA') and s['symbol']==result['symbol'] and s.get('tracking') and s['lifecycle'] in ('PENDING','PAPER_RUNNING','PAPER_TP1')),None)
        if signal is None:return
        from strategy_execution import EXECUTORS
        policy=EXECUTORS[signal['strategy']]
        if not policy.matches(self.store,result,signal):
            return
        if any(r['setup_key']==signal['key'] for r in self.rows()):return
        base=dict(context=dict(review=result.get('review_capture'),condition=result.get('condition'),setup=signal.get('setup'),setup_structure=result.get('setup'),score=result.get('score'),factors=result.get('factors'),chart=result.get('chart',[]),chart_timeframe=result.get('chart_timeframe','M5'),captured_epoch=now),side=signal['side'],volume=self.lot,reference=signal['entry'],sl=signal['stop_loss'],tp1=signal['tp1'],tp2=signal['tp2'],
                  structural_tp=signal.get('structural_tp'),decision='WAIT_BROKER',order_ticket=None,deal_ticket=None,position_id=None,position_ticket=None,
                  note=f'{self.execution_mode} execution. Signal flags are review-only.',currency='',profit=None,realised=None)
        tracking=signal['tracking']
        if tracking.get('created_epoch',0)<self.started or tracking.get('continuity_gap') or result.get('new_analysis') is False:
            self.reserve(signal,'SKIPPED_OLD_SETUP',dict(base,note='Existing/cached/resumed setup is observation-only; no retroactive demo entry'))
            return
        all_rows=self.rows()
        if any(r['strategy']==signal['strategy'] and r['symbol']==signal['symbol'] and r['status'] in BUSY for r in all_rows):
            self.reserve(signal,'SKIPPED_BUSY',dict(base,note='This strategy/symbol already has an active or uncertain demo attempt'))
            return
        _controls=None
        try:
            from trade_controls import controls
            _controls=controls(self.store,self.cfg)
            self.max_positions=_controls['max_positions']  # dashboard setting, read per entry
        except Exception:pass  # keep the last valid cap
        if _controls is not None:
            from trade_controls import in_trade_window,server_minute
            windows=_controls.get('trade_windows') or {}
            if not in_trade_window(windows,signal['strategy'],server_minute(now,self.cfg.get('tick_timestamp_offset_hours') or 0)):
                win=windows.get(signal['strategy'],{})
                self.reserve(signal,'SKIPPED_WINDOW',dict(base,note=f"Outside the {signal['strategy']} trade window ({win.get('start','?')}–{win.get('end','?')} MT5 server time). No new entry is opened; open positions keep running."))
                return
        if closing_time(signal['symbol'],self.server_epoch(signal['symbol']),self.cfg):
            self.reserve(signal,'SKIPPED_WEEKEND',dict(base,note=describe(self.cfg)))
            return
        if getattr(self,'duplicate_block',False):
            self.reserve(signal,'BLOCKED',dict(base,note='Paused: another bot copy is trading this account (System Health). Acknowledge there to resume.'))
            return
        if self.risk_check.capacity_exceeded(all_rows,self.max_positions):
            self.reserve(signal,'SKIPPED_CAPACITY',dict(base,note=f'Open-position/pending-order cap reached ({self.max_positions}, all pairs incl. manual)'))
            return
        self.news.refresh()
        until=policy.news_until(signal,now)
        gate=self.news.check(signal['symbol'],until=until)
        base['news']=gate
        if gate['blocked']:
            self.reserve(signal,'BLOCKED_NEWS',dict(base,note=gate['note']));return
        try:req=self.request(dict(signal,score=result.get('score')))  # score labels Confluence comments
        except Exception as exc:
            self.reserve(signal,'BLOCKED',dict(base,note=str(exc)));return
        from portfolio_guard import check_entry,PortfolioBlocked
        try:base['portfolio_check']=check_entry(self,req)
        except PortfolioBlocked as exc:
            base['portfolio_check']=exc.report
            self.reserve(signal,'BLOCKED_PORTFOLIO',dict(base,note=str(exc)));self._audit('PORTFOLIO_BLOCK',signal,report=exc.report);return
        base.update(risk=self.last_risk,volume=req['volume'],request=req,tag=req['comment'],expires_utc=signal['expires_utc'],submitted_epoch=now)
        base['request_quote']=self.last_quote
        key=self.reserve(signal,'PRECHECK',base)
        if not self._inserted:return
        self._audit('ENTRY_REQUEST',signal,request=req,quote=self.last_quote,portfolio=base.get('portfolio_check'))
        self.guard()
        check=self.broker_check(req,signal)
        if check is None or getattr(check,'retcode',-1)!=0:
            self.save(key,'CHECK_REJECTED',dict(base,note=f'Broker precheck rejected: {getattr(check,"retcode","no response")}'));return
        gate=self.news.check(signal['symbol'],until=until)
        if gate['blocked']:
            self.save(key,'BLOCKED_NEWS',dict(base,news=gate,note=gate['note']));return
        # Reserve durably BEFORE the external request. Never auto-resend an ambiguous attempt.
        with entry_lock:
            if not enabled(disabled_pairs(self.store), signal['strategy'], signal['symbol']):
                self.save(key,'BLOCKED',dict(base,note='Pair is disabled for this strategy.'))
                return
            if not policy.matches(self.store,result,signal):
                self.save(key,'BLOCKED',dict(base,note='BBMA TP mode changed before order send.'))
                return
            self.guard()
            try:
                base['portfolio_check']=check_entry(self,req,exclude_key=key)
                self.quote(signal['symbol'])
                base['send_quote']=self.last_quote
            except Exception as exc:
                status='BLOCKED_PORTFOLIO' if isinstance(exc,PortfolioBlocked) else 'BLOCKED'
                if isinstance(exc,PortfolioBlocked):base['portfolio_check']=exc.report
                self.save(key,status,dict(base,note=str(exc)))
                self._audit('FINAL_ENTRY_BLOCK',signal,note=str(exc));return
            self.save(key,'SENDING',base)
            self.guard()
            try:reply=self.broker_send(req,signal)
            except Exception:
                self.save(key,'UNCERTAIN',dict(base,note='Order-send response unavailable. No automatic retry. Reconcile in MT5.'));return
        if reply is None:
            self.save(key,'UNCERTAIN',dict(base,note='Order-send returned no result. No automatic retry.'));return
        code=int(reply.retcode)
        base.update(retcode=code,order_ticket=int(getattr(reply,'order',0) or 0),deal_ticket=int(getattr(reply,'deal',0) or 0),
                    requested_volume=float(req['volume']),filled_volume=float(getattr(reply,'volume',0) or 0),
                    response_price=float(getattr(reply,'price',0) or 0))
        if code in (10008,10009,10010):
            base['partial']=code==10010
            self.save(key,'SUBMITTED',base)
        elif code in (10012,10031): # Timeout/connection ambiguity: never retry an accepted-but-unacknowledged operation.
            self.save(key,'UNCERTAIN',dict(base,note=f'Ambiguous broker response {code}; no automatic resend'))
        else:self.save(key,'REJECTED',dict(base,note=f'Broker rejected demo request: {code}'))

    def retire_tracking(self,setup_key,status,note):
        # Broker evidence ends the setup admission slot. Do not fabricate a paper exit price/P&L.
        with self.store.connect() as db:
            row=db.execute('SELECT payload FROM strategy_signals WHERE id=?',(setup_key,)).fetchone()
            if row:
                p=json.loads(row['payload'])
                if p.get('tracking'):
                    p['tracking'].update(decision=status,monitor='BROKER_FINAL',price_pnl=None,pnl_r=None,
                                         pnl_state='SEE_BROKER_LEDGER',close_reason=note)
                    db.execute('UPDATE strategy_signals SET status=?,note=?,updated=?,payload=? WHERE id=?',
                               (status,note,time.time(),json.dumps(p),setup_key))

    def cancel(self,row,p,order,reason):
        self.guard()
        if int(order.magic)!=MAGIC[row['strategy']] or order.symbol!=row['symbol']:raise RuntimeError('Pending-order ownership could not be verified')
        self.save(row['key'],'CANCEL_UNCERTAIN',dict(p,note=reason))
        self.guard()
        reply=self.broker_send(dict(action=self.mt5.TRADE_ACTION_REMOVE,order=int(order.ticket)),row,operation='CANCEL')
        if reply is not None and reply.retcode==10009:
            self.save(row['key'],'CANCELLED',dict(p,note=reason))
            self.retire_tracking(row['setup_key'],'DEMO_CANCELLED','Broker confirmed cancellation of the pending demo order; no fill assumed.')

    def refresh(self):
        """Reconcile native orders/deals/positions. No position is called filled from a quote."""
        try:self.guard()
        except Exception as exc:self.publish_meta('BLOCKED',str(exc));return
        self.news.refresh()
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('news_filter',json.dumps(self.news.status(self.cfg['symbols']))))
        m=self.mt5;positions=m.positions_get();orders=m.orders_get()
        if positions is None or orders is None:self.publish_meta('UNAVAILABLE','Broker orders/positions unavailable');return
        self.check_duplicates(positions,orders)
        from pair_controls import disabled_pairs, enabled
        disabled = disabled_pairs(self.store)
        snap=self.store.snapshot();a=m.account_info()
        from trade_monitor import monitor, observe_shadow
        self.monitor_counts = {strategy:[0,0] for strategy in MAGIC}
        for row in self.rows():
            if row['status'] == 'CLOSED':
                p=json.loads(row['payload'])
                if observe_shadow(self,row,p,time.time()):
                    self.save(row['key'],'CLOSED',p)
                continue
            if row['status'] not in BUSY:continue
            p=json.loads(row['payload']);tag=p.get('tag');magic=MAGIC[row['strategy']]
            order=next((o for o in orders if o.symbol==row['symbol'] and int(o.magic)==magic and (int(o.ticket)==p.get('order_ticket') or tag and o.comment==tag)),None)
            pos=next((v for v in positions if v.symbol==row['symbol'] and int(v.magic)==magic and (int(v.identifier)==p.get('position_id') or tag and v.comment==tag)),None)
            deals=None
            if not pos and p.get('deal_ticket'):
                deals=m.history_deals_get(ticket=p['deal_ticket'])
                if deals:
                    p['position_id']=int(deals[0].position_id)
            if not pos and not p.get('position_id') and p.get('order_ticket'):
                start=datetime.fromtimestamp(row['created'],timezone.utc)-timedelta(days=2)
                end=datetime.now(timezone.utc)+timedelta(days=2)
                found=m.history_deals_get(start,end)
                opening=[d for d in (found or []) if int(d.order)==p['order_ticket'] and int(d.magic)==magic and d.symbol==row['symbol']]
                if opening:p['position_id']=int(opening[0].position_id)
            if not pos and p.get('position_id'):
                pos=next((v for v in positions if int(v.identifier)==p['position_id'] and int(v.magic)==magic and v.symbol==row['symbol']),None)
            analysis=next((x for x in snap['symbols'] if x['strategy']==row['strategy'] and x['symbol']==row['symbol']),None)
            analysis_fresh=bool(analysis and analysis.get('fresh') and analysis.get('health')=='OK')
            if pos:
                p.update(position_id=int(pos.identifier),position_ticket=int(pos.ticket),actual_entry=float(pos.price_open),
                         current=float(pos.price_current),volume=float(pos.volume),profit=float(pos.profit),swap=float(pos.swap),currency=a.currency,
                         actual_sl=float(pos.sl),actual_tp=float(pos.tp),last_broker_epoch=time.time(),decision='HOLD_REVIEW')
                try:
                    self.quote(row['symbol']);p['quote_fresh']=True
                except Exception:p['quote_fresh']=False
                if not analysis_fresh or not p['quote_fresh']:p['decision']='REVIEW_DATA'
                elif analysis['condition'].get('side')!=p['side'] or row['strategy']=='CONFLUENCE' and not analysis.get('qualified'):
                    p['decision']='CLOSE_REVIEW'
                if pos.sl<=0 or pos.tp<=0:p['decision']='PROTECTION_REVIEW'
                if p['quote_fresh'] and ((pos.price_current>=p['tp1']) if p['side']=='BUY' else (pos.price_current<=p['tp1'])):p['tp1_observed']=True
                history=m.history_deals_get(position=int(pos.identifier))
                if history is not None:
                    p['realised_to_date']=sum(float(d.profit)+float(d.commission)+float(d.swap)+float(getattr(d,'fee',0)) for d in history)
                p['note']=f'Broker-confirmed {self.execution_mode} position. Signal close flags remain review-only; optional management is logged separately.'
                # Weekend rule: from Friday 20:00 MT5 time, non-crypto positions are closed (no weekend holding).
                if p['quote_fresh'] and not keeps_running(row['symbol'],self.cfg):
                    try:
                        info,tick=self.quote(row['symbol'])
                        if closing_time(row['symbol'],tick.time,self.cfg):
                            close_position(self,row,p,pos,tick,info)
                            self.save(row['key'],'OPEN_PARTIAL' if p.get('partial') else 'OPEN',p)
                            continue                    # no early-exit / management on a position being closed
                    except Exception as exc:
                        p['weekend_close_note']='Weekend close could not be checked: '+str(exc)[:120]
                try:
                    early_exit_sent=monitor(self,row,p,pos)
                except Exception:
                    p['trade_monitor']=dict(status='DATA_UNAVAILABLE',reason='Trade Monitor could not complete the current broker check.')
                    early_exit_sent=False
                counts=self.monitor_counts[row['strategy']]
                counts[0] += 1
                counts[1] += p['trade_monitor']['status'] == 'DATA_UNAVAILABLE'
                try:
                    from trade_management import manage
                    if not early_exit_sent:
                        manage(self,row,p,pos)
                except Exception:
                    p['management_note']='Management unavailable; verify terminal, permissions and broker constraints.'
                self.save(row['key'],'OPEN_PARTIAL' if p.get('partial') else 'OPEN',p)
            elif order:
                p.update(order_ticket=int(order.ticket),last_broker_epoch=time.time(),decision='WAIT_FILL',note='Broker pending order; no position fill yet.')
                self.save(row['key'],'PENDING',p)
                expired=time.time()>=datetime.fromisoformat(p['expires_utc']).timestamp()
                gate=self.news.check(row['symbol'],until=datetime.fromisoformat(p['expires_utc']).timestamp())
                invalid=gate['blocked'] or analysis_fresh and analysis['condition'].get('side')!=p['side']
                if not enabled(disabled, row['strategy'], row['symbol']):
                    reason = 'Pair disabled for this strategy; cancelling its pending entry.'
                elif closing_time(row['symbol'],self.server_epoch(row['symbol']),self.cfg):
                    reason = 'Weekend rule: pending entry cancelled from Friday 20:00 MT5 time (no weekend holding).'
                elif expired or invalid:
                    reason = 'Own pending order expired, news blocked, or strategy invalidated before fill.'
                else:
                    reason = None
                if reason:
                    try:self.cancel(row,p,order,reason)
                    except Exception:self.save(row['key'],'CANCEL_UNCERTAIN',dict(p,note='Cancellation unconfirmed; verify own pending order in MT5.'))
            elif p.get('position_id'):
                history=m.history_deals_get(position=p['position_id'])
                exits=[d for d in (history or []) if int(d.entry) in (1,3)]
                if exits:
                    opening=[d for d in history if int(d.entry)==0 and getattr(d,'volume',0)>0 and getattr(d,'price',0)>0]
                    if opening:
                        filled=sum(float(d.volume) for d in opening)
                        p.update(actual_entry=sum(float(d.price)*float(d.volume) for d in opening)/filled,initial_filled_volume=filled,
                                 entry_epoch=min(getattr(d,'time',0) for d in opening))
                    total=sum(float(d.profit)+float(d.commission)+float(d.swap)+float(getattr(d,'fee',0)) for d in history)
                    last_exit=max(exits,key=lambda d:getattr(d,'time_msc',getattr(d,'time',0)))
                    p.update(exit_price=getattr(last_exit,'price',None),exit_epoch=getattr(last_exit,'time',None),realised=total,currency=a.currency,profit=None,decision='BROKER_CLOSED',last_broker_epoch=time.time(),
                             close_reason=int(last_exit.reason),note='Closure verified from broker exit deals; realised total includes reported costs.')
                    early_exit = p.get('early_exit_order')
                    if early_exit and early_exit.get('deal') and any(
                        int(d.ticket)==early_exit['deal'] for d in exits
                    ):
                        early_exit['status']='BROKER_EXIT_DEAL_CONFIRMED'
                    self.save(row['key'],'CLOSED',p)
                    self.retire_tracking(row['setup_key'],'DEMO_CLOSED','Demo closure verified from broker exit deals. See demo ledger for actual realised P/L; paper tracker retired.')
                else:self.save(row['key'],'REVIEW_UNKNOWN',dict(p,decision='REVIEW_DATA',note='Position absent but exit deals unavailable; closure not assumed.'))
            elif p.get('order_ticket'):
                history=m.history_orders_get(ticket=p['order_ticket'])
                if history and int(history[-1].state) in (2,5,6):
                    self.save(row['key'],'CANCELLED',dict(p,decision='CANCELLED',note='Broker history confirms cancelled, rejected or expired order.'))
                    self.retire_tracking(row['setup_key'],'DEMO_CANCELLED','Broker history confirms demo order cancellation/rejection/expiry; no entry fill assumed.')
                else:self.save(row['key'],'REVIEW_UNKNOWN',dict(p,decision='REVIEW_DATA',note='No matching broker position/order yet; no resend.'))
            elif row['status'] in ('PRECHECK','SENDING','UNCERTAIN','CANCEL_UNCERTAIN','REVIEW_UNKNOWN'):
                self.save(row['key'],'UNCERTAIN',dict(p,decision='REVIEW_DATA',note='Ambiguous demo request; reconcile manually in MT5. No resend.'))
        self.publish_meta('ENABLED')
