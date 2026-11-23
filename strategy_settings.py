"""Which setups each strategy may use, chosen in the dashboard (Settings → Strategy setups).

Stored in the dashboard database (meta key 'strategy_settings') and read by the bot every
cycle, so changes apply without a restart. Defaults come from config.json until the first save.
BBMA: CSA re-entry, Momentum re-entry, MHV reversal (experimental).
Confluence: minimum five-factor score (H4 trend direction is always required).
ORB: trading on/off, trading days, skipped months, signals per day, stop-loss buffer/factor, take-profit mode.
"""
from __future__ import annotations
import json

KEY = 'strategy_settings'
BBMA_SETUPS = {'csa_reentry': 'CSA_REENTRY', 'momentum_reentry': 'MOMENTUM_REENTRY', 'mhv': 'MHV_REVERSAL'}
SCORES = (3, 4, 5)


def defaults(cfg: dict | None = None) -> dict:
    cfg = cfg or {}
    score = int((cfg.get('confluence') or {}).get('min_score', 4))
    return {'bbma': {'csa_reentry': True, 'momentum_reentry': True, 'mhv': bool(cfg.get('enable_mhv', False))},
            'confluence': {'min_score': score if score in SCORES else 4},
            'orb': orb_defaults(cfg)}


ORB_KEYS = {'trade', 'skip_weekdays', 'skip_months', 'signals_per_day', 'sl_multiplier',
            'buy_sl_buffer_points', 'sell_sl_buffer_points', 'tp_mode', 'fixed_rr'}


def orb_defaults(cfg: dict | None = None) -> dict:
    o = (cfg or {}).get('orb') or {}
    return {'trade': bool(o.get('trade', False)), 'skip_weekdays': list(o.get('skip_weekdays', [2, 3])),
            'skip_months': list(o.get('skip_months', [12])), 'signals_per_day': int(o.get('signals_per_day', 1)),
            'sl_multiplier': float(o.get('sl_multiplier', 0.9)), 'buy_sl_buffer_points': int(o.get('buy_sl_buffer_points', 60)),
            'sell_sl_buffer_points': int(o.get('sell_sl_buffer_points', 100)), 'tp_mode': o.get('tp_mode', 'dynamic'),
            'fixed_rr': float(o.get('fixed_rr', 2.0))}


def _number(v, lo, hi, name, integer=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
        raise ValueError(f'{name} must be between {lo} and {hi}')
    if integer:
        if float(v) != int(v):
            raise ValueError(f'{name} must be a whole number')
        return int(v)
    return float(v)


def validate_orb(orb) -> dict:
    if not isinstance(orb, dict) or set(orb) != ORB_KEYS:
        raise ValueError('ORB settings must contain: ' + ', '.join(sorted(ORB_KEYS)))
    if not isinstance(orb['trade'], bool):
        raise ValueError('ORB trade must be true/false')
    days, months = orb['skip_weekdays'], orb['skip_months']
    if not isinstance(days, list) or not all(type(d) is int and 0 <= d <= 4 for d in days) or len(set(days)) != len(days):
        raise ValueError('ORB skipped weekdays must be Mon=0 .. Fri=4')
    if len(days) == 5:
        raise ValueError('ORB must trade on at least one weekday')
    if not isinstance(months, list) or not all(type(m) is int and 1 <= m <= 12 for m in months) or len(set(months)) != len(months):
        raise ValueError('ORB skipped months must be 1 .. 12')
    if len(months) == 12:
        raise ValueError('ORB must trade in at least one month')
    if orb['tp_mode'] not in ('dynamic', 'fixed'):
        raise ValueError('ORB take-profit mode must be dynamic or fixed')
    return {'trade': orb['trade'], 'skip_weekdays': sorted(days), 'skip_months': sorted(months),
            'signals_per_day': _number(orb['signals_per_day'], 1, 3, 'Signals per day', True),
            'sl_multiplier': _number(orb['sl_multiplier'], 0.5, 1.0, 'SL distance factor'),
            'buy_sl_buffer_points': _number(orb['buy_sl_buffer_points'], 0, 2000, 'BUY SL buffer', True),
            'sell_sl_buffer_points': _number(orb['sell_sl_buffer_points'], 0, 2000, 'SELL SL buffer', True),
            'tp_mode': orb['tp_mode'], 'fixed_rr': _number(orb['fixed_rr'], 1.0, 5.0, 'Fixed RR')}


def validate(values) -> dict:
    if not isinstance(values, dict) or set(values) != {'bbma', 'confluence', 'orb'}:
        raise ValueError('Expected bbma, confluence and orb settings')
    bbma, conf, orb = values['bbma'], values['confluence'], values['orb']
    orb = validate_orb(orb)
    if not isinstance(bbma, dict) or set(bbma) != set(BBMA_SETUPS) or not all(isinstance(v, bool) for v in bbma.values()):
        raise ValueError('BBMA setups must be true/false for: ' + ', '.join(BBMA_SETUPS))
    if not any(bbma.values()):
        raise ValueError('Keep at least one BBMA setup on (or turn BBMA off per pair in Pairs on / off)')
    if not isinstance(conf, dict) or set(conf) != {'min_score'} or type(conf['min_score']) is not int or conf['min_score'] not in SCORES:
        raise ValueError('Confluence minimum score must be 3, 4 or 5')
    return {'bbma': dict(bbma), 'confluence': dict(conf), 'orb': dict(orb)}


def load(store, cfg: dict | None = None) -> dict:
    base = defaults(cfg)
    if store is None:
        return base
    try:
        with store.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()
        if not row:
            return base
        saved = json.loads(row['value'])
        if isinstance(saved, dict):  # settings saved before ORB (or before an ORB option) existed keep their values
            saved['orb'] = {**base['orb'], **(saved.get('orb') or {})}
        return validate(saved)
    except Exception:
        return base  # a damaged value never stops the bot; it falls back to config.json


def save(store, values) -> dict:
    clean = validate(values)
    from pair_controls import entry_lock
    with entry_lock, store.connect() as db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (KEY, json.dumps(clean)))
    return clean


def bbma_overrides(settings: dict) -> dict:
    """Extra keys for the BBMA analysis config: MHV switch and the allowed re-entry setup types."""
    b = settings['bbma']
    return {'enable_mhv': b['mhv'], '_allowed_setups': [t for k, t in BBMA_SETUPS.items() if b[k]]}
