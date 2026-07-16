"""Before/after intervention analysis.  (ROADMAP Item 4 — stub)

Primary method is decomposition-based (strip seasonality via ``decompose.py``,
then compare seasonally-adjusted values / residuals between periods, reporting
effect size + CI). The seed notebook's t-test is retained only as a labeled
baseline — see the caveats on ``ttest_baseline``.
"""
from __future__ import annotations

_ITEM = "ROADMAP Item 4 (beforeafter.py)"


def compare_periods(df, before, after, value="Travel Time(Minutes)",
                    by=None, use_decomposition=True):
    """PRIMARY. Compare a before vs after date range on seasonally-adjusted
    values / residuals, per segment (and optionally per day-group×time-bin).
    Reports effect size + confidence interval and n — not just a p-value."""
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")


def ttest_baseline(df, before, after, value="Travel Time(Minutes)", by=None):
    """BASELINE ONLY (interpretable, not robust). One-sample/paired t-test on
    period differences, ported from the seed notebook.

    Caveats, by design: 5-min samples are autocorrelated so the t-test
    understates p-values, and running it across many bins×segments is a
    multiple-comparisons problem. Prefer ``compare_periods``; keep this for
    comparison / continuity with the notebook results.
    """
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")
