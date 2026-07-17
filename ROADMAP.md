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
**Items 15 and 16 are now done** (DESIGN_HISTORY Sessions 12–13); the remaining
order is **11 → 10 → 12 → 13**. Item 13 gained one bullet (the forest hover fix);
the review's §3 ideas await owner acceptance before becoming items.

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

Scope:
- [ ] `timebins.filter_date_range(df, start, end)` (or `io.filter_dates`) — keep
      rows whose local `Date Time` date is in `[start, end]` (inclusive calendar
      days, tz-aware, half-open at the next midnight like `parse_period`); records
      the retained span on `attrs`. Empty/None bound = open on that side.
- [ ] GUI: a "Restrict dates" `DatePickerRange` (defaulting to the full export
      span) applied **at load / on an Apply button**, shrinking the cached
      `Dataset.df` so the reduction is real (memory + speed), not just a display
      filter. The before/after and time-of-day controls then operate within the
      retained span (clamp their allowed range to it).
- [ ] Confirm the before/after default windows and the map/panels recompute
      correctly against the trimmed `span`; invalidate the `_compare_cache`.
- [ ] pytest: date filter (inclusive edges, open bound, tz/attrs, immutability)
      + a GUI test that a trimmed dataset carries fewer rows and the panels drive.
      DESIGN_HISTORY entry.

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

Scope:
- [ ] `speed.network_travel_time(df, ...)` — the corridor sum with **all segments
      as one group** (reuse `corridor_travel_time`'s complete-set machinery with a
      synthetic single-group key), returning the per-timestamp network total.
      Optionally add corridor length/space-mean speed when metadata is supplied,
      mirroring `corridor_travel_time`.
- [ ] GUI: an **analysis-scope selector** — *Segment* (current) / *Corridor*
      (`Corridor/Region Name` picker) / *Network*. In corridor/network scope the
      metric is forced to travel time; the time-series, before/after, and
      decomposition panels run on the aggregate per-timestamp series (which is
      already the `[Date Time, Travel Time, Segment ID-less]` shape `decompose`
      wants once given a single entity id). The day×time summary + map stay
      segment-level (or grey out) as appropriate.
- [ ] Feed the aggregate series into `beforeafter.compare_periods` /
      `decompose_segments` with a single synthetic `Segment ID` so the existing
      adapters work unchanged; confirm the complete-set drop doesn't starve
      decomposition (document if the window guard interacts, cf. Item 9).
- [ ] pytest: known network sum on a synthetic multi-segment fixture (complete-set
      drop applied), before/after + decomposition run on the aggregate, GUI scope
      wiring. DESIGN_HISTORY entry; DATA_FORMAT note if the aggregate path teaches
      anything about the complete-set rule at network scale.

Suggested prompt:
> [Opus] In Inrix/, do Item 12 of ROADMAP.md: corridor + network (all-segments)
> travel-time analysis. Add `speed.network_travel_time` (corridor sum over one
> all-segments group, complete-set rule) and a GUI analysis-scope selector
> (Segment / Corridor / Network) that runs the time-series, before/after, and
> decomposition panels on the aggregate per-timestamp series via a synthetic
> single entity id. Travel time only (no speed weighting). Tests + docs.

---

## 13 — Before/after summary + GUI display polish (Target: Opus) — needs Item 7

A GUI-display session bundling one small feature and three cosmetic tweaks — all
in `gui/figures.py` / `gui/app.py`, no new statistics.

Scope:
- [ ] **Before/after day×time summary (the feature).** `summary_bars` /
      `_fig_summary` gain a before/after mode: compute `speed.segment_summary` on
      the before-subset and the after-subset separately and render them **side by
      side (left/right)** — either paired facets or before/after as an extra bar
      grouping — so the day-group×time-bin means are directly comparable across the
      intervention. Reuse the existing before/after date ranges; fall back to the
      single-period view when periods aren't set.
- [ ] **Compact KML export.** Demote the full-width "Export KML of current view"
      button to a small **icon button next to the map** (the owner rarely needs
      KML now; keep the capability, shrink the footprint). Status/errors surface in
      a tooltip or a small inline note rather than a dedicated block.
- [ ] **Shrink the map midpoint markers.** The midpoint dots are the click target,
      hover anchor, *and* colour-bar carrier (see `figures.segment_map`), so they
      can't just be removed — reduce them to small/near-transparent marks (or move
      the colour scale onto the line traces) while **keeping click + hover working**.
      Verify selection still fires after the change.
- [ ] **Time-formatted ToD slider tooltip.** The `dcc.RangeSlider` tooltip shows
      the raw hour (`13.5`); make it read as a clock time (`1:30 PM` / `13:30`) via
      a Dash `tooltip.transform` client-side JS formatter (mirrors the existing
      Python `_hour_label`). Keep `always_visible` off.
- [ ] **Forest hover fix (from the Item 14 review, B4).** `beforeafter_forest`'s
      hovertemplate uses `%{y}` with a numeric y, so hover shows the row index
      instead of the segment name — move the name into `customdata`/`text`
      (`gui/figures.py:282-291`).
- [ ] pytest: `summary_bars` before/after mode (trace/facet structure, single-period
      fallback), and the layout smoke test still finds the (now-iconified) export
      control + slider. Manual/preview check the marker click still selects.
      DESIGN_HISTORY entry.

Suggested prompt:
> [Opus] In Inrix/, do Item 13 of ROADMAP.md: GUI display work — (1) a before/after
> side-by-side mode for the day×time summary panel (compute `segment_summary` per
> period, render left/right), (2) demote the KML button to a compact icon by the
> map, (3) shrink the map midpoint markers while keeping click+hover, (4) format the
> ToD slider tooltip as a clock time via `tooltip.transform`. Tests + a preview
> check that selection still works. Docs.

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
