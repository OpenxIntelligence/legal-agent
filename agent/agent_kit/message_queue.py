"""Durable user directions; only the agent writes its case at safe boundaries."""
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import uuid


@contextmanager
def locked(root):
    path = Path(root) / 'workspace/steering-inbox.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = json.loads(path.read_text()) if path.exists() else []
        for row in rows:
            if isinstance(row, dict):
                row.setdefault('queue_id', uuid.uuid4().hex)
                row.setdefault('state', 'queued')
                row.setdefault('mode', 'queue')
                row.setdefault('revision', 1)
        try:
            yield rows
        finally:
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(rows))
            temporary.replace(path)


def listing(root, run_id=None):
    with locked(root) as rows:
        return [dict(row, state=row.get('state', 'queued'), mode=row.get('mode', 'queue'),
                     revision=row.get('revision', 1)) for row in rows
                if run_id is None or row.get('run_id') == run_id]


def enqueue(root, run_id, text, request_id='', mode='queue'):
    if mode not in ('queue', 'steer'):
        raise ValueError('Choose queue or steer delivery.')
    text = validate_text(text)
    row = dict(queue_id=uuid.uuid4().hex, run_id=run_id, text=text,
               request_id=request_id, mode=mode, state='queued', revision=1)
    with locked(root) as rows:
        rows.append(row)
    return dict(row)


def validate_text(text):
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 12000:
        raise ValueError('Follow-up must contain 1 to 12,000 characters.')
    return text.strip()


def change(root, queue_id, op, revision, text=None):
    with locked(root) as rows:
        row = next((row for row in rows if row.get('queue_id') == queue_id), None)
        if row is None:
            raise ValueError('Unknown queued message.')
        if row.get('state', 'queued') != 'queued':
            raise ValueError('Message already delivered or cancelled; send a new direction.')
        if isinstance(revision, bool) or revision != row.get('revision', 1):
            raise ValueError('Message changed; refresh before editing or cancelling.')
        if op == 'edit':
            row['text'] = validate_text(text)
        elif op == 'cancel':
            row['state'] = 'cancelled'
        else:
            raise ValueError('Choose edit or cancel.')
        row['revision'] = row.get('revision', 1) + 1
        return dict(row)


def deliver(root, run_id, messages, *, include_queue=False, request_id=None):
    """Checkpoint payload and receipt atomically relative to edits/cancellation.

    The conversation checkpoint is written first. Its queue IDs reconcile a
    crash between that write and the receipt, so an accepted message is never
    injected twice. Queued directions are eligible only at a slice start.
    """
    from .progress import load_progress, checkpoint_steering
    from .case_record import CaseRecord
    additions = []
    with locked(root) as rows:
        saved = load_progress(root, run_id)
        delivered = set(saved.get('steering_directive_ids') or [])
        for row in rows:
            if row.get('run_id') != run_id or row.get('state', 'queued') != 'queued':
                continue
            if request_id is not None and row.get('request_id') != request_id:
                continue
            key = 'QUEUE:' + row['queue_id']
            if key in delivered:
                row['state'] = 'delivered'
                continue
            if not include_queue and row.get('mode', 'queue') != 'steer':
                continue
            # Request answers and direction provenance are handled by the same
            # agent process that owns the case. Never mutate its record in UI.
            case = CaseRecord(Path(root), run_id)
            request = row.get('request_id')
            if request and any(q.get('id') == request and q.get('status') == 'open'
                               for q in case.data.get('requests', [])):
                case.answer_request(request, row['text'])
            content = (f"User direction {row['queue_id']} ({row.get('mode', 'queue')}; "
                       "delivery is not confirmation of application):\n" + row['text'])
            addition = {'role': 'user', 'content': content}
            checkpoint_steering(root, run_id, messages + additions + [addition], [key])
            additions.append(addition)
            delivered.add(key)
            row['state'] = 'delivered'
            row['revision'] = row.get('revision', 1) + 1
    return additions
