"""Statewide one-way couplet detection and pairing (ROADMAP Items 45.3 and 46).

A **couplet** is a state highway that splits into two parallel one-way streets
through a town — Boise's Myrtle St eastbound and Front St westbound, Pocatello's
4th and 5th Ave. It matters to a screening catalogue because the two directions
are *different pavement*, not two carriageways of one road, so they must be
catalogued as one reporting corridor and flagged (``one_way_couplet``).

:func:`detect_couplets` finds them from the XD topology and geometry alone, and
:func:`couplet_catalogue_entries` puts a pair into catalogue shape.
:func:`match_known_couplets` scores the result against :data:`KNOWN_COUPLETS`.

**What the XD network does not tell you**, each of which this had to stop
believing before it found Boise (see DATA_FORMAT.md, "What the attribute fields
do not mean"):

- ``Bearing`` is a compass heading, not a direction of travel — Front St's
  westbound carriageway is coded ``N`` — so pairing tests **geometric**
  anti-parallelism, not cardinal opposition.
- an ``XDGroup`` is a carriageway and spans several streets, so a couplet leg is
  a *run within* a group, split at each change of :func:`street_key`.
- a leading quadrant (``E Front St`` / ``W Front St``) is one street; a trailing
  one (``2nd Ave N`` / ``2nd Ave S``) is two.

Pure core: no hardcoded paths, no CLI. Detection uses only the XD network
topology and geometry — no DuckDB queries required.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge

from . import geometry as _geo
from .routes import ITD_ROUTE_ID_COL, ITD_ROUTES_COL
from .extents import business_band as _business_band
from .extents import place_name as _place_name
from .extents import route_label as _route_label
from .extents import segment_endpoint as _segment_endpoint


# ─── District Counties ───────────────────────────────────────────────

DISTRICT_COUNTIES: dict[int, list[str]] = {
    1: ["Benewah", "Bonner", "Boundary", "Kootenai", "Shoshone"],
    2: ["Clearwater", "Idaho", "Latah", "Lewis", "Nez Perce"],
    3: ["Ada", "Adams", "Boise", "Canyon", "Elmore", "Gem", "Owyhee", "Payette", "Valley", "Washington"],
    4: ["Blaine", "Camas", "Cassia", "Gooding", "Jerome", "Lincoln", "Minidoka", "Twin Falls"],
    5: ["Bannock", "Bear Lake", "Bingham", "Caribou", "Franklin", "Oneida", "Power"],
    6: ["Bonneville", "Butte", "Clark", "Custer", "Fremont", "Jefferson", "Lemhi", "Madison", "Teton"],
}


# ─── Data Structures ─────────────────────────────────────────────────

@dataclass(frozen=True)
class CoupletPair:
    """A detected one-way couplet: two parallel, opposing one-way chains
    carrying the same route through a town on distinct street names."""

    dir1_segment_ids: tuple[int, ...]
    """Segment IDs for direction 1 (e.g., eastbound/northbound), travel order."""
    dir2_segment_ids: tuple[int, ...]
    """Segment IDs for direction 2 (e.g., westbound/southbound), travel order."""
    dir1_bearing: str
    """Cardinal bearing for direction 1 ('N', 'E', etc.)."""
    dir2_bearing: str
    """Cardinal bearing for direction 2 ('S', 'W', etc.)."""
    dir1_street: str
    """Primary street name for direction 1 (e.g., 'Myrtle St')."""
    dir2_street: str
    """Primary street name for direction 2 (e.g., 'Front St')."""
    route_numbers: tuple[str, ...]
    """State route number(s) carried by this couplet (e.g., ('20', '26'))."""
    total_miles: float
    """Average length of the two directions in miles."""
    mean_lateral_sep_m: float
    """Mean perpendicular separation between the two chains in meters."""
    county: str
    """County where couplet is located."""
    postal_code: str
    """PostalCode (city) where couplet is located."""

    @property
    def label(self) -> str:
        """Human-readable label like 'Boise US-20/26 (Myrtle St/Front St)'."""
        routes = "/".join(f"US-{r}" if len(r) <= 2 else f"SH-{r}" for r in self.route_numbers)
        city = self.postal_code or self.county
        return f"{city} {routes} ({self.dir1_street}/{self.dir2_street})"


# ─── Known Couplet Registry ──────────────────────────────────────────
# Validated from AADT layer "COUPLET"/"ONE-WAY" labels and XD topology, and (Item 48)
# against ITD route membership (``inrix_tools.routes``): a pair both of whose streets
# ITD carries as local roads is not a state-highway couplet, whatever INRIX numbers
# them. Removed on that test, with the evidence in route_membership/:
#   * Lewiston US-12 Main St / D St — local (06830AOH000, 01900AOH000, 47980AOH000,
#     06820AOH000); US-12 runs the levee bypass (01910AUS012).
#   * Coeur d'Alene STC-7195 3rd St / 4th St — no route on either street.
#   * Payette "US-95 Conn" Main St / 7th Ave N — local; INRIX's 95 on both is dropped.
#   * Caldwell I-84B/SH-19 Blaine St / Canyon St — I-84B was relinquished to the City of
#     Caldwell (owner, Item 42); Blaine St is local in the layer, Canyon St unnumbered.

#   * Sandpoint US-2/US-95 1st Ave / 5th Ave — a divided highway, not a couplet (owner,
#     Session 65 review; removed Item 51). The detector never found it on the 2026
#     network either.

KNOWN_COUPLETS: list[dict] = [
    # District 2
    {"district": 2, "city": "Moscow", "route": "US-95",
     "street1": "S Washington St", "street2": "S Jackson St", "miles": 0.65},
    # District 3
    {"district": 3, "city": "Boise", "route": "US-20/26",
     "street1": "W Myrtle St", "street2": "W Front St", "miles": 1.15},
    {"district": 3, "city": "Nampa", "route": "I-84B",
     "street1": "3rd St S", "street2": "2nd St S", "miles": 0.70},
    {"district": 3, "city": "Mountain Home", "route": "I-84B/SH-51",
     "street1": "American Legion Blvd", "street2": "Jackson St", "miles": 1.1},
    {"district": 3, "city": "Weiser", "route": "US-95 Spur",
     "street1": "W Idaho St", "street2": "W Main St", "miles": 0.52},
    # District 4
    {"district": 4, "city": "Twin Falls", "route": "US-30",
     "street1": "2nd Ave S", "street2": "2nd Ave N", "miles": 1.30},
    # District 5
    {"district": 5, "city": "Pocatello", "route": "I-15B/US-91",
     "street1": "4th Ave", "street2": "5th Ave", "miles": 2.22},
    {"district": 5, "city": "Blackfoot", "route": "I-15B/US-91",
     "street1": "W Judicial St", "street2": "W Bridge St", "miles": 0.55},
    {"district": 5, "city": "Preston", "route": "US-91/SH-34",
     "street1": "State St", "street2": "800 W Rd", "miles": 0.8},
    {"district": 5, "city": "American Falls", "route": "SH-39",
     "street1": "Idaho St", "street2": "Lamb Weston Rd", "miles": 0.5},
    # District 6
    {"district": 6, "city": "Idaho Falls", "route": "US-26B",
     "street1": "Yellowstone Ave", "street2": "Broadway", "miles": 0.4},
]


# ─── Detection Algorithm ─────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Make URL-friendly identifier string."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s or "corridor"


def _bearing_to_direction(bearing: str) -> str:
    """Convert cardinal bearing to directional suffix."""
    m = {"N": "NB", "S": "SB", "E": "EB", "W": "WB"}
    return m.get(bearing.upper(), bearing)


_STREET_PREFIX = re.compile(r"^(?:N|S|E|W|NE|NW|SE|SW)\s+", re.IGNORECASE)
_STREET_SUFFIX = re.compile(r"\s+(N|S|E|W|NE|NW|SE|SW)$", re.IGNORECASE)
_ORDINAL = re.compile(r"\b(\d+)(St|Nd|Rd|Th)\b")


def _base_street(name: str) -> str:
    """``street_key`` with the trailing quadrant removed as well."""
    return _STREET_SUFFIX.sub("", str(name).strip()).strip().lower()


def _crossing_quadrant(name: str, bearing_deg: float | None) -> bool:
    """True when a street's trailing quadrant is *not* its direction of travel."""
    m = _STREET_SUFFIX.search(str(name).strip())
    if not m:
        return False
    return m.group(1).upper() != _cardinal(bearing_deg)


