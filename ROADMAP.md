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
is now done** (Session 24); **Items 20–21 remain open.**

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

## 20 — GUI map & layout display polish: layout reflow + directional segment display (Target: Opus) — needs Items 7, 8

Two GUI-display fixes that live in the **same region** of the code — `gui/app.py`'s
layout tree and the map container / `figures.segment_map` — bundled into one
session (they were separately scoped as Items 20 and 22 on 2026-07-17, then merged:
running them together loads the GUI context once and places the new map controls
against the final layout in a single coherent pass, rather than reflowing the map
container and then squeezing controls into it a session later). No new statistics.

**(A) Layout reflow.** Today there is **awkward whitespace between the map and the
charts**. Restructure the layout so the chart panels sit **below the map and to
the right of the settings/controls column**, closing the gap.

**(B) Directional segment display.** The map draws each segment as its XD polyline,
but **co-located opposing-direction segments overlap** — only the one plotted on
top is visible, so the other direction is hidden and unclickable (which also makes
the Item 19 map-click-to-row selection ambiguous on those pairs). Make both
directions visible and selectable. Two mechanisms the owner floated — **decide at
the top of the session whether to ship toggles, the offset, or both** (recommend
both: the toggle declutters, the offset lets you see both directions at once);
record it.

1. **Direction display toggles** — show/hide segments by direction, by compass
   (N/E/S/W) or by a **positive/negative** convention (**N & E = `+`, S & W =
   `−`**); the control filters which segments render on the map and are click/hover
   targets.
2. **Minor geometry offset** — nudge co-located segments a small amount
   perpendicular to their bearing so opposing pairs draw side-by-side instead of on
   top of each other. A pure-core helper on the **display** geometry; the analytic
   geometry stays untouched.

Direction comes from metadata `Direction` (DATA_FORMAT) / the XD segment bearing
(Item 8). The `+`/`−` compass convention here is the same one the Future
**directional-AADT** item would reuse — factor it so both can share it.

Scope:
- [ ] **(A)** Reflow the `gui/app.py` layout tree (+ any `gui/` styling):
      settings/controls in a left column; the map top-right; the chart panels
      directly **below the map** in the same right column (or a responsive grid),
      removing the map↔charts whitespace. Every existing control and panel stays
      wired — no callback changes beyond layout; keep it responsive (narrower-width
      check).
- [ ] **(B)** Pure-core direction helper in `geometry.py`: map `Direction` →
      compass group and → `+`/`−` (N/E = `+`, S/W = `−`), and (if offset is chosen)
      an **offset helper** that shifts co-located/overlapping opposing segments
      perpendicular to their bearing by a small map-appropriate amount, exposed as a
      `display_offset` column or a separate display GeoDataFrame so the analytic
      geometry is untouched and the shift is visible-not-silent. Typed; no plotting.
- [ ] **(B)** GUI: a direction-display control (compass multiselect and/or a `+`/`−`
      toggle) that filters which segment directions render and stay selectable; the
      offset applied to the **display** geometry so overlapping pairs separate.
      Toggle + offset compose (a hidden direction needn't be offset). Selection and
      hover still fire on the visible segments; the map colour-bar/metric colouring
      is unaffected.
- [ ] The headless layout smoke test still finds every control/panel it asserts on
      (update the structural assertions for the reflowed tree + the new direction
      control). Preview-verify **both**: the closed map↔charts gap (screenshot +
      responsive resize) and a previously-hidden opposing segment now **visible and
      clickable**.
- [ ] pytest: the direction→compass/`±` mapping; the offset helper (a co-located
      opposing pair separates, a non-co-located segment is untouched, the analytic
      geometry is byte-for-byte unchanged); GUI wiring (toggle filters the rendered
      directions; both directions selectable). DESIGN_HISTORY entry; a DATA_FORMAT
      note if the direction convention is codified.
- [ ] **Split point if the session runs long:** land the pure-core `geometry`
      direction/offset helper + its tests first; the layout reflow and the GUI
      toggles are the clean follow-on (both GUI-only).

Suggested prompt:
> [Opus] In Inrix/, do Item 20 of ROADMAP.md: GUI map & layout display polish, two
> fixes in the same map/layout region — (A) reflow the layout so the charts sit
> below the map and right of the settings column, killing the map↔charts
> whitespace; (B) fix directional segment display so co-located opposing segments
> stop hiding each other, via a pure-core `geometry` direction helper (Direction →
> compass and → `+`/`−`, N/E positive) + optional perpendicular display-offset
> (analytic geometry untouched) and GUI direction toggles that filter which
> directions render. Decide toggles-vs-offset-vs-both at the top and record it.
> Keep every control wired + the layout smoke test green; pytest the mapping + the
> offset (co-located pair separates, analytic geometry unchanged); preview-verify
> the closed gap and a previously-hidden segment now clickable. Docs.

---

## 22 — (merged into Item 20 on 2026-07-17 — directional segment display)

Retired ID — the directional map-display fix was combined with the layout reflow
into **Item 20** (same `gui/app.py` map/layout code region; one GUI-context load).
Not reused. See DESIGN_HISTORY Session 23.

---

## 21 — Database-backed storage & ingest (`store.py` + GUI intake) (Target: Opus) — needs Items 1, 8, 18

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
- [ ] **Pure core `src/inrix_tools/store.py`**: `connect(db_path)`,
      `ingest_export(...)` (writes the typed tz-aware frame + metadata, idempotent
      — re-ingest replaces/updates), `ingest_geometry(...)` / `ingest_aadt(...)`
      (persist the Item 8 geometry and the Item 18 AADT join keyed by dataset),
      `list_datasets()`, and `load_dataset(...)` returning the **same typed frames
      the file loaders produce** (`io.load_data` parity). No hardcoded paths.
- [ ] **Cache the processed GIS join** (segment → geometry, segment → AADT with
      `aadt_source`/match distance) keyed by dataset so Item 18's spatial join
      runs **once at ingest**, not per load.
- [ ] **GUI**: a low-visibility (not top-of-page) **"Ingest export"** intake
      button; the main data control becomes a **select-from-ingested-datasets**
      dropdown reading the DB, replacing/augmenting the path box. Running straight
      from a file path still works unchanged (DB optional).
- [ ] Schema + versioning/migration note in **DATA_FORMAT.md**.
- [ ] pytest: ingest → list → load round-trip returns frames **equal to the direct
      file loaders**; the GIS-join cache hit avoids recompute (monkeypatched
      counter); the DB path is a param (no hardcoded path escapes); a self-skipping
      real-export ingest test. DESIGN_HISTORY entry.
- [ ] **Split point if the session runs long:** land the pure-core `store.py` +
      ingest/round-trip (with tests) first; the GUI intake/select rewiring is the
      clean follow-on.

Suggested prompt:
> [Opus] In Inrix/, do Item 21 of ROADMAP.md: add database-backed storage. A pure
> `src/inrix_tools/store.py` (pick DuckDB vs SQLite at the top, record it) that
> ingests an export + the XD geometry + the Item 18 AADT join into a local DB
> once (idempotent, no hardcoded paths) and loads datasets back as the same typed
> frames the file loaders produce, caching the processed GIS join so the spatial
> match runs once. GUI: a low-visibility "Ingest export" button and a
> select-from-ingested-datasets dropdown; file-path loading still works. pytest
> the ingest→load parity + the join-cache hit. DATA_FORMAT schema note; docs. If
> long, land the pure core first and the GUI second.

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
