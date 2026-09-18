"""Tests for inrix_tools.kml (ROADMAP Item 6)."""
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from inrix_tools import geometry, io, kml

KML_NS = {"k": "http://www.opengis.net/kml/2.2"}

REPO_ROOT = Path(__file__).resolve().parents[1]
XD_ZIP = REPO_ROOT / "USA_Idaho_shapefile.zip"
MYRTLE_ZIP = REPO_ROOT / "Myrtle_2026-02-01_to_2026-07-16_5_min_part_1.zip"
_HAVE_REAL = XD_ZIP.exists() and MYRTLE_ZIP.exists()


def _toy_geo():
    """Two segments: one multi-vertex polyline, one straight-line fallback."""
    return gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(-116.20, 43.61), (-116.20, 43.62), (-116.20, 43.63)]),
                LineString([(-116.30, 43.50), (-116.31, 43.51)]),
            ],
            "source": ["xd", "fallback"],
            "Direction": ["N", "S"],
            "mean_speed": [35.0, 18.0],
            "Combined": ["Myrtle St N Main", "Myrtle St S Broadway"],
        },
        index=pd.Index([1001, 1002], name="Segment ID"),
        crs=geometry.WGS84,
    )


def _toy_geo_no_names():
    """Same two segments, but with none of the columns names.apply_names reads
    (no Road/Direction/Intersection/Combined) — the true segment-id fallback."""
    return gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(-116.20, 43.61), (-116.20, 43.62), (-116.20, 43.63)]),
                LineString([(-116.30, 43.50), (-116.31, 43.51)]),
            ],
            "mean_speed": [35.0, 18.0],
        },
        index=pd.Index([1001, 1002], name="Segment ID"),
        crs=geometry.WGS84,
    )


def _toy_geo_n(n, *, group_prefix="corridor-"):
    """``n`` synthetic segments (unique geometry + Segment ID), each its own
    distinct ``Group`` category — for palette-overflow / folder tests."""
    return gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(-116.0 - 0.01 * i, 43.0), (-116.0 - 0.01 * i, 43.01)])
                for i in range(n)
            ],
            "Group": [f"{group_prefix}{i}" for i in range(n)],
        },
        index=pd.Index(range(2000, 2000 + n), name="Segment ID"),
        crs=geometry.WGS84,
    )


def _write_and_parse(geo, tmp_path, **kw):
    out = kml.geometry_to_kml(geo, tmp_path / "segs.kml", **kw)
    root = ET.parse(out).getroot()
    return out, root


def test_valid_kml_with_multivertex_polyline(tmp_path):
    """The output parses as KML XML and a segment keeps its multi-vertex geometry
    (not collapsed to endpoints)."""
    out, root = _write_and_parse(_toy_geo(), tmp_path)
    assert out.exists()
    assert root.tag == "{http://www.opengis.net/kml/2.2}kml"

    placemarks = root.findall(".//k:Placemark", KML_NS)
    assert len(placemarks) == 2

    coord_els = root.findall(".//k:LineString/k:coordinates", KML_NS)
    vertex_counts = [len(c.text.split()) for c in coord_els]
    assert 3 in vertex_counts          # the polyline survives with all 3 vertices
    assert 2 in vertex_counts          # the fallback line has its 2


def test_coordinates_are_lon_lat(tmp_path):
    """KML coordinates are lon,lat,0 — shapely x is longitude."""
    _, root = _write_and_parse(_toy_geo(), tmp_path)
    first = root.find(".//k:LineString/k:coordinates", KML_NS).text.split()[0]
    lon, lat, alt = first.split(",")
    assert float(lon) == pytest.approx(-116.20)
    assert float(lat) == pytest.approx(43.61)
    assert alt == "0"


def test_labels_hidden_by_default_visible_when_requested(tmp_path):
    _, root_off = _write_and_parse(_toy_geo(), tmp_path)
    scales_off = {e.text for e in root_off.findall(".//k:LabelStyle/k:scale", KML_NS)}
    assert scales_off == {"0"}

    _, root_on = _write_and_parse(_toy_geo(), tmp_path, label_segments=True)
    scales_on = {e.text for e in root_on.findall(".//k:LabelStyle/k:scale", KML_NS)}
    assert scales_on == {"1.0"}


def test_pins_hidden_and_point_present(tmp_path):
    """Each placemark carries a label Point but the pushpin icon is scaled to 0."""
    _, root = _write_and_parse(_toy_geo(), tmp_path, label_segments=True)
    assert root.findall(".//k:Point", KML_NS)                      # label anchors exist
    icon_scales = {e.text for e in root.findall(".//k:IconStyle/k:scale", KML_NS)}
    assert icon_scales == {"0"}


