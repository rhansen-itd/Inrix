#!/usr/bin/env python3
"""Compare a statewide screening re-run against its pre-run outputs.  (ROADMAP Item 58)

Wiring only: ``screen.ranking_changes`` computes. Reads the ranked statewide tables
(``statewide_{peak,7day}_corridor_rankings.csv``) from ``--before`` and ``--after``,
writes ``<tag>_{peak,7day}_ranking_changes.csv`` into ``--after`` and prints Spearman's
rho and the top-N churn. Earlier items' tables (``item54_*``) were built by hand in
the same shape; this makes the next one reproducible.

Usage::

    python scripts/compare_statewide_rankings.py \\
        --before out/statewide_screening/pre_item58 \\
        --after out/statewide_screening --tag item58
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import screen  # noqa: E402

TABLES = {"peak": "statewide_peak_corridor_rankings.csv",
          "7day": "statewide_7day_corridor_rankings.csv"}


def compare(before_dir: Path, after_dir: Path, tag: str, *, write: bool = True) -> dict:
    """``{label: ranking_changes frame}`` for every table present on both sides."""
    out = {}
    for label, fname in TABLES.items():
        b, a = before_dir / fname, after_dir / fname
        if not (b.exists() and a.exists()):
            print(f"{label}: {fname} missing on one side, skipped")
            continue
        changes = screen.ranking_changes(pd.read_csv(b), pd.read_csv(a))
        if write:
            path = after_dir / f"{tag}_{label}_ranking_changes.csv"
            changes.to_csv(path, index=False)
            changes.attrs["path"] = str(path)
        out[label] = changes
    return out


def summary(label: str, changes: pd.DataFrame) -> str:
    at = changes.attrs
    lines = [f"{label}: Spearman rho {at['spearman_rho']:.3f} over {at['n_common']} "
             f"corridors (before {at['n_before']}, after {at['n_after']}); median VHD "
             f"ratio {at['median_ratio']:.3f}; largest move {at['max_abs_rank_change']:.0f}"]
    for n, c in at["top_n"].items():
        lines.append(f"  top {n}: {c['kept']} kept"
                     + (f"; entered {', '.join(c['entered'])}" if c["entered"] else "")
                     + (f"; left {', '.join(c['left'])}" if c["left"] else ""))
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--before", required=True, type=Path)
    p.add_argument("--after", required=True, type=Path)
    p.add_argument("--tag", required=True, help="file prefix, e.g. item58")
    args = p.parse_args(argv)
    for label, changes in compare(args.before, args.after, args.tag).items():
        print(summary(label, changes))
        print(f"  -> {changes.attrs['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
