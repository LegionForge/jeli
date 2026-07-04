"""Inbox dataclasses and enums — no DB logic."""

from dataclasses import dataclass, field
from enum import StrEnum


class InboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    APPROVED = "approved"
    MERGED = "merged"
    HELD = "held"
    REJECTED = "rejected"


class Durability(StrEnum):
    TRANSIENT = "transient"
    SESSION = "session"
    DURABLE = "durable"
    PERMANENT = "permanent"


class Urgency(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Encoding(StrEnum):
    RAW = "raw"
    SUMMARY = "summary"
    KEYWORDS = "keywords"
    HYBRID = "hybrid"


@dataclass
class ClassifierDecision:
    """Output of IngestionClassifier for one inbox item."""

    status: InboxStatus
    importance: float
    urgency: Urgency
    durability: Durability
    encoding: Encoding
    suggested_type: str | None
    suggested_trust: float
    keywords: list[str] = field(default_factory=list)
    entities: dict = field(default_factory=dict)
    requires_review: bool = False
    review_reason: str | None = None
    near_duplicate_of: str | None = None
    duplicate_distance: float | None = None
    merge_strategy: str | None = None
    rejection_reason: str | None = None
    enrichment_log: dict = field(default_factory=dict)
    content_class: str = "general"
