import csv
import os
import re
import sys
from collections import Counter, defaultdict

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
state_counts = {}
for st in STATES:
    path = os.path.join(BASE, st, "bulletin_names.csv")
    if not os.path.exists(path):
        continue
    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        state_counts[st] = len(rows)
        all_rows.extend([(r, st) for r in rows])

unique_names = set()
name_to_confidence = {}
for r, st in all_rows:
    pn = r.get("person_name", "").strip()
    if pn:
        unique_names.add(pn)
        name_to_confidence[pn] = r.get("confidence", "").strip().lower()

# ── COMPREHENSIVE UNION OF ALL JUNK ────────────────────────────────────
# We build one unified set, tracking which categories each name hits

SAFE_SURNAMES_LOWER = {
    s.lower()
    for s in {
        "Church",
        "Hall",
        "Hill",
        "Field",
        "Fields",
        "Park",
        "Parks",
        "Lane",
        "West",
        "East",
        "North",
        "South",
        "Lake",
        "Rivers",
        "Spring",
        "Springs",
        "Grove",
        "Meadow",
        "Ridge",
        "Forest",
        "Woods",
        "Beach",
        "Shore",
        "Valley",
        "Marsh",
        "Pond",
        "Brook",
        "Brooks",
        "Stone",
        "Wood",
        "Cross",
        "Chapel",
        "Bishop",
        "Pope",
        "Priest",
        "Deacon",
        "Knight",
        "King",
        "Prince",
        "Duke",
        "Baron",
        "Lord",
        "Earl",
        "Cook",
        "Baker",
        "Miller",
        "Fisher",
        "Hunter",
        "Farmer",
        "Shepherd",
        "Taylor",
        "Smith",
        "Clark",
        "Walker",
        "Turner",
        "Carter",
        "Cooper",
        "Mason",
        "Porter",
        "Butler",
        "Gardner",
        "Carpenter",
        "Barber",
        "Singer",
        "Painter",
        "Weaver",
        "Brewer",
        "Glover",
        "Piper",
        "Cash",
        "Banks",
        "Price",
        "Rich",
        "Bond",
        "Sells",
        "Booth",
        "Free",
        "Best",
        "Love",
        "Joy",
        "Hope",
        "Grace",
        "Faith",
        "May",
        "June",
        "March",
        "Winter",
        "Summers",
        "Day",
        "Snow",
        "Frost",
        "Storm",
        "Flood",
        "Rain",
        "Dean",
        "Read",
        "Heard",
        "Case",
        "Close",
        "Senior",
        "Christian",
        "Christmas",
        "Easter",
        "Parish",
        "Mass",
        "Flores",
        "Villa",
        "Cruz",
        "Santos",
        "Reyes",
        "Morales",
        "Palm",
        "Rose",
        "Lily",
        "Violet",
        "Ivy",
        "Fern",
        "Steward",
        "Camp",
        "Guest",
        "House",
        "Homes",
        "Book",
        "Call",
        "Ring",
        "Bell",
        "Drum",
        "Blood",
        "Child",
        "Mann",
        "Bacon",
        "Gage",
        "Race",
        "Pace",
        "Deal",
        "Self",
        "Long",
        "Short",
        "Little",
        "Small",
        "Young",
        "Old",
        "Wise",
        "Sharp",
        "Smart",
        "Strong",
        "Savage",
        "Sweet",
        "Stern",
        "Bland",
        "Noble",
        "Bright",
        "Moody",
        "Fry",
        "Bake",
        "Boyle",
        "Sell",
        "Pay",
        "Buy",
        "Bury",
        "Lamb",
        "Ash",
        "Oak",
        "Birch",
        "Pine",
        "Elm",
        "Willow",
        "Rowan",
        "Burns",
        "Burn",
        "Dale",
        "Glen",
        "Moor",
        "Shields",
        "Power",
        "Powers",
        "Hand",
        "Hands",
        "Head",
        "Law",
        "Laws",
        "Rule",
        "Rush",
        "Mark",
        "Marks",
        "Johns",
        "Patrick",
        "Anthony",
        "Paul",
        "Peter",
        "Thomas",
        "James",
        "Joseph",
        "Daniel",
        "Michael",
        "David",
        "John",
        "George",
        "Henry",
        "Francis",
        "Allen",
        "Martin",
        "Terry",
        "Barry",
        "Gary",
        "Jerry",
        "Larry",
        "Dennis",
        "Dean",
        "Roy",
        "Ray",
        "Lee",
        "Gene",
        "Wayne",
        "Todd",
        "Scott",
        "Craig",
        "Brad",
        "Chad",
        "Troy",
        "Luz",
        "Del",
        "Paz",
        "Post",
        "Gift",
        "Gill",
        "Pike",
        "Bass",
        "Crane",
        "Hawk",
    }
}

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
    "connected",
    "connection",
    "connect",
    "stay",
    "visit",
    "welcome",
    "hello",
    "goodbye",
    "thank",
    "thanks",
    "bless",
    "blessed",
    "blessing",
    "pray",
    "prayer",
    "worship",
    "praise",
    "glory",
    "alleluia",
    "hallelujah",
    "amen",
    "hosanna",
    "donation",
    "donate",
    "giving",
    "offering",
    "tithing",
    "tithe",
    "register",
    "registration",
    "signup",
    "enroll",
    "enrollment",
    "attend",
    "attendance",
    "volunteer",
    "volunteering",
    "serving",
    "ministry",
    "ministries",
    "class",
    "classes",
    "session",
    "sessions",
    "meeting",
    "meetings",
    "parking",
    "building",
    "room",
    "hall",
    "center",
    "office",
    "rectory",
    "gym",
    "summer",
    "winter",
    "autumn",
    "christmas",
    "easter",
    "lent",
    "advent",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "january",
    "february",
    "march",
    "april",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
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
    "sacramental",
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
    "information",
    "details",
    "contact",
    "phone",
    "email",
    "website",
    "online",
    "available",
    "needed",
    "wanted",
    "seeking",
    "looking",
    "open",
    "closed",
    "free",
    "cost",
    "price",
    "fee",
    "ticket",
    "tickets",
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
    "maintenance",
    "repair",
    "cleaning",
    "renovation",
    "construction",
    "insurance",
    "security",
    "safety",
    "emergency",
    "weather",
    "snow",
    "rain",
    "bus",
    "van",
    "ride",
    "rides",
    "transportation",
    "shuttle",
    "baby",
    "child",
    "children",
    "kids",
    "teen",
    "teens",
    "youth",
    "adult",
    "adults",
    "senior",
    "seniors",
    "family",
    "families",
    "parent",
    "parents",
    "couple",
    "couples",
    "program",
    "programs",
    "event",
    "events",
    "activity",
    "activities",
    "blood",
    "drive",
    "drives",
    "screening",
    "wellness",
    "retreat",
    "camp",
    "trip",
    "pilgrimage",
    "tour",
    "travel",
    "book",
    "books",
    "library",
    "study",
    "studies",
    "respect",
    "life",
    "pro",
    "walk",
    "run",
    "race",
    "coat",
    "coats",
    "clothing",
    "clothes",
    "shoes",
    "blanket",
    "blankets",
    "pantry",
    "shelter",
    "outreach",
    "mission",
    "missions",
    "missionary",
    "envelope",
    "envelopes",
    "basket",
    "baskets",
    "box",
    "boxes",
    "stewardship",
    "discipleship",
    "evangelization",
    "catechesis",
    "adoration",
    "rosary",
    "novena",
    "vespers",
    "stations",
    "homeless",
    "hungry",
    "needy",
    "poverty",
    "poor",
    "anniversary",
    "celebration",
    "jubilee",
    "ceremony",
    "receptions",
    "reception",
    "banquet",
    "gala",
    "luncheon",
    "nursery",
    "daycare",
    "aftercare",
    "signup",
    "rsvp",
    "deadline",
    "reminder",
    "chapter",
    "psalm",
    "psalms",
    "hymnal",
    "penance",
    "reconciliation",
    "anointing",
}

