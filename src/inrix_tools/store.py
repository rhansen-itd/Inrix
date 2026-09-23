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
**DuckDB**, not SQLite: its columnar engine fits the analytic scans of a ~2M-row
export, and it reads a CSV stream directly, which is what makes
:func:`ingest_export_streaming` (Item 35) possible at district scale. It is declared
in ``pyproject.toml`` as a direct dependency — this module imports it — rather than
relied on to arrive transitively via ``traffic-anomaly``'s
``ibis-framework[duckdb]``. The DuckDB ``spatial`` extension is **not** used — the
GIS layers are a small per-segment table, so geometry is serialized as **WKB blobs** and
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
import os
import shutil
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import io as _io
from .geometry import WGS84
from .io import CORRIDOR_COL, DATETIME_COL, SEGMENT_COL
from .names import INRIX_LABEL_COL as _INRIX_LABEL_COL
from .names import NAME_COL as _NAME_COL

# Bump when the on-disk layout changes; stamped on every area row. v2 = the Item 23
# area/merge model (v1 was the Item 21 one-dataset-per-export layout). The Item 27
# _names table is additive (created on connect, no data migration), so no bump.
SCHEMA_VERSION = 2

AREAS_TABLE = "_areas"                # area registry (one row per corridor-group)
INGESTS_TABLE = "_ingests"           # provenance: one row per ingested export
NAMES_TABLE = "_names"               # friendly names, global by Segment ID (Item 27)
GEOM_WKB_COL = "_geom_wkb"           # BLOB column holding each segment's WKB
BIN_COL = "bin_minutes"              # time-bin partition column on the obs table

# Columns the AADT join contributes to the cached geo layer (Item 18; the record
# identity ones — kind / description / RouteID — are Item 34).
_AADT_JOIN_COLS = ("AADT", "aadt_source", "aadt_dist_m", "aadt_record_kind",
                   "aadt_desc", "RouteID", "Route", "Commercial")


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
    # Friendly segment names (Item 27), **global** by Segment ID — INRIX ids are
    # globally unique, so one name applies across every area. Last-write-wins upsert
    # (an edit overwrites; unlike the keep-first observation merge). Not stamped with
    # SCHEMA_VERSION — the table is additive and created idempotently on connect.
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{NAMES_TABLE}" (
            "{SEGMENT_COL}"       BIGINT PRIMARY KEY,
            "{_NAME_COL}"         VARCHAR,
            "{_INRIX_LABEL_COL}"  VARCHAR,
            updated_at            TIMESTAMP
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
def ingest_export(con, source, *, ingested_at: datetime | None = None,
                  corridor_name: str | None = None) -> dict:
    """Ingest an INRIX export (``source``) — merging it into its corridor **area**.

    Reads with the file loaders (:func:`io.load_data` / :func:`io.load_metadata`),
    derives the area from the corridor set and the bin-length from the timestamp
    spacing, then merges the rows **keep-first** into the area's tables. Returns a
    summary dict: ``area_key`` / ``area_name`` / ``bin_minutes`` / ``n_rows_added``,
    plus ``n_parts`` / ``parts``.

    As in :func:`ingest_export_streaming`, ``source`` is expanded by
    :func:`io.discover_parts`: handing it any ``..._part_N.zip`` reads **every**
    sibling part, and ``n_rows_added`` is the total over all of them. So is
    ``corridor_name``, which files a supplemental export under an existing area's
    label instead of one of its own — see that function for why.
    """
    parts = [Path(p).name for p in _io.discover_parts(source)]
    df = _io.load_data(source)
    metadata = _io.load_metadata(source)
    logged = " + ".join(parts)
    if corridor_name is not None:
        if CORRIDOR_COL not in df.columns:
            raise ValueError(
                f"corridor_name={corridor_name!r} was given but {source!r} carries no "
                f"{CORRIDOR_COL!r} column, so there is no label to rewrite.")
        was = ", ".join(sorted({str(c) for c in df[CORRIDOR_COL].dropna().unique()}))
        df[CORRIDOR_COL] = corridor_name
        logged += f" (corridor {(was or '(none)')!r} -> {corridor_name!r})"
    out = put_export(con, df, metadata, source=logged, ingested_at=ingested_at)
    return {**out, "n_parts": len(parts), "parts": parts}


