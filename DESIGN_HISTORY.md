# Design History — inrix_tools

Build log and design decisions, appended per session. See [ROADMAP.md](ROADMAP.md)
for what's planned; this file records what landed and why. Convention borrowed
from the sibling `iprj_designer` project.

**Architecture rule that makes the phasing work:** everything under
`src/inrix_tools/` is pure Python — no plotting, no GUI imports, no hardcoded
paths. Plotting and the Dash app are a thin shell over it. This is what keeps
the analysis reusable and the GUI framework swappable (codified in
[CLAUDE.md](CLAUDE.md)).

Layout as scaffolded:

```
Inrix/
├── README.md / ROADMAP.md / DESIGN_HISTORY.md / CLAUDE.md / DATA_FORMAT.md
├── pyproject.toml
├── src/inrix_tools/   # pure compute core (stubs until built per ROADMAP)
├── gui/               # Plotly Dash shell
└── tests/             # pytest
```

---

## Session 0 — Scaffolding & documentation pipeline (2026-07-16)

Set up the project to grow from a few notebooks into robust, tested tools. No
compute implemented; this session is structure + decisions.

Done:
- [x] Documentation pipeline mirroring `iprj_designer`: `README.md`,
      `ROADMAP.md`, `DESIGN_HISTORY.md`, `CLAUDE.md`, and `DATA_FORMAT.md` (the
      INRIX-export analog of that project's `IPRJ_FORMAT.md`).
- [x] `pyproject.toml` — `inrix_tools` package, deps pinned (pandas, numpy,
      plotly, scipy, `traffic-anomaly`); Dash + dev tooling as optional extras.
- [x] Package skeleton `src/inrix_tools/` with stub modules (`io`, `timebins`,
      `speed`, `decompose`, `beforeafter`, `changepoint`, `kml`) each raising
      `NotImplementedError` pointing at its ROADMAP item; `gui/app.py` and
      `tests/` placeholders.
- [x] `.gitignore` extended to exclude raw INRIX exports (large + EULA-restricted:
      `data/`, `*.zip`, `data.csv`) and generated output.
- [x] ROADMAP scoped into 7 session-sized items + a Future section.

Decisions (from the owner, 2026-07-16):

- **`traffic-anomaly`: depend, don't vendor.** It's MIT, on PyPI, CI-tested,
  ~1000 lines of non-trivial Ibis/DuckDB code. Depending keeps upstream fixes;
  vendoring would only pay off to fork the decomposition math. INRIX data
  (multi-entity segment travel-time series at 5-min freq) is a direct fit for
  its `decompose`/`changepoint`. Accepted cost: it pulls `ibis-framework[duckdb]`.
  Wrapped by a thin adapter (`decompose.py` / `changepoint.py`) that maps INRIX
  columns onto its schema. Reconsider only if a real requirement forces forking
  the math — record it here if so.

- **Analysis scope: decomposition-based before/after + changepoint detection.**
  The seed t-test is kept only as a labeled *baseline* (Item 4), not the
  primary — 5-min samples are autocorrelated (t-test p-values overstated) and
  running it across many bins×segments is a multiple-comparisons problem. The
  robust primary is: `decompose` to strip daily/weekly seasonality, then compare
  seasonally-adjusted values/residuals between periods with **effect size + CI**.
  Changepoint detection locates *when* a persistent shift happened without
  hand-specifying the boundary. Anomaly/incident flagging (`anomaly()`,
  z-score/GEH) deferred to Future.

- **GUI: Plotly Dash** (owner's choice; diverges from `iprj_designer`'s NiceGUI).
  Purpose-built for reactive data dashboards and embeds the existing Plotly
  figures. Kept an optional extra so the compute core installs without the web
  stack. The pure-core/thin-shell rule holds regardless of framework.

- **Notebooks stay as seeds.** `_Plot Speed.ipynb` and `_metadata KML.ipynb`
  remain in the repo root as the historical seeds; ROADMAP items port *from*
  them into `src/` rather than editing them in place. Superseded code will go to
  `legacy/` rather than being deleted.

Next: Item 1 (`io.py`) — it underpins everything else.

---

## Session 0.1 — Mapping plan: embedded map + real geometry (2026-07-16)

Owner wants the map embedded in the app (KML stays as an export, not the primary
view) and segment shapes that **follow the road**, not straight endpoint lines.

Decision — **use the official INRIX XD network shapefile; drop OSM.** The owner
supplied `USA_Idaho_shapefile.zip` (statewide Idaho, 41,770 segments, EPSG:4326,
PolyLine geometry). Verified: its `XDSegID` joins our `Segment ID` directly (all
sampled Myrtle/Franklin segments resolve, road names match), geometry is
multi-vertex road-following LINESTRINGs, and `NextXDSegI`/`PreviousXD` provide a
free connectivity table. This turns segment geometry from a planned OSM
map-matching pipeline into a **subset + join + cache lookup**. OSM per-segment
matching is demoted to a documented Future fallback for segments outside the XD
shapefile only.

Quirk recorded in DATA_FORMAT.md: the shapefile `.dbf` stores every field as
`C(255)` (266 MB unzipped, numerics-as-strings) — the geometry layer subsets to
an export's segments and caches a typed GeoParquet.

ROADMAP changes:
- Added **Item 8 — Segment geometry layer** (`geometry.py`): load/subset/cache
  the XD shapefile, `Segment ID → LINESTRING` with straight-line fallback,
  `connectivity_table` from the topology fields. Placed by priority before the
  mapping items (6, 7) though its stable ID is higher.
- **Item 6 (KML)** now draws real polylines from Item 8 (was straight endpoints).
- **Item 7 (GUI)** now leads with an embedded interactive map as the primary
  selector; map framework (dash-leaflet vs Plotly native maps) deferred to
  build time.
- Future: connectivity-aware/originated anomalies and auto corridor assembly now
  get their connectivity table free from Item 8; OSM matching kept as fallback.

Added a `geo` optional extra (geopandas/shapely/pyogrio); `gui` pulls it in.
Gitignored the shapefile + extracted components + geometry cache (licensed).

---

## Session 1 — Data I/O core `io.py` (ROADMAP Item 1) (2026-07-16)

The foundation. Ported the load cells of `_Plot Speed.ipynb` into a pure, typed,
tested loader. Done in the scaffolding session (context still warm) rather than
handing off cold.

Built (`src/inrix_tools/io.py`):
- `load_data(source, nrows=None)` — reads `data.csv` straight from the INRIX
  `.zip` (or a dir / csv / list), auto-discovers sibling `..._part_N.zip` splits,
  parses `Date Time` tz-aware (UTC), coerces dtypes (`Segment ID`→int64, metrics
  numeric, `Road Closure`→bool), and records detected units on `df.attrs`.
- `load_metadata(source)` — typed, `Segment ID`-indexed, with a `Combined`
  Road+Direction+Intersection label.
- `to_local(df, tz)` — **DST-correct** UTC→IANA conversion + local
  `day_of_week`/`time_of_day`. Fixes the notebook's fixed-`Timedelta` offset,
  which silently mishandled the MST/MDT switch.
- `filter_cvalue(df, threshold=80)` — tunable, threshold stored on `df.attrs`.
- `mark_complete_timestamps(df)` — the complete-set corridor rule as a flag.

Two bugs caught by the real-export test (not just synthetic):
1. **Zip member suffix collision** — `"metadata.csv".endswith("data.csv")` is
   True, so a suffix match grabbed metadata when it sorted first (as the Myrtle
   zip does). Fixed to exact-basename match. The synthetic fixture had passed by
   accident of insertion order — the real fixture is what exposed it.
2. Test wrongly assumed `Speed` is float; INRIX speeds are integers. Both
   findings recorded in DATA_FORMAT.md.

Tests: `tests/test_io.py` (synthetic zip fixture: types/units, DST, CValue,
complete-set, split discovery) + a real-export slice test that skips when the
licensed Myrtle zip is absent. `tests/test_scaffold.py` updated (io is no longer
a stub). **17 pass.** Verified end-to-end on 20k real rows.

Environment: created `.venv` at repo root; installed the package editable
`--no-deps` + `pandas`/`pytest` only — the heavy `traffic-anomaly` and `geo`
stacks are deferred until Items 4/5/8 need them, keeping this session lean.

Next: Item 2 (`timebins.py`) or, for the mapping path, Item 8 (`geometry.py`) —
both need only Item 1.

---

## Session 2 — Segment geometry layer `geometry.py` (ROADMAP Item 8) (2026-07-16)

Turned the owner-supplied INRIX XD network shapefile into the geometry layer.
Continued in-session off Item 1 (io context warm).

Built (`src/inrix_tools/geometry.py`):
- `load_xd_network(source, segment_ids=None, bbox=None, cache_path=None)` — reads
  the shapefile from its `.zip` via GDAL `/vsizip/` (pyogrio engine), pushes the
  export's segments down as an `XDSegID IN (...)` WHERE clause so only the needed
  features leave the 266 MB statewide `.dbf`, casts the all-`C(255)` fields
  (nullable `Int64` ids, float miles/lat-long), optional GeoParquet cache.
- `segment_geometry(network, segment_ids, metadata)` — `Segment ID → LINESTRING`
  with a straight-line endpoint fallback, flagged via a `source` column
  (`xd`/`fallback`/`missing`).
- `connectivity_table(network, direction)` — `Segment ID`/`next_id` from
  `NextXDSegI`/`PreviousXD`; the `next_id` shape matches
  `traffic_anomaly.anomaly`'s `connectivity_table`.
- `to_geojson` — FeatureCollection string for the map/KML layers.

Verified on real data: all **46** Myrtle segments resolve to real multi-vertex
LINESTRINGs (min 3 / median 7 / max 23 vertices), EPSG:4326, zero unmatched —
confirming the shapefile fully covers the study area and the OSM matcher stays
unneeded. The S 9th St connectivity chain (`1187539993 → 448695937`) links.

Design notes:
- **Reader pushdown over post-filter.** Subsetting via pyogrio's `where=` (not
  loading everything then filtering in pandas) is what keeps the statewide file
  cheap; the WHERE runs in GDAL.
- **Nullable ints for ids.** `PreviousXD`/`NextXDSegI` are blank at network
  ends; cast to `Int64` so a terminal is `<NA>`, not a spurious `0`.
- `geo` stack (geopandas 1.1 / pyogrio 0.13 / shapely 2 / pyarrow) installed into
  `.venv`; still no `traffic-anomaly` (deferred to Items 4/5).

Tests: `tests/test_geometry.py` — synthetic layer (fallback flagging,
connectivity, GeoJSON) with no big-file dependency, plus real-shapefile tests
(subset resolves all Myrtle segments, connectivity chain, cache round-trip) that
skip when the licensed data is absent. **23 pass** across the suite.

Next: Item 6 (`kml.py`) now draws real polylines from this layer; Item 7 (the
Dash app + embedded map) consumes it. Item 2 (`timebins.py`) is the other
independent thread.

