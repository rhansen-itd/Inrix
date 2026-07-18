"""Database-backed storage & ingest for INRIX exports.  (ROADMAP Items 21, 23)

A persistent local **DuckDB** store, organized around a persistent **area** — a
group of corridors — rather than one silo per export (the Item 23 redesign):

- An export's **area** is derived from the set of ``Corridor/Region Name`` values it
  carries (same corridor set → same area). Ingesting several exports of the same
  corridors **merges** them into one growing area, so a session picks an *area* (not
  a file) and sees everything ever ingested for it.
- Rows are **de-duplicated keep-first** on ``(Segment ID, Date Time, bin_minutes)``:
  re-ingesting an overlapping export is idempotent, and a later export's value never
  overwrites one already stored (the owner's choice).
- Different **time bin-lengths** (5-min / 15-min / hourly) coexist in one area,
  partitioned by an auto-detected ``bin_minutes`` column; the GUI selects the bin.
- Segment **metadata** and the processed **geometry + AADT join** (Item 18) are
  merged per area (keep-first per ``Segment ID``) and **persist** with the area, so
  the expensive spatial join runs once and is reused across every later export.

Pure core, per CLAUDE.md: **no GUI imports, no hardcoded paths** (the DB path /
connection is always a parameter). The file loaders (:mod:`inrix_tools.io`,
:mod:`inrix_tools.geometry`, :mod:`inrix_tools.aadt`) keep working unchanged — the
DB is *optional*, an accelerator, not a new requirement.

Why DuckDB (decision recorded 2026-07-17, DESIGN_HISTORY Session 26)
-------------------------------------------------------------------
**DuckDB**, not SQLite: already a transitive dependency (via ``traffic-anomaly``'s
``ibis-framework[duckdb]``), and its columnar engine fits the analytic scans of a
~2M-row export. The DuckDB ``spatial`` extension is **not** used — the GIS layers
are a small per-segment table, so geometry is serialized as **WKB blobs** and
rehydrated with :mod:`shapely`; no in-DB spatial predicates, so ``connect`` stays
offline and the schema is portable.

Round-trip fidelity (see the tests): ``io.load_data`` returns ``Date Time`` as
tz-aware **UTC** (``datetime64[ns, UTC]``); DuckDB stores ``TIMESTAMPTZ`` and reads
it back in the *session* zone at μs resolution, so ``connect`` pins ``SET
TimeZone='UTC'`` and :func:`load_export` normalizes back to ``datetime64[ns, UTC]``.

Schema + versioning live in DATA_FORMAT.md; :data:`SCHEMA_VERSION` is stamped on
every area row. The store is a *cache*: the migration policy is **re-ingest** (bump
the version, drop the file, re-run), not an in-place migrator.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from . import io as _io
from .geometry import WGS84
from .io import CORRIDOR_COL, DATETIME_COL, SEGMENT_COL

# Bump when the on-disk layout changes; stamped on every area row. v2 = the Item 23
# area/merge model (v1 was the Item 21 one-dataset-per-export layout).
SCHEMA_VERSION = 2

AREAS_TABLE = "_areas"                # area registry (one row per corridor-group)
INGESTS_TABLE = "_ingests"           # provenance: one row per ingested export
GEOM_WKB_COL = "_geom_wkb"           # BLOB column holding each segment's WKB
BIN_COL = "bin_minutes"              # time-bin partition column on the obs table

# Columns the AADT join contributes to the cached geo layer (Item 18).
_AADT_JOIN_COLS = ("AADT", "aadt_source", "aadt_dist_m", "Route", "Commercial")


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------
def connect(db_path, *, read_only: bool = False):
    """Open (or create) the DuckDB store at ``db_path`` and return the connection.

    ``db_path`` is a **parameter** — the module never hardcodes a location. Use
    ``":memory:"`` for a throwaway/test store. The session timezone is pinned to UTC
    (so ``TIMESTAMPTZ`` reads back as UTC); the area + ingest registries are created
    if absent.
    """
    import duckdb

    con = duckdb.connect(str(db_path), read_only=read_only)
    con.execute("SET TimeZone='UTC'")
    if not read_only:
        _ensure_registry(con)
    return con


def _ensure_registry(con) -> None:
    """Create the area + ingest registry tables if they do not yet exist."""
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{AREAS_TABLE}" (
            area_key       VARCHAR PRIMARY KEY,
            area_name      VARCHAR,
            corridors      VARCHAR,          -- JSON list of Corridor/Region Name
            units_speed    VARCHAR,
            units_travel_time VARCHAR,
            n_segments     BIGINT,
            date_min       TIMESTAMP WITH TIME ZONE,
            date_max       TIMESTAMP WITH TIME ZONE,
            has_geometry   BOOLEAN,
            has_aadt       BOOLEAN,
            schema_version INTEGER,
            created_at     TIMESTAMP,
            updated_at     TIMESTAMP
        )
        """
    )
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{INGESTS_TABLE}" (
            area_key     VARCHAR,
            source       VARCHAR,
            bin_minutes  INTEGER,
            n_rows_added BIGINT,
            date_min     TIMESTAMP WITH TIME ZONE,
            date_max     TIMESTAMP WITH TIME ZONE,
            ingested_at  TIMESTAMP
        )
        """
    )


def _obs_table(key: str) -> str:
    return f"obs_{key}"


def _meta_table(key: str) -> str:
    return f"meta_{key}"


def _geo_table(key: str) -> str:
    return f"geo_{key}"


def _table_exists(con, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# Area identity + bin detection
# ---------------------------------------------------------------------------
def area_identity(df: pd.DataFrame) -> tuple[str, str, list[str]]:
    """Derive an export's persistent **area** from its corridor set.

    The area is the sorted set of distinct ``Corridor/Region Name`` values — so two
    exports covering the same corridors resolve to the **same** ``area_key`` and
    merge. Returns ``(area_key, area_name, corridors)``. When the export carries no
    corridor column/values, the area falls back to the sorted **Segment ID set**
    (still stable across re-exports of the same segments), named ``"segments(N)"``.
    """
    corridors: list[str] = []
    if CORRIDOR_COL in df.columns:
        corridors = sorted({str(c) for c in df[CORRIDOR_COL].dropna().unique()})
    if corridors:
        canon = "corridors:" + "|".join(corridors)
        name = ", ".join(corridors)
    else:
        seg_ids = sorted({int(s) for s in df[SEGMENT_COL].dropna().unique()}) \
            if SEGMENT_COL in df.columns else []
        canon = "segments:" + ",".join(map(str, seg_ids))
        name = f"segments({len(seg_ids)})"
    key = hashlib.md5(canon.encode("utf-8")).hexdigest()[:12]
    return key, name, corridors


def detect_bin_minutes(df: pd.DataFrame) -> int | None:
    """Auto-detect the export's time bin-length (minutes) from the **modal**
    consecutive-``Date Time`` spacing within each segment.

    5-min INRIX data → ``5``; a 15-min export → ``15``. Robust to gaps (the mode,
    not the mean) and to a mixed frame (one segment's regular cadence dominates).
    ``None`` when the frame is too small to have a spacing (e.g. one row/segment).
    """
    if DATETIME_COL not in df.columns or SEGMENT_COL not in df.columns:
        return None
    work = df[[SEGMENT_COL, DATETIME_COL]].dropna()
    if len(work) < 2:
        return None
    work = work.sort_values([SEGMENT_COL, DATETIME_COL])
    deltas = work.groupby(SEGMENT_COL, observed=True)[DATETIME_COL].diff().dropna()
    secs = deltas.dt.total_seconds()
    secs = secs[secs > 0]
    if secs.empty:
        return None
    return int(round(float(secs.mode().iloc[0]) / 60.0))


# ---------------------------------------------------------------------------
# Ingest (merge into an area)
# ---------------------------------------------------------------------------
def ingest_export(con, source, *, ingested_at: datetime | None = None) -> dict:
    """Ingest an INRIX export (``source``) — merging it into its corridor **area**.

    Reads with the file loaders (:func:`io.load_data` / :func:`io.load_metadata`),
    derives the area from the corridor set and the bin-length from the timestamp
    spacing, then merges the rows **keep-first** into the area's tables. Returns a
    summary dict: ``area_key`` / ``area_name`` / ``bin_minutes`` / ``n_rows_added``.
    """
    df = _io.load_data(source)
    metadata = _io.load_metadata(source)
    return put_export(con, df, metadata, source=str(source), ingested_at=ingested_at)


def put_export(con, df: pd.DataFrame, metadata: pd.DataFrame, *,
               source: str | None = None, ingested_at: datetime | None = None) -> dict:
    """Merge an already-loaded export frame + metadata into its area.

    The lower-level entry behind :func:`ingest_export` — the GUI already holds the
    frames (it builds them for the geometry join), so it calls this directly. New
    rows are those whose ``(Segment ID, Date Time, bin_minutes)`` are not already in
    the area (keep-first); segment metadata is merged keep-first per ``Segment ID``.
    """
    _ensure_registry(con)
    area_key, area_name, corridors = area_identity(df)
    bin_minutes = detect_bin_minutes(df)
    obs = _obs_table(area_key)

    frame = df.copy()
    frame[BIN_COL] = bin_minutes
    n_added = _merge_frame(con, obs, frame,
                           keys=[SEGMENT_COL, DATETIME_COL, BIN_COL])

    # Metadata: merge keep-first by Segment ID (union of segments seen in the area).
    meta_flat = metadata.reset_index() if metadata.index.name else metadata.copy()
    _merge_frame(con, _meta_table(area_key), meta_flat, keys=[SEGMENT_COL])

    _upsert_area(con, area_key, area_name, corridors, df, ingested_at)
    _log_ingest(con, area_key, source, bin_minutes, n_added, df, ingested_at)
    return {"area_key": area_key, "area_name": area_name,
            "bin_minutes": bin_minutes, "n_rows_added": n_added}


def _merge_frame(con, table: str, frame: pd.DataFrame, keys: list[str]) -> int:
    """Insert ``frame`` into ``table``, **keep-first** on ``keys`` (rows whose key
    tuple already exists are skipped). Creates the table on first write. Tolerates
    column drift between exports (missing columns fill NULL; new columns are added).
    Returns the number of rows actually inserted."""
    con.register("_merge_src", frame)
    try:
        if not _table_exists(con, table):
            con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM _merge_src')
            return len(frame)
        _align_columns(con, table, "_merge_src")
        on = " AND ".join(f'o."{k}" = n."{k}"' for k in keys)
        anti = (f'SELECT n.* FROM _merge_src n '
                f'LEFT JOIN "{table}" o ON {on} WHERE o."{keys[0]}" IS NULL')
        n_added = con.execute(f'SELECT count(*) FROM ({anti})').fetchone()[0]
        if n_added:
            con.execute(f'INSERT INTO "{table}" BY NAME {anti}')
        return int(n_added)
    finally:
        con.unregister("_merge_src")


def _align_columns(con, table: str, view: str) -> None:
    """Add any columns present in ``view`` but missing from ``table`` (so an export
    that carries an extra column merges instead of erroring)."""
    existing = {r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()}
    for row in con.execute(f'DESCRIBE "{view}"').fetchall():
        name, coltype = row[0], row[1]
        if name not in existing:
            con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {coltype}')


def _upsert_area(con, area_key, area_name, corridors, df, ingested_at) -> None:
    """Create/refresh the area registry row: units, span, and segment count are
    recomputed from the merged obs; ``created_at`` is preserved on update."""
    units = (df.attrs.get("units") or {}) if hasattr(df, "attrs") else {}
    obs = _obs_table(area_key)
    span = con.execute(
        f'SELECT min("{DATETIME_COL}"), max("{DATETIME_COL}"), '
        f'count(DISTINCT "{SEGMENT_COL}") FROM "{obs}"').fetchone()
    date_min, date_max, n_seg = span
    ts = ingested_at or datetime.now(timezone.utc)
    existing = con.execute(
        f'SELECT created_at, has_geometry, has_aadt FROM "{AREAS_TABLE}" '
        f'WHERE area_key = ?', [area_key]).fetchone()
    created_at = existing[0] if existing else ts
    has_geom = bool(existing[1]) if existing else False
    has_aadt = bool(existing[2]) if existing else False
    con.execute(f'DELETE FROM "{AREAS_TABLE}" WHERE area_key = ?', [area_key])
    con.execute(
        f'INSERT INTO "{AREAS_TABLE}" VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [area_key, area_name, json.dumps(corridors),
         units.get("speed"), units.get("travel_time"),
         int(n_seg), date_min, date_max, has_geom, has_aadt,
         SCHEMA_VERSION, created_at, ts])


def _log_ingest(con, area_key, source, bin_minutes, n_added, df, ingested_at) -> None:
    if DATETIME_COL in df.columns and len(df):
        d = df[DATETIME_COL]
        dmin, dmax = d.min(), d.max()
    else:
        dmin = dmax = None
    con.execute(
        f'INSERT INTO "{INGESTS_TABLE}" VALUES (?,?,?,?,?,?,?)',
        [area_key, source, bin_minutes, int(n_added), dmin, dmax,
         ingested_at or datetime.now(timezone.utc)])


def ingest_geometry(con, area_key: str, geo) -> str:
    """Merge the **processed** segment geometry (+ any AADT-join columns it carries)
    into an area — **keep-first** per ``Segment ID``, so a segment's geometry is
    cached once and reused across every later export of the area. Geometry is stored
    as WKB (``NULL`` for a ``missing`` segment)."""
    flat = _geo_to_frame(geo)
    _merge_frame(con, _geo_table(area_key), flat, keys=[SEGMENT_COL])
    has_aadt = any(c in geo.columns for c in _AADT_JOIN_COLS)
    con.execute(
        f'UPDATE "{AREAS_TABLE}" SET has_geometry = TRUE, '
        f'has_aadt = (has_aadt OR ?) WHERE area_key = ?', [bool(has_aadt), area_key])
    return area_key


def ingest_aadt(con, area_key: str, aadt_geo) -> str:
    """(Re)write the AADT-join columns onto the cached geo layer for an area. A
    companion to :func:`ingest_geometry` for when geometry was cached first and the
    AADT match is added later; only the join columns are updated (geometry kept)."""
    geo = load_geometry(con, area_key)
    if geo is None:
        raise ValueError(f"No geometry cached for area {area_key!r}; "
                         "call ingest_geometry first.")
    src = aadt_geo if aadt_geo.index.name == SEGMENT_COL else aadt_geo.set_index(SEGMENT_COL)
    for col in _AADT_JOIN_COLS:
        if col in src.columns:
            geo[col] = src[col].reindex(geo.index)
    con.execute(f'DROP TABLE IF EXISTS "{_geo_table(area_key)}"')
    con.execute(f'UPDATE "{AREAS_TABLE}" SET has_geometry = FALSE, has_aadt = FALSE '
                f'WHERE area_key = ?', [area_key])
    return ingest_geometry(con, area_key, geo)


def _geo_to_frame(geo) -> pd.DataFrame:
    """Flatten a GeoDataFrame (indexed by ``Segment ID``) into a plain DataFrame with
    a WKB blob column. Object columns carrying ``pd.NA`` are coerced to ``None``."""
    geom_name = geo.geometry.name
    out = pd.DataFrame({SEGMENT_COL: [int(s) for s in geo.index]})
    for col in geo.columns:
        if col == geom_name:
            continue
        s = geo[col]
        if s.dtype == object or str(s.dtype) == "string":
            s = s.where(s.notna(), None).astype(object)
        out[col] = s.to_numpy()
    out[GEOM_WKB_COL] = [
        g.wkb if (g is not None and not getattr(g, "is_empty", True)) else None
        for g in geo.geometry
    ]
    return out


# ---------------------------------------------------------------------------
# Query / load
# ---------------------------------------------------------------------------
def list_areas(con) -> pd.DataFrame:
    """The ingested areas as a DataFrame (most-recently-updated first).

    Columns: ``area_key``, ``area_name``, ``corridors`` (JSON), ``n_segments``,
    ``date_min``, ``date_max``, ``has_geometry``, ``has_aadt``, ``schema_version``,
    ``updated_at``, plus ``bins`` — the list of bin-lengths present in each area.
    """
    _ensure_registry(con)
    df = con.execute(
        f'SELECT area_key, area_name, corridors, n_segments, date_min, date_max, '
        f'has_geometry, has_aadt, schema_version, updated_at '
        f'FROM "{AREAS_TABLE}" ORDER BY updated_at DESC, area_name'
    ).df()
    df["bins"] = [area_bins(con, k) for k in df["area_key"]]
    return df


def area_names(con) -> list[tuple[str, str]]:
    """``[(area_key, area_name), ...]`` for a GUI dropdown (most-recent first)."""
    _ensure_registry(con)
    rows = con.execute(
        f'SELECT area_key, area_name FROM "{AREAS_TABLE}" '
        f'ORDER BY updated_at DESC, area_name').fetchall()
    return [(r[0], r[1]) for r in rows]


def area_bins(con, area_key: str) -> list[int]:
    """The time bin-lengths (minutes) present in an area, ascending."""
    obs = _obs_table(area_key)
    if not _table_exists(con, obs):
        return []
    rows = con.execute(
        f'SELECT DISTINCT "{BIN_COL}" FROM "{obs}" '
        f'WHERE "{BIN_COL}" IS NOT NULL ORDER BY 1').fetchall()
    return [int(r[0]) for r in rows]


def _area_row(con, area_key: str) -> dict | None:
    _ensure_registry(con)
    df = con.execute(
        f'SELECT * FROM "{AREAS_TABLE}" WHERE area_key = ?', [area_key]).df()
    return None if len(df) == 0 else df.iloc[0].to_dict()


def load_export(con, area_key: str, bin_minutes: int | None = None) -> pd.DataFrame:
    """Load an area's merged time-series frame for one bin-length — the same typed,
    tz-aware **UTC** shape :func:`io.load_data` produces (the ``bin_minutes``
    partition column is dropped and ``df.attrs['units']`` restored).

    ``bin_minutes`` selects the partition; if ``None`` and the area has exactly one
    bin, that one is used (else a :class:`ValueError` names the choices).
    """
    row = _area_row(con, area_key)
    if row is None:
        raise KeyError(f"No area {area_key!r} in the store.")
    bin_minutes = _resolve_bin(con, area_key, bin_minutes)
    obs = _obs_table(area_key)
    df = con.execute(
        f'SELECT * EXCLUDE ("{BIN_COL}") FROM "{obs}" WHERE "{BIN_COL}" = ?',
        [bin_minutes]).df()
    if DATETIME_COL in df.columns:
        df[DATETIME_COL] = (
            df[DATETIME_COL].dt.tz_convert("UTC").astype("datetime64[ns, UTC]"))
    df.attrs["units"] = {"speed": row.get("units_speed"),
                         "travel_time": row.get("units_travel_time")}
    return df


def _resolve_bin(con, area_key: str, bin_minutes: int | None) -> int:
    bins = area_bins(con, area_key)
    if bin_minutes is not None:
        if bin_minutes not in bins:
            raise ValueError(
                f"Area {area_key!r} has no {bin_minutes}-min data; bins: {bins}.")
        return bin_minutes
    if len(bins) == 1:
        return bins[0]
    raise ValueError(
        f"Area {area_key!r} holds multiple bins {bins}; pass bin_minutes.")


def load_metadata(con, area_key: str) -> pd.DataFrame:
    """Load an area's merged metadata frame — indexed by ``Segment ID``, the same
    shape :func:`io.load_metadata` produces (``Combined`` label included)."""
    if _area_row(con, area_key) is None:
        raise KeyError(f"No area {area_key!r} in the store.")
    dfm = con.execute(f'SELECT * FROM "{_meta_table(area_key)}"').df()
    if SEGMENT_COL in dfm.columns:
        dfm[SEGMENT_COL] = dfm[SEGMENT_COL].astype("int64")
        dfm = dfm.set_index(SEGMENT_COL)
    return dfm


def load_geometry(con, area_key: str):
    """Load an area's cached geometry layer as a GeoDataFrame indexed by
    ``Segment ID`` (WGS84), or ``None`` when no geometry was ingested. Carries the
    cached AADT-join columns, so the Item 18 join is **read**, not recomputed."""
    import geopandas as gpd
    from shapely import wkb

    row = _area_row(con, area_key)
    if row is None or not row.get("has_geometry"):
        return None
    geot = _geo_table(area_key)
    flat = con.execute(f'SELECT * FROM "{geot}"').df()
    # A missing segment's WKB is stored NULL and comes back as None / pd.NA.
    geoms = [wkb.loads(bytes(b)) if isinstance(b, (bytes, bytearray, memoryview))
             else None for b in flat[GEOM_WKB_COL]]
    sid = flat[SEGMENT_COL].astype("int64")
    attrs = flat.drop(columns=[SEGMENT_COL, GEOM_WKB_COL])
    geo = gpd.GeoDataFrame(attrs, geometry=geoms, crs=WGS84)
    geo.index = pd.Index(sid.to_numpy(), name=SEGMENT_COL)
    return geo


@dataclass
class StoredDataset:
    """The frames a session needs, read back from an area — the DB counterpart of
    what the file loaders produce (:func:`io.load_data` / :func:`io.load_metadata` /
    :func:`geometry.segment_geometry` + the cached AADT join), for one bin-length."""
    area_key: str
    area_name: str
    bin_minutes: int
    df: pd.DataFrame
    metadata: pd.DataFrame
    geo: object = None                 # GeoDataFrame or None
    units: dict = field(default_factory=dict)
    has_aadt: bool = False


def load_dataset(con, area_key: str, bin_minutes: int | None = None, *,
                 with_geometry: bool = True) -> StoredDataset:
    """Load everything cached for an area + bin-length in one call.

    Returns a :class:`StoredDataset` whose ``df`` / ``metadata`` equal the file
    loaders' output and whose ``geo`` is the cached (already-joined) geometry — so a
    session runs from the DB without re-parsing the export or repeating the spatial
    join. Pass ``with_geometry=False`` to skip the geo read.
    """
    row = _area_row(con, area_key)
    if row is None:
        raise KeyError(f"No area {area_key!r} in the store.")
    bin_minutes = _resolve_bin(con, area_key, bin_minutes)
    df = load_export(con, area_key, bin_minutes)
    metadata = load_metadata(con, area_key)
    geo = load_geometry(con, area_key) if with_geometry else None
    return StoredDataset(
        area_key=area_key, area_name=row.get("area_name"), bin_minutes=bin_minutes,
        df=df, metadata=metadata, geo=geo,
        units=df.attrs.get("units", {}), has_aadt=bool(row.get("has_aadt")))


def remove_area(con, area_key: str) -> bool:
    """Drop an area's tables and registry rows. Returns ``True`` if it existed."""
    if _area_row(con, area_key) is None:
        return False
    for tbl in (_obs_table(area_key), _meta_table(area_key), _geo_table(area_key)):
        con.execute(f'DROP TABLE IF EXISTS "{tbl}"')
    con.execute(f'DELETE FROM "{AREAS_TABLE}" WHERE area_key = ?', [area_key])
    con.execute(f'DELETE FROM "{INGESTS_TABLE}" WHERE area_key = ?', [area_key])
    return True
