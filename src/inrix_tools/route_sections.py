"""Route sections: how far along its road a count station's curve reaches.  (ROADMAP Item 61)

Item 59's station rule walked the XD links a fixed mile each way from a count station.
That was too short for how the counts were pulled: several ATRs were taken on I-84 on
the assumption that its profile stays similar along it, and every I-84 segment should
get a fitted curve. A station now covers its **section** of the route, traced the way
routes are traced (the Item 51 chains, :func:`extents.enumerate_mainline_chains`) and
broken where the owner expects the traffic to change (owner, 2026-09-25):

* at a **junction with a state route of the same or higher tier** — Eagle Rd (SH-55,
  State) breaks at Chinden (US-20, State); I-84 does not break at the SH-55 interchange
  (State is below Interstate) but does at the I-184, I-86 and I-15 system interchanges;
* at an Item 60 **hard stop** (``hard_stops.stop_boundaries(..., use="stations")``: a
  row scoped ``both`` or ``stations``);
* at the **route's end**: where the chain ends, or where the route leaves the road the
  chain follows (a concurrency ending, a renumbering merge the chain walked across).

**A change of tier along one route is not a break** without a junction there (owner:
the tier cut-off is somewhat arbitrary).

Tiers are ITD's Highway Tier layer (``itd_layers.segment_tiers``): Interstate >
Expressway > State > Regional > District. The owner counts US and SH routes as one
tier, which the layer does (both State on Eagle Rd and Chinden).

**What is a junction.** At a boundary between two consecutive segments of the section's
route ``r``:

1. *concurrency* — a route joins or leaves the pavement (in one segment's route set and
   not the other's);
2. *geometry* — a state-route segment that does not carry ``r`` passes within
   :data:`JUNCTION_TOL_M` of a section segment: a cross street at grade, or a road
   crossing at an interchange. XD segments break at intersections and interchange
   gores, not always at the overpass, so a crossing is placed on the boundary nearer
   to where it touches the segment.

The joining route's tier is that of its own segments there (the ones not carrying
``r``); the section route's tier is the lower of the two segments either side, with a
segment the layer leaves untiered taking the nearest tier along its run. A route that
crosses without an interchange (an overpass with no ramps) reads as a junction; in
Idaho's state system that is rare, and it only matters when the crossing route is of
the same or higher tier.

Pure: geopandas/shapely geometry only; no paths, no plotting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

JUNCTION_TOL_M = 25.0
"""A state-route segment this close to a section segment meets it (metres). XD lines of
crossing roads are digitised to meet, at grade and at a grade separation alike."""

JUNCTION_MERGE_MILES = 0.5
"""Two junction breaks with the same route this close along the section are one
interchange (a merge and a diverge), and only the first cuts. Without it a system
interchange strands a segment between its ramps as a section of its own."""

BREAK_JUNCTION = "junction"
BREAK_HARD_STOP = "hard_stop"
BREAK_ROUTE_END = "route_end"
BREAK_KINDS = (BREAK_JUNCTION, BREAK_HARD_STOP, BREAK_ROUTE_END)

BL_SUFFIX = " BL"
"""A business loop is its own route key (``"84 BL"``), as the station table writes it:
a station on Garrity Blvd counts the loop, not I-84."""

SECTION_COLUMNS = ("section_id", "route", "direction", "n_segments", "miles",
                   "start_kind", "start", "end_kind", "end", "first_seg", "last_seg")


@dataclass(frozen=True)
class RouteSection:
    """One direction of one route between two breaks, in travel order."""

    section_id: str
    route: str
    """The route key: a number (``"55"``), or a business loop's (``"84 BL"``)."""
    direction: str
    """``NB``/``SB``/``EB``/``WB``: the majority segment bearing."""
    segment_ids: tuple[int, ...]
    miles: float
    start: dict = field(default_factory=dict)
    """Why the section starts: ``{kind, detail}`` (:data:`BREAK_KINDS`)."""
    end: dict = field(default_factory=dict)

    @property
    def number(self) -> str:
        """The route number without a business-loop suffix."""
        return _number(self.route)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_DIR_LABEL = {"N": "NB", "S": "SB", "E": "EB", "W": "WB"}


def _direction(bearings: Iterable) -> str:
    vals = [str(b).strip().upper()[:1] for b in bearings if pd.notna(b) and str(b).strip()]
    if not vals:
        return "?"
    counts = pd.Series(vals).value_counts()
    return _DIR_LABEL.get(counts.index[0], counts.index[0])


