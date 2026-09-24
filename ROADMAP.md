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
32). **Item 31 (chain membership accounting + a recorded CValue gate) is done**
(Session 43) and **Item 32 (the compression as a slope, with the free-flow level gap
reported separately) is done** (Session 44) — arterial median slope **0.536**, near-zero
intercepts, and both rural slope intervals spanning parity. **Item 33 (the directional
free-flow level gap) is done** (Session 45): the INRIX chains are mirrored to 0.04 min
per slice, and the gap is **reference-side** — SH-69 SB's own no-traffic duration is
11.32 min against northbound's 8.27 over the same 7.113 miles, while Eagle Rd NB's
−5.36 is 4.04 min of delay the reference itself reports at the percentile the gap is
quoted at. The level gap now splits into `ref_free_flow_delay` and
`free_flow_gap_static` wherever it is reported. The batch (Items 31–33) is **complete**.
Everything else is in **Future** (needs a planning pass).

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

**Items 38–39 are a new batch** scoped 2026-09-18, closing out the district screen that
Items 34–37 built but never ran. Item 36 resolved **13 of 20** D3 corridors and recorded
the other 7 as findings about the XD network file — correctly, because the mechanism it
replaced bridged breaks by sorting a bounding box geographically. But the findings
include **I-184**, the whole west-side commute into downtown Boise, and a district screen
that cannot rank it is not finished. Re-measured against the D3 subset, six of the seven
are repairable **without inventing any order**: scoping a repair to XD's own carriageway
key (`XDGroup`) plus 25 m endpoint contact fills 6,119 null links and overrides 99 that
point out of their own carriageway, and takes the catalogue to **20/20** — while the
obvious rule (`RoadNumber` + bearing) walks I-184 into a 196-segment, 110-mile runaway
and is rejected on that measurement. The seventh, `front-wb`, is not a topology defect at
all: west of S 13th St all but one lane of Front St becomes I-184 and ITD's interest
transfers to the freeway, so the extent shortens there for a **roadway** reason.
**38** repairs the topology as an auditable committed patch table, opt-in at the walk;
**39** adds the runner that composes `segment_screen` → `join_aadt` → `resolve_catalogue`
→ `rank_corridors` and produces District 3's actual rankings. Run 38 first — 39 ranks 13
corridors and silently omits I-184 until it lands. **Item 38 (carriageway-scoped link
repair) is done** (Session 47): `scripts/d3_link_repairs.csv` carries 6,090 fills and 66
overrides derived by `corridors.repair_links`, the catalogue resolves **20 of 20** using
14 of them, and the rule needed two guards that only running it revealed — a rotary that
reverses a corridor without any junction turning more than 60 degrees, and a link that
leaves the subset rather than the road. **Item 39 (the district screening runner) is
done** (Session 48): `scripts/run_district_screening.py` composes the pipeline end to
end and District 3's rankings exist — **I-84 WB 25,677 veh-hrs** and **EB 19,818** an
order of magnitude clear of everything else, I-184 WB ranking fifth at 2,700 having been
unrankable before Item 38, and SH-45 behaving as the rural control at TTI 1.07. The
`ingest_export_streaming` row-count finding was settled there too, and it was **not** a
counter bug. **Item 40 (reporting corridors) is done** (Session 49), added after the
owner clarified that *for reporting purposes one corridor is both directions*: the
catalogue's 20 directional entries now declare the **10 roads** they group into,
`screen.rank_corridor_groups` combines them without averaging a ratio or summing the two
carriageways' miles, and the grouped view re-orders the district — Eagle Rd to #2, I-184
to #4. **Item 41 (the reporting table) is done** (Session 50): every direction × peak is
its own row beneath a corridor total summed over both, and the ranking moved to
**vehicle-hours of delay per mile** — volume-weighted but not length-rewarded, on which
I-84 leads at 1,539 per mile while the downtown couplet holds 2nd despite ranking 9th of
10 on the bare total. The batch (Items 38–41) is **complete**.

**Items 42–44 are a new batch** scoped 2026-09-18, out of the owner's reaction to the
first district ranking: the corridors they expected to see were not in it. The cause is
**not** a gap in the data — `out/highways/` already inventories D3's state system at 3,947
segment ids, the export observed 3,905 of them, and **nothing was observed that the list
did not ask for**. (An earlier pass of this session claimed Karcher Rd and 354 miles of
arterial were missing; both were wrong — Karcher is 92 of 92 present, and the arterials
are off-system county roads, correctly excluded. What looked like a 99-segment gap was
resolved with the owner and is **7**: the SH-55 Eagle Rd segments were deleted on purpose
as ACHD, the I-84B Caldwell corridor was excluded on purpose as relinquished to the city,
and the only thing missed in excluding it is that **SH-19 between I-84 and Simplot Blvd is
still carried in the data as Centennial Way with `RoadNumber` 84** and went out with it.)
The cause is the **catalogue**: it covers 9.4% of the
store, roughly 1,190 miles of numbered state highway — US-95, SH-21, SH-51, SH-78, SH-52
and more — carry no entry at all, and the extents that do exist are drawn from junctions
and city limits rather than from the congestion. SH-45 is one 17.4-mile corridor of which
only the 4.4-mile 12th Ave section through Nampa is urban, so its congestion is diluted by
three times its length of rural highway. The owner's requirement is that extents be
extracted as contiguous-ish runs of *recurring* congestion, tidied to a junction only when
the data already lands near one. So: **42** reconciles the id files against the export and
fixes the AADT join's missing numbered-over-`OH` preference (without which an on-system
classification flips with the candidate set); **43** extracts corridor candidates from
recurring congestion, with a gap tolerance and endpoint snapping; **44** rebuilds the
catalogue from them. **Item 42 is done** (Session 52), with one correction to its own
premise: preferring a numbered record ahead of distance or coverage is wrong (it hands a
city street at an interchange the interstate's volume), and the instability was that the
join's **last comparison was the record's row position in the layer** — so route class
went in as the last decision, under coverage, and the join is now order-independent.
Reading on-system-ness off the volume join is what made the old candidate list junk;
`aadt.classify_on_system` asks it separately and takes 764 segments / 223 mi down to
**26 / 8.77 mi**, of which the owner's seven confirmed SH-19 segments are found
independently and **W/E State St in Eagle (11 segs, 4.68 mi) is the one genuinely new
question**. No re-download is warranted: the export is split by segment, so the gap can
arrive as a supplemental part. **Nothing here needs new ranking machinery** — Items 38–41 made
adding a corridor a catalogue edit, and that is what these items feed. **Item 43 (corridors
extracted from recurring congestion) is done** (Session 54): `screen.segment_recurrence`
reduces per-day TTI in DuckDB and computes weekday recurrence share, distinguishing
construction fortnights from daily queues with the same mean; `screen.extract_congestion_runs`
walks the topology without sorting, bridging gaps within tolerance and respecting
`XDGroup` boundaries; `screen.tidy_run_endpoints` snaps endpoints within tolerance to
state-route junctions with distance reported; `screen.pair_directions` checks opposing
carriageways; and `screen.emit_candidates` produces catalogue candidates with description
omitted to mandate human review. **Item 44 is the next open item.**

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

## 32 — Report the delay compression as a slope, not a ratio of means ✅ (Session 44)

**Target: Opus; the estimator itself is Fable-eligible** (math-heavy, per the CLAUDE.md
rule of thumb). `agreement.py` + the report's scorecard + a DATA_FORMAT pass.
**Depends on Item 31.**

Item 30 records the finding as a **delay ratio** — `mean(delay_inrix) / mean(delay_ref)`,
each above its own 10th percentile — reported as "median 0.59 (0.37–0.95)". That is a
ratio of two small means, it is the only statistic in the report carrying no uncertainty
interval (against the CLAUDE.md standard the same report meets everywhere else), and it
is visibly unstable: **VSL SB PM reports a delay ratio of 2.32 against a regression
slope of 0.71** on the same bins. Scope:

- [x] **Add a per-route regression of INRIX delay on reference delay** (each above its
      own free-flow percentile) returning **slope, intercept, and a day-blocked CI** on
      both, alongside the existing bias CI. On the 2026 D3 export the arterial slopes
      cluster tightly — median **0.534**, with Eagle Rd NB 0.517 / intercept 0.037,
      Eagle Rd SB 0.514 / 0.080, Franklin EB 0.465 / 0.066, SH-69 NB 0.566 / 0.085 —
      which is a far better-conditioned estimate than the ratio of means. Keep
      `delay_ratio` as a secondary column; don't silently swap the definition under a
      name Item 30's numbers were published under.
      **Done:** `agreement.delay_regression`, folded into `compare` so the whole
      pipeline gets the columns. The CI is a **day-clustered sandwich** (CR1, *t* on
      G − 1) — the regression analogue of the bias CI's t-interval over daily means —
      with the per-bin interval kept beside it as `*_naive` and
      `delay_slope_ci_width_ratio` showing the inflation (**median 2.0×, max 3.2×**).
      Measured: arterial median slope **0.536** (range 0.250–0.875), Eagle Rd NB
      0.520 [0.505, 0.536] / intercept +0.018, SH-69 SB 0.250 [0.208, 0.292]. Both
      rural slope intervals span parity. `delay_ratio` keeps its name, its definition
      and a scorecard column, footnoted as secondary.
- [x] **Separate the two effects the `bias` column currently fuses.** The arterials
      carry a **level gap at free flow** *stacked on top of* the slope: Eagle Rd NB's
      10th-percentile gap is −5.36 min and SH-69 SB's is −4.69 min, while SH-69 NB over
      the same endpoints reversed is −0.82 min. A single `bias` cannot distinguish "the
      two sources are measuring different pavement" from "INRIX compresses delay", and
      those have opposite implications for a before/after study. Report the free-flow
      level gap and the slope as separate columns and lead the scorecard with both.
      **Done:** `free_flow_gap` with its own interval, and the scorecard now **opens**
      with `Delay slope` / `Slope r²` / `Slope 95% CI` / `Free-flow gap` / `Gap 95% CI`
      before `Bias`, sorted by slope rather than bias. The index page leads with both as
      KPIs and draws a `slope_forest` and a `level_gap_forest` above the bias forest;
      each route page reports them as separate KPIs above the bias, which is labelled
      "the two above summed". The gap's interval is a **day-block bootstrap of the
      pooled gap** (fixed seed): a t-interval over per-day gaps was tried first and
      rejected because it estimates a different quantity and put Eagle Rd NB's −5.36
      outside its own interval.
- [x] **Restate the finding in DATA_FORMAT.md as a slope, with the before/after
      consequence made explicit.** The near-zero intercepts are the point: the
      disagreement is **multiplicative in delay**, not a fixed offset, so it does **not**
      cancel in a before/after difference. An intervention that removes 4 real minutes
      of delay scores as roughly 2.1 minutes on INRIX. Item 30's DATA_FORMAT section
      already says "about half the effect size in minutes" — replace the assertion with
      the measured slope and its interval, and keep the rural contrast (Cascade↔HSB,
      both CIs spanning zero) beside it.
      **Done:** new DATA_FORMAT section *The compression is a slope, not an offset
      (Item 32)* with the full per-route table, the interval construction, the rural
      contrast (both slope intervals spanning parity), and the 4 min → **2.1 min**
      consequence. The report's own prose is interpolated from the run's frames, so
      that sentence is now a computed number rather than a claim. Also recorded: the
      near-zero intercept is a **finding**, not an artifact — a constant added to every
      bin is absorbed into that source's own free flow and cannot show up as an
      intercept, so ≈0 means there is no fixed congested-bin penalty (tested both ways).
- [x] pytest: synthetic frames with a known slope and intercept recover them; the
      day-blocked CI widens against the naive one as it does for `bias`; a
      near-zero-delay route produces an unstable ratio **and** a stable slope, pinning
      the VSL SB PM case as a regression. DESIGN_HISTORY entry.
      **Done:** +16 tests (`tests/test_agreement.py` +9, `tests/test_validation_report.py`
      +7; **full suite: 474 passed, 2 skipped**), DESIGN_HISTORY Session 44. The
      instability is *measured* rather than asserted: the same low-delay route split in
      half by date moves its ratio by a third and its slope by a hundredth, each half's
      interval covering the other's estimate. The **end-to-end report re-run is still
      outstanding for the same reason as Item 31** — `TT Logger.xlsx` is not on this
      machine — so the published slope table is computed over the outside pass's
      28,340-bin matched set, with its provenance stated in DATA_FORMAT.

*Suggested prompt (done):* "Do Item 32 of ROADMAP.md — report the INRIX-vs-reference delay
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

- [x] **Test path equivalence, not endpoint equivalence.** Compare the assembled chain's
      geometry against what the reference route must have traversed — cumulative
      distance, road names along the chain (`chain_attributes`), and whether a plausible
      alternative path exists between the same two snapped points (a frontage road, a
      one-way pair, a different carriageway). A directional gap this large at free flow
      is much more likely a *route* difference than a *measurement* difference.
- [x] **Decompose the gap along the chain.** With per-segment INRIX travel time in hand,
      find whether the SB level gap is spread evenly (suggesting a length or extent
      mismatch) or concentrated in one or two members (suggesting a specific
      intersection, ramp terminal, or a segment whose XD extent disagrees with the
      roadway). Do the same for Eagle Rd NB and compare.
- [x] **Check the reference side for a directional artefact** before blaming geometry:
      whether SB and NB samples are drawn at the same times of day, whether the SB
      route's logged origin/destination pair is actually the reverse of NB's, and
      whether the sheet's own extent matches.
- [x] **Land it as a finding either way.** If it is a route mismatch, the affected routes
      need re-endpointing and the headline numbers re-derived; if it is real, it is a
      second INRIX limitation distinct from the delay compression and belongs in
      DATA_FORMAT beside it. A negative result is a valid outcome here — record what was
      excluded. DESIGN_HISTORY entry.

**Outcome (Session 45): reference-side, and two different things.** The INRIX path is
identical in both directions — member-for-member mirrored chains, 7.113 mi requested
each way, a median 0 ft between the two directions' geometry, and per-member 10th
percentiles summing to 8.540 min NB against 8.440 SB with no slice differing by more
than 0.04 min. The asymmetry is entirely in the reference's **own no-traffic duration**
(`Travel Time − Extra TT`, constant per sheet): **11.3167 min SB against 8.2667 NB** over
the same pavement. Eagle Rd NB's −5.36 is mostly not a level gap at all — 4.04 min of it
is delay the reference itself reports at its own 10th percentile. `agreement` now splits
every level gap into `ref_free_flow_delay` and `free_flow_gap_static`; DATA_FORMAT gains
*The directional level gap is on the reference side (Item 33)*. The **size** of SH-69
SB's route difference is **not** established — the logger records no route — and the
leading mechanism (one logged coordinate on the northbound carriageway, 63.9 ft from the
southbound roadway against a 63.2 ft carriageway separation) is recorded with the two
measurements that would settle it.

*Suggested prompt (done):* "Do Item 33 of ROADMAP.md — diagnose the directional free-flow
level gap on SH-69 SB and Eagle Rd NB, testing path equivalence rather than endpoint
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

## 38 — The XD topology, repaired as data: carriageway-scoped link repair ✅ (Session 47)

**Target: Opus.** Pure core (`corridors.py`) plus a committed patch table and a
catalogue edit. No GUI. **Item 39 depends on this** — the district ranking is wrong or
absent for 7 of 20 corridors until it lands.

Item 36 resolved 13 of 20 D3 corridors and recorded the other 7 as findings, correctly:
the mechanism it replaced bridged breaks by **sorting the bounding box geographically**,
which put the Garrity Blvd frontage road in series with I-84. That ban is on *inventing
order the network does not assert*. It is not a ban on **correcting a link the network
asserts wrongly**, and the 7 findings are almost entirely that. I-184 — the whole
west-side commute into downtown Boise — cannot be ranked at all, which is not an
acceptable resting place for a district screen.

The repair rule is **XD's own carriageway grouping**, not proximity: a candidate must
touch this segment's end within 25 m, stay inside its `XDGroup`, carry the same cardinal
`Bearing`, turn no more than 90° on the *local* geometry at the junction, and be the only
candidate that qualifies. On the D3 subset (16,105 segments; 8,499 = **52.8%** null
`NextXDSegI`) that is **6,090 fills** (267 ambiguous, left null) and **66 overrides**
(**zero** ambiguous). Fills alone take the catalogue 13/20 → **17/20**; fills + overrides
to **20/20**, with I-184 back at **EB 10 segments / 4.717 mi, WB 10 / 4.629 mi**.

**Why `XDGroup` and not `RoadNumber` + bearing** — the obvious rule, measured and
rejected: at the Flying Y the I-184 EB mainline's end point is **8.1 m** from the on-ramp
named `1A` (FRC 4, 1 lane) and **4.7 m** from the true mainline continuation. Road number
and bearing agree with both; distance prefers the ramp. Under that rule `i184-eb` walks
away into a **196-segment, 110.6-mile** chain. A repair rule that can do that is not a
repair rule. Scope:

- [x] **`corridors.repair_links(network, *, radius_m=25.0, max_bearing_delta_deg=90.0,
      require_same_bearing=True, kinds=("fill","override"))`** — pure, returns the patch
      **as data**: `REPAIR_COLUMNS` = `segment, old_next, new_next, kind, gap_m,
      bearing_delta_deg, xdgroup, road_name, lanes`, with `attrs` carrying the rule
      parameters, the counts per kind and the **ambiguous** counts. `apply_link_repairs`
      (idempotent, subset-safe) returns the patched network; `load_link_repairs` reads a
      committed table back with its `# key: value` header as `attrs`. Nothing is repaired
      implicitly and nothing raises: an ambiguous break stays a break.
- [x] **Two guards the scoped rule turned out to need**, both found by running it rather
      than by reasoning about it, and both now in DATA_FORMAT: **(a)** a same-`XDGroup`
      continuation can still reverse the direction of travel — at the south end of Eagle
      Rd a **rotary** (`Bearing` `O`, six segments of 8–20 m) joins the two carriageways
      and *no junction in it turns more than 60°*, so the angle guard cannot see it;
      repaired through, `sh55-eagle-nb` walked south, round the rotary and back north —
      39 segments / 10.75 mi against the corridor's 16 / 6.64. `require_same_bearing`
      excludes it. **(b)** a link that leaves the **subset** is not a defect but the edge
      of the extract (96 in D3) and is left alone, which `walk_chain` already reports as
      `off_network`.
- [x] **Opt-in at the walk.** `build_chain` / `resolve_catalogue` / `chain_between_segments`
      take `repairs=` (default `None`). `ChainResult` carries `repaired_links` — the links
      **it actually traversed** — so `summary()` and the `resolve_catalogue` row report
      `n_repaired_links` (added to `CATALOGUE_COLUMNS`), and `attrs` gains
      `repairs_applied` / `n_repairs`. The whole 20-corridor catalogue uses **14** of the
      table's 6,156 rows, which is the honest measure of how much it leans on them.
- [x] **Froze D3's table to `scripts/d3_link_repairs.csv`** (6,156 rows, 506 KB) with the
      rule, the counts and the meaning of each `kind` in a comment header, so a screening
      run is reproducible and the **66 overrides are reviewable by a human**. A test
      regenerates it from the network and asserts the committed rows come back.
- [x] **Decision: the override class ships on by default** (`kinds=(FILL, OVERRIDE)`),
      recorded here and in DESIGN_HISTORY. The evidence: all 66 were read, and in every
      case the replaced target is a cross street, a ramp or a parallel facility while the
      replacement is XD's own same-carriageway segment (`I-184 E` → off-ramp `3`;
      `I-84 W` → ramp `27`; `S Cloverdale Rd` → `W Kuna Mora Rd`); ambiguous overrides are
      **zero**, so none of the 66 is a coin-flip; and exactly one already-accepted
      corridor moves (below). `kinds=(FILL,)` remains available as the setting that never
      contradicts the network.
