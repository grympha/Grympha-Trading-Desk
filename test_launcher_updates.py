"""Launcher self-update wiring: drives the real launcher.App headlessly (withdrawn Tk window,
fake tray, stubbed dialogs) to prove the update state machine — REAL-mode refusal, restart on
the new version, back-off after a failed version, and nothing else running mid-update.
Settings writes are redirected away from the real launcher_settings.json."""
from __future__ import annotations
import os
import unittest
from unittest import mock

try:
    import tkinter as tk
    import launcher
    import app_updater as up
    _SKIP = os.name != 'nt'
except Exception:  # pragma: no cover - non-Windows / no Tk
    _SKIP = True


class FakeTray:
    def __init__(self):
        self.balloons, self.tips = [], []

    def balloon(self, title, text):
        self.balloons.append((title, text))

    def set_tooltip(self, text):
        self.tips.append(text)

    def stop(self):
        pass


class RunningBot:
    def poll(self):
        return None          # still running


AVAILABLE = dict(state='AVAILABLE', message='1 update(s) available (aaa → bbb).', commits=['bbb new feature'],
                 remote='bbb', local='aaa')


@unittest.skipIf(_SKIP, 'Windows launcher with Tk only')
class LauncherUpdateFlow(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(launcher.App, 'acquire_lock', lambda self: None),     # user's launcher may hold the lock
            mock.patch.object(launcher.App, 'build_telegram', lambda self, root: None),  # no .env reads in tests
            mock.patch.object(launcher.App, 'start_tray', lambda self: setattr(self, 'tray', FakeTray())),
            mock.patch.object(launcher, 'save_settings', lambda *_a, **_k: None),    # never write real settings
            mock.patch.object(launcher, 'load_settings', lambda *_a, **_k: _defaults()),  # clean defaults, not the real file
            mock.patch.object(launcher.messagebox, 'showinfo', self._dialog('info')),
            mock.patch.object(launcher.messagebox, 'showwarning', self._dialog('warning')),
            mock.patch.object(launcher.messagebox, 'showerror', self._dialog('error')),
            mock.patch.object(launcher.messagebox, 'askyesno', self._dialog('ask', False)),
        ]
        self.dialogs = []
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = launcher.App(self.root)
        self.addCleanup(self._release_app)   # runs BEFORE root.destroy (cleanups are LIFO)
        self.after_calls = []
        self.app.root.after = lambda ms, fn=None, *a: self.after_calls.append((ms, fn))
        self.applied = []
        p = mock.patch.object(launcher.app_updater, 'apply', lambda *a, **k: self.applied.append(a) or {})
        p.start()
        self.addCleanup(p.stop)

    def _release_app(self):
        """Free the App's Tk variables on the main thread while their interpreter is alive."""
        import gc
        self.app = None
        gc.collect()

    def _dialog(self, kind, answer=None):
        def f(title, msg, **_k):
            self.dialogs.append((kind, title, msg))
            return answer
        return f

    # ------------------------------------------------------------------
    def test_update_panel_is_built(self):
        self.assertIn('Installed:', self.app.version_text.get())
        self.assertEqual(str(self.app.update_install_button['state']), 'disabled')

    def test_available_without_auto_install_enables_button_and_notifies(self):
        self.app.settings['update_auto_install'] = False
        self.app.on_update_status(dict(AVAILABLE), manual=False)
        self.assertEqual(str(self.app.update_install_button['state']), 'normal')
        self.assertTrue(any(t == 'Update available' for t, _ in self.app.tray.balloons))
        self.assertFalse(self.app.update_busy)

    def test_auto_install_never_runs_during_real_execution(self):
        self.app.settings['update_auto_install'] = True
        self.app.bot, self.app.last_run = RunningBot(), ('real', 8765)
        with mock.patch.object(launcher.threading, 'Thread') as thread:
            self.app.on_update_status(dict(AVAILABLE), manual=False)
        thread.assert_not_called()                                   # no install thread started
        self.assertFalse(self.app.update_busy)
        self.assertIn('REAL', self.app.update_state.get())

    def test_auto_install_runs_in_demo(self):
        self.app.settings['update_auto_install'] = True
        self.app.bot, self.app.last_run = RunningBot(), ('demo', 8765)
        with mock.patch.object(launcher.threading, 'Thread') as thread:
            self.app.on_update_status(dict(AVAILABLE), manual=False)
        thread.assert_called_once()
        self.assertTrue(self.app.update_busy)

    def test_installed_update_restarts_running_bot_and_flags_launcher_change(self):
        self.app.bot, self.app.last_run = RunningBot(), ('demo', 8765)
        stops = []
        self.app.stop = lambda: stops.append(1)
        self.app.on_update_result(dict(ok=True, state='UPDATED', message='Updated aaa → bbb (1 commit(s)).',
                                       launcher_changed=True, to='bbb'))
        self.assertEqual(stops, [1])
        self.assertTrue(self.app.update_restart_pending)
        self.assertTrue(any(t == 'Update installed' for t, _ in self.app.tray.balloons))
        logged = []
        while not self.app.events.empty():
            logged.append(self.app.events.get_nowait()[1])
        self.assertTrue(any('reopen' in str(m) for m in logged))

    def test_exit_after_update_restarts_with_crash_safety_net(self):
        self.app.update_restart_pending, self.app.stop_requested = True, True
        self.app.last_run = ('demo', 8765)
        self.app.bot = mock.Mock(poll=lambda: 0)
        self.app.events.put(('exited', 0))
        self.app.drain()
        self.assertFalse(self.app.update_restart_pending)
        self.assertFalse(self.app.stop_requested)
        self.assertIn((1500, self.app.do_restart), self.after_calls)

    def test_failed_version_is_not_retried_automatically(self):
        self.app.on_update_result(dict(ok=False, state='TESTS_FAILED', message='failed', to='bbb',
                                       launcher_changed=False))
        self.assertEqual(self.app.failed_target, 'bbb')
        self.app.settings['update_auto_install'] = True
        with mock.patch.object(launcher.threading, 'Thread') as thread:
            self.app.on_update_status(dict(AVAILABLE), manual=False)
        thread.assert_not_called()
        self.assertIn('failed its tests earlier', self.app.update_state.get())

    def test_nothing_else_runs_while_updating(self):
        self.app.update_busy = True
        ran = []
        self.app.task('Checking', lambda: ran.append(1))
        self.assertEqual(ran, [])
        self.assertTrue(any(k == 'info' and t == 'Updating' for k, t, _ in self.dialogs))
        self.app.close()                                             # closing is refused too
        self.assertTrue(self.root.winfo_exists())


def _defaults():
    import launcher_extras
    return dict(launcher_extras.DEFAULT_SETTINGS)


if __name__ == '__main__':
    unittest.main()
