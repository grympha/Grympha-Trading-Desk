"""BBMA targets frozen from the latest closed M15 candle at signal time."""
import json
import math

from pair_controls import entry_lock


def mode(store):
    if store is None:
        return 'existing'
    with store.connect() as db:
        row = db.execute("SELECT value FROM meta WHERE key='bbma_tp_mode'").fetchone()
    return json.loads(row['value']) if row else 'existing'


def save_mode(store, settings):
    if not isinstance(settings, dict) or set(settings) != {'mode'} or settings['mode'] not in ('existing','structural'):
        raise ValueError('Invalid BBMA TP mode')
    with entry_lock, store.connect() as db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('bbma_tp_mode', json.dumps(settings['mode'])))


def target_plan(plan, candle, cfg, digits, point):
    buy = plan['side'] == 'BUY'
    sign = 1 if buy else -1
    side = 'high' if buy else 'low'
    grid = cfg.get('_tick_size', point)
    entry = plan['entry']
    minimum = max(grid, cfg.get('_minimum_stop_distance', 0))
    zone = [(f'MA{n} {side.upper()}', float(candle[f'ma{n}_{side}'])) for n in (5,10)]
    zone.sort(key=lambda level: sign*level[1])
    mid = ('Mid BB', float(candle['mid']))
    band = ('Upper BB' if buy else 'Lower BB', float(candle['upper' if buy else 'lower']))
    reversal = plan['setup'] == 'MHV_REVERSAL'
    priority = [*zone,mid,band] if reversal else [mid,*zone,band]
    qualifying = []
    for name, raw in priority:
        if not math.isfinite(raw) or raw <= 0:
            continue
        price = round((math.floor(raw/grid+1e-9) if buy else math.ceil(raw/grid-1e-9))*grid,digits)
        if price > 0 and sign*(price-entry) >= minimum-grid*1e-6:
            qualifying.append(dict(source=name,price=price))
    if not qualifying:
        return None
    mandatory = qualifying[0]
    later = next((x for x in qualifying[1:] if sign*(x['price']-mandatory['price']) >= grid*(1-1e-6)),None)
    runner = later if cfg.get('_structural_partial') else None
    final = runner or mandatory
    return dict(plan,tp1=mandatory['price'],tp2=final['price'],structural_tp=dict(
        timeframe='M15', candle_closed_epoch=int(candle['time'])+900,
        mandatory=mandatory,final=final,single_target=runner is None,
        rule='Opposite MA zone first' if reversal else 'Mid BB first'))


def targets_ordered(side, entry, stop, first, final, structural):
    sign = 1 if side == 'BUY' else -1
    separated = sign*(final-first)>0 or bool(structural and structural.get('single_target') and final==first)
    return sign*(entry-stop)>0 and sign*(first-entry)>0 and separated
