"""
test_bulletin_names.py

Unit tests for the bulletin name scoring, cleaning, and validation functions
in run_bulletin_scraper.py.

These tests use the real SSA/Census reference data files in data/reference/
(loaded automatically by _load_reference_data). No mocking needed.

Run these tests with:
    pytest tests/test_bulletin_names.py -v
"""

from run_bulletin_scraper import (
    score_name_confidence,
    confidence_label,
    split_merged_name,
    clean_extracted_name,
    is_valid_name,
)


# ── TestScoreNameConfidence ──────────────────────────────────────────────────


class TestScoreNameConfidence:
    """Tests for score_name_confidence() function."""

    def test_common_name_scores_high(self):
        """Common name 'John Smith' should score >= 0.7 (SSA + Census + word-count + top-1000)."""
        score = score_name_confidence("John Smith")
        assert score >= 0.7

    def test_ssa_first_name_contributes(self):
        """A recognized SSA first name should boost the score."""
        # "Michael" is SSA; "Xyznotaname" is not Census, but first-name adds +0.30
        score_real = score_name_confidence("Michael Xyznotaname")
        score_fake = score_name_confidence("Xyznotaname Xyznotaname")
        assert score_real > score_fake

    def test_census_surname_contributes(self):
        """A recognized Census surname should boost the score."""
        score_real = score_name_confidence("Xyznotaname Johnson")
        score_fake = score_name_confidence("Xyznotaname Xyznotaname")
        assert score_real > score_fake

    def test_clergy_staff_category_boost(self):
        """Category 'clergy_staff' should add +0.15."""
        base = score_name_confidence("John Smith")
        boosted = score_name_confidence("John Smith", category="clergy_staff")
        assert boosted == min(1.0, round(base + 0.15, 2))

    def test_mass_intention_category_boost(self):
        """Category 'mass_intention' should add +0.15."""
        base = score_name_confidence("John Smith")
        boosted = score_name_confidence("John Smith", category="mass_intention")
        assert boosted == min(1.0, round(base + 0.15, 2))

    def test_unknown_category_no_boost(self):
        """An unrecognized category should not change the score."""
        base = score_name_confidence("John Smith")
        same = score_name_confidence("John Smith", category="bulletin_ad")
        assert same == base

    def test_title_prefix_boost(self):
        """Title prefix 'Fr.' should add +0.05."""
        without = score_name_confidence("Michael Johnson")
        with_title = score_name_confidence("Fr. Michael Johnson")
        assert with_title > without

    def test_two_word_name_gets_word_count_bonus(self):
        """A 2-word name should get the +0.10 word-count bonus."""
        # 2-word name
        score_2 = score_name_confidence("John Smith")
        # 4-word name (no word-count bonus)
        score_4 = score_name_confidence("John Robert William Smith")
        # Both have SSA+Census, but 2-word gets +0.10
        assert score_2 > score_4

    def test_top1000_first_name_bonus(self):
        """A top-1000 SSA first name should score higher than a rare SSA name."""
        # "James" is top-1000; a rare SSA name might not be
        score_top = score_name_confidence("James Smith")
        assert score_top >= 0.7  # SSA(0.30) + Census(0.30) + word-count(0.10) + top1000(0.10)

    def test_non_name_word_penalty(self):
        """A name containing a non-name word should be penalized -0.40."""
        # "Hosting" is in non_name_check; compare with and without the penalty
        without = score_name_confidence("John Smith")
        with_penalty = score_name_confidence("John Hosting")
        assert with_penalty < without

    def test_newline_penalty(self):
        """A name with embedded newline should get -0.30 penalty."""
        clean = score_name_confidence("John Smith")
        dirty = score_name_confidence("John\nSmith")
        assert dirty < clean

    def test_truncated_last_word_penalty(self):
        """Short last word 'Sm' should get -0.25 penalty."""
        normal = score_name_confidence("John Smith")
        truncated = score_name_confidence("John Sm")
        assert truncated < normal

    def test_merged_name_penalty(self):
        """3+ words where last word is SSA-only first name gets -0.20."""
        # "Kevin Steinkamp Cynthia" — "cynthia" is SSA top-5000, not Census
        merged = score_name_confidence("Kevin Steinkamp Cynthia")
        normal = score_name_confidence("Kevin Steinkamp")
        assert merged < normal

    def test_org_pattern_penalty(self):
        """'Knights of Columbus' should get -0.15 org pattern penalty."""
        score = score_name_confidence("Knights of Columbus")
        # Should be heavily penalized (non-name words + org pattern)
        assert score <= 0.0

    def test_empty_string_returns_zero(self):
        """Empty string should return 0.0."""
        assert score_name_confidence("") == 0.0

    def test_whitespace_only_returns_zero(self):
        """Whitespace-only string should return 0.0."""
        assert score_name_confidence("   ") == 0.0

    def test_score_capped_at_one(self):
        """Score should never exceed 1.0 even with all bonuses."""
        # clergy_staff + title + common name
        score = score_name_confidence("Fr. James Smith", category="clergy_staff")
        assert score <= 1.0

    def test_score_floor_at_zero(self):
        """Score should never go below 0.0 even with all penalties."""
        score = score_name_confidence("church\ncouncil meeting schedule")
        assert score >= 0.0


