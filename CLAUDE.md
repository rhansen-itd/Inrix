# CLAUDE.md — inrix_tools working conventions

Read [ROADMAP.md](ROADMAP.md) first; sessions are scoped there. At the end of a
session, check off completed items, add a session entry to
[DESIGN_HISTORY.md](DESIGN_HISTORY.md), and update [DATA_FORMAT.md](DATA_FORMAT.md)
if anything you learned about the INRIX export changed.

This project deliberately follows the conventions of the sibling project
`~/econ_itd_tools/EVO/iprj_designer` (docs pipeline, pure-core/thin-GUI split,
session-sized ROADMAP items). When in doubt about process, look at how that
project does it.

## Architecture (non-negotiable)

- `src/inrix_tools/` is **pure Python** — no plotting, no Dash/GUI imports, no
  hardcoded file paths, no global state. All loading, time binning,
  aggregation, and statistics live here and are pytest-testable headless.
- Plotting figures and the Dash app in `gui/` are a **thin shell** over the
  compute core. A function must never both compute *and* plot — that fusion is
  the main thing this refactor exists to undo (the seed notebooks'
  `process_and_plot_*` functions did both). Compute returns a DataFrame; the
  caller (GUI or a notebook) turns it into a figure.
- Keep the core independent of the GUI framework. Dash is the current choice,
  but the compute layer must not assume it.

## traffic-anomaly: dependency, not vendored

Robust decomposition and changepoint detection come from the MIT-licensed
`traffic-anomaly` package (`pip install traffic-anomaly`), wrapped by a thin
adapter (`decompose.py` / `changepoint.py`) that maps INRIX column names onto
its schema and sets INRIX-appropriate defaults (`freq_minutes=5`,
`entity_grouping_columns=['Segment ID']`).

- **Do not copy the package into this repo.** It is CI-tested and maintained
  upstream; vendoring forfeits fixes and adds maintenance for no gain.
- If you ever need one function tweaked, copy *that function* into the adapter
  module **with attribution** (MIT requires the copyright notice) — never the
  whole package. Only fork the decomposition math itself if a real requirement
  forces it, and record that decision in DESIGN_HISTORY.
- It pulls in `ibis-framework[duckdb]`. That is expected; don't try to strip it.

## Data & units

- Canonical facts about the INRIX export (columns, units, timezone handling,
  CValue, Ref/Hist speeds, corridor travel-time summation) live in
  DATA_FORMAT.md. Read it before touching `io.py` or any analysis; update it
  when you learn something new about the format.
- **Units are US traffic-engineering:** speed in mph, travel time in minutes,
  distance in miles. Keep them; don't silently convert.
- **Timezones matter.** `Date Time` carries a local UTC offset and `UTC Date
  Time` is in Z. Parse tz-aware, convert to the corridor's local zone
  explicitly (e.g. `America/Denver`), and never drop tz to naive without a
  reason. This is where the seed notebooks were fragile.

## Analysis approach

Two different questions, kept distinct (the seed notebooks blurred them):

- **Before/after (intervention evaluation)** — the robust primary is
  decomposition-based: strip daily/weekly seasonality with
  `traffic_anomaly.decompose`, then compare the seasonally-adjusted values /
  residuals between periods and report **effect size + confidence interval**,
  not just a p-value. The old one-sample/paired **t-test is kept only as an
  interpretable baseline** (autocorrelation understates its p-values; many
  bins×segments create a multiple-comparisons problem) — label it as such.
- **Changepoint detection** — `traffic_anomaly.changepoint` locates *when* a
  persistent shift occurred without hand-specifying the boundary; use it to
  find/confirm intervention dates.
- Anomaly/incident flagging (`traffic_anomaly.anomaly`, z-score/GEH) is a
  future item, not in the current scope.

## Testing & data

- Tests go in `tests/`, run with `pytest` from the repo root using the project
  venv. New/changed compute code needs coverage.
- The raw INRIX exports are large and license-restricted (each download has an
  `EULA.txt`) — they are **gitignored** (`data/`, `*.zip`, `data.csv`). Treat
  the sample download `Myrtle_2026-02-01_to_2026-07-16_5_min_part_1.zip` as a
  read-only fixture; commit only small derived summaries, never the raw export.
- Generated output (KML, plots, processed CSVs) is gitignored — regenerate from
  the tools, don't commit it.

## Scoping new ROADMAP items

When handed a batch of requests, **group them into session-sized items** — each
item is one reasonable working session (plan + implement + tests + doc pass),
not one item per bullet. Merge small/related requests; split only when too big
for one session. Assign stable IDs continuing from the last used (never reuse
or renumber), note inter-item dependencies inline, and keep the
Target/Scope/Suggested-prompt format the existing items use.

## Style

- The seed notebooks stay in the repo root as historical seeds; port *from*
  them into `src/`, don't edit them in place. Superseded code goes to a
  `legacy/` dir rather than being deleted.
- Match existing module conventions as they land; prefer explicit column names
  and typed returns over positional/`**kwargs` sprawl.

## Finishing work is part of the same session

Every item is *certified and recorded*, not just coded — same session:

- pytest coverage for new/changed compute code,
- the DESIGN_HISTORY.md entry for the session,
- checking off the item's boxes in ROADMAP.md,
- a DATA_FORMAT.md update if you learned something about the export.
