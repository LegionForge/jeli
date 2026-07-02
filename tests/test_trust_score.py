"""Unit tests for trust scoring model."""

from jeli_scoped_mcp.core import (
    TrustAdjustment,
    TrustScorer,
    TrustSource,
)


class TestTrustSource:
    """Test trust source enumeration."""

    def test_trust_values_in_range(self):
        """All trust sources have values in valid range."""
        for source in TrustSource:
            assert 0.3 <= source.value <= 1.0

    def test_hierarchy(self):
        """Trust sources are properly ordered by authority."""
        assert TrustSource.USER_STATED.value == 1.0
        assert TrustSource.USER_CONFIRMED.value == 0.9
        assert TrustSource.AGENT_INFERRED.value == 0.6
        assert TrustSource.BEHAVIOR_INFERRED.value == 0.4
        assert TrustSource.EXTERNAL.value == 0.3


class TestTrustScorer:
    """Test trust score validation and computation."""

    def test_clamp_below_minimum(self):
        """Values below minimum are clamped to 0.3."""
        assert TrustScorer.clamp(0.0) == 0.3
        assert TrustScorer.clamp(-1.0) == 0.3

    def test_clamp_above_maximum(self):
        """Values above maximum are clamped to 1.0."""
        assert TrustScorer.clamp(1.1) == 1.0
        assert TrustScorer.clamp(2.0) == 1.0

    def test_clamp_valid_range(self):
        """Valid values pass through unchanged."""
        assert TrustScorer.clamp(0.5) == 0.5
        assert TrustScorer.clamp(0.9) == 0.9

    def test_validate_numeric_types(self):
        """Valid numeric types are accepted."""
        valid, msg = TrustScorer.validate(0.5)
        assert valid is True
        assert msg is None

        valid, msg = TrustScorer.validate(1)
        assert valid is True

    def test_validate_non_numeric_rejected(self):
        """Non-numeric types are rejected."""
        valid, msg = TrustScorer.validate("0.5")
        assert valid is False
        assert "numeric" in msg

    def test_validate_out_of_range(self):
        """Out-of-range values are rejected."""
        valid, msg = TrustScorer.validate(-0.5)
        assert valid is False
        assert "below" in msg

        valid, msg = TrustScorer.validate(1.5)
        assert valid is False
        assert "exceeds" in msg

    def test_is_user_confirmed(self):
        """Trust >= 0.9 is user-confirmed."""
        assert TrustScorer.is_user_confirmed(1.0) is True
        assert TrustScorer.is_user_confirmed(0.9) is True
        assert TrustScorer.is_user_confirmed(0.89) is False

    def test_is_agent_inferred(self):
        """Trust 0.4-0.6 is agent-inferred."""
        assert TrustScorer.is_agent_inferred(0.6) is True
        assert TrustScorer.is_agent_inferred(0.5) is True
        assert TrustScorer.is_agent_inferred(0.4) is True
        assert TrustScorer.is_agent_inferred(0.39) is False
        assert TrustScorer.is_agent_inferred(0.9) is False

    def test_should_flag_for_judicial_review(self):
        """Trust < 0.5 should be flagged."""
        assert TrustScorer.should_flag_for_judicial_review(0.3) is True
        assert TrustScorer.should_flag_for_judicial_review(0.49) is True
        assert TrustScorer.should_flag_for_judicial_review(0.5) is False
        assert TrustScorer.should_flag_for_judicial_review(0.9) is False

    def test_confidence_interval_user_stated(self):
        """User-stated facts have tight confidence intervals."""
        lower, upper = TrustScorer.compute_confidence_interval(1.0)
        # High trust score means small margin
        assert lower > 0.9
        assert upper >= 1.0

    def test_confidence_interval_external(self):
        """External sources have wide confidence intervals."""
        lower, upper = TrustScorer.compute_confidence_interval(0.3)
        # Low trust score means large margin (clamped to max 1.0)
        assert lower <= 0.3
        assert upper >= 0.9  # Will be clamped to 1.0

    def test_confidence_interval_improves_with_confirmations(self):
        """Confidence interval tightens with confirmations."""
        initial_lower, initial_upper = TrustScorer.compute_confidence_interval(0.6, update_count=0)
        improved_lower, improved_upper = TrustScorer.compute_confidence_interval(
            0.6, update_count=5
        )

        # Interval should be smaller after confirmations
        assert improved_upper - improved_lower < initial_upper - initial_lower

    def test_infer_user_spoken(self):
        """User-spoken input has high trust."""
        score = TrustScorer.infer_from_context(is_user_spoken=True)
        assert score == 1.0

    def test_infer_user_typed(self):
        """User-typed input has high trust."""
        score = TrustScorer.infer_from_context(is_user_typed=True)
        assert score == 1.0

    def test_infer_user_confirmed(self):
        """Explicit confirmation has near-maximum trust."""
        score = TrustScorer.infer_from_context(is_explicit_confirmation=True)
        assert score == 0.9

    def test_infer_agent_proposed(self):
        """Agent-proposed without confirmation has medium trust."""
        score = TrustScorer.infer_from_context(is_agent_proposed=True)
        assert score == 0.6

    def test_infer_default_external(self):
        """No context defaults to external source trust."""
        score = TrustScorer.infer_from_context()
        assert score == 0.3

    def test_infer_precedence(self):
        """Explicit confirmation takes precedence over others."""
        score = TrustScorer.infer_from_context(
            is_user_spoken=True,
            is_explicit_confirmation=True,
        )
        assert score == 0.9  # Confirmation wins


