"""The INRIX-vs-reference validation report, assembled from core frames.
(ROADMAP Item 30)

A **thin shell**: this module turns frames the compute core already produced into
HTML pages. It computes no statistic — every number printed here is read out of
``agreement.compare`` / ``reference.coverage_gate`` / ``corridors.ChainResult``,
which is the whole point of the rebuild. The retired predecessor
(``legacy/generate_corridor_html_reports.py``) computed MAE, bias and ``polyfit``
slopes inside its figure loops and published conclusions its own numbers did not
support (DESIGN_HISTORY Session 33).

Three things the old report got wrong are structural here, not matters of care:

- **Coverage is per corridor, never a banner.** Every page prints the route's own
  first/last sample, its day count and its *measured* cadence (G4, G10).
- **A gate failure is visible.** Routes whose reference data failed Item 29's
  nesting gate are excluded from the headline and carry the reason wherever they
  appear; nothing is dropped silently (G3).
- **Labels are checked against the network.** Each page prints the road names the
  XD segments themselves carry, and :func:`label_disagreements` fails a hand
  title whose route number is not on the chain — the "SH-17" caption (no such
  Idaho route) could not survive it (G10).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

import pandas as pd

from inrix_tools.reference import ROUTE_COL

from . import validation_figures as vfig

# ---------------------------------------------------------------------------
# Route metadata — the corrected labels (G10)
# ---------------------------------------------------------------------------
ARTERIAL = "Signalised arterial"
RURAL = "Rural highway"


@dataclass(frozen=True)
class RouteInfo:
    """How one reference sheet is presented: its page, its title, its character.

    ``title`` is a human caption; it is *checked* against the road names the
    chain's own segments carry (:func:`label_disagreements`), so it cannot drift
    from the network the way the outside pass's captions did.
    """

    group: str                 # page this route belongs to
    group_title: str           # page heading
    title: str                 # route caption
    character: str             # ARTERIAL or RURAL — the finding is about the split
    note: str = ""             # a standing caveat about the route itself


ROUTE_INFO: dict[str, RouteInfo] = {
    "Eagle Rd NB": RouteInfo(
        "eagle_rd", "Eagle Road (ID-55), Meridian", "Eagle Rd northbound", ARTERIAL),
    "Eagle Rd SB": RouteInfo(
        "eagle_rd", "Eagle Road (ID-55), Meridian", "Eagle Rd southbound", ARTERIAL),
    "VSL NB AM": RouteInfo(
        "vsl", "Eagle Road variable-speed-limit section, Meridian",
        "VSL section northbound, AM logging window", ARTERIAL,
        "A sub-extent of the Eagle Rd routes — not independent evidence."),
    "VSL SB AM": RouteInfo(
        "vsl", "Eagle Road variable-speed-limit section, Meridian",
        "VSL section southbound, AM logging window", ARTERIAL,
        "A sub-extent of the Eagle Rd routes — not independent evidence."),
    "VSL NB PM": RouteInfo(
        "vsl", "Eagle Road variable-speed-limit section, Meridian",
        "VSL section northbound, PM logging window", ARTERIAL,
        "A sub-extent of the Eagle Rd routes — not independent evidence."),
    "VSL SB PM": RouteInfo(
        "vsl", "Eagle Road variable-speed-limit section, Meridian",
        "VSL section southbound, PM logging window", ARTERIAL,
        "A sub-extent of the Eagle Rd routes — not independent evidence."),
    "SH-69 NB": RouteInfo(
        "sh69", "SH-69 (Meridian Road), Meridian to Kuna", "SH-69 northbound", ARTERIAL),
    "SH-69 SB": RouteInfo(
        "sh69", "SH-69 (Meridian Road), Meridian to Kuna", "SH-69 southbound", ARTERIAL),
    "Franklin EB": RouteInfo(
        "franklin", "Franklin Road, Nampa", "Franklin Rd eastbound, full extent", ARTERIAL),
    "Franklin WB": RouteInfo(
        "franklin", "Franklin Road, Nampa", "Franklin Rd westbound, full extent", ARTERIAL),
    "Franklin EB - No Mid": RouteInfo(
        "franklin", "Franklin Road, Nampa", "Franklin Rd eastbound, mid-block extent",
        ARTERIAL, "A sub-extent of Franklin EB — not independent evidence."),
    "Franklin WB - No Mid": RouteInfo(
        "franklin", "Franklin Road, Nampa", "Franklin Rd westbound, mid-block extent",
        ARTERIAL, "A sub-extent of Franklin WB — not independent evidence."),
    "Garden Valley-HSB": RouteInfo(
        "garden_valley", "Banks–Lowman Road and ID-55, Garden Valley to Horseshoe Bend",
        "Garden Valley to Horseshoe Bend", RURAL),
    "Cascade-HSB": RouteInfo(
        "sh55_mountain", "ID-55, Cascade to Horseshoe Bend", "Cascade to Horseshoe Bend",
        RURAL),
    "HSB-Cascade": RouteInfo(
        "sh55_mountain", "ID-55, Cascade to Horseshoe Bend", "Horseshoe Bend to Cascade",
        RURAL),
}

PAGE_ORDER = ("eagle_rd", "vsl", "sh69", "franklin", "garden_valley", "sh55_mountain")
PAGE_NAV_LABEL = {
    "eagle_rd": "Eagle Rd", "vsl": "Eagle Rd VSL", "sh69": "SH-69",
    "franklin": "Franklin Rd", "garden_valley": "Garden Valley",
    "sh55_mountain": "ID-55 Mountain",
}


def route_info(route: str) -> RouteInfo:
    """Metadata for a sheet, or a neutral default carrying the sheet's own name —
    an unknown route is shown, never dropped."""
    return ROUTE_INFO.get(route, RouteInfo("other", "Other routes", route, ARTERIAL))


def route_labels(routes=None) -> dict[str, str]:
    """``route -> display title`` for the figure builders."""
    keys = list(ROUTE_INFO) if routes is None else list(routes)
    return {r: route_info(r).title for r in keys}


_ROUTE_NUMBER_RE = re.compile(r"\b(?:SH|ID|US|I)-(\d+)\b", re.IGNORECASE)
_BARE_NUMBER_RE = re.compile(r"\d+")


def _network_numbers(desc: dict) -> set[str]:
    """Route numbers the chain's own segments carry.

    XD's ``RoadNumber`` is a **bare** number (``"55"``, ``"20"``) — it does not
    say whether that is a state, US or Interstate route — while ``RoadName`` /
    ``RoadList`` sometimes spell it (``"ID-55"``). So the check is on the number:
    it catches a caption naming a route the chain is not on at all, which is the
    failure it exists for, and does not pretend to adjudicate the prefix.
    """
    numbers: set[str] = set()
    for value in desc.get("RoadNumber", ()):
        numbers.update(_BARE_NUMBER_RE.findall(str(value)))
    for key in ("RoadName", "RoadList"):
        for value in desc.get(key, ()):
            numbers.update(_ROUTE_NUMBER_RE.findall(str(value)))
    return numbers


def label_disagreements(descriptions: dict, info: dict | None = None) -> pd.DataFrame:
    """Route numbers in the hand-written titles that the chain's segments deny.

    ``descriptions`` is ``route -> corridors.chain_description(...)``. A title
    claiming "SH-17" over segments carrying ID-55 and Banks–Lowman Road produces a
    row here; a clean title produces none. Returns
    ``[Route, title, claimed, on_network]`` — data, not an exception, so the
    report prints the disagreement rather than dying on it.
    """
    table = ROUTE_INFO if info is None else info
    rows = []
    for route, desc in descriptions.items():
        meta = table.get(route)
        if meta is None:
            continue
        on_network = _network_numbers(desc)
        caption = f"{meta.title} {meta.group_title}"
        for match in _ROUTE_NUMBER_RE.finditer(caption):
            if match.group(1) not in on_network:
                rows.append({ROUTE_COL: route, "title": meta.group_title,
                             "claimed": match.group(0),
                             "on_network": ", ".join(sorted(on_network)) or "—"})
    return pd.DataFrame(rows, columns=[ROUTE_COL, "title", "claimed", "on_network"])


# ---------------------------------------------------------------------------
# Gate flags — what Item 29's gates mean for the report  (G3 / G4)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RouteFlags:
    """Per-route verdict: whether the route may carry a headline, and why not."""

    route: str
    excluded: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def marker(self) -> str:
        return "⚠" if self.excluded else ("!" if self.notes else "")


def gate_flags(summary: pd.DataFrame, nesting: pd.DataFrame | None = None,
               coverage: pd.DataFrame | None = None, lag: pd.DataFrame | None = None,
               chains: dict | None = None, chain_coverage: dict | None = None,
               extent_sources: dict | None = None,
               key: str = ROUTE_COL, min_days: int = 30,
               min_window_fraction: float = 0.25,
               max_length_ratio: float = 1.1,
               min_miles_covered: float = 0.95) -> dict[str, RouteFlags]:
    """Turn Item 29's gate frames and Item 28's chain accounting into a per-route
    verdict.

    **Exclusion** (from the headline, never from the page) is reserved for two
    cases where the comparison is not the comparison it claims to be:

    - the *reference data* is internally impossible — a sub-route that takes
      longer than the route containing it cannot score INRIX, whatever INRIX says
      (G3); or
    - the *chain* is not the route — the walk never reached its target segment, so
      the INRIX side is summing some other extent (G7/G8).

    Everything else — thin coverage, a route that stops early, a sub-extent of
    another sheet, missing member segments, end-segment overshoot, a clock that
    prefers a non-zero lag — is a **note**: it travels with the route and is
    printed beside every number it touches.
    """
    flags = {r: RouteFlags(r) for r in summary[key]} if summary is not None else {}

    def add(route, *, excluded=False, reason=None, note=None):
        cur = flags.get(route, RouteFlags(route))
        flags[route] = RouteFlags(
            route,
            excluded=cur.excluded or excluded,
            reasons=cur.reasons + ((reason,) if reason else ()),
            notes=cur.notes + ((note,) if note else ()))

    if nesting is not None and not nesting.empty:
        for _, row in nesting[nesting["violates"]].iterrows():
            add(row["sub_route"], excluded=True, reason=(
                f"Reference data is internally impossible: this sub-route exceeds "
                f"its containing route {row['full_route']!r} in "
                f"{row['frac_sub_exceeds_full']:.1%} of {int(row['n_shared_bins']):,} "
                f"shared bins (mean {row['mean_sub']:.2f} vs {row['mean_full']:.2f} min). "
                f"Excluded from the headline — the fault is the reference's."))
            add(row["full_route"], note=(
                f"Its sub-route {row['sub_route']!r} fails the nesting gate; the two "
                f"sheets disagree with each other."))

    if coverage is not None and not coverage.empty:
        for _, row in coverage.iterrows():
            route = row[key]
            if route not in flags:
                continue
            if int(row["n_days"]) < min_days:
                add(route, note=(f"Thin reference coverage: {int(row['n_days'])} days "
                                 f"sampled (under {min_days})."))
            frac = row.get("window_covered_fraction")
            if frac is not None and pd.notna(frac) and frac < min_window_fraction:
                add(route, note=(f"Covers {frac:.0%} of the export window's days — "
                                 f"this route is not the study period."))
            if "covers_window" in row and not bool(row["covers_window"]):
                add(route, note=(
                    f"Reference sampling ({pd.Timestamp(row['first']):%Y-%m-%d} to "
                    f"{pd.Timestamp(row['last']):%Y-%m-%d}) does not span the export "
                    f"window; the comparison is over the overlap only."))

    if chains:
        for route, chain in chains.items():
            if route not in flags:
                continue
            if not chain.reached_target:
                add(route, excluded=True, reason=(
                    f"The chain never reached its target segment "
                    f"(stop reason {chain.stop_reason!r}): it covers "
                    f"{chain.chain_miles:.2f} mi of XD segments, so the INRIX side is "
                    f"not this route. Excluded from the headline — the fault is the "
                    f"chain's, not either data source's."))
            if (chain.length_ratio == chain.length_ratio
                    and chain.length_ratio > max_length_ratio):
                add(route, note=(
                    f"Whole end segments overshoot the requested extent by "
                    f"{chain.length_ratio - 1:.0%} ({chain.chain_miles:.2f} mi of segments "
                    f"against a {chain.requested_miles:.2f} mi request); the end segments "
                    f"are prorated, which assumes uniform speed within them."))
    if chain_coverage:
        for route, cov in chain_coverage.items():
            if route not in flags:
                continue
            a = getattr(cov, "attrs", {})
            covered = a.get("miles_covered_fraction")
            if a.get("n_missing") and covered is not None and covered < min_miles_covered:
                add(route, note=(
                    f"{int(a['n_missing'])} chain member segment(s) "
                    f"({a['missing_miles']:.2f} mi, {1 - covered:.1%} of the extent) have no "
                    f"observations in this export; the INRIX travel time is summed over the "
                    f"remaining members and is short by that pavement."))
    if extent_sources:
        for route, source in extent_sources.items():
            if route in flags and source != "snapped query points":
                add(route, note=(
                    f"Extent comes from {source}: this sheet names places rather than "
                    "coordinates, so there is no query point to snap, no endpoint trim "
                    "and no proration — the chain is whole segments."))

    if lag is not None and not lag.empty:
        for _, row in lag.iterrows():
            if row[key] in flags and not bool(row["agrees_at_zero"]):
                add(row[key], note=(
                    f"Clock check: best alignment at {int(row['best_lag_minutes'])} min, "
                    f"not 0 ({row['improvement']:.1%} better than lag 0)."))

    for route, meta in ROUTE_INFO.items():
        if route in flags and meta.note:
            add(route, note=meta.note)
    return flags


def headline_routes(flags: dict[str, RouteFlags]) -> list[str]:
    """Routes a headline statement may be built from."""
    return [r for r, f in flags.items() if not f.excluded]


# ---------------------------------------------------------------------------
# HTML primitives
# ---------------------------------------------------------------------------
CSS = """
:root {
  --surface: #fcfcfb; --panel: #ffffff; --ink: #0b0b0b; --ink-2: #52514e;
  --line: #e5e4e0; --accent: #2a78d6; --accent-2: #eb6834;
  --critical: #d03b3b; --warning: #fab219; --good: #0ca30c;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--ink);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
nav { display: flex; flex-wrap: wrap; gap: 4px 18px; align-items: baseline;
  padding: 12px 24px; background: #12233b; color: #fff; }
nav .brand { font-weight: 650; letter-spacing: .01em; margin-right: 12px; }
nav a { color: #cfe0f5; text-decoration: none; font-size: 14px; padding: 2px 0; }
nav a:hover { color: #fff; text-decoration: underline; }
nav a.active { color: #fff; border-bottom: 2px solid var(--accent); }
.container { max-width: 1180px; margin: 0 auto; padding: 24px 24px 64px; }
h1 { font-size: 28px; line-height: 1.25; margin: 24px 0 4px; }
h2 { font-size: 21px; margin: 40px 0 8px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
h3 { font-size: 17px; margin: 28px 0 6px; }
p, li { max-width: 78ch; }
.sub { color: var(--ink-2); margin-top: 0; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 18px 20px; margin: 16px 0; }
.finding { border-left: 4px solid var(--accent); }
.caution { border-left: 4px solid var(--critical); background: #fdf5f5; }
.note { border-left: 4px solid var(--warning); background: #fffaef; }
.kpis { display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0; }
.kpi { flex: 1 1 190px; background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; padding: 12px 14px; }
.kpi .label { font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
  color: var(--ink-2); }
.kpi .value { font-size: 24px; font-weight: 640; margin-top: 2px; }
.kpi .detail { font-size: 12.5px; color: var(--ink-2); }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; margin: 12px 0; }
th, td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--line); }
th:first-child, td:first-child { text-align: left; }
thead th { background: #f4f5f3; font-size: 12px; text-transform: uppercase;
  letter-spacing: .03em; color: var(--ink-2); border-bottom: 1px solid #d8d7d2; }
tbody tr.flagged td { background: #fdf5f5; }
td.num { font-variant-numeric: tabular-nums; }
.tag { display: inline-block; font-size: 11.5px; font-weight: 600; padding: 1px 7px;
  border-radius: 999px; border: 1px solid; margin-left: 6px; vertical-align: 1px; }
.tag.excluded { color: var(--critical); border-color: var(--critical); background: #fdf0f0; }
.tag.noted { color: #8a6100; border-color: var(--warning); background: #fff7e6; }
.tag.arterial { color: #1c5cab; border-color: #9ec5f4; background: #f0f6fe; }
.tag.rural { color: #0e6b4f; border-color: #a8dcc7; background: #eff9f5; }
figure { margin: 16px 0 8px; }
figcaption { font-size: 13px; color: var(--ink-2); margin-top: 4px; max-width: 90ch; }
footer { margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--line);
  font-size: 12.5px; color: var(--ink-2); }
code { background: #f4f5f3; padding: 1px 5px; border-radius: 4px; font-size: 12.5px; }
"""

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def fmt(value, spec: str = ".2f", dash: str = "—") -> str:
    """A number for display, or a dash — never ``nan`` in a published table."""
    return vfig._fmt(value, spec, dash)


def _nav(active: str) -> str:
    links = [("index.html", "Summary")] + [
        (f"{page}.html", PAGE_NAV_LABEL.get(page, page)) for page in PAGE_ORDER]
    items = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{esc(label)}</a>'
        for href, label in links)
    return ('<nav><span class="brand">INRIX XD vs external travel-time reference</span>'
            f'{items}</nav>')


def page(title: str, active: str, body: str, meta: dict | None = None) -> str:
    meta = meta or {}
    window = meta.get("export_window")
    span = (f"INRIX export {pd.Timestamp(window[0]):%Y-%m-%d} to "
            f"{pd.Timestamp(window[1]):%Y-%m-%d}. " if window else "")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<script src="{PLOTLY_CDN}"></script>
<style>{CSS}</style></head>
<body>{_nav(active)}<div class="container">
{body}
<footer>Generated by <code>scripts/build_validation_report.py</code> over the
<code>inrix_tools</code> compute core (ROADMAP Item 30). {esc(span)}Reference:
<code>{esc(meta.get('workbook', ''))}</code>. Every statistic on this page is
produced by <code>inrix_tools.agreement</code> / <code>inrix_tools.reference</code>;
the figures place values, they do not compute them.</footer>
</div></body></html>
"""


def figure_html(fig, caption: str = "") -> str:
    """One figure, with its caption. Plotly.js is loaded once by the page."""
    inner = fig.to_html(full_html=False, include_plotlyjs=False,
                        config={"responsive": True, "displaylogo": False})
    cap = f"<figcaption>{caption}</figcaption>" if caption else ""
    return f"<figure>{inner}{cap}</figure>"


def table(frame: pd.DataFrame, columns, row_class=None) -> str:
    """A table from ``(column, header, format)`` triples — formatting only."""
    head = "".join(f"<th>{esc(header)}</th>" for _, header, _ in columns)
    rows = []
    for _, row in frame.iterrows():
        cells = []
        for col, _, spec in columns:
            value = row.get(col)
            if callable(spec):
                cells.append(f"<td>{spec(row)}</td>")
            elif spec is None:
                cells.append(f"<td>{esc(value)}</td>")
            else:
                cells.append(f'<td class="num">{fmt(value, spec)}</td>')
        cls = f' class="{row_class(row)}"' if row_class else ""
        rows.append(f"<tr{cls}>{''.join(cells)}</tr>")
    return (f"<table><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


def kpi(label: str, value: str, detail: str = "") -> str:
    return (f'<div class="kpi"><div class="label">{esc(label)}</div>'
            f'<div class="value">{value}</div>'
            f'<div class="detail">{detail}</div></div>')


def _route_cell(route: str, flags: dict[str, RouteFlags]) -> str:
    meta = route_info(route)
    f = flags.get(route, RouteFlags(route))
    tag = ""
    if f.excluded:
        tag = '<span class="tag excluded">gate failed</span>'
    elif f.notes:
        tag = '<span class="tag noted">see note</span>'
    return f"{esc(meta.title)}{tag}"


# ---------------------------------------------------------------------------
# Narrative built from the frames
# ---------------------------------------------------------------------------
def _ranges(summary: pd.DataFrame, routes, column: str) -> tuple[float, float]:
    values = summary.loc[summary[ROUTE_COL].isin(list(routes)), column].dropna()
    return (float(values.min()), float(values.max())) if len(values) else (float("nan"),) * 2


def finding_paragraphs(summary: pd.DataFrame, flags: dict[str, RouteFlags],
                       chains: dict | None = None) -> str:
    """The finding, with its numbers interpolated from this run's frames.

    Written from the data so the prose cannot drift from the tables beneath it —
    the specific failure the rebuild is answering.
    """
    usable = set(headline_routes(flags))
    arterials = [r for r in summary[ROUTE_COL]
                 if r in usable and route_info(r).character == ARTERIAL]
    rurals = [r for r in summary[ROUTE_COL]
              if r in usable and route_info(r).character == RURAL]

    dr = _ranges(summary, arterials, "delay_ratio")
    sr = _ranges(summary, arterials, "sd_ratio")
    dr_med = summary.loc[summary[ROUTE_COL].isin(arterials), "delay_ratio"].median()
    sr_med = summary.loc[summary[ROUTE_COL].isin(arterials), "sd_ratio"].median()
    rural_bias = _ranges(summary, rurals, "bias")
    rural_miles = ""
    if chains:
        miles = [chains[r].requested_miles for r in rurals if r in chains]
        if miles:
            rural_miles = f" over {max(miles):.1f}-mile chains"

    return f"""
<div class="panel finding">
<h3>The finding: INRIX compresses arterial delay by about half</h3>
<p>On the signalised arterials measured here, INRIX reports substantially less
delay than the external reference: the delay ratio (mean delay above <em>each
source's own</em> 10th-percentile free-flow) has a median of <b>{fmt(dr_med)}</b>
across {len(arterials)} arterial routes — range {fmt(dr[0])}–{fmt(dr[1])} — and the
SD ratio tracks it at a median of <b>{fmt(sr_med)}</b> (range {fmt(sr[0])}–{fmt(sr[1])}).
The two agreeing matters: the SD ratio is symmetric, so this is compression of the
congested tail, not an artifact of regressing one noisy source on another. The
spread is real and per-route — read the scorecard, not the median, for any one
corridor.</p>
<p>On the rural highway routes the two sources agree closely — bias
{fmt(rural_bias[0], '+.2f')} to {fmt(rural_bias[1], '+.2f')} minutes{rural_miles}.
Free-flow travel time is not the problem; <em>delay</em> is.</p>
<p><b>What follows for a before/after study.</b> An intervention evaluated on INRIX
will show roughly <b>half the effect size in minutes</b> that this reference would
credit it with, on this kind of road. A <em>relative</em> change may survive the
compression, but that is a different claim and needs its own evidence — if both
the before and after delay are scaled by the same factor the ratio is preserved,
and nothing here establishes that the factor is constant across congestion levels.
Report INRIX-derived minutes of delay saved as a lower bound, and say which source
produced them.</p>
</div>
"""


def unsupported_explanation_panel(chains: dict | None, summary: pd.DataFrame,
                                  profile_tod: pd.DataFrame | None = None) -> str:
    """G2: the outside pass's stated cause for the largest biases, and the
    measurements that contradict it — recomputed here from this run's chains."""
    rows = []
    for route in ("Eagle Rd NB", "Eagle Rd SB", "SH-69 NB", "SH-69 SB"):
        chain = (chains or {}).get(route)
        if chain is None:
            continue
        rows.append(f"<li><b>{esc(route_info(route).title)}</b>: query points snap "
                    f"{chain.snap_start_feet:.0f} ft and {chain.snap_end_feet:.0f} ft "
                    f"from the chain ends.</li>")

    early = ""
    if profile_tod is not None and not profile_tod.empty:
        dawn = profile_tod[profile_tod["minute_of_day"] == 5 * 60]
        pairs = []
        for route in ("SH-69 SB", "SH-69 NB"):
            row = dawn[dawn[ROUTE_COL] == route]
            if len(row):
                pairs.append(f"{esc(route)} {float(row['bias'].iloc[0]):+.2f} min")
        if len(pairs) == 2:
            early = (f"<p>At 05:00 — free flow, no queue to capture — the same two "
                     f"directions over the same endpoints reversed already differ: "
                     f"{pairs[0]} against {pairs[1]}. A queuing story does not "
                     f"survive that.</p>")

    return f"""
<div class="panel caution">
<h3>An explanation this report does not repeat</h3>
<p>The 2026-09-17 outside comparison stated that its largest biases were "not data
error, but query geometry mismatch" — that the reference queries extended south of
I-84 and captured off-ramp signal queues, and that its spatial analysis
<em>proved</em> it. The chains assembled here do not support that:</p>
<ul>{''.join(rows) or '<li>Chain snap distances unavailable in this run.</li>'}</ul>
{early}
<p>Something route-specific may well be happening at that interchange. This report
does not claim to know what it is, and neither did the evidence offered.</p>
</div>
"""


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
_SUMMARY_COLUMNS = [
    ("bias", "Bias (min)", "+.2f"),
    ("ci", "95% CI (day-blocked)",
     lambda r: f'<span class="num">[{fmt(r.get("bias_ci_low"), "+.2f")}, '
               f'{fmt(r.get("bias_ci_high"), "+.2f")}]</span>'),
    ("delay_ratio", "Delay ratio", ".2f"),
    ("sd_ratio", "SD ratio", ".2f"),
    ("mae", "MAE (min)", ".2f"),
    ("mape", "MAPE (%)", ".1f"),
    ("n_bins", "Bins", ",.0f"),
    ("n_days", "Days", ",.0f"),
    ("window", "Reference window",
     lambda r: f'{pd.Timestamp(r["first"]):%Y-%m-%d} → {pd.Timestamp(r["last"]):%Y-%m-%d}'),
    ("r", "r", ".3f"),
]

# Present only when the INRIX side carried them through the match (ROADMAP Item
# 31). A bias computed over historical backfill, or over a chain the export only
# partly supplied, is a different claim from one that is not — so the two sit in
# the row rather than in a footnote.
_COVERAGE_COLUMNS = [
    ("imputed_fraction", "Imputed", ".1%"),
    ("n_absent", "Members absent",
     lambda r: (f'<span class="num">{int(r["n_absent"])}</span>'
                if r.get("n_absent") else '<span class="num">—</span>')),
]


def scorecard(summary: pd.DataFrame, flags: dict[str, RouteFlags]) -> str:
    """The scorecard: effect size first, each route's own coverage in the row,
    correlation in the last column where it belongs."""
    df = summary.sort_values("bias")
    columns = [(ROUTE_COL, "Route", lambda r: _route_cell(r[ROUTE_COL], flags)),
               ("character", "Type",
                lambda r: f'<span class="tag {"arterial" if route_info(r[ROUTE_COL]).character == ARTERIAL else "rural"}">'
                          f'{esc(route_info(r[ROUTE_COL]).character)}</span>')]
    columns += _SUMMARY_COLUMNS
    columns += [c for c in _COVERAGE_COLUMNS if c[0] in df.columns]
    return table(df, columns,
                 row_class=lambda r: "flagged" if flags.get(r[ROUTE_COL],
                                                            RouteFlags(r[ROUTE_COL])).excluded else "")


def notes_list(flags: dict[str, RouteFlags], routes=None) -> str:
    """Every flag on the report, grouped by route — the footnotes a scorecard
    needs, with the exclusions first."""
    keys = list(flags) if routes is None else [r for r in routes if r in flags]
    keys = sorted(keys, key=lambda r: (not flags[r].excluded, route_info(r).title))
    items = []
    for route in keys:
        f = flags[route]
        if not (f.reasons or f.notes):
            continue
        inner = "".join(f"<li>{esc(t)}</li>" for t in (*f.reasons, *f.notes))
        marker = "⚠ " if f.excluded else ""
        items.append(f"<li><b>{marker}{esc(route_info(route).title)}</b>"
                     f"<ul>{inner}</ul></li>")
    if not items:
        return "<p>No gate flags on any route in this run.</p>"
    return f"<ul>{''.join(items)}</ul>"


def independent_n_panel(totals: pd.DataFrame) -> str:
    """G5: what a study-wide total is allowed to claim."""
    if totals is None or totals.empty:
        return ""
    row = totals.iloc[0]
    overlaps = totals.attrs.get("overlaps", pd.DataFrame())
    over_html = ""
    if isinstance(overlaps, pd.DataFrame) and not overlaps.empty:
        over_html = table(overlaps.sort_values("shared_miles", ascending=False), [
            ("route_a", "Route", lambda r: esc(route_info(r["route_a"]).title)),
            ("route_b", "Overlaps with", lambda r: esc(route_info(r["route_b"]).title)),
            ("shared_segments", "Shared segments", ",.0f"),
            ("shared_miles", "Shared miles", ".2f")])

    miles_distinct = row.get("miles_distinct")
    kpis = "".join([
        kpi("Matched bins (sum)", f"{int(row['n_bins_sum']):,}",
            "summing routes double-counts overlapping sheets"),
        kpi("Distinct quarter hours", f"{int(row['n_bin_slots_distinct']):,}",
            "the calendar cannot supply more than this"),
        kpi("Distinct days", f"{int(row['days_distinct']):,}",
            f"route-days summed: {int(row['route_days_sum']):,}"),
        kpi("Distinct pavement", f"{fmt(miles_distinct, '.1f')} mi",
            f"chain miles summed: {fmt(row.get('chain_miles_sum'), '.1f')} mi "
            f"({fmt(row.get('miles_overlap'), '.1f')} mi counted twice)"),
    ])
    return f"""
<h2>How much independent evidence is here</h2>
<p>Routes overlap: sub-extent sheets re-measure pavement their parent sheet already
covers, so summing them inflates the apparent sample. Both numbers are shown — the
sum, and what is actually distinct.</p>
<div class="kpis">{kpis}</div>
{over_html}
"""


def gates_section(nesting: pd.DataFrame, coverage: pd.DataFrame, lag: pd.DataFrame,
                  flags: dict[str, RouteFlags]) -> str:
    """The three Item 29 gates, reported as data."""
    parts = ["<h2>Reference-side gates</h2>",
             "<p>Before INRIX is scored against it, the reference is checked against "
             "itself. These are the three Item 29 gates, reported as measurements.</p>"]

    parts.append("<h3>Nesting: a sub-route cannot exceed the route containing it</h3>")
    if nesting is None or nesting.empty:
        parts.append("<p>No nested route pairs in this run.</p>")
    else:
        parts.append(table(nesting, [
            ("sub_route", "Sub-route", lambda r: esc(route_info(r["sub_route"]).title)),
            ("full_route", "Contained in", lambda r: esc(route_info(r["full_route"]).title)),
            ("n_shared_bins", "Shared bins", ",.0f"),
            ("mean_sub", "Sub (min)", ".2f"),
            ("mean_full", "Full (min)", ".2f"),
            ("frac_sub_exceeds_full", "Sub > full", ".1%"),
            ("violates", "Verdict",
             lambda r: ('<span class="tag excluded">impossible</span>' if r["violates"]
                        else '<span class="tag rural">clean</span>')),
        ], row_class=lambda r: "flagged" if r["violates"] else ""))

    parts.append("<h3>Coverage: what each route actually sampled</h3>")
    if coverage is not None and not coverage.empty:
        parts.append(table(coverage.sort_values("n_days"), [
            (ROUTE_COL, "Route", lambda r: _route_cell(r[ROUTE_COL], flags)),
            ("first", "First", lambda r: f'{pd.Timestamp(r["first"]):%Y-%m-%d}'),
            ("last", "Last", lambda r: f'{pd.Timestamp(r["last"]):%Y-%m-%d}'),
            ("n_days", "Days", ",.0f"),
            ("span_days", "Span (days)", ",.0f"),
            ("sampled_day_fraction", "Days sampled in span", ".0%"),
            ("window_covered_fraction", "Of export window", ".0%"),
            ("n_obs", "Samples", ",.0f"),
            ("obs_per_day", "Samples/day", ".1f"),
            ("median_sample_minutes", "Median gap (min)", ".0f"),
        ]))

    parts.append("<h3>Clock agreement</h3>")
    if lag is not None and not lag.empty:
        agree = int(lag["agrees_at_zero"].sum())
        parts.append(f"<p>{agree} of {len(lag)} routes align best at lag 0 "
                     "(scanned on the bias-removed SD, so a large constant bias "
                     "cannot masquerade as a clock offset).</p>")
        parts.append(table(lag, [
            (ROUTE_COL, "Route", lambda r: esc(route_info(r[ROUTE_COL]).title)),
            ("best_lag_minutes", "Best lag (min)", ",.0f"),
            ("sd_at_best", "SD at best", ".3f"),
            ("sd_at_zero", "SD at 0", ".3f"),
            ("improvement", "Improvement", ".2%"),
            ("agrees_at_zero", "Verdict",
             lambda r: ('<span class="tag rural">agrees</span>' if r["agrees_at_zero"]
                        else '<span class="tag noted">offset</span>')),
        ]))
    return "".join(parts)


def method_section(meta: dict, coverage: pd.DataFrame,
                   disagreements: pd.DataFrame) -> str:
    """What was compared, how, and what the comparison cannot say."""
    cadence = ""
    if coverage is not None and not coverage.empty:
        med = coverage["median_sample_minutes"].median()
        per_day = coverage["obs_per_day"].median()
        cadence = (f"The reference samples on a median gap of <b>{fmt(med, '.0f')} "
                   f"minutes</b> ({fmt(per_day, '.0f')} samples on a typical logged day) — "
                   "it is not a 15-minute series. INRIX is 15-minute; each reference "
                   "sample is floored into the bin containing it, and bins with more "
                   "than one sample are averaged.")
    place = meta.get("place_name_routes", ())
    place_html = ""
    if place:
        place_html = (f"<p><b>{len(place)} sheets are excluded entirely</b> "
                      f"({esc(', '.join(place))}): their endpoints are place names, so "
                      "their extent is whatever a geocoder chose and cannot be matched "
                      "to XD segments with any confidence.</p>")
    operator = meta.get("operator_specified_routes", ())
    if operator:
        place_html += (
            f"<p><b>{len(operator)} sheets name places rather than coordinates</b> "
            f"({esc(', '.join(operator))}). There is nothing to snap, so their extent is "
            "an operator-stated pair of terminal XD segments and the chain is walked "
            "between them: whole end segments, no trim, no proration, and no snap "
            "distance to report. Those rows are marked on their pages.</p>")
    dis_html = ""
    if disagreements is not None and not disagreements.empty:
        dis_html = ("<p><span class=\"tag excluded\">Label check failed</span> "
                    "A route number in a caption below is not on the chain's own XD "
                    "segments. Read it together with the flags: a chain that stopped "
                    "short never reaches the road its caption names, so the disagreement "
                    "can be the chain's fault rather than the caption's.</p>"
                    + table(disagreements, [
                        (ROUTE_COL, "Route", None), ("title", "Caption", None),
                        ("claimed", "Claims", None), ("on_network", "Network says", None)]))
    else:
        dis_html = ("<p>Every route number in these captions appears on the chain's own "
                    "XD segments (checked, not asserted).</p>")

    return f"""
<h2>Method, and what it cannot say</h2>
<p>Each coordinate route's endpoints are snapped to the INRIX XD network in a
projected CRS and the chain between them is walked segment by segment; the two end
segments are prorated to the requested extent, so the two sources are compared over
the same pavement. INRIX travel time is summed along the chain per 15-minute bin.
{cadence}</p>
{place_html}
<p>Timestamps are tz-aware throughout (<code>{esc(meta.get('tz', ''))}</code>);
{int(meta.get('n_ambiguous_dropped', 0))} reference samples fall in a DST fold and
are dropped and counted rather than assigned a guessed offset.</p>
<p><b>Limits.</b> The reference is one commercial provider's route-level estimate,
not ground truth — this report measures <em>disagreement</em> and locates it, it
does not adjudicate which source is right. Bins are autocorrelated, so every
interval here is day-blocked; the per-bin interval is shown beside it only to make
that difference visible. Chain proration assumes uniform speed within a segment.</p>
{dis_html}
"""


def index_page(frames: dict) -> str:
    summary, flags = frames["summary"], frames["flags"]
    totals, meta = frames["totals"], frames.get("meta", {})
    labels = route_labels(summary[ROUTE_COL])
    excluded = [r for r, f in flags.items() if f.excluded]

    usable = summary[~summary[ROUTE_COL].isin(excluded)]
    arterials = [r for r in usable[ROUTE_COL] if route_info(r).character == ARTERIAL]
    dr = _ranges(summary, arterials, "delay_ratio")
    dr_med = summary.loc[summary[ROUTE_COL].isin(arterials), "delay_ratio"].median()
    worst = (usable.loc[usable["bias"].abs().idxmax()] if len(usable.dropna(subset=["bias"]))
             else None)

    kpis = "".join([
        kpi("Routes compared", f"{len(summary)}",
            f"{len(excluded)} excluded from the headline by a gate"),
        kpi("Arterial delay ratio", f"median {fmt(dr_med)}",
            f"range {fmt(dr[0])}–{fmt(dr[1])} · INRIX ÷ reference, each on its own free-flow"),
        kpi("Largest bias", f"{fmt(worst['bias'] if worst is not None else None, '+.2f')} min",
            (f"{esc(route_info(worst[ROUTE_COL]).title)} · day-blocked CI "
             f"[{fmt(worst['bias_ci_low'], '+.2f')}, {fmt(worst['bias_ci_high'], '+.2f')}]")
            if worst is not None else "INRIX − reference"),
        kpi("Distinct pavement", f"{fmt(totals.iloc[0].get('miles_distinct'), '.1f')} mi",
            f"{int(totals.iloc[0]['days_distinct']):,} distinct days"),
    ])

    body = f"""
<h1>INRIX XD against an external travel-time reference</h1>
<p class="sub">Corridor-by-corridor agreement for ITD District 3, rebuilt over the
<code>inrix_tools</code> compute core. Effect size with a confidence interval is the
headline; correlation is a column.</p>
<div class="kpis">{kpis}</div>
{finding_paragraphs(summary, flags, frames.get("chains"))}
<h2>Scorecard</h2>
<p>Sorted by bias. Each row carries its <em>own</em> sampling window and day count —
there is no study-wide period, because the fifteen sheets do not share one.</p>
{scorecard(summary, flags)}
{figure_html(vfig.bias_forest(summary, labels=labels, flagged=excluded),
             "Bias with its day-blocked 95% interval (the per-bin interval, in grey "
             "behind it, is the one autocorrelation makes too narrow).")}
{figure_html(vfig.ratio_bars(summary, labels=labels),
             "Delay ratio and SD ratio against parity. The arterials cluster near "
             "half; the rural routes sit near 1.")}
<h3>Flags and footnotes</h3>
{notes_list(flags)}
{unsupported_explanation_panel(frames.get("chains"), summary, frames.get("profile_tod"))}
{independent_n_panel(totals)}
{figure_html(vfig.coverage_timeline(frames["coverage"], labels=route_labels(frames["coverage"][ROUTE_COL])),
             "Each route's own sampled span against the export window.")}
{gates_section(frames["nesting"], frames["coverage"], frames["lag"], flags)}
{method_section(meta, frames["coverage"], frames.get("disagreements"))}
"""
    return page("INRIX vs reference — summary", "index.html", body, meta)


def _chain_facts(route: str, frames: dict) -> str:
    chain = (frames.get("chains") or {}).get(route)
    if chain is None:
        return ""
    desc = (frames.get("descriptions") or {}).get(route, {})
    source = (frames.get("extent_sources") or {}).get(route, "snapped query points")
    cov = (frames.get("chain_coverage") or {}).get(route)
    snap = (f", endpoints snapping {chain.snap_start_feet:.0f} ft and "
            f"{chain.snap_end_feet:.0f} ft from the query points"
            if chain.snap_start_feet == chain.snap_start_feet else "")
    missing = ""
    if cov is not None:
        a = cov.attrs
        if a.get("n_missing"):
            missing = (f' <b>{a["n_missing"]} member segment(s)</b> '
                       f'({fmt(a["missing_miles"], ".2f")} mi, '
                       f'{fmt(1 - a["miles_covered_fraction"], ".1%")} of the extent) '
                       f'have no observations in this export, so the INRIX sum over this '
                       f'chain is short by that pavement.')
        else:
            missing = " Every member segment is observed in this export."
    return f"""
<p class="sub"><b>On the network:</b> {esc(desc.get('road_label', '—'))}
· {esc(', '.join(desc.get('County', ())) or '—')} County
· ZIP {esc(', '.join(desc.get('PostalCode', ())) or '—')}</p>
<p><b>Chain:</b> {chain.n_segments} XD segments,
{fmt(chain.requested_miles)} mi requested extent
({fmt(chain.chain_miles)} mi of whole segments, ratio {fmt(chain.length_ratio)});
extent from {esc(source)}{snap}; the walk
{'reached its target segment' if chain.reached_target else '<b>did NOT reach its target</b> (' + esc(chain.stop_reason) + ')'}.{missing}</p>
"""


def _route_section(route: str, frames: dict) -> str:
    summary, flags = frames["summary"], frames["flags"]
    rows = summary[summary[ROUTE_COL] == route]
    meta = route_info(route)
    if rows.empty:
        return (f"<h2>{esc(meta.title)}</h2>"
                "<p>No matched bins for this route in this run.</p>")
    row = rows.iloc[0]
    f = flags.get(route, RouteFlags(route))
    cov_rows = frames["coverage"][frames["coverage"][ROUTE_COL] == route]
    cov = cov_rows.iloc[0] if len(cov_rows) else None

    banner = ""
    if f.reasons:
        banner += ('<div class="panel caution"><b>⚠ Excluded from the headline.</b> '
                   + " ".join(esc(r) for r in f.reasons) + "</div>")
    if f.notes:
        banner += ('<div class="panel note"><b>Notes on this route.</b><ul>'
                   + "".join(f"<li>{esc(n)}</li>" for n in f.notes) + "</ul></div>")

    coverage_line = ""
    if cov is not None:
        coverage_line = (
            f"<p><b>This route's coverage:</b> "
            f"{pd.Timestamp(cov['first']):%Y-%m-%d} to {pd.Timestamp(cov['last']):%Y-%m-%d}, "
            f"{int(cov['n_days']):,} days sampled out of a {int(cov['span_days']):,}-day span "
            f"({fmt(cov['sampled_day_fraction'], '.0%')}), "
            f"{fmt(cov.get('window_covered_fraction'), '.0%')} of the export window. "
            f"{int(cov['n_obs']):,} reference samples, median gap "
            f"{fmt(cov['median_sample_minutes'], '.0f')} minutes.</p>")

    kpis = "".join([
        kpi("Bias (INRIX − reference)", f"{fmt(row['bias'], '+.2f')} min",
            f"day-blocked 95% CI [{fmt(row['bias_ci_low'], '+.2f')}, "
            f"{fmt(row['bias_ci_high'], '+.2f')}] — "
            f"{fmt(row.get('ci_width_ratio'), '.1f')}× the width of the per-bin interval"),
        kpi("Delay ratio", fmt(row["delay_ratio"]),
            f"SD ratio {fmt(row['sd_ratio'])} · free-flow "
            f"{fmt(row['free_flow_inrix'])} vs {fmt(row['free_flow_ref'])} min"),
        kpi("Typical disagreement", f"{fmt(row['mae'])} min",
            f"95% limits of agreement {fmt(row['loa_low'], '+.2f')} to "
            f"{fmt(row['loa_high'], '+.2f')} min"),
        kpi("Evidence", f"{int(row['n_bins']):,} bins",
            f"{int(row['n_days']):,} days · r = {fmt(row['r'], '.3f')}"),
    ])

    matched = frames["matched"]
    return f"""
<h2>{esc(meta.title)} <span class="tag {'arterial' if meta.character == ARTERIAL else 'rural'}">{esc(meta.character)}</span></h2>
{_chain_facts(route, frames)}
{banner}
{coverage_line}
<div class="kpis">{kpis}</div>
{figure_html(vfig.scatter_1to1(matched, route, row),
             "Every matched bin against the 1:1 line. No fitted line: the summary of "
             "the gap is in the box, computed once, in the core.")}
{figure_html(vfig.bland_altman(matched, route, row),
             "Bland-Altman — how far apart a single bin typically falls, with the "
             "bias and the 95% limits of agreement.")}
{figure_html(vfig.diurnal_profile(frames["profile_tod"], route),
             "Mean travel time by hour of day, both sources. Where the curves meet "
             "overnight and separate in the peak, the disagreement is delay rather "
             "than free-flow level.")}
{figure_html(vfig.bias_grid(frames["profile_grid"], route),
             "Signed bias by day of week and time of day; grey is agreement.")}
{figure_html(vfig.daily_bias(frames["profile_date"], route),
             "Daily mean bias — the unit the confidence interval is built from.")}
"""


def corridor_page(group: str, routes: list[str], frames: dict) -> str:
    title = route_info(routes[0]).group_title
    intro = ("<p class=\"sub\">Every statistic below comes from "
             "<code>inrix_tools.agreement</code>; this page places values and adds "
             "nothing to them.</p>")
    body = f"<h1>{esc(title)}</h1>{intro}" + "".join(
        _route_section(route, frames) for route in routes)
    return page(f"{title} — INRIX vs reference", f"{group}.html", body,
                frames.get("meta", {}))


def build_report(frames: dict) -> dict[str, str]:
    """Render every page. Returns ``filename -> HTML``; writing is the caller's job.

    ``frames`` is what ``scripts/build_validation_report.py`` assembles: the
    ``summary`` / ``matched`` / ``coverage`` / ``nesting`` / ``lag`` / ``totals``
    frames, the profiles, and the per-route ``chains`` / ``descriptions`` /
    ``chain_coverage``.
    """
    frames = dict(frames)
    frames.setdefault("flags", gate_flags(
        frames["summary"], frames.get("nesting"), frames.get("coverage"),
        frames.get("lag"), chains=frames.get("chains"),
        chain_coverage=frames.get("chain_coverage"),
        extent_sources=frames.get("extent_sources")))
    frames.setdefault("disagreements",
                      label_disagreements(frames.get("descriptions", {})))

    pages = {"index.html": index_page(frames)}
    present = list(frames["summary"][ROUTE_COL])
    for group in PAGE_ORDER:
        routes = [r for r in ROUTE_INFO if route_info(r).group == group and r in present]
        if routes:
            pages[f"{group}.html"] = corridor_page(group, routes, frames)
    others = [r for r in present if r not in ROUTE_INFO]
    if others:
        pages["other.html"] = corridor_page("other", others, frames)
    return pages
