# Roadmap — inrix_tools

Work is broken into **named, numbered items**, each sized for one focused
working session (plan + implement + tests + doc pass), following the convention
of the sibling `iprj_designer` project.

- The number is a **stable ID**, assigned once and never reused or renumbered —
  not an execution order.
- **File order is priority order**, read top to bottom; dependencies are noted
  inline.
- Each item carries a **Target** model and a **Suggested prompt**. Default is
  **Opus, end-to-end in one session** (plan + implement + tests + docs, no
  cross-model hand-off); an item marked *Sonnet-eligible* is small and
  mechanical enough to hand to Sonnet whole if you'd rather not spend an Opus
  session on it.
- Tell the agent "do Item N of ROADMAP.md" to run a scope. Check off boxes and
  add a session entry to [DESIGN_HISTORY.md](DESIGN_HISTORY.md) as items land.

The build order below reflects dependencies: I/O and time binning underpin
everything; the analysis items (4, 5) and the geometry layer (8) depend on I/O;
KML (6) depends on geometry; the GUI (7) depends on the analysis and geometry.
Item 8 is placed by priority just before the mapping items it feeds, even though
its stable ID is higher.

---

## 1 — Data I/O core (`io.py`) (Target: Opus)

The foundation. Load an INRIX export into a clean, typed, tz-aware DataFrame and
join segment metadata. Port from `_Plot Speed.ipynb`'s load cells but fix the
fragility (hardcoded paths, naive tz, `.apply` binning).

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 1):
- [x] `load_data(source)` — read `data.csv` **directly from the `.zip`** (stream
      it out; don't require unpacking) or from a plain csv/dir; concatenate
      `..._part_N.zip` splits. Parse `Date Time` tz-aware, keep units from the
      column headers (don't assume mph). Return a typed DataFrame.
- [x] `load_metadata(source)` — read `metadata.csv`; typed columns; `Segment ID`
      index; a `Combined` label (Road+Direction+Intersection).
- [x] `to_local(df, tz)` — convert to an explicit IANA zone (default from a
      param, e.g. `America/Denver`); add `day_of_week` / `time_of_day` in local
      time. **DST-correct** (fixes the notebook's fixed-offset hack).
- [x] `filter_cvalue(df, threshold=80)` — CValue filter as a tunable, threshold
      recorded on the result (attrs) for reproducibility.
- [x] Complete-set corridor helper `mark_complete_timestamps` (the "all
      segments reported" rule from DATA_FORMAT.md) — flags partial timestamps.
- [x] pytest against the Myrtle zip fixture (small slice) + a synthetic tiny
      fixture: tz/DST correctness, unit parsing, split discovery, CValue filter,
      complete-set flagging. 17 pass.
- [x] Update DATA_FORMAT.md with anything learned; DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 1 of ROADMAP.md: build `src/inrix_tools/io.py` —
> load an INRIX export (streamed from the zip, split-part aware) + metadata into
> typed, tz-aware DataFrames per DATA_FORMAT.md, with CValue filtering and the
> complete-set corridor rule. pytest against the Myrtle fixture; land the docs.

---

## 2 — Time binning (`timebins.py`) (Target: Opus, Sonnet-eligible) — needs Item 1

Port and harden the day-group / time-of-day binning from `_Plot Speed.ipynb`
(`map_day_group`, `assign_time_chunks`), which is the reusable heart of the
seed. Pure functions over a DataFrame.

Scope:
- [ ] `assign_day_group(df, scheme)` — configurable day grouping (default
      Mon–Thu / Fri / Sat / Sun); no hardcoded scheme.
- [ ] `assign_time_bins(df, bins)` — arbitrary bin edges like
      `"6:30AM-9:00AM"`, including **overnight** bins (e.g. `9:00PM-6:00AM`);
      vectorized, not per-row `.apply`. Robust `%I:%M%p` parsing.
- [ ] `group_label` composition (`"Mon–Thu, 2:00PM-7:00PM"`).
- [ ] pytest: bin-edge inclusivity, overnight wrap, unassigned handling, DST day.
- [ ] DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 2 of ROADMAP.md: build `src/inrix_tools/timebins.py`
> — configurable day-group + time-of-day binning (overnight-safe, vectorized)
> ported from `_Plot Speed.ipynb`. pytest the edge cases.

---

## 3 — Speed / travel-time aggregation (`speed.py`) (Target: Opus) — needs Items 1, 2

Split the seed's `process_and_plot_*` functions into **compute only** — the
plotting moves out (to the GUI / notebooks). This is the core "undo the
compute+plot fusion" item.

Scope:
- [ ] `segment_summary(df, ...)` — per (segment, day-group, time-bin) stats
      (count, mean, std, median of speed and travel time), tz-aware.
- [ ] `daily_timebin_summary(df, ...)` — the daily mean ± SD per time-bin series
      that `process_and_plot_timebin_daily_summary` produced, as a DataFrame.
- [ ] `corridor_travel_time(df, metadata, ...)` — segment→corridor summation
      with the complete-set rule (Item 1 helper).
- [ ] Optional rolling averages as an explicit, testable transform (not baked
      into the summary).
- [ ] Return typed DataFrames; **no plotting imports in this module**.
- [ ] pytest on a synthetic fixture with known aggregates; DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 3 of ROADMAP.md: build `src/inrix_tools/speed.py` —
> the compute layer (segment summary, daily time-bin summary, corridor travel
> time) split cleanly from plotting, ported from the seed notebook's
> process_and_plot_* functions. pytest known aggregates.

---

## 4 — Decomposition + before/after (`decompose.py`, `beforeafter.py`) (Target: Opus) — needs Items 1, 2

The robust upgrade to the seed's t-test. A thin adapter over
`traffic_anomaly.decompose` plus a decomposition-based before/after comparison.

Scope:
- [ ] `decompose.py`: thin adapter mapping INRIX columns onto
      `traffic_anomaly.decompose` (defaults `freq_minutes=5`,
      `entity_grouping_columns=['Segment ID']`, `value_column` selectable =
      travel time or speed). Returns the decomposed frame (trend/season/resid).
      Attribution note in the module docstring; **no vendoring**.
- [ ] `beforeafter.py`:
  - [ ] `compare_periods(...)` — the **primary** method: compare
        seasonally-adjusted values / residuals between a before and after date
        range, per segment (and optionally per day-group×time-bin). Report
        **effect size + confidence interval** and n, not just a p-value.
  - [ ] `ttest_baseline(...)` — the seed's one-sample/paired t-test kept as a
        labeled, interpretable **baseline**, with an explicit note in the
        docstring about autocorrelation + multiple-comparisons caveats.
- [ ] pytest: synthetic series with a known injected before/after shift — the
      decomposition method recovers it; the two methods agree in the easy case
      and the decomposition method is more robust under seasonality.
- [ ] DESIGN_HISTORY entry; note the analysis rationale.

Suggested prompt:
> [Opus] In Inrix/, do Item 4 of ROADMAP.md: `decompose.py` (thin
> traffic_anomaly.decompose adapter for INRIX) + `beforeafter.py`
> (decomposition-based `compare_periods` with effect size + CI as the primary,
> plus the seed t-test kept as a labeled baseline). pytest a known injected
> shift.

---

## 5 — Changepoint detection (`changepoint.py`) (Target: Opus) — needs Item 1

Locate *when* a persistent shift occurred, without hand-specifying the
before/after boundary — complements Item 4 for finding intervention dates.

Scope:
- [ ] Thin adapter over `traffic_anomaly.changepoint` for INRIX segments
      (`entity_grouping_column='Segment ID'`, value selectable); surface
      `score / avg_before / avg_after / avg_diff`.
- [ ] `changepoints_near(dates, ...)` helper — relate detected changepoints to
      known intervention dates (nearest changepoint within a window).
- [ ] pytest: a synthetic series with a step change is detected at the right
      time; a stationary series yields none.
- [ ] DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 5 of ROADMAP.md: `changepoint.py` — thin
> traffic_anomaly.changepoint adapter for INRIX segments + a
> `changepoints_near(known_dates)` helper. pytest a synthetic step change.

---

## 8 — Segment geometry layer (`geometry.py`) (Target: Opus) — needs Item 1

The mapping foundation. The **official INRIX XD network shapefile**
(`USA_Idaho_shapefile.zip`, statewide, keyed by `XDSegID` = our `Segment ID`)
gives real road-following polylines by lookup — no OSM matching needed (see
DATA_FORMAT.md and the Session 0.1 note in DESIGN_HISTORY.md). This item builds
the `segment_id → LINESTRING` layer that both KML export (Item 6) and the app
map (Item 7) consume from one source of truth.

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 2):
- [x] `load_xd_network(source, segment_ids=None, bbox=None, cache_path=None)` —
      read the XD shapefile (from the zip via GDAL `/vsizip/`), **subset to the
      segments in a given export** via a pushed-down `XDSegID IN (...)` WHERE (or
      a bbox), cast the all-`C(255)` fields to real types (nullable ints for
      ids, floats for `Miles`/lat-long), optional GeoParquet cache. Returns a
      GeoDataFrame (EPSG:4326).
- [x] `segment_geometry(...)` — `Segment ID → shapely LINESTRING` lookup;
      **straight-line-from-endpoints fallback** flagged via a `source` column
      (`xd` / `fallback` / `missing`).
- [x] `connectivity_table(..., direction='next'|'prev')` — downstream/upstream
      table from `NextXDSegI`/`PreviousXD` (`Segment ID`, `next_id`), terminals
      dropped — reused for corridor assembly and originated-anomaly detection.
- [x] `to_geojson(...)` helper (promotes the index to a feature property).
- [x] pytest: all 46 Myrtle segments resolve to real polylines (>2 vertices,
      median 7); synthetic missing-segment falls back + is flagged; the S 9th St
      connectivity chain links; cache round-trip. 23 pass total.
- [x] Update DATA_FORMAT.md's shapefile section; DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 8 of ROADMAP.md: build `src/inrix_tools/geometry.py`
> — load/subset/cache the INRIX XD network shapefile into a
> `Segment ID → LINESTRING` layer (typed, EPSG:4326) with a straight-line
> endpoint fallback, plus a `connectivity_table` from NextXDSegI/PreviousXD.
> pytest against the Myrtle export; land the docs.

---

## 6 — KML export (`kml.py`) (Target: Sonnet-eligible) — needs Item 8

Port `_metadata KML.ipynb`, but draw the **real segment geometry** from the
geometry layer (Item 8) instead of straight endpoint-to-endpoint lines. KML
stays as an *export*; the embedded app map (Item 7) is the primary view.

Scope:
- [ ] `geometry_to_kml(geo, out_path, label_segments=False, color_by=None, ...)`
      — draw each segment as its road-following polyline (from Item 8), optional
      always-visible label, hidden pin icons, optional color-by-metric. One
      clean function consolidated from the two near-duplicate `csv_to_kml`
      versions in the seed notebook.
- [ ] Consume the geometry layer / typed metadata, not a re-read CSV; fall back
      to the straight-line geometry the layer already provides.
- [ ] pytest: valid KML XML out for a 2-segment fixture (parse it back), with a
      multi-vertex polyline present.
- [ ] DESIGN_HISTORY entry.

Suggested prompt:
> [Sonnet] In Inrix/, do Item 6 of ROADMAP.md: `kml.py` — one clean
> `geometry_to_kml` (consolidated from the two `csv_to_kml` versions in
> `_metadata KML.ipynb`) that draws real segment polylines from the Item 8
> geometry layer, with an optional color-by-metric. pytest the KML output.

---

## 7 — Dash data explorer + embedded map (`gui/app.py`) (Target: Opus) — needs Items 1–5, 8

The interactive explorer, a **thin shell** over the compute core. No compute
logic in the callbacks beyond wiring. The **embedded interactive map is the
primary navigation surface** — real segment polylines (Item 8), click a segment
to drive the analysis panels. Map framework (dash-leaflet vs Plotly native
maps) is **decided at build time** — pick per the interaction needs then; both
consume the same GeoJSON from the geometry layer.

Scope:
- [ ] **Embedded map**: render the geometry layer (Item 8) as clickable segment
      polylines over an OSM basemap; click/select a segment (or a drawn
      corridor) to drive every other panel; color segments by a chosen metric
      (mean speed, before/after delta, anomaly count).
- [ ] Load an export (path/upload) → map + segment picker stay in sync.
- [ ] Views: raw speed/travel-time time series; day-group×time-bin summary
      (Item 3); before/after comparison (Item 4); decomposition components and
      changepoints (Items 4/5) overlaid on the series.
- [ ] Date-range pickers for the before/after periods; CValue threshold control.
- [ ] KML export button (Item 6) from the current selection.
- [ ] Every figure built from a compute-core DataFrame — callbacks call
      `inrix_tools.*` and render; they don't compute inline.
- [ ] Run instructions in README; a smoke test that the app builds its layout
      headless. Record the map-framework choice in DESIGN_HISTORY.

Suggested prompt:
> [Opus] In Inrix/, do Item 7 of ROADMAP.md: build the Dash explorer in
> `gui/app.py` as a thin shell over the compute core — an embedded interactive
> map of real segment polylines (Item 8) as the primary selector driving
> time-series / summary / before-after / decomposition+changepoint panels, plus
> a KML-export button. Decide the map framework (dash-leaflet vs Plotly maps)
> at the top of the session and record it. README run steps + a headless layout
> smoke test.

---

## Future (not yet scoped — need a planning pass before they're actionable)

- **Anomaly / incident flagging** — wrap `traffic_anomaly.anomaly` (z-score /
  GEH on residuals, entity- and group-level) to flag bad sensor data, incidents,
  and unusual days. Deferred from the initial analysis scope.
- **Difference-in-differences before/after** — when an unaffected control
  corridor exists, the gold-standard intervention estimate; builds on Item 4.
- **Packaging / deployment** — entry-point console script; hosting the Dash app
  (multi-user state, project/file management) if it moves off localhost.
- **Origin/connectivity-aware anomalies** — use `anomaly()`'s
  `connectivity_table` to separate locally-originated anomalies from
  downstream-propagated ones. The connectivity table now comes **free** from
  Item 8's `NextXDSegI`/`PreviousXD`, so this is mostly wiring once anomaly
  flagging lands.
- **Automatic corridor assembly** — chain segments via the Item 8 connectivity
  table (walk `next_id`) to build corridors from a seed segment instead of
  hand-listing members; feeds `speed.corridor_travel_time`.
- **OSM geometry fallback** — only needed for segments *not* in the XD shapefile
  (out-of-state, or a future provider change): per-segment map-matching
  (osmnx/OSRM/Valhalla + a Shapely endpoint cut, QA'd against `Miles`). Not
  required while the INRIX XD shapefile covers the study area — kept here as the
  documented escape hatch.