- [x] **Explained the one accepted corridor that moves.** Under fills alone all 13
      already-accepted entries are unchanged (`i84-eb` 27 segments / 15.123 mi, SH-45 40 /
      17.429). Adding overrides changes exactly one: `sh44-wb-boise-eagle` 11 segments /
      4.1064 mi → **12 / 4.1441**. The responsible override is `484348318` — `N Glenwood
      St`, `RoadNumber` **44**, 2.705 lanes, FRC 3 — whose `NextXDSegI` pointed at
      `440882032` `N Gary Ln`, **1.113 lanes**, FRC 4, no route number. The new chain is
      the better one: it is the SH-44 mainline block the old one dropped.
- [x] **Reconciled SH-16 against its own description.** Repaired, `sh16-nb` and `sh16-sb`
      resolve at **22 segments / 12.553 chain mi / 11.947 mi in extent** each, share
      **zero** segments and carry clean `N`/`S` bearings — so the coincident-carriageway
      caveat in the entry text did not apply and has been corrected. The description's
      "12.9 mi north of the break walks cleanly" was the wrong number: it came from an
      **unbounded** walk, which runs **41.6 mi** past Emmett to Payette on SH-52. The
      resolved extent checks out independently — sinuosity 1.053 against the 11.345 mi
      straight line, XD `Miles` and the projected geometry agreeing to 0.005 mi.
- [x] **`front-wb`: extent shortened at S 13th St, on a roadway reason.** Not a topology
      defect — the chain reached its target — and not a general case worth generic code.
      West of 13th all but one lane of Front St becomes I-184; the remnant keeps the name
      and functions as a frontage road, and ITD's interest transfers to the freeway there
      because that is the state-owned facility. The XD attributes say the same: the **9**
      members now in the entry carry `RoadNumber = 20` at **5.0 lanes** (1.072 chain mi),
      the **3** blocks beyond carry a null `RoadNumber` at **1.0 lane**. The description
      states the jurisdictional reason **first** and says explicitly that the export's
      73.1% coverage corroborates the boundary rather than setting it — an extent defined
      by where the data stops is what Item 36 refused for SH-16. `end_latlon` is
      `[43.61698, -116.21064]`, 37 ft short of the junction, so the snap lands on the last
      five-lane block instead of the one-lane remnant across it.
- [x] **Rewrote the six repaired entries' descriptions** (`i184-eb`, `i184-wb`,
      `sh44-eb-star-eagle`, `sh44-wb-eagle-star`, `sh16-nb`, `sh16-sb`) and the file's
      `_note`: the FINDING paragraphs are now history rather than status — what the break
      *was* is kept, because it is a real fact about this XD vintage, and each entry states
      its resolved chain and how many repaired links it rests on.
- [x] pytest (`tests/test_corridors.py`, **+14**, 64 in the file; full suite **499
      passed, 2 skipped**, was 485/2): fill, ambiguous fill
      (stays a break), override with its `old_next`, the **ramp trap** (a deliberately
      *nearer* out-of-group candidate loses to a farther in-group one), the **rotary**
      (same group, different `Bearing` → refused, and `require_same_bearing=False` accepts
      it), the anti-parallel cul-de-sac pair, the off-subset pointer, an unknown `kind`
      raising, `apply_link_repairs` idempotent and subset-safe, `repaired_links` credited
      only for links a chain actually traversed, the `resolve_catalogue` accounting, and
      the header round-trip. Plus two real-network tests: the committed table resolves the
      D3 catalogue **20/20** with `i84-eb` untouched at 27 segments and I-184 at 10, and
      regenerating the table from the network reproduces the committed rows.
- [x] DATA_FORMAT.md: a new `XDGroup` subsection — the carriageway key, the two repair
      classes with their D3 counts, the four measured reasons the rule is shaped this way,
      and what separates a repair from the geographic sort Item 36 banned.
- [x] DESIGN_HISTORY entry (Session 47).

*Suggested prompt (done):* "Do Item 38 of ROADMAP.md — repair the XD `NextXDSegI` topology
as an auditable carriageway-scoped patch table, opt-in at the walk, and resolve the D3
catalogue 20/20."

---

## 39 — The district screening runner: D3's actual rankings ✅ (Session 48)

**Target: Opus.** A thin shell in `scripts/` over the existing core plus the run it
produces. **Depends on Item 38** — running it before that ranks 13 corridors and silently
omits I-184 and both halves of west State St.

Items 34–37 built every piece of the district screen and Session 41 deliberately stopped
short of running it ("producing the district's actual rankings needs the 2.1 GB store
ingested and belongs with the display work"). Nothing in the repo ran the pipeline end to
end: `screen.segment_screen`, `aadt.join_aadt`, `corridors.resolve_catalogue` and
`screen.rank_corridors` were each tested in isolation and wired together only inside
tests. The composition is where the interesting failures live — and two of them were
waiting there. Scope:

- [x] **`scripts/run_district_screening.py`**, modelled on
      `scripts/build_validation_report.py` — a **thin shell only**: no computation that
      is not already in `src/inrix_tools/`. CLI over the choices a run actually has:
      `--db`, `--area` (key *or* name; required only when the store holds several),
      `--bin-minutes`, `--catalogue`, `--repairs` / `--no-repairs`, `--network` /
      `--network-cache`, `--aadt` / `--aadt-cache` / `--aadt-year`,
      `--cvalue-threshold` (default 80), `--windows`, `--date-start` / `--date-end`,
      `--tz`, `--min-coverage`, `--out-dir`, `--no-kml`, `--top`.
- [x] **The pipeline:** `store.connect` → `segment_screen(con, area_key,
      cvalue_threshold=80)` → `aadt.load_aadt` + `join_aadt` (mainline-preferred, Item 34)
      → `resolve_catalogue(..., observed=…, repairs=…)` → `rank_corridors`.
- [x] **Outputs:** `corridor_rankings.csv`, `corridor_resolution.csv` (with
      `n_repaired_links` and the coverage columns), `corridors.kml` and
      `screening_provenance.json`. The KML is the first caller to exceed Item 37's
      12-hue guard — 20 corridors — so it passes an explicit 20-colour palette and
      `folder_by="corridor"`, exactly the case that guard exists to force.
- [x] **Provenance in the output, not just in the log** — area, bin, dates, CValue
      threshold, windows, row and segment counts, catalogue, the repair table *with its
      rule and how many of its rows were used*, and the AADT join policy and caveat. It
      is written as JSON **and** as a `#` header on both CSVs, so a ranking read off disk
      still states its basis.
- [x] **Refuses rather than ranks a corridor that did not resolve.** Findings are listed
      in their own section with `stop_reason` and coverage; they never appear in the
      ranking with blank metrics.
- [x] **Settled the `ingest_export_streaming` row-count finding — and it was not a
      counter bug.** `io._discover_parts` expands **any** `..._part_N.zip` into all its
      siblings, so the call was never handed one part: a single call on `part_1.zip`
      ingests all three and `n_rows_added = 91,054,384` is the correct total. That also
      explains the anomaly the finding could not — the store reached its complete state
      "while part 2 was still going" because the *first* call was itself looping over the
      three parts. `d3_store.duckdb`'s `_ingests` table confirms it: **one** row. The fix
      is to the record, not the number — `ingest_export` / `ingest_export_streaming` now
      return `n_parts` / `parts`, log the resolved member list instead of the single
      argument, and say so in their docstrings. DESIGN_HISTORY Session 40's figures stand
      as whole-export figures.
- [x] **Ran it on the D3 store: all 20 corridors ranked, no findings.** 3,905 segments,
      **54,556,187 of 91,054,384** rows surviving `CValue > 80` (**59.9%**, reproducing
      Sessions 40–41). Worst by vehicle-hours of delay: **I-84 WB 25,677** (PM, TTI 1.88)
      and **I-84 EB 19,818** (AM, TTI 1.69), an order of magnitude above everything else;
      then Eagle Rd NB 2,840, Chinden WB 2,700 and **I-184 WB 2,700** — the corridor that
      could not be ranked at all before Item 38. SH-45, carried as the rural control,
      lands near the bottom at TTI 1.07/1.06. Full table in DESIGN_HISTORY Session 48.
- [x] pytest (`tests/test_run_district_screening.py`, **+8**): a miniature district
      (export → ingest → network → catalogue → repair table) run end to end; the resolved
      corridor ranked under its id with its name beside it and `n_repaired_links = 1`;
      the unresolvable entry reported and **not** ranked; `--no-repairs` turning the run
      into a refusal rather than a partial ranking; the provenance present in both the
      JSON and the CSV header; `--no-kml` skipping only the KML; window and area
      resolution; and a missing repair table refused rather than ignored. Plus **+2**
      in `tests/test_store.py` for the multi-part ingest. **Full suite: 509 passed, 2
      skipped** (was 499/2 at the Item 38 checkpoint).
- [x] README run steps; DESIGN_HISTORY entry (Session 48); check these boxes off.

*Suggested prompt (done):* "Do Item 39 of ROADMAP.md — add the district screening runner
and produce D3's actual corridor rankings, per DESIGN_HISTORY Session 41."

---

## 40 — One corridor is both directions: reporting corridors ✅ (Session 49)

**Target: Opus.** Pure core (`screen.py`, `corridors.py`) plus a catalogue schema
addition and a second table in the runner. **Follows Item 39** — it changes how the
ranking is *read*, not how any number is computed.

Item 39 ranked the 20 D3 catalogue entries and the owner named the gap: **for reporting
purposes one corridor is both directions.** `i84-eb` and `i84-wb` competed in that table
as if they were different roads, which is not how a district programmes work — and the
peak and direction breakout still has to survive the grouping, because which direction
and which peak is the next question every time. Scope:

- [x] **The catalogue gains the second unit.** An entry keeps being one **direction** of
      one extent (the unit the `NextXDSegI` walk and the AADT join both work in) and
      gains `corridor` + `direction`; a new `reporting_corridors` block declares the
      roads, with the same `{id, name, description}` shape and the same validation the
      entries get. D3's **20 entries group into 10 roads**. `parse_reporting_corridors`
      cross-checks both ways: a group an entry names must be declared, and a group
      declared with no entries raises — a corridor declared and unused is one silently
      missing from the report. `resolve_catalogue` carries `corridor` / `direction` into
      the resolution table. A catalogue that declares no groups is still valid and ranks
      per direction exactly as before.
- [x] **`screen.rank_corridor_groups(ranking, membership, names=)`** — one row per road ×
      window. The work is that **the metrics do not combine the same way**: `vhd`,
      `n_obs`, `n_segments` and the trip components are **summed**; `miles` is the
      **mean** of the directions because the carriageways run over the same ground and
      summing would double-count the corridor's length — the identical error as summing a
      frontage road in series with its freeway — with the sum reported separately as
      `directional_miles`; and `tti` / `delay_per_mile` / `vhd_per_mile` are
      **recomputed from the summed components**, never averaged, because a ratio of sums
      is not the mean of the ratios.
- [x] **The direction breakout survives the grouping**: `peak_direction` / `peak_entry`
      (by `vhd`, falling back to `delay_min` when no AADT was joined), `tti_min` /
      `tti_max`, `delay_min_max`, and `directions`. `worst_peak` is the **road's** worst
      peak — the window carrying the most *combined* delay — which is not always either
      direction's own. The per-direction rows are untouched: this is a second view.
- [x] **Named the trap grouping sets, and gave it a column.** A grouped row sums its
      directions **at the same clock time**, and a commute corridor's directions peak at
      different times. I-84 WB carries 25,677 veh-hrs in the PM and EB 19,818 in the AM,
      but the grouped PM row reads **26,260** because EB at 5pm is nearly empty — right
      for "how bad at its worst hour", wrong for "how much delay in a day". So the frame
      carries `vhd_directional_peaks` / `delay_min_directional_peaks`: each direction at
      **its own** worst peak, summed (**45,495** for I-84), a group-level constant that
      deliberately spans two windows. D3 splits **five and five** — I-84, Chinden, I-184,
      State St west and SH-69 have directions that peak in different windows; Eagle Rd,
      State St east, the couplet, SH-45 and SH-16 do not, and for those the two numbers
      are identical.
- [x] **The runner writes `reporting_corridor_rankings.csv`** beside the directional one
      and prints the road view as the headline with the per-direction table beneath, both
      with the combining rules stated under the table. An ungrouped catalogue skips it
      entirely. `front-wb`'s *name* was also corrected to its Item 38 extent (it still
      said "to the Connector terminus").