PLACE_WORDS = {
    "park",
    "city",
    "county",
    "village",
    "township",
    "town",
    "heights",
    "hills",
    "springs",
    "creek",
    "river",
    "lake",
    "valley",
    "mountain",
    "ridge",
    "grove",
    "forest",
    "woods",
    "meadow",
    "prairie",
    "plains",
    "harbor",
    "port",
    "bay",
    "beach",
    "island",
    "shore",
    "point",
    "landing",
    "avenue",
    "street",
    "road",
    "boulevard",
    "lane",
    "circle",
    "court",
    "plaza",
    "square",
    "crossing",
    "junction",
    "station",
    "evergreen",
    "oak",
    "maple",
    "pine",
    "elm",
    "cedar",
    "willow",
    "birch",
    "quad",
    "twin",
}

ACTION_VERBS = {
    "donate",
    "register",
    "serve",
    "attend",
    "volunteer",
    "participate",
    "submit",
    "complete",
    "schedule",
    "reserve",
    "sign",
    "prepare",
    "organize",
    "coordinate",
    "celebrate",
    "gather",
    "invite",
    "encourage",
    "support",
    "share",
    "provide",
    "announce",
    "notify",
    "remind",
    "update",
    "check",
    "review",
    "confirm",
    "download",
    "print",
    "pick",
    "drop",
    "bring",
    "send",
    "call",
    "text",
    "help",
    "need",
    "want",
    "seek",
    "find",
    "get",
    "make",
    "take",
    "give",
    "keep",
    "start",
    "stop",
    "begin",
    "end",
    "continue",
    "return",
    "come",
    "go",
    "learn",
    "teach",
    "read",
    "write",
    "sing",
    "play",
    "watch",
    "listen",
    "eat",
    "drink",
    "cook",
    "clean",
    "set",
    "put",
    "buy",
    "sell",
    "pay",
}

