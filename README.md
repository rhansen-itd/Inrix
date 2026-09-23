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

The **segment table** (below the map) does triple duty and is two-way linked with
the map: **edit a friendly name inline** and click **Save names** to write the
names CSV (the in-app editor supersedes hand-editing the template); **tick rows**
to set an explicit corridor/network **member set** — the aggregate panels then sum
exactly the selected segments and the map dims the rest; and the **Coverage %** and
**Completeness cost** columns flag which segments cost the complete-set rule
timestamps. A highlighted row (cost > 0) is a chronically-missing segment —
deselect it to recover the timestamps its absence was dropping and get a more
complete aggregate series (`speed.segment_coverage`; see
[DATA_FORMAT.md](DATA_FORMAT.md)). Clicking a segment on the map highlights and
scrolls to its table row; clicking a row selects that segment for the panels.

Above the map, the **Show directions** control declutters a road's two directions:
a co-located opposing pair (e.g. NB/SB) shares nearly the same polyline, so by
default both are drawn with a small perpendicular **offset** (toggle *Offset
overlapping*) that separates them side-by-side and keeps both clickable. The
compass multiselect (**N (+) / E (+) / S (−) / W (−)** — N & E positive, S & W
negative) filters which directions render; leave it empty to show every direction.
Direction comes from the metadata `Direction` field via `geometry.direction_group`
/ `direction_sign` and the display-only `geometry.offset_overlapping_segments`
(the analytic geometry is never moved). The settings sit in the left column with
the map, segment table, and chart panels stacked in the right column.

The map uses **Plotly native maps** (MapLibre `open-street-map`, no Mapbox token).
The GUI is a thin shell: `gui/figures.py` turns compute-core DataFrames into
figures and `gui/app.py` only wires inputs to them — all statistics stay in
`src/inrix_tools/` (see [CLAUDE.md](CLAUDE.md)).

## Build the INRIX-vs-reference validation report

An external travel-time log (the TT Logger workbook) is compared to INRIX corridor
by corridor and written out as a small static site:

```bash
python scripts/build_validation_report.py \
    --export out/extracted_query_segments_2026.csv \
    --workbook "TT Logger.xlsx" \
    --network USA_Idaho_shapefile.zip \
    --network-cache geometry_cache/d3_validation_network.geoparquet \
    --route-segments scripts/d3_place_name_routes.json \
    --bbox -116.9 43.40 -115.80 44.65 \
    --out-dir out/validation_report
```

The script is wiring only: chains, statistics and gates come from
`inrix_tools.{corridors,reference,agreement}`, `gui/validation_figures.py` and
`gui/validation_report.py` place those values in figures and pages, and the summary
tables are written beside the HTML as CSV. `--route-segments` is for reference
sheets whose endpoints are place names rather than coordinates (see
[DATA_FORMAT.md](DATA_FORMAT.md)); omit it and those sheets are skipped, with the
bbox then derived from the coordinate routes. Output lands under `out/`, which is
gitignored — regenerate it rather than committing it.

## Rank a district's corridors (district-wide screening)