def put_export(con, df: pd.DataFrame, metadata: pd.DataFrame, *,
               source: str | None = None, ingested_at: datetime | None = None) -> dict:
    """Merge an already-loaded export frame + metadata into its area.

    The lower-level entry behind :func:`ingest_export`, for callers that already hold
    the export's **raw** frames. Note the GUI's ingest (:func:`gui.app.ingest_to_db`)
    deliberately **re-reads the raw export** (``io.load_data`` / ``io.load_metadata``)
    for this call rather than reusing its in-memory ``Dataset.df``: that working frame
    is CValue-filtered, localized, and carries the derived ``Delay`` column, so
    persisting it would store a filtered/derived view instead of the raw export — the
    re-read is on purpose, not a missed optimization. New rows are those whose
    ``(Segment ID, Date Time, bin_minutes)`` are not already in the area (keep-first);
    segment metadata is merged keep-first per ``Segment ID``.
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

    _upsert_area(con, area_key, area_name, corridors, df.attrs.get("units"), ingested_at)
    span = ((df[DATETIME_COL].min(), df[DATETIME_COL].max())
            if (DATETIME_COL in df.columns and len(df)) else (None, None))
    _log_ingest(con, area_key, source, bin_minutes, n_added, span, ingested_at)
    return {"area_key": area_key, "area_name": area_name,
            "bin_minutes": bin_minutes, "n_rows_added": n_added}


def _merge_frame(con, table: str, frame: pd.DataFrame, keys: list[str]) -> int:
    """Insert ``frame`` into ``table``, **keep-first** on ``keys`` (rows whose key
    tuple already exists are skipped). Creates the table on first write. Tolerates
    column drift between exports (missing columns fill NULL; new columns are added).
    Returns the number of rows actually inserted."""
    # Rows whose own key column is NULL can't participate in keep-first dedup — SQL
    # NULL never equals NULL, so the anti-join treats them as "new" and re-inserts
    # them on *every* re-ingest (review R6). Drop NULL-key rows up front so keep-first
    # holds and no duplicate NULL-key rows leak in.
    present_keys = [k for k in keys if k in frame.columns]
    if present_keys:
        frame = frame.dropna(subset=present_keys)
    con.register("_merge_src", frame)
    try:
        return _merge_relation(con, table, "_merge_src", keys, n_rows=len(frame))
    finally:
        con.unregister("_merge_src")


def _merge_relation(con, table: str, source: str, keys: list[str],
                    n_rows: int | None = None) -> int:
    """:func:`_merge_frame`'s keep-first merge against an **already-registered
    relation** — a view, a temp table, or a registered frame — instead of a pandas
    frame, so :func:`ingest_export_streaming` can merge an export it never
    materialised.

    ``source`` is a bare (unquoted) relation name whose NULL-key rows are already
    removed — ``_merge_frame`` drops them from the frame, the streaming path filters
    them in SQL (keep-first cannot dedup a key that is NULL).
    """
    if not _table_exists(con, table):
        con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM "{source}"')
        if n_rows is None:
            n_rows = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        return int(n_rows)
    _align_columns(con, table, source)
    _widen_columns(con, table, source)
    on = " AND ".join(f'o."{k}" = n."{k}"' for k in keys)
    anti = (f'SELECT n.* FROM "{source}" n '
            f'LEFT JOIN "{table}" o ON {on} WHERE o."{keys[0]}" IS NULL')
    n_added = con.execute(f'SELECT count(*) FROM ({anti})').fetchone()[0]
    if n_added:
        con.execute(f'INSERT INTO "{table}" BY NAME {anti}')
    return int(n_added)


def _align_columns(con, table: str, view: str) -> None:
    """Add any columns present in ``view`` but missing from ``table`` (so an export
    that carries an extra column merges instead of erroring)."""
    existing = {r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()}
    for row in con.execute(f'DESCRIBE "{view}"').fetchall():
        name, coltype = row[0], row[1]
        if name not in existing:
            con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {coltype}')


_INT_TYPES = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT"}
_FLOAT_TYPES = {"FLOAT", "DOUBLE"}


def _widen_columns(con, table: str, view: str) -> None:
    """Widen an integer column to ``DOUBLE`` when the incoming rows are fractional.

    A column's type is fixed by the **first** export that writes it, and DuckDB casts
    on insert — so an export whose ``Ref Speed`` reads ``45, 46`` (stored ``BIGINT``)
    would silently truncate a later export's ``45.5`` to ``46``. Both ingest paths
    are exposed to it: pandas types a clean column int64 too. Widening keeps the
    merged column the type the concatenated frame would have had.
    """
    existing = {r[0]: r[1] for r in con.execute(f'DESCRIBE "{table}"').fetchall()}
    for row in con.execute(f'DESCRIBE "{view}"').fetchall():
        name, coltype = row[0], row[1]
        old = existing.get(name)
        if old is None or old == coltype:
            continue
        if old in _INT_TYPES and coltype in _FLOAT_TYPES:
            con.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{name}" TYPE DOUBLE')


def _upsert_area(con, area_key, area_name, corridors, units, ingested_at) -> None:
    """Create/refresh the area registry row: span and segment count are recomputed
    from the merged obs; ``created_at`` is preserved on update. ``units`` is the
    export's ``attrs['units']`` dict (``{'speed': ..., 'travel_time': ...}``)."""
    units = units or {}
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


def _log_ingest(con, area_key, source, bin_minutes, n_added, span, ingested_at) -> None:
    """Append the provenance row. ``span`` is the ingested export's own
    ``(min, max)`` ``Date Time`` — the *file's* range, not the area's."""
    dmin, dmax = span if span is not None else (None, None)
    con.execute(
        f'INSERT INTO "{INGESTS_TABLE}" VALUES (?,?,?,?,?,?,?)',
        [area_key, source, bin_minutes, int(n_added), dmin, dmax,
         ingested_at or datetime.now(timezone.utc)])


# ---------------------------------------------------------------------------
# Streaming ingest: zip member -> DuckDB read_csv, no pandas round-trip (Item 35)
# ---------------------------------------------------------------------------
STREAM_STAGE = "_stream_stage"      # the table each chunk is staged into
STREAM_CHUNK_BYTES = 256 * 1024 * 1024   # uncompressed CSV bytes per committed chunk

# Pandas' own integer rule, as SQL: ``pd.to_numeric`` returns int64 only when a
# column has no missing values and every value is integral — otherwise float64.
_INT_RULE = 'count(*) = count("{c}") AND count(*) FILTER (WHERE "{c}" <> floor("{c}")) = 0'


def _zip_member(zpath, basename: str) -> str:
    """The full member name of ``basename`` inside ``zpath`` (exact basename match —
    ``"metadata.csv".endswith("data.csv")`` is True, so a suffix test is wrong)."""
    with zipfile.ZipFile(zpath) as zf:
        names = [n for n in zf.namelist() if Path(n).name == basename]
    if not names:
        raise FileNotFoundError(f"No '{basename}' inside {zpath}")
    return names[0]


