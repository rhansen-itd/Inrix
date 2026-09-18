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

**Consequence for corridor definitions:** a corridor is stated as an endpoint pair
and resolved, and an extent that will not walk is recorded with its `stop_reason` —
never completed by sorting the segments in a bounding box by latitude or longitude.
`scripts/d3_corridors.json` is the District 3 catalogue in that form and
`corridors.load_catalogue` / `resolve_catalogue` are the code contract.

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
| `Extra TT (Min)`    | the provider's own delay-over-typical figure         |

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

**Agreement statistics** (`agreement.compare`, one row per route): bias with a
**day-blocked** 95% CI (15-minute bins are autocorrelated; the per-bin interval is
reported beside it as `*_naive`, and `ci_width_ratio` shows the inflation — up to
2.4× on these corridors), MAE / RMSE / MAPE, **SD ratio**, **delay ratio** (mean
delay above *each source's own* free-flow percentile), Bland-Altman limits of
agreement, and correlation reported last on purpose. `independent_totals` reports
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
   size in minutes** that this reference would credit, on signalised arterials.
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
instead of being restated as "veh-hrs/day". Three definitions, all recorded on `attrs`:

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
view), `Descriptio`, `Descript_1`. Route-measure identity
(`RouteID`/mileposts) is the layer's own linear reference; extras (`DHV`,
`MADT1..12`) are dropped.

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
