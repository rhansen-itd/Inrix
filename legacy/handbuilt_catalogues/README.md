# Hand-built statewide catalogues (superseded by ROADMAP Item 46)

`build_statewide_catalogues.py` here is the Session 60 version: the District 1, 2,
4, 5 and 6 catalogues were written as hand-picked lat/lon hints per corridor
(`build_district_N_catalogue`), and the couplet entries were hand-authored. It
imported `inrix_tools.couplets` and never called it.

Item 46 replaced it with a generated pass (`scripts/build_statewide_catalogues.py`),
which walks each district's numbered mainline chains, cuts extents at detected
split points, and detects couplets from the topology. The five `dN_corridors.json`
files here are the hand-built catalogues as they stood at commit `e0fec0b`, kept
for diffing against the generated ones — see the Session 61 entry in
DESIGN_HISTORY.md for what moved.

Nothing imports this directory; it is kept per CLAUDE.md ("superseded code goes to
a `legacy/` dir rather than being deleted").
