"""Merge release additions with server observations without losing newer snapshots."""
import copy
import json
from datetime import datetime, timezone

def stamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=timezone.utc) if len(value)==10 else datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)

def key(row):
    return row.get('site'), row.get('url')

def union(a, b):
    found = {}
    for row in a + b:
        found[json.dumps(row, sort_keys=True, ensure_ascii=False)] = copy.deepcopy(row)
    return list(found.values())

def merge_release(seed, runtime):
    out = copy.deepcopy(runtime)
    for name in ('selected', 'pairs', 'coverage', 'candidates', 'excluded'):
        if isinstance(seed.get(name), list):
            out[name] = union(seed[name], runtime.get(name, []))
    out.setdefault('history', {})
    for group in ('own', 'analogs'):
        merged = {key(row): copy.deepcopy(row) for row in seed[group]}
        histories = union(seed.get('history', {}).get(group, []), runtime.get('history', {}).get(group, []))
        for row in runtime[group]:
            ident = key(row)
            release = merged.get(ident)
            if release is None:
                merged[ident] = copy.deepcopy(row)
                continue
            # Prefer latest successful observation; runtime wins ties.
            winner, loser = (row, release) if stamp(row.get('checkedAt')) >= stamp(release.get('checkedAt')) else (release, row)
            combined = {**copy.deepcopy(loser), **copy.deepcopy(winner)}
            if stamp(winner.get('checkedAt')) > stamp(loser.get('checkedAt')):
                for error in ('collectionError', 'refreshError'):
                    combined[error] = winner.get(error)
            if group == 'analogs':
                combined['skus'] = list(dict.fromkeys(release.get('skus', []) + row.get('skus', [])))
            if stamp(loser.get('checkedAt')) < stamp(winner.get('checkedAt')):
                histories = union(histories, [loser])
            # A later failed attempt does not change the successful price/date.
            if stamp(loser.get('lastAttemptAt')) > stamp(winner.get('lastAttemptAt')):
                for field in ('lastAttemptAt', 'lastAttemptStatus', 'refreshError', 'collectionError'):
                    if field in loser: combined[field] = copy.deepcopy(loser[field])
            merged[ident] = combined
        out[group] = list(merged.values())
        out['history'][group] = histories
    refs = {x['sku']: copy.deepcopy(x) for x in seed.get('manufacturerPrices', [])}
    for row in runtime.get('manufacturerPrices', []):
        if row['sku'] not in refs or stamp(row.get('checkedAt')) >= stamp(refs[row['sku']].get('checkedAt')):
            refs[row['sku']] = copy.deepcopy(row)
    out['manufacturerPrices'] = list(refs.values())
    if stamp(seed.get('monitoringRun', {}).get('date')) > stamp(runtime.get('monitoringRun', {}).get('date')):
        out['monitoringRun'] = copy.deepcopy(seed['monitoringRun'])
    elif 'monitoringRun' not in out and 'monitoringRun' in seed:
        out['monitoringRun'] = copy.deepcopy(seed['monitoringRun'])
    return out
