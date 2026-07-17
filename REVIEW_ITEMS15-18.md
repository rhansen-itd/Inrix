# Review of Items 15, 16, 10, 11, 12, 13, 17, 18 (Fable, 2026-07-17)

A targeted **correctness** pass over everything built since the Item 14 review
(DESIGN_HISTORY Sessions 12–19): `beforeafter.py`, `speed.py` (delay +
corridor/network `value=`), `aadt.py`, `timebins.py` (date/DOW filters),
`names.py`, and the compute-bearing seams of `gui/app.py` (`_analysis_frame`,
the `_adjusted_cache` / `_compare_cache` keys, `default_periods`,
`_agg_metric_key` / `_resolve_col`). Not a style review; Dash layout, callback
wiring, `figures.py`, and the already-tested `io.py` / `geometry.py` / `kml.py`
were skipped as scoped.

Every finding marked **verified** was reproduced by running the failing case
(scripts in the session scratchpad; the load-bearing ones are reproduced inline).
Baseline: the full pytest suite passes before this review (exit 0; the five
core-module test files alone: 112 pass). **Nothing here changes code** — this
report is the deliverable.

**Status (2026-07-17, same day — see DESIGN_HISTORY Sessions 21–22):**
**All findings are now fixed.** F1–F5 and F8–F11 were resolved in Session 21
(Fable, with regression tests; F2's fix also upgraded the bearing gate to local
tangents so curved same-road features now *match* instead of being misflagged).
**F6 and F7 are resolved in Session 22** (see the resolution notes in §3). The
findings below are left as originally reported.

**One-paragraph summary.** The statistics core (Item 15) held up under attack:
the BH step-up is correct (ties, NaN, monotonicity hand-verified), the period
validation and default windows are genuinely disjoint at every span, and the
Item 16 `adjust_for_periods`/`compare_adjusted` split is behaviour-preserving by
construction. The bugs that matter are at the **newer data seams**: (1) the
complete-set rule counts *rows*, not non-NaN values, so a corridor/network
**delay** sum silently undercounts — and fabricates `0.0` — whenever member rows
carry NaN delay, exactly the failure the rule exists to prevent; (2)
`join_aadt`'s bearing gate changes only the *flag*, never the *value* — a
gate-rejected cross-street's AADT is still attached via the `nearest` fallback
and flows into every weighted number; and (3) the AADT-weighted speed series
shifts level when the reporting set changes, so a coverage change can read as a
speed effect in the before/after. One GUI cache finding (over-specified keys →
duplicate 8-second decompositions) is a performance regression against the very
optimization Item 16 shipped, not a wrong number.

---

## 1. Findings, ranked

### F1 — Corridor/network **delay** sum: NaN member rows silently undercount, all-NaN sums to 0.0  ⚠ verified — HIGH
[speed.py:333-347](src/inrix_tools/speed.py:333) + [io.py:201](src/inrix_tools/io.py:201) (Items 17/12)

`corridor_travel_time(value="Delay(Minutes)")` applies the complete-set rule via
`mark_complete_timestamps`, which counts **rows present** (`nunique(Segment ID)`)
— it never looks at the column being summed. `segment_delay` legitimately
produces NaN delay on rows where the free-flow speed is missing/non-positive
(speed.py:175: `delay = (...).where(valid)`). Those rows count toward
`complete=True` while `groupby(...).sum()` skips them — and pandas' default
`min_count=0` turns an **all-NaN timestamp into `0.0`**. Reproduced:

```
segments {1,2}, both report at every timestamp; segment 2's delay is NaN at t2,
both NaN at t3:
  t1  Delay=5.0  complete=True   (true 5.0 — ok)
  t2  Delay=2.0  complete=True   (true is unknowable; 3.0-delay member dropped silently)
  t3  Delay=0.0  complete=True   (fabricated zero delay from zero data)
```

Failure scenario: an export with missing/zero `Ref Speed` rows (the exact case
the `('pXX', q)` fallback exists for) → GUI Corridor/Network scope, Delay metric
→ the summed series is biased low precisely at the timestamps with unresolvable
free-flow, `complete=True` throughout, and the decomposition / before-after /
day×time summary all run on the contaminated series. If the bad-Ref rows are
concentrated in one period (e.g. a reference re-baseline mid-export), the
before/after Δ-delay reports a confident spurious effect. Travel time is safe
(observed TT is never NaN on a reported row); this is specific to `value=` sums
of a NaN-able column. The Myrtle fixture has **0** such rows (checked:
2,185,368 rows, no NaN/≤0 Ref Speed), which is why the tests never see it.

