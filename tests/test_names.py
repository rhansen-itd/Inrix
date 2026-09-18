"""Tests for inrix_tools.names (ROADMAP Item 10).

Covers the seed simplifier and the ``apply_names`` resolver. The CSV round-trip that
used to live here retired to ``legacy/names_csv.py`` (Item 27 moved names into the
DuckDB store) — its tests are in ``tests/test_legacy_names_csv.py``. ``apply_names`` is
source-agnostic, so these build the override frame directly (indexed by Segment ID).
"""
from pathlib import Path

import pandas as pd
import pytest

from inrix_tools import io, names
from inrix_tools.io import SEGMENT_COL

REPO_ROOT = Path(__file__).resolve().parents[1]
MYRTLE_ZIP = REPO_ROOT / "Myrtle_2026-02-01_to_2026-07-16_5_min_part_1.zip"


# --- synthetic metadata (indexed by Segment ID, like io.load_metadata) -------
def _meta(rows) -> pd.DataFrame:
    """rows: list of (segment_id, road, direction, intersection)."""
    df = pd.DataFrame(rows, columns=[SEGMENT_COL, "Road", "Direction", "Intersection"])
    label_cols = ["Road", "Direction", "Intersection"]
    parts = df[label_cols].fillna("").astype(str)
    df["Combined"] = parts.agg(" ".join, axis=1).str.strip().str.replace(
        r"\s+", " ", regex=True)
    return df.set_index(SEGMENT_COL)


REPRESENTATIVE = _meta([
    (440882720, "N 9th St", "S", "9th St / Idaho St"),          # -> 9th St & Idaho St
    (1187539993, "S 9th St", "S", "9th St"),                    # road repeats -> 9th St
    (448695937, "S 9th St", "S", "US-20 Myrtle St / 9th St"),   # route-strip + dedup
    (448695927, "20 / W Myrtle St", "E", "US-20 Myrtle St / Capitol Blvd"),
    (119675609, "S 5th St", "N", None),                         # no intersection
])


def _overrides(mapping: dict[int, str]) -> pd.DataFrame:
    """A Segment-ID-indexed override frame (``name`` column) — the shape
    ``store.load_names`` returns and ``apply_names`` layers over the seed."""
    return pd.DataFrame({names.NAME_COL: list(mapping.values())},
                        index=pd.Index(list(mapping), name=SEGMENT_COL))


# --- seed simplification ----------------------------------------------------
def test_simplify_canonical_9th_idaho():
    # the ROADMAP's worked example
    assert names.simplify_label("N 9th St", "S", "9th St / Idaho St") == "9th St & Idaho St"


@pytest.mark.parametrize("road, direction, intersection, expected", [
    ("S 9th St", "S", "9th St", "9th St"),                                 # road repeats
    ("S 9th St", "S", "US-20 Myrtle St / 9th St", "9th St & Myrtle St"),   # route strip + dedup
    ("20 / W Myrtle St", "E", "US-20 Myrtle St / Capitol Blvd", "Myrtle St & Capitol Blvd"),
    ("N Capitol Blvd", "N", "Main St / Capitol Blvd", "Capitol Blvd & Main St"),  # cross first
    ("S 5th St", "N", None, "5th St"),                                     # no intersection
    ("184 / I-184 E", "E", "I-184 / Americana Blvd", "I-184 & Americana Blvd"),
])
def test_simplify_representative(road, direction, intersection, expected):
    assert names.simplify_label(road, direction, intersection) == expected


def test_simplify_empty_when_nothing_recoverable():
    assert names.simplify_label(None, None, None) == ""
    assert names.simplify_label(float("nan"), "S", float("nan")) == ""


def test_seed_names_columns_and_values():
    seed = names.seed_names(REPRESENTATIVE)
    assert list(seed.columns) == [SEGMENT_COL, names.INRIX_LABEL_COL, names.NAME_COL]
    by_id = seed.set_index(SEGMENT_COL)
    assert by_id.loc[440882720, names.NAME_COL] == "9th St & Idaho St"
    # inrix_label preserves the raw Combined string
    assert by_id.loc[440882720, names.INRIX_LABEL_COL] == "N 9th St S 9th St / Idaho St"


def test_seed_name_falls_back_to_segment_id_when_unrecoverable():
    meta = _meta([(555, None, None, None)])
    seed = names.seed_names(meta).set_index(SEGMENT_COL)
    # no road, no intersection, no Combined content -> Segment ID as a last resort
    assert seed.loc[555, names.NAME_COL] == "555"


# --- apply_names: user override over seed, with fallbacks -------------------
def test_apply_names_seed_only():
    mapping = names.apply_names(REPRESENTATIVE)
    assert mapping[440882720] == "9th St & Idaho St"
    assert mapping[1187539993] == "9th St"
    # every segment is present in the mapping
    assert set(mapping) == {int(s) for s in REPRESENTATIVE.index}


def test_apply_names_user_override_wins():
    # 2nd is blank -> apply_names treats it as unset and falls back to the seed.
    over = _overrides({440882720: "Idaho @ 9th (my label)", 1187539993: "   "})
    mapping = names.apply_names(REPRESENTATIVE, over)
    assert mapping[440882720] == "Idaho @ 9th (my label)"     # override wins
    assert mapping[1187539993] == "9th St"                    # blank -> seed fallback


def test_apply_names_ignores_unknown_segment_rows():
    over = _overrides({999999: "ghost"})
    mapping = names.apply_names(REPRESENTATIVE, over)
    assert 999999 not in mapping
    assert mapping[440882720] == "9th St & Idaho St"          # untouched


# --- real Myrtle fixture (self-skipping) ------------------------------------
@pytest.mark.skipif(not MYRTLE_ZIP.exists(), reason="Myrtle fixture not present")
def test_seed_on_real_myrtle_export():
    meta = io.load_metadata(MYRTLE_ZIP)
    seed = names.seed_names(meta).set_index(SEGMENT_COL)
    # every segment gets a non-empty name; none leaks a raw route/US- prefix
    assert (seed[names.NAME_COL].str.len() > 0).all()
    assert not seed[names.NAME_COL].str.contains(r"US-\d+ ", regex=True).any()
    # spot-check the documented case survives the real pipeline
    assert seed.loc[440882720, names.NAME_COL] == "9th St & Idaho St"
