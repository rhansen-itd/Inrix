"""State-route membership from ITD's layers, not INRIX ``RoadNumber``.  (ROADMAP Items 48, 52)

Which XD segments *are* US-12? Until Item 48 the answer was INRIX's ``RoadNumber``,
and it is wrong in both directions:

* it numbers streets the state no longer carries — Lewiston's downtown Main St / D St
  couplet is ``RoadNumber`` 12, but ITD's layer carries both streets on local (``OH``)
  records, and US-12 runs the levee bypass;
* it leaves real state highway unnumbered — that bypass is ``Levee Byp`` with no
  ``RoadNumber`` at all, so a route inventory built from ``RoadNumber`` never asked
  for it.

ITD's ``Cumulative_AADT`` layer is the state's own route inventory, so it decides —
with three limits this module is built around:

1. **It carries one record where routes share a road.** US-20/26/93 near Arco is a
   single ``02220AUS093`` record; INRIX says 20. Its ``RoadList``
   (``US-20|US-26|US-93|…``) names all three, so a number mismatch the segment's own
   ``RoadList`` explains is a **concurrency**, never an error.
2. **Its descriptions name the cross street at each break**, not the road
   (``01910AUS012`` on the levee reads ``5TH ST``, ``18TH ST/DIKE BYPASS RD``), so
   the street-name identity test of :func:`aadt.classify_on_system` cannot be used
   here — it rejects the bypass. Identity is geometric instead: the record must lie
   *on* the segment (``ON_DISTANCE_M``) and run beside most of it
   (``ON_MIN_COVERAGE``).
3. **It is not always the newer source.** Reisenauer Rd south of Moscow is still a
   US-95 record in the layer, but US-95 moved to its new alignment in ~2025 and the old
   road went to the county; INRIX is right there. An owner-reviewed override table
   (:func:`load_route_overrides`) beats the layer, and a segment INRIX numbers that no
   ITD record confirms either way is **kept and flagged**, never dropped — that is
   what new construction looks like. (Since Item 52 the State Highway System settles
   Reisenauer Rd and the new alignment, and the override row is retired.)

The evidence is also asymmetric on purpose. Taking a route *away* needs a local record
on the segment **and** no numbered record anywhere near it (``NEAR_DISTANCE_M``): a
divided highway's one centreline lies 22–30 m off each carriageway, and a frontage
road record can sit nearer the carriageway than that. Giving a route to an unnumbered
segment needs a numbered mainline record on it that no local record matches as well.

**Item 52: the State Highway System decides first.** ITD's ``SHS_Primary`` layer
(:func:`inrix_tools.itd_layers.load_shs`) carries only state highway, is current (a
``FromDate`` of 2026-07-28 at the latest), and has a line for each carriageway of a
divided road. Given it, :func:`route_membership` reads identity from it
(:func:`_shs_verdict`):

* a route runs on a segment when a **member** SHS roadway line (``RouteTypeC``
  mainline / spur / connector) of that route is *near* it — the same 40 m / 50% test;
* a numbered segment with **no** SHS line near it is off the system (``inrix_only``);
  the layer is complete, so absence is the evidence the AADT layer needed a local
  record for. That settles Reisenauer Rd without its override, and the ``unconfirmed``
  verdicts of Item 48;
* a segment on an SHS **business loop** (``RouteTypeC`` 3) is state highway and belongs
  to its parent route (I-90 BL is in the I-90 inventory, as it always was), labelled
  ``business`` so an analysis can tell loop from mainline. It keeps any other route
  INRIX's ``RoadList`` gives it (Caldwell Blvd stays SH-55 too). A street that stopped
  being a business loop (Caldwell's Cleveland Blvd / Blaine St) has no SHS line and is
  dropped like any other off-system road;
* the AADT layer decides only where the SHS cannot: a numbered segment with an SHS
  line of another route *near* but not *on* it (a frontage road beside the highway).

Pure: no file paths, no plotting. :func:`route_membership` takes the geometry frame, the
SHS layer and/or the AADT layer, and returns one row per segment; :func:`apply_route_membership` writes
the resolved route onto a network so the chain and couplet code downstream
(:mod:`extents`, :mod:`couplets`), which read ``RoadNumber``, need no change.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd

from . import aadt as aadt_mod

# ---------------------------------------------------------------------------
# Thresholds — metres, and the share of a segment a record runs beside
# ---------------------------------------------------------------------------
ON_DISTANCE_M = 10.0
"""A record this close is *on* the segment (the same centreline, digitised twice)."""
ON_COVER_TOL_M = 15.0
"""Coverage for the *on* test counts sample points within this distance."""
ON_MIN_COVERAGE = 0.8
"""…and at least this share of them must be alongside."""
NEAR_DISTANCE_M = 40.0
"""A numbered record this close still vouches for a segment's route: far enough to
reach a divided highway's centreline (22–30 m) from either carriageway."""
NEAR_MIN_COVERAGE = 0.5
BEARING_TOL_DEG = 45.0
CARRIAGEWAY_MIN_COVERAGE = 0.25
"""Where the SHS draws both carriageways of a route (Item 52), a segment *of* that route
has at least this share of its length within ``ON_COVER_TOL_M`` of one of them. The
real carriageways measured run 0.45–1.0; Silver Valley Rd, a frontage road between
I-90's lines, runs 0.0."""

