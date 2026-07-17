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

**Status (2026-07-16):** the initial build — the full compute core plus the Dash
explorer — is **complete (Items 1–9, all boxes checked; see DESIGN_HISTORY
Sessions 0–9)**. Items 1–9 below are kept as the build record. **Items 10+ are a
new batch of owner-requested refinements** scoped on 2026-07-16 (DESIGN_HISTORY
Session 10); they build on the finished app rather than adding to the core
pipeline. The completed items read top-to-bottom first, then the new batch, then
Future.

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

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 3):
- [x] `assign_day_group(df, scheme)` — configurable day grouping (default
      Mon–Thu / Fri / Sat / Sun); no hardcoded scheme. Scheme is a `{dow: label}`
      dict *or* a list of `"Monday-Thursday"`-style range specs.
- [x] `assign_time_bins(df, bins)` — arbitrary bin edges like
      `"6:30AM-9:00AM"`, including **overnight** bins (e.g. `9:00PM-6:00AM`);
      vectorized, not per-row `.apply`. Robust `%I:%M%p` parsing. Half-open
      `[start, end)` (fixes the seed's `<= <=` double-count at shared edges).
- [x] `assign_group_label` composition (`"Mon–Thu, 2:00PM-7:00PM"`).
- [x] pytest: bin-edge inclusivity, overnight wrap, unassigned handling, DST day
      (spring-forward + non-existent gap hour). 16 pass (39 total).
- [x] DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 2 of ROADMAP.md: build `src/inrix_tools/timebins.py`
> — configurable day-group + time-of-day binning (overnight-safe, vectorized)
> ported from `_Plot Speed.ipynb`. pytest the edge cases.

---

## 3 — Speed / travel-time aggregation (`speed.py`) (Target: Opus) — needs Items 1, 2

Split the seed's `process_and_plot_*` functions into **compute only** — the
plotting moves out (to the GUI / notebooks). This is the core "undo the
compute+plot fusion" item.

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 4):
- [x] `segment_summary(df, ...)` — per (segment, day-group, time-bin) stats
      (count, mean, std, median of speed and travel time), tz-aware. Value cols
      auto-detected by prefix; unassigned bins dropped.
- [x] `daily_timebin_summary(df, ...)` — the daily mean ± SD per time-bin series
      that `process_and_plot_timebin_daily_summary` produced, as a DataFrame
      (Count/Mean/Std/Upper/Lower).
- [x] `corridor_travel_time(df, metadata, ...)` — segment→corridor summation
      with the complete-set rule (Item 1 helper `mark_complete_timestamps`).
      Optional `metadata` adds corridor length + space-mean speed.
- [x] Optional rolling averages as an explicit, testable transform
      (`rolling_average`, trailing/leading/centered) — not baked into the summary.
- [x] Return typed DataFrames; **no plotting imports in this module** (asserted).
- [x] pytest on a synthetic fixture with known aggregates (16 tests, 55 total);
      DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 3 of ROADMAP.md: build `src/inrix_tools/speed.py` —
> the compute layer (segment summary, daily time-bin summary, corridor travel
> time) split cleanly from plotting, ported from the seed notebook's
> process_and_plot_* functions. pytest known aggregates.

---

## 4 — Decomposition + before/after (`decompose.py`, `beforeafter.py`) (Target: Opus) — needs Items 1, 2

The robust upgrade to the seed's t-test. A thin adapter over
`traffic_anomaly.decompose` plus a decomposition-based before/after comparison.

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 5):
- [x] `decompose.py`: thin adapter mapping INRIX columns onto
      `traffic_anomaly.decompose` (defaults `freq_minutes=5`,
      `entity_grouping_columns=['Segment ID']`, `value_column` selectable =
      travel time or speed). Returns the decomposed frame (trend/season/resid).
      Attribution note in the module docstring; **no vendoring**. Adds
      `seasonally_adjust` (= value − season_day − season_week = median + resid,
      retains the level so a step change survives).
- [x] `beforeafter.py`:
  - [x] `compare_periods(...)` — the **primary** method: compare
        seasonally-adjusted values between a before and after date range, per
        segment (and optionally per day-group×time-bin). Reports **effect size
        (mean diff + Cohen's d) + Welch confidence interval** and n, with the
        p-value labeled secondary. `use_decomposition=False` gives the raw
        cross-check.
  - [x] `ttest_baseline(...)` — the seed's paired t-test (across time-of-day
        bins) kept as a labeled, interpretable **baseline**, with the
        autocorrelation + multiple-comparisons caveats in the docstring.
  - [x] `parse_period` — accepts `(start, end)` or the seed's
        `"YYYYMMDD-YYYYMMDD"`; date-only end covers the whole calendar day.
- [x] pytest: synthetic series with a known injected +2 shift — decomposition
      recovers it (CI excludes 0); null case CI straddles 0; the two methods
      agree in the easy case; decomposition is more robust than raw under a
      weekly-seasonality confound. 14 tests (69 total). Verified end-to-end on
      the 46-segment Myrtle export.
- [x] DESIGN_HISTORY entry; analysis rationale recorded.

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

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 6):
- [x] Thin adapter over `traffic_anomaly.changepoint` for INRIX segments
      (`entity_grouping_column='Segment ID'`, value selectable, defaults to the
      detected `Travel Time(...)`); surfaces `score / avg_before / avg_after /
      avg_diff / pct_change`, tz + attrs preserved. Docstring recommends running
      on the seasonally-adjusted series (from `decompose`).
- [x] `changepoints_near(known_dates, window_days=7)` helper — per (segment,
      known date) the nearest detected changepoint with signed `days_off` and a
      `within_window` flag (nearest always reported, non-matches visible).
