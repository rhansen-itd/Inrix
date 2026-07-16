"""Speed / travel-time aggregation — compute only, no plotting.  (ROADMAP Item 3 — stub)

Splits the seed notebook's ``process_and_plot_*`` functions into their compute
half. Plotting lives in the GUI / notebooks, never here (see CLAUDE.md).
"""
from __future__ import annotations

_ITEM = "ROADMAP Item 3 (speed.py)"


def segment_summary(df, value="Speed(miles/hour)"):
    """Per (segment, day-group, time-bin) stats: count, mean, std, median."""
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")


def daily_timebin_summary(df, value="Speed(miles/hour)"):
    """Daily mean ± SD per time-bin series (the ``process_and_plot_timebin_daily_
    summary`` payload as a DataFrame)."""
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")


def corridor_travel_time(df, metadata):
    """Segment -> corridor travel-time summation with the complete-set rule
    (only timestamps where every member segment reported). See DATA_FORMAT.md."""
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")
