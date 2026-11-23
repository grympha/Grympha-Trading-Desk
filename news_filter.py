"""Trading Economics high-impact calendar; no credentials in status or errors."""
from datetime import datetime,timezone,timedelta
import json,os,time,threading,urllib.request,urllib.parse
COUNTRY={'united states':'USD','united kingdom':'GBP','japan':'JPY','australia':'AUD','euro area':'EUR','germany':'EUR','france':'EUR','italy':'EUR','spain':'EUR','canada':'CAD','switzerland':'CHF','new zealand':'NZD','austria':'EUR','belgium':'EUR','cyprus':'EUR','croatia':'EUR','estonia':'EUR','finland':'EUR','greece':'EUR','ireland':'EUR','latvia':'EUR','lithuania':'EUR','luxembourg':'EUR','malta':'EUR','netherlands':'EUR','portugal':'EUR','slovakia':'EUR','slovenia':'EUR'}

def currencies(symbol,cfg):
    override=cfg.get('symbol_currencies',{}).get(symbol)
    if override:return set(override)
    import re
    for pair in re.findall(r'(?:XAU|USD|EUR|GBP|JPY|AUD|CAD|CHF|NZD){2}',symbol.upper()):
        return set([pair[:3],pair[3:]])-{'XAU'}
    return set()

class NewsFilter:
    def __init__(self,cfg):
        self.cfg=cfg.get('news_filter',{});self.enabled=self.cfg.get('enabled',False)
        self.events=[];self.fetched=0;self.error='Calendar not loaded';self.loading=False;self.attempt=0;self.coverage=(0,0);self.lock=threading.Lock()
        self.before=int(self.cfg.get('before_minutes',30))*60;self.after=int(self.cfg.get('after_minutes',30))*60
        if not 0<=self.before<=14400 or not 0<=self.after<=14400:raise ValueError('News window must be 0..240 minutes')
    def refresh(self):
        if not self.enabled or self.loading or time.time()-self.attempt<300:return
        self.loading=True;self.attempt=time.time()
        threading.Thread(target=self.fetch,daemon=True).start()
    def fetch(self):
        try:
            key=os.getenv('TRADING_ECONOMICS_API_KEY','').strip()
            if not key:raise ValueError('API key missing')
            now=datetime.now(timezone.utc);start=now.date()-timedelta(days=1);end=now.date()+timedelta(days=3)
            countries=urllib.parse.quote(','.join(COUNTRY))
            url=f'https://api.tradingeconomics.com/calendar/country/{countries}/{start}/{end}?importance=3&f=json'
            req=urllib.request.Request(url,headers={'Authorization':key,'User-Agent':'BBMA-Demo/1.7'})
            with urllib.request.urlopen(req,timeout=10) as res:raw=json.load(res)
            if not isinstance(raw,list) or len(raw)>=1000:raise ValueError('Incomplete calendar')
            events=[]
            for r in raw:
                if int(r['Importance'])!=3:continue
                cur=COUNTRY.get(str(r.get('Country','')).lower()) or str(r.get('Currency','')).upper()
                if cur not in set(COUNTRY.values()):raise ValueError('Unmapped calendar country')
                dt=datetime.fromisoformat(r['Date'].replace('Z','+00:00'));dt=dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
                estimated=str(r.get('DateSpan','0'))!='0'
                stamp=dt.timestamp();lo=stamp-self.before;hi=stamp+self.after
                if estimated:
                    day=dt.astimezone(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp();lo=day-self.before;hi=day+86400+self.after
                events.append(dict(currency=cur,title=str(r.get('Event','High-impact event')),epoch=stamp,from_epoch=lo,to_epoch=hi,estimated_time=estimated))
            with self.lock:
                self.events=events;self.fetched=time.time();self.error=None;self.coverage=(datetime.combine(start,datetime.min.time(),timezone.utc).timestamp(),datetime.combine(end+timedelta(days=1),datetime.min.time(),timezone.utc).timestamp())
        except Exception:
            with self.lock:self.error='Calendar unavailable: check API key, entitlement or connection. New entries blocked.'
        finally:self.loading=False
    def check(self,symbol,until=None,now=None):
        now=time.time() if now is None else now
        if not self.enabled:return dict(blocked=False,status='DISABLED',events=[],note='News filter disabled')
        with self.lock:
            if self.error or not self.fetched or now-self.fetched>900 or not self.coverage[0]<=now<=self.coverage[1]:return dict(blocked=True,status='UNAVAILABLE',events=[],note=self.error or 'Calendar stale; new entries blocked')
            cur=currencies(symbol,self.cfg)
            if not cur:return dict(blocked=True,status='UNMAPPED',events=[],note='Configure news_filter.symbol_currencies for this broker symbol')
            stop=now if until is None else until
            events=[e for e in self.events if e['currency'] in cur and e['from_epoch']<=stop and e['to_epoch']>=now]
            return dict(blocked=bool(events),status='BLOCKED' if events else 'CLEAR',events=events,note='High-impact blackout intersects entry window' if events else 'No scheduled high-impact event in entry window')
    def status(self,symbols):
        return dict(enabled=self.enabled,error=self.error if self.enabled else None,provider='Trading Economics',fetched_epoch=self.fetched,before_minutes=self.before//60,after_minutes=self.after//60,symbols={s:self.check(s) for s in symbols},events=sorted(self.events,key=lambda e:e['epoch']))


def make_news_filter(cfg,mt5):
    provider=cfg.get('news_filter',{}).get('provider','mt5').lower()
    if provider=='mt5':
        from mt5_calendar_filter import MT5CalendarFilter
        return MT5CalendarFilter(cfg,mt5)
    if provider=='tradingeconomics':return NewsFilter(cfg)
    raise ValueError('Unknown news_filter.provider; use mt5 or tradingeconomics')