- [x] pytest: synthetic step change detected at the right date with the right
      sign/magnitude; stationary series yields none; only shifted segments
      appear; `changepoints_near` matches/flags/multi-date/empty cases. 8 tests
      (77 total). Verified on the Myrtle export (corroborates the Item 4
      before/after result on the same segment).
- [x] DESIGN_HISTORY entry.

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

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 7):
- [x] `geometry_to_kml(geo, out_path, label_segments=False, color_by=None, ...)`
      — draw each segment as its road-following polyline (from Item 8), optional
      always-visible label, hidden pin icons, optional color-by-metric. One
      clean function consolidated from the two near-duplicate `csv_to_kml`
      versions in the seed notebook. `color_by` handles both categorical
      (Direction) and numeric-metric (ramp) columns; optional legend overlay.
- [x] Consume the geometry layer / typed metadata, not a re-read CSV; fall back
      to the straight-line geometry the layer already provides (missing geometry
      skipped).
- [x] pytest: valid KML XML out for a 2-segment fixture (parse it back), with a
      multi-vertex polyline present. 13 tests (89 total); real Myrtle roundtrip.
- [x] DESIGN_HISTORY entry.

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

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 8):
- [x] **Embedded map**: renders the geometry layer (Item 8) as clickable segment
      polylines over an OSM basemap; clicking a midpoint marker (the robust click
      target — thin lines are hard to hit) selects a segment and drives every
      panel; segments coloured by a chosen metric (segment mean, or before/after Δ)
      via a Plotly colour bar.
- [x] Load an export (path + CValue + tz) → map + segment picker stay in sync
      (map click → dropdown → panels; the dropdown is the single selection source).
- [x] Views: raw speed/travel-time time series (with before/after windows shaded);
      day-group×time-bin summary (Item 3); before/after effect + CI forest (Item 4,
      decomposition primary); decomposition components + changepoints (Items 4/5).
- [x] Date-range pickers for the before/after periods; CValue threshold control.
- [x] KML export button (Item 6) — writes the current metric colouring to `out/`.
- [x] Every figure built from a compute-core DataFrame — callbacks call
      `inrix_tools.*` and hand the result to `gui/figures.py`; no stats inline.
- [x] Run instructions in README; headless layout + HTTP smoke tests, plus a
      self-skipping real-export end-to-end test. **Map framework: Plotly native
      maps** (`go.Scattermap`, MapLibre, token-free `open-street-map`) — recorded
      in DESIGN_HISTORY. 14 tests (103 total).

Suggested prompt:
> [Opus] In Inrix/, do Item 7 of ROADMAP.md: build the Dash explorer in
> `gui/app.py` as a thin shell over the compute core — an embedded interactive
> map of real segment polylines (Item 8) as the primary selector driving
> time-series / summary / before-after / decomposition+changepoint panels, plus
> a KML-export button. Decide the map framework (dash-leaflet vs Plotly maps)
> at the top of the session and record it. README run steps + a headless layout
> smoke test.

---

## 9 — Time-of-day analysis window (`timebins.filter_time_window` + GUI slider) (Target: Opus) — needs Items 2, 4, 5, 7

Restrict every calculation to a chosen time of day (e.g. the 4–6PM peak) so a
study can be scoped to when an intervention actually bites, without a separate
export. A **pure-core row filter** feeding the existing panels — *not* a new
statistic. The subtlety, confirmed against the `traffic_anomaly` source, is that
its rolling-window sample guard assumes a full day is present, so a narrow window
must relax that guard or the decomposition returns empty (see DESIGN_HISTORY /
DATA_FORMAT).

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 9):
- [x] `timebins.filter_time_window(df, window)` — overnight-safe half-open
      `[start, end)` filter on local wall-clock time; accepts a `"4:00PM-6:00PM"`
      string or an hour-number/`time`/clock-string `(start, end)` pair; full-day
      window is a no-op; records the window on `attrs`.
- [x] `decompose_segments` auto-scales `min_rolling_window_samples` to the data's
      time-of-day coverage (full day → unchanged 480; a 2-hour window → ~40), so
      **filter-first-then-decompose** doesn't come back empty. Value used is
      recorded on `attrs`; existing full-day results are byte-for-byte unchanged.
- [x] GUI `dcc.RangeSlider` (0–24h) pre-filters **every** panel and the map
      colouring (mean + before/after Δ) and KML export, consistently; a
      plain-language status line under the slider. Filter-first-then-decompose is
      the chosen semantics — trend/changepoints describe the selected window.
- [x] pytest coverage: window filtering (half-open/overnight/no-op/attrs),
      auto-scaling (full-day unchanged, narrow-window non-empty), and an
      end-to-end "filter to 4–6PM → decompose → changepoint recovers a PM-only
      step" test. GUI wiring + real-export windowed path. 12 tests (115 total).

Suggested prompt:
> [Opus] In Inrix/, do Item 9 of ROADMAP.md: add a time-of-day analysis window.
> Pure-core `timebins.filter_time_window` (overnight-safe, half-open) + auto-scale
> `decompose_segments`' rolling-window sample guard so a narrow window still
> decomposes; wire a `dcc.RangeSlider` in `gui/app.py` that pre-filters every
> panel/map/KML. Check the `traffic_anomaly` source first so changepoint/decompose
> don't break. Tests + docs.

---

# New batch — refinements on the finished app (Items 10+, scoped 2026-07-16)

