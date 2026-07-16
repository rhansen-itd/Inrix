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

