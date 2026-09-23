#!/usr/bin/env python3
"""Generate statewide and per-district highway inventory txt files and catalogues.

Creates RITIS/INRIX-paste-ready text files for all six ITD districts:
- Master text files per district (District_1_ALL_Highways.txt ... District_6_ALL_Highways.txt)
- Statewide master text file (Statewide_ALL_Highways.txt)
- Per-highway directional files (_ALL.txt, _EB.txt/_WB.txt or _NB.txt/_SB.txt)
- Detailed summary JSON catalogues and CSV tables.

Matches ITD's official administrative districts (all 44 counties mapped 1-to-1)
and the official Idaho State Highway System as confirmed by ITD's AADT GIS layer.
"""
from __future__ import annotations

import json
from pathlib import Path
import pyogrio
import pandas as pd

# ITD Administrative Districts by County
DISTRICT_COUNTIES = {
    1: ["Benewah", "Bonner", "Boundary", "Kootenai", "Shoshone"],
    2: ["Clearwater", "Idaho", "Latah", "Lewis", "Nez Perce"],
    3: ["Ada", "Adams", "Boise", "Canyon", "Elmore", "Gem", "Owyhee", "Payette", "Valley", "Washington"],
    4: ["Blaine", "Camas", "Cassia", "Gooding", "Jerome", "Lincoln", "Minidoka", "Twin Falls"],
    5: ["Bannock", "Bear Lake", "Bingham", "Caribou", "Franklin", "Oneida", "Power"],
    6: ["Bonneville", "Butte", "Clark", "Custer", "Fremont", "Jefferson", "Lemhi", "Madison", "Teton"],
}

