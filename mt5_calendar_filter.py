"""Read-only adapter for bbma.mt5.calendar.v1; fail closed on incomplete/stale data."""
import json,math,ntpath,time
from pathlib import Path
from news_filter import currencies

class MT5CalendarFilter:
    def __init__(self,cfg,mt5):
        self.cfg=cfg.get('news_filter',{});self.mt5=mt5;self.enabled=self.cfg.get('enabled',False)
        self.before=int(self.cfg.get('before_minutes',30))*60;self.after=int(self.cfg.get('after_minutes',30))*60
        if not 0<=self.before<=14400 or not 0<=self.after<=14400:raise ValueError('Invalid news blackout window')
        self.events=[];self.fetched=0;self.updated=0;self.error='Waiting for MT5 Calendar Bridge';self.coverage=(0,0);self.offset=0;self.available=set()
    @staticmethod
    def number(v):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ValueError('Invalid calendar timestamp')
        return v
    def refresh(self):
        if not self.enabled:return
        try:
            if self.mt5 is None:raise ValueError('MT5 calendar connection unavailable')
            t=self.mt5.terminal_info();a=self.mt5.account_info()
            if t is None or not t.connected or a is None:raise ValueError('MT5 disconnected; calendar entries blocked')
            root=Path(t.data_path);file=root/'MQL5'/'Files'/'bbma_mt5_calendar.json'
            if not file.is_file():raise ValueError('Calendar file missing: attach Calendar Bridge EA to one chart in this terminal')
            if file.stat().st_size>4*1024*1024:raise ValueError('Calendar export exceeds size limit')
            raw=file.read_bytes()
            if len(raw)>4*1024*1024:raise ValueError('Calendar export exceeds size limit')
            d=json.loads(raw.decode('utf-8-sig'));now=time.time()
            if d.get('schema')!='bbma.mt5.calendar.v1':raise ValueError('Unsupported calendar export schema')
            if ntpath.normcase(ntpath.normpath(d.get('terminal_data_path','')))!=ntpath.normcase(ntpath.normpath(str(root))) or d.get('server')!=a.server:
                raise ValueError('Calendar belongs to a different terminal/broker server')
            if d.get('connected') is not True or d.get('ready') is not True or d.get('status')!='HEALTHY' or d.get('clock_verified') is not True:
                raise ValueError('Calendar Bridge '+str(d.get('status','UNAVAILABLE'))+': '+str(d.get('error','data/clock not ready'))[:240])
            updated=self.number(d['updated_epoch']);fetched=self.number(d['fetched_epoch']);offset=self.number(d['server_utc_offset_seconds'])
            heartbeat=self.number(d['heartbeat_seconds']);maxage=self.number(d['max_cache_age_seconds'])
            if not 5<=heartbeat<=60 or not 0<maxage<=1800:raise ValueError('Invalid calendar freshness policy')
            if not -5<=now-updated<=max(90,heartbeat*3):raise ValueError('Calendar heartbeat stale; keep EA and MT5 running')
            if not -5<=now-fetched<=min(900,maxage):raise ValueError('Calendar event data stale; new entries blocked')
            if fetched>updated+5 or abs(offset)>50400 or abs(self.number(d['server_now_epoch'])-updated-offset)>10:raise ValueError('Calendar/server clock mismatch; verify Windows time and EA clock status')
            lo=self.number(d['coverage_from_server_epoch']);hi=self.number(d['coverage_to_server_epoch'])
            if not lo<hi or not lo<=now+offset<hi:raise ValueError('Calendar coverage does not include current server time')
            available=d['currencies']
            if not isinstance(available,list) or any(not isinstance(c,str) or len(c)!=3 or not c.isalpha() or c!=c.upper() for c in available):raise ValueError('Invalid calendar currency coverage')
            if not isinstance(d['events'],list):raise ValueError('Incomplete calendar event list')
            events=[]
            for e in d['events']:
                stamp=self.number(e['server_epoch']);cur=e['currency']
                if e['importance']!=3 or cur not in available or not lo<=stamp<hi or not isinstance(e['estimated_time'],bool) or not isinstance(e['title'],str):raise ValueError('Invalid calendar event; snapshot rejected')
                left=stamp-self.before;right=stamp+self.after
                if e['estimated_time']:
                    day=math.floor(stamp/86400)*86400;left=day-self.before;right=day+86400+self.after
                events.append(dict(currency=cur,title=e['title'],epoch=stamp-offset,from_epoch=left-offset,to_epoch=right-offset,estimated_time=e['estimated_time'],server_epoch=stamp))
            self.events=events;self.offset=offset;self.coverage=(lo-offset,hi-offset);self.available=set(available);self.updated=updated;self.fetched=fetched;self.error=None
        except Exception as exc:
            # Do not expose file paths/account data or generic decoder tracebacks.
            self.error=str(exc) if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError) else 'Calendar file unreadable/incomplete; retry after next EA heartbeat'
            self.events=[]
    def check(self,symbol,until=None,now=None):
        if not self.enabled:return dict(blocked=False,status='DISABLED',events=[],note='News filter disabled by configuration')
        self.refresh();now=time.time() if now is None else now
        if self.error:return dict(blocked=True,status='UNAVAILABLE',events=[],note=self.error)
        cur=currencies(symbol,self.cfg)
        if not cur or not cur<=self.available:return dict(blocked=True,status='UNAVAILABLE',events=[],note='EA currency coverage missing: '+','.join(sorted(cur-self.available)) if cur else 'Symbol currency unmapped')
        stop=max(now,until if until is not None else now)
        if now<self.coverage[0] or stop+self.before>=self.coverage[1]:return dict(blocked=True,status='UNAVAILABLE',events=[],note='Pending entry window exceeds calendar coverage')
        found=[e for e in self.events if e['currency'] in cur and e['from_epoch']<=stop and e['to_epoch']>=now]
        return dict(blocked=bool(found),status='BLOCKED' if found else 'CLEAR',events=found,note='High-impact MT5 calendar blackout intersects entry window' if found else 'MT5 calendar clear for this entry window')
    def status(self,symbols):
        checks={s:self.check(s) for s in symbols}
        return dict(enabled=self.enabled,provider='MT5 Economic Calendar',error=self.error if self.enabled else None,fetched_epoch=self.fetched,updated_epoch=self.updated,before_minutes=self.before//60,after_minutes=self.after//60,symbols=checks,events=sorted(self.events,key=lambda e:e['epoch']),max_age_seconds=900)
