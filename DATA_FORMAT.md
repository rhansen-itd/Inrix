# DATA_FORMAT.md — INRIX export schema & quirks

Reverse-engineered notes on the INRIX Roadway Analytics data downloader exports
this project consumes. Keep this current: update it whenever a session learns
something new about the format. `io.py` is the code contract for what's below.

## The download package

An INRIX "5-min" export is a `.zip` (e.g.
`Myrtle_2026-02-01_to_2026-07-16_5_min_part_1.zip`) containing:

| file                 | purpose                                                    |
|----------------------|------------------------------------------------------------|
| `data.csv`           | the time series — one row per (segment, 5-min timestamp)   |
| `metadata.csv`       | one row per segment: geometry, road name, length           |
| `README.txt`         | INRIX's column descriptions (upstream source of this doc)  |
| `reportContents.json`| report parameters (date range, segments, granularity)      |
| `EULA.txt`           | license — **why the raw data is gitignored**               |

`data.csv` is large (the sample is ~200 MB) and license-restricted. Big exports
are split into `..._part_1.zip`, `..._part_2.zip`, …. `io.py` should read
straight from the zip (stream `data.csv` out of it) rather than requiring it be
unpacked, and be able to concatenate parts.

## `data.csv` columns

Header (observed on the Myrtle export):

```
Date Time, Segment ID, UTC Date Time, Speed(miles/hour),
Hist Av Speed(miles/hour), Ref Speed(miles/hour), Travel Time(Minutes),
CValue, Pct Score30, Pct Score20, Pct Score10, Road Closure,
Corridor/Region Name
```

| column                       | meaning / units                                                                 |
|------------------------------|---------------------------------------------------------------------------------|
| `Date Time`                  | local timestamp **with UTC offset**, e.g. `2026-02-01T00:05:00-07:00`            |
| `Segment ID`                 | INRIX XD segment id (int). The entity key for all analysis.                      |
| `UTC Date Time`              | same instant in UTC (`...Z`). Redundant with `Date Time`; useful as a tz check.  |
| `Speed(miles/hour)`          | estimated **harmonic mean** speed, mph                                           |
| `Hist Av Speed(miles/hour)`  | historical average speed for that hour-of-week, mph                              |
| `Ref Speed(miles/hour)`      | free-flow / reference speed (open road), mph — **not** the legal limit          |
| `Travel Time(Minutes)`       | segment travel time, minutes                                                     |
| `CValue`                     | INRIX confidence value, 0–100 (see below)                                        |
| `Pct Score30/20/10`          | % of the interval derived from real-time data at 30/20/10 confidence tiers       |
| `Road Closure`              | `T`/`F` flag                                                                     |
| `Corridor/Region Name`       | user-assigned corridor label from the report setup                              |

Speed/travel-time units follow the account preference (mph+min or kmh+km). The
header text names the unit — parse it, don't assume mph.

## CValue (confidence)

INRIX confidence score, 0–100. Low CValue means the interval leans on
historical/reference data rather than live probes. The seed notebooks filter to
`CValue > 80` before analysis; treat that as a **tunable default**, not a
hard-coded constant — expose the threshold and record it in results so a study
is reproducible. Filtering on CValue interacts with missing-data handling: a
segment with sparse live coverage will lose more rows.

## `metadata.csv` columns

```
Segment ID, Road, Direction, Start Latitude, End Latitude,
Start Longitude, End Longitude, State/Region, District, Postal Code,
Segment Length(Miles), Intersection
```

- `Start/End Lat/Long` are the segment endpoints — used for the KML export
  (`kml.py`) and for map-based segment pickers in the GUI.
- `Segment Length(Miles)` — segment length; pairs with `Travel Time(Minutes)`
  to derive space-mean speed if needed.
- `Direction` — `N/S/E/W/...`.
- `Intersection` — human-readable cross-street label; good for axis/legend text.
- Join key to `data.csv` is `Segment ID`.

## Timezone handling