- [x] **Re-ran D3 — grouping re-orders the district**, which is the point: `sh55-eagle`
      rises to **#2 (5,059 veh-hrs)** where its directions ranked 3rd and 6th separately,
      and `i184` falls to **#4 (2,738)** because EB is light. `boise-couplet` carries the
      worst delay per mile (0.94). `sh45` stays flat at TTI 1.06 — the rural control
      holding — though it outranks `sh16` on veh-hrs, which is length and volume, not
      congestion, and is exactly why TTI and delay/mile sit beside it.
- [x] pytest (**+19**): `test_screen.py` +8 on the combining rules against hand-computed
      arithmetic — the additive set, the mean-not-sum of miles, a **lopsided-length**
      case where the ratio of sums (2.525) and the mean of ratios (2.05) differ, the peak
      direction flipping by window, the group's own `worst_peak`, the one-window vs
      daily-burden split, the three accepted membership shapes, an ungrouped entry
      reported rather than dropped silently, and the two refusals. `test_corridors.py` +9
      on the schema (both fields required together, two entries claiming one direction,
      undeclared and unused groups, field validation, the resolution columns, and the
      real D3 catalogue grouping 20 → 10 as 2 distinct directions each).
      `test_run_district_screening.py` +2. **Full suite: 528 passed, 2 skipped** (was 509/2 at the Item 39 checkpoint).
- [x] DATA_FORMAT.md (the two units and the combining table); README; DESIGN_HISTORY
      Session 49; check these boxes off.

*Suggested prompt (done):* "Do Item 40 of ROADMAP.md — group the directional catalogue
entries into reporting corridors, keeping the peak and direction breakout."

---

## 41 — The reporting table: every direction × peak visible, ranked per mile ✅ (Session 50)

**Target: Opus.** Pure core (`screen.py`) plus the runner's printed table and two more
CSVs. **Follows Item 40.**

Item 40 grouped the directions into roads but still reported each road at a **single**
window, with the other direction and the other peak collapsed into a `peak_direction`
label and a TTI range. The owner asked for the reporting shape directly: every direction
and peak as **its own row**, the corridor's final ranking as a **sum over peaks and
directions**, and and the ranking taken on a **per-mile rate** rather than a total, so a long
corridor cannot out-rank a short saturated one on length alone. (The rate was first
built as `delay_per_mile`; the owner corrected it mid-item to **vehicle-hours per
mile**, which keeps the volume weighting — see the ranking bullet.) Scope:

- [x] **`screen.corridor_peak_totals(ranking, membership, *, names, windows, rank_by,
      couplets)`** — one row per reporting corridor, summed over every
      `direction × peak window` cell (D3: four cells per road, AM and PM × two
      directions). Delay, travel time, free-flow and `vhd` add, because the two peaks of
      one direction are two separate trips over the same pavement.
- [x] **The mileage denominator is counted once per direction, not once per cell** — a
      direction does not get longer because it has two peaks. Verified first rather than
      assumed: observed miles have **zero spread** across AM and PM for all 20 D3
      entries, and `miles_window_spread` reports it per corridor so a future export that
      breaks that assumption is visible rather than silently halving every rate.
- [x] **`screen.corridor_breakout`** — the same cells unaggregated, a
      `(corridor_group, direction, window)` MultiIndex, ordered by the ranked order it is
      handed. A total that cannot be opened up is a number taken on trust; a test asserts
      the cells sum to the total they sit under.
- [x] **Ranked on `vhd_per_mile` — vehicle-hours of delay per mile** (the owner's
      correction mid-item, after `delay_per_mile` was tried first). The three candidates
      are three different questions and D3 orders them three different ways: `vhd` asks
      how much delay a road causes and rewards **length and volume**; `delay_per_mile`
      asks how bad it is to drive and ignores **how many people it happens to**;
      `vhd_per_mile` keeps the volume weighting and drops the length reward, which is
      what a screening rank wants. Every metric's rank is returned beside the chosen one
      and `--rank-by` selects any of them, so the gaps are visible rather than decided
      silently. D3: **I-84 1st at 1,539 veh-hrs per mile** (nearly 3× the next), the
      **downtown couplet 2nd at 554** — a corridor that is 1st on the unweighted rate
      and **9th of 10** on the bare total — and SH-69 8th where the bare total puts it
      5th, which is 16.5 directional miles doing that work.
- [x] **Both `vhd` metrics are null without an AADT join**, and a table of `<NA>` ranks
      is catalogue order wearing a ranking's clothes. `attrs['rank_metric_all_null']`
      reports it and the runner falls back to `delay_per_mile`, printing a note.
- [x] **The one-way couplet caveat, considered and recorded.** `one_way_couplet` is an
      optional flag on the reporting corridor (D3 has exactly one, and the catalogue says
      so). It **changes no arithmetic** — every per-mile rate divides by the miles a round
      trip covers, here as everywhere, and a test asserts the flagged and unflagged
      totals are identical — but it changes how `directional_miles` *reads*: 2.22 mi of
      distinct centre-line pavement for the couplet, where I-84's 30.02 is 15.01 mi of
      ground driven twice. The flag exists so the column is not misread as centre-line
      mileage for the freeway.
- [x] **The printed table is the reporting table**: every corridor (no truncation), its
      total row ranked, its full name, then each of its direction × peak cells beneath,
      with the combining rules and the rate-vs-total gap explained under it. The
      single-worst-window view from Item 40 follows it. `corridor_peak_totals.csv` and
      `corridor_breakout.csv` join the outputs; the provenance records `ranked_on`,
      `totalled_windows` and the couplets.
- [x] **Caught a regression in the rewrite**: with the per-direction table replaced by
      the breakout, an **ungrouped** catalogue would have printed nothing but its
      findings. The per-direction table is now the fallback when no reporting corridors
      are declared, with a test that pins it.
- [x] pytest (**+12**): `test_screen.py` +8 — the cell sum; miles counted once per
      direction; **a three-corridor fixture on which the three rankings give three
      completely different orders** (`long` 40 mi / 4,000 vh, `short` 1 mi / 500 vh,
      `busy` 10 mi / 8,000 vh → total `busy>long>short`, rate `short>busy>long`,
      vh/mi `busy>short>long`), which is what makes them worth distinguishing; the
      all-null rank metric; the couplet flag changing nothing; peak-window selection and
      an explicit `windows=`; the breakout's index and its cells summing to the total;
      and the ranked ordering. `test_run_district_screening.py` +4, including the
      fallback firing and announcing itself when no AADT is joined. **Full suite: 540
      passed, 2 skipped** (was 528/2).
- [x] DATA_FORMAT.md, README, DESIGN_HISTORY Session 50; check these boxes off.

*Suggested prompt (done):* "Do Item 41 of ROADMAP.md — the reporting table, every
direction and peak as its own row under a corridor total ranked on a per-mile rate."

---

## 42 — Reconcile the export's segment set against the corridor inventory ✅ (Session 52)

**Target: Sonnet-eligible** on the reconciliation; **Opus** for the `join_aadt` preference
fix. Small. Any re-download has a long external lead time, so settle the list first.

**The prior inventory is sound, and an earlier pass of this session said otherwise —
wrongly.** `out/highways/` already holds a per-corridor segment-id list for every D3 state
route, and `District_3_ALL_Highways.txt` collects **3,947** ids. Against the export's
3,905 observed segments:

| | segments |
|---|---|
| requested **and** returned data | 3,905 |
| requested, returned **nothing** | 42 |
| observed but **not** in the list | **0** |

Nothing was observed that the list did not ask for. In particular **SH-55 Karcher Rd was
fully requested and fully returned — 92 of 92 segments.** The claim earlier in this
session that Karcher was missing came from searching XD's `RoadName` (46 segments carry
the string "Karcher", only 2 of them a `RoadNumber` of 55) rather than the corridor list,
and it was wrong. The same correction applies to the "354 miles of missing arterial"
framing: those are off-system county roads and correctly excluded.

**What is genuinely absent is 99 segments, and the two halves have different causes:**

- **SH-55 Eagle Rd — 57 segments, 18.86 mi — never requested.** They are in the
  per-corridor working file `out/highways/SH-55_ALL.txt` (487 ids) but did not make it
  into `District_3_ALL_Highways.txt` (430 of them did). Untried, and 18.9 miles of the
  district's busiest arterial.
- **I-84B Caldwell — 42 segments, 14.81 mi — requested, no rows returned.** The sibling
  file `District_3_ALL_Highways_No_Caldwell_I84B.txt` holds exactly the other 3,905 ids,
  which is precisely the observed set — so this corridor was tried, came back empty, and
  was cut from the query. Re-requesting probably returns nothing again. Neither group is
  the sub-100-ft stub that usually comes back empty (medians 0.26 and 0.32 mi), so "no
  INRIX coverage" is a hypothesis to test, not the answer.

Scope:

- [x] **Reconcile every `out/highways/*_ALL.txt` against the master list** and record which
      per-corridor ids never reached it. SH-55 is the one found so far; the check is cheap
      and should be run over all 32 files rather than assuming it is the only one.
      *Done by `scripts/reconcile_export_segments.py`: 4,004 ids across the 32 files,
      3,947 in the master, 3,905 observed, and the 57 SH-55 Eagle Rd ids are the only
      ones that never reached the master. Every directional file adds up to its `_ALL`.
      A finding on the way: the export is split **by segment**, each part's
      `metadata.csv` listing only its own — the store answers 1,947 to `load_metadata`
      against 3,905 observed, so `store.area_segments` (new) reads the segment set from
      the observations.*
- [x] **Resolved with the owner: the true gap is 7 segments, not 99.** The 57 SH-55 Eagle
      Rd segments were deleted **on purpose** — 38 are south of I-84 and belong to ACHD,
      14 are north of State St, and the remaining 5 are edge stubs just beyond each end of
      the SH-55 extent. The 42 I-84B Caldwell segments were excluded **on purpose** too:
      that corridor was relinquished to the City of Caldwell about a decade ago. What was
      missed in excluding it is that **SH-19 between I-84 and Simplot Blvd is still
      carried in the XD data as Centennial Way with `RoadNumber` 84** — classified as
      I-84B — so it went out with the rest. Those 7 segments (1.89 mi) are the only ones
      to add, and they are in `out/segments_to_add_to_export.txt`, paste-ready. The
      Blaine/Cleveland/Caldwell Blvd remainder stays out.
- [x] **Fix `join_aadt`'s route-class preference** — done, but **not as specified**, and
      the difference is recorded in DESIGN_HISTORY Session 52. Preferring a numbered
      record ahead of distance or coverage was measured on D3 and is wrong (Ustick Rd
      takes `FRANKLIN RD US-20 IC#29`'s 74,500; W Emerald St takes `COLE RD IC #1B`'s
      82,000). The real instability was that the **last comparison was the record's row
      position in the layer** — shuffling the layer moved 9 of the 3,905 export
      segments' AADT. Route class went in as the **last decision, under coverage**,
      replacing the index; the join is now provably order-independent. Also:
      `record_route_number` reads the route an `OH`-band record names for itself
      (`KARCHER RD (SH-55)`), 330 D3 rows' worth.
- [x] **Regenerate the candidate list after the fix** and review it by road. *It shrank
      a long way, but the preference is not what did it: on-system-ness cannot be read
      off the volume join at all, which is why the 483 were junk. New
      `aadt.classify_on_system` adds an **identity** test (the record's description
      names the same street, or the segment names a route itself) — without it, 764
      segments / 223 mi classify on-system off the export; with it, **26 segments /
      8.77 mi**. Reviewed by road: Centennial Way (7, the confirmed SH-19 addition,
      found independently) accept; **W/E State St in Eagle (11 segs, 4.68 mi) and Blaine
      St (1) are the two questions for the owner**; Caldwell/Cleveland Blvd (5) and the
      W Karcher Rd interchange stubs (2) reject. Full output in
      `out/export_reconciliation/`.*
- [x] **Decide whether any of this justifies a re-download** — **no, and it does not
      need one.** The confirmed gap is 7 segments (1.89 mi), at most 19 if State St and
      Blaine come in, against 91.05 M rows / 2.1 GB already held. Because the export is
      split by segment, a **supplemental** export of just those segments over the same
      span (2026-01-01..2026-09-01, 15 min) is the same shape as another part and
      ingests into the same area — ~0.2% of the rows already held. The span has to
      match or the new segments cannot be compared with the rest.
- [x] **Record what I-84 Business is in this vintage**: ITD's AADT layer classes Caldwell
      Blvd and Cleveland Blvd under an `IN084` RouteID, so it is I-84 in the route
      inventory and has no `84B` route number anywhere in the XD attributes. A future
      search for "84B" will fail exactly as this one did. *In DATA_FORMAT, with the one
      place the name does appear — a description parenthetical (`CALDWELL BLVD(I-84
      BUS)`), which `record_route_number` deliberately refuses to read as route 84.*
- [x] pytest for the route-class preference; DATA_FORMAT; DESIGN_HISTORY. *+20 tests
      (`test_aadt` 11, new `test_reconcile_export_segments` 8, `test_store` 1); suite
      560 passed, 2 skipped.*