@contextmanager
def _member_chunks(source, basename: str = "data.csv",
                   chunk_bytes: int = STREAM_CHUNK_BYTES):
    """Yield a generator of **paths DuckDB can read**, one per successive chunk of
    ``basename`` inside ``source`` — the export decompressed on the fly, never
    unzipped to disk and never held whole in memory.

    Each chunk is a line-aligned run of ``chunk_bytes`` uncompressed bytes, prefixed
    with the CSV header so ``read_csv`` types it the same way every time. The
    transport is a **named pipe** (``os.mkfifo``) fed by a thread reading
    :meth:`zipfile.ZipFile.open` in 1 MiB blocks: pure Python and inspectable, with
    no ``subprocess`` / ``unzip -p`` / ``/dev/fd/N`` shell plumbing (what the outside
    screening pass used, and neither portable nor inspectable). Where FIFOs do not
    exist (Windows), each chunk is written to a temp file instead — still a chunked
    stream, never a whole-member extract.

    **Why chunks at all:** DuckDB buffers a statement's appended rows until it
    commits, so one ``CREATE TABLE AS SELECT`` over a 45 M-row district part grows to
    roughly the size of the CSV (measured: 1.8 GiB of resident memory for 1.5 GB of
    input) and dies. One statement per chunk commits as it goes, which is what makes
    the ingest bounded rather than merely lazy.

    A non-zip source (a directory or a ``data.csv`` path) yields exactly one chunk:
    the file itself, read by DuckDB directly.
    """
    p = Path(source)
    if p.suffix.lower() != ".zip":
        direct = p if p.is_file() else next(iter(sorted(p.glob(f"**/{basename}"))), None)
        if direct is None:
            raise FileNotFoundError(f"No '{basename}' under {p}")

        def one():
            yield str(direct)

        yield one()
        return

    member = _zip_member(p, basename)
    use_fifo = hasattr(os, "mkfifo")
    tmpdir = tempfile.mkdtemp(prefix="inrix_stream_")
    path = os.path.join(tmpdir, basename)
    if use_fifo:
        os.mkfifo(path)
    zf = zipfile.ZipFile(p)
    fh = zf.open(member)
    failure: list[BaseException] = []

    def _write(dst, header, lead):
        dst.write(header)
        dst.write(lead)
        left = chunk_bytes - len(lead)
        while left > 0:
            block = fh.read(min(1 << 20, left))
            if not block:
                return
            dst.write(block)
            left -= len(block)
        dst.write(fh.readline())          # finish the line the budget cut in half

    def _pump(header, lead):
        try:
            with open(path, "wb") as dst:
                _write(dst, header, lead)
        except BrokenPipeError:
            pass                           # the reader stopped early
        except BaseException as exc:       # surfaced after the query
            failure.append(exc)

    def chunks():
        header = fh.readline()
        lead = fh.read(1)                  # one-byte lookahead: empty => end of member
        while lead:
            if use_fifo:
                writer = threading.Thread(target=_pump, args=(header, lead),
                                          name="inrix-zip-stream", daemon=True)
                writer.start()
                try:
                    yield path
                finally:
                    if writer.is_alive():
                        # The writer blocks in open() until a reader appears; if the
                        # query never opened the pipe, open and close it so the thread
                        # can finish instead of leaking.
                        try:
                            open(path, "rb").close()
                        except OSError:
                            pass
                    writer.join(timeout=30)
                if failure:
                    raise failure[0]
            else:
                with open(path, "wb") as dst:
                    _write(dst, header, lead)
                yield path
            lead = fh.read(1)

    try:
        yield chunks()
    finally:
        fh.close()
        zf.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _sql_str(value) -> str:
    """A SQL single-quoted literal — for the paths and column names that go into
    ``read_csv``'s arguments, which cannot be bound parameters."""
    return "'" + str(value).replace("'", "''") + "'"


def _csv_projection(header: list[str], corridor_name=None) -> tuple[list[str], list[str]]:
    """``io.load_data``'s typing rules, as a SQL projection over an all-``VARCHAR``
    read: the timestamp to ``TIMESTAMPTZ``, ``Segment ID`` to ``BIGINT``, the numeric
    columns (matched by the same prefixes) via ``TRY_CAST`` to ``DOUBLE`` — the SQL
    equivalent of ``pd.to_numeric(errors='coerce')`` — and ``Road Closure`` to the
    ``== 'T'`` boolean. Returns ``(projection, numeric_columns)``; the numeric ones
    are narrowed to ``BIGINT`` afterwards where pandas would have typed them int64.

    ``corridor_name`` projects a literal in place of the export's own
    ``Corridor/Region Name`` — the relabel :func:`ingest_export_streaming` uses to
    file a supplemental export under the area it belongs to.
    """
    proj, numeric = [], []
    for c in header:
        if c == CORRIDOR_COL and corridor_name is not None:
            proj.append(f'{_sql_str(corridor_name)} AS "{c}"')
        elif c == DATETIME_COL:
            proj.append(f'TRY_CAST("{c}" AS TIMESTAMPTZ) AS "{c}"')
        elif c == SEGMENT_COL:
            proj.append(f'TRY_CAST("{c}" AS BIGINT) AS "{c}"')
        elif c == _io.ROAD_CLOSURE_COL:
            # io: .astype(str).str.strip().str.upper().eq("T") — a missing value is
            # False there, so coalesce rather than leaving it NULL.
            proj.append(f"""coalesce(upper(trim("{c}")) = 'T', FALSE) AS "{c}\"""")
        elif c in _io._NUMERIC_EXACT or c.startswith(_io._NUMERIC_PREFIXES):
            proj.append(f'TRY_CAST("{c}" AS DOUBLE) AS "{c}"')
            numeric.append(c)
        else:
            proj.append(f'"{c}"')
    return proj, numeric


