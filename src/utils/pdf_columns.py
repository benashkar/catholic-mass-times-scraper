"""
Column-aware PDF text extraction using pdfplumber bounding boxes.

Detects multi-column layouts by clustering word x-coordinates, then extracts
text per column to prevent name merging across columns.

Ported from names_people_matcher/name_engine/pdf_columns.py to keep
church_scrapes self-contained (no cross-project dependency).
"""

import statistics
from collections import defaultdict


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
    gaps = [(x_positions[i + 1] - x_positions[i], x_positions[i + 1])
            for i in range(len(x_positions) - 1)]

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


def extract_columns_from_page(page):
    """Extract text from a single pdfplumber page, respecting column layout.

    Args:
        page: A pdfplumber page object.

    Returns:
        List of column text strings. Single-column pages return a one-element list.
        Empty pages return an empty list.
    """
    words = page.extract_words(
        keep_blank_chars=False,
        x_tolerance=2,
        y_tolerance=2,
    )

    if not words:
        return []

    boundaries = _detect_columns(words)
    column_words = _assign_words_to_columns(words, boundaries)

    column_texts = []
    for col_idx in sorted(column_words.keys()):
        text = _words_to_text(column_words[col_idx])
        if text.strip():
            column_texts.append(text)

    return column_texts