def street_key(name: str) -> str:
    """A street's identity with its leading directional quadrant removed.

    ``E Front St`` and ``W Front St`` are one street either side of a numbering
    origin, and a couplet leg routinely crosses that origin; comparing the raw
    ``RoadName`` splits one leg into two runs and then reports the two halves as
    a couplet with themselves.

    A **trailing** quadrant is the opposite case and is kept: Twin Falls'
    ``2nd Ave N`` and ``2nd Ave S`` are two streets a block apart — the US-30
    couplet itself — and normalising both to ``2nd Ave`` makes the real couplet
    fail the distinct-street test.
    """
    out = str(name).strip()
    while True:
        stripped = _STREET_PREFIX.sub("", out).strip()
        if stripped == out or not stripped:
            # ``str.title`` writes "5Th Ave"; ordinals keep a lower-case suffix.
            return _ORDINAL.sub(lambda m: m.group(1) + m.group(2).lower(), out.title())
        out = stripped


def _street_runs(walk: Sequence[int], by_id: pd.DataFrame) -> list[list[int]]:
    """Split a chain into maximal runs sharing one :func:`street_key`."""
    runs: list[list[int]] = []
    current: list[int] = []
    current_key = None
    for sid in walk:
        row = by_id.loc[sid]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        raw = row.get("RoadName")
        key = street_key(raw) if pd.notna(raw) and str(raw).strip() else ""
        if key != current_key and current:
            runs.append(current)
            current = []
        current_key = key
        current.append(sid)
    if current:
        runs.append(current)
    return runs


def _chain_bearing(chain_rows: pd.DataFrame) -> float | None:
    """Compass bearing of a chain's first-segment start to last-segment end."""
    geoms = [g for g in chain_rows.geometry if g is not None and not g.is_empty]
    if not geoms:
        return None
    try:
        first, last = list(geoms[0].coords), list(geoms[-1].coords)
    except NotImplementedError:
        return _geo._bearing_deg(geoms[0])
    return _geo._bearing_deg(LineString([first[0], last[-1]]))