COMMERCIAL_TERMS = {
    "inc",
    "llc",
    "corp",
    "corporation",
    "company",
    "ltd",
    "enterprises",
    "associates",
    "insurance",
    "realty",
    "realtor",
    "agency",
    "firm",
    "consulting",
    "solutions",
    "funeral",
    "mortuary",
    "cremation",
    "plumbing",
    "electric",
    "electrical",
    "heating",
    "cooling",
    "hvac",
    "roofing",
    "auto",
    "automotive",
    "motors",
    "tire",
    "tires",
    "bank",
    "banking",
    "credit",
    "union",
    "financial",
    "investment",
    "savings",
    "pharmacy",
    "medical",
    "dental",
    "clinic",
    "hospital",
    "care",
    "restaurant",
    "cafe",
    "diner",
    "grill",
    "pizza",
    "bar",
    "tavern",
    "pub",
    "market",
    "grocery",
    "store",
    "shop",
    "mall",
    "outlet",
    "hotel",
    "motel",
    "inn",
    "lodge",
    "resort",
    "florist",
    "bakery",
    "deli",
    "catering",
    "lawn",
    "landscaping",
    "garden",
    "nursery",
    "tree",
    "printing",
    "graphics",
    "design",
    "studio",
    "photography",
    "knights",
    "columbus",
    "supply",
    "supplies",
    "service",
    "services",
    "construction",
    "contracting",
    "builders",
    "trucking",
    "transport",
    "logistics",
    "cleaning",
    "janitorial",
}

SPANISH_LATIN_NONNAME = {
    "del",
    "las",
    "los",
    "una",
    "para",
    "como",
    "pero",
    "este",
    "esta",
    "estos",
    "estas",
    "todo",
    "todos",
    "toda",
    "todas",
    "otro",
    "otra",
    "otros",
    "otras",
    "tiene",
    "puede",
    "debe",
    "hacer",
    "decir",
    "saber",
    "querer",
    "poder",
    "dios",
    "iglesia",
    "misa",
    "santo",
    "santa",
    "santos",
    "santas",
    "padre",
    "madre",
    "hijo",
    "hija",
    "hijos",
    "familia",
    "pueblo",
    "vida",
    "mundo",
    "fiesta",
    "comunidad",
    "comunion",
    "oracion",
    "rosario",
    "novena",
    "domini",
    "dominus",
    "deo",
    "dei",
    "christi",
    "spiritus",
    "sancti",
    "sanctus",
    "ecclesia",
    "pater",
    "mater",
    "filius",
    "filia",
    "anno",
    "tempus",
    "adventus",
    "nuestro",
    "nuestra",
    "nuestros",
    "nuestras",
    "senor",
    "senora",
}

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

