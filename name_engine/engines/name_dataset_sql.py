"""NameDataset scoring backed by db99 instead of a 1.86GB in-process dataset.

Drop-in replacement for NameDatasetEngine. The library costs ~1.86 GB resident
(measured: 92 MB -> 1,948 MB on the 2GB cron plan), which cannot coexist with
spaCy en_core_web_lg and pdfplumber in the bulletin sweep — that sweep already
runs at --workers 2 because 4 were OOM-killed 27 times in 56 jobs.

The library is only ever asked membership questions, so `ref_name_dataset`
(built by export_name_dataset_to_db99.py) answers them over an index instead.
Same questions, same answers, ~0 MB.

SCORING IS A DELIBERATE MIRROR of NameDatasetEngine and must stay that way, or
the consensus silently shifts:

    +0.35  first word is a known first name
    +0.35  last word is a known surname
    +0.10  exactly two words
    -0.10  first word is a known surname but NOT a known first name
    clamp 0..1;  is_name = score >= 0.4

If NameDatasetEngine's weights change upstream, change them here too.
"""

import logging

from name_engine.engines.base import BaseEngine, EngineResult

logger = logging.getLogger(__name__)


class NameDatasetSQLEngine(BaseEngine):
    """Membership lookups against ref_name_dataset on db99."""

    name = "name_dataset"  # same key as the library engine, so consensus is unchanged

    def __init__(self, conn_factory=None):
        """conn_factory: callable returning a live DB connection.

        Defaults to the project's db99 helper, imported lazily so this module
        stays importable in contexts that have no database.
        """
        self._conn_factory = conn_factory
        self._conn = None

    def _cursor(self):
        if self._conn is None:
            if self._conn_factory is None:
                from extract_bulletins_to_db99 import get_connection

                self._conn_factory = get_connection
            self._conn = self._conn_factory()
        return self._conn.cursor()

    def _lookup(self, words):
        """One query for every distinct word in the batch."""
        if not words:
            return {}
        cur = self._cursor()
        out = {}
        # Chunked so a large batch cannot build an unbounded IN() list.
        chunk = 900
        words = list(words)
        for i in range(0, len(words), chunk):
            part = words[i : i + chunk]
            fmt = ",".join(["%s"] * len(part))
            cur.execute(
                f"SELECT name_upper, is_first, is_last FROM ref_name_dataset "
                f"WHERE name_upper IN ({fmt})",
                part,
            )
            for r in cur.fetchall():
                key = r["name_upper"] if isinstance(r, dict) else r[0]
                first = r["is_first"] if isinstance(r, dict) else r[1]
                last = r["is_last"] if isinstance(r, dict) else r[2]
                out[key] = (bool(first), bool(last))
        cur.close()
        return out

    def score_batch(self, names, contexts=None):
        contexts = contexts or [""] * len(names)

        wanted = set()
        for n in names:
            w = (n or "").strip().split()
            if len(w) >= 2:
                wanted.add(w[0].strip().upper()[:100])
                wanted.add(w[-1].strip().upper()[:100])

        try:
            known = self._lookup(wanted)
        except Exception as exc:
            # Fail LOUDLY-ish: return the neutral result the library engine uses
            # when it is unavailable, rather than scoring everything 0, which
            # would look like "these are not names" and quietly demote the lot.
            logger.warning(f"ref_name_dataset lookup failed ({exc}); engine neutral")
            return [
                EngineResult(
                    engine_name=self.name,
                    score=0.5,
                    is_name=True,
                    details={"reason": "name_dataset_unavailable"},
                )
                for _ in names
            ]

        results = []
        for n in names:
            words = (n or "").strip().split()
            if len(words) < 2:
                results.append(
                    EngineResult(
                        engine_name=self.name,
                        score=0.0,
                        is_name=False,
                        details={"reason": "too_few_words"},
                    )
                )
                continue

            fkey = words[0].strip().upper()[:100]
            lkey = words[-1].strip().upper()[:100]
            f_is_first, f_is_last = known.get(fkey, (False, False))
            _, l_is_last = known.get(lkey, (False, False))

            score = 0.0
            details = {"first_found": f_is_first, "last_found": l_is_last}
            if f_is_first:
                score += 0.35
            if l_is_last:
                score += 0.35
            if len(words) == 2:
                score += 0.10
            if not f_is_first and f_is_last:
                score -= 0.10
                details["first_is_surname_only"] = True

            score = max(0.0, min(1.0, score))
            results.append(
                EngineResult(
                    engine_name=self.name,
                    score=score,
                    is_name=score >= 0.4,
                    details=details,
                )
            )
        return results

    def score(self, name, context=""):
        return self.score_batch([name], [context])[0]