# ── TestConfidenceLabel ──────────────────────────────────────────────────────


class TestConfidenceLabel:
    """Tests for confidence_label() function."""

    def test_high_at_threshold(self):
        """Score of exactly 0.7 should be 'high'."""
        assert confidence_label(0.7) == "high"

    def test_high_above_threshold(self):
        """Score above 0.7 should be 'high'."""
        assert confidence_label(0.9) == "high"

    def test_medium_at_threshold(self):
        """Score of exactly 0.4 should be 'medium'."""
        assert confidence_label(0.4) == "medium"

    def test_medium_between_thresholds(self):
        """Score between 0.4 and 0.7 should be 'medium'."""
        assert confidence_label(0.55) == "medium"

    def test_low_below_medium(self):
        """Score of 0.39 should be 'low'."""
        assert confidence_label(0.39) == "low"

    def test_low_at_zero(self):
        """Score of 0.0 should be 'low'."""
        assert confidence_label(0.0) == "low"


# ── TestSplitMergedName ──────────────────────────────────────────────────────


class TestSplitMergedName:
    """Tests for split_merged_name() function."""

    def test_splits_three_word_with_ssa_last(self):
        """Should split 'Kevin Steinkamp Cynthia' -> 'Kevin Steinkamp'."""
        # "cynthia" is SSA top-5000 first name but not a Census surname
        result = split_merged_name("Kevin Steinkamp Cynthia")
        assert result == "Kevin Steinkamp"

    def test_no_split_when_last_is_also_surname(self):
        """Should NOT split when last word is also a Census surname."""
        # "James" is both SSA first name and Census surname
        result = split_merged_name("Robert Williams James")
        assert result == "Robert Williams James"

    def test_no_split_on_two_word_name(self):
        """Should return 2-word name unchanged."""
        result = split_merged_name("John Smith")
        assert result == "John Smith"

    def test_no_split_on_single_word(self):
        """Should return single word unchanged."""
        result = split_merged_name("John")
        assert result == "John"

    def test_no_split_when_last_word_not_ssa(self):
        """Should not split when last word is not an SSA first name."""
        result = split_merged_name("John Smith Xyznotaname")
        assert result == "John Smith Xyznotaname"

    def test_preserves_whitespace_handling(self):
        """Should handle leading/trailing whitespace."""
        result = split_merged_name("  Kevin Steinkamp Cynthia  ")
        assert result == "Kevin Steinkamp"

    def test_four_word_split(self):
        """Should split 4-word name when last word is SSA-only first name."""
        # "Mary" is both SSA and Census, so try with a name that's SSA-only
        result = split_merged_name("Kevin Andrew Steinkamp Cynthia")
        assert result == "Kevin Andrew Steinkamp"


# ── TestCleanExtractedName ───────────────────────────────────────────────────


class TestCleanExtractedName:
    """Tests for clean_extracted_name() function."""

    def test_newline_keeps_first_line(self):
        """Should keep only the first line when newlines present."""
        result = clean_extracted_name("Jose Gamboa\nJunk Text")
        assert result == "Jose Gamboa"

    def test_collapses_multiple_spaces(self):
        """Should collapse multiple spaces to single space."""
        result = clean_extracted_name("John    Smith")
        assert result == "John Smith"

    def test_strips_trailing_day_abbreviation(self):
        """Should strip trailing day abbreviations like 'Wed'."""
        result = clean_extracted_name("Jose Gamboa Wed")
        assert result == "Jose Gamboa"

    def test_strips_multiple_trailing_days(self):
        """Should strip multiple trailing day abbreviations."""
        result = clean_extracted_name("Jose Gamboa Sat Sun")
        assert result == "Jose Gamboa"

    def test_strips_trailing_role(self):
        """Should strip trailing ministry role words like 'Lector'."""
        result = clean_extracted_name("Richard Martinez Lector")
        assert result == "Richard Martinez"

    def test_strips_multiple_trailing_roles(self):
        """Should strip multiple trailing role words."""
        result = clean_extracted_name("Richard Martinez Lector Usher")
        assert result == "Richard Martinez"

    def test_strips_trailing_all(self):
        """Should strip trailing 'All'."""
        result = clean_extracted_name("Edwin Arthur All")
        assert result == "Edwin Arthur"

    def test_strips_leading_trailing_punctuation(self):
        """Should strip leading/trailing punctuation."""
        result = clean_extracted_name('"John Smith"')
        assert result == "John Smith"

    def test_calls_split_merged_name(self):
        """Should split merged names after other cleaning."""
        # "Cynthia" is SSA-only, not Census surname
        result = clean_extracted_name("Kevin Steinkamp Cynthia")
        assert result == "Kevin Steinkamp"

    def test_empty_string_returns_empty(self):
        """Should return empty string for empty input."""
        result = clean_extracted_name("")
        assert result == ""

    def test_combined_cleaning(self):
        """Should apply multiple cleaning steps together."""
        result = clean_extracted_name('"Jose   Gamboa\nExtra Line"')
        # First line only, collapse spaces, strip quotes
        assert result == "Jose Gamboa"