# Verdicts
AGREE = "agree"              # ITD confirms INRIX's own number
CONCURRENT = "concurrent"    # ITD names another route the segment's RoadList carries
RENUMBERED = "renumbered"    # ITD puts the segment on a different route than INRIX
INRIX_ONLY = "inrix_only"    # INRIX numbers it; ITD carries it as a local road -> dropped
ITD_ONLY = "itd_only"        # ITD route; INRIX has no number -> added
UNCONFIRMED = "unconfirmed"  # INRIX numbers it; no ITD record decides -> kept, flagged
OFF_SYSTEM = "off_system"    # neither source numbers it
BUSINESS = "business"        # on a business loop: its parent route (+ INRIX's), labelled
RAMP = "ramp"                # ramps keep INRIX's reading; the join handles their volume
OVERRIDE = "override"        # an owner-reviewed override row decided

VERDICTS = (AGREE, CONCURRENT, BUSINESS, RENUMBERED, INRIX_ONLY, ITD_ONLY, UNCONFIRMED,
            OFF_SYSTEM, RAMP, OVERRIDE)
"""Every verdict :func:`route_membership` can return."""

SOURCE_SHS = "shs"          # the State Highway System decided (Item 52)
SOURCE_AADT = "aadt"        # the AADT layer decided (Item 48; the fallback since Item 52)
SOURCE_OVERRIDE = "override"
SOURCE_NONE = "none"        # a ramp, or no layer had anything to say

CHANGED = (RENUMBERED, INRIX_ONLY, ITD_ONLY, OVERRIDE)
"""Verdicts where the resolved route differs from, or may differ from, INRIX's."""

# Route designations in a RoadList token (or the RoadName). INRIX writes them several
# ways — ``ID-34``, ``N ID-34``, ``Highway 30``, ``N Highway 34`` — and all name the
# route. Business/spur forms (``US-12-BL``, ``US-93-BR``, ``I-84-BL``) do **not**: a
# business loop is not the route (Coeur d'Alene's Northwest Blvd is that case), and
# they are read separately by :func:`road_list_business_routes`.
_ROADLIST_TOKEN_RE = re.compile(
    r"^(?:[NSEW]\s+)?(?:I|US|ID|SH|SR|Highway|Hwy)[- ]?(\d{1,3})(?:\s+[NSEW])?$", re.I)
_ROADLIST_BUSINESS_RE = re.compile(
    r"^(?:I|US|ID|SH|SR)[- ]?(\d{1,3})[- ]?(?:BL|BR|BUS|B|BYP|SPUR)$", re.I)

OVERRIDE_COLUMNS = ("county", "road_name", "inrix_route", "route", "note")


# ---------------------------------------------------------------------------
# Segment identity
# ---------------------------------------------------------------------------
def _tokens(road_list, road_name=None):
    toks = [t.strip() for t in road_list.split("|")] if isinstance(road_list, str) else []
    if isinstance(road_name, str):
        toks.append(road_name.strip())
    return toks


def road_list_routes(road_list, road_name=None) -> set[int]:
    """Route numbers a ``RoadList`` (and, if given, the ``RoadName``) names as the
    route itself — ``"US-20|US-26|US-93|Idaho Medal of Honor Hwy"`` -> ``{20, 26, 93}``;
    ``"N ID-34|ID-36|N Highway 34"`` -> ``{34, 36}``; ``"N Main St|ID-13|US-12-BL"`` ->
    ``{13}`` (a business loop is not the route)."""
    out = set()
    for tok in _tokens(road_list, road_name):
        m = _ROADLIST_TOKEN_RE.match(tok)
        if m:
            out.add(int(m.group(1)))
    return out


def road_list_business_routes(road_list) -> set[int]:
    """Route numbers a ``RoadList`` names only as a business/spur route —
    ``"E 3900 N|US-30|US-93-BR"`` -> ``{93}``."""
    out = set()
    for tok in _tokens(road_list):
        m = _ROADLIST_BUSINESS_RE.match(tok)
        if m:
            out.add(int(m.group(1)))
    return out


