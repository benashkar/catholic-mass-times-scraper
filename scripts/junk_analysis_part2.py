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
print("SUPPLEMENTARY JUNK ANALYSIS")
print("=" * 80)

# ── Cat 3 deep dive: Single-char middle words ──────────────────────────
# The main run found 0. Let's check what middle single chars exist.
print("\n" + "-" * 80)
print("CAT 3 DEEP DIVE: Single-character middle words")
print("-" * 80)

single_mid_examples = []
for name in sorted(unique_names):
    words = name.split()
    if len(words) >= 3:
        for i, w in enumerate(words[1:-1], 1):
            if len(w) == 1:
                single_mid_examples.append((name, w, i, w.isupper()))

print(f"  Total names with single-char middle word: {len(single_mid_examples)}")
upper_count = sum(1 for _, _, _, is_up in single_mid_examples if is_up)
lower_count = sum(1 for _, _, _, is_up in single_mid_examples if not is_up)
print(f"  Uppercase (initials, excluded from junk): {upper_count}")
print(f"  Lowercase (flagged as junk): {lower_count}")
print("\n  Sample lowercase single-char middle words:")
lower_examples = [(n, c, i) for n, c, i, iu in single_mid_examples if not iu][:20]
for n, c, i in lower_examples:
    print(f"    '{c}' in: {n}")

# ── Cat 5 deep dive: Numbers/special chars ─────────────────────────────
print("\n" + "-" * 80)
print("CAT 5 DEEP DIVE: Numbers and special characters in names")
print("-" * 80)

has_number = []
has_special = []
for name in sorted(unique_names):
    if re.search(r"[0-9]", name):
        has_number.append(name)
    special = re.findall(r"[^a-zA-Z\s.\-\'\,]", name)
    if special:
        has_special.append((name, "".join(set(special))))

print(f"  Names with numbers: {len(has_number)}")
for n in has_number[:20]:
    print(f"    {n}")

print(f"\n  Names with special chars: {len(has_special)}")
for n, chars in has_special[:20]:
    conf = name_to_confidence.get(n, "?")
    print(f"    [{conf}] {n:55s} chars: {chars}")

# ── Newline characters in names ────────────────────────────────────────
print("\n" + "-" * 80)
print("NAMES CONTAINING NEWLINE CHARACTERS")
print("-" * 80)
newline_names = [n for n in unique_names if "\n" in n or "\r" in n]
print(f"  Count: {len(newline_names)}")
for n in sorted(newline_names)[:20]:
    conf = name_to_confidence.get(n, "?")
    display = n.replace("\n", "\\n").replace("\r", "\\r")
    print(f"    [{conf}] {display}")

# ── "Steward" false positive investigation ─────────────────────────────
print("\n" + "-" * 80)
print("'STEWARD' INVESTIGATION: Is it a surname or truncated 'Stewardship'?")
print("-" * 80)
steward_names = [n for n in unique_names if "Steward" in n and "Stewardship" not in n]
print(f"  Names containing 'Steward' (not 'Stewardship'): {len(steward_names)}")
for n in sorted(steward_names)[:20]:
    conf = name_to_confidence.get(n, "?")
    print(f"    [{conf}] {n}")

# ── Cat 10 deep dive: Merged names analysis ────────────────────────────
print("\n" + "-" * 80)
print("CAT 10 DEEP DIVE: Merged names -- are these real merges?")
print("-" * 80)

