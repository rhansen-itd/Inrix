#!/usr/bin/env python3
"""Derive the generic volume-profile curves from their published sources.  (ROADMAP Item 55)

Two stages, so the curve library can be rebuilt without re-reading the PDFs:

1. ``--extract`` reads the two source PDFs and writes the raw series they contain
   to ``scripts/volume_profile_sources.json`` (committed, small):

   * **TTI 2019 Urban Mobility Report, Appendix A**, Exhibits A-1 to A-5: weekday
     and weekend *directional* 15-minute distributions (% of daily volume), built
     from 713 continuous count stations in 37 states. They are published only as
     raster charts, so the series are **digitised** by line colour against the
     chart's own gridlines (0.0 % and 3.0 %); each digitised series sums to
     0.997–1.009 before normalisation, which is the check on the calibration.
   * **EPA 2017 NEI onroad review plots, Idaho** (``HourlyVMTFractionID.pdf``): the
     hourly VMT fractions Idaho submitted per county, road type and day type, with
     the CRC A-100 regional curves beside them. The plots are vector graphics, so
     the values are **extracted** from the PDF paths (calibrated against the axis
     labels), not read by eye; each curve sums to 1.005–1.011 before
     normalisation.

   Needs poppler (``pdftotext``/``pdfimages``/``pdftocairo``) and Pillow — a
   one-off research step, not a package dependency.

2. The default stage builds ``src/inrix_tools/data/volume_profiles.json`` from the
   extracted series plus the day-of-week tables typed in below (TTI UMR Exhibit A-6
   and INDOT's 2023 factors, both published as text).

Usage:
    python scripts/derive_volume_profiles.py --extract \\
        --umr mobility-report-2019-appx-a.pdf --epa HourlyVMTFractionID.pdf
    python scripts/derive_volume_profiles.py
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCES_JSON = ROOT / "scripts" / "volume_profile_sources.json"
PROFILES_JSON = ROOT / "src" / "inrix_tools" / "data" / "volume_profiles.json"

SOURCES = {
    "tti_umr_2019": {
        "citation": ("Schrank, Eisele, Lomax. 2019 Urban Mobility Report, Appendix A: "
                     "Methodology. Texas A&M Transportation Institute, 2019. Exhibits A-1 "
                     "to A-5 (15-minute traffic distribution profiles) and A-6 (day of "
                     "week volume conversion factors)."),
        "url": ("https://static.tti.tamu.edu/tti.tamu.edu/documents/umr/archive/"
                "mobility-report-2019-appx-a.pdf"),
    },
    "epa_nei2017_id": {
        "citation": ("US EPA, 2017 National Emissions Inventory onroad review: Hourly VMT "
                     "Fraction plots by county, Idaho (Idaho's county database submittal "
                     "and the CRC A-100 regional curves), 2019."),
        "url": ("https://gaftp.epa.gov/Air/emismod/2017/reports/onroad/"
                "HourVMTFraction_byState/HourlyVMTFractionID.pdf"),
    },
    "indot_2023": {
        "citation": ("Indiana DOT, Latest INDOT Traffic Adjustment Factors (2023 data), "
                     "Weekday Factors by Functional Classification, August 2024."),
        "url": "https://www.in.gov/indot/files/INDOT_Factors_2023.pdf",
    },
    "fhwa_recreational": {
        "citation": ("FHWA, National Guidance for Traffic Monitoring in Recreational "
                     "Areas — recreational routes are steady on weekdays and surge at "
                     "weekends, with a single daily peak."),
        "url": ("https://www.fhwa.dot.gov/clas/ctip/traffic_monitoring_recreational_areas/"
                "background.aspx"),
    },
    "itd_aadt_2025": {
        "citation": ("ITD AADT 2025 layer (AADT_2025.zip): Commercial / AADT on interstate "
                     "mainline records."),
        "url": None,
    },
}

# TTI UMR 2019 Exhibit A-6: adjustment to convert average annual volume into a
# day-of-week volume. Mon–Thu +5 %, Fri +10 %, Sat −10 %, Sun −20 % (mean exactly 1).
UMR_DOW = [1.05, 1.05, 1.05, 1.05, 1.10, 0.90, 0.80]

# INDOT 2023 weekday factors, "Average" column, Monday..Sunday. These are the factor
# a day's count is *multiplied* by to give AADT, so the volume factor is the inverse.
INDOT_R1_RURAL_INTERSTATE = [1.057, 1.037, 1.005, 0.948, 0.849, 1.050, 1.112]
INDOT_R2_RURAL_ARTERIAL = [1.008, 0.952, 0.936, 0.921, 0.861, 1.099, 1.367]

# Synthesised (no published Idaho table on hand): steady weekdays, Friday–Sunday
# surge. A placeholder until Item 59 fits it from an ATR on a recreational route.
RECREATIONAL_DOW = [0.95, 0.85, 0.85, 0.90, 1.15, 1.25, 1.05]

# Idaho counties whose rural state highways carry resort / recreation traffic
# (Sun Valley, McCall, Driggs, Stanley, Island Park, Sandpoint).
RECREATIONAL_COUNTIES = ["Blaine", "Valley", "Teton", "Custer", "Fremont", "Bonner"]

# AADT-weighted Commercial / AADT on the 2025 layer's interstate mainline records that
# populate Commercial (318 records; ``aadt.load_aadt`` + record_kind == mainline).
INTERSTATE_TRUCK_SHARE = 0.134


# ---------------------------------------------------------------------------
# Stage 1: extraction (poppler + Pillow)
# ---------------------------------------------------------------------------
_UMR_BLUE, _UMR_GREEN = (74, 126, 187), (155, 187, 89)
_UMR_RED, _UMR_PURPLE, _UMR_RED2 = (192, 80, 77), (125, 96, 160), (190, 75, 72)
_UMR_CHARTS = {   # pdfimages index -> (exhibit, {series: line colour})
    0: ("A-1", {"am_fwy": _UMR_BLUE, "am_nonfwy": _UMR_GREEN,
                "pm_fwy": _UMR_RED, "pm_nonfwy": _UMR_PURPLE}),
    2: ("A-2", {"am_fwy": _UMR_BLUE, "am_nonfwy": _UMR_GREEN,
                "pm_fwy": _UMR_RED, "pm_nonfwy": _UMR_PURPLE}),
    4: ("A-3", {"am_fwy": _UMR_BLUE, "am_nonfwy": _UMR_GREEN,
                "pm_fwy": _UMR_RED, "pm_nonfwy": _UMR_PURPLE}),
    6: ("A-4", {"fwy": _UMR_BLUE, "nonfwy": _UMR_RED2}),
    8: ("A-5", {"fwy": _UMR_BLUE, "nonfwy": _UMR_RED2}),
}
_GRID = (134, 134, 134)


def _digitise_chart(png, series):
    """15-minute shares (fractions) for each line colour in one UMR chart."""
    from PIL import Image

    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    grid_rows = np.where((a == _GRID).all(axis=2).sum(axis=1) > 500)[0]
    top, bot = grid_rows[:2].mean(), grid_rows[-2:].mean()       # 3.0 % and 0.0 %
    masks = {}
    for name, colour in series.items():
        m = np.abs(a - np.array(colour)).sum(axis=2) <= 12
        m[int(bot) + 6:, :] = False                                # drop the legend
        masks[name] = m
    # One category axis per chart: a series whose end point is hidden under another
    # line would otherwise stretch its own x-mapping.
    x0 = min(np.where(m.any(axis=0))[0].min() for m in masks.values())
    x1 = max(np.where(m.any(axis=0))[0].max() for m in masks.values())
    out = {}
    for name, m in masks.items():
        ys = []
        for x in np.linspace(x0, x1, 96):
            xi = int(round(x))
            rows = np.where(m[:, max(xi - 1, 0):xi + 2].any(axis=1))[0]
            ys.append(rows.mean() if len(rows) and np.ptp(rows) < 25 else np.nan)
        ys = np.array(ys)
        ok = ~np.isnan(ys)       # hidden under another line: interpolate across
        ys = np.interp(np.arange(96), np.arange(96)[ok], ys[ok])
        share = (bot - ys) / (bot - top) * 0.03
        out[name] = {"q15": [round(float(v), 6) for v in share],
                     "raw_sum": round(float(share.sum()), 4),
                     "interpolated_points": int((~ok).sum())}
    return out


def extract_umr(pdf):
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdfimages", "-png", "-f", "8", "-l", "10", str(pdf), f"{tmp}/ex"],
                       check=True)
        return {ex: _digitise_chart(f"{tmp}/ex-{i:03d}.png", series)
                for i, (ex, series) in _UMR_CHARTS.items()}


# Plot order on every EPA page (= legend order): 3 CRC A-100 curves, then Idaho's
# submittal by source type. Source type 21 is the passenger car.
_EPA_ORDER = ["CRC_LDV", "CRC_SU", "CRC_CT", "ST11", "ST21", "ST31", "ST32", "ST41",
              "ST42", "ST43", "ST51", "ST52", "ST53", "ST54", "ST61", "ST62"]
_EPA_KEEP = ("ST21", "CRC_CT")


def _svg_curves(svg):
    txt = Path(svg).read_text()
    curves = []
    for m in re.finditer(r'<path style="([^"]*)" d="([^"]*)" '
                         r'transform="matrix\(([^)]*)\)"', txt):
        style, d, mat = m.groups()
        if "stroke-width:14.4" not in style:
            continue
        a, b, c, dd, e, f = map(float, mat.split(","))
        nums = list(map(float, re.findall(r"-?\d+\.?\d*", d)))
        pts = [(a * x + c * y + e, b * x + dd * y + f)
               for x, y in zip(nums[0::2], nums[1::2])]
        if len(pts) > 2:                        # 2-point strokes are legend swatches
            curves.append(pts)
    return curves


def extract_epa(pdf):
    n_pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(
        ["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout).group(1))
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for p in range(1, n_pages + 1):
            text = subprocess.run(["pdftotext", "-f", str(p), "-l", str(p), "-layout",
                                   str(pdf), "-"], capture_output=True, text=True).stdout
            head = [ln.strip() for ln in text.splitlines() if ln.strip()][:4]
            bbox = subprocess.run(["pdftotext", "-f", str(p), "-l", str(p), "-bbox",
                                   str(pdf), "-"], capture_output=True, text=True).stdout
            labels = {float(m.group(3)): (float(m.group(1)) + float(m.group(2))) / 2
                      for m in re.finditer(r'yMin="([\d.]+)" xMax="[\d.]+" '
                                           r'yMax="([\d.]+)">(0\.\d\d)<', bbox)}
            lo, hi = min(labels), max(labels)
            svg = f"{tmp}/{p}.svg"
            subprocess.run(["pdftocairo", "-svg", "-f", str(p), "-l", str(p), str(pdf), svg],
                           check=True)
            curves = dict(zip(_EPA_ORDER, _svg_curves(svg)))
            row = {"county": head[0].split(": ")[1], "area": head[1],
                   "road_type": int(re.search(r"Type (\d)", head[2]).group(1)),
                   "day": head[3].lower()}
            for name in _EPA_KEEP:
                pts = curves.get(name)
                if pts is None or len(pts) != 24:
                    continue
                vals = [lo + (labels[lo] - y) * (hi - lo) / (labels[lo] - labels[hi])
                        for _, y in pts]
                row[name] = [round(v, 6) for v in vals]
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Stage 2: build the curve library
# ---------------------------------------------------------------------------
def _exact(values, total):
    """Round to 8 d.p. and put the rounding residual on the largest element, so the
    stored numbers sum to ``total`` to float precision."""
    v = np.asarray(values, float)
    v = np.round(v / v.sum() * total, 8)
    v[int(np.argmax(v))] += total - v.sum()
    return [round(float(x), 10) for x in v]


def _hourly_from_q15(q15):
    return np.asarray(q15, float).reshape(24, 4).sum(axis=1)


def _dow_from_adjustment(factors):
    """INDOT factors convert a day's count *to* AADT, so a day's volume is 1/factor."""
    return 1.0 / np.asarray(factors, float)


def _epa_median(rows, *, road_type, day, series, counties=None):
    picked = [r[series] for r in rows
              if r["road_type"] == road_type and r["day"] == day and series in r
              and r["area"].startswith("RURAL")
              and (counties is None or r["county"] in counties)]
    if not picked:
        raise SystemExit(f"no EPA curves for road type {road_type} / {day} / {series}")
    m = np.median(np.asarray(picked, float), axis=0)
    return m / m.sum(), len(picked)


def _epa_counties(rows, *, road_type, day, series, counties):
    """The counties of ``counties`` that actually have a complete curve."""
    return sorted({r["county"] for r in rows
                   if r["road_type"] == road_type and r["day"] == day and series in r
                   and r["area"].startswith("RURAL") and r["county"] in counties})


def build_profiles(src):
    umr, epa = src["umr"], src["epa_id"]
    urban_weekend = _hourly_from_q15(umr["A-4"]["nonfwy"]["q15"])
    profiles = []

    def add(curve_id, description, weekday, sat, sun, dow, basis, sources, detail):
        profiles.append({
            "curve_id": curve_id, "description": description,
            "hourly": {"weekday": _exact(weekday, 1.0), "sat": _exact(sat, 1.0),
                       "sun": _exact(sun, 1.0)},
            "dow": _exact(dow, 7.0),
            "provenance": {"basis": basis, "sources": sources, "detail": detail},
        })

    umr_detail = ("Weekday: UMR Exhibit {ex} '{series}' (digitised, 15-min shares summed "
                  "to hours and renormalised). Sat/Sun: Exhibit A-4 non-freeway weekend "
                  "(the UMR publishes one weekend curve). DOW: Exhibit A-6.")
    add("am_commute_urban",
        "Urban arterial, the AM-peak direction (inbound commute): sharp 7 AM peak, "
        "flatter PM.",
        _hourly_from_q15(umr["A-2"]["am_nonfwy"]["q15"]), urban_weekend, urban_weekend,
        UMR_DOW, "digitised", ["tti_umr_2019"],
        umr_detail.format(ex="A-2 (moderate congestion)", series="AM Peak - Non-Freeway"))
    add("pm_commute_urban",
        "Urban arterial, the PM-peak direction (outbound commute): 4–6 PM peak, "
        "muted AM.",
        _hourly_from_q15(umr["A-2"]["pm_nonfwy"]["q15"]), urban_weekend, urban_weekend,
        UMR_DOW, "digitised", ["tti_umr_2019"],
        umr_detail.format(ex="A-2 (moderate congestion)", series="PM Peak - Non-Freeway"))
    add("balanced_urban",
        "Urban arterial with similar AM and PM peaks and a busy midday (no dominant "
        "commute direction).",
        _hourly_from_q15(umr["A-5"]["nonfwy"]["q15"]), urban_weekend, urban_weekend,
        UMR_DOW, "digitised", ["tti_umr_2019"],
        umr_detail.format(ex="A-5 (similar speeds in each peak)", series="Non-Freeway"))

    rt_wd, n_wd = _epa_median(epa, road_type=3, day="weekday", series="ST21")
    rt_we, n_we = _epa_median(epa, road_type=3, day="weekend", series="ST21")
    add("rural_through",
        "Rural two-lane / arterial state highway: one broad daytime hump peaking "
        "3–5 PM.",
        rt_wd, rt_we, rt_we, _dow_from_adjustment(INDOT_R2_RURAL_ARTERIAL),
        "extracted", ["epa_nei2017_id", "indot_2023"],
        f"Hourly: median over {n_wd} (weekday) / {n_we} (weekend) non-MSA Idaho counties "
        "of Idaho's submitted passenger-car (source type 21) curve on rural "
        "non-freeways (MOVES road type 3), extracted from the PDF's vector paths; one "
        "weekend curve for Sat and Sun. DOW: inverse of INDOT's 2023 R2 (rural "
        "principal/minor arterial) weekday factors, renormalised to mean 1.")

    t = INTERSTATE_TRUCK_SHARE
    car_wd, n_i = _epa_median(epa, road_type=2, day="weekday", series="ST21")
    car_we, _ = _epa_median(epa, road_type=2, day="weekend", series="ST21")
    trk_wd, _ = _epa_median(epa, road_type=2, day="weekday", series="CRC_CT")
    trk_we, _ = _epa_median(epa, road_type=2, day="weekend", series="CRC_CT")
    add("interstate_through",
        "Rural interstate: passenger-car daytime hump broadened by round-the-clock "
        "combination-truck traffic.",
        (1 - t) * car_wd + t * trk_wd, (1 - t) * car_we + t * trk_we,
        (1 - t) * car_we + t * trk_we, _dow_from_adjustment(INDOT_R1_RURAL_INTERSTATE),
        "synthesised", ["epa_nei2017_id", "itd_aadt_2025", "indot_2023"],
        f"Hourly: {1 - t:.3f} × Idaho's passenger-car curve + {t:.3f} × the CRC A-100 "
        "combination-truck curve (West Region non-MSA), both on rural freeways (MOVES "
        f"road type 2), median over {n_i} non-MSA Idaho counties; the truck share is "
        "the AADT-weighted Commercial/AADT of the 2025 layer's interstate mainline "
        "records. DOW: inverse of INDOT's 2023 R1 (rural interstate) weekday factors, "
        "renormalised.")

    rec, n_r = _epa_median(epa, road_type=3, day="weekend", series="ST21",
                           counties=RECREATIONAL_COUNTIES)
    used = _epa_counties(epa, road_type=3, day="weekend", series="ST21",
                         counties=RECREATIONAL_COUNTIES)
    add("rural_recreational",
        "Rural recreational / resort route: a single midday hump every day, and "
        "Friday–Sunday heavier than midweek.",
        rec, rec, rec, RECREATIONAL_DOW, "synthesised",
        ["epa_nei2017_id", "fhwa_recreational"],
        f"Hourly: Idaho's rural non-freeway weekend passenger-car curve, median over "
        f"{n_r} recreational counties ({', '.join(used)}; "
        f"{', '.join(sorted(set(RECREATIONAL_COUNTIES) - set(used))) or 'none'} has no "
        "complete weekend curve in the source), used for "
        "every day type. The weekday curves in those counties are indistinguishable "
        "from the rest of rural Idaho, so the leisure (weekend) shape stands in for a "
        "recreational route. DOW: synthesised {Mon 0.95, Tue 0.85, Wed 0.85, Thu 0.90, "
        "Fri 1.15, Sat 1.25, Sun 1.05} after FHWA's recreational guidance — a "
        "placeholder until fitted from an ATR (Item 59).")

    return {
        "schema_version": 1,
        "note": ("Generic 24-hour volume-profile curves (ROADMAP Item 55). Built by "
                 "scripts/derive_volume_profiles.py from scripts/volume_profile_sources.json; "
                 "do not hand-edit. Each day type's hourly shares sum to 1 (hour 0 = "
                 "00:00-01:00 local). dow is Monday..Sunday with mean 1."),
        "sources": SOURCES,
        "profiles": profiles,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--extract", action="store_true",
                    help="re-read the source PDFs into volume_profile_sources.json first")
    ap.add_argument("--umr", type=Path, help="TTI UMR 2019 Appendix A PDF")
    ap.add_argument("--epa", type=Path, help="EPA HourlyVMTFractionID.pdf")
    args = ap.parse_args(argv)

    if args.extract:
        if not (args.umr and args.epa):
            ap.error("--extract needs --umr and --epa")
        src = {"umr": extract_umr(args.umr), "epa_id": extract_epa(args.epa),
               "note": ("Raw series read from the source PDFs by "
                        "scripts/derive_volume_profiles.py --extract (Item 55). UMR "
                        "values are 15-minute fractions of daily volume (digitised); "
                        "EPA values are hourly VMT fractions (extracted from vector "
                        "paths), un-normalised.")}
        SOURCES_JSON.write_text(json.dumps(src, separators=(",", ":")) + "\n")
        print(f"wrote {SOURCES_JSON}")
    src = json.loads(SOURCES_JSON.read_text())
    lib = build_profiles(src)
    PROFILES_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROFILES_JSON.write_text(json.dumps(lib, indent=1) + "\n")
    print(f"wrote {PROFILES_JSON} ({len(lib['profiles'])} curves)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
