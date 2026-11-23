"""Single-folder process ownership and module heartbeat journal."""
import json,os,subprocess,sys,time,uuid
from pathlib import Path
class RuntimeLock:
    def __init__(self,path):self.path=Path(path);self.file=None
    def __enter__(self):
        self.file=self.path.open('a+b');self.file.seek(0)
        if self.path.stat().st_size==0:self.file.write(b' ');self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();self.file=None
            raise SystemExit('Another bot process owns this folder. Stop that process before starting another; no duplicate MT5 session was opened.')
        return self
    def __exit__(self,*args):
        if self.file:
            if os.name=='nt':
                import msvcrt
                self.file.seek(0);msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
            self.file.close();self.file=None

class HealthMonitor:
    def __init__(self,store,cfg):
        self.store,self.cfg=store,cfg;self.session=uuid.uuid4().hex;self.worker=None
        self.states={n:dict(status='WAITING',updated_epoch=time.time(),note='Not sampled yet')
                     for n in ('Risk Check','Dashboard + Journal','Research')}
        from confluence_strategy import settings
        from pair_controls import STRATEGIES
        for strategy in STRATEGIES:
            if strategy == 'ORB':
                try:
                    from orb_strategy import settings as orb_settings
                    enabled = bool(orb_settings(cfg)['enabled'])
                except Exception:
                    enabled = False
            else:
                enabled = strategy == 'BBMA' or settings({'symbols':[],**cfg})['enabled']
            self.states[f'{strategy} Scanner']=dict(status='WAITING' if enabled else 'DISABLED',
                updated_epoch=time.time(),note='Waiting for scan' if enabled else 'Strategy disabled in config')
            self.set(f'{strategy} Executor','DISABLED','Start DEMO execution to enable order submission')
            self.set(f'{strategy} Trade Monitor','DISABLED','Start DEMO execution to monitor bot-owned positions')
        with store.connect() as db:db.execute("INSERT OR REPLACE INTO meta VALUES ('health_session',?)",(json.dumps(self.session),))
    def set(self,name,status,note=''):
        freshness=max(60,self.cfg.get('poll_seconds',15)*3)
        if name=='CONFLUENCE Scanner':
            from confluence_strategy import settings
            freshness=max(freshness,settings({'symbols':[],**self.cfg})['check_seconds']+60)
        self.states[name]=dict(status=status,updated_epoch=time.time(),note=note,freshness_seconds=freshness)
        with self.store.connect() as db:db.execute("INSERT OR REPLACE INTO meta VALUES ('system_health',?)",(json.dumps(dict(updated_epoch=time.time(),components=self.states)),))
    def start_watchdog(self):
        if not self.cfg.get('health_monitor',{}).get('watchdog_enabled',True):return
        kwargs=dict(stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP
        timeout=max(60,float(self.cfg.get('poll_seconds',15))*6)
        self.worker=subprocess.Popen([sys.executable,str(Path(__file__).with_name('health_watchdog.py')),'--db',str(self.store.path.resolve()),'--pid',str(os.getpid()),'--session',self.session,'--timeout',str(timeout)],**kwargs)
    def close(self,error=None):
        with self.store.connect() as db:db.execute("INSERT OR REPLACE INTO meta VALUES ('health_shutdown',?)",(json.dumps(dict(session=self.session,epoch=time.time(),error=error)),))
        if self.worker:
            try:self.worker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.worker.terminate()
                try:self.worker.wait(timeout=2)
                except subprocess.TimeoutExpired:self.worker.kill()
