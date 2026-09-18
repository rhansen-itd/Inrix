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

---

## Session 3 — Time binning `timebins.py` (ROADMAP Item 2) (2026-07-16)

Ported the day-group / time-of-day binning — the reusable heart of the seed —
out of `_Plot Speed.ipynb` into pure, vectorized functions. The notebook had
**two** copies: the inline binning in `process_and_plot_*` and the later
`map_day_group` / `assign_time_chunks`. They disagreed, so this consolidates the
better parts of each.

Built (`src/inrix_tools/timebins.py`):
- `assign_day_group(df, scheme=None)` — local day-of-week → group label via a
  vectorized `.map`. `scheme` is a `{dayofweek: label}` dict (fully explicit) or
  a list of `"Monday-Thursday"`-style weekday range specs (parsed, wrap-around
  allowed) — no hardcoded scheme. Default `DEFAULT_DAY_GROUPS` = Mon–Thu / Fri /
  Sat / Sun. Unmapped days → `pd.NA`.
- `assign_time_bins(df, bins)` — clock ranges like `"6:30AM-9:00AM"`, wrapping
  overnight (`"9:00PM-6:00AM"`). Reduces time-of-day to seconds-since-midnight
  once, then assigns each bin with a boolean mask (no per-row `.apply`).
- `assign_group_label(df)` — composes `"Mon–Thu, 2:00PM-7:00PM"`; NA in either
  part → NA label (matches the seed's `dropna(subset=['Group'])`).
- `parse_clock` / `parse_time_bin` helpers — robust `%I:%M%p` (spaces, case,
  minutes-optional `"9PM"`, `12:00AM`=midnight / `12:00PM`=noon).

Two deliberate fixes to the seed:
1. **Half-open bins `[start, end)`.** `process_and_plot_*` used `start <= t <=
   end`, so a boundary timestamp (`2:00PM`) fell in *both* contiguous bins and
   was double-counted. The later `assign_time_chunks` already used half-open; we
   standardize on it. Bins are assumed non-overlapping; on overlap the
   first-listed bin wins (documented, tested).
2. **Vectorized, not `.apply`.** The seed mapped a Python function over every
   row; here it's array masks over an integer seconds-of-day vector.

Binning reads the **local wall clock** (`.dt.hour` on the tz-aware local
timestamp), so it's DST-correct for free once `io.to_local` has run — an 08:00
row bins to the morning slot on both sides of the MST/MDT switch. Tests assert
this across the 2026-03-08 spring-forward, including that the non-existent
02:00–03:00 gap hour doesn't break neighboring rows.

Tests: `tests/test_timebins.py` — purely synthetic (no export needed): clock/bin
parsing, half-open edge inclusivity, overnight wrap, unassigned → NA,
first-listed-wins on overlap, end-at-midnight, configurable dict + range-spec
day schemes, DST wall-clock binning, group-label composition, attrs preserved /
input unmutated. **16 pass, 39 total.**

No `io.py` / `DATA_FORMAT.md` change — this item is pure binning logic and
learned nothing new about the export format.

Next: Item 3 (`speed.py`) — segment / daily-timebin / corridor aggregation —
now has both its dependencies (Items 1, 2) and consumes these bins directly.

---

## Session 4 — Speed / travel-time aggregation `speed.py` (ROADMAP Item 3) (2026-07-16)

The core "undo the compute+plot fusion" item. Continued in-session off Item 2
(io + timebins context warm; the seed's `process_and_plot_*` functions already
read in full) — the dependency overlap made a warm continuation the right call
over a cold restart.

Built (`src/inrix_tools/speed.py`) — the **compute halves** of the seed's
`process_and_plot_*`, each returning a typed DataFrame, **no plotting imports**:
- `segment_summary(df, values=None, group_cols=None)` — per (segment, day-group,
  time-bin) `count/mean/std/median` for each metric, flattened to
  `"<value>_<stat>"` columns. Value columns auto-detected by prefix
  (`metric_columns`) so the unit isn't hard-coded. `dropna=True` drops
  unassigned bins (the seed's `dropna(subset=['Group'])`).
- `daily_timebin_summary(df, value=None, ...)` — per-date `Mean/Std` with
  `Upper/Lower = Mean ± Std` bands per (segment, group), the
  `process_and_plot_timebin_daily_summary` payload minus the figure.
- `corridor_travel_time(df, metadata=None, require_complete=True)` — segment→
  corridor travel-time sum under the **complete-set rule**, delegating to Item 1's
  `io.mark_complete_timestamps` rather than re-deriving it. `require_complete=
  False` keeps partial timestamps with a `complete` flag instead of dropping
  them. When `metadata` is supplied it adds corridor `Length(Miles)` and, for
  miles/minutes units, a space-mean `Corridor Speed(miles/hour)`.
- `rolling_average(df, value, window, group_cols, direction)` — the seed's
  rolling means pulled **out** of the summary into an explicit opt-in transform
  (trailing/leading/centered; grouped so it never rolls across a segment/day
  boundary).

Design notes:
- **Corridor label comes from the data, not metadata.** Grouping is on
  `Corridor/Region Name` (a `data.csv` column); `metadata` is optional and only
  used for length/speed — consistent with the Session-1 finding that raw
  `metadata.csv` has no corridor column.
- **Complete-set rule reuses the Item 1 helper** instead of the seed's inline
  `count == count.max()` merge — one source of truth for "did every segment
  report."
- **`metric_columns` prefix detection** keeps the module unit-agnostic (mph/kmh),
  matching `io.detect_units`.

One scaffold-test fix: `tests/test_scaffold.py`'s stub check moved off `speed`
(now built) onto `kml` (still a stub).

Tests: `tests/test_speed.py` — synthetic fixture with hand-computed aggregates:
segment stats (incl. singleton-group `std` NaN), daily mean±SD bands, corridor
complete-set drop + partial visibility + length/space-mean speed, rolling
trailing/leading + no-cross-boundary, and a guard that no plotting library is
imported. **16 pass, 55 total.**

No `DATA_FORMAT.md` change — pure aggregation, nothing new learned about the
export.

Next: Items 4 (`decompose.py`/`beforeafter.py`) and 5 (`changepoint.py`) — the
`traffic-anomaly` adapters — are the remaining analysis core; both need the
`traffic-anomaly` dep installed into `.venv` (deferred since Session 1).

---

## Session 5 — Decomposition + before/after `decompose.py` / `beforeafter.py` (ROADMAP Item 4) (2026-07-16)

The robust upgrade to the seed's t-test — the first `traffic-anomaly` adapters.
Installed the dep deferred since Session 1 (`traffic-anomaly` 2.5.4, pulling
`ibis-framework` 11 + `duckdb` + `scipy`).

Built (`src/inrix_tools/decompose.py`) — a **thin** adapter, no vendoring:
- `decompose_segments(df, value=None, freq_minutes=5, ...)` — wraps
  `traffic_anomaly.decompose` with INRIX defaults (`entity_grouping_columns=
  ['Segment ID']`, `Date Time` datetime, 5-min freq). `value` defaults to the
  detected `Travel Time(...)` column (then `Speed(...)`), unit-agnostic via
  `speed.metric_columns`. Returns trend (`median`) / `season_day` / `season_week`
  / `resid` / `prediction`; `attrs['decompose_value']` records the metric.
- `seasonally_adjust(decomposed)` — `value − season_day − season_week`
  (≡ `median + resid`): strips daily/weekly seasonality but **retains the
  level/trend** so a genuine intervention step change survives. This, not the raw
  residual, is what before/after compares — the residual's rolling median would
  partly absorb a persistent shift.

Built (`src/inrix_tools/beforeafter.py`):
- `compare_periods(...)` — **primary**. Decompose once, take the
  seasonally-adjusted series, split into before/after periods, and report a
  difference-in-means **effect size** with a **Welch (unequal-variance) CI**,
  Cohen's d, n per side, and a secondary Welch p-value. `by=` adds day-group ×
  time-bin grouping on top of per-segment; `use_decomposition=False` gives a raw
  (non-robust) cross-check labeled `method="raw"`.
- `ttest_baseline(...)` — the seed's `analyze_travel_time` ported faithfully: a
  **paired** t-test across time-of-day bins (default 15-min) per (segment, group).
  Kept only as a labeled baseline; docstring states the two reasons it isn't
  primary (5-min autocorrelation → overstated p-values; many segments×groups →
  multiple comparisons).
- `parse_period(period, tz)` — normalizes `(start, end)` or the seed's compact
  `"YYYYMMDD-YYYYMMDD"` into half-open tz-aware bounds; a **date-only end covers
  the whole calendar day** (rolls to next midnight via a DST-safe `DateOffset`).

Design decisions:
- **Seasonally-adjusted, not residual, for the level comparison.** The residual
  has the rolling median removed, which absorbs a step change over ~`drop_days`;
  comparing `median + resid` keeps the level so the intervention is visible.
  Verified on synthetic data that this recovers a known +2-min injected step.
- **Effect size + CI as the headline, p-value demoted.** Per CLAUDE.md/Session 0:
  report a difference in means with a CI and a standardized Cohen's d; the Welch
  form handles unequal before/after variance and sample sizes. The p-value is
  emitted but explicitly secondary.
- **Robustness demonstrated, not just asserted.** A test builds a weekly-season
  confound (before period includes a weekend, after is weekdays-only) and shows
  the raw comparison is biased while the decomposition recovers the true +2 —
  the concrete reason the decomposition method is primary.
- **`traffic_anomaly` passthrough quirks verified.** tz-aware timestamps survive
  decompose unchanged; extra columns (day-group/time-bin labels) are carried
  through so `by=` grouping works after decomposition; `drop_extras=False` is
  required to keep the season components (mapped to `keep_components=True`).
- **Heavy imports stay function-local.** `traffic_anomaly` and `scipy` import
  inside the functions (module import stays clean for the scaffold test); only
  `.io`/`.speed`/`.decompose` are imported at module scope.

Environment note: installing `traffic-anomaly` **downgraded pandas 3.0.3 → 2.3.3**
(`ibis-framework` 11 requires pandas <3). Within the `pandas>=2.0` pin and
harmless — full suite green on 2.3.3.

Tests: `tests/test_beforeafter.py` — synthetic 5-min series with daily+weekly
seasonality and an injected step: component/tz/adjust identities, period parsing
(date-only day, seed string, bad order), shift recovery with CI excluding 0, null
case CI straddling 0, `by`/multi-segment, thin-group skip, the seed t-test's
direction, method agreement in the easy case, and decomposition-beats-raw under
the seasonal confound. **14 pass, 69 total.** Also run end-to-end on the real
46-segment Myrtle export (both methods agree in sign/magnitude across an
MST→MDT-spanning window).

No `DATA_FORMAT.md` change — this item is analysis logic; nothing new learned
about the export format.

Next: Item 5 (`changepoint.py`) — the sibling `traffic-anomaly` adapter, deps now
installed. Then the mapping/GUI items (6, 7) and the KML export.

---

## Session 6 — Changepoint detection `changepoint.py` (ROADMAP Item 5) (2026-07-16)

The sibling `traffic-anomaly` adapter to Item 4 — locates *when* a persistent
shift happened without hand-specifying the boundary. Continued in-session off
Item 4 (dep installed, `traffic_anomaly` schema + INRIX adapter pattern + the
synthetic-fixture test approach all warm) rather than a cold restart.

Built (`src/inrix_tools/changepoint.py`) — thin, no vendoring:
- `detect_changepoints(df, value=None, rolling_window_days=14, score_threshold=5.0,
  ...)` — wraps `traffic_anomaly.changepoint` with INRIX defaults
  (`entity_grouping_column='Segment ID'`, `Date Time`, value defaults to the
  detected `Travel Time(...)` via `decompose.default_value_column`). Surfaces one
  row per detected shift: `score / avg_before / avg_after / avg_diff / pct_change`,
  input column names + tz + `df.attrs` preserved. `attrs['changepoint_value']`
  records the metric.
- `changepoints_near(changepoints, known_dates, window_days=7)` — relates
  detections to known intervention dates. For each (segment, known date) it
  reports that segment's **nearest** changepoint with a signed `days_off` and a
  `within_window` flag; the nearest is always reported (non-matches visible as
  `within_window=False`, not dropped — same "make partials visible" stance as the
  corridor complete-set rule). Accepts a scalar or an iterable of dates; naive
  dates are localized to the changepoints' tz.

Design decisions:
- **Recommend the seasonally-adjusted series, don't force it.** Running
  changepoint on raw travel time leaves daily/weekly seasonality in the signal,
  which can register as spurious shifts. Rather than couple this module to
  `decompose` (scope creep — Item 5 is "thin adapter, value selectable"), the
  docstring tells the caller to decompose + `seasonally_adjust` and pass that
  column as `value=`. Keeps the adapter one job.
- **Nearest-always over match-only** in `changepoints_near`, mirroring the
  project's preference for surfacing partials/near-misses instead of silently
  filtering — a segment that shifted nowhere near the date is informative.
- **Heavy imports stay function-local** (`traffic_anomaly` inside the function);
  module import stays clean for the scaffold test.
- **numpy 2.5 / pandas 2.3.3 skew:** `Timedelta` division tripped a numpy
  generic-unit `DeprecationWarning`; compute `days_off` via `.total_seconds()/86400`
  to stay clean (tests pass under `-W error::DeprecationWarning`).

Verified on the real Myrtle export: scanning six segments' travel time surfaced a
changepoint on segment **119036672 at 2026-03-31** (−27.8%) — the *same* segment
that carried the strongest before/after effect in Item 4, so the two independent
methods corroborate. `changepoints_near('2026-04-15', window_days=14)` correctly
flags it as 14.4 days off → `within_window=False`.

Tests: `tests/test_changepoint.py` — synthetic 5-min step series: detection at the
right date with right sign/magnitude, stationary → none (empty frame keeps its
columns), travel-time default, only-shifted-segments-appear, and
`changepoints_near` match / far-date-flag / multi-date / empty-input. **8 pass, 77
total.**

No `DATA_FORMAT.md` change — analysis logic; nothing new learned about the export.

With Items 4 and 5 done, the whole analysis core (io, timebins, speed,
decompose/beforeafter, changepoint) and the geometry layer are built. Remaining:
Item 6 (`kml.py`, Sonnet-eligible) and Item 7 (the Dash explorer + embedded map),
which consume the geometry layer and this analysis core.

---

## Session 7 — KML export `kml.py` (ROADMAP Item 6) (2026-07-16)

Ported `_metadata KML.ipynb` into one clean export, drawing the **real
road-following polylines** from the Item 8 geometry layer instead of the seed's
straight endpoint-to-endpoint lines. The notebook had **two** near-duplicate
`csv_to_kml` functions (cell 0 used a plain LineString + a separate label
Placemark; cell 1 wrapped LineString + hidden-pin Point in a `MultiGeometry` and
added a `base_dir` scatter-plot link) — this consolidates the better parts:
MultiGeometry + hidden pin, always-on-label toggle, shared per-colour styles.

Built (`src/inrix_tools/kml.py`):
- `geometry_to_kml(geo, out_path, *, label_segments=False, name_col=None,
  color_by=None, default_color='blue', palette=None, ramp=None,
  document_name=...)` — consumes a `geometry.segment_geometry` GeoDataFrame
  (indexed by `Segment ID`, shapely `geometry`, plus any joined attribute cols)
  and writes KML. Each segment is a `MultiGeometry` of its full polyline
  (`_coord_string` emits every vertex as `lon,lat,0`) + a midpoint `Point`
  (`interpolate(0.5, normalized=True)`) that anchors an optional always-on label;
  the pushpin icon is scaled to 0 (empty `<Icon>`), the seed's text-only trick.
- **`color_by` generalises the seed's blue=N/E / red=S/W direction colouring.**
  `None` → single `default_color`; a non-numeric column → categorical palette
  (one colour per distinct value, or a `{value: colour}` / list override); a
  numeric column → a continuous `ramp` over min..max (the color-by-metric case).
  `_resolve_color` accepts named colours, `#rrggbb`, `(r,g,b[,a])`, or a raw KML
  `aabbggrr` string; one shared `<Style>` per distinct colour is emitted and
  referenced by `styleUrl`. A text-only `ScreenOverlay` legend is added when
  `color_by` is set.

Design decisions:
- **Real geometry, fallback for free.** Because the layer already substitutes a
  straight endpoint line for any segment missing from the XD shapefile (flagged
  `source='fallback'`), the KML gets that automatically — no re-read of
  `metadata.csv`, no offset hacks. Segments with `geometry is None`
  (`source='missing'`) are skipped rather than emitted empty.
- **Dropped the seed's ~10 ft directional line offset.** It existed to separate
  overlapping opposing-direction straight lines drawn from the same endpoints;
  with real road-following polylines the two directions already trace distinct
  paths, so the offset (and its `lat_per_ft`/`lon_per_ft` fudge at 44°N) is
  unnecessary. Colour still distinguishes direction when `color_by='Direction'`.
- **KML colour is `aabbggrr`, not `#rrggbb`.** Centralised the byte-swap in
  `_rgb_to_kml`; the legend swatch reverses it back to CSS for the HTML overlay.
- **Pure export, no plotting.** Only `xml.etree` + `pathlib` at module scope;
  `pandas` imported inside `_segment_colors` for the numeric-dtype check. Matches
  the pure-core rule (though this is an export module, it stays plotting-free).

One scaffold-test change: `tests/test_scaffold.py` dropped its
`test_stub_raises_not_implemented` — `kml` was the last stub, so no
`NotImplementedError` module remains.

Tests: `tests/test_kml.py` — synthetic 2-segment GeoDataFrame (one 3-vertex
polyline + one straight fallback): parses back as valid KML, the multi-vertex
polyline survives with all 3 vertices, coords are lon,lat, labels hidden by
default / visible on request, pins scaled to 0, `name_col` and default
(segment-id) naming, categorical two-colour + palette override, numeric ramp
endpoints, missing-geometry skip, `Segment ID`-as-column input, `_resolve_color`
forms — plus a real Myrtle roundtrip (all 46 segments, multi-vertex confirmed).
**13 tests, 89 total.**

No `DATA_FORMAT.md` change — this is a pure export over the already-documented
geometry layer; nothing new learned about the INRIX format.

Next: Item 7 — the Dash explorer + embedded map — the last item. It consumes the
geometry layer (map), the analysis core (panels), and this `geometry_to_kml`
(export button).


---

## Session 8 — Dash explorer + embedded map `gui/` (ROADMAP Item 7) (2026-07-16)

The last ROADMAP item: the interactive explorer, a **thin shell** over the compute
core. Split into two files so the pure-core/thin-shell rule holds visibly:
`gui/figures.py` (compute-core DataFrame → Plotly figure; no statistics) and
`gui/app.py` (Dash layout + callbacks; wiring only — every callback calls
`inrix_tools.*` and hands the result to `figures.*`).

**Map framework decision: Plotly native maps** (`go.Scattermap`, MapLibre, the
token-free `open-street-map` style), *not* dash-leaflet. Rationale: the whole GUI
is then one rendering stack — the map, like every panel, is a Plotly figure in a
`dcc.Graph`, and map clicks arrive through the ordinary Dash `clickData` path with
no second component/event model. dash-leaflet would buy nicer built-in draw tools,
but corridor-draw isn't in this item's scope and the single-stack simplicity is
worth more here. Both would have consumed the same geometry layer, so the choice
is reversible if draw tools are needed later.

Built:
- `gui/figures.py` — `segment_map` (per-segment polyline traces coloured from a
  metric + a clickable midpoint-marker layer carrying the colour bar, hover, and
  `customdata=Segment ID`; a highlight ring on the selection), `time_series`
  (WebGL `Scattergl` — a segment is tens of thousands of 5-min points — with the
  before/after windows shaded), `summary_bars` (day-group×time-bin grouped bars
  ±1 SD from `speed.segment_summary`), `beforeafter_forest` (per-segment effect +
  Welch CI from `beforeafter.compare_periods`, dashed rule at 0, selection
  highlighted), and `decomposition` (stacked observed+trend / seasonal / residual
  with changepoint markers). A `_blank` placeholder covers empty selections.
- `gui/app.py` — `build_app()` constructs layout + callbacks without loading data
  (data loads on the *Load* button). Controls: export path, timezone, CValue
  threshold, metric (travel time / speed), map colour mode (segment mean /
  before-after Δ), before + after `DatePickerRange`s, and a KML-export button.
  Five callbacks: load (→ server-side dataset cache), map-click→dropdown,
  map figure, the four panels (keyed on the active tab so only the visible one
  computes), and KML export to `out/segments.kml`.

Design decisions:
- **Server-side dataset cache, not `dcc.Store`.** The Myrtle export is ~1.9M
  filtered rows — far too big to shuttle through a `dcc.Store` on every callback.
  Loaded frames (+ the joined geometry, per-metric columns, and a compare-periods
  cache) live in a module-level `dict[int, Dataset]`; a lightweight token in a
  `dcc.Store` passes the handle between callbacks. This suits the current
  single-user localhost scope (multi-user state is a Future item).
- **Dropdown is the single selection source.** Map click writes `segment.value`
  (`allow_duplicate`), which drives panels + the map highlight; no click→store→
  dropdown loop. Thin polylines are hard to click, so the click target is the
  midpoint marker, not the line.
- **Corridor-wide before/after is computed once and cached** per (metric, before,
  after) on the `Dataset` — it backs both the "before/after Δ" map colouring and
  the forest panel, so selecting the delta metric and opening the forest tab share
  one ~12 s decomposition pass instead of two.
- **Panels compute lazily per active tab**, and the per-segment panels
  (time series / summary / decomposition) run on a single-segment subset, so
  decomposition/changepoint stay responsive (~2 s) instead of decomposing all 46.

Bug found + fixed via a live browser run (the *Load* click 500'd): the default
before/after windows added a `pd.Timedelta` to a `datetime.date` and then called
`.date()` on the result — which is already a `date`, raising `AttributeError` and
failing the callback. Switched to `datetime.timedelta` and dropped the `.date()`
calls (also clears a NumPy generic-unit deprecation warning).

Packaging: `pip install -e .[gui]` already declared `dash` +
`dash-bootstrap-components` + `inrix_tools[geo]`; no map dependency needed on top
of Plotly since we went native. Added `.claude/launch.json` for the preview
runner and `README` run steps.

Tests (`tests/test_gui.py`, 14): headless `build_app()` layout smoke test (every
callback-referenced component present); a real Dash/Flask **HTTP** round-trip
(index + `_dash-layout` + `_dash-dependencies` respond, callbacks registered); the
`figures.*` builders on synthetic frames (trace counts, click `customdata`,
period shading, the x=0 rule, subplot count); and a **self-skipping real-export
end-to-end** test that runs the exact functions the callbacks call (load → map
values → all four panels → corridor before/after → KML) on the Myrtle fixture,
auto-skipping in CI where the licensed fixture is absent. **103 tests total.**

No `DATA_FORMAT.md` change — nothing new learned about the export format; this
item consumes the already-documented I/O, analysis, and geometry layers.

This completes the scoped ROADMAP (Items 1–8). Remaining work is the Future
section (anomaly flagging, difference-in-differences, corridor assembly,
deployment/multi-user state), which needs a planning pass before it's actionable.

---

## Session 9 — Time-of-day analysis window (ROADMAP Item 9) (2026-07-16)

A post-hoc feature request: restrict every calculation to a chosen time of day
(e.g. the 4–6PM peak). Kept to the architecture — a **pure-core row filter**
feeding the existing panels, not a new statistic.

**The load-bearing investigation** was whether time-of-day filtering breaks the
`traffic_anomaly` decomposition / changepoint path (the user flagged this). Read
the package source (`decompose.py` / `changepoint.py`): both use **time-based**
rolling windows (`preceding=ibis.interval(days=N)`, ordered by timestamp), *not*
row-count windows — so filtering to 4–6PM doesn't corrupt the day-to-day spacing;
each window just holds PM-peak samples, giving a coherent "PM-peak level" trend.
The **one real breakage**: `decompose`'s `min_rolling_window_samples` guard
(default `96*5 = 480`) assumes a full day is present, so a 2-hour window's ~168
samples per 7-day window fall below it and the **entire decomposition returns
empty**. Fix: scale the guard by the data's time-of-day coverage.

Semantics chosen (user decision): **filter first, then decompose** — the trend
and detected changepoints describe the selected window on its own terms, and the
before/after, summary, raw series all agree by restricting the same rows. (The
alternative — decompose the full day, restrict only the display — was rejected as
it would report all-day changepoints under a PM-peak label.)

Built:
- `timebins.filter_time_window(df, window)` — overnight-safe half-open
  `[start, end)` filter on local wall-clock seconds-since-midnight (reuses the
  existing `parse_time_bin` / `parse_clock` machinery). Accepts a
  `"4:00PM-6:00PM"` string or a `(start, end)` pair of clock strings /
  `datetime.time` / hour numbers (`16`, `17.5`; `24` = end of day — what the GUI
  slider passes). `start == end` or `0`–`24` is a whole-day no-op. Records the
  window on `attrs['time_window']`.
- `decompose.decompose_segments` — new `min_rolling_window_samples=None` param;
  when `None`, `_auto_min_rolling_samples` scales the upstream 480 by
  `observed_slots_per_day / full_day_slots`. Full-day data → ratio 1 → **480
  unchanged** (existing results byte-for-byte identical, verified by test); a
  4–6PM window → ~40. Value used is recorded on `attrs`.
- `gui/app.py` — a `dcc.RangeSlider` (0–24h, 15-min step) with a plain-language
  status line. `_apply_tod` pre-filters rows before *every* compute path; threaded
  through the map colouring (mean + before/after Δ), all four panels, the KML
  export, and the compare-periods cache key (so windowed and full-day comparisons
  don't collide in the cache). `[0,24]` short-circuits to the unfiltered frame, so
  the default path is unchanged.

Tests (12 new, **115 total**): `test_timebins.py` — window filtering
(half-open, hour-number pair, overnight wrap, full-day no-op, attrs/immutability).
New `test_decompose.py` — `_auto_min_rolling_samples` (full-day == 480,
narrow-window scaled), decomposition records the guard, and the money test:
filter to 4–6PM → the default-480 path is provably **empty** while the
auto-scaled path decomposes, and an end-to-end "filter → decompose →
seasonally-adjust → changepoint recovers a PM-only injected step". `test_gui.py`
— `_apply_tod` no-op/filter, `_hour_label` formatting, and the real-export path
driven through a 4–6PM window.

`DATA_FORMAT.md` updated: added the `min_rolling_window_samples`-vs-coverage
interaction to the "Known quirks" section (it's a fact about how the export's
5-min density meets the decomposition, learned here).

---

## Session 10 — Scoping the post-build refinement batch (ROADMAP Items 10–14) (2026-07-16)

A planning-only session (like Sessions 0 / 0.1): no compute changed. The initial
build (Items 1–9) is complete, and the owner handed over a batch of refinements to
the working explorer. Grouped them into five session-sized ROADMAP items (10–14),
merging small related requests per CLAUDE.md, and recorded the owner decisions that
shaped the scoping so a later session doesn't re-litigate them:

- **Friendly segment names → a user-editable CSV, auto-seeded (Item 10).** The
  owner weighed a generic unique/truncated ID vs. a config file of hand-named
  segments and **chose the config CSV**, with a seed generated by *simplifying the
  existing INRIX labels* (`N 9th St S 9th St / Idaho St` → `9th St & Idaho St`) as
  the starting point to edit. Rationale captured: an auto-ID is opaque; the seed-
  then-edit CSV keeps names readable and under the owner's control. Kept in the pure
  core (`names.py`) with the GUI reading one `Segment ID → name` mapping.

- **KML: keep it, shrink it (Item 13).** The owner questioned whether KML export is
  still needed. **Decision: retain the capability but demote it** from a full-width
  button to a compact icon by the map — it's occasionally useful as a shareable
  export, so removing it loses an option for little gain, while the footprint
  shrinks. (Not removed; not promoted.)

- **Corridor & network analysis: travel time only (Item 12).** The owner wants
  aggregate analysis over a corridor and over the whole network (sum of all
  segments) but **explicitly scoped it to travel time**, because summing travel time
  across segments is well-defined whereas there is no good way to weight *speed*
  across segments. Speed stays segment-level. Noted that `speed.corridor_travel_time`
  already does most of the compute, so the item is mostly a network-total helper +
  GUI scope selector feeding the existing decomposition/before-after adapters via a
  synthetic single entity id.

- **Map midpoint markers: shrink, don't delete (Item 13).** The owner correctly
  identified the dots as hover anchors and asked to hide/shrink them. Flagged in
  scope that they are *also* the click target and colour-bar carrier
  (`figures.segment_map`), so the item must shrink/soften them while preserving
  click + hover — a naive removal would break selection.

- **Date-subset on load (Item 11).** Requested for speed; scoped as the same
  architectural shape as the Item 9 time-of-day window but on calendar date, and —
  per the owner's "discard other dates from the session" — it must **actually shrink
  the cached `Dataset.df`**, not just filter the display, so downstream compute gets
  faster.

- **ToD slider tooltip formatting (Item 13).** Small: format the `dcc.RangeSlider`
  tooltip as a clock time via `tooltip.transform` (client-side JS), mirroring the
  existing Python `_hour_label`. Bundled into the GUI-display item.

- **Fable review (Item 14).** Scoped as a **targeted, review-only** pass (Target:
  Fable) over the interactive layer + the stats adapters — deliberately skipping the
  simple wiring and the well-tested io/geometry/kml core to save tokens — asking for
  bugs, worthwhile optimizations, creative "what else would a user explore"
  generalisations as candidate items, and an honest read on the before/after
  statistical rigor (multiple comparisons, autocorrelation, the chosen estimand,
  difference-in-differences). Its output is a prioritised findings report that
  becomes new ROADMAP items, not code.

Item grouping rationale: the four GUI-display tweaks that share `gui/figures.py` /
`gui/app.py` and add no new statistics were merged into one session (Item 13); the
naming, date-subset, and corridor/network work each carry enough pure-core surface
(and their own tests) to stand alone. No `ROADMAP` renumbering — new IDs continue
from 9.

**Ordering (owner decision):** run the **Fable review (Item 14) first**, ahead of
the feature items — its findings are expected to reshape the scope and priority of
10–13 before effort goes in. So file/priority order is **14 → 10 → 11 → 12 → 13**
(stable IDs unchanged; Item 14 placed first by priority despite the higher ID, the
same convention used for Item 8 earlier).

Next: Item 14 (the targeted Fable review), then re-prioritise 10–13 against its
findings.

## Session 11 — Targeted app review (ROADMAP Item 14) (2026-07-16)

Review-only session (Target: Fable): a focused pass over `gui/app.py`,
`gui/figures.py`, and the `beforeafter`/`decompose`/`changepoint` adapters —
deliberately skipping the well-tested io/geometry/kml core. Deliverable is
**[REVIEW_ITEM14.md](REVIEW_ITEM14.md)** (bugs → quick wins → broader ideas →
stats recommendations); no code changed.

**Method note:** candidate findings were *verified by running them*, not just
read off the code — synthetic-data probes for the crash paths, timings on the
real 1.87M-row Myrtle export for the performance claims, and a 300-replicate
null-coverage simulation for the statistical claim. Two suspected findings were
**disproven** and recorded as such (the short-series decompose→changepoint path
degrades gracefully; the per-redraw groupby/scan passes cost ≤0.02 s and are not
worth caching) — the report separates verified findings from inspection-level
ones.

**Headline findings:**

- **Stats (the big one):** `beforeafter._compare_stats` computes the Welch CI
  over raw 5-min samples as if independent. Simulated null coverage at nominal
  95%: 79.7% (AR(1) ρ=0.5), 50.0% (ρ=0.8), 25.3% (ρ=0.95). Aggregating the
  seasonally-adjusted series to **daily means** first restores ~96% at every ρ.
  Also: no multiple-comparisons handling across the 46-segment forest (BH-FDR
  recommended), no period-overlap/warm-up validation, and two estimand caveats
  (secular drift → promote DiD when a control exists; daily-profile-shape
  effects partially absorb into `season_day` — mitigate with the Item 9 ToD
  window).
- **Verified GUI bug:** the `_load` default before/after windows **overlap for
  any export span < 60 days** (and leave the allowed range < ~30 days), biasing
  default effects toward 0 (`gui/app.py:280-285`).
- **Performance:** `_compare_all`'s cache key includes the period dates, so any
  date-picker change re-decomposes the full export — measured **8.2 s/miss**
  (~90% decomposition). Splitting the cache — adjusted frame per
  (metric, ToD-window), periods sliced on demand — makes date changes
  sub-second. Also measured: 2.3 GB RSS per load with `_DATASETS` never
  evicting; 10.4 s load. Checked-and-fine: `_segment_means` (0.02 s),
  `_segment_df` (<0.01 s), map trace rebuild.
- **Inspection-level bugs:** speed-only export crashes every panel
  (`_metric_col` → `None` → `KeyError`); forest hover shows the row index
  (`%{y}` with numeric y) instead of the segment name; stale segment selection
  + map viewport across export loads; three nits (cleared-CValue `int(None)`,
  equal-handles ToD slider semantics, warm-up-blind empty-state message).

**Outcome / scoping:** confirmed bugs and accepted fixes became **Item 15**
(before/after statistical validity: day-mean CI + BH-FDR + period validation +
default-window fix) and **Item 16** (compare-cache split + dataset eviction +
metric guard + staleness fixes + nits); the forest hover fix was folded into
Item 13's display scope. The seven §3 ideas (results export, day-of-week filter
+ holidays, coverage panel, reliability percentiles, map-as-answer-surface,
congestion-relative views, DiD promotion) stay in the report pending owner
acceptance. Recommended order: **15 → 16 → 11 → 10 → 12 → 13** — validity
first, since it changes every number the app shows; owner to confirm. No
DATA_FORMAT change (nothing new learned about the export itself).

---

## Session 12 — Model assignment: Item 15 → Fable (2026-07-16)

Planning-only. After the Item 14 review landed, the owner asked whether any of the
newly-scoped items should override CLAUDE.md's Opus-end-to-end default and go to
Fable, given Fable's strength on complex/mathematics-heavy work.

Decision: **Item 15 (before/after statistical validity) is reassigned to Fable.**
It is the batch's one genuinely statistics-heavy item — autocorrelation-corrected
inference (effective sample size / daily-mean aggregation), Benjamini–Hochberg FDR
across the segment family, and a null-coverage *simulation* used as a regression
test — and Fable already produced its analytical foundation in the review (the
AR(1) coverage table, the day-mean fix restoring ~96% coverage, the FDR
recommendation). Warm context + aptitude make the override worthwhile.

Everything else stays Opus: **Item 16** is pure engineering (cache split, LRU
eviction, metric guards, stale-state fixes), and **Items 10–13** are naming
heuristics, a date filter, corridor plumbing over existing compute, and GUI polish
— none math-heavy. The standing rule of thumb going forward: *math-heavy → Fable,
everything else → Opus (the CLAUDE.md default)*. The review's §3 candidates most
likely to become Fable items once scoped are **travel-time reliability percentiles**
(needs a block bootstrap) and a promoted **difference-in-differences**.

Recorded on Item 15's Target line + the ROADMAP post-review note; no code changed.

## Session 12 — Before/after statistical validity (ROADMAP Item 15) (2026-07-16)

Implements the Item 14 review's headline findings (REVIEW_ITEM14.md §4.1–4.3 +
bug B1): the forest plot's intervals were ~5–9× too narrow, and the GUI's
default comparison windows overlapped. Target was **Fable** per the owner's
model-assignment note (math-heavy item; warm review context).

**The core change — days, not samples, are the unit of evidence.**
`compare_periods` gained `unit='day'` (the default): within each
(segment[, by-group]) × period, the seasonally-adjusted values are aggregated to
**local-calendar-day means** before the Welch effect/CI. 5-min samples are
strongly autocorrelated, so the old sample-level CI covered a true null only
25–50% of the time at traffic-realistic AR(1) ρ; day means restore ~96%
(review simulation, now a pytest regression: ρ=0.9, 50 seeded reps — day-unit
coverage ≥80% asserted, sample-unit ≤70%). `n_before`/`n_after` now count days;
`n_samples_*` keep the raw counts visible. `unit='sample'` remains as a
documented **non-robust escape hatch**. Design choice: day-mean aggregation over
block bootstrap / HAC — equally honest here, far less machinery, and n becomes
the interpretable "days of evidence".

**Multiple comparisons.** `compare_periods` now emits a Benjamini–Hochberg
`q_value` across all returned rows (hand-rolled step-up, NaN p-values excluded
from the family — no scipy-version dependency). `figures.beforeafter_forest`
de-emphasises rows with q > `fdr_alpha` (default 5%), captions the family size,
and titles the method as e.g. "decomposition, day-level CI".

**Period validation.** Overlapping before/after periods now **raise** in both
`compare_periods` and `ttest_baseline` (half-open bounds — periods that touch
are fine); a period reaching into the decomposition warm-up (`drop_days`)
triggers a `UserWarning` plus `attrs['warnings']`, with effective day counts on
`attrs['before_days_effective']`/`'after_days_effective'`. Day counts are
computed on the naive wall clock so a DST-crossing period still counts whole
calendar days (the March spring-forward otherwise reports 9.958 days — caught
by a test). GUI surfacing: the forest renders `attrs['warnings']` in its
caption; a user-picked overlap becomes a message figure in the before/after tab
(the raise happens before any decomposition, so it's instant), and the delta
map falls back to mean colouring.

**Default windows (review B1).** `gui/app.py` gained `default_periods(lo, hi)`:
disjoint halves of the export span, starting after the 7-day warm-up, clamped
to the span; spans that can't fit two one-day windows get no defaults. Replaces
the fixed ~5-week windows that silently overlapped for spans < 60 days.

**Estimand caveats documented** (module + `compare_periods` docstrings):
daily-profile-shape changes partially absorb into `season_day` (mitigate with
the Item 9 ToD window and/or `by=['Day Group','Time Bin']`); secular drift
needs difference-in-differences (Future item, promotion recommended).

**Compatibility notes:** output gains `n_samples_before/after` + `q_value`
columns and `unit`/effective-days/`warnings` attrs; `n_before/after` change
meaning under the new default (days). One existing test updated accordingly
(`test_compare_periods_recovers_injected_shift`); all other Item 4 tests pass
unchanged — wider day-level CIs still detect the +2 synthetic step cleanly.
Found in passing: `pd.Timedelta(days=7)` (keyword form) trips a numpy 2.5
DeprecationWarning under pandas 2.3.3; `pd.Timedelta(7, "D")` doesn't — used
throughout the new code.

9 new tests (124 total, incl. the real-export end-to-end, all pass). No
DATA_FORMAT change (nothing new about the export itself).

---

## Session 13 — Compare-cache split + GUI hardening (ROADMAP Item 16) (2026-07-16)

The Item 14 review's performance win (O1) plus its confirmed wiring bugs (B2, B3,
B5, B6), all in the interactive layer. Target Opus per the batch's model rule.

**The win — decompose once per (metric, window), not per (metric, window,
periods).** `compare_periods` did everything in one pass: it re-decomposed the
**full export** on every before/after date change (measured 8.2 s/miss), even
though the periods only *slice* the adjusted series after the fact. Split the
compute core into two exposed halves in `beforeafter.py`:
- `adjust_for_periods(df, value, ...)` — the expensive, **period-independent**
  half: decompose the series once and attach the seasonally-adjusted column
  (`ADJUSTED_COL = "_adj"`). Records `decompose_value` / `series_start` /
  `drop_days` on `attrs` (`series_start` stored as a **string** — the frame is
  fed back through ibis/duckdb by changepoint detection, which can't serialize a
  Timestamp in attrs; a Timestamp there silently blanked all attrs).
- `compare_adjusted(adjusted, before, after, ...)` — the cheap, period-dependent
  half: period masking → daily-mean aggregation → Welch stats → BH q-values,
  reading the warm-up metadata off the adjusted frame's attrs.

`compare_periods` is now a thin composition of the two (decomposition path) or
the shared `_compare_core` (raw path) — **behaviour is byte-for-byte identical**
(all Item 4/15 tests pass unchanged; a new equivalence test asserts it column by
column). Also extracted `check_periods()` (parse + disjoint-raise) so a caller
can reject overlapping periods *before* paying for a decomposition.

**GUI wiring (`gui/app.py`).** `Dataset` gained an `_adjusted_cache` keyed on
`(metric col, ToD-window)` — cap 2, LRU (full-export-sized entries) — populated
by `_adjusted_frame()`. `_compare_all` validates periods (fast-fail on overlap),
then runs only the cheap `compare_adjusted` off the cached frame; `_compare_cache`
now holds the tiny per-period result frames (cap 16). `_fig_decomp` **reuses the
same cached frame** and slices to the selected segment (the decomposition groups
by Segment ID, so a segment's rows are identical whether decomposed alone or with
the fleet) instead of decomposing the per-segment slice separately. Verified: 3
period changes + the decomposition tab now trigger **exactly one** decomposition
(monkeypatched counter test).

**Confirmed bugs fixed.**
- **B2 — unbounded `_DATASETS` (2.3 GB/load).** `_store` now clears before
  inserting (single-user, size-1). Old Dataset (and its caches) become
  collectable; the client's data token is refreshed by `_load`.
- **B3 — speed-only / tt-only export crash.** New `_metric_choices` disables the
  absent metric's radio option and lands the selection on a present one; `_load`
  outputs `metric.options`/`metric.value`. `_map`/`_panels` also guard
  `col is None` (mid-load transitions) with a message instead of a KeyError.
- **B5 — stale selection + viewport across loads.** `_load` resets
  `segment.value` to `None` on every load (a new export's ids differ);
  `segment_map` gained a `uirevision` param, keyed on the data token so loading a
  different city recenters instead of holding the old pan/zoom.
- **B6 nits.** Cleared CValue input defaults instead of `int(None)`-ing; the
  decomposition empty-state message names the 7-day warm-up; the ToD slider's
  equal-handles case is documented as *whole day* (matches
  `timebins.filter_time_window`) in the status line, a slider comment, and
  `_window_key` (which collapses it into the whole-day cache bucket).

11 new tests (135 total, incl. the real-export end-to-end, all pass). No
DATA_FORMAT change.

---

## Session 14 — Friendly segment names `names.py` (ROADMAP Item 10) (2026-07-16)

Segments were labelled everywhere by the raw INRIX `Combined` string (Road +
Direction + Intersection, e.g. `N 9th St S 9th St / Idaho St`) — accurate but
noisy. The owner wanted a readable, user-controlled name per segment, seeded by
simplifying the existing labels rather than a bare truncated ID. Target Opus per
the batch's model rule.

**The simplifier (`names.simplify_label`).** Reduces a `(Road, Direction,
Intersection)` triple to `<road core> & <cross street> [& <cross>...]`:
- **Road core** — drop a leading/trailing cardinal direction token, and for a
  `"<route#> / <name>"` road keep the descriptive tail: `N 9th St` → `9th St`,
  `20 / W Myrtle St` → `Myrtle St`, `184 / I-184 E` → `I-184`.
- **Cross streets** — split the `Intersection` on ` / `, route-prefix-strip each
  token (`US-20 Myrtle St` → `Myrtle St`), drop the token that merely repeats the
  road (equal, or the road name contained in it), and join the rest with ` & `.
- The bare `Direction` letter is intentionally dropped, so two opposite-direction
  segments at one corner collapse to the same seed name — the user disambiguates
  by hand-editing the CSV. Verified against all 46 Myrtle labels; the ROADMAP's
  worked example `N 9th St S 9th St / Idaho St` → `9th St & Idaho St` holds, and
  no seed leaks a `US-<n>` prefix.

**Round-trip.** `seed_names(metadata)` → a `(Segment ID, inrix_label, name)`
DataFrame (`inrix_label` keeps the raw `Combined` for reference; `name` falls
back to the label then the Segment ID when nothing is recoverable).
`write_names_template` writes it to a CSV; `load_names` reads it back (validates
the `Segment ID`/`name` columns, types the key, blanks whitespace names).
`apply_names(metadata, names=None)` resolves the single `Segment ID → name`
mapping, layering the user CSV over the seed (per-segment: **non-blank user name
→ seed → Combined → Segment ID**; unknown-segment rows in the CSV ignored).

**GUI wiring (`gui/app.py`, `gui/figures.py`).** `Dataset` gained a `labels`
dict, resolved once at load by `apply_names`; `_labels(ds)` now returns it (the
ad-hoc `Combined` dict is gone) so the dropdown, forest rows, and panel titles
all read the friendly name. `load_dataset` grew an optional `names_path`, adds a
`name` column to `geo`, and the map is drawn with `label_col="name"` +
`sublabel_col="Combined"` — `segment_map` gained `sublabel_col` to show the raw
label as an italic hover subtitle (omitted when it equals the friendly name).
Controls: an optional "Names CSV" input (applied at load) and a "Write name
template" button that writes `out/segment_names.csv` from the loaded metadata.

Kept KML export on `Combined` (out of Item 10's listed surfaces). 18 new tests
(153 total, incl. the real-export end-to-end and an owner-workflow override check,
all pass). No DATA_FORMAT change — the label structure was already documented.

---

## Session 15 — Session date-subset on load (ROADMAP Item 11) (2026-07-16)

The Myrtle export is ~2M rows over ~5.5 months; a study usually cares about a few
weeks. This adds a calendar-date restriction so the loaded frame — and every
downstream compute (map, panels, decomposition) — runs on the smaller slice. Same
architectural shape as the Item 9 time-of-day window, but on calendar date: a
pure-core row filter feeding the existing `Dataset`, not a new statistic.

**Pure core (`timebins.filter_date_range`).** Keeps rows whose **local wall-clock
calendar date** falls in `[start, end]`, inclusive of both endpoint days. The end
is fully inclusive — the exclusive bound is the *following* local midnight via
`pd.DateOffset(days=1)` (calendar-day, so DST-safe), mirroring
`beforeafter.parse_period`'s date-only-end convention. Each bound is normalised to
midnight and localised to the frame's tz; `None`/`""` leaves that side open (so the
function does one-sided trims and a both-open no-op). Placed in `timebins` next to
`filter_time_window` for symmetry (the two "restrict rows before compute"
primitives live together). Records the applied inclusive span on
`attrs['date_range']` as an ISO `(start, end)` pair; returns a copy (input
untouched). A `start` after `end` keeps nothing, consistent with the half-open cut.

**GUI wiring (`gui/app.py`).** A "Restrict dates" `DatePickerRange` in the Data
controls, applied **at load** (the primary of the roadmap's "at load / on an Apply
button" — one code path, no second button, and reload is the natural
session-reset). `load_dataset` grew `date_start`/`date_end`: it computes the
**untrimmed** span first (from the CValue-filtered frame), then trims. `Dataset`
gained `full_span` (defaults to `span` via `__post_init__` for hand-built test
datasets) — the picker's `min/max_date_allowed` are the *full* span so the user can
widen again, while `span` (trimmed) clamps the before/after pickers and drives
`default_periods`. The `_load` callback echoes the applied restriction back into the
picker (defaulting to the full span when none is set) and flags `(restricted)` in
the status line. An over-narrow restriction that empties the frame falls back to the
full span for display bounds rather than a NaN span (the panels already render an
empty df as a blank).

**Why apply-at-load, and cache invalidation.** Re-reading the export from disk on
each restrict change is the deliberate cost of a "trim for the session" action; the
payoff is that the ~8 s decomposition and every panel then run on fewer rows. The
`_compare_cache` / `_adjusted_cache` need no explicit invalidation: each load builds
a fresh `Dataset` with empty caches and `_store` evicts the prior one (Item 16 B2),
so a trimmed session can't read a stale full-span decomposition.

9 new tests (162 total, all pass incl. the real-export end-to-end, which now also
exercises a restricted load — the frame shrinks, `full_span` is retained, and the
panels drive): pure-core inclusive edges / whole-end-day / open bounds / attrs /
immutability / DST day / start>end, plus GUI `full_span` defaulting, a trimmed-frame
shrink+drive check, and default-period clamping to the trimmed span. No DATA_FORMAT
change — date filtering uses the already-documented tz-aware local timestamp.

---

## Session 16 — Corridor & network travel-time analysis (ROADMAP Item 12) (2026-07-16)

Extended the explorer from single-segment analysis to **aggregate travel time** —
a corridor (`Corridor/Region Name` group) or the whole network (all segments) —
without touching the compute adapters. Travel time only, per the owner decision:
summing travel time across segments is well-defined; there is no good
segment-weighting for speed, so speed stays segment-level.

**Compute core (`speed.network_travel_time`).** A four-line function: overwrite
the corridor label to one synthetic `"Network"` value, then delegate to the
existing `corridor_travel_time`. That reuses the complete-set machinery verbatim,
so the network total is the segment sum at only those timestamps where **every**
segment reported (a missing segment drops the timestamp rather than silently
undercounting). Output shape is identical to `corridor_travel_time` (so the GUI
treats corridor and network uniformly), with `Corridor/Region Name == "Network"`
throughout; metadata still attaches summed network length + space-mean speed.

**Why no new decompose/beforeafter code.** The insight the item hinges on: the
adapters group by `Segment ID`, so an aggregate series is just a one-entity
series. Collapsing the per-timestamp total onto a single synthetic
`Segment ID = -1` (`_AGG_SEGMENT_ID`) lets `adjust_for_periods` /
`compare_adjusted` / `decompose_segments` / `detect_changepoints` all run
**unchanged** — corridor/network before-after returns a single aggregate row, and
the decomposition tab slices that one entity out of the cached adjusted frame
exactly as it does for a segment.

**GUI wiring (`gui/app.py`).** An *Analysis scope* dropdown (Segment / Corridor /
Network) + a corridor picker. `_analysis_frame(ds, col, scope, corridor, window)`
is the one new seam: segment scope returns `ds.df`; corridor/network scope returns
the collapsed aggregate. `_adjusted_frame` / `_compare_all` grew scope+corridor
into their cache keys so the three scopes don't read each other's decomposition
(the Item 16 cache split still holds — a date change re-slices, doesn't
re-decompose). The map and the day×time summary stay **segment-level** in every
scope (a segment sum has no per-segment map colouring or day×time decomposition of
its own); the map delta colouring keeps computing per-segment regardless of the
panel scope. `_scope_metric` forces the metric radio to Travel time (disabling
Speed) in the aggregate scopes so the control never lies about what the panels
show, and `_scope_options` disables Corridor when the export has no corridor
column and both aggregate scopes when it carries no travel time.

**Complete-set at network scale.** Requiring all 46 Myrtle segments at a timestamp
is much stricter than a 3-segment corridor, so many 5-min timestamps drop as
partial — but enough complete ones survive to decompose and run before/after on
the aggregate (verified on the real export). Documented in DATA_FORMAT.md; the
auto-scaled Item 9 window guard is the escape hatch if a future export is sparse
enough to starve the decomposition.

11 new tests (173 total, all pass incl. the real-export end-to-end, now exercising
the network aggregate + a corridor before/after): compute-core network sum with
the complete-set drop, network length/space-mean speed, missing-travel-time raise,
scope-option disabling (speed-only + no-corridor exports), aggregate-frame collapse
to the synthetic id, network time-series/before-after/decomposition drive, the
"pick a corridor" guard, and scope cache-keying. DATA_FORMAT complete-set section
gained a network-scale note.

---

## Session 17 — Before/after summary + GUI display polish (ROADMAP Item 13) (2026-07-17)

A GUI-heavy session bundling one pure-core row filter with six display features,
mostly in `gui/app.py` / `gui/figures.py` and one client-side JS asset. No new
statistics.

**Day-of-week filter (`timebins.filter_day_of_week`).** The ToD slider's DOW
sibling: keep rows whose **local** day-of-week (taken from `Date Time` directly,
not a precomputed column, so it is DST-correct) is in a selected set. `None` /
empty / all-seven is a no-op; the applied set is recorded on
`attrs['days_of_week']`. A `parse_day_of_week` accepts an int 0–6, a full name
(`"Monday"`), or a 3-letter abbrev (`"Mon"`). Because it keeps *whole days*, the
Item 9 rolling-window sample guard is unaffected — documented (with the caveat that
too few distinct weekdays weakens `decompose`'s weekly-seasonal fit; the daily
component + residuals still carry the signal).

**GUI DOW wiring.** A `dbc.Checklist` (Mon–Sun, all checked) whose selection
pre-filters **every** panel, the map colouring, and KML export, composing with the
Item 9 ToD window (both applied). Rather than a new plumbing path, `_apply_tod`
grew a `days` arg (window then DOW, each a no-op when unrestricted) and `days`
threads through the same functions the ToD `window` already did; a new `_days_key`
joins `_window_key` in the adjusted/compare **cache keys** so a DOW change gets its
own cached decomposition. A plain-language status line states the active days.

**Before/after day×time summary.** `summary_bars` gained a `summary_after` mode
that draws the two periods as **side-by-side facets** (before | after, shared
y-axis, one deduped day-group legend) via `make_subplots`. `_fig_summary` computes
`segment_summary` on the before-subset and after-subset separately (reusing the
existing date pickers) and falls back to the single panel when periods are unset or
overlap (`check_periods` guards; overlap → single view, no crash).

**Corridor/network day×time summary (revisits the Item 12 decision).** Item 12 kept
the summary segment-level; the owner now wants it for the corridor **sum**. In
aggregate scope `_fig_summary` builds the summary from `_analysis_frame` (the
per-timestamp summed travel time, complete-set rule) so the bars are the **summed**
corridor travel time meaned over time within each day×time bin — a *sum across
segments*, not a mean of segment means (verified on a constant-TT fixture: two
segments at 10 and 20 → bars at 30, not 15). Travel time only, mirroring Item 12.

**Display polish.** (a) The KML button is demoted from a full-width control-panel
button to a compact `⤓ KML` link icon beside the map, status inline. (b) The map
midpoint markers shrank to size 6 / opacity 0.6 — the segment *lines* already carry
the metric colour, so the markers only need to stay the click/hover/colour-bar
carrier; verified in-browser that a marker click still selects (emitted
`plotly_click` → dropdown updated). (c) The ToD RangeSlider tooltip now reads as a
clock time (`1:30 PM`) via a `tooltip.transform="hourToClock"` client-side
formatter in a new `gui/assets/tooltip.js` (mirrors the Python `_hour_label`;
`assets_folder` set explicitly so it resolves whether the app runs as a script or
is imported). (d) Forest hover fix (review B4): the hovertemplate used `%{y}` (the
numeric row index), so hover showed the index, not the segment — the name now lives
in `text` and the template is `%{text}`.

11 new tests (184 total, all pass): `filter_day_of_week`
(subset/no-op/attrs/immutability, name+index forms, composes with the ToD window)
and `parse_day_of_week`; `_days_key` / `_apply_tod` DOW filtering; `summary_bars`
and `_fig_summary` before/after facets + single-period fallback + overlap fallback;
the corridor sum-not-mean check; the forest hover fix; the layout smoke test now
requires the DOW checklist + status. Verified end-to-end in the browser on the real
1.87M-row Myrtle export (marker click, DOW filter + status, side-by-side summary
facets, tooltip formatter, no console errors).

## Session 18 — Delay vs free-flow travel time (ROADMAP Item 17) (2026-07-17)

Added **delay** — the excess travel time a segment carries over its free-flow
(open-road) travel time — as a first-class metric. A pure-core derivation, not a
new data source: INRIX already supplies free-flow speed (`Ref Speed(...)`) and
observed `Travel Time(Minutes)`, and the geometry/metadata supplies length.

**`speed.segment_delay(df, geo_or_metadata=None, free_flow='ref', floor=True)`.**
Adds a per-row `Delay(Minutes)` = observed travel time − free-flow travel time,
where free-flow TT = `Miles / free_flow_speed × 60`. `free_flow` is selectable:
`'ref'` (the per-row `Ref Speed` column, INRIX's open-road reference — *not* the
posted limit) or `('pXX', q)` (each segment's `q`-th percentile of observed speed,
a fallback for exports where `Ref Speed` is missing/suspect). `floor=True` clamps
negative delay (probe noise faster than free-flow) to 0. **Length-cancellation:**
when no length source is given the function degrades to the speed-based form
`TravelTime × (1 − v_obs/v_ff)`, which is algebraically identical to the
length-based value (length cancels) — verified in a test that the two agree. Rows
with a non-positive/missing free-flow speed get `NaN` delay; `attrs['delay']`
records the resolved source, floor, and `length_source`.

**Flows through the existing paths as another value column.** `metric_columns`
now also detects a `Delay(` column (a third key, `delay`), so `segment_summary` /
`daily_timebin_summary` pick it up by prefix and `beforeafter.compare_periods` on
`value='Delay(Minutes)'` reports the **change in delay** (Δ delay + CI) with the
Item 15 day-mean aggregation + BH-FDR unchanged (delay is just another
seasonally-adjustable series — a test recovers an injected free-flow-gap shift with
a CI excluding 0). **Corridor/network scope:** delay sums across member segments
exactly like travel time, so `corridor_travel_time` / `network_travel_time` gained
a `value=` parameter (defaults to the detected travel-time column) and length/space-
mean-speed attach only when summing travel time. A corridor-delay test confirms it
equals the sum of member delays under the complete-set rule.

**GUI.** Delay is a third metric radio option (map colouring, time series, summary,
before/after forest, decomposition). `_metric_choices` learns it (disabled like any
absent metric when the export can't resolve free-flow — the Item 16 B3 pattern);
`_scope_metric` allows Delay **and** travel time in aggregate scope (both sum) while
still disabling Speed. A small **"Delay free-flow"** dropdown (Ref Speed / observed
95th pct) is read at load — delay is computed once into `Dataset.df` via
`segment_delay`, so a source change rebuilds a fresh Dataset (fresh caches). The
map/panels use a new `_agg_metric_key` so Delay survives into corridor/network scope
while Speed falls back to travel time; `_analysis_frame` passes `value=col` to the
corridor/network sums.

10 new tests (all pass): delay math (length-based, speed-fallback equality, floor,
percentile free-flow, NaN on bad free-flow, missing-Ref raise), `segment_summary`
auto-pickup, corridor-delay = sum-of-members, the before/after Δ-delay shift
recovery, GUI metric wiring (segment + aggregate scope, `_agg_metric_key`,
`_parse_freeflow`), and the real-export delay path (computed at load, floored,
network-scope before/after) folded into the end-to-end test. Note: the full suite's
real-export tests exceed this box's ~2.6 GB free RAM when run together (a
pre-existing environment limit, not a regression) — run the heavy tests per-module.

---

## Session 19 — AADT volume-weighting layer (ROADMAP Item 18) (2026-07-17)

Added traffic **volume** as a weighting layer. A segment carrying 40k vehicles/day
and one carrying 2k shouldn't count equally in a corridor summary, and the impact
of delay is really **vehicle-hours**, not per-vehicle minutes. AADT is **not** in
the INRIX export — it comes from the ITD `Cumulative_AADT` GIS layer (in-repo as
`Cumulative_AADT.zip`, a gitignored fixture), which has **no `XDSegID`**, so the
join to our `Segment ID` is necessarily **spatial**.

**`src/inrix_tools/aadt.py` (pure).** `load_aadt(source, year=2024, bbox=None, ...)`
reads the layer via GDAL `/vsizip/` (reusing `geometry._resolve_shp_path`),
**filters `Year == 2024`** with a pushed-down WHERE (the layer is cumulative
1999–2024 — an unfiltered read double-counts every road), and reprojects
**EPSG:8826 → 4326**. The AADT `.dbf` carries real numeric types already, so no
`C(255)` casting is needed (unlike the XD shapefile). A WGS84 `bbox` is reprojected
to the layer CRS for the spatial pushdown so we don't hold all 251k statewide
features; there's no `segment_ids` pushdown because the layer has no join key.
`join_aadt(geo, aadt, max_distance_m=35, bearing_tol_deg=45)` attaches a volume per
`Segment ID` by an **STRtree nearest-line within a metre buffer + an
endpoint-bearing check (mod 180°)** that rejects the opposing-direction split line
and perpendicular cross-streets; the pick is flagged `matched` / `nearest` /
`missing` with the match distance so a marginal join is **visible, not silent**.
Verified on Myrtle: **45/46 segments match at ~0 m** (the AADT and XD centerlines
coincide), the 46th flagged `nearest` at 0.13 m (its short near-intersection
geometry failed the bearing gate — the fallback still gives a sane value).

**Weighting helpers (pure, typed).** The two named in the item, kept **separate
from the corridor sum**: `vehicle_hours_of_delay(mean_delay, aadt)` = delay(hrs) ×
AADT per segment (the headline impact number, summable to a corridor/network
total), and `aadt_weighted_mean_speed(mean_speed, aadt)` = Σ(w·x)/Σw so a corridor
speed reflects where the vehicles are. Plus `weighted_speed_by_time` — the
per-timestamp weighted mean, the **series** form the GUI toggle runs on (a mean, so
it tolerates a missing segment, unlike the complete-set-gated travel-time sum).
Missing/≤0 AADT drops from the speed weighting and contributes 0 vehicle-hours
(row kept). **Corridor/network travel time stays a pure sum — AADT does not
re-weight it** (Item 12 rule); the daily-total-vs-window caveat is recorded on
`attrs['aadt_caveat']`, not silently scaled.

**GUI.** An optional **"AADT layer"** path in the Data controls (defaults to the
in-repo `Cumulative_AADT.zip`); `load_dataset` reads the 2024 rows within the
export's geometry bounds and joins them onto `geo` + `Dataset.aadt` at load. When a
layer resolves: a **Vehicle-hours of delay** map-colour mode (delay × AADT, added to
the mode dropdown only when AADT + the delay metric are present) and, in
corridor/network scope, an **AADT-weighted mean speed** switch that runs the
time-series / before-after / decomposition panels on the per-timestamp weighted-speed
series (`_resolve_col` → `_wspeed_col`; `_analysis_frame` builds it via
`weighted_speed_by_time`). Travel time stays the sum. The segment hover shows the
joined `AADT` value + match quality (`matched`/`nearest`/`none`) so a marginal join
is visible. No layer → the extra options are hidden/disabled and behaviour is
unchanged. On the real export the weighting is meaningful: plain mean speed 20.0 vs
**AADT-weighted 23.6** mph, and ~630 vehicle-hours of delay/day across the corridor.

16 new tests (11 `test_aadt.py` + 5 `test_gui.py`, all pass): the join
(match/reject-crossing/far-nearest/missing/empty + `_line_bearing`), the weighting
math (weighted mean speed, vehicle-hours, zero/missing-AADT, `weighted_speed_by_time`
re-normalizing on a missing segment), GUI wiring (options gate on the layer,
`_resolve_col`, weighted speed ≠ unweighted while travel time stays the sum, vhd
colouring, AADT hover), and the real Myrtle-bbox `load_aadt`/`join_aadt` path (year
filter + reprojection + ≥80% matched). DATA_FORMAT.md gained the AADT-source section
(EPSG:8826, `Year`/2024, spatial-join key, daily-total caveat).

## Session 20 — Correctness review of Items 15–18 batch (review-only) (2026-07-17)

A targeted bug/correctness pass (Fable) over everything since the Item 14 review —
Items 15, 16, 10, 11, 12, 13, 17, 18 (Sessions 12–19) — concentrating on the pure
compute core and the non-trivial compute seams (`beforeafter`, `speed` delay +
`value=` sums, `aadt`, the timebins filters, `names`, and the GUI cache keys /
`_analysis_frame` collapse). Skipped, as scoped: layout, callback wiring,
`figures.py`, and the well-tested `io`/`geometry`/`kml`. **Deliverable:
[REVIEW_ITEMS15-18.md](REVIEW_ITEMS15-18.md) — analysis only, no code changed.**
Confirmed findings were reproduced by running the failing case (scripts in the
session scratchpad; key repros inline in the report). Full test suite green before
review (baseline).

Headlines (details + ranking in the report): the Item 15 statistics held —
BH q-values, period validation, `default_periods` disjointness, and the Item 16
split all attacked and sound. The real bugs sit at the newer data seams:
**(F1)** the complete-set rule counts rows, not non-NaN values, so a
corridor/network **delay** sum silently undercounts and turns an all-NaN
timestamp into a fabricated `0.0` (verified; travel time unaffected; Myrtle has
no NaN-delay rows today, other exports will); **(F2)** `join_aadt`'s bearing
gate changes only the flag — a gate-rejected cross-street's AADT is still
attached via the `nearest` fallback and consumed by every weighted number
(verified both directions: a crossing street's volume is attached, and a curved
same-road line is misflagged `nearest` — Session 19's own 46th segment is this
case); **(F3)** the AADT-weighted speed series level-shifts when the reporting
set changes, so a coverage outage can read as a before/after speed effect
(verified); **(F4)** GUI `_adjusted_cache` keys aren't canonicalised (corridor
keyed in Segment scope, `None` from the map path), so the same decomposition is
cached twice and the cap-2 cache thrashes — the Item 16 win partially defeated
(performance only; no key ever collides, so no stale/cross-contaminated
numbers). Plus smaller items: tz-aware bounds in `filter_date_range`
normalize-before-convert (boundary up to a day off), network-scale complete-set
membership inconsistency on sparse exports, per-segment warm-up not reflected in
truncation warnings, KML export ignoring the vehicle-hours mode, width-0 CIs on
constant day-means, `parse_day_of_week` float truncation, and names-CSV `NA`
strings eaten by pandas NA-parsing. Fix-order suggestion in the report §3
(F1/F2 first — the two that can put a wrong number on screen); becoming ROADMAP
items awaits owner acceptance.

## Session 21 — Review fixes, part 1 (Session 20 findings F1–F5, F8–F11) (2026-07-17)

Implemented the Session 20 review findings that were either subtle
(statistics/geometry) or trivial with the review context warm — F1–F5 and
F8–F11 from [REVIEW_ITEMS15-18.md](REVIEW_ITEMS15-18.md) (statuses annotated
there). **F6 and F7 remain open**, handed to a follow-up session (each needs a
small design decision: an `expected_segments` policy for network-scale
completeness; an attrs contract for per-segment warm-up truncation).

- **F1 (`speed.corridor_travel_time`)** — completeness is now **value-aware**:
  `n_segments` counts segments whose summed value is non-NaN (a reported row
  with NaN delay no longer counts complete), and an all-NaN timestamp sums to
  NaN, never a fabricated 0.0. `expected_segments` stays row-based, so a
  segment whose delay is never resolvable makes its corridor's timestamps
  *incomplete* (loud) rather than silently short. Travel-time sums byte-identical
  (regression-tested); `network_travel_time` inherits.
- **F2 (`aadt.join_aadt`)** — two changes. A volume is attached **only for a
  real match**: the `nearest` fallback still identifies the closest line
  (Route + distance, for diagnosis) but carries NaN AADT, so a gate-rejected
  cross-street's volume can never reach the weighted metrics. And the bearing
  gate now compares **local tangents at the closest approach** (new
  `_local_bearing`: project the other line's nearest point, sample ±5 m) instead
  of endpoint-to-endpoint chords — a curved/L-shaped same-road feature lying on
  the segment now *matches*, a crossing street still fails. Real-layer Myrtle
  join test updated to the new contract (matched-rows-only carry values) and
  passes.
- **F3 (`aadt.weighted_speed_by_time`)** — per-timestamp ``coverage`` column
  (reporting segments' AADT ÷ full member AADT) + a ``min_coverage`` gate
  (default 0.5, ``0`` restores keep-everything), recorded on
  ``attrs['weighted_speed']`` with the dropped count. Kills the
  coverage-artifact failure (mainline missing → 59→20 mph "speed change" with
  no speed changed) before it reaches the before/after.
- **F4 (`gui/app.py`)** — `_adjusted_frame` / `_compare_all` canonicalise
  ``corridor=None`` outside Corridor scope, so the map path (no corridor) and
  the panel path (auto-picked corridor) share cache entries instead of
  decomposing the same frame twice into the cap-2 cache.
- **F5 (`timebins.filter_date_range`)** — tz-aware bounds convert to the
  frame's zone *before* normalize; the bound's **local** calendar day is the one
  that counts (attrs now record the right day). Naive/date/string bounds
  unchanged.
- **F8 (`gui._write_kml`)** — vehicle-hours map mode now exports as
  vehicle-hours (was silently plain means).
- **F9 (`beforeafter._compare_stats`)** — degenerate variance (two constant
  periods, e.g. quantized short-segment TTs) reports NaN CI / p instead of a
  width-0 interval with p=0; NaN p is already excluded from the BH family.
- **F10 (`timebins.parse_day_of_week`)** — non-integral numbers raise
  (``6.9`` no longer truncates to Saturday).
- **F11 (`names.load_names`)** — ``keep_default_na=False`` so a road literally
  named "NA"/"None"/"null" survives the CSV round-trip.

Tests: 9 new/extended (value-aware completeness incl. the travel-time
no-change guard; crossing-street no-leak + curved-road tangent match + real-join
contract; coverage gate; cache canonicalisation counter; tz-aware date bound;
degenerate variance; float DOW; NA names; KML vhd branch). Suite: 220 pass;
the real-export end-to-end GUI test could not run this session (the dev box had
<2.3 GB free with the owner's live app instance running — OOM-killed, not
failing; nothing in this diff touches the paths/values it asserts, and the
real-layer AADT join test did run and pass).

## Session 22 — Review fixes, part 2 (Session 20 findings F6, F7) (2026-07-17)

Closed the two findings [Session 21](DESIGN_HISTORY.md) handed off — each needed
a small design decision, now made and documented in
[REVIEW_ITEMS15-18.md](REVIEW_ITEMS15-18.md) §3.

- **F6 (`io.mark_complete_timestamps` → `speed.corridor_travel_time` /
  `network_travel_time`)** — the complete-set size is now a policy, `expected`:
  `"max"` (max simultaneously observed, the original rule) vs `"total"` (every
  distinct member segment ever seen). They diverge only on a **sparse** group
  where no timestamp holds the whole membership — there `"max"` lets two
  "complete" timestamps sum *different* (N−1)-subsets, so their totals aren't
  level-comparable and decomposition reads the composition change as a spurious
  step. **Decision:** `corridor_travel_time` keeps `"max"` (fine for a short
  corridor that regularly fills); `network_travel_time` **defaults to `"total"`**,
  because sparsity is exactly where the weakness bites and the network docstring
  already promises "every segment present". On Myrtle the 46-segment set is
  regularly achieved, so `"total"` == `"max"` == 46 and the default change is a
  no-op there. DATA_FORMAT.md's complete-set section documents the choice.
- **F7 (`beforeafter._compare_core`)** — the warm-up truncation flag used the
  **global** `series_start`, but the decomposition drops its warm-up **per
  entity**, so a segment whose data begins after the export start (added sensor,
  staged export) lost more of the before period than the export-wide
  `*_days_effective` attrs claimed. **Decision:** surface per-segment effective
  spans as **per-row** `before_days_effective` / `after_days_effective` columns
  (computed from each group's own earliest surviving timestamp, which already
  reflects that entity's warm-up drop), keeping the scalar `attrs` values as the
  export-wide summary. A warning now fires when any segment begins late. The
  Welch stats were already computed off the actual per-segment data — this only
  makes the reported evidence honest.

Tests: 5 new (network `"total"` drops mismatched subsets / `"max"` keeps them /
the two agree when the full set is achieved; `mark_complete_timestamps`
`"max"`-vs-`"total"` + bad-policy raise; late-starting segment reports the
shorter per-row effective span + warns). Full suite: **226 pass** (incl. the
real-export end-to-end GUI test, which ran this session).

## Session 23 — Roadmap cleanup + scoping the next refinement batch (Items 19–21) (2026-07-17)

A planning/housekeeping-only session (like Sessions 0 / 0.1 / 10): no compute
changed. Two things happened.

**Cleanup.** Items 1–18 are all complete and certified (see Sessions 1–22), so
their verbose scopes were **cleared from ROADMAP.md** — the file now opens with a
one-line *Completed* index that points each finished item at its DESIGN_HISTORY
session, keeping every cross-reference navigable without carrying the full build
record twice. The build record itself stays here; nothing was deleted from this
file.

**Scoping.** The owner handed over five requests; grouped into three
session-sized items plus one Future entry, recording the decisions so a later
session doesn't re-litigate them:

- **Segment table = corridor selection + name editing, merged (Item 19).** The
  owner's "selectable segments for a corridor (flag the ones that make it
  incomplete so I can drop them)" and "improve the CSV segment-name workflow with
  a built-in editor, click a map object to select its table row" are the **same
  table surface** — the owner explicitly suggested combining them. Scoped as one
  item: an editable-*and*-selectable `dash_table.DataTable`, two-way linked with
  the map, with a **pure-core completeness helper** (built on Item 1
  `mark_complete_timestamps`) that flags which segments cost the complete-set rule
  timestamps so they can be deselected for a more complete corridor/network
  aggregate. The in-app editor supersedes Item 10's external hand-edit-the-CSV
  path as primary (CSV kept for portability).

- **Layout fix stays its own small item (Item 20).** "Charts below the map, to
  the right of settings — kill the awkward whitespace between map and charts" is a
  pure layout/CSS reflow with no compute and no dependency on the table work, so
  it was **not** folded into Item 19 (that item is already large); kept as a small
  Opus/Sonnet-eligible item.

- **Database storage (Item 21).** "Add a DB (maybe one, connect, then select),
  cache processed GIS data incl. AADT, a low-visibility intake button to ingest an
  export, then run everything from the same DB." Scoped as a new pure-core
  `store.py` (no hardcoded paths, file loaders still work — DB optional) plus a
  GUI intake/select rewiring, caching the Item 18 spatial join at ingest. **DB
  choice (DuckDB vs SQLite) is deliberately deferred to the top of the build
  session** — DuckDB is already transitive via `traffic-anomaly` and fits
  columnar + spatial, but the pick should follow the ingest/query needs. Flagged a
  split point (pure core first, GUI second) because it's the largest item in the
  batch.

- **Directionality de-conflated into two things (owner correction, same day).**
  The owner's original single "directionality" bullet ran together two unrelated
  concerns, now split:
  - **Direction-aware AADT *volume* → Future, unscoped.** "Signed +/− (N/E vs
    S/W) or an N/E/S/W selector/offset/multiselect, fancy version a time-of-day
    directional factor" — but about which direction's **count** to weight by. The
    owner said **don't scope it yet, add it to Future**; it needs a planning pass
    on whether `Cumulative_AADT` carries direction (route direction, `MADT1..12`,
    class fields) or whether direction must be inferred from XD bearing.
  - **Direction-aware *display* on the map → Item 20 (merged).** The concrete
    usability bug: co-located opposing-direction segments overlap, so only the one
    plotted on top renders — the other is hidden and unclickable. First scoped as
    its own **Item 22** (a pure-core direction helper — Direction → compass and →
    `+`/`−`, N/E positive — plus display toggles, a perpendicular display-offset
    that leaves the analytic geometry untouched, or both, decided at build time),
    then **merged into Item 20**: the display fix and the layout reflow edit the
    **same `gui/app.py` map/layout region + `figures.segment_map`**, so running
    them in one session loads the GUI context once and places the new direction
    controls against the reflowed layout in a single coherent pass, rather than
    restructuring the map container and squeezing controls into it a session later.
    Item 22 is retired (stub kept, ID not reused). Noted the relationship to Item 19
    (overlap makes map-click-to-row ambiguous) and that the `+`/`−` convention is
    shared with the Future directional-AADT item.

**Merge economics (owner-directed, 2026-07-17):** the owner asked to combine
sessions only where it's *cheaper* (avoided context re-reads) or *better code*,
and otherwise leave them split. Applied here: Items 20 (layout) + 22 (directional
display) → **one Item 20**, because both edit the same map/layout code and the
context load + the shared map-container edit would otherwise be paid twice; the
lone non-shared piece (a pure-core `geometry` offset helper) is small and additive.
Item 21 (the DB work) was **left standalone** — it shares little code surface with
the GUI items and is the batch's largest lift.

No renumbering — new IDs continue from 18 (→ 19, 20, 21; 22 assigned then retired
into 20). File/priority order is 19 → 20 → 21; each is independent. Next: whichever
the owner picks — Item 20 is the cheapest win (layout + map display), Item 19 the
most-requested feature, Item 21 the largest lift.

## Session 24 — Interactive segment table: coverage + membership + name edit (ROADMAP Item 19) (2026-07-17)

Built the owner's most-requested refinement: one `dash_table.DataTable` doing
triple duty — edit friendly names inline, select which segments belong to the
active corridor/network, and flag which segments cost the complete-set rule its
timestamps — two-way linked with the map. Pure-core first, then the GUI wiring,
then a live preview of both link directions.

**Pure core.**

- **`speed.segment_coverage(df, members=None, value=None)`** — the completeness
  helper. Over the timestamps any member reported, it returns per member the
  **`coverage`** fraction and the **`complete_set_cost`** = the count of timestamps
  at which that segment is the *sole* absent member (exactly how many complete-set
  timestamps its removal recovers). Implemented as a presence pivot
  (timestamp × member, reindexed so a never-reporting member is an all-False
  column); `cost = (present_count == n_members−1) & ¬present[s]` summed over
  timestamps. Value-aware when `value` is passed (a NaN value is *not reported*,
  matching `corridor_travel_time`). Sorted most-worth-dropping first;
  `attrs` carries `n_members` / `n_timestamps` / `n_complete`. Typed, no plotting.
- **Additive `members=` on `corridor_travel_time` / `network_travel_time`** — an
  explicit `Segment ID` list that restricts the rows *before* the complete-set rule
  runs, so the sum + completeness are measured against exactly the selected set.
  `None` (default) keeps the existing `Corridor/Region Name` grouping / whole
  network, so every prior call is unchanged. The cost is exact:
  `len(dropped_complete) − len(full_complete) == complete_set_cost` (asserted).
- **`names.write_names(names_df, path)`** — persists an *edited* `Segment ID → name`
  table (the table's rows) to the same CSV format `load_names` reads, so the in-app
  editor round-trips through the portable store. Distinct from
  `write_names_template` (which regenerates the seed from metadata); `inrix_label`
  optional, name trimmed.

**GUI (`gui/app.py` + `gui/figures.py`).**

- A `dash_table.DataTable` (`segment-table`) with an **editable `name`** column,
  read-only `Combined` / **Coverage %** / **Completeness cost** / `Segment ID`,
  `row_selectable="multi"`, native sort, and a `style_data_conditional` that ambers
  any `complete_set_cost > 0` row. A **Save names** link writes the CSV and updates
  the live label mapping (`ds.labels` + `geo["name"]`) so the dropdown / hover
  refresh without a reload.
- **Explicit membership** flows through a `dcc.Store("corridor-members")` fed by the
  table's `selected_rows`. `_norm_members` canonicalises it: empty / whole-set →
  `None` (no override, so existing corridor/network defaults hold), a real subset →
  the sorted id list. Threaded through `_analysis_frame` (a subset routes both
  corridor and network scope through `network_travel_time(members=…)`, one synthetic
  group), `_adjusted_frame`, and `_compare_all`, with `_members_key` added to both
  caches so a subset decomposition doesn't collide with the whole-network one.
- **Two-way map ↔ table link.** `figures.segment_map` gained a `member_ids` arg that
  draws non-members faint (opacity 0.2) when a *proper* subset is selected (whole /
  empty dims nothing). `_map` passes the normalised member set. Map click →
  `segment.value` (existing) → `_highlight_row` sets the table `active_cell`
  (highlights + scrolls to the row); clicking a row cell → `_row_selects_segment`
  sets `segment.value` (map ring + panels). Both callbacks guard on the current
  value/row so they settle at a fixpoint instead of looping.

**Decisions.** Default selection is **empty** (no override), not "all ticked": a
no-op selection means "use the corridor grouping / whole network", which keeps every
Item 12/17/18 default intact and only lets the table *refine* membership. An
explicit subset in corridor scope routes through `network_travel_time` (one group,
`expected="total"`) rather than the corridor-name grouping — the membership *is* the
corridor once the user has picked it. The visible-not-silent completeness flag
(ambered row + the cost column) is the owner's core ask made concrete.

**Verification.** `pytest tests/` — **243 passing** (incl. the self-skipping
real-export end-to-end, which now also asserts the 46-row table, `segment_coverage`,
and that dropping the costliest segment never loses complete-set timestamps). New
tests: coverage on a fixture with a known chronically-missing segment (exact cost 4,
coverage 0.6, and `dropped − full == cost`), value-aware coverage, subset coverage,
the `members=` override on `corridor_travel_time`, `write_names` round-trip, and the
GUI wiring (table rows/columns, `_rows_to_member_ids`, `_segment_row_index`,
`_norm_members`, membership keys the adjusted cache, `segment_map` dims non-members,
the save round-trip). **Live preview on the real Myrtle export** confirmed both
directions: ticking rows → "2 of 46 segments" + the map dimming 44 of 46 lines; a
map click → the dropdown + the table `active_cell` moving to row 19 (the clicked
segment) and scrolling into view; a row-cell click → the map selection ring; **Save
names** → `out/segment_names.csv` in the exact `load_names` format.

**Docs.** DATA_FORMAT.md gained a "Segment coverage + the completeness cost" note in
the complete-set section; README documents the segment table's three jobs and the
map link; ROADMAP Item 19 boxes checked and the Completed index + status line
updated. Follow-ons unchanged: Item 20 (layout reflow + directional display — it
will place the new table against the reflowed layout) and Item 21 (DB storage).

## Session 25 — GUI map & layout display polish: reflow + directional segment display (ROADMAP Item 20) (2026-07-17)

Two GUI-display fixes in the same `gui/app.py` map/layout region — closing the
awkward map↔charts whitespace, and making co-located opposing segments both visible
and clickable — plus the pure-core direction helpers that back the second. No new
statistics.

**Top-of-session decision (recorded): ship BOTH mechanisms.** The ROADMAP floated
toggles-vs-offset-vs-both; both were built, as recommended — the compass toggle
declutters (filter which directions render), the perpendicular offset lets both
directions of an overlapping pair be seen at once. They compose (a hidden direction
needn't be offset).

**Pure core (`geometry.py`).**

- **`direction_group(d)` / `direction_sign(d)`** — codify one signed compass
  convention: group a raw `Direction` to its primary cardinal `N/E/S/W` (compound
  `NE` → `N`, spelling-tolerant `nb`/`Northbound`), and sign it **N & E = `+1`, S &
  W = `−1`** (unknown → `0`). This is the `+`/`−` convention the toggles display and
  the one the Future directional-AADT item will reuse.
- **`attach_directions(geo, directions)`** — annotate a geo copy with `dir_group` /
  `dir_sign` columns from a `Segment ID → Direction` map (geometry untouched).
- **`offset_overlapping_segments(geo, offset_m=6, tol_m=20, angle_tol_deg=35)`** —
  the display-only offset. Detects co-located opposing pairs (anti-parallel bearing
  **and** geometries within tolerance) and translates each perpendicular to travel,
  **right-hand of its own bearing**, so a NB/SB pair separates east/west. Returns a
  **copy** with an `offset_applied` flag column; the analytic geometry is never
  mutated and isolated segments are byte-for-byte unchanged. Bearing from the
  start→end vector, metre↔degree conversion at the layer's mean latitude.

**GUI (`gui/app.py`).**

- **(A) Layout reflow.** The chart `Tabs` moved out of a full-width bottom row into
  the **right column**, stacked below the map + segment table; the settings stay in
  the left column. Columns are now responsive (`xs=12, lg=3` / `lg=9`) so they stack
  full-width on a narrow viewport. The map↔charts gap is gone.
- **(B) Directional display.** `load_dataset` calls `attach_directions` from
  `metadata.Direction`. A new **`dir-compass`** checklist (options `N (+) / E (+) /
  S (−) / W (−)`, only the present groups) filters which directions render, and a
  **`dir-offset`** switch (default on) applies the offset. A `_display_geo(ds,
  dir_groups, offset_on)` helper builds the per-render display frame (filter +
  offset); the `_map` callback draws it. All metric/coverage compute stays on the
  un-offset `ds.geo`; map colouring is unaffected (colour still comes from the
  metric, not position). Selection ring and hover fire on the visible/offset
  segments; click `customdata` is still the Segment ID.

**Decisions.** The offset uses each segment's **own geometry bearing** (right-hand
rule) rather than the `Direction` sign, so it separates opposing pairs even when
metadata is imperfect — the sign helper is kept separate for the toggle labels and
the Future AADT split. Default toggle state is **empty = render all** (like the Item
19 membership no-op); selecting *every* present group is also a no-op. Offset is a
display copy, never a mutation, so the analytic/KML/coverage paths are provably
untouched.

**Verification.** `pytest tests/` — **250 passing** (243 → +4 geometry, +3 GUI; the
self-skipping real-export end-to-end also gained direction-path assertions inline).
New tests: `direction_group`/
`_sign` across cardinal/compound/spelled/blank forms; `attach_directions` leaves
geometry intact; the offset separates a co-located opposing pair, leaves an isolated
segment byte-for-byte, is perpendicular and ~`offset_m` sized, and never mutates the
input; `_direction_options` lists only present groups in `+`/`−` order;
`_display_geo` filters + offsets; and the reflowed-tree structural assertion (tabs
share the right column with the map + table, not the left controls column). **Live
preview on the real Myrtle export**: the two-column reflow (settings left; map, then
table, then charts in the right column — gap closed); the direction control showing
`N (+)/E (+)/S (−)/W (−)` with the offset switch on; the map drawing all 46 lines +
46 clickable markers with the one real opposing pair (2 of 46) offset apart and both
clickable; and checking `N` filtering the map down to 23 N-segments. No console
errors.

**Docs.** DATA_FORMAT.md gained a "Direction convention & directional map display"
section (the signed `N/E = +` convention + the offset rule); ROADMAP Item 20 boxes
checked and the status line + Completed index updated. Remaining open work: Item 21
(DB-backed storage & ingest).

## Session 26 — DB-backed storage & ingest: DuckDB store + GUI intake/select (ROADMAP Item 21) (2026-07-17)

Closed the Items 19–21 batch. Moved the app off re-parsing the export `.zip` and the
GIS shapefiles every session onto an optional **persistent DuckDB store**: ingest an
export + its processed geometry/AADT join **once**, then *select* it back and run from
the DB — the expensive Item 18 spatial join is cached at ingest, not repeated per load.
Pure core first (`store.py` + its tests), then the GUI intake/select wiring; the
file-path loader stays fully intact (the DB is an accelerator, not a requirement).

**Top-of-session decision (recorded): DuckDB, not SQLite.** DuckDB is already a
transitive dependency (via `traffic-anomaly`'s `ibis-framework[duckdb]`), and its
columnar engine fits the analytic scan of a ~2M-row export. The DuckDB **`spatial`
extension is deliberately *not* used**: the GIS layers here are a small per-segment
table, so geometry is serialized as **WKB blobs** and rehydrated with `shapely` — no
in-DB spatial predicates, which keeps `connect` offline (no extension download) and the
schema portable. SQLite would serve the small tables but loses on the big scan, so the
pick is on the *query* need, per the ROADMAP's guidance.

**Pure core (`src/inrix_tools/store.py`).** No GUI imports, **no hardcoded paths** (the
DB path/connection is always a param).

- **`connect(db_path, read_only=)`** — opens DuckDB, pins `SET TimeZone='UTC'` (so
  `TIMESTAMPTZ` reads back as UTC), ensures the `_datasets` registry.
- **`ingest_export` / `put_export`** — write the `io.load_data` frame + `io.load_metadata`
  frame to per-dataset tables (`obs_<key>` / `meta_<key>`) and upsert a registry row
  (source, tz, units, row/segment counts, date span, `schema_version`, `ingested_at`).
  Idempotent per name (`CREATE OR REPLACE` + registry replace). `put_export` is the
  lower entry for when the caller already holds the frames (the GUI does).
- **`ingest_geometry` / `ingest_aadt`** — persist the **processed** geo layer to
  `geo_<key>`: `Segment ID`, `source`, the `join_aadt` columns
  (`AADT`/`aadt_source`/`aadt_dist_m`/`Route`/`Commercial`), geometry as a WKB blob
  (`NULL` for a `missing` segment). `ingest_aadt` merges just the AADT columns onto an
  existing geo (geometry preserved). This is the once-at-ingest cache of the spatial join.
- **`list_datasets` / `dataset_names` / `load_export` / `load_metadata` / `load_geometry`
  / `load_dataset` / `remove_dataset`** — read side. `load_export` normalizes `Date Time`
  back to `datetime64[ns, UTC]` and restores `df.attrs['units']` so the frame **equals**
  the file loader's; `load_geometry` rebuilds a WGS84 GeoDataFrame indexed by `Segment ID`;
  `load_dataset` bundles all three into a `StoredDataset`.
- **Per-dataset tables keyed by `_dataset_key(name)`** (sanitized name + short md5) so
  heterogeneous exports (different unit-named columns) never collide on one schema, and
  re-ingest is a clean overwrite.

**GUI (`gui/app.py`).** Kept the thin-shell split — the callbacks call `store.*`, no
statistics added.

- **`load_dataset` refactor.** Factored the file build into `_build_geo` (shapefile read
  + `segment_geometry` + `join_aadt` — the expensive step) and the cheap per-session
  decoration into `_decorate_geo` (Combined hover label, directional `dir_group`/`dir_sign`,
  friendly `name`). A new `dataset_name=` param switches the source: unset → the file path
  (build geo); set → `store.load_dataset` (ingested frames + the **cached** join). Both
  paths then run the identical tail (`to_local` → `filter_cvalue` → date range → delay →
  decorate → AADT series), so a DB-loaded `Dataset` is indistinguishable from a file-loaded
  one. The names/directions/Combined are re-derived on load (kept out of the cache — they're
  cheap and `names_path`-dependent).
- **`ingest_to_db`** — reads the export once, `put_export`s it, builds+joins the geo once
  via `_build_geo`, and `ingest_geometry`s it.
- **Intake/select controls (low-visibility).** In the Data card: a "⤓ Ingest current
  export to DB" link button + status, and a "Saved datasets" `dcc.Dropdown` (options read
  from the DB at layout build via `_dataset_options`, which degrades to `[]` when no DB
  file exists — headless-safe). `_ingest` callback ingests then selects the new dataset,
  which (as a second Input on `_load`) auto-loads it from the DB. `_load` now branches on
  `ctx.triggered_id`: the "Load export" button → file path; a dataset selection → DB path
  (loading the full ingested span; a cleared selection is a no-op).
- Single lazily-opened connection (`_db()` over `DEFAULT_DB = inrix_store.duckdb`,
  gitignored). DB features degrade to off if DuckDB/the store is unavailable.

**Decisions.** (1) `store.py` is **persistence-only** — it accepts prebuilt frames rather
than importing the geo stack, so its tests are shapefile-free and it stays dependency-light
(the GUI, which already builds geo, orchestrates). (2) The geo cache holds only
geometry+join columns; the `name`/`Combined`/direction decoration is re-derived per load so
an edited names CSV or a tz change is honoured without re-ingest. (3) Migration policy is
**re-ingest** (the store is a cache): `schema_version` is stamped per dataset and a stale
one is rebuilt from the recorded `source`; no in-place migrator until an ingest is
expensive enough to warrant one. (4) WKB-over-blob instead of the DuckDB spatial extension,
per the top-of-session decision.

**Verification.** `pytest tests/` — **263 passing** (250 → +11 store, +2 GUI). The
`store.py` suite (`tests/test_store.py`, 11 tests) covers: `io.load_data`/`load_metadata`
**parity** after round-trip; tz-aware UTC + units preserved; geometry+AADT round-trip (real
polylines, a `missing` segment as `None`, match flags); `load_dataset` bundling;
`ingest_aadt` updating only the join columns; the **GIS-join cache hit** (monkeypatched
`aadt.join_aadt` counter stays 0 on load); the DB path as a param persisting to a nested
file; and the **self-skipping real-export ingest** (the licensed Myrtle export — ran, 2M-row
round-trip span matched). `tests/test_gui.py` adds the DB intake/select coverage: the layout
ids (`dataset` / `ingest-export` / `ingest-status`) present; an `ingest_to_db` → DB
`load_dataset` frame that **equals** the file load while `_build_geo` is called **once at
ingest, not on load** (the join cache hit at the GUI seam, via a call counter); and
`_dataset_options` empty without a DB file (headless-safe). *(A transient harness issue
mid-session briefly rejected every Python spawn — `echo`/`ls` worked but `python`/`pytest`
returned "Stream closed"; it cleared and the full suite ran green.)* **Live end-to-end on
the real Myrtle export**: file load 1.87M rows / 46 segs / AADT in **16.2 s**; `ingest_to_db`
(real shapefile geometry + real AADT spatial join, 2.19M raw rows cached) in **13.2 s**;
then **DB load of the same 1.87M rows / 46 segs / AADT in 3.8 s** — ~4× faster, reading the
cached join (all 46 AADT segments `matched`), with row/segment parity against the file load
and `has_geometry`/`has_aadt`/`schema_version=1` correct in the registry.

**Docs.** DATA_FORMAT.md gained a "Database store (`store.py`, Item 21)" section (the DuckDB
decision + WKB rationale, the table layout, the tz/geometry round-trip rules, and the
`schema_version` / re-ingest migration policy); ROADMAP Item 21 boxes checked, status line +
Completed index updated (the Items 19–21 batch is now complete). The Items 19–21 refinement
batch is closed; all remaining ROADMAP work is in **Future** (needs a planning pass).

## Session 27 — Area-based store: merge exports by corridor set + bin-length partition (ROADMAP Item 23) (2026-07-17)

An owner follow-on to Item 21, right after it merged: **change the store's data model
from one silo per export to a persistent *area*.** The owner wanted exports of the
same corridors to accumulate (not a separate dataset each), with duplicate entries
merged and different time bin-lengths coexisting behind a selector. Same pure-core-
first discipline: `store.py` rewritten + tested, then the GUI, then docs.

**Top-of-session decisions (asked + recorded).** Three forks resolved with the owner
before building the schema:
1. **Area identity = corridor set.** An export's area is the sorted set of its distinct
   `Corridor/Region Name` values; the same set → the same `area_key` → exports merge.
   (Alternatives offered: pick/name at ingest; segment-ID set. Owner chose corridor set —
   so a *subset*-corridor export becomes a new area, an accepted tradeoff.)
2. **Bin length = partition + selector.** Different bases (5-/15-/60-min) coexist in one
   area, partitioned by an auto-detected `bin_minutes`; the GUI adds a bin selector.
3. **Overlap = keep-first.** A row already stored (same `Segment ID` + `Date Time` +
   `bin_minutes`) is never overwritten by a later export.

**Pure core (`src/inrix_tools/store.py`, rewritten, `SCHEMA_VERSION` 1 → 2).**

- **`area_identity(df)`** — `(area_key, area_name, corridors)` from the sorted distinct
  corridor set (MD5-hashed key, corridors joined for the name); falls back to the sorted
  Segment-ID set (`segments(N)`) when there's no corridor column.
- **`detect_bin_minutes(df)`** — the **modal** consecutive-`Date Time` spacing per
  segment, in minutes (gap-robust; 5-min INRIX → 5).
- **`ingest_export` / `put_export`** — derive area + bin, then **merge keep-first**:
  `_merge_frame` inserts only rows whose key tuple is absent, via an anti-join
  `INSERT … BY NAME` after `_align_columns` adds any new columns (so column-drift between
  exports merges, not errors). Returns `{area_key, area_name, bin_minutes, n_rows_added}`.
  Metadata merges keep-first per `Segment ID`. `_areas` (registry: units, span, segment
  count recomputed from the merged obs; `created_at` preserved) + `_ingests` (provenance
  per export) tables.
- **`ingest_geometry` / `ingest_aadt`** — the processed geometry+AADT layer merges
  keep-first per `Segment ID` (WKB blobs, `NULL` for `missing`) and **persists with the
  area**, so the Item 18 spatial join is cached once and reused across every later export.
- **Read side** — `list_areas` (+ a `bins` column), `area_names`, `area_bins`,
  `load_export(area, bin)` (normalizes `Date Time` back to `datetime64[ns, UTC]`, restores
  units; `SELECT * EXCLUDE (bin_minutes)`; errors listing choices if multi-bin and none
  given), `load_metadata`, `load_geometry`, `load_dataset` (bundles into a `StoredDataset`
  carrying `area_key`/`area_name`/`bin_minutes`), `remove_area`.

**GUI (`gui/app.py`).** Thin-shell split kept.

- `load_dataset` DB branch now takes `(area_key, bin_minutes)` (was `dataset_name`);
  `ingest_to_db` returns the merge summary. The file build (`_build_geo`) and per-session
  decoration (`_decorate_geo`: Combined/direction/name) are unchanged and shared.
- The "Saved datasets" dropdown is replaced by an **Area** dropdown + a **Bin length**
  dropdown. `_ingest` merges into the auto-derived area and selects it → `_area_bins`
  populates the bin options + default → the default bin (an Input to `_load`) triggers the
  DB load. `_load` branches on `ctx.triggered_id == "area-bin"`.

**Decisions.** (1) Keep-first (not last-wins) per the owner — corrected re-exports don't
clobber stored values; `n_rows_added` makes a no-op merge visible. (2) Area = the *full*
corridor set, so a partial-corridor export is deliberately a new area (documented) rather
than a silent partial-merge. (3) Geometry/metadata persist at area level and merge
keep-first, so the spatial join is a one-time cost per area even as exports accumulate.
(4) `store.py` stays persistence-only (no geo-stack import); the GUI orchestrates the build.
(5) v2 is a schema break; the store is a cache, so the policy is re-ingest (a v1 file's old
tables are ignored, not migrated).

**Verification.** `pytest tests/` — **265 passing** (`test_store.py` rewritten to 13 area
tests; `test_gui.py` updated: layout ids `area`/`area-bin`, the ingest→area DB-load
round-trip with the join cache-hit, `_area_options` empty without a DB). Coverage: bin
detection (5 & 15-min); corridor-set grouping (same set merges, other corridor = new area);
**keep-first overlap** (the overlapping timestamp keeps the first value; re-ingest adds 0);
two bin-lengths coexist + selectable (+ the multi-bin “pass a bin” error); geometry/metadata
union keep-first; single-export `load_export`/`load_metadata` **parity** with the file
loaders; DB-path-is-a-param persistence; self-skipping real-export. **Live end-to-end on the
real Myrtle export:** ingested as one area named by its corridor set (5th-6th / 9th / Capitol
/ Front / Myrtle …), bin 5 auto-detected, **+2,185,368 rows**; **re-ingest → +0 new rows**
(keep-first idempotent); **DB load 1.87M rows in 3.3 s vs 12.8 s file** (~4×), row parity, all
46 AADT segments `matched`, registry `schema_version=2`.

**Docs.** DATA_FORMAT "Database store" section rewritten for the area model (corridor-set
rule + its subset caveat, keep-first merge, bin partition, v2 tables, re-ingest migration);
ROADMAP Item 23 added (Completed index + done-section with a v1→v2 migration note) and the
status line updated. **Migration:** an existing v1 `inrix_store.duckdb` isn't read by v2 —
delete it (or just re-ingest) to see data as areas.

---

## Session 28 — Whole-app review + scoping the intake-inversion batch (Items 24–26) (2026-07-18)

A **review-only** session (Fable, per the owner's model rule) prompted by the owner
flagging that two GUI requests from the Item 21 scope were dropped: the **Load export
button was to become low-visibility** and the **main data control was to point at the
database** — the shipped `_controls()` still leads with the file path + a primary Load
button, DB Area/Bin selectors demoted beneath. Full findings + paste-ready item scopes
in [REVIEW_APP_2026-07-18.md](REVIEW_APP_2026-07-18.md); baseline **265 tests pass**,
no code changed.

**Findings (R1–R9).** Confirmed the dropped requests in code (R1, R2 — including the
layout being a *static* tree, so `_area_options()` runs once at import and areas
ingested externally never appear until restart). New: **date restriction is impossible
on DB loads** (`_load` hard-codes it off; the only other trigger loads from file) —
structural under the accumulate-forever area model, fix = date push-down into
`store.load_export` (R3); load status/state doesn't say file-vs-area provenance (R4);
shared DuckDB connection across threaded callbacks vs. the documented cursor-per-thread
pattern (R5, unverified hygiene); `_merge_frame`'s anti-join lets NULL-key rows bypass
keep-first on every re-ingest (R6, unverified); compass checklist reads unchecked while
all directions render (R7); Save-names doesn't write the saved path back into the Names
CSV input, so edits silently drop at next load (R8); `put_export` docstring wrongly
implies the GUI passes its held (filtered/derived) frames (R9).

**Same-day addendum — the segment table (R10–R12).** The owner reported the Item 19
table misbehaving ("checking/unchecking boxes can make unrelated rows disappear") and
directed a redesign. Diagnosis (code-verified): the table's rows carry **no `id` key**,
yet the callbacks map **positional** indices (`selected_rows`, `active_cell["row"]`)
into the unsorted `data` State — under `sort_action="native"` the view index ≠ data
index, so after any sort a click/checkbox resolves to the wrong Segment ID; every
toggle also rewrites the whole `data` prop, which with `fixed_rows={"headers": True}`
hits dash_table's known misrender-on-replace behaviour; and the Item 19 dimming makes
one selected row fade all 45 others by design. Owner direction: the table shows **one
corridor**, membership becomes an **Include/Exclude column** (all included by default),
and names **persist in the DuckDB store** (last-write-wins upsert — deliberately *not*
keep-first), retiring the CSV names workflow (this supersedes finding R8).

**Scoping (owner accepted same day; appended to ROADMAP.md).** Grouped per CLAUDE.md
into four session-sized items, IDs continuing from 23: **24** — DB-first intake
inversion (R1/R2/R4/R7; **Opus**); **25** — date push-down + Restrict-dates on DB
loads (R3; **Opus**, cleanest after 24); **26** — hardening batch (R5/R6/R9;
**Sonnet-eligible**); **27** — corridor-scoped segment table with Include/Exclude +
names in DB (R10–R12; **Opus**, after 24). Nothing math-heavy this batch, so no Fable
implementation item; Fable stays the review/verification tier.

---

## Session 29 — DB-first intake: invert the data controls (ROADMAP Item 24) (2026-07-18)

Delivered the half of Item 21's scope that never landed (review R1): the DuckDB
**store is now the primary intake**, the export-file loader the exception. Pure
GUI/layout + callback wiring in `gui/app.py` — no compute added, the thin-shell split
intact. Addresses review findings R1/R2/R4/R7; R8 was deliberately left for Item 27
(names-in-DB supersedes the CSV save-path patch).

**`_controls()` inverted (R1).** The **Area** and **Bin length** dropdowns lead the
panel with normal-weight labels; the whole file loader — export path, tz/CValue,
Names CSV, AADT layer, Restrict-dates, the **Load export** button (now
`color="secondary"`, not the primary blue — `dbc.Button` defaults to `primary`, so
demoting it needs the explicit colour, caught in preview), *Write name template*, and
the *⤓ Ingest current export to DB* link — moved into a **collapsed
`dbc.Accordion`** titled "Load / ingest an export file". The daily workflow is now
area-select at the top; re-parsing a zip is a deliberate expand-the-accordion act.

**Startup auto-load from the DB (R2).** Two pieces:
- **Auto-select.** `_default_area()` returns the most-recently-updated area
  (`store.area_names` already orders most-recent-first); the Area dropdown's layout
  `value` defaults to it. The `_area_bins` callback lost its `prevent_initial_call`
  so it **fires on the initial page render** — the pre-selected area populates its
  bins and sets the first, which (as an `Input` to `_load`) cascades into a DB load.
  The app opens already showing the last area worked on. Empty/absent store →
  `_default_area()` is `None` and the chain is a harmless no-op (headless-safe).
- **Callable layout.** `build_app` now assigns `app.layout = _layout` (the function,
  not a built tree) so Dash re-evaluates it per page load — areas ingested by another
  process appear on refresh instead of freezing at import. Tree-introspecting tests
  call `_layout()` (helper `_resolve_layout` in the test module).

**Provenance + mutually-exclusive intake (R4).** New `_load_provenance` helper: the
load-status line now leads with `area 'NAME' · 5-min` on a DB load or
`file 'NAME.zip'` on a file load (basename, not the full path), so the two paths never
read identically. `_load` gained an `Output("area", "value", allow_duplicate=True)`
that a **file load sets to `None`** (clearing the DB selection, which cascades the bin
control empty) while a DB load leaves it `no_update` — the inactive path never looks
"still active".

**Compass honesty (R7).** `_load` initialises the `dir-compass` checklist to *all
present groups* instead of `[]`. Both are no-op filters (the map renders every
direction either way — `_display_geo` treats "every present group" as no filter), but
every-box-ticked matches the map instead of reading as "nothing selected".

**Tests (+6, 271 total).** `_load_provenance` both branches; `_default_area` +
layout auto-select picking the most-recent area (and a second, different-corridor-set
area becoming the new default); the `_load` callback owning an `area.value` output +
`area-bin.value` driving it (state-clearing + auto-load wiring); `_area_bins` firing
on the initial render (no `prevent_initial_call`); the file loader demoted into a
collapsed accordion with a non-primary Load button and the Area/Bin controls outside
it; the compass all-present-groups default staying a no-op filter. The headless layout
smoke test still passes DB-free. **Preview-verified on the real Myrtle export**:
ingest → area auto-select → DB load (provenance `area '…' · 5-min · 1,866,593 rows`);
a page reload auto-loads from the store with no click; a file load clears the area and
shows `file 'Myrtle_…zip'`; no console errors. The generated `inrix_store.duckdb` is
gitignored.

---

## Session 30 — Date push-down: Restrict-dates on DB loads (ROADMAP Item 25) (2026-07-18)

Closed review finding **R3**: under the Item 23 area model a DB load could only ever
pull the *whole* area (the callback hard-coded the date restriction off, and the only
other trigger loaded from the file path), so an area accumulating months of merged
exports had no way to be date-restricted — the Myrtle area is already ~2.2 M rows.
Added a **date push-down** to the store and wired the Restrict-dates picker to DB
loads. Core + a small GUI wire-up; no new statistics, thin-shell split intact.

**Decision — exact UTC push-down (recorded).** `store.load_export` / `load_dataset`
grew `date_start` / `date_end` (inclusive **local calendar dates**) + `tz`, compiled
into the SQL `WHERE` on the UTC `Date Time` column. New helper `_date_bounds_utc`
converts the local dates to the **half-open UTC instants**
`[local_midnight(start), local_midnight(end)+1 day)` in `tz` (DST-safe `DateOffset`,
`tz` defaulting to UTC). Because comparing the stored UTC timestamp against those
instants is **tz-invariant**, the pushed-down cut is *exactly* the frame
`timebins.filter_date_range` keeps after `io.to_local` — matched to the row, DST
included — so no pandas re-trim is needed (the alternative "filter conservatively +
re-trim" the review floated was unnecessary once the bounds are computed the same way
`filter_date_range` does). An over-narrow range returns an empty, still-typed
tz-aware frame (the empty-column tz guard handles the 0-row read).

**Registry-sourced picker bounds.** New `store.area_local_span(area_key, tz)` reads
the `_areas` registry's UTC `date_min` / `date_max` back as local calendar dates — the
pushed-down frame no longer carries the full range, so the Restrict-dates picker's
widen-again bounds come from the registry, not the (trimmed) frame.

**GUI (`gui/app.py`).** The DB branch of `load_dataset` now passes
`date_start`/`date_end`/`tz` into `store.load_dataset` (push-down) and takes
`full_span` from `area_local_span`; the file branch is unchanged (localize →
`filter_date_range`). The **Restrict-dates picker moved out of the collapsed
file-loader accordion up to the top-level data control** (a sibling of Area/Bin) — it
governs the primary DB path now, not just a file parse. It became an **`Input` to
`_load`** (its `start_date`/`end_date`), so editing dates reloads the *current* source
(a DB area is re-scanned with the new bounds — there is no Load button on the DB path).
Trigger logic: a fresh **area switch** drops any stale restriction (and clears the
picker if it held one — a one-off that harmlessly re-fires once as a picker event); the
Load button and a picker edit apply the picker's dates; the picker's start/end are left
`no_update` on those paths (the self-Input can't echo, or it would loop). Dash accepts
the self-referential Input↔Output (confirmed by the HTTP smoke test).

**Tests (+9, 279 total).** *store*: push-down parity vs `filter_date_range` on the
localized frame (identity to the row, inclusive end-day), open-sided ranges, empty
over-narrow range degrade (typed empty frame, same columns), `area_local_span` from the
registry (local + UTC, unknown-area `KeyError`), `load_dataset` threading the dates
down. *gui*: Restrict-dates leads the panel outside the accordion (placement guard),
the picker feeds `_load` as an `Input`, and an end-to-end `ingest → DB load` restricting
a two-calendar-day export to just the January rows while `full_span` stays the whole
area. Updated the Item-24 accordion-placement test for the picker's new home.

## Session 31 — Store & app hardening (ROADMAP Item 26) (2026-07-18)

The Sonnet-eligible cleanup batch from the 2026-07-18 review — three small,
well-specified fixes (R5/R6/R9), run in the same sitting as Items 24/25. No new
compute; thin-shell split intact.

**R5 — per-callback DuckDB cursor.** `gui/app.py`'s `_db()` handed the *same*
connection object to every callback, but Dash's dev server is threaded and DuckDB
connections aren't safe for concurrent queries. `_db()` now returns a fresh
`con.cursor()` per call (the sanctioned per-thread pattern) over the still-lazily-opened
shared connection. Verified empirically that a cursor does **not** inherit the parent's
session `TimeZone` (it came back `America/Los_Angeles`), so `_db()` re-pins
`SET TimeZone='UTC'` on each cursor — the timestamp round-trip depends on it.

**R6 — `_merge_frame` drops NULL-key rows.** The keep-first anti-join tests
`o."<key0>" IS NULL` to spot non-matching (new) rows, but a row whose *own* key is NULL
never matches an existing row (SQL `NULL != NULL`), so NULL-key rows re-inserted on
*every* re-ingest — the exact duplication keep-first exists to prevent. `_merge_frame`
now `dropna(subset=present_keys)` up front, so NULL-key rows are never stored (real
exports shouldn't carry a NULL `Segment ID`/timestamp anyway) and re-ingest stays
idempotent.

**R9 — `put_export` docstring corrected.** The old text implied the GUI passes its
in-memory frames straight in "to avoid a re-read". The GUI's `ingest_to_db` deliberately
**re-reads the raw export** because the in-memory `Dataset.df` is CValue-filtered,
localized, and carries the derived `Delay` column — persisting it would store a
derived view, not the raw export. Docstring rewritten so nobody "optimizes" the re-read
away later.

**Tests (+2, 281 total).** *store*: `test_merge_frame_drops_null_key_rows` — a frame
with a NULL `Segment ID` row inserts once and re-ingests to +0 (no dupes). *gui*:
`test_db_hands_out_independent_utc_cursors` — `_db()` returns distinct cursor objects
(neither the shared connection), each UTC-pinned, both seeing the same underlying
database. Full suite green (281 passed).

## Session 32 — Corridor-scoped segment table + names in the DB (ROADMAP Item 27) (2026-07-18)

The owner-directed segment-table redesign (review R10–R12): fix the disappearing-rows
bug at the root, make membership an explicit Include/Exclude column scoped to one
corridor, and move friendly names out of the CSV side-file into the DuckDB store. GUI
redesign + a small store schema addition; no new statistics, thin-shell split intact.

**Owner decisions (top of session, recorded).** Include/exclude sets stay
**session-state only** (not persisted per area+corridor). The `_names` table is keyed
**globally by Segment ID** (INRIX ids are globally unique, so a name applies across
every area — simplest schema, an edit propagates everywhere). Network/Segment scope
list **all** segments; Corridor scope lists that corridor's segments.

**R12 — names in the store (`store.py`).** New **global** `_names` table
(`Segment ID` PK, `name`, `inrix_label`, `updated_at`), created additively on `connect`
(no `SCHEMA_VERSION` bump, no re-ingest). `save_names` upserts **last-write-wins**
(`INSERT … ON CONFLICT DO UPDATE` — an edit *overwrites*, unlike the keep-first
observation merge); a **blank** name deletes the row, clearing the override back to the
seed. `load_names` returns the `Segment ID`-indexed override frame `names.apply_names`
already consumes and is **read-only-safe** (no registry write on a load). The GUI
`load_dataset` applies stored names over the seed on **every** load via a guarded
`_stored_names()` (degrades to seed-only when the store is absent).

**Retired the CSV workflow.** `names.py`'s `write_names_template` / `write_names` /
`load_names` moved to `legacy/names_csv.py` (per CLAUDE.md "superseded → legacy/"); the
seed + resolver (`seed_names` / `simplify_label` / `apply_names`) stayed, with
`apply_names` now source-agnostic. Removed the GUI's Names-CSV input, Write-name-template
button, and the save-path status dance (`pytest` `pythonpath=["."]` so the tested-but-
retired `legacy/` package imports).

**R10 — selection identity bug fixed at the root.** Every table row now carries
`id = Segment ID`; all lookups go through identity, never a positional index into
`data`. `_row_selects_segment` reads `active_cell["row_id"]`; the reverse highlight
(`_highlight_row`) rewrites `style_data_conditional` with a `{Segment ID} = <sel>`
`filter_query` instead of writing a positional `active_cell` — so the accent lands on
the right row under any native sort and can't ping-pong. Dropped `row_selectable` and
`fixed_rows` (the sticky-header + full-data-replace combo was the documented
misrender-on-replace trigger; a plain scroll container replaces it).

**R11 — corridor-scoped Include/Exclude.** `_build_table` (Inputs: token, scope,
corridor; ToD/DOW as States so a slider move doesn't wipe edits) is the sole owner of
`segment-table.data`, building rows for `_corridor_segment_ids`. Membership is an
editable **Include/Exclude** dropdown-presentation column (default all-Include).
`_exclusions_changed` derives the excluded set + effective member set from the table
`data` (id-keyed) **without rewriting `data`** — the per-toggle full rewrite was part of
the disappearing-rows bug. No exclusion → members `None` (natural corridor/network
grouping); any exclusion → the included subset overrides the sum. The map dims **only
excluded** segments (a new `corridor-excluded` store feeds `_map`; `member_ids` there =
all−excluded), so the default view dims nothing. Save-names writes the edited Name
column to the store and refreshes the live labels.

**Tests (+~8 net, 286 total).** *store*: `save_names` last-write-wins + blank-clears,
`load_names` empty-typed + column guard. *names*: split — CSV round-trip tests moved to
`test_legacy_names_csv.py`, `apply_names` tests rebuilt on direct override frames.
*gui*: id-keyed rows + all-Include default, corridor scoping, Include-column
editable/dropdown spec, exclusions survive a (reversed) sort, save→store→apply
round-trip incl. blank-clears, stored names apply on a DB load. Updated the headless
layout test (CSV controls gone, `names-status`/`corridor-excluded` present) and the
real-export end-to-end. **286 pass.** Verified live on the real Myrtle store: app
auto-loads (the `_names` table is created additively), the redesigned table renders with
the Include dropdown + id-keyed rows, and switching to Corridor scope re-scopes the
table to the picked corridor's 11 segments.

---

## Session 33 — Review of an outside INRIX↔Google comparison + scoping the validation batch (Items 28–30) (2026-09-17)

A **review-only** session (Opus). The owner had an outside agent (Gemini, via
`antigravity-cli`) stitch INRIX XD segments into corridors matching a Google Maps
travel-time log (`TT Logger.xlsx`, 15 sheets) and compare the two sources across the
2026 D3 export, then asked whether the analysis was sound. Only the HTML report
generator (`scripts/generate_corridor_html_reports.py`, untracked) and the two output
CSVs under `out/` existed in the working tree; the analysis script itself was recovered
from the agent's own session store. **No code changed this session.**

Verification was done by re-running the join independently from
`out/extracted_query_segments_2026.csv` (5,457,816 rows, 234 segment ids) and
`TT Logger.xlsx` against `out/query_segment_mapping.json`.

**Verdict: sound plumbing, unsound conclusion.** The joins, clocks, and chains hold up;
the headline ("the two data sources are functionally interchangeable") is contradicted
by the study's own numbers.

**G9 — what checks out (verified, not assumed).** The recovered script slices
`Date Time[:19]`, discarding the offset; that is **correct** here, because the export
carries a real DST switch (`-07:00` → `-06:00` at 2026-03-08, confirmed by an offset
tally over the raw CSV), so the slice yields true local wall-clock, which is the frame
the reference timestamps are in. The sheet headers' stated logging windows
(`Start 06:00 / End 18:00`) sit an hour off the observed hours (05–16), so alignment was
tested directly: a **±90-minute lag scan in 15-minute steps** puts the RMSE minimum at
**lag 0** on every corridor with real diurnal signal (VSL NB AM: 0.67 at lag 0 vs 1.04
at ±30). `round("15min")` instead of `floor` is **benign** — reference samples land
~50 s past each quarter hour, so the two are identical for every row — but latent.
CValue ≥ 75 everywhere (mean ≥ 96), every chain terminates at its intended end segment,
every query point sits within ~90 ft of its chain, and segment counts are constant
within each corridor's matched bins.

**G1 — the sources are not interchangeable for delay, which is the stated use case.**
Ratio of INRIX to reference for mean delay above each source's own 10th-percentile
free-flow, with the SD ratio (symmetric, so not a regression-dilution artifact):
Eagle Rd NB/SB 0.52/0.53 (SD 0.56/0.54), SH-69 SB 0.37 (0.38), SH-69 NB 0.63 (0.64),
Franklin EB/WB 0.54/0.62 (0.59/0.69), VSL NB AM/PM 0.69/0.64 (0.66/0.65). On every
signalised arterial INRIX reports **about half** the delay and compresses the variance
by about the same factor, while agreeing to within a minute on rural free-flow
(Cascade-HSB bias −0.15 min over 50.8 mi). A before/after study scored on INRIX would
report roughly half the effect size in minutes.

**G2 — the stated explanation for the largest biases is unsupported.** The report says
"our spatial analysis **proves** this is not data error, but query geometry mismatch:
Google queries extended south of I-84 … and captured I-84 off-ramp signal queues."
Measured: the Eagle Rd NB query start is **72 ft** from its chain start, SH-69 SB
**90 ft** — there is no extra extent. The queuing story also fails at free flow: at
05:00 SH-69 **SB** already runs 3.85 min above INRIX while **NB** is 0.29 min off over
the same endpoints reversed. Something route-specific is happening at that interchange,
but no evidence was offered and "proves" is unearned.

**G3 — bad reference data reported as INRIX error.** "Franklin WB - No Mid" is a strict
sub-route of "Franklin WB", yet Google's sub-route time **exceeds its own full-route
time in 99.9% of 2,760 shared bins** (6.42 vs 5.68 min) — physically impossible. INRIX
gets it right (sub < full in 100% of bins). The corridor is listed as third-worst at
32.2% MAPE. Both sheets were in hand; the check was free.

**G4 — coverage collapse hidden behind a study-wide banner.** "HSB-Cascade" has 352
matched bins, **all between 2026-01-02 and 2026-01-26 (16 days)** — the reference
logger stopped; INRIX is complete all eight months. It sits in the scorecard beside
2,936-bin corridors under "Study Period: Jan 1 – Aug 24, 2026", which is wrong for 13
of the 15 sheets (most end in late June).

**G5 — sample size inflated by overlap.** "28,340 Matched Observations" sums 15 sheets,
many of them overlapping sub-routes of the same pavement: the four Franklin sheets
("12,009 matched intervals") cover the same ~3 miles, and the VSL sheets are a
sub-segment of Eagle Rd Full. The bins are also heavily autocorrelated at 15 minutes.

**G6 — no uncertainty quantification anywhere**, against this project's stated standard
(effect size + CI, CLAUDE.md). The biases do survive it — block-adjusted 95% CI on
Eagle Rd NB is [−8.60, −8.17] — but that should be shown, not assumed. Relatedly the
scorecard leads with correlation: Eagle Rd SB is promoted as "highest correlation in
Idaho (r = 0.945)" while carrying a **5.49-minute** systematic bias. The report's own
section on why R² can mislead is correct in spirit, then misses the case running in the
opposite direction.

**G7 — chain length never normalised against the requested route.** `trace_chain`
includes whole end segments, so the VSL chain is **3.635 mi against a ~3.00 mi request
(+21%)** and Franklin WB is **3.453 mi where Franklin EB is 2.993 mi** for the same
physical extent (0.78 mi of overshoot on one end). VSL's good-looking MAPE is partly
INRIX being 21% longer cancelling against INRIX being faster. The endpoint snap was
also measured in **raw lat/lon degrees** (~28% anisotropic at this latitude) —
harmless at 90 ft, not in general.

**G8 — missing segments handled by a silent fallback.** Three Eagle Rd NB members and
one Franklin member are absent from the export (0.25–0.29 mi each — the short stubs at
the signalised ends, small in miles but exactly where delay concentrates).
`eval_df = complete if len(complete) > 0 else merged` swallows this; a `17/20` string
in one column is the only trace.

**G10 — process and code.** The analysis script **never entered the repo** and had to
be recovered from `~/.gemini/antigravity-cli`; nothing is reproducible or tested. The
surviving report generator computes MAE, bias, and `np.polyfit` slopes inline while
building figures (`scripts/generate_corridor_html_reports.py:478-521`) — the
`process_and_plot_*` fusion this project exists to undo — over a hardcoded
`BASE_DIR = "/home/hansrkid/Inrix"`. `google_extra_tt_min` and CValue are extracted and
never used. `TT Logger.xlsx` (the only copy of the reference data) is untracked **and**
not gitignored in the repo root. Label errors: "Rural Highway (SH-17)" (no such route;
the same report names it Banks-Lowman correctly two sections later), Franklin described
as "Nampa to Caldwell" (the coordinates are entirely within Nampa), "Data Resolution:
15-Minute Intervals" (the reference samples about twice an hour).

**Scoping (appended to ROADMAP.md).** Grouped per CLAUDE.md into three session-sized
items, IDs continuing from 27: **28** — pure-core corridor chain assembly with endpoint
trim, projected-CRS snapping, and explicit missing-segment accounting (G7/G8; **Opus**;
supersedes the *Automatic corridor assembly* Future bullet); **29** — the external
reference loader, consistency gates, and agreement statistics with CIs
(G1/G3/G4/G5/G6; **Opus**, stats split **Fable-eligible**; needs 28); **30** — the
report rebuilt as a thin shell over the core plus the arterial delay-compression
finding recorded in DATA_FORMAT (G1/G2/G10; **Opus**; needs 29).

---

## Session 34 — Corridor chain assembly in the core: snap, walk, trim, account (ROADMAP Item 28) (2026-09-17)

Item 28, the first build session off the Session 33 review. New pure-core module
`src/inrix_tools/corridors.py` — no GUI, no file paths, no plotting. It takes the one
part of the outside pass that held up (the `NextXDSegI` walk) and adds the half that was
missing: a projected-CRS snap, an explicit `reached_target`, the endpoint **trim**, and
**missing-segment accounting** that a report cannot lose (G7/G8). Items 29 and 30 build
on it.

**`build_chain(source, start_latlon, end_latlon)` → `ChainResult`.** A frozen dataclass
carrying the ordered ids, per-segment `Miles`, `in_extent_fraction`, `chain_miles`,
`requested_miles`, both trims, both snap distances **in feet**, `reached_target`, and a
`stop_reason`. `source` is either an already-loaded XD GeoDataFrame or a path handed to
`geometry.load_xd_network`. `frame()` gives the per-member table with the chain summary
in `attrs`; `weights()` gives the proration weights.

**Snapping is metric, and ambiguity is resolved by connectivity.** `project_network`
reprojects to the network's own UTM zone (`estimate_utm_crs`); `_metres_per_unit` reads
the CRS's axis unit so a US-feet State Plane also reports real feet. Since the *nearest*
segment is often the opposing carriageway, `build_chain` tries the `k` nearest candidates
at each end and keeps the pair that **reaches** the target, cheapest snap first.

That preference needed a guard, found by a test: with candidates ranked only by
"reaches the target", a 2-mile query collapsed onto a **single** segment 1,800 ft away
that trivially "reached" itself — `reached_target=True`, `requested_miles=0`. Hence
`max_snap_feet=500` (real query points snap within ~100 ft), with the nearest candidate
kept regardless when nothing is in range, so an out-of-range snap shows as a large
`snap_start_feet` rather than an exception or a silent collapse.

**The walk reports instead of raising.** `walk_chain` returns `(ids, stop_reason)` with
`target` / `dead_end` / `off_network` / `cycle` / `max_steps`. `off_network` is kept
distinct from `dead_end` on purpose: it means the next id isn't in the *loaded subset*
(widen the bbox), not that the road ends.

**Trim — decided and recorded: prorate by default, report always.** Measured on the real
statewide shapefile, reproducing G7 to three decimals: VSL NB AM assembles **3.635 mi**
against a **3.006 mi** request (+21%, with 0.626 mi of it one overshooting end segment),
and Franklin **WB 3.453 mi vs EB 2.993 mi** over the same physical extent. Trimmed, the
two Franklin directions agree to **0.001 mi** (2.763 / 2.764) — which is the argument for
proration: the length bias is large, systematic and *directional*, while proration's cost
is a uniform-speed-within-segment assumption that is unbiased in sign. `chain_travel_time`
prorates the two end segments by their covered fraction by default and reports
`Length(Miles)` as the requested extent; `prorate=False` gives the whole-segment sum for
when the chain's own extent is what's being described. The assumption is stated in the
docstring and in DATA_FORMAT.md — it is weakest exactly where it's used (an end segment
cut at a signal, where delay concentrates at the stop bar).

**Missing segments are accounted, never absorbed.** `chain_coverage(chain, observed)`
takes an export frame (value-aware: an all-NaN metric counts as missing) or plain ids and
returns per-member `n_obs` / `observed` with `attrs` `n_missing`, `missing_miles`,
`observed_miles`, `miles_covered_fraction`. `chain_travel_time` delegates to
`speed.corridor_travel_time` with `members=` and `expected="total"`, so the complete-set
rule is enforced over exactly the chain's membership — a missing member drops the
timestamp instead of shortening the sum. No `complete if len(complete) else merged`
fallback exists anywhere in this path.

**Tests (+19, 305 total).** Synthetic: chain walk, dead end, off-network, cycle,
max-steps; trim arithmetic hand-computed (1-mile segments, points 25%/50% in →
3.0 whole / 2.25 requested / 0.25 + 0.50 trimmed); single-segment span; unreachable →
`reached_target=False` and a NaN end trim; opposing-carriageway candidate choice; the
`max_snap_feet` trade-off both ways (a 50-ft disconnected stub beats a 600-ft mainline,
and `None` flips it); `Miles` fallback to projected geometry; coverage with a missing
member and with a NaN-valued one; proration vs whole-segment sums; complete-set rule with
a gap. **Projected-vs-degrees**: a point 0.010 deg north of one segment and 0.012 deg east
of another — degrees rank the northern one nearer, metres rank the eastern one nearer
(1,113 m vs 967 m), and the projected answer is what's returned. Real-shapefile tests
(skipped without `USA_Idaho_shapefile.zip`, bbox-filtered so each runs in <1 s) pin the
Franklin asymmetry and the VSL +21% overshoot as regressions.

**Not in this item:** the TT Logger workbook loader and its consistency gates (Item 29),
which is also where `TT Logger.xlsx`'s tracked-vs-gitignored home gets decided; the report
rebuild (Item 30). DATA_FORMAT.md gained a "Corridor chain assembly, endpoint trim &
proration" section; the Future *Automatic corridor assembly* bullet is now delivered.

---

## Session 35 — External travel-time reference: loader, gates, agreement statistics (ROADMAP Item 29) (2026-09-17)

Item 29, run straight after Item 28 in the same sitting. Two new pure-core modules —
`reference.py` (load the TT Logger workbook, then check it against **itself**) and
`agreement.py` (INRIX-vs-reference statistics with uncertainty) — plus the source-data
hygiene decision. No GUI, no plotting; Item 30 is the report shell over these frames.

**`reference.load_tt_logger` — one tidy tz-aware frame.** Each sheet is a five-row
header block over a `Timestamp` table. Both header **dialects** are handled because the
same workbook mixes them: `Origin`/`Destination` is either a `lat,lon` pair or a place
name (which *also* contains a comma — "Garden Valley, ID" — so the parse is by value, not
by separator), and `Start`/`End` arrives as a time, an `"HH:MM"` string, or an **Excel day
fraction** (`0.625` → 15:00). Place-name routes are marked `chain_matchable=False`: their
extent is a geocoder's guess, and `route_endpoints` refuses to hand one to
`corridors.build_chain` rather than returning coordinates that look authoritative. 3 of
15 sheets are city-to-city.

**Timezone, and a fold nobody mentioned.** Timestamps are naive local wall clock, so they
are localized explicitly (`America/Denver`). Doing it properly surfaced something the
outside pass never saw: the logger samples **through the 2025-11-02 fall-back fold** on 9
of 15 sheets (01:15 and 01:45 occur twice), which is 36 samples that have no single
correct offset. They are dropped and **counted** in `attrs['n_ambiguous_dropped']`, with
`ambiguous=`/`nonexistent=` exposed for a caller who wants a different policy.

**`align_to_bins` floors.** An INRIX bin is labelled by its start, so a sample belongs to
the bin containing it. Session 33 called `round` "benign but latent" — on the current
workbook it is no longer latent: the two rules disagree on **17 of 62,321** samples (a
sample at :58:50 floors to `:45`, rounds to the next hour). The raw instant is preserved
as `Sample Time`.

**An API trap, caught by a test.** `attrs` is JSON-encoded on `to_parquet`, so the route
table cannot live there as a DataFrame (nor can a `datetime.time`) — the whole frame
becomes unwritable. The route table rides as **records** with clock values as
`"HH:MM:SS"` strings, and `routes_frame(obs)` turns it back into a frame. A parquet
round-trip test guards it.

**The three gates, and what they found.**

- **`nesting_gate` (G3).** A sub-route cannot exceed the route containing it. Measured:
  "Franklin WB - No Mid" exceeds its own full "Franklin WB" in **99.93% of 4,524 shared
  bins** (6.40 vs 5.68 min) — reproducing the review's 99.9% / 6.42 vs 5.68 — while the
  **EB pair is clean at 0%**, so the fault is the reference's, not the check's. Pairs are
  derived from assembled **chains** (proper segment-set containment), not from sheet
  names, since nothing in "- No Mid" says it is a subset.
- **`coverage_gate` (G4).** Per-route `first`/`last`/`n_days`/`span_days` and, against a
  study window, `days_in_window` / `window_covered_fraction` / `covers_window`. On the
  current workbook every route but "VSL NB PM" spans the 2026 export window (VSL NB PM
  stops 2026-06-25), and window coverage ranges 0.30–0.71 — worth noting that the
  workbook has been extended since Session 33, so G4's 16-day HSB-Cascade collapse is not
  reproducible against today's file; the gate reports coverage either way, which is the
  point.
- **`lag_scan` + `lag_summary` (G9).** The alignment statistic is `sd_diff` — RMSE with
  the systematic bias removed — because a lag scan reads **shape**, not level. This was
  not a theoretical nicety: scanning on raw RMSE put the apparent best lag at +30/+60/+90
  min on 5 of 10 real corridors, purely because a large bias dominates the residual; on
  `sd_diff` **every** route minimises at lag 0 with `improvement = 0.0`. That is the
  clock-agreement evidence Session 33 asserted, now measured the right way.

**`agreement.compare` — effect size with an honest interval.** One row per route:
`bias` (INRIX − reference) with a **day-blocked** 95% CI, the per-bin CI beside it as
`*_naive`, and `ci_width_ratio` showing the inflation (0.9–2.4× on the real corridors);
MAE / RMSE / MAPE; `sd_ratio`; `delay_ratio` (mean delay above **each source's own** 10th
percentile); Bland-Altman limits of agreement; and `r` last on purpose (G6 — Eagle Rd SB
was promoted on r = 0.945 while carrying a 5.49-minute bias). Day-blocking follows this
project's existing answer to autocorrelation (REVIEW_ITEM14 §4.1, `beforeafter`'s
day-mean unit). `match_bins` averages multiple reference samples in a bin and keeps
`n_ref_samples`; `independent_totals` reports distinct days and **distinct pavement**
beside the naive sums.

**End-to-end check against the real data** (10 chain-matchable routes, the 2026 D3
15-minute export, 5.46 M rows). The pipeline composes: chains → `chain_travel_time` →
`match_bins` → `compare`. It corroborates G1 independently — Eagle Rd SB bias
**−5.46 min** (review: 5.49), Eagle Rd NB **−8.38** with a day-blocked CI of
[−8.47, −8.23] (review: [−8.60, −8.17]), delay ratios 0.54 on the Eagle Rd pair and
0.37–0.67 across the signalised arterials, SD ratios tracking them within ~0.05. And it
reproduces G5 with numbers: 28,431 matched bins over only **4,411 distinct quarter
hours**, 43.84 summed chain-miles over **33.63 distinct miles** (10.21 double-counted
across four overlapping pairs).

**One Item 28 change fell out of that run.** `build_chain`'s `k_candidates` default went
**4 → 8**. At k=4 two of the ten routes silently returned a wrong chain: SH-69 SB stopped
after 0.41 mi (against 7.17 mi northbound) and VSL SB walked 8.07 mi past its target,
because at a signalised intersection six segments — cross-street approaches, the opposing
carriageway, turn stubs — can sit nearer the query point than the right one (the correct
VSL SB end segment ranked **7th**, at 47 ft). At k=8 both resolve to their northbound
mirror image with zero missing segments. The missing-segment accounting flagged both
failures before the fix (coverage 0.010 and 0.606), which is exactly what it is for.

**Tests (+32, 337 total).** *reference*: a synthetic two-dialect workbook written with
openpyxl (coordinates + `datetime.time` on one sheet, place name + Excel day fraction on
the other); endpoint and clock parsing including the "place name with a comma" trap;
`route_endpoints` refusing a geocoded route; a missing `Timestamp` header naming its
sheet; tz-awareness and minutes-from-seconds; the DST fold dropped **and** counted, with
the policy override; the parquet round-trip; floor-vs-round on a `:58:50` sample where
they genuinely differ; naive timestamps refused; each gate firing on a constructed
violation and staying quiet on a clean case; `nested_pairs` from segment sets; lag scan
finding 0 when aligned, +30 when shifted, and `sd_diff` provably invariant to a constant
bias while `rmse` is not. *agreement*: hand-computed bias / MAE / RMSE / MAPE / SD ratio /
LoA / r; the delay ratio on a constructed half-delay case; degenerate samples reporting
NaN rather than a width-0 interval; **CI widening under injected autocorrelation** — same
marginal SD, AR(1) ρ=0.95 within days, day-blocked interval >2.5× wider while the per-bin
interval barely moves; `independent_totals` refusing to double-count overlapping chains.
Real-workbook tests (skipped without the file) pin the Franklin nesting violation, the
dialect mix and the fold count.

**Source hygiene, decided.** `TT Logger.xlsx` is **raw logged input** that grows every
week the logger runs, so it follows the raw-export rule: gitignored, kept locally, never
committed — tests use the synthetic fixture and skip the real-file cases when absent.
`openpyxl` joined the core dependencies (pandas' `.xlsx` reader). DATA_FORMAT.md gained
an "External travel-time reference" section and the k-candidate finding.

**Not in this item:** the figures and the report (Item 30), which is also where the
arterial delay-compression finding gets written up, and where
`scripts/generate_corridor_html_reports.py` is retired.

---

## Session 36 — The validation report as a thin shell, and the delay-compression finding (ROADMAP Item 30) (2026-09-17)

Item 30, closing the Session 33 review batch. The 2026-09-17 HTML report generator was
the last surviving piece of the outside pass and the clearest instance of the fusion
this project exists to undo: MAE, bias and `np.polyfit` slopes computed inside figure
loops over a hardcoded `BASE_DIR` (G10). It is now
`legacy/generate_corridor_html_reports.py` — kept unchanged but for a retirement note,
per CLAUDE.md's Style rule — and the report is rebuilt as three layers: the core owns
every statistic, `gui/validation_figures.py` + `gui/validation_report.py` place values,
and `scripts/build_validation_report.py` is argparse wiring with no hardcoded paths.

**The split is enforced by tests, not by intent.** Several tests hand a builder a
summary row whose values contradict the matched data it is drawing (a CI bound of 99,
a bias of −42, LoA of ±9) and assert the figure shows *the row's* number. A builder
that recomputed anything fails them. The 1:1 scatter draws no fitted line at all —
the retired report's OLS-inside-the-figure is the specific thing being removed, and the
honest summary of the gap (bias, its interval, the delay ratio) is already computed
once, in `agreement.compare`.

**Four small core additions, because a figure may not compute.** `agreement.profile`
(group a matched frame by time-of-day / day-of-week / date and average both sources —
the diurnal curve, the day × hour bias grid and the daily-bias series are all means,
and a mean is a statistic); two cadence columns on `reference.coverage_gate`
(`obs_per_day`, `median_sample_minutes`, so a report states the sampling rate it
**measured** — the outside pass published "Data Resolution: 15-Minute Intervals" for a
reference logged every 30); and `corridors.chain_attributes` / `chain_description`,
which read a corridor's road names, county and ZIPs **off the XD segments**.

**Labels are now checked, not proofread.** `validation_report.label_disagreements`
extracts route numbers from each caption and verifies them against the chain's own
`RoadName`/`RoadNumber`/`RoadList`. "Rural Highway (SH-17)" over segments carrying 55
produces a row in a table the report prints. The match is on the **number**: XD's
`RoadNumber` is bare (`"55"`), so the check catches a caption naming a route the chain
is not on — the failure it exists for — and does not pretend to adjudicate SH- vs ID-.
The shipped captions are clean, and the two Session 33 label bugs have a regression
test (no "SH-17"; Franklin is Nampa, not "Nampa to Caldwell").

**`corridors.chain_between_segments` (new) — the place-name routes, carried honestly.**
Three of fifteen sheets name cities, so `route_endpoints` refuses them and Item 29 left
them out; but they are the *rural* half of the finding, and a finding about arterials
needs its contrast measured, not recalled. A route can now be given as two terminal
segments (operator input, `scripts/d3_place_name_routes.json`, provenance recorded in
the file) and the chain is walked between them, with everything a query point would
have provided explicitly absent: `extent_fraction` all 1.0, `requested_miles ==
chain_miles`, and `snap_*_feet` **NaN** rather than a fabricated 0.0 that would read as
a perfect match. The two ID-55 chains reproduce the corridor at **50.76 / 50.78 mi**
against the outside mapping's 50.758 / 50.777.

**What the run found** (2026 D3 15-min export, 5,457,816 rows, 15 routes, 38,873
matched bins). Across 11 usable arterial routes the **delay ratio** has a median of
**0.59** (range 0.37–0.95) and the **SD ratio** a median of **0.62** (0.36–0.88) — the
G1 compression, reproduced independently and now with the spread shown rather than a
single "about half". The rural pair is the contrast: Cascade→HSB bias **−0.16 min**
[−0.84, +0.55] and HSB→Cascade **+0.48** [−0.20, +1.19] over 50.8 miles, MAPE 2.5%,
delay ratios *above* 1 — both CIs include zero. G2 is contradicted with measurements:
the query points snap 5/4 ft (Eagle Rd NB), 4/5 ft (SB), 18/2 ft (SH-69 NB) and
64/18 ft (SH-69 SB) from their chain ends, and at 05:00 SH-69 SB already runs 3.94 min
below the reference while NB over the same endpoints reversed is 0.37 min off.

**Two routes are excluded from the headline and kept visible.** "Franklin WB - No Mid"
(reference data internally impossible — 99.9% of 4,524 shared bins exceed its own
containing route) and "Garden Valley-HSB" (**the chain dead-ends after 11.08 mi** of a
~26-mile route, so the INRIX side is not that route — a new chain-side exclusion that
the Session 33 numbers had no way to catch). Exclusion is reserved for those two cases;
everything else — thin coverage, a sheet that stops early, a sub-extent of another
sheet, missing member segments, end-segment overshoot, an operator-stated extent — is a
**note** that travels with the route and prints beside every number it touches.

**Framing that is structural rather than careful.** Each corridor page prints that
route's own first/last sample, day count, span fraction and measured cadence — there is
no study-wide banner to hide behind, because the fifteen sheets span 2025-08-15 to
2026-09-15 with 30–71% coverage of the export window. The scorecard leads with bias and
its day-blocked CI and puts `r` in the last column. The index shows the naive total
beside the honest one: 38,873 matched bins over only **5,931 distinct quarter hours**,
162.46 summed chain-miles over **146.24 distinct** (16.22 double-counted across eight
overlapping pairs).

**Tests (+40, 377 total).** *report/figures* (`tests/test_validation_report.py`, 29):
the "figure shows the row's number" trio; no OLS anywhere in the scatter; placeholders
on empty frames; flagged routes marked with ⚠ **and** the critical colour (never colour
alone); the diverging grid symmetric about zero; label-check hit, miss and
SH-/ID-equivalence; every flag route (impossible nesting, thin coverage, partial
window, dead-ended chain, overshoot, missing members, operator extent, non-zero lag);
page assembly — an index and a page per corridor group, an excluded route keeping its
page *with* the reason while the scorecard still lists it marked, per-corridor coverage
on every page, bias before delay ratio before `r` in the header, an unknown sheet
landing on an "other" page instead of vanishing. *core*: `agreement.profile` (four
groupings, bin width, Monday-first ordering, unknown key); coverage-gate cadence;
`chain_between_segments` (walk, short walk as a finding, off-network error, NaN snaps);
`chain_attributes` / `chain_description`.

**Not in this item.** A dark-mode variant of the report pages; publishing it anywhere
(it writes to a `--out-dir`, default `out/`, which stays gitignored). Figures were
verified structurally and by reading the rendered HTML, not by screenshot — rendering
Plotly to an image needs a browser this machine does not have.

---

## Session 37 — Second review of the validation work: the finding holds, the statistic doesn't (Items 31–33) (2026-09-17)

A **review-only** session (Opus), run straight after Item 30 landed. The owner asked for
a review of the outside corridor analysis; that analysis had already been reviewed in
Session 33 and rebuilt in Sessions 34–36, so this session re-derived the conclusions
**from the raw export** rather than from the previous review's notes, and checked whether
the rebuild delivers the guarantees it advertises. **No code changed; DATA_FORMAT.md
gained the CValue-imputation section, and Items 31–33 were appended to ROADMAP.md.**
(The owner clarified afterwards that they had meant a *different* analysis, at
`../corridor analysis`, which this session never touched and which is going to another
session.)

Verification re-ran the join independently from `out/extracted_query_segments_2026.csv`
(5,457,816 rows, 234 segment ids), `out/inrix_vs_google_matched_timeseries.csv` (28,340
rows), `out/validation_report/tables/*` and the statewide XD shapefile. The full suite
passes (**377 tests**).

**The headline finding survived three independent attempts to explain it away.** This is
the main result of the session and it strengthens Item 30's conclusion rather than
qualifying it:

- *Historical backfill* — 21.7% of export rows carry a **null CValue**, and 88.3% of
  those have `Speed` exactly equal to `Hist Av Speed` (vs 11.3% of rows that do carry
  one): they are imputed, not observed. But the backfill sits overnight. On the
  arterials it is ~0% through the whole 05:00–17:00 logging window (Eagle Rd NB 3.5%
  overall, 0% at every daytime hour). Recomputing every route on bins with <5% imputed
  members moved bias by **≤0.3 min** everywhere (Cascade–HSB −0.15 → −0.29; Eagle Rd NB
  −8.38 → −8.41). Ruled out — and recorded in DATA_FORMAT, because the *rural* early
  morning is 57% imputed at 05:00, so the gate has to be checked rather than assumed.
- *Missing pavement* — real but far too small. Eagle Rd NB is missing 0.290 mi of a
  6.938 mi request (4.2%), Eagle Rd SB 0.260 mi, Franklin EB/WB 0.250 mi each (9.0%).
  Nowhere near 8.4 minutes.
- *Clock lag* — already excluded in Session 33 and confirmed by the rebuild's own
  `lag.csv` (best lag 0 on every arterial).

**The finding is sharper than "roughly half" — it is a slope.** Regressing per-bin INRIX
delay on reference delay (each above its own 10th percentile) gives arterial slopes with
**near-zero intercepts**: median slope **0.534**, Eagle Rd NB 0.517 / 0.037, Eagle Rd SB
0.514 / 0.080, Franklin EB 0.465 / 0.066, SH-69 NB 0.566 / 0.085, VSL NB AM 0.617 /
0.097. A near-zero intercept means the disagreement is **multiplicative in delay, not a
fixed offset** — which is the worst case for this project's primary use, because a
multiplicative factor does **not** cancel in a before/after difference. Item 30's
DATA_FORMAT wording ("about half the effect size in minutes") is right but asserted;
Item 32 replaces it with the measured coefficient and an interval.

**Three defects the first review missed.**

*R1 — the complete-set guarantee is not the one the code provides.* `corridors.py:504-508`
states that with `expected="total"` "a missing member drops the timestamp rather than
silently shortening the sum." It doesn't: `mark_complete_timestamps` resolves the
expected count to `groupby(corridor_col)[SEGMENT_COL].transform("nunique")`
(`speed.py:227`) — every segment *ever seen in the frame*. A member with **zero** rows
never enters the count and so can never fail it. Eagle Rd NB therefore runs with
`expected_segments = 17` against 20 requested members, and all **2,633** bins in the
shipped `summary.csv` are marked complete while summing 17/20 of the route. This is
Session 33's G8 reappearing in subtler form — Item 28 killed the
`complete if len(complete) > 0 else merged` *expression*, not the class of defect. Small
in miles here (4.2%); the problem is the advertised guarantee.

*R2 — proration is applied to end segments carrying no data.* Eagle Rd NB's chain
**first and last segments are both missing** (`1187377510`, `474858971`), as is
Franklin EB's start (`119739402`). `chain_travel_time` prorates them by their trim
fraction and still reports `Length(Miles) = requested_miles`, so the length credits
pavement contributing zero travel time and every derived `Corridor Speed(miles/hour)` on
those routes is overstated by ~4%. Those stubs are the signalised ends — exactly where
delay concentrates.

*R3 — `delay_ratio` is the least stable statistic in the report.* It is a ratio of two
small means and carries no interval, in a report that attaches a day-blocked CI to
everything else. **VSL SB PM reports a delay ratio of 2.32 against a regression slope of
0.71** on the same bins (r² 0.167). The slope is better conditioned and admits a CI.

**Also noted, on the outside pass itself** (beyond Session 33's G1–G10): the recovered
`compare_inrix_google.py` writes *only* the summary CSV — it builds `detailed_corridor_dfs`
and never saves it, and `out/inrix_vs_google_matched_timeseries.csv`, which the HTML
report actually reads, uses different column names (`google_timestamp`/`inrix_tt_min` vs
the script's `google_ts`/`total_tt`), so **the recovered script is not the version that
produced the shipped artifacts**. Its `is_complete` also counts segment *ids* rather than
*values* (`seg_count=("Segment ID","nunique")`), so 428 Garden Valley-HSB bins are marked
complete while containing a NaN travel time — a second, independent undercount path that
the rebuild's value-aware completeness already fixes. And Eagle Rd NB has **zero**
complete bins over its 20 mapped segments, so the entire worst-corridor headline
(−8.38 min) came through the `else merged` fallback.

**Two things checked and found clean**, recorded so they are not re-examined: the
reference cadence is a true 30 min (median gap 30.0 on every sheet, confirming Item 30's
measured-cadence column and the outside report's "15-Minute Intervals" label error), and
`round("15min")` produces **no** duplicate `(sheet, dt_bin)` pairs in the 28,340 matched
rows, so the rounding-vs-floor latency Session 33 flagged as benign-but-latent never
double-counted anything.

**DATA_FORMAT.md** gained a CValue subsection: the null-CValue-as-imputation-marker rule
with the 21.7% / 88.3% measurements, the hour-and-route dependence (arterials ~0% in the
daytime window, Cascade–HSB 57% at 05:00), and the note that `Speed(miles/hour)` is
integer-valued with an observed 5 mph floor.

**Scoping (appended to ROADMAP.md).** Three session-sized items, IDs continuing from 30:
**31** — requested-vs-observed completeness, no proration over unobserved ends, and a
recorded CValue gate (R1/R2; **Opus**); **32** — the delay compression as a regression
slope with a day-blocked CI, the free-flow level gap separated from the slope, and the
DATA_FORMAT finding restated (R3; **Opus**, estimator **Fable-eligible**; needs 31);
**33** — the directional free-flow level gap on SH-69 SB (−4.69 min at the 10th
percentile while NB over the same endpoints reversed is −0.82) and Eagle Rd NB (−5.36),
testing **path** equivalence rather than the endpoint equivalence both reviews have
already confirmed (**Opus**; needs 32).

---

## Session 39 — The AADT join picks ramps over the mainline on divided highways (ROADMAP Item 34) (2026-09-17)

*(There is no Session 38 entry in this file. ROADMAP.md's Items 34–37 preamble cites
one — the review of an outside district-wide corridor screening that scoped the
batch — but it was never written up here; the batch's framing lives in that preamble.)*

**The bug.** `aadt.join_aadt` matched each XD segment to the **nearest** AADT line
passing a 45° bearing gate. On a divided highway that rule is systematically wrong:
the ITD layer carries one mainline centerline, which sits 22–30 m off *each*
carriageway, plus a record per ramp movement — and the ramp runs parallel, so it
clears the bearing gate from 3–8 m and wins. One carriageway got the mainline, the
other got ramps, and both reported `aadt_source == "matched"`.

Reproduced on the 2026 D3 export before touching anything: I-84 **westbound** read
`147500, 18000, 145500, 10500, 135000, 13000, 13500, 122000, 10500…` segment to
segment against a smooth eastbound `114500 → 122000 → 135000 → 145500 → 147500` over
the same ground — length-weighted **61,410 WB / 122,813 EB** on the 28+30 mainline
segments of the screening's I-84 corridor (the 61,410 reproduces the ROADMAP figure
exactly). The same mechanism was worse than ramps alone: on rural I-84 a **frontage
road** record of *70 vehicles/day* at 2.8 m beat the interstate at 28 m.

**What the layer actually carries.** `Route` and `Segment` are **null on all 8,301
2024 rows** — dead diagnostics. The live identifier is `RouteID` (`01010AIN084` →
I-84; five digits of route-segment number, a suffix letter, a two-letter class
`IN`/`SH`/`US`/`OH`, a three-digit number; `OH` is always `000`). `Descriptio` /
`Descript_1` carry the only facility-type signal: a ramp is written as its movement,
a mainline record as the cross-streets it runs between. Both are source-truncated at
40 chars.

**The fix, in four parts.**

1. **`geometry.segment_geometry` carries the XD identity** (`IDENTITY_COLS` = `FRC`,
   `RoadNumber`, `RoadName`, `RoadList`, `Bearing`). The join has to rank on the
   road's identity — which route, mainline or ramp — and the polyline can't answer
   either. Typed like the network, `NaN` off the `"xd"` path, absent when the network
   doesn't carry them (so the toy fixtures are unchanged).
2. **`aadt.classify_aadt_records`** labels every record `mainline` / `ramp` /
   `connector` / `unknown` from `Descriptio`, then **rolls the labels up by
   `RouteID`**. The roll-up is what makes it work: a carriageway between two gores is
   legitimately described `EB ON COLE-OVERLAND IC`, and I-84's mainline record has
   three such rows — but ITD gives every ramp its own route-segment number, so a route
   whose records are *mostly* mainline is a mainline route and its ramp-worded rows
   are relabelled. Verified against all 8,301 statewide rows: 67 `RouteID`s carry mixed
   labels, and the majority rule reads each one the way a human does — I-90's
   `DIVISION ST EB OFF RAMP → DIVISION ST IC #51` (33 mainline rows, 1 ramp-worded)
   becomes mainline; US-95's Weiser spur (1 street + 1 genuine ramp, no majority) keeps
   both labels. `RAMP` is matched **singular only** — the plural names the interchange
   a *mainline* record runs to, on 259 rows.
   `load_aadt` also parses `RouteID` into `route_class` / `route_number` and
   repopulates `Route` from it (the ROADMAP's "either populate it or stop advertising
   it"), and classifies on the way out — including for a pre-Item-34 GeoParquet cache,
   so an old cache can't reinstate nearest-wins.
3. **`join_aadt` ranks instead of taking the nearest**: route number, then
   `mainline` > `unknown` > `connector` > `ramp` (`prefer_mainline=True`), then
   distance to 0.1 m, then how much of the segment the record runs alongside.
   `max_distance_m` 35 → **60 m**, which is only safe *because* ramps stop
   out-competing the mainline — the two changes are one item for that reason. The
   resolved policy and the source counts land on `attrs['aadt_join']`.
4. **`aadt_source` gains `matched_ramp`**, so a segment weighted by a ramp count is a
   finding rather than an indistinguishable `matched`, and the chosen record names
   itself through new `aadt_record_kind` / `aadt_desc` / `RouteID` columns (added to
   `store._AADT_JOIN_COLS`, so they cache with the geo layer).

**Two design decisions the first cut got wrong**, both caught by re-joining all 3,905
D3 segments and reading every value that moved *down*:

- **A route-number mismatch must not be a penalty, only a missing bonus.** Ranking a
  mismatch below an unknown sent SH-55 at New Meadows — where US-95 carries it, a
  concurrency the XD `RoadNumber` doesn't list — to a 130-vehicle substation road.
  Binary (match / everything else) fixes it and keeps every divided-highway case.
- **Distance alone can't break a tie, because several records lie *on* a segment at
  0.0 m.** A rest-area record (`SNAKE RIVER VIEW EB RA`, 560) and the I-84 mainline
  (`OREGON STATE LINE`, 26,500) were both 0.0 m from the same segment, and the winner
  was whichever the STRtree happened to return first. Ranking on the share of the
  segment a record runs alongside (`_alongside_fraction`, 11 sample points) settles
  those, and also decides which of two abutting mainline records owns a segment that
  straddles their milepost break — previously arbitrary.

**Verification.** I-84 WB recovers the monotone mainline profile and a length-weighted
**123,692** (from 61,410), against EB's 124,203 — **0.4%** apart, where the ROADMAP
asked for "within a few percent". Across all 3,905 D3 segments: 482 AADT values move,
112 of them from under 20k to over 50k; **no segment regresses** from `matched` to
`nearest` and 14 recover; 37 land on `matched_ramp`, nearly all unnamed FRC 3–4 XD
stubs at interchanges. Every remaining downward move was read individually and is a
correction — the largest, Franklin Rd at I-84 IC #29 dropping 49,000 → 19,000, was the
*interstate's* volume on a city arterial that had squeaked through the bearing gate at
43.8°. D3 peak vehicle-hours of delay rise **+50.9%** (52,660 → 79,444), which is the
number Item 35's rankings depend on. The one AADT-weighted pair this repo has shipped
(Myrtle, Session 19 — plain 20.1 mph vs weighted 23.8, ~617 vehicle-hours/day) is
**unchanged**: Myrtle is an undivided downtown couplet with the centerlines on the
segments, so nothing there was ever being out-competed.

`vehicle_hours_of_delay` **flags rather than excludes** ramp-weighted rows (the
ROADMAP left the choice open): it carries `aadt_source` through and counts them on
`attrs['aadt_ramp_rows']`. Excluding would silently zero a real ramp's impact, and
this function can't tell a ramp segment from a mis-joined mainline one — the caller
can. Recorded in the docstring.

**13 new tests** (11 `test_aadt.py` + 1 `test_geometry.py` + the real-layer pair):
`RouteID` parsing and labels, the description/roll-up classifier including the
no-majority case and the plural-`RAMPS` trap, the mainline-beats-a-nearer-ramp
fixture and its `prefer_mainline=False` escape hatch, a ramp *segment* still taking
its ramp record as `matched_ramp`, route-number-beats-frontage-road (and an unnumbered
segment still deciding on distance), the coverage tie-break, `attrs['aadt_join']`,
and ramp rows surviving `vehicle_hours_of_delay`. The two real-layer tests self-skip
without the licensed fixtures: one rejoins both I-84 carriageways from
`USA_Idaho_shapefile.zip` + `Cumulative_AADT.zip` and asserts the recovered profile
and the <5% directional agreement, the other asserts `Route` is derived (not read) and
that the Eagle Rd ramps classify as ramps while the mainline record does not. 388
pass, 2 skip.

**DATA_FORMAT.md** gained the divided-highway failure mode beside the existing AADT
caveat — the I-84 numbers as the worked example, the `RouteID` grammar, the dead
`Route`/`Segment` columns, `Descriptio` truncation, what `matched_ramp` means, and the
note that rural divided sections can exceed the 60 m default (12 D3 segments do, and
correctly come back `nearest`).

---

## Session 40 — District-wide screening: which corridors are worst? (ROADMAP Item 35) (2026-09-17)

Everything in this repo was **corridor-scoped**: name two endpoints, get that corridor.
This item adds the prior question — *which* corridors in a district are worst — as a
new pure-core module (`screen.py`) over the DuckDB store, plus the streaming ingest
that makes a district-sized export usable at all.

**What a district export actually is.** The 2026 D3 download is **91,054,384 rows**
across 3,905 segments and 244 days at 15-minute bins: 3.1 GB of CSV inside each 263 MB
zip, and the three parts are split **by segment, not by date** (part 1 carries 1,947
segments over the whole date range). `io.load_data` would hold all of it in memory
before a single row was stored.

**`store.ingest_export_streaming`** decompresses the zip member with
`zipfile.ZipFile.open()` and feeds DuckDB's `read_csv` through a **named pipe** — no
`subprocess`/`unzip -p`/`/dev/fd/N`, which is what the outside script used. Three
things the scope did not anticipate, each found by running it:

1. **Lazy is not bounded.** DuckDB buffers a statement's appended rows until it
   commits, so one `CREATE TABLE AS SELECT` over a part grows to roughly the size of
   the CSV — measured 1.8 GiB resident for 1.5 GB of input, and the OS killed the
   full-part run twice. The member is therefore cut into line-aligned **256 MiB chunks
   (`chunk_bytes=`), each staged and merged in its own committed statement**. Peak
   memory then tracks the chunk, not the export. `preserve_insertion_order` was tried
   first and is *not* the fix (it delays the blow-up, it doesn't bound it); the scan
   stays single-threaded (`parallel=false` — a pipe cannot be split), which is also
   what keeps the stored row order identical to the pandas path's.
2. **Column types have to be derived, not declared.** Nothing may be sniffed (a pipe
   cannot be rewound), so every column is read `VARCHAR` and cast in SQL by
   `io.load_data`'s own rules — `TRY_CAST` for `pd.to_numeric(errors='coerce')`,
   `nullstr=''` for the empty-field NaN, `coalesce(... = 'T', FALSE)` for
   `Road Closure`. A numeric column is then narrowed to `BIGINT` **only when it holds
   no nulls and no fractions**, which is `pd.to_numeric`'s own int64-vs-float64 rule.
   That is what makes the real Myrtle export come back with `Ref Speed` as `BIGINT`
   and `Speed` as `DOUBLE` through *both* paths.
3. **A latent truncation bug on both paths.** A column's type was fixed by the first
   export that wrote it and DuckDB casts on insert — so an export whose `Speed` read
   `30, 31` typed the column `BIGINT` and silently truncated a later export's `30.5`
   to `31`. `_widen_columns` now widens an integer column to `DOUBLE` when the
   incoming rows are fractional, on the pandas merge as well as the streaming one.

**Verified against the file loader, not just against itself:** the 2,185,368-row
Myrtle export ingests **byte-identical** through both paths — same values, same column
types, same row order, same `_areas` / metadata rows — and the streamed table also
equals `io.load_data` directly. Forcing a pathological `chunk_bytes=64` (one row per
chunk) changes nothing, which is what the bin-detection retry exists for: the cadence
is read from the first staged chunk, and a chunk too small to hold two timestamps of
one segment keeps appending before deciding. The real D3 export lands in **262 s at
2.5 GB peak RSS**, into a 2.1 GB store.

**`screen.segment_screen`** reduces that area to **one row per segment** entirely in
DuckDB: per-window mean travel time and speed, `ref_speed`, and the CValue accounting.
All 91 M rows screen to 3,905 rows in **11.9 s** (9.9–10.5 s ungated).

- **Windows are the package's vocabulary, not SQL.** A `PeakWindow` carries a
  `parse_time_bin` range string and `parse_day_of_week` day specs, and its SQL
  predicate is **derived from that parse** — half-open bins, overnight wrap, the
  all-seven-days no-op, and `isodow` 1=Mon against pandas' 0=Mon. `PeakWindow.filter()`
  is the same window as a pandas filter, and the tests assert the two agree row for
  row on the toy export *and* on the real Myrtle one. Presets: `am` 07:00–09:00 and
  `pm` 16:00–18:30 (weekday-gated, `peak=True`), `midday` 10:00–14:00 (weekday-gated,
  context), `night` 22:00–05:00 (every day — the point of it is the quietest hours of
  *any* day).
- **The CValue gate is explicit, recorded, and measured.** `CValue > 80` by default
  (`io.DEFAULT_CVALUE_THRESHOLD`, the same strict comparison `io.filter_cvalue` uses),
  on `attrs['cvalue_threshold']`, with the **surviving-row share carried per segment
  and per window**. On the D3 export **39.4%** of rows carry a null CValue and the gate
  drops **40.1%** of all rows; **990 of 3,905** segments lose more than half their AM
  rows and **84 lose all of them**. (The ROADMAP's scoping figure was 37.8% / 987 / 84,
  from the outside review — the AM counts reproduce exactly, the row share is a point
  and a half higher against the full export.)
- **The real-data anchor reproduces exactly:** 173 weekdays × 8 bins = **1,384** AM
  observations per segment, for **3,902 of 3,905** segments (three carry 872).

**`screen.rank_corridors`** turns a screen plus assembled `ChainResult`s into
`delay_min` / `tti` / `delay_per_mile` / `vhd` / `vhd_per_mile` / `worst_peak`, through
`speed.segment_delay` and `aadt.vehicle_hours_of_delay` — so the free-flow resolution,
the floor, and the AADT daily-total caveat are the package's, not restated inline.
Three definitions the outside pass left open are pinned down and recorded on `attrs`:
delay is floored **once, per segment**, so `delay_min` and `vhd` cannot disagree about
a corridor the way the outside run's two headline numbers did; only **observed**
pavement is credited (a member the screen never saw lands in `missing_miles` rather
than deflating `delay_per_mile` — Item 31's concern at screening level); and end
segments are prorated by the chain's own extent fractions.

**Cross-validated against the outside run.** Ten of the outside catalogue's twelve
corridors resolve through `corridors.chain_between_segments`, all fully observed. On
I-84 westbound, the corridor that run ranked first, the **ungated** screen reproduces
its headline delay to seven significant figures — **17.247493 min** against its
17.2474927894, TTI 2.2991 against 2.2991, over an identical 14.869313 mi — while the
gated number is 17.259148 (+0.07%) and `vhd` is **33,350 against its 33,672.7**,
because the volume weights are Item 34's and no longer the ramp-contaminated ones.
Across the ten corridors the CValue gate moves delay by **≤0.33% on the eight urban
ones** and by 0.60% / 1.58% on the two rural SH-55 North extents — the places
DATA_FORMAT already records as heavily backfilled. That is the argument for the gate
being an explicit *passing* gate rather than an absent one: it changes almost nothing,
visibly, rather than nothing, silently.

**Two corridors would not resolve, and are recorded rather than bridged:** SH-44
(State St) **WB stops `dead_end` after 11 segments** and **EB stops `off_network`
after 31**. That is the finding ROADMAP Item 36 is scoped to handle — the outside
pass's geographic-sort fallback is what let those same extents interleave N Glenwood
St through State St in the first place.

**25 new tests** (19 `test_screen.py` + 6 `test_store.py`), **413 pass, 2 skip**. The
screen tests cover the preset definitions, the SQL-predicate derivation (overnight
wrap, `isodow` mapping, the seven-day no-op), the SQL-vs-pandas agreement per segment
per window, hand-computed bin counts, the gate's effect and its recorded threshold,
the refusal to apply a gate an export cannot support, the ranking metrics against a
corridor with known delay, the segment-level floor, missing-member accounting, end
proration, the ramp flag and the AADT caveat. The store tests cover pandas/streaming
parity (including on the real Myrtle export), keep-first merging, idempotence,
part-split exports, the numeric-typing rule, chunked-vs-one-shot equality, and the
widening fix. `duckdb>=0.10` is now declared in `pyproject.toml` and
`requirements.txt`; DATA_FORMAT.md gained the streaming-ingest section and a screening
section beside the CValue one.

**Not done here (deliberately):** no GUI (the ROADMAP scopes a display pass
separately), and no corridor catalogue — Item 36 owns that, and now has its mechanism.

---

## Session 41 — The D3 corridor catalogue, as endpoint pairs (ROADMAP Item 36) (2026-09-17)

Item 35 gave the district screen its mechanism; this item gives it something to screen.
The outside pass's contribution here was **local knowledge** — which corridors in
District 3 are worth ranking and which interchange to which interchange each one runs
between — and its mechanism was the part to throw away: `RoadNumber` plus a lat/lon
bounding box, a `NextXDSegI` walk, and a fall back to **sorting the box's segments by
latitude or longitude** whenever the walk covered less than 70% of it. That fallback is
what put the Garrity Blvd frontage road in series with I-84 and N Glenwood St in series
with State St.

**The catalogue is data.** `scripts/d3_corridors.json` carries **20 entries**, each
`{id, name, start_latlon, end_latlon, description}` in travel order, beside the existing
`scripts/d3_place_name_routes.json`. The descriptions are the point: they carry *why*
each extent is the meaningful one (I-84 IC 35 → IC 49 because west of Nampa the AM
demand has not accumulated and east of the Connector the through volume splits; Eagle Rd
ends at State St because north of it the road leaves the commercial frontage that
generates the congestion; SH-45 is carried as a rural control that should rank at zero).
The outside catalogue's own text was never committed to this repo and is not recoverable
from it, so the extents were **re-derived against the XD network** — every junction
coordinate is a measured nearest point between the corridor and its cross street, not a
recalled one — and the file says so in its `_note`. 20 rather than the outside pass's 15
because directional pairs are separate entries and two corridors are split (below).

**`corridors.load_catalogue` / `parse_catalogue` / `resolve_catalogue`** are the code
half, in the existing pure-core module — **no new walker**. Validation is loud on
everything that would otherwise resolve somewhere else: unique non-empty ids, endpoints
in range (a `(lon, lat)` swap is out of range and raises), a **non-empty description**
(an extent with no stated reason is a bounding box with better manners), and unknown
keys rejected — a silently ignored `start_latlong` is a corridor resolved at whatever
the other endpoint's default happened to be. Keys beginning `_` are ignored so the file
can carry its own provenance. `resolve_catalogue` projects the network once, runs every
entry through `build_chain`, and returns one row per entry — walk result, miles, trims,
snap distances, terminal segments, and (given an export) the `chain_coverage` columns —
with `attrs['chains']` keyed by id, ready to hand straight to `screen.rank_corridors`.

**Acceptance is per entry, explicit, and allowed to fail:** `reached_target` **and**
`miles_covered_fraction > 0.95`, else the id lands in `attrs['findings']` carrying its
`stop_reason`. Nothing raises and nothing falls back.

**The resolution table** (20 entries in 5.6 s against the District 3 network subset — the 16,105 segments of the ten D3 counties, read out of `USA_Idaho_shapefile.zip`; coverage from the
three 2026 D3 zips, counted per segment straight out of the members in 35 s —
**91,054,384 rows, 3,905 segments, 59.9% surviving `CValue > 80`**, all three figures
reproducing Session 40 exactly):

| entry | reached | stop_reason | segs | chain mi | covered | note |
|---|---|---|---|---|---|---|
| `i84-eb` | ✔ | target | 27 | 15.12 | 1.000 | mainline only — `RoadName` is `('I-84 E',)` and nothing else |
| `i84-wb` | ✔ | target | 29 | 15.89 | 1.000 | |
| `i184-eb` | ✘ | dead_end | 1 | 0.25 | — | 7 of 20 I-184 segments have a null `NextXDSegI` |
| `i184-wb` | ✘ | dead_end | 8 | 2.57 | 0.968 | flips onto I-184 **E** at 1.0 mi |
| `myrtle-eb` | ✔ | target | 9 | 1.20 | 1.000 | |
| `front-wb` | ✔ | target | 12 | 1.47 | **0.731** | three unnumbered `W Front St` members absent from the export |
| `sh55-eagle-nb` | ✔ | target | 16 | 6.64 | 1.000 | |
| `sh55-eagle-sb` | ✔ | target | 15 | 6.64 | 1.000 | |
| `sh69-nb` | ✔ | target | 20 | 8.39 | 1.000 | |
| `sh69-sb` | ✔ | target | 20 | 8.39 | 1.000 | |
| `us2026-chinden-eb` | ✔ | target | 19 | 10.02 | 1.000 | |
| `us2026-chinden-wb` | ✔ | target | 19 | 9.71 | 1.000 | |
| `sh44-eb-star-eagle` | ✘ | dead_end | — | — | — | forks onto a parallel 1-lane State St at Ballantyne Rd |
| `sh44-eb-eagle-boise` | ✔ | target | 11 | 4.11 | 1.000 | |
| `sh44-wb-boise-eagle` | ✔ | target | 11 | 4.11 | 1.000 | |
| `sh44-wb-eagle-star` | ✘ | dead_end | — | — | — | link ends at Ballantyne Rd, 1.50 mi in |
| `sh16-nb` | ✘ | dead_end | 2 | 1.02 | 1.000 | stops 1.02 mi in at W Broken Arrow Ln |
| `sh16-sb` | ✘ | dead_end | — | — | — | southbound walk stops 11.87 mi in at N Pollard Ln |
| `sh45-nb` | ✔ | target | 40 | 17.43 | 1.000 | |
| `sh45-sb` | ✔ | target | 40 | 17.43 | 1.000 | |

(A `—` in the counts marks the three rejected entries whose fallback chain is not the
corridor — see the note on coincident carriageways below; the numbers quoted for them in
the notes column come from the walk run from the directionally correct terminal segment.)

**13 of 20 accepted, 7 findings** — and the findings are the substance of the session,
because each one is a fact about the network file that a geographic sort would have
hidden behind a plausible-looking corridor:

- **The XD topology is incomplete, and asymmetrically so.** Statewide, **38.3%** of
  segments carry a null `NextXDSegI`, tracking road class almost exactly (FRC 1 0.9%,
  FRC 4 28.9%, FRC 5 88.9%); District 3's *numbered* routes are 2.8%. And the gaps are
  **two-sided**: of the 15,997 null-`NextXDSegI` segments statewide, **zero** are named
  by any other segment as its `PreviousXD`, so a walker that fell back to reversed
  upstream links would recover nothing. That is the measurement that settled whether to
  extend the walker — it would not have helped, so it was not extended.
- **I-184 cannot be walked at all** (35% null links), which is not a small corridor: the
  Connector carries the whole west-side commute into downtown Boise. Recorded, twice.
- **SH-44 hides a parallel-facility trap in the topology itself.** Eastbound at State St
  & Ballantyne Rd, `NextXDSegI` points not at the SH-44 mainline (`XDGroup` 114772,
  2.2 lanes) but at a **different 1-lane W State St** a quarter-mile north (`XDGroup`
  3938033) that runs east past Eagle and dead-ends at S Edgewood Ln — same name, same
  FRC, different road. Westbound, the link at Eagle Rd simply ends, and the westbound
  chain from Eagle stops at the same Ballantyne intersection 1.50 mi in. State St west
  of Ballantyne is therefore unreachable from the east in either direction. The
  catalogue **splits the corridor at Eagle Rd** rather than bridging it: the two Eagle–
  Boise halves resolve cleanly (4.11 mi each), the two western halves are findings.
  This is the same corridor the outside pass "completed" by geographic sort, and Session
  40 could not resolve either.
- **SH-16 breaks mid-block, in different places by direction** — northbound 1.02 mi
  north of Star at W Broken Arrow Ln, southbound 11.87 mi south of Emmett at N Pollard
  Ln. No meaningful junction to split at, so both are single entries recorded as
  findings rather than entries defined by where a data gap happens to be.
- **`front-wb` reaches its target and is still rejected**, on coverage: the three
  `RoadNumber`-null `W Front St` members west of 15th St carry **no rows at all** in the
  D3 export, which was queried from the numbered-route lists — 73.1% of the couplet's
  mileage observed. Requested is not observed (Item 31's concern), at catalogue level.

**One behaviour worth knowing before reading a finding's member list.** Undivided
arterials have *coincident* carriageways in XD — the E and W segments share one geometry
— so a snap cannot prefer a direction; `build_chain` resolves it by preferring the
candidate pair that reaches the target, which works for every entry that resolves. For
one that does not, the documented nearest-snap fallback may report the opposing
direction (`sh44-eb-star-eagle` comes back walking west to Middleton, `sh16-sb`
northbound onto SH-52 through the Emmett concurrency). The `stop_reason` is the finding;
the member list of a rejected entry is not a corridor and `accepted=False` says so. The
diagnostic walks quoted above were run from the directionally correct terminal segment.

**19 new tests** (`tests/test_corridors.py`), **432 pass, 2 skip**: schema validation
including the `(lon, lat)` swap, the duplicate id, the empty description and the unknown
key; per-entry accounting and the `attrs` contract; coverage deciding acceptance at a
caller-set threshold; the **parallel-facility trap** — a synthetic mainline whose link
leaves onto a frontage road, asserting the unreachable mainline segments are *not*
bridged in behind it (3.0 chain miles, not the 5.0 a sort would give); and two
self-skipping real-network tests, one asserting I-84 EB resolves as `('I-84 E',)` and
nothing else, one asserting the State St break is recorded as a finding while the Eagle–
Boise half resolves. One more test hands `attrs['chains']` straight to
`screen.rank_corridors` and checks the metrics come back, so the claim that the
catalogue's output is the screen's input is asserted rather than asserted-in-prose. DATA_FORMAT.md gained a topology subsection (the null-link rates,
the two-sidedness, the three traps) and a note on what an export's segment set is.

**Not done here (deliberately):** no D3 ranking run. The interface is tested, but
producing the district's actual rankings needs the 2.1 GB store ingested and belongs
with the display work, not with the catalogue.

---

## Session 42 — KML: stop the palette collision (ROADMAP Item 37) (2026-09-17)

Closes the Items 34–37 batch. The bug: `_segment_colors` assigned categorical colours
with `names[k % len(names)]` over an 8-entry `_CATEGORICAL_PALETTE`, so a color-by
column with more than 8 distinct values wrapped silently — corridor 9 got corridor 1's
colour, corridor 10 got corridor 2's — and `_add_legend` printed a legend row per
distinct *value* with no check that two rows shared a swatch, so the legend actively
claimed a distinction the map didn't draw. The screening output (Item 36's 20-entry
catalogue) is exactly a >8-category case: SH-69 N/S landed on the same colours as
I-84 E/W. All three scope bullets are mechanical, confined to `kml.py`.

**Palette: 8 → 12 hues, raise instead of wrap.** Added `yellow` (already a named colour,
unused in the old palette), `cyan`, `pink` (hot pink `(255,105,180)`, not pale — pale
pink washes out over pavement/soil in satellite basemaps), and `lime` (bright
`(50,205,50)`, chosen for luminance contrast against the existing dark `green`
`(0,128,0)` rather than hue alone). `_segment_colors` now raises `ValueError` naming the
category count and the palette size whenever a `color_by` column has more distinct
values than the *default* `_CATEGORICAL_PALETTE` — but only on the default path. An
explicit `palette=` (list or `{value: colour}` dict) is checked first and, if given,
bypasses the guard entirely: a caller who explicitly wants 3 colours over 10 categories
still gets that, unchanged from before. **Decision, and why:** raising is correct for
the *default* because a wrapped default is a map that lies (the legend asserts 10
distinguishable categories that don't exist); it would be wrong for an explicit palette
because that caller has already made an informed trade-off the module has no business
overriding. `gray` (`(128,128,128)`) stays in the palette despite being the hue most
likely to blend with pavement on satellite imagery — it predates this item, nothing in
the scope or the screening output flagged it as the collision source, and swapping it
risked an unrelated behaviour change for a Sonnet-eligible, "small and mechanical" item;
noted here as a candidate if it turns out to be a real legibility problem in practice.

**`folder_by=`.** New keyword-only parameter on `geometry_to_kml`: one KML `<Folder>`
per distinct value of the named column (first-seen order), holding just that group's
`<Placemark>`s; a missing/NaN value groups under `"(none)"` rather than raising or
silently dropping the row. `folder_by=None` (the default) is unchanged — the flat
placemark list stays directly under `<Document>` — so every existing caller (`gui/
app.py`'s `_write_kml`, which doesn't pass it) is untouched. An unknown column name
raises before any XML is built, matching the module's existing `ValueError`-on-bad-input
style (e.g. the `SEGMENT_COL` check). This is what makes the 252-placemark screening
output toggleable per corridor in Google Earth instead of an all-or-nothing layer.

**`name_col` defaults to a friendly name.** When `name_col` is left `None`,
`geometry_to_kml` now calls `names.apply_names(geo)` — passing the whole `geo` frame in
as the "metadata" argument — and uses the resulting per-segment mapping instead of
`str(Segment ID)`. This works because `apply_names`/`seed_names` were already written
defensively: they read `Road`/`Direction`/`Intersection`/`Combined` only when present
(`if "Road" in metadata.columns else None`, etc.) and fall through per-segment —
simplified name → raw `Combined` label → `str(Segment ID)` — so calling it on a frame
that has *none* of those columns degrades to exactly the old default (segment id), and a
frame that has `Combined` but not `Road`/`Intersection` (i.e. nothing to simplify) uses
the raw `Combined` string. No new coupling to the DB or an overrides table was added —
`apply_names`'s second argument (`names=`, the store's override frame) is left `None`,
so this is the seed/fallback chain only, not a live store lookup; a caller that wants
the DB-persisted overrides still has to pass `name_col` explicitly (as the GUI already
does with `"Combined"`) or resolve `names.apply_names(geo, overrides)` itself and pass
the resulting column in. Explicit `name_col` (including the GUI's only call site)
bypasses this entirely, so no existing caller's output changed.

**One consequence not in the original scope bullets:** the existing
`test_default_name_is_segment_id` test's toy fixture (`_toy_geo()`) carries a `Combined`
column, so under the new default it no longer produces segment ids — it now correctly
produces the friendly names, which is the intended behaviour change. Renamed to
`test_default_name_col_uses_friendly_name_when_available` and reassigned its expected
value; added a second fixture (`_toy_geo_no_names()`, with no naming columns at all) and
a new test asserting the true id-fallback path still works when there's truly nothing to
name from. Recorded here since a reviewer skimming the diff might otherwise read the
renamed assertion as an unexplained test change rather than the point of the item.

**Verification against Item 36's real >8-category case was not run as a KML export** —
`scripts/d3_corridors.json`'s 20 entries would need `corridors.resolve_catalogue`
against the XD shapefile plus a `geometry_to_kml` call to produce an actual `.kml`
file, which is generated output (gitignored, not committed) and not something this
Sonnet-eligible, `kml.py`-only item needed to run end-to-end to prove the fix — the
`_toy_geo_n(13)` synthetic fixture exercises the identical code path
(`_segment_colors` categorical branch) that a 20-corridor `color_by="name"` call would
hit, deterministically and without a multi-GB ingest.

**Tests:** `tests/test_kml.py`, +8 (21 → 29 passed in the file): palette overflow raises
at 13 categories against the 12-hue default and does not at exactly 12; an explicit
2-colour `palette=` over 4 categories still cycles; the legend carries 12 distinct
swatch colours at the palette boundary (no duplicates); `folder_by` groups correctly
(including the `None` → `"(none)"` case) and raises on an unknown column; the
friendly-name default and the true segment-id fallback are each covered by their own
fixture. **Full suite: 440 passed, 2 skipped** (up from 432/2 at the Item 36 checkpoint;
`.venv/bin/python -m pytest -q`, 329 s). DATA_FORMAT.md untouched — nothing here changed
a fact about the INRIX export or the XD network, only a KML rendering default.

The Items 34–37 batch (2026-09-17 district-wide screening review) is now complete.

---

## Session 43 — Chain membership accounting: requested vs observed, and a CValue gate (ROADMAP Item 31) (2026-09-17)

Opens the Items 31–33 batch (the Session 37 re-review). Item 28 killed the outside
pass's `eval_df = complete if len(complete) > 0 else merged` fallback, but a corridor sum
could still shorten silently by a different route, and `corridors.py`'s own docstring
promised it could not.

**The hole: the complete-set size was read out of the data.** `chain_travel_time` passed
`expected="total"`, which `mark_complete_timestamps` resolves to `nunique()` over the
segments *present in the frame*. A requested member with **zero** rows therefore never
enters the count and cannot fail it — Eagle Rd NB ran with `expected_segments = 17`
against a 20-member request, and every bin was stamped complete while summing 17/20 of
the route. The fix is to take the size from `members=` (which `chain_travel_time` now
always passes, from `chain.segment_ids`) rather than from the rows.

**Decided: a third state, not silent completion and not total collapse.** The scope note
asked for this decision explicitly, since dropping all 2,633 bins of a 17/20 route is
correct-but-useless. Three cases are now separated and all three reported:

- sometimes missing → the timestamp drops (`complete=False`) — the original rule, intact;
- never present → the sum is reported and **labelled** (`short=True`, `n_absent=3`);
- whole set present → `complete=True, short=False`, the only combination meaning the sum
  covers what was asked for.

Every row carries `n_requested` / `n_absent` / `expected_segments` / `short` beside
`n_segments`, and `on_absent="drop"` takes the strict branch for a caller who wants it.
Absence is judged **value-aware** (a member with rows but no values in the summed column
is absent for summing) — consistent with how `n_segments` was already counted, and it
incidentally fixes a latent case where an all-NaN member would have collapsed the series.
Without `members=` the observed-membership behaviour is byte-for-byte unchanged; the
accounting columns are still filled in (`n_requested == expected_segments`, `n_absent
== 0`) so the output schema doesn't depend on the call site. The GUI's
`network_travel_time(..., members=picked)` path is unaffected in value: for a user-picked
set, achievable-count and observed-count coincide.

**The length correction.** `Length(Miles)` was `chain.requested_miles` unconditionally,
so an unobserved **end** segment had its trimmed mileage prorated into a sum it
contributed nothing to, and the reported length still credited its pavement. It is now
the extent **actually summed**, with `requested_length_miles`, `missing_length_miles`
and a `length_basis` label (`"requested"` / `"observed"`) beside it — the scope offered
"suppress the columns or report the observed extent and label it", and labelling keeps
the route comparable instead of blanking it. Measured on the 2026 D3 export, 6 of 15
validation chains are short: Eagle Rd NB 0.149 mi of 6.938 (speed overstated 2.1%),
Eagle Rd SB 0.119 of 6.939 (1.7%), Franklin EB/WB 0.128 of 2.763 (4.6%), and the two
No-Mid extents 0.128 of 2.100 (6.1%).

**A units correction to the review's own figures.** Session 37 quoted 0.290 mi missing on
Eagle Rd NB and 0.250 mi on Franklin EB. Those are the absent members' **whole-segment**
lengths (confirmed here: 0.2896 and 0.2500). A prorated sum is only credited the
**in-extent** miles, which for a trimmed end segment is about half — hence the smaller
figures above. The review's percentages (4.2%, 9.0%) are correspondingly about double
the real overstatement.

**The CValue gate, and what it actually does.** `chain_travel_time` grew
`cvalue_threshold` (opt-in in the core, `CValue > 80` by default in
`build_validation_report.py`, recorded on `attrs` and on the summary), matching the
convention Item 35 set in `screen.py`. `io.mark_imputed` marks the null-CValue rows
(the imputation marker) and, separately, the `Speed == Hist Av Speed` signature, so the
share is **measured whether or not the gate is applied** — a study-wide "we gated" says
very little when the share is this route- and hour-dependent. The per-bin
`imputed_fraction` / `cvalue_kept_fraction` travel through `agreement.match_bins`
(new `carry=` parameter) into `compare`, which reports them per route beside the effect
size; the scorecard gained an "Imputed" and a "Members absent" column. `chain_coverage`
takes the same threshold so the coverage panel and the sum can't answer the same
question differently.

**Measured: the gate never changes a retained bin's value.** Across all 15 chains and
~270k complete-set bins, the gated sum equals the ungated sum on every bin the gate
retains — mean difference 0.0000 min, max absolute difference 0.0000 min. That is
structural rather than lucky: a complete-set bin requires every member, so a bin losing
any member to the gate fails the rule and drops out whole. The gate is a **selection on
bins**, never a correction to a sum — conditional on no member being gated away
entirely, which held on all 15 chains (`n_absent` unchanged by the gate). What it costs
is bins, and route-dependently: VSL 8.6–9.4%, Eagle Rd ~14.5%, Franklin 14.2–22.1%,
SH-69 19.9–24.3%, Cascade–HSB 46.8%, HSB–Cascade 45.8%, **Garden Valley–HSB 77.4%**. The
resulting shift in each route's mean corridor travel time is therefore pure composition
(the overnight backfill leaving): +0.03 to +0.34 min on the arterials, −1.23 min on
Cascade–HSB. Hourly shares were re-measured over the assembled chains and DATA_FORMAT's
numbers sharpened: Eagle Rd NB is **0.0%** imputed at every hour 07:00–13:00, Cascade–HSB
**63%** at 05:00 (the file said 57%) and 91% at 01:00.

**The end-to-end report re-run is outstanding, and why.** `scripts/build_validation_report.py`
needs `TT Logger.xlsx`, which is gitignored raw logged input (Session 35's decision) and
is **not on this machine** — only the derived `out/validation_report/tables/` from the
previous run and the seed `Eagle Rd TT Comparisons.xlsx`, which is the t-test workbook,
not the reference. So the INRIX side was re-run in full instead: each published chain was
rebuilt from `tables/chains.json` (the walk between its recorded terminal segments with
its recorded trims re-applied — member counts match the published `n_segments` for all
15) and put through the new code against the real 5,457,816-row export. Everything above
is measured that way. What that cannot produce is a moved `bias` / `delay_ratio`, because
those need the reference side; the numbers that move there are the length-derived speeds
on the six short chains and whatever the gate's bin selection does to the matched set.
**The report should be regenerated once the workbook is back on this machine** —
`--cvalue-threshold` / `--no-cvalue-gate` are wired for it.

**Tests:** +18 (`tests/test_corridors.py` +9, `tests/test_speed.py` +3,
`tests/test_agreement.py` +4, `tests/test_io.py` +2). A member that never appears holds
the expected count at the requested size and marks the chain short; `on_absent="drop"`
collapses the series; a *sometimes*-missing member still drops only its timestamp;
proration with an unobserved end segment reports the observed extent and the correct
speed (60 mph, where crediting the absent 0.75 mi would have said 90); an intact chain's
length is unmoved; the gate records its threshold and its cost, measures the imputed
share when ungated too, and raises on an export with no `CValue` column; the carried
columns survive the match and reach `compare`, and are **absent rather than NaN** when
there are none.

---

## Session 44 — The delay compression as a slope, and the level gap as its own number (ROADMAP Item 32) (2026-09-18)

Continues the Items 31–33 batch. Item 30 published the compression as a **delay
ratio** — `mean(delay_inrix) / mean(delay_ref)`, each above its own 10th percentile,
"median 0.59 (0.37–0.95)". Session 37's re-review found that statistic to be the least
stable thing in a report that attaches a day-blocked interval to everything else: it
divides two small means, it carried no interval at all, and **VSL SB PM read a ratio of
2.34 against a regression slope of 0.685 on the same bins** — a route where INRIX
compresses delay, reported as showing more than twice as much of it.

**The primary statistic is now a regression.** `agreement.delay_regression` fits INRIX
delay on reference delay per route, each source above its **own** free-flow percentile,
over every matched bin including the free-flow ones that anchor the intercept. It is
folded into `compare` through a shared `_delay_block`, so the whole pipeline — summary
CSV, scorecard, figures, route pages — gets the columns without a second code path, and
`delay_regression` stays callable on a matched frame by itself. Measured over the
arterials: **median slope 0.536** (range 0.250–0.875), Eagle Rd NB 0.520 [0.505, 0.536],
Eagle Rd SB 0.514, SH-69 SB 0.250, reproducing Session 37's independently-derived values
to ±0.015. Both rural routes' slope intervals **span parity** (Cascade→HSB 0.505
[−0.040, 1.050]), the same "no measurable disagreement" their bias CIs say.

**`delay_ratio` keeps its name, its definition and a column.** It is what Item 30's
numbers were published in, and redefining a statistic underneath a published name is how
a report stops being comparable to itself. It is demoted to a footnoted secondary column
and the report says why.

**The two effects `bias` fused are now separate columns.** A corridor can disagree
because the two sources sit at different **levels at free flow** and because INRIX
**compresses delay**, and those have opposite consequences: a level difference cancels in
a before/after difference and a slope does not. `free_flow_gap` is reported beside the
slope, each with its own interval, and the scorecard opens with both — `Delay slope` /
`Slope r²` / `Slope 95% CI` / `Free-flow gap` / `Gap 95% CI` — before `Bias`, which is now
captioned "the two above summed". Sorted by slope, not by bias. The index page leads with
both as KPIs and draws a `slope_forest` and a `level_gap_forest` **above** the bias
forest; each route page carries the same two KPIs above its bias KPI.

**Two interval constructions, for two different estimands.** The slope and intercept get
a **day-clustered sandwich** (CR1, *t* on G − 1 days) — the regression analogue of the
bias CI's t-interval over daily means, and blocked for the same reason: bins inside a day
are not independent. The per-bin interval is kept as `*_naive` and
`delay_slope_ci_width_ratio` shows the cost: **median 2.0×, max 3.2×** on these
corridors. The **free-flow level gap is a difference of two quantiles**, which has no
closed-form clustered SE, so its interval is a **day-block bootstrap** — resample whole
days, recompute both pooled percentiles, 1000 draws, fixed seed so a re-run reproduces
its own numbers. A t-interval over **per-day** gaps was written first and **rejected on
measurement**: it estimates the mean of daily gaps rather than the pooled gap that is
reported, and on the real arterials the two differ enough to put Eagle Rd NB's published
−5.36 *outside its own interval* ([−5.96, −5.71]) — which is not a thing a report may
print.

**The near-zero intercept is a finding, and the session established why it is not an
artifact.** Because each source's delay is measured above its *own* free flow, a constant
added to **every** bin is absorbed into that source's free-flow percentile and cannot
appear as an intercept. An intercept therefore only arises from an offset applied to
congested bins and not free-flow ones — so measuring ≈0 (median +0.084 min; +0.018 to
+0.158 across the eight arterials whose fit clears r² 0.5) means there is **no fixed
penalty**, and the entire disagreement rides on the slope. That is the worst case for
this project's primary use: an intervention removing **4 real minutes** of delay scores
as roughly **2.1 minutes** on INRIX. Both directions are pinned by tests — a pure
compression must return an intercept of exactly zero, and a fixed congested-bin penalty
must be visible as a positive one.

**A prose guard.** The finding paragraph interpolates its numbers from the run's frames,
so "an intervention that removes 4 real minutes scores as 2.1" is *computed* from this
run's median slope rather than asserted. The intercept sentence reads its range off the
fits that can carry it (r² > 0.5) and names the count, because VSL SB PM's +0.614 at
r² 0.168 would otherwise make "near zero" false in the report's own table.

**One defect found and fixed while testing.** `_ols_day_blocked` guarded a degenerate fit
with `rss <= 0`, which floating point never quite reaches: an exact synthetic fit came
back with rss ~1e-30 and a CI of width ~1e-16 — a width-0 claim of certainty, the exact
thing `_t_interval` refuses. The guard is now relative (`rss > 1e-12 × max(tss, 1)`).

**The figure layer stays a shell, including the one figure with a fitted line.**
`delay_regression` (the figure) draws delay against delay with the line the **core**
fitted — it evaluates `intercept + slope · x` and calls no `polyfit`, which is precisely
what the retired `generate_corridor_html_reports.py:504-549` did inside its figure loops.
A test hands it a row whose slope the data denies and asserts the drawn line follows the
**row**. `scatter_1to1` keeps its place beside it in travel-time units, where the
free-flow level gap is the offset of the cloud at the bottom left. Wide tables gained a
scroll wrapper rather than being crushed by the new columns.

**The end-to-end report re-run is still outstanding, for Session 43's reason.**
`TT Logger.xlsx` is gitignored raw logged input and is not on this machine, so the
published slope table is computed over the **outside pass's 28,340-bin matched set**
(`out/inrix_vs_google_matched_timeseries.csv`) — the same source Session 37 used — while
the Item 30 bias/ratio table above it comes from the rebuild's 38,873 bins. The two are
**not bin-for-bin comparable**, and DATA_FORMAT says so in a provenance paragraph.
Regenerate both when the workbook is back.

**DATA_FORMAT.md** gained *The compression is a slope, not an offset (Item 32)* — the
per-route table (slope, day-blocked CI, intercept, level gap and its CI, r², ratio), the
interval constructions and why there are two, the rural contrast, the multiplicative
before/after consequence, and the provenance note. Item 30's "roughly half the effect
size in minutes" is kept and now points at the measured 0.536 instead of standing alone.

**Tests:** +16 (`tests/test_agreement.py` +9, `tests/test_validation_report.py` +7; one
existing report test rewritten from the ratio to the slope). **Full suite: 474 passed,
2 skipped.** The instability charge
against the ratio is **measured, not asserted**: the same low-delay route split in half
by date moves its ratio by a third while its slope moves by a hundredth, each half's
interval covering the other half's estimate.

---

## Session 45 — The directional level gap is on the reference side (ROADMAP Item 33) (2026-09-18)

Closes the Items 31–33 batch. The question two reviews had left open: at each
source's own 10th percentile, **SH-69 SB sits 4.69 min below the reference while
SH-69 NB over the same endpoints reversed sits 0.82**, and Eagle Rd NB sits 5.36.
Both reviews had confirmed the *endpoints* coincide (4–64 ft snaps) and stopped
there, which proves only where the routes start and finish. This session tested the
ground between them.

**The INRIX path is identical in both directions — measured four ways.** The SH-69
chains are member-for-member mirrors: every segment length on one appears on the
other, with one pair breaking at a different point (0.198 + 0.806 against 0.500 +
0.503, the same 1.003 miles), and both requested extents are **7.113 mi** (Eagle Rd:
6.938 against 6.939). Sampling 201 points along the NB chain, the **median distance
to the SB chain is 0 ft** and the maximum 56 ft — the XD network carries one
centerline for both directions over almost all of SH-69, splitting only at the
north end; Eagle Rd runs as parallel carriageways a median 43 ft apart. Terminal
vertices coincide **exactly** at SH-69's south end and at both ends of Eagle Rd, and
SH-69's two chains end 63 ft apart at the north end — the carriageway separation
there, which is the thread the mechanism below hangs on. And per-member travel time is
symmetric: summing each member's own 10th percentile over the logged window, **NB is
8.540 min against SB's 8.440** with **no slice of the corridor differing by more
than 0.04 min** — so the gap is neither spread as a length mismatch nor concentrated
in one member, which is the scope's own test for an XD extent that disagrees with
the roadway. Eagle Rd: 9.680 against 9.280.

**The answer was in a reference column nothing had ever read.** `load_tt_logger`
has parsed `Extra TT (Min)` since Item 29 and `align_to_bins` carried it, but
`match_bins` dropped it and no statistic used it. `Travel Time − Extra TT` is the
provider's **no-traffic duration** for the path *it* routed, and on this workbook it
is a constant per sheet — **1,936 of 1,936** matched SH-69 SB bins carry exactly
11.3167 minutes, 1,937 of 1,937 Eagle Rd NB bins carry 11.5383, over six months and
a 13-hour daily window. Over the identical 7.113 miles the two SH-69 sheets read
**11.3167 SB against 8.2667 NB** — 37.7 mph against 51.6 — a 3.05-minute directional
difference **in a number that contains no traffic at all**. The record's extremes say
the same: the fastest southbound reference trip in 1,936 samples was 11.317 min and
the fastest northbound 8.267, against INRIX's 8.00 and 8.12.

**So the level gap splits, and both parts are now columns.** `match_bins` gained
`carry_ref` (the reference-side analogue of Item 31's `carry`), and `_delay_block`
gained `ref_no_traffic`, `ref_no_traffic_spread`, `ref_free_flow_delay` and
`free_flow_gap_static`, with `free_flow_gap == free_flow_gap_static −
ref_free_flow_delay` by construction. The whole pipeline gets them through the same
shared block Item 32 built, so `compare`, `delay_regression`, the summary CSV, the
scorecard and the route pages all carry the split without a second code path.

**The two unexplained routes are two different things, and only one is about a road.**

- **Eagle Rd NB is mostly not a level gap.** 4.04 of its 5.36 minutes is delay the
  reference *itself* reports at its own 10th percentile: on a corridor congested
  through the whole logging window, the 10th percentile is not free flow. Item 32's
  phrase "the quietest conditions measured" is true of INRIX — on the two complete
  SH-69 chains its 10th percentile lands within **0.25 min** of the free-flow time
  its own `Ref Speed` column implies (9.130 against 9.005 NB, 8.950 against 8.716
  SB) — and false of the reference. The remaining −1.32 min (36.1 mph against INRIX's
  40.7) is an ordinary disagreement about what "open road" means on a signalised
  arterial, where the provider's estimate carries typical signal delay and
  `Ref Speed` does not.
- **SH-69 SB is a route difference on the reference side.** −2.37 of its −4.69
  survives removing the reference's own delay, against a **+0.86 northbound** over
  mirrored pavement. INRIX's two directions differ by 0.18 min; the reference's
  no-traffic durations differ by 3.05.

**The mechanism, stated as far as the evidence goes and no further.** The
southbound sheet's origin snaps **63.9 ft** from the southbound roadway while the
northbound sheet's destination snaps **1.5 ft** from the northbound one, and the two
carriageways are **63.2 ft** apart there — so a single coordinate 1.5 ft from the
northbound centerline sits 61.7–64.7 ft from the southbound one, and 63.9 is inside
that band. It is the **only** origin of the twelve coordinate-snapped routes that
lands on the opposing carriageway (next largest: VSL NB at 10.2 ft against a 20.6 ft
separation; Franklin WB - No Mid's 55.0 ft is a longitudinal offset on an undivided
stretch, not a carriageway side). A southbound trip from a point on the northbound
side must reverse direction first, and that endpoint sits ~90 ft south of the I-84
WB ramp terminal and ~400 ft south of the north ramp terminal. **What is not
established is the size of that detour** — a U-turn at the north terminal is ~800 ft
plus a signal, short of 3.05 minutes — and it cannot be, because the logger records
the provider's duration and not the route it returned. Recorded instead: the two
measurements that would settle it (re-log the southbound origin on the southbound
roadway; capture the returned distance).

**Ruled out and recorded, so they are not re-examined.** A directional sampling
artefact — the NB and SB sheets are logged in **exactly the same 1,936 bins**, hour
for hour and day for day, on both corridors. A chain-extent mismatch — requested
extents agree to 0.001 mi on all four routes, and every direction pair in the study
agrees to 0.004 mi or better (the outside pass's Franklin 2.993-vs-3.453 asymmetry is
whole end segments, which Item 28's trim already accounts for). A member-level INRIX
defect. A mid-record route change — `ref_no_traffic_spread` is 0.0000 on both SH-69
sheets.

**One caveat the same column exposed, and it constrains how the number may be
read.** The no-traffic duration is **per sheet, not per path**: VSL NB AM and VSL NB
PM resolve to the same chain and the same two endpoints yet carry 4.1833 and 5.5765
minutes. So it is the provider's open-road duration *for this sheet's route as
logged*, and comparing it across sheets that were not logged together says nothing.
The SH-69 comparison is not that case — both sheets log the same window over the
same six months — but DATA_FORMAT says so explicitly rather than leaving the reader
to assume it.

**The report says what it found, where it used to say it did not know.** The G2
panel ("Something route-specific may well be happening at that interchange. This
report does not claim to know what it is") now closes with the finding, every number
interpolated from the run's frames — and falls back to the old sentence verbatim
when the run carries no reference-side delay column, rather than asserting a finding
the data in hand cannot show. The scorecard carries `Ref delay at ff` and
`Static gap` beside the gap they decompose, the finding panel's level-gap paragraph
gains the split with its worst route named, and each route page's gap KPI states its
own decomposition. All of it is gated on the columns **having values**, not on the
column names existing: `compare` emits them filled with NaN when the reference
supplied no delay, and a column of dashes would advertise a decomposition the run
cannot make.

**What this does *not* touch.** The delay-compression slope. It is fitted on each
source's delay above its own free flow, so a directional difference in the
reference's baseline is absorbed into that baseline and cannot move it. The one
number the routing difference could still be distorting is SH-69 SB's slope of
0.250 — its reference delay includes delay from pavement INRIX was never asked
about — which is one more reason its r² (0.417, against 0.775 northbound) belongs
beside it.

**Tests:** +11 (`tests/test_agreement.py` +6, `tests/test_validation_report.py` +5).
The Item 33 defect is pinned in the smallest form that shows it: a fixture with **no**
level difference, a 0.5 compression and 4 minutes of reference delay in every bin
reports a −2.00 "level gap", and the split returns exactly 4.00 of reference delay
against a +2.00 static remainder. The converse is pinned too — a real −5.00 level gap
over a reference that does reach free flow comes back with 0.00 of its own delay and
the whole −5.00 static — because a split that could not tell those apart would
explain away a real finding. **Full suite: 485 passed, 2 skipped.**

**Still outstanding, for Session 43's reason.** `TT Logger.xlsx` is not on this
machine, so every number above is computed over the outside pass's 28,340-bin matched
set (`out/inrix_vs_google_matched_timeseries.csv`), the same source Items 32 and 33
used; the end-to-end report re-run stays pending the workbook.

---

## Session 46 — Scoping the topology repair and the screening runner (Items 38–39) (2026-09-18)

A scoping pass, not an implementation one. Two things were outstanding after Item 37:
the district screen has never been run end to end (Session 41 deferred it deliberately),
and 7 of the 20 D3 corridors do not resolve. The owner asked for both, and brought an
outside diagnosis of the 7 that proposed a hand-typed table of ~15 `NextXDSegI`
corrections. The diagnosis of *which* corridors break and *why* holds up. The mechanism
was re-measured before being scoped, and the measurement changed the answer.

**The proximity rule everyone reaches for first is unsafe, and I-184 is where it shows.**
Bridging a null link to the nearest segment within 25 m that shares `RoadNumber` and
bearing fixes SH-16 both directions and SH-44 westbound — and walks `i184-eb` into a
**196-segment, 110.6-mile** chain. At the Flying Y the I-184 EB mainline's end point sits
**8.1 m** from the on-ramp named `1A` (FRC 4, 1.0 lanes, `XDGroup` 5930102) and **4.7 m**
from the true mainline continuation `1187532906` (`XDGroup` 3750153). Road number and
bearing agree with both; distance prefers the ramp. That measurement is the reason the
scoped rule is not a proximity rule.

**`XDGroup` is the key that works** — XD's own carriageway grouping, which ramps and
parallel facilities do not share. Scoped to *same `XDGroup`, endpoint contact within
25 m*, over the 16,105-segment D3 subset (8,499 = **52.8%** null `NextXDSegI`):

| class | fires | ambiguous (left alone) |
|---|---|---|
| **fill** — null link, one same-group continuation | 6,119 | 321 |
| **override** — link points *out* of its `XDGroup`, same-group continuation exists | 99 | 52 |

Fills alone take the catalogue **13/20 → 17/20**; fills + overrides → **20/20**. I-184
comes back at **EB 10 segments / 4.717 mi, WB 10 / 4.629 mi**, which independently
reproduces the outside pass's hand-patched figures from a derived rule rather than a
typed list. Every one of the 13 entries that already resolved is unchanged under fills
(`i84-eb` 27 segments / 15.123 mi, SH-45 40 / 17.429). Overrides move exactly one —
`sh44-wb-boise-eagle`, 11 segments / 4.1064 mi → 12 / 4.1441 — and Item 38 is required to
explain that before the override class is trusted.

The two corridor-specific cases the overrides fix are the ones a fill cannot reach,
because XD asserts a link and the link is wrong: `428958469` → off-ramp `1187615222` on
I-184 EB (mainline continues at `1187558526`), and the SH-44 eastbound fork at Ballantyne
Rd from `XDGroup` 114772 onto the parallel 1-lane 3938033.

**This is not the fallback Item 36 banned,** and the distinction is worth stating once.
What was banned is *inventing order the network does not assert* — the geographic sort
that put the Garrity Blvd frontage road in series with I-84. A repair asserts nothing:
the geometries have to physically touch, the continuation has to stay inside XD's own
carriageway group, an ambiguous case stays a break, and every repair is named in the
output. Item 38 carries that through to `ChainResult` (`n_repaired_links`), so a corridor
that leaned on a repair says so in every report downstream.

**Two catalogue corrections fell out of the measurement:**

- **SH-16's description is wrong about its own carriageways.** Repaired, `sh16-nb` and
  `sh16-sb` resolve at 22 segments / 12.55 mi each and share **zero** segments, with
  clean `N` and `S` bearings — the coincident-carriageway caveat in the entry text does
  not apply. Its "1.02 mi + 12.9 mi north of the break" also sums to 13.9 against the
  12.55 measured; Item 38 settles which number is wrong.
- **`front-wb` is not a topology defect at all.** The chain reaches its target; it was
  rejected on 73.1% coverage. The owner supplied the roadway fact the XD attributes
  corroborate exactly: west of S 13th St all but one lane of Front St becomes I-184, the
  remnant keeps the name and functions as a frontage road, and ITD's interest transfers
  to the freeway because that is the state-owned facility. In the network, the first
  **9** members carry `RoadNumber = 20` at **5.0 lanes** (1.072 mi, ending at
  **(43.61703, -116.21077)**) and the last **3** carry a null `RoadNumber` at **1.0
  lane**. So the extent shortens at 13th St for a jurisdictional reason that the export's
  state-route query happens to agree with — which is the opposite of the SH-16 case Item
  36 refused, where an extent would have been defined by where a data gap fell. Recorded
  as a one-off: no generic coverage-allowance machinery is scoped for it.

**Item 39** is the runner Session 41 deferred: `scripts/run_district_screening.py` as a
thin shell composing `segment_screen` → `join_aadt` → `resolve_catalogue` → 
`rank_corridors`, with provenance in the output, findings reported separately rather than
ranked with blank metrics, and the D3 rankings themselves as the deliverable. It also
picks up the `ingest_export_streaming` row-count finding recorded under Known gaps the
same day, because the runner's provenance line quotes exactly that counter.

**One decision deliberately left to the Item 38 session** at the owner's direction:
whether the override class ships on by default or stays opt-in per run. The evidence to
decide it on is the `sh44-wb-boise-eagle` change above.

No code changed this session. ROADMAP.md gained Items 38–39 and their batch paragraph.

---

## Session 47 — The XD topology, repaired as data (ROADMAP Item 38) (2026-09-18)

Item 36 left 7 of the 20 District 3 corridors unresolved and recorded them as findings
about the network file. That was the right call about *mechanism* — what it refused was
completing a corridor by sorting a bounding box geographically, which is what summed the
Garrity Blvd frontage road in series with I-84 — but it is not a resting place: one of
the findings is **I-184**, the whole west-side commute into downtown Boise, and a
district screen that cannot rank it is not finished.

**Repairing a link is a different act from inventing an order,** and the whole design is
built to keep the difference visible. A repair requires the two geometries to physically
**touch** (end to start, within 25 m), the continuation to stay inside the segment's own
**`XDGroup`** — XD's carriageway key, which ramps, opposing directions and parallel
facilities do not share — the same cardinal **`Bearing`**, a junction turning no more than
**90°** measured on the *local* geometry rather than the segment chord, and **exactly one**
qualifying candidate. Two candidates is an ambiguity and an ambiguity stays a break. The
result is returned as a table, not applied in place, and every chain reports the links it
owes to it.

**`XDGroup` rather than `RoadNumber` + bearing was measured, not assumed.** At the Flying
Y the I-184 EB mainline's end point is **8.1 m** from the 1-lane on-ramp named `1A` and
**4.7 m** from the true mainline continuation. Road number and bearing agree with both;
distance prefers the ramp. Repaired that way, `i184-eb` walks into a **196-segment,
110.6-mile** chain. That measurement is why the rule is scoped to the carriageway.

**Two guards that only running it revealed** — both are now the load-bearing part of the
rule, and neither would have been reasoned into existence:

- **A rotary reverses a corridor without ever turning sharply.** At the south end of Eagle
  Rd, six segments of 8–20 m each (`Bearing` `O`, one `XDGroup`) join the southbound and
  northbound carriageways. No junction in it turns more than 60°, so the angle guard is
  blind to it. Repaired through, `sh55-eagle-nb` — a corridor that had resolved cleanly
  since Item 36 — walked *south* down Eagle Rd, round the rotary and back *north* over the
  same ground: **39 segments / 10.75 mi** against the corridor's 16 / 6.64. Requiring the
  same cardinal `Bearing` on both sides excludes it; `O` has no direction to preserve.
- **A link that leaves the *subset* is not a defect.** It is the edge of the extract (96
  in D3), and substituting a same-group continuation for it would put a different road in
  place of one that is merely absent. `walk_chain` already calls that `off_network`, which
  is the honest answer.

**The table.** `scripts/d3_link_repairs.csv` — **6,090 fills** (267 ambiguous, left null)
and **66 overrides** (**zero** ambiguous), 6,156 rows with the rule, the counts and the
meaning of each kind in a `# key: value` header that `load_link_repairs` reads back as
`attrs`. It is *derived*, not typed: a test regenerates it from the network and asserts
the committed rows come back.

**The override class ships on by default, and the decision was left to this session
deliberately.** The evidence: all 66 were read. In every case the replaced target is a
cross street, a ramp or a parallel facility, and the replacement is XD's own
same-carriageway segment — `I-184 E` → off-ramp `3`, `I-84 W` → ramp `27`, `S Cloverdale
Rd` → `W Kuna Mora Rd`, `S Broadway Ave` (US-20, 5.0 lanes) restored over an unnumbered
2.3-lane namesake. Ambiguous overrides are **zero**, so none of the 66 is a coin-flip.
And exactly one already-accepted corridor moves: `sh44-wb-boise-eagle`, 11 segments /
4.1064 mi → **12 / 4.1441**, because `484348318` (`N Glenwood St`, `RoadNumber` 44, 2.705
lanes, FRC 3) had its `NextXDSegI` pointed at `440882032` (`N Gary Ln`, **1.113 lanes**,
FRC 4, unnumbered). The new chain is the better one. `kinds=(FILL,)` remains available as
the setting that never contradicts the network — and on its own it already takes the
catalogue from 13/20 to 17/20.

**The resolution table, 20 of 20:**

| entry | segs | chain mi | repaired links |
|---|---|---|---|
| `i84-eb` | 27 | 15.123 | 0 |
| `i84-wb` | 29 | 15.892 | 0 |
| `i184-eb` | 10 | 4.717 | **5** |
| `i184-wb` | 10 | 4.629 | **4** |
| `myrtle-eb` | 9 | 1.197 | 0 |
| `front-wb` | 9 | 1.072 | 0 |
| `sh55-eagle-nb` / `-sb` | 16 / 15 | 6.643 | 0 |
| `sh69-nb` / `-sb` | 20 / 20 | 8.393 / 8.389 | 0 |
| `us2026-chinden-eb` / `-wb` | 19 / 19 | 10.017 / 9.713 | 0 |
| `sh44-eb-star-eagle` | 10 | 5.892 | **1** |
| `sh44-eb-eagle-boise` / `sh44-wb-boise-eagle` | 11 / 12 | 4.106 / 4.144 | 0 / **1** |
| `sh44-wb-eagle-star` | 9 | 5.387 | **1** |
| `sh16-nb` / `sh16-sb` | 22 / 22 | 12.553 | **1** each |
| `sh45-nb` / `-sb` | 40 / 40 | 17.429 | 0 |

The last column is the number worth keeping: the whole catalogue uses **14** of the
table's 6,156 rows. A repair table large enough to be alarming turns out to be barely
touched by the corridors that matter, and each touch is named.

**Two catalogue corrections fell out of the run.**

- **SH-16's description was wrong about its own carriageways and about its own length.**
  Repaired, `sh16-nb` and `sh16-sb` resolve at 22 segments / 12.553 chain mi / 11.947 mi
  in extent, share **zero** segments and carry clean `N`/`S` bearings — so Item 36's
  coincident-carriageway caveat did not apply here. And its "12.9 mi north of the break
  walks cleanly" was an **unbounded** walk: run without a target it goes **41.6 mi** past
  Emmett to Payette on SH-52. The resolved extent checks out on its own — sinuosity 1.053
  against the 11.345 mi straight line, with XD `Miles` and the projected geometry agreeing
  to 0.005 mi.
- **`front-wb` was never a topology defect.** Its chain reached its target; it was rejected
  on 73.1% coverage. The owner supplied the roadway fact, and the XD attributes corroborate
  it exactly: west of S 13th St all but one lane of Front St becomes I-184, the remnant
  keeps the name and functions as a frontage road, and ITD's interest transfers to the
  freeway because that is the state-owned facility. In the network the 9 members now in the
  entry carry `RoadNumber = 20` at **5.0 lanes** (1.072 chain mi) and the 3 blocks beyond
  carry a null `RoadNumber` at **1.0 lane**. So the extent ends at 13th St for a
  jurisdictional reason that the export's state-route query happens to agree with — the
  opposite of the SH-16 case, where an extent would have been defined by where a data gap
  fell, and the description says so in that order. The endpoint sits 37 ft short of the
  junction so the snap lands on the last five-lane block rather than the one-lane remnant
  across it. Recorded as a one-off: no generic coverage-allowance machinery was built.

The six repaired entries' descriptions were rewritten so their FINDING paragraphs read as
history rather than status — what the break *was* is kept, because it is a real fact about
this XD vintage, and each entry now states its resolved chain and how many repaired links
it rests on.

**Tests:** +14 in `tests/test_corridors.py` (64 in the file). Each rule and each guard is
pinned in the smallest fixture that shows it, including the ramp trap with the out-of-group
candidate deliberately made the *nearer* of the two, the rotary (refused with the direction
guard, accepted with `require_same_bearing=False`, so the guard is shown to be the thing
doing the work), the anti-parallel cul-de-sac pair, and the off-subset pointer. Two
real-network tests assert the committed table resolves the catalogue 20/20 with `i84-eb`
untouched at 27 segments, and that regenerating the table reproduces the committed rows.
**Full suite: 499 passed, 2 skipped** (was 485/2 at Session 45).

**Not done here:** the district ranking run itself — that is Item 39, which now has a
catalogue that resolves completely to rank.

---

## Session 48 — The district screening runner, and District 3's actual rankings (ROADMAP Item 39) (2026-09-18)

Items 34–37 built every piece of the district screen; Session 41 deliberately stopped
short of running it. Nothing in the repo composed `segment_screen` → `join_aadt` →
`resolve_catalogue` → `rank_corridors` outside a test, and the composition is where the
interesting failures live. Two were waiting there, and neither is a statistics bug.

**`scripts/run_district_screening.py` is wiring only** — no computation that is not
already in `src/inrix_tools/`, modelled on `build_validation_report.py`. It writes
`corridor_rankings.csv`, `corridor_resolution.csv`, `corridors.kml` and
`screening_provenance.json`, and the provenance is written **into the CSVs' `#` header as
well as the JSON**, so a ranking read off disk still states the area, dates, CValue gate,
windows, catalogue, repair table (with its rule and how many of its rows were used) and
AADT join policy it was computed under. A ranking with no stated basis cannot be handed to
anyone.

**A corridor that did not resolve is not ranked.** Findings are listed with their
`stop_reason` and coverage rather than carried into the ranking with blank metrics — Item
36's rule applied to the output. `--repairs` is required for the same reason: without the
Item 38 table seven of the twenty corridors do not walk, so an absent table stops the run
instead of silently ranking thirteen. `--no-repairs` walks `NextXDSegI` as published,
deliberately, and on this catalogue that is a refusal, not a partial answer.

**The `ingest_export_streaming` row-count finding was not a counter bug.**
`io._discover_parts` expands **any** `..._part_N.zip` into all of its siblings, so the
call was never handed one part: a single call on `part_1.zip` ingests all three and
`n_rows_added = 91,054,384` is the correct total. That also explains the thing the finding
recorded as unexplainable — the store reached its complete, exactly-correct state "while
the ingest was still working on part 2" because the *first* call was itself looping over
the three parts, and parts 2 and 3 "never logged" because they were never separate
ingests. `d3_store.duckdb`'s `_ingests` table has exactly **one** row. Session 40's "3
parts, 91,054,384 rows, 262 s" stands as a whole-export figure.

What *was* wrong is the record, and that is now fixed: both `ingest_export` and
`ingest_export_streaming` return `n_parts` / `parts`, log the resolved member list
(`part_1.zip + part_2.zip + part_3.zip`) instead of the single path handed in, and say so
in their docstrings. A provenance row can no longer read as "part 1 carried everything".

**Two defects the composition exposed that no unit test could have.** `corridor_geometry`
merged a member table with a GeoDataFrame and got back a plain DataFrame, so the AADT
bbox derivation hit `'Series' object has no attribute 'total_bounds'` — the geometry
column has to be re-declared after the merge. And `geometry.load_xd_network` reads a
GeoParquet only through `cache_path`, so a `--network` that *is* one went to the shapefile
reader; the runner now hands a `.geoparquet` source over as its own cache.

**The KML is the first caller to exceed Item 37's guard.** Twenty corridors against a
12-hue default palette is exactly the case that raise was added for, so the runner passes
an explicit 20-colour palette and `folder_by="corridor"` — the guard did its job on its
first real encounter.

**District 3, screened: 3,905 segments, 54,556,187 of 91,054,384 rows surviving
`CValue > 80` (59.9%, reproducing Sessions 40–41), all 20 corridors resolved, no
findings.** Each corridor at its own worst peak window, by vehicle-hours of delay:

| corridor | win | mi | delay min | TTI | delay/mi | veh-hrs |
|---|---|---|---|---|---|---|
| `i84-wb` | pm | 15.08 | 11.83 | 1.88 | 0.78 | **25,677** |
| `i84-eb` | am | 14.94 | 9.30 | 1.69 | 0.62 | **19,818** |
| `sh55-eagle-nb` | pm | 6.54 | 3.64 | 1.34 | 0.56 | 2,840 |
| `us2026-chinden-wb` | pm | 9.70 | 5.60 | 1.41 | 0.58 | 2,700 |
| `i184-wb` | pm | 4.11 | 3.55 | 1.86 | 0.86 | 2,700 |
| `sh55-eagle-sb` | pm | 6.53 | 2.84 | 1.27 | 0.44 | 2,219 |
| `us2026-chinden-eb` | am | 9.71 | 3.08 | 1.22 | 0.32 | 1,437 |
| `sh44-wb-eagle-star` | pm | 4.55 | 2.66 | 1.48 | 0.58 | 1,383 |
| `sh69-nb` | am | 8.27 | 1.62 | 1.13 | 0.20 | 828 |
| `sh44-wb-boise-eagle` | pm | 3.86 | 1.38 | 1.25 | 0.36 | 826 |
| `front-wb` | pm | 1.06 | 1.66 | 1.69 | **1.56** | 678 |
| `sh69-sb` | pm | 8.19 | 1.36 | 1.12 | 0.17 | 638 |
| `sh44-eb-star-eagle` | am | 4.92 | 1.23 | 1.20 | 0.25 | 620 |
| `sh44-eb-eagle-boise` | pm | 4.11 | 0.89 | 1.16 | 0.22 | 531 |
| `sh45-sb` | pm | 17.43 | 1.50 | 1.07 | 0.09 | 473 |
| `i184-eb` | am | 4.36 | 0.56 | 1.13 | 0.13 | 436 |
| `sh45-nb` | pm | 17.43 | 1.36 | 1.06 | 0.08 | 403 |
| `sh16-sb` | am | 11.95 | 1.10 | 1.08 | 0.09 | 209 |
| `myrtle-eb` | pm | 1.16 | 0.43 | 1.17 | 0.37 | 202 |
| `sh16-nb` | am | 11.95 | 1.02 | 1.08 | 0.09 | 197 |

**Five things this table says.**

- **I-84 is the district, by an order of magnitude.** 25,677 and 19,818 veh-hrs against
  2,840 for the next corridor. The direction split is the expected one — WB is the heavier
  in the PM, EB in the AM — and it is the split Item 34 existed to make trustworthy: before
  the ramp-vs-mainline fix one carriageway was weighted at 61,410 AADT where the other was
  114,981, which near-halved exactly this number.
- **I-184 WB ranks fifth, and could not be ranked at all two sessions ago.** It is the
  clearest return on Item 38: 4.11 mi at TTI 1.86, the second-worst congestion *ratio* in
  the district behind I-84 WB. Its rank carries one caveat that belongs with it — 3 of its
  10 segments are `matched_ramp` in the AADT join (Item 34's flag doing its job on a
  freeway made almost entirely of ramp geometry), so its veh-hrs is the least
  well-weighted of the top five.
- **`front-wb` has the worst delay *per mile* in the district** — 1.56 min/mi against
  I-84 WB's 0.78 — on 1.06 mi. A downtown couplet leg is short and saturated; ranking it
  by total veh-hrs (678) buries that, which is why both columns are reported.
- **SH-45 behaves as the rural control it was carried as**, near the bottom at TTI 1.07 /
  1.06 — not literally zero, but the flattest profile of the twenty over the longest
  extent (17.43 mi). It also has the district's lowest CValue survival, 0.79–0.82 against
  ~0.99 everywhere else, which is what a low-volume rural road looks like through a
  confidence gate and is worth remembering before reading its delay as signal.
- **Coverage is now essentially complete for every entry** (≥0.9999), where Item 36 had
  `front-wb` at 0.731. That is the Item 38 extent change, not better data.

**Tests:** +8 in a new `tests/test_run_district_screening.py`, built on a miniature
district — a synthetic export ingested into a real store, a four-segment network with a
deliberately missing link, a two-entry catalogue and a one-row repair table — run end to
end. It asserts the composition rather than the statistics: the resolved corridor ranked
under its **id** with its name beside it and `n_repaired_links = 1`, the unresolvable
entry reported and not ranked, `--no-repairs` producing a refusal rather than a partial
ranking, the provenance present in both the JSON and the CSV header, `--no-kml` skipping
only the KML, area resolution by key and by name, and a missing repair table refused
rather than ignored. Plus 2 in `tests/test_store.py` pinning the multi-part ingest: any
part path ingests every sibling, `n_parts` / `parts` say so, a second call adds nothing,
and the log names the members. **Full suite: 509 passed, 2 skipped** (was 499/2 at
the Session 47 checkpoint).

**One presentation change worth recording.** `rank_corridors` is keyed on the catalogue
**id**, not the name, with the full name carried as its own column. Two catalogue names
share their first twenty characters — `SH-44 (State St) WB: ...` names both halves of one
corridor — so a name-keyed summary printed two indistinguishable rows.

---

## Session 49 — One corridor is both directions: reporting corridors (ROADMAP Item 40) (2026-09-18)

Item 39 produced a ranking of the 20 catalogue entries, and the owner named what was
missing: **for reporting purposes one corridor is both directions.** `i84-eb` and
`i84-wb` were competing in that table as though they were different roads, which is not
how a district programmes work. The requirement carries its own constraint — "obviously
we want to break out and distinguish the peak and direction congestion information" —
so the grouping has to combine without dissolving.

**Two units, kept deliberately separate.** A catalogue entry stays one **direction** of
one extent, because that is the unit the `NextXDSegI` walk works in (a chain is
directional) and the unit the AADT join works in (the two carriageways of a divided
highway carry different counts — the whole point of Item 34). A **reporting corridor** is
the road, both directions, named the way a district talks about it. The catalogue
declares them in a `reporting_corridors` block and each entry carries `corridor` +
`direction`; D3's **20 entries group into 10 roads**. Validation cross-checks both ways:
a group an entry names must be declared, and a group declared with **no** entries raises,
because a corridor declared and unused is one silently missing from the report. Two
entries claiming the same direction of one corridor raises too — that copy-paste would
double-count one carriageway into the grouped total and drop the other.

**The real work is that these metrics do not combine the same way,** and the repo has a
specific history of getting exactly this class of thing wrong:

- **Additive** — `vhd`, `n_obs`, `n_segments`, `n_observed`, `missing_miles`, and the two
  trip components `travel_time_min` / `free_flow_min`. The directions are different
  vehicles over different pavement.
- **`miles` is NOT additive.** The two carriageways run over the **same ground**, so
  summing them double-counts the corridor's length — the identical error as summing the
  Garrity Blvd frontage road in series with the freeway it parallels, which is what Item
  36 exists to have stopped. `miles` is the **mean** of the directions, which is the
  road's length; the sum is reported separately as `directional_miles`, because that *is*
  the right denominator for a per-mile rate.
- **`tti`, `delay_per_mile`, `vhd_per_mile` are NOT averageable.** Each is recomputed
  from the summed components. A ratio of sums is not the mean of the ratios, and
  averaging would let a 0.6-mile direction pull as hard as a 15-mile one. The test that
  pins this needs lopsided free-flow times to even show a difference — with equal ones
  the two formulas coincide, which is precisely how this kind of bug survives a test
  suite.

**The direction breakout survives**: `peak_direction` / `peak_entry` (the direction
carrying the most `vhd`, falling back to `delay_min` when no AADT was joined), `tti_min` /
`tti_max`, `delay_min_max`, and `directions`. `worst_peak` is the **road's** — the window
carrying the most *combined* delay, which is not always either direction's own. The
per-direction rows are untouched; this is a second view, and the runner writes and prints
both.

**The trap grouping sets, which the run surfaced immediately.** A grouped row sums its
directions **at the same clock time**, and a commute corridor's directions peak at
different times. I-84 WB carries 25,677 veh-hrs in the PM and EB 19,818 in the AM — but
the grouped PM row reads **26,260**, because EB at 5pm is nearly empty. That is the right
answer to "how bad is this road at its worst hour" and the wrong answer to "how much delay
does this road cause in a day", and nothing in the frame said which question it was
answering. So the frame carries `vhd_directional_peaks` (and its delay twin): each
direction at **its own** worst peak, summed — **45,495** for I-84 — a group-level
constant that deliberately spans two windows. D3 splits **five and five**: Eagle Rd, State
St east, the downtown couplet, SH-45 and SH-16 peak in the same window both ways and have
the two numbers identical; I-84, Chinden, I-184, State St west and SH-69 do not.

**District 3 as ten roads** (each at its own worst peak window):

| corridor | win | mi | peak dir | TTI | TTI range | delay/mi | veh-hrs | both peaks |
|---|---|---|---|---|---|---|---|---|
| `i84` | pm | 15.01 | WB | 1.45 | 1.02–1.88 | 0.40 | **26,260** | **45,495** |
| `sh55-eagle` | pm | 6.53 | NB | 1.31 | 1.27–1.34 | 0.50 | 5,059 | 5,059 |
| `us2026-chinden` | pm | 9.71 | WB | 1.26 | 1.11–1.41 | 0.37 | 3,458 | 4,137 |
| `i184` | pm | 4.23 | WB | 1.42 | 1.01–1.86 | 0.42 | 2,738 | 3,135 |
| `sh44-star-eagle` | pm | 4.73 | WB | 1.27 | 1.09–1.48 | 0.35 | 1,696 | 2,003 |
| `sh44-eagle-boise` | pm | 3.98 | WB | 1.20 | 1.16–1.25 | 0.29 | 1,357 | 1,357 |
| `sh69` | pm | 8.23 | NB | 1.11 | 1.10–1.12 | 0.16 | 1,298 | 1,466 |
| `boise-couplet` | pm | 1.11 | WB | 1.43 | 1.17–1.69 | **0.94** | 880 | 880 |
| `sh45` | pm | 17.43 | SB | **1.06** | 1.06–1.07 | 0.08 | 876 | 876 |
| `sh16` | am | 11.95 | SB | 1.08 | 1.08–1.08 | 0.09 | 406 | 406 |

**Grouping re-orders the district, which is the point of having done it.**

- **Eagle Rd rises to #2** (5,059) where its directions ranked 3rd and 6th separately. It
  is the corridor whose two directions are *both* heavy — NB 2,840 and SB 2,219 — and the
  directional table was the wrong place to see that.
- **I-184 falls to #4** (2,738) because EB is light (436 against WB's 2,700). Its TTI
  range, **1.01–1.86**, is the widest in the district: one carriageway at free flow while
  the other is the second-worst in D3. A single grouped TTI would have hidden that
  entirely, which is what `tti_min` / `tti_max` are for.
- **The downtown couplet has the worst delay per mile** (0.94 against I-84's 0.40) on
  1.11 mi. Short and saturated; total veh-hrs buries it, which is why both columns report.
- **SH-45 holds as the rural control** at TTI 1.06–1.07, the flattest in the district —
  but it outranks SH-16 on veh-hrs (876 vs 406). That is length and volume, not
  congestion, and it is exactly the reading error TTI and delay-per-mile sitting beside
  veh-hrs exist to prevent.

Also corrected here: `front-wb`'s **name** still read "to the Connector terminus" after
Item 38 moved its extent to S 13th St.

**Tests:** +19. `test_screen.py` +8 on the combining rules, checked against hand-computed
arithmetic rather than against another function's output — the additive set, mean-not-sum
of miles, the lopsided-length case separating the ratio of sums (2.525) from the mean of
ratios (2.05), the peak direction flipping between AM and PM, the group's own
`worst_peak`, the one-window vs daily-burden split, the three accepted membership shapes,
an ungrouped entry reported rather than dropped, and the two refusals. `test_corridors.py`
+9 on the schema, including the real D3 catalogue grouping 20 → 10 with two distinct
directions each. `test_run_district_screening.py` +2, one of them asserting that a group
whose second direction did **not** resolve reports `n_entries = 1` and its one direction
rather than presenting a one-direction total as a two-direction one. **Full suite: 528
passed, 2 skipped** (was 509/2 at Session 48).

---

## Session 50 — The reporting table: every direction × peak visible, ranked per mile (ROADMAP Item 41) (2026-09-18)

Item 40 grouped the directional entries into roads, but still reported each road at a
**single** window with the other direction and the other peak collapsed into a
`peak_direction` label and a TTI range. The owner asked for the reporting shape
directly: every direction and peak as its own row, the final ranking a **sum over peaks
and directions**, and the ranking taken on a **per-mile rate** so length does not decide
it (first built on delay per mile, corrected mid-session to vehicle-hours per mile —
below). Also answered, since it was asked: the runner **does not write a report** — it
writes CSVs, a KML and a provenance JSON and prints a terminal table. The tables quoted
in these entries are that terminal output.

**`corridor_peak_totals`** sums every `direction × peak window` cell — four per road in
D3, AM and PM across two directions. Delay, travel time, free-flow and `vhd` add: the
two peaks of one direction are two separate trips over the same pavement.

**The mileage denominator is counted once per direction, not once per cell** — a
direction does not get longer because it has two peaks. That was verified before it was
assumed: observed miles have **zero spread** between AM and PM for all 20 D3 entries.
`miles_window_spread` reports it per corridor anyway, so a future export that breaks the
assumption shows up instead of silently halving every rate.

**`corridor_breakout`** returns the same cells unaggregated, as a
`(corridor_group, direction, window)` MultiIndex ordered by the ranked order it is
handed. A total that cannot be opened up is a number taken on trust; a test asserts the
cells sum to the total they sit under, and the printed table interleaves them.

**Which rate to rank on, settled by trying two.** The item was built first on
`delay_per_mile` and the owner corrected it mid-session to **vehicle-hours of delay per
mile**. Both were run against D3, and the correction is right: the three candidates are
three different questions, and the district answers them in three different orders.

| metric | the question | rewards | D3 order (top 4) |
|---|---|---|---|
| `vhd` | how much delay does this road cause | length **and** volume | i84, sh55-eagle, chinden, i184 |
| `delay_per_mile` | how bad is it to drive | intensity, ignoring how many people | boise-couplet, i84, sh55-eagle, chinden |
| **`vhd_per_mile`** | how much delay does each mile cause | volume, **not** length | i84, boise-couplet, sh55-eagle, i184 |

`delay_per_mile` over-corrects: dropping the volume weighting entirely puts a 1.11-mile
couplet leg above a freeway carrying 46,191 vehicle-hours. `vhd_per_mile` keeps the
weighting and drops only the length reward, which is the combination a screening rank
wants — and the ranking it produces is the one that survives being read aloud.

| # | corridor | dir·mi | delay | TTI | d/mi | veh-hrs | **vh/mi** | also: vh, d/mi |
|---|---|---|---|---|---|---|---|---|
| 1 | `i84` | 30.02 | 21.44 | 1.40 | 0.71 | 46,191 | **1,539** | 1, 2 |
| 2 | `boise-couplet` *(couplet)* | 2.22 | 2.89 | 1.29 | 1.30 | 1,233 | 554 | **9**, 1 |
| 3 | `sh55-eagle` | 13.07 | 9.00 | 1.21 | 0.69 | 7,022 | 537 | 2, 3 |
| 4 | `i184` | 8.47 | 4.22 | 1.24 | 0.50 | 3,217 | 380 | 4, 6 |
| 5 | `us2026-chinden` | 19.42 | 11.00 | 1.20 | 0.57 | 5,232 | 269 | 3, 4 |
| 6 | `sh44-eagle-boise` | 7.96 | 3.53 | 1.16 | 0.44 | 2,107 | 265 | 7, 7 |
| 7 | `sh44-star-eagle` | 9.47 | 4.85 | 1.20 | 0.51 | 2,500 | 264 | 6, 5 |
| 8 | `sh69` | 16.46 | 5.33 | 1.10 | 0.32 | 2,605 | 158 | **5**, 8 |
| 9 | `sh45` | 34.86 | 5.10 | 1.06 | 0.15 | 1,416 | 41 | 8, 9 |
| 10 | `sh16` | 23.90 | 3.42 | 1.06 | 0.14 | 656 | 27 | 10, 10 |

**I-84 leads at 1,539 veh-hrs per mile, nearly three times the next.** The two corridors
that move furthest between the orderings are the ones that justify the metric: the
**downtown couplet** is 9th of 10 on the bare total and 1st on the unweighted rate, and
settles at 2nd; **SH-69** is 5th on the total and 8th on both rates, which is 16.5
directional miles doing that work. SH-45 and SH-16, the rural end of the catalogue, are
9th and 10th on every one of the three, which is the reassurance that none of the metrics
is simply rewarding shortness.

Both `vhd` metrics are `NaN` without an AADT join, so `attrs['rank_metric_all_null']`
reports that and the runner falls back to `delay_per_mile` with a printed note — a frame
of `<NA>` ranks is catalogue order wearing a ranking's clothes.

**The cells are where the story is**, and they were invisible in the Item 40 view. I-84's
four cells run 9.30 / 0.25 / 0.05 / 11.83 minutes — the two off-peak-direction cells are
essentially free-flowing, which is what a commute corridor looks like and what a
single-window row could not show. I-184's are 0.56 / 0.05 / 0.07 / 3.55: one carriageway
at a standstill in the PM and everything else at reference speed.

**The one-way couplet, considered.** `one_way_couplet` is an optional flag on the
reporting corridor; D3 has exactly one and the catalogue says so. It **changes no
arithmetic** — every per-mile rate divides by the miles a round trip covers, here as
everywhere, and a test asserts the flagged and unflagged totals are identical — but it
changes how `directional_miles` *reads*. For a divided or undivided road that column is
travel-miles: the same ground driven twice. For the couplet, whose two legs are different
streets, it is *also* 2.22 mi of distinct centre-line pavement. The flag exists so the
column is not misread as centre-line mileage for the fifteen-mile freeway.

**A regression the rewrite introduced and the tests caught.** Replacing the per-direction
table with the breakout left an **ungrouped** catalogue printing nothing but its
findings — `summarise` no longer used its `ranking` argument at all. The per-direction
table is now the explicit fallback when no reporting corridors are declared, with a test
that pins it.

**Tests:** +12. `test_screen.py` +8, the load-bearing one being a three-corridor fixture
on which the three rankings give **three completely different orders** — `long` (40 mi,
40 min, 4,000 vh), `short` (1 mi, 10 min, 500 vh), `busy` (10 mi, 20 min, 8,000 vh) give
`busy>long>short` by total, `short>busy>long` by rate and `busy>short>long` by
vehicle-hours per mile. That is what makes the three worth distinguishing, and an earlier
version of the fixture accidentally tied two of them, which is exactly how a metric change
passes a test suite without being tested. Also: the cell sum; miles counted once per
direction; the all-null rank metric; the couplet flag changing nothing; peak-window
selection by default and by explicit `windows=`; the breakout's index and its cells
summing to their total; and the ranked ordering being followed.
`test_run_district_screening.py` +4, including the AADT-less fallback firing and
announcing itself. **Full suite: 540 passed, 2 skipped** (was 528/2 at Session 49).

---

## Session 51 — The export's segment set, resolved with the owner (ROADMAP Item 42) (2026-09-18)

A correction session. Reading the first district ranking, the owner asked why corridors
they knew to be congested were absent. My first answer was wrong in two ways and the
owner corrected both; this records what is actually true, because the wrong version was
briefly written into ROADMAP Item 42 and would otherwise have been inherited.

**Wrong answer #1: "354 miles of arterial are missing from the export."** Ustick,
Orchard, Linder, Overland, Ten Mile and the rest are **off-system** — county and city
jurisdiction, not ITD's. The route-number query was right to exclude them. ITD's own
route inventory agrees: every one of them matches only `OH000` ("other highway") records
in the cumulative AADT layer.

**Wrong answer #2: "Karcher Rd is 2 of 46 segments in the export."** It is **92 of 92**.
The error was searching XD's `RoadName` — 46 segments contain the string "Karcher" and
only 2 carry a `RoadNumber` of 55 — instead of reading
`out/highways/SH-55_Karcher_ALL.txt`, which had the corridor inventoried correctly all
along. The lesson generalises: `out/highways/` holds a per-corridor segment-id list for
every D3 state route, `District_3_ALL_Highways.txt` collects **3,947** of them, the export
observed **3,905**, and **nothing was observed that the list did not ask for**. The prior
inventory was sound and I should have checked it before measuring against XD attributes.

**What remained after that was 99 segments, and the owner resolved both halves as
deliberate:**

- **SH-55 Eagle Rd, 57 segments / 18.86 mi — deleted on purpose.** They are not the SH-55
  part of Eagle Rd. Checked against the corridor's own extent (43.59627 → 43.69094):
  **38** segments / 12.94 mi sit **south of I-84** and belong to ACHD, **14** / 5.37 mi
  sit north of State St, and the remaining **5** / 0.55 mi are edge stubs just beyond each
  end. All correctly excluded.
- **I-84B Caldwell, 42 segments / 14.81 mi — excluded on purpose.** The corridor was
  relinquished to the City of Caldwell about a decade ago. (The store's own evidence
  supports this reading: `District_3_ALL_Highways_No_Caldwell_I84B.txt` holds exactly the
  3,905 ids that were observed.)

**One thing the exclusion took with it that it should not have.** SH-19 between I-84 and
Simplot Blvd is carried in the XD data as **Centennial Way with `RoadNumber` 84** — still
classified as I-84 Business — so it left with the rest of the Caldwell corridor. The
route is SH-19, it is on-system, and it belongs in the export. That is **7 segments,
1.89 mi**, and it is the entire remaining gap:

```
440866307,440866308,440894666,440894667,484346940,1187396492,1187525172
```

`out/segments_to_add_to_export.txt` now holds exactly that, in the paste format
`out/highways/*.txt` uses, with the per-segment geometry and the list of what was checked
and is correctly absent. The 483 "spatial-join candidates" from the earlier pass are
dropped: they came from a 40 m join with no preference for a numbered route over an `OH`
record, so they are frontage roads, ramps and connector stubs near state routes — median
length 0.076 mi, 130 of them under 0.05 mi. Giving `join_aadt` that preference remains
Item 42's real work; the candidate list should be re-derived from it rather than from
this pass.

**And a correction to Item 40/41's couplet claim.** Session 50 recorded Myrtle/Front as
"the ONLY one-way couplet in the District 3 catalogue" and the catalogue said so. The
owner corrected it: **I-84 Business through downtown Nampa is a second couplet**,
westbound on 2nd St S and eastbound on 3rd St S. Its 35 segments are already in the export
(`out/highways/I-84B_Nampa_ALL.txt`, 7.71 mi) and it has no catalogue entry yet, so the
flag has nowhere to be set — but `one_way_couplet` is not a Boise-only quirk, and any
I-84B Nampa entry needs it. DATA_FORMAT now carries both couplets in a table, the
catalogue's `boise-couplet` description says "the only one currently in this catalogue"
rather than "the only one in the district", and ROADMAP Item 44 carries the instruction.

No code changed. The lesson worth keeping is procedural: three of the four wrong claims in
this session came from measuring against XD's attributes when a hand-built inventory of
the same thing was already sitting in `out/`.

---

## Session 52 — The route-class preference, and what "on-system" actually tests (ROADMAP Item 42) (2026-09-18)

Item 42's own framing did not survive measurement, and the part of it that did was
smaller and more specific than written. Recording both, because the wrong version was
in the ROADMAP.

**The reconciliation, run over all 32 corridor files.** Session 51 checked SH-55 by
hand; `scripts/reconcile_export_segments.py` now does every file. The result confirms
the inventory: the union of the per-corridor `*_ALL.txt` lists is **4,004** ids, the
master list **3,947**, the store **3,905**, and the **57 SH-55 Eagle Rd ids are the
only ones that never reached the master** — nothing else leaks. Every directional
(`_NB`/`_SB`/`_EB`/`_WB`) file adds up exactly to its `_ALL`. Two files differ from the
store and both were already explained: `I-84B_Caldwell_ALL.txt` (42 requested, none
returned) and the SH-55 pair.

**A finding on the way there: the export is split by segment, not by date.** Each part
carries the whole date span for its own segments and a `metadata.csv` listing only
those — D3's parts hold 1,947 + 1,942 + 16 = 3,905. `ingest_export_streaming` ingests
every part's observations but reads metadata from `source` alone (its docstring says
so), so `d3_store.duckdb` answers **1,947** to `load_metadata` while its observations
hold all **3,905**, and the first run of the reconciliation reported 2,000 segments
"requested, returned nothing" that are in fact sitting in the store. New `store.area_segments` reads the segment set from the
observations, which is what the export *contains*. It also settles the re-download
question: a supplemental export of a handful of segments is the same shape as another
part, so the 2.1 GB does not have to come down again.

**`join_aadt`'s route-class preference: real defect, different from the stated one.**
The ROADMAP asked for a numbered route to be preferred over an `OH` record because
"an on-system classification flips with the candidate set". Measured on D3:

- Preferring numbered ahead of distance or coverage is **wrong**. It would hand Ustick
  Rd the 74,500 of `FRANKLIN RD US-20 IC#29` and W Emerald St the 82,000 of
  `COLE RD IC #1B` — interstate records that pass within metres of a city street at an
  interchange. 34 D3 segments have a numbered and an unnumbered record tied on
  distance; ranking class above coverage moves **18** of them to the numbered record,
  and ranking it above distance moves 69 — every one of the ones checked for the worse.
- The instability is real but has a different cause: the **last comparison in the key
  was the record's row position in the layer**. Shuffling the AADT layer changed the
  AADT of **9 of the 3,905** export segments (`HOWARD RD` ↔ `CLARK RD` on SH-78,
  `BISHOP RD` ↔ `BERGLAND RD` on SH-52, `POISON CREEK RD` ↔ `PERSHALL RD` on US-95).

So route class went in as the **last decision**, under coverage, replacing the index:
with route, facility, distance and coverage equal, a record that names a route beats
one that names none, and below that the key falls back to the record's own
`RouteID`/`Descriptio`. The join is now provably independent of layer order (a shuffle
test pins it), and the same 9 segments are the only ones whose value moved. The
district ranking is unchanged to floating point — those 9 are rural and outside every
catalogue corridor.

**A record's `RouteID` class is not its route.** 330 D3 `OH` rows name a state route in
the description (`KARCHER RD (SH-55)`, `CHINDEN BLVD (US-20)`) and 82 more *are* a
route (`SH-52`). `aadt.record_route_number` reads the two forms ITD uses — a trailing
parenthetical and a route-only description — and nothing else: `FRANKLIN RD US-20
IC#29` is I-84's record at the US-20 interchange, and the parenthesis-hugging pattern
also refuses `CALDWELL BLVD(I-84 BUS)`, which is a business route. Feeding this to the
*match* (not just the tie-break) was tried and reverted: it moved 14 segments and every
one for the worse — two Chinden Blvd segments left US-20's 29,000 mainline record for
a 1,100 record covering 9% of the segment. It is used for the tie-break and for the
reported `aadt_route_number`, which is what the classification reads.

**On-system is a different question from "whose volume is this", and that is why the
483 were junk.** The join matches by proximity within a 60 m gate, so reading
on-system-ness off its winner labels **764 segments / 223 miles** on-system off the
export — 24 stubs of E Island Woods Dr on `EAGLE RD (SH-55)`, 66 of Simco Rd on
`GRANDVIEW RD (SH-167)`, cross-streets at every interchange. **Coverage cannot catch
them**: a 0.04-mile stub beside a mile-long record covers 1.00. New
`aadt.classify_on_system` adds the test that can — **identity**: the record's
description names the same street as the segment (`street_names_agree`, directionals
and street types dropped) or the segment names a route itself — plus mainline-only,
≤ 35 m (the divided-highway centerline sits 22–30 m off each carriageway) and ≥ 0.4
coverage (one of the seven confirmed SH-19 segments covers 0.45). **764 → 26 segments
/ 8.77 mi**, and the classifier finds the owner's seven SH-19 Centennial Way segments
independently, which is the ground truth it was checked against.

**The regenerated list, reviewed by road** (`out/export_reconciliation/`):

| | segs | mi | verdict |
|---|---|---|---|
| Centennial Way (SH-19) | 7 | 1.89 | accept — the confirmed addition, found again |
| W/E State St, Eagle | 11 | 4.68 | **ask** — the only new question |
| Blaine St | 1 | 0.48 | **ask** — see below |
| Caldwell Blvd / Cleveland Blvd | 5 | 1.54 | reject — relinquished I-84B |
| W Karcher Rd | 2 | 0.18 | reject — interchange stubs on `KARCHER IC #33` |

State St is the only candidate **no corridor list has ever named** — a contiguous
two-way chain 90–470 m north of the exported ID-44 chain through Eagle, matching an
OH-band `W STATE ST (SH-44)` record carrying 5,200 while the SH-44 segments beside it
match SH-44-banded records at 31,500–35,500. That reads as an older parallel
alignment, not the highway, but it is the owner's call and it is written down as such.
Blaine St sits inside the relinquished Caldwell corridor yet matches
`SIMPLOT BLVD(SH-19)` at 0.0 m and connects end-to-end with the south end of the
accepted Centennial Way run — if SH-19 continues onto Blaine it belongs with the seven.

**Tests.** `test_aadt.py` +11: the two description forms and the four ways to *not*
read a route from one; the tie-break winning and the layer-shuffle giving the same
answer; the tie-break refusing to promote a worse match (both by distance and by
coverage); the new `aadt_coverage` / `aadt_route_number` columns; and
`classify_on_system` accepting the route, rejecting the road beside it, and rejecting
far / clipping / unnumbered / ramp records with the category each time.
`test_reconcile_export_segments.py` is new (+8): never-requested vs never-returned kept
apart, a directional file that stops adding up, the observations-not-metadata rule, the
on-system pass finding the route and not its neighbour, and the report carrying its
policy. `test_store.py` +1. **Full suite: 560 passed, 2 skipped** (was 540/2).

---

## Session 53 — The Item 42 backfill lands, and a supplemental export is relabelled, not re-areaed (2026-09-18)

The owner ordered the seven SH-19 Centennial Way segments Item 42 identified and dropped
`Cent_2026-01-01_to_2026-09-01_15_min_part_1.zip` in the repo root. Ingesting it is one
line — except that it would have gone into **the wrong place**.

**The problem the file posed.** An area is the export's corridor set
(`store.area_identity`), and INRIX names a report whatever it was requested as: this one
carries `Corridor/Region Name = "Cent"`, not `"D3"`. Ingested as it stands it resolves to
its own `area_key`, and every district tool — the screening runner, the reconciliation,
the GUI's area picker — works on one area at a time, so the seven segments would have
been present in the store and invisible to every question anyone asks of D3. The checks
that would have caught it later are exactly the ones Item 42 just built, which is a poor
way to find out.

**The fix: relabel on the way in.** `ingest_export_streaming(..., corridor_name="D3")`
(and `ingest_export`) projects the label as the rows are **staged**, so the stored rows,
the resolved area and any later re-derivation of the identity all agree — the rows
*become* D3 rows, which is what they are. Two deliberate limits: it refuses an export
with no `Corridor/Region Name` column rather than inventing one (that would be a
different feature — forcing an area — and should be asked for by name), and the
provenance row records the rewrite, because an export that says one thing and is stored
as another has to say so somewhere:

```
Cent_2026-01-01_to_2026-09-01_15_min_part_1.zip (corridor 'Cent' -> 'D3')
```

The relabel was verified before writing to the 2.8 GB store, not after:
`area_identity({"Corridor/Region Name": ["D3"]})` returns `32cdb7edbcbd`, the existing
key, so the ingest could only be an additive keep-first merge.

**What landed.** 163,268 rows at 15 minutes over the same span as the district export
(2026-01-01 07:00Z → 2026-09-01 05:45Z), the store's segment set **3,905 → 3,912** —
exactly the seven ids — and its metadata 1,947 → 1,954. Re-running the two pipelines:

- **Reconciliation**: observed 3,912, "requested, returned nothing" **42 → 35** (the
  remainder of the relinquished I-84B Caldwell corridor), nothing observed unrequested,
  and Centennial Way has dropped out of the on-system candidate list — **26 → 19
  segments / 6.88 mi**, all of them already reviewed. The two questions still open for
  the owner are unchanged: W/E State St in Eagle (11 segs, 4.68 mi) and Blaine St (1).
- **District screening**: 3,912 segments and 54,687,762 peak-window observations (was
  3,905 / 54,556,187). The reporting ranking is **identical to floating point** — the
  seven belong to no catalogue entry, since there is no SH-19 corridor in
  `scripts/d3_corridors.json`. Whether they should get one is Item 44's business.

**A correction to Session 52.** That entry blamed the store's 1,947-row metadata table on
"a metadata merge that missed a part". It is not a miss: `ingest_export_streaming`
ingests every discovered part's observations but reads metadata from `source` alone, and
its own docstring says so. The consequence is the same — `load_metadata` under-reports
the export, `area_segments` is what to read — but the cause is documented behaviour, not
an accident, and DATA_FORMAT now says which. Worth revisiting on its own: a part-split
export leaves the store knowing only part 1's segment metadata.

**Tests.** `test_store.py` +3 functions / +4 collected cases (the relabel one is
parametrised over both ingest paths): a supplemental export lands in its own area
unlabelled and in the existing one relabelled, its stored rows carry the label it was
filed under, its metadata merges in, the provenance records the rewrite, an export with
no corridor column raises rather than being invented one, and `area_segments` reads the
observations rather than the part-1-only metadata. **Full suite: 563 passed, 2 skipped.**

---

## Session 54 — Corridors extracted from recurring congestion, not drawn from landmarks (ROADMAP Item 43) (2026-09-18)

**The problem Item 43 solves.** District 3's existing 20 corridor catalogue entries were
hand-drawn from landmarks and municipal borders (Session 33). On rural/urban hybrid
facilities like SH-45, an entire 17.4-mile corridor was declared, of which only 4.4 miles
through Nampa is congested — diluting the delay over three times its length in free-flowing
rural highway. The owner's requirement is that **extents come from the data**:
corridor candidates extracted as maximal contiguous runs of recurrently congested segments,
snapped to a junction only when the data already lands nearby.

**1. Recurrence is the criterion, not the mean (`segment_recurrence`).**
An export-wide mean collapses an incident or construction fortnight into the same figure as a
daily commute queue: 3 days of construction (TTI = 3.0) and 17 free-flow days (TTI = 1.0)
produces the exact same mean TTI (1.30) as 20 weekdays of recurring commute delay (TTI = 1.30).
`screen.segment_recurrence` computes in DuckDB:
- Grouping by `(Segment ID, window, local_date)` to determine daily mean speed and ref speed,
  giving daily TTI (`ref_speed / speed`).
- Classifying a segment-day as congested when daily `TTI > 1.25` (`DEFAULT_TTI_THRESHOLD`).
- Defining recurrence as the share of observed weekdays meeting the threshold.
At `DEFAULT_RECURRENCE_THRESHOLD = 0.50` ("congested most days"), the construction fortnight
has recurrence 0.15 (rejected), while the commute queue has recurrence 1.00 (accepted).

**2. Walking the runs, never sorting (`extract_congestion_runs`).**
Topological contiguity is walked along (repaired) `NextXDSegI` in forward and backward
directions. Geographic sorting is strictly banned (Item 36's rule). A run halts at an
`XDGroup` boundary (different carriageway keys cannot merge).

**3. Gap tolerance, reported not hidden.**
A single free-flowing segment between two congested ones does not fragment a corridor. Up to
`gap_tolerance_segs = 1` and `gap_tolerance_miles = 0.5` are bridged, and every bridged gap
is recorded on `CongestionRun` (`gap_segments`, `gaps_bridged`, `gap_miles`).

**4. Endpoint tidying with distance stated (`tidy_run_endpoints`).**
End segments are examined along the topology up to `snap_tolerance_miles = 0.25` for junctions
with differing `RoadNumber`s. If found, the run endpoint snaps to the junction and reports
`snapped_to` and `snap_distance_miles`. If no junction is within tolerance, it remains where
the data placed it (`snapped_to = None`, distance `NaN`).

**5. Directional pairs (`pair_directions`).**
Each run is searched for an opposing counterpart (`XDGroup` differs, same `RoadNumber`,
opposite bearing). A one-direction run is reported as `paired = False` — a finding, not half
a corridor to be quietly completed.

**6. Catalogue candidate emission (`emit_candidates`).**
Formats runs into catalogue entries with `id`, `name`, `start_latlon`, `end_latlon`, and
metadata. Deliberately omits `description`: `corridors.parse_catalogue` refuses to load
entries with empty descriptions, ensuring human review before any machine-generated candidate
enters the catalogue.

**7. Staying on-system.**
`extract_congestion_runs` and `emit_candidates` accept an `on_system` filter (a
`classify_on_system` DataFrame, boolean Series, or ID collection) and `on_system_only=True`
to ensure off-system county roads do not surface as candidates.

**Tests.** `tests/test_recurrence.py` is new (+24 tests):
- `segment_recurrence` accurately measures weekday recurrence vs daily TTI.
- Distinguishes construction fortnight (3/20 days, recurrence 0.15) from daily queue (20/20 days, recurrence 1.00) despite identical mean TTI (1.30).
- Recovers congested middle of a synthetic corridor.
- Bridges single-segment gaps within tolerance, splits on gaps exceeding tolerance.
- Halts at `XDGroup` boundaries.
- Snaps endpoints within tolerance and reports distance; leaves distant endpoints untidied.
- Directional pairing matches opposing runs and flags unpaired runs.
- `emit_candidates` produces valid coordinates (with geometry fallback) and omits description.
- `parse_catalogue` rejects emitted candidates until described.
- On-system filtering via DataFrame, Series, and ID sets.
**Full suite: 587 passed, 2 skipped.**

---

## Session 55 — Rebuilding the D3 catalogue from extracted runs (ROADMAP Item 44) (2026-09-18)

**The mandate.** Item 44 transforms the District 3 screening catalogue from hand-drawn
landmarks (Session 33) into an empirical, data-derived catalogue backed by recurrence analysis
(Item 43). Hand-drawn extents suffered from dilution (e.g., dragging 13 miles of free-flow rural
highway into SH-45 through Nampa), omitted major congestion hotspots (SH-55 Karcher Rd was
completely uncatalogued despite 92 segments in the store), left rural routes unverified, and
missed one-way couplets (downtown Nampa on 2nd/3rd St S). Item 44 extracts candidates across the
entire on-system network, triages them with an audit trail, rebuilds the catalogue, and re-runs
district screening end-to-end.

### 1. Candidate Extraction and Triage (`scripts/triage_candidates.py`)
- Evaluated all 3,912 segments in `d3_store.duckdb` across 54,687,762 peak-window observations.
- Computed recurrence (`screen.segment_recurrence`) at `DEFAULT_TTI_THRESHOLD = 1.25` and
  `DEFAULT_RECURRENCE_THRESHOLD = 0.50` (cached to `scratch/d3_recurrence.parquet`).
- Extracted 319 candidates (139 AM, 180 PM) via `screen.extract_congestion_runs` on the on-system
  network with link repairs.
- Triage audit trail written to `out/district_screening/candidate_triage.csv` and `.json`:
  - **8 ACCEPTED**: Primary empirical backbones matching core congestion corridors (I-84, I-184,
    Eagle Rd, Chinden Blvd, State St, SH-55 Karcher Rd, Garrity Blvd, downtown Nampa).
  - **75 MERGED**: Contiguous runs crossing `XDGroup` carriageway boundaries or bridging minor
    link breaks, incorporated into consolidated reporting extents.
  - **236 REJECTED**: Isolated intersection approach queues (< 0.25 mi), interchange ramp stubs,
    short local queues, and unnumbered facilities.
  - Rejection reasons are preserved for every candidate rather than silently dropped.

### 2. Catalogue Rebuild (`scripts/d3_corridors.json` via `scripts/rebuild_d3_catalogue.py`)
Expanded the catalogue from 20 directional entries / 10 reporting corridors to **34 directional
entries / 17 reporting corridors**:
- **100% Resolution**: All 34 entries resolve cleanly through `corridors.build_chain` with link
  repairs (`reached_target=True`, coverage 99.99%–100.0%, 0 dead ends).
- **Grouped and Balanced**: Every reporting corridor has exactly two directions (EB/WB or NB/SB),
  and each entry carries a non-empty `id`, `name`, `direction`, `corridor`, and detailed
  rationale `description` (> 80 chars).
- **Couplet Flagging**: `one_way_couplet: true` is set on both `boise-couplet` (Myrtle/Front)
  and `nampa-couplet` (2nd St S / 3rd St S).

### 3. Key Findings & Extent Decisions
1. **SH-55 Karcher Rd (`sh55-karcher`, 3.01 mi)**:
   - Added between Lake Ave / Midway Rd and I-84 IC 33 (EB & WB).
   - **Ranks #4 in District 3** at **420 vhd/mi** (2,109 peak veh-hrs delay, 2.7 min delay per trip),
     ahead of I-184 and Chinden Blvd! Previously absent from the catalogue, this is the most
     significant empirical finding of the screening.
2. **SH-45 Urban vs Rural Split**:
   - `sh45-nampa` (4.86 mi, 15 segs): Locust Ln / Deer Flat Rd to 2nd St S downtown. Delay density
     jumped to **144 vhd/mi** (Rank 10).
   - `sh45-rural` (13.07 mi, 26 segs): Walters Ferry (Snake River) to Locust Ln. Delay density
     dropped to **6 vhd/mi** (Rank 17, TTI 1.04).
   - *Result*: Dilution eliminated. The urban queue is isolated and ranked honestly, while the
     rural control confirms free flow.
   - *Snapping adjustment*: Placed `sh45-nampa-sb` start at `[43.57727, -116.56177]` to snap
     correctly to southbound 12th Ave Rd rather than eastbound 11th Ave S.
3. **Downtown Nampa Couplet (`nampa-couplet`, 0.75 mi)**:
   - 2nd St S WB (0.76 mi) and 3rd St S EB (0.74 mi) between 11th Ave S and Caldwell Blvd (I-84B).
   - Tagged `one_way_couplet: true`, ranks #11 at **67 vhd/mi**.
4. **SH-55 Mountain Highway Extents (North of State St)**:
   - Partitioned the 299 segments / 225 directional miles north of State St into 4 natural
     topographical and functional extents:
     - `sh55-eagle-hsb` (18.87 mi): Eagle to Horseshoe Bend (Rank 14, 18 vhd/mi, TTI 1.04).
     - `sh55-hsb-cascade` (51.5 mi): Horseshoe Bend to Cascade (Rank 16, 10 vhd/mi, TTI 1.04).
     - `sh55-cascade-mccall` (29.4 mi): Cascade to McCall (Rank 13, 19 vhd/mi, TTI 1.06).
     - `sh55-mccall-newmeadows` (12.7 mi): McCall to New Meadows / US-95 (Rank 15, 15 vhd/mi, TTI 1.03).
   - All 4 extents rank near zero, establishing the empirical rural baseline for District 3.
5. **Unranked Rural Routes Verified**:
   - US-95 (345.7 mi), SH-21 (200.5 mi), SH-51 (184.7 mi), SH-78 (183.6 mi), SH-52 (107.6 mi),
     SH-71, SH-19, SH-167, SH-30, SH-67, SH-72 (~1,190 miles in export).
   - Confirmed zero continuous corridor congestion runs. Isolated intersection approach queues
     (e.g., US-95 in Fruitland/Weiser) exist and were triaged as REJECTED (< 0.25 mi signal queues).

### 4. District Screening Results & Rankings
Screening footprint grew from 65.5 directional miles / 367 segments (9.4% of store) to **349.5
directional miles / 693 segments (17.7% of store)**.
Peak totals ranking (`out/district_screening/corridor_peak_totals.csv`, ranked on `vhd_per_mile`):
1. **I-84** (Nampa IC 35 to Boise IC 49): 1,539 vhd/mi (46,191 veh-hrs, 21.4 min delay, TTI 1.40)
2. **Boise Couplet** (Myrtle EB / Front WB): 554 vhd/mi (1,233 veh-hrs, 2.9 min delay, TTI 1.29)
3. **Eagle Rd** (I-84 to SH-44): 431 vhd/mi (5,635 veh-hrs, 10.6 min delay, TTI 1.34)
4. **SH-55 Karcher Rd** (Lake Ave to I-84 IC 33): 420 vhd/mi (2,109 veh-hrs, 2.7 min delay, TTI 1.25)
5. **Chinden Blvd** (Eagle Rd to I-184): 258 vhd/mi (3,365 veh-hrs, 4.4 min delay, TTI 1.24)
6. **I-184 Connector** (I-84 to downtown): 190 vhd/mi (3,217 veh-hrs, 4.2 min delay, TTI 1.24)
7. **Garrity Blvd** (11th Ave N to I-84 IC 38): 188 vhd/mi (1,349 veh-hrs, 2.7 min delay, TTI 1.22)
8. **State St** (Eagle Rd to 23rd St): 173 vhd/mi (2,374 veh-hrs, 4.6 min delay, TTI 1.22)
9. **Caldwell Blvd** (Karcher Rd to 11th Ave N): 159 vhd/mi (1,159 veh-hrs, 2.2 min delay, TTI 1.18)
10. **SH-45 Nampa** (Locust Ln to downtown): 144 vhd/mi (1,404 veh-hrs, 2.1 min delay, TTI 1.16)
11. **Nampa Couplet** (2nd St S WB / 3rd St S EB): 67 vhd/mi (99 veh-hrs, 0.4 min delay, TTI 1.10)
12. **SH-16** (State St to Emmett): 59 vhd/mi (1,631 veh-hrs, 2.4 min delay, TTI 1.07)
13. **SH-55 Cascade to McCall**: 19 vhd/mi (1,102 veh-hrs, 4.4 min delay, TTI 1.06)
14. **SH-55 Eagle to Horseshoe Bend**: 18 vhd/mi (691 veh-hrs, 1.9 min delay, TTI 1.04)
15. **SH-55 McCall to New Meadows**: 15 vhd/mi (382 veh-hrs, 0.8 min delay, TTI 1.03)
16. **SH-55 Horseshoe Bend to Cascade**: 10 vhd/mi (1,061 veh-hrs, 5.0 min delay, TTI 1.04)
17. **SH-45 Rural** (Walters Ferry to Locust Ln): 6 vhd/mi (160 veh-hrs, 1.6 min delay, TTI 1.04)

### 5. Tests
- `tests/test_d3_catalogue.py` (+5 tests):
  - `test_d3_catalogue_schema_and_completeness`: 34 entries, 17 groups, balanced directions, descriptions, couplet tags.
  - `test_d3_catalogue_includes_item44_key_corridors`: checks SH-45 split, Karcher Rd, Nampa couplet, SH-55 mountain extents.
  - `test_all_d3_catalogue_entries_resolve_with_repairs`: 100% `reached_target=True`, valid chain geometries.
  - `test_candidate_triage_audit_trail`: validates 319 candidates, exact 8/75/236 counts, non-empty reasons in CSV and JSON.
  - `test_district_screening_outputs_integrity`: verifies 68 rankings, 17 totals, 68 breakouts, 34 resolutions, >= 70% coverage, ranking order assertions.
- `tests/test_corridors.py`: updated coordinate bounds for northern D3 and updated catalogue group assertions (73 passed).
- **Full suite: 592 passed, 2 skipped.**


