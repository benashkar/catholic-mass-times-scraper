"""
CLI interface for name-engine.

Usage:
    name-engine extract --input bulletin.pdf --output names.csv
    name-engine score --input raw_names.csv --output scored_names.csv
"""

import argparse
import csv
import json
import sys


def cmd_extract(args):
    """Extract names from a PDF file."""
    from name_engine import extract_names_from_pdf

    results = extract_names_from_pdf(args.input)

    if args.format == "json":
        output = [r.to_dict() for r in results]
        text = json.dumps(output, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            print(text)
    else:  # csv
        fieldnames = ["name", "confidence", "final_score", "is_suspect", "reason"]
        out = open(args.output, "w", newline="", encoding="utf-8") if args.output else sys.stdout
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "name": r.name,
                    "confidence": r.confidence,
                    "final_score": round(r.final_score, 3),
                    "is_suspect": r.is_suspect,
                    "reason": r.reason,
                }
            )
        if args.output:
            out.close()

    count_high = sum(1 for r in results if r.confidence == "high")
    count_med = sum(1 for r in results if r.confidence == "medium")
    count_low = sum(1 for r in results if r.confidence == "low")
    count_suspect = sum(1 for r in results if r.is_suspect)
    print(
        f"[OK] Extracted {len(results)} names: "
        f"{count_high} high, {count_med} medium, {count_low} low, "
        f"{count_suspect} suspect",
        file=sys.stderr,
    )


def cmd_score(args):
    """Score names from a CSV file."""
    from name_engine import score_names

    names = []
    with open(args.input, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        name_col = args.name_column or "person_name"
        for row in reader:
            name = row.get(name_col, "").strip()
            if name:
                names.append(name)

    results = score_names(names)

    out = open(args.output, "w", newline="", encoding="utf-8") if args.output else sys.stdout
    fieldnames = ["name", "confidence", "final_score", "is_suspect", "reason"]
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()
    for r in results:
        writer.writerow({k: r[k] for k in fieldnames})
    if args.output:
        out.close()

    print(f"[OK] Scored {len(results)} names", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        prog="name-engine",
        description="Multi-engine PDF name extraction & confidence scoring",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Extract command
    extract_parser = subparsers.add_parser("extract", help="Extract names from a PDF")
    extract_parser.add_argument("--input", "-i", required=True, help="Input PDF path")
    extract_parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    extract_parser.add_argument("--format", choices=["csv", "json"], default="csv")
    extract_parser.set_defaults(func=cmd_extract)

    # Score command
    score_parser = subparsers.add_parser("score", help="Score names from a CSV")
    score_parser.add_argument("--input", "-i", required=True, help="Input CSV path")
    score_parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    score_parser.add_argument(
        "--name-column", default="person_name", help="Column containing names"
    )
    score_parser.set_defaults(func=cmd_score)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