def _read_csv_sql(csv_path, header: list[str]) -> str:
    """The ``read_csv`` call for one chunk. Every column is read as ``VARCHAR`` — the
    source is a pipe, which cannot be rewound, so nothing may be sniffed — and
    ``nullstr=''`` makes an empty field NULL, as ``pd.read_csv`` makes it NaN."""
    colspec = ", ".join(f"{_sql_str(c)}: 'VARCHAR'" for c in header)
    return (f"read_csv({_sql_str(csv_path)}, header=true, columns={{{colspec}}}, "
            f"nullstr='', parallel=false)")


def _load_chunk(con, csv_path, header: list[str], *, replace: bool,
                stage: str = STREAM_STAGE, corridor_name=None) -> None:
    """Load one chunk into the ``stage`` table, typed as :func:`io.load_data` types
    it — replacing the stage, or appending to it when the previous chunk was too
    small to decide the export's cadence."""
    proj, _ = _csv_projection(header, corridor_name)
    body = ",\n  ".join(proj) + "\nFROM " + _read_csv_sql(csv_path, header)
    if replace:
        con.execute(f'CREATE OR REPLACE TABLE "{stage}" AS SELECT\n  ' + body)
    else:
        con.execute(f'INSERT INTO "{stage}" SELECT\n  ' + body)


def _narrow_numerics(con, header: list[str], stage: str = STREAM_STAGE) -> None:
    """Narrow the staged numeric columns pandas would have typed int64 — run once the
    stage is complete, never between appends (inserting a fraction into a column
    already narrowed to BIGINT would truncate it)."""
    _, numeric = _csv_projection(header)
    if not numeric:
        return
    checks = ", ".join(_INT_RULE.format(c=c) for c in numeric)
    for c, is_int in zip(numeric, con.execute(
            f'SELECT {checks} FROM "{stage}"').fetchone()):
        if is_int:
            con.execute(f'ALTER TABLE "{stage}" ALTER COLUMN "{c}" TYPE BIGINT')


def _stage_summary(con, header: list[str], stage: str = STREAM_STAGE) -> dict:
    """What the merge and the registry need from the staged chunk(s)."""
    corridors = []
    if CORRIDOR_COL in header:
        corridors = [r[0] for r in con.execute(
            f'SELECT DISTINCT "{CORRIDOR_COL}" FROM "{stage}" '
            f'WHERE "{CORRIDOR_COL}" IS NOT NULL').fetchall()]
    span = (con.execute(f'SELECT min("{DATETIME_COL}"), max("{DATETIME_COL}") '
                        f'FROM "{stage}"').fetchone()
            if DATETIME_COL in header else (None, None))
    n_rows = con.execute(f'SELECT count(*) FROM "{stage}"').fetchone()[0]
    return {"corridors": corridors, "span": span, "n_rows": int(n_rows),
            "bin_histogram": _bin_histogram(con, stage)}


def _bin_histogram(con, stage: str) -> dict[int, int]:
    """``{seconds_between_rows: count}`` over consecutive timestamps within each
    segment — the histogram :func:`detect_bin_minutes` takes the mode of. Returned
    rather than reduced so several chunks combine into the mode of the whole export."""
    cols = {r[0] for r in con.execute(f'DESCRIBE "{stage}"').fetchall()}
    if not {SEGMENT_COL, DATETIME_COL} <= cols:
        return {}
    rows = con.execute(
        f'SELECT d, count(*) FROM (SELECT date_diff(\'second\', lag("{DATETIME_COL}") '
        f'OVER (PARTITION BY "{SEGMENT_COL}" ORDER BY "{DATETIME_COL}"), '
        f'"{DATETIME_COL}") AS d FROM "{stage}") WHERE d > 0 GROUP BY d').fetchall()
    return {int(d): int(n) for d, n in rows}


def _bin_from_histogram(hist: dict[int, int]) -> int | None:
    """The modal spacing in **minutes**, ties broken by the smaller spacing — which is
    what ``pd.Series.mode().iloc[0]`` does inside :func:`detect_bin_minutes`."""
    if not hist:
        return None
    top = max(hist.values())
    return int(round(min(d for d in hist if hist[d] == top) / 60.0))


def _member_corridors(con, part, header: list[str]) -> list[str]:
    """The distinct ``Corridor/Region Name`` values in a whole member, read in one
    streaming pass with **no materialisation** (a hash aggregate over a handful of
    values). Used when an export spans several chunks or parts, so the area identity
    is the one the concatenated frame would have produced rather than the first
    chunk's."""
    if CORRIDOR_COL not in header:
        return []
    out: list[str] = []
    with _member_chunks(part, "data.csv") as chunks:
        for csv_path in chunks:
            out += [r[0] for r in con.execute(
                f'SELECT DISTINCT "{CORRIDOR_COL}" FROM {_read_csv_sql(csv_path, header)} '
                f'WHERE "{CORRIDOR_COL}" IS NOT NULL').fetchall()]
    return out


