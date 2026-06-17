import csv
import os
import re
import sys
from collections import Counter, defaultdict

# Force UTF-8 output on Windows
sys.stdout.reconfigure(encoding="utf-8")

# ── CONFIG ──────────────────────────────────────────────────────────────
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

# ── LOAD DATA ───────────────────────────────────────────────────────────
all_rows = []
state_counts = {}
for st in STATES:
    path = os.path.join(BASE, st, "bulletin_names.csv")
    if not os.path.exists(path):
        print(f"WARNING: {path} not found, skipping")
        continue
    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        state_counts[st] = len(rows)
        all_rows.extend([(r, st) for r in rows])

print(f"{'=' * 80}")
print(f"JUNK / NON-NAME ANALYSIS ACROSS {len(state_counts)} STATES")
print(f"{'=' * 80}")
print("\nRows loaded per state:")
for st, ct in sorted(state_counts.items()):
    print(f"  {st:15s}: {ct:>7,}")
print(f"  {'TOTAL':15s}: {sum(state_counts.values()):>7,}")

# Unique names and metadata
unique_names = set()
name_to_confidence = {}
name_to_states = defaultdict(set)
for r, st in all_rows:
    pn = r.get("person_name", "").strip()
    if pn:
        unique_names.add(pn)
        name_to_confidence[pn] = r.get("confidence", "").strip().lower()
        name_to_states[pn].add(st)

print(f"\nTotal unique person_name values: {len(unique_names):,}")

# ── CONFIDENCE BREAKDOWN ────────────────────────────────────────────────
conf_counter = Counter()
for r, st in all_rows:
    conf_counter[r.get("confidence", "").strip().lower()] += 1

print(f"\n{'─' * 80}")
print("CONFIDENCE LEVEL BREAKDOWN (all rows):")
print(f"{'─' * 80}")
total_rows = len(all_rows)
for c, ct in conf_counter.most_common():
    pct = 100.0 * ct / total_rows
    print(f"  {c:15s}: {ct:>7,}  ({pct:5.1f}%)")

# ── JUNK DETECTION PATTERNS ────────────────────────────────────────────

# Category 1: Common English nouns/verbs that are NOT plausible surnames
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
    "spring",
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
    "rest",
    "peace",
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
    "confetti",
    "decorations",
    "setup",
    "cleanup",
    "signup",
    "rsvp",
    "deadline",
    "reminder",
    "chapter",
    "verse",
    "psalm",
    "psalms",
    "hymnal",
    "penance",
    "reconciliation",
    "anointing",
}

# Place-related words
PLACE_WORDS = {
    "park",
    "city",
    "county",
    "village",
    "township",
    "town",
    "heights",
    "hills",
    "hill",
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
    "field",
    "fields",
    "prairie",
    "plain",
    "plains",
    "harbor",
    "port",
    "bay",
    "beach",
    "island",
    "shore",
    "point",
    "landing",
    "north",
    "south",
    "east",
    "west",
    "northern",
    "southern",
    "eastern",
    "western",
    "central",
    "upper",
    "lower",
    "greater",
    "metro",
    "downtown",
    "suburban",
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

# Action verbs
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

# Spanish/Latin non-name terms
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

# Common first names for merged-name detection
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
    "eugene",
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
    "carl",
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
    "joe",
    "ted",
    "tim",
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
    "repair",
    "maintenance",
    "cleaning",
    "janitorial",
}

# Truncated word patterns: words ending in unusual incomplete suffixes
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
    "steward",
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

# Known real names that would false-positive on our checks
SAFE_SURNAMES = {
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
    "Church",
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
    "De La Cruz",
    "Del",
    "Delos",
    "Dela",
    "Palm",
    "Palms",
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
    "Pinto",
    "Gill",
    "Pike",
    "Bass",
    "Carp",
    "Crane",
    "Hawk",
    "Blood",
    "Child",
    "Mann",
    "Bond",
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
    "Bland",
    "Bright",
    "Moody",
    "Fry",
    "Bake",
    "Cook",
    "Boyle",
    "Sell",
    "Pay",
    "Buy",
    "Bury",
    "Lamb",
    "Palm",
    "Ash",
    "Oak",
    "Birch",
    "Pine",
    "Elm",
    "Willow",
    "Rowan",
    "Frost",
    "Burns",
    "Burn",
    "Lake",
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
    "Christian",
    "Johns",
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
    "Dale",
    "Todd",
    "Scott",
    "Craig",
    "Brad",
    "Chad",
    "Troy",
    "Luz",
    "Del",
    "Paz",
    "Cash",
    "Sale",
    "Post",
    "Gift",
}
SAFE_SURNAMES_LOWER = {s.lower() for s in SAFE_SURNAMES}