Fix shape: completeness for a `value=` sum should count **non-NaN values of the
summed column** (or at minimum sum with `min_count=1` so all-NaN yields NaN, and
flag value-NaN rows as incomplete).

### F2 — `join_aadt`: the distance/bearing gate affects only the flag — the rejected line's AADT is attached anyway and consumed downstream  ⚠ verified — HIGH
[aadt.py:206-226](src/inrix_tools/aadt.py:206), consumed at [app.py:263-264](gui/app.py:263) (Item 18)

When no candidate passes the distance+bearing gate, the fallback takes
`tree.nearest(...)` — **any bearing, any distance** — and stores its AADT with
`aadt_source="nearest"`. But nothing downstream filters on `aadt_source`: the
GUI stores `geo["AADT"]` wholesale as `ds.aadt`, and `vehicle_hours_of_delay` /
`aadt_weighted_mean_speed` / `weighted_speed_by_time` use the value regardless.
Reproduced both directions of the failure:

- A perpendicular **cross street that crosses the segment** (distance 0 m) is
  correctly rejected by the bearing check — then re-selected by the fallback:
  the segment gets the cross-street's `AADT=99999`, and
  `vehicle_hours_of_delay` happily computes 9,999.9 vehicle-hours from it.
- The converse: an L-shaped **same-road** AADT feature lying *on* the segment is
  flagged `nearest` (its endpoint-to-endpoint bearing is >45° off), so the flag
  can't be trusted in either direction — `nearest` covers both "right road,
  curved feature" and "wrong road entirely", indistinguishably.

Failure scenario: any segment near a high-volume cross street whose own AADT
line is missing/curved gets the cross street's volume; the vehicle-hours map
colouring and the corridor weighted-mean speed silently include it. The ROADMAP
notes the real Myrtle join has one `nearest` segment already — its value is in
`ds.aadt` today.

Fix shape: don't attach a value for `nearest` (keep NaN + flag, letting the
weighting helpers' missing-AADT paths handle it), or gate the fallback by
distance only; and compute bearing from the **nearest-point local tangent**
(or the overlapping portion) instead of endpoints, so long/curved route-measure
features classify correctly.

### F3 — `weighted_speed_by_time`: weight re-normalization turns coverage changes into speed changes  ⚠ verified — MEDIUM
[aadt.py:356-370](src/inrix_tools/aadt.py:356), fed to before/after via [app.py:414-422](gui/app.py:414) (Item 18)

Per-timestamp weights re-normalize over whichever segments reported. Reproduced:
two segments, speeds constant (60 mph @ AADT 50,000; 20 mph @ AADT 1,000) — the
series reads 59.2 when both report and **20.0** when the mainline misses one
timestamp. No speed changed anywhere; the level shift is pure composition.

Failure scenario: the GUI's weighted-speed toggle runs decomposition /
`compare_adjusted` on this series. A reporting outage on a high-AADT segment
concentrated in one period (probe coverage changes, a construction-period feed
gap…) produces a large, tight-CI "speed effect" that is actually a coverage
artifact. The docstring says the mean "tolerates a missing segment", but nothing
quantifies or surfaces how much weight was present per timestamp — unlike the
travel-time sum, which drops incomplete timestamps loudly.

Fix shape: emit a weight-coverage column (Σw present / Σw of all members) and
either drop timestamps below a threshold or surface coverage in the GUI; at
minimum record the caveat on `attrs` like the AADT-daily-total one.

### F4 — GUI cache keys over-specified: duplicate 8-second decompositions in Segment scope, cache thrash  — code-verified — MEDIUM (performance, not wrong numbers)
[app.py:443](gui/app.py:443), [app.py:839](gui/app.py:839) vs [app.py:1081](gui/app.py:1081)/[app.py:1110](gui/app.py:1110) (Items 12/16)

The `_adjusted_cache` key is `(col, window, scope, corridor, days)`, and
`corridor` is **not normalized when it is irrelevant**:

- The map's delta path calls `_compare_all(ds, col, …)` with the defaults
  `scope='segment', corridor=None`.
- The Before/after and Decomposition panels pass `scope=scope,
  corridor=corridor` even in **Segment** scope — and `_load` auto-picks
  `corridors[0]`, so `corridor` is non-None right after load.

