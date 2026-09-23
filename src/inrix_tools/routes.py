"""State-route membership from ITD's AADT layer, not INRIX ``RoadNumber``.  (ROADMAP Item 48)

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
   what new construction looks like.

The evidence is also asymmetric on purpose. Taking a route *away* needs a local record
on the segment **and** no numbered record anywhere near it (``NEAR_DISTANCE_M``): a
divided highway's one centreline lies 22–30 m off each carriageway, and a frontage
road record can sit nearer the carriageway than that. Giving a route to an unnumbered
segment needs a numbered mainline record on it that no local record matches as well.

Pure: no file paths, no plotting. :func:`route_membership` takes the geometry frame and
the AADT layer and returns one row per segment; :func:`apply_route_membership` writes
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

# Verdicts
AGREE = "agree"              # ITD confirms INRIX's own number
CONCURRENT = "concurrent"    # ITD names another route the segment's RoadList carries
RENUMBERED = "renumbered"    # ITD puts the segment on a different route than INRIX
INRIX_ONLY = "inrix_only"    # INRIX numbers it; ITD carries it as a local road -> dropped
ITD_ONLY = "itd_only"        # ITD route; INRIX has no number -> added
UNCONFIRMED = "unconfirmed"  # INRIX numbers it; no ITD record decides -> kept, flagged
OFF_SYSTEM = "off_system"    # neither source numbers it
BUSINESS = "business"        # ITD's band is a business loop of another route -> INRIX kept
RAMP = "ramp"                # ramps keep INRIX's reading; the join handles their volume
OVERRIDE = "override"        # an owner-reviewed override row decided

VERDICTS = (AGREE, CONCURRENT, BUSINESS, RENUMBERED, INRIX_ONLY, ITD_ONLY, UNCONFIRMED,
            OFF_SYSTEM, RAMP, OVERRIDE)
"""Every verdict :func:`route_membership` can return."""

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


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------
def route_membership(geo, aadt, overrides=None) -> pd.DataFrame:
    """Each segment's state route by ITD's layer, INRIX's reading, and the verdict.

    Args:
        geo: GeoDataFrame indexed by ``Segment ID`` (EPSG:4326) carrying
            ``RoadNumber`` / ``RoadList`` / ``RoadName`` / ``FRC`` and, when present,
            ``County`` (for overrides) and ``SlipRoad`` (``"1"`` = ramp) — e.g.
            :func:`inrix_tools.geometry.segment_geometry` plus those columns.
        aadt: the layer from :func:`aadt.load_aadt`.
        overrides: optional :func:`load_route_overrides` table.

    Returns:
        A DataFrame indexed like ``geo`` with ``RoadName``, ``County``,
        ``inrix_route`` / ``itd_route`` (Int64), ``itd_route_id`` / ``itd_desc`` /
        ``itd_dist_m`` / ``itd_coverage`` (the record that decided, numbered or local),
        ``near_routes`` (``"93/20"``), ``verdict`` (one of :data:`VERDICTS`),
        ``routes`` — every route the segment belongs to, ``/``-joined (``""`` = none),
        ``route_number`` — the one route to walk chains by (a ``RoadNumber``-style
        string, ``None`` = unnumbered) — and ``reason``, in words.
        ``attrs['route_membership']`` records the thresholds and verdict counts.
    """
    from shapely import STRtree

    idx = geo.index
    cols = {c: (geo[c] if c in geo.columns else pd.Series([None] * len(geo), index=idx))
            for c in ("RoadNumber", "RoadList", "RoadName", "County", "SlipRoad")}
    kinds = aadt_mod._segment_kinds(geo)

    if aadt_mod.RECORD_KIND_COL not in aadt.columns:
        aadt = aadt_mod.classify_aadt_records(aadt)
    valid = geo.geometry.notna() & ~geo.geometry.is_empty
    metric = geo[valid].estimate_utm_crs() if valid.any() else None
    seg_m = geo.geometry[valid].to_crs(metric) if metric is not None else None
    if metric is not None and len(aadt):
        lines = list(aadt.to_crs(metric).geometry.values)
        tree = STRtree(lines)
    else:
        lines, tree = [], None
    bands, classes, ambiguous = _record_bands(aadt) if len(aadt) else ([], [], [])
    rec_kind = aadt[aadt_mod.RECORD_KIND_COL].to_numpy() if len(aadt) else []
    route_ids = aadt["RouteID"].to_numpy() if "RouteID" in aadt.columns else [None] * len(aadt)
    descs = aadt["Descriptio"].to_numpy() if "Descriptio" in aadt.columns else [None] * len(aadt)

    rows = []
    for sid in idx:
        rn = inrix_route(cols["RoadNumber"].loc[sid])
        listed = road_list_routes(cols["RoadList"].loc[sid], cols["RoadName"].loc[sid])
        business = road_list_business_routes(cols["RoadList"].loc[sid])
        own = listed | ({rn} if rn is not None else set())
        is_ramp = (str(cols["SlipRoad"].loc[sid]).strip() == "1"
                   or kinds.get(sid) == aadt_mod.RAMP)
        on_num = on_loc = on_amb = None
        near: set[int] = set()
        if tree is not None and valid.loc[sid] and not is_ramp:
            on_num, on_loc, on_amb, near = _evidence(
                seg_m.loc[sid], lines, tree, bands, ambiguous, rec_kind, route_ids, descs)

        def rec(ev):
            if ev is None:
                return {"itd_route_id": None, "itd_desc": None,
                        "itd_dist_m": float("nan"), "itd_coverage": float("nan")}
            d, cover, i = ev
            return {"itd_route_id": route_ids[i], "itd_desc": descs[i],
                    "itd_dist_m": round(float(d), 2), "itd_coverage": round(float(cover), 2)}

        itd = None if on_num is None else int(bands[on_num[2]])
        # An interstate band is never *given* to a segment INRIX does not number as
        # that interstate: INRIX numbers interstate mainline reliably, and what lies on
        # an ``IN`` band otherwise is a business loop (Caldwell Blvd, Burley's Overland
        # Ave — ITD bands both ``IN084``) or a frontage road beside the centreline.
        # Likewise a band the RoadList names only as a business route (``US-93-BR``).
        foreign = on_num is not None and (
            (classes[on_num[2]] == "IN" and itd not in own) or itd in business)
        used = on_num
        if is_ramp:
            verdict, routes, number = RAMP, ({rn} if rn else set()), rn
            reason = "a ramp: INRIX's reading kept"
        elif rn is not None:
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
                          f"{itd} ({route_ids[on_num[2]]}); INRIX's {rn} kept")
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
                          f"beside ITD's route {itd} band ({route_ids[on_num[2]]}), which is "
                          f"never given to an unnumbered segment" if on_num is not None
                          else "no route by either source")

        ov = _override_for(overrides, cols["County"].loc[sid], cols["RoadName"].loc[sid], rn)
        if ov is not None:
            r = int(ov["route"]) if ov["route"] else None
            verdict, routes, number = OVERRIDE, ({r} if r else set()), r
            reason = f"override: {ov['note']}"

        row = {"RoadName": cols["RoadName"].loc[sid], "County": cols["County"].loc[sid],
               "inrix_route": rn, "itd_route": itd}
        row.update(rec(used))
        row.update({
            "near_routes": "/".join(str(r) for r in sorted(near)),
            "verdict": verdict,
            "routes": "/".join(str(r) for r in sorted(routes)),
            "route_number": None if number is None else str(number),
            "reason": reason,
        })
        rows.append(row)

    out = pd.DataFrame(rows, index=idx)
    for col in ("inrix_route", "itd_route"):
        out[col] = pd.array(out[col], dtype="Int64")
    out.attrs["route_membership"] = {
        "on_distance_m": ON_DISTANCE_M, "on_cover_tol_m": ON_COVER_TOL_M,
        "on_min_coverage": ON_MIN_COVERAGE, "near_distance_m": NEAR_DISTANCE_M,
        "near_min_coverage": NEAR_MIN_COVERAGE, "bearing_tol_deg": BEARING_TOL_DEG,
        "n_overrides": 0 if overrides is None else len(overrides),
        "verdicts": out["verdict"].value_counts().to_dict(),
    }
    return out


def route_segments(membership, route) -> list:
    """The segment ids :func:`route_membership` puts on ``route``, in index order."""
    key = str(int(route))
    hit = membership["routes"].fillna("").map(lambda s: key in s.split("/"))
    return list(membership.index[hit])


def apply_route_membership(net, membership):
    """``net`` with ``RoadNumber`` replaced by the resolved ``route_number``.

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
