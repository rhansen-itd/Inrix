"""Tests for ROADMAP Item 67's statewide wiring: the statewide runner's scenario list
and the statewide maps' loop over scenarios. The district runner and the aggregate have
their own tests; the rendering itself is ``run_district_screening``'s, tested there."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))

pytest.importorskip("geopandas")
import run_statewide_screening as rss  # noqa: E402

from scripts import generate_statewide_maps as gsm  # noqa: E402


def test_the_scenario_list_is_validated_up_front():
    assert rss.scenario_list("peak, day_7d,sat:summer") == ["peak", "day_7d", "sat:summer"]
    assert rss.scenario_list("x", "both") == ["peak", "day_7d"]       # the old spelling
    with pytest.raises(KeyError):
        rss.scenario_list("peak,tuesday")
    with pytest.raises(ValueError):
        rss.scenario_list("sat,sat")
    with pytest.raises(SystemExit, match="retired"):
        rss.scenario_list("peak", "rec")


def test_scenario_file_names():
    assert gsm.scenario_frames("peak") == ("segment_peak_screen.parquet",
                                           "segment_peak_curve_vhd.parquet")
    assert gsm.scenario_frames("7day") == ("segment_7day_screen.parquet",
                                           "segment_7day_curve_vhd.parquet")
    assert gsm.scenario_frames("sat_summer")[1] == "segment_sat_summer_curve_vhd.parquet"
    assert gsm.statewide_map_names("peak") == ("statewide_peak_map.html",
                                               "statewide_vhd_map.html")
    assert gsm.statewide_map_names("7day") == ("statewide_7day_map.html",
                                               "statewide_7day_vhd_map.html")
    assert gsm.statewide_map_names("sun") == ("statewide_sun_map.html",
                                              "statewide_sun_vhd_map.html")


def test_the_maps_loop_over_every_registered_scenario(tmp_path, monkeypatch):
    from inrix_tools import screen
    sat = screen.resolve_scenario("sat:summer")
    (tmp_path / "d1").mkdir()
    (tmp_path / "d1" / "screening_scenarios.json").write_text(json.dumps({
        "peak": {"label": "Peak", "scenario": screen.resolve_scenario("peak").to_dict(),
                 "files": {}},
        sat.file_tag: {"label": sat.label, "scenario": sat.to_dict(), "files": {}}}))
    pd.DataFrame({"statewide_rank": [1], "corridor_group": ["g"]}).to_csv(
        tmp_path / "statewide_sat_summer_corridor_rankings.csv", index=False)
    scr = pd.DataFrame({"x": [1]})
    frames = {"peak": (scr, None), "sat_summer": (scr, pd.DataFrame(
        {"Segment ID": [1], "AADT": [100.0]}))}
    seen = {}
    monkeypatch.setattr(gsm, "load_statewide_data",
                        lambda districts, base, tags, **kw: (pd.DataFrame(index=[1]), [],
                                                             {}, {t: frames[t] for t in tags}))

    def _render(out_dir, scr, net, entries, chains, ranks, windows, **kw):
        seen[kw["map_filename"]] = (list(windows), ranks, kw["window_label"])
        path = Path(out_dir) / kw["map_filename"]
        path.write_text("<html></html>")
        return path

    monkeypatch.setattr(gsm, "generate_statewide_map", _render)
    monkeypatch.setattr(sys, "argv", ["gsm", "--dir", str(tmp_path), "--districts", "1"])
    gsm.main()
    # The peak has no VHD frame: its TTI map only. The summer Saturday gets both,
    # on its own seasonal window and its own statewide ranks.
    assert set(seen) == {"statewide_peak_map.html", "statewide_sat_summer_map.html",
                         "statewide_sat_summer_vhd_map.html"}
    windows, ranks, label = seen["statewide_sat_summer_vhd_map.html"]
    assert windows == ["sat_summer"] and label == sat.label
    assert ranks["g"]["rank"] == 1
    viewer = (tmp_path / "statewide_map_viewer.html").read_text()
    assert "statewide_sat_summer_vhd_map.html" in viewer