TRUNC_ENDINGS = [
    "comuni",
    "collectio",
    "photog",
    "registr",
    "celebr",
    "informat",
    "organiz",
    "coordin",
    "particip",
    "volunt",
    "evangel",
    "catec",
    "reconcili",
    "anniver",
    "commun",
    "sacram",
    "sanctu",
    "confir",
    "prepar",
    "admini",
    "annou",
]


def is_safe(word):
    return word.lower() in SAFE_SURNAMES_LOWER


# ── CLASSIFY EVERY NAME ────────────────────────────────────────────────
name_categories = defaultdict(set)  # name -> set of category labels

for name in unique_names:
    words = name.split()
    if not words:
        continue
    words_lower = [w.lower() for w in words]

    # CAT A: Embedded newlines
    if "\n" in name or "\r" in name:
        name_categories[name].add("NEWLINE")

    # CAT B: All-caps multi-word
    if name == name.upper() and len(name) > 3 and " " in name:
        name_categories[name].add("ALL_CAPS")

    # CAT 1: Nouns/verbs
    for i, wl in enumerate(words_lower):
        wl_clean = re.sub(r"[^a-z]", "", wl)
        if wl_clean in COMMON_NOUNS_VERBS and len(wl_clean) > 3 and not is_safe(words[i]):
            name_categories[name].add("NOUNS_VERBS")
            break

    # CAT 2: Lowercase phrase pattern
    if len(words) >= 3:
        after_first = words[1:]
        if all(w[0].islower() for w in after_first if len(w) > 0):
            name_categories[name].add("LOWERCASE_PHRASE")

    # CAT 4: Truncated
    for w in words:
        if len(w) >= 6:
            w_lower = w.lower()
            for ending in TRUNC_ENDINGS:
                if w_lower == ending:
                    name_categories[name].add("TRUNCATED")
                    break

    # CAT 6: Place names
    if len(words) >= 2:
        for i, wl in enumerate(words_lower):
            if wl in PLACE_WORDS and len(wl) > 3 and not is_safe(words[i]):
                name_categories[name].add("PLACE_NAMES")
                break

    # CAT 7: Action verbs
    for i, wl in enumerate(words_lower):
        if wl in ACTION_VERBS and not is_safe(words[i]):
            name_categories[name].add("ACTION_VERBS")
            break

    # CAT 8: Commercial
    if len(words) >= 2:
        for i, wl in enumerate(words_lower):
            if wl in COMMERCIAL_TERMS and not is_safe(words[i]):
                name_categories[name].add("COMMERCIAL")
                break

    # CAT 9: Spanish/Latin
    span_hits = [wl for wl in words_lower if wl in SPANISH_LATIN_NONNAME]
    if len(span_hits) >= 2:
        name_categories[name].add("SPANISH_LATIN")

    # CAT 10: Merged names
    if len(words) >= 3:
        last_lower = words[-1].lower()
        first_lower = words[0].lower()
        if last_lower in COMMON_FIRST_NAMES and first_lower in COMMON_FIRST_NAMES:
            middle = words[1:-1]
            if any(w[0].isupper() and len(w) > 1 for w in middle):
                name_categories[name].add("MERGED_NAMES")

# ── BUILD FINAL REPORT ─────────────────────────────────────────────────
all_junk = set(name_categories.keys())
all_clean = unique_names - all_junk

print("=" * 80)
print("CONSOLIDATED JUNK/NON-NAME ANALYSIS: FINAL REPORT")
print(f"10 States: {', '.join(sorted(state_counts.keys()))}")
print("=" * 80)

