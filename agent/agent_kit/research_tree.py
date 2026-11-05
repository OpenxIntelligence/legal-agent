"""Persist concise, evidence-linked research decisions, never private reasoning traces."""
from __future__ import annotations

import copy
import re
from collections import Counter

from .case_record import CaseError

STATUSES = ('open', 'supported', 'contradicted', 'mixed', 'blocked')
FIELDS = {'id', 'parent', 'question', 'hypothesis', 'status', 'summary', 'evidence_ids', 'next_action'}
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')


def update(case, nodes: list) -> dict:
    """Validate the complete proposed tree before applying one audited mutation."""
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 12:
        raise CaseError('research tree needs 1–12 nodes per update')
    existing = case.data.get('research_tree', {})
    if not isinstance(existing, dict):
        raise CaseError('saved research tree is malformed')
    tree = copy.deepcopy(existing)
    seen = set()
    for patch in nodes:
        if not isinstance(patch, dict) or set(patch) - FIELDS:
            raise CaseError('research node contains unknown fields or is not an object')
        node_id = patch.get('id')
        if not isinstance(node_id, str) or not IDENTIFIER.fullmatch(node_id):
            raise CaseError('research node id must be a short identifier')
        if node_id in seen:
            raise CaseError('research node ids must be unique within an update')
        seen.add(node_id)
        node = dict(tree.get(node_id, {'id': node_id, 'parent': '', 'status': 'open',
                                      'hypothesis': '', 'summary': '', 'evidence_ids': [], 'next_action': ''}))
        node.update(copy.deepcopy(patch))
        tree[node_id] = node
    if len(tree) > 40:
        raise CaseError('research tree is limited to 40 nodes')
    active_facts = {fact.get('id') for fact in case.data.get('facts', [])
                    if isinstance(fact, dict) and not fact.get('withdrawn')}
    for node_id, node in tree.items():
        if not isinstance(node, dict) or node.get('id') != node_id:
            raise CaseError('saved research node identity is malformed')
        for field in ('question', 'hypothesis', 'summary', 'next_action'):
            value = node.get(field, '')
            limit = 1200 if field == 'summary' else 600
            if not isinstance(value, str) or len(value) > limit:
                raise CaseError(f'research node {field} must be concise text (max {limit})')
        if not node.get('question', '').strip():
            raise CaseError('research node needs a question')
        parent = node.get('parent', '')
        if not isinstance(parent, str) or (parent and parent not in tree):
            raise CaseError('research node parent must name an existing or batched node')
        status = node.get('status', 'open')
        if status not in STATUSES:
            raise CaseError('unknown research node status')
        if status != 'open' and not node.get('summary', '').strip():
            raise CaseError('non-open research nodes need a summary')
        evidence = node.get('evidence_ids', [])
        if (not isinstance(evidence, list) or any(not isinstance(e, str) for e in evidence)
                or len(evidence) != len(set(evidence)) or any(e not in active_facts for e in evidence)):
            raise CaseError('research evidence_ids must reference unique active case facts')
        if status in ('supported', 'contradicted', 'mixed') and not evidence:
            raise CaseError('assessed research nodes need active evidence references')
        if status == 'blocked' and not node.get('next_action', '').strip():
            raise CaseError('blocked research nodes need a next_action')
    for node_id in tree:
        path, cursor = set(), node_id
        while cursor:
            if cursor in path:
                raise CaseError('research tree cannot contain a cycle')
            path.add(cursor)
            if len(path) > 5:
                raise CaseError('research tree depth cannot exceed 5')
            cursor = tree[cursor].get('parent', '')
    case.data['research_tree'] = tree
    case._save('research_tree_updated', 'agent', {'nodes': [patch['id'] for patch in nodes]})
    return readout(case)


def effective_status(data: dict, node: dict) -> str:
    """Retain historical assessments while exposing withdrawn/missing support."""
    status = node.get('status', 'open')
    if status not in ('supported', 'contradicted', 'mixed'):
        return status
    facts = data.get('facts', [])
    facts = facts if isinstance(facts, list) else []
    active = {fact.get('id') for fact in facts if isinstance(fact, dict) and not fact.get('withdrawn')}
    evidence = node.get('evidence_ids', [])
    if (not isinstance(evidence, list) or not evidence or
            any(not isinstance(ref, str) or ref not in active for ref in evidence)):
        return 'stale'
    return status


def readout(case) -> dict:
    tree = case.data.get('research_tree', {})
    nodes = copy.deepcopy(list(tree.values()))
    for node in nodes:
        node['effective_status'] = effective_status(case.data, node)
        node['stale'] = node['effective_status'] == 'stale'
    counts = Counter(node['effective_status'] for node in nodes)
    return {'nodes': nodes,
            'coverage': {'total': len(tree), 'by_status': {s: counts[s] for s in (*STATUSES, 'stale')},
                         'assessed': sum(counts[s] for s in ('supported', 'contradicted', 'mixed'))},
            'unresolved': [{'id': n['id'], 'question': n['question'], 'status': n['status'],
                            'effective_status': n['effective_status'], 'stale': n['stale'],
                            'next_action': (n.get('next_action', '') or
                                            ('Recheck missing or withdrawn evidence references' if n['stale'] else ''))}
                           for n in nodes if n['effective_status'] in ('open', 'blocked', 'mixed', 'stale')],
            'note': 'Evidence references link recorded facts; they do not certify legal correctness or completeness.'}
