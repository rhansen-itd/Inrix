"""Tests for inrix_tools.names (ROADMAP Item 10)."""
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


# --- CSV round-trip ---------------------------------------------------------
def test_write_and_load_round_trip(tmp_path):
    path = tmp_path / "segment_names.csv"
    written = names.write_names_template(REPRESENTATIVE, path)
    assert written == path and path.exists()

    loaded = names.load_names(path)
    assert loaded.index.name == SEGMENT_COL
    assert loaded.index.dtype == "int64"
    assert loaded.loc[440882720, names.NAME_COL] == "9th St & Idaho St"


def test_load_names_requires_columns(tmp_path):
    bad = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1], "bar": [2]}).to_csv(bad, index=False)
    with pytest.raises(ValueError, match="must have"):
        names.load_names(bad)


# --- write_names: the in-app editor round-trip (Item 19) --------------------
def test_write_names_round_trips_edited_table(tmp_path):
    """The GUI segment table's edited names persist through write_names and read
    back verbatim via load_names (the in-app editor supersedes hand-editing)."""
    edited = pd.DataFrame({
        SEGMENT_COL: [440882720, 1187539993],
        names.INRIX_LABEL_COL: ["N 9th St S 9th St / Idaho St", "S 9th St S 9th St"],
        names.NAME_COL: ["Idaho @ 9th (edited)", "  9th St  "],   # trimmed on write
    })
    path = names.write_names(edited, tmp_path / "segment_names.csv")
    assert path.exists()
    loaded = names.load_names(path)
    assert loaded.loc[440882720, names.NAME_COL] == "Idaho @ 9th (edited)"
    assert loaded.loc[1187539993, names.NAME_COL] == "9th St"       # whitespace stripped
    # the resolved mapping picks up the edited name over the seed.
    mapping = names.apply_names(REPRESENTATIVE, loaded)
    assert mapping[440882720] == "Idaho @ 9th (edited)"


def test_write_names_requires_columns(tmp_path):
    with pytest.raises(ValueError, match="must have"):
        names.write_names(pd.DataFrame({"foo": [1]}), tmp_path / "x.csv")


def test_write_names_optional_inrix_label(tmp_path):
    """inrix_label is optional on input; a Segment ID + name table still round-trips."""
    minimal = pd.DataFrame({SEGMENT_COL: [7], names.NAME_COL: ["Seven"]})
    loaded = names.load_names(names.write_names(minimal, tmp_path / "m.csv"))
    assert loaded.loc[7, names.NAME_COL] == "Seven"
    assert loaded.loc[7, names.INRIX_LABEL_COL] == ""


# --- apply_names: user CSV over seed, with fallbacks ------------------------
def test_apply_names_seed_only():
    mapping = names.apply_names(REPRESENTATIVE)
    assert mapping[440882720] == "9th St & Idaho St"
    assert mapping[1187539993] == "9th St"
    # every segment is present in the mapping
    assert set(mapping) == {int(s) for s in REPRESENTATIVE.index}


def test_apply_names_user_override_wins():
    user = pd.DataFrame({
        SEGMENT_COL: [440882720, 1187539993],
        names.NAME_COL: ["Idaho @ 9th (my label)", "   "],  # 2nd is blank -> seed
    })
    mapping = names.apply_names(REPRESENTATIVE, names.load_names(_to_csv(user)))
    assert mapping[440882720] == "Idaho @ 9th (my label)"     # override wins
    assert mapping[1187539993] == "9th St"                    # blank -> seed fallback


def test_apply_names_ignores_unknown_segment_rows():
    user = pd.DataFrame({SEGMENT_COL: [999999], names.NAME_COL: ["ghost"]})
    mapping = names.apply_names(REPRESENTATIVE, names.load_names(_to_csv(user)))
    assert 999999 not in mapping
    assert mapping[440882720] == "9th St & Idaho St"          # untouched


# helper: materialize a DataFrame to a temp CSV and load it back through the API
_TMP = {"n": 0}


def _to_csv(df: pd.DataFrame) -> Path:
    import tempfile
    _TMP["n"] += 1
    p = Path(tempfile.gettempdir()) / f"inrix_names_test_{_TMP['n']}.csv"
    df.to_csv(p, index=False)
    return p


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


def test_load_names_keeps_na_like_strings():
    """A road genuinely named "NA" (or "None"/"null") is a name, not a missing
    value — pandas' default NA sentinels must not silently blank it back to the
    seed."""
    import tempfile

    p = Path(tempfile.gettempdir()) / "inrix_names_test_na.csv"
    pd.DataFrame({SEGMENT_COL: [101], "name": ["NA"]}).to_csv(p, index=False)
    loaded = names.load_names(p)
    assert loaded.loc[101, names.NAME_COL] == "NA"
    meta = pd.DataFrame(
        {"Road": ["N 9th St"], "Intersection": ["Idaho St"], "Combined": ["x"]},
        index=pd.Index([101], name=SEGMENT_COL))
    assert names.apply_names(meta, loaded)[101] == "NA"
    p.unlink()
