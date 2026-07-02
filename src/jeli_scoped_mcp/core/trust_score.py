"""Trust scoring model: assign trust to memories based on source and verification."""

from enum import Enum


class TrustSource(Enum):
    """Enumeration of trust sources and their default scores."""

    # User directly stated (highest authority)
    USER_STATED = 1.0

    # User confirmed agent proposal (near ground truth)
    USER_CONFIRMED = 0.9

    # Agent inferred from conversation context
    AGENT_INFERRED = 0.6

    # Agent observed from behavior (noisy signal)
    BEHAVIOR_INFERRED = 0.4

    # External source (web, document, article)
    EXTERNAL = 0.3


class TrustScorer:
    """Compute and validate trust scores for memory writes."""

    # Trust score bounds (per spec)
    MIN_TRUST = 0.3
    MAX_TRUST = 1.0

    @classmethod
    def clamp(cls, score: float) -> float:
        """Ensure trust score is within valid range [0.3, 1.0]."""
        return max(cls.MIN_TRUST, min(cls.MAX_TRUST, score))

    @classmethod
    def validate(cls, score: float) -> tuple[bool, str | None]:
        """
        Validate a trust score.

        Args:
            score: Trust score to validate

        Returns:
            Tuple of (is_valid: bool, error_msg: Optional[str])
        """
        if not isinstance(score, (int, float)):
            return False, f"Trust score must be numeric, got {type(score).__name__}"

        if score < 0.0:
            return False, f"Trust score {score} is below minimum 0.0"

        if score > 1.0:
            return False, f"Trust score {score} exceeds maximum 1.0"

        return True, None

    @classmethod
    def is_user_confirmed(cls, score: float) -> bool:
        """Check if score indicates user confirmation (>= 0.9)."""
        return score >= 0.9

    @classmethod
    def is_agent_inferred(cls, score: float) -> bool:
        """Check if score indicates agent inference (0.4-0.6)."""
        return 0.4 <= score < 0.9

    @classmethod
    def should_flag_for_judicial_review(cls, score: float) -> bool:
        """
        Determine if score indicates potential issues requiring review.

        Low trust scores (< 0.5) may need judicial oversight before
        being acted upon.
        """
        return score < 0.5

    @classmethod
    def compute_confidence_interval(
        cls,
        trust_score: float,
        update_count: int = 0,
    ) -> tuple[float, float]:
        """
        Estimate confidence interval for a trust score.

        As a memory is confirmed multiple times, confidence increases.
        Conversely, low-trust memories need more confirmations to become reliable.

        Args:
            trust_score: Current trust score
            update_count: Number of times memory has been confirmed/updated

        Returns:
            Tuple of (lower_bound, upper_bound) for 95% confidence interval
        """
        # Simple model: each confirmation tightens the interval
        base_margin = 1.0 - trust_score  # Higher trust = smaller margin
        adjustment = min(1.0, update_count * 0.05)  # Up to 5% reduction per confirmation
        margin = base_margin * (1.0 - adjustment)

        return (
            max(cls.MIN_TRUST, trust_score - margin),
            min(cls.MAX_TRUST, trust_score + margin),
        )

    @classmethod
    def infer_from_context(
        cls,
        is_user_spoken: bool = False,
        is_user_typed: bool = False,
        is_agent_proposed: bool = False,
        is_explicit_confirmation: bool = False,
    ) -> float:
        """
        Infer trust score from capture context.

        Args:
            is_user_spoken: User spoke the memory (high confidence)
            is_user_typed: User typed/entered the memory (high confidence)
            is_agent_proposed: Agent proposed, user reviewing (medium)
            is_explicit_confirmation: User explicitly confirmed (very high)

        Returns:
            Inferred trust score [0.3, 1.0]
        """
        if is_explicit_confirmation:
            return TrustSource.USER_CONFIRMED.value

        if is_user_spoken or is_user_typed:
            return TrustSource.USER_STATED.value

        if is_agent_proposed:
            return TrustSource.AGENT_INFERRED.value

        # Default: low trust for unverified external sources
        return TrustSource.EXTERNAL.value


class TrustAdjustment:
    """Logic for adjusting trust scores over time."""

    @staticmethod
    def decay_over_time(
        original_score: float,
        days_elapsed: int,
        decay_rate: float = 0.01,
    ) -> float:
        """
        Decay trust score as time passes.

        Older memories (especially low-trust ones) become less reliable
        without re-confirmation. This models memory decay and context drift.

        Args:
            original_score: Initial trust score
            days_elapsed: Number of days since memory creation
            decay_rate: Daily decay rate (default 1% per day)

        Returns:
            Adjusted trust score
        """
        if original_score >= 0.9:
            # User-confirmed facts don't decay
            return original_score

        # Decay: score * (1 - decay_rate) ^ days
        decay_factor = (1.0 - decay_rate) ** days_elapsed
        adjusted = original_score * decay_factor

        return max(TrustScorer.MIN_TRUST, adjusted)

    @staticmethod
    def boost_from_confirmation(
        current_score: float,
        confirmation_strength: float = 0.1,
    ) -> float:
        """
        Boost trust score when memory is confirmed again.

        Args:
            current_score: Current trust score
            confirmation_strength: How much to boost (default 0.1)

        Returns:
            Increased trust score (up to 1.0)
        """
        boosted = min(1.0, current_score + confirmation_strength)
        return boosted

    @staticmethod
    def penalize_from_contradiction(
        current_score: float,
        contradiction_severity: str = "low",
    ) -> float:
        """
        Penalize trust score when contradiction is detected.

        Args:
            current_score: Current trust score
            contradiction_severity: 'low', 'medium', or 'high'

        Returns:
            Reduced trust score
        """
        penalties = {
            "low": 0.05,
            "medium": 0.15,
            "high": 0.3,
        }
        penalty = penalties.get(contradiction_severity, 0.05)
        penalized = max(TrustScorer.MIN_TRUST, current_score - penalty)
        return penalized
