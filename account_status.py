"""Credential-free, read-only MT5 account snapshot."""
import math
import time

def sample(mt5, now=None):
    out=dict(status='UNAVAILABLE',updated_epoch=time.time() if now is None else now,broker=None,server=None,balance=None,equity=None,currency=None,account_type=None)
    try:
        terminal=mt5.terminal_info()
        if terminal is None:return out
        if not terminal.connected:
            out['status']='DISCONNECTED';return out
        a=mt5.account_info()
        if a is None:return out
        balance,equity=float(a.balance),float(a.equity)
        if not math.isfinite(balance) or not math.isfinite(equity):return out
        out.update(status='HEALTHY',broker=str(a.company),server=str(a.server),balance=balance,equity=equity,currency=str(a.currency),account_type={0:'DEMO',1:'CONTEST',2:'REAL'}.get(a.trade_mode,'UNKNOWN'))
    except Exception:pass
    return out