# Highway definitions by District
# type: 'EW' -> EB/WB; 'NS' -> NB/SB
HIGHWAY_DEFS = {
    1: {
        "I-90": {
            "name": "Interstate 90 (Mainline & Business Loops)",
            "type": "EW",
            "rn": ["90"],
        },
        "US-2": {
            "name": "US-2 (Washington State Line to Montana State Line)",
            "type": "EW",
            "rn": ["2"],
        },
        "US-95": {
            "name": "US-95 (Benewah County to Canada Border)",
            "type": "NS",
            "rn": ["95"],
        },
        "SH-1": {
            "name": "SH-1 (Bonners Ferry to Canada Border)",
            "type": "NS",
            "rn": ["1"],
        },
        "SH-3": {
            "name": "SH-3 (St. Maries to I-90 / Rose Lake)",
            "type": "NS",
            "rn": ["3"],
        },
        "SH-4": {
            "name": "SH-4 (Wallace to Burke Canyon)",
            "type": "EW",
            "rn": ["4"],
        },
        "SH-5": {
            "name": "SH-5 (Plummer to St. Maries)",
            "type": "EW",
            "rn": ["5"],
        },
        "SH-6": {
            "name": "SH-6 (Emida to Benewah/Latah Line)",
            "type": "EW",
            "rn": ["6"],
        },
        "SH-41": {
            "name": "SH-41 (Post Falls to Oldtown)",
            "type": "NS",
            "rn": ["41"],
        },
        "SH-53": {
            "name": "SH-53 (WA State Line to Garwood / US-95)",
            "type": "EW",
            "rn": ["53"],
        },
        "SH-54": {
            "name": "SH-54 (Spirit Lake to Athol / Farragut)",
            "type": "EW",
            "rn": ["54"],
            "extra_names": ["ID-54"],
        },
        "SH-57": {
            "name": "SH-57 (Priest River to Nordman)",
            "type": "NS",
            "rn": ["57"],
        },
        "SH-58": {
            "name": "SH-58 (WA State Line to US-95)",
            "type": "EW",
            "rn": ["58"],
        },
        "SH-60": {
            "name": "SH-60 (WA State Line to US-95 / Plummer)",
            "type": "EW",
            "rn": ["60"],
        },
        "SH-97": {
            "name": "SH-97 (Coeur d'Alene Lake East Shore)",
            "type": "NS",
            "rn": ["97"],
        },
        "SH-200": {
            "name": "SH-200 (Sandpoint to MT State Line / Clark Fork)",
            "type": "EW",
            "rn": ["200"],
        },
        "Freeway_Ramps": {
            "name": "Freeway Ramps (All I-90 & US-95 Interchanges)",
            "type": "EW",
            "is_ramp": True,
        },
    },
    2: {
        "US-12": {
            "name": "US-12 (Lewiston to Lolo Pass / MT State Line)",
            "type": "EW",
            "rn": ["12"],
        },
        "US-95": {
            "name": "US-95 (Riggins to Moscow to Potlatch)",
            "type": "NS",
            "rn": ["95"],
        },
        "SH-3": {
            "name": "SH-3 (Spalding/Kendrick to Bovill to Benewah Line)",
            "type": "NS",
            "rn": ["3"],
        },
        "SH-6": {
            "name": "SH-6 (Potlatch to Harvard to Benewah Line)",
            "type": "EW",
            "rn": ["6"],
        },
        "SH-7": {
            "name": "SH-7 (Orofino to Cavendish)",
            "type": "EW",
            "rn": ["7"],
        },
        "SH-8": {
            "name": "SH-8 (Moscow to Troy to Deary to Bovill to Elk River)",
            "type": "EW",
            "rn": ["8"],
        },
        "SH-9": {
            "name": "SH-9 (Harvard to Deary)",
            "type": "NS",
            "rn": ["9"],
        },
        "SH-11": {
            "name": "SH-11 (Greer to Weippe to Pierce to Headquarters)",
            "type": "EW",
            "rn": ["11"],
        },
        "SH-13": {
            "name": "SH-13 (Grangeville to Kooskia)",
            "type": "NS",
            "rn": ["13"],
        },
        "SH-62": {
            "name": "SH-62 (Nezperce to Craigmont / Kamiah)",
            "type": "EW",
            "rn": ["62"],
        },
        "SH-64": {
            "name": "SH-64 (Nezperce to Kamiah)",
            "type": "EW",
            "rn": ["64"],
        },
        "SH-66": {
            "name": "SH-66 (Viola to Harvard)",
            "type": "NS",
            "rn": ["66"],
        },
        "SH-99": {
            "name": "SH-99 (Kendrick to Troy)",
            "type": "NS",
            "rn": ["99"],
        },
        "SH-128": {
            "name": "SH-128 (Lewiston Down River Rd to WA State Line)",
            "type": "EW",
            "rn": ["128"],
        },
        "SH-162": {
            "name": "SH-162 (Kamiah to Winona to Nezperce)",
            "type": "EW",
            "rn": ["162"],
        },
        "Freeway_Ramps": {
            "name": "Highway Ramps (US-12 & US-95 Lewiston Interchanges)",
            "type": "EW",
            "is_ramp": True,
        },
    },
    4: {
        "I-84": {
            "name": "Interstate 84 (Mainline & Business Loops)",
            "type": "EW",
            "rn": ["84"],
        },
        "I-86": {
            "name": "Interstate 86 (I-84 Jct to Power County Line)",
            "type": "EW",
            "rn": ["86"],
        },
        "US-20": {
            "name": "US-20 (Elmore County Line to Carey)",
            "type": "EW",
            "rn": ["20"],
        },
        "US-26": {
            "name": "US-26 (Bliss to Gooding to Shoshone to Richfield to Arco)",
            "type": "EW",
            "rn": ["26"],
        },
        "US-30": {
            "name": "US-30 (Bliss to Buhl to Twin Falls to Burley)",
            "type": "EW",
            "rn": ["30"],
        },
        "US-93": {
            "name": "US-93 (Nevada State Line to Twin Falls to Shoshone to Carey)",
            "type": "NS",
            "rn": ["93"],
        },
        "SH-24": {
            "name": "SH-24 (Shoshone to Dietrich to Minidoka to Rupert)",
            "type": "EW",
            "rn": ["24"],
        },
        "SH-25": {
            "name": "SH-25 (Jerome to Hazelton to Paul to Rupert)",
            "type": "EW",
            "rn": ["25"],
        },
        "SH-27": {
            "name": "SH-27 (Oakley to Burley to Paul)",
            "type": "NS",
            "rn": ["27"],
        },
        "SH-46": {
            "name": "SH-46 (Gooding to Camas County Line / Fairfield)",
            "type": "NS",
            "rn": ["46"],
        },
        "SH-50": {
            "name": "SH-50 (Twin Falls / Hansen Bridge to I-84)",
            "type": "EW",
            "rn": ["50"],
        },
        "SH-74": {
            "name": "SH-74 (Twin Falls Southern Bypass)",
            "type": "EW",
            "rn": ["74"],
        },
        "SH-75": {
            "name": "SH-75 (Shoshone to Bellevue to Hailey to Ketchum to Galena Summit)",
            "type": "NS",
            "rn": ["75"],
        },
        "SH-77": {
            "name": "SH-77 (Albion to Malta to Raft River)",
            "type": "NS",
            "rn": ["77"],
        },
        "SH-79": {
            "name": "SH-79 (Jerome to I-84)",
            "type": "NS",
            "rn": ["79"],
        },
        "SH-81": {
            "name": "SH-81 (Burley to Declo to Malta)",
            "type": "EW",
            "rn": ["81"],
        },
        "Freeway_Ramps": {
            "name": "Freeway Ramps (All I-84, I-86 & US-93 Interchanges)",
            "type": "EW",
            "is_ramp": True,
        },
    },
    5: {
        "I-15": {
            "name": "Interstate 15 (Utah State Line to Pocatello to Blackfoot)",
            "type": "NS",
            "rn": ["15"],
        },
        "I-84": {
            "name": "Interstate 84 (Oneida County Section)",
            "type": "EW",
            "rn": ["84"],
        },
        "I-86": {
            "name": "Interstate 86 (American Falls to Pocatello)",
            "type": "EW",
            "rn": ["86"],
        },
        "US-20": {
            "name": "US-20 (Atomic City / Bingham County Section)",
            "type": "EW",
            "rn": ["20"],
        },
        "US-26": {
            "name": "US-26 (Blackfoot to INL)",
            "type": "EW",
            "rn": ["26"],
        },
        "US-30": {
            "name": "US-30 (Pocatello to Lava Hot Springs to Montpelier to WY Line)",
            "type": "EW",
            "rn": ["30"],
        },
        "US-89": {
            "name": "US-89 (Bear Lake to Montpelier to WY State Line)",
            "type": "NS",
            "rn": ["89"],
        },
        "US-91": {
            "name": "US-91 (Utah State Line to Preston to Downey to Pocatello)",
            "type": "NS",
            "rn": ["91"],
        },
        "SH-34": {
            "name": "SH-34 (Preston to Grace to Soda Springs to Freedom)",
            "type": "EW",
            "rn": ["34"],
        },
        "SH-36": {
            "name": "SH-36 (Malad City to Weston to Preston to Ovid)",
            "type": "EW",
            "rn": ["36"],
            "extra_names": ["ID-36"],
        },
        "SH-37": {
            "name": "SH-37 (Malad City to Rockland to American Falls)",
            "type": "NS",
            "rn": ["37"],
        },
        "SH-38": {
            "name": "SH-38 (Malad City to Holbrook / Stone)",
            "type": "EW",
            "rn": ["38"],
        },
        "SH-39": {
            "name": "SH-39 (American Falls to Aberdeen to Blackfoot)",
            "type": "NS",
            "rn": ["39"],
        },
        "SH-40": {
            "name": "SH-40 (I-15 to Downey)",
            "type": "EW",
            "rn": ["40"],
        },
        "SH-61": {
            "name": "SH-61 (Wayan to WY State Line)",
            "type": "NS",
            "rn": ["61"],
        },
        "Freeway_Ramps": {
            "name": "Freeway Ramps (All I-15, I-84 & I-86 Interchanges)",
            "type": "NS",
            "is_ramp": True,
        },
    },
    6: {
        "I-15": {
            "name": "Interstate 15 (Idaho Falls to Roberts to Dubois to MT Line)",
            "type": "NS",
            "rn": ["15"],
        },
        "US-20": {
            "name": "US-20 (Arco to Idaho Falls to Rexburg to Island Park to MT Line)",
            "type": "EW",
            "rn": ["20"],
        },
        "US-26": {
            "name": "US-26 (Arco to Blackfoot Line / Idaho Falls to Swan Valley to Alpine)",
            "type": "EW",
            "rn": ["26"],
        },
        "US-91": {
            "name": "US-91 (Idaho Falls to Firth)",
            "type": "NS",
            "rn": ["91"],
        },
        "US-93": {
            "name": "US-93 (Arco to Mackay to Challis to Salmon to MT Line)",
            "type": "NS",
            "rn": ["93"],
        },
        "SH-21": {
            "name": "SH-21 (Stanley to Lowman / Custer County Section)",
            "type": "EW",
            "rn": ["21"],
        },
        "SH-22": {
            "name": "SH-22 (Dubois to Howe)",
            "type": "EW",
            "rn": ["22"],
        },
        "SH-28": {
            "name": "SH-28 (Salmon to Leadore to Mud Lake / Sage Junction)",
            "type": "NS",
            "rn": ["28"],
        },
        "SH-29": {
            "name": "SH-29 (Leadore to MT State Line)",
            "type": "NS",
            "rn": ["29"],
        },
        "SH-31": {
            "name": "SH-31 (Swan Valley to Victor)",
            "type": "EW",
            "rn": ["31"],
        },
        "SH-32": {
            "name": "SH-32 (Ashton to Tetonia)",
            "type": "NS",
            "rn": ["32"],
        },
        "SH-33": {
            "name": "SH-33 (Howe to Mud Lake to Rexburg to Driggs to Victor)",
            "type": "EW",
            "rn": ["33"],
        },
        "SH-43": {
            "name": "SH-43 (Beachs Corner to Ucon Connector)",
            "type": "NS",
            "rn": ["43"],
        },
        "SH-47": {
            "name": "SH-47 (Ashton to Warm River / Mesa Falls)",
            "type": "EW",
            "rn": ["47"],
        },
        "SH-48": {
            "name": "SH-48 (Roberts to Menan to Lewisville to Rigby to Ririe)",
            "type": "EW",
            "rn": ["48"],
        },
        "SH-75": {
            "name": "SH-75 (Galena Summit to Stanley to Clayton to Challis)",
            "type": "NS",
            "rn": ["75"],
        },
        "SH-87": {
            "name": "SH-87 (US-20 Island Park to MT State Line)",
            "type": "NS",
            "rn": ["87"],
        },
        "Freeway_Ramps": {
            "name": "Freeway Ramps (All I-15 & US-20 Expressway Interchanges)",
            "type": "NS",
            "is_ramp": True,
        },
    },
}


