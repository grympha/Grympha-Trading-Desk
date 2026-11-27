"""Readable MT5 order comments that still work as the bot's tracking ID.

Entry comments look like "BBMA Reentry CSA #a1b2c3" or "Confluence 4/5 #a1b2c3". The text after
'#' is a short hash of (account, setup key): the whole comment is recorded in the ledger before the
order is sent, so the bot can recognise its own orders (and spot another bot copy) by comment.
MT5 limits comments to 31 characters and does not allow changing the comment of an open trade.
"""
from __future__ import annotations
import hashlib

MAX_LEN = 31
SETUPS = {'CSA_REENTRY': 'Reentry CSA', 'MOMENTUM_REENTRY': 'Reentry Momentum', 'MHV_REVERSAL': 'MHV Reversal'}


def strategy_name(strategy: str) -> str:
    if str(strategy).startswith('AGENT:'):
        return 'LT agent'                       # a Live Test agent trading live
    return {'BBMA': 'BBMA', 'CONFLUENCE': 'Confluence', 'ORB': 'ORB'}.get(strategy, str(strategy).title())


def short_id(account_hash: str, key: str) -> str:
    return hashlib.sha256((str(account_hash) + str(key)).encode()).hexdigest()[:6]


def entry_comment(strategy: str, setup: str | None, score, account_hash: str, key: str) -> str:
    if strategy == 'BBMA':
        text = 'BBMA ' + SETUPS.get(str(setup), 'Setup')
    elif strategy == 'ORB':
        text = 'ORB Retest'
    elif str(strategy).startswith('AGENT:'):
        text = 'LT ' + str(setup or 'agent').replace('_', ' ').title()   # e.g. 'LT Amd Po3 #a1b2c3'
    else:
        text = f'Confluence {int(score)}/5' if isinstance(score, (int, float)) and 0 < score <= 5 else 'Confluence'
    suffix = ' #' + short_id(account_hash, key)
    return text[:MAX_LEN - len(suffix)] + suffix


def action_comment(strategy: str, action: str) -> str:
    """Comment for management orders, e.g. 'BBMA TP1 partial', 'Confluence early exit'."""
    return f'{strategy_name(strategy)} {action}'[:MAX_LEN]