- `Date Time` is tz-aware (carries the offset). Parse with the offset intact,
  then convert to an explicit IANA zone for the corridor (e.g.
  `America/Denver`) — **do not** silently drop to naive local time.
- `UTC Date Time` is the same instant in UTC; use it to validate the offset if a
  file looks suspicious.
- Day-of-week and time-of-day binning (see `timebins.py`) must be done in
  **local** time — that's the whole point of the conversion.

## Corridor travel time (segment → corridor)

A corridor is several consecutive segments. Total corridor travel time at a
timestamp is the **sum of the member segments' `Travel Time(Minutes)`**, but
only for timestamps where **all** segments reported (the seed notebook keeps
rows whose per-timestamp segment count equals the corridor's max count, to avoid
undercounting when a segment is missing). `io.py` / `speed.py` should reproduce
that "complete-set-only" rule and make partial timestamps visible rather than
silently summing incomplete data.

**How the complete-set size is defined (`expected` — "max" vs "total").**
`mark_complete_timestamps` (and `corridor_travel_time` / `network_travel_time`
through it) offers two definitions of "all segments":

- `"max"` — the group's **max simultaneously observed** segment count. This is the
  seed notebook's rule and the default for `corridor_travel_time`. It is fine when
  the group regularly achieves its full membership.
- `"total"` — every **distinct** segment ever seen in the group.

They diverge only on a **sparse** group where *no* timestamp ever holds the whole
membership. There `"max"` falls to `max < N`, and two "complete" timestamps can
sum *different* (N−1)-segment subsets — a long segment present in one but not the
other — so their totals are not level-comparable and decomposition/changepoint
will read the composition change as a spurious step. `"total"` is stricter (only
timestamps with the whole set present count), keeping the series level-comparable
at the cost of more dropped timestamps.

**At network scale (all segments as one group).** `speed.network_travel_time`
reuses the same rule with a single synthetic all-segments group, so a network
total requires **every** segment in the export to have reported at that
timestamp. Because sparsity is exactly where the `"max"` weakness bites, network
scope **defaults to `expected="total"`** (pass `expected="max"` for the older
behaviour). That is far stricter than a short corridor: with dozens of segments a
sizeable fraction of 5-min timestamps are partial and drop, so the surviving
network series is sparse (but each retained total is undercount-free and
level-comparable, which is the point). Verified on the 46-segment Myrtle export:
the full set is regularly achieved (so `"total"` == `"max"` == 46) and enough
complete timestamps survive to decompose and run before/after on the aggregate.
If a future export were sparse enough to starve the decomposition, the fix is the
Item 9 window guard (already auto-scaled) or relaxing `require_complete`, not
abandoning the rule.

**Segment coverage + the "completeness cost" (Item 19).** Which segments are
*costing* the corridor/network its complete-set timestamps is answerable directly:
`speed.segment_coverage(df, members=...)` reports, per member segment over the
timestamps any member reported, its **coverage** (fraction of those timestamps it
reports) and its **`complete_set_cost`** — the number of timestamps at which it is
the *sole* absent member, i.e. exactly how many complete-set timestamps its
removal would recover. Dropping the highest-cost segment via the additive
`members=` list on `corridor_travel_time` / `network_travel_time` restricts the
sum to the remaining segments *before* the complete-set rule runs, so those
timestamps become complete and the aggregate series gets denser (at the cost of no
longer covering that segment). The cost number is exact: `len(dropped_complete) −
len(full_complete) == complete_set_cost` for the dropped segment. The GUI surfaces
these two columns in the interactive segment table and flags any row with
`complete_set_cost > 0`, so a chronically-missing segment is visible-not-silent and
can be deselected for a more complete corridor/network aggregate.

## INRIX XD network shapefile (segment geometry)

The official INRIX XD road network, delivered as a shapefile
(`USA_Idaho_shapefile.zip` — statewide Idaho, 41,770 segments, EPSG:4326). This
is the **source of real road-following geometry** and the reason no OSM
map-matching is needed; `geometry.py` (ROADMAP Item 8) is the code contract.