**The seven landed** (Session 53). The owner ordered them and
`Cent_2026-01-01_to_2026-09-01_15_min_part_1.zip` is ingested: 163,268 rows over the
district export's own span, the store **3,905 → 3,912** segments, "requested, returned
nothing" 42 → 35. The export labelled itself corridor `"Cent"`, which under the area
model would have made it an area no district run ever looks at, so
`store.ingest_export_streaming` gained `corridor_name=` — it relabels as the rows are
staged and records the rewrite in the provenance. The reporting ranking is unchanged to
floating point (the seven belong to no catalogue entry — **there is no SH-19 corridor**,
which is Item 44's business).

**Open for the owner (Item 44 inherits it):** W/E State St in Eagle — 11 segments /
4.68 mi, the only candidate no corridor list has ever named — and Blaine St in
Caldwell (1 segment, connects to the now-ingested SH-19 run). Both are laid out with
their evidence in `out/segments_to_add_to_export.txt`.

*Suggested prompt (done):* "Do Item 42 of ROADMAP.md — reconcile the per-corridor id files
against the export, re-request the 99 absent segments, and give `join_aadt` a
numbered-over-OH route-class preference."

---

## 43 — Corridors extracted from recurring congestion, not drawn from landmarks

**Target: Opus.** Pure core (`screen.py` + `corridors.py`). Runs on today's export.

This is the item that changes what the catalogue *is*. The current 20 entries were
hand-derived from junctions and city limits, and the consequence is visible in the
ranking: SH-45 is carried as one 17.4-mile corridor of which only the 4.4-mile 12th Ave
section through Nampa is urban, so its congestion is diluted by three times its length of
rural highway — which is precisely what a per-mile rate is meant to prevent, and cannot
when the extent is wrong. The owner's requirement is that **extents come from the
congestion**: contiguous-ish runs of segments that are *recurrently* congested, with a
junction or a city limit used to tidy an endpoint only when the data already lands near
one. Scope:

- [x] **Recurrence is the criterion, and it is not the mean.** `segment_screen` averages
      over the whole date range, so a fortnight of construction and a daily queue look
      alike. Add a per-**day** reduction — segment × window × local calendar day, over or
      under a delay/TTI threshold — and define recurrence as the **share of weekdays**
      the segment is over it. A corridor is a run of segments that are congested *most
      days*, and the share is reported per segment so the threshold can be argued with.
- [x] **Walk the runs, never sort them.** Extend contiguity along the repaired
      `NextXDSegI` (Item 38) through qualifying segments and emit each maximal run. This
      is the one place a geographic sort would be tempting and it is the thing Item 36
      exists to have banned — a run is a chain or it is two runs.
- [x] **"Contiguous-ish": a gap tolerance, reported not hidden.** One free-flowing
      segment between two congested runs should not split a corridor; allow a tolerance
      in segments or miles, and return how many gaps each run bridged and their total
      length, so the tolerance is visible in the output.
- [x] **Endpoint tidying, with the distance stated.** After a run is found, look for a
      meaningful landmark near each end — a junction with another state route, an
      interchange, a city limit — and snap to it **only within a stated tolerance**,
      returning `snapped_to` and `snap_distance_miles` for each end. An end that has no
      landmark within tolerance stays where the data put it, which is the whole point.
      A run that is snapped half a mile is a decision; a run that is snapped 50 feet is
      tidying, and the column is what tells them apart.
- [x] **Directional pairs.** A run found NB should look for its counterpart over the same
      ground SB (Item 40's reporting corridors are two directions) and **say when it does
      not find one** — a one-direction run is a finding about the road or the data, not
      half a corridor to be quietly completed.
- [x] **Emit candidates in the catalogue's own shape**, carrying `start_latlon` /
      `end_latlon`, the measured recurrence and metrics, and the label read off the
      members (`corridors.chain_description` — the outside pass hand-wrote a route number
      that does not exist in Idaho). Deliberately **no `description`**: that is the field
      `parse_catalogue` refuses to accept empty, so a candidate cannot become a catalogue
      entry until a human writes down why that extent is meaningful.
- [x] **Stay on-system.** Candidates are drawn from the export's segments, which Item 42
      defines; nothing here should surface a county arterial.
- [x] pytest: a synthetic corridor congested in its middle recovers exactly that middle,
      not the whole route; a segment congested on 3 days of 20 does **not** qualify while
      the same mean spread over every day does; a one-segment gap bridges only within
      tolerance; a run does not continue past a carriageway change; an endpoint within
      tolerance of a junction snaps and reports the distance while one beyond it does
      not; candidates are rejected by `parse_catalogue` until described.
- [x] DATA_FORMAT (recurrence definition and thresholds); DESIGN_HISTORY.

*Suggested prompt:* "Do Item 43 of ROADMAP.md — extract corridor candidates as contiguous
runs of recurring congestion, with a gap tolerance and endpoint snapping."

---

## 44 — Rebuild the D3 catalogue from the extracted runs

**Target: Opus**, possibly two sessions. **Depends on Item 43**. Item 42 settled the
segment set: the export is complete bar **7 confirmed SH-19 segments** (and two open
questions, W/E State St in Eagle and Blaine St), and no re-download is needed — a
supplemental part would carry them.

The catalogue becomes data-derived: Item 43 says where the congestion actually starts and
stops, a human says why that extent is the meaningful one and what to call it. The
existing 20 entries stop being the catalogue and become a **check** on it. Scope:

- [x] **Run Item 43 over the whole on-system export** and triage the candidates: accept,
      merge, or reject with a reason. The rejections are as interesting as the
      acceptances and should be recorded, not dropped.
- [x] **Compare against the current 10 reporting corridors.** Where an extracted run
      disagrees with a hand-drawn extent, the presumption is that the data is right about
      the *ends* and the hand-drawn entry is right about the *name* — but check each
      disagreement rather than applying that as a rule. I-84 and Eagle Rd should come back
      close to what is there now; **SH-45 should split at Nampa** and **SH-55 north of
      State St** (299 segments / 219 mi, fully in the export today) should break into
      several extents, most of them ranking near zero. **SH-55 Karcher Rd is rankable
      today** — all 92 of its segments are in the export — and has no entry; it should
      pick one up here if the congestion supports one.
- [x] **The unranked routes get their extents from the data too** — US-95 (345.7 mi in the
      export), SH-21 (200.5), SH-51 (184.7), SH-78 (183.6), SH-52 (107.6), SH-71, SH-19,
      SH-167, SH-30, SH-67, SH-72 — roughly 1,190 miles with no catalogue entry at all.
      Expect most of it to produce **no** candidate, which is the correct answer for a
      rural state highway and is itself worth recording.
- [x] **Keep the reporting-corridor grouping honest as it grows**: `corridor` +
      `direction` on every entry, and both directions per group or a stated reason.
      **A second one-way couplet is already known** — I-84 Business through downtown
      Nampa runs westbound on 2nd St S and eastbound on 3rd St S, all 35 segments in the
      export — so `one_way_couplet` is not a Boise-only flag and any I-84B Nampa entry
      needs it set. Watch for more; the earlier claim that Myrtle/Front was the district's
      only couplet was wrong.
- [x] **Re-run the district screening and record how the ranking moves.** Ten corridors
      over 9.4% of the store is not a district screen; the number that replaces it is the
      deliverable.
- [x] pytest for any new validation; DESIGN_HISTORY with the new ranking and the triage.

Delivered: Rebuilt `scripts/d3_corridors.json` via `scripts/rebuild_d3_catalogue.py`, expanding District 3's screening catalogue from 20 directional entries / 10 reporting corridors to **36 directional entries / 18 reporting corridors**. All 36 entries resolve 100% via `corridors.build_chain` with link repairs (`reached_target=True`, coverage 95.7%-100.0%, mean 99.8%). Incorporates engineering rules and network corrections:
- **Congestion Congruence Rule & Alignment Refinements**:
  - **SH-44 Glenwood Alignment & Urban/Rural Split**: Corrected alignment so SH-44 turns south along Glenwood St to terminate at Chinden Blvd (`sh44-urban`, 13.06 mi, 34 segs; State St east of Glenwood is local ACHD arterial). Stitched across Eagle Rd per congruent delay density (~56-67 vhd/mi) and minimum-length rule (Glenwood-Eagle is < 3 mi). Split at Star Rd where congestion drops sharply into rural baseline (`sh44-rural`, 10.65 mi, 22 segs, TTI 1.08, ~15 vhd/mi). SH-44 Urban ranks **#6 in District 3** (319 vhd/mi, 4,171 PM veh-hrs); SH-44 Rural ranks **#11** (100 vhd/mi).
  - **Chinden Blvd Extension**: Extended to full extent from SH-16 to I-184 Connector (`us2026-chinden`, 14.10-14.45 mi, 27-28 segs) stitching across Eagle Rd due to twin congestion levels (TTI 1.25 west vs 1.27 east). Ranks **#7 in District 3** (261 vhd/mi, 5,330 PM veh-hrs).
  - **Broadway Ave**: Added official 3.0-mile south Boise arterial (`broadway`, 14-15 segs) from I-84 IC 54 past BSU to Front/Myrtle couplet. Ranks **#8 in District 3** (215 vhd/mi, 1,239 peak veh-hrs).
  - **SH-55 at Avimor**: Empirical VHD analysis demonstrated negligible commute delay south of Avimor (4.0 vhd/mi, 28 veh-hrs), confirming unity of State St to Horseshoe Bend (`sh55-eagle-hsb`, 18.9 mi).
- **Candidate Triage Audit Trail**: Congestion candidate extraction (`scripts/triage_candidates.py`) triaged 319 candidates across 3,912 segments and 54.7M peak observations: 8 accepted as empirical backbones, 101 merged into larger corridors (subsuming isolated signal queues per the ~3-mile minimum rule), and 210 rejected (isolated queues on rural routes like US-95, SH-21, SH-51, SH-78, ramp stubs, unnumbered facilities), preserved in `out/district_screening/candidate_triage.csv` and `.json`.
- **Other Key Outcomes**:
  - **SH-55 Karcher Rd** (`sh55-karcher`, 3.01 mi): ranks **#5 in District 3** at 420 vhd/mi (2,109 peak veh-hrs delay).
  - **SH-45 urban/rural split**: urban Nampa (`sh45-nampa`, 4.36 mi) ranks **#10** at 144 vhd/mi; rural control (`sh45-rural`, 13.07 mi) ranks **#18** at 6 vhd/mi (TTI 1.04).
  - **Downtown Nampa couplet** (`nampa-couplet`, 2nd St S WB / 3rd St S EB, 0.70 mi): ranks **#12** at 67 vhd/mi (`one_way_couplet: true`).
  - **SH-55 Mountain Highway Sections**: 4 extents covering 225 directional miles (`sh55-eagle-hsb`, `sh55-hsb-cascade`, `sh55-cascade-mccall`, `sh55-mccall-newmeadows`) rank near zero (10-19 vhd/mi, Ranks 14-17, TTI 1.03-1.06, establishing rural baseline).
- **District Screening Pipeline Re-Run**: Screened footprint expanded to **358.3 directional miles / 723 segments (18.5% of store)**. All 36 directional entries resolve and rank with 0 unranked/findings. Outputs updated in `out/district_screening/`.
- **Tests**: Full test suite passing (**592 passed, 2 skipped**).

*Suggested prompt:* "Do Item 44 of ROADMAP.md — rebuild the D3 catalogue from Item 43's
extracted runs, and re-run the district screening."


---

## 45 — Statewide Corridor Extent Alternatives, Couplet Synthesis, and Objective Triage ✅ (Sessions 61–62, completed by Item 46 / Session 63)

**Target: Opus / Automated Pipeline.** Builds on Items 40, 43, and 44. Statewide scope (Districts 1–6).

When scaling from District 3 to the entire state of Idaho, local highway intuition is sparse. The corridor screening pipeline cannot rely on hand-drawn endpoints or route-specific hardcoding. Instead, the data itself must propose corridor extents, synthesize one-way couplets, generate hierarchical alternatives (core vs. commuter vs. regional baseline), and triage candidate runs using objective, statewide rules. Scope:

- [x] **Objective Split Criteria for Corridor Extents** ✅ *(module in
      `src/inrix_tools/extents.py`; wired into the catalogue builder by Item 46,
      Session 63 — the D1/2/4/5/6 catalogues are cut at these split points)*:
      Formalize four orthogonal data-driven split dimensions along each linear state highway chain:
      1. **City Limits & Urban/Rural Transitions**: Detect crossings using ITD Urban Adjusted Boundaries (UAB) / municipal polygons, corroborated by sudden shifts in segment density, speed limits, and functional road classification (FRC). Prevents dilution of concentrated urban arterial bottlenecks by long rural tails (e.g. SH-45 Nampa vs. Melba/Owyhee).
      2. **Major Highway-to-Highway Junctions & Interchanges**: Graph-topological splits where state routes cross or merge (degree $\ge 3$) and system interchange terminals (e.g. I-84/I-184, US-95/US-2, US-91/US-30, I-15/US-26), where commuter demand profiles bifurcate.
      3. **AADT Volume Step-Changes**: Detect sharp volume gradients ($|\Delta\text{AADT}| / \text{AADT} > 40\%$ or crossing ITD volume tiers) along the chain, separating high-volume metropolitan commuter sheds from intercity corridors.
      4. **Congestion Discontinuities (TTI & Delay Rate)**: Segment-level change-point detection identifying where recurring peak TTI drops below 1.15 or delay density drops from bottleneck levels ($> 150$ vhd/mi) to free-flowing baseline ($< 25$ vhd/mi).
- [x] **Multi-Scale Extent Alternatives & Comparative Ranking** ✅ *(Item 46,
      Session 63: `aggregate_statewide_rankings.load_extent_tier_groups` reads the
      tiering out of the generated catalogues — **36 facilities / 80 tier rows**,
      against the 6 hand-listed facilities `EXTENT_TIER_GROUPS` carried)*:
      Rather than forcing a single arbitrary extent per route, automatically construct and rank three standardized extent alternatives per congested corridor:
      - **Tier 1: Congested Core (Empirical Hotspot)** — The contiguous recurring congestion run snapped to nearest major cross-streets; maximizes delay rate (VHD/mile).
      - **Tier 2: Commuter Corridor (Functional Facility)** — Stitched across moderate-delay gaps between regional junctions or city limits; evaluates trip-level reliability and corridor-wide travel time.
      - **Tier 3: Regional Highway Baseline (Full Facility / Rural Control)** — Full county/district extent; provides total delay volume context and establishes the non-congested baseline.
      - **Comparison Matrix**: Report all alternatives side-by-side ranked by Rate (Peak VHD/mi), Volume (Total Peak VHD), and Reliability (95th percentile TTI), explicitly highlighting the "dilution factor" of extended lengths.
- [x] **Statewide One-Way Couplet Detection & Pairing** ✅ *(Item 46, Session 63: the
      shipped couplet entries are `detect_couplets` output; the registry is validated
      by `match_known_couplets`, 9 of 16 matched on street names — see the table below)*:
      Couplets are directional carriageways on parallel, separate street alignments (e.g., Boise Myrtle/Front, Nampa 2nd/3rd St S). Codify an automated topological detector:
      1. **Geometric Signature**: Opposing one-way segments on distinct street alignments separated by $30\text{ m} \le d \le 300\text{ m}$ (1–2 city blocks) running parallel for $\ge 0.2$ miles.
      2. **Topological Split/Merge**: Branch-and-converge cycles in the directed highway graph where a two-way mainline bifurcates into two opposing one-way links and rejoins downstream.
      3. **Corridor Synthesis**: Automatically pair detected couplet branches under a single reporting corridor ID, assigning matching directional tags (`one_way_couplet: true`) and resolving them in `corridors.resolve_catalogue`.
      - **Statewide Couplet Registry**: Validate against the statewide catalogue:
        - **D1**: Sandpoint US-2/US-95 Byway & downtown split; Coeur d'Alene 3rd/4th St (I-90 connector).
        - **D2**: Moscow US-95 (Jackson St SB / Washington St NB); Lewiston US-12 (Main St EB / D St & 1st St WB).
        - **D3**: Boise US-20/26 (Front/Myrtle); Nampa I-84B (2nd/3rd St S); Caldwell I-84B/SH-19 (Canyon/Blaine/Cleveland); Mountain Home I-84B/SH-51 (Main/American Legion/8th/Jackson); Weiser US-95 Spur (Idaho/Main).
        - **D4**: Twin Falls US-30 (2nd Ave North WB / 2nd Ave South EB).
        - **D5**: Pocatello I-15B/US-91 (4th Ave NB / 5th Ave SB); Blackfoot I-15B (W Bridge St WB / W Judicial St EB — clarifying D5 Bingham County administration vs. D6/US-20); Preston US-91/SH-34 (State St / 800 W); American Falls SH-39 (Idaho St / Lamb Weston Rd).
        - **D6**: Idaho Falls & Rexburg grade-separated splits / business loops.
- [x] **Objective Generalization of Triage Code (`triage_candidates.py`)**:
      Refactor `triage_candidates.py` to eliminate D3-specific hardcoded route lists (`in ("78", "51", ...)` and `CORE_ACCEPTED_RUN_CHECKS`), replacing them with rule-based criteria:
      - **ACCEPTED**: Run length $\ge 1.0$ mile, recurrence $\ge 0.50$, peak TTI $\ge 1.20$, on a designated state highway mainline forming an empirical corridor extent.
      - **MERGED**: Contiguous or bridging candidate subsumed into an extent across minor signal gaps ($\le 0.5$ mi) or matching an active corridor chain.
      - **REJECTED**: Explicitly audited rejections categorized by rule:
        - *Rule R1 (Topology)*: Ramp stubs, turning loops, or unnumbered local roads outside the state system.
        - *Rule R2 (Isolated Signal Queue)*: Queue length $< 0.35$ miles at an isolated rural junction without upstream corridor congestion.
        - *Rule R3 (Geometric Delay)*: Low free-flow speed on mountain passes or tight curves without commute delay or volume.
      - Output comprehensive, machine-readable audit tables (`candidate_triage.csv` and `.json`) with exact rule IDs and metric thresholds.
- [x] **Automated Integration & Reporting**:
      Provide a CLI runner (`scripts/run_statewide_screening.py` or generalized `run_district_screening.py`) that accepts any district or statewide DuckDB store, executes candidate extraction, generates multi-scale extents and couplet pairings, triages candidates, and exports comparative ranking tables and interactive maps.

**Status after Session 60 (review pass).** The two rule-based pieces — the triage
generalization and the CLI runner — landed and drive the shipped output. The three
*generative* pieces did not: `extents.py` and `couplets.py` were written and unit-tested
but imported by nothing outside their own tests, and the D1/D2/D4/D5/D6 catalogues in
`scripts/build_statewide_catalogues.py` were built from hand-written lat/lon hints per
district. Item 46 was added to close that gap.

**Closed by Item 46 (Session 63, 2026-09-22).** The five catalogues are generated:
**194 directional entries / 100 reporting corridors, 194/194 reaching target and
194/194 accepted** through `corridors.resolve_catalogue`, against 56/28 hand-built.
The couplet registry is validated for the first time (8 `streets`, 1 `one_street`,
2 `route_county`, 5 `none` — two of the misses are registry entries the XD network
carries no route number for). The validation table ships as
`out/statewide_screening/couplet_registry_validation.csv`. See DESIGN_HISTORY
Session 63 for what the generated extents moved and the four hand-built corridors
the measurement does not support as congested.

---

## 45R — Review fixes: the statewide VHD/mile join (Session 60) ✅

Found by review of the Item 42–45 branch: every statewide segment rendered at 0 VHD/mi.

- [x] `generate_statewide_maps.load_statewide_data` joins AADT per segment instead of
      passing the raw ITD route-measure layer cache, and concatenates the *joined* frames
      (the old positional-index dedupe dropped 6,510 of 9,317 rows). New `--aadt` /
      `--aadt-year`; the VHD maps are skipped rather than zero-drawn without a source.
- [x] Unmatched AADT stays **NaN** through `_segment_tti_frame` rather than being
      zero-filled, and gets a `No AADT Data (unvolumed)` map tier — a dead join can no
      longer read as "everything is free-flowing". Tooltips print `n/a` via `_fmt_or_na`.
- [x] Verified: statewide non-low segments (2,017) now equal the sum across D1–D6 (2,017).
      Re-run that equality if this regresses.
- [x] `tests/test_district_inventories.py` skips when `out/` is absent (it asserted on
      gitignored artifacts and failed on a clean checkout); D2 timezone counts replaced
      with a partition assertion.
- [x] `aggregate_statewide_rankings.build_couplet_analysis` no longer raises
      `KeyError: False` when `one_way_couplet` is missing.
- [x] `generate_screening_maps.py` runs the full-network AADT join before the
      corridor-bounds one (shared cache, `load_aadt` ignores bbox on a hit). Root cause
      is Item 47.
- [x] `extents.detect_urban_rural_splits` scores on `abs(frc_prev - frc_curr) - 1`, so a
      boundary no longer scores differently depending on the chain's direction.

---

## 46 — Wire the extent/couplet detectors into the statewide catalogue builder ✅ (Session 63)

**Target: one session.** Depends on Item 45 (the modules exist and are tested).

`src/inrix_tools/extents.py` and `src/inrix_tools/couplets.py` **were** dead code:
`build_statewide_catalogues.py` imported `couplets` and never called it, and the five
district catalogues were hand-drawn coordinate hints in
`build_district_{1,2,4,5,6}_catalogue`. That is exactly the hand-specification Item 45
existed to remove, and it does not scale to a re-run on a new export. Scope, all
delivered in Session 63:

- [x] **Replaced the hand-written hints with a generated pass.** New pure-core
      `extents.enumerate_mainline_chains` (walks `NextXDSegI` within one route number, so a
      chain ends where the route ends), `pair_chains` (route identity **plus** proximity —
      matching on route alone marries Coeur d'Alene's US-95 northbound to Bonners Ferry's
      southbound), `mirror_extent` (the opposing direction's extent is projected onto it,
      not analysed separately, or the two halves of a corridor end at different
      cross-streets) and `generate_catalogue`. `build_statewide_catalogues.py` is now a
      ~200-line runner over the core instead of 981 lines of coordinates. Every entry's
      `description` carries `split_rationale` **and both bounding splits**, rendered by the
      new `describe_split`: *"Upstream boundary: an AADT step-change (34,000 -> 11,000 vpd,
      68%); downstream boundary: a highway junction (crossing 53)"*.
- [x] **Couplet entries are detector output, and the registry is validated.** The detector
      found **2** couplets statewide as written and missed Boise's Myrtle/Front; three
      structural causes were fixed — pairing required exact cardinal opposition (XD codes
      Front St's westbound carriageway `Bearing = "N"`, and the declared
      `bearing_tolerance_deg` was never used; pairing is now anti-parallel on the chain's
      *geometric* heading), a candidate was a whole `XDGroup` chain (Boise's westbound
      US-20/26 runs up Broadway Ave first, so the length filter threw it out — candidates
      are now **street runs**), and `street_key` stripped the trailing quadrant, merging
      Twin Falls' `2nd Ave N`/`2nd Ave S`. **17 couplets now ship in the five generated
      catalogues**; six more are detected in District 3 for the registry check, whose
      catalogue Item 44 owns. New `couplets.match_known_couplets`
      scores the registry and **reports rather than asserts** — two registry entries name a
      leg the XD network carries no `RoadNumber` for, and a detector tuned until those
      passed would be tuned to the wrong target.
- [x] **`EXTENT_TIER_GROUPS` is gone**, replaced by `load_extent_tier_groups`, which reads
      `_facility` / `_tier_number` / `_tier_label` off the generated catalogues'
      reporting corridors: **36 facilities and 80 tier rows** against six hand-picked
      facilities. District 3 is absent *and the run says so* — its catalogue is the Item 44
      empirical rebuild, so it carries no tier metadata and is not back-filled by hand.
- [x] **`legacy/handbuilt_catalogues/`** holds the old builder, the five hand-built
      catalogues at `e0fec0b`, and a README. The mapping is recorded in DESIGN_HISTORY
      Session 63: **14 of the 28** hand-built corridors are ≥80% covered by a generated
      extent; **5** have no generated counterpart, and four of those are measurably not
      congested (I-84 Twin Falls peak TTI 1.050, I-15 Pocatello 1.017, I-15 Idaho Falls
      1.084, US-20 Idaho Falls–Rexburg 1.146 — none with a single segment at ≥1.20). Only
      `us20-if-urban` is arguable: it clears 1.20 on two segments but over a core under the
      0.75-mile floor.
- [x] **The gate was met before the Item 45 boxes were re-checked: 194/194
      `reached_target` and 194/194 `accepted`**, with zero findings in every district's
      `screening_provenance.json`. Getting there needed `extents.segment_endpoint`: XD
      record `1187395985` declares a start **294 m from its own geometry**, so an entry
      written from `StartLat`/`StartLong` snapped onto a piece of SH-43 776 ft away.
      Endpoints are now taken from the geometry, with the declared values deciding only
      *which* terminal. `couplets.couplet_catalogue_entries` had the same bug and now
      shares the function.

**Two things found along the way that were not in scope and are worth knowing:**

- **A `#` in a generated name nulled its own CSV row.** Endpoint names come from the AADT
  layer's `Descriptio` (`W POST FALLS IC #5`) — the only cross-street naming available
  offline. Every CSV here carries a `# key: value` provenance header, and
  `pd.read_csv(comment="#")` treats a `#` **anywhere** in a line as a comment start, so six
  of District 1's sixteen corridors arrived downstream as rows of nulls. Fixed at both
  ends: `#` is written as `No. `, and `load_district_table` skips the header by counting
  its leading `#` lines. The second half is the one that matters for any future value.
- **Reading one peak window dropped real corridors.** The first pass read PM only and
  produced nothing for US-20 Idaho Falls–Rexburg, an **AM** inbound commute. New
  `extents.worst_window_tti`; `--window` now defaults to `am,pm`. Recovered three
  facilities (D1 7→10, D2 6→7, D6 9→10).

**Known limitation, not papered over:** splitting couplet candidates by street run reports
a couplet that changes street name mid-way as two half-couplets (Twin Falls' US-30 as
`2nd Ave E/2nd Ave S` 0.77 mi and `2nd Ave N/2nd Ave W` 0.56 mi; SH-43 in Bonneville County
likewise). The halves are correct; merging them is not attempted.

**Tests:** +31 in `tests/test_extents.py`, +14 in `tests/test_couplets.py`. Full suite
**668 passed, 2 skipped**.

*Suggested prompt:* "Do Item 46 of ROADMAP.md — wire `extents.py` and `couplets.py` into
`build_statewide_catalogues.py` so the statewide catalogues are generated rather than
hand-drawn, and reconcile the result against the current hand-built ones."

---

## 47 — Screening-pipeline hardening: cache keying, hoisted joins, script hygiene ✅ (Session 64)

**Target: one session.** Small, independent cleanups found in the Session 60 review.

None of these change a number today; each is a trap that will change one later. Scope:

- [x] **The AADT layer cache is keyed on what it covers.** `load_aadt` writes a
      `<cache>.meta.json` sidecar recording the `year` / `bbox` / `columns` it was built
      for, serves the cache only when that covers the request, and otherwise rebuilds for
      the **union** of the two extents — so a cache shared by a corridor-bounds caller and
      a full-network one widens to serve both instead of thrashing.
      `attrs['aadt_layer']` reports `hit` / `written` / `rebuilt (<reason>)`. A cache with
      no sidecar is judged on the extent of the data it holds, which is conservative in
      the safe direction (a bbox-filtered read's features never reach past the bbox, so it
      rebuilds needlessly rather than under-answering). **Verified it was not already
      wrong:** on District 1 the cached 1,394-record layer and a fresh statewide
      8,301-record read give an identical join (4,652 matched, same 31,417,670 total), so
      Item 46's numbers stand.
- [x] **The junction adjacency map is built once.** New public
      `extents.incoming_route_map(network)`, built in `generate_catalogue` and threaded
      through `analyse_chain` → `detect_split_points` → `detect_junction_splits` as an
      optional argument (each still builds its own when not given, so the functions stand
      alone). District 1 catalogue generation **4.0 s → 1.19 s**, and all five catalogues
      regenerate byte-identical.
- [x] **`build_statewide_catalogues.py` hygiene — landed early, in Item 46 (Session 63)**,
      because both halves were load-bearing there. The rewritten builder writes a catalogue
      **only when every entry resolves** (a *generated* pass overwriting a good catalogue
      with a broken one is a worse risk than a hand-built one doing it), and
      `find_chain_endpoints` is gone with the rest of the hand-built builder —
      `extents._nearest_index` and `extents.mirror_extent` measure in a projected metric CRS,
      which is exactly the automatic reuse that made degrees-on-EPSG:4326 wrong.
- [x] **`io.discover_parts` is public.** It is not an implementation detail — it is *why*
      handing `load_data` or `ingest_export` any one `..._part_N.zip` ingests the whole
      export (Session 48). `_discover_parts` kept as an alias; `store.py`, the script and
      `tests/test_io.py` updated.
- [x] **`ruff --select F` is clean** across `src/`, `scripts/`, `tests/` **and** `gui/`
      (23 at the Session 60 review → 13 after Item 46 → 0). Three were dead *locals* rather
      than imports — `dr_med`, `t_type`, and `ids` in `geometry.offset_overlapping_segments`
      — each computed and never read.
- [x] **`tests/test_aadt.py` +12** on the cache keying: the sidecar records the coverage;
      a contained request hits; a wider one rebuilds and answers the *wider* question; a
      rebuild widens rather than narrows; an unrestricted cache serves any bbox and a
      restricted one does not serve an unrestricted read; a year mismatch replaces rather
      than widens; a sidecar-less cache is judged on its own data; a missing column
      rebuilds; the shortfall reasons name what is wrong.

**Two regressions the cache change exposed**, both in the *rebuild* path that nothing
had reached while the cache always won unconditionally — each now with its own test:

- a `.geoparquet` **source** went to pyogrio (`tests/test_reconcile_export_segments`
  legitimately passes one file as both source and cache), and GDAL's driver probe died
  on an unrelated broken DuckDB plugin. `load_aadt` reads a `.parquet`/`.geoparquet`
  source directly now.
- a bbox of `(nan, nan, nan, nan)` — what `geo.total_bounds` returns for an empty frame,
  which `reconcile_export_segments` produces when nothing is absent — reached shapely.
  `_clean_bbox` reads a non-finite box as *no restriction*, which is what the caller means.

**Verification that the ROADMAP's framing held** ("none of these change a number today"):
the statewide rankings before and after differ by at most **7e-12** on `vhd` — float
summation order, nothing else — with every rank, name and integer count identical.

**Tests:** `tests/test_aadt.py` +12. Full suite **680 passed, 2 skipped**.

*Suggested prompt:* "Do Item 47 of ROADMAP.md — the screening-pipeline hardening cleanups
from the Session 60 review."

---

## 48 — Route membership from ITD's AADT layer, not INRIX `RoadNumber` ✅ (Session 65)

**Target: one session.** Raised by the owner in Session 65 while reviewing the D2 export.

**The defect.** `scripts/generate_district_highway_inventories.py` (D1, D2, D4–D6) picks
a route's segments by INRIX `RoadNumber` alone. The AADT layer is used only later, for
volume. INRIX's route numbering is wrong in both directions:
- **Lewiston:** INRIX puts US-12 on the downtown Main St / D St couplet. Both streets are
  local (`OH`) records in ITD's layer (`06830AOH000`, `01900AOH000`, `47980AOH000`,
  `06820AOH000`). US-12 (`01910AUS012`) actually runs the levee bypass. INRIX names it
  "Levee Byp" with no route number, so ~3.3 mi (12 segments) were never downloaded.
  `couplets.KNOWN_COUPLETS` also lists Main St/D St as a "validated" US-12 couplet, so
  the D2 catalogue holds two false couplet corridors.