def ingest_export_streaming(con, source, *, ingested_at: datetime | None = None,
                            chunk_bytes: int = STREAM_CHUNK_BYTES,
                            corridor_name: str | None = None) -> dict:
    """Ingest an INRIX export **without materialising it in pandas** — the zip member
    is streamed straight into DuckDB.  (ROADMAP Item 35)

    The same result as :func:`ingest_export` — the same area, the same keep-first
    merge on ``(Segment ID, Date Time, bin_minutes)``, the same column types, the
    same registry rows — reached without a whole-frame read. That is what makes a
    district-sized export ingestable at all: the 2026 D3 download is ~45 M rows *per
    part*, and :func:`io.load_data` would hold every one of them in memory first.

    Args:
        con: an open :func:`connect` connection.
        source: the export ``.zip`` (a ``..._part_1.zip`` auto-discovers its sibling
            parts, as :func:`io.load_data` does), a directory, or a ``data.csv`` path.
        ingested_at: timestamp recorded on the registry rows (default: now, UTC).
        chunk_bytes: uncompressed CSV bytes per committed chunk (default 256 MiB).
            Peak memory tracks this, not the export's size.
        corridor_name: file this export under **this** corridor label instead of the
            one it carries, so a supplemental export lands in the area it belongs to
            rather than an area of its own (see below). Raises when the export has no
            ``Corridor/Region Name`` column — there is nothing to relabel, and this
            does not invent one.

    Returns:
        The same summary dict :func:`ingest_export` returns — ``area_key`` /
        ``area_name`` / ``bin_minutes`` / ``n_rows_added`` — plus ``n_parts`` and
        ``parts``, the members this call actually covered.

    **Five things worth knowing:**

    - **One call can ingest the whole export, and usually does.** ``source`` is
      expanded by :func:`io.discover_parts`, so handing it *any* ``..._part_N.zip``
      ingests **every sibling part**, and ``n_rows_added`` is the total across all of
      them. That is by design and matches :func:`io.load_data`, but it is easy to
      misread as one part carrying everything: the 2026 D3 export returns
      **91,054,384** from a single call on ``part_1.zip`` where that member alone
      holds 45,403,216 rows. ``n_parts`` / ``parts`` say which members the number
      covers, and the provenance row records the resolved list rather than the one
      path handed in. Calling it again on ``part_2.zip`` re-discovers the same three
      and adds nothing — the merge is keep-first.
    - **Chunks are staged and merged one at a time**, each its own committed
      statement, so memory is bounded by ``chunk_bytes`` however large the export is.
    - **The area identity is the whole export's.** When an export needs more than one
      chunk (or carries several parts), the corridor set is read from every member
      first — a cheap aggregate-only pass — so the area is the one the concatenated
      frame would resolve to, not the first chunk's.
    - **The bin-length is detected from the first chunk** and every row is labelled
      with it; the histogram is accumulated across the rest, and a disagreement
      raises rather than silently mixing two cadences under one label. For an export
      that fits in one chunk this is exactly :func:`detect_bin_minutes`.
    - **Metadata comes from ``source`` alone**, exactly as :func:`ingest_export` does
      (``io.load_metadata`` reads a single source — it does not walk the parts). For
      a part-split export that means the metadata of part 1; the observations of
      every part are still ingested.

    **A supplemental export is relabelled, not re-areaed.** The area is the corridor
    set (:func:`area_identity`), and INRIX names a report whatever it was requested
    as: the seven SH-19 segments backfilled into D3 after ROADMAP Item 42 came back
    labelled ``"Cent"``, which would have made them an area of their own that no
    district run would ever see. ``corridor_name="D3"`` rewrites the label **as the
    rows are staged**, so the stored rows, the resolved area and a later
    re-derivation of the identity all agree — the rows become D3 rows, which is what
    they are. The relabel is recorded in the provenance row's source string
    (``Cent_….zip (corridor 'Cent' -> 'D3')``), because an export that says one thing
    and is stored as another has to say so somewhere.

    A duplicate ``(Segment ID, Date Time)`` **spanning two chunks or parts** is
    de-duplicated here (each chunk merges against what is already stored) where the
    pandas path inserts both copies — its anti-join sees the table, not the frame it
    is about to insert. The streaming behaviour is the keep-first one this module
    documents; the difference is only reachable on an export that overlaps itself.
    """
    _ensure_registry(con)
    parts = _io.discover_parts(source)
    headers = [list(_io._read_member_csv(p, "data.csv", nrows=0).columns) for p in parts]
    if corridor_name is not None and not all(CORRIDOR_COL in h for h in headers):
        raise ValueError(
            f"corridor_name={corridor_name!r} was given but {source!r} carries no "
            f"{CORRIDOR_COL!r} column, so there is no label to rewrite.")

    # Does the export span more than one chunk? (A zip's uncompressed member size is
    # in the central directory — no decompression needed to ask.)
    multi = len(parts) > 1 or any(_member_bytes(p) > chunk_bytes for p in parts)
    identity_corridors: list[str] = []
    original_corridors: list[str] = []
    if corridor_name is not None:
        # The relabel decides the identity; reading the members for it would only
        # find the label being replaced. Its own value is read for the provenance.
        identity_corridors = [corridor_name]
        for part, header in zip(parts, headers):
            original_corridors += _member_corridors(con, part, header)
    elif multi:
        for part, header in zip(parts, headers):
            identity_corridors += _member_corridors(con, part, header)

    area_key = area_name = None
    corridors: list[str] = []
    bin_minutes = None
    units: dict = {}
    n_added = 0
    span_lo = span_hi = None
    histogram: dict[int, int] = {}
    try:
        for part, header in zip(parts, headers):
            with _member_chunks(part, "data.csv", chunk_bytes) as chunks:
                pending = next(chunks, None)
                while pending is not None:
                    _load_chunk(con, pending, header, replace=True,
                                corridor_name=corridor_name)
                    pending = next(chunks, None)
                    # The cadence is read from the first staged chunk. A chunk too
                    # small to hold two timestamps for any one segment cannot decide
                    # it, so keep appending before labelling anything.
                    while (area_key is None and pending is not None
                           and not _bin_histogram(con, STREAM_STAGE)):
                        _load_chunk(con, pending, header, replace=False,
                                    corridor_name=corridor_name)
                        pending = next(chunks, None)
                    info = _stage_summary(con, header)
                    for d, n in info["bin_histogram"].items():
                        histogram[d] = histogram.get(d, 0) + n
                    if area_key is None:
                        area_key, area_name, corridors = _identity_from_corridors(
                            con, identity_corridors or info["corridors"], header)
                        bin_minutes = _bin_from_histogram(info["bin_histogram"])
                        units = _io.detect_units(header)
                    n_added += _merge_stage(con, _obs_table(area_key), header,
                                            bin_minutes)
                    lo, hi = info["span"]
                    span_lo = lo if span_lo is None else min(span_lo, lo or span_lo)
                    span_hi = hi if span_hi is None else max(span_hi, hi or span_hi)
    finally:
        con.execute(f'DROP VIEW IF EXISTS "{STREAM_STAGE}_src"')
        con.execute(f'DROP TABLE IF EXISTS "{STREAM_STAGE}"')

    if area_key is None:
        raise ValueError(
            f"No observation rows found in {source!r} — its 'data.csv' carries a "
            "header and nothing else.")
    whole = _bin_from_histogram(histogram)
    if whole != bin_minutes:
        raise ValueError(
            f"The export's modal time bin-length is {whole} min but its first chunk "
            f"read {bin_minutes} min; the rows just ingested are labelled "
            f"bin_minutes={bin_minutes}. Ingest the cadences separately.")

    # Metadata: single-source, exactly as ``ingest_export`` reads it.
    metadata = _io.load_metadata(source)
    meta_flat = metadata.reset_index() if metadata.index.name else metadata.copy()
    _merge_frame(con, _meta_table(area_key), meta_flat, keys=[SEGMENT_COL])

    _upsert_area(con, area_key, area_name, corridors, units, ingested_at)
    part_names = [Path(p).name for p in parts]
    # The resolved parts, not the single path handed in: a provenance row reading
    # "part_1.zip -> 91,054,384" invites the conclusion that part 1 held everything.
    logged = " + ".join(part_names)
    if corridor_name is not None:
        was = ", ".join(sorted(set(original_corridors))) or "(none)"
        logged += f" (corridor {was!r} -> {corridor_name!r})"
    _log_ingest(con, area_key, logged, bin_minutes, n_added,
                (span_lo, span_hi), ingested_at)
    return {"area_key": area_key, "area_name": area_name,
            "bin_minutes": bin_minutes, "n_rows_added": n_added,
            "n_parts": len(parts), "parts": part_names}


