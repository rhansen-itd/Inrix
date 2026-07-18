# Whole-app review — GUI intake inversion + follow-on fixes (Fable, 2026-07-18)

A review-only pass over the finished Items 19–23 app (`gui/app.py`,
`gui/figures.py`, `src/inrix_tools/store.py`, spot-checks of the rest of the
core). Baseline: **265 tests pass** (87 s). Nothing here changes code — the
findings are grouped into four proposed session-sized items (**24–27**, IDs
continuing from 23) with a Target model each — **accepted by the owner and
appended to ROADMAP.md the same day**. *(Same-day addendum: the owner reported
the segment table misbehaving and directed a redesign — findings R10–R12,
scoped as Item 27.)*

**The one-paragraph summary:** the code is healthy — the store round-trips
faithfully, the callbacks degrade gracefully, and no correctness bug in the
statistics surfaced this pass. The real finding is the one the owner already
flagged: **Item 21's GUI scope was only half-delivered.** The scope said "the
main data control becomes a select-from-ingested-datasets dropdown … replacing/
augmenting the path box", but the shipped `_controls()` still leads with the
file-path box and a `color="primary"` **Load export** button, with the DB Area/
Bin selectors tucked underneath in muted small-text labels. Inverting that
(DB-first intake, file loader demoted) is Item 24. Two structural follow-ons
fall straight out of the area model: date restriction is **impossible** on DB
loads today, which bites harder as areas accumulate months of merged exports
(Item 25), and a handful of small robustness/doc nits make a Sonnet-sized
cleanup (Item 26).

---

## 1. The owner's dropped requests (both confirmed in code)

### R1 — File loader is still the primary intake; DB select is the afterthought
`gui/app.py:784-830` (`_controls()`). Top-to-bottom order today: Export path →
Timezone/CValue → Names CSV → AADT → Restrict dates → **Load export (primary
button)** → name-template link → *then* the low-visibility "⤓ Ingest current
export to DB" link and the **Area (DB)** / **Bin length** dropdowns, labelled
`small text-muted`. Item 21's own scope box (checked off as delivered) says the
main data control becomes the DB select. The daily workflow after Item 23 is:
open app → pick area → work; re-parsing a zip is the rare path. The layout
says the opposite.

### R2 — App start doesn't point at the database
Two halves:
- **No auto-load.** On startup with a populated store, the Area dropdown sits
  empty and nothing loads until the user finds it. `store.area_names()` already
  orders most-recent-first — defaulting the Area value (and its first bin,
  which triggers the existing `_load` path) makes the app open on the last
  thing worked on.
- **Options frozen at process start.** `build_app()` assigns
  `app.layout = _layout()` (`gui/app.py:1028`) — a static tree — so
  `_area_options()` runs **once at import**. Areas ingested by another process
  (or a CLI ingest) never appear until restart; a browser refresh doesn't help.
  Dash's documented fix is assigning the *callable* (`app.layout = _layout`) so
  the layout — and the dropdown options — re-evaluate per page load.

---

## 2. New findings