- **Session 65 audit** (a scratch join of every district segment to its AADT record,
  keeping only matches ≤10 m apart, ≥80% overlap, main road only):
  - *Exported as a state route but ITD shows a local road:* Coeur d'Alene Northwest
    Blvd / Sherman Ave (I-90, ~6 mi); Kellogg Bunker Ave / Division St (I-90); Sandpoint
    Pine St / Cedar St (US-2); Caldwell Cleveland / Blaine (I-84B, already recorded in
    Item 42); Nampa Northside / Karcher (SH-55); SH-37 south of the SH-38 junction in
    Oneida (27.6 mi); Rexburg Center St / 7th E (SH-33).
  - *On a state route in ITD's layer but unnumbered in INRIX, so never selected:* D2
    Levee Byp (US-12, 3.3 mi); D4 Elba-Almo Hwy (SH-77, ~33 mi); D4 Sun Valley Rd
    (SH-75, 4.8 mi). Each district also has short gaps under 0.2 mi.
- **The AADT layer is not always the newer source.** D2 Reisenauer Rd (10.1 mi) matches
  a US-95 record in ITD's layer, but the owner confirms INRIX is right: US-95 moved to a
  new alignment within the past year, and the old one was renamed and handed to the
  county. The AADT layer is therefore authoritative for route identity, but not newer
  than a recent realignment. The rule needs a reviewed escape hatch.
