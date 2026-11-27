"""Which desk is this? A name per app folder, so two desks (two MT5 accounts) on one PC can be told apart
in the launcher, the dashboard, the home page and Telegram alerts.

The name is the launcher's "Desk name" (launcher_settings.json in this folder). The dashboard and home
page fall back to the MT5 broker server and account type when no name is set; Telegram alerts only
carry a name the person chose, so a single unnamed desk sends exactly what it sent before.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / 'launcher_settings.json'


def configured_name(path: Path | None = None) -> str:
    try:
        raw = json.loads((path or SETTINGS_FILE).read_text(encoding='utf-8')).get('desk_name', '')
    except (OSError, ValueError, AttributeError):
        return ''
    from launcher_extras import clean_desk_name
    return clean_desk_name(raw)


def desk_name(meta: dict | None = None, path: Path | None = None) -> str:
    """The chosen name, else "<MT5 server> · <DEMO/REAL>" from the bot's account snapshot, else ''."""
    name = configured_name(path)
    if name:
        return name
    acct = (meta or {}).get('mt5_account') or {}
    server, kind = acct.get('server'), acct.get('account_type')
    return ' · '.join(str(x) for x in (server, kind) if x) if server else ''


def tag_message(text: str, path: Path | None = None) -> str:
    """Telegram text with the desk name in front, e.g. "[Account 2 · Real] 🔔 New setup …"."""
    name = configured_name(path)
    return f'[{name}] {text}' if name else text
