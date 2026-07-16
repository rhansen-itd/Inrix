"""Plotly Dash data explorer — thin shell over inrix_tools.  (ROADMAP Item 7 — stub)

A thin shell over the compute core: callbacks call ``inrix_tools.*`` and render
the returned DataFrames as figures — no compute logic lives here (see CLAUDE.md).
Run once built:  python gui/app.py   (install with  pip install -e .[gui])
"""
from __future__ import annotations

_ITEM = "ROADMAP Item 7 (gui/app.py)"


def build_app():
    """Construct and return the Dash app (segment picker; time-series,
    summary, before/after, and decomposition+changepoint views)."""
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")


if __name__ == "__main__":
    raise SystemExit(f"Not built yet — see {_ITEM}.")