These are owner-requested refinements to the working explorer, grouped into
session-sized items (small related requests merged). File order is priority
order; each is independent of the others unless noted. Owner decisions behind the
scoping are recorded in DESIGN_HISTORY Session 10.

**Item 14 (the Fable review) is placed first by owner priority** — its findings
are expected to reshape the scope/order of 10–13 before effort goes into them —
even though its stable ID is higher.

**Post-review (2026-07-16):** Item 14 is done ([REVIEW_ITEM14.md](REVIEW_ITEM14.md));
its confirmed findings became **Items 15–16**, placed right after it. The review's
recommended order is **15 → 16 → 11 → 10 → 12 → 13** (stats validity first — it
changes every number the app shows — then responsiveness, then the owner batch).
**Items 15, 16, 10, 11, 12 and 13 are now done** (DESIGN_HISTORY Sessions 12–17);
Item 13 gained one bullet (the forest hover fix) before it landed. The remaining
batch items are **17–18** (delay metric + AADT weighting); the review's §3 ideas
await owner acceptance before becoming items.

**Appended 2026-07-16 (new owner request):** **Items 17–18** — a delay metric
(travel time vs free-flow) with before/after delta, and an AADT GIS layer for
volume-weighting delay/speed (the ITD layer is now in-repo as
`Cumulative_AADT.zip`; use only the 2024 `Year` rows). Placed at the end of the
batch by arrival; Item 18 depends on Item 17. Both Opus (data plumbing + weighted
aggregation, not statistics-heavy). Same day, **Item 13 absorbed two more owner
requests** — a day-of-week checkbox filter (ToD-slider sibling) and a
corridor-sum day×time summary — and Item 18 was corrected so **corridor travel
time stays a sum, never AADT-re-weighted**.

**Model assignment (owner, 2026-07-16):** **Item 15 is reassigned to Fable**,
overriding CLAUDE.md's Opus-end-to-end default — it is the batch's one
statistics/mathematics-heavy item and Fable produced its analytical foundation in
the review. All other items (16, 10–13) stay Opus. Going forward the rule of thumb
is *math-heavy → Fable, everything else → Opus*; the review's §3 candidates
**travel-time reliability percentiles** (block bootstrap) and a promoted
**difference-in-differences** are the next likely Fable items once scoped.

---

## 14 — Targeted app review by Fable (Target: Fable) — needs Item 7 (review-only)

A focused review pass over the explorer, **not** a re-read of the whole codebase —
skip the simple app wiring and the already-tested compute core; concentrate tokens
on where bugs and improvements actually live. Produces a written findings report
(and, if the owner acts on it, feeds new ROADMAP items) — this item ships analysis,
not code. **Run this before Items 10–13** so its findings can reshape them.

Scope (done 2026-07-16 — see [REVIEW_ITEM14.md](REVIEW_ITEM14.md) and
DESIGN_HISTORY.md Session 11):
- [x] **Bug hunt** in the interactive layer: `gui/app.py` callbacks (selection/
      cache/period-clamp edge cases, the `_compare_cache` keying, ToD/date
      interactions), `gui/figures.py` (empty-selection / NaN / single-point paths),
      and the thin adapter seams (`decompose`/`beforeafter`/`changepoint` inputs).
      Flag concrete failure scenarios, not style nits. → 6 findings (2 verified by
      running them), plus a verified-sound list; the feared short-series
      decompose→changepoint crash path was attacked and holds.
- [x] **Optimizations** where they matter: the ~2M-row load, repeated groupby/mean
      passes, the decomposition cost per panel, cache hit rates — cheap wins that
      keep the single-user localhost app responsive. → one real win (split the
      compare cache so period changes stop re-decomposing: 8.2 s → sub-second,
      measured); groupby/scan passes measured cheap and left alone.
- [x] **Broad / creative suggestions**: 7 candidate items, prioritised (results
      export, day-of-week filter + holidays, coverage panel, reliability
      percentiles, map-as-answer-surface, congestion-relative views, DiD).
- [x] **Statistical rigor of the before/after analysis** → the headline finding:
      the Welch CI treats 5-min samples as independent; simulated null coverage is
      25–50% at realistic autocorrelation (nominal 95%). Day-mean aggregation
      restores ~96%. Plus BH-FDR across segments, period-overlap validation, and
      two estimand caveats (drift → DiD; profile-shape absorption → ToD window).
- [x] Deliverable: [REVIEW_ITEM14.md](REVIEW_ITEM14.md) (bugs → quick wins →
      broader ideas → stats recommendations). Confirmed bugs/fixes became Items
      15–16 below; the hover fix was folded into Item 13; creative ideas await
      owner acceptance in the report §3. DESIGN_HISTORY Session 11 records it.

Suggested prompt:
> [Fable] In Inrix/, do Item 14 of ROADMAP.md: a targeted review of the Dash app —
> read `gui/app.py`, `gui/figures.py`, and the `beforeafter`/`decompose`/
> `changepoint` adapters (skip the simple wiring and well-tested io/geometry/kml).
> Report, in priority order: concrete bugs with failure scenarios, worthwhile
> optimizations for the ~2M-row single-user app, creative "what else would a user
> want to explore" generalisations as candidate ROADMAP items, and an assessment of
> the before/after statistical rigor (multiple comparisons, autocorrelation, the
> chosen estimand, difference-in-differences). Don't implement — deliver a
> prioritised findings report.

---

## 15 — Before/after statistical validity (Target: Fable) — needs Item 4; from the Item 14 review

