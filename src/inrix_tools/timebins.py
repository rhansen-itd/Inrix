"""Day-group and time-of-day binning.  (ROADMAP Item 2 — stub)

Ports ``map_day_group`` / ``assign_time_chunks`` from ``_Plot Speed.ipynb`` as
pure, vectorized, overnight-safe functions. Binning is done in local time.
"""
from __future__ import annotations

_ITEM = "ROADMAP Item 2 (timebins.py)"

DEFAULT_DAY_GROUPS = {  # day-of-week (0=Mon) -> group label
    0: "Mon–Thu", 1: "Mon–Thu", 2: "Mon–Thu", 3: "Mon–Thu",
    4: "Fri", 5: "Sat", 6: "Sun",
}


def assign_day_group(df, scheme=None):
    """Add a ``Day Group`` column from local day-of-week (configurable scheme)."""
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")


def assign_time_bins(df, bins):
    """Add a ``Time Bin`` column from bin edges like ``"6:30AM-9:00AM"``,
    including overnight bins (e.g. ``"9:00PM-6:00AM"``). Vectorized."""
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")
