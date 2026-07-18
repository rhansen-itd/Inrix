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
one-dataset-per-export model. The batch (Items 19–21, 23) is complete; remaining
work is all in **Future** (needs a planning pass).

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
  vs multiselect) before it's actionable.
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
  hand-listing members; feeds `speed.corridor_travel_time` (and the Item 19
  membership table).
- **OSM geometry fallback** — only needed for segments *not* in the XD shapefile
  (out-of-state, or a future provider change): per-segment map-matching
  (osmnx/OSRM/Valhalla + a Shapely endpoint cut, QA'd against `Miles`). Not
  required while the INRIX XD shapefile covers the study area — kept here as the
  documented escape hatch.
