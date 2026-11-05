"""Deterministic work prioritization, independent of model-internal attention."""
from agent_kit.research_tree import effective_status


def priorities(data: dict, limit: int = 6) -> list[dict]:
    """Rank observable alignment risks and unresolved questions, not private thoughts.

    Stale interpretation and material ambiguity take precedence. Grouping links
    are not execution dependencies. Ties use stable IDs, not invented confidence.
    This is advice to the worker, never authorization or a truth assessment.
    """
    result = []
    understanding = data.get('task_understanding')
    if isinstance(understanding, dict):
        if understanding.get('stale'):
            result.append({'kind': 'alignment', 'target': 'task_understanding',
                           'reason': 'New user direction has not been reassessed.',
                           'action': 'Re-read the original request and delivered user directions; revise the interpretation before relying on it.'})
        alternatives = understanding.get('alternatives', [])
        if isinstance(alternatives, list):
            candidates = []
            for index, alternative in enumerate(alternatives[:6]):
                if isinstance(alternative, dict) and alternative.get('material') is True:
                    candidates.append({'kind': 'interpretation', 'target': f'alternative-{index + 1}',
                                   'reason': 'Recorded alternative could change the answer.',
                                   'action': str(alternative.get('next_action', ''))[:600],
                                   'interpretation': str(alternative.get('interpretation', ''))[:600]})
            # Leave room for evidence work. Resolved alternatives should be
            # removed or marked immaterial in the next interpretation revision.
            result.extend(candidates[:2])
    raw = data.get('research_tree', {})
    tree = {k: n for k, n in raw.items() if isinstance(k, str) and isinstance(n, dict)} if isinstance(raw, dict) else {}
    unresolved = {k: n for k, n in tree.items()
                  if effective_status(data, n) in ('open', 'mixed', 'blocked', 'stale')}
    order = {'stale': 0, 'mixed': 1, 'open': 2, 'blocked': 3}
    ranked = sorted(unresolved.items(), key=lambda pair: (
        order[effective_status(data, pair[1])], pair[0]))
    for node_id, node in ranked:
        status = effective_status(data, node)
        result.append({'kind': 'research', 'target': node_id, 'status': status,
                       'question': str(node.get('question', ''))[:600],
                       'reason': {'stale': 'Evidence changed; reassess this claim.',
                                  'mixed': 'Competing evidence needs reconciliation.',
                                  'open': 'This question remains unresolved.',
                                  'blocked': 'Choose another route or disclose the precise limitation.'}[status],
                       'action': str(node.get('next_action', ''))[:600]})
    return result[:max(0, min(limit, 12))]
