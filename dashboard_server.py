"""Loopback-only BBMA dashboard with validated same-origin control settings. Standard-library HTTP server."""
from __future__ import annotations
import argparse
import csv
import io
import json
import re
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import urlsplit, parse_qs
from dashboard_store import Store

ROOT=Path(__file__).resolve().parent


# Heavy per-trade context (entry-time candle captures) is only needed in the trade-detail dialog,
# which loads it from /api/trade. The 5-second /api/status poll omits it.
HEAVY_TRADE_CONTEXT=('chart','review')


def light_snapshot(snap):
    for o in snap.get('demo_orders') or []:
        ctx=o.get('context')
        if isinstance(ctx,dict) and any(k in ctx for k in HEAVY_TRADE_CONTEXT):
            o['context']={k:v for k,v in ctx.items() if k not in HEAVY_TRADE_CONTEXT}
            o['context_trimmed']=True
    for x in snap.get('symbols') or []:
        if isinstance(x.get('charts'),dict) and x['charts']:x.pop('chart',None)  # multi-timeframe charts supersede the single chart
        x.pop('review_market',None)  # research capture for Trade Review; never read by the page
    try:
        from telegram_notify import status_view
        snap.setdefault('meta',{})['telegram']=status_view()  # on/off + configured only; never the token
    except Exception:pass
    return snap


