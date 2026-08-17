"""
Dictionary Engine — Scores names against SSA baby names + Census 2010 surnames.

This is the existing scoring approach from church scrapes, extracted into a
reusable engine. It checks first names against SSA data (100K entries) and
last names against Census data (162K entries).
"""

import csv
import os
from functools import lru_cache

from name_engine.engines.base import BaseEngine, EngineResult

# Default reference data paths (can be overridden)
_DEFAULT_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@lru_cache(maxsize=1)
def _load_ssa_names(path=None):
    """Load SSA baby names into a dict: lowercase name -> rank."""
    path = path or os.path.join(_DEFAULT_DATA_DIR, "ssa_first_names.csv")
    names = {}
    if not os.path.exists(path):
        return names
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or "").strip().lower()
            rank = int(row.get("rank") or row.get("Rank") or 99999)
            if name:
                names[name] = min(names.get(name, 99999), rank)
    return names


@lru_cache(maxsize=1)
def _load_census_surnames(path=None):
    """Load Census 2010 surnames into a dict: lowercase name -> rank."""
    path = path or os.path.join(_DEFAULT_DATA_DIR, "census_surnames.csv")
    names = {}
    if not os.path.exists(path):
        return names
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or "").strip().lower()
            rank = int(row.get("rank") or row.get("Rank") or 99999)
            if name:
                names[name] = min(names.get(name, 99999), rank)
    return names


# Common non-name words that score high in dictionaries
_SCORE_GAMING_WORDS = {
    "grace", "faith", "hope", "joy", "christian", "dean", "rich", "art",
    "mark", "bill", "frank", "ray", "grant", "young", "page", "cook",
    "long", "short", "love", "angel", "fortune", "best", "fair", "noble",
}


class DictionaryEngine(BaseEngine):
    """Score names against SSA baby names + Census 2010 surnames."""

    name = "dictionary"

    def __init__(self, ssa_path=None, census_path=None):
        self._ssa = _load_ssa_names(ssa_path)
        self._census = _load_census_surnames(census_path)

    def score(self, name, context=""):
        words = name.strip().split()
        if not words or len(words) < 2:
            return EngineResult(
                engine_name=self.name,
                score=0.0,
                is_name=False,
                details={"reason": "too_few_words"},
            )

        first = words[0].lower()
        last = words[-1].lower()

        score = 0.0
        details = {}

        # First name in SSA
        first_in_ssa = first in self._ssa
        if first_in_ssa:
            score += 0.30
            rank = self._ssa[first]
            details["first_ssa_rank"] = rank
            if rank <= 1000:
                score += 0.10

        # Last name in Census
        last_in_census = last in self._census
        if last_in_census:
            score += 0.30
            rank = self._census[last]
            details["last_census_rank"] = rank
            if rank <= 1000:
                score += 0.10

        # 2-word name bonus
        if len(words) == 2:
            score += 0.10

        # Penalties
        if not first_in_ssa:
            score -= 0.20
            details["first_not_in_ssa"] = True
        if not last_in_census and last not in self._ssa:
            score -= 0.20
            details["last_not_in_census"] = True

        # Score gaming penalty
        lower_words = {w.lower() for w in words}
        gaming = lower_words & _SCORE_GAMING_WORDS
        if gaming:
            score -= 0.15 * len(gaming)
            details["gaming_words"] = list(gaming)

        # Contains newline
        if "\n" in name:
            score -= 0.30

        # Short last name (not initial)
        if len(last) < 3 and not (len(last) == 1 and last.isupper()):
            score -= 0.25

        score = max(0.0, min(1.0, score))

        return EngineResult(
            engine_name=self.name,
            score=score,
            is_name=score >= 0.4,
            details=details,
        )