- **Join key:** `XDSegID` (shapefile) == `Segment ID` (`data.csv` / `metadata.csv`).
  Verified: all sampled Myrtle/Franklin segments resolve, road names match.
- **Geometry:** shape type 3 (PolyLine), multi-vertex LINESTRINGs that follow
  the road (first feature has 13 vertices). CRS WGS84 / EPSG:4326.
- **Topology:** `NextXDSegI` / `PreviousXD` give the downstream / upstream
  segment id — a free connectivity table (columns become `Segment ID`,
  `next_id`). Used for corridor assembly and originated-anomaly detection.

Attribute fields (all stored as `C(255)` — see quirk below):

| field                       | notes                                                     |
|-----------------------------|-----------------------------------------------------------|
| `XDSegID`                   | segment id — join key                                     |
| `PreviousXD`, `NextXDSegI`  | upstream / downstream segment id (topology)               |
| `FRC`                       | functional road class (0=motorway … higher=local)         |
| `RoadName`, `RoadNumber`, `RoadList` | road naming                                      |
| `Miles`                     | segment length (matches `metadata.Segment Length(Miles)`) |
| `Lanes`, `Bearing`, `SlipRoad` | lane count, direction, ramp flag                       |
| `County`, `District`, `PostalCode`, `State` | admin geography                          |
| `StartLat/Long`, `EndLat/Long` | endpoints (redundant with the geometry)                |
| `XDGroup`, `LinearID`       | grouping / linear reference ids                           |

**Quirk — everything is `C(255)`:** every `.dbf` field is a 255-char string, so
the `.dbf` is ~266 MB unzipped and numeric fields (`Miles`, `StartLat`, `Lanes`)
must be cast on load. `geometry.py` subsets the statewide file to the segments in
a given export — pushing an `XDSegID IN (...)` WHERE clause down into the reader
(pyogrio, via GDAL `/vsizip/`) so the full `.dbf` never loads — and can cache a
small typed GeoParquet. Ids (`XDSegID`, `PreviousXD`, `NextXDSegI`) cast to
nullable `Int64` so a blank network-end neighbour is `<NA>`, not `0`.

**Quirk — DBF 10-char field-name truncation:** the downstream-neighbour field is
`NextXDSegI` (truncated from `NextXDSegID`). Use the truncated name.

Confirmed 2026-07-16: all 46 Myrtle segments resolve to real multi-vertex
LINESTRINGs (min 3 / median 7 / max 23 vertices) — the geometry follows the road,
and the statewide file fully covers the Ada County study area (zero unmatched).

**License:** INRIX/NPMRDS geometry — treat like the data exports: gitignored,
not redistributed.

## Delay vs free-flow travel time (derived)

**Delay** is the excess travel time a segment carries over its free-flow
(open-road) travel time — a pure derivation from columns already in the export, so
no new data source is needed (`speed.segment_delay`, ROADMAP Item 17):

```
Delay(Minutes) = Travel Time(Minutes) − free-flow travel time
free-flow travel time = Miles / free_flow_speed × 60
```

- **Free-flow speed** defaults to the per-row `Ref Speed(miles/hour)` column —
  INRIX's open-road reference speed, **not** the posted/legal limit. Where
  `Ref Speed` is missing or suspect, a per-segment high percentile of *observed*
  speed (e.g. the 95th) is the fallback.
- **Length** comes from `Segment Length(Miles)` (metadata) or `Miles`
  (XD shapefile). When length is unavailable the computation degrades to the
  **speed-based form** `Travel Time × (1 − v_obs/v_ff)`, which is algebraically the
  same value (length cancels), as long as a `Speed(...)` column is present.
- Negative delay (probe noise measuring faster than free-flow) is floored to 0 by
  default; a non-positive/missing free-flow speed yields `NaN` delay (that segment
  is treated as a missing metric in the GUI).