### R3 — Date restriction is impossible on a DB load (grows worse forever)
`gui/app.py:1090-1091`: a DB-triggered load hard-codes
`r_ds, r_de = (None, None)` (deliberate — the picker may hold the previous
export's range), and the only other trigger, **Load export**, always loads from
the *file* path. Net: **there is no way to date-restrict a DB-loaded area.**
Under the Item 23 model areas accumulate indefinitely (the Myrtle area is
already 2.19 M rows ≈ 2.3 GB in memory per Item 14's measurement), so every DB
load pulls the full history, and `_adjusted_cache` holds up to two full-area
decomposition frames on top. The clean fix is **push-down**:
`store.load_export` grows optional `date_start`/`date_end` params compiled into
the `WHERE` clause (DuckDB scans this well), and the GUI applies the
Restrict-dates picker to DB loads the same way it does to file loads. This is
the difference between the area model scaling for years vs. OOMing next spring.

### R4 — Load status/state doesn't say *what* is loaded
The status line (`gui/app.py:1120-1121`) reports rows/segments/span but not
provenance — after a DB load it looks identical to a file load, the Export path
box still shows a zip path that was *not* read, and a later file load leaves
the Area dropdown still showing an area that is no longer what's on screen. Two
intake paths need mutually-exclusive visible state: status says
`area 'Myrtle…' · 5-min` or the file name, and the inactive path's selection is
cleared (or visually dimmed).

### R5 — Shared DuckDB connection across threaded callbacks
`_DB` (`gui/app.py:188-197`) hands the **same** connection object to every
callback; Dash's dev server is threaded, and DuckDB's Python API documents
connections as not safe for concurrent queries — the sanctioned pattern is
`con.cursor()` per thread. Today the exposure is small (ingest and load are
user-sequential), but it's a one-line-per-callsite hardening. *(Unverified —
no race reproduced; filed as hygiene, not a crash.)*

### R6 — `_merge_frame` keep-first leaks NULL-key rows
`store.py:236-255`: the anti-join `o."Segment ID" IS NULL` test means an
incoming row whose *own* key column is NULL never matches an existing row, so
NULL-key rows re-insert on **every** re-ingest — duplicates the keep-first rule
exists to prevent. Real exports shouldn't carry a NULL `Segment ID`/timestamp,
but the store shouldn't rely on that; filter NULL-key rows (or COALESCE the
join) and add the test. *(Unverified against a live frame; the SQL reads
unambiguously, though.)*

### R7 — Compass checklist reads as "nothing selected" while everything renders
`_load` resets `dir-compass` to `[]` (render all — `gui/app.py:1145`), so the
control shows four unchecked boxes above a map drawing all four directions.
Both `[]` and all-checked are no-ops by design; initialising the value to *all
present groups* makes the control state match the map at zero behaviour cost.

### R8 — "Save names" round-trip drops on the floor at next load
`_save_names` (`gui/app.py:1522`) writes to `out/segment_names.csv` when the
Names CSV box is blank and *tells* the user to go set the path — but doesn't
set it. Next load silently loses the edits. **Superseded by Item 27** (owner
decision, same day): names move into the DuckDB store and the CSV workflow is
retired, which closes this loop structurally.

### R9 — Doc nit: `put_export` docstring vs. reality
`store.py:210-212` says "the GUI already holds the frames … so it calls this
directly" — but the GUI (`ingest_to_db`, `gui/app.py:363-376`) correctly
re-reads the **raw** export (the in-memory `Dataset.df` is CValue-filtered,
localized, and carries the derived Delay column — persisting it would be
wrong). Fix the docstring so nobody "optimises" the re-read away later.

### R10 — Segment table: positional row indices corrupt under native sort  ⚠ verified in code
Owner-reported: "checking/unchecking boxes can make unrelated rows disappear."
Three interacting mechanisms, all in the Item 19 table wiring:

1. **No row `id`s + positional indices.** `_table_rows` (`gui/app.py:571`)
   emits rows without the `id` key dash_table needs for its id-based selection
   props, and the callbacks then use *positions*:
   `_rows_to_member_ids(data, selected_rows)` (`gui/app.py:611`) and
   `active_cell["row"]` indexed straight into the `data` State
   (`gui/app.py:1490-1499`). With `sort_action="native"`, `active_cell.row` is
   the **view** index while `data` keeps load order — after any sort, a click
   or checkbox resolves to the *wrong* Segment ID, and the two-way
   highlight↔select callbacks (`_highlight_row` / `_row_selects_segment`)
   ping-pong on mismatched rows.
2. **Full-data rewrite per toggle + `fixed_rows`.** Every checkbox change makes
   `_members_changed` rewrite the entire `data` prop (to refresh the coverage
   columns); combined with `fixed_rows={"headers": True}` and an active sort,
   dash_table is known to misrender/drop rows on data replacement — the
   literal disappearing-rows symptom.
3. **Dimming amplifies it.** Ticking one box makes every *other* segment fade
   to 20 % opacity on the map by design (select-to-include, Item 19) — so one
   wrong-row selection reads as "unrelated things vanished".

### R11 — Table lists the whole export, selection means "include" (owner: invert it)
The table shows all export segments and an empty selection means "everything";
membership is opt-*in* via checkboxes. Owner direction (2026-07-18): the table
should show **one corridor's segments**, with an explicit **Include/Exclude
column**, **all included by default** — exclusion is the marked, deliberate
act. That matches the actual workflow (drop the one chronically-missing
segment) and inverts the dimming so the default view dims nothing.

