"""Plotly Dash explorer for INRIX data — a thin shell over ``inrix_tools``.

``app.py`` builds the Dash app (layout + callbacks; wiring only), ``figures.py``
turns compute-core DataFrames into Plotly figures. No statistics live here — that
is the pure ``src/inrix_tools/`` core (see CLAUDE.md). ROADMAP Item 7.
"""
