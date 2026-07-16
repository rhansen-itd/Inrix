# Item 14 — Targeted review of the explorer (Fable, 2026-07-16)

A review-only pass over `gui/app.py`, `gui/figures.py`, and the
`beforeafter` / `decompose` / `changepoint` adapters, per ROADMAP Item 14.
Findings in priority order: **bugs → quick wins → broader ideas → statistical
rigor**. Every claim marked *verified* was reproduced by running code (scripts
in the session scratchpad; the two load-bearing experiments are reproduced
inline below). Nothing here changes code — accepted findings became ROADMAP
Items 15–16 plus one bullet added to Item 13.

**The one-paragraph summary:** the app's wiring is sound — the empty/edge paths
I attacked (short series through decompose→changepoint, empty compare frames,
malformed periods) all degrade gracefully. The two findings that matter are
statistical, not mechanical: (1) the Welch CI in `compare_periods` treats ~6,000
autocorrelated 5-min samples as independent, so the forest plot's intervals are
~5–9× too narrow — at realistic autocorrelation a nominal 95% CI covers the
truth only 25–50% of the time (verified by simulation); (2) the GUI's default
before/after windows silently **overlap** for exports shorter than ~60 days
(verified). Both make the headline numbers wrong while looking authoritative.
The main performance win is caching the decomposition independently of the
period dates (8.2s re-decompose per date-picker keystroke today, verified).

---

## 1. Bugs (concrete failure scenarios)

### B1 — Default before/after windows overlap on short exports  ⚠ verified
`gui/app.py:280-285`. On load, the default windows are
`window = max(21, span//5)` days, `before` starting `min(8, span//6)` days in,
`after` ending at the last day. For any span **< 60 days the two windows
overlap**, and **< ~30 days they also extend outside `[lo, hi]`** (outside the
pickers' own `min/max_date_allowed`). Measured:

| span | before | after | overlap |
|---|---|---|---|
| 45 d | day 9 → 30 | day 24 → 45 | **6 days shared** |
| 30 d | day 5 → 26 | day 9 → 30 | **17 days shared** |
| 14 d | day 2 → 23 | day −7 → 14 | almost total |

Failure scenario: load a 6-week export, open the Before/after tab without
touching the pickers → rows in the shared days sit in *both* samples, biasing
every effect toward 0 → "the intervention did nothing", presented as a sensible
default. Fix (Item 15): clamp defaults to disjoint halves of the span (after the
7-day decomposition warm-up), and have `compare_periods` itself reject/warn on
overlapping periods — it currently accepts them silently (`beforeafter.py:205-217`).

### B2 — `_DATASETS` grows without bound; ~2.3 GB per load  ⚠ verified size
`gui/app.py:81-88`. Every *Load export* click stores a new `Dataset` under a
fresh token; nothing is ever evicted. One Myrtle load costs **2.3 GB RSS**
(measured). Failure scenario: tweak CValue three times (each is a fresh load) →
~7 GB and climbing → OOM on a typical laptop. A single-user app needs to keep
only the latest dataset (or a size-1 LRU). Fix in Item 16.

### B3 — Speed-only export crashes every panel
`gui/app.py:120-121` → `:139`. `speed.metric_columns` legitimately returns
`None` for an absent metric, and the default metric radio is *Travel time*.
Load an export that carries only `Speed(...)` → `_metric_col` returns `None` →
`groupby(...)[None]` raises `KeyError` inside the map callback (and every
panel), leaving Dash error toasts, not a message. The Myrtle fixture has both
columns, which is why tests never see it. Fix (Item 16): guard on load, disable
the missing metric's radio option.

### B4 — Forest-plot hover shows the row number, not the segment
`gui/figures.py:282-291`. `y` is `list(range(len(d)))` and the hovertemplate is
`"%{y}<br>%{text}"` — `%{y}` substitutes the *data value* (the integer row
index), not the axis ticktext. Hovering the 24th row reads "23". The segment
name is already in `ytext`; put it in `customdata`/`text` instead. One-line fix
— added to Item 13's display-polish scope.

### B5 — Stale selection and stale viewport across export loads (minor)
- `gui/app.py:288`: `_load` returns `no_update` for `segment.value`, so after
  loading a *different* export the dropdown still holds the old export's segment
  id → every panel shows the generic "Pick or click a segment" blank until the
  user re-picks. Reset the value when the segment set changes.
- `gui/figures.py:139`: `uirevision="segment-map"` is a constant, so loading an
  export in a different city keeps the old pan/zoom — the new segments render
  off-screen. Key `uirevision` on the data token.

### B6 — Nits (each a one-liner, folded into Item 16)
- `gui/app.py:270`: `int(cvalue)` with a cleared numeric input (`None`) →
  the user sees `⚠ Load failed: int() argument must be…`. Default it instead.
- ToD slider with both handles at the same hour: `filter_time_window` treats
  `start == end` as *whole day* (`timebins.py:243-244` — documented for the
  string API, but surprising from a slider where it reads as "empty window").
- The decomposition tab's empty state says "No decomposition for this
  selection" — when the real reason is the 7-day warm-up (any series < ~8 days
  decomposes to 0 rows), say so; users will otherwise blame the data.