### R12 — Names belong in the DB, not a CSV side-file (owner decision)
With the table as the name editor (Item 19) and the store as the primary
intake (Item 24), the CSV round-trip — `Write name template` → hand-edit →
paste path into `Names CSV` → reload — is dead weight and a silent-loss trap
(R8). Names should persist in the DuckDB store and apply automatically on
load. Note one semantic difference from the observation tables: name saves are
**last-write-wins** (an edit must overwrite), *not* keep-first.

### Noted, no action proposed
- `segment_map` draws one trace per segment and `beforeafter_forest` grows
  22 px per row — fine at 46 segments, worth re-measuring only if an area ever
  merges hundreds.
- `ingest_to_db` re-parses the export (~13 s) even when the same file was just
  loaded — correct per R9; the cost is once-per-area-ever.
- The shapefile path has no GUI control (hard default to the Idaho zip). Fine
  until a non-Idaho export shows up; the OSM-fallback Future item is the
  existing escape hatch.

---

## 3. Proposed ROADMAP items (accepted — now in ROADMAP.md)

Per the owner's rule (2026-07-16): *math-heavy → Fable, everything else →
Opus, small/mechanical → Sonnet*. Nothing in this batch is math-heavy, so no
Fable implementation item — Fable stays the review tier.

---

### 24 — DB-first intake: invert the data controls (owner request)

**Target: Opus** (GUI/layout work, no new compute). Independent.

The Item 21 scope line that didn't land, plus the display nits that live in the
same `_controls()`/`_load` region. Scope:

- [ ] **Area + Bin become the top-of-panel data control** (normal-weight
      labels); auto-select the most-recent area (and its first bin) at startup
      so the app opens loaded from the DB (R1, R2).
