"""Event-local divisions stored in the existing result-processing configuration."""
from collections import defaultdict


def division_config(event):
    cfg = (event.result_processing_config or {}).get('event_divisions') or {}
    return cfg if cfg.get('enabled') else {}


def participant_division(event, participant_id):
    cfg = division_config(event)
    if not cfg:
        return ''
    name = (cfg.get('assignments') or {}).get(str(participant_id), '')
    if name not in cfg.get('names', []):
        raise ValueError('Every event participant must belong to a configured division.')
    return name


def group_participants(event, participant_ids):
    groups = defaultdict(list)
    for participant_id in participant_ids:
        groups[participant_division(event, participant_id)].append(participant_id)
    return dict(groups)


def rank_divisions(event, rows):
    """Retain existing score/tie comparison while restarting rank in each division."""
    cfg = division_config(event)
    if not cfg:
        return rows
    groups = defaultdict(list)
    for row in rows:
        row['division'] = participant_division(event, row['participant_id'])
        groups[row['division']].append(row)
    result = []
    for name in cfg['names']:
        prior, rank = None, 0
        for index, row in enumerate(groups[name], 1):
            comparison = row['rank']
            if prior != comparison:
                rank = index
            prior = comparison
            row['rank'] = rank
            result.append(row)
    return result


def validate_division_setup(raw, entries):
    if not isinstance(raw, dict):
        raise ValueError('Division settings must be an object.')
    if 'enabled' in raw and not isinstance(raw['enabled'], bool):
        raise ValueError('Enable Divisions must be true or false.')
    if not raw.get('enabled'):
        return {'enabled': False, 'names': []}
    names = raw.get('names')
    if not isinstance(names, list) or not names or len(names) > 50:
        raise ValueError('Add between 1 and 50 division names.')
    if any(not isinstance(name, str) or not name.strip() or len(name.strip()) > 100 for name in names):
        raise ValueError('Division names must contain between 1 and 100 characters.')
    names = [name.strip() for name in names]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('Division names must be unique.')
    for row in entries:
        if row.get('division') not in names:
            raise ValueError('Assign each selected participant to a configured division.')
    return {'enabled': True, 'names': names}
