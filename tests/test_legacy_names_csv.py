"""Tests for the retired names-CSV round-trip (``legacy/names_csv.py``).

The CSV workflow was superseded by names-in-the-DuckDB-store (ROADMAP Item 27); the
functions live in ``legacy/`` for reference and are exercised here so the old format
still reads/writes if a legacy CSV turns up. ``apply_names`` (the live resolver) stays
in ``inrix_tools.names`` and is source-agnostic.
"""
import pandas as pd
import pytest

from inrix_tools import names
from inrix_tools.io import SEGMENT_COL
from legacy import names_csv

REPRESENTATIVE = pd.DataFrame(
    {"Road": ["N 9th St", "S 9th St"],
     "Direction": ["S", "S"],
     "Intersection": ["9th St / Idaho St", "9th St"],
     "Combined": ["N 9th St S 9th St / Idaho St", "S 9th St S 9th St"]},
    index=pd.Index([440882720, 1187539993], name=SEGMENT_COL))


def test_write_and_load_round_trip(tmp_path):
    path = tmp_path / "segment_names.csv"
    written = names_csv.write_names_template(REPRESENTATIVE, path)
    assert written == path and path.exists()

    loaded = names_csv.load_names(path)
    assert loaded.index.name == SEGMENT_COL
    assert loaded.index.dtype == "int64"
    assert loaded.loc[440882720, names.NAME_COL] == "9th St & Idaho St"


def test_load_names_requires_columns(tmp_path):
    bad = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1], "bar": [2]}).to_csv(bad, index=False)
    with pytest.raises(ValueError, match="must have"):
        names_csv.load_names(bad)


def test_write_names_round_trips_edited_table(tmp_path):
    """The GUI segment table's edited names persist through write_names and read
    back verbatim via load_names (the retired in-app editor path)."""
    edited = pd.DataFrame({
        SEGMENT_COL: [440882720, 1187539993],
        names.INRIX_LABEL_COL: ["N 9th St S 9th St / Idaho St", "S 9th St S 9th St"],
        names.NAME_COL: ["Idaho @ 9th (edited)", "  9th St  "],   # trimmed on write
    })
    path = names_csv.write_names(edited, tmp_path / "segment_names.csv")
    assert path.exists()
    loaded = names_csv.load_names(path)
    assert loaded.loc[440882720, names.NAME_COL] == "Idaho @ 9th (edited)"
    assert loaded.loc[1187539993, names.NAME_COL] == "9th St"       # whitespace stripped
    # the resolved mapping picks up the edited name over the seed.
    mapping = names.apply_names(REPRESENTATIVE, loaded)
    assert mapping[440882720] == "Idaho @ 9th (edited)"


def test_write_names_requires_columns(tmp_path):
    with pytest.raises(ValueError, match="must have"):
        names_csv.write_names(pd.DataFrame({"foo": [1]}), tmp_path / "x.csv")


def test_write_names_optional_inrix_label(tmp_path):
    """inrix_label is optional on input; a Segment ID + name table still round-trips."""
    minimal = pd.DataFrame({SEGMENT_COL: [7], names.NAME_COL: ["Seven"]})
    loaded = names_csv.load_names(names_csv.write_names(minimal, tmp_path / "m.csv"))
    assert loaded.loc[7, names.NAME_COL] == "Seven"
    assert loaded.loc[7, names.INRIX_LABEL_COL] == ""


def test_load_names_keeps_na_like_strings(tmp_path):
    """A road genuinely named "NA" (or "None"/"null") is a name, not a missing
    value — pandas' default NA sentinels must not silently blank it back to the
    seed."""
    p = tmp_path / "na.csv"
    pd.DataFrame({SEGMENT_COL: [101], "name": ["NA"]}).to_csv(p, index=False)
    loaded = names_csv.load_names(p)
    assert loaded.loc[101, names.NAME_COL] == "NA"
    meta = pd.DataFrame(
        {"Road": ["N 9th St"], "Intersection": ["Idaho St"], "Combined": ["x"]},
        index=pd.Index([101], name=SEGMENT_COL))
    assert names.apply_names(meta, loaded)[101] == "NA"