### Attacked and found sound (for the record)
- **Short/empty series through the whole decomp path** — 1-day and 5-day series
  return an *empty frame with the component columns*, `seasonally_adjust` and
  `detect_changepoints` both pass empties through, figures blank cleanly. No
  crash (verified end-to-end).
- Empty `compare_periods` results (no columns at all) are caught by
  `figures.beforeafter_forest`'s guard; `_shade_periods` swallows malformed
  period specs; `_load` failures surface in the UI; `_click_select` handles
  missing customdata; index alignment in `compare_periods` is safe because
  `load_data` concatenates with `ignore_index=True` (unique index).
- Thread-safety of the caches: worst case is a duplicated computation, never
  corruption — acceptable for single-user localhost.

---

## 2. Optimizations (measured on the real 1.87M-row Myrtle export)

### O1 — Decompose once per (metric, window), not per (metric, window, periods)  ⚠ the win
`gui/app.py:142-150`. `_compare_all` caches on
`(col, before, after, window)`, and `compare_periods` re-decomposes the **full
export** on every cache miss — measured **8.2 s**, ~90% of it decomposition.
The periods only *slice* the adjusted series after the fact, so keying the
expensive half on the dates is wasted: every date-picker change is a guaranteed
miss, and a `DatePickerRange` fires per *field*, so adjusting one range can
stall the app twice. Fix (Item 16): cache the seasonally-adjusted frame per
`(col, window)` (cap at ~2 entries — each is full-export sized) and compute the
per-period Welch stats from it on demand (sub-second). Same cache serves the
delta map, the forest, and (per-segment slice) the decomposition tab, whose
1.0 s/render currently re-runs on unrelated input changes too.

### O2 — Checked and *not* worth optimizing (negative results)
- `_segment_means` full-frame groupby per map redraw: **0.02 s** — leave it.
- `_segment_df` boolean scan per panel: **< 0.01 s** — leave it.
- Map trace rebuild (46 line traces + markers) per selection click: cheap at
  this segment count; revisit only if exports grow 10×.

### O3 — Load-time cache (optional, low priority)
`load_dataset` costs **10.4 s** per click, dominated by CSV parse from the zip.
A parquet sidecar keyed on the zip's mtime (like the existing geometry cache)
would cut reloads to ~1 s. Worth bundling into Item 11 (date subset), which
already touches the load path — not its own session.

---

## 3. Broader ideas — candidate ROADMAP items (prioritized, not implemented)

1. **Results export.** A "download CSV" for the forest table (+ changepoints),
   and a one-click self-contained HTML study report (config + the four panels)
   — what an engineer actually attaches to a memo. Small session.
2. **Day-of-week analysis filter + holiday exclusion.** The exact architectural
   twin of the Item 9 ToD window (`assign_day_group` already exists): restrict
   every panel to Mon–Thu, and exclude a user-listed set of holiday dates —
   holidays are classic before/after contaminants. Small session.
3. **Data-quality / coverage panel.** A segment×day completeness heatmap
   (CValue survival, `mark_complete_timestamps`) so users see *whether to
   trust* an effect before reading it. Note `speed.daily_timebin_summary` is
   currently a compute-core orphan — no GUI consumer; it belongs here or in a
   daily-trend view.
4. **Travel-time reliability metrics.** Before/after on the 95th-percentile
   travel time, planning-time and buffer index — interventions are often sold
   on reliability, not the mean; 5-min data supports it directly. Percentile
   CIs need a (block) bootstrap, so sequence after Item 15.
5. **Map as an answer surface.** Colour segments by changepoint date/magnitude
   or by FDR-significant effect (after Item 15), not only by mean/Δ. Small.
6. **Congestion-relative views.** The export carries `Ref Speed` / `Hist Av
   Speed`: delay = TT − free-flow TT, speed ratio vs reference; effects in
   vehicle-delay units read better than raw minutes. Medium.
7. **Difference-in-differences** (promote the Future item) — see §4.4.