COMMON_FIRST_NAMES = {
    "james",
    "john",
    "robert",
    "michael",
    "william",
    "david",
    "richard",
    "joseph",
    "thomas",
    "charles",
    "christopher",
    "daniel",
    "matthew",
    "anthony",
    "mark",
    "donald",
    "steven",
    "paul",
    "andrew",
    "joshua",
    "kenneth",
    "kevin",
    "brian",
    "george",
    "timothy",
    "ronald",
    "edward",
    "jason",
    "jeffrey",
    "ryan",
    "jacob",
    "gary",
    "nicholas",
    "eric",
    "jonathan",
    "stephen",
    "larry",
    "justin",
    "scott",
    "brandon",
    "benjamin",
    "samuel",
    "raymond",
    "gregory",
    "frank",
    "patrick",
    "jack",
    "dennis",
    "jerry",
    "tyler",
    "mary",
    "patricia",
    "jennifer",
    "linda",
    "barbara",
    "elizabeth",
    "susan",
    "jessica",
    "sarah",
    "karen",
    "lisa",
    "nancy",
    "betty",
    "margaret",
    "sandra",
    "ashley",
    "dorothy",
    "kimberly",
    "emily",
    "donna",
    "michelle",
    "carol",
    "amanda",
    "melissa",
    "deborah",
    "stephanie",
    "rebecca",
    "sharon",
    "laura",
    "cynthia",
    "kathleen",
    "amy",
    "angela",
    "shirley",
    "anna",
    "brenda",
    "pamela",
    "emma",
    "nicole",
    "helen",
    "samantha",
    "katherine",
    "christine",
    "debra",
    "rachel",
    "carolyn",
    "janet",
    "catherine",
    "maria",
    "heather",
    "diane",
    "ruth",
    "julie",
    "olivia",
    "joyce",
    "virginia",
    "victoria",
    "kelly",
    "lauren",
    "christina",
    "joan",
    "evelyn",
    "judith",
    "megan",
    "andrea",
    "cheryl",
    "hannah",
    "jacqueline",
    "martha",
    "gloria",
    "teresa",
    "ann",
    "janice",
    "jean",
    "abigail",
    "alice",
    "judy",
    "sophia",
    "grace",
    "denise",
    "amber",
    "doris",
    "marilyn",
    "danielle",
    "beverly",
    "isabella",
    "theresa",
    "diana",
    "natalie",
    "brittany",
    "charlotte",
    "marie",
    "kayla",
    "alexis",
    "lori",
    "peter",
    "henry",
    "carl",
    "arthur",
    "fred",
    "albert",
    "joe",
    "eugene",
    "wayne",
    "ralph",
    "roy",
    "louis",
    "russell",
    "bobby",
    "philip",
    "harry",
    "vincent",
    "roger",
    "dale",
    "howard",
    "bruce",
    "alan",
    "willie",
    "gabriel",
    "alexander",
    "aaron",
    "randy",
    "ray",
    "joel",
    "tommy",
    "adam",
    "sean",
    "nathan",
    "jesse",
    "craig",
    "billy",
    "todd",
    "johnny",
    "phillip",
    "terry",
    "tony",
    "allen",
    "douglas",
    "leonard",
    "harold",
    "stanley",
    "dylan",
    "logan",
    "luke",
    "alex",
    "connor",
    "jose",
    "mike",
    "jim",
    "bob",
    "bill",
    "tom",
    "dan",
    "ed",
    "ted",
    "tim",
}

merged = []
for name in unique_names:
    words = name.split()
    if len(words) >= 3:
        last_lower = words[-1].lower()
        first_lower = words[0].lower()
        if last_lower in COMMON_FIRST_NAMES and first_lower in COMMON_FIRST_NAMES:
            middle = words[1:-1]
            if any(w[0].isupper() and len(w) > 1 for w in middle):
                merged.append(name)

# Count how many have exactly 3 words (FirstName LastName FirstName pattern)
three_word = [n for n in merged if len(n.split()) == 3]
four_plus = [n for n in merged if len(n.split()) >= 4]
print(f"  Total merged: {len(merged)}")
print(f"  3-word pattern (FN LN FN): {len(three_word)}")
print(f"  4+ word pattern: {len(four_plus)}")

# Most common last-position first names (the "merged" name)
last_name_counter = Counter()
for n in merged:
    last_name_counter[n.split()[-1].lower()] += 1

print("\n  Most common 'appended' first names:")
for name, ct in last_name_counter.most_common(20):
    print(f"    {name:15s}: {ct:>4,}")

print("\n  Sample 4+ word merged names:")
for n in sorted(four_plus)[:15]:
    print(f"    {n}")

# ── Confidence-specific junk examples ──────────────────────────────────
print("\n" + "-" * 80)
print("HIGH-CONFIDENCE JUNK EXAMPLES (most concerning)")
print("-" * 80)

