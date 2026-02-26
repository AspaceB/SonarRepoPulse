import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SonarProject:
    """A single SonarQube project."""
    key: str
    name: str
    qualifier: str = "TRK"


@dataclass
class MatchResult:
    """Result of matching a repo slug to a SonarQube project."""
    sonar_key: str
    sonar_name: str
    strategy: str
    confidence: float


class SonarProjectIndex:
    """Pre-built index of all SonarQube projects for fast, accurate matching.

    Built once per scan by fetching all projects from SonarQube.
    Provides multi-strategy matching ranked by confidence.
    """

    PREFIX_PATTERN = re.compile(
        r'^(?:com\.|org\.|net\.|io\.)?'
        r'(?:[a-z0-9_-]+\.)*'
    )

    def __init__(self):
        self._projects: list[SonarProject] = []
        self._by_key_lower: dict[str, SonarProject] = {}
        self._by_name_lower: dict[str, SonarProject] = {}
        self._by_key_suffix: dict[str, list[SonarProject]] = {}
        self._by_normalized: dict[str, list[SonarProject]] = {}
        self._by_collapsed: dict[str, list[SonarProject]] = {}
        self._built = False

    @property
    def project_count(self) -> int:
        return len(self._projects)

    async def build(self, sonar_client) -> "SonarProjectIndex":
        """Fetch all SonarQube projects and build lookup indexes."""
        logger.info("Building SonarQube project index...")
        self._projects = await sonar_client.list_all_projects()
        self._build_indexes()
        self._built = True
        logger.info(f"SonarQube project index built: {len(self._projects)} projects indexed")
        return self

    def _build_indexes(self) -> None:
        for proj in self._projects:
            key_lower = proj.key.lower()
            name_lower = proj.name.lower()

            self._by_key_lower[key_lower] = proj
            self._by_name_lower[name_lower] = proj

            suffix = self._extract_key_suffix(key_lower)
            self._by_key_suffix.setdefault(suffix, []).append(proj)

            normalized = self._normalize(key_lower)
            self._by_normalized.setdefault(normalized, []).append(proj)

            norm_name = self._normalize(name_lower)
            if norm_name != normalized:
                self._by_normalized.setdefault(norm_name, []).append(proj)

            # Collapsed index: remove ALL separators for fuzzy comparison
            # This catches cases like "abc-service" vs "a-bc-service"
            collapsed_key = self._collapse(key_lower)
            self._by_collapsed.setdefault(collapsed_key, []).append(proj)

            collapsed_name = self._collapse(name_lower)
            if collapsed_name != collapsed_key:
                self._by_collapsed.setdefault(collapsed_name, []).append(proj)

    def find_match(self, repo_slug: str) -> Optional[MatchResult]:
        """Find the best SonarQube project match for a repository slug.

        Strategies (in confidence order):
        1. Exact key match           (1.0)
        2. Exact name match          (0.95)
        3. Key suffix match          (0.85)
        4. Normalized match          (0.75)
        5. Token overlap (Jaccard)   (0.5-0.7, threshold 0.6)
        """
        if not self._built:
            raise RuntimeError("Index not built. Call build() first.")

        slug_lower = repo_slug.lower()

        # Strategy 1: Exact key match
        if slug_lower in self._by_key_lower:
            proj = self._by_key_lower[slug_lower]
            return MatchResult(proj.key, proj.name, "exact_key", 1.0)

        # Strategy 2: Exact name match
        if slug_lower in self._by_name_lower:
            proj = self._by_name_lower[slug_lower]
            return MatchResult(proj.key, proj.name, "exact_name", 0.95)

        # Strategy 3: Key suffix match (unique only)
        suffix_candidates = self._by_key_suffix.get(slug_lower, [])
        if len(suffix_candidates) == 1:
            proj = suffix_candidates[0]
            return MatchResult(proj.key, proj.name, "key_suffix", 0.85)

        # Strategy 4: Normalized match
        slug_normalized = self._normalize(slug_lower)
        norm_candidates = self._by_normalized.get(slug_normalized, [])
        if len(norm_candidates) == 1:
            proj = norm_candidates[0]
            return MatchResult(proj.key, proj.name, "normalized", 0.75)
        elif len(norm_candidates) > 1:
            for proj in norm_candidates:
                if self._extract_key_suffix(proj.key.lower()) == slug_lower:
                    return MatchResult(proj.key, proj.name, "normalized_disambig", 0.80)

        # Strategy 5: Collapsed match (all separators removed)
        # Catches: "abc-service" vs "a-bc-service" (separator differences)
        slug_collapsed = self._collapse(slug_lower)
        collapsed_candidates = self._by_collapsed.get(slug_collapsed, [])
        if len(collapsed_candidates) == 1:
            proj = collapsed_candidates[0]
            return MatchResult(proj.key, proj.name, "collapsed", 0.70)
        elif len(collapsed_candidates) > 1:
            for proj in collapsed_candidates:
                if self._collapse(self._extract_key_suffix(proj.key.lower())) == slug_collapsed:
                    return MatchResult(proj.key, proj.name, "collapsed_disambig", 0.72)

        # Strategy 6: Token overlap scoring
        best = self._token_overlap_match(slug_lower)
        if best:
            return best

        return None

    def _token_overlap_match(
        self, slug_lower: str, threshold: float = 0.6
    ) -> Optional[MatchResult]:
        slug_tokens = self._tokenize(slug_lower)
        if not slug_tokens:
            return None

        best_score = 0.0
        best_proj = None

        for proj in self._projects:
            key_score = self._jaccard(slug_tokens, self._tokenize(proj.key.lower()))
            name_score = self._jaccard(slug_tokens, self._tokenize(proj.name.lower()))
            score = max(key_score, name_score)
            if score > best_score:
                best_score = score
                best_proj = proj

        if best_proj and best_score >= threshold:
            confidence = 0.5 + (best_score - threshold) / (1.0 - threshold) * 0.2
            return MatchResult(
                best_proj.key, best_proj.name,
                f"token_overlap({best_score:.2f})",
                round(confidence, 2),
            )
        return None

    @classmethod
    def _normalize(cls, text: str) -> str:
        """Strip domain prefixes, unify separators."""
        text = text.lower()
        text = cls.PREFIX_PATTERN.sub('', text)
        text = re.sub(r'[-_.\s]+', '-', text)
        return text.strip('-')

    @classmethod
    def _collapse(cls, text: str) -> str:
        """Strip prefixes and remove ALL separators for loose comparison."""
        text = text.lower()
        text = cls.PREFIX_PATTERN.sub('', text)
        return re.sub(r'[-_.\s]+', '', text)

    @staticmethod
    def _extract_key_suffix(key: str) -> str:
        """Last segment after the last dot (Maven-style keys)."""
        parts = key.split('.')
        return parts[-1] if parts else key

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        tokens = set(re.split(r'[-_.\s]+', text.lower()))
        noise = {'com', 'org', 'net', 'io', ''}
        return {t for t in tokens if len(t) > 1 and t not in noise}

    @staticmethod
    def _jaccard(set_a: set[str], set_b: set[str]) -> float:
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)
