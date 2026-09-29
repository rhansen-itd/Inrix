# Review — the recreational-corridor branch (commit `2f99514`, 2026-09-29)

A technical review of the commit that added recreational corridor screening on the
`recreational-corridors` branch ("Support recreational corridor screening, catalogue
derivation, and multi-day comparison"). It was written outside this project's
session process: no ROADMAP item, no DESIGN_HISTORY entry, one new test. Baseline:
**1031 tests pass, 31 skip** on the branch as committed.

**Summary.** The idea is right and the per-day-of-week recurrence fix is a real bug
fix. But the branch builds recreational corridors as a **second, parallel
catalogue** (`dN_rec_corridors.json`) on **different floors and a different delay
basis**, so its corridors cannot be ranked beside the commute ones. The committed
catalogues cannot be regenerated from the committed code. The `--relaxed` switch
*tightens* two of the limits it claims to relax. The owner's direction (2026-09-29)
is one integrated analysis: one catalogue per district whose corridors are
**tagged by type** (commute, recreational, others), each type discovered on its own
subset of days, and every corridor ranked together under selectable time
scenarios. Items 65–68 do that. Section 3 lists what is kept, what is fixed, and
what is retired.

---

## 1. Correctness findings

### F1 — The committed rec catalogues can't be regenerated from the committed code
All six `dN_rec_corridors.json` record `route_stitching` `join_tol_m 100`,
`bridge_max_miles 0.75`, `core_gap [4, 1.0]`, `min_core_vhd 0.5`,
`min_effective_core_miles 0.6`. Neither code path produces that set. The default
path gives 20 m / 2.0 mi / [2, 0.5] / 0.8 / 0.6. `--relaxed` gives 150 m / 1.5 mi /
[6, 2.0] / 0.2 / 0.2. So the files came from uncommitted code or ad-hoc flags. A
generated catalogue that its own builder can't reproduce is a hand-edited one.

### F2 — `--relaxed` tightens the turn and bridge limits
`build_statewide_catalogues.py` "relaxes" `max_turn_deg` to **110°**, but the
default `ROUTE_JOIN_MAX_TURN_DEG` is **120°**. It "relaxes" `bridge_max_miles` to
**1.5**, but the default `ROUTE_BRIDGE_MAX_MILES` is **2.0**. The help text states
defaults that don't exist: "50m", "90 deg" and "0.35 mi" for 20 m, 120° and 2.0 mi.

### F3 — `--min-effective-miles` is parsed and dropped
The script computes `min_effective_miles` (the flag, or 0.2 under `--relaxed`) and
never passes it to `generate_catalogue`. Every catalogue it writes uses 0.6.

### F4 — The rec cores are gated on a different delay than the commute cores
With `--rec-screen`, the builder discards `bins`/`curves` and reads
`segment_vhd_rec.parquet` as `curve_vhd`. That file is written by
`run_district_screening.segment_vhd`, which measures delay against **INRIX's
reference speed** (the ranking's free flow). The commute cores (Item 50) are
measured against **each segment's own night baseline**, and so are the floors
`MIN_CORE_VHD` and `MIN_CORE_VHD_PER_MILE`. The rec catalogue's peak/baseline
*ratios* use the night baseline, but its *VHD* (floors and facility order) uses
ref speed. So its cores are gated on the wrong quantity, and differently from
every other catalogue.

### F5 — The rec catalogue loses its monthly profile and seasonal flag
Dropping `bins` also drops `monthly`. No rec facility carries `_monthly_vhd` or
the `seasonal` / `episodic` flags. Those flags are the Item 50 tool built to say
"this core is summer-heavy". Recreational corridors are exactly where it matters.

### F6 — Different floors mean different inclusion rules
Leaving F1–F3 aside, the rec catalogue deliberately relaxes the core floors
(`min_core_vhd` 0.5, a gap of 4 segments / 1 mi). Any single ranking of commute
and recreational corridors then compares cores admitted under two different
rules. Route-stitching limits are worse: they change the **chains**, the network
the cores are found on. Two types built on two different chainings of the same
pavement can't be reconciled segment by segment. **Resolution (Item 66):** every
type is built on the same chains and the same floors. Only the days and hours
that define the type differ.

### F7 — Mixing day-gated windows pools their delay (latent, pre-existing; exposed here)
`segment_bin_screen` keys its delay cells by **month × day type × bin**, not by
window, and `aadt.curve_vehicle_hours_of_delay` joins windows to cells on those
keys. Suppose two windows in one run cover the same cell with different day
gates, e.g. `pm` (Mon–Fri 4–6:30 PM) and `fri` (Friday 9 AM–9 PM). Both land in
the `weekday` day type at 4 PM. Friday's "delay" there is then the Monday–Friday
mean. The branch avoids this only because each rec run happened to carry
`fri,sat,sun` alone. A run of `am,pm,fri`, which `--windows` accepts, is silently
wrong. **Fix (Item 65):** a guard raises when two windows in one bin screen share
a cell but cover different days in it.

### F8 — `segment_recurrence`: right fix, undocumented contract change
The fix is right. It counts the window's own gated days, so `sat` recurrence is
over Saturdays, not the weekdays a Saturday-only window never has. It also
changes **ungated** windows (`night`, `day_7d`) from weekday-only to every day.
The docstring still says "share of weekdays", and `<name>_n_weekdays` now counts
Saturdays. **Kept and documented (Item 65).** The only production caller
(`triage_candidates.py`) passes `am, pm`, which are unaffected.

## 2. Structural and hygiene findings

- **S1 — A parallel catalogue can't be joined.** `compare_recreational_vs_commute.py`
  joins the two statewide tables on `(district, corridor_group)`. Those ids come
  from two independent `generate_catalogue` runs. A shared id means the same road
  and place, **not** the same extent: in D3, 22 of 32 rec facility ids also exist
  in the commute catalogue, usually with different cores. A matching id pairs two
  different extents, and a non-matching one hides the same queue under two names.
  One typed catalogue removes the join entirely.
- **S2 — Duplicate presets.** `RECREATIONAL_WINDOWS["fri_sun"]` is identical to
  `"weekend_rec"`.
- **S3 — A season carried by the date range alone.** "Summer" exists only as
  whatever `--date-start/--date-end` the run was given (the comparison docstring
  says May–Aug). The map subtitles hard-code "(May–August)" whatever was run, and
  nothing in a window or its provenance says the data were seasonal. A seasonal
  Saturday and an all-year Saturday are different scenarios, and their names
  should say so.
- **S4 — Path-name magic.** `generate_statewide_maps.py` switches catalogues when
  `"rec" in str(base_dir)`.
- **S5 — Copy-paste per window.** Three near-identical map blocks per day, six
  hard-coded aggregate calls, a hard-coded tag list in `run_district_screening`.
  Adding a scenario should be adding a name, not editing four scripts.
- **S6 — Parameter sprawl in the core.** Thirteen threshold kwargs were threaded
  through six `extents` functions to support F2/F6's per-run relaxation, against
  CLAUDE.md's "explicit column names and typed returns over `**kwargs` sprawl".
  Once F6 is resolved (one set of floors), none are needed.
- **S7 — No tests** for any of the `extents` plumbing, `curve_vhd`, or the scripts.
- **S8 — `vhd_annual` assumes all-year gates.** It is `vhd × 365 × gate_days / 7`.
  For a seasonal window that overstates the annual figure by roughly 365/108.

## 3. Disposition

| Piece | Disposition |
|---|---|
| `segment_recurrence` day-gate fix | **Kept**, docstring and contract documented (F8) |
| Weekend windows (Fri/Sat/Sun 9 AM–9 PM) | **Kept** as the recreational type's windows and as scenarios, with a proper **season gate** instead of a date range (S3) |
| `--window-tag` / tagged output filenames | **Generalised** into named scenarios (Item 67) |
| `extents` threshold kwargs, `curve_vhd`, `--relaxed`, `--rec-screen`, `--filename-pattern` | **Reverted/removed** (F1–F4, F6, S6) |
| `dN_rec_corridors.json` ×6, `compare_recreational_vs_commute.py` | **Moved to `legacy/rec_catalogues_2026-09-29/`** with a README (F1, S1) |
| Per-window map/aggregate blocks | **Replaced** by loops over scenarios (S4, S5) |

## 4. Items

- **65 — Season-gated windows, named scenarios, and the window-overlap guard** (core,
  `screen` + `volume_profiles` + `aadt`). F7, F8, S2, S3, S8.
- **66 — Corridor types: one catalogue, tagged.** `extents.CorridorType` presets
  (commute, recreational, retail → urban hybrid), per-type discovery on the same
  chains and floors, and a deterministic cross-type merge. F1–F6, S1, S6. Depends
  on 65.
- **67 — One ranking, selectable scenarios.** `--scenarios` through the district
  and statewide runners, the aggregate and the maps. The type tags ride into every
  table, plus a cross-scenario matrix. S4, S5. Depends on 66.
- **68 — Regenerate and review on the real data** (owner-run: the exports are not
  in the cloud container). Depends on 67.