def inrix_route(road_number) -> int | None:
    """INRIX's ``RoadNumber`` as an int, ``None`` when blank or not a plain number."""
    if road_number is None or (isinstance(road_number, float) and math.isnan(road_number)):
        return None
    text = str(road_number).strip()
    return int(text) if text.isdigit() else None


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------
def load_route_overrides(path) -> pd.DataFrame:
    """The owner-reviewed override table: ``county, road_name, inrix_route, route,
    note``. A row applies to every segment in ``county`` named ``road_name`` (and, when
    ``inrix_route`` is given, numbered that by INRIX). ``route`` blank means **not a
    state route** — the layer is stale there. ``#`` lines are comments."""
    frame = pd.read_csv(path, dtype=str, comment="#").fillna("")
    missing = [c for c in OVERRIDE_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"{path}: override table lacks columns {missing}")
    for col in OVERRIDE_COLUMNS:
        frame[col] = frame[col].str.strip()
    bad = frame[(frame["route"] != "") & ~frame["route"].str.isdigit()]
    if len(bad):
        raise ValueError(f"{path}: route must be a number or blank: {list(bad['route'])}")
    if (frame["note"] == "").any():
        raise ValueError(f"{path}: every override needs a note saying why")
    return frame[list(OVERRIDE_COLUMNS)].reset_index(drop=True)


def _override_for(overrides, county, road_name, rn) -> dict | None:
    if overrides is None or len(overrides) == 0:
        return None
    hit = overrides[(overrides["county"] == str(county or ""))
                    & (overrides["road_name"] == str(road_name or ""))]
    if len(hit) and rn is not None:
        hit = hit[(hit["inrix_route"] == "") | (hit["inrix_route"] == str(rn))]
    elif len(hit):
        hit = hit[hit["inrix_route"] == ""]
    return None if hit.empty else hit.iloc[0].to_dict()


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
def _record_bands(aadt) -> tuple[list, list, list]:
    """Per record: the route its ``RouteID`` **band** carries (``None`` for ``OH``), and
    whether an ``OH`` record's description names a route (``KARCHER RD (SH-55)``,
    ``US-95``).

    The band is the only reading membership trusts. A route named in an ``OH``
    record's description is **ambiguous**: ITD's descriptions name the cross street
    at a break, so ``US-95`` on Reubens-Gifford Rd's record — 3.8 km from US-95 — means
    "to US-95", not "is US-95". :func:`aadt.record_route_number` reads it the other
    way for the volume join's tie-break (Item 42); that is a different question. An
    ambiguous record can neither give a route nor, lying on a segment, take one away.
    """
    route_ids = aadt["RouteID"] if "RouteID" in aadt.columns else [None] * len(aadt)
    parsed = [aadt_mod.parse_route_id(r) for r in route_ids]
    bands = [num for _, _, num in parsed]
    classes = [cls for _, cls, _ in parsed]
    named = aadt_mod._record_route_numbers(aadt)
    ambiguous = [b is None and n is not None for b, n in zip(bands, named)]
    return bands, classes, ambiguous


def _evidence(seg, lines, tree, bands, ambiguous, rec_kind, route_ids, descs):
    """The ITD records on and near one (metric) segment geometry.

    Returns ``(on_numbered, on_local, on_ambiguous, near_routes)`` — the best
    numbered, local and ambiguous record *on* the segment as ``(dist, coverage, i)``
    (or ``None``), and the set of route numbers any numbered mainline record *near*
    it carries."""
    from shapely.ops import nearest_points

    best = {"num": None, "loc": None, "amb": None}
    near = set()
    for i in tree.query(seg.buffer(NEAR_DISTANCE_M)):
        i = int(i)
        if rec_kind[i] in (aadt_mod.RAMP, aadt_mod.CONNECTOR):
            continue
        line = lines[i]
        d = seg.distance(line)
        if d > NEAR_DISTANCE_M:
            continue
        p_seg, p_line = nearest_points(seg, line)
        if aadt_mod._bearing_diff(aadt_mod._local_bearing(seg, p_line),
                                  aadt_mod._local_bearing(line, p_seg)) > BEARING_TOL_DEG:
            continue
        band = bands[i]
        if band is not None and aadt_mod._alongside_fraction(
                seg, line, NEAR_DISTANCE_M) >= NEAR_MIN_COVERAGE:
            near.add(int(band))
        if d > ON_DISTANCE_M:
            continue
        cover = aadt_mod._alongside_fraction(seg, line, ON_COVER_TOL_M)
        if cover < ON_MIN_COVERAGE:
            continue
        # Stable order: nearer, then more alongside, then the record's own identity.
        key = (round(d, 1), -cover, f"{route_ids[i] or ''}|{descs[i] or ''}", d, cover, i)
        slot = "num" if band is not None else ("amb" if ambiguous[i] else "loc")
        if best[slot] is None or key < best[slot]:
            best[slot] = key
    pack = (lambda k: None if k is None else (k[3], k[4], k[5]))
    return pack(best["num"]), pack(best["loc"]), pack(best["amb"]), near


