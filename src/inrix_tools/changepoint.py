"""Thin adapter over ``traffic_anomaly.changepoint`` for INRIX data.  (ROADMAP Item 5 — stub)

Locates *when* a persistent shift occurred without hand-specifying the
before/after boundary — complements beforeafter.py for finding intervention
dates. traffic_anomaly is a dependency, not vendored (see CLAUDE.md).
"""
from __future__ import annotations

_ITEM = "ROADMAP Item 5 (changepoint.py)"


def detect_changepoints(df, value="Travel Time(Minutes)", rolling_window_days=14,
                        score_threshold=5):
    """Detect changepoints per segment via traffic_anomaly.changepoint; surface
    score / avg_before / avg_after / avg_diff."""
    # from traffic_anomaly import changepoint  # import inside once implemented
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")


def changepoints_near(changepoints, known_dates, window_days=7):
    """Relate detected changepoints to known intervention dates (nearest within
    a window) — did a shift line up with a retiming / construction date?"""
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")