**Target override (owner, 2026-07-16): Fable, not the Opus default.** This is the
one math-heavy item in the batch — autocorrelation-corrected inference (effective
sample size / daily-mean aggregation), Benjamini–Hochberg FDR, and null-coverage
*simulation* as a regression test — and Fable already produced its statistical
foundation in the Item 14 review (the coverage table, the day-mean fix, the FDR
recommendation). Overriding CLAUDE.md's Opus-end-to-end default is warranted where
the work is statistics-heavy and Fable holds warm context; the rest of the batch
(16, 10–13) stays Opus.

The Item 14 review's headline: the numbers the forest plot shows are
overconfident. The Welch CI in `beforeafter._compare_stats` treats ~6,000
autocorrelated 5-min samples per period as independent — simulated null coverage
is 25–50% at traffic-realistic AR(1) ρ (nominal 95%), restored to ~96% by
aggregating the seasonally-adjusted series to daily means first (see
REVIEW_ITEM14.md §4.1). Bundled with the other validity fixes in the same file:
multiple comparisons, period validation, and the GUI's overlapping default
windows (review B1, verified).

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 12):
- [x] `compare_periods`: aggregate the adjusted series to **daily means per
      segment** (per by-group) before `_compare_stats`; `n_before`/`n_after` now
      count days (the underlying 5-min counts reported as `n_samples_*`).
      `unit='sample'` escape hatch kept, labeled non-robust.
- [x] Benjamini–Hochberg `q_value` column across the returned family (pure core,
      hand-rolled step-up, NaN-safe); `beforeafter_forest` de-emphasises rows with
      q > 5% and captions the family size (`"N comparisons · k pass BH-FDR 5%"`).
- [x] Period validation: overlapping before/after periods **raise** (in
      `compare_periods` *and* `ttest_baseline`; half-open, so touching is fine);
      warm-up truncation emits a `UserWarning` + `attrs['warnings']`, with
      DST-safe effective day counts on `attrs['{before,after}_days_effective']`.
      The GUI surfaces both: the forest caption shows warnings; an overlap raise
      becomes a message figure (map delta falls back to mean colouring).
- [x] GUI default windows (`default_periods` in `gui/app.py`): disjoint halves
      after the warm-up, clamped to `[lo, hi]`, `None` when the span can't fit
      two one-day windows — disjoint at every span (B1's spans are the test).
- [x] Docstring notes in `beforeafter.py` (module + `compare_periods`):
      profile-shape absorption → ToD window / `by=` groups; secular drift → DiD.
- [x] pytest: null-coverage regression (AR(1) ρ=0.9, 50 reps: day-unit CI covers
      0 ≥80%, sample-unit ≤70%), day-vs-sample semantics, BH hand-case +
      monotonicity + NaN, overlap raise (+ touching-OK), warm-up warning +
      effective days, default-window disjointness across 11 spans, forest
      de-emphasis/caption. 9 new tests (124 total, all pass incl. the real-export
      end-to-end). DESIGN_HISTORY entry.

Suggested prompt:
> [Fable] In Inrix/, do Item 15 of ROADMAP.md: make the before/after CIs honest —
> aggregate the seasonally-adjusted series to daily means before the Welch CI
> (report n_days), add BH-FDR q-values across segments, validate periods
> (overlap raise + warm-up truncation warning), and fix the GUI's overlapping
> default windows. See REVIEW_ITEM14.md §1-B1 and §4. Tests + docs.

---

## 16 — Compare-cache split + GUI hardening (Target: Opus) — needs Item 7; from the Item 14 review

The review's performance win plus the confirmed wiring bugs, all in
`gui/app.py` (see REVIEW_ITEM14.md §1–§2). One session.

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 13):
- [x] **Split the compare cache (review O1, measured 8.2 s/miss).** Cache the
      seasonally-adjusted full-export frame per `(metric, ToD-window)` (cap ~2
      entries) and compute per-period Welch stats from it on demand, so
      date-picker changes stop re-decomposing. Reuse the per-segment slice for
      the decomposition tab. Compute core split into `adjust_for_periods` +
      `compare_adjusted` (+ `check_periods`); `compare_periods` composes them,
      behaviour byte-for-byte unchanged. Verified: 3 period changes + decomp tab
      = one decomposition.
- [x] **Evict old datasets (review B2, 2.3 GB/load).** `_DATASETS` keeps only the
      latest load (size-1); dependent caches live on the Dataset so they drop with it.
- [x] **Metric guard (review B3).** Speed-only or travel-time-only exports:
      `_metric_choices` disables the missing metric's radio option and picks the
      present one; `_map`/`_panels` guard `col is None`. No `KeyError`.
- [x] **Selection/viewport staleness (review B5).** `_load` resets `segment.value`
      to `None` on load; the map `uirevision` is keyed on the data token so a new
      export recenters.
- [x] Nits (review B6): a cleared CValue input defaults instead of `int(None)`;
      the decomposition empty-state message names the 7-day warm-up; the ToD
      slider's equal-handles case is documented as *whole day*.
- [x] pytest: cache-split hit behavior (period change = no re-decompose, via a
      monkeypatched counter), dataset eviction, metric-guard on a speed-only
      fixture, selection reset. 11 new tests (135 total). DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 16 of ROADMAP.md: GUI hardening from the Item 14
> review — cache the adjusted decomposition per (metric, ToD-window) so
> before/after date changes stop re-decomposing (8.2 s → sub-second), evict old
> datasets (2.3 GB each), guard missing metric columns, fix stale
> selection/viewport across loads, plus the review's B6 nits. See
> REVIEW_ITEM14.md §1–§2. Tests + docs.

---

## 10 — Friendly segment names (`names.py` + config CSV) (Target: Opus) — needs Item 1