def test_name_col_labels_placemarks(tmp_path):
    _, root = _write_and_parse(_toy_geo(), tmp_path, name_col="Combined")
    names = {e.text for e in root.findall(".//k:Placemark/k:name", KML_NS)}
    assert names == {"Myrtle St N Main", "Myrtle St S Broadway"}


def test_default_name_col_uses_friendly_name_when_available(tmp_path):
    """No name_col given, but geo carries Combined -> names.apply_names resolves
    a friendly name from it automatically (no Road/Intersection to simplify from
    here, so the seed falls through to the raw Combined label unchanged)."""
    _, root = _write_and_parse(_toy_geo(), tmp_path)
    names = {e.text for e in root.findall(".//k:Placemark/k:name", KML_NS)}
    assert names == {"Myrtle St N Main", "Myrtle St S Broadway"}


def test_default_name_falls_back_to_segment_id_without_naming_columns(tmp_path):
    """No name_col, and geo carries none of Road/Direction/Intersection/Combined
    -> names.apply_names has nothing to seed from and falls back to the id."""
    _, root = _write_and_parse(_toy_geo_no_names(), tmp_path)
    names = {e.text for e in root.findall(".//k:Placemark/k:name", KML_NS)}
    assert names == {"1001", "1002"}


def test_color_by_categorical_two_colors(tmp_path):
    """A non-numeric color_by yields one style per distinct value (2 directions)."""
    _, root = _write_and_parse(_toy_geo(), tmp_path, color_by="Direction")
    line_colors = {e.text for e in root.findall(".//k:LineStyle/k:color", KML_NS)}
    assert len(line_colors) == 2


def test_color_by_categorical_palette_override(tmp_path):
    """An explicit {value: colour} palette maps N->green, S->purple."""
    _, root = _write_and_parse(
        _toy_geo(), tmp_path, color_by="Direction",
        palette={"N": "green", "S": "purple"},
    )
    # green = rgb(0,128,0) -> aabbggrr ff008000 ; purple = (128,0,128) -> ff800080
    line_colors = {e.text for e in root.findall(".//k:LineStyle/k:color", KML_NS)}
    assert line_colors == {"ff008000", "ff800080"}


def test_color_by_numeric_ramp_endpoints(tmp_path):
    """A numeric color_by ramps between endpoints: min speed gets the low colour,
    max speed the high colour."""
    ramp = ((0, 0, 255), (255, 0, 0))  # blue(low) -> red(high)
    _, root = _write_and_parse(_toy_geo(), tmp_path, color_by="mean_speed", ramp=ramp)
    # placemark order follows the frame: 1001 (35, high=red), 1002 (18, low=blue)
    styles = {s.get("id"): s.find("k:LineStyle/k:color", KML_NS).text
              for s in root.findall(".//k:Style", KML_NS)}
    pm_colors = []
    for pm in root.findall(".//k:Placemark", KML_NS):
        url = pm.find("k:styleUrl", KML_NS).text.lstrip("#")
        pm_colors.append(styles[url])
    assert pm_colors[0] == "ff0000ff"   # 1001, max -> red high endpoint
    assert pm_colors[1] == "ffff0000"   # 1002, min -> blue low endpoint


def test_more_categories_than_default_palette_raises(tmp_path):
    """13 distinct categories against the 12-hue default palette raises rather
    than silently cycling (ROADMAP Item 37)."""
    geo = _toy_geo_n(13)
    with pytest.raises(ValueError, match="exceed"):
        kml.geometry_to_kml(geo, tmp_path / "segs.kml", color_by="Group")


def test_exactly_palette_size_does_not_raise(tmp_path):
    """The boundary case: exactly 12 categories against 12 hues is fine."""
    geo = _toy_geo_n(len(kml._CATEGORICAL_PALETTE))
    _, root = _write_and_parse(geo, tmp_path, color_by="Group")
    line_colors = {e.text for e in root.findall(".//k:LineStyle/k:color", KML_NS)}
    assert len(line_colors) == len(kml._CATEGORICAL_PALETTE)


def test_explicit_palette_still_cycles(tmp_path):
    """A caller who passes an explicit (shorter) palette keeps the old cycling
    behaviour — only the *default* palette raises on overflow."""
    geo = _toy_geo_n(4)
    _, root = _write_and_parse(
        geo, tmp_path, color_by="Group", palette=["green", "purple"],
    )
    line_colors = {e.text for e in root.findall(".//k:LineStyle/k:color", KML_NS)}
    assert len(line_colors) == 2   # 4 categories cycled over 2 explicit colours


