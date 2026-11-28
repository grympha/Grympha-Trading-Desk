"""Phone access through `tailscale serve`: a *.ts.net name may view the dashboard, never change it."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from dashboard_server import build_server, remote_hosts
from dashboard_store import Store

TS = 'office-pc.tail1234.ts.net'


class RemoteView(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = build_server(Store(Path(self.tmp.name) / 'db.sqlite3'), 0, remote_hosts({'dashboard_remote_hosts': [TS]}))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.tmp.cleanup()

    def request(self, method, path, host, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        c.putrequest(method, path, skip_host=True)
        for k, v in dict({'Host': host}, **(headers or {})).items():
            c.putheader(k, v)
        data = json.dumps(body).encode() if body is not None else b''
        if body is not None:
            c.putheader('Content-Length', str(len(data)))
        c.endheaders(data or None)
        r = c.getresponse(); r.read(); c.close()
        return r.status

    def test_tailscale_name_can_view(self):
        self.assertEqual(self.request('GET', '/home', TS), 200)
        self.assertEqual(self.request('GET', '/api/home', TS), 200)
        self.assertEqual(self.request('GET', '/', TS.upper()), 200)          # host names are case-insensitive

    def test_other_names_are_refused(self):
        for host in ('evil.example.com', 'other-pc.tail1234.ts.net', f'{TS}.evil.com'):
            self.assertEqual(self.request('GET', '/home', host), 403, host)

    def test_remote_cannot_change_anything(self):
        # no body: the refusal comes before the body is read (a sent body can race the close on Windows)
        h = {'Origin': f'https://{TS}', 'X-Dashboard-Request': 'controls', 'Content-Type': 'application/json', 'Content-Length': '0'}
        self.assertEqual(self.request('POST', '/api/controls', TS, None, h), 403)
        # even if a proxy rewrote Host to the local name, the phone's Origin does not match it
        self.assertEqual(self.request('POST', '/api/controls', f'127.0.0.1:{self.server.server_port}', None, h), 403)

    def test_only_ts_net_names_are_accepted(self):
        got = remote_hosts({'dashboard_remote_hosts': ['Office-PC.Tail1234.TS.NET.', 'mydesk.com', '0.0.0.0', '', 'a.ts.net.evil']})
        self.assertEqual(got, ('office-pc.tail1234.ts.net',))
        self.assertEqual(remote_hosts({}), ())


if __name__ == '__main__':
    unittest.main()
