# Separate recreational catalogues (retired by ROADMAP Item 66)

These six `dN_rec_corridors.json` files and `compare_recreational_vs_commute.py` came
from the 2026-09-29 recreational-corridor commit (`2f99514`). It built a second
catalogue per district from Friday/Saturday/Sunday 9 AM–9 PM congestion. Separately,
it ran summer-dated screening runs and compared the two statewide rankings.

They were reviewed in `REVIEW_RECREATIONAL_2026-09-29.md` and retired:

- **They can't be regenerated.** Their recorded thresholds (join 100 m, bridge
  0.75 mi, core gap 4 segments / 1 mi, `min_core_vhd` 0.5) match neither the builder's
  default path nor its `--relaxed` one (F1).
- **They are cored on a different delay basis** (ref speed) and **different floors**
  from the commute catalogues (F4, F6), so their corridors can't be ranked beside
  the commute ones.
- **The comparison joins two independent catalogues on id** (S1). A shared id is
  the same road and place, not the same extent.

What replaced them: one catalogue per district in which every corridor is **tagged**
(`_types`, `_class`). Each corridor type (commute, recreational, retail) is
discovered on its own windows, on the same chains and floors
(`extents.CORRIDOR_TYPES`, `merge_typed_catalogues`, `build_statewide_catalogues.py
--types`). The rankings run under selectable scenarios (Item 67), and every
scenario ranks the same corridors, so no join is needed.

Kept here for reference and diffing, per CLAUDE.md. Nothing reads them. The
comparison script's paths point at files the current pipeline no longer writes.
