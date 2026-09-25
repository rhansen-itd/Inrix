"""Tests for the statewide ranking comparison runner (ROADMAP Item 58).

Wiring only — ``screen.ranking_changes`` is tested in ``test_screen``. Pinned here: both
tables are compared when present, one missing side is skipped, and the files land where
earlier items put theirs (``<tag>_{peak,7day}_ranking_changes.csv`` beside the re-run).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import compare_statewide_rankings as csr  # noqa: E402


def _table(ranks, vhd):
    return pd.DataFrame({"district": 3, "corridor_group": ["a", "b"],
                         "group_name": ["A", "B"], "statewide_rank": ranks,
                         "rank": ranks, "vhd": vhd, "vhd_per_mile": vhd})


def test_compare_writes_the_tagged_tables(tmp_path):
    before, after = tmp_path / "pre", tmp_path / "post"
    before.mkdir(), after.mkdir()
    _table([1, 2], [100.0, 50.0]).to_csv(before / csr.TABLES["peak"], index=False)
    _table([2, 1], [10.0, 20.0]).to_csv(after / csr.TABLES["peak"], index=False)
    out = csr.compare(before, after, "item99")
    assert set(out) == {"peak"}                         # no 7-day table on either side
    written = pd.read_csv(after / "item99_peak_ranking_changes.csv")
    assert list(written["corridor_group"]) == ["b", "a"]
    assert out["peak"].attrs["spearman_rho"] == pytest.approx(-1.0)
    text = csr.summary("peak", out["peak"])
    assert "Spearman rho -1.000" in text and "top 10: 2 kept" in text
