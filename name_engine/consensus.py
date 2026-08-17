"""
Consensus Scorer — Combines multiple engine results into a final verdict.

The key insight: when engines disagree, the name is suspect. When all engines
agree, we have high confidence in the verdict (whether it's a name or junk).
"""

from dataclasses import dataclass, field

from name_engine.engines.base import EngineResult


@dataclass
class ConsensusResult:
    """Final verdict from combining all engines."""
    name: str
    confidence: str  # "high", "medium", "low"
    final_score: float  # 0.0 to 1.0
    is_suspect: bool  # True if engines significantly disagree
    engine_results: dict = field(default_factory=dict)  # engine_name -> EngineResult
    reason: str = ""

    def to_dict(self):
        return {
            "name": self.name,
            "confidence": self.confidence,
            "final_score": round(self.final_score, 3),
            "is_suspect": self.is_suspect,
            "reason": self.reason,
            "engines": {
                name: {"score": round(r.score, 3), "is_name": r.is_name, "label": r.label}
                for name, r in self.engine_results.items()
            },
        }


class ConsensusScorer:
    """Combine engine results using agreement-based scoring.

    Engines that agree reinforce each other. Engines that disagree
    flag the name as suspect for human review.
    """

    def __init__(self, engines=None, weights=None):
        """
        Args:
            engines: List of engine instances. If None, uses all three defaults.
            weights: Dict of engine_name -> weight (0.0-1.0). Defaults to equal.
        """
        if engines is None:
            from name_engine.engines import (
                DictionaryEngine,
                NameDatasetEngine,
                SpacyNEREngine,
            )
            engines = [DictionaryEngine(), SpacyNEREngine(), NameDatasetEngine()]

        self.engines = engines
        self.weights = weights or {e.name: 1.0 for e in engines}

    def score(self, name, context=""):
        """Score a single name using all engines and consensus logic.

        Args:
            name: The name string to score
            context: Surrounding text (used by context-aware engines like spaCy)

        Returns:
            ConsensusResult with final verdict
        """
        results = {}
        for engine in self.engines:
            result = engine.score(name, context)
            results[engine.name] = result

        return self._compute_consensus(name, results)

    def score_batch(self, names, contexts=None):
        """Score multiple names using batch-optimized engines.

        Args:
            names: List of name strings
            contexts: Optional list of context strings

        Returns:
            List of ConsensusResult
        """
        contexts = contexts or [""] * len(names)

        # Collect per-engine results
        all_results = {}
        for engine in self.engines:
            engine_results = engine.score_batch(names, contexts)
            all_results[engine.name] = engine_results

        # Compute consensus for each name
        consensus_results = []
        for i, name in enumerate(names):
            engine_results_for_name = {
                engine_name: results[i]
                for engine_name, results in all_results.items()
            }
            consensus = self._compute_consensus(name, engine_results_for_name)
            consensus_results.append(consensus)

        return consensus_results

    def _compute_consensus(self, name, engine_results):
        """Compute final consensus from engine results.

        Rules:
        - Weighted average of engine scores
        - If engines disagree (spread > 0.4), flag as suspect
        - All high = high confidence
        - All low = low confidence
        - Mixed = medium, suspect for review
        """
        scores = []
        total_weight = 0

        for engine_name, result in engine_results.items():
            weight = self.weights.get(engine_name, 1.0)
            scores.append((result.score, weight, result.is_name))
            total_weight += weight

        if total_weight == 0:
            return ConsensusResult(
                name=name, confidence="low", final_score=0.0,
                is_suspect=True, engine_results=engine_results,
                reason="no_engines",
            )

        # Weighted average score
        weighted_sum = sum(s * w for s, w, _ in scores)
        final_score = weighted_sum / total_weight

        # Check agreement
        verdicts = [is_name for _, _, is_name in scores]
        all_agree_name = all(verdicts)
        all_agree_not_name = not any(verdicts)
        engines_disagree = not all_agree_name and not all_agree_not_name

        # Score spread (max - min)
        raw_scores = [s for s, _, _ in scores]
        spread = max(raw_scores) - min(raw_scores) if raw_scores else 0

        # Determine confidence and suspect status
        is_suspect = engines_disagree and spread > 0.3

        if final_score >= 0.7 and all_agree_name:
            confidence = "high"
            reason = "all_engines_agree_name"
        elif final_score < 0.3 and all_agree_not_name:
            confidence = "low"
            reason = "all_engines_agree_not_name"
        elif final_score >= 0.5:
            confidence = "medium" if not is_suspect else "medium"
            reason = "engines_disagree" if engines_disagree else "moderate_score"
        else:
            confidence = "low"
            reason = "low_consensus_score"

        return ConsensusResult(
            name=name,
            confidence=confidence,
            final_score=final_score,
            is_suspect=is_suspect,
            engine_results=engine_results,
            reason=reason,
        )
