"""
spaCy NER Engine — Validates names using Named Entity Recognition.

Runs spaCy's NER model on the surrounding context text and checks whether
the extracted name overlaps with a PERSON entity. This catches non-names
like "Everything Athens" or "Silver Angels Meet" that dictionary engines miss.
"""

import logging

from name_engine.engines.base import BaseEngine, EngineResult

logger = logging.getLogger(__name__)

_nlp = None


def _get_nlp(model="en_core_web_lg"):
    """Lazy-load spaCy model (heavy import, do it once).

    Prefers en_core_web_lg (91.3 F1 on CoNLL-03) for better PERSON precision.
    Falls back to en_core_web_sm if lg is not installed.
    """
    global _nlp
    if _nlp is None:
        try:
            import spacy
            try:
                _nlp = spacy.load(model)
            except OSError:
                # Fallback to smaller model if the requested one isn't installed
                fallback = "en_core_web_sm"
                logger.info(f"spaCy model {model} not found, falling back to {fallback}")
                _nlp = spacy.load(fallback)
            logger.info(f"Loaded spaCy model: {_nlp.meta['name']}")
        except Exception as e:
            logger.warning(f"spaCy not available ({e}), NER engine disabled")
            _nlp = False  # Sentinel: tried and failed
    return _nlp if _nlp is not False else None


class SpacyNEREngine(BaseEngine):
    """Score names by checking if spaCy recognizes them as PERSON entities."""

    name = "spacy_ner"

    def __init__(self, model="en_core_web_lg"):
        self._model = model

    def score(self, name, context=""):
        nlp = _get_nlp(self._model)
        if nlp is None:
            return EngineResult(
                engine_name=self.name,
                score=0.5,  # Neutral if unavailable
                is_name=True,
                details={"reason": "spacy_unavailable"},
            )

        # Use context if provided, otherwise just the name
        text = context if context else name
        doc = nlp(text)

        name_lower = name.lower().strip()
        person_match = False
        org_match = False
        gpe_match = False

        for ent in doc.ents:
            ent_text_lower = ent.text.lower().strip()
            # Check for overlap (name appears within entity or vice versa)
            if name_lower in ent_text_lower or ent_text_lower in name_lower:
                if ent.label_ == "PERSON":
                    person_match = True
                elif ent.label_ == "ORG":
                    org_match = True
                elif ent.label_ in ("GPE", "LOC"):
                    gpe_match = True

        if person_match:
            return EngineResult(
                engine_name=self.name,
                score=1.0,
                is_name=True,
                details={"entity": "PERSON"},
            )
        elif org_match:
            return EngineResult(
                engine_name=self.name,
                score=0.2,
                is_name=False,
                details={"entity": "ORG", "reason": "matched_organization"},
            )
        elif gpe_match:
            return EngineResult(
                engine_name=self.name,
                score=0.1,
                is_name=False,
                details={"entity": "GPE", "reason": "matched_location"},
            )
        else:
            return EngineResult(
                engine_name=self.name,
                score=0.3,
                is_name=False,
                details={"entity": None, "reason": "no_entity_match"},
            )

    def score_batch(self, names, contexts=None):
        """Batch scoring — process all contexts in one spaCy pipe call."""
        nlp = _get_nlp(self._model)
        if nlp is None:
            return [
                EngineResult(self.name, 0.5, True, {"reason": "spacy_unavailable"})
                for _ in names
            ]

        contexts = contexts or [""] * len(names)
        texts = [c if c else n for c, n in zip(contexts, names)]

        results = []
        docs = list(nlp.pipe(texts, batch_size=50))

        for name, doc in zip(names, docs):
            name_lower = name.lower().strip()
            person_match = False
            org_match = False

            for ent in doc.ents:
                ent_lower = ent.text.lower().strip()
                if name_lower in ent_lower or ent_lower in name_lower:
                    if ent.label_ == "PERSON":
                        person_match = True
                    elif ent.label_ == "ORG":
                        org_match = True

            if person_match:
                results.append(EngineResult(self.name, 1.0, True, {"entity": "PERSON"}))
            elif org_match:
                results.append(EngineResult(self.name, 0.2, False, {"entity": "ORG"}))
            else:
                results.append(EngineResult(self.name, 0.3, False, {"entity": None}))

        return results
