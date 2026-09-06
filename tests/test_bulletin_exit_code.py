"""Regression: the bulletin extractor's exit code must describe the RUN.

Two real incidents bracket this behaviour:

  * `errors == 0` (the original rule) failed almost every full sweep, because a
    16k-church run always has a few unreachable parish sites. That is why the
    step was parked in run_daily_pipeline's non_critical set, which then hid
    bulletins=FAIL every Tuesday from 2026-07-14 to 2026-08-04.

  * `processed > 0 and nothing extracted` (my replacement) failed HEALTHY
    shards. scrape_log 825 recorded `discovered=1 pdfs=0 names=0 [queue-drained]`
    and exited 1; all six shards at 2026-08-14 03:01 did the same. A shard that
    picks up one church whose PDFs are already extracted has not failed. Since
    `bulletins` is now critical, that false failure fails the whole pipeline.

So the rule needs a sample floor: judge the run only once it has seen enough
churches for "nothing came out" to mean something.
"""

import pytest

MIN_SAMPLE = 50


def verdict(processed, pdfs, names, errors):
    """Mirror of the exit-code rule in extract_bulletins_to_db99.main()."""
    no_output = processed >= MIN_SAMPLE and pdfs == 0 and names == 0
    mostly_errors = processed >= MIN_SAMPLE and errors / processed > 0.25
    return 1 if (no_output or mostly_errors) else 0


class TestDrainedQueueIsNotAFailure:
    def test_scrape_log_825_exits_zero(self):
        """discovered=1 pdfs=0 names=0 — the shard-0 failure of 2026-08-15."""
        assert verdict(processed=1, pdfs=0, names=0, errors=0) == 0

    def test_empty_queue_exits_zero(self):
        assert verdict(processed=0, pdfs=0, names=0, errors=0) == 0

    @pytest.mark.parametrize("processed", [1, 5, 20, 49])
    def test_small_batches_never_fail_on_no_output(self, processed):
        assert verdict(processed, pdfs=0, names=0, errors=0) == 0


class TestRealFailuresStillFail:
    def test_large_run_with_no_output_fails(self):
        """400 churches and not one PDF is a broken run, not a quiet one."""
        assert verdict(processed=400, pdfs=0, names=0, errors=0) == 1

    def test_at_the_sample_floor(self):
        assert verdict(processed=MIN_SAMPLE, pdfs=0, names=0, errors=0) == 1
        assert verdict(processed=MIN_SAMPLE - 1, pdfs=0, names=0, errors=0) == 0

    def test_mostly_errors_fails(self):
        assert verdict(processed=200, pdfs=5, names=50, errors=60) == 1

    def test_a_few_dead_sites_do_not_fail_a_sweep(self):
        """The case that made the original rule useless: 5 bad sites in 220."""
        assert verdict(processed=220, pdfs=195, names=5830, errors=5) == 0


class TestMatchesProductionRuns:
    """Replayed from scrape_log."""

    @pytest.mark.parametrize(
        "note,processed,pdfs,names,errors,expected",
        [
            ("825 discovered=1 pdfs=0 names=0", 1, 0, 0, 0, 0),
            ("819 discovered=57 pdfs=216 names=9624", 57, 216, 9624, 0, 0),
            ("740 discovered=220 pdfs=195 names=5830 err=5", 220, 195, 5830, 5, 0),
            ("741 discovered=112 pdfs=87 names=1402 err=0", 112, 87, 1402, 0, 0),
        ],
    )
    def test_replay(self, note, processed, pdfs, names, errors, expected):
        assert verdict(processed, pdfs, names, errors) == expected, note