def _aligned_fraction(seg, line, tol: float, n: int = 11) -> float:
    """Share of ``seg`` running within ``tol`` of ``line`` **and in its direction**,
    sampled at ``n`` points.

    The bearing is compared at every sample, not once at the single nearest point:
    on a winding grade (ID-162 above Kamiah) the SHS line and the segment touch at
    one point where their local tangents differ by 54°, though the line runs beside
    the whole segment, and a one-point gate rejected it. A cross street still fails,
    because only the samples at the crossing are within ``tol`` of it."""
    if n < 2 or seg.length == 0:
        return 0.0
    step = seg.length / (n - 1)
    hits = 0
    for k in range(n):
        p = seg.interpolate(k * step)
        if line.distance(p) > tol:
            continue
        if aadt_mod._bearing_diff(aadt_mod._local_bearing(seg, p),
                                  aadt_mod._local_bearing(line, p)) <= BEARING_TOL_DEG:
            hits += 1
    return hits / n


def _shs_evidence(seg, lines, tree, bands, route_types, carriageways):
    """The SHS roadway lines on and near one (metric) segment geometry.

    Returns ``(member_on, business_on, near_member, near_business, near_any,
    beside)``: the best *member* (mainline / spur / connector) and *business-loop*
    line **on** the segment as ``(dist, coverage, i)`` or ``None``; the route bands of
    member and business lines **near** it; whether *any* roadway line is near it at
    all — the SHS carries nothing but state highway, so ``near_any`` false means the
    segment is off the system; and ``beside``, the member bands drawn as **both**
    carriageways (``Travelway`` A and D) near the segment with neither within
    :data:`ON_COVER_TOL_M` of it. A carriageway of such a route lies on its own line;
    a road that lies on neither is a parallel road (Silver Valley Rd beside I-90).
    Coverage is :func:`_aligned_fraction` (distance *and* direction per sample)."""
    best = {"member": None, "business": None}
    near_member, near_business, near_any = set(), set(), False
    ways: dict[int, set] = {}
    close: set[int] = set()
    for i in tree.query(seg.buffer(NEAR_DISTANCE_M)):
        i = int(i)
        line = lines[i]
        d = seg.distance(line)
        if d > NEAR_DISTANCE_M:
            continue
        slot = "business" if route_types[i] == "business" else "member"
        if _aligned_fraction(seg, line, NEAR_DISTANCE_M) >= NEAR_MIN_COVERAGE:
            near_any = True
            if bands[i] is not None:
                (near_business if slot == "business" else near_member).add(int(bands[i]))
                if slot == "member":
                    ways.setdefault(int(bands[i]), set()).add(carriageways[i])
        cover = (_aligned_fraction(seg, line, ON_COVER_TOL_M)
                 if d <= ON_COVER_TOL_M else 0.0)
        if slot == "member" and bands[i] is not None and cover >= CARRIAGEWAY_MIN_COVERAGE:
            close.add(int(bands[i]))
        if d > ON_DISTANCE_M:
            continue
        if cover < ON_MIN_COVERAGE:
            continue
        key = (round(d, 1), -cover, str(i), d, cover, i)
        if best[slot] is None or key < best[slot]:
            best[slot] = key
    pack = (lambda k: None if k is None else (k[3], k[4], k[5]))
    beside = {b for b, w in ways.items() if {"A", "D"} <= w and b not in close}
    return (pack(best["member"]), pack(best["business"]), near_member, near_business,
            near_any, beside)


class _Layer:
    """One reference layer prepared for evidence queries in a metric CRS."""

    def __init__(self, frame, metric):
        from shapely import STRtree

        self.frame = frame
        self.lines = list(frame.to_crs(metric).geometry.values) if len(frame) else []
        self.tree = STRtree(self.lines) if self.lines else None
        n = len(frame)
        col = (lambda c: frame[c].to_numpy() if c in frame.columns else [None] * n)
        self.route_ids = col("RouteID")
        self.descs = col("Descriptio")


def _record(layer, ev, desc=None):
    if ev is None:
        return {"itd_route_id": None, "itd_desc": None,
                "itd_dist_m": float("nan"), "itd_coverage": float("nan")}
    d, cover, i = ev
    return {"itd_route_id": layer.route_ids[i],
            "itd_desc": desc(i) if desc is not None else layer.descs[i],
            "itd_dist_m": round(float(d), 2), "itd_coverage": round(float(cover), 2)}


