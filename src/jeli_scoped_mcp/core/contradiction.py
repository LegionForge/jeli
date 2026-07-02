"""Contradiction detection: identify conflicting memories for Judicial review."""

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ContradictionType(Enum):
    """Types of contradictions that can be flagged."""

    DIRECT = "direct"  # Explicit conflict (e.g., "I like coffee" vs "I like tea")
    TEMPORAL = "temporal"  # Time-based conflict (past fact contradicted by newer evidence)
    TRUST_CONFLICT = "trust_conflict"  # Same fact, different trust scores (needs resolution)
    SEMANTIC_DRIFT = "semantic_drift"  # Subtle meaning change over amendments


class ContradictionSeverity(Enum):
    """Severity levels guide Judicial action."""

    LOW = "low"  # Minor inconsistency, needs documentation
    MEDIUM = "medium"  # Clear conflict, review recommended
    HIGH = "high"  # Critical contradiction, must be resolved


@dataclass
class ContradictionFlag:
    """A flagged contradiction needing Judicial review."""

    memory_id: str
    conflicting_memory_id: str
    contradiction_type: ContradictionType
    severity: ContradictionSeverity
    reason: str
    confidence: float = 0.5  # 0.0-1.0, higher = more confident in the flag


class ContradictionDetector:
    """Detect contradictions between memories."""

    # Keyword patterns for contradiction detection
    AFFINITY_PATTERNS = {
        "prefer": r"\b(prefer|like|love|enjoy|want)\s+(.*?)(?:\s+over|\s+more|\.|\,|$)",
        "dislike": r"\b(hate|dislike|don't?\s+like|avoid|detest)\s+(.*?)(?:\.|,|$)",
        "identity": r"\b(am|'m|is)\s+(?:a\s+)?(\w+)(?:\s+person)?(?:\s+.*)?(?:\.|,|$)",
        "ability": r"\b(can|cannot|can't|able to|unable to)\s+(.*?)(?:\.|,|$)",
    }

    @staticmethod
    def extract_affinity(text: str) -> dict:
        """
        Extract preference/identity statements from text.

        Returns dict of {pattern_type: [matches]}
        """
        affinity = {}
        for pattern_type, pattern in ContradictionDetector.AFFINITY_PATTERNS.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                affinity[pattern_type] = matches
        return affinity

    @staticmethod
    def are_direct_contradictions(
        affinity1: dict,
        affinity2: dict,
    ) -> tuple[bool, Optional[str]]:
        """
        Detect direct contradictions between two affinity sets.

        Examples:
          - "I prefer coffee" vs "I prefer tea"
          - "I like dogs" vs "I hate dogs"

        Args:
            affinity1: Affinity dict from first memory
            affinity2: Affinity dict from second memory

        Returns:
            Tuple of (is_contradiction: bool, reason: Optional[str])
        """
        # Check if one memory says "prefer X" and other says "dislike X"
        prefer1 = affinity1.get("prefer", [])
        dislike1 = affinity1.get("dislike", [])
        prefer2 = affinity2.get("prefer", [])
        dislike2 = affinity2.get("dislike", [])

        # Extract nouns from the preference clauses
        prefer_nouns_1 = set()
        for match_tuple in prefer1:
            if isinstance(match_tuple, tuple) and len(match_tuple) > 1:
                prefer_nouns_1.add(match_tuple[1].lower())
            else:
                prefer_nouns_1.add(str(match_tuple).lower())

        dislike_nouns_1 = set()
        for match_tuple in dislike1:
            if isinstance(match_tuple, tuple) and len(match_tuple) > 1:
                dislike_nouns_1.add(match_tuple[1].lower())
            else:
                dislike_nouns_1.add(str(match_tuple).lower())

        prefer_nouns_2 = set()
        for match_tuple in prefer2:
            if isinstance(match_tuple, tuple) and len(match_tuple) > 1:
                prefer_nouns_2.add(match_tuple[1].lower())
            else:
                prefer_nouns_2.add(str(match_tuple).lower())

        dislike_nouns_2 = set()
        for match_tuple in dislike2:
            if isinstance(match_tuple, tuple) and len(match_tuple) > 1:
                dislike_nouns_2.add(match_tuple[1].lower())
            else:
                dislike_nouns_2.add(str(match_tuple).lower())

        # Check for contradictions
        for noun in prefer_nouns_1:
            if noun in dislike_nouns_2:
                return True, f"Prefer {noun} vs dislike {noun}"

        for noun in dislike_nouns_1:
            if noun in prefer_nouns_2:
                return True, f"Dislike {noun} vs prefer {noun}"

        return False, None

    @staticmethod
    def detect_temporal_contradiction(
        old_memory: dict,
        new_memory: dict,
    ) -> tuple[bool, Optional[str]]:
        """
        Detect temporal contradictions (old fact contradicted by new evidence).

        Args:
            old_memory: Earlier memory record
            new_memory: Later memory record

        Returns:
            Tuple of (is_contradiction: bool, reason: Optional[str])
        """
        old_content = old_memory.get("content", "")
        new_content = new_memory.get("content", "")

        old_affinity = ContradictionDetector.extract_affinity(old_content)
        new_affinity = ContradictionDetector.extract_affinity(new_content)

        return ContradictionDetector.are_direct_contradictions(old_affinity, new_affinity)

    @staticmethod
    def detect_semantic_similarity(
        text1: str,
        text2: str,
        threshold: float = 0.7,
    ) -> float:
        """
        Compute simple semantic similarity via word overlap (0.0-1.0).

        For production, this would use embedding similarity. For v1, word overlap
        provides a quick heuristic.

        Args:
            text1: First text
            text2: Second text
            threshold: Similarity above this triggers closer inspection

        Returns:
            Similarity score (0.0-1.0)
        """
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0

    @staticmethod
    def detect_trust_conflict(
        old_trust: float,
        new_trust: float,
        trust_delta_threshold: float = 0.4,
    ) -> tuple[bool, Optional[str]]:
        """
        Detect trust conflicts (same fact, vastly different trust scores).

        Example: User-stated (1.0) vs agent-inferred (0.3) for the same fact.

        Args:
            old_trust: Old memory's trust score
            new_trust: New memory's trust score
            trust_delta_threshold: Difference to trigger conflict flag

        Returns:
            Tuple of (is_conflict: bool, reason: Optional[str])
        """
        delta = abs(old_trust - new_trust)
        if delta >= trust_delta_threshold:
            return True, f"Trust delta {delta:.2f} (old: {old_trust}, new: {new_trust})"

        return False, None