- **Corridor/network delay is a sum** across member segments, exactly like travel
  time, under the same complete-set rule (`corridor_travel_time(..., value='Delay(Minutes)')`).

## AADT volume layer (ITD `Cumulative_AADT`)

Annual Average Daily Traffic (traffic **volume**) is **not** in the INRIX export —
it comes from the ITD `Cumulative_AADT` GIS layer, added to the repo root as
`Cumulative_AADT.zip` (a gitignored fixture, like the Myrtle export). `aadt.py`
(ROADMAP Item 18) is the code contract; it powers volume weighting (vehicle-hours
of delay, AADT-weighted corridor speed).

- **Format:** a shapefile inside the zip — **251,310 `LineString Z` features in
  EPSG:8826** (Idaho state plane; `aadt.load_aadt` reprojects to EPSG:4326).
  Unlike the XD shapefile, the `.dbf` carries **real numeric types**, so no
  `C(255)` casting is needed.
- **`Year` is cumulative.** The layer stacks 1999–2024 (~8–11k features per year);
  an unfiltered read double-counts every road. **Use only 2024** (the latest) —
  `load_aadt` filters `Year == year` (default 2024) with a pushed-down WHERE.
- **No `XDSegID`.** There is no INRIX join key, so the join to our `Segment ID` is
  necessarily **spatial**: `aadt.join_aadt` matches each Item 8 segment polyline to
  the nearest AADT line within a metre buffer, gated by an **endpoint-bearing check
  (mod 180°)** that rejects the opposing-direction split and perpendicular
  cross-streets. The result is flagged `matched` / `nearest` / `missing` with the
  match distance, so a marginal join is visible, not silent. On the Myrtle export
  the AADT centerlines coincide with the XD segments — 45/46 match at ~0 m.

Fields kept (`_KEEP_COLS`): `Year`, `RouteID`, `Route`, `FromMeasur`, `ToMeasure`,
`AADT`, `PassengerA`, `Commercial` (the truck split, for a future truck view).
Route-measure identity (`RouteID`/`Route`/mileposts) is the layer's own linear
reference; extras (`DHV`, `MADT1..12`) are dropped.

**AADT is a daily total.** Vehicle-hours of delay (`Delay/60 × AADT`) and the
AADT-weighted mean speed use it as a **relative** weight, not an absolute VMT: the
per-window impact figure is scaled to an average day at the window's mean delay,
not the window's own duration — the code records the caveat
(`attrs['aadt_caveat']`) and does **not** silently rescale. **Corridor/network
travel time stays a pure sum** across segments (Item 12); AADT does not re-weight
it — volume weighting only applies where a *mean across segments* is summarized.

**License:** treat like the data exports — gitignored, not redistributed.

## Direction convention & directional map display (Item 20)

Segment direction comes from `metadata.Direction` (`N/S/E/W`, sometimes `NB` /
`Northbound` / a compound `NE`). `geometry.py` (ROADMAP Item 20) codifies one
signed compass convention, shared with the Future *directional-AADT* item so both
read direction the same way:

- **`direction_group(d)` → `N/E/S/W`** — the primary cardinal. Compound labels fold
  to their **first** cardinal letter (`NE` → `N`, `SW` → `S`); spelling-tolerant
  (`nb`, `Northbound`, `E` all resolve). Unrecognizable → `None`.
- **`direction_sign(d)` → `+1 / −1 / 0`** — **N & E are positive (`+`), S & W are
  negative (`−`)**; unknown → `0`. This is the `+`/`−` convention the direction
  toggles show (`N (+)`, `S (−)`) and the one the directional-AADT volume split will
  reuse.