Segments are currently labelled by the raw `Combined` string (Road + Direction +
Intersection, e.g. `N 9th St S 9th St / Idaho St`) everywhere they appear — the
dropdown, map hover, forest rows, panel titles. The owner wants a **readable,
user-controlled name per segment**, driven by a config file rather than an opaque
auto-ID. **Decision (owner): a user-editable CSV of names, seeded automatically by
simplifying the existing INRIX labels** (e.g. `9th St & Idaho St`), not a bare
truncated-ID scheme — the seed gives a good starting point the user then hand-edits.

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 14):
- [x] `src/inrix_tools/names.py` (pure): `seed_names(metadata) -> DataFrame`
      (`Segment ID`, `inrix_label`, `name`) where `name` is a simplified label
      derived from `Road`/`Direction`/`Intersection` — collapse the repeated
      road token and reduce `A / B` intersections to the cross street
      (`N 9th St S 9th St / Idaho St` → `9th St & Idaho St`). `simplify_label`
      strips the leading/trailing cardinal direction, keeps the descriptive tail
      of a `"<route#> / <name>"` road, and route-prefix-strips (`US-20 Myrtle St`
      → `Myrtle St`) each cross token before de-duping the road. Heuristic; a
      good hand-edit starting point (verified on all 46 Myrtle labels).
- [x] `write_names_template(metadata, path)` / `load_names(path)` — round-trip the
      CSV (stable `Segment ID` key, `name` column authoritative, unknown/blank
      rows fall back to the seed or `Segment ID`). No hardcoded paths; `load_names`
      validates the required columns.
- [x] `apply_names(...)` helper returning the `Segment ID → name` mapping the GUI
      labels read from (single source of truth, replacing the ad-hoc `_labels`
      dict in `gui/app.py`). Keep the raw `Combined` available for the hover
      second line so nothing is lost.
- [x] GUI wiring: an optional "Names CSV" path in the Data controls; when set,
      the dropdown, map hover, forest, and titles use the friendly name (stored on
      `Dataset.labels` at load, `geo["name"]` for the map title with `Combined` as
      the italic hover subtitle). A "Write name template" button writes the seed to
      `out/segment_names.csv` for editing.
- [x] pytest: seed simplification on representative Myrtle labels (the 9th/Idaho
      case + a few more), CSV round-trip, required-column validation, fallback for
      missing/blank/unknown rows, `apply_names` override precedence, the map hover
      title/subtitle, and the real-export label path. 18 new tests (153 total).
      DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 10 of ROADMAP.md: add `src/inrix_tools/names.py` —
> seed a simplified friendly-name per segment from the INRIX labels, round-trip a
> user-editable names CSV, and expose one `Segment ID → name` mapping the GUI uses
> for the dropdown/hover/forest/titles (with a "write template" button). Keep the
> raw `Combined` for the hover subtitle. pytest the simplifier + round-trip.

---

## 11 — Session date-subset on load (`io.filter_dates` + GUI) (Target: Opus, Sonnet-eligible) — needs Items 1, 7

The Myrtle export is ~2M rows; an analysis often only cares about a few weeks. Let
the user **pick a date range on/after load and discard the rest for the session**,
so every downstream compute (map, panels, decomposition) runs on the smaller frame
and stays snappy. A **pure-core row filter** feeding the existing `Dataset`, not a
new statistic — the same architectural shape as the Item 9 time-of-day window, but
on calendar date rather than time-of-day.

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 15):
- [x] `timebins.filter_date_range(df, start, end)` — keep rows whose local
      `Date Time` date is in `[start, end]` (inclusive calendar days, tz-aware,
      half-open at the next local midnight like `parse_period`, DST-safe); records
      the applied inclusive span on `attrs['date_range']` as an ISO `(start, end)`
      pair. `None`/`""` bound = open on that side; a `start` after `end` keeps
      nothing.
- [x] GUI: a "Restrict dates" `DatePickerRange` (defaulting to the full export
      span) applied **at load**, shrinking the cached `Dataset.df` so the reduction
      is real (memory + downstream speed), not just a display filter. Picker bounds
      are the *untrimmed* export span (`Dataset.full_span`) so it can widen again;
      the before/after and ToD controls clamp to the trimmed `span`.
- [x] The before/after default windows (`default_periods`) and the map/panels
      recompute against the trimmed `span` (they read `ds.df` / `ds.span`); the
      `_compare_cache` is fresh because each load builds a new `Dataset` with empty
      caches (`_store` evicts the prior one).
- [x] pytest: date filter (inclusive edges, whole end-day, open bound, tz/attrs,
      immutability, DST day, start>end) + GUI tests that a trimmed dataset carries
      fewer rows and the panels drive, plus the real-export restricted-load path.
      9 new tests (162 total). DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 11 of ROADMAP.md: add a session date-subset. Pure-core
> `filter_date_range` (inclusive local calendar days, tz-aware, attrs-recorded) +
> a GUI "Restrict dates" range that actually shrinks the cached `Dataset.df` on
> Apply so downstream compute runs on fewer rows. Clamp the before/after + ToD
> controls to the retained span and invalidate the compare cache. Tests + docs.

---

## 12 — Corridor & network travel-time analysis (`speed` + GUI scope) (Target: Opus) — needs Items 3, 4, 7

Analyse aggregate travel time, not just single segments: a **corridor** (a
`Corridor/Region Name` group) and the **network** (sum of *all* segments). The
compute half largely exists — `speed.corridor_travel_time` already sums member
segments per timestamp under the complete-set rule — so this item is a small core
addition (a network total) plus wiring an **analysis-scope selector** so the
time-series / before-after / decomposition panels can run on the aggregate series.
**Decision (owner): travel time only** — summing travel time across segments is
well-defined; there is no good segment-weighting for speed, so the corridor/network
scope offers travel time exclusively (speed stays segment-level).

