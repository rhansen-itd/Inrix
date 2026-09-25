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

**The split is by segment, not by date** (Item 42). Each part carries the *whole* date
span for its own subset of segments, and its `metadata.csv` lists **only those
segments**: D3's three parts hold 1,947 + 1,942 + 16 = **3,905**, and part 3's
`data.csv` is 25 MB against part 1's. Two consequences:

- **The store's metadata covers one part, by design.** `ingest_export_streaming`
  ingests the observations of *every* discovered part but reads metadata from `source`
  alone (`io.load_metadata` does not walk the parts), so `d3_store.duckdb` answers
  **1,947** to `store.load_metadata` while its observations carry all **3,905**.
  Anything reconciling the segment set must therefore read `store.area_segments` — the
  observations — not the metadata index.
- Adding segments later does **not** need the whole export re-downloaded: a supplemental
  export of just those segments over the same date span is the same shape as another
  part, and ingests into the same area — see the corridor relabel below.

**A supplemental export carries its own corridor label, and that is an area.** The area
is the corridor set (`store.area_identity`), and INRIX names a report whatever it was
requested as. The seven SH-19 segments backfilled after Item 42 arrived as
`Cent_2026-01-01_to_2026-09-01_15_min_part_1.zip` with `Corridor/Region Name = "Cent"` —
ingested as-is they would have been **an area of their own** that no district run would
ever look at. `store.ingest_export_streaming(..., corridor_name="D3")` (also on
`ingest_export`) rewrites the label as the rows are staged, so the rows, the resolved
area and any later re-derivation agree; the provenance row records the rewrite
(`Cent_….zip (corridor 'Cent' -> 'D3')`). It refuses an export that carries no corridor
column rather than inventing one. **D3 now holds 3,912 segments** (163,268 rows added,
metadata 1,947 → 1,954) over the same span, 2026-01-01 → 2026-09-01 at 15 min.

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

**A null `CValue` is the imputation marker, and it is common.** In the 2026 D3
*validation* export (the extracted query segments, 5.46 M rows) 1,182,246 of 5,457,816
rows (**21.7%**) carry no `CValue` at all — and in the **full** 15-minute D3 download
(91,054,384 rows, all 3,905 segments) it is **39.4%**, so the share is a property of
which segments you asked for, not a constant. Of the validation export's null rows,
**88.3%** have `Speed(miles/hour)` **exactly equal** to
`Hist Av Speed(miles/hour)` (against 11.3% of rows that do carry a CValue).
Those rows are historical backfill, not observation. A `CValue > 80` filter
drops them by default because the comparison is false for NaN — but only where the
filter is applied: `screen.segment_screen` gates by default and records the threshold
(Item 35), the GUI's load does, and since Item 31 so does the validation path
(`corridors.chain_travel_time(..., cvalue_threshold=80)`, which
`scripts/build_validation_report.py` passes by default and records on the summary).
`io.mark_imputed` marks the rows rather than removing them, so the share can be
**measured** wherever it is not gated.

**What the gate costs on the chain path, measured (Item 31, 2026-09-17).** Two facts,
both worth knowing before reading a gated number:

- **It never changes a retained bin's value.** Across all 15 validation chains and
  ~270k complete-set bins, the gated sum equals the ungated sum on every bin the gate
  retains — mean difference **0.0000 min, maximum absolute difference 0.0000 min**. The
  reason is structural, not luck: a complete-set bin needs every member to report, so a
  bin that loses any member to the gate fails the rule and drops out whole. The gate is
  therefore a **selection on bins**, never a correction to a sum — provided no member is
  gated away *entirely*, which on this export none was (`n_absent` is unchanged by the
  gate on all 15 chains). If that ever stops holding, that member becomes absent and the
  chain is marked `short`; it does not quietly shrink the sum.
- **What it costs is bins, and the cost is route-dependent** — the share of complete-set
  bins the gate removes: VSL 8.6–9.4%, Eagle Rd ~14.5%, Franklin 14.2–22.1%, SH-69
  19.9–24.3%, Cascade–HSB 46.8%, HSB–Cascade 45.8%, **Garden Valley–HSB 77.4%**. A
  three-quarters cut is not a detail a "we gated on CValue" sentence covers, which is
  why the share is carried per route rather than asserted study-wide.

Because the gate only selects, the difference between a gated and an ungated *mean* is a
composition effect — the overnight backfill-heavy bins leaving. On the 2026 D3 export
that moves the mean corridor travel time by +0.03 to +0.34 min on the arterials and
−1.23 min on Cascade–HSB, the rural route whose nights are mostly backfill.

Where the backfill sits matters more than how much of it there is: it is
concentrated **overnight and in the early morning**, and it is **route-dependent**.
Measured per hour on the 2026 D3 export over each route's assembled chain
membership, the signalised arterials are ~0% imputed through the whole daytime
window (Eagle Rd NB 3.5% overall — **0.0% at every hour from 07:00 to 13:00**,
0.4% through 17:00, peaking at 25.4% at 02:00), while rural Cascade–HSB is
**63% imputed at 05:00 and 39% at 06:00** (29% overall, 91% at 01:00, and 1.2% or
less from 10:00 to 17:00). So a daytime arterial study is untouched by the gate,
while an early-morning rural comparison can be largely comparing against INRIX's
own historical average — which is smooth by construction and will agree with
almost anything. (Re-running the 2026-09-17 reference comparison on bins with
<5% imputed members moved every corridor's bias by ≤0.3 min, so the gate changed
no conclusion *there*; the point is that it has to be checked, not assumed.)

**`Speed(miles/hour)` is integer-valued.** Every non-null speed in the 2026 D3
export is a whole number, with an observed floor of **5 mph** (0.1st percentile
12 mph). Quantisation is negligible at highway speed and about ±3% at a
congested-arterial 15 mph; it is not large enough to explain a travel-time
disagreement of any consequence, but it does mean derived speeds should not be
reported to more precision than the input carries.

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

**Requested vs. observed membership (Item 31).** Both definitions above read the
complete-set size **out of the data**, and that is a hole: a member with *zero*
rows never enters `nunique()`, so it cannot fail the check. Eagle Rd NB is missing
3 of its 20 members from the 2026 D3 export entirely, ran with
`expected_segments = 17`, and every one of its bins was marked **complete** while
summing 17/20 of the route. Passing `members=` (which
`corridors.chain_travel_time` now always does, from `chain.segment_ids`) makes the
requested size known, and the three cases are then reported separately:

| case | `complete` | `short` | meaning |
|------|-----------|---------|---------|
| whole set present | True | False | the sum covers what was asked for — **only this combination** |
| a member missing *at this timestamp* | False | False | the original rule: the timestamp drops |
| a member **never** present | True | True | the sum is reported *and labelled short*; `n_absent` says how many |

Every row carries `n_requested`, `n_absent`, `expected_segments` and `short`
beside `n_segments`. The third state is a deliberate choice: dropping all 2,633
bins of a 17/20 route is correct but useless, so the sum is kept and the shortfall
travels with it. `on_absent="drop"` takes the strict branch explicitly.
"Present" is **value-aware** throughout — a member with rows but no values in the
summed column is absent for summing, and is counted as absent.

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

### What the attribute fields do **not** mean (Item 46)

Four of these columns invite a reading they do not support. Each cost a defect in
the generated catalogue pass, so they are recorded here rather than rediscovered.

- **`StartLat`/`StartLong` are not guaranteed to lie on the geometry.** On the 2026
  District 6 network, record `1187395985` declares a start **294 m from its own
  LINESTRING**. That is enough for a catalogue endpoint written from the declared
  value to snap onto a different road — in that case a piece of SH-43 776 ft away,
  which resolved `off_network`. Take an endpoint's **position** from the geometry and
  use the declared values only to decide *which* terminal it is, by proximity
  (`extents.segment_endpoint`). That also absorbs a geometry digitised against the
  travel direction, where `coords[0]` is the segment's end.

- **`Bearing` is the segment's compass heading, not the carriageway's direction of
  travel.** Boise's Front St carries US-20/26 **westbound** and is coded `Bearing =
  "N"`, because the street curves. Any test of the form "the opposing direction of E
  is W" therefore fails on exactly the cases that matter: it rejected the best-known
  one-way couplet in the state. Compare **geometric** bearings (start of the first
  segment to end of the last) and test for anti-parallel within a tolerance. A
  geometric bearing in lon/lat must scale the east-west step by `cos(latitude)`
  (`geometry._bearing_deg` does since Item 51): at 43° N a degree of longitude is 0.73
  of a degree of latitude, and the raw-degree reading turned Pocatello's NNW (320°)
  4th/5th Ave couplet into "WB/EB".

- **`PostalCode` is a ZIP code, not a place name** (`83702`, not `Boise`), and
  **`RoadList` holds the segment's own aliases, not the roads that cross it**
  (`College Ave|S Emida College Ave|W College Ave`). Neither can name a corridor or
  its endpoints. The only offline cross-street/place naming in this project is the
  ITD AADT layer's `Descriptio`, carried per segment as `aadt_desc` by
  `aadt.join_aadt` (`W POST FALLS IC #5`, `SH-3, FERNWOOD`). Note that those strings
  contain `#`, which collides with the `# key: value` provenance header every CSV
  here carries — see the note under *District-wide screening*.

- **`XDGroup` is a carriageway, and a carriageway spans more than one street.**
  Boise's westbound US-20/26 is one 3.98-mile group that runs up S Broadway Ave and
  only then turns onto Front St; its majority `RoadName` is "S Broadway Ave". A
  feature defined on a *street* (a couplet leg) is a **run within** a group, split at
  each change of street name, not the group itself.

- **A two-way street shows up as two coincident segments of the same street,
  running opposite ways.** A one-way couplet street has none. That is how
  `couplets.drop_two_way_legs` recognises a false couplet (Item 49): a leg with its
  own street's opposing segment within 15 m over at least half its length. The
  `Bearing` label is no help here either: Elba-Almo Hwy's westbound segments carry
  `S` and its eastbound ones `E`. The detector's 25–300 m "lateral separation" also
  passes two consecutive pieces of one road where the name changes (Elba-Almo Rd /
  Elba-Almo Hwy, 272 m). Nine detected pairs statewide failed the test. Every
  registry couplet that matched before still matches.

