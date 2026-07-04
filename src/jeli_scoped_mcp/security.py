"""Security layer: API key validation, injection defense, input sanitization."""

import hmac
import re
from typing import Literal

# Recognised content categories for the two-axis trust model.
ContentClass = Literal["general", "security-doc", "code-sample", "external-untrusted"]

# Source trust threshold above which authoritative content classes bypass the
# injection trust cap (pattern is still flagged and logged, just not penalised).
AUTHORITATIVE_SOURCE_TRUST = 0.9

# Content classes that, when combined with authoritative source trust, override
# the injection cap.  "security-doc" is the primary use-case: JP's own session
# notes describing attacks look injection-like but are ground-truth.
AUTHORITATIVE_CONTENT_CLASSES: frozenset[str] = frozenset({"security-doc"})


class APIKeyValidator:
    """Timing-safe API key validation using HMAC constant-time comparison."""

    def __init__(self, expected_key: str):
        """Initialize with expected API key."""
        self.expected_key = expected_key.encode()

    def verify(self, provided_key: str) -> bool:
        """
        Verify provided key matches expected key using timing-safe comparison.

        Uses hmac.compare_digest to prevent timing oracle attacks.
        Always takes same time regardless of where mismatch occurs.
        """
        provided_bytes = provided_key.encode()
        return hmac.compare_digest(self.expected_key, provided_bytes)


class InjectionDefense:
    """Detect and defend against prompt injection and SQL injection attacks."""

    # Prompt injection patterns (from Firmwright, validated in MINJA research)
    PROMPT_INJECTION_PATTERNS = [
        r"ignore\s+(previous|above|prior|all|instructions|everything)",
        r"\bsystem\s*:",
        r"(you are|act as|pretend to be|roleplay as)\s+\w",
        r"\[SYSTEM\]\s*:",
        r"\[INSTRUCTION\]\s*:",
        r"\[ADMIN\]\s*:",
        r"\[CRITICAL\]\s*:",
        r"\badmin\s+mode",
        r"\bbypass",
        r"\boverride",
        r"\binstead\s+of",
        r"disregard\s+(previous|above|prior|all)",
    ]

    # SQL injection patterns (for search_memory SQL mode validation)
    SQL_DANGEROUS_PATTERNS = [
        r"\bDROP\s+(TABLE|DATABASE|SCHEMA)",
        r"\bDELETE\s+(?:FROM)?",  # Catch DELETE with or without FROM
        r"\bTRUNCATE",
        r"\bALTER\s+TABLE",
        r"\bUNION\s+(?:ALL\s+)?SELECT",
        r"\bEXEC\s*\(",
        r"--\s+",  # SQL comments
        r";\s*\w+\s*(?:SELECT|DROP|DELETE|INSERT|UPDATE|TRUNCATE)",  # Multiple statements
        r"/\*.*?\*/",  # Multi-line comments
        r"\(\s*SELECT",  # Subqueries
    ]

    # Whitelisted columns for SQL mode search_memory
    ALLOWED_SQL_COLUMNS = {
        "content",
        "trust_score",
        "memory_type",
        "created_at",
        "embedding_model",
        "embedding_dimensions",
    }

    @classmethod
    def is_instruction_like(cls, text: str) -> bool:
        """Check if text contains instruction-like patterns (prompt injection indicators)."""
        return any(re.search(p, text, re.IGNORECASE) for p in cls.PROMPT_INJECTION_PATTERNS)

    @classmethod
    def detect_sql_injection_patterns(cls, query: str) -> bool:
        """Detect dangerous SQL patterns. Returns True if dangerous pattern found."""
        for pattern in cls.SQL_DANGEROUS_PATTERNS:
            if re.search(pattern, query, re.IGNORECASE):
                return True
        return False

    @classmethod
    def validate_sql_query(cls, query: str) -> None:
        """
        Validate SQL query for mode=sql in search_memory.

        Raises ValueError if dangerous patterns detected.
        Only allows: WHERE, ORDER BY on whitelisted columns.
        Rejects: UNION, DROP, DELETE, EXEC, etc.
        """
        if not query:
            raise ValueError("Query cannot be empty")

        # Check for dangerous patterns
        if cls.detect_sql_injection_patterns(query):
            raise ValueError("Dangerous SQL pattern detected in query")

        # Check for suspicious keywords
        suspicious = {"UNION", "DROP", "DELETE", "INSERT", "UPDATE", "EXEC", "CALL", "PRAGMA"}
        query_upper = query.upper()
        for keyword in suspicious:
            if re.search(rf"\b{keyword}\b", query_upper):
                raise ValueError(f"Dangerous keyword '{keyword}' detected in query")

        # Column whitelist enforcement (ALLOWED_SQL_COLUMNS) arrives with
        # the sql search mode; fts mode never reaches raw SQL.

    @classmethod
    def sanitize_content(
        cls,
        content: str,
        max_length: int = 10000,
        source_trust: float = 0.0,
        content_class: str = "general",
    ) -> tuple[str, bool, str | None]:
        """Sanitize content for capture_memory.

        Returns: (sanitized_content, is_flagged, trust_override_reason)

        Two-axis trust logic:
        - Unknown/low-trust sources with injection patterns → cap trust at
          FLAGGED_TRUST_CEILING (0.3) in the caller; override_reason is None.
        - Authoritative source (trust >= 0.9) with a recognised content class
          (security-doc) → preserve trust; override_reason explains why the cap
          was skipped so the audit trail is unambiguous.
        """
        if len(content) > max_length:
            content = content[:max_length]

        is_flagged = cls.is_instruction_like(content)
        override_reason: str | None = None

        if is_flagged and source_trust >= AUTHORITATIVE_SOURCE_TRUST:
            if content_class in AUTHORITATIVE_CONTENT_CLASSES:
                override_reason = (
                    f"authoritative-{content_class}: "
                    f"source_trust={source_trust:.2f} qualifies for trust preservation"
                )

        return content, is_flagged, override_reason

    @classmethod
    def validate_embedding_dimensions(cls, dimensions: int, model_id: str) -> bool:
        """Validate embedding dimensions match the model."""
        if dimensions <= 0:
            return False

        expected_dims = {
            # openai is truncated to the 1024 index standard (matryoshka)
            "openai/text-embedding-3-small": 1024,
            "ollama/nomic-embed-text": 768,
            "ollama/snowflake-arctic-embed2": 1024,
            "ollama/qwen3-embedding": 1024,
            "ollama/bge-m3": 1024,
        }

        expected = expected_dims.get(model_id)
        if expected is not None:
            return dimensions == expected

        # Unknown model: check if dimensions are reasonable
        # Typical embeddings range from 128 to 4096
        return 64 <= dimensions <= 4096


def validate_api_key(provided_key: str, expected_key: str) -> bool:
    """Convenience function: validate API key."""
    validator = APIKeyValidator(expected_key)
    return validator.verify(provided_key)
