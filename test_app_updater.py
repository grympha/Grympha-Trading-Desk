"""Tests for app_updater (launcher self-update) against REAL temporary git repositories:
a bare "GitHub" origin, a dev clone that pushes new commits, and a "live" install folder.
The test suite run inside the temporary worktree is injected (`test_cmd`) and pip is stubbed,
so these tests prove the git safety rules without running the real suite or touching packages."""
from __future__ import annotations
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import app_updater as up

GIT_ID = ['-c', 'user.name=Updater Test', '-c', 'user.email=updater@test.invalid',
          '-c', 'commit.gpgsign=false', '-c', 'core.autocrlf=false', '-c', 'init.defaultBranch=main']
PASS = [sys.executable, '-c', 'import sys; sys.exit(0)']
FAIL = [sys.executable, '-c', 'import sys; sys.exit(1)']


def git(cwd, *args):
    r = subprocess.run(['git', *GIT_ID, *args], cwd=str(cwd), capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f'git {args} failed: {r.stderr}')
    return r.stdout.strip()


@unittest.skipIf(shutil.which('git') is None, 'git not installed')
class UpdaterRepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='upd_test_'))
        self.origin = self.tmp / 'origin.git'
        git(self.tmp, 'init', '--bare', '-b', 'main', str(self.origin))
        self.dev = self.tmp / 'dev'
        git(self.tmp, 'clone', str(self.origin), str(self.dev))
        self.write(self.dev, 'app.py', 'VERSION = 1\n')
        self.write(self.dev, 'requirements.txt', 'numpy\n')
        self.commit_push('initial')
        self.live = self.tmp / 'live'
        git(self.tmp, 'clone', str(self.origin), str(self.live))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers -----------------------------------------------------------
    @staticmethod
    def write(repo, name, text):
        p = Path(repo) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')

    def commit_push(self, msg):
        git(self.dev, 'add', '-A')
        git(self.dev, 'commit', '-m', msg)
        git(self.dev, 'push', '-q', 'origin', 'main')

    def head(self, repo):
        return git(repo, 'rev-parse', 'HEAD')

    def apply(self, test_cmd=PASS, pip=None):
        return up.apply(self.live, sys.executable, 'main', test_cmd=test_cmd,
                        pip=pip or (lambda _req: True))

    # ---- status ------------------------------------------------------------
    def test_up_to_date(self):
        st = up.status(self.live)
        self.assertEqual(st['state'], up.UP_TO_DATE)
        self.assertEqual(st['behind'], 0)

    def test_available_lists_commits_and_changed_files(self):
        self.write(self.dev, 'app.py', 'VERSION = 2\n')
        self.commit_push('bump version')
        st = up.status(self.live)
        self.assertEqual(st['state'], up.AVAILABLE)
        self.assertEqual(st['behind'], 1)
        self.assertTrue(any('bump version' in c for c in st['commits']))
        self.assertEqual(st['files'], ['app.py'])
        self.assertFalse(st['launcher_changed'])
        self.assertFalse(st['requirements_changed'])

    def test_launcher_and_requirements_changes_are_flagged(self):
        self.write(self.dev, 'launcher.py', '# new launcher\n')
        self.write(self.dev, 'requirements.txt', 'numpy\npandas\n')
        self.commit_push('launcher + deps')
        st = up.status(self.live)
        self.assertTrue(st['launcher_changed'])
        self.assertTrue(st['requirements_changed'])

    def test_edited_tracked_file_blocks_the_update(self):
        self.write(self.dev, 'app.py', 'VERSION = 2\n')
        self.commit_push('v2')
        self.write(self.live, 'app.py', 'VERSION = 99  # local edit\n')
        self.assertEqual(up.status(self.live)['state'], up.DIRTY)

    def test_untracked_files_do_not_block(self):
        self.write(self.dev, 'app.py', 'VERSION = 2\n')
        self.commit_push('v2')
        self.write(self.live, 'config.json', '{"secret": "stays"}')    # like the real, ignored config
        self.assertEqual(up.status(self.live)['state'], up.AVAILABLE)

    def test_local_commits_make_it_ahead_then_diverged(self):
        self.write(self.live, 'local.txt', 'mine\n')
        git(self.live, 'add', 'local.txt')
        git(self.live, 'commit', '-m', 'local only')
        self.assertEqual(up.status(self.live)['state'], up.AHEAD)
        self.write(self.dev, 'app.py', 'VERSION = 2\n')
        self.commit_push('remote v2')
        self.assertEqual(up.status(self.live)['state'], up.DIVERGED)

    def test_other_branch_is_refused(self):
        git(self.live, 'checkout', '-q', '-b', 'feature')
        self.assertEqual(up.status(self.live)['state'], up.WRONG_BRANCH)

    def test_plain_folder_is_not_git(self):
        plain = self.tmp / 'plain'
        plain.mkdir()
        self.assertEqual(up.status(plain)['state'], up.NOT_GIT)

    def test_current_version_reads_installed_commit(self):
        v = up.current_version(self.live)
        self.assertEqual(v['subject'], 'initial')
        self.assertTrue(v['hash'] and v['date'])

    # ---- apply -------------------------------------------------------------
    def test_apply_up_to_date_is_a_no_op(self):
        before = self.head(self.live)
        res = self.apply()
        self.assertTrue(res['ok'])
        self.assertEqual(res['state'], up.UP_TO_DATE)
        self.assertEqual(self.head(self.live), before)

    def test_apply_fast_forwards_and_keeps_untracked_files(self):
        self.write(self.live, 'config.json', '{"secret": "stays"}')
        self.write(self.dev, 'app.py', 'VERSION = 2\n')
        self.commit_push('v2')
        res = self.apply()
        self.assertTrue(res['ok'], res['message'])
        self.assertEqual(res['state'], up.UPDATED)
        self.assertEqual(self.head(self.live), self.head(self.dev))
        self.assertEqual((self.live / 'app.py').read_text(), 'VERSION = 2\n')
        self.assertEqual((self.live / 'config.json').read_text(), '{"secret": "stays"}')
        self.assertEqual(len(git(self.live, 'worktree', 'list').splitlines()), 1)   # temp checkout removed

    def test_failed_tests_leave_the_live_folder_untouched(self):
        before = self.head(self.live)
        self.write(self.dev, 'app.py', 'VERSION = 2\n')
        self.commit_push('broken v2')
        res = self.apply(test_cmd=FAIL)
        self.assertFalse(res['ok'])
        self.assertEqual(res['state'], up.TESTS_FAILED)
        self.assertEqual(self.head(self.live), before)
        self.assertEqual((self.live / 'app.py').read_text(), 'VERSION = 1\n')
        self.assertEqual(len(git(self.live, 'worktree', 'list').splitlines()), 1)

    def test_tests_run_against_the_new_code_not_the_live_folder(self):
        # NEW_MARKER exists only in the incoming commit; the check passes only inside its checkout.
        self.write(self.dev, 'NEW_MARKER', 'x')
        self.commit_push('add marker')
        probe = [sys.executable, '-c', "import os,sys; sys.exit(0 if os.path.exists('NEW_MARKER') else 1)"]
        res = self.apply(test_cmd=probe)
        self.assertEqual(res['state'], up.UPDATED, res['message'])

    def test_untracked_file_collision_changes_nothing(self):
        before = self.head(self.live)
        self.write(self.dev, 'newfile.txt', 'from github\n')
        self.commit_push('add newfile')
        self.write(self.live, 'newfile.txt', 'local, untracked\n')
        res = self.apply()
        self.assertFalse(res['ok'])
        self.assertEqual(res['state'], up.MERGE_FAILED)
        self.assertEqual(self.head(self.live), before)
        self.assertEqual((self.live / 'newfile.txt').read_text(), 'local, untracked\n')

    def test_folder_changed_during_testing_is_not_overwritten(self):
        before = self.head(self.live)
        self.write(self.dev, 'app.py', 'VERSION = 2\n')
        self.commit_push('v2')
        live_app = str(self.live / 'app.py').replace('\\', '/')
        edit_live = [sys.executable, '-c', f"open('{live_app}','w').write('VERSION = 7  # edited meanwhile\\n')"]
        res = self.apply(test_cmd=edit_live)
        self.assertEqual(res['state'], up.CHANGED)
        self.assertEqual(self.head(self.live), before)
        self.assertIn('edited meanwhile', (self.live / 'app.py').read_text())

    def test_new_requirements_installed_before_tests_and_restored_on_failure(self):
        calls = []

        def pip(req):
            calls.append((req.name, Path(req).read_text(encoding='utf-8')))
            return True
        self.write(self.dev, 'requirements.txt', 'numpy\npandas\n')
        self.commit_push('add pandas')
        res = self.apply(test_cmd=FAIL, pip=pip)
        self.assertEqual(res['state'], up.TESTS_FAILED)
        self.assertEqual(len(calls), 2)
        self.assertIn('pandas', calls[0][1])          # 1) the NEW requirements, before testing
        self.assertNotIn('pandas', calls[1][1])       # 2) the previous ones restored after the failure

    def test_requirements_success_installs_once(self):
        calls = []
        self.write(self.dev, 'requirements.txt', 'numpy\npandas\n')
        self.commit_push('add pandas')
        res = self.apply(pip=lambda req: calls.append(Path(req).read_text()) or True)
        self.assertEqual(res['state'], up.UPDATED, res['message'])
        self.assertEqual(len(calls), 1)
        self.assertIn('pandas', calls[0])

    def test_a_crashing_logger_never_aborts_an_update(self):
        # e.g. print() to a cp1252 console that cannot encode a character in a message
        def broken_log(_msg):
            raise UnicodeEncodeError('charmap', '→', 0, 1, 'cannot encode')
        self.write(self.dev, 'app.py', 'VERSION = 2\n')
        self.commit_push('v2')
        res = up.apply(self.live, sys.executable, 'main', log=broken_log, test_cmd=PASS, pip=lambda _r: True)
        self.assertEqual(res['state'], up.UPDATED, res['message'])
        self.assertEqual(self.head(self.live), self.head(self.dev))

    def test_runtime_messages_are_console_safe_ascii(self):
        self.write(self.dev, 'app.py', 'VERSION = 2\n')
        self.commit_push('v2')
        for res in (up.status(self.live), self.apply()):
            res['message'].encode('ascii')            # raises if a non-ASCII character slipped back in

    def test_failed_dependency_install_aborts_the_update(self):
        before = self.head(self.live)
        self.write(self.dev, 'requirements.txt', 'numpy\nnot-a-real-package\n')
        self.commit_push('bad dep')
        results = iter([False, True])                 # new reqs fail, restore succeeds
        res = self.apply(pip=lambda _req: next(results))
        self.assertEqual(res['state'], up.DEPS_FAILED)
        self.assertEqual(self.head(self.live), before)


class UpdaterHelpers(unittest.TestCase):
    def test_default_test_cmd_prefers_pytest_when_installed(self):
        cmd = up.default_test_cmd(sys.executable)
        try:
            import pytest  # noqa: F401
            self.assertEqual(cmd[1:3], ['-m', 'pytest'])
        except ImportError:
            self.assertEqual(cmd[1:3], ['-m', 'unittest'])

    def test_launcher_files_cover_the_running_launcher(self):
        for name in ('launcher.py', 'launcher_extras.py', 'app_updater.py', 'START_APP.cmd'):
            self.assertIn(name, up.LAUNCHER_FILES)

    def test_updater_never_uses_destructive_git_commands(self):
        src = Path(up.__file__).read_text(encoding='utf-8')
        for bad in ("'reset'", "'rebase'", "'--force-with-lease'", "'push'", "'clean'", "'checkout'"):
            self.assertNotIn(bad, src, f'app_updater must not run git {bad}')
        self.assertIn("'--ff-only'", src)


if __name__ == '__main__':
    unittest.main()