def _cardinal(bearing_deg: float | None, fallback: str = "") -> str:
    """Nearest cardinal letter to a compass bearing (``N``/``E``/``S``/``W``)."""
    if bearing_deg is None:
        return fallback
    return ["N", "E", "S", "W", "N"][int((bearing_deg % 360.0 + 45.0) // 90.0)]


def _combine_geometries(geoms: Sequence[LineString | MultiLineString]) -> LineString | MultiLineString | None:
    """Merge sequence of line geometries into a unified line."""
    valid = [g for g in geoms if g is not None and not g.is_empty]
    if not valid:
        return None
    try:
        merged = linemerge(valid)
        return merged
    except Exception:
        return MultiLineString(valid)


def _membership_routes(value) -> tuple[str, ...]:
    """``"12/95"`` -> ``("12", "95")``; ``()`` for no route."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ()
    return tuple(sorted(x.strip() for x in str(value).split("/") if x.strip()))


def detect_couplets(
    network: gpd.GeoDataFrame,
    *,
    counties: Sequence[str] | None = None,
    min_length_mi: float = 0.20,
    max_length_mi: float = 3.50,
    max_lateral_sep_m: float = 300.0,
    min_lateral_sep_m: float = 25.0,
    bearing_tolerance_deg: float = 60.0,
    rejected: list | None = None,
) -> list[CoupletPair]:
    """Detect one-way couplet pairs in an XD network.

    With ITD route membership applied (``routes.apply_route_membership``: the
    ``itd_routes`` and ``itd_route_id`` columns), a leg must be on the state system and
    the two legs must **share a route under membership**, and the pair must pass the
    Item 51 tests on the SHS lines (:func:`drop_same_line_pairs`,
    :func:`drop_divided_pairs`) as well as the Item 49 ones.

    Args:
        network: XD network GeoDataFrame (EPSG:4326).
        counties: Optional filter to specific counties.
        min_length_mi: Minimum couplet chain length (default 0.20 mi).
        max_length_mi: Maximum couplet chain length (default 3.50 mi).
        max_lateral_sep_m: Maximum perpendicular separation in meters (default 300 m).
        min_lateral_sep_m: Minimum separation to exclude same-street dups (default 25 m).
        bearing_tolerance_deg: Maximum angular deviation from anti-parallel (default 60°).
        rejected: when given, every pair a one-way test removed is appended as
            ``(pair, reason)``, for the review table.

    Returns:
        List of CoupletPair objects, sorted by county then total_miles descending.
    """
    if network.empty:
        return []

    slip_series = network["SlipRoad"] if "SlipRoad" in network.columns else pd.Series(0, index=network.index)
    use_membership = ITD_ROUTES_COL in network.columns
    if use_membership:
        on_route = network[ITD_ROUTES_COL].map(_membership_routes).map(bool)
    else:
        on_route = (network["RoadNumber"].notna()
                    & (network["RoadNumber"].astype(str).str.strip() != ""))
    hw = network[
        on_route
        & (slip_series.fillna(0).astype(str).isin(["0", "0.0"]))
        & network["Bearing"].isin(["N", "S", "E", "W"])
    ].copy()

    if counties is not None:
        hw = hw[hw["County"].isin(counties)].copy()

    if hw.empty:
        return []

    # Ensure XDSegID is indexed
    if "XDSegID" in hw.columns:
        hw_by_id = hw.set_index("XDSegID", drop=False)
    else:
        hw_by_id = hw.copy()

    # Step 2: Build chains per XDGroup
    candidate_chains: list[dict] = []

    # Prepare metric CRS for distance measuring
    try:
        metric_crs = network.estimate_utm_crs()
    except Exception:
        metric_crs = "EPSG:3857"

    for xdgroup, grp_df in hw.groupby("XDGroup"):
        if pd.isna(xdgroup):
            continue
        xdgroup = int(xdgroup)
        grp_ids = set(grp_df["XDSegID"].astype(int))

        # Build forward adjacency within group
        nxt_map: dict[int, int] = {}
        for sid, row in grp_df.iterrows():
            seg_id = int(row["XDSegID"])
            nxt_raw = row.get("NextXDSegI")
            if pd.notna(nxt_raw):
                nxt_val = int(nxt_raw)
                if nxt_val in grp_ids:
                    nxt_map[seg_id] = nxt_val

        # Find head segments (no predecessor in group)
        predecessors = set(nxt_map.values())
        heads = [s for s in grp_ids if s not in predecessors]
        if not heads:
            heads = list(grp_ids)[:1]

        visited: set[int] = set()
        walks: list[list[int]] = []
        for head in heads:
            if head in visited:
                continue
            curr = head
            walk: list[int] = []
            while curr is not None and curr in grp_ids and curr not in visited:
                walk.append(curr)
                visited.add(curr)
                curr = nxt_map.get(curr)
            if walk:
                walks.append(walk)

        # A couplet leg is a *sub-run* of a carriageway, not a whole XDGroup chain.
        # Boise's westbound US-20/26 is one 3.98-mile group that runs up Broadway
        # Ave and only then turns onto Front St; filtering whole groups by length
        # threw it out, and its majority street name was "S Broadway Ave". Splitting
        # each chain at every change of street name is also the couplet definition
        # itself — "two parallel one-way *streets* on distinct alignments".
        for walk in walks:
            for chain in _street_runs(walk, hw_by_id):
                chain_rows = hw_by_id.loc[chain]
                if isinstance(chain_rows, pd.Series):
                    chain_rows = chain_rows.to_frame().T

                miles_sum = float(chain_rows["Miles"].fillna(0.0).sum())
                if not (min_length_mi <= miles_sum <= max_length_mi):
                    continue

                bearings = [str(b).strip() for b in chain_rows["Bearing"].dropna()]
                if not bearings:
                    continue
                majority_bearing = Counter(bearings).most_common(1)[0][0]

                if use_membership:
                    # "Share a route" means under ITD membership (Item 51), not
                    # INRIX's RoadNumber.
                    rnums = tuple(sorted({r for v in chain_rows[ITD_ROUTES_COL]
                                          for r in _membership_routes(v)}))
                else:
                    rnums = tuple(sorted(set(
                        str(rn).strip() for rn in chain_rows["RoadNumber"].dropna()
                        if str(rn).strip()
                    )))
                if not rnums:
                    continue

                rnames = [str(rn).strip() for rn in chain_rows["RoadName"].dropna() if str(rn).strip()]
                primary_street = Counter(street_key(n) for n in rnames).most_common(1)[0][0] if rnames else ""
                if not primary_street:
                    continue

                county = str(chain_rows["County"].iloc[0]) if "County" in chain_rows else ""
                postal = str(chain_rows["PostalCode"].iloc[0]) if "PostalCode" in chain_rows else ""
                if pd.isna(postal):
                    postal = ""

                merged_geom = _combine_geometries(list(chain_rows.geometry))
                if merged_geom is None or merged_geom.is_empty:
                    continue

                # The run's *geometric* heading, start of the first segment to end
                # of the last, in travel order. The XD ``Bearing`` field cannot
                # stand in for it: Boise's Front St westbound carriageway is coded
                # ``N`` because the street curves, so a cardinal-opposition test
                # ("E needs W") rejects the best-known couplet in the state.
                candidate_chains.append({
                    "segment_ids": tuple(chain),
                    "miles": miles_sum,
                    "bearing": majority_bearing,
                    "geo_bearing": _chain_bearing(chain_rows),
                    "xdgroup": xdgroup,
                    "street": primary_street,
                    "route_numbers": rnums,
                    "county": county,
                    "postal_code": postal,
                    "geometry": merged_geom,
                })

    # Step 3: Find opposing pairs. A run belongs to at most one couplet — without
    # that, two adjacent parallel runs report themselves twice with the roles
    # swapped (Idaho Falls' SH-43 did exactly this).
    pairs: list[CoupletPair] = []
    n_chains = len(candidate_chains)
    paired_runs: set[int] = set()

    for i in range(n_chains):
        c1 = candidate_chains[i]
        if c1["geo_bearing"] is None or i in paired_runs:
            continue

        for j in range(i + 1, n_chains):
            if j in paired_runs:
                continue
            c2 = candidate_chains[j]
            if not _geo._anti_parallel(c1["geo_bearing"], c2["geo_bearing"], bearing_tolerance_deg):
                continue
            if c2["xdgroup"] == c1["xdgroup"]:
                continue

            # Distinct streets (ignoring case and whitespace)
            s1 = street_key(c1["street"]).lower()
            s2 = street_key(c2["street"]).lower()
            if s1 == s2:
                continue
            # ...and distinct *alignments*, which is not the same test. Keeping the
            # trailing quadrant is what lets Twin Falls' ``2nd Ave N`` / ``2nd Ave S``
            # read as two streets, but it also makes ``US-95 N`` / ``US-95 S`` — the
            # two carriageways of one divided highway — look like a couplet. They
            # are told apart by what the quadrant means: on a divided highway it
            # restates the carriageway's own direction of travel, and on a grid
            # couplet it names the side of the origin the street sits on.
            if _base_street(s1) == _base_street(s2) and not (
                _crossing_quadrant(c1["street"], c1["geo_bearing"])
                and _crossing_quadrant(c2["street"], c2["geo_bearing"])
            ):
                continue

            # Overlapping route numbers
            shared_routes = tuple(sorted(set(c1["route_numbers"]) & set(c2["route_numbers"])))
            if not shared_routes:
                continue

            # County/City agreement
            if c1["county"] and c2["county"] and c1["county"] != c2["county"]:
                continue

            # Lateral separation in projected metric CRS
            try:
                g1_proj = gpd.GeoSeries([c1["geometry"]], crs="EPSG:4326").to_crs(metric_crs).iloc[0]
                g2_proj = gpd.GeoSeries([c2["geometry"]], crs="EPSG:4326").to_crs(metric_crs).iloc[0]

                # Sample 7 points along line 1 and compute orthogonal distance to line 2
                fractions = np.linspace(0.1, 0.9, 7)
                dists = []
                for f in fractions:
                    pt = g1_proj.interpolate(f, normalized=True)
                    dists.append(g2_proj.distance(pt))

                mean_sep = float(np.mean(dists))
            except Exception:
                # Fallback if reprojection fails
                mean_sep = float(min_lateral_sep_m + 50.0)

            if not (min_lateral_sep_m <= mean_sep <= max_lateral_sep_m):
                continue

            avg_miles = round((c1["miles"] + c2["miles"]) / 2.0, 3)
            county_name = c1["county"] or c2["county"]
            postal_code = c1["postal_code"] or c2["postal_code"]

            paired_runs.add(i)
            paired_runs.add(j)
            pairs.append(CoupletPair(
                dir1_segment_ids=c1["segment_ids"],
                dir2_segment_ids=c2["segment_ids"],
                dir1_bearing=_cardinal(c1["geo_bearing"], c1["bearing"]),
                dir2_bearing=_cardinal(c2["geo_bearing"], c2["bearing"]),
                dir1_street=c1["street"],
                dir2_street=c2["street"],
                route_numbers=shared_routes,
                total_miles=avg_miles,
                mean_lateral_sep_m=round(mean_sep, 1),
                county=county_name,
                postal_code=postal_code,
            ))
            break

    # Step 4: a couplet is two one-way streets. A street pair found in **both**
    # directions, or a leg with its own street's opposing carriageway lying on it, is
    # a two-way road (Item 49; Item 51 takes the rule further).
    def _drop(kept: list[CoupletPair], reason: str) -> list[CoupletPair]:
        nonlocal pairs
        if rejected is not None:
            keep_ids = {id(p) for p in kept}
            rejected.extend((p, reason) for p in pairs if id(p) not in keep_ids)
        pairs = kept
        return kept

    _drop(drop_mirrored_pairs(pairs), "mirrored")
    _drop(drop_two_way_legs(pairs, network, metric_crs=metric_crs), "two_way_leg")
    # Item 51: the SHS says which line each leg lies on.
    _drop(drop_same_line_pairs(pairs, network), "same_shs_line")
    _drop(drop_divided_pairs(pairs, network), "divided_highway")
    # ...and a leg ends where the couplet does (Pocatello's 5th Ave ran on 0.65 mi
    # north of 4th Ave and 1 mi south onto two-way pavement).
    pairs = trim_leg_overhang(pairs, network, metric_crs=metric_crs,
                              max_sep_m=max_lateral_sep_m)
    pairs = [p for p in pairs if p.dir1_segment_ids and p.dir2_segment_ids]

    # Step 5: Sort by county then total miles descending
    pairs.sort(key=lambda p: (p.county, -p.total_miles))
    return pairs


TWO_WAY_TOL_M = 15.0
"""How close an opposing segment of the leg's own street must lie to count as the
other half of a two-way road: well under the 25 m minimum couplet separation."""


def _opposed(b1: float | None, b2: float | None) -> bool:
    if b1 is None or b2 is None:
        return False
    d = abs((b1 - b2 + 180.0) % 360.0 - 180.0)
    return d > 120.0


def drop_two_way_legs(pairs: Sequence[CoupletPair], network: gpd.GeoDataFrame, *,
                      metric_crs=None, tol_m: float = TWO_WAY_TOL_M,
                      min_share: float = 0.5) -> list[CoupletPair]:
    """Drop every pair with a leg that is one carriageway of a two-way street.

    A leg is two-way when, over at least ``min_share`` of its length, a segment of the
    **same street** (:func:`street_key`) runs the **opposite way** (bearings more than
    120 degrees apart) within ``tol_m`` of it along its whole length. A one-way couplet
    street has no such twin. SH-77 at Elba is the case (Item 49): the detector paired
    Elba-Almo Rd westbound with Elba-Almo Hwy eastbound, two consecutive pieces of one
    rural two-way road where its name changes, and the 272 m "separation" was the
    offset along the road. The test is local to the leg's own segments, so a one-way
    street that continues as a two-way street beyond the couplet still passes.
    ``XD``'s ``Bearing`` is not used: it is a carriageway label (Elba-Almo's westbound
    segments carry ``S``), not a direction of travel."""
    if not pairs or network.empty:
        return list(pairs)
    net = network.set_index("XDSegID", drop=False) if "XDSegID" in network.columns \
        else network
    names = net["RoadName"].fillna("").map(lambda n: street_key(n).lower())
    crs = metric_crs or network.estimate_utm_crs()
    kept = []
    for p in pairs:
        two_way = False
        for leg, street in ((p.dir1_segment_ids, p.dir1_street),
                            (p.dir2_segment_ids, p.dir2_street)):
            leg_ids = [s for s in leg if s in net.index]
            if not leg_ids:
                continue
            own = set(leg_ids)
            leg_streets = {names[s] for s in leg_ids} | {street_key(street).lower()}
            cand = net[names.isin(leg_streets) & ~net.index.isin(own)]
            if cand.empty:
                continue
            cand_m = cand.geometry.to_crs(crs)
            cand_b = {i: _geo._bearing_deg(g) for i, g in cand.geometry.items()}
            leg_geo = net.loc[leg_ids]
            leg_m = leg_geo.geometry.to_crs(crs)
            total = twinned = 0.0
            for sid, g in leg_m.items():
                total += g.length
                b = _geo._bearing_deg(leg_geo.geometry[sid])
                near = cand_m[cand_m.distance(g) <= tol_m]
                for cid, cg in near.items():
                    pts = [g.interpolate(f, normalized=True) for f in (0.1, 0.5, 0.9)]
                    if _opposed(b, cand_b[cid]) and all(cg.distance(pt) <= tol_m
                                                         for pt in pts):
                        twinned += g.length
                        break
            if total > 0 and twinned / total >= min_share:
                two_way = True
                break
        if not two_way:
            kept.append(p)
    return kept


DIVIDED_MAX_SEP_M = 50.0
"""An ``A``/``D`` pair of one SHS route closer than this is a divided highway's two
carriageways. The real couplets the SHS draws as ``A`` + ``D`` sit a block apart
(Blackfoot 78 m, Nampa 115 m, Moscow 179 m); American Falls' ID-39 is 28 m."""


def _leg_line(leg: Sequence[int], net) -> str | None:
    """The SHS line (``itd_route_id``, travelway letter included) most of a leg's miles
    lie on, or ``None``."""
    if ITD_ROUTE_ID_COL not in net.columns:
        return None
    sub = net.reindex([s for s in leg if s in net.index])
    rid = sub[ITD_ROUTE_ID_COL]
    miles = pd.to_numeric(sub.get("Miles"), errors="coerce").fillna(0.0)
    have = rid.notna() & (rid.astype(str).str.len() >= 6)
    if not have.any():
        return None
    by = miles[have].groupby(rid[have].astype(str)).sum()
    return str(by.idxmax())


def _indexed(network):
    return network.set_index("XDSegID", drop=False) if "XDSegID" in network.columns \
        else network


def drop_same_line_pairs(pairs: Sequence[CoupletPair], network) -> list[CoupletPair]:
    """Drop every pair whose two legs lie on **one** SHS line: that is one road.

    ITD draws a couplet's second leg as its own line — the route's ``D`` travelway
    (Moscow's Jackson St ``01540DUS095`` beside Washington St's ``01540AUS095``; Boise's
    Front St; Pocatello's 4th Ave) — so two legs on the same ``RouteID`` *and*
    travelway are the two directions of one two-way road, whatever INRIX names them.
    Shoshone is the case (Item 51): ``S Greenwood St`` southbound and ``US-93``
    northbound, 172 m "apart" only because the one runs on from the other, both on
    ``02220AUS093``. A pair with no SHS line on a leg is left alone."""
    net = _indexed(network)
    out = []
    for p in pairs:
        a, b = _leg_line(p.dir1_segment_ids, net), _leg_line(p.dir2_segment_ids, net)
        if a is not None and a == b:
            continue
        out.append(p)
    return out


def drop_divided_pairs(pairs: Sequence[CoupletPair], network, *,
                       max_sep_m: float = DIVIDED_MAX_SEP_M) -> list[CoupletPair]:
    """Drop every pair that is the ``A`` and ``D`` carriageways of one divided route.

    ``Travelway`` ``D`` is not by itself "not a couplet" — the SHS draws most real
    couplets' second leg as ``D`` (Item 51 found six) — so the test is the pair's
    spacing: legs on the ``A`` and ``D`` lines of one ``RouteID`` closer than
    ``max_sep_m`` are a divided highway's carriageways (American Falls' ID-39 S / ID-39
    N at 28 m), not two streets a block apart."""
    net = _indexed(network)
    out = []
    for p in pairs:
        a, b = _leg_line(p.dir1_segment_ids, net), _leg_line(p.dir2_segment_ids, net)
        if (a is not None and b is not None and a[:5] == b[:5] and a[6:] == b[6:]
                and {a[5], b[5]} == {"A", "D"} and p.mean_lateral_sep_m < max_sep_m):
            continue
        out.append(p)
    return out


def trim_leg_overhang(pairs: Sequence[CoupletPair], network, *, metric_crs=None,
                      max_sep_m: float = 300.0,
                      tol_m: float = TWO_WAY_TOL_M) -> list[CoupletPair]:
    """Trim each leg's end segments that are not part of the couplet (Item 51).

    A leg is a run of one street, and the street goes on past the couplet: Pocatello's
    5th Ave leg ran 0.65 mi north of where 4th Ave ends (532 m from it) and 1 mi south
    onto pavement that carries both directions. From each end of a leg, a segment is
    trimmed while it is **two-way** (an opposing segment of its own street lies on it,
    as :func:`drop_two_way_legs` reads it) or its midpoint lies more than ``max_sep_m``
    from the other leg. Only the ends: a leg never gets a hole. ``total_miles`` is
    recomputed."""
    import dataclasses

    if not pairs or network.empty:
        return list(pairs)
    net = _indexed(network)
    crs = metric_crs or network.estimate_utm_crs()
    geom = net.geometry.to_crs(crs)
    names = net["RoadName"].fillna("").map(lambda n: street_key(n).lower())
    brg = {}

    def _b(sid):
        if sid not in brg:
            brg[sid] = _geo._bearing_deg(net.geometry[sid])
        return brg[sid]

    def _two_way(sid, leg) -> bool:
        g = geom[sid]
        cand = net.index[(names == names[sid]) & ~net.index.isin(leg)]
        for cid in cand:
            cg = geom[cid]
            if cg.distance(g) > tol_m or not _opposed(_b(sid), _b(cid)):
                continue
            pts = [g.interpolate(f, normalized=True) for f in (0.1, 0.5, 0.9)]
            if all(cg.distance(pt) <= tol_m for pt in pts):
                return True
        return False

    def _trim(leg, other):
        leg = [s for s in leg if s in net.index]
        other_g = geom.reindex([s for s in other if s in net.index]).union_all()

        def _out(sid):
            mid = geom[sid].interpolate(0.5, normalized=True)
            return other_g.distance(mid) > max_sep_m or _two_way(sid, leg)

        lo, hi = 0, len(leg)
        while lo < hi and _out(leg[lo]):
            lo += 1
        while hi > lo and _out(leg[hi - 1]):
            hi -= 1
        return tuple(leg[lo:hi])

    miles = pd.to_numeric(net["Miles"], errors="coerce").fillna(0.0) \
        if "Miles" in net.columns else None
    out = []
    for p in pairs:
        a = _trim(list(p.dir1_segment_ids), p.dir2_segment_ids)
        b = _trim(list(p.dir2_segment_ids), p.dir1_segment_ids)
        if (a, b) == (p.dir1_segment_ids, p.dir2_segment_ids):
            out.append(p)
            continue
        total = (round((float(miles.reindex(a).sum()) + float(miles.reindex(b).sum())) / 2, 3)
                 if miles is not None else p.total_miles)
        out.append(dataclasses.replace(p, dir1_segment_ids=a, dir2_segment_ids=b,
                                       total_miles=total))
    return out


def drop_mirrored_pairs(pairs: Sequence[CoupletPair]) -> list[CoupletPair]:
    """Drop every pair whose two streets were also paired the other way round.

    A couplet is two one-way streets, each carrying one direction. When the detector
    pairs street A westbound with street B eastbound **and** A eastbound with B
    westbound, each street carries both directions, so they are two parallel two-way
    (or divided) roads, and neither pairing is a couplet. Lewiston is the case (Item
    49): once Item 52's membership put US-12 on the levee bypass, "Us Highway 12" and
    the Levee Byp were paired twice, 223 m apart, as mirror images. Both pairs got the
    same catalogue id, which is how the builder found them."""
    def key(p):
        return (p.county, frozenset((street_key(p.dir1_street), street_key(p.dir2_street))))

    bearings: dict = {}
    for p in pairs:
        bearings.setdefault(key(p), set()).add(
            (street_key(p.dir1_street), p.dir1_bearing))
        bearings[key(p)].add((street_key(p.dir2_street), p.dir2_bearing))
    two_way = {k for k, seen in bearings.items()
               if any(sum(1 for s2, _ in seen if s2 == s) > 1 for s, _ in seen)}
    return [p for p in pairs if key(p) not in two_way]


def filter_by_district(
    couplets: list[CoupletPair],
    district: int,
) -> list[CoupletPair]:
    """Filter detected couplets to those in a specific ITD district."""
    if district not in DISTRICT_COUNTIES:
        return []
    counties = set(DISTRICT_COUNTIES[district])
    return [c for c in couplets if c.county in counties]


def couplet_catalogue_entries(
    pair: CoupletPair,
    network: gpd.GeoDataFrame,
) -> tuple[dict, dict, dict]:
    """Convert a CoupletPair into corridor catalogue entries.

    Returns:
        (dir1_entry, dir2_entry, reporting_corridor) matching the
        corridor catalogue JSON schema.
    """
    if "XDSegID" in network.columns:
        net_idx = network.set_index("XDSegID", drop=False)
    else:
        net_idx = network

    def _get_coords(seg_id: int, end: bool = False) -> list[float]:
        # Off the geometry, not off ``StartLat``/``EndLat``: the two disagree on
        # some XD records, and a catalogue endpoint written from the declared
        # value snaps to whichever road happens to be nearer (see
        # :func:`extents.segment_endpoint`).
        lat, lon = _segment_endpoint(net_idx, seg_id, end=end)
        return [round(lat, 5), round(lon, 5)]

    rnum = pair.route_numbers[0] if pair.route_numbers else "HWY"
    # The band comes from what the network calls the route. Guessing it from the
    # digit count ("two digits means US") labels SH-55 "US-55". RoadList is read as
    # well as RoadName (Item 51).
    names = []
    if "RoadNumber" in net_idx.columns and "RoadName" in net_idx.columns:
        same = net_idx[net_idx["RoadNumber"].astype(str).str.strip() == rnum]
        names = [str(v).strip() for v in same["RoadName"].dropna() if str(v).strip()]
        if "RoadList" in same.columns:
            names += [t.strip() for v in same["RoadList"].dropna()
                      for t in str(v).split("|") if t.strip()]
    legs = net_idx.reindex([s for s in (*pair.dir1_segment_ids, *pair.dir2_segment_ids)
                            if s in net_idx.index])
    label = _business_band(_route_label(rnum, names), legs)
    # Named for the town (Item 52's urban area) where there is one, like the corridors.
    town = _place_name(legs) if len(legs) else ""
    place = town or (f"{pair.county} County" if pair.county else "Couplet")

    # Named for the two streets rather than the ZIP: the XD ``PostalCode`` is a
    # postal code, so a ZIP-keyed id reads ``83672-95-couplet`` and says nothing.
    group_id = _slugify(f"couplet-{rnum}-{street_key(pair.dir1_street)}-{street_key(pair.dir2_street)}")
    dir1_dir = _bearing_to_direction(pair.dir1_bearing)
    dir2_dir = _bearing_to_direction(pair.dir2_bearing)

    dir1_id = _slugify(f"{group_id}-{dir1_dir.lower()}")
    dir2_id = _slugify(f"{group_id}-{dir2_dir.lower()}")

    dir1_start = _get_coords(pair.dir1_segment_ids[0], end=False)
    dir1_end = _get_coords(pair.dir1_segment_ids[-1], end=True)
    dir2_start = _get_coords(pair.dir2_segment_ids[0], end=False)
    dir2_end = _get_coords(pair.dir2_segment_ids[-1], end=True)

    reporting = {
        "id": group_id,
        "name": f"{label}: {pair.dir1_street} / {pair.dir2_street} couplet, {place}",
        "description": (
            f"One-way couplet carrying {label} across {pair.dir1_street} "
            f"({dir1_dir}) and {pair.dir2_street} ({dir2_dir}) in {place} "
            f"(ZIP {pair.postal_code or 'n/a'}); {pair.total_miles:.2f} mi per leg, "
            f"mean lateral separation {pair.mean_lateral_sep_m:.0f} m. Detected by "
            f"inrix_tools.couplets.detect_couplets (ROADMAP Item 46)."
        ),
        "one_way_couplet": True,
        "_couplet": True,
        "_mean_lateral_sep_m": pair.mean_lateral_sep_m,
    }

    entry1 = {
        "id": dir1_id,
        "name": f"{label} {dir1_dir}: {pair.dir1_street} couplet leg, {place}",
        "start_latlon": dir1_start,
        "end_latlon": dir1_end,
        "description": (f"One-way couplet leg on {pair.dir1_street} ({dir1_dir}) carrying "
                        f"{label} through {place}, {len(pair.dir1_segment_ids)} segments; "
                        f"the opposing leg runs on {pair.dir2_street} "
                        f"{pair.mean_lateral_sep_m:.0f} m away."),
        "corridor": group_id,
        "direction": dir1_dir,
    }

    entry2 = {
        "id": dir2_id,
        "name": f"{label} {dir2_dir}: {pair.dir2_street} couplet leg, {place}",
        "start_latlon": dir2_start,
        "end_latlon": dir2_end,
        "description": (f"One-way couplet leg on {pair.dir2_street} ({dir2_dir}) carrying "
                        f"{label} through {place}, {len(pair.dir2_segment_ids)} segments; "
                        f"the opposing leg runs on {pair.dir1_street} "
                        f"{pair.mean_lateral_sep_m:.0f} m away."),
        "corridor": group_id,
        "direction": dir2_dir,
    }

    return entry1, entry2, reporting


# ─── Validating the detector against the known registry (Item 46) ────

_ROUTE_NUM_RE = re.compile(r"(\d+)")


def registry_route_numbers(route: str) -> set[str]:
    """Route numbers a :data:`KNOWN_COUPLETS` ``route`` string names.

    ``"US-20/26"`` -> ``{"20", "26"}``; ``"I-84B"`` -> ``{"84"}``. ``"STC-7195"``
    yields ``{"7195"}``, which the XD network carries no ``RoadNumber`` for —
    that is a finding about the registry entry, not a parsing failure.
    """
    return set(_ROUTE_NUM_RE.findall(str(route)))


def _street_tokens(name: str) -> set[str]:
    """Comparable words of a street name, without quadrants or the road type."""
    drop = {"n", "s", "e", "w", "ne", "nw", "se", "sw",
            "st", "street", "ave", "avenue", "rd", "road", "blvd", "boulevard",
            "dr", "drive", "way", "hwy", "highway", "ln", "lane", "pkwy"}
    words = re.split(r"[^a-z0-9]+", str(name).lower())
    return {w for w in words if w and w not in drop}


def match_known_couplets(
    detected: Sequence[CoupletPair],
    *,
    district: int | None = None,
    known: Sequence[dict] = None,
) -> pd.DataFrame:
    """Check the detector against :data:`KNOWN_COUPLETS`, one row per registry entry.

    ROADMAP Item 45 listed sixteen couplets the statewide catalogue was supposed
    to carry, and Item 46 found the registry was only ever asserted for *shape* —
    nothing checked whether :func:`detect_couplets` actually finds them. This is
    that check. It **reports** rather than asserts, because several registry
    entries are not findable from the XD network at all (a leg that carries no
    ``RoadNumber``, or a leg absent from the network), and a detector tuned until
    those pass would be tuned to the wrong target.

    Args:
        detected: pairs from :func:`detect_couplets` (statewide, or one district's).
        district: restrict the registry to one district.
        known: registry to check against (default :data:`KNOWN_COUPLETS`).

    Returns:
        One row per registry entry: ``district``, ``city``, ``route``,
        ``street1`` / ``street2``, ``match_kind`` (``"streets"`` — both legs'
        names recognised; ``"one_street"``; ``"route_county"`` — the right route
        in the right county but neither street name matched; ``"none"``),
        ``detected_streets``, ``detected_miles``, ``registry_miles``,
        ``mean_lateral_sep_m``.
    """
    rows = []
    registry = list(KNOWN_COUPLETS if known is None else known)
    if district is not None:
        registry = [k for k in registry if k.get("district") == district]

    for entry in registry:
        want_routes = registry_route_numbers(entry.get("route", ""))
        counties = set(DISTRICT_COUNTIES.get(entry.get("district"), []))
        t1, t2 = _street_tokens(entry.get("street1", "")), _street_tokens(entry.get("street2", ""))

        best, best_rank = None, -1
        for pair in detected:
            if counties and pair.county and pair.county not in counties:
                continue
            route_ok = bool(want_routes & set(pair.route_numbers))
            d1, d2 = _street_tokens(pair.dir1_street), _street_tokens(pair.dir2_street)
            hits = sum(bool(t & d) for t in (t1, t2) for d in (d1, d2))
            rank = (2 if hits >= 2 else 1 if hits == 1 else 0) * 2 + (1 if route_ok else 0)
            if rank > best_rank and rank > 0:
                best, best_rank = pair, rank

        if best is None:
            kind = "none"
        elif best_rank >= 4:
            kind = "streets"
        elif best_rank >= 2:
            kind = "one_street"
        else:
            kind = "route_county"

        rows.append({
            "district": entry.get("district"),
            "city": entry.get("city"),
            "route": entry.get("route"),
            "street1": entry.get("street1"),
            "street2": entry.get("street2"),
            "match_kind": kind,
            "detected_streets": (f"{best.dir1_street} / {best.dir2_street}" if best else ""),
            "detected_routes": ("/".join(best.route_numbers) if best else ""),
            "detected_miles": (best.total_miles if best else None),
            "registry_miles": entry.get("miles"),
            "mean_lateral_sep_m": (best.mean_lateral_sep_m if best else None),
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    # A route-and-county-only match is the weakest claim the matcher makes, and it
    # is wrong whenever a stronger row already owns that detected pair: Caldwell and
    # Mountain Home are both I-84B in counties that also hold Nampa's couplet, and
    # without this they would each be reported as "found" against Nampa's 3rd/2nd St.
    strong = set(out.loc[out["match_kind"].isin(["streets", "one_street"]), "detected_streets"])
    weak = (out["match_kind"] == "route_county") & out["detected_streets"].isin(strong)
    out.loc[weak, ["match_kind", "detected_streets", "detected_routes",
                   "detected_miles", "mean_lateral_sep_m"]] = ["none", "", "", None, None]
    return out
