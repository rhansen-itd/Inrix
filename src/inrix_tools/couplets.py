"""Statewide one-way couplet detection and pairing (ROADMAP Item 45.3).

Detects locations where a state highway route bifurcates into two parallel
one-way streets through an urban grid (e.g., Boise Myrtle/Front, Pocatello
4th/5th Ave) and pairs the opposing directions for corridor catalogue generation.

Pure core: no hardcoded paths, no CLI. Detection uses only the XD network
topology and geometry — no DuckDB queries required.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge

from . import geometry as _geo


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
# Validated from AADT layer "COUPLET"/"ONE-WAY" labels and XD topology.

KNOWN_COUPLETS: list[dict] = [
    # District 1
    {"district": 1, "city": "Sandpoint", "route": "US-2/US-95",
     "street1": "1st Ave", "street2": "5th Ave", "miles": 1.4},
    {"district": 1, "city": "Coeur d'Alene", "route": "STC-7195",
     "street1": "3rd St", "street2": "4th St", "miles": 0.8},
    # District 2
    {"district": 2, "city": "Moscow", "route": "US-95",
     "street1": "S Washington St", "street2": "S Jackson St", "miles": 0.65},
    {"district": 2, "city": "Lewiston", "route": "US-12",
     "street1": "Main St", "street2": "D St", "miles": 0.75},
    # District 3
    {"district": 3, "city": "Boise", "route": "US-20/26",
     "street1": "W Myrtle St", "street2": "W Front St", "miles": 1.15},
    {"district": 3, "city": "Nampa", "route": "I-84B",
     "street1": "3rd St S", "street2": "2nd St S", "miles": 0.70},
    {"district": 3, "city": "Caldwell", "route": "I-84B/SH-19",
     "street1": "Blaine St", "street2": "Canyon St", "miles": 0.9},
    {"district": 3, "city": "Mountain Home", "route": "I-84B/SH-51",
     "street1": "American Legion Blvd", "street2": "Jackson St", "miles": 1.1},
    {"district": 3, "city": "Weiser", "route": "US-95 Spur",
     "street1": "W Idaho St", "street2": "W Main St", "miles": 0.52},
    {"district": 3, "city": "Payette", "route": "US-95 Conn",
     "street1": "Main St", "street2": "7th St", "miles": 0.4},
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


def detect_couplets(
    network: gpd.GeoDataFrame,
    *,
    counties: Sequence[str] | None = None,
    min_length_mi: float = 0.20,
    max_length_mi: float = 3.50,
    max_lateral_sep_m: float = 300.0,
    min_lateral_sep_m: float = 25.0,
    bearing_tolerance_deg: float = 60.0,
) -> list[CoupletPair]:
    """Detect one-way couplet pairs in an XD network.

    Args:
        network: XD network GeoDataFrame (EPSG:4326).
        counties: Optional filter to specific counties.
        min_length_mi: Minimum couplet chain length (default 0.20 mi).
        max_length_mi: Maximum couplet chain length (default 3.50 mi).
        max_lateral_sep_m: Maximum perpendicular separation in meters (default 300 m).
        min_lateral_sep_m: Minimum separation to exclude same-street dups (default 25 m).
        bearing_tolerance_deg: Maximum angular deviation from anti-parallel (default 60°).

    Returns:
        List of CoupletPair objects, sorted by county then total_miles descending.
    """
    if network.empty:
        return []

    slip_series = network["SlipRoad"] if "SlipRoad" in network.columns else pd.Series(0, index=network.index)
    hw = network[
        network["RoadNumber"].notna()
        & (network["RoadNumber"].astype(str).str.strip() != "")
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
    opposing_bearing = {"N": "S", "S": "N", "E": "W", "W": "E"}
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
        for head in heads:
            if head in visited:
                continue
            curr = head
            chain: list[int] = []
            while curr is not None and curr in grp_ids and curr not in chain:
                chain.append(curr)
                visited.add(curr)
                curr = nxt_map.get(curr)

            if not chain:
                continue

            chain_rows = hw_by_id.loc[chain]
            if isinstance(chain_rows, pd.Series):
                chain_rows = chain_rows.to_frame().T

            miles_sum = float(chain_rows["Miles"].fillna(0.0).sum())
            if not (min_length_mi <= miles_sum <= max_length_mi):
                continue

            # Bearing consistency check
            bearings = [str(b).strip() for b in chain_rows["Bearing"].dropna()]
            if not bearings:
                continue
            b_counts = Counter(bearings)
            majority_bearing, maj_count = b_counts.most_common(1)[0]
            if maj_count / len(bearings) < 0.70:
                continue

            # Route numbers
            rnums = tuple(sorted(set(
                str(rn).strip() for rn in chain_rows["RoadNumber"].dropna()
                if str(rn).strip()
            )))
            if not rnums:
                continue

            # Street name (most common non-empty)
            rnames = [str(rn).strip() for rn in chain_rows["RoadName"].dropna() if str(rn).strip()]
            primary_street = Counter(rnames).most_common(1)[0][0] if rnames else ""
            if not primary_street:
                continue

            # County and PostalCode
            county = str(chain_rows["County"].iloc[0]) if "County" in chain_rows else ""
            postal = str(chain_rows["PostalCode"].iloc[0]) if "PostalCode" in chain_rows else ""
            if pd.isna(postal):
                postal = ""

            # Geometry
            geoms = list(chain_rows.geometry)
            merged_geom = _combine_geometries(geoms)
            if merged_geom is None or merged_geom.is_empty:
                continue

            candidate_chains.append({
                "segment_ids": tuple(chain),
                "miles": miles_sum,
                "bearing": majority_bearing,
                "xdgroup": xdgroup,
                "street": primary_street,
                "route_numbers": rnums,
                "county": county,
                "postal_code": postal,
                "geometry": merged_geom,
            })

    # Step 3: Find opposing pairs
    pairs: list[CoupletPair] = []
    n_chains = len(candidate_chains)

    for i in range(n_chains):
        c1 = candidate_chains[i]
        opp_b = opposing_bearing.get(c1["bearing"])
        if not opp_b:
            continue

        for j in range(i + 1, n_chains):
            c2 = candidate_chains[j]
            if c2["bearing"] != opp_b:
                continue
            if c2["xdgroup"] == c1["xdgroup"]:
                continue

            # Distinct streets (ignoring case and whitespace)
            s1 = c1["street"].lower().strip()
            s2 = c2["street"].lower().strip()
            if s1 == s2:
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

            pairs.append(CoupletPair(
                dir1_segment_ids=c1["segment_ids"],
                dir2_segment_ids=c2["segment_ids"],
                dir1_bearing=c1["bearing"],
                dir2_bearing=c2["bearing"],
                dir1_street=c1["street"],
                dir2_street=c2["street"],
                route_numbers=shared_routes,
                total_miles=avg_miles,
                mean_lateral_sep_m=round(mean_sep, 1),
                county=county_name,
                postal_code=postal_code,
            ))

    # Step 4: Sort by county then total miles descending
    pairs.sort(key=lambda p: (p.county, -p.total_miles))
    return pairs


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
        if seg_id in net_idx.index:
            row = net_idx.loc[seg_id]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            lat_col = "EndLat" if end else "StartLat"
            lon_col = "EndLong" if end else "StartLong"
            if lat_col in row and lon_col in row and pd.notna(row[lat_col]) and pd.notna(row[lon_col]):
                return [round(float(row[lat_col]), 5), round(float(row[lon_col]), 5)]
            if hasattr(row, "geometry") and row.geometry is not None:
                coords = list(row.geometry.coords)
                idx = -1 if end else 0
                return [round(float(coords[idx][1]), 5), round(float(coords[idx][0]), 5)]
        return [0.0, 0.0]

    rnum = pair.route_numbers[0] if pair.route_numbers else "HWY"
    route_label = f"US-{rnum}" if len(rnum) <= 2 else f"SH-{rnum}"
    city = pair.postal_code or pair.county or "Couplet"

    group_id = _slugify(f"{city}-{rnum}-couplet")
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
        "name": f"{route_label}: {city} Couplet ({pair.dir1_street}/{pair.dir2_street})",
        "description": f"One-way couplet in {city} carrying {route_label} across {pair.dir1_street} and {pair.dir2_street}",
        "one_way_couplet": True,
    }

    entry1 = {
        "id": dir1_id,
        "name": f"{route_label} {dir1_dir}: {city} ({pair.dir1_street})",
        "start_latlon": dir1_start,
        "end_latlon": dir1_end,
        "description": f"One-way couplet leg on {pair.dir1_street} ({dir1_dir}); opposing direction on {pair.dir2_street}",
        "corridor": group_id,
        "direction": dir1_dir,
    }

    entry2 = {
        "id": dir2_id,
        "name": f"{route_label} {dir2_dir}: {city} ({pair.dir2_street})",
        "start_latlon": dir2_start,
        "end_latlon": dir2_end,
        "description": f"One-way couplet leg on {pair.dir2_street} ({dir2_dir}); opposing direction on {pair.dir1_street}",
        "corridor": group_id,
        "direction": dir2_dir,
    }

    return entry1, entry2, reporting
