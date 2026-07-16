"""Segment geometry -> KML export.  (ROADMAP Item 6)

One clean ``geometry_to_kml`` consolidated from the two near-duplicate
``csv_to_kml`` functions in ``_metadata KML.ipynb``. The key upgrade over the
seed: each segment is drawn as its **real road-following polyline** from the
geometry layer (:func:`inrix_tools.geometry.segment_geometry`, ROADMAP Item 8)
instead of a straight endpoint-to-endpoint line. If the geometry layer only had
endpoints for a segment it already substituted the straight-line fallback, so
that path is preserved for free.

The seed's other touches are kept: optional always-visible labels with the
pushpin icon hidden (text-only), and colouring by a per-segment attribute — the
seed's blue=N/E / red=S/W direction colouring generalises to an arbitrary
``color_by`` column (categorical *or* a numeric metric ramp).

Pure export: a GeoDataFrame in, a ``.kml`` out. No plotting, no compute — the
caller supplies whatever metric column it wants coloured.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

SEGMENT_COL = "Segment ID"

# Named colours as RGB; KML wants aabbggrr so we build the string on resolve.
_NAMED_COLORS = {
    "blue": (0, 0, 255),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "yellow": (255, 215, 0),
    "black": (0, 0, 0),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "white": (255, 255, 255),
    "teal": (0, 128, 128),
    "magenta": (255, 0, 255),
}
# Cycled for categorical color_by columns (e.g. Direction).
_CATEGORICAL_PALETTE = ["blue", "red", "green", "orange", "purple", "teal",
                        "magenta", "gray"]
# Endpoints of the default numeric ramp (low -> high): cool blue -> hot red.
_DEFAULT_RAMP = ((43, 131, 186), (215, 25, 28))


# ---------------------------------------------------------------------------
# Colour helpers  (KML colour is aabbggrr — alpha, blue, green, red)
# ---------------------------------------------------------------------------
def _rgb_to_kml(rgb, alpha=255) -> str:
    r, g, b = (int(c) for c in rgb[:3])
    a = int(rgb[3]) if len(rgb) > 3 else alpha
    return f"{a:02x}{b:02x}{g:02x}{r:02x}"


def _resolve_color(color) -> str:
    """Normalise a colour spec to a KML ``aabbggrr`` string. Accepts a named
    colour, ``#rrggbb``, an ``(r, g, b[, a])`` tuple, or an already-KML 8-hex
    string."""
    if isinstance(color, (tuple, list)):
        return _rgb_to_kml(color)
    if isinstance(color, str):
        c = color.strip()
        if c.startswith("#"):
            h = c[1:]
            return _rgb_to_kml((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
        low = c.lower()
        if low in _NAMED_COLORS:
            return _rgb_to_kml(_NAMED_COLORS[low])
        if len(c) == 8:  # assume already aabbggrr
            int(c, 16)  # validate
            return c.lower()
    raise ValueError(f"Unrecognised colour: {color!r}")


def _lerp_ramp(t, ramp) -> str:
    """Colour at fraction ``t`` in ``[0, 1]`` along a two-endpoint RGB ramp."""
    lo, hi = ramp
    t = 0.0 if t < 0 else 1.0 if t > 1 else float(t)
    rgb = tuple(lo[i] + (hi[i] - lo[i]) * t for i in range(3))
    return _rgb_to_kml(rgb)


def _segment_colors(geo, color_by, default_color, palette, ramp):
    """Map each segment (index-aligned) to a KML colour string.

    Returns ``(colors: dict[label_index -> kml_color], legend: list[(text, kml)])``.
    ``color_by=None`` -> every segment the single ``default_color``.
    A non-numeric column -> categorical palette (one colour per distinct value).
    A numeric column -> continuous ``ramp`` over its min..max (NaN -> default).
    """
    import pandas as pd

    default = _resolve_color(default_color)
    if color_by is None or color_by not in geo.columns:
        return {i: default for i in geo.index}, []

    col = geo[color_by]
    if pd.api.types.is_numeric_dtype(col):
        ramp = ramp or _DEFAULT_RAMP
        lo, hi = col.min(), col.max()
        span = (hi - lo) if pd.notna(lo) and pd.notna(hi) and hi != lo else None
        colors = {}
        for i, v in col.items():
            if pd.isna(v):
                colors[i] = default
            elif span is None:
                colors[i] = _lerp_ramp(0.5, ramp)
            else:
                colors[i] = _lerp_ramp((v - lo) / span, ramp)
        legend = [] if span is None else [
            (f"{color_by} = {lo:g}", _lerp_ramp(0.0, ramp)),
            (f"{color_by} = {hi:g}", _lerp_ramp(1.0, ramp)),
        ]
        return colors, legend

    # categorical
    if isinstance(palette, dict):
        value_color = {v: _resolve_color(c) for v, c in palette.items()}
    else:
        names = list(palette) if palette else _CATEGORICAL_PALETTE
        uniq = [v for v in col.dropna().unique()]
        value_color = {v: _resolve_color(names[k % len(names)])
                       for k, v in enumerate(uniq)}
    colors = {i: value_color.get(v, default) for i, v in col.items()}
    legend = [(f"{color_by}: {v}", c) for v, c in value_color.items()]
    return colors, legend


# ---------------------------------------------------------------------------
# KML building blocks
# ---------------------------------------------------------------------------
def _coord_string(geom) -> str:
    """``lon,lat,0`` triples from a shapely LineString (EPSG:4326: x=lon, y=lat)."""
    return " ".join(f"{x},{y},0" for x, y in geom.coords)


def _add_line_style(document, style_id, kml_color, label_scale):
    style = ET.SubElement(document, "Style", id=style_id)
    linestyle = ET.SubElement(style, "LineStyle")
    ET.SubElement(linestyle, "color").text = kml_color
    ET.SubElement(linestyle, "width").text = "3"
    # Label text visible only when requested; colour it like the line.
    labelstyle = ET.SubElement(style, "LabelStyle")
    ET.SubElement(labelstyle, "color").text = kml_color
    ET.SubElement(labelstyle, "scale").text = "1.0" if label_scale else "0"
    # Hide the default pushpin icon (empty <Icon> + scale 0), as in the seed.
    iconstyle = ET.SubElement(style, "IconStyle")
    ET.SubElement(iconstyle, "scale").text = "0"
    ET.SubElement(iconstyle, "Icon")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def geometry_to_kml(geo, out_path, *, label_segments=False, name_col=None,
                    color_by=None, default_color="blue", palette=None,
                    ramp=None, document_name="INRIX segments"):
    """Write a KML drawing each segment as its road-following polyline.

    Args:
        geo: a GeoDataFrame from :func:`inrix_tools.geometry.segment_geometry` —
            indexed by ``Segment ID`` with a shapely ``geometry`` column (real
            polylines, or the straight-line fallback the layer already provides).
            May carry extra attribute columns (e.g. ``Direction``, a metric) used
            for labels and colouring. Segments with no geometry (``None``) are
            skipped.
        out_path: destination ``.kml`` path.
        label_segments: if True, each segment's name is always visible (the seed's
            "always-on label, hidden pin" behaviour) instead of on-hover only.
        name_col: column whose value labels each segment; defaults to the segment
            id (the index). ``Combined`` from ``io.load_metadata`` is a good pick.
        color_by: column to colour by. ``None`` -> every segment
            ``default_color``. A non-numeric column -> a categorical palette (one
            colour per distinct value, e.g. ``Direction``). A numeric column -> a
            continuous ``ramp`` over its min..max (the color-by-metric case).
        default_color: colour for the no-``color_by`` case and NaN metric values.
            A named colour, ``#rrggbb``, ``(r, g, b)``, or KML ``aabbggrr``.
        palette: categorical override — a list of colours to cycle, or a
            ``{value: colour}`` dict for explicit control.
        ramp: numeric ramp as ``((r, g, b), (r, g, b))`` low/high endpoints.
        document_name: KML ``<Document>`` name.

    Returns:
        ``out_path`` as a :class:`pathlib.Path`.
    """
    geo = geo.reset_index() if geo.index.name == SEGMENT_COL else geo.copy()
    if SEGMENT_COL not in geo.columns:
        raise ValueError(f"geo must carry {SEGMENT_COL!r} (as index or column)")
    geo = geo.set_index(SEGMENT_COL)

    colors, legend = _segment_colors(geo, color_by, default_color, palette, ramp)

    kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    document = ET.SubElement(kml, "Document")
    ET.SubElement(document, "name").text = document_name

    # One shared <Style> per distinct colour, referenced by styleUrl.
    style_ids = {}
    for kml_color in dict.fromkeys(colors.values()):
        style_id = f"line_{kml_color}"
        style_ids[kml_color] = style_id
        _add_line_style(document, style_id, kml_color, label_segments)

    if legend:
        _add_legend(document, legend)

    for sid, row in geo.iterrows():
        geom = row["geometry"]
        if geom is None or geom.is_empty:
            continue
        label = str(row[name_col]) if name_col and name_col in geo.columns else str(sid)
        kml_color = colors[sid]

        placemark = ET.SubElement(document, "Placemark")
        ET.SubElement(placemark, "name").text = label
        desc = f"Segment ID: {sid}"
        if "source" in geo.columns:
            desc += f"<br>Geometry: {row['source']}"
        if color_by and color_by in geo.columns:
            desc += f"<br>{color_by}: {row[color_by]}"
        ET.SubElement(placemark, "description").text = desc
        ET.SubElement(placemark, "styleUrl").text = f"#{style_ids[kml_color]}"

        # MultiGeometry: the polyline + a hidden-pin point that carries the label
        # (a Point at the line midpoint so an always-on label sits on the road).
        multigeom = ET.SubElement(placemark, "MultiGeometry")
        linestring = ET.SubElement(multigeom, "LineString")
        ET.SubElement(linestring, "tessellate").text = "1"
        ET.SubElement(linestring, "coordinates").text = _coord_string(geom)
        midpoint = geom.interpolate(0.5, normalized=True)
        point = ET.SubElement(multigeom, "Point")
        ET.SubElement(point, "coordinates").text = f"{midpoint.x},{midpoint.y},0"

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(kml).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def _add_legend(document, legend):
    """A text-only floating legend (ScreenOverlay) built from the colour map."""
    rows = "".join(
        # KML aabbggrr -> css #rrggbb for the legend swatch text
        f"<font color='#{kml[6:8]}{kml[4:6]}{kml[2:4]}'>&#9632;</font> {text}<br>"
        for text, kml in legend
    )
    overlay = ET.SubElement(document, "ScreenOverlay")
    ET.SubElement(overlay, "name").text = "Legend"
    icon = ET.SubElement(overlay, "Icon")
    ET.SubElement(icon, "href").text = ""
    ET.SubElement(overlay, "description").text = (
        "<div style='background-color:rgba(255,255,255,0.85);"
        "border:1px solid #000;padding:5px;font-size:12px;'>"
        f"<b>Legend</b><br>{rows}</div>"
    )
    ET.SubElement(overlay, "overlayXY", x="0", y="1", xunits="fraction", yunits="fraction")
    ET.SubElement(overlay, "screenXY", x="0.05", y="0.95", xunits="fraction", yunits="fraction")
    ET.SubElement(overlay, "size", x="0", y="0", xunits="pixels", yunits="pixels")