Scope (done 2026-07-16 — see DESIGN_HISTORY.md Session 16):
- [x] `speed.network_travel_time(df, ...)` — the corridor sum with **all segments
      as one group** (reuses `corridor_travel_time`'s complete-set machinery by
      overwriting the corridor label to one synthetic `"Network"` value),
      returning the per-timestamp network total. Attaches summed network length /
      space-mean speed when metadata is supplied, mirroring `corridor_travel_time`.
- [x] GUI: an **analysis-scope selector** — *Segment* (current) / *Corridor*
      (`Corridor/Region Name` picker) / *Network*. In corridor/network scope the
      metric is forced to travel time (`_scope_metric` disables Speed); the
      time-series, before/after, and decomposition panels run on the aggregate
      per-timestamp series. The day×time summary + map stay **segment-level** in
      every scope. Scope options disable Corridor when the export has no corridor
      column and both aggregate scopes when it carries no travel-time column.
- [x] Feeds the aggregate series into `beforeafter.compare_periods` /
      `decompose_segments` with a single synthetic `Segment ID` (`_AGG_SEGMENT_ID
      = -1`) so the existing adapters work unchanged; the `_adjusted_cache` /
      `_compare_cache` keys gained scope+corridor so scopes don't collide. The
      complete-set drop doesn't starve decomposition on the Myrtle export (the
      auto-scaled Item 9 window guard covers any future sparse case).
- [x] pytest: known network sum on a synthetic multi-corridor fixture (complete-set
      drop applied), length/space-mean speed, before/after + decomposition run on
      the aggregate, scope-option disabling, aggregate-frame collapse, corridor
      guard, cache-keying, and the real-export network path. 11 new tests (173
      total). DESIGN_HISTORY entry + DATA_FORMAT complete-set-at-network-scale note.

Suggested prompt:
> [Opus] In Inrix/, do Item 12 of ROADMAP.md: corridor + network (all-segments)
> travel-time analysis. Add `speed.network_travel_time` (corridor sum over one
> all-segments group, complete-set rule) and a GUI analysis-scope selector
> (Segment / Corridor / Network) that runs the time-series, before/after, and
> decomposition panels on the aggregate per-timestamp series via a synthetic
> single entity id. Travel time only (no speed weighting). Tests + docs.

---

## 13 — Before/after summary + GUI display polish (Target: Opus) — needs Item 7

A GUI session bundling a few small features with cosmetic tweaks. Mostly
`gui/figures.py` / `gui/app.py` with no new statistics — **plus one pure-core row
filter** (`timebins.filter_day_of_week`), architecturally parallel to the Item 9
time-of-day window. (Grew from the original 5 tweaks with the owner's day-of-week
selector and corridor day×time summary requests, 2026-07-16; if a session runs
long, the day-of-week filter is the clean split-off point.)

Scope:
- [x] **Day-of-week selector (the ToD slider's DOW sibling).** Pure-core
      `timebins.filter_day_of_week(df, days)` — keep rows whose local `day_of_week`
      is in a selected set (accepts names/abbrevs/0–6; empty or all-seven = no-op;
      records the applied set on `attrs`), same overnight-safe local-wall-clock
      basis as `filter_time_window`. GUI: a row of **weekday checkboxes**
      (`dcc.Checklist`, Mon–Sun, all checked by default) that pre-filters **every**
      panel, the map colouring, and KML export exactly like the Item 9 slider,
      composing with it (ToD **and** DOW both applied) — a plain-language status
      line states the active days. Subtlety to note: the filter keeps *whole days*,
      so the Item 9 rolling-window sample guard is unaffected; but restricting to
      too few distinct weekdays weakens `decompose`'s weekly-seasonal fit — document
      it (the daily component + residuals still carry the before/after signal), and
      the day-group scheme (Item 2) is unchanged.
- [x] **Before/after day×time summary (the feature).** `summary_bars` /
      `_fig_summary` gain a before/after mode: compute `speed.segment_summary` on
      the before-subset and the after-subset separately and render them **side by
      side (left/right)** — either paired facets or before/after as an extra bar
      grouping — so the day-group×time-bin means are directly comparable across the
      intervention. Reuse the existing before/after date ranges; fall back to the
      single-period view when periods aren't set.
- [x] **Corridor/network day×time summary (revisits the Item 12 decision).** Item 12
      kept the day×time summary panel segment-level in every scope; the owner now
      wants it available for the **corridor sum**. In corridor/network scope, build
      the day×time summary on the **aggregate per-timestamp travel-time series**
      (`speed.corridor_travel_time` / `network_travel_time`, complete-set rule) so
      the bars show the **summed** corridor travel time per day-group×time-bin —
      **sum across member segments, not the mean of segment means** (matches the
      "corridor travel time is a sum" rule; the panel still means *over time* within
      each bin). Travel time only, mirroring the Item 12 scope; segment scope is
      unchanged. Composes with the before/after side-by-side mode above.
- [x] **Compact KML export.** Demote the full-width "Export KML of current view"
      button to a small **icon button next to the map** (the owner rarely needs
      KML now; keep the capability, shrink the footprint). Status/errors surface in
      a tooltip or a small inline note rather than a dedicated block.
- [x] **Shrink the map midpoint markers.** The midpoint dots are the click target,
      hover anchor, *and* colour-bar carrier (see `figures.segment_map`), so they
      can't just be removed — reduce them to small/near-transparent marks (or move
      the colour scale onto the line traces) while **keeping click + hover working**.
      Verify selection still fires after the change.
