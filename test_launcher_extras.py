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
        self.assertEqual(lx.validate_real_account(' 12345678 ', ' RoboForex-Pro '), (12345678, 'RoboForex-Pro'))
        for login, server in [('', 'X'), ('12a', 'X'), ('0', 'X'), ('-5', 'X'), ('123', ''), ('123', '   ')]:
            with self.assertRaises(ValueError):
                lx.validate_real_account(login, server)

    def test_save_real_account_keeps_other_settings_and_backs_up(self):
        cfg = self.dir / 'config.json'
        cfg.write_text(json.dumps({'symbols': ['XAUUSD'], 'real_execution': {'account_login': None, 'account_server': '', 'max_positions': 6}}))
        backup = lx.save_real_account(cfg, 12345678, 'RoboForex-Pro')
        data = json.loads(cfg.read_text())
        self.assertEqual(data['real_execution'], {'account_login': 12345678, 'account_server': 'RoboForex-Pro', 'max_positions': 6})
        self.assertEqual(data['symbols'], ['XAUUSD'])
        self.assertIsNone(json.loads(backup.read_text())['real_execution']['account_login'])
        self.assertEqual(lx.read_real_account(cfg), (12345678, 'RoboForex-Pro'))
        self.assertIs(type(data['real_execution']['account_login']), int)  # the executor requires an int

    def test_startup_entry_created_and_removed_in_given_folder(self):
        folder = self.dir / 'Startup'
        app = Path('D:/Grympha Trading Desk')
        self.assertFalse(lx.startup_enabled(folder, app_root=app))
        path = lx.set_startup(True, folder, app_root=app)
        self.assertTrue(lx.startup_enabled(folder, app_root=app))
        text = path.read_text()
        self.assertIn('START_APP.cmd" --startup', text); self.assertIn('/min', text)
        lx.set_startup(False, folder, app_root=app)
        self.assertFalse(lx.startup_enabled(folder, app_root=app))

    def test_two_desks_each_start_with_windows(self):
        folder = self.dir / 'Startup'
        a, b = Path('D:/Grock AI Bot/Grympha Trading Desk'), Path('D:/Grock AI Bot/Grympha Desk Acct2')
        pa = lx.set_startup(True, folder, app_root=a)
        pb = lx.set_startup(True, folder, app_root=b)
        self.assertEqual((pa.name, pb.name), ('Grympha Trading Desk (Grympha Trading Desk).cmd', 'Grympha Trading Desk (Grympha Desk Acct2).cmd'))
        self.assertNotIn('timeout', pa.read_text())
        self.assertIn('timeout /t 20 /nobreak', pb.read_text())               # the second desk waits
        self.assertIn(str(b / 'START_APP.cmd'), pb.read_text())
        lx.set_startup(False, folder, app_root=a)                              # only touches its own entry
        self.assertFalse(lx.startup_enabled(folder, app_root=a))
        self.assertTrue(lx.startup_enabled(folder, app_root=b))

    def test_old_shared_entry_is_recognised_and_replaced(self):
        folder = self.dir / 'Startup'; folder.mkdir()
        a, b = Path('D:/Grock AI Bot/Grympha Trading Desk'), Path('D:/Grock AI Bot/Grympha Desk Acct2')
        legacy = folder / lx.LEGACY_STARTUP_NAME
        legacy.write_text(f'@echo off\r\nstart "Grympha Trading Desk" /min "{a / "START_APP.cmd"}" --startup\r\n')
        self.assertTrue(lx.startup_enabled(folder, app_root=a))
        self.assertFalse(lx.startup_enabled(folder, app_root=b))
        lx.set_startup(True, folder, app_root=b)                               # another desk leaves it alone
        self.assertTrue(legacy.exists())
        lx.set_startup(True, folder, app_root=a)                               # its own desk renames it
        self.assertFalse(legacy.exists())
        self.assertTrue((folder / lx.startup_name(a)).exists())

    def test_same_folder_name_elsewhere_gets_its_own_entry(self):
        folder = self.dir / 'Startup'
        a, b = Path('C:/Desks/Grympha Trading Desk'), Path('E:/Grympha Trading Desk')
        pa, pb = lx.set_startup(True, folder, app_root=a), lx.set_startup(True, folder, app_root=b)
        self.assertNotEqual(pa, pb)
        self.assertTrue(lx.startup_enabled(folder, app_root=a) and lx.startup_enabled(folder, app_root=b))

    def test_desk_name_is_cleaned(self):
        p = self.dir / 's3.json'
        self.assertEqual(lx.load_settings(p)['desk_name'], '')
        lx.save_settings({'desk_name': '  Account 2\n·  Real\t' + 'x' * 60}, p)
        self.assertEqual(lx.load_settings(p)['desk_name'], ('Account 2 · Real ' + 'x' * 60)[:40])
        lx.save_settings({'desk_name': 42}, p)
        self.assertEqual(lx.load_settings(p)['desk_name'], '')

    def test_desk_identity_and_telegram_tag(self):
        import desk_identity as di
        p = self.dir / 's4.json'
        meta = {'mt5_account': {'server': 'RoboForex-Pro', 'account_type': 'REAL'}}
        self.assertEqual((di.desk_name(meta, p), di.desk_name(None, p), di.tag_message('🔔 New setup', p)),
                         ('RoboForex-Pro · REAL', '', '🔔 New setup'))              # unnamed: alerts unchanged
        lx.save_settings({'desk_name': 'Account 2 · Real'}, p)
        self.assertEqual((di.desk_name(meta, p), di.tag_message('🔔 New setup', p)),
                         ('Account 2 · Real', '[Account 2 · Real] 🔔 New setup'))

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

    def test_update_settings_defaults_and_validation(self):
        p = self.dir / 'u.json'
        s = lx.load_settings(p)
        self.assertEqual((s['update_auto_check'], s['update_auto_install'], s['update_check_minutes'], s['update_branch']),
                         (True, True, 30, 'main'))
        lx.save_settings({'update_check_minutes': 1}, p)                       # GitHub is not polled more than every 5 min
        self.assertEqual(lx.load_settings(p)['update_check_minutes'], 5)
        lx.save_settings({'update_check_minutes': 'often'}, p)
        self.assertEqual(lx.load_settings(p)['update_check_minutes'], 30)
        for bad in ('', '-main', '../x', 'a b', 'main;rm', 5):                  # never handed to git as an option/path trick
            lx.save_settings({'update_branch': bad}, p)
            self.assertEqual(lx.load_settings(p)['update_branch'], 'main', bad)
        lx.save_settings({'update_branch': 'release/v2'}, p)
        self.assertEqual(lx.load_settings(p)['update_branch'], 'release/v2')
        lx.save_settings({'update_auto_install': 'yes'}, p)                    # only a real bool counts
        self.assertTrue(lx.load_settings(p)['update_auto_install'] is True)
        lx.save_settings({'update_auto_install': False}, p)
        self.assertFalse(lx.load_settings(p)['update_auto_install'])

    def test_tray_menu_offers_update_check(self):
        events = [e for _, _, e in lx.TrayIcon.MENU if e]
        self.assertIn('update', events)
        ids = [c for c, _, _ in lx.TrayIcon.MENU if c]
        self.assertEqual(len(ids), len(set(ids)))                                # menu command ids stay unique

    def test_icon_is_valid_ico(self):
        data = lx.write_icon(self.dir / 'i.ico').read_bytes()
        reserved, kind, count = struct.unpack('<HHH', data[:6])
        w, h, _, _, planes, bpp, size, offset = struct.unpack('<BBBBHHII', data[6:22])
        self.assertEqual((reserved, kind, count, w, h, bpp, offset), (0, 1, 1, 32, 32, 32, 22))
        self.assertEqual(len(data), 22 + size)


if __name__ == '__main__':
    unittest.main()