def _aadt_verdict(seg, *, rn, own, business, layer, bands, classes, ambiguous, rec_kind):
    """Item 48's verdict for one non-ramp segment from the AADT layer (``seg`` metric,
    or ``None`` when it has no geometry). Returns the verdict fields as a dict."""
    on_num = on_loc = on_amb = None
    near: set[int] = set()
    if layer.tree is not None and seg is not None:
        on_num, on_loc, on_amb, near = _evidence(
            seg, layer.lines, layer.tree, bands, ambiguous, rec_kind, layer.route_ids,
            layer.descs)
    itd = None if on_num is None else int(bands[on_num[2]])
    # An interstate band is never *given* to a segment INRIX does not number as
    # that interstate: INRIX numbers interstate mainline reliably, and what lies on
    # an ``IN`` band otherwise is a business loop (Caldwell Blvd, Burley's Overland
    # Ave — ITD bands both ``IN084``) or a frontage road beside the centreline.
    # Likewise a band the RoadList names only as a business route (``US-93-BR``).
    foreign = on_num is not None and (
        (classes[on_num[2]] == "IN" and itd not in own) or itd in business)
    used = on_num
    if rn is not None:
        confirmed = near & own
        if confirmed:
            verdict = AGREE if rn in near else CONCURRENT
            routes, number = {rn} | confirmed, rn
            itd = rn if rn in near else min(confirmed)
            reason = (f"ITD route {itd} runs here" if verdict == AGREE else
                      f"ITD records route {itd}, which RoadList also carries "
                      f"(a shared road)")
        elif on_num is not None and foreign:
            verdict, routes, number = BUSINESS, {rn}, rn
            reason = (f"ITD's record is a business loop or interstate band of route "
                      f"{itd} ({layer.route_ids[on_num[2]]}); INRIX's {rn} kept")
        elif on_num is not None:
            verdict, routes, number = RENUMBERED, {itd}, itd
            reason = f"INRIX says {rn}; ITD's record on the segment is route {itd}"
        elif on_loc is not None and on_amb is None and not near:
            verdict, routes, number = INRIX_ONLY, set(), None
            used = on_loc
            reason = (f"INRIX says {rn}; ITD carries this as a local road and no "
                      f"numbered record lies within {NEAR_DISTANCE_M:g} m")
        else:
            verdict, routes, number = UNCONFIRMED, {rn}, rn
            used = on_amb or on_loc
            reason = ("no ITD record decides it — kept as INRIX numbers it"
                      + (f" (numbered records nearby: {sorted(near)})" if near else ""))
    else:
        local_as_good = (on_loc is not None and on_num is not None
                         and on_loc[0] <= on_num[0] + 2.0)
        if on_num is not None and not local_as_good and not foreign:
            verdict, routes, number = ITD_ONLY, {itd}, itd
            reason = f"ITD route {itd} runs here; INRIX gives no route number"
        else:
            verdict, routes, number = OFF_SYSTEM, set(), None
            used = on_loc
            reason = ("an ITD route and a local record both fit — left off"
                      if local_as_good else
                      f"beside ITD's route {itd} band ({layer.route_ids[on_num[2]]}), "
                      f"which is never given to an unnumbered segment"
                      if on_num is not None else "no route by either source")
    out = {"itd_route": itd, "near": near, "verdict": verdict, "routes": routes,
           "number": number, "reason": reason, "source": SOURCE_AADT}
    out.update(_record(layer, used))
    return out


