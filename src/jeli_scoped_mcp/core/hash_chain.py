"""Hash-chain integrity: HMAC-SHA256 computation, chain validation, amendment tracking."""

import hashlib
import hmac
import json


def canonical_json(obj: dict) -> str:
    """
    Serialize dict to deterministic JSON (sorted keys, no whitespace).

    Essential for hash-chain: same logical object must always produce same JSON.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def compute_record_hash(
    chain_key: str,
    canonical_content: str,
    prev_record_hash: str | None = None,
) -> str:
    """
    Compute HMAC-SHA256 for a memory record, forming the hash-chain.

    Args:
        chain_key: Secret key (kept server-side, used for all records in chain)
        canonical_content: Canonical JSON representation of memory record
        prev_record_hash: Previous record's hash (optional for first record)

    Returns:
        Hex-encoded HMAC-SHA256 digest

    Why HMAC? Prevents tampering: attacker cannot modify content and recompute
    hash without knowing chain_key. Unlike simple SHA256, this protects against
    silent modification attacks.
    """
    if prev_record_hash is None:
        # First record in chain
        message = canonical_content
    else:
        # Subsequent records: include previous hash to form chain
        message = canonical_content + prev_record_hash

    return hmac.new(
        chain_key.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def build_canonical_record(
    content: str,
    embedding_model: str,
    embedding_dimensions: int,
    trust_score: float,
    memory_type: str,
    key_id: str,
    metadata: dict | None = None,
) -> str:
    """
    Build canonical JSON representation for hashing (canonical format v1).

    Includes all load-bearing fields that define the memory. Order must be
    consistent so that identical records produce identical hashes.

    Two representation rules exist for cross-driver hash stability:
    - trust_score is canonicalized to INTEGER HUNDREDTHS (0.6 -> 60) so
      float/Decimal round-trips through the DB can never change the hash
    - key_id is INSIDE the hash so a record cannot be re-pointed at a
      different (weaker/compromised) signing key without breaking its hash

    Args:
        content: Memory text
        embedding_model: Model ID (e.g., "openai/text-embedding-3-small")
        embedding_dimensions: Embedding vector dimensions (1536 for OpenAI)
        trust_score: Numeric trust level (0.3-1.0)
        memory_type: Classification (preference, identity, episodic, etc.)
        key_id: Identifier of the chain key that signs this record
        metadata: Optional dict for additional attributes

    Returns:
        Canonical JSON string
    """
    record = {
        "content": content,
        "embedding_model": embedding_model,
        "embedding_dimensions": embedding_dimensions,
        "trust_hundredths": round(trust_score * 100),
        "memory_type": memory_type,
        "key_id": key_id,
    }
    if metadata:
        record["metadata"] = metadata
    return canonical_json(record)


class HashChainValidator:
    """Validate hash-chain integrity and detect tampering."""

    def __init__(self, chain_key: str):
        """Initialize with the chain key (kept server-side)."""
        self.chain_key = chain_key

    def validate_record(
        self,
        canonical_content: str,
        record_hash: str,
        prev_record_hash: str | None = None,
    ) -> bool:
        """
        Verify a record's hash matches expected value.

        Args:
            canonical_content: Canonical JSON of record content
            record_hash: Hash claimed by the record
            prev_record_hash: Previous record's hash (if not first in chain)

        Returns:
            True if hash is valid, False if tampering detected
        """
        expected_hash = compute_record_hash(
            self.chain_key,
            canonical_content,
            prev_record_hash,
        )
        return hmac.compare_digest(record_hash, expected_hash)

    def validate_chain(
        self,
        records: list[dict],
    ) -> tuple[bool, str | None]:
        """
        Validate entire chain integrity.

        Walks the chain, verifying each record's hash. If any record fails
        validation, returns False and the ID of the first bad record.

        Args:
            records: Ordered list of memory records (oldest to newest)
                Each record dict must have: canonical_content, record_hash, prev_hash

        Returns:
            Tuple of (chain_valid: bool, first_bad_record_id: Optional[str])
        """
        if not records:
            return True, None

        prev_hash = None
        for record in records:
            canonical_content = str(record.get("canonical_content") or "")
            record_hash = str(record.get("record_hash") or "")
            record_id = record.get("id")

            if not self.validate_record(canonical_content, record_hash, prev_hash):
                return False, record_id

            prev_hash = record_hash

        return True, None


class AmendmentTracker:
    """Track amendments and validate amendment chains."""

    @staticmethod
    def is_amendment(
        old_trust_score: float,
        new_trust_score: float,
        old_canonical: str,
        new_canonical: str,
    ) -> tuple[bool, str | None]:
        """
        Determine if a new record is an amendment of an old one.

        Amendment heuristics:
        1. New trust_score >= 0.9 (user-confirmed correction)
        2. Content is similar enough to suggest a correction (same memory_type, etc.)
        3. Both records should be for the same entity/session

        Args:
            old_trust_score: Trust of original record
            new_trust_score: Trust of new record
            old_canonical: Canonical JSON of old record
            new_canonical: Canonical JSON of new record

        Returns:
            Tuple of (is_amendment: bool, reason: Optional[str])
        """
        # High trust from user signals a correction
        if new_trust_score < 0.9:
            return False, "New trust_score < 0.9 (not user-confirmed)"

        # Check if memory_type is the same (needed for amendment to make sense)
        try:
            old_obj = json.loads(old_canonical)
            new_obj = json.loads(new_canonical)

            if old_obj.get("memory_type") != new_obj.get("memory_type"):
                return False, "memory_type differs (not same fact)"

            # Amendment is plausible
            return True, "User-confirmed correction (trust >= 0.9)"
        except json.JSONDecodeError:
            return False, "Failed to parse canonical JSON"

    @staticmethod
    def compute_delta_embedding(
        old_embedding: list[float],
        new_embedding: list[float],
    ) -> float:
        """
        Compute L2 distance between two embeddings for amendment audit.

        Used to track how much the embedding changed when a memory is amended.
        Large delta suggests significant content change; small delta suggests
        minor correction.

        Args:
            old_embedding: Previous embedding vector
            new_embedding: New embedding vector

        Returns:
            L2 distance (Euclidean distance)
        """
        if len(old_embedding) != len(new_embedding):
            raise ValueError("Embedding dimensions must match")

        sum_sq = sum(
            (new - old) ** 2 for old, new in zip(old_embedding, new_embedding, strict=True)
        )
        return float(sum_sq**0.5)