- [x] **Time-formatted ToD slider tooltip.** The `dcc.RangeSlider` tooltip shows
      the raw hour (`13.5`); make it read as a clock time (`1:30 PM` / `13:30`) via
      a Dash `tooltip.transform` client-side JS formatter (mirrors the existing
      Python `_hour_label`). Keep `always_visible` off.
- [x] **Forest hover fix (from the Item 14 review, B4).** `beforeafter_forest`'s
      hovertemplate uses `%{y}` with a numeric y, so hover shows the row index
      instead of the segment name — move the name into `customdata`/`text`
      (`gui/figures.py:282-291`).
- [x] pytest: `filter_day_of_week` (subset/no-op/attrs/immutability, name+index
      forms, composes with `filter_time_window`); `summary_bars` before/after mode
      (trace/facet structure, single-period fallback) and corridor-sum mode (bars =
      summed corridor travel time, sum-not-mean verified on a synthetic fixture);
      the layout smoke test still finds the (now-iconified) export control + slider
      + the new DOW checklist. Manual/preview check the marker click still selects.
      DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 13 of ROADMAP.md: GUI work — (1) a pure-core
> `timebins.filter_day_of_week` + a weekday-checkbox row that pre-filters every
> panel/map/KML like the Item 9 ToD slider (composes with it); (2) a before/after
> side-by-side mode for the day×time summary panel (compute `segment_summary` per
> period, render left/right); (3) a corridor/network day×time summary built on the
> **summed** `corridor_travel_time` series (sum across segments, not mean of means);
> (4) demote the KML button to a compact icon by the map; (5) shrink the map
> midpoint markers while keeping click+hover; (6) format the ToD slider tooltip as a
> clock time via `tooltip.transform`; (7) the B4 forest hover fix. Tests + a preview
> check that selection still works. Docs.

---

## 17 — Delay vs free-flow travel time + before/after delay increase (`speed.segment_delay`) (Target: Opus) — needs Items 3, 4, 8

Add **delay** as a first-class metric: the excess travel time a segment carries
over its free-flow (open-road) travel time. INRIX already supplies free-flow
speed per row (`Ref Speed(miles/hour)`, DATA_FORMAT.md — the open-road reference,
**not** the posted limit) and observed `Travel Time(Minutes)`, and the geometry
layer supplies segment length (`Miles`), so delay is a pure-core derivation, not a
new data source. The owner's framing — *"approximate delay increase as delta of
travel time vs free-flow TT in before and after period"* — is then just the
existing before/after machinery (Item 4/15) run on the delay column: the reported
effect is the change in delay across the intervention.

Scope:
- [ ] `speed.segment_delay(df, geo_or_metadata=None, free_flow='ref', floor=True)`
      — add a per-row `Delay(Minutes)` column = observed `Travel Time(Minutes)` −
      free-flow travel time, where free-flow TT = `Miles / free_flow_speed × 60`.
      `free_flow` selectable: `'ref'` (the per-row `Ref Speed` column, default),
      or a per-segment high percentile of observed speed (e.g. `('pXX', 95)`) for
      exports/segments where `Ref Speed` is missing or suspect. `floor=True`
      clamps negatives (probe noise faster than free-flow) to 0; record the source
      + floor on `attrs`. Pure — no plotting. Degrade gracefully when length is
      unavailable (fall back to speed-based delay: `Miles·(1/v − 1/v_ff)·60`,
      which cancels the length dependence into the same value).
- [ ] Delay flows through the existing aggregation and before/after paths as
      another value column: `segment_summary` / `daily_timebin_summary` pick it up
      by prefix, and `beforeafter.compare_periods` on `value_column='Delay(Minutes)'`
      gives the **delay increase (Δ delay + CI)** the owner asked for. Confirm the
      Item 15 day-mean aggregation + BH-FDR apply unchanged (delay is just another
      seasonally-adjustable series). Corridor/network scope (Item 12): delay sums
      across member segments exactly like travel time (same complete-set rule).
- [ ] GUI: add **Delay** as a metric option alongside Speed / Travel time (map
      colouring, time series, summary, before/after forest, decomposition). The
      metric radio + `_scope_metric` / `_metric_choices` guards learn the new
      column; a segment with no resolvable free-flow speed is disabled like a
      missing metric (Item 16 B3 pattern). Free-flow source is a small control
      (default `Ref Speed`).
- [ ] pytest: known delay on a synthetic fixture (observed TT − length/RefSpeed·60,
      floor behaviour, the speed-based fallback equals the length-based value),
      `attrs` recording, before/after Δ-delay recovers an injected free-flow-gap
      shift with a CI excluding 0, corridor delay = sum of member delays under the
      complete-set rule, GUI metric wiring, and the real-export delay path.
      DESIGN_HISTORY entry; DATA_FORMAT delay-definition note.

Suggested prompt:
> [Opus] In Inrix/, do Item 17 of ROADMAP.md: add a delay metric —
> `speed.segment_delay` = observed `Travel Time(Minutes)` − free-flow TT
> (`Miles / Ref Speed × 60`, floor at 0, percentile-speed fallback), pure-core.
> Wire it through `segment_summary`/`compare_periods` so the before/after forest
> reports the **change in delay**, and add Delay as a GUI metric (map/panels).
> pytest the delay math + an injected before/after shift. Docs.

---

## 18 — AADT weighting layer (`aadt.py` + GIS join + GUI) (Target: Opus) — needs Items 8, 12, 17

