import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request
from contextlib import closing
from pathlib import Path

import db_backup
from dashboard_store import Store


class Backup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True); self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'dashboard.sqlite3')
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('marker','\"original\"')")
        (self.root / 'config.json').write_text('{"symbols":["XAUUSD"]}')
        (self.root / 'launcher_settings.json').write_text('{"port":8765}')
        (self.root / '.env').write_text('TELEGRAM_BOT_TOKEN=secret')
        self.s = db_backup.settings({})

    def tearDown(self):
        self.tmp.cleanup()

    def test_backup_is_consistent_restorable_and_skips_env(self):
        stop = threading.Event()
        def writer():  # the bot keeps writing during the backup
            i = 0
            while not stop.is_set():
                with self.store.connect() as db:
                    db.execute("INSERT OR REPLACE INTO meta VALUES ('counter',?)", (json.dumps(i),))
                i += 1
        t = threading.Thread(target=writer); t.start()
        try:
            folder = db_backup.backup_now(self.store.path, self.root, self.s)
        finally:
            stop.set(); t.join()
        with closing(sqlite3.connect(folder / 'dashboard.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(json.loads(db.execute("SELECT value FROM meta WHERE key='marker'").fetchone()[0]), 'original')
        self.assertTrue((folder / 'config.json').exists()); self.assertTrue((folder / 'launcher_settings.json').exists())
        self.assertFalse((folder / '.env').exists())  # Telegram token is never copied
        restored = Store(folder / 'dashboard.sqlite3')
        self.assertIn('meta', restored.snapshot())

    def test_keeps_only_newest(self):
        s = dict(self.s, keep=3)
        for i in range(5):
            db_backup.backup_now(self.store.path, self.root, s, now=1_700_000_000 + i * 60)
        kept = [p.name for p in db_backup.list_backups(self.root, s)]
        self.assertEqual(len(kept), 3); self.assertEqual(kept, sorted(kept))
        (self.root / 'backups' / 'my-notes').mkdir()  # folders without a backup inside are never touched
        db_backup.backup_now(self.store.path, self.root, s, now=1_700_001_000)
        self.assertTrue((self.root / 'backups' / 'my-notes').exists())

    def wait(self):
        for _ in range(100):
            if not db_backup._running.locked():
                return
            time.sleep(.05)

    def test_schedule_and_status(self):
        self.assertTrue(db_backup.start(self.store, self.root, {})); self.wait()
        with self.store.connect() as db:
            st = json.loads(db.execute("SELECT value FROM meta WHERE key='db_backup'").fetchone()[0])
        self.assertTrue(st['last_ok_epoch'] and st['kept'] == 1 and not st['running'])
        self.assertFalse(db_backup.start(self.store, self.root, {}))  # not due again for 24 h
        self.assertTrue(db_backup.start(self.store, self.root, {}, force=True)); self.wait()  # Back up now
        self.assertFalse(db_backup.start(self.store, self.root, {'backup': {'enabled': False}}))

    def test_dashboard_back_up_now(self):
        import dashboard_server
        old = dashboard_server.ROOT; dashboard_server.ROOT = self.root
        server = dashboard_server.build_server(self.store, 0); threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            req = urllib.request.Request(base + '/api/backup-now', b'{}', {'Content-Type': 'application/json', 'X-Dashboard-Request': 'controls', 'Origin': base}, method='POST')
            with urllib.request.urlopen(req) as r:
                self.assertTrue(json.load(r)['started'])
            self.wait()
            self.assertEqual(len(db_backup.list_backups(self.root, self.s)), 1)
        finally:
            server.shutdown(); server.server_close(); dashboard_server.ROOT = old


if __name__ == '__main__':
    unittest.main()