def _shs_verdict(seg, *, rn, own, business, layer, bands, classes, route_types,
                 carriageways):
    """The State Highway System's verdict for one non-ramp segment (Item 52).

    Returns the verdict fields as a dict, or ``None`` where the SHS cannot decide —
    a numbered segment with only *another* route's line near it, never on it — and
    the AADT layer is asked instead."""
    member_on = bus_on = None
    near_member: set[int] = set()
    near_bus: set[int] = set()
    near_any = False
    beside: set[int] = set()
    if layer.tree is not None and seg is not None:
        member_on, bus_on, near_member, near_bus, near_any, beside = _shs_evidence(
            seg, layer.lines, layer.tree, bands, route_types, carriageways)
    desc = (lambda i: f"SHS {route_types[i]}, travelway {carriageways[i]}")
    itd = None if member_on is None else int(bands[member_on[2]])
    foreign = member_on is not None and (
        (classes[member_on[2]] == "IN" and itd not in own) or itd in business)
    used = member_on
    if rn is not None:
        # Evidence *on* the segment outranks evidence *near* it: a business line lying
        # on Rigby's Farnsworth Way beats US-20's carriageway 21 m away.
        confirmed = (near_member - beside) & own if (member_on is not None
                                                     or bus_on is None) else set()
        if confirmed:
            verdict = AGREE if rn in confirmed else CONCURRENT
            routes, number = {rn} | confirmed, rn
            itd = rn if rn in confirmed else min(confirmed)
            reason = (f"on the State Highway System as route {itd}" if verdict == AGREE
                      else f"the SHS records route {itd}, which RoadList also carries "
                           f"(a shared road)")
        elif near_bus or bus_on is not None:
            parents = near_bus | ({int(bands[bus_on[2]])} if bus_on is not None else set())
            routes, number = own | parents, rn
            verdict, used = BUSINESS, bus_on or member_on
            itd = min(parents)
            others = sorted(own - parents)
            reason = (f"on the SHS business loop of route {itd} (state highway, in route "
                      f"{itd}'s inventory)" + (f"; RoadList's {others} kept" if others
                                               else ""))
        elif member_on is not None and foreign:
            verdict, routes, number = BUSINESS, {rn}, rn
            reason = (f"the SHS line is interstate band {itd} or a route RoadList names "
                      f"only as a business route ({layer.route_ids[member_on[2]]}); "
                      f"INRIX's {rn} kept")
        elif member_on is not None:
            verdict, routes, number = RENUMBERED, {itd}, itd
            reason = f"INRIX says {rn}; the SHS line on the segment is route {itd}"
        elif beside & own:
            itd = min(beside & own)
            verdict, routes, number = INRIX_ONLY, set(), None
            reason = (f"INRIX says {rn}; the SHS draws both carriageways of route {itd} "
                      f"beside it and it lies on neither — a parallel road")
        elif not near_any:
            verdict, routes, number = INRIX_ONLY, set(), None
            reason = (f"INRIX says {rn}; no State Highway System line within "
                      f"{NEAR_DISTANCE_M:g} m — not a state route")
        else:
            return None
    else:
        if member_on is not None and not foreign:
            verdict, routes, number = ITD_ONLY, {itd}, itd
            reason = f"on the State Highway System as route {itd}; INRIX gives no number"
        elif bus_on is not None:
            itd = int(bands[bus_on[2]])
            verdict, routes, number, used = BUSINESS, {itd}, itd, bus_on
            reason = (f"on the SHS business loop of route {itd} (state highway); INRIX "
                      f"gives no number")
        else:
            verdict, routes, number = OFF_SYSTEM, set(), None
            reason = (f"beside SHS interstate band {itd} ({layer.route_ids[member_on[2]]}), "
                      f"which is never given to an unnumbered segment"
                      if member_on is not None else "not on the State Highway System")
    out = {"itd_route": itd, "near": near_member | near_bus, "verdict": verdict,
           "routes": routes, "number": number, "reason": reason, "source": SOURCE_SHS}
    out.update(_record(layer, used, desc))
    return out


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------
def route_membership(geo, aadt=None, overrides=None, shs=None) -> pd.DataFrame:
    """Each segment's state route by ITD's layers, INRIX's reading, and the verdict.

    Args:
        geo: GeoDataFrame indexed by ``Segment ID`` (EPSG:4326) carrying
            ``RoadNumber`` / ``RoadList`` / ``RoadName`` / ``FRC`` and, when present,
            ``County`` (for overrides) and ``SlipRoad`` (``"1"`` = ramp) — e.g.
            :func:`inrix_tools.geometry.segment_geometry` plus those columns.
        aadt: the layer from :func:`aadt.load_aadt`. With ``shs`` it is only the
            fallback (see the module docstring); without it, it decides (Item 48).
        overrides: optional :func:`load_route_overrides` table.
        shs: the State Highway System from :func:`itd_layers.load_shs` (Item 52).

    Returns:
        A DataFrame indexed like ``geo`` with ``RoadName``, ``County``,
        ``inrix_route`` / ``itd_route`` (Int64), ``itd_route_id`` / ``itd_desc`` /
        ``itd_dist_m`` / ``itd_coverage`` (the line or record that decided),
        ``near_routes`` (``"93/20"``), ``verdict`` (one of :data:`VERDICTS`),
        ``source`` — which layer decided (``shs`` / ``aadt`` / ``override`` /
        ``none``), ``routes`` — every route the segment belongs to, ``/``-joined
        (``""`` = none), ``route_number`` — the one route to walk chains by (a
        ``RoadNumber``-style string, ``None`` = unnumbered) — and ``reason``, in words.
        ``attrs['route_membership']`` records the thresholds and verdict counts.
    """
    if aadt is None and shs is None:
        raise ValueError("route_membership needs the SHS layer, the AADT layer, or both")
    idx = geo.index
    cols = {c: (geo[c] if c in geo.columns else pd.Series([None] * len(geo), index=idx))
            for c in ("RoadNumber", "RoadList", "RoadName", "County", "SlipRoad")}
    kinds = aadt_mod._segment_kinds(geo)

    valid = geo.geometry.notna() & ~geo.geometry.is_empty
    metric = geo[valid].estimate_utm_crs() if valid.any() else None
    seg_m = geo.geometry[valid].to_crs(metric) if metric is not None else None

    a_layer = a_args = None
    if aadt is not None:
        if aadt_mod.RECORD_KIND_COL not in aadt.columns:
            aadt = aadt_mod.classify_aadt_records(aadt)
        a_layer = _Layer(aadt if metric is not None else aadt.iloc[:0], metric)
        bands, classes, ambiguous = _record_bands(aadt) if len(aadt) else ([], [], [])
        rec_kind = aadt[aadt_mod.RECORD_KIND_COL].to_numpy() if len(aadt) else []
        a_args = {"layer": a_layer, "bands": bands, "classes": classes,
                  "ambiguous": ambiguous, "rec_kind": rec_kind}
    s_args = None
    if shs is not None:
        from . import itd_layers

        roadway = shs[shs[itd_layers.SHS_ROAD_KIND_COL] == "roadway"].reset_index(drop=True)
        s_layer = _Layer(roadway if metric is not None else roadway.iloc[:0], metric)
        s_args = {
            "layer": s_layer,
            "bands": [None if pd.isna(v) else int(v)
                      for v in roadway[itd_layers.SHS_ROUTE_NUMBER_COL]],
            "classes": roadway[itd_layers.SHS_ROUTE_CLASS_COL].to_numpy(),
            "route_types": roadway[itd_layers.SHS_ROUTE_TYPE_COL].to_numpy(),
            "carriageways": roadway[itd_layers.SHS_CARRIAGEWAY_COL].to_numpy(),
        }

    rows = []
    for sid in idx:
        rn = inrix_route(cols["RoadNumber"].loc[sid])
        listed = road_list_routes(cols["RoadList"].loc[sid], cols["RoadName"].loc[sid])
        business = road_list_business_routes(cols["RoadList"].loc[sid])
        own = listed | ({rn} if rn is not None else set())
        is_ramp = (str(cols["SlipRoad"].loc[sid]).strip() == "1"
                   or kinds.get(sid) == aadt_mod.RAMP)
        seg = seg_m.loc[sid] if (seg_m is not None and valid.loc[sid]) else None
        common = {"rn": rn, "own": own, "business": business}

        if is_ramp:
            v = {"itd_route": None, "near": set(), "verdict": RAMP,
                 "routes": ({rn} if rn else set()), "number": rn,
                 "reason": "a ramp: INRIX's reading kept", "source": SOURCE_NONE}
            v.update(_record(None, None))
        else:
            v = _shs_verdict(seg, **common, **s_args) if s_args is not None else None
            if v is None and a_args is not None:
                v = _aadt_verdict(seg, **common, **a_args)
                if s_args is not None:
                    v["reason"] = ("the SHS has only another route's line near it; "
                                   "AADT layer: " + v["reason"])
            elif v is None:
                v = {"itd_route": None, "near": set(), "verdict": UNCONFIRMED,
                     "routes": {rn}, "number": rn, "source": SOURCE_NONE,
                     "reason": "the SHS has only another route's line near it — "
                               "kept as INRIX numbers it"}
                v.update(_record(None, None))

        ov = _override_for(overrides, cols["County"].loc[sid], cols["RoadName"].loc[sid], rn)
        if ov is not None:
            r = int(ov["route"]) if ov["route"] else None
            v.update(verdict=OVERRIDE, routes=({r} if r else set()), number=r,
                     reason=f"override: {ov['note']}", source=SOURCE_OVERRIDE)

        row = {"RoadName": cols["RoadName"].loc[sid], "County": cols["County"].loc[sid],
               "inrix_route": rn, "itd_route": v["itd_route"]}
        row.update({k: v[k] for k in ("itd_route_id", "itd_desc", "itd_dist_m",
                                      "itd_coverage")})
        row.update({
            "near_routes": "/".join(str(r) for r in sorted(v["near"])),
            "verdict": v["verdict"],
            "source": v["source"],
            "routes": "/".join(str(r) for r in sorted(v["routes"])),
            "route_number": None if v["number"] is None else str(v["number"]),
            "reason": v["reason"],
        })
        rows.append(row)

    out = pd.DataFrame(rows, index=idx)
    for col in ("inrix_route", "itd_route"):
        out[col] = pd.array(out[col], dtype="Int64")
    out.attrs["route_membership"] = {
        "identity": "shs" if shs is not None else "aadt",
        "fallback": "aadt" if (shs is not None and aadt is not None) else None,
        "on_distance_m": ON_DISTANCE_M, "on_cover_tol_m": ON_COVER_TOL_M,
        "on_min_coverage": ON_MIN_COVERAGE, "near_distance_m": NEAR_DISTANCE_M,
        "near_min_coverage": NEAR_MIN_COVERAGE, "bearing_tol_deg": BEARING_TOL_DEG,
        "carriageway_min_coverage": CARRIAGEWAY_MIN_COVERAGE,
        "n_overrides": 0 if overrides is None else len(overrides),
        "verdicts": out["verdict"].value_counts().to_dict(),
        "sources": out["source"].value_counts().to_dict(),
    }
    return out


