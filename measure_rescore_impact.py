"""What would the current name_engine consensus do to names already in db99?

DRY RUN ONLY — this writes nothing. The point is to see the blast radius before
touching 776K rows, because the thresholds genuinely differ from what produced
them:

    live pipeline today          name_engine (current)
    ------------------------     ---------------------------------
    2 engines (dict + NER veto)  3 engines (dict, NER, NameDataset)
    medium >= 0.4                medium >= 0.5
    high   >= 0.7                high >= 0.7 AND all engines agree
    is_suspect = NER vetoed      engines disagree AND spread > 0.3

The third engine matters most here: SSA + Census are US-centric, and Catholic
parish bulletins are full of Polish, Hispanic, Vietnamese and Filipino
surnames. Those score low on US dictionaries and get filtered off the
dashboard, which looks exactly like the scraper having missed them.

Reports the full old-tier -> new-tier matrix, plus the names that would GAIN
confidence (the ones we are currently losing) and those that would LOSE it.

Usage (Render one-off):
    python -u measure_rescore_impact.py --state WI --sample 4000
"""

import argparse
import os
import sys
import traceback
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402

TIERS = ("high", "medium", "low")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="WI")
    ap.add_argument("--sample", type=int, default=4000)
    args = ap.parse_args()
    state = args.state.upper()

    from extract_bulletins_to_db99 import get_connection
    from name_engine.consensus import ConsensusScorer

    conn = get_connection()
    cur = conn.cursor()

    # A deterministic slice, not RAND(): the same rows every run so two
    # measurements are comparable, and RAND() over a large table is a full sort.
    cur.execute(
        """
        SELECT bn.person_name, bn.confidence, bn.is_suspect, bn.context
        FROM bulletin_name bn
        JOIN bulletin_pdf bp ON bp.bulletin_pdf_id = bn.bulletin_pdf_id
        JOIN bulletin_source bs ON bs.bulletin_source_id = bp.bulletin_source_id
        JOIN church c ON c.church_id = bs.church_id
        WHERE c.state_code = %s AND bn.person_name IS NOT NULL AND bn.person_name <> ''
        ORDER BY bn.bulletin_name_id
        LIMIT %s
        """,
        (state, args.sample),
    )
    rows = cur.fetchall()

    names = [r["person_name"] for r in rows]
    contexts = [(r.get("context") or "")[:300] for r in rows]

    scorer = ConsensusScorer()

    # REFUSE to report a consensus that is missing an engine.
    #
    # The engines disable themselves on import failure rather than raising, so a
    # broken spaCy degrades the "consensus" to two engines silently. That is not
    # a slightly weaker score, it inverts verdicts: measured locally on Python
    # 3.14 where spaCy cannot load, "Parish Council" came back medium/0.67 and
    # "Fr. John Smith" came back low/0.35. Rescoring 776K rows in that state
    # would corrupt the table while reporting success.
    probe = scorer.score_batch(["John Smith"], [""])[0]
    active = sorted(probe.engine_results.keys())
    if len(active) < 3:
        # Publish BEFORE exiting. SystemExit inherits BaseException, so the
        # `except Exception` handler at the bottom never saw it — the first
        # version of this guard aborted correctly and then reported nothing,
        # which from outside is indistinguishable from a crash.
        msg = (
            f"ABORT: only {len(active)} engines active ({', '.join(active)}). "
            "Expected dictionary + spacy_ner + name_dataset. A missing engine "
            "silently inverts verdicts — refusing to report a consensus."
        )
        print(msg, flush=True)
        publish("measure_rescore_impact", msg)
        raise SystemExit(msg)
    lines_prefix = f"engines active: {', '.join(active)}"

    results = scorer.score_batch(names, contexts)

    matrix = Counter()
    suspect_delta = Counter()
    gained, lost = [], []

    for r, res in zip(rows, results):
        old = (r["confidence"] or "low").lower()
        new = res.confidence
        matrix[(old, new)] += 1
        suspect_delta[(int(r["is_suspect"] or 0), int(res.is_suspect))] += 1
        rank = {"low": 0, "medium": 1, "high": 2}
        if rank.get(new, 0) > rank.get(old, 0) and len(gained) < 25:
            gained.append((r["person_name"], old, new, round(res.final_score, 2), res.reason))
        elif rank.get(new, 0) < rank.get(old, 0) and len(lost) < 25:
            lost.append((r["person_name"], old, new, round(res.final_score, 2), res.reason))

    lines = [
        f"rescore impact — state={state} sample={len(rows)} (DRY RUN, nothing written)",
        lines_prefix,
        "",
        "--- old tier -> new tier ---",
        f"{'old':8} {'new':8} {'count':>7}",
    ]
    for old in TIERS:
        for new in TIERS:
            n = matrix.get((old, new), 0)
            if n:
                flag = "" if old == new else "   <-- changes"
                lines.append(f"{old:8} {new:8} {n:7}{flag}")

    unchanged = sum(v for (o, n), v in matrix.items() if o == n)
    changed = sum(v for (o, n), v in matrix.items() if o != n)
    lines.append("")
    lines.append(f"unchanged {unchanged}   changed {changed}  "
                 f"({(100.0 * changed / max(1, len(rows))):.1f}%)")

    # Dashboard visibility is confidence IN (high, medium) AND is_suspect = 0,
    # so that is the number that actually matters to a reader.
    def visible(tier, suspect):
        return tier in ("high", "medium") and not suspect

    old_vis = sum(1 for r in rows if visible((r["confidence"] or "low").lower(),
                                             int(r["is_suspect"] or 0)))
    new_vis = sum(1 for res in results if visible(res.confidence, res.is_suspect))
    lines.append(f"dashboard-visible: {old_vis} -> {new_vis}  ({new_vis - old_vis:+d})")

    lines.append("")
    lines.append("--- is_suspect (old -> new) ---")
    for (o, n), v in sorted(suspect_delta.items()):
        lines.append(f"  {o} -> {n}: {v}")

    lines.append("")
    lines.append("--- would GAIN confidence (currently being lost) ---")
    for nm, o, n, sc, why in gained:
        lines.append(f"  {nm[:34]:36} {o:6} -> {n:6} score={sc} {why}")

    lines.append("")
    lines.append("--- would LOSE confidence ---")
    for nm, o, n, sc, why in lost:
        lines.append(f"  {nm[:34]:36} {o:6} -> {n:6} score={sc} {why}")

    cur.close()
    conn.close()

    out = "\n".join(lines)
    print(out, flush=True)
    publish("measure_rescore_impact", out)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise  # already reported at the raise site
    except BaseException:
        tb = "measure_rescore_impact FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("measure_rescore_impact", tb)
        sys.exit(1)