Which corridors in a district are worst — the prior question to analysing a corridor
you have already named. It runs off the DuckDB store, so the export must be ingested
first (the explorer's "Ingest export" control, or `store.ingest_export_streaming`):

```bash
python scripts/run_district_screening.py \
    --db d3_store.duckdb \
    --catalogue scripts/d3_corridors.json \
    --repairs scripts/d3_link_repairs.csv \
    --network USA_Idaho_shapefile.zip \
    --network-cache geometry_cache/d3_network.geoparquet \
    --aadt Cumulative_AADT.zip \
    --out-dir out/district_screening
```

Wiring only: `screen.segment_screen` reduces the store to one row per segment per
named peak window, `corridors.resolve_catalogue` walks each corridor's extent,
`aadt.join_aadt` supplies the mainline-preferred volume weights, and
`screen.rank_corridors` produces the ranking. It writes `corridor_rankings.csv`,
`corridor_resolution.csv`, a per-corridor `corridors.kml` and a
`screening_provenance.json` — and the CSVs carry that provenance in a `#` header, so
a ranking read off disk still states the area, dates, CValue gate, windows, catalogue
and repair table it was computed under.

It writes **two rankings**. A catalogue entry is one *direction* of one extent,
because that is the unit the network walk and the AADT join work in; a **reporting
corridor** is the road — both directions of it — which is the unit a district reads a
ranking in. `reporting_corridor_rankings.csv` is the headline view (District 3's 20
entries group into 10 roads), `corridor_rankings.csv` keeps the per-direction detail,
and the printed summary shows both. Nothing is averaged across directions and the two
carriageways' miles are never summed — they run over the same ground. Note also that
a grouped row sums its directions *at the same clock time*, so a commute corridor
whose directions peak in different windows carries a second number,
`vhd_directional_peaks`, that takes each direction at its own peak (see
[DATA_FORMAT.md](DATA_FORMAT.md)).

The headline table is `corridor_peak_totals.csv`: one row per reporting corridor,
**totalled over every peak window and both directions**, ranked on **vehicle-hours of
delay per mile** — volume-weighted, but length does not decide the order (`--rank-by`
changes it; every metric's rank travels in the frame either way, and without an AADT
join the run falls back to the unweighted rate and says so). `corridor_breakout.csv` is the same cells
unaggregated — a `(corridor, direction, window)` MultiIndex — and the printed summary
interleaves them, each corridor's total followed by every one of its direction × peak
rows.

Two behaviours worth knowing. **A corridor that does not resolve is not ranked** — it
is listed separately with its `stop_reason` and coverage rather than carried in with
blank metrics. And `--repairs` is the Item 38 topology patch table: without it seven
of the twenty District 3 corridors do not walk (I-184 not at all), so an absent table
stops the run rather than silently ranking thirteen. `--no-repairs` walks
`NextXDSegI` exactly as published, deliberately.

Note that one call ingests a **whole** export: handing `ingest_export_streaming` any
`..._part_N.zip` reads every sibling part, and its `n_rows_added` is the total across
all of them (`n_parts` / `parts` say which). Output lands under `out/`, which is
gitignored — regenerate it rather than committing it.

## Generate a district's catalogue instead of drawing it

The corridor catalogue a screening run reads is itself derived from the network for
Districts 1, 2, 4, 5 and 6 (District 3's is the Item 44 empirical rebuild):

```bash
# 1. screen once, so the extent pass has TTI to read (any catalogue will do)
python scripts/run_statewide_screening.py --mode full --districts 1 2 4 5 6

# 2. generate the catalogues from the network + that screening
python scripts/build_statewide_catalogues.py          # --dry-run to verify only

# 3. re-screen on the generated catalogues
python scripts/run_statewide_screening.py --mode full --districts 1 2 4 5 6
```

`extents.enumerate_mainline_chains` walks each numbered route into directional
chains, `extents.analyse_chain` cuts them at detected split points (urban/rural FRC
transitions, highway junctions, AADT step-changes, congestion discontinuities), and
each facility is emitted at up to three scales — **Tier 1 Congested Core**, **Tier 2
Commuter Corridor**, **Tier 3 Regional Baseline** — so the ranking can show how much
of a bottleneck's delay density a longer extent dilutes away. Every entry's
`description` states the split that ended it. `couplets.detect_couplets` finds the
one-way couplets topologically and pairs them under one reporting corridor.

Two rules worth knowing. **A catalogue is written only when every entry resolves**
through `corridors.resolve_catalogue`; a failed verification leaves the file as it
was rather than shipping a broken catalogue that looks like a good one. And **only a
measured bottleneck is catalogued**: a facility whose peak TTI never reaches 1.20
over a core of at least 0.75 miles is not a corridor, which is why the generated
District 4/5/6 catalogues drop I-84 at Twin Falls (peak TTI 1.05), I-15 at Pocatello
(1.02) and I-15 at Idaho Falls (1.08) that the hand-built ones carried. The
predecessor catalogues are kept in `legacy/handbuilt_catalogues/` for diffing.

`scripts/aggregate_statewide_rankings.py` reads the tiers back out of the generated
catalogues for `statewide_extent_tiers_comparison.csv`, and
`out/statewide_screening/couplet_registry_validation.csv` scores the detector against
`couplets.KNOWN_COUPLETS` — it reports rather than asserts, because several registry
entries name a leg the XD network carries no route number for.

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
