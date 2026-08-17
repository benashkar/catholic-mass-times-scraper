"""Base class for scoring engines."""

from dataclasses import dataclass, field


@dataclass
class EngineResult:
    """Result from a single scoring engine."""
    engine_name: str
    score: float  # 0.0 to 1.0
    is_name: bool  # Engine's binary verdict
    details: dict = field(default_factory=dict)  # Engine-specific metadata

    @property
    def label(self):
        if self.score >= 0.7:
            return "high"
        elif self.score >= 0.4:
            return "medium"
        return "low"


class BaseEngine:
    """Interface for scoring engines."""

    name: str = "base"

    def score(self, name, context=""):
        """Score a name candidate.

        Args:
            name: The extracted name string (e.g., "John Smith")
            context: Surrounding text for context-aware engines

        Returns:
            EngineResult with score and metadata.
        """
        raise NotImplementedError

    def score_batch(self, names, contexts=None):
        """Score multiple names. Override for batch-optimized engines.

        Args:
            names: List of name strings
            contexts: Optional list of context strings (same length as names)

        Returns:
            List of EngineResult
        """
        contexts = contexts or [""] * len(names)
        return [self.score(n, c) for n, c in zip(names, contexts)]
