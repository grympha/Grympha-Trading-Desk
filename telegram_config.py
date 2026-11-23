"""Telegram settings in .env, shared by the launcher and the bot (standard library only).

.env keys: TELEGRAM_ENABLED (true/false), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. The launcher writes them;
the bot re-reads the file before sending, so changes apply without a restart. Other .env lines are kept.
The token is never printed, logged or shown in the dashboard (only a masked form).
"""
from __future__ import annotations
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / '.env'
TOKEN_RE = re.compile(r'^\d{5,12}:[A-Za-z0-9_-]{30,64}$')
CHAT_RE = re.compile(r'^(-?\d{3,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$')


def _parse(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            s = line.strip()
            if not s or s.startswith('#') or '=' not in s:
                continue
            k, v = s.split('=', 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def read(path: Path = ENV_PATH) -> dict:
    env = _parse(path)
    token, chat = env.get('TELEGRAM_BOT_TOKEN', ''), env.get('TELEGRAM_CHAT_ID', '')
    flag = env.get('TELEGRAM_ENABLED', '').lower()
    enabled = (flag in ('1', 'true', 'yes', 'on')) if flag else bool(token and chat)  # old .env: on when both set
    return dict(enabled=enabled, token=token, chat=chat, configured=bool(token and chat))


def active(path: Path = ENV_PATH):
    """(token, chat) when Telegram is enabled and complete, else None."""
    c = read(path)
    return (c['token'], c['chat']) if c['enabled'] and c['configured'] else None


def mask(token: str) -> str:
    return (token.split(':')[0] + ':••••' + token[-4:]) if token and ':' in token else ('set' if token else 'not set')


def validate(token: str, chat: str) -> tuple[str, str]:
    token, chat = token.strip(), chat.strip()
    if token and not TOKEN_RE.match(token):
        raise ValueError('Bot token should look like 123456789:AA… (from @BotFather).')
    if chat and not CHAT_RE.match(chat):
        raise ValueError('Chat ID should be a number (e.g. 123456789 or -100…) or @channelname.')
    return token, chat


def write(enabled: bool, token: str, chat: str, path: Path = ENV_PATH) -> Path | None:
    """Update the three Telegram keys in .env, keep every other line, back up the old file."""
    token, chat = validate(token, chat)
    if enabled and not (token and chat):
        raise ValueError('Enter both the bot token and the chat ID to enable Telegram.')
    lines = path.read_text(encoding='utf-8-sig').splitlines() if path.exists() else []
    values = {'TELEGRAM_ENABLED': 'true' if enabled else 'false', 'TELEGRAM_BOT_TOKEN': token, 'TELEGRAM_CHAT_ID': chat}
    seen, out = set(), []
    for line in lines:
        key = line.split('=', 1)[0].strip() if '=' in line and not line.strip().startswith('#') else None
        if key in values:
            if key not in seen:
                out.append(f'{key}={values[key]}'); seen.add(key)
        else:
            out.append(line)
    out += [f'{k}={v}' for k, v in values.items() if k not in seen]
    backup = None
    if path.exists():
        backup = path.with_name('.env.backup.' + datetime.now().strftime('%Y%m%d_%H%M%S'))
        shutil.copy2(path, backup)
    tmp = path.with_name('.env.tmp'); tmp.write_text('\n'.join(out) + '\n', encoding='utf-8'); os.replace(tmp, path)
    return backup


def api(token: str, method: str, body: dict | None = None, timeout: float = 12) -> dict:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}', data, {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as exc:  # never include the URL (it contains the token) in errors
        try:
            desc = json.loads(exc.read()).get('description', '')
        except Exception:
            desc = ''
        raise RuntimeError(f'Telegram refused the request ({exc.code}) {desc}'.strip()) from None
    except OSError:
        raise RuntimeError('Cannot reach Telegram; check the internet connection') from None
    if not payload.get('ok'):
        raise RuntimeError('Telegram refused the request: ' + str(payload.get('description', '')))
    return payload.get('result', {})


def send(token: str, chat: str, text: str) -> int:
    return api(token, 'sendMessage', {'chat_id': chat, 'text': text[:4000], 'disable_web_page_preview': True}).get('message_id', 0)


def find_chats(token: str) -> list[dict]:
    """Chats that recently messaged the bot (send /start to your bot first)."""
    out, seen = [], set()
    for u in api(token, 'getUpdates', {'limit': 50}):
        msg = u.get('message') or u.get('channel_post') or u.get('my_chat_member') or {}
        chat = msg.get('chat') or {}
        if chat.get('id') is not None and chat['id'] not in seen:
            seen.add(chat['id'])
            out.append(dict(id=str(chat['id']), name=chat.get('title') or ' '.join(x for x in (chat.get('first_name'), chat.get('last_name')) if x) or chat.get('username') or '', type=chat.get('type', '')))
    return out