def _route_keys(sets: Mapping[int, frozenset[str]], primary: Mapping[int, str],
                business: set[int]) -> dict[int, frozenset[str]]:
    """Each segment's route keys: its routes, with its primary route written ``"N BL"``
    where the segment is on that route's business loop."""
    out = {}
    for s, rs in sets.items():
        p = primary.get(s)
        out[s] = frozenset(f"{r}{BL_SUFFIX}" if s in business and r == p else r for r in rs)
    return out


def _runs(ids: Sequence[int], keys: Mapping[int, frozenset[str]]) -> list[tuple[str, int, int]]:
    """``(key, lo, hi)`` — every maximal run ``ids[lo:hi]`` whose segments all carry
    ``key``."""
    out = []
    for key in sorted({k for s in ids for k in keys.get(s, ())}):
        lo = None
        for i, s in enumerate(ids):
            on = key in keys.get(s, ())
            if on and lo is None:
                lo = i
            elif not on and lo is not None:
                out.append((key, lo, i))
                lo = None
        if lo is not None:
            out.append((key, lo, len(ids)))
    return out


def _fill_along(values: list[float]) -> list[float]:
    """NaNs take the nearest known value along the run (earlier wins a tie)."""
    arr = np.asarray(values, dtype=float)
    known = np.flatnonzero(~np.isnan(arr))
    if not len(known):
        return list(arr)
    out = arr.copy()
    for i in np.flatnonzero(np.isnan(arr)):
        out[i] = arr[known[np.argmin(np.abs(known - i))]]
    return list(out)


def _note(joins: dict[int, dict[str, float]], b: int, route: str, tier: float) -> None:
    """Record ``route`` meeting the run at boundary ``b``, keeping its highest tier."""
    here = joins.setdefault(b, {})
    prev = here.get(route, np.nan)
    here[route] = tier if np.isnan(prev) else prev if np.isnan(tier) else max(prev, tier)


def _tier_name(rank) -> str:
    from .itd_layers import TIERS
    return "untiered" if rank is None or pd.isna(rank) else TIERS[int(rank)]


def _number(key: str) -> str:
    """``"84 BL"`` → ``"84"``."""
    return key[:-len(BL_SUFFIX)] if key.endswith(BL_SUFFIX) else key


def _route_labels(net_idx: pd.DataFrame, sets: Mapping[int, frozenset[str]]
                  ) -> dict[str, str]:
    """``{route number: "US-20"}`` for every route in ``sets``, read as
    ``extents.enumerate_mainline_chains`` reads it (``RoadName`` and ``RoadList``)."""
    from .extents import route_label
    members: dict[str, list[int]] = {}
    for s, rs in sets.items():
        for r in rs:
            members.setdefault(r, []).append(s)
    names_col = net_idx.get("RoadName", pd.Series(dtype=object))
    list_col = net_idx.get("RoadList", pd.Series(dtype=object))
    frc_col = pd.to_numeric(net_idx.get("FRC", pd.Series(dtype=float)), errors="coerce")
    out = {}
    for r, ids in members.items():
        names = [str(v).strip() for v in names_col.reindex(ids).dropna() if str(v).strip()]
        names += [t.strip() for v in list_col.reindex(ids).dropna()
                  for t in str(v).split("|") if t.strip()]
        frc = frc_col.reindex(ids).dropna()
        out[r] = route_label(r, names, int(frc.min()) if len(frc) else None)
    return out


