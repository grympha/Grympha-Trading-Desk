"""Self-update for the Grympha Trading Desk launcher (stdlib only).

Pulls the latest code from the GitHub remote — but only after the new code has passed the
test suite in a SEPARATE temporary checkout, so the live folder and the running bot are never
exposed to untested code.

Safety model (this updates a live trading bot):
  1. `git fetch origin <branch>`; the target commit is pinned, so a push made while an update
     is running cannot change what gets installed.
  2. Refuse unless the live folder is on <branch>, has no modified TRACKED files, and is
     strictly BEHIND origin. Fast-forward only: never merge, rebase, reset or force-push.
     Untracked and git-ignored files (config.json, .env, databases, logs) are never touched.
  3. Check the target out into a temporary git worktree OUTSIDE the live folder and run the
     test suite there (pytest if installed, otherwise unittest).
  4. Only if the tests pass — and the live folder is still unchanged — fast-forward it with
     `git merge --ff-only`. Git applies that atomically: on any conflict (for example an
     untracked file that the update would overwrite) nothing is changed.
  5. If requirements.txt changed, the new requirements are installed before the tests (the new
     code may need them). If the update is then rejected, the previous requirements are
     reinstalled.
  6. The caller (launcher) restarts the bot so the new code takes effect. Changes to launcher
     files are reported so the launcher itself can be reopened.

Nothing here places orders, reads credentials, or contacts any service except the configured
git remote.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Files that the RUNNING launcher has already loaded: a change needs a launcher restart.
LAUNCHER_FILES = frozenset({'launcher.py', 'launcher_extras.py', 'launcher_checks.py',
                            'app_updater.py', 'START_APP.cmd'})

# status() states
NO_GIT, NOT_GIT, ERROR = 'NO_GIT', 'NOT_GIT', 'ERROR'
WRONG_BRANCH, DIRTY, DIVERGED, AHEAD = 'WRONG_BRANCH', 'DIRTY', 'DIVERGED', 'AHEAD'
UP_TO_DATE, AVAILABLE = 'UP_TO_DATE', 'AVAILABLE'
# apply() outcomes
UPDATED, TESTS_FAILED, DEPS_FAILED = 'UPDATED', 'TESTS_FAILED', 'DEPS_FAILED'
MERGE_FAILED, CHANGED = 'MERGE_FAILED', 'CHANGED_DURING_UPDATE'

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _run(args, cwd, timeout=120, env=None):
    """Run a command quietly (no console window). Never raises for a non-zero exit."""
    try:
        return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=timeout, creationflags=_NO_WINDOW,
                              env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'PYTHONUTF8': '1',
                                   **(env or {})})
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args, 124, exc.stdout or '', f'timed out after {timeout}s')
    except OSError as exc:
        return subprocess.CompletedProcess(args, 127, '', str(exc))


def _git(root, *args, timeout=120):
    return _run(['git', *args], root, timeout=timeout)


def _out(r):
    return (r.stdout or '').strip()


def _tail(text, lines=40):
    return '\n'.join((text or '').strip().splitlines()[-lines:])


def current_version(root) -> dict:
    """The installed commit (no network). {'hash','date','subject'} or {} when unavailable."""
    if shutil.which('git') is None:
        return {}
    r = _git(root, 'log', '-1', '--format=%h|%cs|%s')
    if r.returncode or '|' not in _out(r):
        return {}
    h, d, s = _out(r).split('|', 2)
    return dict(hash=h, date=d, subject=s)


def status(root, branch: str = 'main', fetch: bool = True) -> dict:
    """Compare the live folder with origin/<branch>. Read-only apart from `git fetch`."""
    root = Path(root)
    base = dict(state=ERROR, branch=branch, local=None, remote=None, behind=0, ahead=0,
                commits=[], files=[], launcher_changed=False, requirements_changed=False, message='')
    if shutil.which('git') is None:
        return {**base, 'state': NO_GIT,
                'message': 'Git is not installed, so automatic updates are unavailable. Install Git for Windows.'}
    r = _git(root, 'rev-parse', '--is-inside-work-tree')
    if r.returncode or _out(r) != 'true':
        return {**base, 'state': NOT_GIT,
                'message': 'This folder is not a git checkout, so automatic updates are unavailable.'}
    cur = _out(_git(root, 'rev-parse', '--abbrev-ref', 'HEAD'))
    if cur != branch:
        return {**base, 'state': WRONG_BRANCH,
                'message': f'On "{cur}", not "{branch}". Updates only install on {branch}.'}
    if fetch:
        f = _git(root, 'fetch', '--quiet', 'origin', branch, timeout=90)
        if f.returncode:
            return {**base, 'message': 'Could not reach GitHub: ' + _tail(f.stderr or f.stdout, 3)}
    remote_ref = f'origin/{branch}'
    local, remote = _git(root, 'rev-parse', '--short', 'HEAD'), _git(root, 'rev-parse', '--short', remote_ref)
    if local.returncode or remote.returncode:
        return {**base, 'message': f'Could not read {remote_ref}. Has it been fetched?'}
    behind = int(_out(_git(root, 'rev-list', '--count', f'HEAD..{remote_ref}')) or 0)
    ahead = int(_out(_git(root, 'rev-list', '--count', f'{remote_ref}..HEAD')) or 0)
    dirty = bool(_out(_git(root, 'status', '--porcelain', '--untracked-files=no')))
    commits, files = [], []
    if behind:
        commits = _out(_git(root, 'log', '--format=%h %s', '-n', '50', f'HEAD..{remote_ref}')).splitlines()
        files = _out(_git(root, 'diff', '--name-only', 'HEAD', remote_ref)).splitlines()
    info = {**base, 'local': _out(local), 'remote': _out(remote), 'behind': behind, 'ahead': ahead,
            'commits': commits, 'files': files,
            'launcher_changed': any(Path(p).name in LAUNCHER_FILES for p in files),
            'requirements_changed': 'requirements.txt' in files}
    if not behind:
        if ahead:
            return {**info, 'state': AHEAD,
                    'message': f'{ahead} local commit(s) not on GitHub yet; nothing to install.'}
        return {**info, 'state': UP_TO_DATE, 'message': f'Up to date ({info["local"]}).'}
    if ahead:
        return {**info, 'state': DIVERGED,
                'message': f'Local and GitHub histories differ ({ahead} local, {behind} remote commit(s)). '
                           'Not updated automatically; resolve it with git.'}
    if dirty:
        return {**info, 'state': DIRTY,
                'message': f'{behind} update(s) available, but tracked files were edited in this folder. '
                           'Commit or discard those edits first; nothing was changed.'}
    return {**info, 'state': AVAILABLE, 'message': f'{behind} update(s) available ({info["local"]} -> {info["remote"]}).'}


def default_test_cmd(python: str) -> list[str]:
    """pytest when it is installed for `python`, otherwise the stdlib unittest runner."""
    if _run([python, '-c', 'import pytest'], Path.cwd(), timeout=30).returncode == 0:
        return [python, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--maxfail=5']
    return [python, '-m', 'unittest', 'discover', '-s', '.', '-p', 'test*.py', '-q']


def _pip(python: str, requirements: Path, log) -> bool:
    log(f'Installing requirements ({requirements.name})...')
    r = _run([python, '-m', 'pip', 'install', '-q', '-r', str(requirements)], requirements.parent, timeout=900)
    if r.returncode:
        log('pip failed: ' + _tail(r.stderr or r.stdout, 8))
    return r.returncode == 0


def apply(root, python: str, branch: str = 'main', log=None, test_cmd=None, test_timeout: int = 900,
          pip=None) -> dict:
    """Install the pending update if (and only if) it passes the tests. Returns a result dict:
    {'ok', 'state', 'message', 'from', 'to', 'commits', 'launcher_changed', 'requirements_changed'}.
    `test_cmd` / `pip` are injectable for testing; by default the real suite and pip are used."""
    root = Path(root)
    sink = log or (lambda _msg: None)

    def log(msg):  # a broken logger (e.g. a console that cannot encode a character) must never abort an update
        try:
            sink(msg)
        except Exception:
            pass
    pip = pip or (lambda req: _pip(python, req, log))
    st = status(root, branch, fetch=True)
    result = dict(ok=False, state=st['state'], message=st['message'], commits=st['commits'],
                  launcher_changed=st['launcher_changed'], requirements_changed=st['requirements_changed'],
                  **{'from': st['local'], 'to': st['remote']})
    if st['state'] == UP_TO_DATE:
        return {**result, 'ok': True}
    if st['state'] != AVAILABLE:
        return result

    prev = _out(_git(root, 'rev-parse', 'HEAD'))
    target = _out(_git(root, 'rev-parse', f'origin/{branch}'))   # pinned for the whole update
    log(f'Update {st["local"]} -> {st["remote"]}: {st["behind"]} commit(s).')

    def restore_requirements():
        if st['requirements_changed']:
            log('Restoring the previous requirements...')
            pip(root / 'requirements.txt')

    # 1) new requirements first: the new code (and its tests) may depend on them
    if st['requirements_changed']:
        tmp_req = Path(tempfile.mkdtemp(prefix='grympha_req_')) / 'requirements.txt'
        try:
            tmp_req.write_text(_out(_git(root, 'show', f'{target}:requirements.txt')) + '\n', encoding='utf-8')
            if not pip(tmp_req):
                restore_requirements()
                return {**result, 'state': DEPS_FAILED,
                        'message': 'New requirements could not be installed; the update was not applied.'}
        finally:
            shutil.rmtree(tmp_req.parent, ignore_errors=True)

    # 2) test the target in a temporary worktree outside the live folder
    base = Path(tempfile.mkdtemp(prefix='grympha_update_'))
    wt = base / 'wt'
    try:
        add = _git(root, 'worktree', 'add', '--detach', str(wt), target, timeout=180)
        if add.returncode:
            restore_requirements()
            return {**result, 'state': ERROR, 'message': 'Could not prepare the test checkout: ' + _tail(add.stderr, 3)}
        cmd = test_cmd or default_test_cmd(python)
        log('Testing the update in a separate checkout (the running bot is not affected)...')
        t = _run(cmd, wt, timeout=test_timeout, env={'PYTHONDONTWRITEBYTECODE': '1'})
        if t.returncode:
            log(_tail(t.stdout + '\n' + t.stderr, 25))
            restore_requirements()
            return {**result, 'state': TESTS_FAILED,
                    'message': f'The update failed its tests, so it was NOT installed. Still on {st["local"]}.'}
        log('Tests passed.')
    finally:
        _git(root, 'worktree', 'remove', '--force', str(wt), timeout=120)
        _git(root, 'worktree', 'prune')
        shutil.rmtree(base, ignore_errors=True)

    # 3) the live folder must be exactly as it was when the update started
    if _out(_git(root, 'rev-parse', 'HEAD')) != prev or _out(_git(root, 'status', '--porcelain', '--untracked-files=no')):
        restore_requirements()
        return {**result, 'state': CHANGED,
                'message': 'This folder changed while the update was being tested; nothing was installed. Try again.'}

    # 4) fast-forward (atomic: git changes nothing if any file would conflict)
    m = _git(root, 'merge', '--ff-only', '--quiet', target, timeout=180)
    if m.returncode:
        restore_requirements()
        return {**result, 'state': MERGE_FAILED,
                'message': 'Git could not fast-forward this folder (nothing was changed): ' + _tail(m.stderr or m.stdout, 4)}
    now = _out(_git(root, 'rev-parse', '--short', 'HEAD'))
    log(f'Installed {now}.')
    return {**result, 'ok': True, 'state': UPDATED, 'to': now,
            'message': f'Updated {st["local"]} -> {now} ({st["behind"]} commit(s)).'}


if __name__ == '__main__':  # manual use:  py -3.11 app_updater.py [--apply]
    try:  # Windows consoles are often cp1252: never crash on a character in a commit subject
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass
    here = Path(__file__).resolve().parent
    if '--apply' in sys.argv:
        res = apply(here, sys.executable, log=print)
    else:
        res = status(here)
    print(res['state'], '-', res['message'])
    sys.exit(0 if res['state'] in (UP_TO_DATE, AVAILABLE, UPDATED, AHEAD) else 1)
