"""Database-backed storage & ingest for INRIX exports.  (ROADMAP Item 21)

Move from re-parsing the export ``.zip`` and the GIS shapefiles every session to a
**persistent local database**. An export (the typed tz-aware time series + its
metadata) and the *processed* GIS layers (segment → geometry, segment → AADT with
the Item 18 match flags) are ingested **once**; thereafter a session **selects** an
ingested dataset instead of re-parsing, and the expensive spatial join is read back
from the cache rather than recomputed.

Pure core, per CLAUDE.md: **no GUI imports, no hardcoded paths** (the DB path /
connection is always a parameter). The file loaders (:mod:`inrix_tools.io`,
:mod:`inrix_tools.geometry`, :mod:`inrix_tools.aadt`) keep working unchanged — the
DB is *optional*, an accelerator, not a new requirement.

Why DuckDB (decision recorded 2026-07-17, DESIGN_HISTORY Session 26)
-------------------------------------------------------------------
**DuckDB**, not SQLite. It is already a transitive dependency (via
``traffic-anomaly``'s ``ibis-framework[duckdb]``), so nothing new is pulled in, and
its columnar engine is the natural fit for the analytic scans of a ~2M-row export.
DuckDB's ``spatial`` extension is **not** used: the GIS layers here are a small
per-segment table (a few hundred rows), so their geometry is serialized as **WKB
blobs** and rehydrated with :mod:`shapely` — no in-DB spatial predicates are needed,
which keeps ``connect`` offline (no extension download) and the schema portable.
SQLite would also serve the small tables but loses on the big observations scan, so
DuckDB is chosen on the *query* need, per the ROADMAP.

Round-trip fidelity (see the tests):

- **Timezones.** ``io.load_data`` returns ``Date Time`` as tz-aware **UTC**
  (``datetime64[ns, UTC]``). DuckDB stores it as ``TIMESTAMP WITH TIME ZONE`` and,
  on read, materializes it in the *session* zone at μs resolution; the connection
  pins ``SET TimeZone='UTC'`` and :func:`load_export` normalizes back to
  ``datetime64[ns, UTC]`` so the loaded frame **equals** the file loader's.
- **Geometry.** Stored as WKB (``NULL`` for a ``missing`` segment) and reloaded
  through ``shapely.wkb`` into a WGS84 GeoDataFrame indexed by ``Segment ID`` —
  matching :func:`inrix_tools.geometry.segment_geometry` output plus any cached
  AADT-join columns.

Schema + versioning live in DATA_FORMAT.md; :data:`SCHEMA_VERSION` is stamped on
every dataset row so a future migration can detect an older store.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from . import io as _io
from .geometry import WGS84
from .io import DATETIME_COL, SEGMENT_COL

# Bump when the on-disk layout changes in a way that needs a migration; stamped on
# every dataset row (see DATA_FORMAT.md "Database store").
SCHEMA_VERSION = 1

REGISTRY_TABLE = "_datasets"          # dataset registry (one row per ingested name)
GEOM_WKB_COL = "_geom_wkb"            # BLOB column holding each segment's WKB
_GEOMETRY_NAME = "geometry"           # active geometry column on the reloaded frame

# Columns the AADT join contributes to the cached geo layer (Item 18). Kept explicit
# so :func:`ingest_aadt` knows exactly which columns to (re)write.
_AADT_JOIN_COLS = ("AADT", "aadt_source", "aadt_dist_m", "Route", "Commercial")


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------
def connect(db_path, *, read_only: bool = False):
    """Open (or create) the DuckDB store at ``db_path`` and return the connection.

    ``db_path`` is a **parameter** — the module never hardcodes a location. Use
    ``":memory:"`` for a throwaway/test store. The session timezone is pinned to
    UTC so ``TIMESTAMPTZ`` columns read back as UTC (see the module docstring); the
    dataset registry is created if absent.

    Args:
        db_path: filesystem path to the ``.duckdb`` file, or ``":memory:"``.
        read_only: open the file read-only (for a pure select-from-ingested run;
            the registry must already exist).

    Returns:
        A ``duckdb.DuckDBPyConnection``.
    """
    import duckdb

    con = duckdb.connect(str(db_path), read_only=read_only)
    con.execute("SET TimeZone='UTC'")
    if not read_only:
        _ensure_registry(con)
    return con


def _ensure_registry(con) -> None:
    """Create the dataset registry table if it does not yet exist."""
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{REGISTRY_TABLE}" (
            name           VARCHAR PRIMARY KEY,
            tbl_key        VARCHAR,
            source         VARCHAR,
            tz             VARCHAR,
            units_speed    VARCHAR,
            units_travel_time VARCHAR,
            n_rows         BIGINT,
            n_segments     BIGINT,
            date_min       DATE,
            date_max       DATE,
            has_geometry   BOOLEAN,
            has_aadt       BOOLEAN,
            schema_version INTEGER,
            ingested_at    TIMESTAMP
        )
        """
    )