Result: the identical full-export segment-scope decomposition is computed and
cached **twice** (keys `(…,'segment',None,…)` and `(…,'segment','Myrtle',…)`),
filling the entire `_ADJ_CACHE_CAP=2` with one payload; any metric or window
switch then evicts and re-decomposes (~8 s each, per the Item 14 measurement).
Changing the corridor dropdown while in Segment scope also re-decomposes for
nothing. Same over-keying applies in Network scope and to `_compare_cache`.
No key ever *collides* (I checked the converse — distinct filters always get
distinct keys, including the weighted-speed column and the no-op ToD/DOW
spellings), so numbers are never stale or cross-contaminated; the cost is
re-running the exact work Item 16 set out to cache.

Fix shape: canonicalise the key — `corridor=None` unless `scope == 'corridor'`
(and pass scope/corridor from the map path explicitly so both call sites agree).

### F5 — `filter_date_range`: tz-aware bounds normalized before conversion — boundary lands up to a day off  ⚠ verified — MEDIUM-LOW
[timebins.py:363-367](src/inrix_tools/timebins.py:363) (Item 11)

`_bound` does `pd.Timestamp(x).normalize()` **then** `tz_convert(tz)`. For a
tz-aware input in another zone the cut lands mid-day local and the recorded
attrs are wrong. Reproduced with Denver data and
`start=Timestamp("2026-03-01", tz="UTC")`: earliest kept row is
**2026-02-28 18:00 local**, and `attrs['date_range']` records `('2026-02-28',
None)` — violating the documented "any time-of-day component is ignored — the
whole day counts". The GUI passes date strings so the app is unaffected; any
API/notebook caller passing tz-aware timestamps (natural, since the data is
tz-aware) gets a silently shifted window. Fix: convert to the frame's tz first,
then normalize.

### F6 — Complete-set membership inconsistency at network scale: "complete" timestamps can sum different segment subsets — MEDIUM-LOW (inherited, amplified)
[io.py:201-206](src/inrix_tools/io.py:201) via [speed.py:384-434](src/inrix_tools/speed.py:384) (Item 12)

`expected_segments` is the **max simultaneous** distinct-segment count, not the
export's total distinct segments. If no timestamp ever has all N segments
(sparse exports — the exact case the network docstring worries about), then
`expected = max observed < N`, and two timestamps summing *different*
(N−1)-subsets both count `complete=True`. Their totals differ by whichever
segment is missing (a long segment vs a short one), so the "complete" network
series is not level-comparable across time — spurious steps that decomposition
/ changepoint will faithfully detect. Not new to this batch (Item 1 helper),
but Item 12's network scope is where it becomes reachable. On Myrtle the full
46-segment set is regularly achieved, so `expected=46` and the issue is moot
there. Worth a DATA_FORMAT note + a stricter option (`expected` = total
distinct segments) for network scope.

### F7 — Warm-up truncation flag uses the global series start, not per-segment starts — LOW
[beforeafter.py:336](src/inrix_tools/beforeafter.py:336), [beforeafter.py:428-443](src/inrix_tools/beforeafter.py:428) (Item 15)

`series_start = df[datetime_col].min()` is global; the decomposition's
`drop_days` warm-up applies **per entity**. A segment whose data begins later
than the export start (added sensor, staged export) has its own warm-up bite
into the before period with no warning, and
`before_days_effective` overstates its evidence. Advisory-only impact (the
counts/warnings are informational; the Welch stats use the actual data), but
the attrs can claim more days than a late-starting segment contributed.

### F8 — `_write_kml` ignores the vehicle-hours map mode — LOW
[app.py:1121-1134](gui/app.py:1121) (Items 13/18)

The KML button is documented as "the current map colouring", but `_write_kml`
only handles `delta`-vs-means: with the map on **Vehicle-hours of delay**, the
exported KML silently colours by plain metric means. Mislabeled output, not a
wrong in-app number.

### F9 — Zero-variance day-means produce a width-0 CI and p=0 — LOW  ⚠ verified
[beforeafter.py:158-197](src/inrix_tools/beforeafter.py:158) (Item 15)

`_compare_stats(Series([5,5]), Series([7,7]))` → `effect=2, CI=[2,2], p=0.0`
(scipy also emits a catastrophic-cancellation RuntimeWarning). With `unit='day'`
and `min_samples=2`, two quantized days per period suffice — INRIX travel times
are heavily rounded on short segments, so short periods can genuinely be
constant. The forest then shows an authoritative zero-width interval. A
degenerate-variance guard (report NaN CI or flag the row) would be honest.

### F10 — `parse_day_of_week` truncates floats: `6.9` → Saturday — LOW  ⚠ verified
[timebins.py:277](src/inrix_tools/timebins.py:277) (Item 13). `int(day)`
truncates instead of rejecting non-integral values. One-line fix
(`d != day → raise`). No GUI path hits it (checklist sends ints).

