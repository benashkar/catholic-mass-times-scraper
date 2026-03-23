import csv
import os
import re
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

STATES = [
    "illinois",
    "texas",
    "california",
    "ohio",
    "florida",
    "wisconsin",
    "connecticut",
    "minnesota",
    "alaska",
    "arizona",
]
BASE = r"C:\Users\cashk\OneDrive\Projects\church scrapes\data\output"

all_rows = []
for st in STATES:
    path = os.path.join(BASE, st, "bulletin_names.csv")
    if not os.path.exists(path):
        continue
    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for r in reader:
            all_rows.append((r, st))

unique_names = set()
name_to_confidence = {}
for r, st in all_rows:
    pn = r.get("person_name", "").strip()
    if pn:
        unique_names.add(pn)
        name_to_confidence[pn] = r.get("confidence", "").strip().lower()

print("=" * 80)
print("PART 3: NEWLINE CHARACTER AND REVISED TOTALS")
print("=" * 80)

# ── NEWLINE ANALYSIS ───────────────────────────────────────────────────
newline_names = {n for n in unique_names if "\n" in n or "\r" in n}
clean_no_newline = unique_names - newline_names

print(f"\n  Total unique names:              {len(unique_names):>8,}")
print(
    f"  Names WITH newline characters:   {len(newline_names):>8,}  ({100.0*len(newline_names)/len(unique_names):.1f}%)"
)
print(
    f"  Names WITHOUT newlines:          {len(clean_no_newline):>8,}  ({100.0*len(clean_no_newline)/len(unique_names):.1f}%)"
)

# Confidence of newline names
nl_conf = Counter()
for n in newline_names:
    nl_conf[name_to_confidence.get(n, "?")] += 1
print("\n  Newline names by confidence:")
for c, ct in nl_conf.most_common():
    print(f"    {c}: {ct:,}")

# How many lines in newline names?
line_counts = Counter()
for n in newline_names:
    lines = n.count("\n") + 1
    line_counts[lines] += 1
print("\n  Number of lines in newline names:")
for lc in sorted(line_counts.keys()):
    print(f"    {lc} lines: {line_counts[lc]:,} names")

# ── After stripping newlines, what happens? ────────────────────────────
print("\n" + "-" * 80)
print("NEWLINE NAMES: What are they after splitting on newlines?")
print("-" * 80)

# For newline names, take just the first line and see if it looks like a name
first_line_looks_like_name = 0
second_line_has_name = 0
for n in newline_names:
    parts = [p.strip() for p in re.split(r"[\n\r]+", n) if p.strip()]
    if parts:
        first = parts[0]
        words = first.split()
        # Does first line have 2-3 words, all capitalized?
        if 2 <= len(words) <= 3 and all(w[0].isupper() for w in words if w):
            first_line_looks_like_name += 1
    if len(parts) >= 2:
        second = parts[1]
        words2 = second.split()
        if 2 <= len(words2) <= 3 and all(w[0].isupper() for w in words2 if w):
            second_line_has_name += 1

print(f"  First line looks like a name:  {first_line_looks_like_name:,}")
print(f"  Second line also looks like a name: {second_line_has_name:,}")

# Examples of what the newline splits look like
import random  # noqa: E402

random.seed(99)
sample_nl = random.sample(sorted(newline_names), min(30, len(newline_names)))
print("\n  30 random newline-name examples (lines separated by | ):")
for n in sample_nl:
    parts = [p.strip() for p in re.split(r"[\n\r]+", n) if p.strip()]
    conf = name_to_confidence.get(n, "?")
    display = " | ".join(parts)
    print(f"    [{conf}] {display}")

# ── ALL CAPS NAMES ─────────────────────────────────────────────────────
print("\n" + "-" * 80)
print("ALL-CAPS NAMES (may be headings, not person names)")
print("-" * 80)
all_caps = {n for n in unique_names if n == n.upper() and len(n) > 3 and " " in n}
print(f"  All-caps multi-word names: {len(all_caps):,}")
for n in sorted(all_caps)[:25]:
    conf = name_to_confidence.get(n, "?")
    print(f"    [{conf}] {n.replace(chr(10), ' | ')}")

# ── VERY LONG NAMES (more than 4 words, probably phrases) ─────────────
print("\n" + "-" * 80)
print("VERY LONG NAMES (5+ words after newline removal, probably phrases)")
print("-" * 80)
long_names = []
for n in unique_names:
    clean = re.sub(r"[\n\r]+", " ", n).strip()
    words = clean.split()
    if len(words) >= 5:
        long_names.append((n, len(words)))
long_names.sort(key=lambda x: -x[1])
print(f"  Names with 5+ words: {len(long_names):,}")

# Confidence breakdown
long_conf = Counter()
for n, wc in long_names:
    long_conf[name_to_confidence.get(n, "?")] += 1
print(f"  By confidence: {dict(long_conf)}")

print("\n  Longest 25:")
for n, wc in long_names[:25]:
    conf = name_to_confidence.get(n, "?")
    display = re.sub(r"[\n\r]+", " | ", n)
    print(f"    [{conf}] ({wc}w) {display[:80]}")

# ── VERY SHORT NAMES (single word) ────────────────────────────────────
print("\n" + "-" * 80)
print("SINGLE-WORD NAMES (just one word, possibly incomplete extraction)")
print("-" * 80)
single_word = {n for n in unique_names if " " not in n and "\n" not in n and len(n) > 1}
print(f"  Single-word names: {len(single_word):,}")
sw_conf = Counter()
for n in single_word:
    sw_conf[name_to_confidence.get(n, "?")] += 1
print(f"  By confidence: {dict(sw_conf)}")
print("\n  Sample:")
for n in sorted(single_word)[:30]:
    conf = name_to_confidence.get(n, "?")
    print(f"    [{conf}] {n}")

# ── REVISED GRAND TOTAL ───────────────────────────────────────────────
print("\n" + "=" * 80)
print("REVISED GRAND TOTAL: All junk categories combined")
print("=" * 80)

all_junk = set()

# From part 1 categories (re-run quick check on nouns/verbs subset only for confirmation)
# But we can sum from the original run:
# 01_NOUNS_VERBS: 48,517
# 02_LOWERCASE_PHRASE: 4,905
# 04_TRUNCATED: 149
# 06_PLACE_NAMES: 3,535
# 07_ACTION_VERBS: 5,192
# 08_COMMERCIAL: 6,845
# 09_SPANISH_LATIN: 237
# 10_MERGED_NAMES: 7,974

# New categories found in part 2/3:
# Newline names: 77,427
# All-caps: counted above
# 5+ word names: counted above

print("\n  Category                               Unique Names")
print("  " + "-" * 55)
print(f"  Names with embedded newlines:          {len(newline_names):>8,}")
print(f"  All-caps multi-word (headings):        {len(all_caps):>8,}")
print(f"  5+ word names (phrases):               {len(long_names):>8,}")
print(f"  Single-word names:                     {len(single_word):>8,}")
print("  (Original 10 categories from Part 1):   ~73,155")
print("")
print("  NOTE: These categories overlap significantly.")
print("  Many newline names also trigger noun/verb or merged-name detection.")

# Combined unique junk: union of newline + all_caps + long + single_word
structural_junk = newline_names | all_caps | set(n for n, wc in long_names) | single_word
print(f"\n  Structural junk (newline/caps/long/single): {len(structural_junk):,}")
print(f"  As % of all unique names: {100.0*len(structural_junk)/len(unique_names):.1f}%")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)
