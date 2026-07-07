"""Constitutional Layer — user-signed, inviolable read-time constraints.

The highest tier of Jeli's three-branch governance model. Rules are managed
only by the user (via the CLI); agents never see or manage them. Their search
results are filtered through the Read Gate, and their writes are checked by the
Write Gate — both before agents can observe or persist anything.
"""

from .gate import ReadGate, WriteGate
from .manager import ConstitutionalManager
from .rules import ConstitutionalRule, RuleType, build_canonical_rule, sign_rule

__all__ = [
    "ConstitutionalManager",
    "ConstitutionalRule",
    "ReadGate",
    "RuleType",
    "WriteGate",
    "build_canonical_rule",
    "sign_rule",
]
