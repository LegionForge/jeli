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

from ..database.pool import AsyncPostgresPool
from .rules import ConstitutionalRule, RuleType, sign_rule

logger = logging.getLogger(__name__)

VALID_RULE_TYPES = {t.value for t in RuleType}


class ConstitutionalError(Exception):
    """Raised for invalid rule input; message is safe to surface to the user."""


class ConstitutionalManager:
    """CRUD-lite over the constitutional_rules table."""

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
        """Active rules for the Read Gate — revoked_at IS NULL AND active."""
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
