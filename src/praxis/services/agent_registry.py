"""Agent registry — loads and indexes agency-agents personas.

Each agent is a markdown file with YAML frontmatter (name, description,
emoji, vibe) and a detailed system prompt body.  The registry parses
these into AgentPersona objects and indexes them by name, division,
and keyword for fast lookup.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Default library path (relative to the praxis package)
DEFAULT_LIBRARY_PATH = Path(__file__).parent.parent / "agents_library"

# Common regions/countries that appears in agent names/descriptions
KNOWN_REGIONS = {
    "global", "international", "us", "usa", "united states", "canada",
    "uk", "united kingdom", "britain", "british", "europe", "european",
    "france", "french", "germany", "german", "spain", "spanish",
    "italy", "italian", "netherlands", "dutch", "switzerland", "swiss",
    "australia", "australian", "new zealand", "nz",
    "china", "chinese", "baidu", "bilibili", "douyin", "kuaishou",
    "wechat", "weibo", "xiaohongshu", "zhihu",
    "korea", "korean",
    "japan", "japanese",
    "india", "indian",
    "singapore", "hong kong",
    "uae", "dubai", "middle east",
}

# Mapping of region signals to canonical region names
REGION_CANONICAL = {
    "us": "United States", "usa": "United States", "united states": "United States",
    "uk": "United Kingdom", "united kingdom": "United Kingdom", "britain": "United Kingdom", "british": "United Kingdom",
    "europe": "Europe", "european": "Europe",
    "france": "France", "french": "France",
    "germany": "Germany", "german": "Germany",
    "spain": "Spain", "spanish": "Spain",
    "italy": "Italy", "italian": "Italy",
    "netherlands": "Netherlands", "dutch": "Netherlands",
    "switzerland": "Switzerland", "swiss": "Switzerland",
    "australia": "Australia", "australian": "Australia",
    "new zealand": "New Zealand", "nz": "New Zealand",
    "china": "China", "chinese": "China", "baidu": "China", "bilibili": "China",
    "douyin": "China", "kuaishou": "China", "wechat": "China", "weibo": "China",
    "xiaohongshu": "China", "zhihu": "China",
    "korea": "South Korea", "korean": "South Korea",
    "japan": "Japan", "japanese": "Japan",
    "india": "India", "indian": "India",
    "singapore": "Singapore", "hong kong": "Hong Kong",
    "uae": "UAE", "dubai": "UAE", "middle east": "Middle East",
    "canada": "Canada",
    "global": "Global", "international": "Global",
}


def detect_region(text: str) -> str:
    """Detect region hints from an agent name/description.

    Returns the canonical region name or empty string if no strong signal.
    """
    text_lower = text.lower()
    # Split on common separators and punctuation
    tokens = re.split(r"[\s\-_]", text_lower)
    found = set()
    for token in tokens:
        if token in KNOWN_REGIONS:
            found.add(REGION_CANONICAL.get(token, ""))
    # Also check multi-word phrases like "New Zealand", "Hong Kong", "United States"
    for phrase, canonical in REGION_CANONICAL.items():
        if " " in phrase and phrase in text_lower:
            found.add(canonical)
    # Remove empty strings
    found.discard("")
    if len(found) == 1:
        return found.pop()
    if len(found) > 1:
        # Multi-region -> global/unspecified
        return "Global"
    return ""


@dataclass
class AgentPersona:
    """A single agency-agent persona loaded from a markdown file."""

    name: str  # e.g. "Financial Analyst"
    slug: str  # e.g. "finance-financial-analyst"
    division: str  # e.g. "finance"
    description: str = ""
    emoji: str = ""
    vibe: str = ""
    color: str = ""
    system_prompt: str = ""  # Full markdown body (the system prompt)
    file_path: str = ""
    keywords: list[str] = field(default_factory=list)
    region: str = ""  # Region detected from name/description, e.g. "China", "France"

    def summary(self) -> str:
        """One-line summary for listing."""
        region_tag = f" [{self.region}]" if self.region else ""
        return f"{self.emoji} {self.name} ({self.division}){region_tag}: {self.description[:80]}"


class AgentRegistry:
    """Loads and indexes all agency-agent personas.

    Usage:
        registry = AgentRegistry()
        registry.load()
        agent = registry.get("finance-financial-analyst")
        agents = registry.search(keyword="invoice")
        agents = registry.list_division("finance")
    """

    def __init__(self, library_path: str | Path | None = None) -> None:
        self.library_path = Path(library_path) if library_path else DEFAULT_LIBRARY_PATH
        self._agents: dict[str, AgentPersona] = {}  # slug -> persona
        self._divisions: dict[str, list[str]] = {}  # division -> [slugs]
        self._loaded = False

    @property
    def count(self) -> int:
        return len(self._agents)

    @property
    def divisions(self) -> list[str]:
        return sorted(self._divisions.keys())

    def load(self) -> int:
        """Load all agent files from the library path.

        Returns the number of agents loaded.
        """
        if not self.library_path.exists():
            logger.warning("agents.library_notFound path=%s", self.library_path)
            return 0

        count = 0
        for md_file in sorted(self.library_path.rglob("*.md")):
            if md_file.name in ("README.md", "CONTRIBUTING.md", "SECURITY.md"):
                continue

            persona = self._parse_agent_file(md_file)
            if persona:
                self._agents[persona.slug] = persona
                self._divisions.setdefault(persona.division, []).append(persona.slug)
                count += 1

        self._loaded = True
        logger.info("agents.loaded count=%d divisions=%d", count, len(self._divisions))
        return count

    def _parse_agent_file(self, path: Path) -> AgentPersona | None:
        """Parse a single agent markdown file into an AgentPersona."""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("agents.parse_failed path=%s error=%s", path, exc)
            return None

        # Parse YAML frontmatter
        frontmatter = {}
        body = text

        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                    body = parts[2].strip()
                except yaml.YAMLError:
                    body = text

        # Extract fields
        name = frontmatter.get("name", path.stem)
        description = frontmatter.get("description", "")
        emoji = frontmatter.get("emoji", "")
        vibe = frontmatter.get("vibe", "")
        color = frontmatter.get("color", "")

        # Determine division from path
        division = path.parent.name

        # Generate slug: use the file stem (filename without extension)
        # Most files already include the division prefix (e.g. "finance-financial-analyst.md")
        slug = path.stem

        # Extract keywords from description and body
        keywords = self._extract_keywords(description + " " + body)

        # Detect region from name and description
        region = detect_region(name + " " + description)

        return AgentPersona(
            name=name,
            slug=slug,
            division=division,
            description=description,
            emoji=emoji,
            vibe=vibe,
            color=color,
            system_prompt=body,
            file_path=str(path),
            keywords=keywords,
            region=region,
        )

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from text for search indexing."""
        # Remove markdown syntax
        clean = re.sub(r"[#*`_\[\]()-]", " ", text.lower())
        words = clean.split()
        # Filter short words and common stop words
        stop = {
            "the", "a", "an", "to", "of", "and", "or", "in", "for", "with",
            "is", "are", "you", "your", "that", "this", "it", "as", "by",
            "on", "at", "from", "be", "will", "can", "not", "but", "they",
            "have", "has", "was", "were", "been", "their", "them", "these",
            "those", "its", "our", "we", "i", "me", "my", "he", "she",
        }
        keywords = [w for w in words if len(w) > 3 and w not in stop]
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for w in keywords:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        return unique[:30]  # Limit to top 30 keywords

    def get(self, slug: str) -> AgentPersona | None:
        """Get an agent by slug (e.g. 'finance-financial-analyst')."""
        if not self._loaded:
            self.load()
        return self._agents.get(slug)

    def list_division(self, division: str) -> list[AgentPersona]:
        """List all agents in a division."""
        if not self._loaded:
            self.load()
        slugs = self._divisions.get(division, [])
        return [self._agents[s] for s in slugs]

    def search(
        self,
        keyword: str,
        region: str | None = None,
        limit: int = 10,
    ) -> list[AgentPersona]:
        """Search agents by keyword and optional region.

        Region-specific agents matching the requested region are boosted.
        """
        if not self._loaded:
            self.load()

        keyword_lower = keyword.lower()
        requested_region = region.strip().lower() if region else ""
        scored: list[tuple[int, AgentPersona]] = []

        for agent in self._agents.values():
            score = 0
            # Name match (highest weight)
            if keyword_lower in agent.name.lower():
                score += 10
            # Description match
            if keyword_lower in agent.description.lower():
                score += 5
            # Keyword match
            if keyword_lower in agent.keywords:
                score += 3
            # Division match
            if keyword_lower in agent.division:
                score += 2

            # Region boosting
            if requested_region:
                agent_region = agent.region.lower()
                if requested_region == agent_region:
                    score += 8  # Strong boost for exact region match
                elif not agent_region:
                    score += 1  # Slight boost for global agents
                elif agent_region == "global":
                    score += 2  # Global agents are generally applicable

            if score > 0:
                scored.append((score, agent))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [a for _, a in scored[:limit]]

    def for_region(self, region: str, limit: int = 50) -> list[AgentPersona]:
        """Return agents that are either region-specific for the region or region-agnostic."""
        if not self._loaded:
            self.load()

        requested = region.strip().lower()
        result = []
        for agent in self._agents.values():
            agent_region = agent.region.lower()
            # Include if exact region match or region-agnostic
            if agent_region in (requested, "", "global"):
                result.append(agent)

        return sorted(result, key=lambda a: (a.region.lower() != requested, a.name))[:limit]

    def list_all(self) -> list[AgentPersona]:
        """List all agents."""
        if not self._loaded:
            self.load()
        return list(self._agents.values())

    def divisions_summary(self) -> list[dict[str, Any]]:
        """Get a summary of all divisions with agent counts."""
        if not self._loaded:
            self.load()
        return [
            {"division": div, "count": len(slugs)}
            for div, slugs in sorted(self._divisions.items())
        ]