# ── RUN DETECTION ───────────────────────────────────────────────────────
categories = {
    "01_NOUNS_VERBS": {
        "desc": "Common English nouns/verbs (not plausible surnames)",
        "matches": {},
        "trigger_words": defaultdict(list),
    },
    "02_LOWERCASE_PHRASE": {
        "desc": "All-lowercase words after first (phrase pattern)",
        "matches": {},
        "trigger_words": defaultdict(list),
    },
    "03_SINGLE_CHAR_MID": {
        "desc": "Single non-initial character in middle positions",
        "matches": {},
        "trigger_words": defaultdict(list),
    },
    "04_TRUNCATED": {
        "desc": "Truncated/incomplete words",
        "matches": {},
        "trigger_words": defaultdict(list),
    },
    "05_NUMBERS_SPECIAL": {
        "desc": "Contains numbers or special characters",
        "matches": {},
        "trigger_words": defaultdict(list),
    },
    "06_PLACE_NAMES": {
        "desc": "Likely place names, not person names",
        "matches": {},
        "trigger_words": defaultdict(list),
    },
    "07_ACTION_VERBS": {
        "desc": "Contains action verbs",
        "matches": {},
        "trigger_words": defaultdict(list),
    },
    "08_COMMERCIAL": {
        "desc": "Commercial/business terms",
        "matches": {},
        "trigger_words": defaultdict(list),
    },
    "09_SPANISH_LATIN": {
        "desc": "Spanish/Latin non-name phrases",
        "matches": {},
        "trigger_words": defaultdict(list),
    },
    "10_MERGED_NAMES": {
        "desc": "Two names merged (first name as last word)",
        "matches": {},
        "trigger_words": defaultdict(list),
    },
}


def is_safe_as_surname(word):
    """Check if a word that matches a pattern is actually a known surname."""
    return word.lower() in SAFE_SURNAMES_LOWER


