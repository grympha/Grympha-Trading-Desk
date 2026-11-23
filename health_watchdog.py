"""External local-only observer. Never restarts bots, sends messages or touches MT5."""
import argparse,json,os,sqlite3,time
from pathlib import Path

def alive(pid):
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];api.OpenProcess.restype=wintypes.HANDLE
        api.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)];api.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=api.OpenProcess(0x1000,False,pid)
        if not handle:return False if ctypes.get_last_error()==87 else None
        code=wintypes.DWORD()
        try:return code.value==259 if api.GetExitCodeProcess(handle,ctypes.byref(code)) else None
        finally:api.CloseHandle(handle)
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return None

def inspect(meta,pid_is_alive,session,timeout,now):
    if meta.get('health_session')!=session:return 'SUPERSEDED'
    shutdown=meta.get('health_shutdown') or {}
    if shutdown.get('session')==session:return 'BOT ERROR' if shutdown.get('error') else 'STOPPED'
    if meta.get('running') is False:return 'STOPPED'
    if pid_is_alive is False:return 'BOT EXITED'
    if now-meta.get('heartbeat',0)>timeout:return 'HEARTBEAT STALE'
    return 'WATCHING' if pid_is_alive else 'PROCESS STATE UNKNOWN'

def run(db_path,pid,session,timeout):
    root=Path(db_path).parent;last=None
    while True:
        now=time.time()
        try:
            with sqlite3.connect(db_path,timeout=2) as db:
                meta={k:json.loads(v) for k,v in db.execute('SELECT key,value FROM meta')}
            status=inspect(meta,alive(pid),session,timeout,now)
        except Exception:status='BOT EXITED' if alive(pid) is False else 'DATABASE UNAVAILABLE'
        if status=='SUPERSEDED':return
        report=dict(status=status,updated_epoch=now,session=session,parent_pid=pid,local_only=True)
        temporary=root/'health_watchdog.json.tmp';temporary.write_text(json.dumps(report));temporary.replace(root/'health_watchdog.json')
        if status!=last and status not in ('WATCHING','STOPPED'):
            with (root/'health_alerts.jsonl').open('a') as stream:stream.write(json.dumps(report)+'\n')
        try:
            with sqlite3.connect(db_path,timeout=2) as db:
                db.execute('PRAGMA synchronous=NORMAL')  # do not force a disk flush every 2 s
                current=db.execute("SELECT value FROM meta WHERE key='health_session'").fetchone()
                if current and json.loads(current[0])==session:db.execute("INSERT OR REPLACE INTO meta VALUES ('health_watchdog',?)",(json.dumps(report),))
        except Exception:pass
        if status in ('STOPPED','BOT EXITED','BOT ERROR'):return
        last=status;time.sleep(2)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--pid',required=True,type=int);p.add_argument('--session',required=True);p.add_argument('--timeout',type=float,default=90)
    a=p.parse_args();run(a.db,a.pid,a.session,max(10,a.timeout))
