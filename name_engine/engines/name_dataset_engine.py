"""
name-dataset Engine — Scores names against 730K+ first names and 983K+ last names
from 54 countries.

Provides broader international coverage than SSA + Census, plus gender probability
and country-of-origin data.
"""

import logging

from name_engine.engines.base import BaseEngine, EngineResult

logger = logging.getLogger(__name__)

_nd = None


def _get_name_dataset():
    """Lazy-load name-dataset (large download on first use)."""
    global _nd
    if _nd is None:
        try:
            from names_dataset import NameDataset
            _nd = NameDataset()
            logger.info("Loaded name-dataset (730K+ first, 983K+ last)")
        except ImportError as e:
            logger.warning(f"names-dataset not available ({e}), engine disabled")
            _nd = False
    return _nd if _nd is not False else None


class NameDatasetEngine(BaseEngine):
    """Score names using the names-dataset library (54 countries)."""

    name = "name_dataset"

    def score(self, name, context=""):
        nd = _get_name_dataset()
        if nd is None:
            return EngineResult(
                engine_name=self.name,
                score=0.5,
                is_name=True,
                details={"reason": "name_dataset_unavailable"},
            )

        words = name.strip().split()
        if len(words) < 2:
            return EngineResult(
                engine_name=self.name,
                score=0.0,
                is_name=False,
                details={"reason": "too_few_words"},
            )

        first = words[0]
        last = words[-1]

        first_result = nd.search(first)
        last_result = nd.search(last)

        score = 0.0
        details = {}

        # Check first name
        first_as_first = first_result.get("first_name", {})
        first_found = bool(first_as_first) and any(
            v > 0 for v in (first_as_first.get("country", {}) or {}).values()
        )
        if first_found:
            score += 0.35
            details["first_found"] = True
            gender = first_as_first.get("gender", {})
            if gender:
                details["gender"] = gender
            countries = first_as_first.get("country", {})
            if countries:
                top_country = max(countries, key=countries.get)
                details["first_top_country"] = top_country
        else:
            details["first_found"] = False

        # Check last name
        last_as_last = last_result.get("last_name", {})
        last_found = bool(last_as_last) and any(
            v > 0 for v in (last_as_last.get("country", {}) or {}).values()
        )
        if last_found:
            score += 0.35
            details["last_found"] = True
            countries = last_as_last.get("country", {})
            if countries:
                top_country = max(countries, key=countries.get)
                details["last_top_country"] = top_country
        else:
            details["last_found"] = False

        # 2-word name bonus
        if len(words) == 2:
            score += 0.10

        # Penalty: first word is a last name but NOT a first name (reversed?)
        first_as_last = first_result.get("last_name", {})
        first_only_last = (
            not first_found
            and bool(first_as_last)
            and any(v > 0 for v in (first_as_last.get("country", {}) or {}).values())
        )
        if first_only_last:
            score -= 0.10
            details["first_is_surname_only"] = True

        score = max(0.0, min(1.0, score))

        return EngineResult(
            engine_name=self.name,
            score=score,
            is_name=score >= 0.4,
            details=details,
        )