for name in unique_names:
    words = name.split()
    if not words:
        continue
    words_lower = [w.lower() for w in words]

    # Cat 1: Nouns/verbs -- flag if ANY word matches AND it is NOT a known safe surname
    for i, wl in enumerate(words_lower):
        wl_clean = re.sub(r"[^a-z]", "", wl)
        if wl_clean in COMMON_NOUNS_VERBS and len(wl_clean) > 3:
            # Dont flag if this word is a known surname
            if not is_safe_as_surname(words[i]):
                categories["01_NOUNS_VERBS"]["matches"][name] = True
                categories["01_NOUNS_VERBS"]["trigger_words"][name].append(wl_clean)

    # Cat 2: Lowercase phrase pattern (all words after first are lowercase, >= 3 words)
    if len(words) >= 3:
        after_first = words[1:]
        if all(w[0].islower() for w in after_first if len(w) > 0):
            categories["02_LOWERCASE_PHRASE"]["matches"][name] = True
            categories["02_LOWERCASE_PHRASE"]["trigger_words"][name].append(
                "all-lowercase-after-first"
            )

    # Cat 3: Single char in middle that is not an uppercase letter (not an initial)
    if len(words) >= 3:
        for i, w in enumerate(words[1:-1], 1):
            if len(w) == 1 and (not w.isalpha() or w.islower()):
                categories["03_SINGLE_CHAR_MID"]["matches"][name] = True
                categories["03_SINGLE_CHAR_MID"]["trigger_words"][name].append(
                    f"char='{w}' at pos {i}"
                )

    # Cat 4: Truncated words
    for w in words:
        if len(w) >= 6:
            w_lower = w.lower()
            for ending in TRUNC_ENDINGS:
                if w_lower == ending or (w_lower.endswith(ending) and len(w_lower) == len(ending)):
                    categories["04_TRUNCATED"]["matches"][name] = True
                    categories["04_TRUNCATED"]["trigger_words"][name].append(
                        f"'{w}' looks truncated"
                    )
                    break

    # Cat 5: Numbers or special characters
    triggers5 = []
    if re.search(r"[0-9]", name):
        triggers5.append("has-number")
    special = re.findall(r"[^a-zA-Z\s.\-\x27,]", name)
    if special:
        triggers5.append(f"special-chars: {''.join(set(special))}")
    if triggers5:
        categories["05_NUMBERS_SPECIAL"]["matches"][name] = True
        categories["05_NUMBERS_SPECIAL"]["trigger_words"][name].extend(triggers5)

    # Cat 6: Place names -- only if not a known surname
    if len(words) >= 2:
        place_hits = []
        for i, wl in enumerate(words_lower):
            if wl in PLACE_WORDS and len(wl) > 3 and not is_safe_as_surname(words[i]):
                place_hits.append(wl)
        if place_hits:
            categories["06_PLACE_NAMES"]["matches"][name] = True
            categories["06_PLACE_NAMES"]["trigger_words"][name].extend(place_hits)

    # Cat 7: Action verbs -- only if not a known surname
    verb_hits = []
    for i, wl in enumerate(words_lower):
        if wl in ACTION_VERBS and not is_safe_as_surname(words[i]):
            verb_hits.append(wl)
    if verb_hits:
        categories["07_ACTION_VERBS"]["matches"][name] = True
        categories["07_ACTION_VERBS"]["trigger_words"][name].extend(verb_hits)

    # Cat 8: Commercial terms -- only if not a known surname
    if len(words) >= 2:
        comm_hits = []
        for i, wl in enumerate(words_lower):
            if wl in COMMERCIAL_TERMS and not is_safe_as_surname(words[i]):
                comm_hits.append(wl)
        if comm_hits:
            categories["08_COMMERCIAL"]["matches"][name] = True
            categories["08_COMMERCIAL"]["trigger_words"][name].extend(comm_hits)

    # Cat 9: Spanish/Latin non-name -- need >= 2 matches to flag
    span_hits = [wl for wl in words_lower if wl in SPANISH_LATIN_NONNAME]
    if len(span_hits) >= 2:
        categories["09_SPANISH_LATIN"]["matches"][name] = True
        categories["09_SPANISH_LATIN"]["trigger_words"][name].extend(span_hits)

    # Cat 10: Merged names -- first name appearing as last word in 3+ word name
    if len(words) >= 3:
        last_word_lower = words[-1].lower()
        first_word_lower = words[0].lower()
        if last_word_lower in COMMON_FIRST_NAMES and first_word_lower in COMMON_FIRST_NAMES:
            # Check middle word(s) look like a surname
            middle = words[1:-1]
            middle_looks_like_name = any(w[0].isupper() and len(w) > 1 for w in middle)
            if middle_looks_like_name:
                categories["10_MERGED_NAMES"]["matches"][name] = True
                categories["10_MERGED_NAMES"]["trigger_words"][name].append(
                    f"'{words[0]} ... {words[-1]}' both common first names"
                )

# ── OUTPUT RESULTS ──────────────────────────────────────────────────────
print(f"\n{'=' * 80}")
print("JUNK DETECTION RESULTS BY CATEGORY")
print(f"{'=' * 80}")

all_junk = set()
for cat_key in sorted(categories.keys()):
    cat = categories[cat_key]
    matches = cat["matches"]
    all_junk.update(matches.keys())

    print(f"\n{'─' * 80}")
    print(f"CATEGORY {cat_key}: {cat['desc']}")
    print(f"  Unique names matching: {len(matches):,}")
    print(f"{'─' * 80}")

    # Show up to 15 examples with trigger words, sorted by trigger variety
    sorted_matches = sorted(matches.keys())[:15]
    for nm in sorted_matches:
        triggers = cat["trigger_words"].get(nm, [])
        trigger_str = ", ".join(triggers[:5])
        conf = name_to_confidence.get(nm, "?")
        print(f"  [{conf:6s}] {nm:55s} <-- {trigger_str}")

# ── CONFIDENCE vs JUNK RATE ─────────────────────────────────────────────
print(f"\n{'=' * 80}")
print("CONFIDENCE vs JUNK RATE ANALYSIS")
print(f"{'=' * 80}")

conf_total = defaultdict(int)
conf_junk = defaultdict(int)
for nm in unique_names:
    conf = name_to_confidence.get(nm, "?")
    conf_total[conf] += 1
    if nm in all_junk:
        conf_junk[conf] += 1

print(f"\n  {'Confidence':<15s} {'Total Unique':>12s} {'Junk':>8s} {'Junk Rate':>10s}")
print(f"  {'─' * 50}")
for conf in sorted(conf_total.keys()):
    total = conf_total[conf]
    junk = conf_junk[conf]
    rate = 100.0 * junk / total if total > 0 else 0
    print(f"  {conf:<15s} {total:>12,} {junk:>8,} {rate:>9.1f}%")