def route_segments(membership, route) -> list:
    """The segment ids :func:`route_membership` puts on ``route``, in index order."""
    key = str(int(route))
    hit = membership["routes"].fillna("").map(lambda s: key in s.split("/"))
    return list(membership.index[hit])


ITD_ROUTES_COL = "itd_routes"        # "/"-joined routes the segment belongs to ("" = none)
ITD_ROUTE_ID_COL = "itd_route_id"    # the SHS/AADT line that decided (``01540DUS095``)


def apply_route_membership(net, membership):
    """``net`` with ``RoadNumber`` replaced by the resolved ``route_number``, plus
    :data:`ITD_ROUTES_COL` (every route, ``"12/95"``) and :data:`ITD_ROUTE_ID_COL`.

    INRIX's value is kept as ``RoadNumber_inrix`` so nothing is lost. Segments the
    membership frame doesn't cover keep their ``RoadNumber``. ``net`` may be indexed
    by ``XDSegID`` or carry it as a column; ``membership`` is indexed by segment id.
    """
    out = net.copy()
    ids = out.index if "XDSegID" not in out.columns else out["XDSegID"]
    ids = pd.Series(pd.array(ids, dtype="Int64"), index=out.index)
    resolved = membership["route_number"]
    resolved.index = pd.array(resolved.index, dtype="Int64")
    covered = ids.isin(resolved.index)
    out["RoadNumber_inrix"] = out["RoadNumber"]
    new = ids[covered].map(resolved)
    out.loc[covered, "RoadNumber"] = new.where(new.notna(), None)
    # Every route the segment belongs to, and the SHS line that decided it (Item 51):
    # the chain walk follows concurrency (SH-8 on Moscow's US-95 couplet) and the
    # couplet tests read the line's travelway.
    for src, col in (("routes", ITD_ROUTES_COL), ("itd_route_id", ITD_ROUTE_ID_COL)):
        if src in membership.columns:
            vals = membership[src]
            vals.index = pd.array(vals.index, dtype="Int64")
            out[col] = None
            out.loc[covered, col] = ids[covered].map(vals).where(
                ids[covered].map(vals).notna(), None)
    return out


