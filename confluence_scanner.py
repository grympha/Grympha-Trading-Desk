"""Confluence scanner with its own cadence and pair controls."""
import json
import logging
import time

from pair_controls import enabled
from market_hours import MarketClosed, log_closed_once


class ConfluenceScanner:
    def __init__(self, mt5, root, runner, stamp):
        self.mt5, self.root, self.runner, self.stamp = mt5, root, runner, stamp

    def scan(self, target, disabled):
        mt5, ROOT, confluence_runner, stamp = self.mt5, self.root, self.runner, self.stamp
        if not confluence_runner or not confluence_runner.c['enabled']:
            return dict(checked=0, blocked=0, status='DISABLED')
        if time.monotonic() < confluence_runner.next_due:
            return None
        stats = dict(checked=0, blocked=0, closed=0)
        def publish(result):
            stats['checked'] += 1
            stats['closed'] += result.get('health') == 'MARKET_CLOSED'
            stats['blocked'] += result.get('health') not in ('OK', 'MARKET_CLOSED')
            target(result)
        if confluence_runner and confluence_runner.c['enabled'] and time.monotonic() >= confluence_runner.next_due:
            confluence_runner.next_due = time.monotonic() + confluence_runner.c['check_seconds']
            for csymbol in confluence_runner.c['symbols']:
                if not enabled(disabled, 'CONFLUENCE', csymbol):
                    continue
                try:
                    cr = confluence_runner.check(mt5, csymbol, time.time())
                    publish(cr)
                    from decision_log import append as log_decision
                    log_decision(ROOT/'confluence_decisions.jsonl', cr, allow_nan=False)
                    print('CONFLUENCE', csymbol, f"{cr['score']}/5", cr['reason'], flush=True)
                except MarketClosed as exc:
                    log_closed_once(str(exc))
                    publish(dict(strategy='CONFLUENCE', symbol=csymbol, health='MARKET_CLOSED',
                                 checked_utc=stamp(time.time()), reason=str(exc),
                                 condition={'name':'MARKET CLOSED','side':None},setup=None,entry_plan=None))
                except Exception as exc:
                    logging.warning('CONFLUENCE %s analysis blocked: %s', csymbol, str(exc))
                    publish(dict(strategy='CONFLUENCE', symbol=csymbol, health='BLOCKED',
                                 checked_utc=stamp(time.time()), reason=str(exc),
                                 condition={'name':'UNAVAILABLE','side':None},setup=None,entry_plan=None))

        return stats
