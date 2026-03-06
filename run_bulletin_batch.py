"""
run_bulletin_batch.py

Runs the bulletin scraper for multiple states, always keeping exactly 2 running.
When one finishes, immediately starts the next. After each state, commits + pushes.

Usage:
    python run_bulletin_batch.py
    python run_bulletin_batch.py --max-parallel 3
"""

import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "data" / "output"
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# States that need the full pipeline (no bulletin data at all)
STATES = [
    "idaho", "kansas", "kentucky", "maine", "maryland", "mississippi",
    "missouri", "montana", "nevada", "new_hampshire", "north_carolina",
    "north_dakota", "oregon", "rhode_island", "south_carolina",
    "south_dakota", "tennessee", "utah", "vermont", "virginia",
    "washington", "west_virginia", "wyoming",
]

MAX_PARALLEL = 2


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def count_names(state):
    csv_path = OUTPUT_DIR / state / "bulletin_names.csv"
    if csv_path.exists():
        return sum(1 for _ in open(csv_path, encoding="utf-8")) - 1
    return 0


def commit_and_push(state):
    state_dir = OUTPUT_DIR / state
    csv_path = state_dir / "bulletin_names.csv"
    if not csv_path.exists():
        print(f"  [{state}] No bulletin_names.csv, skipping commit.")
        return

    files_to_add = []
    for fname in ["bulletin_names.csv", "bulletin_names.json",
                   "bulletin_progress.json", "bulletin_discovery.json",
                   "capped_churches.json"]:
        fpath = state_dir / fname
        if fpath.exists():
            files_to_add.append(str(fpath))

    if not files_to_add:
        return

    subprocess.run(["git", "add"] + files_to_add,
                    cwd=str(PROJECT_DIR), capture_output=True)

    # Check if anything staged
    result = subprocess.run(["git", "diff", "--cached", "--quiet"],
                            cwd=str(PROJECT_DIR), capture_output=True)
    if result.returncode == 0:
        print(f"  [{state}] No new changes to commit.")
        return

    name_count = count_names(state)
    msg = (f"Add bulletin names for {state} ({name_count} names extracted)\n\n"
           f"Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>")

    subprocess.run(["git", "commit", "-m", msg],
                    cwd=str(PROJECT_DIR), capture_output=True)
    subprocess.run(["git", "pull", "--rebase"],
                    cwd=str(PROJECT_DIR), capture_output=True)
    subprocess.run(["git", "push"],
                    cwd=str(PROJECT_DIR), capture_output=True)
    print(f"  [{state}] Committed + pushed ({name_count} names).")


def start_state(state):
    log_path = LOG_DIR / f"bulletin_{state}.log"
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-u", "run_bulletin_scraper.py", "all", state, "--resume"],
        cwd=str(PROJECT_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    print(f"[{state}] Started (PID {proc.pid}) at {now()}")
    return proc, log_file, state


def main():
    # Filter out states that already have bulletin_names.csv
    remaining = []
    for s in STATES:
        csv_path = OUTPUT_DIR / s / "bulletin_names.csv"
        if csv_path.exists():
            n = count_names(s)
            print(f"[{s}] Already has {n} names, skipping.")
        else:
            remaining.append(s)

    print(f"\n=== BULLETIN BATCH SCRAPER (max {MAX_PARALLEL} parallel) ===")
    print(f"Started at {now()}")
    print(f"States to process: {len(remaining)}")
    print()

    # Active processes: list of (proc, log_file, state)
    active = []
    state_idx = 0

    # Seed initial processes
    while len(active) < MAX_PARALLEL and state_idx < len(remaining):
        state = remaining[state_idx]
        active.append(start_state(state))
        state_idx += 1

    # Main loop: poll for completion, start next
    while active:
        time.sleep(5)

        # Check which processes have finished
        still_running = []
        for proc, log_file, state in active:
            ret = proc.poll()
            if ret is not None:
                # Process finished
                log_file.close()
                print(f"[{state}] Finished (exit code {ret}) at {now()}")
                n = count_names(state)
                if n > 0:
                    print(f"  [{state}] {n} names extracted")
                else:
                    print(f"  [{state}] No names produced")
                commit_and_push(state)

                # Start next state immediately
                if state_idx < len(remaining):
                    next_state = remaining[state_idx]
                    still_running.append(start_state(next_state))
                    state_idx += 1
            else:
                still_running.append((proc, log_file, state))

        active = still_running

    print(f"\n=== ALL {len(remaining)} STATES DONE at {now()} ===")


if __name__ == "__main__":
    main()
