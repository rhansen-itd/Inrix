# The curated District 3 catalogue (archived by ROADMAP Item 63)

`d3_corridors.json` is District 3's hand-curated screening catalogue: 52
directional entries in 26 reporting corridors, built by `rebuild_d3_catalogue.py`
in ROADMAP Item 44 (Session 55), with later edits from Items 49 and 51. It was
built from the Item 43 congestion runs and then checked against road geometry,
connectivity and delay density. Its endpoints were written by hand as lat/lon
pairs.

From Item 44 until Item 63 it was D3's primary catalogue, and the generated one
(`build_statewide_catalogues.py --districts 3`) ran alongside it as
`scripts/d3_corridors_generated.json`. On 2026-09-25, after Session 80, the owner
moved D3 to the generated catalogue for statewide consistency: one method in every
district. The Item 60 hard stops removed most of the reason to curate by hand.
`scripts/d3_corridors.json` is now the generated catalogue.

The files are kept here for reference and diffing, per CLAUDE.md ("superseded code
goes to a `legacy/` dir rather than being deleted"):

- `tests/test_d3_catalogue.py` and parts of `tests/test_corridors.py` still read
  this file. It is a stable real-network fixture for chain resolution.
- A statewide run can still screen D3 on it with
  `--catalogue-override 3=legacy/d3_curated/d3_corridors.json`.
- `rebuild_d3_catalogue.py` rewrites the file in place here. Nothing else imports
  it.

Session 82 in DESIGN_HISTORY.md lists each curated corridor that has no generated
counterpart, and why.
