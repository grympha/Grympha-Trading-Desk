import copy
import json
import threading
import unittest
import urllib.error
import urllib.request

from dashboard_server import build_server, light_snapshot

ORDER = dict(key='k1', symbol='XAUUSD', status='OPEN',
             context=dict(condition={'name': 'BULLISH'}, setup='CSA', chart=[{'time': 1}] * 50,
                          review={'timeframes': {'H1': [1] * 50}}))
SNAP = dict(version='t', server_epoch=0, signals=[], meta={}, demo_orders=[ORDER],
            symbols=[dict(symbol='XAUUSD', chart=[1, 2], charts={'M5': [1, 2]}, review_market={'H1': [1] * 20}),
                     dict(symbol='EURUSD', chart=[3])])


class FakeStore:
    def snapshot(self):
        return copy.deepcopy(SNAP)


class LightStatus(unittest.TestCase):
    def test_trims_heavy_trade_context_but_keeps_summary(self):
        s = light_snapshot(copy.deepcopy(SNAP))
        ctx = s['demo_orders'][0]['context']
        self.assertNotIn('chart', ctx); self.assertNotIn('review', ctx)
        self.assertEqual(ctx['setup'], 'CSA'); self.assertEqual(ctx['condition']['name'], 'BULLISH')
        self.assertTrue(s['demo_orders'][0]['context_trimmed'])

    def test_single_chart_dropped_only_when_multi_timeframe_present(self):
        s = light_snapshot(copy.deepcopy(SNAP))
        self.assertNotIn('chart', s['symbols'][0]); self.assertIn('charts', s['symbols'][0])
        self.assertEqual(s['symbols'][1]['chart'], [3])  # old bot without charts keeps its chart
        self.assertNotIn('review_market', s['symbols'][0])

    def test_status_is_light_and_trade_endpoint_returns_full_record(self):
        server = build_server(FakeStore(), 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            with urllib.request.urlopen(base + '/api/status') as r:
                status = json.load(r)
            self.assertNotIn('chart', status['demo_orders'][0]['context'])
            with urllib.request.urlopen(base + '/api/trade?key=k1') as r:
                full = json.load(r)
            self.assertEqual(len(full['context']['chart']), 50)
            for path in ['/api/trade?key=nope', '/api/trade']:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base + path)
                self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
