# inrix_tools

Tools for loading, exploring, and running before/after analysis on **INRIX XD
segment travel-time data** (the 5-minute speed/travel-time exports from INRIX
Roadway Analytics). Grew out of a handful of notebooks for one-off corridor
studies; this repo is the effort to turn those seeds into robust, tested,
reusable tools with a data-exploration GUI.

## Why

The starting point was two notebooks in the repo root:

- **`_Plot Speed.ipynb`** — load an INRIX export + segment metadata, convert to
  local time, bin by day-group × time-of-day, plot speeds/travel times, and run
  a before/after t-test between two date periods.
- **`_metadata KML.ipynb`** — turn segment metadata (lat/lon endpoints) into a
  KML file for mapping.

They work, but compute and plotting are fused, paths are hardcoded, config is
copy-pasted between cells, and the statistics are a first pass. This project
refactors that into a pure-Python compute core with a thin GUI shell, and
upgrades the before/after analysis (see [DATA_FORMAT.md](DATA_FORMAT.md) and
[ROADMAP.md](ROADMAP.md)).

## Design constraints

- **Compute core is pure Python — no plotting, no GUI imports.** All I/O, time
  binning, aggregation, and statistical analysis live in `src/inrix_tools/`
  and are pytest-testable headless. Plotting and the Dash app are thin shells
  over it. This is the load-bearing rule (see [CLAUDE.md](CLAUDE.md)); it is
  what made the sibling `iprj_designer` project's GUI swappable, and it keeps
  the analysis reusable outside any UI.
- **`traffic-anomaly` is a dependency, not vendored.** Robust decomposition and
  changepoint detection come from the MIT-licensed
  [`traffic-anomaly`](https://pypi.org/project/traffic-anomaly/) package via a
  thin adapter that maps INRIX column names onto its schema. See CLAUDE.md for
  when to reconsider.
- **GUI: Plotly Dash.** The data explorer is a Dash app under `gui/`, kept
  optional (`pip install -e .[gui]`) so the compute core installs without the
  web stack.

## Layout

```
Inrix/
├── README.md  ROADMAP.md  DESIGN_HISTORY.md  CLAUDE.md  DATA_FORMAT.md
├── pyproject.toml
├── src/inrix_tools/     # pure compute core (no plotting / GUI)
│   ├── io.py            # load INRIX data.csv/zip + metadata.csv
│   ├── timebins.py      # day-group + time-of-day binning
│   ├── speed.py         # speed / travel-time aggregation
│   ├── decompose.py     # thin adapter over traffic_anomaly.decompose
│   ├── beforeafter.py   # decomposition-based before/after (+ t-test baseline)
│   ├── changepoint.py   # thin adapter over traffic_anomaly.changepoint
│   ├── geometry.py      # segment polylines from the INRIX XD shapefile
│   └── kml.py           # segment geometry -> KML
├── gui/app.py           # Plotly Dash explorer + embedded map (thin shell)
└── tests/               # pytest
```

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .          # compute core
pip install -e .[gui,dev] # + Dash explorer + test tooling
```

## Run the explorer

```bash
pip install -e .[gui]
python gui/app.py         # serves http://127.0.0.1:8050
```

Then, in the browser: set the **export** path (defaults to the Myrtle sample
`.zip`), timezone, and CValue threshold, and click **Load export**. The embedded
map is the primary selector — click a segment to drive the panels below (raw time
series, day-group × time-bin summary, before/after effect + CI, and decomposition
+ changepoints). Set the **before**/**after** date ranges to run the comparison;
switch the map colouring between segment mean and the before/after Δ; and **Export
KML** writes the current colouring to `out/segments.kml`. The **time-of-day
window** slider restricts every panel, the map, and the export to a chosen part of
the day (e.g. the 4–6PM peak) — the decomposition/changepoint runs on that window
on its own terms; leave it at the full 0–24h span for no filter. **Restrict dates**
narrows the session to a calendar sub-range at load — the loaded frame really
shrinks, so a few weeks of a multi-month export stays snappy; leave it at the full
span (the default) to keep everything, or widen it and re-**Load** to restore.

The map uses **Plotly native maps** (MapLibre `open-street-map`, no Mapbox token).
The GUI is a thin shell: `gui/figures.py` turns compute-core DataFrames into
figures and `gui/app.py` only wires inputs to them — all statistics stay in
`src/inrix_tools/` (see [CLAUDE.md](CLAUDE.md)).

## Documents

- [ROADMAP.md](ROADMAP.md) — planned work as named, numbered, session-sized
  items ordered by priority, each with a suggested prompt.
- [DESIGN_HISTORY.md](DESIGN_HISTORY.md) — build log and decisions, appended
  per session.
- [DATA_FORMAT.md](DATA_FORMAT.md) — the INRIX export schema, units, timezone
  handling, and quirks (CValue, Ref/Hist speeds, corridor summation).
- [CLAUDE.md](CLAUDE.md) — working conventions for Claude Code sessions here.

## Status

**Session 0 (2026-07-16) — scaffolding.** Documentation pipeline, package
skeleton, and `pyproject.toml` in place. Remaining `src/inrix_tools/` modules are
stubs that raise `NotImplementedError` pointing at their ROADMAP item; the
original notebooks in the repo root are the seeds those items port from.

**Session 1 (2026-07-16) — `io.py` (Item 1) done.** Typed, tz-aware INRIX loader:
`load_data` (streams `data.csv` from the `.zip`, split-part aware), `load_metadata`,
DST-correct `to_local`, `filter_cvalue`, and the complete-set corridor flag.

**Session 2 (2026-07-16) — `geometry.py` (Item 8) done.** Segment geometry layer
from the INRIX XD network shapefile: `load_xd_network` (subset by segment id via a
pushed-down WHERE, EPSG:4326, optional GeoParquet cache), `segment_geometry`
(`Segment ID → LINESTRING` with a flagged straight-line fallback),
`connectivity_table` (`next_id` from the XD topology), and `to_geojson`. All 46
real Myrtle segments resolve to road-following polylines. **23 tests pass**
(`.venv/bin/pytest`).

**Sessions 3–7 (2026-07-16) — the compute core.** `timebins.py` (Item 2),
`speed.py` (Item 3), `decompose.py` + `beforeafter.py` (Item 4), `changepoint.py`
(Item 5), and `kml.py` (Item 6) — see [DESIGN_HISTORY.md](DESIGN_HISTORY.md).

**Session 8 (2026-07-16) — Dash explorer + embedded map (Item 7) done.** The
interactive explorer under `gui/` as a thin shell over the compute core: a
Plotly-native OSM map of real segment polylines as the primary selector driving
time-series / day×time summary / before-after (effect + CI) / decomposition +
changepoint panels, before/after date pickers, CValue control, and a KML export
button. **103 tests pass.** This completes the scoped ROADMAP (Items 1–8);
remaining work is the Future section.