Weight the segment metrics by traffic **volume**. A segment carrying 40k
vehicles/day and one carrying 2k should not count equally when you summarize
speed across a corridor, and the impact of delay is really **vehicle-hours**, not
per-vehicle minutes. AADT (Annual Average Daily Traffic) is **not** in the INRIX
export — the owner has added the ITD layer as **`Cumulative_AADT.zip`** in the
repo root (a gitignored fixture, like the Myrtle export). Confirmed schema
(2026-07-16): 251k `LineString Z` features in **EPSG:8826** (reproject to 4326);
a **`Year`** field (the layer is cumulative across years — **use only 2024**, the
most recent, per owner); an `AADT` value column; route-measure identity
(`RouteID` / `Route` / `FromMeasur` / `ToMeasure` / milepost); and extras
(`PassengerA`, `Commercial`, `MADT1..12`). **There is no `XDSegID`**, so the join
to our `Segment ID` is necessarily **spatial** (nearest-line + bearing) — that
decision is now made, not deferred.

Scope:
- [ ] `src/inrix_tools/aadt.py` (pure): `load_aadt(source, year=2024, bbox=None, ...)`
      — read `Cumulative_AADT.zip` via GDAL `/vsizip/` (like
      `geometry.load_xd_network`), **filter to `Year == year` (default 2024)**,
      reproject **EPSG:8826 → EPSG:4326**, cast to real types, keep `AADT` + route
      identity (`RouteID`/`Route`/measures) and optionally the class split
      (`Commercial` for a future truck view). Optional `bbox`/`segment_ids`
      pushdown so we don't hold all 251k statewide features. No hardcoded paths.
      `join_aadt(geo, aadt, ...)` — attach an `AADT` value per `Segment ID` by
      spatial match to the Item 8 segment geometry (buffered nearest-line with a
      bearing check to reject the opposing/cross-street line), flagged via an
      `aadt_source` column (`matched` / `nearest` / `missing`) with the match
      distance, so a bad join is visible not silent.
- [ ] `weighted_metric(...)` helpers (pure). **Corridor/network travel time stays a
      pure sum across segments (Item 12) — AADT does *not* re-weight it.** Volume
      weighting applies where a *mean across segments* is what's summarized:
      (a) **vehicle-hours of delay** = `Delay(Minutes)/60 × AADT` per segment (the
      headline volume-aware impact number, using Item 17's delay; summable to a
      corridor/network total), and (b) an **AADT-weighted mean speed** across member
      segments (Σ w·x / Σ w, weights = AADT) so a corridor speed reflects where the
      vehicles actually are. Return typed DataFrames; document the AADT-as-daily-total
      vs analysis-window caveat (it's a relative weight, not an absolute VMT unless
      the window is scaled — note it, don't silently scale).
- [ ] GUI: an optional **"AADT layer"** path in the Data controls (like the Item 10
      names CSV; defaults to the repo `Cumulative_AADT.zip`). When set: a
      **vehicle-hours-of-delay** map colouring option (Item 17 delay × AADT) and, in
      corridor/network scope, an **AADT-weighted mean speed** toggle — travel time
      stays the sum. Segments with no AADT match are shown/flagged, not dropped.
      Unset AADT → the extra options hidden, behaviour unchanged. Show the `AADT`
      value (and match quality) in the segment hover.
- [ ] pytest: `load_aadt` year filter (only 2024 rows) + reprojection to 4326;
      `join_aadt` on a synthetic geometry+AADT fixture (correct nearest match,
      cross-street rejected by the bearing check, missing flagged); `weighted_metric`
      known vehicle-hours and weighted-mean-speed values (weights sum, a
      zero/missing-AADT segment handled); GUI wiring (options appear only with a
      layer, weighted speed differs from unweighted, travel time unchanged); and a
      **real Myrtle-bbox join** against `Cumulative_AADT.zip` (self-skip if absent,
      like the real-export tests). DESIGN_HISTORY entry; DATA_FORMAT.md gains an
      AADT-source section (fields, EPSG:8826, `Year`/2024, spatial-join key).

Suggested prompt:
> [Opus] In Inrix/, do Item 18 of ROADMAP.md: add an AADT volume-weighting layer.
> `src/inrix_tools/aadt.py` — `load_aadt` reads the repo `Cumulative_AADT.zip`
> (filter `Year==2024`, reproject EPSG:8826→4326, keep `AADT` + route identity) and
> `join_aadt` spatially attaches AADT to each `Segment ID` against the Item 8
> geometry (nearest-line + bearing check, match flagged). `weighted_metric` helpers:
> vehicle-hours of delay (Item 17 delay × AADT) and AADT-weighted mean speed across
> a corridor — **corridor travel time stays a sum, not re-weighted**. GUI: optional
> AADT path → vehicle-hours map colouring + a weighted-speed toggle. pytest the year
> filter, spatial join, and weighted math against a Myrtle-bbox slice. Docs.

---

## Future (not yet scoped — need a planning pass before they're actionable)

- **Anomaly / incident flagging** — wrap `traffic_anomaly.anomaly` (z-score /
  GEH on residuals, entity- and group-level) to flag bad sensor data, incidents,
  and unusual days. Deferred from the initial analysis scope.
- **Difference-in-differences before/after** — when an unaffected control
  corridor exists, the gold-standard intervention estimate; builds on Item 4.
  The Item 14 review recommends promoting this once an export carries a
  plausible control (secular drift is otherwise attributed to the intervention
  — REVIEW_ITEM14.md §4.4); the compute slots onto Item 15's day-mean machinery.
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