def _member_bytes(part, basename: str = "data.csv") -> int:
    """The uncompressed size of a member, from the zip's central directory (0 for a
    non-zip source, which DuckDB reads in one pass anyway)."""
    p = Path(part)
    if p.suffix.lower() != ".zip":
        return 0
    with zipfile.ZipFile(p) as zf:
        return zf.getinfo(_zip_member(p, basename)).file_size


def _merge_stage(con, obs: str, header: list[str], bin_minutes) -> int:
    """Merge the staged chunk into an area's obs table, keep-first.

    ``bin_minutes`` is added as a **projection** rather than an ``ALTER``/``UPDATE``
    on the stage: updating every row of a staged district chunk would rebuild it in
    memory, which is the cost this whole path exists to avoid. The NULL-key rows are
    dropped in the same view — keep-first cannot dedup a NULL key (SQL NULL never
    equals NULL), the guard :func:`_merge_frame` applies in pandas.
    """
    _narrow_numerics(con, header)
    keys = [SEGMENT_COL, DATETIME_COL, BIN_COL]
    nn = " AND ".join(f'"{k}" IS NOT NULL' for k in keys
                      if k == BIN_COL or k in header)
    view = f"{STREAM_STAGE}_src"
    con.execute(f'CREATE OR REPLACE VIEW "{view}" AS SELECT *, '
                f'CAST({"NULL" if bin_minutes is None else int(bin_minutes)} AS BIGINT) '
                f'AS "{BIN_COL}" FROM "{STREAM_STAGE}" WHERE {nn}')
    try:
        return _merge_relation(con, obs, view, keys)
    finally:
        con.execute(f'DROP VIEW IF EXISTS "{view}"')


def _identity_from_corridors(con, corridors, header) -> tuple[str, str, list[str]]:
    """The area identity for a staged export — :func:`area_identity`'s rule, fed the
    distinct values read in SQL rather than a materialised frame. Falls back to the
    **Segment ID set** (read from the stage) exactly as ``area_identity`` does when
    the export carries no corridor values."""
    if corridors:
        return area_identity(pd.DataFrame({CORRIDOR_COL: sorted(set(corridors))}))
    seg_ids = [r[0] for r in con.execute(
        f'SELECT DISTINCT "{SEGMENT_COL}" FROM "{STREAM_STAGE}" '
        f'WHERE "{SEGMENT_COL}" IS NOT NULL').fetchall()] if SEGMENT_COL in header else []
    return area_identity(pd.DataFrame({SEGMENT_COL: seg_ids}))

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


