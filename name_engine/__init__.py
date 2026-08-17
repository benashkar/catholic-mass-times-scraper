"""
names_people_matcher — Multi-engine PDF name extraction & confidence scoring.

Usage:
    from name_engine import extract_names_from_pdf, score_names
    from name_engine.engines import DictionaryEngine, SpacyNEREngine, NameDatasetEngine
    from name_engine.consensus import ConsensusScorer
    from name_engine.pdf_columns import extract_text_by_columns
"""

from name_engine.extract import extract_names_from_pdf
from name_engine.score import score_names

__version__ = "0.1.0"
__all__ = ["extract_names_from_pdf", "score_names"]