- **Shared roads are not errors.** Where two routes share a road, ITD records only one
  route number: US-20/26/93 near Arco and Carey, US-2/95 at Bonners Ferry, US-20 on
  I-184. A segment whose INRIX number differs from its ITD number is still on the state
  system. Most of the raw mismatch count in the audit is this case.

Scope:

- [x] **Pure-core route membership** (`aadt.py`, or a new `routes.py`): each segment's
      ITD route and a verdict — `agree` / `concurrent` / `inrix_only` (INRIX numbers a
      route, but ITD shows a local road) / `itd_only` (ITD route, but INRIX has no
      number) / `no_record`. Name agreement cannot be the identity test here: AADT
      descriptions name the *cross street* at each break ("5TH ST"), so
      `classify_on_system`'s street-name test rejects Levee Byp. Decide the test from
      distance, overlap and bearing, and record why.
      *New `routes.py`, with `business` added as a verdict. Identity is geometric: on
      ≤ 10 m and ≥ 80% alongside; near ≤ 40 m. It has its own search, because
      `join_aadt`'s route bonus prefers INRIX's number. Only the `RouteID` **band** is
      trusted: a route named in an `OH` record's description means "to that route".
      That bug first added 12 mi of Reubens-Gifford Rd to US-95.*
- [x] **Shared-road handling from the data:** decide `concurrent` from the ITD record's
      own description or route list where it names both routes. Otherwise use a small
      explicit table. Never read "any number mismatch" as an error.
      *From the segment's `RoadList`/`RoadName`, in every spelling INRIX uses (`N ID-34`,
      `Highway 30`); the record descriptions never name two routes. Business loops are
      banded like their parent route (`IN084` Caldwell Blvd, `US093` Twin Falls). So an
      `IN` band is never given to a segment INRIX doesn't number as that interstate, and
      a band the `RoadList` names only as `-BL`/`-BR` keeps INRIX's number.*
- [x] **An owner-reviewed override file** (e.g. `scripts/route_overrides.csv`: segment
      range or road, route, keep/drop, reason, date). The first entry: Reisenauer Rd is
      **not** US-95 (realigned ~2025, handed to the county). A segment INRIX numbers with
      no AADT record nearby (new construction) is kept and flagged, not dropped.
      *`scripts/route_overrides.csv`; a note is required. The new US-95 alignment's 14
      segments come out `unconfirmed` and are kept.*
- [x] **Rewire the inventory generator** to select by ITD route membership plus
      overrides, with INRIX `RoadNumber` as a backup only. Regenerate `out/highways/`
      for D1–D6 and report the per-route changes in miles.
      *Done for D1/D2/D4/D5/D6, reading `out/highways/route_membership/` (built by
      `scripts/build_route_membership.py`). Changes by road are in
      `route_changes_by_road.csv` and DESIGN_HISTORY Session 65. **D3 was not
      regenerated** — its lists are the owner-curated Item 42/44 set. D3 membership is
      computed and reported, and the decision is carried into Item 49. The old masters
      are kept in `out/highways/pre_item48/`.*
- [x] **Generalise Item 42's reconciliation to D1–D6** and write the add-list of
      segments to download (Levee Byp, SH-77 Elba-Almo, SH-75 Sun Valley Rd, the short
      gaps). The drop-list stays in the stores but out of the catalogues.
      *`--district` / `--previous-master`. The add-lists total **117 segments**: D1 4,
      D2 25, D4 84, D5 2, D6 2. They are paste-ready and labelled by road in
      `out/export_reconciliation/item48_add_lists.txt`. None of the earlier requests
      came back empty.*
- [x] **Fix `KNOWN_COUPLETS`**: remove Lewiston US-12 Main/D St. Check every other entry
      against ITD route membership (Coeur d'Alene `STC-7195` and Payette `US-95 Conn` in
      particular). Regenerate the D1–D6 catalogues so the false couplet corridors drop out.
      *Removed Lewiston, Coeur d'Alene STC-7195, Payette and Caldwell (all local in ITD's
      layer). The D1/D2/D4/D5/D6 catalogues regenerated and verified. D2 loses its three
      false US-12 couplet legs, D1 its six I-90 business-loop entries, and D6 four false
      SH-43 "couplet" legs.*
- [x] pytest: a synthetic bypass/couplet where the numbering is on the wrong street;
      a shared-road segment is not flagged; an override beats the layer; a numbered
      segment with no record is kept and flagged. DATA_FORMAT: INRIX `RoadNumber` vs ITD
      route identity, the cross-street descriptions, and the AADT layer lagging
      realignments. DESIGN_HISTORY.
      *`test_routes.py` +21 (including the real Lewiston case), reconcile +3, and a
      couplet-registry guard. Suite 705 passed, 2 skipped.*

*Suggested prompt (done):* "Do Item 48 of ROADMAP.md — select route segments by ITD AADT-layer
route membership with an override file, and fix the Lewiston US-12 couplet."

## 52 — ITD reference layers: the state highway system, AADT 2025, and urban areas ✅ (Session 67)

**Target: one session. Depends on Item 48. Do it before the rest of Item 49**, which
rebuilds the catalogues. Items 50 and 51 build on it. Scoped 2026-09-23 when the owner
supplied three ITD layers (gitignored fixtures in the repo root; the names are ours, the
ArcGIS Online download names meant nothing):

- **`SHS_Primary.zip`**: ITD's State Highway System, the authority for route identity.
  1,179 lines in EPSG:8826, ~6,000 mi. Fields: `RouteId` with `FromMeasur`/`ToMeasure`
  (a linear reference, so mileposts order a route), `SHSNumber`, `SignTypeCo` (1 = I,
  2 = US, 3 = SH; matches the `RouteId` band), `LoopSpurCo`, `RouteTypeC` (1: 1,018
  lines, 2/3/4: the rest, which include ramps), `RoadType`, and `Travelway` (`A` 1,110 /
  `D` 69; `D` looks like the second carriageway of a divided highway, e.g. `01010DIN084`).
  It has `FromDate`/`ToDate` (no `ToDate` set; latest `FromDate` 2026-07-28).
  **"Primary" means one route per road:** US-2/95 at Sandpoint is recorded as 95,
  US-20/26/93 as 93, ID-3 on SH-8 as 8. Concurrency is **not** in this layer.
- **`AADT_2025.zip`**: the 2025 year of ITD's AADT layer, 8,423 lines. It has the same fields as
  `Cumulative_AADT.zip` (1999–2024), so it drops into `aadt.load_aadt`. It carries US-95 on
  its new alignment south of Moscow (`01540AUS095`, 7,700 AADT). Reisenauer Rd is no
  longer US-95, and in the 2024 year it still was.
- **`Urban_Area.zip`**: 26 polygons carrying Census 2020 urban-area codes (`UACE`),
  population and density. Bonners Ferry, Kellogg and Salmon are not urban areas.

**What a scratch check found (2026-09-23).** Each D1–D6 membership segment was tested
against the state highway lines, needing ≥80% of its length within 12 m:
- Item 48 was right almost everywhere. 9,201 of the 9,255 "agree" miles are on the
  system. Only 19 of the 7,370 off-system miles are on it, and those are mostly ramps.
  **No new download is implied.**
- The Reisenauer override is confirmed: 10.5 mi, all off the system.
- **Sun Valley Rd (4.8 mi, Item 48 `itd_only`, on the add-list and now ingested) is not
  on the system.** It stays in the store and goes out of the catalogues.
- Levee Byp (US-12) and Elba-Almo (SH-77) are confirmed.
- Of Item 48's 187 `unconfirmed` miles, 135 are on the system, including the new US-95
  alignment. 52 are off: Old Highway 81 (14 mi), WY-89 (8 mi), Elmore Old Highway 30,
  Cleveland Blvd, Northside Blvd, Payette 7th Ave / S 7th St, Rexburg Center St / ID-33,
  Coeur d'Alene Sherman Ave and Kellogg Cameron Ave.
- 53 "agree" miles miss the 12 m buffer, in 1–7-mi pieces: W Chinden Blvd 6.6 mi,
  Idaho County US-95 3.9 mi, and others. These are probably alignment offsets; the
  session should check them.

Scope:

- [x] **Loaders in the pure core** for the highway system and the urban areas (in
      `routes.py` and a small new module, or one `itd_layers.py`). Handle the one
      `MultiLineString` and the dropped M values. Decide which `RouteTypeC` /
      `RoadType` codes are mainline, and what `Travelway` `D` means; record both in
      DATA_FORMAT.
      *New `itd_layers.py`: `load_shs` and `load_urban_areas`. The `MultiLineString` is
      exploded (1,180 parts), and Z/M are dropped. `RoadType` 4 = roadway (the only
      mainline evidence), 5 = ramp, 6 = short rest-area/interchange pieces.
      `RouteTypeC` 1 = mainline, 2 = spur, 3 = business loop, 4 = connector. Spurs and
      connectors are their route. Business loops are state highway in their parent route,
      labelled `business` (owner correction, Session 67). `Travelway` `D` is a divided
      road's second carriageway (`01010DIN084` runs all of I-84).*
- [x] **Route identity from the highway system.** `routes.route_membership` takes the
      SHS mainline as the identity source, and the AADT layer only as a fallback. The
      INRIX `RoadList` concurrency logic stays, because SHS names one route per road.
      Resolve the `unconfirmed` verdicts and explain the 53-mi buffer misses. Retire
      the Reisenauer override: keep the file, and note there that the source now agrees.
      *`route_membership(geo, aadt, overrides, shs=)`. The SHS decides every D1–D6
      non-ramp segment but 2, and a new `source` column records who decided.
      - Three rule fixes came from the data:
        - direction is compared at every sample, not at one contact point (winding
          ID-162 and SH-52 had first come out "off the system");
        - a business line *on* a segment outranks a mainline *near* it, so Farnsworth
          Way is labelled `business`;
        - a road lying on neither of a route's two drawn carriageways is a parallel
          road (Silver Valley Rd beside I-90).
      - The `unconfirmed` verdicts are resolved: 124.5 mi on the system (mostly
        interstate `D` carriageways and the new US-95), 33.9 mi off, 11.6 mi business
        loop (which stays in its route). The 53 mi are 138 segments confirmed only by the 40 m test: alignment
        offsets (W Chinden Blvd; US-93 near Twin Falls drawn as one line).
      - The Reisenauer row is a comment in `route_overrides.csv`.
      - **Business loops stay in their parent route**, labelled `business`, plus any
        route INRIX's `RoadList` names. A first pass took them out, on the strength
        of "D1 I-90 (business loops out)" in Item 49's text. The owner never said
        that; they corrected it. The only loop excluded is Caldwell's Cleveland Blvd /
        Blaine St, which is no longer state highway, so the SHS has no line on it.*
- [x] **Volumes from AADT 2025.** Change `aadt.DEFAULT_YEAR` and the scripts' `--aadt`
      defaults to `AADT_2025.zip`, and check that the geometry cache keys on the new
      source. Report how segment AADT and D3 peak VHD shift from 2024 to 2025. Item
      34's divided-highway problem is unchanged, but note whether the `Travelway` `D`
      lines give that join a carriageway to match.
      *`DEFAULT_YEAR = 2025` and `DEFAULT_SOURCE = "AADT_2025.zip"`, used by every
      script and the GUI. It did **not** key on the source; the cache sidecar now
      records the source's name and size, and a mismatch rebuilds. **Record kinds now
      come from the SHS where it is unambiguous** (roadway → mainline, ramp → ramp,
      matched by `RouteID`). ITD's descriptions had made I-184's mainline a
      connector, so both carriageways took 5,000–10,000 instead of ~68,000. They also
      made the Broadway interchange ramps "mainline" (US-20 on Broadway took 9,500
      instead of 29,500). Owner-flagged, Session 67.
      - With the SHS classification, inventory VMT is +2.9% (districts +1.6% to
        +3.5%) and D3 peak VHD is +2.6%.
      - With correct volumes I-184 is D3's #3–4 (≈4,800 VHD); it was #6 in both years
        before.
      - A ramp count filed on a roadway route id stays a ramp (the ramp signature,
        `aadt.ramp_signature`). I-84 at the Cotterell junction now reads 12,000 /
        19,500, and Weiser's W 7th St 6,300 (owner-checked).
      - AADT 2025 has almost no `D`-carriageway records, so the join still has one
        centreline. The SHS `D` lines are a geometry a future join could map the
        counts onto.*
- [x] **Urban context per segment**: the urban area it lies in (`UACE`, name) and its
      distance to the boundary. This is a context column, **never a gate**. The owner's
      rule (2026-09-23): the boundaries show where to look for a rural/urban transition,
      and they are not cutoffs (see Item 50).
      *`itd_layers.urban_context`: the area it lies in (or the nearest one), whether
      it is inside, its share inside, and a signed distance to the boundary. Written
      to `out/highways/route_membership/d<N>_urban_context.csv`. 848 of 9,725 route
      miles are inside an urban area.*
- [x] Rebuild membership for D1–D6 and regenerate the D1/D2/D4/D5/D6 inventories. Report
      the per-road changes against Item 48's in miles, and confirm the add-list diff is
      empty. Compute and report D3 as Item 48 did, for Item 49's decision.
      *124.0 mi changed membership. 43.3 mi lost a route: 36.4 dropped off the
      system, 4.8 Sun Valley Rd reversed, and short stubs. 81.0 mi gained one: 75.1 of
      it is business-loop concurrency, 1.3 was added. By road:
      `out/highways/route_membership/item52_changes_vs_item48.csv`.
      - The masters barely move: D1 2215→2214, D2 2106→2102, D4 3013→2999, D5
        2590→2582, D6 3447→3446. The pre-Item-52 copies are in
        `out/highways/pre_item52/`.
      - **The add-list is not empty: 36 segments, 1.95 mi**
        (`out/export_reconciliation/item52_d<N>/segments_to_add.txt`). 19 are
        unnumbered pieces lying on business loops (Markwell Ave, River St, E Seltice
        Way, Pocatello Ave); 17 are SHS connector pieces or 12–47 m slivers. All are
        short. The catalogue builder uses only observed segments.
      - D3 is reported in `d3_curated_vs_shs.csv`. 89.4 mi in the curated list have
        no SHS route, of which 65.8 mi is Banks-Lowman Hwy, kept in the export on
        purpose (owner). 1.3 mi of SHS route are missing from the list.*
- [x] pytest: an SHS record wins over an AADT-layer route; a `Travelway` `D` carriageway
      is on the system; an urban tag at a boundary; the synthetic Reisenauer case without
      its override. DATA_FORMAT: a section per layer. DESIGN_HISTORY.
      *`test_routes.py` +13: every requested case, plus a business loop in its
      parent route, a former loop off the SHS, a spur and a connector, ramps, the AADT
      fallback, a winding road, and a frontage road between carriageways. New
      `test_itd_layers.py` (8, including SHS record kinds and the I-184 join).
      `test_aadt.py` +2 (the source key, the defaults). DATA_FORMAT: the SHS,
      urban-area and AADT 2025 sections.*

*Suggested prompt (done):* "Do Item 52 of ROADMAP.md — load ITD's State Highway System, AADT 2025
and urban-area layers, and take route identity from the highway system."

## 49 — Ingest the Item 48 add-list and re-run the statewide screening ✅ (Sessions 66, 68)

**Target: one session. Depends on Item 48, and now on Item 52** (re-scoped 2026-09-23):
the catalogues are rebuilt once, on the highway-system membership and AADT 2025, not
twice.

