"""Judicial branch: settled case law (precedent) + human escalation queue.

The Judicial branch arbitrates contradictions surfaced by the conflict
resolver. This module gives it memory: past rulings become precedent that is
applied (and reinforced) instead of re-derived, and conflicts it cannot settle
are escalated to the user via the human queue.
"""

from .escalation import HumanEscalationQueue
from .precedent import JudicialPrecedent, PrecedentStore

__all__ = ["HumanEscalationQueue", "JudicialPrecedent", "PrecedentStore"]
