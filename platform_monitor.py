"""Read-only dashboard aggregation for portfolio, audit and runtime health."""
import json,time
from execution_audit import summary as audit_summary
from portfolio_guard import settings,evaluate

def summary(store):
    with store.connect() as db:
        meta={r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM meta')}
    scope=meta.get('portfolio_scope')
    portfolio=meta.get('portfolio_snapshot') or dict(status='UNKNOWN',updated_epoch=None,equity=None,known_risk=0.,total_risk=None,rows=[],unknown=['Waiting for account snapshot'],by_pair={},by_currency={},by_strategy={})
    health=meta.get('system_health') or dict(components={})
    alive=bool(meta.get('running') and time.time()-meta.get('heartbeat',0)<=max(60,meta.get('poll_seconds',15)*3))
    alerts=[]
    path=store.path.parent/'health_alerts.jsonl'
    if path.exists():
        try:
            for line in path.read_text().splitlines()[-20:]:
                try:alerts.append(json.loads(line))
                except (ValueError,TypeError):pass
        except OSError:pass
    return dict(freshness_seconds=max(60,meta.get('poll_seconds',15)*3),alerts=alerts,portfolio=portfolio,controls=settings(store),current_check=evaluate(portfolio,settings(store)),last_check=meta.get('portfolio_last_check'),
                audit=audit_summary(store,scope),health=health,bot_online=alive,watchdog=meta.get('health_watchdog') if (meta.get('health_watchdog') or {}).get('session')==meta.get('health_session') else None,
                note='No daily-loss limit, auto-close or automatic restart added. Currency risk is a non-netted stop-risk proxy, not notional or measured correlation.')
