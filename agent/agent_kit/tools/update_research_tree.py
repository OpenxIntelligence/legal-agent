"""Maintain a compact public research decision tree alongside source-linked findings."""
from .. import research_tree

PROPERTIES = {
    'id': {'type': 'string'}, 'parent': {'type': 'string'},
    'question': {'type': 'string'}, 'hypothesis': {'type': 'string'},
    'status': {'type': 'string', 'enum': list(research_tree.STATUSES)},
    'summary': {'type': 'string'},
    'evidence_ids': {'type': 'array', 'items': {'type': 'string'}},
    'next_action': {'type': 'string'},
}
DEFINITION = {
    'type': 'function', 'name': 'update_research_tree',
    'description': 'Upsert concise research questions and branches, evidence-linked assessments, blockers, and next actions. '
                   'Record public decisions, not private chain-of-thought. Preserve the user question; test alternative hypotheses. '
                   'Non-open status needs summary; supported/contradicted/mixed needs active fact IDs; blocked needs next_action. '
                   'Omitted fields retain existing values. References do not certify correctness.',
    'parameters': {'type': 'object', 'additionalProperties': False,
                   'properties': {'nodes': {'type': 'array', 'minItems': 1, 'maxItems': 12,
                                             'items': {'type': 'object', 'additionalProperties': False,
                                                       'properties': PROPERTIES, 'required': ['id']}}},
                   'required': ['nodes']},
}


def run(ctx, args):
    return research_tree.update(ctx.case, args.get('nodes'))
