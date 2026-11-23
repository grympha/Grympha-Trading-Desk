"""Entry policies for the strategies on the shared execution engine."""
from datetime import datetime

from structural_tp import mode


class BBMAExecutor:
    strategy = 'BBMA'

    @staticmethod
    def matches(store, result, signal):
        key = 'BBMA|' + result['symbol'] + ':' + result['entry_plan']['id']
        return signal['key'] == key and bool(signal.get('structural_tp')) == (mode(store) == 'structural')

    @staticmethod
    def news_until(signal, now):
        return datetime.fromisoformat(signal['expires_utc']).timestamp()


class ConfluenceExecutor:
    strategy = 'CONFLUENCE'

    @staticmethod
    def matches(store, result, signal):
        key = 'CONFLUENCE|' + result['symbol'] + ':' + result['entry_plan']['id']
        return signal['key'] == key

    @staticmethod
    def news_until(signal, now):
        return now


class ORBExecutor:
    strategy = 'ORB'

    @staticmethod
    def matches(store, result, signal):
        key = 'ORB|' + result['symbol'] + ':' + result['entry_plan']['id']
        return signal['key'] == key

    @staticmethod
    def news_until(signal, now):
        # Market entry now: same news filter and window as BBMA/Confluence (dashboard News filter settings).
        return now


EXECUTORS = {p.strategy:p for p in (BBMAExecutor(), ConfluenceExecutor(), ORBExecutor())}
