"""Launcher helpers (stdlib only): REAL account settings, Start-with-Windows, system tray icon.

Nothing here places orders or stores passwords. The REAL account number and server are
identifiers that the executor compares with the logged-in MT5 account before any REAL order.
"""
from __future__ import annotations
import json
import os
import shutil
import struct
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / 'launcher_settings.json'
ICON_FILE = ROOT / 'launcher_icon.ico'
LEGACY_STARTUP_NAME = 'Grympha Trading Desk.cmd'     # before each folder had its own entry
STARTUP_STAGGER_SECONDS = 20                         # a second desk starts this much later
DESK_NAME_MAX = 40
DEFAULT_SETTINGS = {'minimize_to_tray': True, 'start_in_tray': True, 'autostart_bot': False,
                    'autostart_mode': 'analysis', 'port': 8765, 'auto_restart': True,
                    'last_mode': 'analysis', 'allow_real_auto': False,
                    # self-update from GitHub (app_updater.py); never auto-installs while REAL is running
                    'update_auto_check': True, 'update_auto_install': True,
                    'update_check_minutes': 30, 'update_branch': 'main',
                    # shown in the launcher, dashboard, home page and Telegram alerts (several desks on one PC)
                    'desk_name': ''}


# ---------------------------------------------------------------- settings
def load_settings(path: Path = SETTINGS_FILE) -> dict:
    out = dict(DEFAULT_SETTINGS)
    try:
        out.update({k: v for k, v in json.loads(path.read_text(encoding='utf-8')).items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    allowed = ('analysis', 'demo', 'real') if out['allow_real_auto'] is True else ('analysis', 'demo')
    if out['autostart_mode'] not in allowed:  # REAL only after the person confirmed it once in the launcher
        out['autostart_mode'] = 'analysis'
    if out['last_mode'] not in ('analysis', 'demo', 'real'):
        out['last_mode'] = 'analysis'
    try:  # update cadence: whole minutes, at least 5 (GitHub is not polled more often than that)
        out['update_check_minutes'] = max(5, int(out['update_check_minutes']))
    except (TypeError, ValueError):
        out['update_check_minutes'] = DEFAULT_SETTINGS['update_check_minutes']
    branch = out['update_branch']
    if not (isinstance(branch, str) and branch and all(c.isalnum() or c in '-_./' for c in branch)
            and not branch.startswith(('-', '/')) and '..' not in branch):
        out['update_branch'] = DEFAULT_SETTINGS['update_branch']
    for key in ('update_auto_check', 'update_auto_install'):
        if not isinstance(out[key], bool):
            out[key] = DEFAULT_SETTINGS[key]
    out['desk_name'] = clean_desk_name(out['desk_name'])
    return out


def clean_desk_name(value) -> str:
    """Printable, single line, at most DESK_NAME_MAX characters; anything else becomes ''."""
    if not isinstance(value, str):
        return ''
    text = ' '.join(''.join(c if c.isprintable() else ' ' for c in value).split())
    return text[:DESK_NAME_MAX]


def save_settings(values: dict, path: Path = SETTINGS_FILE) -> None:
    data = {**load_settings(path), **{k: v for k, v in values.items() if k in DEFAULT_SETTINGS}}
    tmp = path.with_suffix('.tmp'); tmp.write_text(json.dumps(data, indent=2), encoding='utf-8'); os.replace(tmp, path)


# ---------------------------------------------------------------- REAL account in config.json
def validate_real_account(login_text: str, server: str) -> tuple[int, str]:
    login_text, server = str(login_text).strip(), str(server).strip()
    if not login_text.isdigit() or int(login_text) <= 0:
        raise ValueError('Account number must be digits only, e.g. 12345678.')
    if not server or len(server) > 100:
        raise ValueError('Enter the broker server exactly as MT5 shows it, e.g. RoboForex-Pro.')
    return int(login_text), server


def read_real_account(config: Path) -> tuple[int | None, str]:
    try:
        r = json.loads(config.read_text(encoding='utf-8-sig')).get('real_execution', {})
        return r.get('account_login'), r.get('account_server') or ''
    except (OSError, ValueError):
        return None, ''


def save_real_account(config: Path, login: int, server: str) -> Path:
    """Write real_execution.account_login/server, keeping every other setting. Returns the backup path."""
    cfg = json.loads(config.read_text(encoding='utf-8-sig'))
    section = cfg.setdefault('real_execution', {})
    section['account_login'], section['account_server'] = int(login), str(server)
    backup = config.with_name(config.name + '.backup.' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    shutil.copy2(config, backup)
    tmp = config.with_suffix('.launcher.tmp'); tmp.write_text(json.dumps(cfg, indent=2), encoding='utf-8'); os.replace(tmp, config)
    return backup


# ---------------------------------------------------------------- Start with Windows (per-user Startup folder)
def startup_folder() -> Path:
    return Path(os.environ.get('APPDATA', str(Path.home() / 'AppData/Roaming'))) / 'Microsoft/Windows/Start Menu/Programs/Startup'


def startup_name(app_root: Path = ROOT) -> str:
    """One Startup entry per app folder, so two desks on one PC can both start with Windows."""
    return f'Grympha Trading Desk ({app_root.name}).cmd'


def _launches(path: Path, app_root: Path) -> bool:
    try:
        return str(app_root / 'START_APP.cmd').lower() in path.read_text(encoding='utf-8', errors='ignore').lower()
    except OSError:
        return False


def _entry(folder: Path, app_root: Path) -> Path:
    """This folder's Startup file; a desk in another folder with the same name gets a path hash."""
    plain = folder / startup_name(app_root)
    if plain.is_file() and not _launches(plain, app_root):
        import hashlib
        tag = hashlib.sha1(str(app_root).lower().encode()).hexdigest()[:6]
        return folder / f'Grympha Trading Desk ({app_root.name} {tag}).cmd'
    return plain


def startup_enabled(folder: Path | None = None, app_root: Path = ROOT) -> bool:
    folder = folder or startup_folder()
    legacy = folder / LEGACY_STARTUP_NAME
    return _entry(folder, app_root).is_file() or (legacy.is_file() and _launches(legacy, app_root))


def set_startup(enabled: bool, folder: Path | None = None, app_root: Path = ROOT) -> Path:
    """Add/remove this folder's .cmd in the user's Startup folder (visible in Task Manager > Startup
    apps). The old shared entry is replaced when it starts this folder; another desk's entry is never
    touched. When other desks already start with Windows, this one waits STARTUP_STAGGER_SECONDS per
    desk so they do not connect to MT5 at the same moment."""
    folder = folder or startup_folder()
    target = _entry(folder, app_root)
    legacy = folder / LEGACY_STARTUP_NAME
    if legacy.is_file() and _launches(legacy, app_root):
        legacy.unlink()
    if enabled:
        folder.mkdir(parents=True, exist_ok=True)
        others = [x for x in folder.glob('Grympha Trading Desk*.cmd') if x.name != target.name]
        wait = STARTUP_STAGGER_SECONDS * len(others)
        target.write_text('@echo off\r\n'
                          'rem Created by the Grympha Trading Desk launcher ("Start with Windows").\r\n'
                          'rem Untick the option in the launcher, or delete this file, to stop auto-start.\r\n'
                          + (f'timeout /t {wait} /nobreak >nul\r\n' if wait else '')
                          + f'start "Grympha Trading Desk" /min "{app_root / "START_APP.cmd"}" --startup\r\n', encoding='utf-8')
    elif target.exists():
        target.unlink()
    return target


# ---------------------------------------------------------------- icon (generated; no image files needed)
def write_icon(path: Path = ICON_FILE) -> Path:
    size, teal, dark = 32, (0x5e, 0xe0, 0xbe), (0x0e, 0x1e, 0x1b)
    glyph = ['.###.', '#...#', '#....', '#.###', '#...#', '#...#', '.###.']  # "G"
    px = [[(0, 0, 0, 0)] * size for _ in range(size)]
    r = 7
    for y in range(size):
        for x in range(size):
            cx = min(max(x, r), size - 1 - r); cy = min(max(y, r), size - 1 - r)
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                px[y][x] = (*teal, 255)
    s, ox, oy = 3, 9, 5
    for gy, row in enumerate(glyph):
        for gx, ch in enumerate(row):
            if ch == '#':
                for dy in range(s):
                    for dx in range(s):
                        px[oy + gy * s + dy][ox + gx * s + dx] = (*dark, 255)
    xor = b''.join(bytes((b, g, r_, a)) for row in reversed(px) for (r_, g, b, a) in row)
    mask = b'\x00' * (4 * size)
    bmp = struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0) + xor + mask
    data = struct.pack('<HHH', 0, 1, 1) + struct.pack('<BBBBHHII', size, size, 0, 0, 1, 32, len(bmp), 22) + bmp
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------- console window (the cmd that hosts the launcher)
def show_console(visible: bool) -> None:
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 6 if visible else 0)  # SW_MINIMIZE / SW_HIDE
    except Exception:
        pass


# ---------------------------------------------------------------- system tray icon (Shell_NotifyIconW via ctypes)
class TrayIcon:
    """Tray icon on its own thread. `on_event(name)` is called from that thread with
    'show', 'dashboard', 'stop', 'exit'; the launcher forwards it to Tk via its event queue."""

    MENU = [(1, 'Show launcher', 'show'), (2, 'Open dashboard', 'dashboard'), (3, 'Stop bot', 'stop'), (5, 'Restart bot', 'restart'),
            (6, 'Check for updates', 'update'), (None, None, None), (4, 'Exit launcher', 'exit')]

    def __init__(self, tooltip: str, icon_path: Path, on_event):
        self.tooltip, self.icon_path, self.on_event = tooltip[:127], icon_path, on_event
        self.hwnd = None; self.ready = threading.Event(); self._nid = None
        self.thread = threading.Thread(target=self._run, name='tray-icon', daemon=True)

    def start(self) -> bool:
        self.thread.start(); self.ready.wait(5)
        return bool(self.hwnd)

    def _api(self):
        import ctypes
        from ctypes import wintypes as W
        self.ct, self.W = ctypes, W
        u, s, k = ctypes.WinDLL('user32', use_last_error=True), ctypes.WinDLL('shell32'), ctypes.WinDLL('kernel32')
        LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [('style', W.UINT), ('lpfnWndProc', self.WNDPROC), ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                        ('hInstance', W.HINSTANCE), ('hIcon', W.HICON), ('hCursor', W.HANDLE), ('hbrBackground', W.HBRUSH),
                        ('lpszMenuName', W.LPCWSTR), ('lpszClassName', W.LPCWSTR)]

        class GUID(ctypes.Structure):
            _fields_ = [('Data1', W.DWORD), ('Data2', W.WORD), ('Data3', W.WORD), ('Data4', ctypes.c_ubyte * 8)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [('cbSize', W.DWORD), ('hWnd', W.HWND), ('uID', W.UINT), ('uFlags', W.UINT), ('uCallbackMessage', W.UINT),
                        ('hIcon', W.HICON), ('szTip', W.WCHAR * 128), ('dwState', W.DWORD), ('dwStateMask', W.DWORD),
                        ('szInfo', W.WCHAR * 256), ('uVersion', W.UINT), ('szInfoTitle', W.WCHAR * 64), ('dwInfoFlags', W.DWORD),
                        ('guidItem', GUID), ('hBalloonIcon', W.HICON)]
        self.WNDCLASSW, self.NOTIFYICONDATAW = WNDCLASSW, NOTIFYICONDATAW
        u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]; u.DefWindowProcW.restype = LRESULT
        u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]; u.CreateWindowExW.restype = W.HWND
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]; u.RegisterClassW.restype = W.ATOM
        u.LoadImageW.argtypes = [W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT]; u.LoadImageW.restype = W.HANDLE
        u.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.LPVOID]; u.TrackPopupMenu.restype = W.BOOL
        u.AppendMenuW.argtypes = [W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR]
        u.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u.GetMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(W.MSG)]; u.TranslateMessage.argtypes = [ctypes.POINTER(W.MSG)]
        u.SetForegroundWindow.argtypes = [W.HWND]; u.DestroyWindow.argtypes = [W.HWND]; u.DestroyMenu.argtypes = [W.HMENU]
        u.RegisterWindowMessageW.argtypes = [W.LPCWSTR]; u.RegisterWindowMessageW.restype = W.UINT
        u.CreatePopupMenu.restype = W.HMENU
        s.Shell_NotifyIconW.argtypes = [W.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]; s.Shell_NotifyIconW.restype = W.BOOL
        k.GetModuleHandleW.argtypes = [W.LPCWSTR]; k.GetModuleHandleW.restype = W.HMODULE
        self.u, self.s, self.k = u, s, k

    def _notify(self, action, flags, info=None, title=None):
        nid = self.NOTIFYICONDATAW(); nid.cbSize = self.ct.sizeof(self.NOTIFYICONDATAW)
        nid.hWnd, nid.uID, nid.uFlags, nid.uCallbackMessage = self.hwnd, 1, flags, 0x8001  # WM_APP+1
        nid.hIcon, nid.szTip = self._icon, self.tooltip
        if info is not None:
            nid.szInfo, nid.szInfoTitle, nid.dwInfoFlags = info[:255], (title or '')[:63], 0x1  # NIIF_INFO
        return self.s.Shell_NotifyIconW(action, self.ct.byref(nid))

    def _run(self):
        try:
            self._api()
            W, u = self.W, self.u
            hinst = self.k.GetModuleHandleW(None)
            self._taskbar_created = u.RegisterWindowMessageW('TaskbarCreated')
            self._proc = self.WNDPROC(self._wndproc)  # keep a reference (callback must outlive the window)
            wc = self.WNDCLASSW(); wc.lpfnWndProc = self._proc; wc.hInstance = hinst; wc.lpszClassName = 'GrymphaTradingDeskTray'
            u.RegisterClassW(self.ct.byref(wc))
            self.hwnd = u.CreateWindowExW(0, wc.lpszClassName, 'Grympha Trading Desk tray', 0, 0, 0, 0, 0, None, None, hinst, None)
            self._icon = u.LoadImageW(None, str(self.icon_path), 1, 0, 0, 0x10 | 0x40)  # IMAGE_ICON, LR_LOADFROMFILE|LR_DEFAULTSIZE
            if not self.hwnd or not self._notify(0, 0x1 | 0x2 | 0x4):  # NIM_ADD, NIF_MESSAGE|NIF_ICON|NIF_TIP
                self.hwnd = None
        except Exception:
            self.hwnd = None
        finally:
            self.ready.set()
        if not self.hwnd:
            return
        msg = W.MSG()
        while u.GetMessageW(self.ct.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(self.ct.byref(msg)); u.DispatchMessageW(self.ct.byref(msg))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        u = self.u
        if msg == 0x8001:  # tray callback
            if lparam in (0x202, 0x203):  # left click / double click
                self.on_event('show')
            elif lparam == 0x205:  # right click
                self._menu()
            return 0
        if msg == self._taskbar_created:  # Explorer restarted: put the icon back
            self._notify(0, 0x1 | 0x2 | 0x4); return 0
        if msg == 0x0010:  # WM_CLOSE
            u.DestroyWindow(hwnd); return 0
        if msg == 0x0002:  # WM_DESTROY
            self._notify(2, 0); u.PostQuitMessage(0); return 0
        return u.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self):
        u, W = self.u, self.W
        menu = u.CreatePopupMenu()
        for cmd, label, _ in self.MENU:
            u.AppendMenuW(menu, 0x800 if cmd is None else 0, cmd or 0, label)  # MF_SEPARATOR / MF_STRING
        pt = W.POINT(); u.GetCursorPos(self.ct.byref(pt)); u.SetForegroundWindow(self.hwnd)
        chosen = u.TrackPopupMenu(menu, 0x100 | 0x2, pt.x, pt.y, 0, self.hwnd, None)  # TPM_RETURNCMD|TPM_RIGHTBUTTON
        u.DestroyMenu(menu)
        for cmd, _, event in self.MENU:
            if cmd and chosen == cmd:
                self.on_event(event)

    def set_tooltip(self, text: str):
        self.tooltip = text[:127]
        if self.hwnd:
            self._notify(1, 0x4)  # NIM_MODIFY, NIF_TIP

    def balloon(self, title: str, text: str):
        if self.hwnd:
            self._notify(1, 0x10, info=text, title=title)  # NIF_INFO

    def stop(self):
        if self.hwnd:
            self.u.PostMessageW(self.hwnd, 0x0010, 0, 0)
            self.thread.join(2)
            self.hwnd = None
