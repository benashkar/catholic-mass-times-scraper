"""
build_junk_test_set.py

Builds a labeled test CSV for measuring name filter precision/recall.
Samples 200 names from each of 10 diverse states (2,000 total), stratified
50% likely-clean / 50% likely-junk based on current heuristics.

The output CSV has a blank 'manual_label' column for human review.
After manual review, this becomes the ground truth benchmark.

Usage:
    python scripts/build_junk_test_set.py

Output:
    data/reference/junk_test_set.csv
"""

import csv
import random
import sys
from pathlib import Path

# Resolve project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import OUTPUT_DIR  # noqa: E402

# 10 diverse states for geographic coverage
TARGET_STATES = [
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

SAMPLES_PER_STATE = 200
CLEAN_RATIO = 0.5  # 50% likely-clean, 50% likely-junk


def load_state_names(state_name):
    """Load bulletin_names.csv for a state, return list of dicts."""
    csv_path = OUTPUT_DIR / state_name / "bulletin_names.csv"
    if not csv_path.exists():
        print(f"  SKIP {state_name}: no bulletin_names.csv found")
        return []

    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def classify_heuristic(row):
    """Classify a row as likely 'clean' or 'junk' based on existing heuristics.

    Uses the confidence field: high = clean, medium = borderline, low = junk.
    Also checks confidence_score if available.
    """
    conf = row.get("confidence", "").lower()
    score = row.get("confidence_score", "")

    if score:
        try:
            score = float(score)
            return "clean" if score >= 0.5 else "junk"
        except ValueError:
            pass

    if conf == "high":
        return "clean"
    elif conf == "low":
        return "junk"
    else:
        # Medium: treat as borderline — split evenly
        return "clean"


def sample_state(state_name, n=SAMPLES_PER_STATE):
    """Sample n names from a state, stratified by clean/junk."""
    rows = load_state_names(state_name)
    if not rows:
        return []

    clean = [r for r in rows if classify_heuristic(r) == "clean"]
    junk = [r for r in rows if classify_heuristic(r) == "junk"]

    n_clean = int(n * CLEAN_RATIO)
    n_junk = n - n_clean

    # Sample (with replacement if needed for small states)
    if len(clean) >= n_clean:
        sampled_clean = random.sample(clean, n_clean)
    else:
        sampled_clean = clean[:]
        print(f"  {state_name}: only {len(clean)} clean names (wanted {n_clean})")

    if len(junk) >= n_junk:
        sampled_junk = random.sample(junk, n_junk)
    else:
        sampled_junk = junk[:]
        print(f"  {state_name}: only {len(junk)} junk names (wanted {n_junk})")

    sampled = sampled_clean + sampled_junk
    random.shuffle(sampled)

    # Build output rows with relevant columns
    output = []
    for row in sampled:
        output.append(
            {
                "person_name": row.get("person_name", ""),
                "church_name": row.get("church_name", ""),
                "city": row.get("city", ""),
                "state": state_name,
                "category": row.get("category", ""),
                "confidence": row.get("confidence", ""),
                "confidence_score": row.get("confidence_score", ""),
                "auto_label": classify_heuristic(row),
                "manual_label": "",  # blank — for human review
            }
        )

    print(
        f"  {state_name}: sampled {len(output)} names "
        f"({len(sampled_clean)} clean, {len(sampled_junk)} junk)"
    )
    return output


def main():
    random.seed(42)  # reproducible sampling

    output_path = PROJECT_ROOT / "data" / "reference" / "junk_test_set.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_samples = []
    print(f"Building junk test set from {len(TARGET_STATES)} states...")

    for state in TARGET_STATES:
        samples = sample_state(state)
        all_samples.extend(samples)

    if not all_samples:
        print(
            "ERROR: No samples collected. Check that bulletin_names.csv exists for target states."
        )
        sys.exit(1)

    # Write output CSV
    fieldnames = [
        "person_name",
        "church_name",
        "city",
        "state",
        "category",
        "confidence",
        "confidence_score",
        "auto_label",
        "manual_label",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_samples)

    print(f"\nWrote {len(all_samples)} samples to {output_path}")
    print("Next step: open the CSV and fill in the 'manual_label' column (clean/junk)")


if __name__ == "__main__":
    main()