- **`RoadNumber` alone cannot name the route band.** `95` is US-95 and `55` is SH-55;
  digit count decides nothing. `RoadName` states it (`US-95`, `I-90 W`, `ID-3` —
  normalise `ID-`/`SR-` to this project's `SH-`), and it must be read **per route
  across the network**, not per chain: no segment of I-90's Coeur d'Alene business
  route is named "I-90" — every one is named "Northwest Blvd" — so a per-chain read
  labels it "SH-90". `extents.route_label` does this, falling back to FRC 0 = interstate.

### What `NextXDSegI` does and does not connect (Item 36)

The topology table is real and usable — it is what `corridors.build_chain` walks —
but it is **incomplete, and incomplete asymmetrically**. Measured on
`USA_Idaho_shapefile.zip` (41,770 segments):

- **38.3% of segments carry a null `NextXDSegI`**, and the rate tracks road class
  almost perfectly: FRC 1 **0.9%**, FRC 2 1.8%, FRC 3 5.0%, FRC 4 28.9%, FRC 5
  **88.9%**. Across District 3 the whole subset is 52.8% null but the *numbered*
  routes are **2.8%** — so a highway corridor usually walks and a city-street one
  usually does not.
- **The gaps are two-sided.** Of the 15,997 null-`NextXDSegI` segments statewide,
  **zero** are named by any other segment as its `PreviousXD`. A walker that fell
  back to reversed upstream links would recover nothing; a break is a break.
- **A route number is no guarantee.** I-184 (the Boise Connector) carries a null
  `NextXDSegI` on **7 of its 20 segments (35%)** against 2.4% on I-84, so the
  Connector cannot be walked end to end at all in this vintage.

Three traps that a walk (or a bounding box) hits on real corridors, all found while
resolving the District 3 catalogue:

1. **The link can leave the highway for a parallel local street.** Eastbound SH-44
   at State St & Ballantyne Rd points not at the SH-44 mainline (`XDGroup` 114772,
   `Lanes` 2.2) but at a **different 1-lane W State St** a quarter-mile north
   (`XDGroup` 3938033), which runs east past Eagle and dead-ends at S Edgewood Ln.
   Same street name, same FRC, different road. `RoadNumber` and `Lanes` tell them
   apart; the geometry does not.
2. **The link can flip carriageway.** Westbound I-184 leaves the WB carriageway
   after ~1.0 mi onto an unnamed link, crosses to W Chinden Blvd and returns
   **eastbound** on I-184 E. A chain built without checking `reached_target` would
   run both directions of one freeway in series.
3. **Undivided arterials have coincident carriageways.** The E and W segments of an
   undivided road (State St, SH-16) share one geometry, so a snap cannot prefer a
   direction — `build_chain` resolves this by preferring the candidate pair that
   *reaches the target*, which works whenever the chain resolves. When it does not,
   the documented nearest-snap fallback may report the opposing direction, so for a
   failed entry the `stop_reason` is the finding and the member list is not.
   Route **concurrencies** do the same thing at a junction: SH-16 and SH-52 share
   pavement through Emmett, and a query point at that junction is 1 ft from both.
4. **The link follows INRIX's route, not ITD's** (Item 49). Where ITD moved a route
   onto a bypass that INRIX still numbers on the old street, `NextXDSegI` continues
   down the old street. The bypass is a clean chain on its own, but nothing links
   into it, and the other direction dead-ends where it rejoins. `repair_links` can't
   bridge this, because the two roads are different `XDGroup`s. Both known cases:
   - **Payette, US-95.** Northbound `1187491533` (`XDGroup` 2866405) links to S Main St.
     ITD's US-95 leaves it partway along, as `95 N` `384126765` (`XDGroup` 2866119),
     which has no `PreviousXD`, and runs up 16th St. Southbound 16th St ends at
     `383865907` with a null `NextXDSegI`, 1 m from the US-95 segment `1187629755`,
     which has no `PreviousXD`.
   - **Lewiston, US-12.** Walked on ITD membership, US-12 ends at Main St and the Levee
     Byp is a separate 1.7-mi chain each way; INRIX's link carries on to D St.

   A catalogue entry couldn't span such a junction, so Item 49 carried D3's US-95 as two
   entries (Fruitland, Payette 16th St), and the builder saw Lewiston as two facilities.
   **Resolved in Item 51:** such a junction is a `route_junction` row in the repair
   table (see *Chains across route-numbering changes*), and D3's US-95 is one entry
   again, `us95-fruitland-payette`, on the SHS alignment.
5. **A gap in the network is not a junction** (Item 51). SH-8 eastbound east of Bovill
   has no XD segments between SHS mp 36.27 and 37.60 in the D2 network; only westbound
   segments exist there, and the next eastbound segment's `PreviousXD` isn't in the
   network. The chain breaks there, and nothing joins it, correctly: no rule should
   invent pavement.

**Consequence for corridor definitions:** a corridor is stated as an endpoint pair
and resolved, and an extent that will not walk is recorded with its `stop_reason` —
never completed by sorting the segments in a bounding box by latitude or longitude.
`scripts/d3_corridors.json` is the District 3 catalogue in that form and
`corridors.load_catalogue` / `resolve_catalogue` are the code contract.

### `XDGroup` is the carriageway key, and it is what makes a repair safe (Item 38)

`XDGroup` groups the segments of **one carriageway of one road**. A ramp, the
opposing direction, and a parallel frontage road of the same name each carry a
*different* `XDGroup`, which is what makes it the right scope for correcting the
topology above — and what `RoadNumber`, `RoadName`, `FRC` and proximity each fail
to supply.

`corridors.repair_links` derives a `NextXDSegI` patch table under five conditions:
the candidate's start must lie within **25 m** of this segment's end, stay inside
the segment's own `XDGroup`, carry the **same cardinal `Bearing`**, turn no more
than **90°** at the junction (measured over 30 m of *local* geometry, not the
segment chord), and be the **only** candidate that qualifies. Two candidates is an
ambiguity and stays a break. On the District 3 subset (16,105 segments, 8,499 null
links) that yields **6,090 fills** (a null link completed — the network asserted
nothing) and **66 overrides** (a link that pointed out of its own carriageway,
replaced), with **267** ambiguous fills and **zero** ambiguous overrides left
alone. The table is committed as `scripts/d3_link_repairs.csv` with its rule in the
header; walking on it is opt-in (`repairs=`) and every chain reports the links it
owes to it as `n_repaired_links`.

Four measured facts about why the rule is shaped this way — each is a real corridor
that a looser rule gets wrong:

1. **Proximity picks the ramp.** At the Flying Y the I-184 EB mainline's end point
   is **8.1 m** from the 1-lane on-ramp `1A` and **4.7 m** from the true mainline
   continuation. `RoadNumber` and `Bearing` agree with both. Repaired on
   proximity + route number, `i184-eb` walks into a **196-segment, 110.6-mile**
   chain; scoped to `XDGroup` it resolves at 10 segments / 4.72 mi.
2. **A rotary reverses a corridor without ever turning sharply.** At the south end
   of Eagle Rd, six segments of 8–20 m each (`Bearing` `O`, one `XDGroup`) join the
   southbound and northbound carriageways. No single junction in it turns more than
   60°, so an angle guard does not see it; repaired through, `sh55-eagle-nb` walks
   *south* down Eagle Rd, round the rotary and back *north* over the same ground —
   39 segments and 10.75 mi against the corridor's 16 and 6.64. The **same-cardinal-
   `Bearing`** condition is what excludes it: `O` has no direction to preserve.
3. **Coincident ends can be anti-parallel.** The subset carries cul-de-sac pairs in
   one `XDGroup` whose ends coincide at 0.0 m and which run *at* each other; the
   90° guard is what stops them repairing into a 2-cycle.
4. **A link leaving the *subset* is not a defect.** It is the edge of the extract
   (96 such links in D3), and `walk_chain` already calls it `off_network`.
   Repairing it would substitute a different road for one that is merely absent.

**What repairing is not.** Item 36 banned completing a corridor by **sorting a
bounding box geographically** — inventing an order the network does not assert,
which is what summed the Garrity Blvd frontage road in series with I-84. A repair
asserts nothing new: the geometries must physically touch, the continuation must be
XD's own same-carriageway segment, an ambiguity stays a break, and every repair is
named in the output and reviewable in the committed table. With it, the D3
catalogue resolves **20 of 20** against Item 36's 13, and the whole 20-corridor set
uses just **14** of the table's 6,156 rows.

### A catalogue entry is a direction; a reporting corridor is the road (Item 40)

The two units are deliberately separate, because the pipeline needs one and the
reader needs the other:

- **A catalogue entry is one direction of one extent.** That is the unit the
  `NextXDSegI` walk works in (a chain is directional), and the unit the AADT join
  works in (the two carriageways of a divided highway carry different counts — the
  whole point of Item 34).
- **A reporting corridor is both directions of one road**, named the way a district
  talks about it. `scripts/d3_corridors.json` declares them in a
  `reporting_corridors` block and each entry carries `corridor` + `direction`;
  District 3's 20 entries group into **10** roads. `screen.rank_corridor_groups`
  combines, and the per-direction rows are untouched — it is a second view, not a
  replacement.

**Most of these metrics do not combine the same way, and getting that wrong is the
whole risk of grouping:**

| | rule | why |
|---|---|---|
| `vhd`, `n_obs`, `n_segments`, `missing_miles`, `travel_time_min`, `free_flow_min` | **sum** | the two directions are different vehicles over different pavement |
| `miles` | **mean** of the directions | the carriageways run over the **same ground**; summing double-counts the corridor's length — the identical error as summing a frontage road in series with the freeway it parallels |
| `directional_miles` | sum | centre-line miles × directions, and the right denominator for a per-mile rate |
| `tti`, `delay_per_mile`, `vhd_per_mile` | **recomputed** from the summed components | a ratio of sums is not the mean of the ratios; averaging would let a 0.6-mile direction pull as hard as a 15-mile one |

**One window is not one day.** A grouped row sums its directions **at the same clock
time**, and a commute corridor's directions peak at different times. D3's I-84 is the
case: WB carries 25,677 veh-hrs in the PM and EB 19,818 in the AM, but the grouped PM
row reads **26,260**, because EB at 5pm is nearly empty. That is the right answer to
"how bad is this road at its worst hour" and the wrong answer to "how much delay does
this road cause in a day". `vhd_directional_peaks` is the second number — each
direction at **its own** worst peak, summed (**45,495** for I-84) — and it
deliberately spans two windows. D3 splits five and five: Eagle Rd, State St east,
the downtown couplet, SH-45 and SH-16 peak in the same window both ways and have the
two numbers identical; I-84, Chinden, I-184, State St west and SH-69 do not.

### The reporting total: summed over peaks and directions, ranked per mile (Item 41)

`rank_corridor_groups` answers *how bad is this road at one hour*.
`screen.corridor_peak_totals` answers *how much congestion does it carry across its
peaks* — the row a corridor is finally ranked on. Delay, travel time, free-flow and
`vhd` sum over every `direction × peak window` cell; the two peaks of one direction
are two separate trips over the same pavement, so they add.

**The mileage denominator is counted once per direction, not once per cell.** A
direction's observed miles do not change between windows — verified on the D3 run,
zero spread across AM and PM for all 20 entries — so `directional_miles` sums each
direction's miles a single time, and every per-mile rate divides the *summed* delay
by it. `screen.corridor_breakout` returns the same cells unaggregated, as a
`(corridor, direction, window)` MultiIndex, so a total can always be opened up.

**Rank on `vhd_per_mile` — vehicle-hours of delay per mile.** The three candidates
are different questions, and District 3 orders them three different ways, so the
choice is recorded rather than left implicit:

| metric | the question | what it rewards | D3's top 3 |
|---|---|---|---|
| `vhd` | how much delay does this road cause | length **and** volume | i84, sh55-eagle, chinden |
| `delay_per_mile` | how bad is it to drive | intensity, ignoring how many people | boise-couplet, i84, sh55-eagle |
| **`vhd_per_mile`** | how much delay does each mile of it cause | volume, **not** length | i84, boise-couplet, sh55-eagle |

`vhd_per_mile` keeps the volume weighting and drops the length reward, which is the
combination a screening rank wants. The two corridors that move furthest between the
orderings are the ones that prove the point: the **downtown couplet** is 1st on the
unweighted rate and **9th of 10** on the bare total (1.11 mi of saturated one-way
pavement against I-84's 30.02 directional miles) and settles at **2nd** on
vehicle-hours per mile; **SH-69** is 5th on the total and 8th on both rates, which is
16.5 directional miles doing the work. Every metric's rank is returned beside the
chosen one, so the gaps stay visible instead of being decided silently.

Both `vhd` metrics are `NaN` without an AADT join. `attrs['rank_metric_all_null']`
reports that, because a frame of `<NA>` ranks is catalogue order wearing a ranking's
clothes.

**One-way couplets.** For a divided or undivided road the two directions run over the
*same ground*, so `directional_miles` is travel-miles (the ground driven twice), not
centre-line miles. For a one-way couplet — whose two legs are different streets — the
same number is *also* distinct centre-line pavement.

District 3 has **at least two** couplets on the state system, and only one is in the
catalogue so far:

| couplet | EB/NB leg | WB/SB leg | in the catalogue |
|---|---|---|---|
| downtown Boise, US-20/26 | Myrtle St | Front St | yes (`boise-couplet`) |
| downtown Nampa, I-84 Business | 3rd St S | 2nd St S | **not yet** — segments are in the export (`out/highways/I-84B_Nampa_ALL.txt`, 35 segments / 7.71 mi) but no entry exists |

So `one_way_couplet` is **not** a one-off flag for a single quirk of downtown Boise, and
an earlier note in this repo that called Myrtle/Front "the only couplet in the district"
was wrong. Any new entry on I-84B through Nampa needs the flag set. Either way it is the distance a round trip covers, which is
what every rate divides by, so the ranking stays comparable; the
`one_way_couplet` flag on the reporting corridor exists so nobody reads the column as
centre-line mileage for the fifteen-mile freeway. It changes no arithmetic.

## Corridor chain assembly, endpoint trim & proration (Item 28)

A **chain** is the ordered run of XD segments between two query points — how an
external route (a Google Maps travel-time log, a study corridor described by its
end coordinates) is turned into a set of `Segment ID`s. `corridors.py` is the code
contract; the facts that bite are these.

- **Snap in a projected CRS, never in degrees.** At this latitude a degree of
  longitude is only ~0.72 of a degree of latitude on the ground, so a degree-space
  "nearest segment" is ~28% anisotropic and can pick the wrong road.
  `corridors.project_network` defaults to the network's own UTM zone
  (`estimate_utm_crs`); snap distances are reported in **feet**.
- **The nearest segment may be the wrong direction.** Opposing carriageways share
  the road's geometry (see the direction section), so `build_chain` tries the *k*
  nearest candidates **within `max_snap_feet`** (default 500 ft) at each end and
  keeps the pair that actually **reaches** the target, cheapest snap first. The
  distance guard is load-bearing: without it a far-but-connecting candidate beats
  the true snap, and a 2-mile route can collapse onto one nearby segment while
  still reporting `reached_target=True`.
- **Try more candidates than feels necessary.** The default is the 8 nearest
  segments per endpoint, and that is empirical: at a signalised arterial
  intersection the cross-street approaches, the opposing carriageway and the turn
  stubs routinely put **six** segments nearer the query point than the one the
  route runs on. At `k = 4` two of ten measured routes silently returned the wrong
  chain — SH-69 SB stopped after 0.41 mi (against 7.17 mi northbound) and VSL SB
  walked 8.07 mi past its target; at `k = 8` both resolve to their northbound
  mirror image (7.167 mi and 3.007 mi) with zero missing segments.
- **A chain that doesn't connect is data, not an exception.** `reached_target` +
  `stop_reason` (`target` / `dead_end` / `off_network` / `cycle` / `max_steps`)
  ride on the result. `off_network` means the next `NextXDSegI` isn't in the
  network *subset* that was loaded — widen the bbox/id filter, it is not a
  terminal segment.

**The trim, and what we do about it.** `NextXDSegI` walking returns **whole**
segments, but a query point lands wherever it lands — usually mid-segment. The
un-trimmed chain therefore overshoots the requested extent, and asymmetrically,
because the two directions of a road are cut at different places:

| chain (measured 2026-09-17) | whole segments | requested extent | ratio |
|---|---|---|---|
| Franklin EB | 2.993 mi | 2.763 mi | 1.08 |
| Franklin WB | 3.453 mi | 2.764 mi | 1.25 |
| VSL NB AM   | 3.635 mi | 3.006 mi | 1.21 |

Franklin EB and WB cover the *same physical extent*; untrimmed they differ by
0.46 mi (0.57 mi of it a single overshooting end segment), and trimmed they agree
to **0.001 mi**. A travel-time comparison scored on the untrimmed chain is
comparing two different roads' worth of pavement.

**Decision (Session 34): prorate by default for comparison work; report always.**
`build_chain` always reports `trim_start_miles` / `trim_end_miles`, the per-member
`in_extent_fraction`, and the `chain_miles` ÷ `requested_miles` ratio — nothing is
silently rescaled. `corridors.chain_travel_time(df, chain)` then credits each end
segment the covered fraction of its travel time (`prorate=True`, the default), and
reports `Length(Miles)` as the requested extent.

- **Assumption, stated:** proration assumes **uniform speed within a segment**.
  That is weakest exactly where it is used most — an end segment cut at an
  intersection, where the delay is concentrated at the stop bar. Proration removes
  a length bias (which is large, systematic, and directional) at the cost of a
  within-segment distribution assumption (smaller, and unbiased in sign).
- `prorate=False` gives the whole-segment sum, which is the right choice when the
  chain's own extent is the thing being described (e.g. a corridor defined *by*
  its segments) rather than compared against an outside route.

**Missing-segment accounting.** `corridors.chain_coverage(chain, df)` returns one
row per member with `Miles`, `extent_miles`, `n_obs`, and `observed`, plus
`attrs`: `n_missing`, `missing_miles`, `observed_miles`, `miles_covered_fraction`
(and, when the frame carries `CValue`, `n_imputed` / `imputed_fraction` per member).
Chain travel time keeps the **complete-set rule** over exactly the chain's members
against the **requested** membership (see "Requested vs. observed membership"
above), so a sometimes-missing segment drops the timestamp and a never-present one
marks the sum `short` rather than passing as whole. There is **no silent fallback**
to "whatever reported" — the 2026-09-17 outside pass compared 17 of 20 Eagle Rd NB
segments with a `17/20` string as the only evidence, and the absent members were
the short signalised stubs where the delay lives (0.25–0.29 mi each).

**The length a short chain reports (Item 31).** `Length(Miles)` is the extent
**actually summed** — the requested (or whole-segment) miles less any member with no
data — with `requested_length_miles`, `missing_length_miles` and a `length_basis`
label (`"requested"` / `"observed"`) beside it. Before this, the length credited the
full request even where an unobserved **end** segment had been prorated into a sum it
contributed nothing to, so every derived `Corridor Speed(miles/hour)` on such a route
was overstated by that fraction. Measured on the 2026 D3 export, 6 of the 15
validation chains are short, and the correction is:

| route | members absent | missing (in-extent) | requested | speed was overstated by |
|-------|---------------:|--------------------:|----------:|------------------------:|
| Eagle Rd NB | 3 of 20 | 0.149 mi | 6.938 mi | 2.1% |
| Eagle Rd SB | 2 of 19 | 0.119 mi | 6.939 mi | 1.7% |
| Franklin EB | 1 of 10 | 0.128 mi | 2.763 mi | 4.6% |
| Franklin WB | 1 of 9 | 0.128 mi | 2.764 mi | 4.6% |
| Franklin EB – No Mid | 1 of 8 | 0.128 mi | 2.100 mi | 6.1% |
| Franklin WB – No Mid | 1 of 8 | 0.128 mi | 2.101 mi | 6.1% |

The other nine chains (SH-69 both ways, all four VSL extents, Garden Valley–HSB,
Cascade–HSB, HSB–Cascade) observe every requested member and are unchanged.
**Note the units of "missing":** the Session 37 review quoted 0.290 mi for Eagle Rd NB
and 0.250 mi for Franklin EB — those are the absent members' **whole-segment** lengths.
A prorated sum is only ever credited the **in-extent** miles, which for a trimmed end
segment is roughly half of that, so the correction is the smaller figure above.

## External travel-time reference (TT Logger workbook, Item 29)

A second, independent measurement of the same corridors: a Google-Maps-style
travel-time logger, delivered as an `.xlsx` with **one sheet per route**.
`reference.py` is the code contract; `agreement.py` compares it to INRIX.

**Sheet layout.** Five header rows (`Origin`, `Destination`, `Days`, `Start`,
`End`) in columns A/B, a blank row, then a table starting at the row whose first
cell is `Timestamp`:

| column              | notes                                               |
|---------------------|-----------------------------------------------------|
| `Timestamp`         | **naive local wall clock** — no offset in the cell   |
| `Travel Time (s)`   | the precise value; minutes are derived from this     |
| `Travel Time (min)` | the same number, already rounded                     |
| `Extra TT (Min)`    | the provider's own delay figure — see below           |

**Header dialects — the same workbook mixes them.** `Origin`/`Destination` is
**either** a `lat,lon` pair (with or without a space after the comma) **or** a
place name — and a place name *also* contains a comma ("Garden Valley, ID"), so
the parse is by value, not by separator. A place-name route is whatever the
provider's geocoder chose, so it **cannot be chain-matched** to XD segments; the
route table marks this as `chain_matchable` (3 of 15 sheets are city-to-city).
`Start`/`End` arrives as a real time, as an `"HH:MM"` string, **or as an Excel day
fraction** (`0.625` → 15:00, from a cell formatted as a time but stored as a
number). These windows are what the sheet *claims*; they sit about an hour off the
observed timestamps, which is why clock agreement is measured, not assumed.

**Timezone.** The timestamps are naive local wall clock, so the zone is supplied
explicitly (`America/Denver`) and localized DST-correctly — never sliced off a
string. This matters in both directions: the logger samples straight through the
**fall-back fold** (01:15 and 01:45 on 2025-11-02 occur twice, on 9 of 15 sheets),
so those samples are dropped and **counted** in `attrs['n_ambiguous_dropped']`
rather than being assigned a guessed offset.

**Cadence and binning.** Samples land roughly **every 30 minutes**, about 50 s
past a quarter hour. They are **floored** to the bin, never rounded: an INRIX bin
is labelled by its *start*, so a sample belongs to the bin containing it. On the
current workbook the two rules disagree on 17 of 62,321 samples (a sample at
:58:50 floors to `:45` and rounds to the next hour) — small, but the rule is what
the label means, not what the arithmetic happens to agree on today. Several
samples can share a bin; `agreement.match_bins` averages them and keeps
`n_ref_samples`.

**`Extra TT (Min)` is load-bearing, not decoration (Item 33).** Subtracting it from
the travel time gives that sheet's **no-traffic duration** — the provider's own
open-road estimate for the path *it* routed — and on this workbook that difference
is a **constant per sheet**: 1,936 of 1,936 matched SH-69 SB bins carry exactly
11.3167 minutes, 1,937 of 1,937 Eagle Rd NB bins carry 11.5383, across six months
and a 05:00–17:59 logging window. (The sheets with more scatter still put 98.5% or
more of their bins on one value; `agreement` takes the **median** and reports
`ref_no_traffic_spread` beside it, so a sheet the provider re-routed mid-record is
visible rather than silently averaged.) Two consequences:

- It is the only way to tell **where a level gap comes from**. `free_flow_ref` is
  the reference's travel time at its own 10th percentile, and the no-traffic
  duration says how much of that is still delay. See the Item 33 section below.
- It is a **per-sheet** number, not a pure property of the path: VSL NB AM and VSL
  NB PM resolve to the *same* chain and the same two endpoints, yet carry 4.1833
  and 5.5765 minutes. Read it as "the provider's open-road duration for this
  sheet's route as of when this sheet was logged", and do not compare it across
  sheets that were not logged together.

`agreement.match_bins` carries the column through the join as
`Reference Extra Travel Time(Minutes)`; a sheet without it is skipped rather than
filled, and every column derived from it is then NaN.

**Reference-side gates (run these before comparing anything).**

- `nesting_gate` — a sub-route cannot exceed the route that contains it. Measured
  on this workbook: "Franklin WB - No Mid" exceeds its own full "Franklin WB" in
  **99.9% of 4,524 shared bins** (6.40 vs 5.68 min) — physically impossible, and
  previously published as a 32.2% INRIX error. The EB pair is clean (0%), so it is
  the reference that is wrong, not the check. Nesting is derived from the
  assembled **chains** (segment-set containment), not from sheet names.
- `coverage_gate` — per-route first/last/`n_days` plus, against a study window,
  `days_in_window` / `window_covered_fraction` / `covers_window`. One route's
  coverage cannot be described by another's banner.
- `lag_scan` / `lag_summary` — shift the reference ±90 min and find the best
  alignment. The statistic is `sd_diff` (**bias removed**), because a lag scan
  reads shape: on raw RMSE a large systematic bias moved the apparent best lag to
  +30/+60 min on half the routes, while the bias-free scan puts **every** route at
  lag 0. That is the evidence the two clocks agree.

**Agreement statistics** (`agreement.compare`, one row per route): the **delay
slope** and the **free-flow level gap**, each with a day-blocked 95% CI (Item 32 —
see the section below); bias with a **day-blocked** 95% CI (15-minute bins are
autocorrelated; the per-bin interval is reported beside it as `*_naive`, and
`ci_width_ratio` shows the inflation — up to 2.4× on these corridors), MAE / RMSE /
MAPE, **SD ratio**, **delay ratio** (mean delay above *each source's own* free-flow
percentile, kept as a secondary statistic), Bland-Altman limits of agreement, and
correlation reported last on purpose. `independent_totals` reports
distinct days and **distinct pavement** beside the naive sums, because overlapping
sub-routes of one road are not independent evidence: on ten routes here, 28,431
matched bins span only 4,411 distinct quarter hours, and 43.84 summed chain-miles
are 33.63 distinct miles.

**Source hygiene (decided Session 35).** The workbook is **raw logged input** and
grows every week the logger runs, so it follows the raw-export rule: gitignored
(`/TT Logger.xlsx`), kept locally, never committed. Tests build a synthetic
workbook of the same shape in `tmp_path`; the real-file tests skip when it is
absent, exactly like the XD shapefile tests.

## INRIX vs. the external reference: arterial delay compression (Item 30)

The finding from the 2026-09-17 validation run (`scripts/build_validation_report.py`
over the 2026 D3 15-minute export, 15 TT Logger routes, 38,873 matched bins):
**on signalised arterials INRIX reports roughly half to two-thirds of the delay the
external reference does, while on rural free-flow highway the two agree to within
about a minute over 50 miles.**

| route | bias (INRIX − ref), min | day-blocked 95% CI | delay ratio | SD ratio | MAPE |
|---|---|---|---|---|---|
| Eagle Rd NB | −8.38 | [−8.47, −8.23] | 0.54 | 0.58 | 37.6% |
| Eagle Rd SB | −5.46 | [−5.55, −5.34] | 0.54 | 0.57 | 28.7% |
| SH-69 SB | −6.04 | [−6.12, −5.93] | 0.37 | 0.36 | 38.2% |
| SH-69 NB | −1.43 | [−1.46, −1.40] | 0.64 | 0.66 | 12.2% |
| Franklin EB / WB | −1.08 / −1.08 | [−1.10, −1.05] both | 0.53 / 0.57 | 0.57 / 0.65 | ~19% |
| VSL section (4 sheets) | −1.11 … −3.05 | see report | 0.59–0.95 | 0.60–0.88 | 18–30% |
| **Cascade→HSB (rural, 50.8 mi)** | **−0.16** | **[−0.84, +0.55]** | **1.08** | 0.76 | **2.5%** |
| **HSB→Cascade (rural, 50.8 mi)** | **+0.48** | **[−0.20, +1.19]** | **1.09** | 0.45 | **2.6%** |

Across the 11 usable arterial routes the **delay ratio** (mean delay above *each
source's own* 10th-percentile free-flow) has a **median of 0.59** (range
0.37–0.95) and the **SD ratio** a median of **0.62** (range 0.36–0.88). The two
agreeing matters: the SD ratio is symmetric, so this is compression of the
congested tail, not regression dilution. Both rural routes' CIs include zero — INRIX
and the reference do not measurably disagree on rural free-flow travel time, and
their delay ratios sit slightly **above** 1.

**Two consequences to carry into any study.**

1. **A before/after evaluation scored on INRIX will report roughly half the effect
   size in minutes** that this reference would credit, on signalised arterials —
   measured as a slope of **0.536** (see *The compression is a slope* below).
   Report INRIX-derived minutes of delay saved as a lower bound and name the
   source. A *relative* change may survive the compression (if both periods are
   scaled by the same factor, the ratio is preserved) — but that is a separate
   claim, and nothing measured here establishes that the factor is constant across
   congestion levels.
2. **The published explanation for the largest biases is not supported.** The
   outside pass stated that its spatial analysis "proves" the Eagle Rd / SH-69 SB
   gaps are query-geometry mismatch — reference queries reaching south of I-84 and
   capturing off-ramp queues. Measured here: the query points snap **5/4 ft**
   (Eagle Rd NB), **4/5 ft** (Eagle Rd SB), **18/2 ft** (SH-69 NB) and **64/18 ft**
   (SH-69 SB) from their chain ends — there is no extra extent. And the gap is
   already there at free flow: at **05:00** SH-69 **SB** runs 3.94 min below the
   reference while **NB** — the same endpoints reversed — is 0.37 min off. No
   queuing story explains that. Something route-specific may be happening at that
   interchange; it has not been identified.

**What the comparison does not say.** The reference is one commercial provider's
route-level estimate, not ground truth: this measures *disagreement* and locates
it, it does not adjudicate. Two routes are excluded from the headline by the gates
and kept visible with their reasons — "Franklin WB - No Mid" (its reference data
exceeds its own containing route in 99.9% of shared bins) and "Garden Valley-HSB"
(the chain dead-ends after 11.08 mi, so the INRIX side is not that route).

**Extent for the place-name routes.** The three rural sheets name cities rather
than coordinates, so `reference.route_endpoints` refuses them. They are carried via
`corridors.chain_between_segments` from an **operator-stated pair of terminal
segments** (`scripts/d3_place_name_routes.json`): whole end segments, no trim, no
proration, `snap_*_feet` NaN. The two ID-55 chains reproduce the corridor at
**50.76 / 50.78 mi**. Rural numbers therefore rest on an operator's extent
assertion, which the arterial numbers do not.

**Cadence, stated because it was measured.** The reference's median sample gap on
this workbook is **30 minutes** (~25 samples on a typical logged day) against
INRIX's 15-minute bins; per-route coverage of the export window runs 30–71%. A
report that prints "Data Resolution: 15-Minute Intervals" over both sources is
describing only one of them.

## The compression is a slope, not an offset (Item 32)

Item 30 reported the compression as a **delay ratio** — `mean(delay_inrix) /
mean(delay_ref)`, each above its own 10th percentile, "median 0.59 (0.37–0.95)".
That number stands as published, but it is not the one to quote. It divides two
small means, it is the only statistic in the report that carried no interval, and
on a low-delay route it is wild: **VSL SB PM reads a ratio of 2.34 against a
regression slope of 0.685 on the same bins** (r² 0.168) — a route where INRIX
compresses delay, reported as showing more than twice as much of it.

The primary statistic is now a **per-route regression of INRIX delay on reference
delay**, each above its own free-flow percentile, with a **day-blocked** interval
on both coefficients (`agreement.delay_regression`, folded into
`agreement.compare`). The free-flow **level gap** is reported as a separate
column, because a single `bias` fuses two effects that do not have the same
consequence.

| route | slope | day-blocked 95% CI | intercept, min | free-flow gap, min | gap 95% CI | r² | delay ratio |
|---|---|---|---|---|---|---|---|
| Eagle Rd NB | 0.520 | [0.505, 0.536] | +0.018 | −5.36 | [−5.53, −5.21] | 0.862 | 0.52 |
| Eagle Rd SB | 0.514 | [0.497, 0.530] | +0.084 | −2.67 | [−2.77, −2.53] | 0.890 | 0.53 |
| SH-69 NB | 0.581 | [0.536, 0.626] | +0.056 | −0.82 | [−0.92, −0.76] | 0.775 | 0.62 |
| SH-69 SB | 0.250 | [0.208, 0.292] | +0.226 | −4.69 | [−4.81, −4.59] | 0.417 | 0.37 |
| Franklin EB | 0.453 | [0.424, 0.482] | +0.082 | −0.53 | [−0.62, −0.47] | 0.603 | 0.55 |
| Franklin WB | 0.536 | [0.512, 0.560] | +0.083 | +0.08 | [0.00, +0.20] | 0.604 | 0.62 |
| VSL NB AM | 0.621 | [0.578, 0.663] | +0.092 | +0.23 | [+0.18, +0.32] | 0.872 | 0.68 |
| VSL NB PM | 0.570 | [0.529, 0.611] | +0.158 | −1.04 | [−1.25, −0.82] | 0.768 | 0.64 |
| VSL SB AM | 0.875 | [0.792, 0.959] | +0.047 | +0.13 | [+0.08, +0.19] | 0.872 | 0.92 |
| VSL SB PM | 0.685 | [0.512, 0.859] | +0.614 | +0.25 | [+0.14, +0.40] | 0.168 | **2.34** |
| Franklin EB - No Mid | 0.502 | [0.467, 0.537] | +0.087 | −0.38 | [−0.46, −0.32] | 0.401 | 0.68 |
| *Franklin WB - No Mid* (gated out) | 0.288 | [0.262, 0.314] | +0.205 | −1.71 | [−1.86, −1.53] | 0.393 | 0.51 |
| *Garden Valley-HSB* (gated out) | 0.672 | [0.544, 0.800] | +0.768 | +2.23 | [+2.14, +2.33] | 0.778 | 1.22 |
| **Cascade→HSB (rural)** | **0.505** | **[−0.040, 1.050]** | +1.561 | −0.36 | [−0.48, −0.24] | 0.442 | 1.09 |
| **HSB→Cascade (rural)** | **0.922** | **[0.619, 1.224]** | +1.065 | +0.32 | [−0.00, +0.54] | 0.289 | 1.93 |

Across the **11 usable arterial routes** the slope has a **median of 0.536** (range
0.250–0.875). Both rural routes' slope intervals **span parity** — as their bias
CIs span zero — so on rural free-flow highway there is no measurable compression to
report, and their ratios (1.09, 1.93) are the same small-denominator artefact seen
on VSL SB PM rather than evidence of the opposite effect.

**The intercepts are the point.** They run **+0.018 to +0.158 minutes** across the
**eight** arterials whose fit explains more than half the variance (median +0.084
over all eleven) — near zero. A route whose regression explains little, such as VSL
SB PM at r² 0.168, has an intercept that means correspondingly little, which is why
r² sits in the scorecard beside the slope. The disagreement is therefore
**multiplicative in delay, not a fixed offset**, and *a multiplicative factor does
not cancel in a before/after difference*. An intervention that removes **4 real
minutes** of delay scores as roughly **2.1 minutes** on INRIX at the median slope.
This is the strongest form of the Item 30 consequence, not a softening of it: a
constant offset would have subtracted out of a before/after comparison, and this
does not.

**A near-zero intercept is a real finding, not an artifact of the method.** Because
each source's delay is measured above its *own* free flow, a constant added to
every bin is absorbed into that source's free flow and cannot appear as an
intercept. An intercept only arises from an offset that applies to congested bins
and not to free-flow ones — so measuring ≈0 means there is no such fixed penalty,
and the whole disagreement rides on the slope. (Tested both ways in
`tests/test_agreement.py`: a pure compression must return an intercept of exactly
zero, and a fixed congested-bin penalty must be visible as a positive one.)

**The level gap is reported separately, and it is directional.** At each source's
own 10th percentile, Eagle Rd NB sits **−5.36 min** and SH-69 SB **−4.69 min**
below the reference, while SH-69 NB over the same endpoints reversed is **−0.82
min**. A level gap that large is not a compression, and unlike the slope it *would*
cancel in a before/after difference. Fusing it into `bias` is what made those two
routes look like the worst compression in the study when SH-69 SB's slope (0.250)
and its level gap are two separate things.

> **Read the next section before quoting a level gap.** This paragraph originally
> called the 10th percentile "the quietest conditions measured", which is true of
> INRIX and **not** of the reference: Item 33 measured the reference's own delay at
> that percentile (1.68–4.04 min on these routes) and split the gap accordingly.
> Both parts are columns now — `ref_free_flow_delay` and `free_flow_gap_static`.
> The gaps above are unchanged; what they mean is not.

**How the intervals are built.** The slope and intercept get a **day-clustered**
sandwich (CR1, *t* on G − 1 days) — the regression analogue of the bias CI's
t-interval over per-day means, and for the same reason: bins inside a day are not
independent. The per-bin interval is kept beside it as `*_naive`, and the honest
one is **2.0× wider at the median** (up to 3.2×). The free-flow level gap is a
difference of two quantiles, which has no closed-form clustered SE, so its interval
is a **day-block bootstrap** — resample whole days, recompute both pooled
percentiles, take the 1000-draw percentile interval (fixed seed, so a re-run
reproduces its own numbers). The bootstrap estimates the **pooled** gap that is
reported; a t-interval over per-day gaps was tried first and rejected, because it
estimates a different quantity and put Eagle Rd NB's published −5.36 outside its
own interval.

**Provenance of the numbers in this table.** They are computed over the
**28,340-bin matched set the outside pass shipped**
(`out/inrix_vs_google_matched_timeseries.csv`), not over a fresh rebuild run:
`TT Logger.xlsx` is gitignored raw logged input and is not on this machine
(Session 43). The slopes reproduce Session 37's independently-derived values to
±0.015, and the free-flow gaps match Item 33's scoping figures exactly. The bias /
delay-ratio table in the Item 30 section above comes from the **rebuild's** 38,873
matched bins, so the two tables are not bin-for-bin comparable; **regenerate both
from `scripts/build_validation_report.py` once the workbook is back on this
machine.**


## The directional level gap is on the reference side (Item 33)

Item 32 reported the free-flow level gap separately from the slope and left one
thing unexplained: at each source's own 10th percentile, **SH-69 SB sat −4.69 min
below the reference while SH-69 NB over the same endpoints reversed sat −0.82**,
and Eagle Rd NB sat −5.36. Two reviews had confirmed the *endpoints* coincide
(snap distances of 4–64 ft) and concluded nothing, because a snap distance proves
only that the two routes start and finish in the same place — not that they cover
the same ground in between. Item 33 tested the ground.

**The INRIX path is identical in both directions.** Measured on the assembled
chains, not asserted:

- The SH-69 chains are **member-for-member mirrors** — every segment length on one
  appears on the other (one pair of members breaks at a different point: 0.198 +
  0.806 against 0.500 + 0.503, the same 1.003 miles) — and the two requested
  extents are **7.113 mi both ways**. Eagle Rd: 6.938 against 6.939 mi.
- Their geometry is the same pavement. Sampling 201 points along the SH-69 NB
  chain, the **median distance to the SB chain is 0 ft** (the XD network carries
  one centerline for both directions over most of it) and the **maximum is 56 ft**,
  at the north end where the carriageways split. Eagle Rd runs as two parallel
  carriageways a median **43 ft** apart over its whole length. Terminal vertices
  coincide **exactly** at SH-69's south end and at both ends of Eagle Rd; SH-69's
  two chains end **63 ft** apart at the north end, which is the carriageway
  separation there and matters below.
- Per-member INRIX travel time is symmetric. Summing each member's own 10th
  percentile over the 05:00–17:59 window, **SH-69 NB is 8.540 min against SB's
  8.440** — 6 seconds apart end to end, and no slice of the corridor differs by
  more than 0.04 min. (Eagle Rd: 9.680 against 9.280.) Nothing is concentrated in
  one member, which is what a segment whose XD extent disagreed with the roadway
  would look like.

**The reference's own numbers carry the whole asymmetry, and they carry it in a
number that contains no traffic.** `Travel Time − Extra TT` is the provider's
no-traffic duration for the path it routed, and it is constant per sheet (see the
TT Logger section above). Over the identical 7.113 miles it reads **11.3167 min
southbound against 8.2667 northbound** — 37.7 mph against 51.6 mph. That 3.05-minute
difference is in a static estimate: it cannot be congestion, a probe-sampling
artefact, or a measurement of anything. The reference's *fastest trip in the whole
record* says the same thing — over 1,936 matched bins from 2026-01-01 to 2026-06-25
its quickest southbound run was 11.317 min and its quickest northbound run 8.267,
while INRIX's quickest were 8.00 and 8.12.

**So the level gap splits, and `agreement` now reports both parts** —
`ref_free_flow_delay` (the delay the reference itself reports at the percentile the
gap is quoted at) and `free_flow_gap_static` (what is left), with
`free_flow_gap == free_flow_gap_static − ref_free_flow_delay` by construction:

| route | free-flow gap | ref delay at that percentile | static gap | ref no-traffic | mph | INRIX free flow | mph |
|---|---|---|---|---|---|---|---|
| **SH-69 SB** | **−4.69** | 2.32 | **−2.37** | 11.32 | 37.7 | 8.95 | 47.7 |
| **SH-69 NB** | **−0.82** | 1.68 | **+0.86** | 8.27 | 51.6 | 9.13 | 46.7 |
| **Eagle Rd NB** | **−5.36** | **4.04** | −1.32 | 11.54 | 36.1 | 10.22 | 40.7 |
| Eagle Rd SB | −2.67 | 2.27 | −0.40 | 10.13 | 41.1 | 9.73 | 42.8 |
| Franklin EB | −0.53 | 0.58 | +0.06 | 4.08 | 40.6 | 4.14 | 40.0 |
| Franklin WB | +0.08 | 0.62 | +0.70 | 4.05 | 40.9 | 4.75 | 34.9 |
| Franklin EB - No Mid | −0.38 | 0.42 | +0.04 | 3.18 | 39.6 | 3.22 | 39.1 |
| *Franklin WB - No Mid* (gated out) | −1.71 | 1.09 | −0.62 | 4.51 | 27.9 | 3.89 | 32.4 |
| VSL NB AM | +0.23 | 0.42 | +0.65 | 4.18 | 43.1 | 4.83 | 37.3 |
| VSL SB AM | +0.13 | 0.20 | +0.33 | 4.43 | 40.6 | 4.76 | 37.8 |
| VSL NB PM | −1.04 | 2.21 | +1.18 | 5.58 | 32.3 | 6.75 | 26.7 |
| VSL SB PM | +0.25 | 0.27 | +0.52 | 6.20 | 29.1 | 6.72 | 26.8 |

**The two unexplained routes turn out to be two different things.**

- **Eagle Rd NB is mostly not a level gap at all.** 4.04 of its 5.36 minutes is
  delay the reference *itself* reports at its own 10th percentile: on a corridor
  congested through the whole logging window, the 10th percentile is not free
  flow. Its static remainder, −1.32 min (36.1 mph against INRIX's 40.7), is an
  ordinary disagreement about what "open road" means on a signalised arterial —
  the provider's open-road estimate carries typical signal delay and INRIX's
  `Ref Speed` does not. Nothing here needs explaining by the road.
- **SH-69 SB is a route difference — on the reference side.** Half its gap
  (−2.37 of −4.69) survives removing the reference's own delay, and it is
  **directional against a northbound +0.86** over mirrored pavement. INRIX's two
  directions differ by 0.18 min; the reference's no-traffic durations differ by
  3.05. The reference's southbound route is not the reverse of its northbound one.

**The mechanism, and why it is not yet proven.** The southbound sheet's origin
snaps **63.9 ft** from the southbound roadway, while the northbound sheet's
destination snaps **1.5 ft** from the northbound one — and the two carriageways are
**63.2 ft** apart there, so a single coordinate 1.5 ft from the northbound
centerline would sit 61.7–64.7 ft from the southbound one. The measurement is
consistent with **one logged coordinate, lying on the northbound carriageway**, used
as the northbound destination and the southbound origin. It is the **only** origin
of the twelve coordinate-snapped routes that lands on the opposing carriageway
(the next largest true carriageway offset is VSL NB's 10.2 ft against a 20.6 ft
separation). A router given a southbound trip from a point on the northbound side
must first reverse direction, and that endpoint sits ~90 ft south of the I-84 WB
ramp terminal and ~400 ft south of the north ramp terminal — a reversal there is
extra path, on ramp terminals INRIX is not being asked about.

**What this does not establish.** The size of that detour. The logger records the
provider's travel time, not the route it returned, so nothing in this workbook says
how far the southbound trip actually drove; a U-turn at the north ramp terminal is
~800 ft of extra path plus a signal, which is short of 3.05 minutes, so either the
router takes a wider loop or something else contributes. **What would settle it:**
re-log SH-69 SB with an origin placed on the southbound roadway, or capture the
provider's returned distance/polyline alongside its duration. Ruled out and
recorded so they are not re-examined: a directional sampling artefact (the NB and
SB sheets are logged in **exactly the same 1,936 bins**, same hours, same days), a
chain-extent mismatch (7.113 mi both ways), a member-level INRIX defect (no slice
of the corridor differs by more than 0.04 min between directions), and a mid-record
route change (`ref_no_traffic_spread` is 0.0000 on both SH-69 sheets).

**Consequences for what has been published.** The Item 32 wording — the level gap
measured "at the quietest conditions measured" — is **wrong for the reference
side** on a congested corridor, and is corrected here: read `free_flow_gap_static`
for a statement about the road and `ref_free_flow_delay` for how far the percentile
was from free flow. None of it is an INRIX limitation: **the delay-compression
slope is untouched by this finding**, because the slope is fitted on each source's
delay above its own free flow, and a directional difference in the reference's
baseline is absorbed into that baseline. SH-69 SB's slope of 0.250 is the one
number on that route that the routing difference *could* still be distorting — its
reference delay includes delay from pavement INRIX was never asked about — which is
another reason to read the slope's r² (0.417 there, against 0.775 northbound)
beside it.


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

## District-wide screening (`screen.py`, Item 35)

Screening asks the question that comes *before* a corridor study: across every segment
in an area, which corridors are worst. `segment_screen` reduces an area's whole time
series to **one row per segment** — per-named-window mean travel time and speed, the
free-flow `ref_speed`, and the CValue accounting — computed in DuckDB against the
store's `obs_<area_key>` table. The 2026 D3 area (**91,054,384 rows**, 3,905 segments)
screens in **≈12 s**.

**Named windows are the `timebins` vocabulary.** A `PeakWindow` holds a
`parse_time_bin` range string plus `parse_day_of_week` day specs, and its SQL predicate
is **derived from that parse** — so a window means here exactly what it means in the
GUI's time-of-day slider: half-open `[start, end)`, overnight ranges wrap, all-seven-
days is a no-op gate, and DuckDB's `isodow` (1=Mon) maps onto pandas' `dayofweek`
(0=Mon). `PeakWindow.filter()` is the same window as the pandas filters, and the tests
assert the two paths agree row for row. The presets:

| name | window | days | peak? |
|------|--------|------|-------|
| `am` | 07:00–09:00 | Mon–Fri | yes |
| `pm` | 16:00–18:30 | Mon–Fri | yes |
| `midday` | 10:00–14:00 | Mon–Fri | no (context) |
| `night` | 22:00–05:00 | every day | no (the closest thing to an observed free-flow window) |

Only `peak` windows compete for a corridor's `worst_peak`. They are **presets, not
constants** — pass your own.

**The CValue gate is applied, recorded, and costed.** `CValue > 80` by default (the
strict comparison `io.filter_cvalue` uses), recorded on `attrs['cvalue_threshold']`,
with the **surviving-row share carried per segment and per window** so a screened-in
segment built from backfill is visible rather than indistinguishable. On the 2026 D3
export: **39.4%** of rows carry a null CValue, the gate drops **40.1%** of all rows,
**990 of 3,905** segments lose more than half their AM rows and **84 lose all of
them** — yet across ten real corridors it moves delay by **≤0.33% on the eight urban
ones** and 0.60% / 1.58% on the two rural SH-55 North extents (the backfill-heavy ones
this file already flags). It changes almost nothing, *visibly*, which is the argument
for gating explicitly rather than not at all. A gate on an export with no `CValue`
column raises; `cvalue_threshold=None` screens ungated, on purpose.

A 15-minute export gives **8 AM bins per weekday per segment**; the D3 export's 173
weekdays therefore anchor at **1,384 AM observations per segment**, which 3,902 of its
3,905 segments hit exactly.

**Ranking (`rank_corridors`)** takes the screen plus assembled `ChainResult`s and
reports `delay_min`, `tti`, `delay_per_mile`, `vhd`, `vhd_per_mile` and `worst_peak`
per corridor × window, computed through `speed.segment_delay` and
`aadt.vehicle_hours_of_delay` — so the AADT caveat above travels with the number
instead of being restated as "veh-hrs/day". (Item 57: given `bins=` and `curves=`,
`vhd` is curve-weighted instead, and the index stays as `vhd_index`; see
"Curve-weighted VHD".) Three definitions, all recorded on `attrs`:

- **Delay is floored once, at the segment** (`attrs['delay_floor'] == 'segment'`). A
  segment faster than its free-flow reference contributes 0 and can never pay for a
  congested one, and the *same* floored values feed `delay_min` and `vhd`, so the two
  cannot disagree about a corridor.
- **Only observed pavement is credited.** `miles` sums the in-extent miles of members
  the screen actually observes; a member it never saw lands in `missing_miles`, so
  `tti` and `delay_per_mile` are not deflated by pavement that contributed no travel
  time.
- **End segments are prorated** by the chain's extent fractions, the same proration
  (and the same uniform-speed-within-a-segment assumption) as `chain_travel_time`.

**What an export's segment set is — and is not.** A district download is a *query*,
and the D3 one was built from the numbered-route lists: 3,905 segments, every one of
which does carry observations (measured straight off the three zips: 91,054,384 rows,
3,905 distinct `Segment ID`s, 59.9% of rows surviving `CValue > 80`). But segments with
a null `RoadNumber` were never asked for, so a corridor that runs partly on an
unnumbered city street is **requested but not observed** — the Front St leg of the
downtown Boise couplet resolves as a 12-segment chain of which the three `RoadNumber`
null `W Front St` members west of 15th St have no rows at all, leaving it **73.1%
covered by mileage**. That is a finding about the download, not about the road, and it
is why catalogue acceptance tests coverage as well as connectivity.

**A `#` anywhere in a value truncates its own row on the way back in (Item 46).** Every
CSV these runners write carries the run's provenance as a leading block of
`# key: value` lines, and the obvious way to skip it — `pd.read_csv(comment="#")` —
treats `#` **anywhere** in a line as the start of a comment. A corridor named after an
interchange ("US-95 IC #12") therefore arrives with every column after the name blank:
ranked in the right place, all metrics null. Skip the header by **counting** its leading
`#` lines instead, and keep `#` out of generated names.

## Recurring-congestion corridor extraction (`screen.py`, Item 43)

Extracting corridor candidates directly from congestion patterns rather than from
hand-drawn landmarks and municipal borders. A *candidate corridor* is a maximal
contiguous run of segments that are recurrently congested, with state-route junctions
used to tidy endpoints only when the data already lands nearby.

### Recurrence vs Mean: distinguishing queues from construction fortnights

Screening via `segment_screen` averages over an entire export's date span. That collapses
temporary anomalies into the same metric as daily queues: a fortnight of construction
with TTI = 3.0 and 17 normal days (TTI = 1.0) produces the exact same mean TTI (1.30)
as a commuter facility congested every single weekday at TTI = 1.30.

`segment_recurrence` computes in DuckDB:
1. **Per-day reduction**: For each `(Segment ID, window, local_date)`, aggregate daily
   mean speed and ref speed, computing daily `TTI = ref_speed / speed`.
2. **Congestion criterion**: A weekday is congested if daily `TTI > tti_threshold`
   (`DEFAULT_TTI_THRESHOLD = 1.25`, 25% longer than free-flow).
3. **Recurrence rate**: Share of observed weekdays meeting the congestion criterion
   (`am_recurrence = n_congested / n_weekdays`).

Under `DEFAULT_RECURRENCE_THRESHOLD = 0.50` ("congested most days"):
- The construction fortnight segment: 3/20 days = 0.15 recurrence → **rejected**.
- The daily queue segment: 20/20 days = 1.00 recurrence → **accepted**.

### Run extraction and topology walking (`extract_congestion_runs`)

- **Topological walk, never geographic sort**: Maximal runs are extended along the
  (repaired) `NextXDSegI` topology in both forward and reverse directions. Geographic
  sorting is strictly prohibited (Item 36's rule).
- **Carriageway boundary guard**: A run stops at an `XDGroup` boundary; different
  carriageways are different facilities and never merge in series.
- **Gap tolerance**: One free-flowing segment between two congested segments does not
  split a corridor. Up to `DEFAULT_GAP_TOLERANCE_SEGS = 1` and `DEFAULT_GAP_TOLERANCE_MILES = 0.5`
  of non-qualifying pavement is bridged. Bridged gaps and total gap miles are recorded
  on `CongestionRun` so the bridging decision is explicit in the output.
- **On-system filtering**: Restricts qualifying seeds and run members to numbered state
  routes via `on_system` (accepts `classify_on_system` DataFrames, boolean Series, or ID sets),
  preventing off-system county roads from forming candidates.

### Endpoint tidying (`tidy_run_endpoints`)

After maximal runs are extracted, endpoints are examined for nearby junctions with
differing `RoadNumber`s within `DEFAULT_SNAP_TOLERANCE_MILES = 0.25`:
- If a state-route junction is found along the topology within tolerance, the endpoint snaps
  and returns `snapped_to` and `snap_distance_miles`.
- If no junction exists within tolerance, the endpoint stays where the congestion data placed
  it (`snapped_to = None`, `snap_distance_miles = NaN`).

### Directional pairing (`pair_directions`)

For each extracted run in one direction (e.g. NB), `pair_directions` searches for a
counterpart on the opposing carriageway (`XDGroup` differs, same `RoadNumber`, opposite
cardinal bearing). If no opposing run is found, it is explicitly reported as unpaired
(`paired = False`), treating one-way congestion as a finding rather than silently creating
a synthetic counterpart.

### Catalogue candidate emission (`emit_candidates`)

`emit_candidates` outputs candidate dicts carrying `id`, `name`, `start_latlon`,
`end_latlon`, and underscore-prefixed metadata (`_recurrence`, `_mean_tti`, `_total_miles`,
`_gaps_bridged`, `_direction`, `_paired`). Crucially, **`description` is omitted**, so
`corridors.parse_catalogue` refuses to load the candidates until a human operator writes down
why that extent is meaningful and reviews the candidate.

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
  an unfiltered read double-counts every road. `load_aadt` filters `Year == year`
  with a pushed-down WHERE.
- **2025 is a separate download (Item 52).** `AADT_2025.zip` holds the same fields
  (its shapefile is still named `Cumulative_AADT`) but only `Year == 2025`, 8,423
  records. It is the default since Item 52 (`aadt.DEFAULT_SOURCE` /
  `DEFAULT_YEAR = 2025`; every script's `--aadt`). It carries US-95 on its new
  alignment south of Moscow (`01540AUS095`, 7,700 AADT), and Reisenauer Rd is no
  longer a US-95 record. Pass `--aadt Cumulative_AADT.zip --aadt-year 2024` to
  reproduce the earlier volumes. Because two downloads can hold the same year, the
  layer cache's sidecar records its **source** (file name and size) as well as its
  year and extent, and a cache built from another download is rebuilt. A sidecar
  from before Item 52 has no source and rebuilds once.
- **Record kinds come from the State Highway System where it is unambiguous (Item 52).**
  The description classifier (Item 34) reads the record's *end points*, and they
  mislead in both directions:
  - I-184's mainline `02410AIN184` reads `JCT I-84 FLYING WYE IC` → `I-84 EB ON RAMP`
    (68,000–84,500), so it was classed as a connector or ramp. Both I-184 carriageways
    near the Flying Wye then took the 5,000–10,000 of the actual connector
    `25273AIN184`.
  - The Broadway Ave interchange ramps `02080AUS020` / `02082AUS020` had no
    description in 2024. In 2025 they carry another road's (`MCBRIDE RD` → `SH-6`,
    `US-20 RAMPS N RIGBY IC`), which made them "mainline". Broadway then took their
    9,500 instead of US-20's 29,500.
  - Other 2025 cases: Sandpoint's new US-95 alignment took 50 (`S WHISPERWOOD WAY
    ROUNDABOUT`); I-90 W at Compressor/Golconda took 30 (`LOCAL RD COMPRESSOR DST`);
    the I-15/I-86 system interchange in Pocatello took 1,500 (`W BRIDGE ST (I-15
    BUS)`).

  The two layers share the `RouteID` scheme. So `itd_layers.classify_records_with_shs`
  (`load_aadt(..., shs=)`) makes a record `mainline` when every SHS piece of its
  route id is roadway (`RoadType` 4), and `ramp` when every piece is a ramp (5).
  Anything else, including local `OH` roads the SHS doesn't draw, keeps its
  description reading. `record_kind_source` says which decided. The scripts pass
  `SHS_Primary.zip` by default (`--shs ''` turns it off); the cache itself stays
  SHS-independent.

  **A record with the ramp signature stays a ramp** (`aadt.ramp_signature`). ITD
  writes a ramp count as a movement with no "to" point (`WB ON COTTERELL IC #222` /
  `NONE`). A mainline record names both ends, even when an end is a ramp
  (`I-84 EB ON RAMP` → `WB OFF FRANKLIN IC #1`). A few ramp counts are filed on a
  roadway route id. The SHS rule would have promoted them, and so did Item 34's
  route roll-up, which relabelled every record on a mostly-mainline route id. Now
  neither does. In 2025 there are three such records, all owner-checked against
  known volumes (Session 67):
  - `01010AIN084` `EB OFF COTTERELL IC#222` (6,100) and `01010DIN084` `WB ON
    COTTERELL IC #222` (6,000), at the I-84/I-86 system interchange. I-84 east of
    the split is 12,000; west of it, where it carries I-86 too, ITD's Declo–Cotterell
    record is 19,500. Both years had put ~6,000 on 2.8 mi of I-84 there.
  - `01543DUS095` (150) on Weiser's W 7th St, which carries 6,000–8,000 (6,300
    from the mainline record beside it).

  Record kinds are recomputed on every load, including a cache hit, so a kind
  frozen into an old cache can't keep an old rule. On the 2025 inventory join the
  SHS classification changes 56 segments (16.8 mi) from the description reading,
  all to the mainline count.
- **2025's descriptions were partly relabelled.** Of the 7,473 records that match a
  2024 record by route id and measure, 47 have a changed description. Most fill a
  blank; some are text from elsewhere. The Boise Broadway ramps `02080`–`02083AUS020`
  (same geometry, measures and counts as 2024) now read `MCBRIDE RD` → `SH-6` and
  `US-20 RAMPS N RIGBY IC`, text that belongs 300 miles away. Nothing moves between
  places, because the join is spatial and each record keeps its own geometry and
  count. Four records changed both label and count, all ramps; the notable one is
  `29207AIN015` at the I-15/I-86 interchange in Pocatello, 20,500 → 1,500.
- **What 2024 → 2025 changed** (Session 67, every D1–D6 inventory segment joined to
  both years with the SHS classification, 17,036 with both):
  - VMT on the inventories rose **+2.9%** (D1 +1.6%, D2 +3.0%, D3 +2.8%, D4 +3.5%,
    D5 +3.4%, D6 +3.2%). A third of segments kept the same count; the middle 80% of
    segment ratios are 0.97–1.08. The biggest real move is Gooding's US-30, 1,900 →
    3,400 on 13 mi.
  - D3's peak VHD, re-ranked on the same peak screen with each year, rose **+2.6%**
    (the ramp-signature fix touches no D3 corridor).
    Adjacent pairs swap: 3/4 (the Boise couplet passes I-184), 9/10, 11/12 and
    22/23.
  - **The classification matters more than the year.** With correct volumes I-184 is
    D3's #3–4 corridor (≈4,800 peak VHD). The description join had it at #6 (3,200)
    in both years.
  - AADT 2025 still has **one centreline per divided highway**. The `D`-carriageway
    route ids the State Highway System draws are mostly absent (I-84's `01010DIN084`
    has 1 record against 59 on the A side; I-15/I-90/I-86 have none). So Item 34's
    divided-highway problem is unchanged. The SHS `D` lines give a future join a
    carriageway geometry to map the counts onto by route id and measure.
  - **A couplet's counts are one-way (Session 71).** ITD records each one-way leg of
    a couplet under its own route ID, `A` for one leg and `D` for the other
    (`01360AIN015` / `01360DIN015`, Pocatello's I-15 BL). The count on each is that
    leg's traffic alone. Where the road splits the layer halves it: 15,000 two-way
    at "END 1-WAY N OF RAMPS", then 7,500 (A) and 7,700 (D). So a divided highway's
    one centreline carries a **two-way** count that both carriageways inherit
    through the join, but a couplet leg carries a **one-way** count. Uncorrected, VHD
    on couplet legs is about half as large, on the same delay, as on every other road.
    **Corrected in Item 53 (Session 72):** `join_aadt` puts `AADT` on the
    two-way-equivalent basis, doubling a one-way count, and keeps the published count
    as `aadt_layer`, with `aadt_basis` / `aadt_basis_reason` per segment. How the layer
    shows a one-way count (`aadt.classify_aadt_basis`):
    - **Words.** `BEG 1-WAY` / `END 2-WAY` open a one-way section, and `END 1-WAY` /
      `END OF 1-WAY` / `BEG 2-WAY` close it. The marker can be in the "from" point
      (`Descriptio`) or the "to" point (`Descript_1`). A record starting where a section
      opens, or ending where one closes, is inside it. The reverse is the two-way road
      beside it: `END 1-WAY @ ELM ST → CEDAR ST` is 21,500. `COUPLET` / `CPLT` name
      junctions, not sections. Nampa's `D` leg starts at `CALDWELL BLVD(END CPLT)`,
      the same point where the `A` leg reads `CANYON ST (BEG 1-WAY)`.
    - **An `A`/`D` pair beside each other** (within 350 m, over at least half the
      record, nearest to a point *along* the other leg rather than one of its ends).
      **The route measures can't be used for this.** Moscow's and Twin Falls' `D`
      legs are measured differently from their `A` legs, so measure overlap pairs
      records a kilometre apart.
    - **Not every `D` record is a couplet leg.** Of the layer's 100 `D` records, about
      30 are roundabouts, ramps, or the second structure at an interchange crossing.
      Two-way roads cross those (Tank Farm Rd, Hawkins Rd, W Broad St in Boise). So a
      count is doubled only on an XD segment that has **no opposing twin** on its own
      street.
    - **The same count on both carriageways is a duplicated two-way count.**
      Sandpoint's 5th Ave has `A` = `D` = 13,000 on identical measures, with 11,500
      and 16,000 either side.
    - **The Boise legs check out.** I-184 ends at 58,500 two-way. The US-20 Spur's
      `A` + `D` are 29,000 + 27,000, which fits. Myrtle and Front legs add to
      49,000–61,500, against Broadway's 29,500 at the east end: traffic disperses
      downtown.
    - **Divided highways drawn as `A`/`D` pairs with their own counts are doubled
      too** (SH-1 at the border: 220 + 210, against 440 two-way). That is the same
      basis.
    **The legs need not sum to the two-way road on either side.** Moscow's US-95 couplet
    also carries SH-8 between 3rd St (SH-8 west) and the south junction (Troy Rd, SH-8
    east). There the legs are 12,500 NB + 12,000 SB, against 14,000 two-way on US-95
    south. North of 3rd, where only US-95 uses it, they are 6,800–10,500 NB + 9,600 SB,
    against 16,000 two-way. Both are one-way counts; a concurrent route joining inside a
    couplet raises the sum (owner, Session 71).
    **Moscow's south-junction segments were not a bad join (Session 72).**
    Segments `1236966046` / `1236966035` are SH-8 Troy Rd itself: `RoadName` `ID-8`,
    two-way, 0.51 mi. So Troy Rd's 13,000 is the right count for them. They entered
    the couplet legs through the catalogue endpoint. It was written to 5 decimals,
    which put it 1 ft nearer Troy Rd's end than Washington St's start at the node.
    The fix is in `corridors.build_chain`: `JUNCTION_TIE_FEET`.
    **Basis changed to per direction in Item 54 (Session 73).** `AADT` is now the volume
    the XD segment's own direction carries: a two-way count × 0.5
    (`aadt.TWO_WAY_SPLIT`, an even directional split, basis `two_way_half`); a one-way
    count (`one_way`) and a ramp movement (`ramp`) as published. The Item 53 evidence
    and the two-way-street gate decide which is which, unchanged. So every VHD is
    exactly half its Item 53 value, and rankings, ratios and the relative tests don't
    move. What changes: a corridor's NB + SB VHD is the facility's VHD instead of
    twice it, and a ramp count now sits on the same footing as the mainline beside it
    (it was one movement against a two-way count). The absolute thresholds were halved
    to match: `MIN_CORE_VHD[_PER_MILE]` 10 → 5, `extents.AADT_ABSOLUTE_STEP` 8,000 →
    4,000 vpd, and the VHD/mi map tiers 25/100/300 → 10/50/150 (the bottom one rounded:
    the old 25 was a round number, not a break in the data). `aadt_layer` is still the
    published count. A GUI geometry cache from before Item 54 is rebased on load
    (`aadt.to_directional_basis`).
- **No `XDSegID`.** There is no INRIX join key, so the join to our `Segment ID` is
  necessarily **spatial**: `aadt.join_aadt` matches each Item 8 segment polyline to a
  candidate AADT line within `max_distance_m` (default **60 m**), gated by a
  **local-tangent bearing check (mod 180°)** that rejects the opposing-direction
  split and perpendicular cross-streets. Among the candidates that pass, the winner
  is chosen by a **ranked preference** — route number, then mainline over ramp, then
  distance, then coverage — **not** by distance alone (see the divided-highway
  subsection below). The result is flagged `matched` / `matched_ramp` / `nearest` /
  `missing` with the match distance, so a marginal join is visible, not silent. On
  the Myrtle export the AADT centerlines coincide with the XD segments — 45/46 match
  at ~0 m, and the ranked join leaves those numbers unchanged.

Fields kept (`_KEEP_COLS`): `Year`, `RouteID`, `Route`, `Segment`, `FromMeasur`,
`ToMeasure`, `AADT`, `PassengerA`, `Commercial` (the truck split, for a future truck
view), `Descriptio`, `Descript_1`, and since Item 55 `DHV` and `MADT1..12` (see
*Monthly factors and DHV* below). Route-measure identity (`RouteID`/mileposts) is the
layer's own linear reference.

- **`Route` and `Segment` are null on every Idaho row** — dead columns in the shipped
  layer. The live identifier is **`RouteID`**: five digits of route-segment number, a
  suffix letter, a two-letter class, a three-digit route number — `01010AIN084` is
  I-84, `02150ASH069` is SH-69, `01540DUS095` is US-95. Class is `IN` / `SH` / `US` /
  `OH`, and `OH` ("other highway", a local road) always carries route number `000`.
  `aadt.load_aadt` parses it into `route_class` / `route_number` and **repopulates
  `Route`** from it, so the column means something.
- **`Descriptio` / `Descript_1`** are the record's from/to descriptions and are the
  only facility-type signal the layer has: a ramp feature is written as the movement
  it carries (`WB OFF EAGLE RD IC #46`), a mainline record as the cross-streets it
  runs between (`EAGLE RD IC #46` → `JCT I-184 IC #49`). Both are **truncated in the
  source** (max 40 chars; `SNAKE RIVER VIEW EB RA` is stored that way).

### Monthly factors (`MADT1..12`) and `DHV` (ROADMAP Item 55)

`MADTm` is the record's average daily traffic in month *m* (1 = January); `DHV` is its
design-hour volume. All thirteen are integers on the same basis as the record's
`AADT` (two-way on a two-way road, one-way on a couplet leg).

- **Coverage.** Every 2025 record (8,423 of 8,423) carries all twelve MADTs and a DHV,
  none null or zero. On `Cumulative_AADT.zip` the MADTs are **absent (zero) for every
  record up to 2021** and present for 2022–2024. `DHV` is present from 2018 on (a
  handful of records missing).
- **2025 statistics** (MADT*m* / AADT, median over records): Jan 0.84, Feb 0.84, Mar
  0.94, Apr 1.00, May 1.06, Jun 1.07, Jul 1.04, Aug 1.07, Sep 1.09, Oct 1.05, Nov 0.98,
  Dec 0.90. The means run higher in summer (Jul 1.15), pulled up by a tail of
  strongly seasonal records (up to 2.55 in July). There are 717 distinct rounded
  patterns, so the factor is per record, not one statewide table.
- **The twelve don't average to exactly 1.** The median of a record's twelve ratios is
  0.996 (0.82–1.02). An AADT is not the plain mean of its MADTs (months differ in
  length, and ITD's factoring differs). A curve-weighted VHD therefore uses the ratio
  as published and does not renormalise it.
- **703 records (8 %) are flat:** every MADT equals the AADT. These carry no seasonal
  information (presumably unfactored counts); they read as ratio 1.0, the same as a
  missing MADT, but are flagged `layer`.
- **`DHV / AADT`:** median 0.12 (IQR 0.10–0.14; interstates 0.119), with a few
  records at 1.0. DHV is the 30th-highest hour of the year (K30), so it is an **upper
  bound** on a volume curve's average-day peak-hour share, not a target.

`aadt.join_aadt` carries **`madt_ratio_01` .. `madt_ratio_12`** = MADT*m* / AADT of
the chosen record (`aadt.madt_ratios`). The ratio is taken on the published record,
so the Item 54 halving does not change it: the per-direction MADT is
`AADT × madt_ratio_MM`. **`madt_source`** says where the ratios came from:
- `layer`: the record's own MADTs;
- `missing`: the record has no usable MADT (fields absent, any month null or ≤ 0, or
  AADT ≤ 0). All twelve become 1.0, meaning no seasonal adjustment;
- `no_aadt`: no volume was attached (`nearest` / `missing`). All twelve are 1.0 and
  irrelevant.

On a 1,500-segment D3 sample, 1,492 are `layer` and 8 are `no_aadt`.

**Layer cache.** `aadt.LAYER_CACHE_VERSION = 2` is written to the cache sidecar. A
cache without it (version 1, before Item 55, lacking the new fields) rebuilds once.
The sidecar also records `absent_columns`: requested fields the *source* lacks, such
as a pre-2022 subset or a test fixture. Their absence is then not a shortfall, so such
a source does not rebuild on every call.

### The divided-highway failure mode (ROADMAP Item 34)

On a divided highway the layer carries **one mainline centerline** — which sits
**22–30 m off each carriageway** — plus a **record per ramp movement**, and the ramp
runs parallel to the carriageway 3–8 m away. Nearest-wins therefore gives one
carriageway the mainline and the other a string of ramps, and nothing in the output
says so: both used to report `aadt_source == "matched"`.

Measured on the 2026 D3 export, I-84 **westbound** read
`147500, 18000, 145500, 10500, 135000, 13000, 13500, 122000, 10500…` segment to
segment while eastbound over the same ground was smooth and monotone
(`114500 → 122000 → 135000 → 145500 → 147500`) — a length-weighted mean AADT of
**61,410 WB against 122,813 EB**, near-halving the westbound vehicle-hours of delay.
For segment `1187323545` the picked record was `08627AIN084` /
`"WB OFF EAGLE RD IC #46"` (18,000) at 4.1 m; the right one was `01010AIN084` /
`"EAGLE RD IC #46"` (147,500) at 25.9 m. The same mechanism let a **frontage road**
record of 70 vehicles/day at 2.8 m out-compete the interstate at 28 m on rural I-84.

What fixes it (`aadt.classify_aadt_records` + the ranked `join_aadt`):

- Each record is labelled `mainline` / `ramp` / `connector` / `unknown` from
  `Descriptio`, then **rolled up by `RouteID`**: ITD gives every ramp its own
  route-segment number (I-84's mainline is all `01010AIN084`; its ramps are `01098`,
  `08627`, `25580`, …), so a route whose records are *mostly* mainline descriptions is
  a mainline route and its odd ramp-worded row — a carriageway between two gores,
  legitimately written `EB ON COLE-OVERLAND IC` — is relabelled `mainline`. A route
  with no mainline majority (a US-95 spur pairing one street with one ramp) keeps its
  per-record labels. Note `RAMP` is matched **singular only**: the plural
  (`I-15 NB RAMPS IC #108`) names the interchange a *mainline* record runs to, and
  259 rows use it that way.
- The route-number test is a **bonus, never a penalty** — a record that names the
  wrong route ranks with one that names none. Penalising a mismatch sent SH-55 at New
  Meadows (where US-95 carries it, a concurrency the XD side doesn't list) to a
  130-vehicle substation road.
- The 60 m default is only safe **because** ramps stop out-competing the mainline;
  under nearest-wins it would have handed *more* segments to the ramps.
- After the fix I-84 WB reads `114500 → 122000 → 135000 → 145500 → 147500` like the
  other carriageway, a length-weighted **123,692** against EB's **124,203** (0.4%).
  Across all 3,905 D3 segments 482 AADT values move, 112 of them from under 20k to
  over 50k; D3 peak vehicle-hours of delay rise **+50.9%**. The one AADT-weighted pair
  this repo has shipped (Myrtle, DESIGN_HISTORY Session 19: plain 20.1 mph vs weighted
  23.8, ~617 vehicle-hours) is **unchanged** — Myrtle is an undivided downtown couplet.

- **`matched_ramp` is a finding, not an error.** A segment that genuinely *is* a ramp
  needs its ramp volume, so the preference labels rather than filters:
  `aadt_source == "matched_ramp"` means the winning record is a ramp or connector.
  On D3 that is 37 segments, nearly all of them unnamed FRC 3–4 XD stubs at
  interchanges. `vehicle_hours_of_delay` **flags and keeps** those rows (the column is
  carried through, `attrs['aadt_ramp_rows']` counts them) rather than dropping them —
  dropping would silently zero real ramp impact. Filter on it when a corridor total
  must be mainline-only.
- **Rural divided sections can exceed 60 m.** 12 rural I-84 WB segments in D3 sit
  61–85 m from the mainline centerline and come back `nearest` (no volume) — correct
  behaviour, but raise `max_distance_m` if you need them.

### Route class, the tie-break, and what is *on-system* (ROADMAP Item 42)

**A record's `RouteID` class is not the whole answer to which route it is on.** 330 of
the D3 layer's `OH` ("other highway") rows name a state route in the description
instead — `KARCHER RD (SH-55)`, `EAGLE RD (SH-55)`, `CHINDEN BLVD (US-20)`,
`E 7TH ST (US-95)` — and 82 more are nothing *but* a route (`SH-52`, `US-95`). Read
class off `RouteID` alone and a state highway comes back an unnumbered street.
`aadt.record_route_number` reads both, and reads **only** those two forms:

- a **trailing parenthetical** — `N WASHINGTON AVE(SH-52)`;
- a description that is **only** a route designation — `US-95`.

A route named anywhere else in a description is a cross-street or a junction, not the
record's own route: `FRANKLIN RD US-20 IC#29` is **I-84's** mainline record at the
US-20 interchange, and `IDAHO AVE @ US-95 CONN` is a connector. The closing
parenthesis must follow the number, which also drops `CALDWELL BLVD(I-84 BUS)` and
`CLEVELAND BLVD (I-84 B)` — **I-84 Business, not I-84**.

**Where route class enters the join: last, as a tie-break.** The ranked preference is
route → facility → distance → coverage → **route class** → the record's own identity.
Class sits under coverage deliberately. Ranking a numbered record above distance or
coverage was measured on D3 and is wrong: Ustick Rd would take
`FRANKLIN RD US-20 IC#29`'s 74,500 and W Emerald St `COLE RD IC #1B`'s 82,000, because
an interstate record passes within metres of a city street at an interchange. What the
tie-break fixes is smaller and real — the last comparison used to be **the record's
row position in the layer**, so two records on the same ground, equally close and
equally alongside, were separated by load order. Shuffling the AADT layer moved the
AADT of **9 of the 3,905** D3 segments (`HOWARD RD` ↔ `CLARK RD` on SH-78,
`BISHOP RD` ↔ `BERGLAND RD` on SH-52, `POISON CREEK RD` ↔ `PERSHALL RD` on US-95 —
adjacent records on the same route, both lying on the segment). The join is now
independent of layer order, and the description-derived route number is used **only**
for this tie-break and for the reported `aadt_route_number`: letting it decide the
*match* moved 14 segments and every one for the worse (two Chinden Blvd segments left
US-20's 29,000 mainline record for a 1,100 record covering 9% of the segment).

**On-system is a different question from "whose volume is this".** The join answers
the second by proximity within a 60 m gate, so a frontage stub, a ramp and a
cross-street at an interchange all match a numbered record. Taking that as *on-system*
labelled **764 D3 segments / 223 miles** on-system off the export — 24 stubs of
E Island Woods Dr on `EAGLE RD (SH-55)`, 66 of Simco Rd on `GRANDVIEW RD (SH-167)`.
**Coverage cannot catch this**: a 0.04-mile stub beside a mile-long record covers
1.00. `aadt.classify_on_system` adds the test that can — **identity**: the record's
description names the same street as the XD segment (`street_names_agree`, which drops
directionals and street types) *or* the segment names a route in its own
`RoadNumber` / `RoadList`. With it, mainline-only, ≤ 35 m and ≥ 0.4 coverage, the 764
become **26 segments / 8.77 miles**. Thresholds worth knowing:

- **35 m, not 20 m** — on a divided highway the mainline centerline sits 22–30 m off
  each carriageway, so a 20 m rule rules out the interstates themselves.
- **0.4 coverage** — a record boundary landing mid-segment cuts coverage without
  saying anything about the route; one of the seven SH-19 segments the owner confirmed
  as a real omission covers 0.45.
- The test is **sufficient, not necessary**: 3,415 of the export's own 3,905 segments
  pass it, the rest failing mostly because their matched record names no route at all
  (a rural record described by its cross-streets).

**What I-84 Business is in this vintage.** Nothing carries `84B`. ITD's AADT layer
classes Caldwell Blvd and Cleveland Blvd under an `IN084` **`RouteID`** — I-84 in the
route inventory — and the XD attributes give those segments `RoadNumber` **84**. The
only place the business route appears by name is a description parenthetical
(`CALDWELL BLVD(I-84 BUS)`, `N MAIN ST (I-84 BUS)`, `CLEVELAND BLVD (I-84 B)`), which
`record_route_number` deliberately refuses to read as a route number. A future search
for "84B" will fail exactly as this one did; search the corridor lists in
`out/highways/` instead.

**AADT is a daily total.** Vehicle-hours of delay (`Delay/60 × AADT`) and the
AADT-weighted mean speed use it as a **relative** weight, not an absolute VMT: the
per-window impact figure is scaled to an average day at the window's mean delay,
not the window's own duration — the code records the caveat
(`attrs['aadt_caveat']`) and does **not** silently rescale. **Corridor/network
travel time stays a pure sum** across segments (Item 12); AADT does not re-weight
it — volume weighting only applies where a *mean across segments* is summarized.

**The layer cache is not a join.** `load_aadt(cache_path=...)` caches the **raw
route-measure layer** — `Year / RouteID / FromMeasur / ToMeasure / AADT / geometry`,
indexed by row number, *no segment key*. `join_aadt` produces the different thing:
a frame indexed by **`Segment ID`** (== `XDSegID`) carrying the matched `AADT`.
`geometry_cache/d{N}_aadt.parquet` holds the **former**. Reading it and treating it
as per-segment volume matches nothing and, if the result is zero-filled, renders as
universal free flow — which is exactly what the statewide VHD/mile map did until
Session 60. Anything that needs per-segment volume must call `join_aadt` (or
`run_district_screening.join_volumes`), never load the cache directly.

**The cache is keyed on what it covers (Item 47).** It used to short-circuit to the
cached file without checking that it covered the requested bbox or year, so whichever
caller wrote it first fixed the extent every later caller saw — and the later caller
had no way to tell. `load_aadt` now writes a `<cache>.meta.json` sidecar recording the
`year`, `bbox` and `columns` it was built for, serves the cache only when that covers
the request, and otherwise rebuilds for the **union** of the two extents, so a cache
shared by a corridor-bounds caller and a full-network one widens to serve both instead
of thrashing. `attrs['aadt_layer']` says which happened (`hit` / `written` /
`rebuilt (<reason>)`).

A cache with no sidecar (written before Item 47) is judged on the extent of the data
it holds, which is conservative in the safe direction: the features in a bbox-filtered
read never reach past the bbox, so a request the data covers was certainly covered by
the request that built it, and one it does not may only mean the layer has nothing out
there — that rebuilds needlessly, never under-answers.

Two consequences worth knowing. A request that cannot be proved covered **re-reads the
source**, so a `source` that is itself a `.parquet`/`.geoparquet` is now read directly
rather than handed to pyogrio. And a bbox of `(nan, nan, nan, nan)` — what
`geo.total_bounds` returns for an empty frame — reads as *no restriction*, not as a box.

### Route membership: ITD's route, not INRIX `RoadNumber` (ROADMAP Item 48)

**INRIX `RoadNumber` is not the state's route inventory, and it is wrong both ways.**
Lewiston's downtown Main St / D St couplet is `RoadNumber` 12, but ITD's layer carries
both streets on local (`OH`) records (`06830AOH000`, `01900AOH000`, `47980AOH000`,
`06820AOH000`). US-12 (`01910AUS012`) runs the levee bypass, which INRIX names
`Levee Byp` and leaves **unnumbered**. The same pattern appears statewide:
- *Numbered by INRIX, local in ITD's layer:* the I-90 business loops (Coeur d'Alene
  Northwest Blvd / Sherman Ave; Kellogg Bunker Ave / Cameron Ave), SH-37 south of
  Holbrook, Rexburg's N 7th E, and Payette's S Main St / 7th Ave N.
- *On a state route in ITD's layer, unnumbered by INRIX:* the levee bypass, SH-77 on
  the Elba-Almo Hwy (~30 mi), and SH-75 on Sun Valley Rd.

`routes.route_membership` decides each segment's route from the layer, and
`scripts/build_route_membership.py` writes the per-district tables to
`out/highways/route_membership/`. The inventory generator and the statewide catalogue
builder read those tables instead of `RoadNumber`. Four facts about the layer shape
the rules:

- **One record where routes share a road.** US-20/26/93 near Arco is one
  `02220AUS093` record; INRIX says 20. INRIX's `RoadList` names every route the road
  carries, in several spellings: `US-20|US-26|US-93`, `N ID-34`, `Highway 30`,
  `N Highway 34`. So a mismatch that the segment's own `RoadList` explains is a
  `concurrent` verdict, not an error. Chains keep INRIX's number there, and the
  segment joins both route files.
- **Business loops are banded as their parent route.** ITD bands I-84 Business
  `IN084` (Caldwell Blvd `02042`, Burley's Overland Ave `02290`, Mountain Home
  `01020`), I-15 Business `IN015`, and US-93 Business in Twin Falls `US093`. Only the
  5-digit route-segment number tells them from the mainline (`01010AIN084` is I-84
  itself). Two rules follow:
  - an `IN` band is never *given* to a segment that INRIX does not number as that
    interstate, because INRIX numbers interstate mainline reliably;
  - a band that the `RoadList` names only as a business route (`US-93-BR`, `I-84-BL`)
    keeps INRIX's number (the `business` verdict).

  The same `IN` rule stops frontage roads beside I-90/I-84 (Grouse Creek Rd,
  Markwell Ave) from being given the interstate.
- **Descriptions name the cross street at a break, not the road.** On the levee,
  `01910AUS012` reads `5TH ST` and `18TH ST/DIKE BYPASS RD`, so street-name identity
  (`aadt.classify_on_system`) rejects the bypass. Membership identity is geometric
  instead:
  - *on* the segment means ≤ 10 m, and within 15 m for at least 80% of the segment's
    length;
  - *near* it means ≤ 40 m and alongside ≥ 50%, which reaches a divided highway's
    single centreline.

  The same reading resolves the `OH` descriptions that name a route:
  - Reubens-Gifford Rd's record, 3.8 km from US-95, reads just `US-95`;
  - E Palouse River Dr's reads `S MAIN ST (US-95)`.

  Both mean "to US-95". So for **membership only**, a route that appears only in an
  `OH` record's description is ambiguous: it can neither add a route nor, lying on a
  segment, take one away. This does not change `aadt.record_route_number`'s reading
  for the volume join's tie-break (Item 42); that answers a different question.
- **The layer can lag a realignment.** Reisenauer Rd south of Moscow is still a US-95
  record, but US-95 moved to its new alignment in ~2025 and the old road went to Latah
  County (owner, Session 65). The new alignment has no record at all yet.
  `scripts/route_overrides.csv` (county + road + note) beats the layer. A segment INRIX
  numbers that no record decides either way is kept as `unconfirmed`, not dropped:
  that is what new construction looks like.

Dropping needs more evidence than adding, on purpose. A route is taken away
(`inrix_only`) only when a clearly local record lies *on* the segment **and** no
numbered record lies *near* it. A frontage-road record can sit closer to a carriageway
than the highway's own centreline (22–30 m away), and the *near* test is what protects
the carriageway.

**Since Item 52 the State Highway System decides**, and the rules above are the AADT
layer's fallback (next section).

**License:** treat like the data exports — gitignored, not redistributed.

## Volume-profile curves (`volume_profiles.py`, Item 55)

VHD up to Item 54 multiplies a window's mean delay by the **whole day's** directional
AADT. The curve library supplies the time-of-day and day-of-week factors that turn a
daily count into a volume per 5-minute bin (Item 57 does the multiplying; see
"Curve-weighted VHD" below):

```
vol(bin) = dirAADT × madt_ratio_MM(month of bin's date) × bin_volume_factor(curve, bin)
```

**Schema** (`src/inrix_tools/data/volume_profiles.json`, package data loaded with
`importlib.resources`; `volume_profiles.load_profiles()` → `{curve_id: VolumeProfile}`):

| field | meaning |
|---|---|
| `curve_id` | stable id; Item 56 assigns one per XD segment |
| `hourly.weekday` / `.sat` / `.sun` | 24 shares of that day type's volume, each Σ = 1. Hour 0 = 00:00–01:00 **local** |
| `dow` | 7 factors, Monday..Sunday (pandas `dayofweek`), mean 1: a day's volume relative to the average day |
| `provenance` | `basis` (`digitised` / `extracted` / `synthesised` / `fitted`), `sources` (keys into the file's `sources` citation table), `detail` (exactly how it was built) |
| `description` | one line on the shape |

`VolumeProfile` rejects a missing day type, a count other than 24 / 7, a negative or
non-finite value, Σ ≠ 1 or mean(dow) ≠ 1 (tolerance 1e-6), and an unknown basis.

**`bin_volume_factor(curve, local_ts, bin_minutes=5)`** gives the share of the average
day's volume in each bin, from the bin's **start** read on the local wall clock:

```
dow[weekday] × hourly[day type][local hour] / S(date) / (60 / bin_minutes)
```

`S(date)` is the day type's hourly shares summed over the hours that local date
actually has:
- 1 on an ordinary day;
- 1 − the skipped hour on the 23-hour spring-forward day;
- 1 + the repeated hour on the 25-hour fall-back day.

So a complete day's bins always sum to that day's DOW factor, and a week sums to 7.
The factor depends only on its own timestamp, so a window or a sparse index gets the
same values as the whole day. A tz-naive index is rejected. Holidays are not
special-cased.

**The equal-daily-directional-volume assumption.** Both directions of a two-way road
carry the same daily volume: the Item 54 AADT × 0.5. All directional asymmetry is in
*which curve* each direction is given. The AM-commute (inbound) side gets
`am_commute_urban` and the PM-commute side `pm_commute_urban`. Over a day they carry
equal volume at different hours. This is a documented simplification: a direction
that really carries more daily traffic is not represented until a count says so
(Item 59).

### The starter curves and their sources

Rebuilt by `python scripts/derive_volume_profiles.py` from
`scripts/volume_profile_sources.json`. Re-extracting that file from the PDFs is
`--extract --umr … --epa …` and needs poppler + Pillow. A test asserts that the
packaged JSON is exactly what the script builds, so don't hand-edit it.

| curve_id | basis | weekday peak hour (share) | Sat peak | DOW Mon..Sun |
|---|---|---|---|---|
| `am_commute_urban` | digitised | 07–08 (0.076) | 0.077 | 1.05 ×4, 1.10, 0.90, 0.80 |
| `pm_commute_urban` | digitised | 16–17 (0.090) | 0.077 | same |
| `balanced_urban` | digitised | 16–17 (0.064) | 0.077 | same |
| `rural_through` | extracted | 16–17 (0.082) | 0.082 | 0.99, 1.05, 1.07, 1.09, 1.16, 0.91, 0.73 |
| `interstate_through` | synthesised | 15–16 (0.077) | 0.079 | 0.95, 0.97, 1.00, 1.06, 1.18, 0.95, 0.90 |
| `rural_recreational` | synthesised | 14–15 (0.082) | 0.082 | 0.95, 0.85, 0.85, 0.90, 1.15, 1.25, 1.05 |

- **Urban curves: TTI 2019 Urban Mobility Report, Appendix A.** Its profiles come
  from 713 urban continuous-count stations in 37 states and are **directional**: the
  "AM Peak" curve is the direction whose AM-peak speed is lower. That is the
  inbound/outbound pair the owner's scheme needs, so nothing is synthesised from a
  two-way shape plus a D-factor.
  - Weekday: Exhibit A-2 (moderate congestion) non-freeway AM/PM, and A-5 (similar
    speeds each peak) non-freeway for `balanced_urban`.
  - Sat and Sun: Exhibit A-4's single weekend non-freeway curve.
  - DOW: Exhibit A-6 (Mon–Thu +5 %, Fri +10 %, Sat −10 %, Sun −20 %), which already has
    mean 1.
  - The exhibits are raster charts. They are digitised by line colour against the
    0 % and 3 % gridlines, and each 96-point series sums to 0.997–1.009 before
    renormalising (the calibration check). Spot values match the chart to about
    ±0.03 percentage points per 15 minutes.
  - The freeway variants (and A-1/A-3's low/severe congestion levels) are in the
    source extract for later use.
- **Rural curves: EPA 2017 NEI onroad review, Idaho.** These are per-county plots of
  the hourly VMT fractions Idaho submitted. They are vector graphics, so the values
  are **extracted** from the PDF paths, not read by eye (each sums to 1.005–1.011
  before renormalising).
  - `rural_through`: the median of the passenger-car (MOVES source type 21) curve on
    rural non-freeways (road type 3) over the non-MSA counties (28 weekday / 25
    weekend county pages).
  - One weekend curve serves Sat and Sun: MOVES has only weekday/weekend.
  - DOW is the inverse of INDOT's 2023 R2 (rural arterial) weekday factors,
    renormalised. INDOT publishes the factor that turns a day's count *into* AADT,
    so a day's volume is its inverse. No Idaho DOW table was found.
- **`interstate_through`** mixes curves on rural freeways (road type 2), median over
  32 non-MSA county pages:
  - 0.866 × Idaho's passenger-car curve;
  - 0.134 × the CRC A-100 combination-truck curve (West Region non-MSA), which runs
    through the night.
  - 0.134 is the AADT-weighted `Commercial / AADT` on the 2025 layer's interstate
    mainline records that populate `Commercial` (318). Many records carry
    `Commercial = 0`, so this is a floor, not a measurement of rural I-84.
  - DOW: the inverse of INDOT R1 (rural interstate), renormalised. It is
    Friday-heavy, with a lighter weekend than rural arterials.
- **`rural_recreational`**: in the recreational counties (Blaine, Valley, Teton,
  Custer, Fremont; Bonner's weekend plot has no complete curve), Idaho's weekday curves are indistinguishable from the
  rest of rural Idaho. So the curve takes those counties' **weekend** shape (a
  single midday hump) for every day type. The weekend-heavy DOW is **synthesised**
  after FHWA's recreational guidance (steady weekdays, weekend surge). It is a
  placeholder until Item 59 fits it from an ATR.
- **Sanity against DHV.** The busiest average-day hour on any curve, × its largest
  DOW factor, is 0.085–0.102. That is below the layer's median DHV/AADT of 0.12,
  as it must be: DHV is K30 and adds the seasonal peak. This is checked in
  `test_volume_profiles`.

## Curve assignment per XD segment (`profile_assignment.py`, Item 56)

Every segment of a district's network gets one `curve_id`. The first of these that
decides wins, and `curve_source` records which:

*(Item 59 inserts **`station`** between `override` and `inferred`: see "Traffic counts
and count-fitted curves" below.)*

1. **`override`**: `scripts/volume_profile_overrides.csv` (`#` comments, like
   `route_overrides.csv`). Columns: `xd_seg_id, district, corridor, direction,
   curve_id, note`. A row is keyed on `xd_seg_id` **or** on a catalogue
   `corridor` + `direction` (`i84,EB`), never both. `district` blank = any district,
   because corridor ids repeat across districts. A segment row beats a corridor row.
   `curve_id` must be in the library and every row needs a note. The file ships with
   no rows. `rural_recreational` is never chosen by the rule, only by an override.
2. **`inferred`**: the *orientation* of a chain, from its delay.
   - A chain is one direction of a road. Catalogue entries pair within their
     reporting `corridor` by opposite direction sign (N/E = +, S/W = −). Route runs
     are the segments of one ITD route (`route_number`) with one travel sign, one
     urban area, one zone and one radial sense, paired with the same route, area and
     zone, the other sign and the mirror radial. Ramps, and segments with no route
     or no N/E/S/W `Bearing`, are in no run.
   - Per side: `am_share = Σ am delay / Σ (am + pm delay)`, over the segments
     observed in both windows. The delay is `screen.window_delay`: the floored mean
     delay (minutes per vehicle) at the `am` (07:00–09:00) and `pm` (16:00–18:30)
     weekday means, against INRIX's Ref Speed, the same definition `rank_corridors`
     sums. A segment's delay already scales with its length, so the sum is
     length-weighted.
   - AM-commute when `am_share ≥ 0.65` on this side **and** `≤ 0.35` on the
     opposite side (the mirror case is PM-commute), with both sides' worse-peak
     delay ≥ **0.10 min per observed mile**. The AM side gets `am_commute_urban`,
     the PM side `pm_commute_urban`. Unpaired chains, both-peaks chains, chains
     that don't oppose, and chains below the floor fall through.
   - A segment on several chains takes the first decisive one: catalogue before
     route run, then the shortest (a generated catalogue's `core` tier before its
     `regional`).
   - The inference sets orientation only, never volume. So the circularity with
     delay is harmless.
3. **`urban_rule`**, from `d<N>_urban_context.csv`, plus the bearing from the
   segment's midpoint to its urban area's **centre**. That is the economic
   (employment) centre in `scripts/urban_centres.csv` where the area has a row
   (`profile_assignment.load_urban_centres` / `apply_urban_centres`), else the
   polygon centroid (`itd_layers.urban_centroids`):
   - an **interstate** (the ITD route id is `…IN…`, or with no id the number is
     15/84/86/90/184) → `interstate_through`. A **business loop** is never an
     interstate, even though it carries the interstate's ITD id (I-84 BL on Garrity
     and Caldwell Blvds is `02042AIN084`);
   - the **commute zone** is inside an urban area of ≥ 50,000 people, or outside it
     within 5 km of its boundary (the `approach` zone). There, travel within 45° of
     the bearing to the centre is **inbound** → `am_commute_urban`, within 45° of
     the bearing away is **outbound** → `pm_commute_urban`, and anything between (or
     within 1.5 km of the centre) is **tangential**;
   - inside any urban area without a clear radial (tangential, or a town under
     50,000) → `balanced_urban`;
   - everything else → `rural_through`.
4. **`default`**: `balanced_urban`, where there is no urban context at all.

The thresholds are module constants, and the run's provenance JSON carries them
(`volume_profiles.thresholds`).

**Output.** `run_district_screening.py` writes `<out-dir>/d<N>_volume_profiles.csv`
(`XDSegID, curve_id, curve_source, am_share_self, am_share_opposite, chain_id,
reason`; `profile_assignment.write_assignment` / `read_assignment`), prints the counts
by source, and records them in the provenance JSON.
- `am_share_*` and `chain_id` come from the chain that decided. Where the inference
  fell through, they come from the segment's first chain, and `reason` ends with
  `(inference: …)` saying why.
- A `day_7d` run screens `am`/`pm` for the inference, so both runs of a district
  write the same file (checked byte-identical on D3).

**What it assigns (2025 AADT, the export period, with the urban-centre table,
Session 76):**

| | segments | inferred | chains inferred | am / pm commute | balanced | rural | interstate |
|---|---|---|---|---|---|---|---|
| D1 | 5,007 | 0 | 0 / 141 | 410 / 426 | 1,190 | 2,717 | 264 |
| D2 | 3,499 | 0 | 0 / 65 | 140 / 146 | 544 | 2,669 | 0 |
| D3 | 16,105 | 269 | 22 / 225 | 2,499 / 2,518 | 5,887 | 4,876 | 325 |
| D4 | 6,087 | 56 | 14 / 170 | 353 / 339 | 1,207 | 3,703 | 485 |
| D5 | 4,386 | 0 | 0 / 116 | 221 / 226 | 581 | 2,795 | 563 |
| D6 | 6,686 | 0 | 0 / 136 | 348 / 340 | 936 | 4,785 | 277 |

- Every chain that infers orients the textbook way. In the Treasure Valley: I-84 EB,
  I-184 EB (0.91 / 0.02), Chinden EB (0.66 / 0.15), US-20/26 Star–Middleton EB
  (0.89 / 0.28) and SH-44 EB near Star are AM-inbound. So is I-84 EB leaving Nampa
  (0.87 / 0.07), the Nampa→Boise commute, which the urban rule would have called
  outbound. In D4, SH-75 NB into Ketchum (0.90 / 0.17) and through Hailey
  (0.71 / 0.22) is the Wood River Valley's AM worker commute.
- Nothing infers in D1, D2, D5 or D6. Most chains are below the delay floor, and the
  rest are congested at both peaks.
- The signalised Boise arterials (State St, Eagle Rd, SH-69, Broadway) are
  both-peaks or both PM-heavy, not opposed, and fall to the urban rule.

**The polygon centroid is not the city centre, so the rule reads an economic centre
(`scripts/urban_centres.csv`).** The Boise City urban area's centroid is at
(43.615, −116.295), 7.5 km west of downtown Boise (−116.202), because the polygon
takes in Meridian. Between the two, the radial reverses: on the centroid, I-184 EB
read "outbound", and Front St WB got `am_commute_urban` although its am_share is 0.20.
- Owner, 2026-09-25: the employment centre is downtown, so Boise's row is downtown.
- The other six commute-sized areas have **proposed** downtown rows, awaiting the
  owner. Their centroid offsets: Coeur d'Alene 8.1 km (pulled by Post Falls/Hayden),
  Nampa 4.5, Lewiston 3.0, Pocatello 2.7, Idaho Falls 1.7, Twin Falls 1.0.
- Columns `uace, urban_area, centre_lat, centre_lon, note`, with `#` comments. A
  row needs a note; a UACE may appear once; a row for an area absent from the layer
  is ignored.
- Against the centroid run, the table changes the curve on 5,126 D3 segments and
  164–757 in each other district. Front St WB and Myrtle EB are now within 1.5 km
  of downtown (`balanced_urban`). State St runs EB → AM and WB → PM, matching its
  data (0.60 / 0.19). D3's inferred segments rise 235 → 269, because the route runs
  now split at the real centre.
- `--urban-centres ''` reverts to the polygon centroids.

## Curve-weighted VHD (`aadt.curve_vehicle_hours_of_delay`, Item 57)

The headline VHD is now **vehicle-hours of delay on an average day of the window in
the data period**: **per weekday** for the weekday-gated peaks, per calendar day for
an ungated window (`day_7d`, `night`). The `vhd_per` column says which. For segment
`s` on curve `c` and window `W`, over the `N_W` local days of the period that `W`'s
day gate covers:

```
VHD(s, W) = (1/N_W) Σ_cells  max(tt(cell) − ref_tt(s), 0) × dirAADT × madt_ratio_m × volume_days(c, W, cell) / 60
volume_days(c, W, cell) = Σ over the period's days d in the cell and in W  bin_volume_factor(c, bin on d)
```

- A **cell** is month × day type (`weekday` / `sat` / `sun`) × bin of the day.
  `screen.segment_bin_screen` gives its mean travel time over the CValue-gated rows
  (DuckDB, local wall clock, rows inside any of the named windows).
- **The delay floor is per cell**, not per window mean: a bin that runs faster than the
  reference cannot pay for a congested one. `ref_tt` is the caller's: `Miles /
  Ref Speed × 60` for ranking, the segment's own baseline for the extents.
- `volume_days` (`volume_profiles.window_volume_weights`) sums the curve's bin factor
  over each real day of the cell, so each day carries its own DOW factor and day-type
  shape, and the DST days their 23/25 hours. The cell's mean delay stands for every
  day of the cell. Within the weekday type the delay is pooled over Mon–Fri; a window
  gated to part of a day type gets the type's pooled delay with only its own days'
  volume.
- **`N_W` counts the days the window's gate covers** (owner, 2026-09-25: weekday for
  the peaks). So windows with the **same gate** add up as VHD: AM + PM is their union,
  per weekday, which is all the peak totals sum. Windows with **different gates** add
  only as totals, `vhd × N_W`: a weekday AM (per weekday) + its weekend twin (per
  weekend day) is not the ungated AM (per day). Don't add a peak to `night` or
  `day_7d`, or compare their shares of the day. `vhd_annual = vhd × 365 × gate
  days / 7` (about 261 weekdays). The period is the area's first to last local date
  (`screen.data_period`), the same for every segment.
  - The alternative considered and dropped was dividing every window by all the
    period's days. All windows then add up, but a weekday peak is shrunk by 5/7
    against the 7-day window just because weekends exist.
- **A missing cell** (no gated row for that segment, month, day type and bin) takes
  the segment's same day type × bin pooled over all months, weighted by observations.
  A cell with no data in any month contributes nothing. `coverage` = the share of the
  window's volume with a delay after the fill; `observed_share` = from the cell's own
  month.
- `by_month=True` gives each month's VHD per average window day **of that month**, from that
  month's own cells only (no fill). `extents.monthly_delay_profile` reads it, so
  `_monthly_vhd` is real per-month volume at `MADT_m`.
- **Flat-curve check.** With a flat curve (every hour 1/24, DOW 1, MADT 1), `VHD =
  index × window hours / 24`, gated or not. The index is Item 54's
  `vhd_index` (window mean delay × daily dirAADT / 60), which differs from the flat
  curve only in the floor (per window mean vs per cell). Tested on toy data.

**Where it is used.** The core entry points: `screen.rank_corridors(..., bins=,
curves=)` or `(..., segment_vhd=)` (a precomputed `screen.segment_curve_vhd`),
`extents.segment_congestion(..., bins=, curves=)` (the VHD read in the segment's
`peak_window`) and `generate_catalogue(..., bins=, curves=)` (its monthly profile
too). Without `bins`, `vhd` is the index. Every output carries `vhd_index` and
`attrs['vhd_basis']` (`curve` / `index`), and `attrs['aadt_caveat']` says "average
window day of the period, generic curves". `screen.segment_curve_vhd` runs the whole
thing off the store in segment chunks that share one period.

**Every consumer reads the curve VHD since Item 58.** Nothing that ranks, maps or
catalogues is on the index any more:

- `run_district_screening.py` computes the curve VHD once per run, for every screened
  segment against the reference (`Miles / Ref Speed × 60`). It ranks the corridors
  on it, colours the VHD/mile map by it (the worst-TTI window's value), and saves it
  as `segment_{peak,7day}_curve_vhd.parquet`. The provenance JSON gains a `vhd` block
  (basis, `per`, caveat, period, `madt_ratios`). The corridor totals carry `vhd_per`
  and refuse to total windows that are per different days.
- `generate_statewide_maps.py` reads those parquets. It no longer joins AADT itself,
  so the map can't weight by a different AADT than the ranking did (it used to
  default to `Cumulative_AADT.zip` while the runs used `AADT_2025.zip`).
- `aggregate_statewide_rankings.py` refuses districts on different VHD bases (a
  stale index table, or per weekday beside per day). The district summary gains
  `peak_vhd_per` / `day7_vhd_per`.
- `build_statewide_catalogues.py` passes a cached peak bin screen
  (`segment_peak_bins.parquet` + `.json` attrs) and the screening run's curves
  (`dN_volume_profiles.csv`). So it needs a screening run first.
- The GUI's vehicle-hours map mode and its KML export use `screen.frame_curve_vhd`.
  That is the same calculation over the in-memory rows: the time-of-day slider and
  weekday checklist become the window (`screen.clock_window`), and the cells are 15
  minutes (or the export's bin, if coarser). The floor is per cell, against
  `speed.free_flow_travel_time` under the GUI's free-flow choice (Ref Speed or an
  observed percentile). Curves come from the screening runs' saved assignments, else
  the default `balanced_urban`. The legend reads "Vehicle-hours of delay / weekday"
  (or `/ day`).

**The scale, statewide (Session 78).** The curve VHD / index ratio, over segments at
≥ 10 VHD/mi on the index, from the full 2026 re-run with MADT joined:

| window (map) | median | IQR | by district |
|---|---|---|---|
| peak, worse of AM / PM, per weekday | **0.165** (2,262 segments) | 0.126–0.191 | 0.163–0.166 |
| `day_7d`, per day | **0.936** (1,794) | 0.88–1.04 | 0.889–0.970 |

The peak ratio is the same in every index tier (0.164 / 0.165 / 0.167 for [10, 50),
[50, 150), ≥ 150), so one factor rescales the whole scale. At the catalogue cores
(best candidate per chain, index ≥ 5) it is 0.165 (197 cores, IQR 0.154–0.191). The
thresholds moved by these ratios:

- the core floors `MIN_CORE_VHD[_PER_MILE]` 5 → 0.8 (× 0.165, rounded down), and the
  unused `VHD_BOTTLENECK` / `VHD_FREEFLOW` 75 / 10 → 12 / 1.5;
- the **peak** VHD/mi map tiers 10 / 50 / 150 → **1.5 / 8 / 25** (× 0.165 = 1.65 /
  8.25 / 24.75, the bottom rounded down as Item 54 did);
- the **7-day** map tiers stay 10 / 50 / 150. × 0.936 gives 9.4 / 47 / 140, and the
  ratio's spread includes 1. The two maps now have their own tier sets
  (`run_district_screening._vhd_tiers`, chosen by the frame's `vhd_per`), because a
  weekday peak is about 2 of a day's hours and the 7-day window 15.

Ratios (spill retention, dilution, the AADT gradient, proration, the episodic share)
don't care about scale and did not move.

**On D3 (Session 77).** The 2026 export (1 Jan – 31 Aug, 15-minute bins), for the
1,913 segments with an Item 56 curve and a reference speed, with AADT = 1 and MADT = 1
so only the time shape shows. The whole district runs in about 30 s.

- **Per-cell floor vs window-mean floor**, summed delay: AM +4.6 %, PM +2.5 %,
  `day_7d` +9.4 %. Where a segment's window delay is material (> 0.05 min) the median
  ratio is 1.00 (AM, PM) and 1.01 (`day_7d`). The difference is in the free-flowing
  segments: 316 / 321 / 387 segments (of about 1,900) have no window-mean delay but a
  few congested cells.
- **Curve VHD / index**, median per segment (the ratio Item 58 rescales the floors
  by): AM 0.117 (a flat curve would give 2/24 = 0.083), PM 0.189 (flat: 2.5/24 =
  0.104), `day_7d` 0.94 (flat: 15/24 = 0.63). The peaks are per weekday: the
  period's 243 days hold 173 weekdays. The commute windows carry more than their hours'
  share of the day, and 06:00–21:00 carries about 90 % of it. By curve at PM:
  `pm_commute_urban` 0.154, `rural_through` 0.135, `am_commute_urban` 0.123,
  `balanced_urban` 0.118.
- Coverage: the median segment's window volume is 100 % covered, and 99.9 % from its
  own month's cells.

## Traffic counts and count-fitted curves (`counts.py`, Item 59)

### The count schema

One row per station × direction × local hour (`counts.COUNT_COLUMNS`). Every importer
produces it, so the fitting never sees a source's quirks.

| column | meaning |
|---|---|
| `station_id` | the source's id, a string (`"00279"`, ATSPM signal id, a tube-count name) |
| `direction` | direction of **travel**: `NB SB EB WB NE NW SE SW`, or `2WAY` for a total |
| `timestamp` | the **start** of the hour, tz-aware local time; on disk ISO 8601 with offset (`2026-04-01T07:00:00-06:00`) |
| `volume` | vehicles in the hour, ≥ 0 |
| `lat`, `lon` | the station, WGS84 |
| `route` | the state route it counts: `"84"`, `"55"`; a business loop `"84 BL"`; blank if unknown |
| `source` | `atr`, `tube` or `atspm` |

`validate_counts` rejects naive or off-the-hour timestamps, unknown directions and
sources, negative volumes, missing coordinates, and repeated station × direction ×
hour rows (lanes and detectors are summed before the schema). `write_counts` /
`read_counts` round-trip it.

### ITD ATRs (TCDS)

ITD's permanent count stations are in the MS2Soft TCDS portal. Report 87, "Volume by
Hour by Day for Month", is one workbook per station-month. `tcds-scraper/tidy.py`
flattens it to one long CSV (`series_id, direction, roadbed, district, county,
community, location, route, collection_type, date, hour, volume, source_file`). What
Item 59 learned about that export:

- **Series.** Each workbook holds the 2-way total (`00279`), each direction
  (`00279_EB`, `direction = EB`) and each lane (`00279_1_EB`, `direction = 1`, the
  lane number). Lanes sum to the direction and directions to the total.
  `import_tcds_hourly` keeps the direction series. A station with no direction
  series (00291, a 7-day March 2026 count) keeps its total as `2WAY`.
- **Hours.** `hour` 0–23 is the hour *starting* then on the station's local wall
  clock (TCDS's `12-1A` row is 0). The spring-forward day's missing hour is dropped;
  a fall-back day cannot say which 01:00 a row is.
- **`route` in the tidy CSV is not the route number** (00002 on I-84 reads `20`). The
  route is parsed from the station list's `on` field (`INT 84 MAIN`, `US 20 MAIN`,
  `SH 55 MAIN`, `INT 84 BL` → `84 BL`), else the start of its description (00330's
  `on` is `N Eagle RD`; its description starts `SH-55`).
- **Diagonal labels.** Some stations label directions NW/SE (00002, 00236, 00262) or
  NE/SW (00204, 00266). These are kept as-is; the station rule matches them by
  bearing.
- **Coordinates** come from the TCDS map layer, not the report
  (`data/atr/atr_stations_statewide.csv`, 280 stations).
- **Zone.** The fitting script reads each district's zone: D1/D2 Pacific, D3–D6
  Mountain.

On hand (`data/atr/`, gitignored): April 2026 for 24 stations, plus fallback months
for 5 (00048 2023-04, 00161 2025-04, 00275 and 00300 2022-04, 00291 2026-03). The
April files have no days dropped as outages. 00002 has 29 days and 00059 28, with
whole days missing.

### Tube counts and ATSPM

No files are on hand yet. The importers are built against the common layouts and
tested on synthetic data:

- `import_interval_counts`: one row per interval (15-minute default), a datetime (or a
  date column plus a clock column), and one volume column per direction.
- `import_atspm_volumes`: one row per signal × approach direction × bin (ATSPM's
  approach volumes, or per-detector rows, which are summed). Signal locations and
  routes come from a separate table. The approach direction is the direction of
  travel. Stop-bar detectors include turning traffic, and a dead lane detector
  undercounts.

Both drop an hour that is missing any interval rather than scaling it up. Naive
timestamps are read on the local wall clock.

### Fitting (`fit_profile`)

For one station-direction:

- **Usable days.** A usable day is a 24-hour local day (not a DST changeover) with
  all 24 hours. Its total must also be at least **0.5 ×** its day type's median at
  that station, or it is an outage (`OUTAGE_FRACTION`). Dropped days are listed in
  the provenance with the reason.
- **Hourly shape per day type:** `Σ_days vol(h) / Σ_days total`, a volume-weighted
  mean, so busy days count for more. It is fitted when the type has ≥ 1 usable day.
- **DOW factors:** each weekday's mean total over the mean of the seven. They are
  fitted only from ≥ 7 usable days covering every day of the week.
- **Borrowing.** A day type with no usable day is borrowed, and so are the DOW
  factors of a short count. They come from the generic curve nearest the fitted
  shapes, or from an explicit `fallback`. The provenance records `fitted`,
  `borrowed` and `borrowed_from`.
- **What the curve represents.** A month of counts gives that month's DOW factors.
  The curve is not seasonally adjusted, because the layer's MADT ratios carry the
  month.
- **Output.** A `VolumeProfile`, `basis: fitted`, `curve_id =
  fitted_<station>_<direction>`. The provenance adds `station`: id, direction,
  source, lat/lon, route, period, usable days per type, dropped days, and mean
  daily volume.

**Comparing curves** (`nearest_generic`, `cluster_profiles`): the **misplaced share**
is `½ Σ_h |a(h) − b(h)|`, averaged over the day types with week weights (5/7, 1/7,
1/7). It is the share of a day's volume that sits in a different hour: 0 means the
same shape, 1 means disjoint.

**The 55 fitted April curves** (29 stations):
- Nearest generic: `pm_commute_urban` 20, `am_commute_urban` 17, `rural_through` 9,
  `rural_recreational` 5, `balanced_urban` 4. `interstate_through` is never the
  nearest.
- Misplaced share to the nearest: median **0.061** (IQR 0.051–0.081). The maximum,
  0.223, is 00291's 7-day two-way count.
- Weekday peak-hour share: median 0.083 per direction (0.042–0.130).
- DOW: Friday 1.12, Saturday 0.93, Sunday 0.71 (medians).

### The station rule (`profile_assignment.station_rule`)

The new `curve_source` **`station`** sits between `override` and `inferred`, so the
order is **override > station > inferred > urban_rule > default**. A count measures
the shape that the inference only orients. For each station-direction:

1. **Snap.** Take the nearest segment that is on the station's route, not a ramp,
   travelling within **60°** of the direction's compass bearing (NB 0°, NE 45°, …),
   and within **0.25 mi** (`STATION_SNAP_MILES`).
   - An interstate number matches interstate mainline only.
   - `"84 BL"` matches business-loop segments of route 84.
   - A US/SH number matches any segment whose membership `routes` carries it,
     concurrencies included.
2. **Cover its route section** (Item 61; the Item 59 one-mile walk, `STATION_MAX_MILES`,
   is gone). The station covers the section its snapped segment lies on
   (`route_sections.trace_sections`, below). Of the sections through that segment it
   takes the one of its own route key (`"84 BL"` for a loop), else of its route
   number, else the longest.
3. **Nearest along the path.** A section holding several stations splits between them:
   each segment takes the station nearest along the path (to the segment's near end;
   `station_miles`). A segment on sections of two routes (a concurrency) takes the
   nearer of either's stations. `2WAY` and route-less stations cover nothing. Without
   sections (`sections=None`) a station covers only its own segment.
4. **A section with no station** falls through to inference / urban rule, as before.
   To keep a station's curve off part of its section (the owner is unsure Eagle Rd's
   Chinden-area ATRs apply all the way to I-84), add a hard stop scoped `stations`
   (below), or a segment override.

The station's **fitted** curve is assigned, or with `station_generic` its nearest
generic id. The assignment CSV is unchanged. The curves it uses that are not packaged
are written beside it as `d<N>_volume_profiles_curves.json`
(`write_assignment(..., profiles=)`). `assignment_profiles(path)` reads the packaged
library plus that companion, which is how the GUI and `build_statewide_catalogues.py`
resolve fitted ids.

**Coverage.** Every directional station snaps. Under the Item 59 walk, **258
segments** statewide got a station curve (D1 37, D2 18, D3 140, D4 16, D5 40, D6 7).
Under the Item 61 sections it is **1,520 segments, 891 mi** (D1 300, D2 138, D3 717,
D4 188, D5 23, D6 154). Every D3 I-84 mainline segment on the route (446) carries
one. D5 drops (40 → 23): its two US-91 ATRs sit on a 2.1-mi section between I-86 and
the I-15 BL, and the one-mile walk ran past both ends of it.

### Route sections (`route_sections.py`, Item 61)

A **section** is one direction of one route, traced as the Item 51 chains trace it
(`enumerate_mainline_chains(min_miles=0, hard_stops=...)`, then split into runs of one
route key), and broken:
- at a **junction with a state route of the same or higher tier** (owner, 2026-09-25;
  US and SH count as one tier, as the tier layer has them);
- at a **hard stop** scoped `both` or `stations`;
- at the **route's end**: the chain's end, or where the route leaves the road the chain
  follows (a concurrency ending, a renumbering the chain walked across).

A change of tier along the route is **not** a break without a junction there.

**Junctions** are found two ways at each boundary of a run of route `r`:
- *concurrency*: a route in one neighbour's set and not the other's;
- *geometry*: a state-route segment not carrying `r` within **25 m**
  (`JUNCTION_TOL_M`) of a section segment. The crossing is placed on the boundary
  nearer to where the lines are closest; XD breaks at interchange gores, not always at
  the overpass.

The joining route's tier is that of its own nearby segments (not carrying `r`). The
section's tier is the lower of the two segments either side, an untiered one taking the
nearest tier along the run. Two breaks for the same route within **0.5 mi**
(`JUNCTION_MERGE_MILES`) are one interchange: without that the Flying Wye stranded a
0.1-mi I-84 EB segment between I-184's merge and diverge. A crossing without ramps would
also read as a junction; in the state system that is rare.

**Business loops** are their own route key (`"84 BL"`), from `segment_context`'s
`business`. A loop never breaks its parent (it carries the same number).

**What the sections look like** (April 2026 run): D1 77, D2 96, D3 235, D4 192, D5 123,
D6 140. In D3:
- I-84 breaks only at the Flying Wye: 49 mi west (ATRs 00195, 00279, 00328 split it) and
  83 mi east (00002, 00262).
- Eagle Rd (SH-55) runs I-84 → Chinden (US-20), 4.5 mi, split between 00275 and 00330.
  North of Chinden has no ATR yet (00270 is Item 62's pull), so it falls through.
- **SH-44 does not break at Eagle Rd.** The tier layer has SH-44 through Eagle as
  **Expressway**, which outranks SH-55's State. So 00333 and 00340 share SH-44 from
  I-84 to US-20 (22.9 mi). That follows the rule as written. The owner should check it.
- SH-55 in Nampa is broken by the I-84 BL (State) as well as I-84, so ATR 00228 covers
  0.37 mi.

**Gaps it exposes (not fixed here):** D2 segment 771090123 on US-95 south of Moscow
has no route membership. The SB walk (and the corridor chain) ends there, so ATR
00146 SB covers 2.6 mi while NB covers 28.4 mi.

**Review tables** (`run_district_screening.py`, beside `dN_volume_profiles.csv`):
- `dN_station_coverage.csv`: per station-direction, its section (`section_from` /
  `section_to`), `section_miles`, `n_assigned`, `miles_assigned`;
- `dN_route_sections.csv`: every section with the stations on it;
- `dN_untiered_segments.csv`: state-route segments the tier layer left without a tier.

## ITD Highway Tier layer (`Highway Tier.geojson`, Item 61)

Owner-supplied (2026-09-25), gitignored, repo root. It is a GeoJSON export from the ITD
GIS app, which did not display it correctly, so the owner is not fully confident in it.
- **1,880 lines** in WGS84. Properties: `segcode`, `bmp` / `emp` (mileposts, strings),
  `tier`, plus `OBJECTID` and `LOC_ERROR`.
- **Tiers**, lowest first: District 487, Regional 702, State 544, Expressway 2,
  Interstate 145 (`itd_layers.TIERS`).
- **`segcode`** is the SHS `RouteId`'s first five digits, zero-padded to six
  (`001540` ↔ `01540AUS095`; `itd_layers.segcode`). **Four pieces write the travelway
  into it** (`A01540` / `D01540` on US-95 at mp 476–477, `A02350` / `D02350` at mp 80);
  the loader drops the letter.
- It covers only 201 of the SHS's 1,061 segcodes and has milepost gaps (SH-55 mp
  16.1–47.3, US-20 22.1–24.8 and 52.8–95.3).

**The join** (`itd_layers.segment_tiers`), per segment, first of:
1. `milepost`: the segment's own segcode piece whose `bmp`–`emp` holds its midpoint
   milepost (±0.05 mi); overlapping pieces take the highest tier;
2. `proximity_route`: the nearest piece of its own segcode within 40 m;
3. `proximity`: the nearest piece of any route within 40 m.

On the state-route segments it leaves **35 of 16,114 untiered** (D1 6, D2 13, D3 6,
D4 5, D5 1, D6 4). Sources statewide are mostly `milepost`; in D3, 3,032 milepost, 242
proximity_route, 237 proximity.

**The count check on Item 56**, at those 258 segments, comparing each station's
nearest generic with the curve Items 56/58 had assigned:
- **Inferred: 67 / 75 agree.** The inference never flipped an orientation. Its 8
  misses are AM-commute segments whose counts are closer to `balanced_urban`.
- **Urban rule: 50 / 183 agree.**
  - When it commits to a side it is mostly right: `am_commute` → 14 AM vs 5 PM, and
    `pm_commute` → 31 PM vs 5 AM.
  - But 35 of its segments count as `rural_recreational` or `rural_through`, and 34 of
    its `balanced_urban` ones as a commute shape.
  - I-90 at Coeur d'Alene, `interstate_through` under the rule, counts nearest
    `rural_through`.

## ITD State Highway System (`SHS_Primary.zip`, Item 52)

ITD's State Highway System layer, downloaded by the owner from ArcGIS Online
(2026-09-23) and renamed; a gitignored fixture. `itd_layers.load_shs` is the code
contract. It is the authority for **which road is a state route**.

- **Format:** 1,179 lines in EPSG:8826, about 6,000 mi, stored as `Measured 3D
  LineString`. GDAL can't read the M values, so they arrive as Z and `load_shs` drops
  both; the measures that matter are the `FromMeasur`/`ToMeasure` fields. One record
  (`LocError` "PARTIAL MATCH FOR THE TO-MEASURE") is a two-part `MultiLineString`,
  which is exploded into 1,180 parts. `FromDate` runs to 2026-07-28 and no `ToDate`
  is set: the layer is current. `load_shs(as_of=...)` filters by date.
- **`RouteId` uses the AADT layer's scheme** (`01910AUS012` is US-12), so
  `aadt.parse_route_id` reads both. `load_shs` renames it `RouteID`. `SignTypeCo`
  (1 = I, 2 = US, 3 = SH) always matches the class band, and `SHSNumber` the number.
- **The codes**, as read against the AADT descriptions of the same route ids:

  | Field | Code | Meaning | Evidence |
  |---|---|---|---|
  | `RoadType` | 4 | **roadway** — the route itself | 304 lines, ~5,700 mi |
  | | 5 | **ramp** | 724 of its 794 lines match an AADT ramp record (`EB OFF TWIN FALLS IC173`) |
  | | 6 | other short pieces (rest-area and interchange links) | 81 lines, 31 mi; INRIX segments on them are unnamed FRC 5 |
  | `RouteTypeC` | 1 | mainline route | 1,018 lines (ramps included, via `RoadType`) |
  | | 2 | **spur** (`LoopSpurCo` numbers it) | `US-95 SPUR` at Weiser, SH-77's Elba–Almo spur `05100ASH077`, `US-20 SPUR` |
  | | 3 | **business loop** | Mountain Home's I-84 Business `01020AIN084`, Pocatello's I-15 Business `01360AIN015`, Twin Falls' US-93 Business `02043AUS093` |
  | | 4 | **connector**: short wye, turn and couplet links | `C ST & US-95 SB COUPLET` (Moscow), `SIMPLOT BLVD (SH-19)` |
  | `Travelway` | A | the primary carriageway | 1,110 lines |
  | | D | **the second carriageway of a divided road, or a couplet's second leg**; the route id's letter is `D` too | `01010DIN084` runs all 275 mi of I-84 beside `01010AIN084`; 63 roadway lines, 776 mi. Couplets too (Item 51): Moscow's Jackson St `01540DUS095` beside Washington St `01540AUS095`, Boise's Front St, Nampa's 2nd St S, Pocatello's 4th Ave, Blackfoot's Judicial St, Twin Falls' 2nd Ave |

  **Mainline evidence is `RoadType` 4 only.** A spur and a connector *are* their
  route. A business loop is **state highway and belongs to its parent route** (owner,
  2026-09-23): membership puts it in the parent's inventory and labels it `business` so
  an analysis can separate loop from mainline. A street that stopped being a business
  loop (Caldwell's Cleveland Blvd / Blaine St) has no SHS line and drops out like any
  other off-system road.
- **"Primary": one route per road.** US-2/95 at Sandpoint is recorded as 95,
  US-20/26/93 as 93, and ID-3 on SH-8 as 8. Concurrency is **not** in the layer, so
  the INRIX `RoadList` still supplies it (`concurrent`).

### How membership reads it (`routes.route_membership(..., shs=...)`)

The SHS holds nothing but state highway. So where the AADT layer needed a local
(`OH`) record *on* a segment to take a route away, here the **absence** of any SHS
line within 40 m is the evidence. The geometry tests keep Item 48's distances (*on*:
≤ 10 m and ≥ 80% alongside; *near*: ≤ 40 m and ≥ 50%), with two changes:

- **Direction is compared at every sample, not at one point**
  (`routes._aligned_fraction`). ID-162 above Kamiah lies exactly on its SHS line, but
  at the single point where they touch, their tangents differ by 54°. The one-point
  bearing gate rejected it, and about 7.6 mi of real state route lying on its own
  line (ID-162, SH-52 in Emmett, ID-33, SH-128, …) first came out "off the system".
- **A divided road's carriageways each lie on a line.** Where the SHS draws both
  carriageways of a route near a segment and the segment is within 15 m of neither
  for at least 25% of its length (`CARRIAGEWAY_MIN_COVERAGE`), it is a parallel road,
  not the route. Silver Valley Rd, a frontage road INRIX numbers 90, sits between
  I-90's lines with 0% on either; real carriageways measured 45–100%. Item 48's
  40 m *near* test would still confirm such a road.

The order of the rules for a numbered segment:
1. evidence *on* the segment outranks evidence *near* it. An SHS business line lying
   on Rigby's Farnsworth Way beats US-20's carriageway 21 m away, so the segment is
   labelled `business`, not `agree`;
2. then `agree` / `concurrent` (a member line of an own route near it);
3. `business`: the parent route plus every route its `RoadList` names (Caldwell Blvd
   is 55/84);
4. `renumbered`;
5. the parallel-road `inrix_only`;
6. `inrix_only` when no line is near at all;
7. otherwise the SHS can't decide (another route's line is near but not on it), and
   the **AADT layer decides** (`source = aadt`).

An unnumbered segment gets `itd_only` from a member line on it, or `business` with the
parent route from a business line on it (Kellogg's Markwell Ave). Otherwise Item 48's
rules still apply: an interstate *mainline* band is never given to an unnumbered
segment, and neither is a band that `RoadList` names only as a business route. The membership CSV's new
`source` column says which layer decided. On D1–D6 the SHS decides every non-ramp
segment but 2.

**What the SHS changed, against Item 48** (Session 67, 124.0 mi of route membership;
the per-road table is `out/highways/route_membership/item52_changes_vs_item48.csv`):
- **Business loops stay in their parent route** and are labelled `business`. Where
  INRIX's `RoadList` names another route, that route is kept too, which *adds*
  75.1 mi of concurrent membership. For example Twin Falls' E 3900 N is 30/93, and
  Pocatello's 5th Ave is 15/30/91. Unnumbered pieces lying on a business line join
  their parent: Kellogg's Markwell Ave, Wallace's River St, Post Falls' E Seltice Way,
  American Falls' Pocatello Ave.
- **Dropped as off the system (36.4 mi):** Item 48's `unconfirmed` roads, and a few it
  had agreed:
  - WY-89 and Widmer Ln in Bear Lake;
  - Elmore's Old Highway 30, and Caldwell's Cleveland Blvd / Blaine St (no longer a
    business loop) and Northside Blvd;
  - Payette's S 7th St / 7th Ave N, and Cedar St in Sandpoint;
  - Kellogg's W Cameron Ave, and Sherman Ave in Coeur d'Alene;
  - Rexburg's Center St / N 7th E, and Old Highway 81;
  - Silver Valley Rd where it runs beside I-90, and the stubs past route ends.
- **Reversed:** Item 48 added Sun Valley Rd as SH-75 (4.8 mi); the SHS has no line on
  it.
- **Confirmed:** 124.5 of Item 48's unconfirmed miles are on the system. Most are the
  second carriageways of I-84/I-86/I-15/I-90 (now on their `D` lines) and the new
  US-95 alignment.
- **The Reisenauer override is retired.** The SHS leaves Reisenauer Rd off, the same
  answer the override gave. `scripts/route_overrides.csv` keeps the retired row as a
  comment.
- **Item 48's 53 "agree" miles that miss a 12 m buffer** are 138 segments that are
  confirmed only by the 40 m *near* test. They are alignment offsets, not errors:
  - W Chinden Blvd, partly 10–20 m off its line;
  - US-93 north and south of Twin Falls, mostly drawn as one line, 10–30 m off each
    carriageway;
  - short segments whose end runs past a line break.
- **Banks-Lowman Hwy** (D3, 65.8 mi, the old SH-17) is not on the system and comes
  out `off_system`. It stays in the curated D3 inventory and the export **on
  purpose**, for analyses outside the ranking (owner). Membership is what filters
  it out of the ranking.
- **The curated D3 lists take the membership** (Item 49, owner decision
  2026-09-23; `scripts/apply_d3_membership.py`, `routes.reconcile_curated_list`).
  77 segments / 23.5 mi that membership leaves on no route are removed from the
  master and the per-highway lists:
  - Cleveland Blvd 8.0 mi, Old Highway 30 (Elmore) 3.8, Northside Blvd 2.1,
    Payette's 7th Ave N 2.0 / S Main St 1.8 / S 7th St 0.8, and Blaine St 2.0;
  - short stubs making up the rest.

  Banks-Lowman is exempt. The 16 SHS segments (1.27 mi) the lists lack are an
  add-list in `out/export_reconciliation/item49_d3/`. The segments stay in
  `d3_store.duckdb`. The old lists are kept in `out/highways/pre_item49/`.
- **Screening's AADT join reads membership too** (Item 49). `run_district_screening`
  resolves `RoadNumber` through the district's membership file before the join,
  the same reading the catalogue builder walks, and the provenance records the
  file.

## ITD urban areas (`Urban_Area.zip`, Item 52)

26 Census 2020 urban-area polygons (EPSG:8826): `UACE` (a 5-digit code; kept as a
string), `NAME` ("Lewiston, ID--WA", "Ontario--Payette, OR--ID"), `Population`,
`HouseUnits`, `PopulDensi` (people per sq mi). Boise City (433k) down to Shelley (5k).
Bonners Ferry, Kellogg and Salmon are **not** urban areas. `itd_layers.load_urban_areas`
loads them. `itd_layers.urban_context` gives every segment:
- `urban_uace` / `urban_area`: the area its midpoint lies in, or the *nearest* one
  when it is outside every area;
- `urban_inside`;
- `urban_share`: the share of its length inside any area;
- `urban_edge_m`: the signed distance of its midpoint to that area's boundary,
  positive inside.

`build_route_membership.py` writes the table to
`out/highways/route_membership/d<N>_urban_context.csv`. Of the 9,647 mi on a state
route in D1–D6, 810 mi lie inside an urban area; Boise City has the most (150 mi),
then Nampa (81 mi).

**Context, never a gate** (owner, 2026-09-23). The boundaries show where to look for
a rural/urban transition when an extent ends (Item 50), not where it must stop.

`itd_layers.urban_centroids` gives each area's polygon centroid (computed in UTM) and
population, for the Item 56 radial rule. A centroid is not a city centre: Boise
City's lies ~7.5 km west of downtown. `scripts/urban_centres.csv` supplies the
economic centre instead (see *Curve assignment*).

## Corridor cores from recurring congestion (`extents.py`, Item 50)

Until Item 50, a corridor core was peak TTI ≥ 1.20 against INRIX's `Ref Speed` over
≥ 0.75 mi of segments. That test let through grades that are just as slow at 2 a.m.,
roads with 180 AADT and mostly imputed data, and single long rural segments. The
generator now judges each segment against **its own baseline**, and scores a core on
delay and data quality.

**The baseline screen.** `screen.segment_screen(windows=screen.BASELINE_WINDOWS,
quantiles=extents.BASELINE_QUANTILES)` covers `am`, `pm`, `night` (22:00–05:00, every
day) and `weekday` (all hours, Mon–Fri). It carries the per-window `tt_p10`/`tt_p15`
quantiles of gated travel time. `build_statewide_catalogues.py` caches it as
`out/statewide_screening/d<N>/segment_baseline_screen.parquet`; `--refresh-baseline`
rebuilds it after an ingest. Each district takes about 15 s in DuckDB.

**`Pct Score30` is the real-time share.** It is 0–100 per row. The screen reports its
**ungated** mean as `realtime_share` (0–1), overall and per window. It tracks the
CValue gate closely: on D1, rows that pass `CValue > 80` average 91.7 and rows that
fail it average 1.2. A segment's peak-window share is therefore the plain-language
version of "how much of this is real data".

**Baseline per segment** (`extents.segment_congestion`):
1. the night mean, when the night has ≥ 100 gated observations;
2. otherwise the weekday 15th percentile (`weekday_tt_p15`), when the weekday has
   ≥ 100;
3. otherwise the baseline is **unknown**. The ratio and delay are NaN. Inside a run
   the segment is bridged like a gap, but its miles are left out of the per-mile
   rates. It is never counted as free flow with zero delay.

Nearly every urban segment gets the night baseline. The fallback is mostly used on
rural roads whose overnight data is imputed (Galena, Lowell).

**Per-segment measures.** `ratio` = worst peak window ÷ baseline. `weight` = a smooth
ramp from 0 at 1.05 to 1 at 1.20. `delay_min` = peak − baseline (floored at 0).
`vhd_index` = delay × AADT 2025 / 60, on the same relative-weight basis as
`aadt.vehicle_hours_of_delay`; `vhd` is the curve-weighted VHD against the baseline
in the peak window (Item 58; the builder always passes `bins` / `curves`), per
weekday. Each core's `_core` stats carry both. Session 65's peak/night ratios hold on these data:
real hotspots run 1.2–2.4 per segment, geometric roads 0.95–1.05.

**Core floors.** Calibrated on the Session 65 list (Session 69). All of them are in
`catalogue["_generated"]["thresholds"]`:

| constant | value | why |
|---|---|---|
| `CORE_SEED_RATIO` | 1.10 | a core starts and ends on a segment at least this congested |
| `CORE_GAP_SEGMENTS` / `_MILES` | 2 / 0.5 mi | a core bridges up to this much in between (so SH-8's 1.199 is inside it) |
| `SEGMENT_MILES_CAP` | 0.5 mi | a segment counts toward effective miles only up to this |
| `MIN_EFFECTIVE_CORE_MILES` | 0.6 | Σ min(miles, cap) × weight; one long segment can't pass alone |
| `MIN_CORE_VHD_PER_MILE` | 0.8 | a **noise floor**, not a policy cut: a cut between two real towns would be arbitrary. It removes only the rural geometric and low-volume roads; the smallest towns (Blackfoot, Bonners Ferry, Soda Springs, at 4–5× the floor) stay in and rank low. Thinning to a top-N is the ranking's job (owner, Session 69). 10 on the two-way index, 5 per direction (Item 54), × 0.165 onto the curve VHD per weekday (Item 58: the median core ratio), rounded down |
| `MIN_CORE_VHD` | 0.8 | total vehicle-hours per weekday, the same noise floor (10 → 5 → × 0.165) |
| `MIN_REALTIME_SHARE` | 0.90 | mile-weighted, peak window. Keep list ≥ 0.98; Lowell 0.01, Benewah 0.02–0.03, Idaho County 0.16, Gilbert Grade 0.02–0.04, Galena 0.01 |
| `SPILL_RETENTION` | 0.5 | Tier 2 grows while the grown extent keeps ≥ 50% of the core's VHD/mi |
| `CONTEXT_PAD_MILES` | 3 mi | Tier 3 goes at most this far past Tier 2 |

**Tiers.**
- **Tier 1** is the core, and the only tier that ranks.
- **Tier 2** grows the core outward. It stops at the first of: a junction/FRC/AADT
  split, more than 2 segments (0.5 mi) under 1.05, or dilution. Past an urban-area
  boundary it bridges no gaps: it continues only through segments that are themselves
  congested and don't dilute the core.
- **Tier 3** is context: out to the next split, the urban edge (for an urban core),
  or 3 mi, whichever comes first. The longest Tier 3 is now about 10 mi (it used to be
  180).
- Tiers 2 and 3 carry `_ranked: false`. `aggregate_statewide_rankings.py` writes them
  to `statewide_*_context_extents.csv`, each with its facility's core rank.

**Each direction answers for itself.** The opposing carriageway is catalogued only
where it has a qualifying core of its own that overlaps the lead's Tier 2 footprint,
and then with its own boundaries. Otherwise it is dropped, and the group's
`_companion` says why. For example: "EB not catalogued: … (peak/baseline 1.01,
16 VHD/mi over the mirrored span)".

**Every qualifying core on a chain is its own facility.** Before this, only the
longest core on a chain was catalogued. That lost Hailey behind Ketchum on SH-75. A
second facility is named after the town its core lies in (`SH-75: Blaine County
(Hailey)`).

**Monthly profile and flags (never exclusions).** `screen.segment_monthly_screen` gives
AM/PM travel time per segment per local month. It is cached as
`segment_monthly_screen.parquet` (about 18–28k segment-months per district).
`extents.monthly_delay_profile` turns it into a core's VHD per month, against the
segment's **export-wide** baseline, so a work-zone month shows as delay instead of
moving its own baseline. Each facility carries `_monthly_vhd`, and `_flags` when its
busiest third of months holds a large share of the delay (an even spread over 8
months is 0.375):
- `episodic` (≥ 0.70): check for a work zone or event. I-90 WB Coeur d'Alene 0.96,
  US-95 SB Sandpoint 0.77.
- `seasonal` (≥ 0.55): recurring but summer-heavy. Soda Springs 0.68, Burley I-84 BL
  0.67, Twin Falls Blue Lakes 0.66, Ketchum 0.65, Victor 0.63.
- 30 of the 39 cores sit at 0.40–0.47.

The flags travel into the statewide ranking as a `flags` column.

**Audit.** `out/statewide_screening/d<N>/core_audit.csv` has one row per analysed
direction: its best candidate, the candidate's metrics, and the floors it failed.

**I-90 westbound in Coeur d'Alene is a summer-2026 event.** The winter/summer ratio
of 0.43 is real. Look at weekday 16:00–18:30 travel time over the five core segments
(4th St IC 13 to Northwest Blvd IC 11):
- 1 January to 16 June it is 1.62–1.76 min, the same as the night's 1.7.
- It rises on Mon 22 June (3.1) and stays at 5–8 min from Tue 23 June through
  August. Weekends and middays rise with it; nights don't.

A permanent step on one day, affecting daytime and weekends but not nights, looks
like a daytime work zone rather than recurring commute congestion. It stays in the
ranking, carrying the `episodic` flag.

## Chains across route-numbering changes (`extents.py`, Item 51)

Until Item 51 a chain was one `RoadNumber` walked along `NextXDSegI`. A road whose number
changes along its length came out as several short chains, and each one had to clear the
1-mi minimum and find its own core. Four facts about the data shape the replacement:

- **The SHS records one route per road, so concurrency comes from INRIX.** Moscow's US-95
  couplet is route 95 alone in the SHS, but SH-8 runs on it, and the couplet segments'
  `RoadList` names `ID-8`. Membership's `routes` column adds the `RoadList` route only
  when ITD and INRIX disagree (`concurrent`), so an `agree` segment carries one route.
  A chain walks a route over its membership routes **plus** the routes its `RoadList`
  names, for segments on the system (`extents.segment_route_sets`). Ramps are never
  members.
- **The link follows INRIX's through movement, not ITD's route** (trap 4). Where a route
  turns, its walk ends at the junction and resumes on a segment nothing in the route
  links into. A tail is **joined** to such a head when the head starts on the tail's
  last segment (within 20 m, past its first half), and the turn is ≤ 120° (a corner,
  not the other carriageway). Where both lie on one SHS line, the mileposts must agree
  within 0.25 mi.
- **The SHS mileposts show the concurrency.** A segment's ends are projected onto its
  own route id's line and the measure interpolated (`itd_layers.shs_mileposts`). SH-8's
  line stops at mp 1.92 at 3rd St & Washington St and resumes at 2.35 on Troy Rd; the
  0.43 mi between is the couplet. Eastbound, SH-8 turns off 3rd St onto Jackson St one
  block *before* its own line ends. A junction may leave such a **stub** (≤ 0.3 mi of the
  route's own run) only when the head's run comes back to the tail's line further
  along, within 2 mi (`stub_junction`, recorded with `mp_leave` / `mp_return`).
- **A street keeps its name when its number changes.** Yellowstone Hwy in Idaho Falls is
  US-91, then I-15 BL / US-26, US-20 BR / US-26 on Northgate Mile, then US-26 alone.
  Chains of different routes are **merged** where the same street runs straight on
  (≤ 60° turn): tail to head, or where one route arrives onto the street, or turns off
  it, mid-chain (US-26 arrives from Sunnyside Rd). A merge that cuts a chain needs the
  street to run on for ≥ 0.5 mi on both sides. At Troy, SH-8 on S Main St is renamed
  "ID-8" where SH-99 starts down S Main St for 0.04 mi: that is a route changing its
  name, not a street changing its number. The street's own name decides, not
  `RoadList`.

**What the walk produces.** SH-8 is one chain each way from the Washington line through
Moscow (via Jackson St eastbound and Washington St westbound). Yellowstone Hwy is one
75.6-mi chain each way (US-91 → US-26), and Broadway east of I-15 is part of US-20's.
A concurrent segment can lie on two chains. A chain wholly inside a longer one is
dropped. Per district, 37–49 chains (Item 50: 40–62).

**Junctions as repairs.** `corridors.build_chain`, which resolves every catalogue
entry, walks the link alone. Two mechanisms carry the joins to it:

- `route_junction` rows in `scripts/d<N>_link_repairs.csv`
  (`scripts/generate_route_junctions.py`, `extents.route_junction_repairs`). A tail
  junction is written only where the segment's own link is null or leaves **every**
  route it carries, and all its routes turn onto the same head. 98 statewide: D1 17,
  D2 14, D3 24, D4 22, D5 13, D6 8, with Payette, Lewiston (Main St ↔ Levee Byp),
  SH-8 in Moscow and at Bovill among them. They cross `XDGroup`s, unlike Item 38's.
- A catalogue entry's own `links` (`[[segment, next], …]`, parsed by
  `corridors.parse_catalogue`). These are the steps a generated extent takes that the
  network doesn't assert: stub junctions and renumbering merges, which can't be global
  patches. At Sunnyside Rd the US-26 chain follows the street onto US-91 while the
  I-15 BL approach keeps the link. `resolve_catalogue` applies them to that entry only
  and counts them in `n_repaired_links`.

**`build_chain` at a junction point.** When the start point is exactly where one segment
ends and the next begins, both snap at the same distance. The tie now goes to the
segment the point *begins* (and, for the end point, the one it *finishes*). Myrtle St
EB would otherwise have started on I-184 once its route junction let I-184 walk onto
Myrtle. The same rule dropped a phantom end segment (0.1–0.5% in extent) from D3's
`myrtle-eb` and `sh69-sb`; their requested miles are unchanged.

**One segment, one ranked core — or a flag.** With chains following concurrency, two
facilities can hold the same pavement. Cores are claimed across all chains, strongest
first:
- a core with ≥ 50% of its miles already in a stronger core, or in its Tier 2, is that
  queue seen from a concurrent route;
- what is left of it, re-found with those segments excluded, stands if it still
  qualifies (Pocatello's I-15 BL up Pocatello Creek Rd / Alameda Rd); otherwise it is
  absorbed;
- a core that only *shares* some pavement keeps it and carries a
  `shares <mi> mi with <facility>` flag. US-95's Moscow core shares 0.91 mi of the
  couplet with SH-8's.

**The other direction is found on the ground, not by pairing.** A lead core's companion
is the strongest qualifying core, on **any** chain sharing a route on its pavement, that
runs beside the lead's Tier 2 (≥ 50% of it within 200 m) and **against** it (its ends
projected onto the Tier 2 in travel order land backwards). One-to-one `pair_chains` and
cardinal bearings both failed here:
- SH-8 eastbound is three chains (a data gap past Bovill);
- Burley's Overland Ave is a southbound SH-27 chain one way and an eastbound I-84 BL
  business-loop chain the other.

A mirrored span claims the other direction's ground only where it runs alongside and
covers ≥ 30% of the footprint. A chain that merely touches it at a junction doesn't
count: Twin Falls' US-93 on Pole Line Rd ends where US-93 turns onto Blue Lakes Blvd.

**Names say what the road is.** A facility is named `<band>: <street>, <town>`:
- **band:** the route carrying most of the core's miles, with `BL` / `BR` / `Spur` when
  the core's `RoadList` says so (`I-15 BL`);
- **street:** the core's street by `RoadName` miles, with its leading quadrant
  dropped, plus a second street when it has ≥ 30%. A segment named only by its route
  (`US-20`, `Highway 95`) gives no street, and its `RoadList` byway aliases
  ("Idaho Medal of Honor Hwy") are not read;
- **town:** Item 52's urban area, else "<County> County".

Examples: "US-26: Yellowstone Hwy, Idaho Falls", "SH-8: Pullman Rd, Moscow", "I-90:
Coeur d'Alene". Couplets read "US-95: Washington St / Jackson St couplet, Moscow", and
each leg is labelled by its own compass direction.

**Couplets must be one-way pairs** (`couplets.detect_couplets`, with membership applied):
- both legs must be on the state system, and share a route **under membership**, not
  `RoadNumber`;
- `drop_same_line_pairs`: both legs mostly on **one** SHS line (route id *and*
  travelway) are one two-way road. Shoshone's S Greenwood St / US-93, both on
  `02220AUS093`, 172 m "apart" only because one runs on from the other;
- `drop_divided_pairs`: the `A` and `D` lines of one route closer than 50 m are a
  divided highway (American Falls' ID-39 S / N at 28 m). `D` alone is no evidence: real
  couplets sit a block apart (Blackfoot 78 m, Moscow 179 m);
- `trim_leg_overhang`: a leg's end segments are trimmed while they carry both
  directions of their street or lie > 300 m from the other leg. Pocatello's 5th Ave ran
  0.65 mi north of 4th Ave and 1 mi south onto two-way pavement: 2.80 → 2.34 mi against
  the registry's 2.22. Weiser went from 0.79 to 0.57 (registry 0.52).

`out/statewide_screening/couplet_review.csv` lists every detected pair with the test
that decided it. Chubbuck's Quinn Rd / US-91 and SH-43 / E 105 N fail (two-way legs).
Moscow, Boise, Nampa, Weiser, Twin Falls, Pocatello and Blackfoot pass. Sandpoint is
out of `KNOWN_COUPLETS`: a divided highway, not a couplet (owner). For the owner:
Mountain Home's N Main St / 2nd St E (I-84 BL, 35.5 m) survives; D3's catalogue is
curated and doesn't carry it.

## Manual hard stops (`hard_stops.py`, Item 60)

The chain walk stitches a route across junctions and renumberings. The owner sometimes
reads a corridor as ending where the walk runs on: a **logical terminus** (FHWA NEPA
sense), or a real change of context (a downtown couplet vs the two-way road feeding it).
No screening signal finds these, so they are authored in
`scripts/corridor_hard_stops.csv` and ingested as **input only**. The cores, tiers and
floors are unchanged.

**The table.** It has `#` comment lines, like `route_overrides.csv`, and one row per stop:
`district, route, direction, lat, lon, xd_seg_id, side, note`, plus an optional
`scope` (Item 61): blank or `both` = corridor chains and count-station sections;
`corridors` = the chains only; `stations` = only a count station's reach.
`stop_boundaries(resolved, use=)` keeps the rows for one use (default `corridors`).
- `route` is the number (`55`, not `SH-55`).
- `direction` is `NB`/`SB`/`EB`/`WB`, or blank for both.
- Each row names its place one of two ways:
  - `lat, lon`: a point near the boundary;
  - `xd_seg_id` + `side` (`before`/`after` that segment).
- `note` is required: why it is a terminus, and who decided.

`read_hard_stops` validates the table and names the bad rows.

**A stop is a segment boundary** `(from_seg, to_seg)` on the route
(`resolve_hard_stops`).
- **Candidates.** Consecutive pairs on the stop-free chains, plus `NextXDSegI` links
  between two of the route's segments.
- **Route.** **Both** segments must carry the route. A US-95 stop at the couplet's south
  corner in Moscow therefore does not also cut SH-8's turn there, and vice versa.
- **Point rows.** A point takes the nearest boundary within 100 m, together with
  **every** other boundary at the same place (within 5 m).
  - A blank direction also takes the nearest boundary running the other way (no
    bearing in common, one opposite). That gives both carriageways at a divided
    junction, or both couplet legs where they meet at one corner.
  - The "same place" rule matters. Where the walk turned off the link, the link's own
    continuation is still a candidate there. Cutting only the turn hands the walk that
    stub:
    - W Front St runs on 0.15 mi past the Connector;
    - SH-8 eastbound on 3rd St could run on along the westbound-only block to
      Washington St.
  - Such link-only boundaries are reported with `on_chain = False`.
- A row that resolves to nothing is an error listing every such row, never a silent
  drop.

**How the chains take them** (`extents.enumerate_mainline_chains(hard_stops=)`,
`{(from, to): reason}` from `stop_boundaries`):
- the link is cut;
- junction joins, stub joins and renumbering merges refuse a stop boundary;
- a final pass splits anything that still steps across one.

`linked_into` still reads the uncut links, so the segment past a stop does not become a
head for some other junction join. Each chain records the stops it ends at (`stops`,
`stop_at("start"|"end")`). An end can sit on two stops: the Moscow couplet's
northbound leg starts past both US-95's and SH-8's.
- A piece with a stop at **both** ends is an owner-declared section. It is kept below
  the 1-mi chain minimum: Moscow's couplet legs are 0.63–0.65 mi.
- A piece with a stop at one end only is still a stub (the W Front St stub, 0.15 mi).
- Of two identical walks (US-20 and US-26 on Myrtle St, once the Connector is cut
  away), the lower route number names the chain. Before, the order was arbitrary.

**In the catalogue.** An extent that runs to a chain end at a stop gives it as its
boundary (`SplitKind.HARD_STOP`, "a manual hard stop (route 55: …)"). The core audit
gains `chain_stops`, and `_generated.hard_stops` lists the boundaries used.

**Duplicate sections need nothing new.** Once the Moscow stops cut SH-8 at the couplet,
SH-8's couplet pieces lie wholly inside US-95's. The Item 51 rule, which drops a chain
wholly inside a longer one, removes them. So the couplet is one corridor (US-95's), with
no explicit duplicate mapping.

**The seed rows** (owner, 2026-09-25) resolve to 15 boundaries:
- **D3, 6 boundaries:**
  - Eagle Rd | SH-44, both ways (the SH-55 chain turns east along SH-44 there);
  - Broadway | Front St (NB);
  - Front St | I-184 W, plus the W Front St stub (WB);
  - I-184 E | the EB Connector's last segment (EB).
- **D2, 9 boundaries:**
  - US-95: south | couplet (both), couplet | north (NB and SB rows: the legs rejoin
    Main St 120 m apart);
  - SH-8: couplet | Troy Rd (both), Washington St | 3rd St (WB), and 3rd St | Jackson
    St plus the 3rd St block (EB).
- **Broadway's other side is left alone.** Myrtle St EB → Broadway SB is not linked, and
  the walk never joined it, so the chains are separate already. No SB row is needed
  there.

`scripts/audit_hard_stops.py` writes three files to `out/hard_stops/`:
- `resolved_hard_stops.csv`;
- `hard_stops_map.html` (the segment before each boundary in blue, the one after in
  orange);
- `catalogue_crossings.csv`: every committed catalogue entry that steps across a stop.

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

**Why DuckDB (not SQLite).** Its columnar engine is the right fit for the analytic
scans of a ~2M-row export, and it reads a CSV stream directly, which is what makes
the district-scale streaming ingest below possible. It is declared as a **direct**
dependency in `pyproject.toml` (Item 35) — `store.py` imports it — rather than left to
arrive transitively via `traffic-anomaly`'s `ibis-framework[duckdb]`. The DuckDB **`spatial` extension is
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

**Date push-down (Item 25).** `load_export` / `load_dataset` take optional
`date_start` / `date_end` (inclusive **local calendar dates**) plus a `tz`, and push
the restriction *into the DuckDB scan* — an area accumulates indefinitely under the
merge model, so pulling it whole then trimming in pandas is what this avoids. The
bounds are the **half-open UTC instants** `[local_midnight(start), local_midnight(end)+1 day)`
computed in `tz` (defaulting to UTC), so the pushed-down cut is *exactly* the frame
`timebins.filter_date_range` would keep after `io.to_local` — comparing the stored
UTC `Date Time` against those instants is tz-invariant, so it matches to the row,
DST included, with no pandas re-trim. An over-narrow range returns an empty (still
typed, tz-aware) frame. `area_local_span(area_key, tz)` reads the registry's
`date_min` / `date_max` back as local calendar dates for the GUI's picker bounds
(the trimmed frame no longer carries the full range).

**Layout.** Per-area tables keyed by `area_key`; two shared registries:

| table | contents |
|-------|----------|
| `_areas` | registry: one row per area — `area_key` (PK), `area_name`, `corridors` (JSON), `units_*`, `n_segments`, `date_min/max`, `has_geometry`, `has_aadt`, `schema_version`, `created_at`, `updated_at` |
| `_ingests` | provenance: one row per ingested export — `area_key`, `source`, `bin_minutes`, `n_rows_added`, `date_min/max`, `ingested_at` |
| `_names` | friendly segment names (Item 27), **global** — `Segment ID` (PK), `name`, `inrix_label`, `updated_at`. Not keyed by area (INRIX Segment IDs are globally unique, so a name applies everywhere the segment appears). |
| `obs_<area_key>` | the merged `io.load_data` rows for the area + a `bin_minutes` partition column (`Date Time` as `TIMESTAMPTZ`) |
| `meta_<area_key>` | the merged `io.load_metadata` frame (Segment ID as a column; `Combined` included) |
| `geo_<area_key>` | the **processed** GIS join: `Segment ID`, `source`, the XD identity columns (`FRC` / `RoadNumber` / `RoadName` / `RoadList` / `Bearing`), the `join_aadt` columns (`AADT` / `aadt_source` / `aadt_dist_m` / `aadt_record_kind` / `aadt_desc` / `RouteID` / `Route` / `Commercial`), geometry as a WKB blob (`_geom_wkb`, `NULL` for a `missing` segment). Cached at ingest so the Item 18 spatial join runs **once**, not per load. |

Exports whose column sets differ (an extra column) still merge — a missing column
fills `NULL`, a new one is added via `ALTER TABLE ADD COLUMN` (`_align_columns`), and
the anti-join insert uses `INSERT … BY NAME`. NULL-key rows are dropped before the
merge (they can't participate in keep-first dedup — Item 26 / R6).

**Friendly names (Item 27).** `store.save_names` upserts into `_names`
**last-write-wins** per `Segment ID` (`INSERT … ON CONFLICT DO UPDATE` — an edit
*overwrites*, unlike the keep-first observation merge); a **blank** name *deletes* the
row, clearing the override so the segment falls back to its INRIX seed. `load_names`
returns the `Segment ID`-indexed override frame that `names.apply_names` layers over
the seed, applied automatically on every load (the file path too). This retired the
old `segment_names.csv` round-trip (the CSV writers moved to `legacy/names_csv.py`).

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

**Streaming ingest (`ingest_export_streaming`, Item 35).** `ingest_export` reads the
whole export into pandas first, which a district download makes impossible: the 2026
D3 export is **45.4 M rows in part 1 alone** (3.1 GB of CSV inside a 263 MB zip, split
across parts **by segment**, not by date — every part carries the whole date range).
The streaming path decompresses the zip member with `zipfile.ZipFile.open()` and feeds
it to DuckDB's `read_csv` through a **named pipe** (`os.mkfifo`; a temp file where
FIFOs don't exist), landing the *same table* `ingest_export` lands — same area, same
keep-first merge, same column types, same row order — with no whole-frame read.

Three things the implementation had to get right, all measured:

- **Memory is bounded by chunking, not by laziness.** DuckDB buffers a statement's
  appended rows until it commits, so a single `CREATE TABLE AS SELECT` over a part
  grows to roughly the size of the CSV (1.8 GiB resident for 1.5 GB of input, then
  killed). The member is therefore cut into line-aligned chunks (default 256 MiB,
  `chunk_bytes=`), each staged and merged in its own committed statement, so peak
  memory tracks the chunk size rather than the export. The scan is single-threaded
  (`parallel=false` — a pipe cannot be split), which is also what keeps the stored row
  order identical to the pandas path's.
- **Column types come from the same rule pandas uses.** Every column is read as
  `VARCHAR` (nothing may be sniffed — a pipe cannot be rewound) and cast in SQL by
  `io.load_data`'s rules: `TRY_CAST` is `pd.to_numeric(errors='coerce')`, `nullstr=''`
  is pandas' empty-field NaN, and `Road Closure` coalesces to `FALSE` (pandas'
  `.astype(str)…eq("T")` on a missing value). A numeric column is then **narrowed to
  `BIGINT` only when it holds no nulls and no fractions** — `pd.to_numeric`'s own
  int64-vs-float64 rule — which is why the real Myrtle export round-trips with
  `Ref Speed` as `BIGINT` and `Speed` as `DOUBLE` through both paths.
- **A column's type is no longer fixed by the first export.** Merging now *widens* an
  integer column to `DOUBLE` when the incoming rows are fractional (`_widen_columns`).
  Without it, an export whose `Speed` read `30, 31` typed the column `BIGINT` and
  DuckDB's insert cast silently truncated a later export's `30.5` to `31` — a latent
  bug on **both** ingest paths, not just the new one.

Two deliberate differences from the pandas path, both documented on the function: the
**bin-length is detected from the first chunk** (the histogram is accumulated across
the rest and a disagreement raises rather than mixing cadences under one label), and a
duplicate `(Segment ID, Date Time)` **spanning two chunks or parts** is de-duplicated
here where the pandas path inserts both copies (its anti-join sees the table, not the
frame it is about to insert). Segment **metadata** comes from `source` alone in both
paths — `io.load_metadata` reads a single source and does not walk the parts, so a
part-split export stores part 1's metadata while ingesting every part's observations.

**Versioning / migration.** Every area row stamps `schema_version`
(`store.SCHEMA_VERSION`, now **2** — the area/merge model; **1** was the Item-21
one-dataset-per-export layout). There is no in-place migrator — the store is a
*cache*, so the migration policy is **re-ingest**: bump the version, delete the
`.duckdb` file, and re-run ingest (a v1 store's old `obs_<key>`/`_datasets` tables are
simply ignored by v2 code, so an existing file keeps working but its old data won't
appear as an area until re-ingested). The Item 27 `_names` table is **additive** —
it's created on `connect` if absent, so an existing v2 store gains it with no re-ingest
and no version bump (the observation/area layout is unchanged).

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
