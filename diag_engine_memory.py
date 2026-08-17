"""Which consensus engine blows the 2GB budget?

measure_rescore_impact died with no output at both 4,000 and 50 rows. Identical
behaviour at two very different data volumes means the cost is the fixed
model load, not the rows — and dying without running any Python handler is a
SIGKILL, i.e. the OOM killer, not an exception.

That matters beyond this script: the bulletin sweep already runs at
--workers 2 because 4 workers were OOM-killed 27 times in 56 jobs on this same
2GB plan, with spaCy en_core_web_lg and pdfplumber already near the ceiling.
If NameDataset costs most of a gigabyte, wiring the 3-engine consensus INTO
extraction would break the sweep that is currently recovering churches.

Loads each engine in turn and reports resident memory after each, publishing as
it goes so the last line that arrives names the culprit even if the process is
killed mid-step.

Usage (Render one-off):
    python -u diag_engine_memory.py
"""

import os
import resource
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402

steps = []


def rss_mb():
    # ru_maxrss is kilobytes on Linux.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def note(label):
    steps.append(f"{label:38} rss={rss_mb():7.0f} MB")
    out = "\n".join(steps)
    print(out, flush=True)
    # Publish after EVERY step: if the next one is SIGKILLed there is no
    # handler to report from, so the mailbox must already hold the last good
    # checkpoint.
    publish("diag_engine_memory", out)


note("baseline (interpreter only)")

from name_engine.engines.dictionary import DictionaryEngine  # noqa: E402

d = DictionaryEngine()
d.score_batch(["John Smith"], [""])
note("+ DictionaryEngine (SSA+Census)")

from name_engine.engines.spacy_ner import SpacyNEREngine  # noqa: E402

s = SpacyNEREngine()
s.score_batch(["John Smith"], [""])
note("+ SpacyNEREngine (en_core_web_lg)")

from name_engine.engines.name_dataset_engine import NameDatasetEngine  # noqa: E402

n = NameDatasetEngine()
n.score_batch(["John Smith"], [""])
note("+ NameDatasetEngine (730K/983K)")

steps.append("")
steps.append("ALL THREE LOADED — consensus is affordable in one process.")
out = "\n".join(steps)
print(out, flush=True)
publish("diag_engine_memory", out)
