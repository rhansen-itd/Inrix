"""Friendly, user-controlled segment names.  (ROADMAP Item 10)

Segments are otherwise labelled by the raw INRIX ``Combined`` string (Road +
Direction + Intersection, e.g. ``N 9th St S 9th St / Idaho St``) everywhere they
appear — the dropdown, map hover, forest rows, panel titles. That string is
accurate but noisy: the road repeats, the intersection carries route
designations, and the leading cardinal direction rarely helps a reader.

This module gives each segment a **readable name**, driven by a user-editable
CSV rather than an opaque auto-ID:

- ``seed_names`` turns the INRIX labels into a simplified starting point
  (``N 9th St S 9th St / Idaho St`` → ``9th St & Idaho St``) — heuristic, not
  perfect; the point is a good hand-edit seed.
- ``write_names_template`` / ``load_names`` round-trip that seed to/from a CSV
  the user hand-edits (stable ``Segment ID`` key, ``name`` column authoritative).
- ``apply_names`` resolves the single ``Segment ID -> name`` mapping the GUI
  reads from, layering the user CSV over the seed with sensible fallbacks. The
  raw ``Combined`` label is kept alongside (the GUI shows it as a hover subtitle)
  so nothing is lost.

Pure Python — no plotting, no GUI, no hardcoded paths (see CLAUDE.md).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .io import SEGMENT_COL

# Output column names for the names table / CSV.
INRIX_LABEL_COL = "inrix_label"   # the raw Combined string, kept for reference
NAME_COL = "name"                 # the friendly, user-editable name

# Cardinal-direction tokens stripped from road / cross-street names. The bare
# INRIX ``Direction`` letter (``N``/``S``/``E``/``W``) is dropped from the
# simplified name — two opposite-direction segments at the same corner collapse
# to the same seed name, which the user disambiguates by hand-editing the CSV.
_DIR_TOKENS = {"N", "S", "E", "W", "NB", "SB", "EB", "WB",
               "NORTH", "SOUTH", "EAST", "WEST"}

# A route designation prefixing a street name in the Intersection field, e.g.
# ``US-20 Myrtle St`` -> ``Myrtle St``, ``SR-55 State St`` -> ``State St``. Only
# stripped when a street name follows (``I-184`` alone is left as the name).
_ROUTE_PREFIX = re.compile(r"^(?:US|I|SR|ID|SH|CR)-\d+\s+", re.IGNORECASE)


def _road_core(road) -> str:
    """The descriptive street name of a ``Road`` field, direction-stripped.

    ``N 9th St`` -> ``9th St``; ``20 / W Myrtle St`` -> ``Myrtle St`` (INRIX
    prefixes a route number with `` / ``, so keep the last part); ``184 / I-184
    E`` -> ``I-184`` (trailing direction dropped too).
    """
    if road is None or (isinstance(road, float) and pd.isna(road)):
        return ""
    road = str(road).strip()
    if not road or road.lower() == "nan":
        return ""
    if " / " in road:            # "<route#> / <name>" -> keep the descriptive tail
        road = road.split(" / ")[-1].strip()
    toks = road.split()
    if toks and toks[0].upper() in _DIR_TOKENS:      # leading "N 9th St"
        toks = toks[1:]
    if toks and toks[-1].upper() in _DIR_TOKENS:     # trailing "I-184 E"
        toks = toks[:-1]
    return " ".join(toks).strip()


def _clean_cross(token: str) -> str:
    """Tidy one intersection cross-street token: drop a route-number prefix so
    ``US-20 Myrtle St`` reads as ``Myrtle St``."""
    return _ROUTE_PREFIX.sub("", token.strip()).strip()


def simplify_label(road, direction, intersection) -> str:
    """Simplify one (Road, Direction, Intersection) triple to a friendly name.

    Reduces ``road`` to its core street name, splits ``intersection`` on `` / ``
    into cross streets, drops the token(s) that merely repeat the road, and joins
    the rest as ``<road> & <cross> [& <cross>...]``:

        ``N 9th St`` / ``S`` / ``9th St / Idaho St``  -> ``9th St & Idaho St``
        ``S 9th St`` / ``S`` / ``9th St``             -> ``9th St``
        ``20 / W Myrtle St`` / ``E`` / ``US-20 Myrtle St / Capitol Blvd``
                                                      -> ``Myrtle St & Capitol Blvd``

    Heuristic, not perfect — a hand-edit starting point. Returns ``""`` only when
    neither a road core nor any cross street can be recovered (caller falls back
    to the raw label / Segment ID).
    """
    core = _road_core(road)

    tokens: list[str] = []
    if intersection is not None and not (isinstance(intersection, float) and pd.isna(intersection)):
        raw = str(intersection).strip()
        if raw and raw.lower() != "nan":
            tokens = [t for t in (_clean_cross(p) for p in raw.split(" / ")) if t]

    core_l = core.lower()
    cross: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        tl = t.lower()
        # skip a token that repeats the road (equal, or the road name is inside it,
        # e.g. core "Myrtle St" inside "US-20 Myrtle St" once route-stripped).
        if core_l and (tl == core_l or core_l in tl):
            continue
        if tl in seen:           # de-dup repeated cross streets
            continue
        seen.add(tl)
        cross.append(t)

    if core and cross:
        return core + " & " + " & ".join(cross)
    if core:
        return core
    if cross:
        return " & ".join(cross)
    return ""


def seed_names(metadata: pd.DataFrame) -> pd.DataFrame:
    """Seed a friendly-name table from INRIX ``metadata`` (indexed by
    ``Segment ID``, as ``io.load_metadata`` returns it).

    Returns a DataFrame with columns ``Segment ID``, ``inrix_label`` (the raw
    ``Combined`` string, or empty if absent), and ``name`` (the simplified seed,
    falling back to ``inrix_label`` then ``Segment ID`` when the simplifier can't
    recover a name). This is the hand-edit starting point ``write_names_template``
    writes and ``apply_names`` layers a user CSV over.
    """
    road = metadata["Road"] if "Road" in metadata.columns else None
    direction = metadata["Direction"] if "Direction" in metadata.columns else None
    intersection = metadata["Intersection"] if "Intersection" in metadata.columns else None
    combined = metadata["Combined"] if "Combined" in metadata.columns else None

    rows = []
    for sid in metadata.index:
        r = road.loc[sid] if road is not None else None
        d = direction.loc[sid] if direction is not None else None
        i = intersection.loc[sid] if intersection is not None else None
        label = "" if combined is None else str(combined.loc[sid])
        if label.lower() == "nan":
            label = ""
        name = simplify_label(r, d, i)
        if not name:                       # simplifier gave nothing -> keep a label
            name = label or str(int(sid))
        rows.append({SEGMENT_COL: int(sid), INRIX_LABEL_COL: label, NAME_COL: name})

    return pd.DataFrame(rows, columns=[SEGMENT_COL, INRIX_LABEL_COL, NAME_COL])


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


def apply_names(metadata: pd.DataFrame, names: pd.DataFrame | None = None) -> dict[int, str]:
    """Resolve the single ``Segment ID -> name`` mapping the GUI labels read from.

    Layers, per segment, in priority order:
      1. the user CSV ``name`` (``names``, from ``load_names``) when non-blank,
      2. the simplified ``seed_names`` name,
      3. the raw ``Combined`` label,
      4. ``str(Segment ID)``.

    ``names`` rows for segments not in ``metadata`` are ignored; segments absent
    from ``names`` (or with a blank name there) fall through to the seed. This is
    the single source of truth that replaces the ad-hoc ``Combined`` dict the GUI
    used before.
    """
    seed = seed_names(metadata).set_index(SEGMENT_COL)
    overrides = {}
    if names is not None:
        col = names[NAME_COL].fillna("").astype(str).str.strip()
        overrides = {int(sid): v for sid, v in col.items() if v}

    mapping: dict[int, str] = {}
    for sid in seed.index:
        sid = int(sid)
        if sid in overrides:
            mapping[sid] = overrides[sid]
        else:
            mapping[sid] = str(seed.loc[sid, NAME_COL])
    return mapping