- [x] Ingest the new segments into the district stores. Confirm that everything on the
      add-list was delivered: `reconcile_export_segments.py --district N
      --previous-master out/highways/pre_item48/District_N_ALL_Highways.txt` should
      show zero new requests.
      *Verified 2026-09-23. All 117 add-list segments are observed (D1 4, D2 25, D4 84,
      D5 2, D6 2). They came from `Backfill-Pacific_…_15_min` (D1/D2) and
      `Backfill-Mountain_…_15_min` (D4–D6), ingested with
      `scripts/ingest_backfill_segments.py` through a new `segment_ids` filter on
      `store.ingest_export[_streaming]`. They use the same 15-min cadence and 2026-01-01 to
      2026-08-31 span as each store's main export, so each went into the existing
      partition. Each district's reconciliation reports 0 new requests and 0 returned
      empty. The segments observed but no longer in the inventory (Item 48's drop-list:
      D1 73, D2 14, D4 5, D5 45, D6 9) stay in the stores, out of the catalogues.*
- [x] **Regenerate the D1/D2/D4/D5/D6 catalogues** (`build_statewide_catalogues.py`)
      on Item 52's inventories. The builder uses only observed segments. Lewiston's
      US-12 facility gains the levee bypass, and Sun Valley Rd stays out.
      *Re-screened first, so the builder's frames carry the 117 new segments (the old
      frames predated the ingest). All 169 entries verify.*
      - *The rebuild shipped two false couplets:*
        - *Lewiston:* "Us Highway 12" and the Levee Byp were paired twice, as mirror
          images. Both pairs got the same id, and D2 crashed on it.
        - *SH-77:* Elba-Almo Rd / Elba-Almo Hwy, two consecutive pieces of one rural
          road.

        *Two detector guards now reject them (`couplets.drop_mirrored_pairs`,
        `drop_two_way_legs`; a slice of Item 51). They drop 9 pairs statewide,
        including Chubbuck, and every registry couplet that matched before still matches. Net change: D2
        −2 couplets, D4 −1, D5 −3, D6's SH-33 Rexburg core re-tiered.*
      - *Lewiston: with membership, the Levee Byp **is** US-12 and D St is off it.
        But it forms its own 1.7-mi chain, because INRIX's `NextXDSegI` continues to
        D St (DATA_FORMAT trap 4, handed to Item 51). It has no congested core, so
        neither catalogue carries a Lewiston US-12 entry.*
      - *No entry walks Sun Valley Rd. Off-route miles inside entries fell in every
        district (D6 1.9 → 0.2, D3 4.5 → 1.9).*