def test_legend_never_lists_a_colour_twice_with_default_palette(tmp_path):
    """Within the default palette's capacity, every legend swatch is unique —
    the collision this item exists to stop."""
    geo = _toy_geo_n(len(kml._CATEGORICAL_PALETTE))
    _, root = _write_and_parse(geo, tmp_path, color_by="Group")
    legend_desc = root.find(".//k:ScreenOverlay/k:description", KML_NS).text
    # one <font color='#rrggbb'> per legend row; every swatch colour must be
    # distinct (no two categories sharing a colour on the legend).
    import re
    swatches = re.findall(r"<font color='(#[0-9a-fA-F]{6})'>", legend_desc)
    assert len(swatches) == len(kml._CATEGORICAL_PALETTE)
    assert len(set(swatches)) == len(swatches)


def test_folder_by_groups_placemarks(tmp_path):
    """folder_by emits one <Folder> per distinct group, each containing exactly
    that group's placemarks."""
    geo = _toy_geo_n(4)
    geo["Group"] = ["A", "A", "B", "B"]
    _, root = _write_and_parse(geo, tmp_path, folder_by="Group")

    folders = root.findall("k:Document/k:Folder", KML_NS)
    assert len(folders) == 2

    membership = {}
    for folder in folders:
        label = folder.find("k:name", KML_NS).text
        pm_names = {p.find("k:name", KML_NS).text
                    for p in folder.findall("k:Placemark", KML_NS)}
        membership[label] = pm_names

    assert membership["A"] == {"2000", "2001"}
    assert membership["B"] == {"2002", "2003"}
    # no placemark escaped into the flat document level
    assert root.findall("k:Document/k:Placemark", KML_NS) == []


def test_folder_by_missing_value_grouped_as_none(tmp_path):
    geo = _toy_geo_n(2)
    geo["Group"] = ["A", None]
    _, root = _write_and_parse(geo, tmp_path, folder_by="Group")
    folder_names = {f.find("k:name", KML_NS).text
                    for f in root.findall("k:Document/k:Folder", KML_NS)}
    assert folder_names == {"A", "(none)"}


def test_folder_by_unknown_column_raises(tmp_path):
    with pytest.raises(ValueError):
        kml.geometry_to_kml(_toy_geo(), tmp_path / "segs.kml", folder_by="NoSuchCol")


def test_missing_geometry_skipped(tmp_path):
    geo = _toy_geo()
    geo.loc[1002, "geometry"] = None
    _, root = _write_and_parse(geo, tmp_path)
    assert len(root.findall(".//k:Placemark", KML_NS)) == 1


def test_accepts_segment_id_as_column(tmp_path):
    """geo with Segment ID as a plain column (not the index) also works."""
    geo = _toy_geo().reset_index()
    _, root = _write_and_parse(geo, tmp_path)
    assert len(root.findall(".//k:Placemark", KML_NS)) == 2


def test_resolve_color_forms():
    assert kml._resolve_color("blue") == "ffff0000"
    assert kml._resolve_color("#ff0000") == "ff0000ff"
    assert kml._resolve_color((0, 255, 0)) == "ff00ff00"
    assert kml._resolve_color("ff123456") == "ff123456"
    with pytest.raises(ValueError):
        kml._resolve_color("not-a-color")


# --- real data (skipped when the licensed exports are absent) ----------------
@pytest.mark.skipif(not _HAVE_REAL, reason="XD shapefile / Myrtle export absent")
def test_real_myrtle_kml_roundtrip(tmp_path):
    meta = io.load_metadata(MYRTLE_ZIP)
    ids = list(meta.index)
    net = geometry.load_xd_network(XD_ZIP, segment_ids=ids)
    geo = geometry.segment_geometry(net, segment_ids=ids, metadata=meta)
    geo = geo.join(meta[["Direction", "Combined"]])

    out, root = _write_and_parse(geo, tmp_path, label_segments=True,
                                 name_col="Combined", color_by="Direction")
    placemarks = root.findall(".//k:Placemark", KML_NS)
    assert len(placemarks) == len(ids)
    # real segments are multi-vertex road-following polylines
    max_vertices = max(len(c.text.split())
                       for c in root.findall(".//k:LineString/k:coordinates", KML_NS))
    assert max_vertices > 2