def _dataset_key(name: str) -> str:
    """Deterministic, collision-safe table suffix for a dataset ``name``.

    A sanitized prefix keeps table names human-readable; a short hash of the *full*
    name disambiguates two names that sanitize to the same token. Same name → same
    key (so re-ingest overwrites the same tables — idempotent)."""
    slug = re.sub(r"[^0-9A-Za-z]+", "_", str(name)).strip("_").lower()[:40] or "ds"
    h = hashlib.md5(str(name).encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{h}"


def _obs_table(key: str) -> str:
    return f"obs_{key}"


def _meta_table(key: str) -> str:
    return f"meta_{key}"


def _geo_table(key: str) -> str:
    return f"geo_{key}"


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------
def ingest_export(con, name: str, source, *, ingested_at: datetime | None = None) -> str:
    """Ingest an INRIX export (``source``) into the store under ``name``.

    Reads the export with the **file loaders** (:func:`io.load_data` /
    :func:`io.load_metadata`) — so the stored frames carry exactly the file
    loaders' types/units — and writes them to per-dataset tables. Idempotent:
    re-ingesting the same ``name`` **replaces** its observation + metadata tables
    and updates the registry row (the geometry/AADT cache is left as-is; refresh it
    with :func:`ingest_geometry` / :func:`ingest_aadt`).

    Args:
        con: a connection from :func:`connect`.
        name: the dataset label the GUI selects by (e.g. the export stem).
        source: anything :func:`io.load_data` accepts (``.zip`` / dir / ``.csv`` /
            list of parts).
        ingested_at: override the ingest timestamp (defaults to ``now`` UTC).

    Returns:
        The dataset ``name`` (for chaining).
    """
    df = _io.load_data(source)
    metadata = _io.load_metadata(source)
    return put_export(con, name, df, metadata,
                      source=str(source), ingested_at=ingested_at)


def put_export(con, name: str, df: pd.DataFrame, metadata: pd.DataFrame, *,
               source: str | None = None, ingested_at: datetime | None = None) -> str:
    """Persist an already-loaded export frame + metadata under ``name``.

    The lower-level entry behind :func:`ingest_export` — useful when the caller
    already holds the :func:`io.load_data` / :func:`io.load_metadata` frames (the
    GUI builds them once for the geometry/AADT join, then hands them straight here
    without re-reading the zip). Idempotent per ``name``.
    """
    _ensure_registry(con)
    key = _dataset_key(name)
    obs, meta = _obs_table(key), _meta_table(key)

    # Observations: store the frame verbatim (tz-aware Date Time -> TIMESTAMPTZ).
    con.register("_obs_src", df)
    con.execute(f'CREATE OR REPLACE TABLE "{obs}" AS SELECT * FROM _obs_src')
    con.unregister("_obs_src")

    # Metadata: the frame is indexed by Segment ID; store it as a plain column.
    meta_flat = metadata.reset_index() if metadata.index.name else metadata.copy()
    con.register("_meta_src", meta_flat)
    con.execute(f'CREATE OR REPLACE TABLE "{meta}" AS SELECT * FROM _meta_src')
    con.unregister("_meta_src")

    units = (df.attrs.get("units") or {}) if hasattr(df, "attrs") else {}
    if DATETIME_COL in df and len(df):
        d = df[DATETIME_COL].dt.tz_convert("UTC")
        date_min, date_max = d.min().date(), d.max().date()
    else:
        date_min = date_max = None
    n_segments = (int(metadata.shape[0]) if metadata is not None
                  else int(df[SEGMENT_COL].nunique()) if SEGMENT_COL in df else 0)
    ts = ingested_at or datetime.now(timezone.utc)

    con.execute(f'DELETE FROM "{REGISTRY_TABLE}" WHERE name = ?', [name])
    con.execute(
        f'INSERT INTO "{REGISTRY_TABLE}" VALUES '
        f'(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [name, key, source, _frame_tz(df),
         units.get("speed"), units.get("travel_time"),
         int(len(df)), n_segments, date_min, date_max,
         False, False, SCHEMA_VERSION, ts],
    )
    return name