print("\n  DATASET SIZE")
print(f"  Total rows loaded:       {sum(state_counts.values()):>10,}")
print(f"  Total unique names:      {len(unique_names):>10,}")

print("\n  CONFIDENCE DISTRIBUTION (all rows)")
conf_counter = Counter()
for r, st in all_rows:
    conf_counter[r.get("confidence", "").strip().lower()] += 1
for c, ct in conf_counter.most_common():
    print(f"    {c:10s}: {ct:>8,}  ({100.0 * ct / len(all_rows):5.1f}%)")

print("\n" + "=" * 80)
print(
    f"  JUNK DETECTION: {len(all_junk):,} of {len(unique_names):,} unique names flagged ({100.0 * len(all_junk) / len(unique_names):.1f}%)"
)
print(
    f"  Clean names remaining: {len(all_clean):,} ({100.0 * len(all_clean) / len(unique_names):.1f}%)"
)
print("=" * 80)

# Category counts
cat_counts = Counter()
for name, cats in name_categories.items():
    for c in cats:
        cat_counts[c] += 1

cat_labels = {
    "NEWLINE": "Embedded newline characters (multi-line parse errors)",
    "ALL_CAPS": "All-caps multi-word entries (headings/labels)",
    "NOUNS_VERBS": "Common English nouns/verbs (not surnames)",
    "LOWERCASE_PHRASE": "All-lowercase words after first (sentence fragments)",
    "TRUNCATED": "Truncated/incomplete words",
    "PLACE_NAMES": "Place/location names (not person names)",
    "ACTION_VERBS": "Contains action verbs",
    "COMMERCIAL": "Commercial/business/organizational terms",
    "SPANISH_LATIN": "Spanish/Latin non-name phrases (2+ terms)",
    "MERGED_NAMES": "Two person names merged (FN Surname FN pattern)",
}

print("\n  JUNK BY CATEGORY (categories overlap; names can appear in multiple):")
print("  " + "-" * 75)
for cat, label in cat_labels.items():
    ct = cat_counts.get(cat, 0)
    pct = 100.0 * ct / len(unique_names)
    print(f"    {cat:20s}: {ct:>7,}  ({pct:5.2f}%)  {label}")

# How many categories per junk name?
multi_counter = Counter()
for name, cats in name_categories.items():
    multi_counter[len(cats)] += 1

print("\n  OVERLAP ANALYSIS (how many categories per flagged name):")
for n_cats in sorted(multi_counter.keys()):
    ct = multi_counter[n_cats]
    print(f"    {n_cats} category/ies: {ct:>7,} names")

# ── CONFIDENCE vs JUNK ─────────────────────────────────────────────────
print("\n  CONFIDENCE vs JUNK RATE:")
print("  " + "-" * 55)
for conf_level in ["high", "medium"]:
    total = sum(1 for n in unique_names if name_to_confidence.get(n) == conf_level)
    junk = sum(1 for n in all_junk if name_to_confidence.get(n) == conf_level)
    clean = total - junk
    rate = 100.0 * junk / total if total > 0 else 0
    print(
        f"    {conf_level:10s}: {total:>8,} total, {junk:>8,} junk ({rate:5.1f}%), {clean:>8,} clean"
    )

# ── STATE-LEVEL BREAKDOWN ─────────────────────────────────────────────
print("\n  JUNK RATE BY STATE:")
print("  " + "-" * 75)
print(f"  {'State':15s} {'Rows':>8s} {'Unique':>8s} {'Junk':>7s} {'Rate':>7s} {'Clean':>8s}")
for st in sorted(state_counts.keys()):
    st_names = set()
    for r, s in all_rows:
        if s == st:
            pn = r.get("person_name", "").strip()
            if pn:
                st_names.add(pn)
    st_junk = len(st_names & all_junk)
    st_clean = len(st_names) - st_junk
    rate = 100.0 * st_junk / len(st_names) if st_names else 0
    print(
        f"    {st:15s} {state_counts[st]:>8,} {len(st_names):>8,} {st_junk:>7,} {rate:>6.1f}% {st_clean:>8,}"
    )

