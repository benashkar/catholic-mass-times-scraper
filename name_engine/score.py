"""
Batch scoring API — score a list of names without PDF extraction.

Useful for re-scoring existing data in a database.
"""

from name_engine.consensus import ConsensusScorer


def score_names(names, contexts=None, scorer=None):
    """Score a list of name strings using multi-engine consensus.

    Args:
        names: List of name strings (e.g., ["John Smith", "Forever Young"])
        contexts: Optional list of context strings for each name
        scorer: Optional ConsensusScorer instance. If None, creates default.

    Returns:
        List of dicts with scoring results:
        [
            {
                "name": "John Smith",
                "confidence": "high",
                "final_score": 0.85,
                "is_suspect": False,
                "engines": {...}
            },
            ...
        ]
    """
    if scorer is None:
        scorer = ConsensusScorer()

    results = scorer.score_batch(names, contexts)
    return [r.to_dict() for r in results]
