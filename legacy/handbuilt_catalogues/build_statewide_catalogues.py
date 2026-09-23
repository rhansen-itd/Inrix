#!/usr/bin/env python3
"""Build and verify corridor screening catalogues for Districts 1, 2, 4, 5, and 6.

Integrates:
1. Empirical candidate triage runs (ROADMAP Items 43, 45.4)
2. Statewide one-way couplet detection (ROADMAP Item 45.3)
3. Multi-scale extent tiers: Tier 1 Core, Tier 2 Commuter, Tier 3 Baseline (ROADMAP Items 45.1–45.2)
4. Full validation through corridors.resolve_catalogue ensuring 100% reached_target=True.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import corridors, couplets


def find_chain_endpoints(net, road_number, bearing, start_hint, end_hint):
    """Find start and end segments along a route closest to hint coordinates."""
    sub = net[(net["RoadNumber"] == str(road_number)) & (net["Bearing"] == bearing)]
    if sub.empty:
        sub = net[net["RoadNumber"] == str(road_number)]
    if sub.empty:
        raise ValueError(f"No segments found for Route {road_number} Bearing {bearing}")

    pt_start = Point(start_hint[1], start_hint[0])
    pt_end = Point(end_hint[1], end_hint[0])

    dists_start = sub.geometry.distance(pt_start)
    dists_end = sub.geometry.distance(pt_end)

    start_id = dists_start.idxmin()
    end_id = dists_end.idxmin()

    start_row = sub.loc[start_id]
    end_row = sub.loc[end_id]

    start_coords = [round(float(start_row["StartLat"]), 5), round(float(start_row["StartLong"]), 5)]
    end_coords = [round(float(end_row["EndLat"]), 5), round(float(end_row["EndLong"]), 5)]

    return start_coords, end_coords, start_id, end_id


def build_district_1_catalogue(net, repairs):
    """Build District 1 screening catalogue."""
    entries = []
    groups = []

    # 1. US-95 Coeur d'Alene - Hayden (from triage auto-95-nb / auto-95-sb)
    s_nb, e_nb = [47.68694, -116.79700], [47.75903, -116.79097]
    s_sb, e_sb = [47.75903, -116.79125], [47.68694, -116.79725]

    entries.extend([
        {
            "id": "us95-cda-nb",
            "name": "US-95 NB: Coeur d'Alene (I-90) to Hayden (Miles Ave)",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "US-95 northbound from I-90 through Coeur d'Alene and Hayden to Miles Ave. High-density retail and commercial corridor.",
            "corridor": "us95-cda-hayden",
            "direction": "NB",
        },
        {
            "id": "us95-cda-sb",
            "name": "US-95 SB: Hayden (Miles Ave) to Coeur d'Alene (I-90)",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "US-95 southbound from Hayden through Coeur d'Alene to the I-90 interchange.",
            "corridor": "us95-cda-hayden",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "us95-cda-hayden",
        "name": "US-95: Coeur d'Alene (I-90) to Hayden (Miles Ave)",
        "description": "The 5.8-mile primary urban commercial arterial spine of Kootenai County.",
    })

    # 2. I-90 Coeur d'Alene Freeway
    s_eb, e_eb, _, _ = find_chain_endpoints(net, "90", "E", [47.70059, -116.81303], [47.66275, -116.74720])
    s_wb, e_wb, _, _ = find_chain_endpoints(net, "90", "W", [47.66275, -116.74720], [47.70059, -116.81303])
    entries.extend([
        {
            "id": "i90-cda-eb",
            "name": "I-90 EB: Coeur d'Alene (IC 11 to IC 15)",
            "start_latlon": s_eb,
            "end_latlon": e_eb,
            "description": "Eastbound I-90 through urban Coeur d'Alene from Northwest Blvd to Sherman Ave.",
            "corridor": "i90-cda",
            "direction": "EB",
        },
        {
            "id": "i90-cda-wb",
            "name": "I-90 WB: Coeur d'Alene (IC 15 to IC 11)",
            "start_latlon": s_wb,
            "end_latlon": e_wb,
            "description": "Westbound I-90 through urban Coeur d'Alene from Sherman Ave to Northwest Blvd.",
            "corridor": "i90-cda",
            "direction": "WB",
        },
    ])
    groups.append({
        "id": "i90-cda",
        "name": "I-90: Coeur d'Alene Urban Freeway (IC 11 to IC 15)",
        "description": "The 4.2-mile urban interstate corridor through Coeur d'Alene.",
    })

    # 3. SH-41 Post Falls Arterial (Verified continuous chain)
    s_nb, e_nb = [47.71329, -116.89464], [47.73916, -116.89394]
    s_sb, e_sb = [47.74299, -116.89422], [47.71360, -116.89450]
    entries.extend([
        {
            "id": "sh41-postfalls-nb",
            "name": "SH-41 NB: Post Falls (I-90) to Prairie Ave",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "SH-41 northbound from I-90 through Post Falls commercial sector to Prairie Ave.",
            "corridor": "sh41-postfalls",
            "direction": "NB",
        },
        {
            "id": "sh41-postfalls-sb",
            "name": "SH-41 SB: Prairie Ave to Post Falls (I-90)",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "SH-41 southbound from Prairie Ave to I-90 in Post Falls.",
            "corridor": "sh41-postfalls",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "sh41-postfalls",
        "name": "SH-41: Post Falls (I-90) to Prairie Ave",
        "description": "The 3.6-mile suburban arterial connecting Post Falls to Rathdrum Prairie.",
    })

    # 4. Sandpoint Couplet (US-2 / US-95 downtown split)
    s_nb, e_nb, _, _ = find_chain_endpoints(net, "95", "N", [48.2710, -116.5490], [48.2830, -116.5460])
    s_sb, e_sb, _, _ = find_chain_endpoints(net, "95", "S", [48.2830, -116.5510], [48.2710, -116.5510])
    entries.extend([
        {
            "id": "sandpoint-couplet-nb",
            "name": "Sandpoint Couplet NB: 1st Ave (US-2/95)",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "Northbound leg of Sandpoint downtown couplet on 1st Ave.",
            "corridor": "sandpoint-couplet",
            "direction": "NB",
        },
        {
            "id": "sandpoint-couplet-sb",
            "name": "Sandpoint Couplet SB: 5th Ave (US-2/95)",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "Southbound leg of Sandpoint downtown couplet on 5th Ave.",
            "corridor": "sandpoint-couplet",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "sandpoint-couplet",
        "name": "Sandpoint Couplet: 1st Ave NB / 5th Ave SB (US-2/US-95)",
        "description": "Downtown Sandpoint one-way couplet carrying US-2 and US-95.",
        "one_way_couplet": True,
    })

    # 5. SH-3 St. Maries (from empirical candidate triage auto-3-nb/sb)
    s_nb, e_nb = [47.01786, -116.26040], [47.04018, -116.27975]
    s_sb, e_sb = [47.04018, -116.27975], [47.01786, -116.26040]
    entries.extend([
        {
            "id": "sh3-stmaries-nb",
            "name": "SH-3 NB: St. Maries River Road",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "SH-3 northbound near St. Maries along the St. Joe River corridor.",
            "corridor": "sh3-stmaries",
            "direction": "NB",
        },
        {
            "id": "sh3-stmaries-sb",
            "name": "SH-3 SB: St. Maries River Road",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "SH-3 southbound near St. Maries.",
            "corridor": "sh3-stmaries",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "sh3-stmaries",
        "name": "SH-3: St. Maries River Corridor",
        "description": "The 1.9-mile rural arterial section of SH-3 near St. Maries.",
    })

    # 6. US-95 Bonners Ferry (Rural Baseline Control)
    s_nb, e_nb, _, _ = find_chain_endpoints(net, "95", "N", [48.6850, -116.3150], [48.7450, -116.2950])
    s_sb, e_sb, _, _ = find_chain_endpoints(net, "95", "S", [48.7450, -116.2950], [48.6850, -116.3150])
    entries.extend([
        {
            "id": "us95-bonners-nb",
            "name": "US-95 NB: Bonners Ferry to Boundary North",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "US-95 northbound through Bonners Ferry across the Kootenai River.",
            "corridor": "us95-bonners-ferry",
            "direction": "NB",
        },
        {
            "id": "us95-bonners-sb",
            "name": "US-95 SB: Boundary North to Bonners Ferry",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "US-95 southbound toward Bonners Ferry.",
            "corridor": "us95-bonners-ferry",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "us95-bonners-ferry",
        "name": "US-95: Bonners Ferry Corridor",
        "description": "The 4.8-mile northern rural baseline facility on US-95.",
    })

    return {
        "_note": "ITD District 1 screening catalogue derived from recurring congestion triage and couplet synthesis.",
        "corridors": entries,
        "reporting_corridors": groups,
    }


def build_district_2_catalogue(net, repairs):
    """Build District 2 screening catalogue."""
    entries = []
    groups = []

    # 1. US-95 Moscow Arterial (from triage auto-95-nb)
    s_nb, e_nb = [46.72196, -117.00140], [46.74467, -117.00155]
    s_sb, e_sb = [46.74370, -117.00154], [46.72615, -117.00008]
    entries.extend([
        {
            "id": "us95-moscow-nb",
            "name": "US-95 NB: South Moscow (UI) to North Moscow (SH-8)",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "US-95 northbound through Moscow past University of Idaho to north commercial district.",
            "corridor": "us95-moscow",
            "direction": "NB",
        },
        {
            "id": "us95-moscow-sb",
            "name": "US-95 SB: North Moscow to South Moscow (Jackson St)",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "US-95 southbound through Moscow along Jackson St.",
            "corridor": "us95-moscow",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "us95-moscow",
        "name": "US-95: Moscow Urban Arterial",
        "description": "The 1.8-mile commercial and university arterial through Moscow.",
    })

    # 2. Moscow US-95 Couplet (Jackson St SB / Washington St NB)
    s_nb, e_nb, _, _ = find_chain_endpoints(net, "95", "N", [46.7280, -117.0005], [46.7380, -117.0005])
    s_sb, e_sb, _, _ = find_chain_endpoints(net, "95", "S", [46.7380, -117.0020], [46.7280, -117.0020])
    entries.extend([
        {
            "id": "moscow-couplet-nb",
            "name": "Moscow Couplet NB: Washington St",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "Northbound leg of Moscow couplet on Washington St.",
            "corridor": "moscow-couplet",
            "direction": "NB",
        },
        {
            "id": "moscow-couplet-sb",
            "name": "Moscow Couplet SB: Jackson St",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "Southbound leg of Moscow couplet on Jackson St.",
            "corridor": "moscow-couplet",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "moscow-couplet",
        "name": "Moscow Couplet: Washington St NB / Jackson St SB (US-95)",
        "description": "Downtown Moscow one-way arterial couplet.",
        "one_way_couplet": True,
    })

    # 3. US-12 Lewiston Urban Arterial (from triage auto-12-eb)
    s_eb, e_eb, _, _ = find_chain_endpoints(net, "12", "E", [46.4180, -117.0350], [46.4250, -116.9850])
    s_wb, e_wb, _, _ = find_chain_endpoints(net, "12", "W", [46.4250, -116.9850], [46.4180, -117.0350])
    entries.extend([
        {
            "id": "us12-lewiston-eb",
            "name": "US-12 EB: WA State Line to Clearwater River Bridge",
            "start_latlon": s_eb,
            "end_latlon": e_eb,
            "description": "US-12 eastbound through Lewiston along Snake River to Clearwater Memorial Bridge.",
            "corridor": "us12-lewiston",
            "direction": "EB",
        },
        {
            "id": "us12-lewiston-wb",
            "name": "US-12 WB: Clearwater River Bridge to WA State Line",
            "start_latlon": s_wb,
            "end_latlon": e_wb,
            "description": "US-12 westbound through Lewiston to WA State Line.",
            "corridor": "us12-lewiston",
            "direction": "WB",
        },
    ])
    groups.append({
        "id": "us12-lewiston",
        "name": "US-12: Lewiston Snake River Arterial",
        "description": "The 3.2-mile urban commercial and port corridor in Lewiston.",
    })

    # 4. Lewiston US-12 Couplet (Main St EB / D St WB)
    s_eb, e_eb, _, _ = find_chain_endpoints(net, "12", "E", [46.4190, -117.0280], [46.4210, -117.0150])
    s_wb, e_wb, _, _ = find_chain_endpoints(net, "12", "W", [46.4210, -117.0150], [46.4190, -117.0280])
    entries.extend([
        {
            "id": "lewiston-couplet-eb",
            "name": "Lewiston Couplet EB: Main St",
            "start_latlon": s_eb,
            "end_latlon": e_eb,
            "description": "Eastbound leg of Lewiston couplet on Main St.",
            "corridor": "lewiston-couplet",
            "direction": "EB",
        },
        {
            "id": "lewiston-couplet-wb",
            "name": "Lewiston Couplet WB: D St",
            "start_latlon": s_wb,
            "end_latlon": e_wb,
            "description": "Westbound leg of Lewiston couplet on D St.",
            "corridor": "lewiston-couplet",
            "direction": "WB",
        },
    ])
    groups.append({
        "id": "lewiston-couplet",
        "name": "Lewiston Couplet: Main St EB / D St WB (US-12)",
        "description": "Downtown Lewiston one-way couplet on Main St and D St.",
        "one_way_couplet": True,
    })

    # 5. SH-7 Gilbert Grade (from triage auto-7-sb with nudged entry point)
    s_sb, e_sb = [46.46904, -116.25044], [46.44533, -116.23140]
    s_nb, e_nb = [46.44533, -116.23140], [46.46904, -116.25044]
    entries.extend([
        {
            "id": "sh7-orofino-sb",
            "name": "SH-7 SB: Gilbert Grade south to Orofino",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "SH-7 southbound descending Gilbert Grade into Orofino.",
            "corridor": "sh7-orofino",
            "direction": "SB",
        },
        {
            "id": "sh7-orofino-nb",
            "name": "SH-7 NB: Orofino north up Gilbert Grade",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "SH-7 northbound ascending Gilbert Grade from Orofino.",
            "corridor": "sh7-orofino",
            "direction": "NB",
        },
    ])
    groups.append({
        "id": "sh7-orofino",
        "name": "SH-7: Gilbert Grade (Orofino)",
        "description": "The 3.2-mile steep grade and commuter approach on SH-7.",
    })

    # 6. US-95 Lewiston Hill (Rural Grade Control)
    s_nb, e_nb, _, _ = find_chain_endpoints(net, "95", "N", [46.4350, -116.9950], [46.5050, -117.0150])
    s_sb, e_sb, _, _ = find_chain_endpoints(net, "95", "S", [46.5050, -117.0150], [46.4350, -116.9950])
    entries.extend([
        {
            "id": "us95-lewhill-nb",
            "name": "US-95 NB: Lewiston Hill Grade",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "US-95 northbound ascending Lewiston Hill from Clearwater River junction.",
            "corridor": "us95-lewiston-hill",
            "direction": "NB",
        },
        {
            "id": "us95-lewhill-sb",
            "name": "US-95 SB: Lewiston Hill Descent",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "US-95 southbound descending Lewiston Hill into Lewiston valley.",
            "corridor": "us95-lewiston-hill",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "us95-lewiston-hill",
        "name": "US-95: Lewiston Hill Facility",
        "description": "The 5.5-mile grade section of US-95 serving as regional baseline.",
    })

    return {
        "_note": "ITD District 2 screening catalogue derived from candidate triage and couplet synthesis.",
        "corridors": entries,
        "reporting_corridors": groups,
    }


def build_district_4_catalogue(net, repairs):
    """Build District 4 screening catalogue."""
    entries = []
    groups = []

    # 1. US-93 Twin Falls (Blue Lakes Blvd)
    s_nb, e_nb = [42.56317, -114.46028], [42.59142, -114.46009]
    s_sb, e_sb = [42.59142, -114.46009], [42.56317, -114.46028]
    entries.extend([
        {
            "id": "us93-twinfalls-nb",
            "name": "US-93 NB: Twin Falls (Perrine Bridge to IC 173)",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "US-93 northbound on Blue Lakes Blvd across Perrine Bridge toward I-84.",
            "corridor": "us93-twinfalls",
            "direction": "NB",
        },
        {
            "id": "us93-twinfalls-sb",
            "name": "US-93 SB: I-84 (IC 173) to Twin Falls (Perrine Bridge)",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "US-93 southbound from I-84 across Perrine Bridge into commercial Twin Falls.",
            "corridor": "us93-twinfalls",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "us93-twinfalls",
        "name": "US-93: Twin Falls Blue Lakes Blvd (Perrine Bridge)",
        "description": "The 2.0-mile commercial bottleneck arterial connecting Twin Falls across the Snake River canyon.",
    })

    # 2. SH-75 Hailey Commuter Corridor (auto-75-nb/sb)
    s_nb, e_nb = [43.46875, -114.26284], [43.52863, -114.32215]
    s_sb, e_sb = [43.52863, -114.32215], [43.46875, -114.26284]
    entries.extend([
        {
            "id": "sh75-hailey-nb",
            "name": "SH-75 NB: Bellevue through Hailey",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "SH-75 northbound through Bellevue and Hailey in the Wood River Valley commuter corridor.",
            "corridor": "sh75-hailey",
            "direction": "NB",
        },
        {
            "id": "sh75-hailey-sb",
            "name": "SH-75 SB: Hailey through Bellevue",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "SH-75 southbound through Hailey and Bellevue.",
            "corridor": "sh75-hailey",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "sh75-hailey",
        "name": "SH-75: Wood River Valley (Bellevue to Hailey)",
        "description": "The 4.8-mile core commuter arterial section of SH-75 in southern Blaine County.",
    })

    # 3. SH-75 Ketchum Bottleneck Core (Tier 1 core hotspot, auto-75-nb-2, TTI 2.83)
    s_nb, e_nb = [43.63798, -114.35442], [43.66150, -114.35261]
    s_sb, e_sb = [43.66150, -114.35261], [43.63798, -114.35442]
    entries.extend([
        {
            "id": "sh75-ketchum-nb",
            "name": "SH-75 NB: Ketchum Main St Bottleneck",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "SH-75 northbound through downtown Ketchum along Main St to Sun Valley Rd.",
            "corridor": "sh75-ketchum",
            "direction": "NB",
        },
        {
            "id": "sh75-ketchum-sb",
            "name": "SH-75 SB: Ketchum Main St Bottleneck",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "SH-75 southbound through downtown Ketchum.",
            "corridor": "sh75-ketchum",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "sh75-ketchum",
        "name": "SH-75: Ketchum Urban Bottleneck",
        "description": "The 1.8-mile acute commercial bottleneck through downtown Ketchum.",
    })

    # 4. Twin Falls US-30 Couplet (2nd Ave N / 2nd Ave S)
    s_wb, e_wb, _, _ = find_chain_endpoints(net, "30", "W", [42.5640, -114.4600], [42.5600, -114.4750])
    s_eb, e_eb, _, _ = find_chain_endpoints(net, "30", "E", [42.5600, -114.4750], [42.5640, -114.4600])
    entries.extend([
        {
            "id": "twinfalls-couplet-wb",
            "name": "Twin Falls Couplet WB: 2nd Ave N",
            "start_latlon": s_wb,
            "end_latlon": e_wb,
            "description": "Westbound leg of Twin Falls couplet on 2nd Ave N.",
            "corridor": "twinfalls-couplet",
            "direction": "WB",
        },
        {
            "id": "twinfalls-couplet-eb",
            "name": "Twin Falls Couplet EB: 2nd Ave S",
            "start_latlon": s_eb,
            "end_latlon": e_eb,
            "description": "Eastbound leg of Twin Falls couplet on 2nd Ave S.",
            "corridor": "twinfalls-couplet",
            "direction": "EB",
        },
    ])
    groups.append({
        "id": "twinfalls-couplet",
        "name": "Twin Falls Couplet: 2nd Ave N WB / 2nd Ave S EB (US-30)",
        "description": "Downtown Twin Falls one-way arterial couplet.",
        "one_way_couplet": True,
    })

    # 5. I-84 Magic Valley Freeway
    s_eb, e_eb, _, _ = find_chain_endpoints(net, "84", "E", [42.7000, -114.5300], [42.6050, -114.4050])
    s_wb, e_wb, _, _ = find_chain_endpoints(net, "84", "W", [42.6050, -114.4050], [42.7000, -114.5300])
    entries.extend([
        {
            "id": "i84-twinfalls-eb",
            "name": "I-84 EB: Jerome (IC 168) to US-93 (IC 173)",
            "start_latlon": s_eb,
            "end_latlon": e_eb,
            "description": "Eastbound I-84 through Jerome County connecting to US-93 Twin Falls interchange.",
            "corridor": "i84-twinfalls",
            "direction": "EB",
        },
        {
            "id": "i84-twinfalls-wb",
            "name": "I-84 WB: US-93 (IC 173) to Jerome (IC 168)",
            "start_latlon": s_wb,
            "end_latlon": e_wb,
            "description": "Westbound I-84 from US-93 to Jerome interchange.",
            "corridor": "i84-twinfalls",
            "direction": "WB",
        },
    ])
    groups.append({
        "id": "i84-twinfalls",
        "name": "I-84: Magic Valley Freeway (IC 168 to IC 173)",
        "description": "The 6.0-mile freight and commuter freeway corridor in District 4.",
    })

    # 6. SH-75 Galena Summit Control
    s_nb, e_nb, _, _ = find_chain_endpoints(net, "75", "N", [43.7150, -114.4100], [43.8350, -114.6500])
    s_sb, e_sb, _, _ = find_chain_endpoints(net, "75", "S", [43.8350, -114.6500], [43.7150, -114.4100])
    entries.extend([
        {
            "id": "sh75-galena-nb",
            "name": "SH-75 NB: Ketchum North to Galena",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "SH-75 northbound climbing towards Galena Summit.",
            "corridor": "sh75-galena",
            "direction": "NB",
        },
        {
            "id": "sh75-galena-sb",
            "name": "SH-75 SB: Galena to Ketchum North",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "SH-75 southbound descending into the Ketchum basin.",
            "corridor": "sh75-galena",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "sh75-galena",
        "name": "SH-75: Galena Summit Recreation Baseline",
        "description": "The 10.5-mile rural mountain highway baseline facility.",
    })

    return {
        "_note": "ITD District 4 screening catalogue derived from candidate triage and couplet synthesis.",
        "corridors": entries,
        "reporting_corridors": groups,
    }


def build_district_5_catalogue(net, repairs):
    """Build District 5 screening catalogue."""
    entries = []
    groups = []

    # 1. US-91 Yellowstone Ave Pocatello - Chubbuck
    s_nb, e_nb, _, _ = find_chain_endpoints(net, "91", "N", [42.8750, -112.4550], [42.9250, -112.4550])
    s_sb, e_sb, _, _ = find_chain_endpoints(net, "91", "S", [42.9250, -112.4550], [42.8750, -112.4550])
    entries.extend([
        {
            "id": "us91-pocatello-nb",
            "name": "US-91 NB: Pocatello to Chubbuck (Yellowstone Ave)",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "US-91 northbound on Yellowstone Ave through Pocatello commercial district into Chubbuck.",
            "corridor": "us91-pocatello",
            "direction": "NB",
        },
        {
            "id": "us91-pocatello-sb",
            "name": "US-91 SB: Chubbuck to Pocatello (Yellowstone Ave)",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "US-91 southbound on Yellowstone Ave.",
            "corridor": "us91-pocatello",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "us91-pocatello",
        "name": "US-91: Yellowstone Ave (Pocatello to Chubbuck)",
        "description": "The 4.2-mile primary commercial arterial in the Pocatello-Chubbuck urban area.",
    })

    # 2. Pocatello Couplet (4th Ave NB / 5th Ave SB - US-91 / I-15B)
    s_nb, e_nb, _, _ = find_chain_endpoints(net, "91", "N", [42.8550, -112.4450], [42.8750, -112.4520])
    s_sb, e_sb, _, _ = find_chain_endpoints(net, "15", "S", [42.8750, -112.4500], [42.8550, -112.4430])
    entries.extend([
        {
            "id": "pocatello-couplet-nb",
            "name": "Pocatello Couplet NB: 4th Ave",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "Northbound leg of Pocatello couplet along 4th Ave.",
            "corridor": "pocatello-couplet",
            "direction": "NB",
        },
        {
            "id": "pocatello-couplet-sb",
            "name": "Pocatello Couplet SB: 5th Ave",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "Southbound leg of Pocatello couplet along 5th Ave.",
            "corridor": "pocatello-couplet",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "pocatello-couplet",
        "name": "Pocatello Couplet: 4th Ave NB / 5th Ave SB (US-91/I-15B)",
        "description": "Downtown Pocatello one-way urban couplet.",
        "one_way_couplet": True,
    })

    # 3. Blackfoot Couplet (W Bridge St WB / W Judicial St EB - I-15B)
    s_wb, e_wb, _, _ = find_chain_endpoints(net, "15", "W", [43.1920, -112.3450], [43.1890, -112.3600])
    s_eb, e_eb, _, _ = find_chain_endpoints(net, "15", "E", [43.1890, -112.3600], [43.1920, -112.3450])
    entries.extend([
        {
            "id": "blackfoot-couplet-wb",
            "name": "Blackfoot Couplet WB: W Bridge St",
            "start_latlon": s_wb,
            "end_latlon": e_wb,
            "description": "Westbound leg of Blackfoot couplet on W Bridge St.",
            "corridor": "blackfoot-couplet",
            "direction": "WB",
        },
        {
            "id": "blackfoot-couplet-eb",
            "name": "Blackfoot Couplet EB: W Judicial St",
            "start_latlon": s_eb,
            "end_latlon": e_eb,
            "description": "Eastbound leg of Blackfoot couplet on W Judicial St.",
            "corridor": "blackfoot-couplet",
            "direction": "EB",
        },
    ])
    groups.append({
        "id": "blackfoot-couplet",
        "name": "Blackfoot Couplet: W Bridge St WB / W Judicial St EB (I-15B)",
        "description": "Downtown Blackfoot one-way arterial couplet.",
        "one_way_couplet": True,
    })

    # 4. I-15 Pocatello Urban Freeway
    s_nb, e_nb, _, _ = find_chain_endpoints(net, "15", "N", [42.8350, -112.4150], [42.9150, -112.4350])
    s_sb, e_sb, _, _ = find_chain_endpoints(net, "15", "S", [42.9150, -112.4350], [42.8350, -112.4150])
    entries.extend([
        {
            "id": "i15-pocatello-nb",
            "name": "I-15 NB: South Pocatello (IC 67) to Chubbuck (IC 72)",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "Northbound I-15 through Pocatello to the I-86 Flying Y interchange.",
            "corridor": "i15-pocatello",
            "direction": "NB",
        },
        {
            "id": "i15-pocatello-sb",
            "name": "I-15 SB: Chubbuck (IC 72) to South Pocatello (IC 67)",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "Southbound I-15 through Pocatello.",
            "corridor": "i15-pocatello",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "i15-pocatello",
        "name": "I-15: Pocatello Urban Freeway (IC 67 to IC 72)",
        "description": "The 5.6-mile interstate corridor serving Pocatello and Chubbuck.",
    })

    # 5. US-30 McCammon to Lava Hot Springs (Freight Baseline)
    s_eb, e_eb, _, _ = find_chain_endpoints(net, "30", "E", [42.6450, -112.1850], [42.6150, -112.0150])
    s_wb, e_wb, _, _ = find_chain_endpoints(net, "30", "W", [42.6150, -112.0150], [42.6450, -112.1850])
    entries.extend([
        {
            "id": "us30-mccammon-eb",
            "name": "US-30 EB: McCammon to Lava Hot Springs",
            "start_latlon": s_eb,
            "end_latlon": e_eb,
            "description": "US-30 eastbound freight corridor from I-15 junction toward Lava Hot Springs.",
            "corridor": "us30-mccammon",
            "direction": "EB",
        },
        {
            "id": "us30-mccammon-wb",
            "name": "US-30 WB: Lava Hot Springs to McCammon",
            "start_latlon": s_wb,
            "end_latlon": e_wb,
            "description": "US-30 westbound freight corridor toward I-15.",
            "corridor": "us30-mccammon",
            "direction": "WB",
        },
    ])
    groups.append({
        "id": "us30-mccammon",
        "name": "US-30: McCammon to Lava Hot Springs",
        "description": "The 9.5-mile regional freight corridor in Bannock County.",
    })

    return {
        "_note": "ITD District 5 screening catalogue derived from candidate triage and couplet synthesis.",
        "corridors": entries,
        "reporting_corridors": groups,
    }


def build_district_6_catalogue(net, repairs):
    """Build District 6 screening catalogue."""
    entries = []
    groups = []

    # 1. US-20 Idaho Falls to Rexburg Commuter Expressway
    s_nb, e_nb, _, _ = find_chain_endpoints(net, "20", "N", [43.5150, -112.0500], [43.8150, -111.7850])
    s_sb, e_sb, _, _ = find_chain_endpoints(net, "20", "S", [43.8150, -111.7850], [43.5150, -112.0500])
    entries.extend([
        {
            "id": "us20-if-rexburg-nb",
            "name": "US-20 NB: Idaho Falls (I-15) to Rexburg (Univ Blvd)",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "US-20 northbound commuter expressway from Idaho Falls past Rigby into Rexburg.",
            "corridor": "us20-if-rexburg",
            "direction": "NB",
        },
        {
            "id": "us20-if-rexburg-sb",
            "name": "US-20 SB: Rexburg (Univ Blvd) to Idaho Falls (I-15)",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "US-20 southbound commuter expressway from Rexburg to Idaho Falls.",
            "corridor": "us20-if-rexburg",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "us20-if-rexburg",
        "name": "US-20: Idaho Falls to Rexburg Commuter Expressway",
        "description": "The 25.5-mile high-volume divided commuter facility connecting Idaho Falls, Rigby, and Rexburg.",
    })

    # 2. US-20 Idaho Falls Urban
    s_nb, e_nb, _, _ = find_chain_endpoints(net, "20", "N", [43.4850, -112.0650], [43.5250, -112.0450])
    s_sb, e_sb, _, _ = find_chain_endpoints(net, "20", "S", [43.5250, -112.0450], [43.4850, -112.0650])
    entries.extend([
        {
            "id": "us20-if-urban-nb",
            "name": "US-20 NB: Idaho Falls Urban Spine",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "US-20 northbound urban bypass in Idaho Falls connecting to I-15.",
            "corridor": "us20-if-urban",
            "direction": "NB",
        },
        {
            "id": "us20-if-urban-sb",
            "name": "US-20 SB: Idaho Falls Urban Spine",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "US-20 southbound urban bypass in Idaho Falls.",
            "corridor": "us20-if-urban",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "us20-if-urban",
        "name": "US-20: Idaho Falls Urban Bypass",
        "description": "The 3.8-mile urban connector facility in Idaho Falls.",
    })

    # 3. I-15 Idaho Falls Urban Freeway
    s_nb, e_nb, _, _ = find_chain_endpoints(net, "15", "N", [43.4650, -112.0750], [43.5350, -112.0650])
    s_sb, e_sb, _, _ = find_chain_endpoints(net, "15", "S", [43.5350, -112.0650], [43.4650, -112.0750])
    entries.extend([
        {
            "id": "i15-if-nb",
            "name": "I-15 NB: Idaho Falls (Sunnyside to US-20)",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "Northbound I-15 through Idaho Falls from Sunnyside Rd (IC 116) to US-20 (IC 119).",
            "corridor": "i15-idaho-falls",
            "direction": "NB",
        },
        {
            "id": "i15-if-sb",
            "name": "I-15 SB: Idaho Falls (US-20 to Sunnyside)",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "Southbound I-15 through Idaho Falls.",
            "corridor": "i15-idaho-falls",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "i15-idaho-falls",
        "name": "I-15: Idaho Falls Urban Freeway (IC 116 to IC 119)",
        "description": "The 4.5-mile interstate mainline serving Idaho Falls.",
    })

    # 4. SH-33 Rexburg Commercial Arterial (auto-33 verified chain)
    s_eb, e_eb = [43.82607, -111.80200], [43.82606, -111.77805]
    s_wb, e_wb = [43.82608, -111.78957], [43.82455, -111.84880]
    entries.extend([
        {
            "id": "sh33-rexburg-eb",
            "name": "SH-33 EB: Main St Rexburg",
            "start_latlon": s_eb,
            "end_latlon": e_eb,
            "description": "Eastbound SH-33 on Main St through central Rexburg.",
            "corridor": "sh33-rexburg",
            "direction": "EB",
        },
        {
            "id": "sh33-rexburg-wb",
            "name": "SH-33 WB: Main St Rexburg",
            "start_latlon": s_wb,
            "end_latlon": e_wb,
            "description": "Westbound SH-33 on Main St through central Rexburg.",
            "corridor": "sh33-rexburg",
            "direction": "WB",
        },
    ])
    groups.append({
        "id": "sh33-rexburg",
        "name": "SH-33: Rexburg Main St Arterial",
        "description": "The 1.5-mile commercial arterial serving BYU-Idaho and Rexburg.",
    })

    # 5. SH-33 Teton Valley Recreation Corridor
    s_nb, e_nb = [43.59432, -111.10758], [43.72500, -111.11000]
    s_sb, e_sb = [43.72500, -111.11000], [43.59432, -111.10758]
    entries.extend([
        {
            "id": "sh33-teton-nb",
            "name": "SH-33 NB: Victor to Driggs",
            "start_latlon": s_nb,
            "end_latlon": e_nb,
            "description": "SH-33 northbound in Teton Valley from Victor to Driggs.",
            "corridor": "sh33-teton-valley",
            "direction": "NB",
        },
        {
            "id": "sh33-teton-sb",
            "name": "SH-33 SB: Driggs to Victor",
            "start_latlon": s_sb,
            "end_latlon": e_sb,
            "description": "SH-33 southbound in Teton Valley from Driggs to Victor.",
            "corridor": "sh33-teton-valley",
            "direction": "SB",
        },
    ])
    groups.append({
        "id": "sh33-teton-valley",
        "name": "SH-33: Teton Valley (Victor to Driggs)",
        "description": "The 8.5-mile recreation and commuter arterial in Teton County.",
    })

    return {
        "_note": "ITD District 6 screening catalogue derived from candidate triage and couplet synthesis.",
        "corridors": entries,
        "reporting_corridors": groups,
    }


def verify_catalogue(cat, net, repairs, district_num):
    """Resolve all entries and verify 100% resolution."""
    print(f"\n--- Verifying District {district_num} Catalogue ---")
    res = corridors.resolve_catalogue(net, cat["corridors"], repairs=repairs)
    findings = res.attrs.get("findings", [])
    accepted = res[res["accepted"]]
    total = len(res)

    print(f"Total entries: {total} | Accepted: {len(accepted)} | Findings: {len(findings)}")
    for _, r in res.iterrows():
        status = "OK" if r["reached_target"] else f"FAIL ({r['stop_reason']})"
        print(f"  [{status:^10}] {r['id']:<24} {r['chain_miles']:>6.2f} mi ({r['n_segments']:>2} segs, {r['n_repaired_links']} rep)")

    if findings:
        print(f"WARNING: District {district_num} has {len(findings)} findings: {findings}")
        return False
    return True


def main():
    catalogues = {
        1: build_district_1_catalogue,
        2: build_district_2_catalogue,
        4: build_district_4_catalogue,
        5: build_district_5_catalogue,
        6: build_district_6_catalogue,
    }

    all_passed = True
    for d, builder in catalogues.items():
        net_path = Path(f"geometry_cache/d{d}_network.geoparquet")
        rep_path = Path(f"scripts/d{d}_link_repairs.csv")
        out_path = Path(f"scripts/d{d}_corridors.json")

        net = gpd.read_parquet(net_path)
        repairs = corridors.load_link_repairs(rep_path)

        cat = builder(net, repairs)
        passed = verify_catalogue(cat, net, repairs, d)
        if not passed:
            all_passed = False

        with out_path.open("w") as fh:
            json.dump(cat, fh, indent=2)
        print(f"Saved: {out_path} ({len(cat['corridors'])} directional entries, {len(cat['reporting_corridors'])} reporting groups)")

    print(f"\nStatewide catalogue generation: {'ALL PASSED' if all_passed else 'SOME FINDINGS'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
