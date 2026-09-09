"""Core cryptographic and integrity components."""

from .contradiction import (
    ContradictionClassifier,
    ContradictionDetector,
    ContradictionFlag,
    ContradictionSeverity,
    ContradictionType,
)
from .hash_chain import (
    AmendmentTracker,
    HashChainValidator,
    build_canonical_record,
    build_canonical_record_v2,
    canonical_json,
    compute_record_hash,
)
from .trust_score import (
    TrustAdjustment,
    TrustScorer,
    TrustSource,
)

__all__ = [
    "compute_record_hash",
    "canonical_json",
    "build_canonical_record",
    "build_canonical_record_v2",
    "HashChainValidator",
    "AmendmentTracker",
    "TrustSource",
    "TrustScorer",
    "TrustAdjustment",
    "ContradictionType",
    "ContradictionSeverity",
    "ContradictionFlag",
    "ContradictionDetector",
    "ContradictionClassifier",
]
