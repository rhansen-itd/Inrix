#!/usr/bin/env python3
"""Ingest timezone-grouped backfill exports into per-district DuckDB stores.

The owner downloaded backfill segments grouped by timezone to minimize downloads:
  - Pacific: Backfill-Pacific_2026-01-01_to_2026-09-01_15_min_part_1.zip (D1 and D2)
  - Mountain: Backfill-Mountain_2026-01-01_to_2026-09-01_15_min_part_1.zip (D4, D5, and D6)

This script loads each district's appropriate segments (per out/export_reconciliation/d{N}/segments_to_add.txt)
into that district's dedicated store (d{N}_store.duckdb), relabels the corridor to "D{N}",
and leaves the raw zip files intact on the external USB storage.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import io, store
from scripts import reconcile_export_segments

DISTRICT_CONFIG = {
    1: {
        "tz_group": "Pacific",
        "zip_name": "Backfill-Pacific_2026-01-01_to_2026-09-01_15_min_part_1.zip",
        "corridor": "D1",
        "db_name": "d1_store.duckdb",
        "reconcile_dir": "d1",
        "highways_dir": "out/highways/district_1",
        "master_name": "District_1_ALL_Highways.txt",
        "prev_master": "out/highways/pre_item48/District_1_ALL_Highways.txt",
    },
    2: {
        "tz_group": "Pacific",
        "zip_name": "Backfill-Pacific_2026-01-01_to_2026-09-01_15_min_part_1.zip",
        "corridor": "D2",
        "db_name": "d2_store.duckdb",
        "reconcile_dir": "d2",
        "highways_dir": "out/highways/district_2",
        "master_name": "District_2_ALL_Highways.txt",
        "prev_master": "out/highways/pre_item48/District_2_ALL_Highways.txt",
    },
    4: {
        "tz_group": "Mountain",
        "zip_name": "Backfill-Mountain_2026-01-01_to_2026-09-01_15_min_part_1.zip",
        "corridor": "D4",
        "db_name": "d4_store.duckdb",
        "reconcile_dir": "d4",
        "highways_dir": "out/highways/district_4",
        "master_name": "District_4_ALL_Highways.txt",
        "prev_master": "out/highways/pre_item48/District_4_ALL_Highways.txt",
    },
    5: {
        "tz_group": "Mountain",
        "zip_name": "Backfill-Mountain_2026-01-01_to_2026-09-01_15_min_part_1.zip",
        "corridor": "D5",
        "db_name": "d5_store.duckdb",
        "reconcile_dir": "d5",
        "highways_dir": "out/highways/district_5",
        "master_name": "District_5_ALL_Highways.txt",
        "prev_master": "out/highways/pre_item48/District_5_ALL_Highways.txt",
    },
    6: {
        "tz_group": "Mountain",
        "zip_name": "Backfill-Mountain_2026-01-01_to_2026-09-01_15_min_part_1.zip",
        "corridor": "D6",
        "db_name": "d6_store.duckdb",
        "reconcile_dir": "d6",
        "highways_dir": "out/highways/district_6",
        "master_name": "District_6_ALL_Highways.txt",
        "prev_master": "out/highways/pre_item48/District_6_ALL_Highways.txt",
    },
}


def resolve_export_file(zip_name: str, usb_dirs: list[Path]) -> Path:
    """Locate the export file across possible mount / staging locations."""
    for d in usb_dirs:
        candidate = d / zip_name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Export zip {zip_name} not found in search paths: {[str(d) for d in usb_dirs]}"
    )


def load_segments_to_add(reconcile_base: Path, district: int) -> list[int]:
    """Read the segments to add for this district."""
    seg_file = reconcile_base / f"d{district}" / "segments_to_add.txt"
    if not seg_file.exists():
        raise FileNotFoundError(f"Missing segments_to_add.txt: {seg_file}")
    tokens = seg_file.read_text().strip().split(",")
    return [int(t.strip()) for t in tokens if t.strip()]


def ingest_district_backfill(district: int, usb_dirs: list[Path],
                             reconcile_base: Path, db_dir: Path,
                             chunk_bytes: int = store.STREAM_CHUNK_BYTES) -> dict:
    """Ingest backfill segments into a single district store."""
    cfg = DISTRICT_CONFIG[district]
    zip_path = resolve_export_file(cfg["zip_name"], usb_dirs)
    segs = load_segments_to_add(reconcile_base, district)
    db_path = db_dir / cfg["db_name"]
    if not db_path.exists():
        raise FileNotFoundError(f"District {district} database not found: {db_path}")

    print("\n" + "=" * 65)
    print(f"INGESTING BACKFILL FOR DISTRICT {district}: {db_path.name}")
    print("=" * 65)
    print(f"Timezone group:  {cfg['tz_group']}")
    print(f"Source export:   {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")
    print(f"Target DB:       {db_path}")
    print(f"Corridor label:  {cfg['corridor']}")
    print(f"Segments to add: {len(segs)} segments: {segs[:5]}{'...' if len(segs) > 5 else ''}")

    t0 = time.perf_counter()
    con = store.connect(db_path)
    try:
        areas = con.execute("SELECT area_key, area_name, n_segments FROM _areas").fetchall()
        if not areas:
            raise ValueError(f"No areas registered in {db_path}")
        area_key, area_name, n_seg_before = areas[0]
        obs_tbl = store._obs_table(area_key)
        rows_before = con.execute(f'SELECT count(*) FROM "{obs_tbl}"').fetchone()[0]

        info = store.ingest_export_streaming(
            con, zip_path, corridor_name=cfg["corridor"], segment_ids=segs,
            chunk_bytes=chunk_bytes
        )

        rows_after = con.execute(f'SELECT count(*) FROM "{obs_tbl}"').fetchone()[0]
        areas_after = con.execute("SELECT area_key, area_name, n_segments, date_min, date_max FROM _areas").fetchall()
        n_seg_after = areas_after[0][2]
        observed = set(store.area_segments(con, area_key))
    finally:
        con.close()

    elapsed = time.perf_counter() - t0
    rows_added = info.get("n_rows_added", 0)
    missing = set(segs) - observed

    print(f"\nDistrict {district} Ingest Summary ({elapsed:.1f}s):")
    print(f"  Rows added:     {rows_added:,} (DB total: {rows_before:,} -> {rows_after:,})")
    print(f"  Segments:       {n_seg_before:,} -> {n_seg_after:,} (+{len(segs)})")
    print(f"  Unobserved:     {len(missing)} (expected 0)")
    if missing:
        print(f"  WARNING: {len(missing)} segments still missing: {sorted(missing)}")

    return {
        "district": district,
        "rows_added": rows_added,
        "n_seg_before": n_seg_before,
        "n_seg_after": n_seg_after,
        "missing": missing,
        "elapsed": elapsed,
    }


def verify_district_reconciliation(district: int, reconcile_base: Path, db_dir: Path) -> dict:
    """Run reconciliation check to confirm all requested segments are now observed."""
    cfg = DISTRICT_CONFIG[district]
    db_path = db_dir / cfg["db_name"]
    out_dir = reconcile_base / cfg["reconcile_dir"]

    parser = reconcile_export_segments.build_parser()
    args = parser.parse_args([
        "--db", str(db_path),
        "--district", str(district),
        "--highways-dir", str(cfg["highways_dir"]),
        "--master", str(Path(cfg["highways_dir"]) / cfg["master_name"]),
        "--previous-master", str(cfg["prev_master"]),
        "--out-dir", str(out_dir),
    ])

    print(f"\n--- Verifying District {district} Reconciliation ---")
    result = reconcile_export_segments.run(args)
    inv = result["inventory"]
    totals = inv.attrs["totals"]
    changes = result["changes"]

    print(f"  Observed in store:         {totals['n_observed']}")
    print(f"  Requested, returned none:  {totals['requested_not_returned']}")
    print(f"  Observed, not requested:   {totals['observed_not_requested']}")
    print(f"  New requests remaining:    {len(changes['add'])}")

    assert totals["requested_not_returned"] == 0, (
        f"District {district} still has {totals['requested_not_returned']} unreturned requests!"
    )
    assert len(changes["add"]) == 0, (
        f"District {district} still has {len(changes['add'])} unreturned add requests!"
    )

    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--districts", default="1,2,4,5,6",
                        help="Comma-separated district numbers to process (default: 1,2,4,5,6)")
    parser.add_argument("--usb-dir", default="/mnt/chromeos/removable/32GB_PNY",
                        help="Primary path to USB drive (default: /mnt/chromeos/removable/32GB_PNY)")
    parser.add_argument("--staging-dir", default="/tmp",
                        help="Fallback staging directory for zip files (default: /tmp)")
    parser.add_argument("--reconciliation-dir", default="out/export_reconciliation",
                        help="Base directory containing export reconciliation outputs")
    parser.add_argument("--db-dir", default=".",
                        help="Directory containing per-district DuckDB files (default: .)")
    parser.add_argument("--chunk-bytes", type=int, default=store.STREAM_CHUNK_BYTES,
                        help=f"Streaming chunk size in bytes (default: {store.STREAM_CHUNK_BYTES})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate inputs and segments without modifying databases")
    args = parser.parse_args()

    usb_dirs = [Path(args.usb_dir), Path(args.staging_dir)]
    reconcile_base = Path(args.reconciliation_dir)
    db_dir = Path(args.db_dir)
    dist_list = [int(x.strip()) for x in args.districts.split(",") if x.strip()]

    print("Backfill Segment Ingestion Pipeline")
    print(f"Districts to process: {dist_list}")
    print(f"USB / Staging search paths: {[str(d) for d in usb_dirs]}")
    print(f"Reconciliation base: {reconcile_base.resolve()}")
    print(f"Database dir:        {db_dir.resolve()}")

    for d in dist_list:
        if d not in DISTRICT_CONFIG:
            raise ValueError(f"District {d} not in config. Valid: {list(DISTRICT_CONFIG.keys())}")
        cfg = DISTRICT_CONFIG[d]
        zpath = resolve_export_file(cfg["zip_name"], usb_dirs)
        segs = load_segments_to_add(reconcile_base, d)
        db_path = db_dir / cfg["db_name"]
        print(f"  D{d}: {len(segs)} segments from {zpath.name} -> {db_path.name}")

    if args.dry_run:
        print("\n[Dry Run] All district configurations and inputs verified. No changes made.")
        return 0

    t_total = time.perf_counter()
    results = []
    for d in dist_list:
        res = ingest_district_backfill(
            d, usb_dirs, reconcile_base, db_dir, chunk_bytes=args.chunk_bytes
        )
        verify_district_reconciliation(d, reconcile_base, db_dir)
        results.append(res)

    elapsed_total = time.perf_counter() - t_total
    print("\n" + "=" * 65)
    print(f"ALL {len(results)} DISTRICTS SUCCESSFULLY INGESTED & RECONCILED")
    print(f"Total pipeline time: {elapsed_total:.1f}s ({elapsed_total/60:.2f} min)")
    print("=" * 65)
    for r in results:
        print(f"  District {r['district']}: +{r['rows_added']:,} rows, "
              f"{r['n_seg_before']} -> {r['n_seg_after']} segments (0 missing)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
