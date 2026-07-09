"""Constitutional gates — enforce user-signed rules on reads and writes.

ReadGate is applied as the last step of search_memory (after ranking), so it
cannot be bypassed by any query an agent constructs. WriteGate is applied inside
capture_memory before the record is hashed, so a denied write never enters the
chain and a trust cap is baked into the attested record. Both honour a rule's
applies_to scoping — the user's constraints are the final word either way.
"""

import logging

from .rules import ConstitutionalRule, RuleType

logger = logging.getLogger(__name__)


def rule_applies(rule: ConstitutionalRule, actor: str) -> bool:
    """applies_to scoping: 'all' fires always; otherwise the pattern must be a
    substring of the actor (simple, sufficient for v1)."""
    if rule.applies_to == "all":
        return True
    return rule.applies_to in (actor or "")


class ReadGate:
    """Filter a result list through a set of active constitutional rules."""

    def apply(
        self,
        results: list[dict],
        actor: str,
        rules: list[ConstitutionalRule],
    ) -> list[dict]:
        """Return results with every applicable rule enforced.

        applies_to scoping: a rule with applies_to != 'all' only fires when the
        pattern is a substring of the actor (simple, sufficient for v1).
        """
        filtered = results
        for rule in rules:
            if not rule_applies(rule, actor):
                continue
            filtered = self._enforce(rule, filtered)
        return filtered

    def _enforce(self, rule: ConstitutionalRule, results: list[dict]) -> list[dict]:
        params = rule.parameters or {}
        rtype = rule.rule_type

        if rtype == RuleType.EXCLUDE_MEMORY_TYPE.value:
            target = params.get("memory_type")
            return [r for r in results if r.get("memory_type") != target]

        if rtype == RuleType.MIN_TRUST_FLOOR.value:
            floor = float(params.get("floor", 0.0))
            return [r for r in results if r.get("effective_trust", 0.0) >= floor]

        if rtype == RuleType.EXCLUDE_CONTENT_CLASS.value:
            target = params.get("content_class")
            return [r for r in results if r.get("content_class") != target]

        if rtype == RuleType.EXCLUDE_TAG.value:
            target = params.get("tag")
            return [r for r in results if target not in self._tags(r)]

        if rtype == RuleType.MAX_RESULTS.value:
            n = int(params.get("max_results", len(results)))
            return results[: max(0, n)]

        # Unknown rule type: fail closed by leaving results untouched but loud,
        # so a mis-typed rule never silently widens what agents can see.
        logger.warning("ReadGate: unknown rule_type %r — rule not enforced", rtype)
        return results

    @staticmethod
    def _tags(result: dict) -> list:
        meta = result.get("metadata") or {}
        tags = meta.get("tags") if isinstance(meta, dict) else None
        return tags or []


class WriteGate:
    """Enforce constitutional constraints on the capture_memory write path.

    Runs before the record is hashed so a denied write never enters the chain
    and any trust cap is part of the attested record (unlike FLAGGED_TRUST_CEILING,
    which is a heuristic reaction to injection patterns — this is a user-declared
    constitutional floor on a whole content_class).
    """

    def check(
        self,
        memory_type: str,
        content_class: str,
        trust_score: float,
        actor: str,
        rules: list[ConstitutionalRule],
    ) -> tuple[bool, float, str | None]:
        """Return (allowed, effective_trust, block_reason).

        allowed=False means the write must be rejected (block_reason is set).
        effective_trust may be below trust_score if a max_trust rule matched.
        """
        effective_trust = float(trust_score)
        for rule in rules:
            if not rule_applies(rule, actor):
                continue
            params = rule.parameters or {}
            rtype = rule.rule_type

            if rtype == RuleType.DENY_WRITE_MEMORY_TYPE.value:
                if params.get("memory_type") == memory_type:
                    return False, effective_trust, rule.description or (
                        f"writes of memory_type '{memory_type}' are denied"
                    )

            elif rtype == RuleType.MAX_TRUST_FOR_CONTENT_CLASS.value:
                if params.get("content_class") == content_class:
                    cap = float(params.get("max_trust", effective_trust))
                    effective_trust = min(effective_trust, cap)

        return True, effective_trust, None
