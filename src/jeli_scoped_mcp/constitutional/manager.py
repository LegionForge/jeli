"""ConstitutionalManager — user-tier CRUD (append-only) for constitutional rules.

Rules are added, listed, revoked, and verified here. There is no update and no
delete: a rule is retired by setting revoked_at (active=FALSE), keeping the full
record of what constraints were ever in force. Every add is HMAC-signed with the
chain key so tampering is detectable via verify_rule / `jeli constitutional
verify`.
"""

import hmac
import json
import logging
import time

from ..database.pool import AsyncPostgresPool
from .rules import ConstitutionalRule, RuleType, sign_rule

logger = logging.getLogger(__name__)

VALID_RULE_TYPES = {t.value for t in RuleType}


class ConstitutionalError(Exception):
    """Raised for invalid rule input; message is safe to surface to the user."""


def validate_rule_parameters(rule_type: str, parameters: dict) -> None:
    """Ensure a rule carries the parameter its gate needs to enforce (GH #54).

    The Read/Write gates read parameters with `.get(key, <default>)` and fail
    open on an absent key, so a rule with a missing or misspelled parameter
    signs and stores fine yet enforces nothing — a signed, active-looking rule
    that silently does nothing. Validate at creation so the typo fails loud
    here instead of becoming an invisible sovereignty hole. Enforcement and
    stored rules are untouched; this only guards the add path.
    """

    def _require(key: str) -> None:
        if key not in parameters:
            raise ConstitutionalError(
                f"rule_type '{rule_type}' requires parameter '{key}'"
            )

    def _require_unit_float(key: str) -> None:
        _require(key)
        v = parameters[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
            raise ConstitutionalError(
                f"parameter '{key}' must be a number between 0.0 and 1.0"
            )

    def _require_nonneg_number(key: str) -> None:
        _require(key)
        v = parameters[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise ConstitutionalError(f"parameter '{key}' must be a non-negative number")

    if rule_type == RuleType.EXCLUDE_MEMORY_TYPE.value:
        _require("memory_type")
    elif rule_type == RuleType.MIN_TRUST_FLOOR.value:
        _require_unit_float("floor")
    elif rule_type == RuleType.EXCLUDE_TAG.value:
        _require("tag")
    elif rule_type == RuleType.EXCLUDE_CONTENT_CLASS.value:
        _require("content_class")
    elif rule_type == RuleType.MAX_RESULTS.value:
        _require_nonneg_number("max_results")
    elif rule_type == RuleType.DENY_WRITE_MEMORY_TYPE.value:
        _require("memory_type")
    elif rule_type == RuleType.MAX_TRUST_FOR_CONTENT_CLASS.value:
        _require("content_class")
        _require_unit_float("max_trust")


class ConstitutionalManager:
    """CRUD-lite over the constitutional_rules table.

    load_active_rules is on the hottest path (every search and capture), so it
    is backed by a per-instance TTL cache. The cache is per-instance rather than
    class-level so each server/CLI process owns its own state; a process's own
    mutations invalidate it immediately, and cross-process changes converge
    within one TTL window.
    """

    def __init__(self, ttl: float = 30.0) -> None:
        self._cache: list[ConstitutionalRule] | None = None
        self._cache_expires: float = 0.0
        self._CACHE_TTL = ttl

    async def add_rule(
        self,
        db: AsyncPostgresPool,
        chain_key: str,
        key_id: str,
        rule_type: str,
        parameters: dict,
        description: str,
        applies_to: str = "all",
    ) -> dict:
        """Sign and append a new constitutional rule. Returns id + rule_hash."""
        if rule_type not in VALID_RULE_TYPES:
            raise ConstitutionalError(f"rule_type must be one of {sorted(VALID_RULE_TYPES)}")
        if not isinstance(parameters, dict):
            raise ConstitutionalError("parameters must be a JSON object")
        if not description or not description.strip():
            raise ConstitutionalError("description is required — a rule states user intent")
        # A rule missing its parameter would sign fine and enforce nothing (GH #54).
        validate_rule_parameters(rule_type, parameters)

        created_at = await db.fetchval("SELECT now()")
        rule_hash = sign_rule(
            chain_key, rule_type, parameters, description, applies_to, created_at
        )
        row = await db.fetchrow(
            """
            INSERT INTO constitutional_rules (
                rule_type, parameters, description, applies_to,
                created_at, rule_hash, key_id
            ) VALUES ($1, $2::jsonb, $3, $4, $5, $6, $7)
            RETURNING id, created_at
            """,
            rule_type,
            json.dumps(parameters),
            description,
            applies_to,
            created_at,
            rule_hash,
            key_id,
        )
        if row is None:
            raise ConstitutionalError("insert failed: no row returned")
        logger.info(
            "constitutional add_rule: id=%s type=%s applies_to=%s",
            row["id"],
            rule_type,
            applies_to,
        )
        self.invalidate_cache()
        return {
            "id": str(row["id"]),
            "rule_type": rule_type,
            "applies_to": applies_to,
            "rule_hash": rule_hash,
        }

    async def list_rules(self, db: AsyncPostgresPool) -> list[ConstitutionalRule]:
        """All active (not revoked) rules, oldest first."""
        return await self.load_active_rules(db)

    async def load_active_rules(self, db: AsyncPostgresPool) -> list[ConstitutionalRule]:
        """Active rules for the Read Gate — revoked_at IS NULL AND active.

        Served from a TTL cache; only misses hit the DB.
        """
        if self._cache is not None and time.monotonic() < self._cache_expires:
            return self._cache
        rules = await self._fetch_from_db(db)
        self._cache = rules
        self._cache_expires = time.monotonic() + self._CACHE_TTL
        return rules

    async def _fetch_from_db(
        self, db: AsyncPostgresPool
    ) -> list[ConstitutionalRule]:
        rows = await db.fetchall(
            """
            SELECT id, rule_type, parameters, description, applies_to,
                   active, created_at, revoked_at, rule_hash, key_id
            FROM constitutional_rules
            WHERE revoked_at IS NULL AND active = TRUE
            ORDER BY created_at ASC
            """
        )
        return [self._row_to_rule(r) for r in rows]

    async def load_all_rules(self, db: AsyncPostgresPool) -> list[ConstitutionalRule]:
        """Every rule ever signed, revoked included — for verification.

        A revoked rule is retired history, not deleted history: its HMAC must
        still verify, otherwise tampering with the retired record would be
        undetectable. Uncached — verification always reads the DB.
        """
        rows = await db.fetchall(
            """
            SELECT id, rule_type, parameters, description, applies_to,
                   active, created_at, revoked_at, rule_hash, key_id
            FROM constitutional_rules
            ORDER BY created_at ASC
            """
        )
        return [self._row_to_rule(r) for r in rows]

    def invalidate_cache(self) -> None:
        """Force the next load_active_rules to hit the DB.

        Called after add_rule/revoke_rule so a process sees its own mutations
        immediately rather than up to one TTL later.
        """
        self._cache = None
        self._cache_expires = 0.0

    async def revoke_rule(self, db: AsyncPostgresPool, rule_id: str) -> dict:
        """Retire a rule (never delete): set revoked_at + active=FALSE."""
        result = await db.execute(
            """
            UPDATE constitutional_rules
            SET revoked_at = now(), active = FALSE
            WHERE id = $1 AND revoked_at IS NULL
            """,
            rule_id,
        )
        if result == "UPDATE 0":
            raise ConstitutionalError(f"rule {rule_id} not found or already revoked")
        self.invalidate_cache()
        logger.info("constitutional revoke_rule: id=%s", rule_id)
        return {"revoked": rule_id}

    async def verify_rule(self, rule: ConstitutionalRule, chain_key: str) -> bool:
        """Recompute a rule's HMAC and compare — False means tampered."""
        expected = sign_rule(
            chain_key,
            rule.rule_type,
            rule.parameters,
            rule.description,
            rule.applies_to,
            rule.created_at,
        )
        return hmac.compare_digest(rule.rule_hash, expected)

    @staticmethod
    def _row_to_rule(row) -> ConstitutionalRule:
        params = row["parameters"]
        if isinstance(params, str):
            params = json.loads(params)
        return ConstitutionalRule(
            id=str(row["id"]),
            rule_type=row["rule_type"],
            parameters=params or {},
            description=row["description"],
            applies_to=row["applies_to"],
            active=row["active"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
            rule_hash=row["rule_hash"],
            key_id=row["key_id"],
        )
