"""Edit one Telegram message per tracked setup. No messages are sent by tests."""
import hashlib
import json
import os
import time
import urllib.request
import urllib.error
from tracking import notification_text


def request(token,method,body):
    req=urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}',json.dumps(body).encode(),{'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=12) as r:payload=json.load(r)
    except urllib.error.HTTPError as exc:
        try:payload=json.loads(exc.read())
        except Exception:raise RuntimeError('Telegram HTTP failure') from None
        if method=='editMessageText' and 'message is not modified' in payload.get('description','').lower():return {}
        raise RuntimeError('Telegram rejected setup update; original message is retained') from None
    if not payload.get('ok'):raise RuntimeError('Telegram rejected setup update')
    return payload.get('result',{})


class Board:
    def __init__(self,store,send=request):self.store=store;self.send=send

    def update(self,now=None):
        now=time.time() if now is None else now
        snap=self.store.snapshot(now=now)
        import telegram_config
        from telegram_notify import load_prefs
        creds=telegram_config.active() if load_prefs(self.store)['setup_board'] else None
        token,chat=creds if creds else (None,None)
        chat_hash=hashlib.sha256((chat or '').encode()).hexdigest()
        interval=snap['meta'].get('tracking',{}).get('telegram_update_seconds',60)
        errors=[]
        demo_links={d['setup_key']:d for d in snap.get('demo_orders',[])} if snap['meta'].get('demo_execution',{}).get('enabled') else {}
        for row in snap['signals']:
            row=dict(row)
            if row['key'] in demo_links:
                d=dict(demo_links[row['key']])
                if d['status'] in ('OPEN','OPEN_PARTIAL') and (snap['meta'].get('demo_execution',{}).get('status')!='ENABLED' or now-d.get('last_broker_epoch',0)>60 or not d.get('quote_fresh',False)):
                    d.update(decision='REVIEW_DATA',note='Broker/quote snapshot stale. P/L is last reported, not a current mark.')
                row['demo_execution']=d
            t=row.get('tracking')
            if not t:continue
            if not token or not chat:
                self.store.delivery(row['key'],'DISABLED');continue
            key=row['key'];text=notification_text(row)
            digest=hashlib.sha256(text.encode()).hexdigest()
            event=json.dumps([row['lifecycle'],t.get('decision'),t.get('tp1_observed'),row.get('demo_execution',{}).get('status'),row.get('demo_execution',{}).get('decision')])
            # Store event signature alongside the text hash; critical state changes edit immediately.
            with self.store.connect() as db:
                prior=db.execute('SELECT * FROM telegram_updates WHERE key=?',(key,)).fetchone()
            if prior and prior['chat_hash']!=chat_hash:prior=None
            if prior:
                last=json.loads(prior['text_hash'])
                if last.get('hash')==digest:continue
                if now-prior['last_edit']<interval and last.get('event')==event:continue
            elif t.get('created_epoch',row['created'])<snap['meta'].get('started',0) and row['lifecycle'] not in ('PENDING','PAPER_RUNNING','PAPER_TP1'):
                continue # Do not back-send historical closed scenarios on an upgrade/restart.
            try:
                method='editMessageText' if prior else 'sendMessage'
                body=dict(chat_id=chat,text=text)
                if prior:body['message_id']=prior['message_id']
                result=self.send(token,method,body)
                message_id=prior['message_id'] if prior else result.get('message_id')
                if message_id is None:raise RuntimeError('Telegram did not return a message identifier')
                with self.store.connect() as db:
                    db.execute('INSERT OR REPLACE INTO telegram_updates VALUES (?,?,?,?,?)',
                               (key,int(message_id),chat_hash,now,json.dumps(dict(hash=digest,event=event))))
                self.store.delivery(key,'EDITED' if prior else 'SENT')
            except Exception:
                # Never print exception URLs/tokens and never fall back to sending a duplicate.
                self.store.delivery(key,'UPDATE_FAILED');errors.append(key)
        return errors
