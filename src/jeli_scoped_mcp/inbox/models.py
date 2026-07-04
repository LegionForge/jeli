"""Inbox dataclasses and enums — no DB logic."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class InboxStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    APPROVED = "approved"
    MERGED = "merged"
    HELD = "held"
    REJECTED = "rejected"


class Durability(str, Enum):
    TRANSIENT = "transient"
    SESSION = "session"
    DURABLE = "durable"
    PERMANENT = "permanent"


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Encoding(str, Enum):
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
    suggested_type: Optional[str]
    suggested_trust: float
    keywords: list[str] = field(default_factory=list)
    entities: dict = field(default_factory=dict)
    requires_review: bool = False
    review_reason: Optional[str] = None
    near_duplicate_of: Optional[str] = None
    duplicate_distance: Optional[float] = None
    merge_strategy: Optional[str] = None
    rejection_reason: Optional[str] = None
    enrichment_log: dict = field(default_factory=dict)
