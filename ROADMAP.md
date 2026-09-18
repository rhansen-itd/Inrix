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
  session on it. The rule of thumb the owner set (2026-07-16) is *math-heavy →
  Fable, everything else → Opus*.
- Tell the agent "do Item N of ROADMAP.md" to run a scope. Check off boxes and
  add a session entry to [DESIGN_HISTORY.md](DESIGN_HISTORY.md) as items land.

**Status (2026-07-17):** the initial build (Items 1–9) plus the post-build
refinement batch (Items 10–18 — friendly names, date subset, corridor/network
scope, the delay metric, and the AADT volume-weighting layer) are **complete**;
the full build record lives in [DESIGN_HISTORY.md](DESIGN_HISTORY.md) (Sessions
0–22). The finished items have been **cleared from this file** to keep it focused
on open work — a one-line index below points each one at its DESIGN_HISTORY
session so nothing is orphaned. **Items 19+ are a new owner-requested batch**
scoped 2026-07-17 (DESIGN_HISTORY Session 23); they refine the working explorer
rather than adding to the core pipeline. **Item 19 (the interactive segment table)
is done** (Session 24), **Item 20 (GUI map & layout display polish) is done**
(Session 25), and **Item 21 (DB-backed storage & ingest) is done** (Session 26).
An owner follow-on, **Item 23 (area-based store: merge exports by corridor set +
bin-length partition), is now done** (Session 27), superseding Item 21's
one-dataset-per-export model. The batch (Items 19–21, 23) is complete.
**Items 24–27 are a new batch** from the 2026-07-18 whole-app review
([REVIEW_APP_2026-07-18.md](REVIEW_APP_2026-07-18.md), DESIGN_HISTORY Session 28):
DB-first intake, date push-down, a hardening pass, and the corridor-scoped
segment-table redesign. **Item 24 (DB-first intake inversion) is done** (Session
29) and **Item 25 (date push-down: Restrict-dates on DB loads) is done** (Session
30), **Item 26 (store & app hardening — R5/R6/R9) is done** (Session 31), and **Item 27
(corridor-scoped segment table + names in the DuckDB store — R10/R11/R12) is done**
(Session 32). The 2026-07-18 review batch (Items 24–27) is **complete**.

