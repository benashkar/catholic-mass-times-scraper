"""
Scoring engines — each provides an independent confidence signal.

All engines implement the same interface:
    engine.score(name: str, context: str = "") -> EngineResult
"""

from name_engine.engines.dictionary import DictionaryEngine
from name_engine.engines.name_dataset_engine import NameDatasetEngine
from name_engine.engines.spacy_ner import SpacyNEREngine

__all__ = ["DictionaryEngine", "SpacyNEREngine", "NameDatasetEngine"]
