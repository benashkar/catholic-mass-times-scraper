"""One-time script to fix church data quality issues in bulletin_names.csv files.

1. Fill missing city values from church_details.jsonl (authoritative source)
2. Consolidate true duplicate slugs (same church, same city, different slug variants)
"""

import csv
import json
import os
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "output")


def build_slug_lookup():
    """Build slug -> {city, name} from all church_details.jsonl files."""
    lookup = {}
    for state in sorted(os.listdir(DATA_DIR)):
        path = os.path.join(DATA_DIR, state, "church_details.jsonl")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                church = entry.get("church", {})
                slug = church.get("slug", "")
                city = church.get("city", "")
                name = church.get("name", "")
                if slug:
                    lookup[slug] = {"city": city, "name": name}
    return lookup


def find_true_duplicates(slug_lookup):
    """Find same-city, same-name churches with different slugs.
    Returns dict: canonical_slug -> set of alias slugs to merge into it."""
    # Group by (state, city, church_name) -> {slug: row_count}
    state_groups = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for state in sorted(os.listdir(DATA_DIR)):
        path = os.path.join(DATA_DIR, state, "bulletin_names.csv")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                slug = (row.get("church_slug") or "").strip()
                name = (row.get("church_name") or "").strip()
                if slug and name:
                    city = slug_lookup.get(slug, {}).get("city", "")
                    if city:
                        state_groups[state][(name, city)][slug] += 1

    # For each group with multiple slugs, pick canonical (most rows) and alias the rest
    merge_map = {}  # alias_slug -> canonical_slug
    for state, groups in state_groups.items():
        for (name, city), slug_counts in groups.items():
            if len(slug_counts) > 1:
                # Canonical = slug with most rows
                canonical = max(slug_counts, key=slug_counts.get)
                for slug in slug_counts:
                    if slug != canonical:
                        merge_map[slug] = canonical
    return merge_map


def fix_state_csv(state, slug_lookup, merge_map):
    """Fix a single state's bulletin_names.csv: fill cities + merge duplicate slugs."""
    path = os.path.join(DATA_DIR, state, "bulletin_names.csv")
    if not os.path.isfile(path):
        return 0, 0, 0

    rows = []
    cities_fixed = 0
    slugs_merged = 0

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            slug = (row.get("church_slug") or "").strip()

            # Fix 1: Fill missing city from JSONL
            city = (row.get("city") or "").strip()
            if (not city or city.lower() == "unknown") and slug in slug_lookup:
                new_city = slug_lookup[slug].get("city", "")
                if new_city:
                    row["city"] = new_city
                    cities_fixed += 1

            # Fix 2: Merge duplicate slugs
            if slug in merge_map:
                canonical = merge_map[slug]
                row["church_slug"] = canonical
                # Also update church_name if the canonical has a different one
                if canonical in slug_lookup:
                    row["church_name"] = slug_lookup[canonical].get(
                        "name", row.get("church_name", "")
                    )
                slugs_merged += 1

            rows.append(row)

    # Write back
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return len(rows), cities_fixed, slugs_merged


def main():
    print("Building slug lookup from church_details.jsonl...")
    slug_lookup = build_slug_lookup()
    print(f"  {len(slug_lookup)} churches in lookup")

    print("Finding true duplicate slugs...")
    merge_map = find_true_duplicates(slug_lookup)
    print(f"  {len(merge_map)} alias slugs to merge")

    total_rows = 0
    total_cities = 0
    total_merges = 0

    for state in sorted(os.listdir(DATA_DIR)):
        path = os.path.join(DATA_DIR, state, "bulletin_names.csv")
        if not os.path.isfile(path):
            continue
        rows, cities, merges = fix_state_csv(state, slug_lookup, merge_map)
        if cities or merges:
            print(f"  {state}: {rows} rows, {cities} cities filled, {merges} slugs merged")
        total_rows += rows
        total_cities += cities
        total_merges += merges

    print(
        f"\nDone: {total_rows} total rows, {total_cities} cities filled, {total_merges} slugs merged"
    )


if __name__ == "__main__":
    main()
