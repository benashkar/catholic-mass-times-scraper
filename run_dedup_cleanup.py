"""
Deduplication & Cross-State Cleanup Script
==========================================

This script cleans up the church_details.jsonl files across all 50 states by:

1. CROSS-STATE REMOVAL: Each church's slug contains its true state (e.g., us-oh-...).
   The 25-mile radius search causes border-area churches to appear in neighboring
   states' data. This script removes records whose slug state doesn't match the
   directory they're stored in.

2. ORPHAN RETENTION: Some churches (e.g., DC churches) don't have a home state
   directory. These "orphans" are kept in whichever state directory found them
   (only one copy — the first encountered alphabetically).

3. BASE-SLUG DEDUPLICATION: CatholicIndex sometimes has the same church listed
   twice with slightly different names (e.g., "Holy Family Parish (St. Mary Campus)"
   vs "Holy Family Parish St. Mary Campus"). These share the same base slug but
   have different 8-character hash suffixes. We keep only the first (most recently
   updated) record per base slug.

4. REGENERATES: After cleanup, regenerates parsed_addresses.csv and service CSVs
   for each modified state.

Usage:
    python run_dedup_cleanup.py          # Dry run — show what would be removed
    python run_dedup_cleanup.py --apply  # Actually modify the JSONL files
"""

import json
import os
import sys
import logging
from collections import defaultdict
from datetime import datetime

# ---------------------------------------------------------------------------
# Setup logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State code mapping — maps directory names to 2-letter codes used in slugs
# ---------------------------------------------------------------------------
STATE_DIR_TO_CODE = {
    'alabama': 'al', 'alaska': 'ak', 'arizona': 'az', 'arkansas': 'ar',
    'california': 'ca', 'colorado': 'co', 'connecticut': 'ct', 'delaware': 'de',
    'dc': 'dc', 'florida': 'fl', 'georgia': 'ga', 'hawaii': 'hi', 'idaho': 'id',
    'illinois': 'il', 'indiana': 'in', 'iowa': 'ia', 'kansas': 'ks',
    'kentucky': 'ky', 'louisiana': 'la', 'maine': 'me', 'maryland': 'md',
    'massachusetts': 'ma', 'michigan': 'mi', 'minnesota': 'mn', 'mississippi': 'ms',
    'missouri': 'mo', 'montana': 'mt', 'nebraska': 'ne', 'nevada': 'nv',
    'new_hampshire': 'nh', 'new_jersey': 'nj', 'new_mexico': 'nm', 'new_york': 'ny',
    'north_carolina': 'nc', 'north_dakota': 'nd', 'ohio': 'oh', 'oklahoma': 'ok',
    'oregon': 'or', 'pennsylvania': 'pa', 'rhode_island': 'ri', 'south_carolina': 'sc',
    'south_dakota': 'sd', 'tennessee': 'tn', 'texas': 'tx', 'utah': 'ut',
    'vermont': 'vt', 'virginia': 'va', 'washington': 'wa', 'west_virginia': 'wv',
    'wisconsin': 'wi', 'wyoming': 'wy',
}


def extract_slug_state(slug: str) -> str:
    """
    Extract the 2-letter state code from a CatholicIndex slug.

    Slug format: us-{state_code}-{city}-{church_name}-{hash}
    Example: us-oh-ada-our-lady-of-lourdes-7728ad72 -> 'oh'

    Returns the state code, or '??' if the slug format is unexpected.
    """
    parts = slug.split('-')
    if len(parts) >= 3 and parts[0] == 'us':
        return parts[1].lower()
    return '??'


def extract_base_slug(slug: str) -> str:
    """
    Remove the trailing 8-character hex hash from a slug to get the base slug.

    CatholicIndex sometimes creates duplicate entries for the same church with
    different hashes. The base slug (without hash) identifies the unique church.

    Example: us-oh-ada-our-lady-of-lourdes-7728ad72 -> us-oh-ada-our-lady-of-lourdes
    """
    parts = slug.rsplit('-', 1)
    if len(parts) == 2 and len(parts[1]) == 8:
        # Verify it looks like a hex hash
        if all(c in '0123456789abcdef' for c in parts[1]):
            return parts[0]
    return slug


def load_all_records(output_dir: str) -> list:
    """
    Load all church records from all states' JSONL files.

    Returns a list of tuples: (state_dir, record_dict, slug, base_slug, slug_state)
    """
    all_records = []

    for state_dir in sorted(os.listdir(output_dir)):
        jsonl_path = os.path.join(output_dir, state_dir, 'church_details.jsonl')
        if not os.path.exists(jsonl_path):
            continue

        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                church = record.get('church', {})
                slug = church.get('slug', '')
                base_slug = extract_base_slug(slug)
                slug_state = extract_slug_state(slug)

                all_records.append((state_dir, record, slug, base_slug, slug_state))

    return all_records