def _label(route: str, labels: Mapping[str, str]) -> str:
    base = labels.get(_number(route), _number(route))
    return f"{base}{BL_SUFFIX}" if route.endswith(BL_SUFFIX) else base


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------
def trace_sections(network, *, tiers: pd.Series | None = None,
                   hard_stops: Mapping[tuple[int, int], str] | None = None,
                   business: Iterable[int] = (),
                   chains=None,
                   junction_tol_m: float = JUNCTION_TOL_M,
                   metric_crs=None) -> list[RouteSection]:
    """Every state route's sections, per direction.

    Args:
        network: the XD network as the catalogue builder loads it — ``XDSegID``,
            ``NextXDSegI``, ``Bearing``, ``Miles``, ``RoadNumber``, geometry, with link
            repairs and ITD membership (``itd_routes`` / ``itd_route_id``) applied, and
            SHS mileposts for the Item 51 joins where available.
        tiers: tier rank per segment (``itd_layers.segment_tiers``' ``tier_rank``),
            indexed by ``XDSegID``; ``None`` = no tiers, so no junction breaks (only
            hard stops and route ends).
        hard_stops: ``hard_stops.stop_boundaries(resolved, use="stations")``.
        business: segments on a business loop (``profile_assignment.segment_context``'
            ``business``); their primary route is keyed ``"N BL"``.
        chains: ``extents.enumerate_mainline_chains(network, min_miles=0,
            hard_stops=hard_stops)``, when the caller already has them.
        junction_tol_m: see :data:`JUNCTION_TOL_M`.

    Returns:
        Sections sorted by route key, direction and first segment. A segment carrying
        several routes lies on a section of each (US-20/26 on I-84).
    """
    import geopandas as gpd
    from shapely import STRtree
    from shapely.ops import nearest_points

    from . import extents

    if network is None or len(network) == 0:
        return []
    net_idx = extents._ensure_indexed(network)
    net_idx = net_idx[~net_idx.index.duplicated()]
    if chains is None:
        chains = extents.enumerate_mainline_chains(network, min_miles=0.0,
                                                   hard_stops=hard_stops)
    sets = {s: r for s, r in extents.segment_route_sets(network).items()
            if s in net_idx.index}
    primary = {int(s): str(r).strip() for s, r in net_idx.get(
        "RoadNumber", pd.Series(dtype=object)).dropna().items()}
    keys = _route_keys(sets, primary, {int(s) for s in business})
    miles_of = pd.to_numeric(net_idx["Miles"], errors="coerce").fillna(0.0)
    bearing = net_idx.get("Bearing", pd.Series(dtype=object))
    rank = (pd.Series(tiers, dtype=float) if tiers is not None
            else pd.Series(dtype=float))
    labels = _route_labels(net_idx, sets)

    # Metric geometry of every state-route segment, for the junction test.
    ids = [s for s in sets if net_idx.at[s, "geometry"] is not None]
    if metric_crs is None:
        try:
            metric_crs = gpd.GeoDataFrame(net_idx.loc[ids], geometry="geometry",
                                          crs=network.crs).estimate_utm_crs()
        except Exception:
            metric_crs = "EPSG:3857"
    geo = gpd.GeoSeries(net_idx.loc[ids, "geometry"].values, index=ids,
                        crs=network.crs).to_crs(metric_crs)
    geo = geo[~geo.is_empty]
    gids = np.asarray(geo.index, dtype="int64")
    tree = STRtree(list(geo.values))

    # 1. Each chain, split into runs of one route key.
    runs: dict[tuple[str, tuple[int, ...]], dict] = {}
    for c in chains:
        ids_c = list(c.segment_ids)
        for key, lo, hi in _runs(ids_c, keys):
            seg = tuple(ids_c[lo:hi])
            if (key, seg) in runs:
                continue
            start = (c.stop_at("start") if lo == 0 else None)
            end = (c.stop_at("end") if hi == len(ids_c) else None)
            runs[(key, seg)] = {
                "start": ({"kind": BREAK_HARD_STOP, "detail": start["reason"]} if start
                          else {"kind": BREAK_ROUTE_END,
                                "detail": ("the route's walk starts here" if lo == 0
                                           else "the route joins this road here")}),
                "end": ({"kind": BREAK_HARD_STOP, "detail": end["reason"]} if end
                        else {"kind": BREAK_ROUTE_END,
                              "detail": ("the route's walk ends here" if hi == len(ids_c)
                                         else "the route leaves this road here")}),
            }
    # A run wholly inside a longer run of the same key adds nothing.
    by_key: dict[str, list[tuple[int, ...]]] = {}
    for key, seg in runs:
        by_key.setdefault(key, []).append(seg)
    keep = []
    for key, segs in by_key.items():
        segs.sort(key=len, reverse=True)
        taken: list[set[int]] = []
        for seg in segs:
            if any(set(seg) <= t for t in taken):
                continue
            taken.append(set(seg))
            keep.append((key, seg))

    # 2. Junction breaks inside each run, then the sections.
    out: list[RouteSection] = []
    for key, seg in keep:
        number = _number(key)
        own = _fill_along([rank.get(s, np.nan) for s in seg])
        joins: dict[int, dict[str, float]] = {}      # boundary index -> {route: tier}

        def _tier_near(point, route: str) -> float:
            """The highest tier among ``route``'s segments near ``point`` that do not
            carry the section's route."""
            best = np.nan
            for j in tree.query(point, predicate="dwithin", distance=junction_tol_m):
                o = int(gids[j])
                rs = sets.get(o, frozenset())
                if route in rs and number not in rs:
                    t = rank.get(o, np.nan)
                    best = t if np.isnan(best) or (not np.isnan(t) and t > best) else best
            return best

        for i in range(1, len(seg)):
            a, b = seg[i - 1], seg[i]
            changed = {k for k in keys.get(a, frozenset()) ^ keys.get(b, frozenset())
                       if _number(k) != number}
            if changed and a in geo.index:
                g = geo[a]
                end_pt = g.interpolate(g.length)
                for k in changed:
                    _note(joins, i, k, _tier_near(end_pt, _number(k)))
        on_run = set(seg)
        for i, s in enumerate(seg):
            if s not in geo.index:
                continue
            g = geo[s]
            mine = sets.get(s, frozenset())
            for j in tree.query(g, predicate="dwithin", distance=junction_tol_m):
                o = int(gids[j])
                rs = sets.get(o, frozenset())
                if o in on_run or number in rs:
                    continue
                other = {k for k in keys.get(o, frozenset()) if _number(k) not in mine}
                if not other:
                    continue
                # Where the two lines are nearest, placed on the nearer boundary.
                p, _ = nearest_points(g, geo[o])
                t_along = g.project(p, normalized=True) if g.length > 0 else 0.0
                b = i if t_along < 0.5 else i + 1
                if b <= 0 or b >= len(seg):
                    continue
                for r in other:
                    _note(joins, b, r, rank.get(o, np.nan))

        cuts: dict[int, str] = {}
        last: tuple[int, set[str]] | None = None
        cum = np.concatenate([[0.0], np.cumsum(miles_of.reindex(list(seg)).to_numpy())])
        for b, routes_at in sorted(joins.items()):
            mine_t = np.nanmin([own[b - 1], own[b]]) if not (
                np.isnan(own[b - 1]) and np.isnan(own[b])) else np.nan
            if np.isnan(mine_t):
                continue
            hits = sorted(r for r, t in routes_at.items() if not np.isnan(t) and t >= mine_t)
            if not hits:
                continue
            # One interchange, one break: the Flying Wye's I-184 merge and diverge
            # touch I-84 eastbound 0.1 mi apart.
            if last is not None and cum[b] - cum[last[0]] < JUNCTION_MERGE_MILES \
                    and last[1] & set(hits):
                continue
            last = (b, set(hits))
            cuts[b] = ", ".join(
                f"{_label(r, labels)} ({_tier_name(routes_at[r])})" for r in hits) \
                + f" meets {_label(key, labels)} ({_tier_name(mine_t)})"
        bounds = [0, *sorted(cuts), len(seg)]
        info = runs[(key, seg)]
        for lo, hi in zip(bounds, bounds[1:]):
            piece = seg[lo:hi]
            start = info["start"] if lo == 0 else {"kind": BREAK_JUNCTION,
                                                   "detail": cuts[lo]}
            end = info["end"] if hi == len(seg) else {"kind": BREAK_JUNCTION,
                                                      "detail": cuts[hi]}
            direction = _direction(bearing.reindex(list(piece)))
            out.append(RouteSection(
                section_id=f"{key.replace(' ', '')}-{direction}-{piece[0]}",
                route=key, direction=direction, segment_ids=tuple(int(s) for s in piece),
                miles=round(float(miles_of.reindex(list(piece)).sum()), 3),
                start=start, end=end))
    out.sort(key=lambda s: (len(s.number), s.route, s.direction, s.segment_ids[0]))
    return out


def sections_frame(sections: Sequence[RouteSection]) -> pd.DataFrame:
    """One row per section, :data:`SECTION_COLUMNS` — the review table."""
    rows = [{"section_id": s.section_id, "route": s.route, "direction": s.direction,
             "n_segments": len(s.segment_ids), "miles": s.miles,
             "start_kind": s.start.get("kind"), "start": s.start.get("detail"),
             "end_kind": s.end.get("kind"), "end": s.end.get("detail"),
             "first_seg": s.segment_ids[0], "last_seg": s.segment_ids[-1]}
            for s in sections]
    return pd.DataFrame(rows, columns=list(SECTION_COLUMNS))


def section_index(sections: Sequence[RouteSection]) -> dict[int, list[RouteSection]]:
    """``{segment: [sections it lies on]}``."""
    out: dict[int, list[RouteSection]] = {}
    for s in sections:
        for sid in s.segment_ids:
            out.setdefault(int(sid), []).append(s)
    return out
