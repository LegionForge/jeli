"""Entity graph: named-entity extraction + a lightweight personal knowledge graph.

`EntityExtractor` pulls named entities out of memory text with fast, sync,
rule-based heuristics (no LLM, no I/O) so it can run on every capture.
`GraphStore` persists entities, memory↔entity links, and entity↔entity
relations, and answers entity-scoped queries.
"""

from .extractor import EntityExtractor
from .store import GraphStore

__all__ = ["EntityExtractor", "GraphStore"]
