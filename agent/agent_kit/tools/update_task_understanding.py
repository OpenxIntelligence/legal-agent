"""Record the model's current interpretation of the user's task, without overwriting it."""
from .. import task_understanding

DEFINITION = {
    'type': 'function', 'name': 'update_task_understanding',
    'description': 'Record or revise who is acting, what happened, the task interpretation, intended outcome, '
                   'success criteria, assumptions, and materially different interpretations to test. '
                   'This is a model assessment, never user confirmation. The server preserves the original request '
                   'and marks interpretations stale when new steering arrives. Explain each revision.',
    'parameters': {'type': 'object', 'additionalProperties': False,
                   'properties': {
                       **{field: {'type': 'string', 'minLength': 1, 'maxLength': 1200}
                          for field in task_understanding.TEXT_FIELDS},
                       'success_criteria': {'type': 'array', 'minItems': 1, 'maxItems': 6,
                                            'items': {'type': 'string', 'minLength': 1, 'maxLength': 600}},
                       'assumptions': {'type': 'array', 'maxItems': 8,
                                       'items': {'type': 'string', 'minLength': 1, 'maxLength': 600}},
                       'alternatives': {'type': 'array', 'maxItems': 6, 'items': {
                           'type': 'object', 'additionalProperties': False,
                           'properties': {'interpretation': {'type': 'string', 'minLength': 1, 'maxLength': 1200},
                                          'material': {'type': 'boolean'},
                                          'next_action': {'type': 'string', 'minLength': 1, 'maxLength': 600}},
                           'required': ['interpretation', 'material', 'next_action']}},
                   },
                   'required': [*task_understanding.TEXT_FIELDS, 'success_criteria']},
}


def run(ctx, args):
    return task_understanding.update(ctx.case, args)
