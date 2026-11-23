import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace as N

import duplicate_guard as dg
from dashboard_server import build_server
from dashboard_store import Store

MAGIC = {'BBMA': 816001, 'CONFLUENCE': 816002}


def pos(ticket, magic, comment, symbol='XAUUSD', typ=1, volume=0.02):
    return N(ticket=ticket, identifier=ticket, magic=magic, comment=comment, symbol=symbol, type=typ, volume=volume, time=1)


ROWS = [{'payload': json.dumps({'tag': 'SDCown000000000001', 'position_ticket': 11})},
        {'payload': json.dumps({'tag': 'SDBown000000000002'})}]


class DuplicateGuard(unittest.TestCase):
    def test_flags_only_bot_trades_this_copy_did_not_place(self):
        positions = [pos(11, 816002, 'SDCown000000000001'),         # ours (tag + ticket)
                     pos(12, 816001, 'SDBown000000000002'),         # ours (tag recorded before send)
                     pos(13, 0, 'manual trade'),                    # manual: not a bot trade
                     pos(14, 123456, 'other EA'),                   # another EA: ignored
                     pos(15, 816001, 'SDBforeign000000003', 'GBPJPY')]
        orders = [N(ticket=16, magic=816002, comment='SDCforeign000000004', symbol='EURUSD', type=3, volume_current=0.01, time_setup=2)]
        found = dg.foreign_trades(positions, orders, ROWS, MAGIC)
        self.assertEqual([(t['ticket'], t['kind'], t['strategy'], t['side']) for t in found],
                         [(15, 'POSITION', 'BBMA', 'SELL'), (16, 'ORDER', 'CONFLUENCE', 'SELL')])
        self.assertEqual(dg.foreign_trades(positions, orders, ROWS, MAGIC, acknowledged=[15, 16]), [])
        self.assertIn('ANOTHER BOT', dg.describe(found))

    def test_acknowledge_store_and_validation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = Store(Path(tmp) / 'd.sqlite3')
            dg.publish(store, [dict(ticket=15, symbol='GBPJPY'), dict(ticket=16, symbol='EURUSD')])
            self.assertEqual(dg.acknowledge(store, [15]), [15])
            with store.connect() as db:
                state = json.loads(db.execute("SELECT value FROM meta WHERE key='duplicate_bot'").fetchone()[0])
            self.assertEqual((state['status'], [t['ticket'] for t in state['trades']]), ('WARNING', [16]))
            dg.acknowledge(store, [16])
            with store.connect() as db:
                self.assertEqual(json.loads(db.execute("SELECT value FROM meta WHERE key='duplicate_bot'").fetchone()[0])['status'], 'CLEAR')
            for bad in ([], ['15'], [True], [0], list(range(1, 300))):
                with self.assertRaises(ValueError):
                    dg.acknowledge(store, bad)

    def test_executor_pauses_new_entries_while_unacknowledged(self):
        from demo_execution import DemoExecutor
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            ex = DemoExecutor.__new__(DemoExecutor)
            ex.store = Store(Path(tmp) / 'd.sqlite3'); ex.rows = lambda: ROWS
            ex.check_duplicates([pos(15, 816001, 'SDBforeign000000003')], [])
            self.assertTrue(ex.duplicate_block)
            dg.acknowledge(ex.store, [15])
            ex.check_duplicates([pos(15, 816001, 'SDBforeign000000003')], [])
            self.assertFalse(ex.duplicate_block)

    def test_dashboard_acknowledge_endpoint(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = Store(Path(tmp) / 'd.sqlite3')
            server = build_server(store, 0); threading.Thread(target=server.serve_forever, daemon=True).start()
            port = server.server_address[1]; base = f'http://127.0.0.1:{port}'
            def post(body, origin=None):
                h = {'Content-Type': 'application/json', 'X-Dashboard-Request': 'controls', 'Origin': origin or base}
                return urllib.request.urlopen(urllib.request.Request(base + '/api/duplicate-ack', json.dumps(body).encode(), h, method='POST'))
            try:
                with post({'tickets': [15, 16]}) as r:
                    self.assertEqual(json.load(r)['acknowledged'], [15, 16])
                for bad, code in (({'tickets': 'x'}, 400), ({'tickets': [15], 'extra': 1}, 400)):
                    with self.assertRaises(urllib.error.HTTPError) as e:
                        post(bad)
                    self.assertEqual(e.exception.code, code)
                with self.assertRaises(urllib.error.HTTPError) as e:
                    post({'tickets': [15]}, origin='http://evil.example')
                self.assertEqual(e.exception.code, 403)
            finally:
                server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
