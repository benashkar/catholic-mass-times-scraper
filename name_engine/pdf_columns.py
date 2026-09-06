"""
Column-aware PDF text extraction using pdfplumber bounding boxes.

Detects multi-column layouts by clustering word x-coordinates, then extracts
text per column to prevent name merging across columns.
"""

import statistics
from collections import defaultdict

import pdfplumber


def _detect_columns(words, gap_threshold=None):
    """Detect column boundaries from word bounding boxes.

    Groups words by their x0 (left edge) position. If there's a gap larger
    than the threshold between groups, that's a column boundary.

    Args:
        words: List of word dicts from pdfplumber (must have 'x0', 'top', 'text')
        gap_threshold: Minimum gap between columns in points. If None, auto-detected
                       as 2x the median word spacing.

    Returns:
        List of column boundary x-positions (left edges), sorted.
        E.g., [72.0, 310.5] means two columns starting at x=72 and x=310.5.
    """
    if not words:
        return [0]

    # Get all x0 positions
    x_positions = sorted(set(round(w["x0"], 1) for w in words))
    if len(x_positions) < 2:
        return [x_positions[0]] if x_positions else [0]

    # Calculate gaps between adjacent x positions
    gaps = [
        (x_positions[i + 1] - x_positions[i], x_positions[i + 1])
        for i in range(len(x_positions) - 1)
    ]

    if not gaps:
        return [x_positions[0]]

    # Auto-detect threshold: significant gap = 3x median gap
    if gap_threshold is None:
        gap_values = [g for g, _ in gaps]
        median_gap = statistics.median(gap_values)
        gap_threshold = max(median_gap * 3, 40)  # At least 40pt gap for a column break

    # Find column boundaries
    boundaries = [x_positions[0]]
    for gap_size, x_pos in gaps:
        if gap_size >= gap_threshold:
            boundaries.append(x_pos)

    return boundaries


def _assign_words_to_columns(words, boundaries):
    """Assign each word to its nearest column based on x0 position.

    Args:
        words: List of word dicts with 'x0', 'top', 'text'
        boundaries: Sorted list of column x-positions

    Returns:
        Dict mapping column_index -> list of words, sorted by (top, x0).
    """
    columns = defaultdict(list)

    for word in words:
        x0 = word["x0"]
        # Find the nearest column boundary to the left
        col_idx = 0
        for i, bx in enumerate(boundaries):
            if x0 >= bx - 5:  # 5pt tolerance
                col_idx = i
        columns[col_idx].append(word)

    # Sort each column by vertical position (top), then horizontal (x0)
    for col_idx in columns:
        columns[col_idx].sort(key=lambda w: (round(w["top"], 1), w["x0"]))

    return columns


def _words_to_text(words, line_tolerance=3):
    """Convert sorted word list to text string, respecting line breaks.

    Args:
        words: List of word dicts sorted by (top, x0)
        line_tolerance: Words within this many points vertically are on the same line.

    Returns:
        Extracted text string with proper line breaks.
    """
    if not words:
        return ""

    lines = []
    current_line = [words[0]["text"]]
    current_top = words[0]["top"]

    for word in words[1:]:
        if abs(word["top"] - current_top) <= line_tolerance:
            current_line.append(word["text"])
        else:
            lines.append(" ".join(current_line))
            current_line = [word["text"]]
            current_top = word["top"]

    lines.append(" ".join(current_line))
    return "\n".join(lines)


def extract_text_by_columns(pdf_path, pages=None):
    """Extract text from a PDF, respecting column layout.

    For each page, detects column boundaries and extracts text per column,
    preventing name merging across columns.

    Args:
        pdf_path: Path to the PDF file.
        pages: Optional list of page numbers (0-indexed) to extract.
               If None, extracts all pages.

    Returns:
        List of dicts, one per page:
        [
            {
                "page": 0,
                "num_columns": 2,
                "columns": ["column 1 text...", "column 2 text..."],
                "full_text": "all columns concatenated with separator"
            },
            ...
        ]
    """
    results = []

    with pdfplumber.open(pdf_path) as pdf:
        page_range = pages if pages is not None else range(len(pdf.pages))

        for page_num in page_range:
            if page_num >= len(pdf.pages):
                continue

            page = pdf.pages[page_num]
            words = page.extract_words(
                keep_blank_chars=False,
                x_tolerance=2,
                y_tolerance=2,
            )

            if not words:
                results.append(
                    {
                        "page": page_num,
                        "num_columns": 0,
                        "columns": [],
                        "full_text": "",
                    }
                )
                continue

            boundaries = _detect_columns(words)
            column_words = _assign_words_to_columns(words, boundaries)

            column_texts = []
            for col_idx in sorted(column_words.keys()):
                text = _words_to_text(column_words[col_idx])
                if text.strip():
                    column_texts.append(text)

            # Join columns with a clear separator
            full_text = "\n\n--- COLUMN BREAK ---\n\n".join(column_texts)

            results.append(
                {
                    "page": page_num,
                    "num_columns": len(column_texts),
                    "columns": column_texts,
                    "full_text": full_text,
                }
            )

    return results


def extract_full_text(pdf_path, column_aware=True):
    """Convenience function: extract all text from a PDF as a single string.

    Args:
        pdf_path: Path to the PDF file.
        column_aware: If True, uses column detection. If False, uses pdfplumber's
                      default extraction (original behavior for backward compat).

    Returns:
        Full text string from the PDF.
    """
    if not column_aware:
        with pdfplumber.open(pdf_path) as pdf:
            texts = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                texts.append(text)
            return "\n\n".join(texts)

    pages = extract_text_by_columns(pdf_path)
    return "\n\n".join(p["full_text"] for p in pages if p["full_text"])
