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

**At network scale (all segments as one group).** `speed.network_travel_time`
reuses the same rule with a single synthetic all-segments group, so a network
total requires **every** segment in the export to have reported at that
timestamp. That is far stricter than a short corridor: with dozens of segments a
sizeable fraction of 5-min timestamps are partial and drop, so the surviving
network series is sparse (but each retained total is undercount-free, which is
the point). Verified on the 46-segment Myrtle export: enough complete timestamps
survive to decompose and run before/after on the aggregate. If a future export
were sparse enough to starve the decomposition, the fix is the Item 9 window
guard (already auto-scaled) or relaxing `require_complete`, not abandoning the
rule.

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