### F11 — Names CSV: a hand-edited name pandas parses as NA is silently dropped — LOW  ⚠ verified
[names.py:176-183](src/inrix_tools/names.py:176) (Item 10). `read_csv` default
NA parsing turns a user's literal `NA` / `None` / `null` / `nan` name into NaN →
`load_names` blanks it → `apply_names` silently falls back to the seed. Read
with `keep_default_na=False` (the `Segment ID`/`name` columns need no NA
sentinels). Round-trip is otherwise clean, including commas/quoting and the
seed→template→load→apply precedence chain.

---

## 2. Attacked and found sound

Each of these was specifically attacked (by construction of adversarial inputs,
hand-computation, or running the case) and held:

- **BH `q_value`** (`_bh_qvalues`): correct step-up — reverse-cummin implements
  `q_i = min_{j≥i} p_(j)·m/j`; hand case with ties (`0.04, 0.04`) and NaN
  reproduces the textbook values `[.025, .04, .04, .025, NaN, .04]`; NaN
  excluded from the family size; monotone in p; clipped to [0,1]. Family = the
  rows of one `compare_adjusted` call, as documented.
- **Day-mean aggregation** (`_compare_core`): local-calendar-day grouping off
  the tz-aware column, index-aligned masks, `n_before/n_after` in days with the
  5-min counts reported separately as `n_samples_*`; `min_samples` applied in
  the chosen unit; NaN-only days excluded. The `unit='sample'` escape hatch
  reproduces the old behaviour and is labeled non-robust. (One residual stats
  note, not a defect: day means still carry day-scale autocorrelation from
  multi-day disturbances — the AR(1)-at-5-min model behind the "~96% coverage"
  claim makes day means nearly independent by construction. Acceptable per the
  Item 14 analysis; a block bootstrap remains the upgrade path.)
- **Item 16 split is behaviour-preserving by construction**:
  `compare_periods(use_decomposition=True)` *is* `adjust_for_periods` +
  `compare_adjusted` — one code path, no drifted duplicate. `series_start` /
  `drop_days` round-trip through attrs (as strings, for the ibis constraint)
  and `compare_adjusted` re-hydrates them correctly.
- **Period validation**: `check_periods` half-open overlap test
  (`b0 < a1 and a0 < b1`) is exact; touching periods pass; end-before-start
  raises in `parse_period`; the GUI fast-fails before decomposing.
- **`default_periods`**: disjoint at every span ≥ 2 days given `parse_period`'s
  whole-day-inclusive ends (`a_start = b_end + 1 day` → exactly touching
  half-open bounds); warm-up clamped so it never eats the span; degenerate
  spans return all-None. Edge spans 2 and 3 checked by hand.
- **`compare_periods(by=['Day Group'])`** still works after the split — the
  binning columns pass through `traffic_anomaly.decompose` unchanged (run).
- **`segment_delay`**: the length-based and speed-based forms agree wherever
  `TT = Miles/Speed × 60` holds (the speed form is derived by substituting
  exactly that identity — on real data they differ only by INRIX rounding, and
  the per-row `fillna` mixes the two estimates only for length-less segments);
  NaN/non-positive free-flow → NaN delay (preserved through `clip`);
  `floor=True` clamps negatives only; missing Ref Speed with `free_flow='ref'`
  raises; the `('pXX', q)` percentile path is per-segment and NaN-tolerant;
  `attrs['delay']` records source/floor/length-source. GUI `_parse_freeflow`
  maps `'p95'` → `('pXX', 95)` correctly.
- **`value=` threading** (Item 17→12): `corridor_travel_time` /
  `network_travel_time` keep the summed column's own name, and length /
  space-mean speed attach **only** when the summed column is the travel-time
  column (the `tt == metric_columns(df)["travel_time"]` gate) — delay sums
  never get a bogus "corridor speed". (F1 above is the one hole.)
- **AADT positional alignment**: `to_crs` preserves row order;
  `aadt_geoms` / `aadt_bearings` / `aadt_vals` / route / commercial arrays are
  all positional off the same frame, and STRtree query/nearest indices index
  that same list — no misalignment path found (including after `load_aadt`'s
  `reset_index(drop=True)`). Bbox reprojection uses `always_xy` both ways and
  all four corners; year pushdown is a plain attribute `where`.
- **Weighting math edge cases**: `vehicle_hours_of_delay` zeroes missing/≤0
  AADT (documented, row kept), keeps NaN delay as NaN; `aadt_weighted_mean_speed`
  drops NaN values and non-positive weights from both sums and returns NaN on
  zero total weight; `weighted_speed_by_time` never divides by zero (rows are
  pre-filtered to `w > 0`).
