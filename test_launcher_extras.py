import json
import struct
import tempfile
import unittest
from pathlib import Path

import launcher_extras as lx


class LauncherExtras(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_account_validation(self):
        self.assertEqual(lx.validate_real_account(' 37369024 ', ' RoboForex-Pro '), (37369024, 'RoboForex-Pro'))
        for login, server in [('', 'X'), ('12a', 'X'), ('0', 'X'), ('-5', 'X'), ('123', ''), ('123', '   ')]:
            with self.assertRaises(ValueError):
                lx.validate_real_account(login, server)

    def test_save_real_account_keeps_other_settings_and_backs_up(self):
        cfg = self.dir / 'config.json'
        cfg.write_text(json.dumps({'symbols': ['XAUUSD'], 'real_execution': {'account_login': None, 'account_server': '', 'max_positions': 6}}))
        backup = lx.save_real_account(cfg, 37369024, 'RoboForex-Pro')
        data = json.loads(cfg.read_text())
        self.assertEqual(data['real_execution'], {'account_login': 37369024, 'account_server': 'RoboForex-Pro', 'max_positions': 6})
        self.assertEqual(data['symbols'], ['XAUUSD'])
        self.assertIsNone(json.loads(backup.read_text())['real_execution']['account_login'])
        self.assertEqual(lx.read_real_account(cfg), (37369024, 'RoboForex-Pro'))
        self.assertIs(type(data['real_execution']['account_login']), int)  # the executor requires an int

    def test_startup_entry_created_and_removed_in_given_folder(self):
        folder = self.dir / 'Startup'
        self.assertFalse(lx.startup_enabled(folder))
        path = lx.set_startup(True, folder, app_root=Path('D:/Grympha Trading Desk'))
        self.assertTrue(lx.startup_enabled(folder))
        text = path.read_text()
        self.assertIn('START_APP.cmd" --startup', text); self.assertIn('/min', text)
        lx.set_startup(False, folder)
        self.assertFalse(lx.startup_enabled(folder))

    def test_real_autostart_needs_saved_permission(self):
        p = self.dir / 's.json'
        lx.save_settings({'autostart_bot': True, 'autostart_mode': 'real', 'unknown': 1}, p)
        s = lx.load_settings(p)
        self.assertEqual(s['autostart_mode'], 'analysis'); self.assertNotIn('unknown', s); self.assertTrue(s['autostart_bot'])
        lx.save_settings({'autostart_mode': 'real', 'allow_real_auto': True}, p)
        self.assertEqual(lx.load_settings(p)['autostart_mode'], 'real')
        lx.save_settings({'autostart_mode': 'real', 'allow_real_auto': 'yes'}, p)  # only a real True counts
        self.assertEqual(lx.load_settings(p)['autostart_mode'], 'analysis')

    def test_last_mode_is_remembered_and_validated(self):
        p = self.dir / 's2.json'
        self.assertEqual(lx.load_settings(p)['last_mode'], 'analysis')  # first run default
        lx.save_settings({'last_mode': 'real'}, p)
        self.assertEqual(lx.load_settings(p)['last_mode'], 'real')
        lx.save_settings({'last_mode': 'bogus'}, p)
        self.assertEqual(lx.load_settings(p)['last_mode'], 'analysis')

    def test_icon_is_valid_ico(self):
        data = lx.write_icon(self.dir / 'i.ico').read_bytes()
        reserved, kind, count = struct.unpack('<HHH', data[:6])
        w, h, _, _, planes, bpp, size, offset = struct.unpack('<BBBBHHII', data[6:22])
        self.assertEqual((reserved, kind, count, w, h, bpp, offset), (0, 1, 1, 32, 32, 32, 22))
        self.assertEqual(len(data), 22 + size)


if __name__ == '__main__':
    unittest.main()
