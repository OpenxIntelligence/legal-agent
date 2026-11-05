"""A revisable public task interpretation grounded in server-owned request context."""
from __future__ import annotations

import copy
import hashlib
import json

from .case_record import CaseError
from .progress import load_progress, now
from .runtime_ownership import run_file

TEXT_FIELDS = ('actor', 'event', 'interpretation', 'intended_outcome', 'revision_reason')
FIELDS = set(TEXT_FIELDS) | {'success_criteria', 'assumptions', 'alternatives'}
LABEL = 'Model assessment · not user confirmation'


def _text(value, field, limit=1200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise CaseError(f'{field} must be non-empty text of at most {limit} characters')
    return value.strip()


def _strings(value, field, minimum, maximum):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise CaseError(f'{field} must contain {minimum}–{maximum} text entries')
    return [_text(item, field, 600) for item in value]


def _context(case):
    run_id = case.data['run_id']
    original = run_file(case.root, run_id, 'objective.md').read_text(encoding='utf-8')
    checkpoint = load_progress(case.root, run_id)
    receipts = sorted(checkpoint.get('steering_directive_ids', []))
    version = hashlib.sha256(json.dumps({'objective': original, 'steering_ids': receipts},
                                        sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return original, version


def update(case, args: dict) -> dict:
    if not isinstance(args, dict) or set(args) - FIELDS:
        raise CaseError('task understanding contains unknown or server-owned fields')
    clean = {field: _text(args.get(field), field) for field in TEXT_FIELDS}
    clean['success_criteria'] = _strings(args.get('success_criteria'), 'success_criteria', 1, 6)
    clean['assumptions'] = _strings(args.get('assumptions', []), 'assumptions', 0, 8)
    alternatives = args.get('alternatives', [])
    if not isinstance(alternatives, list) or len(alternatives) > 6:
        raise CaseError('alternatives must contain 0–6 entries')
    clean['alternatives'] = []
    for alternative in alternatives:
        if (not isinstance(alternative, dict) or
                set(alternative) != {'interpretation', 'material', 'next_action'} or
                not isinstance(alternative['material'], bool)):
            raise CaseError('alternative needs interpretation, material boolean, and next_action')
        clean['alternatives'].append({
            'interpretation': _text(alternative['interpretation'], 'alternative interpretation'),
            'material': alternative['material'],
            'next_action': _text(alternative['next_action'], 'alternative next_action', 600),
        })
    original, context_version = _context(case)
    previous = case.data.get('task_understanding')
    previous_version = previous.get('version', 0) if isinstance(previous, dict) else 0
    if isinstance(previous_version, bool) or not isinstance(previous_version, int) or previous_version < 0:
        raise CaseError('saved task understanding version is malformed')
    history = case.data.get('task_understanding_history', [])
    if not isinstance(history, list):
        raise CaseError('saved task understanding history is malformed')
    record = dict(clean, version=previous_version + 1, original_request=original,
                  context_version=context_version, stale=False, assessed_by='model', updated_at=now())
    case.data['task_understanding'] = record
    case.data['task_understanding_history'] = (copy.deepcopy(history) + [copy.deepcopy(record)])[-20:]
    case._save('task_understanding_updated', 'agent', {'version': record['version'],
                                                    'context_version': context_version})
    return readout(case)


def refresh(case) -> dict:
    """Mark a historical assessment stale once, without changing its interpretation."""
    record = case.data.get('task_understanding')
    if not isinstance(record, dict):
        return readout(case)
    _, context_version = _context(case)
    if record.get('context_version') != context_version and record.get('stale') is not True:
        record['stale'] = True
        case._save('task_understanding_stale', 'system', {'version': record.get('version'),
                                                        'context_version': context_version})
    return readout(case)


def readout(case) -> dict:
    """Return bounded display data; never treat model interpretation as confirmation."""
    record = case.data.get('task_understanding')
    if not isinstance(record, dict):
        return {'assessment': None, 'label': LABEL, 'stale': False}
    _, current_version = _context(case)
    stale = record.get('stale') is True or record.get('context_version') != current_version
    def safe_text(value, limit=1200):
        return value[:limit] if isinstance(value, str) else ''
    def safe_strings(value, count):
        return [item[:600] for item in value[:count] if isinstance(item, str)] if isinstance(value, list) else []
    clean = {field: safe_text(record.get(field)) for field in TEXT_FIELDS}
    version = record.get('version')
    clean.update(version=version if isinstance(version, int) and not isinstance(version, bool) and version > 0 else 0,
                 original_request=safe_text(record.get('original_request'), 12000),
                 context_version=safe_text(record.get('context_version'), 64),
                 stale=stale, assessed_by='model', updated_at=safe_text(record.get('updated_at'), 80),
                 success_criteria=safe_strings(record.get('success_criteria'), 6),
                 assumptions=safe_strings(record.get('assumptions'), 8), alternatives=[])
    alternatives = record.get('alternatives', [])
    if isinstance(alternatives, list):
        clean['alternatives'] = [
            {'interpretation': safe_text(item.get('interpretation')),
             'material': item.get('material') is True,
             'next_action': safe_text(item.get('next_action'), 600)}
            for item in alternatives[:6] if isinstance(item, dict)]
    return {'assessment': clean, 'label': LABEL, 'stale': stale}
