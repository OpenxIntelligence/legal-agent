"""Runtime review and bounded repair of claims and reports.

The controller (ordinary Python) runs registered check modules against a
memo draft, turns defects into bounded repair tasks, and rechecks after
revision. It never alters the acceptance policy or its verifiers: memo
rules and the claims check still gate every filing.
"""

from . import controller, policy, records, registry  # noqa: F401