class TestTrustAdjustment:
    """Test trust score adjustments over time and events."""

    def test_decay_user_confirmed_not_affected(self):
        """User-confirmed facts don't decay."""
        original = 0.9
        decayed = TrustAdjustment.decay_over_time(original, days_elapsed=30)
        assert decayed == original

    def test_decay_agent_inferred_over_time(self):
        """Agent-inferred facts decay with time."""
        original = 0.6
        decayed = TrustAdjustment.decay_over_time(original, days_elapsed=10)
        # Expect some decay
        assert decayed < original
        assert decayed >= 0.3

    def test_decay_rate_parameter(self):
        """Custom decay rate affects the decay."""
        original = 0.6
        fast_decay = TrustAdjustment.decay_over_time(original, days_elapsed=10, decay_rate=0.05)
        slow_decay = TrustAdjustment.decay_over_time(original, days_elapsed=10, decay_rate=0.01)
        # Faster decay should result in lower score
        assert fast_decay < slow_decay

    def test_boost_from_confirmation(self):
        """Confirmation increases trust score."""
        original = 0.6
        boosted = TrustAdjustment.boost_from_confirmation(original)
        assert boosted > original
        assert boosted <= 1.0

    def test_boost_capped_at_maximum(self):
        """Boost cannot exceed 1.0."""
        boosted = TrustAdjustment.boost_from_confirmation(1.0)
        assert boosted == 1.0

        boosted = TrustAdjustment.boost_from_confirmation(0.95, confirmation_strength=0.1)
        assert boosted == 1.0

    def test_boost_custom_strength(self):
        """Custom boost strength affects increase."""
        original = 0.5
        small_boost = TrustAdjustment.boost_from_confirmation(original, confirmation_strength=0.05)
        large_boost = TrustAdjustment.boost_from_confirmation(original, confirmation_strength=0.2)

        assert large_boost > small_boost
        assert small_boost > original

    def test_penalize_low_contradiction(self):
        """Low contradiction slightly penalizes trust."""
        original = 0.8
        penalized = TrustAdjustment.penalize_from_contradiction(original, "low")
        assert penalized < original
        assert penalized > original - 0.1

    def test_penalize_high_contradiction(self):
        """High contradiction severely penalizes trust."""
        original = 0.8
        penalized = TrustAdjustment.penalize_from_contradiction(original, "high")
        assert penalized < original
        assert penalized <= original - 0.2

    def test_penalize_capped_at_minimum(self):
        """Penalty cannot drop below minimum."""
        penalized = TrustAdjustment.penalize_from_contradiction(0.35, "high")
        assert penalized >= 0.3

    def test_penalize_unknown_severity_defaults(self):
        """Unknown severity defaults to 'low' penalty."""
        original = 0.7
        penalized = TrustAdjustment.penalize_from_contradiction(original, "unknown")
        # Should apply default penalty (0.05)
        assert penalized < original
        assert penalized > original - 0.1
