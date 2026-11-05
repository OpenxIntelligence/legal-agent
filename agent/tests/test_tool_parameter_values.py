"""Semantic argument boundaries: a malformed value must not change its meaning."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools import (register_authority, assess_authority, answer_foundational,
                             adversarial_review, resolve_query, search_corpus, update_plan)
from tests import support


class BooleanMeaningTest(unittest.TestCase):
    CASES = (
        (register_authority, 'treatment_checked', 'register_authority'),
        (assess_authority, 'treatment_read', 'assess_authority'),
        (answer_foundational, 'threshold', 'answer_foundational'),
        (adversarial_review, 'revision_needed', 'record_adversarial'),
    )

    def context(self, method):
        case = Mock()
        case.data = {}
        case.adversarial_lenses.return_value = []
        getattr(case, method).return_value = {
            'id': 'test', 'authority': 'A1', 'level': 'binding',
            'verification': 'provisional', 'class': 'applicable_law',
            'status': 'answered', 'revision_needed': False,
        }
        return SimpleNamespace(case=case)

    def test_invalid_boolean_values_do_not_reach_case_mutations(self):
        for module, field, method in self.CASES:
            for malformed in ('false', 'true', 0, 1, [], {}, None):
                with self.subTest(tool=module.DEFINITION['name'], field=field, value=malformed):
                    ctx = self.context(method)
                    with self.assertRaisesRegex(CaseError, field + ' must be a boolean'):
                        module.run(ctx, {field: malformed})
                    getattr(ctx.case, method).assert_not_called()

    def test_real_false_and_true_keep_exact_boolean_meaning(self):
        for module, field, method in self.CASES:
            for value in (False, True):
                with self.subTest(tool=module.DEFINITION['name'], value=value):
                    ctx = self.context(method)
                    module.run(ctx, {field: value})
                    self.assertIs(getattr(ctx.case, method).call_args.kwargs[field], value)


class PlanValueTest(unittest.TestCase):
    def test_malformed_plan_never_changes_persisted_plan(self):
        for malformed in ('abc', {'step': 'research'}, None, False, 3, ['read', 5], [{}]):
            with self.subTest(value=malformed):
                ctx = SimpleNamespace(case=Mock())
                with self.assertRaisesRegex(CaseError, 'list of strings'):
                    update_plan.run(ctx, {'plan': malformed})
                ctx.case.record_plan.assert_not_called()

    def test_missing_plan_is_not_a_request_to_clear(self):
        ctx = SimpleNamespace(case=Mock())
        with self.assertRaisesRegex(CaseError, 'list of strings'):
            update_plan.run(ctx, {})
        ctx.case.record_plan.assert_not_called()

    def test_explicit_empty_and_valid_text_plans_preserved(self):
        for steps in ([], ['Read the statute', 'Test exceptions']):
            ctx = SimpleNamespace(case=Mock())
            self.assertEqual(update_plan.run(ctx, {'plan': steps}), {'plan': steps})
            ctx.case.record_plan.assert_called_once_with(steps)


class QueryAndSearchScopeTest(unittest.TestCase):
    def setUp(self):
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        run_id = new_run(self.root, instructions='i', objective='o', verification=True)
        self.case = CaseRecord(self.root, run_id)
        self.case.data['queries'] = [{'id': 'Q1', 'status': 'open'}]
        self.ctx = SimpleNamespace(case=self.case, root=self.root)

    def test_query_nontext_values_do_not_close_or_change_query(self):
        base = {'query': 'Q1', 'outcome': 'answered', 'summary': 'Verified rule.'}
        for field in ('query', 'outcome', 'summary', 'coverage'):
            for malformed in (None, [], {}, False, 3):
                with self.subTest(field=field, value=malformed):
                    with self.assertRaisesRegex(CaseError, field + ' must be text'):
                        resolve_query.run(self.ctx, {**base, field: malformed})
                    self.assertEqual(self.case.data['queries'][0]['status'], 'open')

    def test_query_invalid_enum_is_refused_instead_of_resolved(self):
        with self.assertRaisesRegex(CaseError, 'must be one of'):
            resolve_query.run(self.ctx, {'query': 'Q1', 'outcome': 'complete', 'summary': 'Rule found.'})
        self.assertEqual(self.case.data['queries'][0]['status'], 'open')

    def test_valid_query_resolution_still_closes(self):
        result = resolve_query.run(self.ctx, {'query': 'Q1', 'outcome': 'answered', 'summary': 'Verified rule.'})
        self.assertEqual(result['resolved']['status'], 'closed')
        self.assertEqual(result['resolved']['summary'], 'Verified rule.')

    def test_explicit_empty_document_scope_never_expands_to_all(self):
        empty = search_corpus.run(self.ctx, {'query': 'report', 'document_ids': []})
        unspecified = search_corpus.run(self.ctx, {'query': 'report'})
        self.assertEqual(empty['documents'], [])
        self.assertEqual(empty['unindexed'], [])
        self.assertTrue(unspecified['unindexed'])

    def test_nonlist_scope_and_nonstring_document_ids_are_refused(self):
        for malformed in ('DOC-REPORT', {}, [False], [3]):
            with self.subTest(value=malformed), self.assertRaisesRegex(CaseError, 'list of strings'):
                search_corpus.run(self.ctx, {'query': 'report', 'document_ids': malformed})


if __name__ == '__main__':
    unittest.main()