class ContradictionClassifier:
    """Classify contradictions and assign severity."""

    @staticmethod
    def classify(
        old_memory: dict,
        new_memory: dict,
        similarity_score: float = 0.0,
    ) -> list[ContradictionFlag]:
        """
        Classify contradictions between two memories.

        Args:
            old_memory: Earlier memory record
            new_memory: Later memory record
            similarity_score: Pre-computed semantic similarity (optional)

        Returns:
            List of ContradictionFlag objects
        """
        flags = []

        old_content = old_memory.get("content", "")
        new_content = new_memory.get("content", "")
        old_trust = old_memory.get("trust_score", 0.5)
        new_trust = new_memory.get("trust_score", 0.5)
        old_id = old_memory.get("id")
        new_id = new_memory.get("id")

        # Detect direct contradictions
        is_direct, reason = ContradictionDetector.detect_temporal_contradiction(
            old_memory,
            new_memory,
        )
        if is_direct:
            severity = (
                ContradictionSeverity.HIGH
                if old_trust > 0.8 and new_trust > 0.8
                else ContradictionSeverity.MEDIUM
            )
            flags.append(
                ContradictionFlag(
                    memory_id=old_id,
                    conflicting_memory_id=new_id,
                    contradiction_type=ContradictionType.DIRECT,
                    severity=severity,
                    reason=reason or "Direct contradiction detected",
                    confidence=0.95,
                )
            )

        # Detect trust conflicts if content is similar enough
        if similarity_score == 0.0:
            similarity_score = ContradictionDetector.detect_semantic_similarity(
                old_content,
                new_content,
            )

        if similarity_score > 0.7:
            # Similar content but possibly different trustworthiness
            is_trust_conflict, reason = ContradictionDetector.detect_trust_conflict(
                old_trust,
                new_trust,
            )
            if is_trust_conflict:
                severity = (
                    ContradictionSeverity.HIGH
                    if max(old_trust, new_trust) > 0.8
                    else ContradictionSeverity.MEDIUM
                )
                flags.append(
                    ContradictionFlag(
                        memory_id=old_id,
                        conflicting_memory_id=new_id,
                        contradiction_type=ContradictionType.TRUST_CONFLICT,
                        severity=severity,
                        reason=reason or "Trust score conflict",
                        confidence=0.8,
                    )
                )

        # Temporal contradiction (if later memory contradicts earlier)
        # Already covered by direct contradiction detection above

        return flags

    @staticmethod
    def filter_by_severity(
        flags: list[ContradictionFlag],
        min_severity: ContradictionSeverity = ContradictionSeverity.MEDIUM,
    ) -> list[ContradictionFlag]:
        """
        Filter flags by minimum severity.

        Args:
            flags: List of flags
            min_severity: Include flags with severity >= this level

        Returns:
            Filtered list
        """
        severity_order = {
            ContradictionSeverity.LOW: 0,
            ContradictionSeverity.MEDIUM: 1,
            ContradictionSeverity.HIGH: 2,
        }
        min_level = severity_order[min_severity]
        return [f for f in flags if severity_order[f.severity] >= min_level]