def identify_orphans(all_records: list) -> set:
    """
    Find slugs that ONLY exist in foreign state directories (no home state has them).

    These are "orphans" — churches like DC churches that got picked up by Maryland
    or Virginia but don't have their own state directory. We need to keep one copy
    of each orphan.

    Returns a set of slugs that are orphans.
    """
    # First, find all slugs that exist in their correct home state
    slugs_in_home_state = set()
    all_slugs_anywhere = set()

    for state_dir, record, slug, base_slug, slug_state in all_records:
        expected_code = STATE_DIR_TO_CODE.get(state_dir, state_dir)
        all_slugs_anywhere.add(slug)
        if slug_state == expected_code:
            slugs_in_home_state.add(slug)

    # Orphans are slugs that appear somewhere but never in their home state
    orphan_slugs = all_slugs_anywhere - slugs_in_home_state
    return orphan_slugs


def run_cleanup(output_dir: str, apply: bool = False):
    """
    Main cleanup logic.

    Phase 1: Load all records and identify what to keep vs remove.
    Phase 2: For each state directory, rewrite the JSONL with only kept records.

    Args:
        output_dir: Path to data/output directory
        apply: If True, actually modify files. If False, dry run only.
    """
    logger.info("=" * 70)
    logger.info("CHURCH DATA DEDUPLICATION & CROSS-STATE CLEANUP")
    logger.info("=" * 70)
    logger.info(f"Mode: {'APPLY (modifying files)' if apply else 'DRY RUN (no changes)'}")
    logger.info(f"Output dir: {output_dir}")
    logger.info("")

    # -----------------------------------------------------------------------
    # Phase 1: Load all records
    # -----------------------------------------------------------------------
    logger.info("Phase 1: Loading all records...")
    all_records = load_all_records(output_dir)
    logger.info(f"  Total records loaded: {len(all_records):,}")

    # -----------------------------------------------------------------------
    # Phase 2: Identify orphans
    # -----------------------------------------------------------------------
    logger.info("Phase 2: Identifying orphan records...")
    orphan_slugs = identify_orphans(all_records)
    logger.info(f"  Orphan slugs (no home state dir): {len(orphan_slugs)}")

    # -----------------------------------------------------------------------
    # Phase 3: Decide what to keep
    # -----------------------------------------------------------------------
    # Strategy:
    #   - KEEP records where slug_state matches directory state (home state records)
    #   - KEEP orphan records (first copy only, by alphabetical state_dir order)
    #   - REMOVE cross-state duplicates (record exists in foreign state AND home state)
    #   - DEDUPLICATE base-slug variants: if multiple slugs share a base_slug,
    #     keep the one with the most recent updatedAt
    # -----------------------------------------------------------------------
    logger.info("Phase 3: Determining which records to keep...")

    # Track which base_slugs we've already decided to keep (globally)
    # This handles the case where the same base_slug has multiple hash variants
    global_base_slugs_kept = {}  # base_slug -> (slug, updatedAt)

    # Track orphan base_slugs we've already kept
    orphan_base_slugs_kept = set()

    # Build per-state keep/remove lists
    state_results = defaultdict(lambda: {'keep': [], 'remove_cross_state': [], 'remove_dupe': []})

    # First pass: collect all home-state records and pick best per base_slug
    for state_dir, record, slug, base_slug, slug_state in all_records:
        expected_code = STATE_DIR_TO_CODE.get(state_dir, state_dir)

        if slug_state == expected_code:
            # This record is in its home state
            updated_at = record.get('church', {}).get('updatedAt', '')

            if base_slug not in global_base_slugs_kept:
                global_base_slugs_kept[base_slug] = (slug, updated_at, state_dir)
            else:
                # Compare updatedAt — keep the more recent one
                existing_slug, existing_updated, existing_dir = global_base_slugs_kept[base_slug]
                if updated_at > existing_updated:
                    # New one is more recent — swap
                    global_base_slugs_kept[base_slug] = (slug, updated_at, state_dir)

    # Second pass: assign keep/remove for each record
    for state_dir, record, slug, base_slug, slug_state in all_records:
        expected_code = STATE_DIR_TO_CODE.get(state_dir, state_dir)
        church_name = record.get('church', {}).get('name', 'Unknown')

        if slug_state == expected_code:
            # Home state record — keep only if it's the chosen slug for this base_slug
            chosen_slug, _, chosen_dir = global_base_slugs_kept.get(base_slug, (None, None, None))
            if slug == chosen_slug and state_dir == chosen_dir:
                state_results[state_dir]['keep'].append(record)
            else:
                state_results[state_dir]['remove_dupe'].append(
                    f"{church_name} ({slug})"
                )
        elif slug in orphan_slugs:
            # Orphan — keep only the first copy (alphabetically by state_dir)
            if base_slug not in orphan_base_slugs_kept:
                orphan_base_slugs_kept.add(base_slug)
                state_results[state_dir]['keep'].append(record)
            else:
                state_results[state_dir]['remove_dupe'].append(
                    f"[orphan dupe] {church_name} ({slug})"
                )
        else:
            # Cross-state duplicate — this record also exists in its home state
            state_results[state_dir]['remove_cross_state'].append(
                f"{church_name} ({slug})"
            )

    # -----------------------------------------------------------------------
    # Phase 4: Print summary
    # -----------------------------------------------------------------------
    logger.info("")
    logger.info("=" * 70)
    logger.info("CLEANUP SUMMARY")
    logger.info("=" * 70)

    total_kept = 0
    total_removed_cross = 0
    total_removed_dupe = 0
    modified_states = []

    print(f"\n{'State':<20s} {'Before':>7s} {'After':>7s} {'Cross-St':>9s} {'Dupes':>6s}")
    print("-" * 55)

    for state_dir in sorted(state_results.keys()):
        result = state_results[state_dir]
        kept = len(result['keep'])
        removed_cross = len(result['remove_cross_state'])
        removed_dupe = len(result['remove_dupe'])
        before = kept + removed_cross + removed_dupe

        total_kept += kept
        total_removed_cross += removed_cross
        total_removed_dupe += removed_dupe

        if removed_cross > 0 or removed_dupe > 0:
            modified_states.append(state_dir)
            marker = " *"
        else:
            marker = ""

        print(f"{state_dir:<20s} {before:>7,d} {kept:>7,d} {removed_cross:>9,d} {removed_dupe:>6,d}{marker}")

    print("-" * 55)
    print(f"{'TOTAL':<20s} {total_kept + total_removed_cross + total_removed_dupe:>7,d} "
          f"{total_kept:>7,d} {total_removed_cross:>9,d} {total_removed_dupe:>6,d}")
    print(f"\n* = modified ({len(modified_states)} states)")

    logger.info(f"Total kept:            {total_kept:,}")
    logger.info(f"Total cross-state:     {total_removed_cross:,}")
    logger.info(f"Total base-slug dupes: {total_removed_dupe:,}")
    logger.info(f"States to modify:      {len(modified_states)}")

    if not apply:
        logger.info("")
        logger.info("DRY RUN COMPLETE — no files modified.")
        logger.info("Re-run with --apply to actually clean up the data.")
        return modified_states, total_kept, total_removed_cross, total_removed_dupe

    # -----------------------------------------------------------------------
    # Phase 5: Rewrite JSONL files
    # -----------------------------------------------------------------------
    logger.info("")
    logger.info("Phase 5: Rewriting JSONL files...")

    for state_dir in modified_states:
        result = state_results[state_dir]
        jsonl_path = os.path.join(output_dir, state_dir, 'church_details.jsonl')

        # Sort kept records by city then name for consistent ordering
        kept_records = sorted(
            result['keep'],
            key=lambda r: (
                (r.get('church', {}).get('city') or '').lower(),
                (r.get('church', {}).get('name') or '').lower()
            )
        )

        # Write the cleaned JSONL
        with open(jsonl_path, 'w', encoding='utf-8') as f:
            for record in kept_records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')

        before_count = len(result['keep']) + len(result['remove_cross_state']) + len(result['remove_dupe'])
        logger.info(f"  {state_dir}: {before_count:,} -> {len(kept_records):,} records")

    logger.info(f"  Rewrote {len(modified_states)} JSONL files.")

    # -----------------------------------------------------------------------
    # Phase 6: Clean up resolve_progress.json files (they have wrong counts now)
    # -----------------------------------------------------------------------
    logger.info("Phase 6: Removing stale resolve_progress.json files...")
    removed_progress = 0
    for state_dir in sorted(os.listdir(output_dir)):
        prog_path = os.path.join(output_dir, state_dir, 'resolve_progress.json')
        if os.path.exists(prog_path):
            os.remove(prog_path)
            removed_progress += 1
    logger.info(f"  Removed {removed_progress} resolve_progress.json files.")

    logger.info("")
    logger.info("CLEANUP COMPLETE!")
    logger.info(f"  Unique churches remaining: {total_kept:,}")
    logger.info(f"  Duplicate records removed: {total_removed_cross + total_removed_dupe:,}")
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. python run_parse_addresses.py all   (regenerate parsed_addresses.csv)")
    logger.info("  2. python run_resolve_urls.py all      (resolve website URLs)")
    logger.info("  3. git add -A && git commit            (commit cleaned data)")

    return modified_states, total_kept, total_removed_cross, total_removed_dupe


if __name__ == '__main__':
    apply = '--apply' in sys.argv
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'output')

    run_cleanup(output_dir, apply=apply)