# ── TestIsValidName ──────────────────────────────────────────────────────────


class TestIsValidName:
    """Tests for is_valid_name() function."""

    # --- Valid names ---

    def test_valid_two_word_name(self):
        """Standard two-word name should be valid."""
        assert is_valid_name("Michael Johnson") is True

    def test_valid_three_word_name(self):
        """Three-word name (with middle) should be valid."""
        assert is_valid_name("Mary Ann Johnson") is True

    def test_valid_name_with_middle_initial(self):
        """Name with middle initial should be valid."""
        assert is_valid_name("John A. Smith") is True

    def test_valid_four_word_name(self):
        """Four-word name should be valid."""
        assert is_valid_name("Mary Jane Van Horn") is True

    # --- Rejected: structural ---

    def test_rejects_empty_string(self):
        """Empty string should be rejected."""
        assert is_valid_name("") is False

    def test_rejects_single_word(self):
        """Single word should be rejected (needs first + last)."""
        assert is_valid_name("Michael") is False

    def test_rejects_five_plus_words(self):
        """Five or more words should be rejected."""
        assert is_valid_name("John Robert William Charles Smith") is False

    def test_rejects_newlines(self):
        """Names with newlines should be rejected (PDF artifacts)."""
        assert is_valid_name("John\nSmith") is False

    def test_rejects_short_string(self):
        """Strings shorter than 4 characters should be rejected."""
        assert is_valid_name("Jo") is False

    # --- Rejected: capitalization ---

    def test_rejects_lowercase_first_letter(self):
        """Words starting with lowercase should be rejected."""
        assert is_valid_name("john Smith") is False

    def test_rejects_all_caps_words(self):
        """All-caps words (>2 chars) should be rejected."""
        assert is_valid_name("JOHN Smith") is False

    def test_allows_two_letter_caps(self):
        """Two-letter all-caps (initials) should be allowed."""
        assert is_valid_name("John AB Smith") is True

    # --- Rejected: false positives ---

    def test_rejects_holy_spirit(self):
        """'Holy Spirit' should be rejected as a false positive."""
        assert is_valid_name("Holy Spirit") is False

    def test_rejects_sacred_heart(self):
        """'Sacred Heart' should be rejected as a false positive."""
        assert is_valid_name("Sacred Heart") is False

    # --- Rejected: truncated ---

    def test_rejects_truncated_last_word(self):
        """Short last word (not an initial) should be rejected."""
        assert is_valid_name("Teresa Mu") is False

    def test_allows_initial_last(self):
        """Single-letter last word (initial) should be allowed."""
        assert is_valid_name("Michael J") is True

    # --- Rejected: long words ---

    def test_rejects_merged_word_artifact(self):
        """Words >15 chars should be rejected (merged PDF artifacts)."""
        assert is_valid_name("John Honoryourfamilymember") is False

    # --- Rejected: non_name_words ---

    def test_rejects_non_name_word_church(self):
        """Words in non_name_words set should be rejected."""
        assert is_valid_name("Church Council") is False

    def test_rejects_non_name_word_case_insensitive(self):
        """Non-name word check should be case-insensitive."""
        assert is_valid_name("John Sunday") is False

    # --- Rejected: non_name_starters ---

    def test_rejects_non_name_starter_the(self):
        """'The' as first word should be rejected."""
        assert is_valid_name("The Romo") is False

    def test_rejects_non_name_starter_of(self):
        """'Of' as first word should be rejected."""
        assert is_valid_name("Of Jensen") is False
