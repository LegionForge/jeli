"""ConstitutionalRule model, rule types, canonical form, and HMAC signing.

A constitutional rule is a user-signed constraint on what agents may retrieve.
Its integrity rests on the same HMAC chain-key mechanism as memory records:
rule_hash = HMAC-SHA256(chain_key, canonical(type + params + description +
applies_to + created_at)). Any edit to a stored rule changes the canonical
form and therefore fails verification — tampering is detectable without the key.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..core.hash_chain import canonical_json, compute_record_hash


class RuleType(StrEnum):
    """The constraint kinds the Read and Write gates know how to enforce."""

    # Read-gate constraints (enforced on search results).
    EXCLUDE_MEMORY_TYPE = "exclude_memory_type"
    MIN_TRUST_FLOOR = "min_trust_floor"
    EXCLUDE_TAG = "exclude_tag"
    EXCLUDE_CONTENT_CLASS = "exclude_content_class"
    MAX_RESULTS = "max_results"
    # Write-gate constraints (enforced in capture_memory, before the hash).
    DENY_WRITE_MEMORY_TYPE = "deny_write_memory_type"
    MAX_TRUST_FOR_CONTENT_CLASS = "max_trust_for_content_class"


def build_canonical_rule(
    rule_type: str,
    parameters: dict,
    description: str,
    applies_to: str,
    created_at: datetime,
) -> str:
    """Canonical JSON for a rule — the exact bytes that get HMAC-signed.

    created_at is inside the hash (ISO-8601, like state events) so a rule
    cannot be back- or post-dated without breaking its signature.
    """
    return canonical_json(
        {
            "rule_type": rule_type,
            "parameters": parameters,
            "description": description,
            "applies_to": applies_to,
            "created_at": created_at.isoformat(),
        }
    )


def sign_rule(
    chain_key: str,
    rule_type: str,
    parameters: dict,
    description: str,
    applies_to: str,
    created_at: datetime,
) -> str:
    """Compute the HMAC-SHA256 signature (rule_hash) for a rule."""
    canonical = build_canonical_rule(rule_type, parameters, description, applies_to, created_at)
    return compute_record_hash(chain_key, canonical)


@dataclass
class ConstitutionalRule:
    """A single user-signed constraint.

    parameters is rule-specific, e.g. {"memory_type": "transient"} for
    exclude_memory_type or {"floor": 0.6} for min_trust_floor.
    """

    rule_type: str
    parameters: dict
    description: str
    applies_to: str
    created_at: datetime
    rule_hash: str
    key_id: str = "k1"
    id: str | None = None
    active: bool = True
    revoked_at: datetime | None = None

    def canonical(self) -> str:
        return build_canonical_rule(
            self.rule_type,
            self.parameters,
            self.description,
            self.applies_to,
            self.created_at,
        )
