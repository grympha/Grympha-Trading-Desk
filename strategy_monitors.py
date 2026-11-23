"""Invalidation levels for the shared position monitor."""


class BBMATradeMonitor:
    reason = 'original setup boundary.'

    @staticmethod
    def boundary(context, structure, buy):
        return (context.get('setup_structure') or {}).get('low' if buy else 'high')


class ConfluenceTradeMonitor:
    reason = 'preceding five-bar structure.'

    @staticmethod
    def boundary(context, structure, buy):
        return min(bar[2] for bar in structure) if buy else max(bar[1] for bar in structure)


class ORBTradeMonitor:
    reason = 'broken-out range edge (failed breakout).'

    @staticmethod
    def boundary(context, structure, buy):
        s = context.get('setup_structure') or {}
        return s.get('range_high' if buy else 'range_low')


MONITORS = {'BBMA':BBMATradeMonitor(), 'CONFLUENCE':ConfluenceTradeMonitor(), 'ORB':ORBTradeMonitor()}