Re-prioritization of the existing batch in light of the review: **15 → 16 → 11
→ 10 → 12 → 13** — the stats-validity fixes first (they change every number the
app shows), then responsiveness; then the owner batch, unchanged internally,
except Item 13 gains the B4 hover fix.

---

## 4. Statistical rigor of the before/after analysis

The architecture is right: seasonally adjust, compare levels, lead with effect
size + CI, keep the t-test as a labeled baseline (`beforeafter.py` does what
DESIGN_HISTORY says it does). Four substantive issues, in order:

### 4.1 The Welch CI ignores autocorrelation — intervals are ~5–9× too narrow  ⚠ verified
`beforeafter.py:81-120` computes `se = sqrt(vb/nb + va/na)` with `n` = the
number of **5-minute samples** (~6,000 per 21-day period). Adjacent 5-min
residuals are strongly dependent, so the effective sample size is far smaller
(×(1−ρ)/(1+ρ) for AR(1)). Simulated coverage of the *null* (no true change,
nominal 95%):

| residual AR(1) ρ | raw 5-min coverage | daily-mean coverage |
|---|---|---|
| 0.5 | 79.7% | 96.0% |
| 0.8 | **50.0%** | 96.3% |
| 0.95 | **25.3%** | 96.3% |

At traffic-realistic persistence, *half or more* of the "significant" segments
in a null world would show CIs excluding zero. The p-value being labeled
secondary doesn't help — the CI is the headline and is equally wrong. The
docstring caveats this for `ttest_baseline` but the primary method has the same
disease.

**Recommendation (Item 15):** aggregate the seasonally-adjusted series to
**daily means per segment** before `_compare_stats` (day-to-day dependence of
adjusted values is mild; the table shows coverage restored). n becomes "days",
which is also the honest unit of evidence. Alternatives if 30–60 day periods
feel n-starved: block bootstrap or a HAC/Newey–West SE — more machinery for
little gain here. Report `n_days` alongside `n` either way.

### 4.2 No multiple-comparisons handling across the forest
`compare_periods` emits one CI per segment (46 on Myrtle);
`figures.beforeafter_forest` draws them all at 95%. Even with valid per-segment
CIs, ~2–3 segments clear zero by chance in a null world — and today's
narrowness (§4.1) multiplies that. **Recommendation (Item 15):** add a
Benjamini–Hochberg `q_value` column in `compare_periods` (cheap, pure-core) and
de-emphasize non-significant rows in the forest; a caption stating the family
size. Simultaneous CIs are overkill for an exploratory tool.

### 4.3 Period validation
`compare_periods` accepts overlapping before/after periods (double-counts the
shared rows — see B1, where the GUI defaults do exactly this) and a `before`
period that begins inside the decomposition's 7-day warm-up silently loses
those days (`drop_days`, `decompose.py:106-108` documents it; nothing checks).
**Recommendation (Item 15):** raise on overlap, warn (attrs + GUI note) when a
period is truncated by the warm-up, and surface the *effective* n_days used.

### 4.4 The estimand — right for level shifts, two honest caveats
`seasonally_adjust` (= `median + resid`, `decompose.py:159-187`) retains the
level, so a step change survives adjustment — the right choice, and materially
better than comparing raw values or residuals. Two things it can't do:

- **Secular drift is attributed to the intervention.** Anything that moved
  travel time between the periods (seasonal demand beyond weekly cycles, fuel
  prices, a school calendar) lands in the effect. That is the textbook case for
  **difference-in-differences** against an unaffected control corridor — the
  Future item deserves promotion once a control exists in an export; the
  compute slots cleanly on top of the current adjusted series (effect =
  Δtreated − Δcontrol, same Welch/day-mean machinery).
- **Profile-shape changes are partially absorbed.** The daily/weekly seasonal
  components are estimated from the *whole* series, both periods included. An
  intervention that reshapes the daily profile (say, only the PM peak) leaks
  into `season_day`, shrinking the measured effect. The existing mitigation is
  already built: the Item 9 ToD window (filter-first-then-decompose) — worth a
  docstring note in `beforeafter.py` recommending a windowed run when the
  effect is expected to be time-of-day-specific, plus `by=['Day Group','Time
  Bin']` for diagnosis.

`ttest_baseline` needs no change — its caveats are documented and it is
honestly labeled. `changepoint` usage is sound (the GUI runs it on the adjusted
series, `gui/app.py:449-456`, exactly as the module docstring prescribes);
`score_threshold=5.0` is a heuristic without error control, which is fine for
its confirm-the-date role — just don't present changepoint *absence* as
evidence of no effect.
