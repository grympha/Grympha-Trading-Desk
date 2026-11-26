"""Telegram settings (.env via launcher) and event alerts. No real message is ever sent."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import telegram_config as tg
import telegram_notify as tn
from dashboard_store import Store

TOKEN = '123456789:' + 'A' * 35


class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True); self.env = Path(self.tmp.name) / '.env'

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_keeps_other_lines_and_backs_up(self):
        self.env.write_text('# my notes\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\nTRADING_ECONOMICS_API_KEY=keep-me\n')
        backup = tg.write(True, TOKEN, '987654321', self.env)
        text = self.env.read_text()
        self.assertIn('TRADING_ECONOMICS_API_KEY=keep-me', text); self.assertIn('# my notes', text)
        self.assertEqual(tg.read(self.env), dict(enabled=True, token=TOKEN, chat='987654321', configured=True))
        self.assertTrue(backup and backup.exists())
        tg.write(False, TOKEN, '987654321', self.env)
        self.assertIsNone(tg.active(self.env))  # switched off: nothing is sent
        self.assertEqual(self.env.read_text().count('TELEGRAM_BOT_TOKEN='), 1)

    def test_validation_and_old_env_files(self):
        for token, chat in (('nope', ''), (TOKEN, 'abc'), ('', '12 34')):
            with self.assertRaises(ValueError):
                tg.validate(token, chat)
        with self.assertRaises(ValueError):
            tg.write(True, TOKEN, '', self.env)  # enabling needs both
        self.env.write_text(f'TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_ID=-1001234567890\n')  # pre-launcher .env
        self.assertEqual(tg.active(self.env), (TOKEN, '-1001234567890'))
        self.assertNotIn(TOKEN[12:], tg.mask(TOKEN))


class NetworkErrors(unittest.TestCase):
    TOKEN = '123456789:' + 'A' * 35

    def fail_with(self, exc):
        with patch('urllib.request.urlopen', side_effect=exc), self.assertRaises(RuntimeError) as e:
            tg.send(self.TOKEN, '12345', 'hi')
        self.assertNotIn(self.TOKEN, str(e.exception))
        return str(e.exception)

    def test_each_cause_is_named(self):
        import ssl
        self.assertIn('certificate', self.fail_with(urllib.error.URLError(ssl.SSLCertVerificationError('CERTIFICATE_VERIFY_FAILED'))))
        self.assertIn('DNS', self.fail_with(urllib.error.URLError(OSError(11001, 'getaddrinfo failed'))))
        self.assertIn('timed out', self.fail_with(urllib.error.URLError(TimeoutError('timed out'))))
        self.assertIn('blocked', self.fail_with(urllib.error.URLError(ConnectionRefusedError(10061, 'refused'))))
        self.assertIn('OSError', self.fail_with(OSError('odd')))


def snap(orders=(), signals=(), meta=None):
    return {'meta': dict(meta or {}), 'demo_orders': [dict(o) for o in orders], 'signals': [dict(s) for s in signals]}


ROW = dict(key='k1', strategy='CONFLUENCE', symbol='XAUUSD', side='SELL', volume=0.01, reference=3850.0, sl=3870.0, tp2=3810.0,
           currency='USD', context={'setup': 'FIVE_FACTOR_CONFLUENCE'})


class Alerts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = Store(Path(self.tmp.name) / 'd.sqlite3')
        self.sent = []
        self.patch = patch('telegram_config.active', return_value=('t', 'c')); self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def notifier(self, frames):
        n = tn.Notifier(self.store, {}, mode='REAL', send=lambda t, c, text: self.sent.append(text))
        it = iter(frames)
        n.store.snapshot = lambda now=None: next(it)
        return n

    def drain(self, n):
        n.flush(3)

    def test_trade_lifecycle_messages(self):
        frames = [snap(), snap([dict(ROW, status='PENDING')]), snap([dict(ROW, status='OPEN', actual_entry=3849.5, actual_sl=3870.0)]),
                  snap([dict(ROW, status='OPEN_PARTIAL', actual_entry=3849.5, actual_sl=3870.0, profit=12.0)]),
                  snap([dict(ROW, status='OPEN_PARTIAL', actual_entry=3849.5, actual_sl=3849.6, profit=12.0)]),
                  snap([dict(ROW, status='CLOSED', actual_entry=3849.5, exit_price=3810.0, realised=39.5, close_reason=5)])]
        n = self.notifier(frames)
        events = [n.check() for _ in frames]
        self.drain(n)
        self.assertEqual(events, [[], ['order_placed'], ['filled'], ['tp1'], [], ['tp_hit']])  # SL-moved is off by default
        self.assertIn('Take-profit hit', self.sent[-1]); self.assertIn('+39.50 USD', self.sent[-1]); self.assertIn('REAL', self.sent[-1])
        self.assertIn('Confluence', self.sent[1])

    def test_sl_and_early_exit_and_preferences(self):
        tn.save_prefs(self.store, {**tn.defaults(), 'sl_moved': True, 'filled': False})
        frames = [snap(), snap([dict(ROW, status='OPEN', actual_sl=3870.0)]), snap([dict(ROW, status='OPEN', actual_sl=3850.0)]),
                  snap([dict(ROW, status='CLOSED', realised=-1.0, close_reason=4)]),
                  snap([dict(ROW, status='CLOSED', realised=-1.0, close_reason=4), dict(ROW, key='k2', status='OPEN')]),
                  snap([dict(ROW, status='CLOSED', realised=-1.0, close_reason=4), dict(ROW, key='k2', status='CLOSED', realised=3.0, close_reason=3)])]
        n = self.notifier(frames)
        self.assertEqual([n.check() for _ in frames], [[], [], ['sl_moved'], ['sl_hit'], [], ['closed_other']])
        self.drain(n)
        self.assertIn('early exit', self.sent[-1])

    def test_new_setup_alert_marks_analysis_only(self):
        sig = dict(key='ORB|XAUUSD:x', strategy='ORB', symbol='XAUUSD', side='BUY', setup='ORB_RETEST', signal_kind='REFERENCE',
                   lifecycle='REFERENCE', reference_current=True, entry=3000.0, stop_loss=2990.0, tp1=3010.0, tp2=3020.0, created=time.time())
        n = self.notifier([snap(), snap(signals=[sig]), snap(signals=[sig])])
        self.assertEqual([n.check() for _ in range(3)], [[], ['entry_signal'], []])
        self.drain(n)
        self.assertIn('analysis only', self.sent[0])  # ORB trading is off by default

    def test_restart_does_not_resend_and_first_run_primes(self):
        closed = snap([dict(ROW, status='CLOSED', realised=5.0, close_reason=5)])
        n = self.notifier([closed, closed])
        self.assertEqual(n.check(), [])  # first ever run: history is not announced
        again = self.notifier([closed])
        self.assertEqual(again.check(), [])

    def test_duplicate_bot_and_disabled_telegram(self):
        dup = {'duplicate_bot': {'status': 'WARNING', 'trades': [{'ticket': 5}]}}
        n = self.notifier([snap(), snap(meta=dup), snap(meta=dup)])
        self.assertEqual([n.check() for _ in range(3)], [[], ['health'], []])
        self.patch.stop()
        with patch('telegram_config.active', return_value=None):
            n2 = self.notifier([snap(), snap([dict(ROW, status='OPEN')])])
            n2.check(); n2.check(); before = len(self.sent); self.drain(n2)
            self.assertEqual(len(self.sent), before)  # switched off in launcher: nothing goes out
        self.patch.start()

    def test_dashboard_endpoints(self):
        from dashboard_server import build_server
        server = build_server(self.store, 0); threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        def post(path, body):
            h = {'Content-Type': 'application/json', 'X-Dashboard-Request': 'controls', 'Origin': base}
            return urllib.request.urlopen(urllib.request.Request(base + path, json.dumps(body).encode(), h, method='POST'))
        try:
            with post('/api/telegram-prefs', {**tn.defaults(), 'sl_moved': True}) as r:
                self.assertTrue(json.load(r)['prefs']['sl_moved'])
            with self.assertRaises(urllib.error.HTTPError) as e:
                post('/api/telegram-prefs', {'entry_signal': 'yes'})
            self.assertEqual(e.exception.code, 400)
            with patch('telegram_config.active', return_value=None), self.assertRaises(urllib.error.HTTPError) as e:
                post('/api/telegram-test', {})
            self.assertEqual(e.exception.code, 409)
            with patch('telegram_config.send', return_value=1) as fake, post('/api/telegram-test', {}) as r:
                self.assertTrue(json.load(r)['ok']); fake.assert_called_once()
            with urllib.request.urlopen(base + '/api/status') as r:
                body = r.read().decode()
            self.assertNotIn('TELEGRAM_BOT_TOKEN', body); self.assertIn('"telegram"', body)
        finally:
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
