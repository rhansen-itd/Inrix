#!/usr/bin/env python3
"""Build per-district DuckDB stores on SSD from external USB exports.

Streams data directly from compressed zip exports on the USB drive into
dedicated per-district DuckDB files:
  d1_store.duckdb, d2_store.duckdb, d4_store.duckdb, d5_store.duckdb, d6_store.duckdb
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inrix_tools import io, store

DISTRICT_EXPORTS = {
    1: "D1_2026-01-01_to_2026-09-01_15_min_part_1.zip",
    2: "District2_2026-01-01_to_2026-09-01_15_min_part_1.zip",
    4: "District4_2026-01-01_to_2026-09-01_15_min_part_1.zip",
    5: "District5_2026-01-01_to_2026-09-01_15_min_part_1.zip",
    6: "District6_2026-01-01_to_2026-09-01_15_min_part_1.zip",
}


def build_district_store(district: int, usb_dir: Path, out_dir: Path,
                         chunk_bytes: int = store.STREAM_CHUNK_BYTES) -> Path:
    """Ingest a district's export parts into its dedicated DuckDB store."""
    fname = DISTRICT_EXPORTS[district]
    p1 = usb_dir / fname
    if not p1.exists():
        raise FileNotFoundError(f"District {district} part 1 not found: {p1}")

    parts = io._discover_parts(p1)
    db_path = out_dir / f"d{district}_store.duckdb"
    corridor_label = f"D{district}"

    print(f"\n=======================================================")
    print(f"BUILDING DISTRICT {district} STORE: {db_path.name}")
    print(f"=======================================================")
    print(f"Source: {p1}")
    print(f"Discovered {len(parts)} parts:")
    for p in parts:
        print(f"  - {p.name} ({p.stat().st_size / 1e6:.1f} MB)")
    print(f"Target DB: {db_path}")
    print(f"Corridor label: {corridor_label}")

    t0 = time.perf_counter()
    con = store.connect(db_path)
    try:
        info = store.ingest_export_streaming(
            con, p1, corridor_name=corridor_label, chunk_bytes=chunk_bytes
        )
    finally:
        con.close()
    elapsed = time.perf_counter() - t0

    db_size_mb = db_path.stat().st_size / 1e6
    rows = info.get("n_rows_added", 0)
    rate = rows / elapsed if elapsed > 0 else 0

    print(f"District {district} completed in {elapsed:.1f}s ({elapsed/60:.2f} min):")
    print(f"  Rows added:  {rows:,}")
    print(f"  Throughput:  {rate:,.0f} rows/s")
    print(f"  DB Size:     {db_size_mb:.1f} MB ({db_size_mb/1e3:.2f} GB)")
    print(f"  Area Key:    {info.get('area_key')}")
    print(f"  Parts covered: {info.get('n_parts')}")

    return db_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--districts", default="1,2,4,5,6",
                        help="Comma-separated district numbers to build (default: 1,2,4,5,6)")
    parser.add_argument("--usb-dir", default="/mnt/chromeos/removable/32GB_PNY",
                        help="Path to directory containing export zip files")
    parser.add_argument("--out-dir", default=".",
                        help="Output directory for DuckDB files (default: project root)")
    parser.add_argument("--chunk-bytes", type=int, default=store.STREAM_CHUNK_BYTES,
                        help=f"Streaming chunk size in bytes (default: {store.STREAM_CHUNK_BYTES})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate export files without ingesting")
    args = parser.parse_args()

    usb_dir = Path(args.usb_dir)
    out_dir = Path(args.out_dir)
    dist_list = [int(x.strip()) for x in args.districts.split(",") if x.strip()]

    print(f"Per-District DuckDB Store Builder")
    print(f"USB Directory: {usb_dir}")
    print(f"Output Directory (SSD): {out_dir.resolve()}")
    print(f"Districts to process: {dist_list}")

    for d in dist_list:
        if d not in DISTRICT_EXPORTS:
            raise ValueError(f"Unknown district: {d}. Valid: {list(DISTRICT_EXPORTS.keys())}")
        p1 = usb_dir / DISTRICT_EXPORTS[d]
        if not p1.exists():
            raise FileNotFoundError(f"Missing export for District {d}: {p1}")

    if args.dry_run:
        print("\n[Dry Run] All district exports verified successfully. No databases created.")
        return 0

    total_t0 = time.perf_counter()
    built = []
    for d in dist_list:
        db_path = build_district_store(d, usb_dir, out_dir, chunk_bytes=args.chunk_bytes)
        built.append(db_path)

    total_elapsed = time.perf_counter() - total_t0
    print(f"\n=======================================================")
    print(f"ALL {len(built)} DISTRICT STORES BUILT SUCCESSFULLY")
    print(f"Total time: {total_elapsed:.1f}s ({total_elapsed/60:.2f} min)")
    print(f"=======================================================")
    for b in built:
        print(f"  {b.name:20} Size: {b.stat().st_size / 1e6:7.1f} MB")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
