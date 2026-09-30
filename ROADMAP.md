# Roadmap — inrix_tools

Work is broken into **named, numbered items**, each sized for one focused
working session (plan + implement + tests + doc pass), following the convention
of the sibling `iprj_designer` project.

- The number is a **stable ID**, assigned once and never reused or renumbered —
  not an execution order.
- **File order is priority order**, read top to bottom; dependencies are noted
  inline.
- Each item carries a **Target** model and a **Suggested prompt**. **Since
  2026-09-30 the owner's aim is to spend fewer *Claude* tokens** (Gemini is not
  usage-constrained), so the default is **Opus plans, Gemini executes**:
  - Opus writes the spec and golden tests;
  - Gemini implements and re-runs via `delegate`
    (see `~/.claude/CLAUDE.md`, *Hybrid Claude + Gemini workflow*);
  - Opus reviews the report and closes the item (DESIGN_HISTORY, boxes).

  Item 69 was the first done this way. Math, statistics and class/threshold calls
  stay with Opus. This replaces the earlier "Opus end-to-end, no cross-model
  hand-off" default (and its *math-heavy → Fable* rule of 2026-07-16); older items'
  *Sonnet-eligible* tags now read as *Gemini-eligible*.
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

**Items 65–68 are a new batch** scoped 2026-09-29 (DESIGN_HISTORY Session 85) after a
review of the recreational-corridor branch
([REVIEW_RECREATIONAL_2026-09-29.md](REVIEW_RECREATIONAL_2026-09-29.md)). The owner
wants **one** analysis: every corridor tagged by type (commute from Monday–Friday,
recreational from summer weekends, and a retail type whose overlap with commute marks
an urban hybrid), each type discovered on its own days but on the same chains and
floors, then ranked together under selectable scenarios. **65** adds season-gated
windows and named scenarios; **66** adds the typed catalogue; **67** adds the scenario
rankings; **68** is the owner's real-data regeneration. **65–68 are done** (Sessions 86–89); **69**, from the review of the real run (Session 90), is next.

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

# Archived items 19–69

Finished items, one line each. The full scopes, decisions and suggested prompts
are in [ROADMAP_ARCHIVE.md](ROADMAP_ARCHIVE.md) (moved verbatim from
`011a5bd` lines 252–3892, in their original file order, with the batch
preambles); the build record is in DESIGN_HISTORY.md.