- [ ] **Demote the file loader** — path/tz/CValue/Names/AADT/Restrict-dates +
      the Load button move into a collapsed `dbc.Accordion` section ("Load /
      ingest an export file"); Load button drops `color="primary"` (R1).
- [ ] `app.layout = _layout` (callable) so area options refresh per page load
      (R2b).
- [ ] **Provenance in the load status** (area name + bin vs. file name) and
      mutually-exclusive intake state — a file load clears the Area selection
      and vice versa (R4).
- [ ] Compass checklist initialises to all present groups (R7). *(R8 — the
      save-names path wiring — was scoped here originally but is superseded by
      Item 27's names-in-DB; don't patch the CSV loop.)*
- [ ] pytest: startup auto-select wiring, provenance strings, state clearing;
      headless layout test keeps passing DB-free. DESIGN_HISTORY entry.

*Suggested prompt:* "Do Item 24 of ROADMAP.md — make the DB the primary intake
in gui/app.py per REVIEW_APP_2026-07-18.md R1/R2/R4/R7/R8."

---

### 25 — Date push-down: Restrict-dates on DB loads

**Target: Opus.** Core (`store.py`) + a small GUI wire-up; lands cleanest
*after* Item 24 (same controls column), but no hard dependency.

- [ ] `store.load_export` / `load_dataset` grow `date_start`/`date_end`
      (inclusive local-calendar semantics **identical** to
      `timebins.filter_date_range` — note the tz conversion happens after load,
      so push down in UTC bounds derived from the local dates, or filter on the
      UTC column conservatively and re-trim in pandas; decide + record).
- [ ] GUI: the Restrict-dates picker applies to DB loads; picker bounds come
      from the area registry's `date_min`/`date_max` (R3).
- [ ] pytest: DB-restricted load == file-load + `filter_date_range` on the same
      span (parity test); empty-range degrade. DESIGN_HISTORY entry; DATA_FORMAT
      note if the bound semantics need stating.

*Suggested prompt:* "Do Item 25 of ROADMAP.md — add date push-down to
inrix_tools.store and wire Restrict-dates to DB loads, per
REVIEW_APP_2026-07-18.md R3."

---

### 26 — Store & app hardening (small fixes batch)

**Target: Sonnet-eligible** (mechanical, each fix is small and testable;
Opus if run in the same sitting as 24/25).

- [ ] Per-callback `con.cursor()` (or a module lock) for the shared DuckDB
      connection in `gui/app.py` (R5).
- [ ] `_merge_frame` drops (or COALESCE-joins) NULL-key rows so keep-first
      holds; test: re-ingest a frame with a NULL `Segment ID` row → no dupes
      (R6).
- [ ] `put_export` docstring corrected (GUI re-reads raw on purpose) (R9).
- [ ] pytest for each; DESIGN_HISTORY entry (one line each).

*Suggested prompt:* "Do Item 26 of ROADMAP.md — the hardening batch from
REVIEW_APP_2026-07-18.md R5/R6/R9."

---

### 27 — Corridor-scoped segment table: include/exclude column + names in DB (owner request)

**Target: Opus** (GUI + a small store schema addition; no new math). Lands
cleanest **after Item 24** (same controls region, and it assumes the DB is the
primary intake); supersedes Item 24's R8 bullet.

Owner direction (2026-07-18): the table shows **one corridor**; membership is
an **Include/Exclude column** (all included by default, deselect to exclude);
segment names **save to the DB**, retiring the CSV workflow. Also fixes the
owner-reported disappearing-rows bug (R10) at the root. Scope:

- [ ] **Fix the selection identity bug (R10)**: every table row carries
      ``id = Segment ID``; all row lookups go through id-based props
      (``active_cell["row_id"]`` / row data), never positional indices into
      ``data``. Drop ``row_selectable``; re-evaluate ``fixed_rows`` (a plain
      sticky-header CSS scroll avoids the known misrender).
- [ ] **Corridor-scoped rows (R11)**: the table lists the segments of the
      corridor picked in the Corridor dropdown (Network scope: all segments —
      decide + record). One **Include** column (editable dropdown-presentation
      cell, ``Include``/``Exclude``), default all-Include; the member set =
      corridor minus exclusions, feeding the existing ``members=`` path and
      map dimming (which now dims only *excluded* segments).
- [ ] **Names in the store (R12)**: a ``_names`` table in ``store.py``
      (``Segment ID`` key → name, **last-write-wins** upsert — unlike the
      keep-first observation merge, an edit must overwrite; decide global vs
      per-area keying at the top of the session and record it). ``load_dataset``
      / the GUI load path applies stored names over the INRIX seed
      automatically; Save in the table writes to the store.
- [ ] **Retire the CSV workflow**: remove the ``Names CSV`` input, ``Write
      name template`` button, and the save-path status dance from the GUI;
      ``names.py``'s ``apply_names`` seed logic stays (it feeds the DB path);
      the now-unused CSV writers move to ``legacy/`` per CLAUDE.md style.
- [ ] **Decide + record:** do include/exclude sets also persist in the store
      (per area + corridor), or stay session-state? (Persisting matches the
      DB-first direction; ask the owner at the top of the session.)
- [ ] pytest: id-keyed selection survives a sorted view (unit-test the row→id
      helpers), exclusion set → ``members=`` mapping, names upsert round-trip
      (edit → save → reload applies), CSV controls gone from the layout.
      DESIGN_HISTORY entry; DATA_FORMAT gains the ``_names`` table.

*Suggested prompt:* "Do Item 27 of ROADMAP.md — corridor-scoped segment table
with an Include/Exclude column and names persisted in the DuckDB store, per
REVIEW_APP_2026-07-18.md R10–R12."

---

## 4. Model-assignment summary

| Item | What | Model | Why |
|---|---|---|---|
| 24 | DB-first intake inversion (owner request) | **Opus** | Pure GUI/layout + callback wiring; no math |
| 25 | Date push-down for DB loads | **Opus** | SQL + tz-boundary care, but engineering not statistics |
| 26 | Hardening batch (cursor, NULL keys, docstring) | **Sonnet** | Small, mechanical, well-specified fixes |
| 27 | Corridor-scoped table + include/exclude + names in DB | **Opus** | GUI redesign + store schema; fixes the R10 selection bug at the root |
| — | Post-batch verification pass | **Fable** | Per the owner's rule, Fable reviews; nothing here is math-heavy enough to need Fable implementing |
