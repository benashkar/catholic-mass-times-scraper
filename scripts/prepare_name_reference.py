"""
prepare_name_reference.py

Downloads and prepares reference datasets for name validation:
  1. SSA baby names (1880–2024) — deduplicated first names with total frequency
  2. Census 2010 surnames (162K entries) — with frequency data

Also builds a curated non_names.txt — common English words that are NOT names.

Usage:
    python scripts/prepare_name_reference.py

Output:
    data/reference/ssa_first_names.csv   — ~100K unique first names
    data/reference/census_surnames.csv   — ~162K surnames
    data/reference/non_names.txt         — common English non-name words
"""

import csv
import io
import zipfile
from pathlib import Path

import requests

# Resolve project root (one level up from scripts/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"

SSA_URL = "https://www.ssa.gov/oact/babynames/names.zip"
# GitHub mirror of SSA data (fallback when SSA blocks automated downloads)
SSA_MIRROR_URL = "https://raw.githubusercontent.com/hackerb9/ssa-baby-names/main/alldata.txt"
CENSUS_URL = "https://www2.census.gov/topics/genealogy/2010surnames/names.zip"

# Common English words that should never be treated as person names.
# Curated from bulletin junk analysis — words that pass capitalization checks
# but are clearly not names.
NON_NAME_WORDS = sorted(
    {
        # Prepositions / conjunctions / articles
        "the",
        "and",
        "for",
        "from",
        "with",
        "that",
        "this",
        "are",
        "was",
        "has",
        "have",
        "had",
        "their",
        "there",
        "where",
        "when",
        "what",
        "which",
        "also",
        "than",
        "them",
        "not",
        "out",
        "who",
        "how",
        "its",
        "may",
        "can",
        "you",
        "your",
        "will",
        "but",
        "all",
        "our",
        "been",
        "being",
        "into",
        "over",
        "under",
        "about",
        "after",
        "before",
        # Action verbs (bulletin event descriptions)
        "donate",
        "register",
        "attend",
        "schedule",
        "update",
        "celebrate",
        "hosting",
        "join",
        "call",
        "email",
        "visit",
        "contact",
        "please",
        "welcome",
        "support",
        "help",
        "need",
        "serve",
        "bring",
        "submit",
        "prepare",
        "complete",
        "provide",
        "receive",
        # Common nouns (bulletin vocabulary)
        "festival",
        "hospital",
        "clinic",
        "county",
        "height",
        "heights",
        "church",
        "parish",
        "school",
        "center",
        "hall",
        "room",
        "chapel",
        "office",
        "street",
        "avenue",
        "boulevard",
        "road",
        "drive",
        "lane",
        "table",
        "prayer",
        "music",
        "director",
        "ministers",
        "ministry",
        "council",
        "meeting",
        "committee",
        "board",
        "group",
        "team",
        "club",
        "program",
        "service",
        "services",
        "class",
        "classes",
        "event",
        "events",
        "dinner",
        "lunch",
        "breakfast",
        "picnic",
        "sale",
        "store",
        "shop",
        "bank",
        "kitchen",
        "pantry",
        "food",
        "soup",
        "thrift",
        "drive",
        "raffle",
        "bazaar",
        "auction",
        # Days and months
        "sunday",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
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
        # Religious terms
        "mass",
        "masses",
        "confession",
        "communion",
        "lent",
        "easter",
        "christmas",
        "holy",
        "blessed",
        "sacred",
        "saint",
        "rosary",
        "adoration",
        "benediction",
        "sacrament",
        "eucharist",
        "gospel",
        "amen",
        "liturgy",
        "novena",
        "retreat",
        "bible",
        "scripture",
        "baptism",
        "confirmation",
        "marriage",
        "funeral",
        "anointing",
        # Organization words
        "columbus",
        "columbian",
        "knights",
        "knight",
        "society",
        "council",
        "association",
        "foundation",
        "corporation",
        "insurance",
        "attorney",
        # Food/drink (bulletin ads)
        "salad",
        "pasta",
        "pizza",
        "chicken",
        "sausage",
        "pork",
        "beef",
        "taco",
        "tamale",
        "tamales",
        "chocolate",
        "dessert",
        "recipe",
        # Commercial/business
        "plumbing",
        "roofing",
        "heating",
        "cooling",
        "dental",
        "pharmacy",
        "flooring",
        "carpet",
        "landscaping",
        "electrician",
        "remodeling",
        "discount",
        "coupon",
        "insurance",
        "realtor",
        # Calendar/time
        "daily",
        "weekly",
        "monthly",
        "annual",
        "weekday",
        "weekend",
        "morning",
        "evening",
        "night",
        "today",
        "tomorrow",
        "yesterday",
        # Misc
        "bulletin",
        "newsletter",
        "online",
        "download",
        "facebook",
        "instagram",
        "twitter",
        "website",
        "flocknote",
        "donation",
        "donations",
        "contribution",
        "volunteer",
        "volunteers",
        "needed",
        "upcoming",
        "recently",
        "connected",
        "alert",
    }
)


