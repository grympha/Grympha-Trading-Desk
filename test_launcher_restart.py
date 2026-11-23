"""Crash auto-restart policy of the launcher (no window is created)."""
import unittest
from types import SimpleNamespace

import launcher


def stub(mode='analysis', auto_restart=True):
    s = SimpleNamespace(last_run=(mode, 8765), settings={'auto_restart': auto_restart}, restart_times=[], tray=None,
                        log=[], scheduled=[], status=SimpleNamespace(set=lambda *_: None),
                        RESTART_DELAYS=launcher.App.RESTART_DELAYS)
    s.write = s.log.append
    s.do_restart = lambda: None
    s.root = SimpleNamespace(after=lambda ms, fn: s.scheduled.append(ms))
    return s


class AutoRestart(unittest.TestCase):
    def crash(self, s):
        launcher.App.handle_crash(s, 1)

    def test_analysis_and_demo_restart_with_backoff(self):
        for mode in ('analysis', 'demo'):
            s = stub(mode)
            for _ in range(5):
                self.crash(s)
            self.assertEqual(s.scheduled, [15000, 30000, 60000, 120000, 300000])

    def test_pauses_after_five_crashes_in_30_minutes(self):
        s = stub()
        for _ in range(6):
            self.crash(s)
        self.assertEqual(len(s.scheduled), 5)
        self.assertIn('auto-restart paused', s.log[-1])

    def test_old_crashes_expire(self):
        s = stub(); s.restart_times = [0, 1, 2, 3, 4]  # long ago
        self.crash(s)
        self.assertEqual(s.scheduled, [15000])

    def test_real_restarts_only_when_allowed(self):
        s = stub('real'); s.settings['allow_real_auto'] = True
        self.crash(s)
        self.assertEqual(s.scheduled, [15000])

    def test_real_is_never_restarted(self):
        s = stub('real')
        self.crash(s)
        self.assertEqual(s.scheduled, [])
        self.assertIn('NOT restarted automatically', s.log[-1])

    def test_respects_setting_off(self):
        s = stub(auto_restart=False)
        self.crash(s)
        self.assertEqual(s.scheduled, [])


class RealAutoStart(unittest.TestCase):
    def test_auto_start_refuses_real_without_permission(self):
        s = stub('real'); s.settings.update(allow_real_auto=False, port=8765)
        s.port_number = lambda: 8765
        s.mode = SimpleNamespace(get=lambda: 'real')
        s.python = lambda: self.fail('REAL check must not even run without permission')
        launcher.App.start(s, auto=True)
        self.assertIn('REAL auto-start is not allowed', s.log[-1])


class StartupNotice(unittest.TestCase):
    def test_explains_why_bot_did_not_start(self):
        s = stub(); s.settings['autostart_bot'] = False
        launcher.App.startup_notice(s)
        self.assertIn('was not started', s.log[-1])

    def test_announces_auto_start(self):
        s = stub(); s.settings.update(autostart_bot=True, autostart_mode='analysis')
        launcher.App.startup_notice(s)
        self.assertIn('will start automatically in analysis', s.log[-1])


if __name__ == '__main__':
    unittest.main()