def changes_by_road(membership, miles=None) -> pd.DataFrame:
    """The segments whose route membership differs from INRIX's reading, grouped
    by county, road and verdict — the review unit (a road can be argued with, an id
    cannot). ``miles`` is an optional per-segment length Series."""
    hit = membership[membership["verdict"].isin(CHANGED)].copy()
    cols = ["County", "RoadName", "verdict", "inrix_route", "routes", "segments", "miles",
            "itd_route_id", "reason", "segment_ids"]
    if hit.empty:
        return pd.DataFrame(columns=cols)
    hit["miles"] = (miles.reindex(hit.index).astype(float) if miles is not None
                    else float("nan"))
    hit["RoadName"] = hit["RoadName"].fillna("(unnamed)")
    hit["County"] = hit["County"].fillna("")
    g = hit.groupby(["County", "RoadName", "verdict", "inrix_route", "routes"],
                    dropna=False)
    out = g.agg(
        segments=("reason", "size"),
        miles=("miles", "sum"),
        itd_route_id=("itd_route_id", lambda s: ";".join(sorted({str(v) for v in s
                                                                 if v is not None}))),
        reason=("reason", "first"),
        segment_ids=("reason", lambda s: ",".join(str(i) for i in sorted(s.index))),
    ).reset_index()
    out["miles"] = out["miles"].round(2)
    return out[cols].sort_values("miles", ascending=False, ignore_index=True)


def no_route_segments(membership) -> set:
    """The segment ids membership leaves on **no** route: dropped, off the system, or
    a loop with no route of its own. Ramps are exempt (they keep INRIX's reading and
    the AADT join handles their volume). The same rule the district inventories use."""
    routes_ = membership["routes"].fillna("")
    return set(membership.index[(membership["verdict"] != RAMP) & (routes_ == "")])


def reconcile_curated_list(ids, membership, *, keep=()) -> dict:
    """A hand-curated segment list (District 3's) checked against route membership.

    Returns ``kept`` (``ids`` in their order, minus the no-route segments of
    :func:`no_route_segments` that are not in ``keep``), ``dropped`` (those removed,
    in order) and ``missing`` (non-ramp segments membership puts on a route that the
    list lacks, sorted). ``keep`` exempts segments kept on purpose — Banks-Lowman Hwy
    is off the system but stays in the D3 export for other analyses (owner). Ids the
    membership frame doesn't cover are kept: no evidence either way. (Item 49.)"""
    keep = {int(s) for s in keep}
    off = no_route_segments(membership) - keep
    ids = [int(s) for s in ids]
    kept = [s for s in ids if s not in off]
    dropped = [s for s in ids if s in off]
    on_route = membership[(membership["verdict"] != RAMP)
                          & (membership["routes"].fillna("") != "")]
    have = set(ids)
    missing = sorted(int(s) for s in on_route.index if int(s) not in have)
    return {"kept": kept, "dropped": dropped, "missing": missing}


def write_membership(membership, path) -> Path:
    """Write a membership frame as CSV (the index as ``XDSegID``)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    membership.rename_axis("XDSegID").to_csv(path)
    return path


def read_membership(path) -> pd.DataFrame:
    """Read :func:`write_membership` output back, indexed by ``XDSegID`` (int)."""
    frame = pd.read_csv(path, dtype={"routes": str, "route_number": str,
                                     "near_routes": str})
    frame["routes"] = frame["routes"].fillna("")
    frame["route_number"] = frame["route_number"].where(frame["route_number"].notna(), None)
    for col in ("inrix_route", "itd_route"):
        frame[col] = pd.array(frame[col], dtype="Int64")
    return frame.set_index("XDSegID")
