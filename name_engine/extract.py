"""
High-level extraction API — extracts names from PDFs using column-aware
text extraction and multi-engine scoring.
"""

from name_engine.consensus import ConsensusScorer
from name_engine.pdf_columns import extract_text_by_columns


def extract_names_from_pdf(pdf_path, name_patterns=None, scorer=None):
    """Extract and score names from a PDF file.

    Args:
        pdf_path: Path to the PDF file
        name_patterns: Optional custom regex patterns for name extraction.
                       If None, uses built-in patterns.
        scorer: Optional ConsensusScorer instance. If None, creates default.

    Returns:
        List of ConsensusResult for each extracted name.
    """
    if scorer is None:
        scorer = ConsensusScorer()

    # Phase 1: Column-aware text extraction
    pages = extract_text_by_columns(pdf_path)

    # Phase 2: Extract name candidates from text
    candidates = []
    for page_data in pages:
        for col_idx, col_text in enumerate(page_data["columns"]):
            names = _extract_name_candidates(col_text)
            for name, context in names:
                candidates.append((name, context))

    # Phase 3: Deduplicate
    seen = set()
    unique_candidates = []
    for name, context in candidates:
        key = name.strip().lower()
        if key not in seen:
            seen.add(key)
            unique_candidates.append((name, context))

    # Phase 4: Score all candidates
    names = [n for n, _ in unique_candidates]
    contexts = [c for _, c in unique_candidates]
    results = scorer.score_batch(names, contexts)

    return results


def _extract_name_candidates(text):
    """Extract potential name strings from text using regex patterns.

    This is a simplified version. The full church scrapes patterns can be
    plugged in via the name_patterns parameter.

    Args:
        text: Text string to search for names

    Returns:
        List of (name, context) tuples
    """
    import re

    candidates = []

    # Pattern: Two or more capitalized words (basic name pattern)
    name_re = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]{2,})\b")

    for match in name_re.finditer(text):
        name = match.group(1).strip()
        # Get surrounding context (100 chars each side)
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 100)
        context = text[start:end]
        candidates.append((name, context))

    return candidates