def _frame_tz(df: pd.DataFrame) -> str | None:
    """The dataset tz recorded on the frame (``io.to_local`` sets ``attrs['tz']``);
    ``io.load_data`` output is UTC, so this is typically ``None`` -> the GUI applies
    its own tz on load, exactly as on the file path."""
    return (df.attrs or {}).get("tz") if hasattr(df, "attrs") else None


def ingest_geometry(con, name: str, geo) -> str:
    """Cache the **processed** segment geometry (and any columns it already carries,
    e.g. an AADT join) for dataset ``name``.

    ``geo`` is a GeoDataFrame indexed by ``Segment ID`` — typically
    :func:`inrix_tools.geometry.segment_geometry` output, optionally already passed
    through :func:`inrix_tools.aadt.join_aadt`. The geometry is stored as WKB
    (``NULL`` for a ``missing`` segment) so the layer round-trips without the DuckDB
    spatial extension; non-geometry columns are stored as-is. Idempotent: replaces
    the dataset's geo table. This is what makes the Item 18 spatial join run **once
    at ingest**, not per session load.
    """
    key = _dataset_key(name)
    flat = _geo_to_frame(geo)
    con.register("_geo_src", flat)
    con.execute(f'CREATE OR REPLACE TABLE "{_geo_table(key)}" AS SELECT * FROM _geo_src')
    con.unregister("_geo_src")

    has_aadt = any(c in geo.columns for c in _AADT_JOIN_COLS)
    con.execute(
        f'UPDATE "{REGISTRY_TABLE}" SET has_geometry = TRUE, has_aadt = ? '
        f'WHERE name = ?', [bool(has_aadt), name])
    return name


def ingest_aadt(con, name: str, aadt_geo) -> str:
    """(Re)write the AADT-join columns onto the cached geo layer for ``name``.

    A companion to :func:`ingest_geometry` for the case where geometry was ingested
    first and the AADT match is added (or refreshed) later. ``aadt_geo`` is a frame
    keyed by ``Segment ID`` carrying the :func:`inrix_tools.aadt.join_aadt` columns
    (``AADT`` / ``aadt_source`` / ``aadt_dist_m`` / ``Route`` / ``Commercial``);
    only those columns are merged onto the stored geometry (the geometry itself is
    untouched). Requires geometry to have been ingested already.
    """
    geo = load_geometry(con, name)
    if geo is None:
        raise ValueError(f"No geometry cached for dataset {name!r}; "
                         "call ingest_geometry first.")
    src = aadt_geo if aadt_geo.index.name == SEGMENT_COL else aadt_geo.set_index(SEGMENT_COL)
    for col in _AADT_JOIN_COLS:
        if col in src.columns:
            geo[col] = src[col].reindex(geo.index)
    return ingest_geometry(con, name, geo)


def _geo_to_frame(geo) -> pd.DataFrame:
    """Flatten a GeoDataFrame (indexed by ``Segment ID``) into a plain DataFrame
    with a WKB blob column, suitable for a DuckDB table. Object columns carrying
    ``pd.NA`` are coerced to ``None`` so DuckDB accepts them."""
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
def list_datasets(con) -> pd.DataFrame:
    """The ingested datasets as a DataFrame (most-recent first).

    Columns: ``name``, ``source``, ``n_rows``, ``n_segments``, ``date_min``,
    ``date_max``, ``has_geometry``, ``has_aadt``, ``schema_version``,
    ``ingested_at``. Empty (with those columns) when nothing is ingested yet.
    """
    _ensure_registry(con)
    return con.execute(
        f'SELECT name, source, n_rows, n_segments, date_min, date_max, '
        f'has_geometry, has_aadt, schema_version, ingested_at '
        f'FROM "{REGISTRY_TABLE}" ORDER BY ingested_at DESC, name'
    ).df()


def dataset_names(con) -> list[str]:
    """Just the ingested dataset names (most-recent first) — for a GUI dropdown."""
    _ensure_registry(con)
    rows = con.execute(
        f'SELECT name FROM "{REGISTRY_TABLE}" ORDER BY ingested_at DESC, name'
    ).fetchall()
    return [r[0] for r in rows]


