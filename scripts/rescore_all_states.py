"""
Rescore all existing bulletin_names.csv files with the new numeric confidence scoring.

Reads each state's CSV, computes score_name_confidence() for every row,
adds/updates the confidence_score column, removes rows below threshold,
and removes org-pattern names (containing "of", "for", "de").

Usage:
    python scripts/rescore_all_states.py                    # process all states
    python scripts/rescore_all_states.py --dry-run          # preview without modifying files
    python scripts/rescore_all_states.py --threshold 0.30   # custom threshold (default 0.25)
    python scripts/rescore_all_states.py illinois texas      # specific states only
"""

import argparse
import csv
import sys
from pathlib import Path

# Add project root to path so we can import from run_bulletin_scraper
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from run_bulletin_scraper import (
    score_name_confidence,
    confidence_label,
    split_merged_name,
    clean_extracted_name,
    is_valid_name,
)


# Org-pattern prepositions to detect org names
ORG_PREPS = [" of ", " for ", " de "]

CSV_FIELDNAMES = [
    "church_name",
    "church_slug",
    "city",
    "church_url",
    "pdf_file",
    "pdf_url",
    "pdf_date",
    "person_name",
    "title",
    "first_name",
    "middle_name",
    "last_name",
    "role",
    "category",
    "confidence",
    "confidence_score",
    "context",
]


def is_org_pattern(name):
    """Check if a name matches an organizational pattern like 'Knights of Columbus'."""
    name_lower = name.lower()
    return any(prep in name_lower for prep in ORG_PREPS)


def process_state(state_dir, threshold=0.25, dry_run=False):
    """Process a single state's bulletin_names.csv.

    Returns (state_name, rows_before, rows_after, org_removed, score_removed).
    """
    csv_path = state_dir / "bulletin_names.csv"
    if not csv_path.exists():
        return None

    state_name = state_dir.name

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    rows_before = len(rows)
    kept = []
    org_removed = 0
    score_removed = 0
    cleaned_removed = 0

    for row in rows:
        person_name = row.get("person_name", "")

        # Re-clean name
        cleaned = clean_extracted_name(person_name)
        if not cleaned or not is_valid_name(cleaned):
            cleaned_removed += 1
            continue

        # Update person_name if cleaning changed it
        if cleaned != person_name:
            row["person_name"] = cleaned

        # Try splitting merged names
        split = split_merged_name(row["person_name"])
        if split != row["person_name"]:
            row["person_name"] = split

        # Remove org patterns (user request: remove all "X of Y" etc.)
        if is_org_pattern(row["person_name"]):
            org_removed += 1
            continue

        # Score the name
        conf_score = score_name_confidence(
            row["person_name"],
            category=row.get("category", ""),
            role=row.get("role", ""),
            title=row.get("title", ""),
        )

        # Remove below threshold
        if conf_score < threshold:
            score_removed += 1
            continue

        row["confidence_score"] = f"{conf_score:.2f}"
        row["confidence"] = confidence_label(conf_score)
        kept.append(row)

    rows_after = len(kept)

    if not dry_run and kept:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(kept)

    return {
        "state": state_name,
        "before": rows_before,
        "after": rows_after,
        "cleaned": cleaned_removed,
        "org_removed": org_removed,
        "score_removed": score_removed,
        "total_removed": rows_before - rows_after,
    }


def main():
    parser = argparse.ArgumentParser(description="Rescore all bulletin name CSVs")
    parser.add_argument("states", nargs="*", help="Specific states to process (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without modifying files")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.25,
        help="Minimum confidence score to keep (default: 0.25)",
    )
    args = parser.parse_args()

    output_dir = PROJECT_ROOT / "data" / "output"
    if not output_dir.exists():
        print(f"[ERR] Output directory not found: {output_dir}")
        sys.exit(1)

    if args.states:
        state_dirs = [output_dir / s for s in args.states]
        state_dirs = [d for d in state_dirs if d.is_dir()]
    else:
        state_dirs = sorted([d for d in output_dir.iterdir() if d.is_dir()])

    if args.dry_run:
        print("=== DRY RUN — no files will be modified ===\n")

    print(f"Threshold: {args.threshold}")
    print(f"States to process: {len(state_dirs)}\n")

    total_before = 0
    total_after = 0
    total_org = 0
    total_score = 0
    total_cleaned = 0

    print(
        f"{'State':<20} {'Before':>10} {'After':>10} {'Cleaned':>10} {'Org Rm':>10} {'Score Rm':>10} {'Total Rm':>10} {'Pct':>7}"
    )
    print("-" * 97)

    for state_dir in state_dirs:
        result = process_state(state_dir, threshold=args.threshold, dry_run=args.dry_run)
        if result is None:
            continue

        pct = (result["total_removed"] / result["before"] * 100) if result["before"] > 0 else 0
        print(
            f"{result['state']:<20} "
            f"{result['before']:>10,} "
            f"{result['after']:>10,} "
            f"{result['cleaned']:>10,} "
            f"{result['org_removed']:>10,} "
            f"{result['score_removed']:>10,} "
            f"{result['total_removed']:>10,} "
            f"{pct:>6.1f}%"
        )

        total_before += result["before"]
        total_after += result["after"]
        total_org += result["org_removed"]
        total_score += result["score_removed"]
        total_cleaned += result["cleaned"]

    print("-" * 97)
    total_removed = total_before - total_after
    pct = (total_removed / total_before * 100) if total_before > 0 else 0
    print(
        f"{'TOTAL':<20} "
        f"{total_before:>10,} "
        f"{total_after:>10,} "
        f"{total_cleaned:>10,} "
        f"{total_org:>10,} "
        f"{total_score:>10,} "
        f"{total_removed:>10,} "
        f"{pct:>6.1f}%"
    )

    if args.dry_run:
        print("\n[OK] Dry run complete — no files were modified.")
    else:
        print(f"\n[OK] Rescored {len(state_dirs)} states. Removed {total_removed:,} rows total.")


if __name__ == "__main__":
    main()
