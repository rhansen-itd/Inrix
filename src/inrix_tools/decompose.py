"""Thin adapter over ``traffic_anomaly.decompose`` for INRIX data.  (ROADMAP Item 4 — stub)

Maps INRIX column names onto the traffic_anomaly schema and sets INRIX defaults
(freq_minutes=5, entity = 'Segment ID'). traffic_anomaly is a *dependency*, not
vendored — do not copy its source here (see CLAUDE.md). If one function ever
needs tweaking, copy that function with its MIT attribution, not the package.
"""
from __future__ import annotations

_ITEM = "ROADMAP Item 4 (decompose.py)"


def decompose_segments(df, value="Travel Time(Minutes)", freq_minutes=5,
                       rolling_window_days=7):
    """Decompose per-segment series into trend / daily+weekly season / residual
    via traffic_anomaly.decompose. Returns the decomposed frame."""
    # from traffic_anomaly import decompose  # import inside once implemented
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")
