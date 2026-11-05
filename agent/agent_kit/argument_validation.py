"""Validate supplied tool properties without changing wrapper-owned required fields."""
from __future__ import annotations

import json
import math


class ArgumentError(ValueError):
    def __init__(self, field: str, expected: str):
        self.field = field
        super().__init__(f'Invalid tool argument at {field}: expected {expected}.')

    def result(self):
        return {'error': str(self), 'code': 'invalid_tool_arguments', 'field': self.field}


def parse(raw):
    if raw is None:  # SDK calls historically omit empty arguments.
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw, parse_constant=lambda constant: _invalid_constant())
        except (ValueError, TypeError, RecursionError):
            raise ArgumentError('arguments', 'a JSON object (input is not valid JSON)') from None
    if not isinstance(raw, dict):
        raise ArgumentError('arguments', 'a JSON object')
    return raw


def _invalid_constant():
    raise ValueError('non-finite JSON number')


def validate(args, schema, *, tool=''):
    """Check only declared, supplied properties; preserve legacy page strings."""
    _validate(args, schema, 'arguments', tool)


def _validate(value, schema, path, tool):
    kind = schema.get('type')
    kinds = kind if isinstance(kind, list) else [kind] if kind else []
    compatible_page = (tool == 'record_fact' and path == 'arguments.page') or (
        tool == 'check_signatures' and path in ('arguments.page', 'arguments.reference_page'))
    digit_page = compatible_page and isinstance(value, str) and value.strip().isdigit()
    checks = {
        'object': lambda: isinstance(value, dict),
        'array': lambda: isinstance(value, list),
        'string': lambda: isinstance(value, str),
        'integer': lambda: isinstance(value, int) and not isinstance(value, bool) or digit_page,
        'number': lambda: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        'boolean': lambda: isinstance(value, bool),
        'null': lambda: value is None,
    }
    if kinds and not any(checks[k]() for k in kinds if k in checks):
        raise ArgumentError(path, ' or '.join(kinds))
    if 'enum' in schema and not any(type(value) is type(option) and value == option for option in schema['enum']):
        raise ArgumentError(path, 'a declared enum value')
    if isinstance(value, dict):
        for field, subschema in schema.get('properties', {}).items():
            if field in value:
                _validate(value[field], subschema, f'{path}.{field}', tool)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', float('inf')):
            raise ArgumentError(path, 'an array within the declared size bounds')
        if isinstance(schema.get('items'), dict):
            for index, item in enumerate(value):
                _validate(item, schema['items'], f'{path}[{index}]', tool)
