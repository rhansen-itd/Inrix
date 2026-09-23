"""Tests for statewide and per-district highway inventories.

``out/`` is generated output and gitignored, so these assert on artifacts that a
fresh checkout does not have. They skip when the inventories are absent —
regenerate them with ``scripts/generate_district_highway_inventories.py``.
"""
from pathlib import Path
import pytest

HIGHWAYS_DIR = Path(__file__).resolve().parents[1] / "out" / "highways"

STATEWIDE_MASTER = HIGHWAYS_DIR / "Statewide_ALL_Highways.txt"
DISTRICT_MASTERS = [HIGHWAYS_DIR / f"District_{d}_ALL_Highways.txt" for d in range(1, 7)]
D2_TZ_SPLIT = [HIGHWAYS_DIR / f"District_2_{z}_ALL_Highways.txt"
               for z in ("Pacific", "Mountain")]

_HAVE_DISTRICTS = all(p.exists() for p in DISTRICT_MASTERS)
_HAVE_STATEWIDE = _HAVE_DISTRICTS and STATEWIDE_MASTER.exists()
_HAVE_D2_TZ = all(p.exists() for p in D2_TZ_SPLIT + DISTRICT_MASTERS[1:2])

_REASON = "highway inventories not generated (out/ is gitignored)"


def _ids(path: Path) -> set[int]:
    return {int(x) for x in path.read_text().split(",") if x.strip()}


pytestmark = pytest.mark.skipif(not HIGHWAYS_DIR.exists(), reason=_REASON)


@pytest.mark.skipif(not _HAVE_DISTRICTS, reason=_REASON)
def test_district_master_files_exist():
    for d, p in enumerate(DISTRICT_MASTERS, start=1):
        assert p.read_text().strip(), f"District {d} master file empty: {p}"
        assert len(_ids(p)) > 1000, f"District {d} has unexpectedly few segments"


@pytest.mark.skipif(not _HAVE_STATEWIDE, reason=_REASON)
def test_statewide_master_file_exists():
    statewide_ids = _ids(STATEWIDE_MASTER)
    assert len(statewide_ids) > 15000, f"Statewide set unexpectedly small: {len(statewide_ids)}"

    # Every district ID must be in statewide set
    for d, dp in enumerate(DISTRICT_MASTERS, start=1):
        assert _ids(dp).issubset(statewide_ids), f"District {d} has IDs not in Statewide master"


@pytest.mark.skipif(not _HAVE_DISTRICTS, reason=_REASON)
def test_district_subdirectories_contain_summaries():
    for d in range(1, 7):
        d_dir = HIGHWAYS_DIR / f"district_{d}"
        assert d_dir.exists(), f"District directory missing: {d_dir}"
        summary_csv = d_dir / f"district_{d}_highways_summary.csv"
        assert summary_csv.exists(), f"Summary CSV missing for District {d}"


@pytest.mark.skipif(not _HAVE_D2_TZ, reason=_REASON)
def test_district_2_timezone_split():
    """D2 straddles the Pacific/Mountain line, so its segments partition exactly.

    The split is asserted as a *partition* rather than by exact counts, which would
    pin the test to one XD network snapshot.
    """
    d2_all = _ids(DISTRICT_MASTERS[1])
    d2_pt, d2_mt = (_ids(p) for p in D2_TZ_SPLIT)

    assert d2_pt and d2_mt, "both timezone halves should be non-empty"
    assert d2_pt | d2_mt == d2_all
    assert not (d2_pt & d2_mt)
    # The Mountain sliver is the small side of the line.
    assert len(d2_mt) < len(d2_pt)