**Co-located opposing segments overlap on the map.** A road's two directions are
two XD segments sharing (nearly) the same polyline, so only the one drawn last is
visible/clickable. `geometry.offset_overlapping_segments(geo)` detects such pairs
(anti-parallel bearing within ~35° **and** geometries within ~20 m) and nudges each
one a few metres **perpendicular to travel, to the right-hand side of its bearing**
— so a NB/SB pair separates east/west and both draw side-by-side. It returns a
**display copy** (with an `offset_applied` bool column); the analytic geometry from
`segment_geometry` is never mutated. Isolated segments are left byte-for-byte. On
the Myrtle export exactly one opposing pair (2 of 46 segments) overlaps and is
offset; the other 44 are untouched. The GUI applies this on a per-render display
frame keyed off the direction toggles — the metric/coverage compute all stay on the
un-offset `ds.geo`.

## Database store (`store.py`, Items 21 + 23)

An optional **persistent DuckDB store** lets a session *select* a previously ingested
**area** instead of re-parsing a `.zip` and re-running the GIS spatial join every
time. It is an accelerator, not a requirement — the file loaders (`io` / `geometry` /
`aadt`) keep working, and running straight from a path is unchanged.

**Why DuckDB (not SQLite).** DuckDB is already a transitive dependency (via
`traffic-anomaly`'s `ibis-framework[duckdb]`), and its columnar engine is the right
fit for the analytic scans of a ~2M-row export. The DuckDB **`spatial` extension is
not used**: the GIS layers here are a small per-segment table, so geometry is
serialized as **WKB blobs** and rehydrated with `shapely` — no in-DB spatial
predicates, so `store.connect` stays offline (no extension download) and the schema
is portable. SQLite would serve the small tables but loses on the big scan, so the
choice is made on the *query* need (decision recorded in DESIGN_HISTORY Session 26).

**Organizing unit: the area (Item 23).** The store is **not** one silo per export.
An export's **area** is the sorted set of its distinct `Corridor/Region Name` values
(`area_key` = a hash of that set; `area_name` = the corridors joined). Exports of the
**same corridor set merge** into one growing area, so a session picks an *area*, not a
file. Consequences of the corridor-set rule to know:

- A later export covering **only a subset** of an area's corridors has a *different*
  corridor set → it becomes a **new area**, it does not merge into the superset one.
- When an export carries no corridor column/values, the area falls back to the sorted
  **Segment ID set** (named `segments(N)`).

**Merge is keep-first.** New observation rows are those whose
`(Segment ID, Date Time, bin_minutes)` tuple is **not already present**; an overlapping
row keeps the **first-ingested** value (a later export never overwrites it). So
re-ingesting the same or an overlapping export is idempotent (`n_rows_added` reports
what was actually new). Segment **metadata** and the processed **geometry+AADT** layer
merge the same way — keep-first per `Segment ID` — so a segment's geometry/join is
cached once and reused across every later export of the area.

**Bin length is a partition (Item 23).** Different time bases coexist in one area,
kept apart by an auto-detected `bin_minutes` column (the **modal** consecutive-
`Date Time` spacing per segment: 5-min INRIX → `5`, a 15-min export → `15`). The GUI
adds a **bin-length selector**; `load_export(area_key, bin_minutes)` reads one
partition (and errors, listing the choices, if an area holds several and none is
given).

**Layout.** Per-area tables keyed by `area_key`; two shared registries:

| table | contents |
|-------|----------|
| `_areas` | registry: one row per area — `area_key` (PK), `area_name`, `corridors` (JSON), `units_*`, `n_segments`, `date_min/max`, `has_geometry`, `has_aadt`, `schema_version`, `created_at`, `updated_at` |
| `_ingests` | provenance: one row per ingested export — `area_key`, `source`, `bin_minutes`, `n_rows_added`, `date_min/max`, `ingested_at` |
| `obs_<area_key>` | the merged `io.load_data` rows for the area + a `bin_minutes` partition column (`Date Time` as `TIMESTAMPTZ`) |
| `meta_<area_key>` | the merged `io.load_metadata` frame (Segment ID as a column; `Combined` included) |
| `geo_<area_key>` | the **processed** GIS join: `Segment ID`, `source`, the `join_aadt` columns (`AADT` / `aadt_source` / `aadt_dist_m` / `Route` / `Commercial`), geometry as a WKB blob (`_geom_wkb`, `NULL` for a `missing` segment). Cached at ingest so the Item 18 spatial join runs **once**, not per load. |

Exports whose column sets differ (an extra column) still merge — a missing column
fills `NULL`, a new one is added via `ALTER TABLE ADD COLUMN` (`_align_columns`), and
the anti-join insert uses `INSERT … BY NAME`.

**Round-trip fidelity (a single-export area must equal the file loaders):**

- **Timezone.** `io.load_data` returns `Date Time` as tz-aware **UTC**
  (`datetime64[ns, UTC]`). DuckDB stores `TIMESTAMPTZ` and, on read, materializes it
  in the *session* zone at μs resolution — so the connection pins `SET
  TimeZone='UTC'` and `load_export` normalizes back to `datetime64[ns, UTC]`. Units
  (`df.attrs['units']`) are restored from the `_areas` row.
- **Geometry.** WKB in / `shapely.wkb` out, into a WGS84 GeoDataFrame indexed by
  `Segment ID` — matching `geometry.segment_geometry` plus the cached AADT columns.
- **Object NULLs.** A NULL string/object cell round-trips as `None` (not `NaN`), so
  parity holds only where the source frame has no missing string cells; numeric NaN
  round-trips as NaN. (Not an issue for the covered frames.)

**Versioning / migration.** Every area row stamps `schema_version`
(`store.SCHEMA_VERSION`, now **2** — the area/merge model; **1** was the Item-21
one-dataset-per-export layout). There is no in-place migrator — the store is a
*cache*, so the migration policy is **re-ingest**: bump the version, delete the
`.duckdb` file, and re-run ingest (a v1 store's old `obs_<key>`/`_datasets` tables are
simply ignored by v2 code, so an existing file keeps working but its old data won't
appear as an area until re-ingested).

## Known quirks / open questions

- **Missing intervals**: segments do not always report every 5 minutes;
  downstream binning and decomposition must tolerate gaps (this is exactly why
  `traffic_anomaly.decompose` has `min_*_samples` guards).
- **Direction on split roadways**: interstate segments can appear as
  `184 / I-184 E` style composite names — verify direction filtering against
  `Direction`, not the road-name string.
- **Speed unit ambiguity** across accounts (mph vs kmh) — resolved by reading
  the column header; flag if a future export uses kmh so nothing assumes mph.
  `io.detect_units` parses the headers and stores the result on `df.attrs`.
- **Corridor lives in `data.csv`, not `metadata.csv`.** The raw INRIX
  `metadata.csv` has *no* Corridor column; the corridor label is
  `Corridor/Region Name` in `data.csv`. (The seed notebook assumed a hand-edited
  metadata with a Corridor column — don't rely on that.) Confirmed 2026-07-16.
- **Speeds are integers** in the export (e.g. `16`, `23`); only `Travel
  Time(Minutes)` is fractional. So a `Speed(...)` column loads as int64 — that's
  correct, not a parsing miss.
- **Zip member names collide on suffix:** `"metadata.csv".endswith("data.csv")`
  is `True`, so member lookup must match the **basename exactly** (`io` does).
- **Time-of-day filtering vs the decomposition sample guard.**
  `traffic_anomaly.decompose` uses **time-based** rolling windows (`preceding=N
  days`), so restricting rows to a time-of-day window (e.g. 4–6PM) preserves the
  day-to-day spacing and yields a coherent within-window trend. But its
  `min_rolling_window_samples` guard (default `96*5 = 480`) assumes a **full
  day** of 5-min samples is present; a 2-hour window's ~24 samples/day fall below
  it and the decomposition returns **empty**. `decompose.decompose_segments`
  auto-scales the guard by the fraction of the day's freq-grid slots actually
  present (full day → unchanged 480; 4–6PM → ~40) — the reason "decompose the PM
  peak only" works. `changepoint` uses the same time-based windowing, so it
  tolerates the sparser filtered series too.
- *(add findings here as sessions learn them)*
