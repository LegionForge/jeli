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
from ..security import VALID_CONTENT_CLASSES
from ..tools.memory_tools import VALID_MEMORY_TYPES
from .rules import ConstitutionalRule, RuleType, sign_rule, sign_rule_event

logger = logging.getLogger(__name__)

VALID_RULE_TYPES = {t.value for t in RuleType}


class ConstitutionalError(Exception):
    """Raised for invalid rule input; message is safe to surface to the user."""


class ConstitutionalIntegrityError(ConstitutionalError):
    """Raised when a stored constitutional rule cannot be authenticated."""


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

    def _require_nonempty_string(key: str) -> str:
        _require(key)
        value = parameters[key]
        if not isinstance(value, str) or not value.strip():
            raise ConstitutionalError(f"parameter '{key}' must be a non-empty string")
        return value

    def _require_choice(key: str, choices: set[str] | frozenset[str]) -> None:
        value = _require_nonempty_string(key)
        if value not in choices:
            raise ConstitutionalError(f"parameter '{key}' must be one of {sorted(choices)}")

    def _require_unit_float(key: str) -> None:
        _require(key)
        v = parameters[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
            raise ConstitutionalError(
                f"parameter '{key}' must be a number between 0.0 and 1.0"
            )

    def _require_nonneg_int(key: str) -> None:
        _require(key)
        v = parameters[key]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ConstitutionalError(f"parameter '{key}' must be a non-negative integer")

    if rule_type == RuleType.EXCLUDE_MEMORY_TYPE.value:
        _require_choice("memory_type", VALID_MEMORY_TYPES)
    elif rule_type == RuleType.MIN_TRUST_FLOOR.value:
        _require_unit_float("floor")
    elif rule_type == RuleType.EXCLUDE_TAG.value:
        _require_nonempty_string("tag")
    elif rule_type == RuleType.EXCLUDE_CONTENT_CLASS.value:
        _require_choice("content_class", VALID_CONTENT_CLASSES)
    elif rule_type == RuleType.MAX_RESULTS.value:
        _require_nonneg_int("max_results")
    elif rule_type == RuleType.DENY_WRITE_MEMORY_TYPE.value:
        _require_choice("memory_type", VALID_MEMORY_TYPES)
    elif rule_type == RuleType.MAX_TRUST_FOR_CONTENT_CLASS.value:
        _require_choice("content_class", VALID_CONTENT_CLASSES)
        _require_unit_float("max_trust")


class ConstitutionalManager:
    """CRUD-lite over the constitutional_rules table.

    load_active_rules is on the hottest path (every search and capture), so it
    is backed by a per-instance TTL cache. The cache is per-instance rather than
    class-level so each server/CLI process owns its own state; a process's own
    mutations invalidate it immediately, and cross-process changes converge
    within one TTL window.
    """

    def __init__(
        self,
        ttl: float = 30.0,
        key_registry: dict[str, str] | None = None,
    ) -> None:
        self._cache: list[ConstitutionalRule] | None = None
        self._cache_expires: float = 0.0
        self._CACHE_TTL = ttl
        # When configured, every active rule must authenticate before it can
        # influence a gate.  None preserves an explicit inspection-only mode
        # for callers that do not enforce rules (for example migration tools).
        self._key_registry = None if key_registry is None else dict(key_registry)

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
        await self._authenticate_active_rules(rules)
        active_rules = [rule for rule in rules if rule.active]
        self._cache = active_rules
        self._cache_expires = time.monotonic() + self._CACHE_TTL
        return active_rules

    async def _fetch_from_db(
        self, db: AsyncPostgresPool
    ) -> list[ConstitutionalRule]:
        rows = await db.fetchall(
            """
            SELECT r.id, r.rule_type, r.parameters, r.description, r.applies_to,
                   r.active, r.created_at, r.revoked_at, r.rule_hash, r.key_id,
                   e.event_type AS lifecycle_event_type,
                   e.event_at AS lifecycle_event_at,
                   e.event_hash AS lifecycle_event_hash,
                   e.key_id AS lifecycle_key_id,
                   e.migration_baseline
            FROM constitutional_rules AS r
            LEFT JOIN constitutional_rule_event AS e ON e.rule_id = r.id
            ORDER BY r.created_at ASC
            """
        )
        rules = []
        for row in rows:
            rule = self._row_to_rule(row)
            await self._derive_lifecycle(rule, row)
            rules.append(rule)
        return rules

    async def _derive_lifecycle(self, rule: ConstitutionalRule, row) -> None:
        """Derive authority only from an authenticated append-only event."""
        event_at = row.get("lifecycle_event_at")
        if event_at is None:
            rule.active = True
            rule.revoked_at = None
            return

        event_hash = row.get("lifecycle_event_hash")
        event_key_id = row.get("lifecycle_key_id")
        baseline = bool(row.get("migration_baseline"))
        if baseline:
            if event_hash is not None or event_key_id is not None:
                raise ConstitutionalIntegrityError(
                    f"constitutional rule {rule.id} has malformed lifecycle baseline"
                )
        elif self._key_registry is not None:
            chain_key = self._key_registry.get(event_key_id)
            if chain_key is None:
                raise ConstitutionalIntegrityError(
                    f"constitutional rule {rule.id} lifecycle event uses unknown key_id"
                )
            expected = sign_rule_event(
                chain_key,
                rule.id or "",
                rule.rule_hash,
                row.get("lifecycle_event_type"),
                event_at,
            )
            if event_hash is None or not hmac.compare_digest(event_hash, expected):
                raise ConstitutionalIntegrityError(
                    f"constitutional rule {rule.id} lifecycle event failed authentication"
                )
        rule.active = False
        rule.revoked_at = event_at

    async def _authenticate_active_rules(
        self, rules: list[ConstitutionalRule]
    ) -> None:
        """Fail closed before an unauthenticated rule reaches a gate."""
        if self._key_registry is None:
            return
        for rule in rules:
            chain_key = self._key_registry.get(rule.key_id)
            if chain_key is None:
                raise ConstitutionalIntegrityError(
                    f"constitutional rule {rule.id} uses unknown key_id; enforcement denied"
                )
            if not await self.verify_rule(rule, chain_key):
                raise ConstitutionalIntegrityError(
                    f"constitutional rule {rule.id} failed authentication; enforcement denied"
                )

    async def load_all_rules(self, db: AsyncPostgresPool) -> list[ConstitutionalRule]:
        """Every rule ever signed, revoked included — for verification.

        A revoked rule is retired history, not deleted history: its HMAC must
        still verify, otherwise tampering with the retired record would be
        undetectable. Uncached — verification always reads the DB.
        """
        rules = await self._fetch_from_db(db)
        await self._authenticate_active_rules(rules)
        return rules

    def invalidate_cache(self) -> None:
        """Force the next load_active_rules to hit the DB.

        Called after add_rule/revoke_rule so a process sees its own mutations
        immediately rather than up to one TTL later.
        """
        self._cache = None
        self._cache_expires = 0.0

    async def revoke_rule(
        self,
        db: AsyncPostgresPool,
        rule_id: str,
        chain_key: str,
        key_id: str,
    ) -> dict:
        """Retire a rule by appending one signed terminal event."""
        rule = await db.fetchrow(
            """
            SELECT r.id, r.rule_hash, now() AS event_at
            FROM constitutional_rules AS r
            WHERE r.id = $1
              AND NOT EXISTS (
                  SELECT 1 FROM constitutional_rule_event e
                  WHERE e.rule_id = r.id AND e.event_type = 'revoked'
              )
            """,
            rule_id,
        )
        if rule is None:
            raise ConstitutionalError(f"rule {rule_id} not found or already revoked")
        event_hash = sign_rule_event(
            chain_key, str(rule["id"]), rule["rule_hash"], "revoked", rule["event_at"]
        )
        inserted = await db.fetchrow(
            """
            INSERT INTO constitutional_rule_event
                (rule_id, event_type, event_at, event_hash, key_id)
            VALUES ($1, 'revoked', $2, $3, $4)
            RETURNING id
            """,
            rule_id,
            rule["event_at"],
            event_hash,
            key_id,
        )
        if inserted is None:
            raise ConstitutionalError("revocation insert failed: no row returned")
        self.invalidate_cache()
        logger.info("constitutional revoke_rule: id=%s", rule_id)
        return {"revoked": rule_id, "event_hash": event_hash}

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
