"""Supplied tool arguments have strict JSON types and value-free errors."""
import json
import unittest
from unittest.mock import patch

from agent_kit import tools
from agent_kit.argument_validation import ArgumentError, parse, validate
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from tests import support


class ArgumentValidationTest(unittest.TestCase):
    def setUp(self):
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions='i', objective='o', verification=False)
        self.case = CaseRecord(self.root, self.run_id)

    def test_nonobject_arguments_refused_without_dispatch_or_mutation(self):
        before = json.dumps(self.case.data, sort_keys=True)
        with patch.object(tools.MODULES['set_checklist'], 'run') as dispatch:
            for raw in (False, [], [['items', []]], 'false', 'null', '[]', '"secret-value"', '{secret-value', ''):
                with self.subTest(raw=raw):
                    result = tools.run(None, self.root, self.run_id,
                                       {'name': 'set_checklist', 'arguments': raw})
                    self.assertEqual('invalid_tool_arguments', result['code'])
                    self.assertNotIn('secret-value', result['error'])
            dispatch.assert_not_called()
        self.assertEqual(before, json.dumps(CaseRecord(self.root, self.run_id).data, sort_keys=True))
        self.assertEqual({}, parse(None))

    def test_supplied_nested_fields_enums_and_array_bounds(self):
        schema = {'type': 'object', 'properties': {
            'items': {'type': 'array', 'minItems': 1, 'maxItems': 2,
                      'items': {'type': 'object', 'properties': {
                          'count': {'type': 'integer'}, 'flag': {'type': 'boolean'},
                          'status': {'type': 'string', 'enum': ['open', 'blocked']}}}}}}
        for args in ({'items': None}, {'items': []}, {'items': [{}, {}, {}]},
                     {'items': [False]}, {'items': [{'count': True}]},
                     {'items': [{'count': 1.5}]}, {'items': [{'flag': 'false'}]},
                     {'items': [{'status': []}]}, {'items': [{'status': None}]},
                     {'items': [{'status': 'secret-value'}]}):
            with self.subTest(args=args):
                with self.assertRaises(ArgumentError) as captured:
                    validate(args, schema)
                self.assertNotIn('secret-value', str(captured.exception))
        validate({'unknown_legacy': [], 'items': [{'count': 2, 'flag': False}]}, schema)
        validate({}, schema)  # Required fields remain wrapper-owned.

    def test_dispatch_rejects_nested_bad_type_before_wrapper(self):
        with patch.object(tools.MODULES['set_checklist'], 'run') as dispatch:
            result = tools.run(None, self.root, self.run_id, {'name': 'set_checklist',
                'arguments': {'items': [{'id': [], 'question': 'question'}]}})
            self.assertEqual('arguments.items[0].id', result['field'])
            dispatch.assert_not_called()

    def test_page_digit_string_compatibility_is_narrow(self):
        for name, field in (('record_fact', 'page'), ('check_signatures', 'page'),
                            ('check_signatures', 'reference_page')):
            validate({field: ' 12 '}, tools.MODULES[name].DEFINITION['parameters'], tool=name)
            with self.assertRaises(ArgumentError):
                validate({field: True}, tools.MODULES[name].DEFINITION['parameters'], tool=name)
        with self.assertRaises(ArgumentError):
            validate({'page': '12'}, {'properties': {'page': {'type': 'integer'}}}, tool='other')
        with self.assertRaises(ArgumentError):
            parse('{"number": NaN}')
