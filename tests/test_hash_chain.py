"""Unit tests for hash-chain integrity and amendment tracking."""

import pytest
from jeli_scoped_mcp.core import (
    AmendmentTracker,
    HashChainValidator,
    build_canonical_record,
    canonical_json,
    compute_record_hash,
)


class TestCanonicalJson:
    """Test deterministic JSON serialization."""

    def test_same_dict_produces_same_json(self):
        """Keys sorted, output deterministic."""
        obj = {"c": 3, "a": 1, "b": 2}
        result = canonical_json(obj)
        assert result == '{"a":1,"b":2,"c":3}'

    def test_different_key_order_same_result(self):
        """Order of keys in input doesn't affect output."""
        obj1 = canonical_json({"x": 1, "y": 2})
        obj2 = canonical_json({"y": 2, "x": 1})
        assert obj1 == obj2

    def test_no_whitespace(self):
        """Compact output, no spaces."""
        result = canonical_json({"key": "value"})
        assert " " not in result


class TestComputeRecordHash:
    """Test HMAC-SHA256 computation."""

    def test_first_record_hash(self):
        """Hash computed without prev_record_hash for first record."""
        chain_key = "secret_key_12345"
        content = "test content"
        hash1 = compute_record_hash(chain_key, content)
        # Should be deterministic
        hash2 = compute_record_hash(chain_key, content)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex is 64 chars

    def test_hash_depends_on_chain_key(self):
        """Different chain keys produce different hashes."""
        content = "same content"
        hash1 = compute_record_hash("key_a", content)
        hash2 = compute_record_hash("key_b", content)
        assert hash1 != hash2

    def test_chain_formation(self):
        """Hash of subsequent record includes previous hash."""
        chain_key = "secret"
        content1 = "first"
        hash1 = compute_record_hash(chain_key, content1)

        content2 = "second"
        hash2 = compute_record_hash(chain_key, content2, prev_record_hash=hash1)

        # Computing hash2 with same prev_hash should give same result
        hash2_again = compute_record_hash(chain_key, content2, prev_record_hash=hash1)
        assert hash2 == hash2_again

    def test_tampering_changes_hash(self):
        """If content is modified, hash changes."""
        chain_key = "secret"
        hash1 = compute_record_hash(chain_key, "original")
        hash2 = compute_record_hash(chain_key, "modified")
        assert hash1 != hash2


class TestBuildCanonicalRecord:
    """Test canonical record construction."""

    def test_canonical_record_format(self):
        """Record includes all required fields."""
        result = build_canonical_record(
            content="test memory",
            embedding_model="openai/text-embedding-3-small",
            embedding_dimensions=1536,
            trust_score=0.95,
            memory_type="preference",
        )
        assert "test memory" in result
        assert "openai/text-embedding-3-small" in result
        assert "1536" in result
        assert "preference" in result

    def test_canonical_record_deterministic(self):
        """Same inputs produce same canonical form."""
        record1 = build_canonical_record(
            content="fact",
            embedding_model="model_a",
            embedding_dimensions=1536,
            trust_score=0.9,
            memory_type="semantic",
        )
        record2 = build_canonical_record(
            content="fact",
            embedding_model="model_a",
            embedding_dimensions=1536,
            trust_score=0.9,
            memory_type="semantic",
        )
        assert record1 == record2

    def test_canonical_record_with_metadata(self):
        """Metadata is included in canonical form."""
        result = build_canonical_record(
            content="test",
            embedding_model="model",
            embedding_dimensions=768,
            trust_score=0.6,
            memory_type="episodic",
            metadata={"session_id": "abc123"},
        )
        assert "session_id" in result


