"""
run_all_states.py

Run statewide scrape + address parsing + git commit for all remaining US states.
Skips states that already have church_details.jsonl in their output directory.

Usage:
    python run_all_states.py
"""

import subprocess
import sys
from pathlib import Path

# All states in alphabetical order
ALL_STATES = [
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "dc",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new_hampshire",
    "new_jersey",
    "new_mexico",
    "new_york",
    "north_carolina",
    "north_dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode_island",
    "south_carolina",
    "south_dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west_virginia",
    "wisconsin",
    "wyoming",
]

STATE_CODES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "dc": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new_hampshire": "NH",
    "new_jersey": "NJ",
    "new_mexico": "NM",
    "new_york": "NY",
    "north_carolina": "NC",
    "north_dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode_island": "RI",
    "south_carolina": "SC",
    "south_dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west_virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "data" / "output"


def is_state_done(state: str) -> bool:
    return (OUTPUT_DIR / state / "church_details.jsonl").exists()


def run_state(state: str) -> bool:
    """Run statewide scrape for a single state. Returns True on success."""
    print(f"\n{'='*70}")
    print(f"STARTING: {state.upper()} ({STATE_CODES.get(state, '??')})")
    print(f"{'='*70}")

    # Run statewide scrape
    result = subprocess.run(
        [sys.executable, "run_statewide.py", state],
        cwd=str(PROJECT_DIR),
    )
    if result.returncode != 0:
        print(f"ERROR: Statewide scrape failed for {state}")
        return False

    # Parse addresses
    result = subprocess.run(
        [sys.executable, "run_parse_addresses.py", state],
        cwd=str(PROJECT_DIR),
    )
    if result.returncode != 0:
        print(f"WARNING: Address parsing failed for {state}")

    # Git commit
    subprocess.run(
        ["git", "add", f"data/churches/{state}/", f"data/output/{state}/"],
        cwd=str(PROJECT_DIR),
    )
    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            f"Add {state.replace('_', ' ').title()} statewide scrape\n\n"
            f"Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>",
        ],
        cwd=str(PROJECT_DIR),
    )

    return True


def main():
    remaining = [s for s in ALL_STATES if not is_state_done(s)]
    total = len(remaining)

    if total == 0:
        print("All states have been scraped!")
        return

    print(f"States remaining: {total}")
    for i, state in enumerate(remaining):
        print(f"  {i+1}. {state}")

    print(f"\nStarting sequential scrape of {total} states...")

    completed = 0
    failed = []

    for i, state in enumerate(remaining):
        print(f"\n[{i+1}/{total}] Processing {state}...")
        success = run_state(state)
        if success:
            completed += 1
        else:
            failed.append(state)

    print(f"\n{'='*70}")
    print("ALL STATES BATCH COMPLETE")
    print(f"Completed: {completed}/{total}")
    if failed:
        print(f"Failed: {', '.join(failed)}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