## 19 — Interactive segment table ✅ (Session 24) — archived
## 20 — GUI map & layout display polish ✅ (Session 25) — archived
## 22 — Directional segment display — merged into Item 20 — archived
## 21 — DB-backed storage & ingest ✅ (Session 26) — archived
## 23 — Area-based store: merge by corridor set + bin partition ✅ (Session 27) — archived
## 24 — DB-first intake: invert the data controls ✅ (Session 29) — archived
## 25 — Date push-down: Restrict-dates on DB loads ✅ (Session 30) — archived
## 26 — Store & app hardening ✅ (Session 31) — archived
## 27 — Corridor-scoped segment table + names in DB ✅ (Session 32) — archived
## 28 — Corridor chain assembly in the core (trim, verify, account) ✅ (Session 34) — archived
## 29 — External travel-time reference: load, gate, and compare ✅ (Session 35) — archived
## 30 — Validation report as a thin shell + record the arterial-delay finding ✅ (Session 36) — archived
## 31 — Chain membership accounting: requested vs observed, and a CValue gate ✅ (Session 43) — archived
## 32 — Report the delay compression as a slope, not a ratio of means ✅ (Session 44) — archived
## 33 — The directional free-flow level gap (SH-69 SB, Eagle Rd) ✅ (Session 45) — archived
## 34 — The AADT join picks ramps over the mainline on divided highways ✅ (Session 39) — archived
## 35 — District-wide screening: which corridors are worst? ✅ (Session 40) — archived
## 36 — The D3 corridor catalogue, as endpoint pairs ✅ (Session 41) — archived
## 37 — KML: the categorical palette collides silently ✅ (Session 42) — archived
## 38 — The XD topology, repaired as data: carriageway-scoped link repair ✅ (Session 47) — archived
## 39 — The district screening runner: D3's actual rankings ✅ (Session 48) — archived
## 40 — One corridor is both directions: reporting corridors ✅ (Session 49) — archived
## 41 — The reporting table: every direction × peak visible, ranked per mile ✅ (Session 50) — archived
## 42 — Reconcile the export's segment set against the corridor inventory ✅ (Session 52) — archived
## 43 — Corridors extracted from recurring congestion, not drawn from landmarks ✅ (Session 54) — archived
## 44 — Rebuild the D3 catalogue from the extracted runs ✅ (Session 55) — archived
## 45 — Statewide Corridor Extent Alternatives, Couplet Synthesis, and Objective Triage ✅ (Sessions 61–62, completed by Item 46 / Session 63) — archived
## 45R — Review fixes: the statewide VHD/mile join ✅ (Session 60) — archived
## 46 — Wire the extent/couplet detectors into the statewide catalogue builder ✅ (Session 63) — archived
## 47 — Screening-pipeline hardening: cache keying, hoisted joins, script hygiene ✅ (Session 64) — archived
## 48 — Route membership from ITD's AADT layer, not INRIX `RoadNumber` ✅ (Session 65) — archived
## 52 — ITD reference layers: the state highway system, AADT 2025, and urban areas ✅ (Session 67) — archived
## 49 — Ingest the Item 48 add-list and re-run the statewide screening ✅ (Sessions 66, 68) — archived
## 50 — Corridor cores from recurring congestion, not TTI against INRIX's ref speed ✅ (Session 69) — archived
## 51 — Chains across route-numbering changes, and couplets that are real ✅ (Session 70) — archived
## 53 — Couplet legs carry one-way AADT; everything else carries two-way ✅ (Session 72) — archived
## 54 — AADT per direction: halve two-way counts instead of doubling one-way ✅ (Session 73) — archived
## 55 — Volume-profile curve library + MADT carried through the AADT join ✅ (Session 75) — archived
## 56 — Assign a curve to every XD segment: inferred orientation, urban rule, override ✅ (Session 76) — archived
## 57 — Curve-weighted VHD in the compute core ✅ (Session 77) — archived
## 58 — Curve-weighted VHD everywhere: consumers, floor rescale, ranking comparison ✅ (Session 78) — archived
## 59 — Curves from counts: importers + fitting ✅ (Session 79) — archived
## 60 — Manual hard stops for corridor stitching ✅ (Session 80) — archived
## 61 — Station coverage by route section (replaces the Item 59 one-mile walk) ✅ (Session 81) — archived
## 64 — Corridor names without the town, and companion cores that face the lead ✅ (Session 83) — archived
## 62 — Statewide ATR pull, refit, and the statewide re-run on fitted curves ✅ (Session 84) — archived
## 63 — Generated D3 as primary, couplet-leg pairing, and adopting the stop-cut catalogues ✅ (Session 82) — archived
## 65 — Season-gated windows, named scenarios, and the window-overlap guard ✅ (Session 86) — archived
## 66 — Corridor types: one catalogue per district, every corridor tagged ✅ (Session 87) — archived
## 67 — One ranking, selectable scenarios ✅ (Session 88) — archived
## 68 — Regenerate the typed catalogues and the statewide scenario run (owner-run) ✅ (Session 89) — archived
## 69 — Compare corridor types on equal-length windows; check the summer I-90 work zone ✅ (Session 91) — archived

---

## Future (not yet scoped — need a planning pass before they're actionable)

- **Class by delay burden, not only by congestion intensity (option).** Item 69
  classifies on `peak_ratio_k`, the worst 2-hour travel time over baseline. That
  measures how *congested* a corridor gets in each type's windows, and the owner
  (2026-09-30) accepts it as the meaning of `_class`. It is not *when the delay
  burden falls*: SH-75 Ketchum stays `commute` (weekday AM 1.57 vs 1.13 on summer
  weekends) although its summer-weekend VHD per mile is nearly double its weekday
  one, because weekend volume is higher. If a burden-based class is ever wanted, it
  needs a VHD measure normalised for window length (VHD per window-hour, or the
  worst k hours of curve-weighted VHD). Keep `_class` as intensity and add it
  beside it rather than replacing it.
- **Directional AADT (direction-aware *volume* + a time-of-day directional
  factor)** — *scoped as Items 55–59 (2026-09-24).* The layer carries no direction
  field. The per-direction count is Item 54, and the time-of-day directional split
  comes from each direction's assigned volume-profile curve (AM- vs PM-commute).
  The map UX (sign vs selector) stays out of scope: no new GUI.
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