# Reload categories quickly for high-confidence junk
COMMON_NOUNS_VERBS = {
    "salad",
    "pasta",
    "dinner",
    "lunch",
    "breakfast",
    "supper",
    "food",
    "meal",
    "snack",
    "chicken",
    "beef",
    "pork",
    "soup",
    "bread",
    "cake",
    "cookie",
    "pie",
    "casserole",
    "potluck",
    "raffle",
    "bingo",
    "auction",
    "carnival",
    "festival",
    "picnic",
    "bazaar",
    "bulletin",
    "newsletter",
    "announcement",
    "flyer",
    "brochure",
    "calendar",
    "schedule",
    "meeting",
    "meetings",
    "class",
    "classes",
    "session",
    "sessions",
    "parking",
    "building",
    "room",
    "hall",
    "center",
    "office",
    "rectory",
    "gym",
    "mass",
    "masses",
    "confession",
    "confessions",
    "baptism",
    "baptisms",
    "funeral",
    "funerals",
    "wedding",
    "weddings",
    "communion",
    "confirmation",
    "education",
    "religious",
    "formation",
    "sacrament",
    "sacraments",
    "cemetery",
    "memorial",
    "remembrance",
    "deceased",
    "collection",
    "weekly",
    "monthly",
    "annual",
    "daily",
    "special",
    "church",
    "parish",
    "diocese",
    "archdiocese",
    "catholic",
    "christian",
    "school",
    "academy",
    "preschool",
    "kindergarten",
    "elementary",
    "council",
    "committee",
    "board",
    "team",
    "guild",
    "society",
    "club",
    "music",
    "choir",
    "band",
    "song",
    "songs",
    "hymn",
    "hymns",
    "reading",
    "readings",
    "gospel",
    "scripture",
    "bible",
    "verse",
    "flower",
    "flowers",
    "altar",
    "candle",
    "candles",
    "sanctuary",
    "ministry",
    "ministries",
    "adoration",
    "rosary",
    "novena",
    "stations",
    "anniversary",
    "celebration",
    "retreat",
    "camp",
    "trip",
    "pilgrimage",
    "book",
    "books",
    "library",
    "study",
    "studies",
    "pantry",
    "shelter",
    "outreach",
    "mission",
    "missions",
    "stewardship",
    "evangelization",
    "catechesis",
    "thank",
    "thanks",
    "welcome",
    "blessed",
    "blessing",
    "prayer",
    "pray",
    "volunteer",
    "volunteering",
    "serving",
    "offering",
    "donation",
    "donate",
    "families",
    "family",
    "parent",
    "parents",
    "children",
    "kids",
    "youth",
    "adult",
    "adults",
    "senior",
    "seniors",
    "program",
    "programs",
    "event",
    "events",
    "activity",
    "activities",
    "food",
    "drive",
    "breakfast",
    "dinner",
    "lunch",
}

high_junk = []
for n in unique_names:
    if name_to_confidence.get(n) != "high":
        continue
    words_lower = [w.lower() for w in n.split()]
    for wl in words_lower:
        if wl in COMMON_NOUNS_VERBS and len(wl) > 3:
            high_junk.append(n)
            break

import random  # noqa: E402

random.seed(123)
random.shuffle(high_junk)
print(f"  High-confidence names with noun/verb words: {len(high_junk)}")
print("\n  Random sample of 25:")
for n in high_junk[:25]:
    print(f"    {n}")

# ── "Knights of Columbus" special analysis ─────────────────────────────
print("\n" + "-" * 80)
print("'KNIGHTS OF COLUMBUS' SPECIAL ANALYSIS")
print("-" * 80)
koc = [n for n in unique_names if "knight" in n.lower() or "columbus" in n.lower()]
print(f"  Names containing 'knight' or 'columbus': {len(koc)}")
for n in sorted(koc)[:20]:
    conf = name_to_confidence.get(n, "?")
    print(f"    [{conf}] {n}")

print("\n" + "=" * 80)
print("SUPPLEMENTARY ANALYSIS COMPLETE")
print("=" * 80)
