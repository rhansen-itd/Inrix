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
