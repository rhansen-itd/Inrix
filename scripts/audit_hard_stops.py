#!/usr/bin/env python3
"""Resolve the manual hard stops and audit the catalogues against them (ROADMAP Item 60).

For each district with rows in ``scripts/corridor_hard_stops.csv`` this:

1. resolves every row to its segment boundary (``inrix_tools.hard_stops``) on the
   district network as the catalogue builder loads it, and writes
   ``resolved_hard_stops.csv``;
2. draws them on ``hard_stops_map.html``: the segment before each boundary in blue,
   the one after in orange, the boundary as a dot whose popup carries the note — to
   confirm each stop lands where the owner meant it;
3. lists every catalogue entry that **steps across** a stop, in
   ``catalogue_crossings.csv``: the curated D3 catalogue (``d3_corridors.json``, which
   is never regenerated, so the owner decides what to do with each crossing) and the
   committed generated catalogues (which a re-run with the stops would re-cut).

A row that resolves to nothing stops the script with the row named.

Usage:
    python scripts/audit_hard_stops.py
    python scripts/audit_hard_stops.py --out-dir out/hard_stops --districts 2 3
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from inrix_tools import corridors, hard_stops  # noqa: E402
from build_statewide_catalogues import GENERATED_D3, load_district  # noqa: E402

CURATED_D3 = "scripts/d3_corridors.json"


def catalogue_paths(district: int) -> list[tuple[str, Path]]:
    """``(kind, path)`` for each committed catalogue of the district."""
    if district == 3:
        return [("curated", Path(CURATED_D3)), ("generated", Path("scripts") / GENERATED_D3)]
    return [("generated", Path(f"scripts/d{district}_corridors.json"))]


def entry_segments(cat: dict, net, repairs) -> dict[str, tuple[int, ...]]:
    """Each entry's segments in travel order: ``_segment_ids`` where the generator wrote
    them, else the entry resolved through ``corridors.resolve_catalogue``."""
    out = {e["id"]: tuple(int(s) for s in e["_segment_ids"])
           for e in cat["corridors"] if e.get("_segment_ids")}
    rest = [e for e in cat["corridors"] if e["id"] not in out]
    if rest:
        res = corridors.resolve_catalogue(net, rest, repairs=repairs)
        for eid, chain in res.attrs["chains"].items():
            out[eid] = tuple(int(s) for s in chain.segment_ids)
    return out


def catalogue_crossings(district: int, net, repairs, boundaries) -> list[dict]:
    rows = []
    for kind, path in catalogue_paths(district):
        if not path.exists():
            continue
        cat = json.loads(path.read_text())
        names = {e["id"]: e.get("name", "") for e in cat["corridors"]}
        for eid, ids in entry_segments(cat, net, repairs).items():
            for x in hard_stops.crossings(ids, boundaries):
                rows.append({"district": district, "catalogue": path.name, "kind": kind,
                             "entry": eid, "name": names.get(eid, ""),
                             "n_segments": len(ids), "at_index": x["index"],
                             "from_seg": x["from_seg"], "to_seg": x["to_seg"],
                             "stop": x["reason"]})
    return rows


def _feature(net_idx, seg: int, props: dict) -> dict | None:
    if seg not in net_idx.index:
        return None
    g = net_idx.loc[seg, "geometry"]
    return {"type": "Feature", "geometry": g.__geo_interface__, "properties": props}


def write_map(resolved: pd.DataFrame, nets: dict, path: Path) -> None:
    """A self-contained Leaflet page of every resolved boundary."""
    feats = []
    for r in resolved.itertuples(index=False):
        idx = nets[r.district]
        label = (f"D{r.district} route {r.route} {r.direction or 'both'} (row {r.row}): "
                 f"{r.from_seg} {r.from_name} | {r.to_seg} {r.to_name}"
                 + ("" if r.on_chain else " [link stub]"))
        for seg, role in ((r.from_seg, "from"), (r.to_seg, "to")):
            f = _feature(idx, int(seg), {"role": role, "label": html.escape(label)})
            if f:
                feats.append(f)
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [r.at_lon, r.at_lat]},
                      "properties": {"role": "stop", "label": html.escape(label),
                                     "note": html.escape(r.note)}})
    data = json.dumps({"type": "FeatureCollection", "features": feats})
    path.write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Hard stops</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#map{{height:100%;margin:0}}
.legend{{background:#fff;padding:6px 8px;font:13px sans-serif;border-radius:4px}}</style>
</head><body><div id="map"></div><script>
const data = {data};
const map = L.map('map');
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
  {{maxZoom: 19, attribution: '&copy; OpenStreetMap'}}).addTo(map);
const colour = {{from: '#1f6fd1', to: '#e8730c'}};
const layer = L.geoJSON(data, {{
  style: f => ({{color: colour[f.properties.role], weight: 6, opacity: 0.8}}),
  pointToLayer: (f, ll) => L.circleMarker(ll, {{radius: 6, color: '#111', fillColor: '#d11',
                                                fillOpacity: 1, weight: 1}}),
  onEachFeature: (f, l) => l.bindPopup(f.properties.label +
      (f.properties.note ? '<br><i>' + f.properties.note + '</i>' : ''))
}}).addTo(map);
map.fitBounds(layer.getBounds(), {{padding: [20, 20]}});
const legend = L.control({{position: 'topright'}});
legend.onAdd = () => {{ const d = L.DomUtil.create('div', 'legend');
  d.innerHTML = '<b>Hard stops</b><br><span style="color:#1f6fd1">&#9644;</span> before ' +
    '<span style="color:#e8730c">&#9644;</span> after &nbsp;<span style="color:#d11">&#9679;</span> boundary';
  return d; }};
legend.addTo(map);
</script></body></html>
""")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stops", default="scripts/corridor_hard_stops.csv")
    parser.add_argument("--districts", nargs="*", type=int, default=None,
                        help="default: every district with a row")
    parser.add_argument("--shs", default="SHS_Primary.zip")
    parser.add_argument("--out-dir", default="out/hard_stops")
    args = parser.parse_args()

    table = hard_stops.read_hard_stops(args.stops)
    districts = args.districts or sorted(table["district"].unique().tolist())
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    shs = args.shs if args.shs and Path(args.shs).exists() else None

    resolved, crossings, nets = [], [], {}
    for d in districts:
        net, repairs = load_district(d, aadt_source=None, aadt_year=0, shs=shs)
        r = hard_stops.resolve_hard_stops(table, net, d)
        resolved.append(r)
        nets[d] = net.set_index("XDSegID", drop=False) if net.index.name != "XDSegID" else net
        print(f"\nDistrict {d}: {r['row'].nunique()} rows -> {len(r)} boundaries")
        for x in r.itertuples(index=False):
            print(f"  row {x.row:>2} route {x.route:>3} {x.direction or '-':1} "
                  f"{x.from_seg} {x.from_name!s:<18} | {x.to_seg} {x.to_name!s:<18} "
                  f"{x.distance_m:>5.1f} m{'' if x.on_chain else '  (link stub)'}")
        rows = catalogue_crossings(d, net, repairs, hard_stops.stop_boundaries(r))
        crossings.extend(rows)
        for c in rows:
            print(f"  crosses: [{c['kind']}] {c['entry']} at {c['from_seg']}|{c['to_seg']}")

    resolved_all = pd.concat(resolved, ignore_index=True)
    resolved_all.to_csv(out_dir / "resolved_hard_stops.csv", index=False)
    pd.DataFrame(crossings, columns=["district", "catalogue", "kind", "entry", "name",
                                     "n_segments", "at_index", "from_seg", "to_seg",
                                     "stop"]).to_csv(out_dir / "catalogue_crossings.csv",
                                                     index=False)
    write_map(resolved_all, nets, out_dir / "hard_stops_map.html")
    print(f"\n{len(resolved_all)} boundaries -> {out_dir / 'resolved_hard_stops.csv'}, "
          f"{out_dir / 'hard_stops_map.html'}")
    print(f"{len(crossings)} catalogue crossings -> {out_dir / 'catalogue_crossings.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