def download_file(url, desc="file"):
    """Download a file and return bytes content."""
    print(f"Downloading {desc} from {url} ...")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    resp = requests.get(url, timeout=60, headers=headers)
    resp.raise_for_status()
    print(f"  Downloaded {len(resp.content) / 1024 / 1024:.1f} MB")
    return resp.content


def prepare_ssa_names(zip_bytes):
    """Parse SSA baby names zip and deduplicate across all years.

    Each file inside the zip is yobYYYY.txt with format: name,sex,count
    We sum counts across all years per name (case-insensitive) and write
    a CSV with columns: name, total_count, rank, male_count, female_count, male_ratio.

    Gender data enables husband+wife couple detection:
    If male_ratio > 0.90, the name is almost exclusively male.
    If male_ratio < 0.10, the name is almost exclusively female.
    """
    print("\nProcessing SSA baby names...")
    name_counts = {}  # lowercase name -> {display_name, total_count, male_count, female_count}

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        txt_files = [f for f in zf.namelist() if f.startswith("yob") and f.endswith(".txt")]
        print(f"  Found {len(txt_files)} year files in SSA zip")

        for fname in sorted(txt_files):
            with zf.open(fname) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"))
                for row in reader:
                    if len(row) < 3:
                        continue
                    name, sex, count = row[0], row[1], int(row[2])
                    key = name.lower()
                    if key not in name_counts:
                        name_counts[key] = {"name": name, "total_count": 0,
                                            "male_count": 0, "female_count": 0}
                    name_counts[key]["total_count"] += count
                    if sex.upper() == "M":
                        name_counts[key]["male_count"] += count
                    else:
                        name_counts[key]["female_count"] += count

    # Sort by total count descending and assign rank
    sorted_names = sorted(name_counts.values(), key=lambda x: -x["total_count"])
    out_path = REFERENCE_DIR / "ssa_first_names.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "total_count", "rank", "male_count", "female_count", "male_ratio"])
        for rank, entry in enumerate(sorted_names, 1):
            total = entry["total_count"]
            male_ratio = round(entry["male_count"] / total, 4) if total > 0 else 0.5
            writer.writerow([
                entry["name"], entry["total_count"], rank,
                entry["male_count"], entry["female_count"], male_ratio,
            ])

    print(f"  Wrote {len(sorted_names):,} unique first names to {out_path}")
    return len(sorted_names)