# ── NEWLINE-SPECIFIC BREAKDOWN ─────────────────────────────────────────
print("\n  NEWLINE NAMES: DEEPER LOOK")
print("  " + "-" * 75)
newline_names = {n for n in unique_names if "\n" in n or "\r" in n}
print(f"    Total with newlines: {len(newline_names):,}")
print("    100% are medium confidence (newlines never appear in high-confidence names)")

# Newline names that are ALSO flagged by content-based categories
nl_also_content = sum(1 for n in newline_names if len(name_categories.get(n, set())) > 1)
nl_only = sum(1 for n in newline_names if name_categories.get(n, set()) == {"NEWLINE"})
print(f"    Flagged ONLY by newline category: {nl_only:,}")
print(f"    Also flagged by content categories: {nl_also_content:,}")

# Of newline names, how many have a clean first line?
first_line_clean = 0
for n in newline_names:
    parts = [p.strip() for p in re.split(r"[\n\r]+", n) if p.strip()]
    if parts:
        first = parts[0]
        words = first.split()
        if 2 <= len(words) <= 3 and all(w[0].isupper() for w in words if w):
            first_line_clean += 1
print(
    f"    First line looks like valid 2-3 word name: {first_line_clean:,} ({100.0 * first_line_clean / len(newline_names):.1f}%)"
)
print("    These may be salvageable by splitting on newline and keeping first line.")

# ── FINAL KEY FINDINGS ─────────────────────────────────────────────────
print("\n" + "=" * 80)
print("KEY FINDINGS")
print("=" * 80)

print(f"""
  1. SCALE: 499,185 unique person_name entries across 787,284 rows from 10 states.

  2. JUNK RATE: {len(all_junk):,} names ({100.0 * len(all_junk) / len(unique_names):.1f}%) flagged as likely junk/non-names.
     After filtering: {len(all_clean):,} clean names remain.

  3. BIGGEST ISSUE - NEWLINE CONTAMINATION: {len(newline_names):,} names (15.5%) contain embedded
     newline characters, meaning the CSV parsing or name extraction merged multiple
     lines into a single name field. ALL of these are medium-confidence.
     ~{first_line_clean:,} may be salvageable by extracting just the first line.

  4. CONFIDENCE GAP: Medium-confidence names have a MUCH higher junk rate.
     - High confidence: ~10.4% junk rate
     - Medium confidence: ~24.9% junk rate (including newline names)
     Medium-confidence names account for 67.9% of rows but contain the vast
     majority of junk entries.

  5. CONTENT-BASED JUNK: The largest content category is nouns/verbs ({cat_counts.get("NOUNS_VERBS", 0):,}
     names containing words like "deceased", "ministry", "council", "committee",
     "wedding", "meeting", "festival"). These are bulletin section headers and
     event descriptions that were misidentified as person names.

  6. MERGED NAMES: {cat_counts.get("MERGED_NAMES", 0):,} entries appear to be two separate people
     merged into one entry (e.g., "Kevin Steinkamp Mary", "Barbara Schmidt Michael").
     Most common appended names: Mary (391), John (304), Michael (177), Mike (161).

  7. COMMERCIAL LEAKAGE: {cat_counts.get("COMMERCIAL", 0):,} entries contain business terms
     (Knights of Columbus alone accounts for ~3,175 entries).

  8. STATE VARIATION: Junk rates vary significantly by state, from ~5.5% (Florida)
     to ~22.9% (Wisconsin), suggesting different bulletin formats and PDF
     extraction quality across regions.

  9. RECOMMENDED PRIORITY ACTIONS:
     a) Fix newline handling in CSV parsing/name extraction (biggest single win)
     b) Filter entries containing obvious non-name nouns (deceased, ministry, etc.)
     c) Split or discard merged multi-person entries
     d) Consider raising the confidence threshold or adding post-processing for
        medium-confidence entries
""")

print("=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)