def _registry_row(con, name: str) -> dict | None:
    _ensure_registry(con)
    df = con.execute(
        f'SELECT * FROM "{REGISTRY_TABLE}" WHERE name = ?', [name]).df()
    return None if len(df) == 0 else df.iloc[0].to_dict()


def load_export(con, name: str) -> pd.DataFrame:
    """Load dataset ``name``'s time-series frame — **equal to** what
    :func:`io.load_data` produced at ingest.

    ``Date Time`` is normalized back to ``datetime64[ns, UTC]`` and
    ``df.attrs['units']`` is restored, so the frame is a drop-in for the file
    loader's output (see the parity test)."""
    row = _registry_row(con, name)
    if row is None:
        raise KeyError(f"No dataset named {name!r} in the store.")
    obs = _obs_table(_dataset_key(name))
    df = con.execute(f'SELECT * FROM "{obs}"').df()
    if DATETIME_COL in df.columns:
        df[DATETIME_COL] = (
            df[DATETIME_COL].dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
        )
    df.attrs["units"] = {"speed": row.get("units_speed"),
                         "travel_time": row.get("units_travel_time")}
    return df


def load_metadata(con, name: str) -> pd.DataFrame:
    """Load dataset ``name``'s metadata frame — indexed by ``Segment ID``, equal to
    :func:`io.load_metadata` output (``Combined`` label included)."""
    if _registry_row(con, name) is None:
        raise KeyError(f"No dataset named {name!r} in the store.")
    meta = _meta_table(_dataset_key(name))
    dfm = con.execute(f'SELECT * FROM "{meta}"').df()
    if SEGMENT_COL in dfm.columns:
        dfm[SEGMENT_COL] = dfm[SEGMENT_COL].astype("int64")
        dfm = dfm.set_index(SEGMENT_COL)
    return dfm


def load_geometry(con, name: str):
    """Load dataset ``name``'s cached geometry layer as a GeoDataFrame indexed by
    ``Segment ID`` (WGS84), or ``None`` when no geometry was ingested.

    Carries the same columns that were cached — ``source`` and any AADT-join
    columns — so the Item 18 spatial join is **read**, not recomputed."""
    import geopandas as gpd
    from shapely import wkb

    row = _registry_row(con, name)
    if row is None or not row.get("has_geometry"):
        return None
    geot = _geo_table(_dataset_key(name))
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
    """The frames a session needs, read back from the store — the DB counterpart of
    what the file loaders produce (:func:`io.load_data` / :func:`io.load_metadata` /
    :func:`geometry.segment_geometry` + the cached AADT join)."""
    name: str
    df: pd.DataFrame
    metadata: pd.DataFrame
    geo: object = None                 # GeoDataFrame or None
    source: str | None = None
    tz: str | None = None
    units: dict = field(default_factory=dict)
    has_aadt: bool = False


def load_dataset(con, name: str, *, with_geometry: bool = True) -> StoredDataset:
    """Load everything cached for dataset ``name`` in one call.

    Returns a :class:`StoredDataset` whose ``df`` / ``metadata`` are **equal to**
    the file loaders' output and whose ``geo`` is the cached (already-joined)
    geometry layer — so a session runs from the DB without re-parsing the export or
    repeating the spatial join. Pass ``with_geometry=False`` to skip the geo read.
    """
    row = _registry_row(con, name)
    if row is None:
        raise KeyError(f"No dataset named {name!r} in the store.")
    df = load_export(con, name)
    metadata = load_metadata(con, name)
    geo = load_geometry(con, name) if with_geometry else None
    return StoredDataset(
        name=name, df=df, metadata=metadata, geo=geo,
        source=row.get("source"), tz=row.get("tz"),
        units=df.attrs.get("units", {}), has_aadt=bool(row.get("has_aadt")),
    )


def remove_dataset(con, name: str) -> bool:
    """Drop a dataset's tables and registry row. Returns ``True`` if it existed."""
    row = _registry_row(con, name)
    if row is None:
        return False
    key = _dataset_key(name)
    for tbl in (_obs_table(key), _meta_table(key), _geo_table(key)):
        con.execute(f'DROP TABLE IF EXISTS "{tbl}"')
    con.execute(f'DELETE FROM "{REGISTRY_TABLE}" WHERE name = ?', [name])
    return True
