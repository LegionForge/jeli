"""Rule-based named-entity extraction for the capture path.

Deliberately sync and I/O-free: this runs on every memory write, so it must be
fast and never block. v1 uses regex + capitalization heuristics plus a
configurable keyword gazetteer — no LLM. Precision is favored over recall; a
missed entity is cheap, a wrong one pollutes the graph.
"""

import re

# Default gazetteer: canonical name -> entity_type. Case-insensitive match on
# word boundaries. Extend via EntityExtractor(extra_keywords=...).
DEFAULT_KEYWORDS: dict[str, str] = {
    "PostgreSQL": "technology",
    "Postgres": "technology",
    "pgvector": "technology",
    "Python": "technology",
    "Docker": "technology",
    "Redis": "technology",
    "SQLite": "technology",
    "DuckDB": "technology",
    "Ollama": "technology",
    "FastAPI": "technology",
    "Jeli": "project",
    "OB1": "project",
    "Hermes": "project",
    "NetSuite": "organization",
    "Anthropic": "organization",
    "LegionForge": "organization",
}

# One or more capitalized words; the first token may be all-caps (initials like
# "JP"), the rest are Capitalized. Matches "JP Cruz", "Nate B Jones".
_PERSON_RE = re.compile(r"\b[A-Z][A-Za-z.]*(?:\s+[A-Z][a-z]+){1,2}\b")
_URL_RE = re.compile(r"https?://([^/\s:]+)")
_EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")


class EntityExtractor:
    """Extract named entities from memory text (sync, no I/O)."""

    def __init__(self, extra_keywords: dict[str, str] | None = None):
        self.keywords = dict(DEFAULT_KEYWORDS)
        if extra_keywords:
            self.keywords.update(extra_keywords)

    def extract(self, content: str) -> list[dict]:
        """Return [{"name", "entity_type", "confidence"}], deduped by (name,type)."""
        if not content or not content.strip():
            return []

        seen: set[tuple[str, str]] = set()
        out: list[dict] = []

        def add(name: str, entity_type: str, confidence: float) -> None:
            name = name.strip()
            if not name:
                return
            key = (name.lower(), entity_type)
            if key in seen:
                return
            seen.add(key)
            out.append(
                {"name": name, "entity_type": entity_type, "confidence": confidence}
            )

        # Gazetteer first — highest precision, and claims tokens that the person
        # heuristic would otherwise misread (e.g. "PostgreSQL").
        keyword_names: set[str] = set()
        for canonical, etype in self.keywords.items():
            if re.search(rf"\b{re.escape(canonical)}\b", content, re.IGNORECASE):
                add(canonical, etype, 0.9)
                keyword_names.add(canonical.lower())

        # URLs → organization (registered hostname, sans leading www.).
        for host in _URL_RE.findall(content):
            add(_clean_host(host), "organization", 0.8)

        # Email domains → organization.
        for domain in _EMAIL_RE.findall(content):
            add(_clean_host(domain), "organization", 0.7)

        # Person names — skip anything already claimed by the gazetteer.
        for match in _PERSON_RE.findall(content):
            if match.lower() in keyword_names:
                continue
            if any(tok.lower() in keyword_names for tok in match.split()):
                continue
            add(match, "person", 0.7)

        return out

    def extract_relations(
        self, entities: list[dict]
    ) -> list[tuple[str, str, str, str]]:
        """Infer relations between co-occurring entities.

        Returns (subject_name, predicate, object_name, subject_type) tuples.
        Rules are conservative co-occurrence heuristics (no LLM, stays sync):
        only high-confidence type pairings emit an edge, so the graph isn't
        poisoned with noise. Capped at 20 to avoid combinatorial blow-up on
        entity-dense content.
        """
        by_type: dict[str, list[str]] = {}
        for e in entities:
            by_type.setdefault(e["entity_type"], []).append(e["name"])

        persons = by_type.get("person", [])
        projects = by_type.get("project", [])
        orgs = by_type.get("organization", [])
        techs = by_type.get("technology", [])

        relations: list[tuple[str, str, str, str]] = []
        for person in persons:
            for project in projects:
                relations.append((person, "works_on", project, "person"))
        for person in persons:
            for org in orgs:
                relations.append((person, "member_of", org, "person"))
        for project in projects:
            for tech in techs:
                relations.append((project, "uses", tech, "project"))
        for org in orgs:
            for project in projects:
                relations.append((org, "develops", project, "organization"))

        return relations[:20]


def _clean_host(host: str) -> str:
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host