def build_server(store, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def send(self, body, mime, code=200, attachment=None):
            self.send_response(code)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            if attachment:
                self.send_header('Content-Disposition',f'attachment; filename="{attachment}"')
            self.end_headers();self.wfile.write(body)

        def do_POST(self):
            origin=self.headers.get('Origin','')
            host=self.headers.get('Host','')
            if host not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}') or origin!='http://'+host or self.headers.get('X-Dashboard-Request')!='controls' or self.headers.get('Content-Type')!='application/json':
                return self.send(b'Forbidden','text/plain',403)
            if self.path not in ('/api/controls','/api/pair-controls','/api/bbma-tp-mode','/api/early-exit','/api/portfolio-controls','/api/replay-settings','/api/replay-version','/api/strategy-settings','/api/duplicate-ack','/api/telegram-prefs','/api/telegram-test','/api/backup-now','/api/live-test-control'):return self.send(b'Not found','text/plain',404)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4096:raise ValueError('Invalid length')
                if self.path in ('/api/replay-settings','/api/replay-version'):
                    from replay_lab import initialise,register_settings,add_version
                    initialise(store)
                    values=json.loads(self.rfile.read(size))
                    with store.connect() as db:
                        if self.path=='/api/replay-settings':key=register_settings(db,values)
                        else:
                            if not isinstance(values,dict) or set(values)!=set(('name','policy')):raise ValueError('Invalid version request')
                            if db.execute('SELECT count(*) FROM strategy_versions').fetchone()[0]>=30:raise ValueError('Version limit reached')
                            key=add_version(db,values['name'],values['policy'])
                    return self.send(json.dumps({'ok':True,'id':key,'research_only':True}).encode(),'application/json')
                if self.path=='/api/backup-now':
                    import db_backup
                    self.rfile.read(size)
                    try:cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
                    except Exception:cfg={}
                    started=db_backup.start(store,ROOT,cfg,force=True)
                    return self.send(json.dumps({'ok':True,'started':started}).encode(),'application/json')
                if self.path=='/api/telegram-prefs':
                    from telegram_notify import save_prefs
                    return self.send(json.dumps({'ok':True,'prefs':save_prefs(store,json.loads(self.rfile.read(size)))}).encode(),'application/json')
                if self.path=='/api/telegram-test':
                    import telegram_config
                    self.rfile.read(size)
                    creds=telegram_config.active()
                    if not creds:return self.send(b'Telegram is off or not set up. Use the launcher: Telegram alerts row.','text/plain',409)
                    try:telegram_config.send(*creds,'\u2705 Grympha Trading Desk: test message from the dashboard. No trade was placed.')
                    except RuntimeError as exc:return self.send(str(exc).encode(),'text/plain',502)
                    return self.send(b'{"ok":true}','application/json')
                if self.path=='/api/duplicate-ack':
                    from duplicate_guard import acknowledge
                    body=json.loads(self.rfile.read(size))
                    if not isinstance(body,dict) or set(body)!={'tickets'}:raise ValueError('Expected tickets')
                    return self.send(json.dumps({'ok':True,'acknowledged':acknowledge(store,body['tickets'])}).encode(),'application/json')
                if self.path=='/api/strategy-settings':
                    from strategy_settings import save as save_strategy
                    saved=save_strategy(store,json.loads(self.rfile.read(size)))
                    return self.send(json.dumps({'ok':True,'settings':saved}).encode(),'application/json')
                if self.path=='/api/live-test-control':
                    from live_test import LiveTestEngine, build_agent
                    body=json.loads(self.rfile.read(size))
                    if not isinstance(body,dict):raise ValueError('Invalid Live Test control')
                    eng=LiveTestEngine(store);action=body.get('action')
                    if action in ('start','pause'):
                        if body.get('agent_id') not in eng.agents:raise ValueError('Unknown agent')
                        eng.set_status(body['agent_id'],'RUNNING' if action=='start' else 'PAUSED')
                    elif action=='register':
                        agent=build_agent(body.get('strategy_id'),body.get('config') or {})
                        if agent is None:raise ValueError('Unknown or unavailable strategy')
                        eng.register(agent,str(body.get('symbol') or 'XAUUSD'),'PAUSED')
                    else:raise ValueError('Unknown action')
                    return self.send(b'{"ok":true}','application/json')
                if self.path=='/api/bbma-tp-mode':
                    from structural_tp import save_mode
                    save_mode(store,json.loads(self.rfile.read(size)))
                    return self.send(b'{"ok":true}','application/json')
                if self.path=='/api/early-exit':
                    from trade_monitor import set_auto
                    set_auto(store,json.loads(self.rfile.read(size)))
                    return self.send(b'{"ok":true}','application/json')
                if self.path=='/api/pair-controls':
                    from pair_controls import save
                    save(store,json.loads(self.rfile.read(size)))
                    return self.send(b'{"ok":true}','application/json')
                if self.path=='/api/portfolio-controls':
                    from portfolio_guard import save_settings
                    values=save_settings(store,json.loads(self.rfile.read(size)))
                    return self.send(json.dumps({'ok':True,'controls':values}).encode(),'application/json')
                from trade_controls import validate
                values=validate(json.loads(self.rfile.read(size)))
                with store.connect() as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('trade_controls',json.dumps(values)))
                return self.send(json.dumps({'ok':True,'controls':values}).encode(),'application/json')
            except (ValueError,TypeError):return self.send(b'Invalid controls','text/plain',400)

        def do_GET(self):
            host=self.headers.get('Host','').split(':')[0].lower()
            if host not in ('127.0.0.1','localhost'):
                return self.send(b'Invalid host','text/plain',403)
            path=urlsplit(self.path).path
            if path=='/api/replay':
                from replay_lab import summary
                query=parse_qs(urlsplit(self.path).query)
                try:report=summary(store,query.get('strategy',['ALL'])[0],query.get('symbol',['ALL'])[0])
                except ValueError:return self.send(b'Invalid research filters','text/plain',400)
                return self.send(json.dumps(report,allow_nan=False).encode(),'application/json')
            if path=='/api/platform':
                from platform_monitor import summary
                return self.send(json.dumps(summary(store),allow_nan=False).encode(),'application/json')
            if path=='/api/live-test':
                from live_test import LiveTestEngine
                return self.send(json.dumps(LiveTestEngine(store).report(),allow_nan=False).encode(),'application/json')
            if path=='/api/live-test.csv':
                from live_test import LiveTestEngine
                agent=parse_qs(urlsplit(self.path).query).get('agent',[None])[0]
                eng=LiveTestEngine(store);rows=eng.csv_rows(agent)
                buf=io.StringIO(newline='');w=csv.DictWriter(buf,fieldnames=LiveTestEngine.CSV_FIELDS,extrasaction='ignore');w.writeheader()
                for r in rows:
                    w.writerow({k:("'"+str(r.get(k,'')) if str(r.get(k,'')).startswith(('=','+','-','@')) else r.get(k,'')) for k in LiveTestEngine.CSV_FIELDS})
                return self.send(buf.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8',attachment='live_test_trades.csv')
            if path=='/api/ai-shadow':
                from ai_shadow import summary as shadow_summary
                return self.send(json.dumps(shadow_summary(store),allow_nan=False).encode(),'application/json')
            if path in ('/api/review','/api/performance.csv','/api/performance.pdf'):
                try:
                    from trade_review import summary
                    query=parse_qs(urlsplit(self.path).query)
                    filters=(query.get('strategy',['ALL'])[0],query.get('symbol',['ALL'])[0],
                             query.get('from',[None])[0],query.get('to',[None])[0])
                    exporting=path!='/api/review'
                    report=summary(store,*filters,all_reviews=exporting)
                    if not exporting:
                        return self.send(json.dumps(report,allow_nan=False).encode(),'application/json')
                    from performance_export import render
                    kind=path.rsplit('.',1)[1]
                    try:
                        body=render(report,filters,kind)
                    except ModuleNotFoundError as exc:
                        if exc.name!='reportlab':raise
                        return self.send(b'PDF export requires reportlab. Install requirements.txt and retry.','text/plain',503)
                    mime='application/pdf' if kind=='pdf' else 'text/csv; charset=utf-8'
                    return self.send(body,mime,attachment=f'grympha_performance_report.{kind}')
                except (ValueError,TypeError,OverflowError):return self.send(b'Invalid review filters','text/plain',400)
            if path=='/api/equity':
                try:
                    from equity_history import series
                    selected=parse_qs(urlsplit(self.path).query).get('range',['1W'])[0]
                    return self.send(json.dumps(series(store,selected),allow_nan=False).encode(),'application/json')
                except ValueError:return self.send(b'Invalid range; use 1D, 1W, 1M, 3M or ALL','text/plain',400)
            if path=='/api/daily':
                try:
                    selected=parse_qs(urlsplit(self.path).query).get('date',[None])[0]
                    return self.send(json.dumps(store.daily(selected),allow_nan=False).encode(),'application/json')
                except (ValueError,TypeError):return self.send(b'Invalid date; use YYYY-MM-DD through today','text/plain',400)
            if path=='/api/status':
                return self.send(json.dumps(light_snapshot(store.snapshot()),allow_nan=False).encode(),'application/json')
            if path=='/api/trade':
                key=parse_qs(urlsplit(self.path).query).get('key',[''])[0]
                row=next((o for o in store.snapshot().get('demo_orders',[]) if o.get('key')==key),None) if key else None
                if row is None:return self.send(b'Trade not found','text/plain',404)
                return self.send(json.dumps(row,allow_nan=False).encode(),'application/json')
            if path=='/api/export.csv':
                fields=['strategy','symbol','side','setup','entry','stop_loss','tp1','tp2','confirmation_utc','expires_utc','lifecycle','paper_price_pnl','paper_r','paper_decision','note']
                buf=io.StringIO(newline='');w=csv.DictWriter(buf,fieldnames=fields,extrasaction='ignore');w.writeheader()
                strategy=parse_qs(urlsplit(self.path).query).get('strategy',['ALL'])[0]
                for row in store.snapshot()['signals']:
                    if strategy!='ALL' and row['strategy']!=strategy: continue
                    tracking=row.get('tracking',{})
                    row=dict(row,paper_price_pnl=tracking.get('price_pnl'),paper_r=tracking.get('pnl_r'),paper_decision=tracking.get('decision'))
                    # Formula-neutralise textual CSV cells even if a custom symbol was supplied.
                    w.writerow({k:("'"+str(row.get(k,'')) if str(row.get(k,'')).startswith(('=','+','-','@')) else row.get(k,'')) for k in fields})
                return self.send(buf.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8',attachment="bbma_signals.csv")
            files={'/':'dashboard.html','/dashboard.css':'dashboard.css','/demo.json':'demo.json','/favicon.svg':'favicon.svg','/favicon.ico':'launcher_icon.ico'}
            # Dashboard scripts live in js/ (split by page area); only plain js/<name>.js files are served.
            if re.fullmatch(r'/js/[a-z][a-z0-9_-]*\.js',path):files[path]=path[1:]
            if path in files:
                p=ROOT/files[path]
                if p.is_file():
                    mime={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8','json':'application/json','svg':'image/svg+xml','ico':'image/x-icon'}[p.suffix[1:]]
                    return self.send(p.read_bytes(),mime)
            return self.send(b'Not found','text/plain',404)
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def start_dashboard(store,port=8765):
    server=build_server(store,port)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8765)
    args=p.parse_args();server=build_server(Store(ROOT/'dashboard.sqlite3'),args.port)
    print(f'Dashboard: http://127.0.0.1:{server.server_port} (keep the bot running separately)',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
