"""Segment metadata -> KML.  (ROADMAP Item 6 — stub)

Consolidates the two near-duplicate ``csv_to_kml`` versions in
``_metadata KML.ipynb`` into one clean function that consumes the typed metadata
from ``io.load_metadata``.
"""
from __future__ import annotations

_ITEM = "ROADMAP Item 6 (kml.py)"


def metadata_to_kml(metadata, out_path, label_segments=False,
                    colors=("blue", "red")):
    """Write a KML drawing each segment as a colored line from its start/end
    lat-long, with optional always-visible labels and hidden pin icons."""
    raise NotImplementedError(f"Not built yet — see {_ITEM}.")