def prepare_ssa_names_from_text(text_bytes):
    """Parse SSA alldata.txt (GitHub mirror format): name,sex,count,year

    Same deduplication logic as prepare_ssa_names() but reads a flat text file
    instead of a zip of per-year files. Includes gender data (male_count,
    female_count, male_ratio) for couple detection.
    """
    print("\nProcessing SSA baby names (from GitHub mirror)...")
    name_counts = {}

    text = text_bytes.decode("utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < 3:
            continue
        name = parts[0]
        sex = parts[1] if len(parts) > 1 else ""
        count = int(parts[2])
        key = name.lower()
        if key not in name_counts:
            name_counts[key] = {"name": name, "total_count": 0,
                                "male_count": 0, "female_count": 0}
        name_counts[key]["total_count"] += count
        if sex.upper() == "M":
            name_counts[key]["male_count"] += count
        else:
            name_counts[key]["female_count"] += count

    sorted_names = sorted(name_counts.values(), key=lambda x: -x["total_count"])
    out_path = REFERENCE_DIR / "ssa_first_names.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "total_count", "rank", "male_count", "female_count", "male_ratio"])
        for rank, entry in enumerate(sorted_names, 1):
            total = entry["total_count"]
            male_ratio = round(entry["male_count"] / total, 4) if total > 0 else 0.5
            writer.writerow([
                entry["name"], entry["total_count"], rank,
                entry["male_count"], entry["female_count"], male_ratio,
            ])

    print(f"  Wrote {len(sorted_names):,} unique first names to {out_path}")
    return len(sorted_names)


def prepare_census_surnames(zip_bytes):
    """Parse Census 2010 surnames zip.

    The zip contains Names_2010Census.csv with columns:
    name, rank, count, prop100k, cum_prop100k, pctwhite, pctblack, ...
    """
    print("\nProcessing Census 2010 surnames...")

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # Find the CSV file in the zip
        csv_files = [f for f in zf.namelist() if f.lower().endswith(".csv")]
        if not csv_files:
            print("  ERROR: No CSV file found in Census zip")
            return 0

        csv_name = csv_files[0]
        print(f"  Reading {csv_name}")

        with zf.open(csv_name) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
            rows = []
            for row in reader:
                name = row.get("name", "").strip()
                if not name:
                    continue
                # Census names are ALL CAPS — title-case them
                display_name = name.title()
                count = row.get("count", "0").replace(",", "")
                try:
                    count = int(count)
                except ValueError:
                    count = 0
                rank = row.get("rank", "0")
                try:
                    rank = int(rank)
                except ValueError:
                    rank = 0
                rows.append(
                    {
                        "name": display_name,
                        "count": count,
                        "rank": rank,
                    }
                )

    out_path = REFERENCE_DIR / "census_surnames.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "count", "rank"])
        for row in rows:
            writer.writerow([row["name"], row["count"], row["rank"]])

    print(f"  Wrote {len(rows):,} surnames to {out_path}")
    return len(rows)


def write_non_names():
    """Write the curated non-name words list."""
    out_path = REFERENCE_DIR / "non_names.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        for word in NON_NAME_WORDS:
            f.write(word + "\n")
    print(f"\nWrote {len(NON_NAME_WORDS)} non-name words to {out_path}")


def main():
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)

    # Download SSA baby names (try official URL first, then GitHub mirror)
    ssa_count = 0
    try:
        ssa_bytes = download_file(SSA_URL, "SSA baby names (official)")
        ssa_count = prepare_ssa_names(ssa_bytes)
    except Exception as e:
        print(f"  Official SSA download failed ({e}), trying GitHub mirror...")
        try:
            mirror_bytes = download_file(SSA_MIRROR_URL, "SSA baby names (GitHub mirror)")
            ssa_count = prepare_ssa_names_from_text(mirror_bytes)
        except Exception as e2:
            print(f"ERROR downloading SSA names from mirror: {e2}")
            # Last resort: check if alldata.txt was manually placed
            local_alldata = REFERENCE_DIR / "ssa_alldata.txt"
            if local_alldata.exists():
                print(f"  Found local {local_alldata}, processing...")
                ssa_count = prepare_ssa_names_from_text(local_alldata.read_bytes())
            else:
                print(
                    "  No SSA data available. Place names.zip or ssa_alldata.txt "
                    "in data/reference/ and re-run."
                )

    # Download Census surnames
    try:
        census_bytes = download_file(CENSUS_URL, "Census 2010 surnames")
        census_count = prepare_census_surnames(census_bytes)
    except Exception as e:
        print(f"ERROR downloading/processing Census surnames: {e}")
        census_count = 0

    # Write non-name words
    write_non_names()

    print(f"\n{'='*50}")
    print("Reference data preparation complete:")
    print(f"  SSA first names:    {ssa_count:,}")
    print(f"  Census surnames:    {census_count:,}")
    print(f"  Non-name words:     {len(NON_NAME_WORDS)}")
    print(f"  Output directory:   {REFERENCE_DIR}")


if __name__ == "__main__":
    main()