def area_segments(con, area_key: str) -> list[int]:
    """Every ``Segment ID`` the area's **observations** carry, ascending (Item 42).

    Not the same set as :func:`load_metadata`'s index, and the difference matters. An
    INRIX district export is split into parts **by segment**, each part carrying the
    whole date span for its own segments and a ``metadata.csv`` that lists only those
    — D3's three parts hold 1,947 + 1,942 + 16 = 3,905. :func:`ingest_export_streaming`
    ingests every part's **observations** but reads metadata from ``source`` alone, so
    the store under-reports the export through ``load_metadata``: ``d3_store.duckdb``
    answers 1,947 there and 3,912 here. What the export *contains* is what was
    observed, so anything reconciling the segment set reads it from this.
    """
    obs = _obs_table(area_key)
    if not _table_exists(con, obs):
        return []
    rows = con.execute(
        f'SELECT DISTINCT "{SEGMENT_COL}" FROM "{obs}" '
        f'WHERE "{SEGMENT_COL}" IS NOT NULL ORDER BY 1').fetchall()
    return [int(r[0]) for r in rows]


def _area_row(con, area_key: str) -> dict | None:
    _ensure_registry(con)
    df = con.execute(
        f'SELECT * FROM "{AREAS_TABLE}" WHERE area_key = ?', [area_key]).df()
    return None if len(df) == 0 else df.iloc[0].to_dict()


def _date_bounds_utc(date_start, date_end, tz):
    """Half-open **UTC** instants ``[lo, hi)`` equivalent to
    :func:`timebins.filter_date_range`'s *inclusive local-calendar-date* cut, for
    pushing a date restriction into the SQL ``WHERE`` clause (Item 25).

    ``date_start`` / ``date_end`` are local calendar dates (a ``"YYYY-MM-DD"`` string,
    a ``date`` / ``Timestamp``, or ``None`` / ``""`` for an open side); ``tz`` names
    the local zone they are read in (``None`` → UTC). The bounds are
    ``[local_midnight(start), local_midnight(end)+1 day)`` computed in ``tz`` and then
    expressed in UTC. Because comparing the stored UTC ``Date Time`` against these
    instants is *tz-invariant*, the pushed-down cut is **exactly** the frame that
    ``filter_date_range`` would keep after ``io.to_local`` — DST included — so no
    re-trim in pandas is needed. Returns ``(lo, hi)`` as tz-aware UTC
    :class:`datetime.datetime` (or ``None`` for an open side)."""
    zone = tz or "UTC"

    def _midnight(x):
        if x is None or (isinstance(x, str) and not x.strip()):
            return None
        ts = pd.Timestamp(x)
        ts = ts.tz_localize(zone) if ts.tzinfo is None else ts.tz_convert(zone)
        return ts.normalize()  # midnight of that local calendar day

    lo_local = _midnight(date_start)
    hi_local = _midnight(date_end)
    lo = None if lo_local is None else lo_local.tz_convert("UTC").to_pydatetime()
    # inclusive of the whole end day -> exclusive at the next local midnight (a
    # DateOffset advances a calendar day, DST-safe — like ``filter_date_range``).
    hi = (None if hi_local is None
          else (hi_local + pd.DateOffset(days=1)).tz_convert("UTC").to_pydatetime())
    return lo, hi


def load_export(con, area_key: str, bin_minutes: int | None = None, *,
                date_start=None, date_end=None, tz=None) -> pd.DataFrame:
    """Load an area's merged time-series frame for one bin-length — the same typed,
    tz-aware **UTC** shape :func:`io.load_data` produces (the ``bin_minutes``
    partition column is dropped and ``df.attrs['units']`` restored).

    ``bin_minutes`` selects the partition; if ``None`` and the area has exactly one
    bin, that one is used (else a :class:`ValueError` names the choices).

    ``date_start`` / ``date_end`` (optional, Item 25) restrict the load to an
    **inclusive local calendar-date range**, *pushed down* into the SQL scan so an
    ever-growing area isn't pulled into memory whole. The bounds are read in ``tz``
    (the corridor's local zone; ``None`` → UTC) and are semantically **identical** to
    :func:`timebins.filter_date_range` applied after ``io.to_local`` — see
    :func:`_date_bounds_utc`. An over-narrow range yields an empty (but still typed)
    frame.
    """
    row = _area_row(con, area_key)
    if row is None:
        raise KeyError(f"No area {area_key!r} in the store.")
    bin_minutes = _resolve_bin(con, area_key, bin_minutes)
    obs = _obs_table(area_key)
    where = [f'"{BIN_COL}" = ?']
    params: list = [bin_minutes]
    lo, hi = _date_bounds_utc(date_start, date_end, tz)
    if lo is not None:
        where.append(f'"{DATETIME_COL}" >= ?')
        params.append(lo)
    if hi is not None:
        where.append(f'"{DATETIME_COL}" < ?')
        params.append(hi)
    df = con.execute(
        f'SELECT * EXCLUDE ("{BIN_COL}") FROM "{obs}" WHERE {" AND ".join(where)}',
        params).df()
    if DATETIME_COL in df.columns:
        col = df[DATETIME_COL]
        # DuckDB reads TIMESTAMPTZ in the (UTC-pinned) session zone; normalize to the
        # canonical dtype. Guard the empty case (an over-narrow range may return 0
        # rows as a tz-naive column) by localizing before converting.
        if getattr(col.dtype, "tz", None) is not None:
            col = col.dt.tz_convert("UTC")
        else:
            col = col.dt.tz_localize("UTC")
        df[DATETIME_COL] = col.astype("datetime64[ns, UTC]")
    df.attrs["units"] = {"speed": row.get("units_speed"),
                         "travel_time": row.get("units_travel_time")}
    return df