# ── SUMMARY ─────────────────────────────────────────────────────────────
print(f"\n{'=' * 80}")
print("OVERALL SUMMARY")
print(f"{'=' * 80}")
total_r = sum(state_counts.values())
print(f"\n  Total rows across {len(state_counts)} states:    {total_r:>10,}")
print(f"  Total unique names:                  {len(unique_names):>10,}")
print(f"  Total flagged as likely junk:         {len(all_junk):>10,}")
pct_junk = 100.0 * len(all_junk) / len(unique_names) if unique_names else 0
print(f"  Junk rate (of unique names):          {pct_junk:>9.1f}%")
print(f"  Clean names remaining:                {len(unique_names) - len(all_junk):>10,}")

print("\n  Junk breakdown by category:")
for cat_key in sorted(categories.keys()):
    cat = categories[cat_key]
    ct = len(cat["matches"])
    pct = 100.0 * ct / len(unique_names) if unique_names else 0
    print(f"    {cat_key:30s}: {ct:>6,} names ({pct:5.2f}%)")

# ── MULTI-CATEGORY JUNK ────────────────────────────────────────────────
print(f"\n{'─' * 80}")
print("NAMES FLAGGED IN MULTIPLE CATEGORIES (highest-confidence junk):")
print(f"{'─' * 80}")
name_cat_count = Counter()
name_cats = defaultdict(list)
for cat_key in sorted(categories.keys()):
    for nm in categories[cat_key]["matches"]:
        name_cat_count[nm] += 1
        name_cats[nm].append(cat_key.split("_", 1)[1])

multi = [(nm, ct) for nm, ct in name_cat_count.items() if ct >= 3]
multi.sort(key=lambda x: -x[1])
print(f"\n  Found {len(multi)} names flagged in 3+ categories:\n")
for nm, ct in multi[:30]:
    cats = ", ".join(name_cats[nm])
    print(f"  [{ct} cats] {nm:55s} ({cats})")

single_cat = sum(1 for v in name_cat_count.values() if v == 1)
two_cat = sum(1 for v in name_cat_count.values() if v == 2)
three_plus = sum(1 for v in name_cat_count.values() if v >= 3)
print(f"\n  Names in 1 category only:  {single_cat:,}")
print(f"  Names in 2 categories:     {two_cat:,}")
print(f"  Names in 3+ categories:    {three_plus:,}")

# ── TOP TRIGGER WORDS ──────────────────────────────────────────────────
print(f"\n{'─' * 80}")
print("TOP 40 TRIGGER WORDS (most frequently matched):")
print(f"{'─' * 80}")
all_triggers = Counter()
for cat_key in categories:
    for nm, triggers in categories[cat_key]["trigger_words"].items():
        for t in triggers:
            all_triggers[t] += 1
for word, ct in all_triggers.most_common(40):
    print(f"  {word:40s}: {ct:>5,} names")

# ── RANDOM SAMPLE OF JUNK FOR REVIEW ───────────────────────────────────
import random  # noqa: E402

random.seed(42)
print(f"\n{'─' * 80}")
print("RANDOM SAMPLE: 30 junk entries for manual review")
print(f"{'─' * 80}")
junk_list = sorted(all_junk)
sample = random.sample(junk_list, min(30, len(junk_list)))
for nm in sample:
    conf = name_to_confidence.get(nm, "?")
    cats = name_cats.get(nm, [])
    print(f"  [{conf:6s}] {nm:55s} cats: {', '.join(cats)}")

# ── STATE-LEVEL JUNK RATES ─────────────────────────────────────────────
print(f"\n{'─' * 80}")
print("JUNK RATE BY STATE:")
print(f"{'─' * 80}")
for st in sorted(state_counts.keys()):
    st_names = set()
    st_junk = 0
    for r, s in all_rows:
        if s == st:
            pn = r.get("person_name", "").strip()
            if pn:
                st_names.add(pn)
    st_junk = len(st_names & all_junk)
    st_total = len(st_names)
    rate = 100.0 * st_junk / st_total if st_total > 0 else 0
    print(f"  {st:15s}: {st_total:>6,} unique names, {st_junk:>5,} junk ({rate:5.1f}%)")

print(f"\n{'=' * 80}")
print("ANALYSIS COMPLETE")
print(f"{'=' * 80}")