**Items 28–30 are a new batch** scoped 2026-09-17 (DESIGN_HISTORY Session 33) out of a
review of an outside INRIX-vs-Google travel-time comparison run against this project's
data. That comparison's plumbing largely held up — clock alignment, chain termination,
and confidence values all check out — but its headline ("the two sources are
functionally interchangeable") is contradicted by its own numbers: **INRIX reports
roughly half the delay** the external reference does on every signalised arterial
measured. None of it was reproducible (the analysis script never entered the repo), so
this batch brings the work into the pure core with tests: **28** — corridor chain
assembly with endpoint trim and missing-segment accounting; **29** — the external
reference loader, consistency gates, and agreement statistics; **30** — the report
rebuilt as a thin shell, plus the delay-compression finding recorded in DATA_FORMAT.
Run them in order (29 needs 28, 30 needs 29). **Item 28 (corridor chain assembly —
snap, walk, trim, account) is done** (Session 34), **Item 29 (external reference
loader, consistency gates, agreement statistics) is done** (Session 35), and **Item 30
(validation report as a thin shell + the delay-compression finding) is done** (Session
36). The batch (Items 28–30) is **complete**.

**Items 31–33 are a new batch** scoped 2026-09-17 (DESIGN_HISTORY Session 37) out of a
**second** review of the same validation work, run against the raw export rather than
the first review's notes. The arterial delay-compression finding **held up under every
alternative explanation tested** — historical backfill, missing member segments, and
clock lag were each measured and ruled out — but three things need fixing: the
complete-set rule counts *observed* rather than *requested* membership, so a chain can
still sum short while marked complete (**31**); the finding is a **slope**, not an
offset, and the ratio-of-means currently reporting it is the least stable statistic
available (**32**); and the directional free-flow level gap on SH-69 SB / Eagle Rd
remains unexplained after two reviews (**33**). Run them in order (32 needs 31, 33 needs
32). Everything else is in **Future** (needs a planning pass).

**Items 34–37 are a new batch** scoped 2026-09-17 (DESIGN_HISTORY Session 38) out of a
review of an **outside district-wide corridor screening** run against this project's
data — an attempt to rank the most congested corridors in ITD District 3 across all
3,905 segments. Its aggregation checked out (the peak-window bin counts, the timezone
conversion, and the per-segment travel-time means all reproduce exactly against the raw
zips), and it demonstrated a capability this repo does not have: a **screening** pass
that asks *which* corridors are worst rather than analysing a corridor you already
named. But it got there by reimplementing `corridors.py` badly against a stale copy of
the package, and it surfaced a **real bug in `aadt.join_aadt`**: on a divided highway
the parallel on/off ramp out-competes the mainline for the spatial match, so one
carriageway of I-84 is weighted at **61,410** AADT where the other is 114,981 — a
near-halving of the westbound vehicle-hours of delay, which is the metric everything
was ranked by. This batch fixes the join (**34**), brings the screening pass into the
core over the DuckDB store (**35**), converts the outside work's D3 corridor catalogue
to endpoint pairs resolved through `build_chain` (**36**), and fixes the KML palette
collision the screening output exposed (**37**). Run 34 first — 35's rankings are
meaningless until the volume weights are right. **Item 34 (the ramp-vs-mainline AADT
join) is done** (Session 39), **Item 35 (district-wide screening: `screen.py` +
the streaming ingest) is done** (Session 40) — the whole 2026 D3 export (**91,054,384
rows**) now ingests in 262 s and screens to 3,905 segment rows in 11 s — and **Item 36
(the D3 corridor catalogue as endpoint pairs) is done** (Session 41): 20 entries in
`scripts/d3_corridors.json`, 13 resolving and 7 recorded as findings about the XD
topology rather than bridged, and **Item 37 (the KML palette collision) is done**
(Session 42): the categorical palette extended 8 → 12 hues with a raise-on-overflow
guard (an explicit `palette=` still cycles), `folder_by=` groups placemarks into KML
`<Folder>`s, and `name_col` defaults to the `names.apply_names` friendly name. The batch
(Items 34–37) is **complete**. Everything else is in **Future** (needs a planning pass).

---

## Completed (build record in DESIGN_HISTORY.md)

Items 1–18 are done and certified (tests + DESIGN_HISTORY entries). Kept here as a
one-line index only; the full scopes, decisions, and rationale are in the linked
sessions.

- **1** — Data I/O core (`io.py`) — DESIGN_HISTORY Session 1
- **2** — Time binning (`timebins.py`) — Session 3
- **3** — Speed / travel-time aggregation (`speed.py`) — Session 4
- **4** — Decomposition + before/after (`decompose.py`, `beforeafter.py`) — Session 5
- **5** — Changepoint detection (`changepoint.py`) — Session 6
- **6** — KML export (`kml.py`) — Session 7
- **7** — Dash data explorer + embedded map (`gui/app.py`) — Session 8
- **8** — Segment geometry layer (`geometry.py`) — Session 2
- **9** — Time-of-day analysis window (`timebins.filter_time_window` + slider) — Session 9
- **10** — Friendly segment names (`names.py` + config CSV) — Session 14
- **11** — Session date-subset on load (`io`/`timebins.filter_date_range` + GUI) — Session 15
- **12** — Corridor & network travel-time analysis (`speed` + GUI scope) — Session 16
- **13** — Before/after summary + GUI display polish — Session 17
- **14** — Targeted app review by Fable (review-only, [REVIEW_ITEM14.md](REVIEW_ITEM14.md)) — Session 11
- **15** — Before/after statistical validity (day-mean CI, BH-FDR, period validation) — Session 12
- **16** — Compare-cache split + GUI hardening — Session 13
- **17** — Delay vs free-flow travel time (`speed.segment_delay`) — Session 18
- **18** — AADT weighting layer (`aadt.py` + spatial join + GUI) — Session 19

- **19** — Interactive segment table: editable names + corridor selection +
  completeness (`speed.segment_coverage`, `members=` on corridor/network,
  `names.write_names`, `dash_table` + map link) — DESIGN_HISTORY Session 24
- **20** — GUI map & layout display polish: layout reflow (charts below the map,
  right of the settings column) + directional segment display (`geometry`
  direction/sign helpers + perpendicular display offset + compass toggles) —
  DESIGN_HISTORY Session 25
- **21** — DB-backed storage & ingest (`store.py`, DuckDB) + GUI intake/select
  (ingest once, run from the DB; the Item 18 spatial join cached at ingest) —
  DESIGN_HISTORY Session 26
- **23** — Area-based store (owner follow-on to Item 21): merge exports by
  **corridor set** into a persistent *area* (keep-first dedup), partition by
  auto-detected **bin length**, GUI area + bin selectors — DESIGN_HISTORY Session 27

Post-batch correctness review of Items 15–18 and its fixes: Sessions 20–22.

---

# New batch — refinements on the finished app (Items 19+, scoped 2026-07-17)

Owner-requested refinements to the working explorer, grouped into session-sized
items per CLAUDE.md (small related requests merged). File order is priority
order; each is independent of the others unless noted. Owner decisions behind the
scoping are recorded in DESIGN_HISTORY Session 23.

**Grouping note:** the owner's "selectable segments for a corridor" request and
the "improve the CSV segment-name workflow (built-in editor)" request were
**merged into one item (Item 19)** at the owner's suggestion — both are the same
editable-*and*-selectable segment table surface, linked to the map. The GUI-display
work (layout reflow + directional segment display, **Item 20**) and the database
storage work (Item 21) stand alone.

The owner's original single "directionality" bullet conflated **two** things,
now split (2026-07-17): **direction-aware AADT *volume*** (which direction's
count to weight by) was routed to **Future** (unscoped — it needs a planning pass
on whether the AADT layer even carries direction), while **directional *display*
of segments on the map** (co-located opposing segments overlapping so only one
renders) is the concrete GUI/geometry fix — initially scoped as its own Item 22,
then **merged into Item 20** (same `gui/app.py` map/layout code region, so one
session loads that context once). The display fix and the AADT-volume idea share a
compass/`+`-`−` direction convention but are otherwise independent.

---

## 19 — (done 2026-07-17 — interactive segment table) — see Completed index + DESIGN_HISTORY Session 24

Delivered: `speed.segment_coverage` (per-segment coverage % + exact
`complete_set_cost`), additive `members=` on `corridor_travel_time` /
`network_travel_time`, `names.write_names` (the in-app name-edit round-trip), and a
two-way map-linked `dash_table.DataTable` (editable names, row-selection membership,
coverage/completeness columns, non-member map dimming). 243 tests pass incl. the
real-export path; both link directions preview-verified on the Myrtle export. Full
scope cleared from this file per the Completed-index convention.

---

## 20 — (done 2026-07-17 — GUI map & layout display polish) — see Completed index + DESIGN_HISTORY Session 25

Delivered **both** fixes in one pass (decision recorded in Session 25: ship toggles
*and* offset). **(A)** Layout reflow — the chart tabs moved into the right column
below the map + segment table (settings stay left), columns made responsive
(`xs=12/lg=3` · `lg=9`), map↔charts gap closed. **(B)** Directional display — pure
`geometry` helpers `direction_group` / `direction_sign` (N/E = `+`, S/W = `−`, shared
with the Future directional-AADT item), `attach_directions`, and
`offset_overlapping_segments` (display-only perpendicular nudge for co-located
opposing pairs; analytic geometry untouched); GUI `dir-compass` multiselect +
`dir-offset` switch driving a `_display_geo` render frame. 250 tests pass incl. the
real-export path; preview-verified the closed gap and a previously-hidden opposing
segment now offset + clickable (N filter → 23 of 46). Full scope cleared per the
Completed-index convention.

---

## 22 — (merged into Item 20 on 2026-07-17 — directional segment display)

Retired ID — the directional map-display fix was combined with the layout reflow
into **Item 20** (same `gui/app.py` map/layout code region; one GUI-context load).
Not reused. See DESIGN_HISTORY Session 23.

---

## 21 — (done 2026-07-17 — DB-backed storage & ingest) — see Completed index + DESIGN_HISTORY Session 26

Delivered the pure-core `store.py` (DuckDB, decision recorded) and the GUI intake:
`connect`/`ingest_export`/`put_export`/`ingest_geometry`/`ingest_aadt`/`list_datasets`/
`load_dataset` over idempotent per-dataset tables, with `Date Time` round-tripping
tz-aware UTC and geometry as WKB blobs (no in-DB spatial needed); the Item 18 spatial
join is cached **once at ingest** and read back, never recomputed per load. GUI gained
a low-visibility "⤓ Ingest current export to DB" button + a "Saved datasets" select
(reads the DB, auto-loads on pick), with the file-path loader unchanged (DB optional).
Full scope cleared per the Completed-index convention.

<details><summary>Original scope (delivered)</summary>

Move from re-parsing the export zip and the GIS shapefiles every session to a
**persistent local database**. Establish one DB connection; a low-visibility
**intake/ingest** control loads an export (and the GIS layers — XD geometry and
the AADT segment info, including the Item 18 join) into the DB once; thereafter
the app runs from the DB — **select** an ingested dataset instead of re-parsing.
Cache the processed/joined GIS data (segment → geometry, segment → AADT with the
Item 18 match flags) so the expensive spatial join isn't repeated per session.

Keep the pure-core architecture: the DB adapter is a **new pure module** (no GUI
imports, **no hardcoded paths** — the DB path / connection is a param), and the
existing file loaders keep working so the DB is optional, not required.

**Decide at the top of the session and record it:** which DB. **DuckDB** is
already a transitive dependency (via `traffic-anomaly`'s
`ibis-framework[duckdb]`) and is the natural fit for columnar analytic scans of
~2M-row exports plus spatial (DuckDB `spatial` extension for the geometry/AADT
tables); SQLite is the fallback if in-DB spatial isn't needed. Pick per the
ingest/query needs, not by default.

Scope:
- [x] **Pure core `src/inrix_tools/store.py`**: `connect(db_path)`,
      `ingest_export(...)` (writes the typed tz-aware frame + metadata, idempotent
      — re-ingest replaces/updates), `ingest_geometry(...)` / `ingest_aadt(...)`
      (persist the Item 8 geometry and the Item 18 AADT join keyed by dataset),
      `list_datasets()`, and `load_dataset(...)` returning the **same typed frames
      the file loaders produce** (`io.load_data` parity). No hardcoded paths.
- [x] **Cache the processed GIS join** (segment → geometry, segment → AADT with
      `aadt_source`/match distance) keyed by dataset so Item 18's spatial join
      runs **once at ingest**, not per load.
- [x] **GUI**: a low-visibility (not top-of-page) **"Ingest export"** intake
      button; the main data control becomes a **select-from-ingested-datasets**
      dropdown reading the DB, replacing/augmenting the path box. Running straight
      from a file path still works unchanged (DB optional).
- [x] Schema + versioning/migration note in **DATA_FORMAT.md**.
- [x] pytest: ingest → list → load round-trip returns frames **equal to the direct
      file loaders**; the GIS-join cache hit avoids recompute (monkeypatched
      counter); the DB path is a param (no hardcoded path escapes); a self-skipping
      real-export ingest test. DESIGN_HISTORY entry.
- [x] **Split point if the session runs long:** landed the pure-core `store.py` +
      ingest/round-trip *and* the GUI intake/select rewiring in one session.

</details>

---

## 23 — (done 2026-07-17 — area-based store: merge by corridor set + bin partition) — see Completed index + DESIGN_HISTORY Session 27

Owner follow-on to Item 21, changing the store's **data model** from one silo per
export to a persistent **area**. Owner decisions (recorded Session 27): area identity
= **corridor set** (same `Corridor/Region Name` set → same area, exports merge);
differing bin-lengths = **partition + selector** (auto-detected `bin_minutes`);
overlap = **keep-first** (a later export never overwrites a stored value).

Delivered:
- [x] `store.py` rewritten to the area model (`SCHEMA_VERSION=2`): `area_identity`
      (corridor-set key, Segment-ID fallback), `detect_bin_minutes` (modal spacing),
      `ingest_export`/`put_export` merging **keep-first** on
      `(Segment ID, Date Time, bin_minutes)` via an anti-join `INSERT … BY NAME`
      (column-drift tolerant), `list_areas`/`area_bins`/`area_names`,
      `load_export`/`load_metadata`/`load_geometry`/`load_dataset(area, bin)`,
      `remove_area`; `_areas` + `_ingests` registries; metadata & geometry+AADT merged
      keep-first per `Segment ID` and persisting with the area.
- [x] GUI: the "Saved datasets" dropdown replaced by an **Area** dropdown + a
      **Bin length** dropdown; ingest merges into the auto-derived area and selects
      it (which populates bins and loads); file-path loading unchanged.
- [x] DATA_FORMAT "Database store" section rewritten (area model, corridor-set rule,
      keep-first merge, bin partition, v2 + re-ingest migration note).
- [x] pytest: bin detection; corridor-set grouping (same set merges, subset/other =
      new area); keep-first overlap dedup + idempotent re-ingest; two bin-lengths
      coexist + selectable; geometry/metadata union; single-export load parity; GUI
      area+bin round-trip with the join cache-hit; self-skipping real-export. **265
      passing**; real Myrtle export verified end-to-end (area by corridor set, +2.19M
      rows, re-ingest +0, DB load 3.3 s vs 12.8 s file).

**Migration note:** an existing Item-21 (`v1`) `inrix_store.duckdb` isn't read by the
`v2` code (its old tables are ignored) — delete the file and re-ingest, or just
re-ingest into the fresh area tables.

---

# Review batch — findings from the 2026-07-18 whole-app review (Items 24–27)

Scoped from [REVIEW_APP_2026-07-18.md](REVIEW_APP_2026-07-18.md) (Fable,
review-only; owner accepted 2026-07-18 — DESIGN_HISTORY Session 28). Finding IDs
(R1–R12) below refer to that document. File order is priority order: 24 first
(the owner's dropped Item 21 request), then 25 and 27 (both land cleanest after
24), 26 any time. Nothing here is math-heavy, so no Fable implementation item —
per the owner's rule, Fable stays the review/verification tier.

---

## 24 — (done 2026-07-18 — DB-first intake: invert the data controls) — see DESIGN_HISTORY Session 29

**Target: Opus** (GUI/layout work, no new compute). Independent.

The Item 21 scope line that didn't land — "the main data control becomes a
select-from-ingested-datasets dropdown" — plus the display nits that live in the
same `_controls()`/`_load` region (review R1/R2/R4/R7). Scope:

- [x] **Area + Bin become the top-of-panel data control** (normal-weight
      labels); auto-select the most-recent area (and its first bin) at startup
      so the app opens loaded from the DB (R1, R2).
- [x] **Demote the file loader** — path/tz/CValue/Names/AADT/Restrict-dates +
      the Load button move into a collapsed `dbc.Accordion` section ("Load /
      ingest an export file"); Load button drops `color="primary"` (R1).
- [x] `app.layout = _layout` (callable) so area options refresh per page load
      instead of freezing at import (R2).
- [x] **Provenance in the load status** (area name + bin vs. file name) and
      mutually-exclusive intake state — a file load clears the Area selection
      and vice versa (R4).
- [x] Compass checklist initialises to all present groups (R7). *(R8 — the
      save-names path wiring — is superseded by Item 27's names-in-DB; don't
      patch the CSV loop.)*
- [x] pytest: startup auto-select wiring, provenance strings, state clearing;
      headless layout test keeps passing DB-free. DESIGN_HISTORY entry.

Delivered: DB-first `_controls()` (Area + Bin lead; file loader in a collapsed
`dbc.Accordion`, Load button `color="secondary"`), `_default_area`/`_area_label`/
`_load_provenance` helpers, `app.layout = _layout` (callable), the `_area_bins`
callback firing on initial render (startup auto-load cascade), a file load clearing
the Area selection + file-vs-area provenance in the load status, and the compass
checklist initialising to all present groups. **271 tests pass**; the real Myrtle
export verified end-to-end in the browser (ingest → area auto-select → DB load;
page reload auto-loads from the store; file load clears the area + shows file
provenance). Full scope kept above per the box-checking convention.

*Suggested prompt:* "Do Item 24 of ROADMAP.md — make the DB the primary intake
in gui/app.py per REVIEW_APP_2026-07-18.md R1/R2/R4/R7."

---

## 25 — (done 2026-07-18 — date push-down: Restrict-dates on DB loads) — see DESIGN_HISTORY Session 30

**Target: Opus.** Core (`store.py`) + a small GUI wire-up; lands cleanest
*after* Item 24 (same controls column), but no hard dependency.

Today a DB load hard-codes the date restriction off and the only other trigger
loads from the file path — so an area (which accumulates indefinitely under the
Item 23 model) can only ever be loaded whole (review R3). Scope:

- [x] `store.load_export` / `load_dataset` grow `date_start`/`date_end`
      (inclusive local-calendar semantics **identical** to
      `timebins.filter_date_range` — the tz conversion happens after load, so
      push down UTC bounds derived from the local dates, or filter
      conservatively in SQL and re-trim in pandas; decide + record).
- [x] GUI: the Restrict-dates picker applies to DB loads; picker bounds come
      from the area registry's `date_min`/`date_max` (R3).
- [x] pytest: DB-restricted load == file-load + `filter_date_range` on the same
      span (parity); empty-range degrade. DESIGN_HISTORY entry; DATA_FORMAT
      note if the bound semantics need stating.

Delivered: **exact UTC push-down** (decision recorded) — `store.load_export` /
`load_dataset` take `date_start`/`date_end` (local calendar dates) + `tz` and compile
the half-open UTC instants `[local_midnight(start), local_midnight(end)+1 day)` into the
SQL `WHERE`, tz-invariantly **identical** to `timebins.filter_date_range` after
`io.to_local` (DST included, no pandas re-trim); new `store.area_local_span` sources the
picker's widen-again bounds from the `_areas` registry. GUI: the DB `load_dataset`
branch pushes the range down (file branch unchanged); the **Restrict-dates picker moved
to the top-level data control** (out of the file-loader accordion — it governs the DB
path now) and became an `Input` to `_load`, so editing dates re-scans the current area;
an area switch drops the stale restriction. **279 tests pass** (+9: store push-down
parity / open-sided / empty-degrade / `area_local_span` / `load_dataset` threading; GUI
placement + `Input` wiring + end-to-end DB restrict). Full scope kept above per the
box-checking convention.

*Suggested prompt:* "Do Item 25 of ROADMAP.md — add date push-down to
inrix_tools.store and wire Restrict-dates to DB loads, per
REVIEW_APP_2026-07-18.md R3."

---

## 26 — (done 2026-07-18 — store & app hardening) — see DESIGN_HISTORY Session 31

**Target: Sonnet-eligible** (mechanical, each fix is small and testable; Opus
if run in the same sitting as 24/25). Independent.

- [x] Per-callback `con.cursor()` for the shared DuckDB connection in
      `gui/app.py` — `_db()` now returns a fresh UTC-pinned cursor per call
      (a cursor doesn't inherit the parent's session TimeZone) so threaded
      callbacks don't share one connection (R5).
- [x] `_merge_frame` drops NULL-key rows (`dropna(subset=present_keys)`) so
      keep-first holds; test: re-ingest a frame with a NULL `Segment ID` row →
      no dupes (R6).
- [x] `put_export` docstring corrected — the GUI re-reads the raw export on
      purpose; the in-memory frames are filtered/derived (R9).
- [x] pytest for each (+2, 281 total); DESIGN_HISTORY entry.

Delivered all three review nits: `_db()` hands out per-call `con.cursor()`s
(re-pinning `TimeZone='UTC'`, verified cursors don't inherit it), `_merge_frame`
filters NULL-key rows before the keep-first anti-join (they can't dedup and were
re-inserting every ingest), and the `put_export` docstring now records that the
GUI's raw re-read is intentional. **281 tests pass.**

*Suggested prompt:* "Do Item 26 of ROADMAP.md — the hardening batch from
REVIEW_APP_2026-07-18.md R5/R6/R9."

---

## 27 — (done 2026-07-18 — corridor-scoped segment table + names in DB) — see DESIGN_HISTORY Session 32

**Target: Opus** (GUI + a small store schema addition; no new math). Lands
cleanest **after Item 24** (same controls region, and it assumes the DB is the
primary intake); supersedes Item 24's original R8 bullet.

Owner direction (2026-07-18): the table shows **one corridor**; membership is
an **Include/Exclude column** (all included by default, deselect to exclude);
segment names **save to the DB**, retiring the CSV workflow. Also fixes the
owner-reported disappearing-rows bug at the root — the table's positional
`selected_rows`/`active_cell.row` indices corrupt under a native sort because
rows carry no `id` key (review R10, code-verified).

**Owner decisions (recorded Session 32):** include/exclude sets stay
**session-state only**; the `_names` table is keyed **globally by Segment ID**
(INRIX ids are globally unique); Network/Segment scope list **all** segments,
Corridor scope lists that corridor's segments. Scope:

- [x] **Fix the selection identity bug (R10)**: every table row carries
      ``id = Segment ID``; lookups go through ``active_cell["row_id"]`` / row
      data, never positional indices. Dropped ``row_selectable`` and
      ``fixed_rows`` (plain scroll container avoids the misrender-on-replace);
      the map→row highlight is a ``{Segment ID}`` ``filter_query`` style, not a
      positional ``active_cell`` write, so it survives a native sort.
- [x] **Corridor-scoped rows (R11)**: ``_build_table`` lists the picked
      corridor's segments (all/Network + Segment scope list everything). One
      editable **Include/Exclude** dropdown-presentation column, default
      all-Include; ``_exclusions_changed`` derives the member set (corridor
      minus exclusions) **without rewriting ``data``**, feeding the existing
      ``members=`` path; the map dims only *excluded* segments.
- [x] **Names in the store (R12)**: a global ``_names`` table
      (``Segment ID`` PK → name, **last-write-wins** upsert; blank clears the
      override). ``load_dataset`` applies stored names over the seed on every
      load; Save in the table upserts to the store.
- [x] **Retire the CSV workflow**: removed the ``Names CSV`` input, ``Write
      name template`` button, and the save-path status dance;
      ``names.py``'s ``apply_names`` seed logic stayed (now source-agnostic);
      the CSV writers moved to ``legacy/names_csv.py``.
- [x] **Decided + recorded:** include/exclude sets stay **session-state only**
      (owner call — not persisted per area+corridor).
- [x] pytest: id-keyed selection survives a sorted view, exclusion set →
      member mapping, names upsert round-trip (edit → save → reload applies;
      blank clears), CSV controls gone from the layout, stored names apply on a
      DB load. DESIGN_HISTORY entry; DATA_FORMAT gained the ``_names`` table.

Delivered: global `_names` table (LWW upsert, blank-clears, read-only-safe load) +
`save_names`/`load_names`; DB-first name application on every load (`_stored_names`);
the CSV round-trip retired to `legacy/names_csv.py`; a corridor-scoped, id-keyed table
(`_build_table` sole owner of the data) with an editable Include/Exclude column
(`_exclusions_changed` → member set + `corridor-excluded` map dimming, no per-toggle
`data` rewrite); the R10 highlight rewired to a Segment-ID `filter_query`;
`row_selectable`/`fixed_rows` dropped. **286 tests pass**; verified live on the real
Myrtle store (auto-load with the additive `_names` table; Include-dropdown table;
Corridor scope re-scopes to the 11-segment corridor). Full scope kept above per the
box-checking convention.

*Suggested prompt:* "Do Item 27 of ROADMAP.md — corridor-scoped segment table
with an Include/Exclude column and names persisted in the DuckDB store, per
REVIEW_APP_2026-07-18.md R10–R12."

---

## 28 — Corridor chain assembly in the core (trim, verify, account)

**Target: Opus.** Pure core (`src/inrix_tools/corridors.py`), no GUI. Independent,
but **Items 29 and 30 depend on it**. Supersedes the *Automatic corridor assembly*
bullet in Future (same `NextXDSegI` walk, now with the trim/verify half the
Gemini pass was missing — G7/G8, DESIGN_HISTORY Session 33).

The 2026-09-17 comparison assembled corridors by snapping each endpoint
coordinate to the nearest XD segment and walking `NextXDSegI` until it hit the
target. The walk itself is right — every chain terminated at its intended end
segment and every query point sat within ~90 ft of its chain. What's missing is
everything after the walk: whole end segments are included even when the query
point falls mid-segment, so the VSL chain is **3.635 mi against a ~3.00 mi
request (+21%)** and Franklin WB is **3.453 mi where Franklin EB is 2.993 mi for
the same physical extent** (0.78 mi of overshoot on one end). Segments absent
from an export vanish silently into the sum. Scope:

- [x] **`build_chain(shapefile_or_cache, start_latlon, end_latlon)`** — snap both
      endpoints, walk `NextXDSegI`, return a typed result carrying the ordered
      segment ids, per-segment miles, the **snap distance at each end**, and an
      explicit `reached_target` flag. Snap in a **projected CRS**, not raw
      lat/lon degrees (the Gemini pass measured distance in degrees, which is
      ~28% anisotropic at this latitude — harmless at 90 ft, not in general).
- [x] **Endpoint trim**: report `trim_start_miles` / `trim_end_miles` (the
      fraction of the first/last segment that lies outside the requested
      extent) and a `chain_miles` vs `requested_miles` ratio. Decide + record
      whether to *trim* (prorate the end segments' travel time by the covered
      fraction) or *report only* — proration is the honest default for
      comparison work, but it assumes uniform speed within a segment, so state
      the assumption either way.
- [x] **Missing-segment accounting**: given an export/area, return which chain
      members have no observations, their mileage, and the covered fraction.
      **No silent fallback** — the caller decides, and the frame carries the
      coverage so a report can't lose it (this is what let Eagle Rd NB compare
      17 of 20 segments with only a `17/20` string as evidence).
- [x] pytest: a synthetic connectivity fixture (chain walk, dead end, target
      unreachable → `reached_target=False`); trim arithmetic against a
      hand-computed case; missing-segment coverage; degrees-vs-projected snap
      on a case where they disagree. DESIGN_HISTORY entry; DATA_FORMAT note on
      the trim/proration decision.

Delivered: `src/inrix_tools/corridors.py` — `build_chain` (UTM-projected snap via
`project_network`, k-candidate endpoint choice resolved by connectivity with a
`max_snap_feet=500` guard, `NextXDSegI` walk) returning a frozen `ChainResult` with
ordered ids, per-segment miles, snap distances **in feet**, `reached_target` +
`stop_reason` (`target`/`dead_end`/`off_network`/`cycle`/`max_steps`), both endpoint
trims and the `chain_miles` ÷ `requested_miles` ratio; `chain_coverage` (per-member
`n_obs`/`observed`, `attrs` `n_missing`/`missing_miles`/`miles_covered_fraction`,
value-aware); `chain_travel_time` (complete-set rule over exactly the chain's members,
end segments prorated by covered fraction). **Decision: prorate by default for
comparison work, always report the trim** — measured on the real shapefile, Franklin
WB 3.453 mi vs EB 2.993 mi over the same extent trim to 2.764 / 2.763 mi, and VSL is
3.635 mi against a 3.006 mi request; the uniform-speed-within-segment assumption is
stated in the docstring and DATA_FORMAT.md. **305 tests pass** (+19), including
real-shapefile regressions for both G7 cases.

*Suggested prompt:* "Do Item 28 of ROADMAP.md — pure-core corridor chain
assembly with endpoint trim and missing-segment accounting, per DESIGN_HISTORY
Session 33 (G7/G8)."

---

## 29 — External travel-time reference: load, gate, and compare

**Target: Opus** (the loader + consistency gates dominate; the agreement
statistics are a clean **Fable-eligible** split if the session runs long).
Pure core (`src/inrix_tools/reference.py` + `agreement.py`). **Depends on Item
28** for the corridor chains.

Stand up the INRIX-vs-external-reference comparison properly in the core. The
2026-09-17 pass produced the right shape of answer but lived entirely outside
the repo — the analysis script was never committed and had to be recovered from
Gemini's session store, so none of it is reproducible or tested (G10). Scope:

- [x] **`reference.load_tt_logger(path)`** — read a TT Logger-shaped workbook
      (per-sheet header block: Origin/Destination/Days/Start/End, then
      Timestamp / Travel Time (s) / Travel Time (min) / Extra TT (min)) into a
      tidy tz-aware frame. Parse Origin/Destination as **either** a lat/lon pair
      **or** a place name, and mark which — three sheets use city names
      ("Garden Valley, ID" → "Horseshoe Bend, ID"), so their route is a
      geocoded guess and cannot be chain-matched with any confidence.
- [x] **Bin alignment** — `floor` to the bin, not `round`. The reference samples
      land ~50 s past each quarter hour so the two agree today, but that's
      luck; `floor` is what INRIX's bin-start labelling means. Keep the
      **timestamps tz-aware end to end** (the recovered script sliced
      `Date Time[:19]`; that happens to be correct because the export carries a
      real DST switch, but the tz should be explicit, not implied — see G9).
- [x] **Reference-side consistency gates**, reported as data, not exceptions:
      (a) **sub-route ≤ full route** where one sheet's extent nests inside
      another's — Google's "Franklin WB - No Mid" exceeds its own full
      "Franklin WB" in **99.9% of 2,760 shared bins**, which is physically
      impossible and was reported as a 32.2% INRIX error (G3); (b) **per-sheet
      date coverage**, so a sheet that stops early can't hide inside a study-wide
      banner — "HSB-Cascade" is **16 days in January** (Jan 2–26) beside
      2,936-bin corridors (G4); (c) a **lag scan** (±90 min) confirming the two
      clocks agree, since the sheet headers' stated logging windows are an hour
      off from the observed timestamps.
- [x] **`agreement.compare(inrix_frame, reference_frame)`** → one tidy frame per
      corridor with: bias + **confidence interval** (autocorrelation-adjusted —
      15-min bins are not independent), MAE / RMSE / MAPE, **SD ratio**, **delay
      ratio** (mean delay above each source's own free-flow percentile), and
      Bland-Altman limits of agreement. Correlation is *reported* but must not
      be the headline: Eagle Rd SB carries r = 0.945 alongside a **5.49-minute**
      systematic bias (G1/G6).
- [x] **Independent-n accounting** — the frame carries days covered and distinct
      pavement, so a report can't sum overlapping sub-routes of the same road
      into one impressive total (the Gemini report's "28,340 matched
      observations" includes four Franklin sheets over the same ~3 miles — G5).
- [x] **Source data hygiene**: `TT Logger.xlsx` is currently untracked **and**
      not gitignored in the repo root. Decide its home (fixture vs. gitignored
      input, following the raw-export rule) and record it.
- [x] pytest: workbook fixture (both header dialects) → tidy frame; floor-vs-
      round on a cadence where they differ; each gate firing on a constructed
      violation and staying quiet on a clean case; agreement statistics against
      hand-computed values; CI widening under injected autocorrelation.
      DESIGN_HISTORY entry; DATA_FORMAT section on the reference format.

Delivered: `src/inrix_tools/reference.py` — `load_tt_logger` (both header dialects:
`lat,lon` **or** place name, and clock cells as time / `"HH:MM"` / **Excel day
fraction**; place-name routes marked `chain_matchable=False` and refused by
`route_endpoints`), explicit tz localization that drops **and counts** the 36 samples
in the 2025-11-02 DST fold, `align_to_bins` (**floor**, raw instant kept as
`Sample Time`), and the three gates: `nesting_gate` (chain-derived pairs;
"Franklin WB - No Mid" exceeds its own full route in **99.93% of 4,524 bins**, EB pair
clean at 0%), `coverage_gate` (per-route days + window coverage), `lag_scan` /
`lag_summary` (scanned on **bias-removed** `sd_diff` — raw RMSE put 5 of 10 real
corridors at a false +30/+60 lag; on `sd_diff` every route minimises at **lag 0**).
`src/inrix_tools/agreement.py` — `match_bins`, `compare` (bias + **day-blocked** CI with
the per-bin CI beside it as `*_naive` and a `ci_width_ratio`, MAE/RMSE/MAPE, SD ratio,
delay ratio on each source's own free-flow, Bland-Altman LoA, `r` reported last) and
`independent_totals` (distinct days + **distinct pavement**: 28,431 matched bins are
4,411 distinct quarter hours; 43.84 summed chain-miles are 33.63 distinct). End-to-end
on the real 2026 D3 export it corroborates G1 independently (Eagle Rd SB bias −5.46 min,
NB −8.38 with CI [−8.47, −8.23], delay ratios 0.37–0.67 on the signalised arterials).
**Decision: `TT Logger.xlsx` is gitignored raw input**, with a synthetic workbook
fixture in tests. Item 28's `k_candidates` default went 4 → 8 (at 4, two of ten real
routes returned a wrong chain). **337 tests pass** (+32).

*Suggested prompt:* "Do Item 29 of ROADMAP.md — pure-core external travel-time
reference loader, consistency gates, and agreement statistics, per
DESIGN_HISTORY Session 33 (G1/G3/G4/G5/G6)."

---

## 30 — Validation report as a thin shell + record the arterial-delay finding

**Target: Opus.** Report/figure layer over Item 29's frames + a DATA_FORMAT
pass. **Depends on Item 29.**

The 2026-09-17 HTML report is the one artifact that survived into the repo, and
it is the `process_and_plot_*` fusion this project exists to undo: it computes
MAE, bias, and `np.polyfit` slopes inline while building figures
(`scripts/generate_corridor_html_reports.py:478-521`) over a hardcoded
`BASE_DIR = "/home/hansrkid/Inrix"` (G10). Its conclusion is also wrong in a way
worth fixing carefully rather than quietly dropping. Scope:

- [x] **Rebuild over the compute core** — figures consume Item 29's frames and
      compute **nothing**. Retire `scripts/generate_corridor_html_reports.py`
      to `legacy/` per the CLAUDE.md convention rather than editing it in place.
- [x] **Honest framing on every page**: per-corridor date coverage and days (not
      one study-wide banner — 13 of 15 sheets end in late June, one covers 16
      days), independent-n rather than summed overlapping sheets, and
      bias-with-CI as the headline metric with correlation demoted to a column.
- [x] **Drop or footnote the corridors Item 29's gates fail** — "Franklin WB -
      No Mid" (reference data internally impossible) and "HSB-Cascade"
      (January-only) should not sit unmarked in a scorecard.
- [x] **Fix the labels**: there is no SH-17 (Garden Valley is Banks-Lowman Hwy +
      ID-55, and the same report names it correctly two sections later);
      Franklin is entirely within Nampa, not "Nampa to Caldwell"; the reference
      samples about twice an hour, so "Data Resolution: 15-Minute Intervals"
      describes the INRIX side only.
- [x] **Record the actual finding in DATA_FORMAT.md.** On every signalised
      arterial measured, INRIX reports **roughly half** the delay the external
      reference does — delay ratio 0.52–0.69 and SD ratio 0.54–0.69 on Eagle Rd,
      SH-69, Franklin, and the VSL section — while agreeing to within about a
      minute on rural free-flow travel time (Cascade-HSB bias −0.15 min over
      50.8 mi). Two consequences to state: a before/after study scored on INRIX
      will report **about half the effect size in minutes** that the reference
      would (relative change may survive; that is a different claim and needs
      its own evidence), and the report's stated explanation for the Eagle Rd /
      SH-69 SB biases — Google capturing I-84 off-ramp queues — **is not
      supported**: the chain starts 72 ft and 90 ft from the query points, and
      at 05:00 the SH-69 **SB** gap is already 3.85 min while **NB** is 0.29 min
      off over the same endpoints reversed, which no queuing story explains
      (G1/G2).
- [x] pytest for any new figure-building helper (shape/labels only — no
      statistics live here). DESIGN_HISTORY entry; DATA_FORMAT section as above.

Delivered: the report is three layers — the core owns every statistic,
`gui/validation_figures.py` + `gui/validation_report.py` place values, and
`scripts/build_validation_report.py` is argparse wiring (no hardcoded paths). The old
generator is `legacy/generate_corridor_html_reports.py`, unchanged but for a
retirement note. The no-compute rule is **enforced by tests**: builders handed a
summary row that contradicts its own data must show the row's number, and the 1:1
scatter draws no fitted line. Four core additions, because a figure may not compute —
`agreement.profile` (time-of-day / day-of-week / date means), `coverage_gate`'s
measured cadence (`obs_per_day`, `median_sample_minutes`), `corridors.chain_attributes`
/ `chain_description` (labels read off the XD segments, so
`label_disagreements` can fail a caption the network denies — "SH-17" over segments
carrying 55), and `corridors.chain_between_segments`, which carries the three
place-name sheets from operator-stated terminal segments with everything a query point
would have provided explicitly absent (`snap_*_feet` NaN, nothing prorated). **Measured
on the 2026 D3 export** (38,873 bins, 15 routes): arterial delay ratio **median 0.59**
(0.37–0.95), SD ratio median 0.62 — and the rural contrast, Cascade↔HSB bias −0.16 /
+0.48 min over 50.8 mi with both CIs spanning zero. G2 contradicted with snap distances
(5/4 ft, 4/5 ft, 18/2 ft, 64/18 ft) and the 05:00 SB/NB split (−3.94 vs −0.37 min). Two
routes excluded from the headline and kept visible with their reasons: "Franklin WB -
No Mid" (impossible reference data) and "Garden Valley-HSB" (**chain dead-ends at
11.08 mi** — a chain-side exclusion the original review had no way to catch). **377
tests pass (+40).**

*Suggested prompt (done):* "Do Item 30 of ROADMAP.md — rebuild the INRIX-vs-reference
validation report as a thin shell over the compute core and record the
arterial delay-compression finding in DATA_FORMAT.md, per DESIGN_HISTORY
Session 33 (G1/G2/G10)."

---

# Follow-on batch — second review of the validation work (Items 31–33, scoped 2026-09-17)

A re-review of the Session 33 material after Items 28–30 landed, run against the raw
export rather than against the previous review's notes (DESIGN_HISTORY Session 37).
**The arterial delay-compression finding survived every attempt to explain it away** —
historical backfill, missing member segments, and clock lag were each tested and each
ruled out. What the re-review found instead is that the finding is *sharper* than
"roughly half" (it is a slope, not an offset), that the statistic currently reporting it
is the least stable one available, and that the complete-set guarantee Item 28's
docstring advertises is not the one the code provides. Items are session-sized per
CLAUDE.md; **32 depends on 31** (the numbers move once the sum is fixed) and **33
depends on 32** (it needs level and slope reported separately).

---

## 31 — Chain membership accounting: requested vs observed, and a CValue gate ✅ (Session 43)

**Target: Opus.** Pure-core correctness in `speed.py` / `corridors.py` / the reference
path. No GUI. Blocks Item 32.

Item 28 set out to kill the outside pass's `eval_df = complete if len(complete) > 0 else
merged` fallback (G8). It killed that *expression*, but a corridor sum can still shorten
silently, by a different route — and the module's own docstring promises otherwise.
Scope:

- [x] **Derive the complete-set size from the requested membership, not the observed
      data.** `corridors.chain_travel_time` passes `expected="total"`, which
      `io.mark_complete_timestamps` resolves to `groupby(corridor_col)[SEGMENT_COL]
      .transform("nunique")` (`speed.py:227`) — *every segment ever seen in the frame*.
      A member with **zero** rows never enters that count, so it cannot fail it.
      Eagle Rd NB is missing 3 of its 20 members from the export entirely and therefore
      runs with `expected_segments = 17`: all 2,633 bins in the shipped
      `out/validation_report/tables/summary.csv` are marked complete while summing 17/20
      of the route. The docstring at `corridors.py:504-508` states the opposite
      ("a missing member drops the timestamp rather than silently shortening the sum");
      make that true, by taking the expected count from `len(chain.segment_ids)` rather
      than from the data. Decide explicitly what the right behaviour *is* when a member
      is *never* present — dropping every timestamp is correct-but-useless, so the likely answer
      is a third state (report the sum, mark it short, carry the missing miles) rather
      than either silent completion or total collapse.
      **Done:** `chain_travel_time` always passes `members=chain.segment_ids`, and
      `speed.corridor_travel_time` takes the complete-set size from that request. The
      third state is what was chosen: every row carries `n_requested` / `n_absent` /
      `short` beside `n_segments`, so a never-present member is reported *and labelled*
      rather than collapsing the series (`on_absent="drop"` takes the strict branch
      explicitly). Absence is value-aware. Without `members=` the old behaviour is
      unchanged.
- [x] **Stop prorating end segments that carry no data.** `chain_travel_time` applies
      `chain.weights()` to the two end segments and reports
      `Length(Miles) = requested_miles`. On Eagle Rd NB the chain's **first and last
      segments are both missing** (`1187377510`, `474858971`), and Franklin EB's start
      is missing (`119739402`) — so the trim prorates segments contributing nothing
      while the reported length still credits their pavement. Missing mileage is
      0.290 mi of a 6.938 mi request on Eagle Rd NB (4.2%) and 0.250 mi of 2.763 mi on
      Franklin EB (9.0%); every derived `Corridor Speed(miles/hour)` on those routes is
      overstated by that fraction. Either suppress the length/speed columns when a
      trimmed end is unobserved, or report the **observed** extent and label it.
      **Done (observed + labelled):** `Length(Miles)` is now the extent actually summed,
      with `requested_length_miles`, `missing_length_miles` and `length_basis`
      (`"requested"` / `"observed"`) beside it. Measured on the export, 6 of 15 chains
      are short. **The figures above are whole-segment miles**; a prorated sum is only
      credited *in-extent* miles, so the real overstatement is about half: Eagle Rd NB
      0.149 mi of 6.938 (2.1%), Franklin EB 0.128 of 2.763 (4.6%), the No-Mid extents
      6.1%. See DATA_FORMAT's table.
- [x] **Apply a CValue gate in the reference/agreement path, and record the threshold.**
      Nothing in the pipeline gates on CValue today, though DATA_FORMAT prescribes
      `CValue > 80` as the tunable default. 21.7% of the 2026 D3 export has a **null**
      CValue and 88.3% of those are historical backfill (`Speed == Hist Av Speed`
      exactly). It changes no conclusion in the 2026-09-17 comparison (re-running on
      bins with <5% imputed members moved every bias by ≤0.3 min) — which is the
      argument for making it an explicit, recorded, *passing* gate rather than an
      absent one. The imputation share is strongly route- and hour-dependent (rural
      Cascade–HSB is 57% imputed at 05:00), so carry the measured share per route as a
      coverage column beside `obs_per_day`, not just a pass/fail.
      **Done:** `chain_travel_time(..., cvalue_threshold=)` (opt-in in the core,
      `CValue > 80` by default in `build_validation_report.py`, recorded on `attrs` and
      on the summary — the convention Item 35 set). `io.mark_imputed` measures the share
      whether or not the gate runs; the per-bin `imputed_fraction` /
      `cvalue_kept_fraction` reach `compare` through `match_bins(carry=...)` and the
      scorecard. **Measured:** the gate never alters a retained bin's value (0.0000 min,
      max 0.0000) — it only selects bins, and route-dependently: 8.6% on VSL to **77.4%
      on Garden Valley–HSB**.
- [x] pytest for each: a chain whose member never appears in the frame (expected count
      holds at the requested size); proration with an unobserved end segment; the gate
      with and without nulls, asserting the recorded threshold travels with the result.
      DESIGN_HISTORY entry; re-run `scripts/build_validation_report.py` and note which
      numbers moved.
      **Done, with one carve-out:** +18 tests (**full suite: 458 passed, 2 skipped**),
      DESIGN_HISTORY Session 43, DATA_FORMAT updated in three places. The **end-to-end
      report re-run is outstanding** — it needs `TT Logger.xlsx`, which is gitignored raw
      input (Session 35) and is not on this machine. The INRIX side was re-run in full
      instead: every published chain rebuilt from `tables/chains.json` and put through
      the new code against the real 5.46 M-row export, which is where all the numbers
      above come from. Regenerate the report when the workbook is back
      (`--cvalue-threshold` / `--no-cvalue-gate` are wired).

*Suggested prompt (done):* "Do Item 31 of ROADMAP.md — make the complete-set rule count the
chain's requested membership rather than its observed membership, stop prorating
unobserved end segments, and add a recorded CValue gate to the reference path, per
DESIGN_HISTORY Session 37."

---

## 32 — Report the delay compression as a slope, not a ratio of means

**Target: Opus; the estimator itself is Fable-eligible** (math-heavy, per the CLAUDE.md
rule of thumb). `agreement.py` + the report's scorecard + a DATA_FORMAT pass.
**Depends on Item 31.**

Item 30 records the finding as a **delay ratio** — `mean(delay_inrix) / mean(delay_ref)`,
each above its own 10th percentile — reported as "median 0.59 (0.37–0.95)". That is a
ratio of two small means, it is the only statistic in the report carrying no uncertainty
interval (against the CLAUDE.md standard the same report meets everywhere else), and it
is visibly unstable: **VSL SB PM reports a delay ratio of 2.32 against a regression
slope of 0.71** on the same bins. Scope:

- [ ] **Add a per-route regression of INRIX delay on reference delay** (each above its
      own free-flow percentile) returning **slope, intercept, and a day-blocked CI** on
      both, alongside the existing bias CI. On the 2026 D3 export the arterial slopes
      cluster tightly — median **0.534**, with Eagle Rd NB 0.517 / intercept 0.037,
      Eagle Rd SB 0.514 / 0.080, Franklin EB 0.465 / 0.066, SH-69 NB 0.566 / 0.085 —
      which is a far better-conditioned estimate than the ratio of means. Keep
      `delay_ratio` as a secondary column; don't silently swap the definition under a
      name Item 30's numbers were published under.
- [ ] **Separate the two effects the `bias` column currently fuses.** The arterials
      carry a **level gap at free flow** *stacked on top of* the slope: Eagle Rd NB's
      10th-percentile gap is −5.36 min and SH-69 SB's is −4.69 min, while SH-69 NB over
      the same endpoints reversed is −0.82 min. A single `bias` cannot distinguish "the
      two sources are measuring different pavement" from "INRIX compresses delay", and
      those have opposite implications for a before/after study. Report the free-flow
      level gap and the slope as separate columns and lead the scorecard with both.
- [ ] **Restate the finding in DATA_FORMAT.md as a slope, with the before/after
      consequence made explicit.** The near-zero intercepts are the point: the
      disagreement is **multiplicative in delay**, not a fixed offset, so it does **not**
      cancel in a before/after difference. An intervention that removes 4 real minutes
      of delay scores as roughly 2.1 minutes on INRIX. Item 30's DATA_FORMAT section
      already says "about half the effect size in minutes" — replace the assertion with
      the measured slope and its interval, and keep the rural contrast (Cascade↔HSB,
      both CIs spanning zero) beside it.
- [ ] pytest: synthetic frames with a known slope and intercept recover them; the
      day-blocked CI widens against the naive one as it does for `bias`; a
      near-zero-delay route produces an unstable ratio **and** a stable slope, pinning
      the VSL SB PM case as a regression. DESIGN_HISTORY entry.

*Suggested prompt:* "Do Item 32 of ROADMAP.md — report the INRIX-vs-reference delay
compression as a regression slope with a day-blocked CI, separate the free-flow level
gap from the slope in the scorecard, and restate the DATA_FORMAT finding accordingly,
per DESIGN_HISTORY Session 37."

---

## 33 — The directional free-flow level gap (SH-69 SB, Eagle Rd)

**Target: Opus.** Diagnostic session over `corridors.py` + the geometry layer; may end
in a documented finding rather than a code change. **Depends on Item 32** (it needs the
level gap reported separately from the slope).

The one thing two reviews have failed to explain. At the quietest conditions measured —
each source's own 10th percentile, over the same matched bins — **SH-69 SB runs 4.69 min
slower on the reference than on INRIX, while SH-69 NB over the same endpoints reversed
runs 0.82 min**. Eagle Rd NB shows the same shape at −5.36 min. Session 33's G2 rejected
the published explanation (Google capturing I-84 off-ramp queues) on snap distance, and
Item 30 confirmed it with measured snaps of 4–64 ft — but **a snap distance only proves
the endpoints coincide, not that the two routes traverse the same path between them**,
and that is the hypothesis nobody has actually tested. Scope:

- [ ] **Test path equivalence, not endpoint equivalence.** Compare the assembled chain's
      geometry against what the reference route must have traversed — cumulative
      distance, road names along the chain (`chain_attributes`), and whether a plausible
      alternative path exists between the same two snapped points (a frontage road, a
      one-way pair, a different carriageway). A directional gap this large at free flow
      is much more likely a *route* difference than a *measurement* difference.
- [ ] **Decompose the gap along the chain.** With per-segment INRIX travel time in hand,
      find whether the SB level gap is spread evenly (suggesting a length or extent
      mismatch) or concentrated in one or two members (suggesting a specific
      intersection, ramp terminal, or a segment whose XD extent disagrees with the
      roadway). Do the same for Eagle Rd NB and compare.
- [ ] **Check the reference side for a directional artefact** before blaming geometry:
      whether SB and NB samples are drawn at the same times of day, whether the SB
      route's logged origin/destination pair is actually the reverse of NB's, and
      whether the sheet's own extent matches.
- [ ] **Land it as a finding either way.** If it is a route mismatch, the affected routes
      need re-endpointing and the headline numbers re-derived; if it is real, it is a
      second INRIX limitation distinct from the delay compression and belongs in
      DATA_FORMAT beside it. A negative result is a valid outcome here — record what was
      excluded. DESIGN_HISTORY entry.

*Suggested prompt:* "Do Item 33 of ROADMAP.md — diagnose the directional free-flow level
gap on SH-69 SB and Eagle Rd NB, testing path equivalence rather than endpoint
equivalence, per DESIGN_HISTORY Session 37."

---

## 34 — The AADT join picks ramps over the mainline on divided highways

**Target: Opus.** Pure-core correctness in `aadt.py` + the geometry layer. No GUI.
**Blocks Item 35** (and should land before the Future *directional AADT* work, which
builds on this join).

`join_aadt` matches each XD segment to the **nearest** AADT line that passes a 45°
bearing gate within `max_distance_m=35`. On a divided highway that rule is
systematically wrong: the ITD layer carries **one mainline centerline** — which sits
22–30 m off each carriageway — plus a **ramp record per movement**, and the ramp runs
parallel, so it passes the bearing gate from 3–8 m away and wins. One carriageway
snaps to the mainline, the other picks up ramps, and nothing in the output says so:
both report `aadt_source == "matched"`.

Measured on the 2026 D3 export, I-84 westbound reads
`147500, 18000, 145500, 10500, 135000, 13000, 13500, 122000, 10500…` segment to
segment while eastbound over the same ground is smooth and monotone
(`114500 → 122000 → 135000 → 145500 → 147500`). Length-weighted mean AADT is
**61,410 WB against 114,981 EB**. The layer *does* carry the discriminator — for
segment `1187323545` the picked record is `08627AIN084 / "WB OFF EAGLE RD IC #46"`
(18,000) at 4.1 m and the correct one is `01010AIN084 / "EAGLE RD IC #46"` (147,500)
at 25.9 m — but `load_aadt` never reads `Descriptio` and `join_aadt` carries `Route`
through without gating on it. Scope:

- [x] **Read the fields that identify a record.** Add `Descriptio`, `Descript_1` and
      `Segment` to `_KEEP_COLS`. `Route` is **null throughout** the Idaho layer, so the
      existing `Route` column is a dead diagnostic — the usable route identifier is the
      `RouteID` code (`01010AIN084`, `02150ASH069`: a route-segment prefix, a class
      `IN`/`SH`/`US`/`OH`, and a route number). Either populate `Route` from `RouteID`
      or stop advertising it.
- [x] **Classify records rather than dropping them.** Add
      `classify_aadt_records(aadt)` returning a `record_kind` column
      (`mainline` / `ramp` / `connector`) from `Descriptio` (`^(NB|SB|EB|WB)\s+(ON|OFF)\b`,
      `RAMP`, `CONN`, `JCT`) and the `RouteID` band. A blanket pre-filter is the wrong
      shape — a segment that genuinely *is* a ramp still needs its ramp volume — so
      this labels and lets the matcher decide.
- [x] **Replace nearest-wins with a ranked preference** in `join_aadt`: (1) the
      `RouteID` route number matches the XD `RoadNumber`, (2) `record_kind == "mainline"`
      when the segment is mainline (use `FRC` / the XD road name, not a guess), (3) then
      nearest. Raise the default `max_distance_m` to ~60 m — **only safe once ramps stop
      out-competing the mainline**, which is why the two changes are one item and not
      two. Add `prefer_mainline: bool = True` and record the resolved policy on
      `attrs`, per the module's convention.
- [x] **Make a ramp attribution visible.** Extend `aadt_source` beyond
      `matched`/`nearest`/`missing` with `matched_ramp`, so a segment weighted by a ramp
      count is a *finding* downstream rather than an indistinguishable `matched`. Decide
      whether `vehicle_hours_of_delay` should exclude those rows or merely flag them —
      and say which, in the docstring.
- [x] Verification: a ramp-aware rejoin of the I-84 WB carriageway must recover the
      monotone mainline profile and a length-weighted mean of ≈111k (from 61,410),
      bringing it within a few percent of the EB side. Re-derive any shipped
      AADT-weighted number and note which moved. **Done:** WB recovers the monotone
      profile at a length-weighted **123,692** against EB's 124,203 (0.4% apart);
      across all 3,905 D3 segments 482 values move, none regress from `matched` to
      `nearest`, and D3 peak vehicle-hours of delay rise +50.9%. The only shipped
      AADT-weighted pair (Myrtle, Session 19) is unchanged — an undivided couplet.
- [x] pytest (`tests/test_aadt.py`): synthetic mainline + nearer parallel ramp — the
      mainline wins; a segment that *is* the ramp still matches its ramp record; a
      perpendicular cross-street is still rejected; `matched_ramp` is set where a ramp
      is genuinely the best match. Record the divided-highway failure mode in
      DATA_FORMAT.md beside the existing AADT caveat, with the I-84 numbers as the
      worked example. DESIGN_HISTORY entry.

*Suggested prompt:* "Do Item 34 of ROADMAP.md — fix `aadt.join_aadt` so a parallel
on/off ramp can no longer out-compete the mainline centerline on a divided highway, per
DESIGN_HISTORY Session 38."

---

## 35 — District-wide screening: which corridors are worst?

**Target: Opus.** New `screen.py` in the pure core + a streaming ingest path in
`store.py`. No GUI (the display pass is a separate item if the owner wants one).
**Depends on Item 34** — every ranking here is volume-weighted, and the weights are
wrong until that lands.

Everything in this repo is **corridor-scoped**: you name two endpoints, and it analyses
that corridor. There is no way to ask the prior question — *which* corridors in a
district are worst — and that is what the outside work was actually for. Two pieces of
it are worth keeping: a **named peak-window vocabulary**, and a **streaming aggregation**
that never materialises the export in pandas (measured: 34 s per 263 MB part, ~5.4M
rows, against `io.load_data`'s whole-frame read). Scope:

- [x] **`PEAK_WINDOWS` presets** in a new `src/inrix_tools/screen.py` — `am`
      (07:00–09:00), `pm` (16:00–18:30), `midday` (10:00–14:00), `night` (22:00–05:00),
      weekday-gated where that is the intent. Express them through the existing
      `timebins` vocabulary (`parse_time_bin` / `filter_time_window` /
      `filter_day_of_week`) rather than hand-rolled SQL predicates, so a window means
      the same thing here as everywhere else. Presets, not hard-coded constants: the
      caller can pass their own. **Done:** a `PeakWindow` dataclass whose SQL
      predicate is *derived* from `parse_time_bin` / `parse_day_of_week`, with
      `.filter()` giving the same window as the pandas filters — the tests assert the
      two paths agree row for row, on the toy export and on the real Myrtle one.
- [x] **`segment_screen(con, area_key, windows=PEAK_WINDOWS, cvalue_threshold=80)`** —
      one row per segment (`n_obs`, `ref_speed`, per-window mean travel time and speed),
      computed **in DuckDB** against `store.py`'s `obs_<area_key>` table. Gate on CValue
      and record the threshold on `attrs`, per Item 31's rule. The outside run skipped
      the gate entirely; on the D3 export **37.8% of rows carry no CValue**, and 987 of
      3,905 segments lose more than half their AM rows to it (84 lose all of them) — it
      moved no top-corridor delay by more than 0.4%, which is the argument for making it
      an explicit *passing* gate rather than an absent one. Carry the surviving-row
      share per segment so a screened-in segment built from backfill is visible.
      **Done:** the whole 91,054,384-row D3 area screens to 3,905 rows in **11.4 s**.
      The measured gate cost is close to the scoped figure but not identical — **39.4%**
      of rows carry a null CValue and the gate drops **40.1%** of them — and the AM
      accounting reproduces exactly: **990** segments lose more than half their AM rows,
      **84** lose all of them. `kept_fraction` is carried per segment *and per window*.
- [x] **`rank_corridors(screen, chains, aadt)`** — the metric vocabulary the outside
      work got right, computed through the existing primitives rather than inline:
      `delay_min`, `tti`, `delay_per_mile`, `vhd`, `vhd_per_mile`, `worst_peak`, via
      `speed.segment_delay` and `aadt.vehicle_hours_of_delay` so the AADT caveat
      propagates instead of being silently restated as "veh-hrs/day". **Floor delay at
      one level and say which** — the outside run floored corridor delay at the corridor
      level (`max(0, Σtt − Σff)`) while summing per-segment floored delay into VHD, so
      its two headline numbers disagreed about the same corridor. **Done:** floored
      **per segment** (`attrs['delay_floor'] == 'segment'`), so the same floored values
      feed `delay_min` and `vhd` and the two cannot disagree. Only **observed** pavement
      is credited — a member the screen never saw lands in `missing_miles` rather than
      deflating `delay_per_mile` (Item 31's concern, applied at the screening level).
- [x] **`store.ingest_export_streaming(con, zip_path)`** — the zip-member → DuckDB
      `read_csv` path, so a district export lands without a 5.4M-row pandas round-trip.
      Port it off `subprocess`/`unzip -p`/`/dev/fd/N` onto `zipfile.ZipFile.open()` with
      a pipe: the shell form is what the outside script used and it is neither portable
      nor inspectable. Must produce a table identical to `ingest_export`'s — assert that
      in the test, on a fixture small enough to run both ways. **Done, with one thing
      the scope did not anticipate:** DuckDB buffers a statement's appended rows until
      it commits, so a single `CREATE TABLE AS SELECT` over a part grows to the size of
      the CSV and is killed — the member is therefore cut into committed 256 MiB chunks,
      and peak memory tracks the chunk, not the export. The real D3 export (**3 parts,
      91,054,384 rows**) lands in **262 s at 2.5 GB peak RSS**; the 2.18 M-row Myrtle
      export is **byte-identical** through both paths, column types and row order
      included.
- [x] **Declare `duckdb` explicitly** in `pyproject.toml` (and `requirements.txt`).
      `store.py` imports it directly today while relying on it arriving transitively via
      `traffic-anomaly`'s `ibis-framework[duckdb]`.
- [x] pytest (`tests/test_screen.py`): window presets bin the rows a hand-computed
      expectation says they should (the D3 export gives 173 weekdays × 8 bins = 1,384 AM
      observations per segment as a real-data anchor); the CValue gate changes the count
      and records its threshold; ranking metrics against a synthetic corridor with known
      delay; streaming and pandas ingest agree. DESIGN_HISTORY entry. **Done:** 19 new
      `test_screen.py` tests + 6 in `test_store.py`; the real-data anchor is reproduced
      in DESIGN_HISTORY Session 40 (1,384 AM observations for 3,902 of 3,905 segments),
      and self-skipping real-export tests cover both the screen and the ingest.

*Suggested prompt:* "Do Item 35 of ROADMAP.md — add district-wide corridor screening
(`screen.py`) over the DuckDB store, with named peak windows, a recorded CValue gate,
and a streaming ingest path, per DESIGN_HISTORY Session 38."

---

## 36 — The D3 corridor catalogue, as endpoint pairs ✅ (Session 41)

**Target: Opus.** *Sonnet-eligible* once Item 35 lands — the mechanism is then in
place and this is largely transcription plus verification. **Depends on Items 34–35.**

The outside work included a hand-built catalogue of 15 District 3 corridors (I-84,
SH-55, US-20/26, SH-44, SH-69, SH-16, SH-45, I-184, the Front/Myrtle couplet) with
real local knowledge in it — which interchange to which interchange, and why that
extent is the meaningful one. That content is worth keeping. **The mechanism that
resolved it is not.**

It selected by `RoadNumber` + a lat/lon bounding box, attempted a `NextXDSegI` walk, and
fell back to sorting by latitude or longitude whenever the walk covered less than 70% of
the box. The fallback fired routinely — only **84%** of consecutive pairs in the I-84
eastbound result are actual network links, and **70%** in SH-44 westbound. The visible
consequence is that the I-84 "corridor" interleaves the freeway with the Garrity Blvd
frontage road (FRC 3, 20 mph reference speed) and **sums two parallel facilities in
series**: 1.37 mi of frontage road double-counting the same ground. Correcting to
mainline-only moves I-84 EB from 17.01 mi / 17.15 min free-flow / TTI 1.469 to 15.60 mi
/ 14.00 min / **TTI 1.553**, and WB from TTI 2.131 to **2.338**. SH-44 has the same
shape with N Glenwood St — a different street — interleaved through State St. Scope:

- [x] **Transcribe the catalogue to endpoint pairs** in `scripts/d3_corridors.json`,
      beside the existing `scripts/d3_place_name_routes.json`: `{id, name, start_latlon,
      end_latlon, description}` per corridor. Keep the descriptions — they carry the
      *why* of each extent, which is the part that cannot be re-derived. **Done: 20
      entries** (directional pairs are separate entries; SH-44 is split, below). One
      thing the scope assumed that did not hold: the outside pass's catalogue *text* was
      never committed to this repo and is not recoverable from it, so the extents were
      **re-derived against the XD network** — every junction coordinate is a measured
      nearest point between the corridor and its cross street — and the file's `_note`
      records that provenance.
- [x] **Resolve every entry through `corridors.build_chain`.** `load_catalogue` /
      `parse_catalogue` / `resolve_catalogue` in the existing pure-core module — **no new
      walker**. Validation is loud on everything that would otherwise resolve somewhere
      else (a `(lon, lat)` swap, a duplicate id, an empty description, an unknown key
      such as `start_latlong`). `resolve_catalogue` projects once, returns one row per
      entry with the walk, the trims, the snaps and the coverage columns, and carries
      `attrs['chains']` keyed by id — the shape `screen.rank_corridors` takes, asserted
      in a test rather than in prose.
- [x] **Acceptance is per-entry and explicit:** `reached_target` **and**
      `miles_covered_fraction > 0.95`, else the id lands in `attrs['findings']` with its
      `stop_reason`. **13 of 20 accepted, 7 findings**, and the findings are the
      substance: statewide **38.3%** of XD segments carry a null `NextXDSegI` (FRC 1
      0.9% → FRC 5 88.9%) and the gaps are **two-sided** — of 15,997 null-link segments,
      *zero* are named by any other segment as its `PreviousXD`, which is what settled
      that extending the walker would not help. I-184 cannot be walked at all (35% null
      links); SH-44 eastbound forks at Ballantyne Rd onto a **different 1-lane W State
      St** (`XDGroup` 3938033) that dead-ends at Edgewood Ln, so the corridor is **split
      at Eagle Rd** — the two Eagle–Boise halves resolve at 4.11 mi each, the two western
      halves are findings; SH-16 breaks mid-block in different places by direction.
      `front-wb` reaches its target and is still rejected, on **coverage**: the three
      `RoadNumber`-null `W Front St` members were never in the D3 query (73.1% covered).
- [x] pytest (`tests/test_corridors.py`): **19 new tests, 432 pass / 2 skip** — schema
      validation, per-entry accounting and the `attrs` contract, coverage deciding
      acceptance, the **parallel-facility trap** (a synthetic mainline whose link leaves
      onto a frontage road: the unreachable mainline segments are *not* bridged in behind
      it — 3.0 chain miles, not the 5.0 a geographic sort would give), plus two
      self-skipping real-network tests (I-84 EB resolves as `('I-84 E',)` and nothing
      else; the State St break is recorded while its Eagle–Boise half resolves). The full
      catalogue was run against the D3 export — coverage counted straight off the three
      zips, **91,054,384 rows / 3,905 segments / 59.9% surviving `CValue > 80`**,
      reproducing Session 40 — and the resolution table is in DESIGN_HISTORY Session 41.

*Suggested prompt:* "Do Item 36 of ROADMAP.md — convert the D3 corridor catalogue to
endpoint pairs resolved through `corridors.build_chain`, with per-entry resolution
accounting, per DESIGN_HISTORY Session 38."

---

## 37 — KML: the categorical palette collides silently ✅ (Session 42)

**Target: Sonnet-eligible.** Small and mechanical, in `kml.py` only.

`_segment_colors` assigns categorical colours with `names[k % len(names)]` and
`_CATEGORICAL_PALETTE` holds **8**. A ten-corridor map therefore draws corridors 9 and
10 in the same colours as 1 and 2 — and `_add_legend` prints the duplicate swatches
without complaint, so the legend actively asserts a distinction the map does not make.
The screening output hit exactly this: SH-69 N/S are indistinguishable from I-84 E/W.
Scope:

- [x] **Stop the silent wrap.** Extended `_CATEGORICAL_PALETTE` from 8 to **12** hues
      (added `yellow`/`cyan`/`pink`/`lime` — bright, high-saturation, chosen to read
      against muted satellite greens/tans/greys rather than blend into them) and
      `_segment_colors` now **raises `ValueError`** when a `color_by` column's distinct
      values exceed the *default* palette's length, naming the count and pointing at
      `palette=`. An explicit `palette` (list or `{value: colour}` dict) is untouched —
      that path still cycles exactly as before.
- [x] **Added `folder_by=`** on `geometry_to_kml` — one KML `<Folder>` per distinct
      value (first-seen order), each holding just that group's `<Placemark>`s; a
      missing/NaN value groups into `"(none)"`. `folder_by=None` (default) keeps the
      flat placemark list under `<Document>`, so every existing caller is unaffected.
      An unknown column raises rather than silently producing zero folders.
- [x] **`name_col` defaults to the friendly name** from `names.apply_names(geo)` (called
      with `geo` itself as the "metadata" frame — it only reads
      `Road`/`Direction`/`Intersection`/`Combined` when present and falls through
      per-segment to the raw `Combined` label, then to `str(Segment ID)`, exactly
      matching `apply_names`'s existing three-level fallback). Passing an explicit
      `name_col` (as `gui/app.py`'s `_write_kml` already does with `"Combined"`) bypasses
      it unchanged — the GUI's only caller is untouched by this default.
- [x] pytest (`tests/test_kml.py`, +8): 13 categories against the 12-hue default
      raises (and 12-exactly does not); an explicit 2-colour palette over 4 categories
      still cycles; the legend has 12 distinct swatch colours at the palette boundary
      (zero duplicates); `folder_by` emits one `<Folder>` per group with the right
      membership, a `None` value groups as `"(none)"`, and an unknown `folder_by` column
      raises; the id-fallback default is re-tested against a fixture that carries none of
      the naming columns (the old toy fixture carries `Combined`, so its "default name"
      test now asserts the friendly name, not the id — that assertion changed on
      purpose, see DESIGN_HISTORY). **Full suite: 440 passed, 2 skipped** (was 432/2 at
      the Item 36 checkpoint). DESIGN_HISTORY entry.

*Suggested prompt (done):* "Do Item 37 of ROADMAP.md — stop `kml._segment_colors` cycling
the categorical palette silently, add folder grouping and friendly placemark names, per
DESIGN_HISTORY Session 38."

---

## Future (not yet scoped — need a planning pass before they're actionable)

- **Directional AADT (direction-aware *volume* + a time-of-day directional
  factor).** Distinct from Item 20's direction-aware *display*; this is about the
  AADT **count** used for weighting. AADT (Item 18) is currently a single
  undirected volume per segment;
  the owner wants **direction-aware** volume — split N/E vs S/W (a signed `+`/`−`
  convention) *or* an N/E/S/W selector for the map (as an offset or a multiselect)
  — and, as the fancy version, a **directional factor by time of day** (the
  classic peak-direction split, e.g. AM inbound / PM outbound). It would refine
  the Item 18 vehicle-hours-of-delay and weighted-speed numbers by using the
  direction-appropriate volume. Needs a planning pass first: check whether the
  `Cumulative_AADT` layer actually carries direction (route direction, the
  `MADT1..12` monthly split, class fields) or whether direction must be inferred
  from the XD segment bearing, and decide the map UX (sign vs selector vs offset
  vs multiselect) before it's actionable. **Item 34 comes first:** the join this would
  refine currently attributes ramp counts to one carriageway of a divided highway, so a
  directional split built on today's `join_aadt` would be splitting the wrong number.
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
- ~~**Automatic corridor assembly**~~ — **delivered as Item 28** (2026-09-17):
  `corridors.build_chain` walks `NextXDSegI` from snapped endpoint coordinates instead
  of hand-listing members, with endpoint trim and missing-segment accounting; feeds
  `chain_travel_time` / `speed.corridor_travel_time` (and the Item 19 membership table).
- **OSM geometry fallback** — only needed for segments *not* in the XD shapefile
  (out-of-state, or a future provider change): per-segment map-matching
  (osmnx/OSRM/Valhalla + a Shapely endpoint cut, QA'd against `Miles`). Not
  required while the INRIX XD shapefile covers the study area — kept here as the
  documented escape hatch.
