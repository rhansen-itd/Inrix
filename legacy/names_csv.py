"""Retired CSV round-trip for friendly segment names (superseded by ROADMAP Item 27).

Originally the friendly names (Item 10 / Item 19) lived in a user-editable CSV: the
GUI wrote a seed template, the user hand-edited it (or edited names in the in-app
table), the path went into a "Names CSV" box, and a reload layered the CSV over the
seed. Item 27 moved names into the DuckDB store (``store.save_names`` /
``store.load_names``, last-write-wins, applied automatically on load), retiring the
CSV workflow and its silent-loss failure mode (a blank Names-CSV path dropped edits).

These three functions are kept here — unchanged, un-imported by the app — so the old
format can still be read/written if a legacy CSV turns up. The name **seed** logic
(``names.seed_names`` / ``names.simplify_label``) and the resolver
(``names.apply_names``) stay in :mod:`inrix_tools.names`, since the DB path reuses
them; only the CSV writers/loader moved.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from inrix_tools.io import SEGMENT_COL
from inrix_tools.names import INRIX_LABEL_COL, NAME_COL, seed_names


def write_names_template(metadata: pd.DataFrame, path) -> Path:
    """Write the ``seed_names`` table to ``path`` as a CSV for the user to edit.

    Overwrites ``path`` (a fresh template mirrors the current export's segments).
    Returns the written path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    seed_names(metadata).to_csv(path, index=False)
    return path


def write_names(names_df: pd.DataFrame, path) -> Path:
    """Write an edited ``Segment ID -> name`` table to ``path`` as a names CSV that
    ``load_names`` reads back verbatim (ROADMAP Item 19's in-app editor path).

    Unlike ``write_names_template`` (which regenerates the *seed* from metadata),
    this persists names the user has already edited — e.g. the rows of the GUI
    segment table. ``names_df`` needs a ``Segment ID`` and a ``name`` column
    (``inrix_label`` optional, blanked if absent); the output column order matches
    the template so the store stays a single portable format. Returns the path.
    """
    if SEGMENT_COL not in names_df.columns or NAME_COL not in names_df.columns:
        raise ValueError(
            f"names_df must have '{SEGMENT_COL}' and '{NAME_COL}' columns; "
            f"got {list(names_df.columns)}"
        )
    out = pd.DataFrame({
        SEGMENT_COL: names_df[SEGMENT_COL].astype("int64"),
        INRIX_LABEL_COL: (names_df[INRIX_LABEL_COL].fillna("").astype(str)
                          if INRIX_LABEL_COL in names_df.columns else ""),
        NAME_COL: names_df[NAME_COL].fillna("").astype(str).str.strip(),
    })
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return path


def load_names(path) -> pd.DataFrame:
    """Read a names CSV written by ``write_names_template`` (or hand-authored).

    Requires a ``Segment ID`` column and a ``name`` column; ``inrix_label`` is
    optional. ``Segment ID`` is typed to int and set as the index. Blank/whitespace
    ``name`` cells become empty strings (``apply_names`` treats them as unset and
    falls back to the seed). No hardcoded paths — the caller supplies ``path``.
    """
    # keep_default_na=False: a road genuinely named "NA"/"None"/"null" is a
    # name, not a missing value — pandas' NA sentinels would silently blank it.
    df = pd.read_csv(path, keep_default_na=False)
    if SEGMENT_COL not in df.columns or NAME_COL not in df.columns:
        raise ValueError(
            f"names CSV {path} must have '{SEGMENT_COL}' and '{NAME_COL}' columns; "
            f"got {list(df.columns)}"
        )
    df[SEGMENT_COL] = df[SEGMENT_COL].astype("int64")
    df[NAME_COL] = df[NAME_COL].fillna("").astype(str).str.strip()
    if INRIX_LABEL_COL not in df.columns:
        df[INRIX_LABEL_COL] = ""
    return df.set_index(SEGMENT_COL)