- [x] **District 3, the owner's call.** Item 48 reported but did not apply D3's
      membership (50 dropped / 8 added). Most of it is Caldwell/Cleveland Blvd and
      Blaine St, which Item 42 kept out, plus Payette's S Main St / 7th Ave N and
      Nampa's Northside Blvd. Decide whether the curated D3 lists take it. *Evidence
      from Item 52's scratch check: Cleveland Blvd, Blaine St, Northside Blvd and Payette
      7th Ave / S 7th St are all off the state highway system.* *Item 52's full D3 report (`out/highways/route_membership/d3_curated_vs_shs.csv`):
      178 curated segments / 89.4 mi have no SHS route.
      - 65.8 mi is **Banks-Lowman Hwy**, kept in the export on purpose (owner): it
        stays in the data and is filtered out of the ranking by membership.
      - The rest is the roads above plus Elmore's Old Highway 30 and short stubs.
      - Business loops (Garrity Blvd, Caldwell Blvd, and Mountain Home's I-84
        Business) are state highway and stay in.
      - 16 segments / 1.3 mi of SHS route are not in the curated list.*
      ***Decided (owner, 2026-09-23): the D3 lists take the SHS membership, and US-95
      moves onto 16th St.***
      - *`scripts/apply_d3_membership.py` removes 77 segments / 23.5 mi from the
        master and the per-highway lists (Cleveland Blvd, Blaine St, Northside Blvd,
        Payette's S Main St / S 7th St / 7th Ave N, Old Highway 30, stubs). It keeps
        Banks-Lowman and writes the 16-segment add-list to
        `out/export_reconciliation/item49_d3/`. Old lists: `out/highways/pre_item49/`.*
      - *None of D3's catalogue entries touched Cleveland Blvd, Blaine St, Northside
        Blvd or Banks-Lowman. Only `us95-fruitland-payette` did, running 1.3 mi down
        Main St / 7th St. It is replaced by `us95-fruitland` (Whitley Dr, 2.2 mi) and
        `us95-payette-16th` (2.5 mi): one entry can't span the 16th St junction
        (DATA_FORMAT trap 4). D3 now has 54 entries / 27 reporting corridors.*
      - *For the owner: SH-44 urban's extent to Chinden Blvd runs 0.6 mi down
        Glenwood St, south of where the SHS ends SH-44 (43.649 N). Left as drawn.*
- [x] Re-run the district and statewide screening on the corrected catalogues. Record
      how the ranking moves, especially:
      - D2 US-12 (bypass instead of downtown);
      - D1 I-90 (Coeur d'Alene's Northwest Blvd / Sherman Ave are off the SHS; the
        Silver Valley and Post Falls business loops stay, labelled `business`);
      - D4 SH-77;
      - D3 I-184, whose volumes Item 52 corrected. This is the baseline Items 50 and 51 are
      measured against.
      *`run_statewide_screening.py --mode full --windows both --maps`, all six
      districts, on AADT 2025. The screening's AADT join now reads the route
      membership too (`--membership`, recorded in the provenance).
      115 reporting corridors ranked, down from 120. Tables:
      `out/statewide_screening/item49_{peak,7day}_ranking_changes.csv`; the old run is
      in `pre_item49/`.*
      - *D3 I-184: peak #6 → **#4** (3,217 → 4,843 VHD, +51%), 7-day #36 → #19.*
      - *D1 I-90: Kootenai core holds at #7 (−3% VHD). D2 US-12: no Lewiston entry
        either way; the Idaho County core is flat. D4 SH-77: never ranked; its only
        appearance was the false couplet, caught before shipping.*
      - *The top 11 are otherwise unchanged. Other movers: Moscow's US-95 cores swap
        (#15 → #25, #19 → #12), where the core ends were re-read from the refreshed
        frames; Gooding SH-46 core #33 → #43; D6 SH-33 Rexburg is Tier 2 (#13), no
        longer Tier 1 (#14).*
- [x] DESIGN_HISTORY with the before/after ranking. *Session 68.*

*Suggested prompt (done):* "Do Item 49 of ROADMAP.md — regenerate the catalogues on Item 52's
membership, settle District 3, and re-run the statewide screening."

---

## 50 — Corridor cores from recurring congestion, not TTI against INRIX's ref speed ✅ (Session 69)

**Target: one session. Depends on Item 52** (AADT 2025 volumes and the per-segment urban
context). Best done after Item 49, so the regenerated catalogues include the new
segments. Raised by the owner's Session 65 review of the maps; amended 2026-09-23 for
the ITD layers.

**The defect.** `extents.generate_catalogue` keeps a facility when either direction has
a **core**: at least 0.75 mi (`MIN_CORE_MILES`) of segments whose worse AM/PM TTI is
≥ 1.20 (`TTI_CONGESTED`), bridging up to 2 uncongested segments. TTI here is
`ref_speed / mean speed`. Nothing checks volume, delay, data quality, or whether the
road is equally slow off-peak. Session 65 measured the result on the store data
(Jan–Aug 2026, weekdays, CValue ≥ 80):
- **Geometry and low data pass as congestion.**
  - *SH-7 Gilbert Grade:* ref speed 32–34 mph; peak TTI 1.33 against 1.27 at night;
    180 AADT; about 2 VHD/mi; about 65% real-time data.
  - *US-12 near Lowell:* 550 AADT; 53% real-time data; no overnight data passes
    the CValue gate.
  - *US-95 in Idaho County:* peak TTI 1.32 against 1.29 at night.
  - *SH-3 in Benewah County:* 460 AADT.
- **One segment can be the whole core.** Bonners Ferry's core is one 0.84-mi
  northbound segment at TTI 1.26 (3,600 AADT, 22 VHD/mi).
- **Hard cliffs.** SH-8 through Moscow misses on a Pullman Rd segment at **1.199**,
  which leaves a 0.59-mi run on 3rd St, under the 0.75-mi floor.
- **Mirroring puts an extent on a free-flowing direction.** The opposite direction
  gets the lead direction's extent without its own data being checked:
  - I-90 eastbound in Coeur d'Alene: TTI 1.04 against 1.67 westbound;
  - Bonners Ferry southbound;
  - Rexburg SH-33 eastbound, whose extent opens with a 0.98-mi ID-33 segment at TTI
    0.92 (the owner's "west end with TTI < 1").
- **Tier 2 and Tier 3 sweep in free flow, and the rankings mix all tiers.**
  - Tier 3 is the whole chain, up to 180 mi. SH-75's 98.5 mi includes Galena, and
    US-30's 83 mi includes McCammon–Lava.
  - Tier 2 extends to the nearest split point. Coeur d'Alene's Northwest Blvd Tier 2,
    ranked #2, added 5 free-flow segments to a ~0.3-mi hotspot.
  - The rankings list Tier 1, 2 and 3 as peers.

Scope:

- [x] **A congestion test against the road's own baseline.** Compare peak travel time
      with the same segment's off-peak baseline (overnight, or a low percentile of
      weekday travel time), not only against INRIX's `Ref Speed`. The peak/night
      ratios are already in hand from Session 65's diagnostics:
      - real hotspots are 1.2–1.5 (I-90 westbound 1.54, US-95 Coeur d'Alene 1.30,
        SH-75 Hailey 1.23, Rexburg 1.23);
      - the geometric ones are 1.00–1.04.

      Decide how to handle a segment with too little overnight data; don't treat the
      gap as zero.
      *Done: `extents.segment_congestion` on the new baseline screen (`screen.BASELINE_WINDOWS`, `quantiles=`). Night mean with ≥ 100 gated rows, else weekday p15, else **unknown** (bridged, never zero delay).*
- [x] **Delay and data-quality floors.** A core needs a minimum VHD/mi or total VHD
      from the AADT join, and a minimum real-time share (`Pct Score30`) or kept
      fraction. Calibrate both on the Session 65 list, **using AADT 2025 volumes**
      (Item 52), and record the values in DATA_FORMAT.
      *Done: ≥ 90% `Pct Score30` real-time at peak, plus a **noise floor** of 10 VHD/mi and 10 VHD (AADT 2025), recorded in DATA_FORMAT. The owner (Session 69) chose a permissive floor over a 60 VHD/mi cut: VHD is an index, and thinning to a top-N belongs to the ranking. Small towns (Blackfoot, Bonners Ferry, Soda Springs, Sandpoint, Ammon) stay in and rank low.*
- [x] **No cliffs.** Replace the per-segment 1.20 / 0.75-mi gates with a length- and
      delay-weighted score, so one long rural segment can't be a core by itself and
      1.199 doesn't fall off an edge. Record where SH-8 through Moscow lands.
      *Done: a smooth weight from 1.05 to 1.20, seeds at ≥ 1.10 with 2-segment/0.5-mi bridges, and ≥ 0.6 effective miles with each segment capped at 0.5 mi. SH-8 through Moscow is now a 1.79-mi core, Warbonnet Dr to Jackson St (rank 30).*
- [x] **Each direction answers for itself.** Trim a mirrored extent to where that
      direction's own data supports it. A direction with no core of its own is either
      reported as the lead direction's companion, clearly labelled, or dropped;
      decide which.
      *Done, **dropped** with the reason in `_companion`: the opposite direction is catalogued only with its own qualifying core, at its own bounds. I-90 EB is dropped (1.01, 16 VHD/mi).*
- [x] **Tier 2 and Tier 3 stop at the congestion.** End Tier 2 at the congestion
      discontinuity, not just the next junction. Rank Tier 1 cores; report Tiers 2
      and 3 as context, not as peer rows.
      *Done: Tier 2 grows until the congestion ends or dilutes (50%); Tier 3 is ≤ 3 mi of context. Only Tier 1 ranks; Tiers 2 and 3 go to `statewide_*_context_extents.csv`. The ranking went from 115 rows to 64.*
- [x] **Urban areas guide the extent; they don't cut it** (owner, 2026-09-23). Use
      Item 52's urban context as the first place to look for the rural/urban
      transition where an extent should end. **The boundary is not a hard cutoff.**
      Keep congestion that runs past the boundary when it is significant, meaning
      it doesn't significantly dilute the primary congestion. For example, the
      extended extent's delay per mile stays within some fraction of the core's;
      calibrate that fraction and record it. Congestion inside an urban area can
      also end short of the boundary. Being rural must not be what drops a core:
      Gilbert Grade, Lowell, Idaho County, Benewah and Bonners Ferry must fail the
      congestion, delay or data-quality tests on their own. Show where Galena,
      McCammon–Lava, and SH-45 beyond 12th Ave in Nampa land.
      *Done: the retention fraction is 0.5; past the boundary nothing uncongested is bridged. The rural false cores fail on data or delay. Galena and McCammon–Lava are out; SH-45 (D3 dry run) cores 12th Ave S to Meadowbrook Dr.*
- [x] **Acceptance: the owner's list, checked segment by segment.**
      - Drop: SH-7 Gilbert Grade, US-12 near Lowell, the US-95 Idaho County core,
        SH-3 Benewah, and Bonners Ferry.
      - Keep: I-90 westbound IC 12–11, the US-95 Coeur d'Alene core, SH-75 Hailey,
        the Rexburg Main St hotspot, and Twin Falls US-93.
      - Galena and McCammon–Lava stay out of the ranked set.
      - Settle the I-90 westbound winter/summer ratio of 0.43: is it summer-only,
        and possibly construction?
      *Done: see the Session 69 table. Gilbert Grade, Lowell, Idaho County and Benewah fail on delay and data. Bonners Ferry passes the noise floor and ranks near the bottom (owner's permissive-floor decision). All five keeps rank. Also fixed: several cores per chain (SH-75 Hailey had been lost behind Ketchum). I-90 WB is summer-only: a step change on 22–23 June 2026 with nights unaffected, a work zone. It carries an `episodic` flag rather than being excluded.*
- [x] pytest on synthetic chains: a geometric grade, a one-segment core, a mirrored
      free-flow direction, and a 1.199 neighbour. Regenerate D1/D2/D4–D6, re-run the
      screening and maps, and record in DATA_FORMAT and DESIGN_HISTORY how the
      rankings move.
      *Done: `test_extents.py` (Item 50 classes), `test_screen.py` +2, `test_aggregate_statewide_rankings.py`; 760 passed. D1/D2/D4–D6 regenerated and re-screened; maps and aggregation re-run.*

*Suggested prompt (done):* "Do Item 50 of ROADMAP.md — base corridor cores on recurring peak
congestion against each road's own baseline, with delay and data-quality floors."

## 51 — Chains across route-numbering changes, and couplets that are real ✅ (Session 70)

**Target: one session. Depends on Item 52** (the highway system's route IDs, mileposts and
`Travelway`). **Independent of Item 50**, but both regenerate the catalogues, so do one
after the other and re-run the screening once, after Item 49. Amended 2026-09-23 for
the ITD layers.

**The defect.** `extents.enumerate_mainline_chains` walks one `RoadNumber` at a time,
so a street whose number changes along its length becomes several short chains. Each
one has to clear the 1-mi chain minimum and find its own core:
- **SH-8 through Moscow** splits in two where it runs concurrently with the US-95
  couplet, which is numbered 95 there.
- **Idaho Falls Yellowstone Hwy / Northgate Mile** is numbered as five routes along its
  length: I-15 BL, US-20 BR, US-26, SH-43 and US-91.
- **Broadway east of I-15** is a 0.9-mi I-15 BL chain, under the minimum. West of
  I-15 it is ranked, as US-20.

The couplet detector pairs parallel roads that are not one-way pairs:
- **Chubbuck:** Yellowstone Ave / Quinn Rd paired with US-91 as a "US-91 couplet",
  miles north of the real Pocatello couplet.
- **SH-43 with E 105 N.** Item 48 removed this pair indirectly.
- **Sandpoint** (US-2/US-95, 1st Ave / 5th Ave) is still in `KNOWN_COUPLETS`. The owner
  says it is a divided highway, not a couplet.

Scope:

- [x] **Walk a road through its numbering changes.** Build chains on ITD route
      membership (`routes.py`, including `concurrent` segments), or on street and
      `XDGroup` continuity, so SH-8 through Moscow and Yellowstone Hwy are each one
      facility. Show that SH-8 from the WA line to the east city limits resolves as
      one chain. *From Item 52:* order each chain by the highway system's `RouteId` and
      milepost, not only by `NextXDSegI`. The layer records one route per road, so SH-8
      runs as a gap in its own mileposts where it shares the US-95 couplet. Bridge
      that gap with INRIX `RoadList` concurrency, where the milepost gap shows it.
      *From Item 49:* two junctions where `NextXDSegI` follows INRIX's old route
      and not ITD's (DATA_FORMAT, trap 4):
      - **Lewiston US-12**, where the Levee Byp is its own 1.7-mi chain;
      - **Payette US-95** on 16th St, which D3's catalogue now carries as two entries,
        `us95-fruitland` and `us95-payette-16th`.

      An SHS-ordered chain should make each one facility again.
      *Done (`extents.enumerate_mainline_chains`). A route is walked over its
      membership routes plus the routes its `RoadList` names. Chains are joined where
      the route turns off the link, and merged where only the number changes along the
      street.*
      - *A junction leaving the route's own run as a stub is taken only on SHS
        milepost evidence (`itd_layers.shs_mileposts`): SH-8 leaves its line at mp 1.79
        on 3rd St and returns at 2.35 on Troy Rd.*
      - *SH-8 is one chain each way from the WA line (mp 0.12) through Moscow, via
        Jackson St eastbound and Washington St westbound, with its own-line mileposts
        in order. It ends at a genuine data gap past Bovill (no eastbound segments
        between mp 36.27 and 37.60).*
      - *Yellowstone Hwy (US-91 → US-26) is one 75.6-mi chain each way, and Broadway
        east of I-15 is part of US-20's. Lewiston's US-12 is one chain: Levee Byp →
        Main St → US Highway 12.*
      - *98 route junctions statewide are now `route_junction` rows in the repair
        tables (`scripts/generate_route_junctions.py`). The joins a global patch can't
        carry go into each generated entry's own `links`.*
      - *D3's US-95 is one entry again, `us95-fruitland-payette`, on 16th St. D3 now
        has 52 entries / 26 reporting corridors.*
- [x] **Couplets must be one-way pairs.**
      - Require that each leg is actually one-way: there is no opposing-bearing XD
        segment on the same street. *Partly done in Item 49, because the rebuilt
        catalogues shipped false couplets at Lewiston and Elba:*
        `couplets.drop_mirrored_pairs` and `couplets.drop_two_way_legs` (an opposing
        same-street segment within 15 m over ≥50% of a leg). Chubbuck's Quinn Rd /
        US-91 now fails, and every registry couplet that matched before still does. What is left here: the
        route-share test, Sandpoint, the `Travelway` `D` test, and a review of the
        survivors: Shoshone Greenwood St / US-93, and American Falls ID-39 S / ID-39 N
        at 28 m.
      - Require that the legs share a route under ITD membership.
      - Remove Sandpoint from `KNOWN_COUPLETS`, with the owner's reason recorded.
      - Use the highway system's `Travelway` `D` lines (Item 52): a divided highway's
        second carriageway is not a couplet leg.
      - The Chubbuck pair and the SH-43 / E 105 N pair must fail.
      - Pocatello 4th/5th Ave and Blackfoot Bridge/Judicial must still pass. Also check
        the 0.67-mi Pocatello Ave extension on Pocatello's legs.

      *Done.*
      - *The legs must share a route under membership (`itd_routes`).*
      - *Sandpoint is out of `KNOWN_COUPLETS`, with the owner's reason recorded.*
      - *`Travelway` `D` turned out to be **also** how the SHS draws a real couplet's
        second leg (Moscow, Boise, Nampa, Pocatello, Blackfoot, Twin Falls). So the
        tests are `drop_same_line_pairs` (both legs on one SHS line: Shoshone fails)
        and `drop_divided_pairs` (`A` + `D` closer than 50 m: American Falls' ID-39
        fails at 28 m).*
      - *Chubbuck and SH-43 / E 105 N fail as two-way legs. Pocatello and Blackfoot
        pass.*
      - *Pocatello's 5th Ave leg overran 4th Ave by 0.65 mi north (the extension) and
        1 mi south onto two-way pavement. `trim_leg_overhang` cuts it: 2.80 → 2.34 mi
        against the registry's 2.22.*
      - *Every decision is in `out/statewide_screening/couplet_review.csv`. For the
        owner: Mountain Home's Main St / 2nd St E (35.5 m) survives, and D3's catalogue
        doesn't carry it.*
- [x] **Names say what the road is.** Facility names come from the street and the
      city (the urban-area name from Item 52 where there is one), not "US-20:
      Bonneville County (2)"; Northgate Mile ranks as #6 under
      that name today. Couplet legs are labelled by their own bearing (NB/SB), not
      WB/EB.
      *Done (`extents.facility_naming`): `<band>: <street>, <town>`. The town is the
      urban area, else "<County> County". The band takes `BL`/`BR` from `RoadList`.
      The street is dropped when the road is named only by its route.*
      - *Examples: "US-26: Yellowstone Hwy, Idaho Falls" (the old "US-20: Bonneville
        County"), "SH-8: Pullman Rd, Moscow", "I-15 BL: Bergener Dr, Blackfoot".*
      - *Couplets read "US-95: Washington St / Jackson St couplet, Moscow".*
      - *Legs are labelled by their compass direction: `geometry._bearing_deg` now
        scales longitude by cos(latitude), so Pocatello's legs read NB/SB.*
- [x] pytest; regenerate the catalogues; DESIGN_HISTORY.
      *Session 70. New tests in `test_extents.py` (Item 51 classes), `test_couplets.py`,
      `test_corridors.py`, `test_geometry.py` and `test_itd_layers.py`. D1/D2/D4–D6
      regenerated and all entries verify. Full screening re-run; the ranking moves are
      in DESIGN_HISTORY.*

*Suggested prompt (done):* "Do Item 51 of ROADMAP.md — walk corridor chains across route-number
changes and require couplets to be real one-way pairs."

---

## 53 — Couplet legs carry one-way AADT; everything else carries two-way ✅ (Session 72)

**Target: one session. Independent of the other open items**, but it changes VHD, so
run it before the next statewide re-run, not after. A prerequisite for the Future
"Directional AADT" bullet: that split needs every segment on one volume basis first.
Scoped 2026-09-24 (Session 71) from the owner's question.

**The defect (verified Session 71, not yet fixed).** Every XD segment is one direction
of travel, and VHD is `delay/60 × AADT` per segment (`extents.segment_congestion`,
`aadt.vehicle_hours_of_delay`). What AADT a segment gets depends on the road:
- **Two-way road:** both directions match the one centreline and get its **two-way**
  count.
- **Divided highway:** AADT 2025 has one centreline per divided highway (the `D` routes
  are mostly absent). `join_aadt` reaches it from both carriageways (60 m), so both
  directions get the **two-way** count.
- **Couplet:** ITD carries each leg as its own record (`A` and `D` route IDs, e.g.
  `01360AIN015`/`01360DIN015` for Pocatello's I-15 BL), and that count is **one-way**.
  Where the road splits, the layer halves it: `01360AIN015` goes from 15,000
  (two-way, "END 1-WAY N OF RAMPS") to 7,500 on the A leg and 7,700 on the D leg.
  After the join, couplet legs carry 6,500–12,000. The two-way road just past each
  end carries 13,000–21,500 (Pocatello, Blackfoot, Twin Falls).

So each couplet leg's VHD and VHD/mi is **about half** of what the same delay scores on
any other road. Nothing corrects for it today: `couplets.py` and the screening never
touch AADT. The Boise Myrtle/Front couplet (D3 curated) is affected the same way.
**Moscow fits once SH-8 is included** (owner, Session 71). Between 3rd St and the
south junction the couplet carries US-95 *and* SH-8: SH-8 comes in from the west on 3rd St
and leaves to the east on Troy Rd. So that section adds up two routes' traffic, not one. The layer
agrees:
- **Concurrent section** (3rd St → south junction): 12,500 on the NB leg (Washington) plus
  12,000 on the SB leg (Jackson) = 24,500. Compare 14,000 two-way on US-95 south of the
  couplet, 13,000 on SH-8 Troy Rd east, and 11,000–17,000 on SH-8 3rd St west.
- **US-95-only section** north of 3rd: 6,800–10,500 NB plus 9,600 SB, against 16,000
  two-way at "END 1-WAY N OF C ST".

Both legs carry one-way counts, as in every other couplet. One join detail to check:
the first segment of each leg at the south junction joins to SH-8's
`WASHINGTON ST (US-95) → BLAINE ST` record (13,000, two-way Troy Rd), not the leg's
own. With 8 and 95 both in its `RoadList`, the route-number preference lets the SH-8
record win.

Scope:

- [x] **Decide the basis.** Recommended: keep the **two-way-equivalent** basis the
      rest of the network already uses (that is what the noise floors,
      `MIN_CORE_VHD[_PER_MILE]`, were tuned on). Give a couplet leg its one-way count
      × 2. A true one-way VHD would halve every other segment instead, and every floor
      would need re-tuning. Record the decision in DESIGN_HISTORY.
      *Done: two-way-equivalent, as recommended. `AADT` is the two-way-equivalent
      volume, and the published count stays alongside as `aadt_layer`.*
- [x] **Decide per record, not per corridor.** Use the layer's own evidence where you
      can: an `A`/`D` pair on the same measures, or a "BEG/END 1-WAY" boundary where
      the count halves. Fall back to couplet-leg membership (`one_way_couplet`). Give
      each segment an `aadt_basis` column (`two_way` / `one_way_x2`) so the per-segment
      VHD maps agree with the corridor rankings. Apply it once, right after
      `join_aadt` (`run_district_screening.join_volumes`,
      `build_statewide_catalogues`, `generate_statewide_maps`). Do not add it at each
      place VHD is used.
      *Done (`aadt.classify_aadt_basis`, `aadt.apply_two_way_basis`, applied inside
      `join_aadt`; `join_volumes` re-applies it with the catalogue's couplet legs,
      `corridors.couplet_segments`).*
      - *The layer evidence is the 1-WAY/2-WAY words, read by which end of the record
        they sit at.*
      - *Or an `A`/`D` pair running **beside** each other. "The same measures" could
        not be used: Moscow's and Twin Falls' `D` legs are measured differently from
        their `A` legs.*
      - *A count is doubled only on a segment with no opposing twin on its own
        street. The layer's `D` records include interchange crossings and roundabouts
        that two-way roads cross.*
      - *`aadt_basis` is `two_way` / `one_way_x2` / `ramp` (a ramp's count is one
        movement), with `aadt_basis_reason`.*
      - *Statewide, 164 segments are doubled, and 51 two-way streets reaching a
        one-way record are left alone.*
- [x] Fix the Moscow south-junction segments that join to SH-8's Troy Rd record
      (above). Check the Boise couplet's legs against I-184 and the two-way ends. Any
      check that "the legs add up to the two-way road" has to allow for a concurrent
      route joining inside the couplet, as Moscow's does.
      *Done, but it was not a join defect.*
      - *Those segments are Troy Rd itself (`ID-8`, two-way, 0.51 mi), and 13,000 is
        right for them.*
      - *They were in the legs because the catalogue endpoint, rounded to 5 decimals,
        snapped 1 ft nearer Troy Rd's end than Washington St's start.
        `corridors.build_chain` now treats snaps within `JUNCTION_TIE_FEET` (5 ft) as
        a tie, broken toward the segment the point begins or ends. That changes 150
        of 339 resolved chains by an end segment with ~0% inside the extent. Rankings
        prorate by extent fraction, so metrics move by ≤ 2.5%.*
      - *The same artefact was Session 33's "VSL +21% overshoot": that chain is now
        3.007 mi against 3.006 requested. Franklin's asymmetry is real and is
        unchanged.*
      - *Boise checks out: I-184 58,500 two-way; the US-20 Spur `A` + `D` = 29,000 +
        27,000; Myrtle + Front 49,000–61,500 against Broadway's 29,500.*
- [x] pytest (the factor, the flag, and an `A`/`D` split fixture); re-run the screening;
      report how the couplet rankings move; DATA_FORMAT + DESIGN_HISTORY.
      *Session 72. 7 new tests in `test_aadt.py`, 3 in `test_corridors.py`, and the
      VSL test is rewritten. The screening is re-run with maps.*
      - *Every couplet's VHD/mi doubles, and it is ranked on the same basis as
        everything else.*
      - *Peak moves: Boise #4 → #2, Moscow #20 → #9, Twin Falls #50 → #27 and #54 →
        #33, Pocatello #51 → #29, Nampa #55 → #36, Blackfoot #60 → #57.*
      - *Tables: `out/statewide_screening/item53_{peak,7day}_ranking_changes.csv`.*

*Suggested prompt (done):* "Do Item 53 of ROADMAP.md — put couplet legs' AADT on the same
two-way basis as the rest of the network before VHD is computed."

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
- **Segment-level route overrides (SH-8 in Moscow).** Owner, 2026-09-23 (Session 70):
  SH-8 eastbound follows 3rd St, then goes down Jackson St (with US-95) to Troy Rd.
  Westbound, it goes north on Washington St (with US-95) to 3rd St, then heads west.
  So the block of 3rd St between Washington and Jackson is state route **westbound
  only**, although it is a two-way street. SHS draws SH-8 as one centreline down 3rd
  St to Washington, so membership puts both directions on route 8. The two eastbound
  segments `448932361` / `448932362` are therefore counted as SH-8 in the D2
  inventory and export lists.
  - **Today:** the Item 51 chain walk already leaves them out as a milepost-evidenced
    stub. The catalogue is right, and the owner is OK keeping them in the data for now.
  - **Later:** `scripts/route_overrides.csv` matches on county + road name, which
    can't separate the two directions of one street. Add an optional segment-id
    column, and override those two segments off route 8, so membership states the
    routing directly instead of relying on the stub rule. After that, rebuild D2
    membership and the inventories, and re-run the D2 catalogue. Other one-direction
    route splits like this may exist.
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
- **~~`ingest_export_streaming` mis-attributes its row count~~ — resolved 2026-09-18
  (Item 39, Session 48): there was no counter bug.** `io._discover_parts` expands *any*
  `..._part_N.zip` into all of its siblings, so a call handed `part_1.zip` ingests the
  **whole** export and `n_rows_added = 91,054,384` is the correct total across the three
  parts, not part 1's own 45,403,216. That also explains what the finding could not —
  the store reached its complete, exactly-correct state "while the ingest was still
  working on part 2" because the *first* call was itself looping over all three parts,
  and parts 2 and 3 "never logged" because they were never separate ingests.
  `d3_store.duckdb`'s `_ingests` table has exactly **one** row. DESIGN_HISTORY Session
  40's "3 parts, 91,054,384 rows, 262 s" stands as a whole-export figure. What was
  actually wrong was the *record*: both ingest functions now return `n_parts` / `parts`
  and log the resolved member list rather than the single path handed in, so a
  provenance row can no longer read as "part 1 carried everything".
- **OSM geometry fallback** — only needed for segments *not* in the XD shapefile
  (out-of-state, or a future provider change): per-segment map-matching
  (osmnx/OSRM/Valhalla + a Shapely endpoint cut, QA'd against `Miles`). Not
  required while the INRIX XD shapefile covers the study area — kept here as the
  documented escape hatch.