def area_local_span(con, area_key: str, tz=None) -> tuple:
    """The area's **untrimmed** span as ``(first_date, last_date)`` local calendar
    dates (Item 25) — read from the ``_areas`` registry's UTC ``date_min`` /
    ``date_max`` and converted into ``tz`` (``None`` → UTC).

    Used by the GUI to bound the Restrict-dates picker on a DB load: with the date
    push-down the loaded frame no longer carries the full range, so the picker's
    widen-again bounds come from the registry, not the (possibly trimmed) frame.
    Returns ``(None, None)`` when the area has no recorded span. Raises
    :class:`KeyError` for an unknown area."""
    row = _area_row(con, area_key)
    if row is None:
        raise KeyError(f"No area {area_key!r} in the store.")
    zone = tz or "UTC"

    def _local_date(v):
        if v is None or pd.isna(v):
            return None
        ts = pd.Timestamp(v)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        return ts.tz_convert(zone).date()

    return (_local_date(row.get("date_min")), _local_date(row.get("date_max")))


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


# ---------------------------------------------------------------------------
# Friendly names (Item 27): global, last-write-wins
# ---------------------------------------------------------------------------
def save_names(con, names_df: pd.DataFrame, *, updated_at: datetime | None = None) -> int:
    """Upsert friendly segment names into the global :data:`NAMES_TABLE`,
    **last-write-wins** per ``Segment ID`` — an edit overwrites the stored name
    (unlike the keep-first observation merge). A **blank** name *clears* the override
    (the row is deleted), so the segment falls back to its INRIX seed on next load,
    matching ``names.apply_names``'s blank-means-unset rule.

    ``names_df`` needs a ``Segment ID`` and a ``name`` column (``inrix_label``
    optional). Names are keyed **globally** by ``Segment ID`` (INRIX ids are globally
    unique), so a name saved once applies in every area. Returns the number of names
    written (blanks/clears not counted)."""
    _ensure_registry(con)
    if SEGMENT_COL not in names_df.columns or _NAME_COL not in names_df.columns:
        raise ValueError(
            f"names_df must have '{SEGMENT_COL}' and '{_NAME_COL}' columns; "
            f"got {list(names_df.columns)}")
    ts = updated_at or datetime.now(timezone.utc)
    has_label = _INRIX_LABEL_COL in names_df.columns
    written = 0
    for _, r in names_df.iterrows():
        sid = int(r[SEGMENT_COL])
        raw = r[_NAME_COL]
        name = "" if pd.isna(raw) else str(raw).strip()
        if not name:  # a cleared name removes the override (fall back to the seed)
            con.execute(f'DELETE FROM "{NAMES_TABLE}" WHERE "{SEGMENT_COL}" = ?', [sid])
            continue
        label = r[_INRIX_LABEL_COL] if has_label else None
        label = "" if (label is None or pd.isna(label)) else str(label)
        con.execute(
            f'INSERT INTO "{NAMES_TABLE}" VALUES (?,?,?,?) '
            f'ON CONFLICT ("{SEGMENT_COL}") DO UPDATE SET '
            f'"{_NAME_COL}" = excluded."{_NAME_COL}", '
            f'"{_INRIX_LABEL_COL}" = excluded."{_INRIX_LABEL_COL}", '
            f'updated_at = excluded.updated_at',
            [sid, name, label, ts])
        written += 1
    return written


def load_names(con) -> pd.DataFrame:
    """The stored friendly names as a ``Segment ID``-indexed frame (columns ``name``,
    ``inrix_label``) — the shape :func:`names.apply_names` layers over the seed. An
    empty (but typed) frame when nothing has been saved.

    Does **not** create the table (unlike the write path) so it is safe on a
    read-only connection and never writes to the store as a side effect of a load."""
    if not _table_exists(con, NAMES_TABLE):
        return pd.DataFrame(
            {_NAME_COL: pd.Series([], dtype="object"),
             _INRIX_LABEL_COL: pd.Series([], dtype="object")},
            index=pd.Index([], name=SEGMENT_COL, dtype="int64"))
    df = con.execute(
        f'SELECT "{SEGMENT_COL}", "{_NAME_COL}", "{_INRIX_LABEL_COL}" '
        f'FROM "{NAMES_TABLE}"').df()
    if len(df):
        df[SEGMENT_COL] = df[SEGMENT_COL].astype("int64")
    return df.set_index(SEGMENT_COL)


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
                 with_geometry: bool = True, date_start=None, date_end=None,
                 tz=None) -> StoredDataset:
    """Load everything cached for an area + bin-length in one call.

    Returns a :class:`StoredDataset` whose ``df`` / ``metadata`` equal the file
    loaders' output and whose ``geo`` is the cached (already-joined) geometry — so a
    session runs from the DB without re-parsing the export or repeating the spatial
    join. Pass ``with_geometry=False`` to skip the geo read.

    ``date_start`` / ``date_end`` / ``tz`` (Item 25) restrict ``df`` to an inclusive
    local calendar-date range, pushed down into the scan — see :func:`load_export`.
    """
    row = _area_row(con, area_key)
    if row is None:
        raise KeyError(f"No area {area_key!r} in the store.")
    bin_minutes = _resolve_bin(con, area_key, bin_minutes)
    df = load_export(con, area_key, bin_minutes,
                     date_start=date_start, date_end=date_end, tz=tz)
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