- **`filter_date_range`** (naive/date/string bounds): inclusive both ends,
  exclusive at the next local midnight via DST-safe `DateOffset`; open bounds;
  `start > end` → empty; attrs recorded. (F5 is the tz-aware-input exception.)
- **`filter_day_of_week`**: name/abbrev/int/numeric-string forms all parse;
  a bare string like `"Monday"` (iterated as characters) raises rather than
  silently filtering; empty/None/all-seven are no-ops with `attrs=None`;
  composes with `filter_time_window` as independent masks.
- **GUI key/no-op consistency**: `_window_key` / `_days_key` collapse exactly
  the spellings `_apply_tod` treats as no-ops (including equal slider handles =
  whole day), so a cached decomposition is shared across equivalent filters and
  never across distinct ones; `_agg_metric_key` / `_resolve_col` /
  `_scope_metric` agree on which metrics are valid per scope (speed never
  aggregates; the weighted-speed column only substitutes in aggregate scopes
  with the toggle on and data behind it).
- **`_analysis_frame` collapse**: the synthetic `_AGG_SEGMENT_ID` frame carries
  only `[Date Time, value]` + the id, attrs preserved; slicing the cached
  full-export decomposition per entity is equivalent to decomposing the entity
  alone (per-entity grouping), so the decomposition tab's reuse is sound.
- **`simplify_label` / seed fallbacks**: route-prefix stripping requires a
  following street name (`I-184` alone survives); direction tokens stripped
  only at token boundaries; cross-street dedup via substring catches
  `US-20 Myrtle St` vs `Myrtle St`; empty/`nan` roads and intersections fall
  through name → Combined → Segment ID without crashing; `apply_names` ignores
  unknown segment rows and blank overrides.

## 3. Suggested fix order — resolution

*(Original guidance: F1/F2 first — the two that can put a wrong number on
screen; F3 needs a coverage-threshold decision; F4 is a key canonicalisation;
F5–F11 one-liners.)*

**Resolved same-day (Session 21):**

- **F1** — `corridor_travel_time` completeness is now value-aware: `n_segments`
  counts segments with a non-NaN value, an all-NaN timestamp sums to NaN (never
  0.0). Travel-time sums are unchanged (regression-tested).
- **F2** — `join_aadt` attaches a volume **only** for `matched`; a `nearest`
  line is identified (Route + distance) with NaN AADT. The gate now compares
  **local tangents at the closest approach** (`_local_bearing`), so a curved
  same-road feature matches and a crossing street can never leak its volume.
- **F3** — `weighted_speed_by_time` emits a per-timestamp `coverage` column
  (reporting AADT ÷ member AADT) and drops timestamps below `min_coverage`
  (default 0.5; 0 restores keep-everything), recorded on
  `attrs['weighted_speed']`.
- **F4** — `_adjusted_frame` / `_compare_all` canonicalise `corridor=None`
  outside Corridor scope; the map and panel paths now share cache entries
  (counter-tested).
- **F5** — `filter_date_range` converts a tz-aware bound to the frame's zone
  *before* normalizing (its **local** calendar day counts).
- **F8** — `_write_kml` gained the vehicle-hours branch, mirroring the map.
- **F9** — `_compare_stats` reports NaN CI/p on degenerate (zero) variance
  instead of a width-0 interval with p=0.
- **F10** — `parse_day_of_week` rejects non-integral numbers.
- **F11** — `load_names` reads with `keep_default_na=False` so a literal
  `"NA"`/`"None"` name survives.

**Resolved in Session 22:**

- **F6** — `mark_complete_timestamps` gained an `expected` policy: `"max"` (the
  original max-simultaneous count, still the `corridor_travel_time` default) vs
  `"total"` (every distinct member segment). `network_travel_time` now defaults
  to `expected="total"`, so on a sparse network only timestamps with the *whole*
  membership count complete — two "complete" timestamps can no longer sum
  different subsets. Threaded through `corridor_travel_time`; DATA_FORMAT.md's
  complete-set section documents the "max" vs "total" choice. On Myrtle the full
  46-segment set is regularly achieved, so the two agree and the default change
  is a no-op there (regression-tested both ways).
- **F7** — `_compare_core` now emits **per-row** `before_days_effective` /
  `after_days_effective`, computed from each segment's own earliest surviving
  timestamp (which already reflects the per-entity decomposition warm-up drop),
  so a late-starting segment reports its shorter span instead of inheriting the
  export-wide figure. The scalar `attrs['*_days_effective']` are kept as the
  export-wide summary; a warning fires when any segment begins after the export
  start.