class TestHashChainValidator:
    """Test hash-chain validation."""

    def test_valid_single_record(self):
        """Single record with correct hash validates."""
        chain_key = "secret"
        canonical = "content"
        record_hash = compute_record_hash(chain_key, canonical)

        validator = HashChainValidator(chain_key)
        assert validator.validate_record(canonical, record_hash)

    def test_invalid_hash_detected(self):
        """Tampering (wrong hash) is detected."""
        chain_key = "secret"
        canonical = "original content"
        record_hash = compute_record_hash(chain_key, canonical)

        # Attacker modifies content but keeps old hash
        validator = HashChainValidator(chain_key)
        assert not validator.validate_record("modified content", record_hash)

    def test_chain_validation_success(self):
        """Valid chain passes validation."""
        chain_key = "secret"

        # Build a valid chain
        canonical1 = "first"
        hash1 = compute_record_hash(chain_key, canonical1)

        canonical2 = "second"
        hash2 = compute_record_hash(chain_key, canonical2, prev_record_hash=hash1)

        records = [
            {"canonical_content": canonical1, "record_hash": hash1, "prev_hash": None, "id": "1"},
            {"canonical_content": canonical2, "record_hash": hash2, "prev_hash": hash1, "id": "2"},
        ]

        validator = HashChainValidator(chain_key)
        valid, bad_id = validator.validate_chain(records)
        assert valid is True
        assert bad_id is None

    def test_chain_validation_detects_break(self):
        """Chain break (tampering) is detected."""
        chain_key = "secret"

        canonical1 = "first"
        hash1 = compute_record_hash(chain_key, canonical1)

        canonical2 = "second"
        hash2 = compute_record_hash(chain_key, canonical2, prev_record_hash=hash1)

        # Tamper: modify the second record's content but keep old hash
        records = [
            {"canonical_content": canonical1, "record_hash": hash1, "prev_hash": None, "id": "1"},
            {"canonical_content": "tampered!", "record_hash": hash2, "prev_hash": hash1, "id": "2"},
        ]

        validator = HashChainValidator(chain_key)
        valid, bad_id = validator.validate_chain(records)
        assert valid is False
        assert bad_id == "2"

    def test_empty_chain_valid(self):
        """Empty chain is valid (no records to validate)."""
        validator = HashChainValidator("secret")
        valid, bad_id = validator.validate_chain([])
        assert valid is True
        assert bad_id is None


class TestAmendmentTracker:
    """Test amendment detection and tracking."""

    def test_high_trust_amendment_detected(self):
        """User-confirmed correction (trust >= 0.9) is marked as amendment."""
        old_canonical = build_canonical_record(
            content="original fact",
            embedding_model="model",
            embedding_dimensions=768,
            trust_score=0.6,
            memory_type="semantic",
        )
        new_canonical = build_canonical_record(
            content="corrected fact",
            embedding_model="model",
            embedding_dimensions=768,
            trust_score=0.95,  # User-confirmed
            memory_type="semantic",
        )

        is_amend, reason = AmendmentTracker.is_amendment(
            old_trust_score=0.6,
            new_trust_score=0.95,
            old_canonical=old_canonical,
            new_canonical=new_canonical,
        )
        assert is_amend is True
        assert "User-confirmed" in reason

    def test_low_trust_not_amendment(self):
        """Low trust score doesn't qualify as amendment."""
        old_canonical = build_canonical_record(
            content="fact1",
            embedding_model="model",
            embedding_dimensions=768,
            trust_score=0.4,
            memory_type="semantic",
        )
        new_canonical = build_canonical_record(
            content="fact2",
            embedding_model="model",
            embedding_dimensions=768,
            trust_score=0.5,  # Still low
            memory_type="semantic",
        )

        is_amend, reason = AmendmentTracker.is_amendment(
            old_trust_score=0.4,
            new_trust_score=0.5,
            old_canonical=old_canonical,
            new_canonical=new_canonical,
        )
        assert is_amend is False

    def test_different_memory_type_not_amendment(self):
        """Different memory_type means different fact, not amendment."""
        old_canonical = build_canonical_record(
            content="preference fact",
            embedding_model="model",
            embedding_dimensions=768,
            trust_score=1.0,
            memory_type="preference",
        )
        new_canonical = build_canonical_record(
            content="episodic fact",
            embedding_model="model",
            embedding_dimensions=768,
            trust_score=0.95,
            memory_type="episodic",  # Different type
        )

        is_amend, reason = AmendmentTracker.is_amendment(
            old_trust_score=1.0,
            new_trust_score=0.95,
            old_canonical=old_canonical,
            new_canonical=new_canonical,
        )
        assert is_amend is False
        assert "differs" in reason

    def test_delta_embedding_computation(self):
        """L2 distance between embeddings is computed correctly."""
        old = [1.0, 0.0, 0.0]
        new = [0.0, 0.0, 0.0]
        delta = AmendmentTracker.compute_delta_embedding(old, new)
        assert delta == pytest.approx(1.0)  # L2 distance is 1

    def test_delta_embedding_zero_for_identical(self):
        """Identical embeddings have zero delta."""
        vec = [1.0, 2.0, 3.0]
        delta = AmendmentTracker.compute_delta_embedding(vec, vec)
        assert delta == pytest.approx(0.0)

    def test_delta_embedding_dimension_mismatch(self):
        """Dimension mismatch raises error."""
        old = [1.0, 2.0]
        new = [1.0, 2.0, 3.0]
        with pytest.raises(ValueError, match="dimensions must match"):
            AmendmentTracker.compute_delta_embedding(old, new)