def split_directional(df_hw: pd.DataFrame, direction_type: str) -> tuple[pd.DataFrame, pd.DataFrame, str, str]:
    """Split a highway dataframe into two directional dataframes."""
    if direction_type == "EW":
        d1_name, d2_name = "EB", "WB"
        d1_df = df_hw[df_hw["Bearing"].isin(["E", "N"])].copy()
        d2_df = df_hw[df_hw["Bearing"].isin(["W", "S"])].copy()
    else:
        d1_name, d2_name = "NB", "SB"
        d1_df = df_hw[df_hw["Bearing"].isin(["N", "E"])].copy()
        d2_df = df_hw[df_hw["Bearing"].isin(["S", "W"])].copy()
    return d1_df, d2_df, d1_name, d2_name


def write_id_file(path: Path, ids: list[int]) -> None:
    """Write segment IDs as comma-separated text on a single line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(str(i) for i in ids))


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    shp_path = base_dir / "USA_Idaho_shapefile.zip!USA_Idaho.shp"
    out_highways = base_dir / "out" / "highways"
    out_highways.mkdir(parents=True, exist_ok=True)

    print("Loading USA_Idaho shapefile...")
    df = pyogrio.read_dataframe(f"zip://{shp_path}", read_geometry=False)
    df["Miles_num"] = df["Miles"].astype(float)
    df["XDSegID_int"] = df["XDSegID"].astype(int)

    county_to_dist = {}
    for d, counties in DISTRICT_COUNTIES.items():
        for c in counties:
            county_to_dist[c] = d
    df["District"] = df["County"].map(county_to_dist)

    all_statewide_ids = set()
    statewide_rows = []

    # Process District 3 first using existing master files to maintain exact consistency
    d3_dir = out_highways / "district_3"
    d3_dir.mkdir(parents=True, exist_ok=True)
    with open(out_highways / "District_3_ALL_Highways.txt") as f:
        d3_ids = [int(x) for x in f.read().strip().split(",") if x.strip()]
    all_statewide_ids.update(d3_ids)

    # Copy D3 master and existing files to district_3 folder as well
    write_id_file(d3_dir / "District_3_ALL_Highways.txt", d3_ids)
    if (out_highways / "district_3_highways_summary.csv").exists():
        (d3_dir / "district_3_highways_summary.csv").write_text((out_highways / "district_3_highways_summary.csv").read_text())
    if (out_highways / "district_3_highways.json").exists():
        (d3_dir / "district_3_highways.json").write_text((out_highways / "district_3_highways.json").read_text())
    for f_txt in out_highways.glob("*.txt"):
        if not f_txt.name.startswith("District_") and not f_txt.name.startswith("Statewide_"):
            (d3_dir / f_txt.name).write_text(f_txt.read_text())

    # Read D3 master JSON if exists to populate statewide summary
    d3_json_path = out_highways / "district_3_master_highways.json"
    if d3_json_path.exists():
        with open(d3_json_path) as f:
            d3_json = json.load(f)
        for comp_name, comp_data in d3_json.get("components", {}).items():
            if comp_name == "SH-55_Full":
                continue
            t_segs = comp_data.get("total_segments", 0)
            t_miles = comp_data.get("total_miles", 0.0)
            eb_segs = comp_data.get("EB", {}).get("count")
            eb_miles = comp_data.get("EB", {}).get("miles")
            wb_segs = comp_data.get("WB", {}).get("count")
            wb_miles = comp_data.get("WB", {}).get("miles")
            nb_segs = comp_data.get("NB", {}).get("count")
            nb_miles = comp_data.get("NB", {}).get("miles")
            sb_segs = comp_data.get("SB", {}).get("count")
            sb_miles = comp_data.get("SB", {}).get("miles")
            statewide_rows.append({
                "District": 3,
                "Highway": comp_name,
                "Description": comp_data.get("description", comp_name),
                "Total Segs": t_segs,
                "Total Miles": round(t_miles, 2),
                "EB Segs": eb_segs,
                "EB Miles": round(eb_miles, 2) if eb_miles is not None else None,
                "WB Segs": wb_segs,
                "WB Miles": round(wb_miles, 2) if wb_miles is not None else None,
                "NB Segs": nb_segs,
                "NB Miles": round(nb_miles, 2) if nb_miles is not None else None,
                "SB Segs": sb_segs,
                "SB Miles": round(sb_miles, 2) if sb_miles is not None else None,
            })

    # Process Districts 1, 2, 4, 5, 6
    for d in [1, 2, 4, 5, 6]:
        print(f"\nProcessing District {d}...")
        dist_df = df[df["District"] == d].copy()
        hws = HIGHWAY_DEFS[d]
        dist_dir = out_highways / f"district_{d}"
        dist_dir.mkdir(parents=True, exist_ok=True)

        dist_master_ids = set()
        dist_json = {}
        dist_summary_rows = []

        all_hw_rn = [rn for meta in hws.values() for rn in meta.get("rn", [])]

        for hw_code, meta in hws.items():
            if meta.get("is_ramp"):
                # Ramps
                cond = (dist_df["SlipRoad"] == "1") & (~dist_df["RoadNumber"].astype(str).isin(all_hw_rn))
                hw_df = dist_df[cond].copy()
            else:
                cond = dist_df["RoadNumber"].astype(str).isin(meta["rn"])
                if "extra_names" in meta:
                    cond = cond | (dist_df["RoadName"].isin(meta["extra_names"]) & dist_df["RoadNumber"].isna())
                hw_df = dist_df[cond].copy()

            hw_ids = hw_df["XDSegID_int"].tolist()
            dist_master_ids.update(hw_ids)

            # Split directionally
            d1_df, d2_df, d1_name, d2_name = split_directional(hw_df, meta["type"])

            d1_ids = d1_df["XDSegID_int"].tolist()
            d2_ids = d2_df["XDSegID_int"].tolist()

            total_segs = len(hw_df)
            total_miles = hw_df["Miles_num"].sum()
            d1_segs = len(d1_df)
            d1_miles = d1_df["Miles_num"].sum()
            d2_segs = len(d2_df)
            d2_miles = d2_df["Miles_num"].sum()

            # Write individual highway files in district folder
            write_id_file(dist_dir / f"{hw_code}_ALL.txt", hw_ids)
            write_id_file(dist_dir / f"{hw_code}_{d1_name}.txt", d1_ids)
            write_id_file(dist_dir / f"{hw_code}_{d2_name}.txt", d2_ids)

            # JSON catalog entry
            hw_entry = {
                "name": meta["name"],
                "type": meta["type"],
                "total_segments": total_segs,
                "total_miles": round(total_miles, 2),
                d1_name: {
                    "count": d1_segs,
                    "miles": round(d1_miles, 2),
                    "segment_ids": d1_ids,
                },
                d2_name: {
                    "count": d2_segs,
                    "miles": round(d2_miles, 2),
                    "segment_ids": d2_ids,
                },
            }
            dist_json[hw_code] = hw_entry

            # Summary row
            row = {
                "Highway": hw_code,
                "Description": meta["name"],
                "Total Segs": total_segs,
                "Total Miles": round(total_miles, 2),
                "EB Segs": d1_segs if d1_name == "EB" else None,
                "EB Miles": round(d1_miles, 2) if d1_name == "EB" else None,
                "WB Segs": d2_segs if d2_name == "WB" else None,
                "WB Miles": round(d2_miles, 2) if d2_name == "WB" else None,
                "NB Segs": d1_segs if d1_name == "NB" else None,
                "NB Miles": round(d1_miles, 2) if d1_name == "NB" else None,
                "SB Segs": d2_segs if d2_name == "SB" else None,
                "SB Miles": round(d2_miles, 2) if d2_name == "SB" else None,
            }
            dist_summary_rows.append(row)

            state_row = dict(row)
            state_row["District"] = d
            statewide_rows.append(state_row)

        sorted_dist_ids = sorted(dist_master_ids)
        all_statewide_ids.update(sorted_dist_ids)

        # Write District Master txt in both top-level out/highways/ and district folder
        write_id_file(out_highways / f"District_{d}_ALL_Highways.txt", sorted_dist_ids)
        write_id_file(dist_dir / f"District_{d}_ALL_Highways.txt", sorted_dist_ids)

        # Special timezone split for District 2 (Goff Bridge / Salmon River: lat 45.446976)
        if d == 2:
            us95_df = dist_df[dist_df["RoadNumber"] == "95"].copy()
            us95_df["mid_lat"] = (us95_df["StartLat"].astype(float) + us95_df["EndLat"].astype(float)) / 2.0
            
            d2_mt_df = us95_df[us95_df["mid_lat"] < 45.446976]
            d2_mt_ids = sorted(d2_mt_df["XDSegID_int"].tolist())
            d2_pt_ids = sorted(list(dist_master_ids - set(d2_mt_ids)))

            # Write District 2 Timezone Split master files
            write_id_file(out_highways / "District_2_Pacific_ALL_Highways.txt", d2_pt_ids)
            write_id_file(dist_dir / "District_2_Pacific_ALL_Highways.txt", d2_pt_ids)
            write_id_file(out_highways / "District_2_Mountain_ALL_Highways.txt", d2_mt_ids)
            write_id_file(dist_dir / "District_2_Mountain_ALL_Highways.txt", d2_mt_ids)

            # Write US-95 split directional files
            us95_pt_df = us95_df[us95_df["mid_lat"] >= 45.446976]
            for split_name, s_df in [("Pacific", us95_pt_df), ("Mountain", d2_mt_df)]:
                s_nb, s_sb, _, _ = split_directional(s_df, "NS")
                write_id_file(dist_dir / f"US-95_{split_name}_ALL.txt", s_df["XDSegID_int"].tolist())
                write_id_file(dist_dir / f"US-95_{split_name}_NB.txt", s_nb["XDSegID_int"].tolist())
                write_id_file(dist_dir / f"US-95_{split_name}_SB.txt", s_sb["XDSegID_int"].tolist())

            print(f"    -> D2 Timezone Split: Pacific={len(d2_pt_ids)} segs, Mountain={len(d2_mt_ids)} segs (Goff Bridge / Riggins)")

        # Write District JSON and CSV
        with open(dist_dir / f"district_{d}_highways.json", "w") as f:
            json.dump(dist_json, f, indent=2)
        with open(out_highways / f"district_{d}_highways.json", "w") as f:
            json.dump(dist_json, f, indent=2)

        summary_df = pd.DataFrame(dist_summary_rows)
        summary_df.to_csv(dist_dir / f"district_{d}_highways_summary.csv", index=False)
        summary_df.to_csv(out_highways / f"district_{d}_highways_summary.csv", index=False)

        print(f"  District {d}: {len(hws)} corridors, {len(sorted_dist_ids)} total segments.")

    # Write Statewide Master
    sorted_statewide = sorted(all_statewide_ids)
    write_id_file(out_highways / "Statewide_ALL_Highways.txt", sorted_statewide)

    state_summary_df = pd.DataFrame(statewide_rows)[
        ["District", "Highway", "Description", "Total Segs", "Total Miles",
         "EB Segs", "EB Miles", "WB Segs", "WB Miles", "NB Segs", "NB Miles", "SB Segs", "SB Miles"]
    ]
    state_summary_df.to_csv(out_highways / "statewide_highways_summary.csv", index=False)

    print("\n=======================================================")
    print("STATEWIDE HIGHWAY NETWORK SUMMARY")
    print("=======================================================")
    print(f"Total Unique Statewide Segments: {len(sorted_statewide)}")
    for d in range(1, 7):
        d_segs = state_summary_df[state_summary_df['District'] == d]['Total Segs'].sum()
        d_miles = state_summary_df[state_summary_df['District'] == d]['Total Miles'].sum()
        print(f"  District {d}: {d_segs:5} segments, {d_miles:8.2f} miles")
    print("Generated all txt, JSON, and CSV inventory files successfully.")


if __name__ == "__main__":
    main()
