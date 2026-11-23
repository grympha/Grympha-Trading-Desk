"""ORB scanner: runs the ORB agent every bot cycle for its symbols (default XAUUSD only)."""
import logging
import time

from pair_controls import enabled
from market_hours import MarketClosed, log_closed_once


class ORBScanner:
    def __init__(self, mt5, root, runner, stamp):
        self.mt5, self.root, self.runner, self.stamp = mt5, root, runner, stamp
        self.last_status = {}

    def scan(self, target, disabled):
        runner, stamp = self.runner, self.stamp
        if not runner or not runner.c['enabled']:
            return dict(checked=0, blocked=0, status='DISABLED')
        stats = dict(checked=0, blocked=0, closed=0)

        def publish(result):
            stats['checked'] += 1
            stats['closed'] += result.get('health') == 'MARKET_CLOSED'
            stats['blocked'] += result.get('health') not in ('OK', 'MARKET_CLOSED')
            target(result)

        for symbol in runner.c['symbols']:
            if not enabled(disabled, 'ORB', symbol):
                continue
            try:
                r = runner.check(self.mt5, symbol, time.time())
                publish(r)
                # Log state changes and every entry plan; not one line per 15 s while nothing changes.
                if r['entry_plan'] or self.last_status.get(symbol) != r['orb_status']:
                    from decision_log import append as log_decision
                    log_decision(self.root / 'orb_decisions.jsonl', r, allow_nan=True)
                    print('ORB', symbol, r['orb_status'], r['reason'], flush=True)
                self.last_status[symbol] = r['orb_status']
            except MarketClosed as exc:
                log_closed_once(str(exc))
                publish(dict(strategy='ORB', symbol=symbol, health='MARKET_CLOSED', checked_utc=stamp(time.time()),
                             reason=str(exc), condition={'name': 'MARKET CLOSED', 'side': None}, setup=None, entry_plan=None))
            except Exception as exc:
                logging.warning('ORB %s analysis blocked: %s', symbol, str(exc))
                publish(dict(strategy='ORB', symbol=symbol, health='BLOCKED', checked_utc=stamp(time.time()),
                             reason=str(exc), condition={'name': 'UNAVAILABLE', 'side': None}, setup=None, entry_plan=None))
        return stats
