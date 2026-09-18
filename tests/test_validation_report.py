"""Tests for the validation report shell (ROADMAP Item 30).

The load-bearing property is that this layer **computes nothing**: the figures and
the pages place numbers the core produced. Several tests here hand a builder a
summary row whose values are deliberately not what the matched data implies and
assert the figure shows the *row's* number — a builder that recomputed would fail
them. Everything else is shape, labels and flag routing; the statistics are tested
in ``test_agreement`` / ``test_reference``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from gui import validation_figures as vfig
from gui import validation_report as vr
from inrix_tools import agreement
from inrix_tools.reference import ROUTE_COL, TT_COL

TZ = "America/Denver"
SUB, FULL = "Franklin WB - No Mid", "Franklin WB"


# ---------------------------------------------------------------------------
# Fixtures — small, synthetic, dependency-free
# ---------------------------------------------------------------------------
@pytest.fixture
def matched():
    """Two routes, three days of hourly bins, INRIX faster than the reference."""
    stamps = pd.date_range("2026-03-03 06:00", periods=36, freq="1h", tz=TZ)
    rng = np.random.default_rng(7)
    frames_i, frames_r = [], []
    for route, base in (("Eagle Rd NB", 8.0), ("Cascade-HSB", 50.0)):
        frames_i.append(pd.DataFrame({ROUTE_COL: route, "Date Time": stamps,
                                      TT_COL: base + rng.normal(0, .3, len(stamps))}))
        frames_r.append(pd.DataFrame({ROUTE_COL: route, "Date Time": stamps,
                                      TT_COL: base + 2 + rng.normal(0, .3, len(stamps))}))
    return agreement.match_bins(pd.concat(frames_i, ignore_index=True),
                                pd.concat(frames_r, ignore_index=True))


@pytest.fixture
def summary(matched):
    return agreement.compare(None, None, matched=matched)


@pytest.fixture
def row(summary):
    return summary.set_index(ROUTE_COL).loc["Eagle Rd NB"]


class _Chain:
    """The bits of ``corridors.ChainResult`` the report reads."""

    def __init__(self, *, reached=True, stop="target", miles=7.0, requested=7.0,
                 snap=(5.0, 4.0), n=12):
        self.reached_target, self.stop_reason = reached, stop
        self.chain_miles, self.requested_miles = miles, requested
        self.snap_start_feet, self.snap_end_feet = snap
        self.n_segments = n

    @property
    def length_ratio(self):
        return self.chain_miles / self.requested_miles


# ---------------------------------------------------------------------------
# Labels (G10)
# ---------------------------------------------------------------------------
def test_no_route_label_repeats_the_retired_reports_mistakes():
    """The two captions Session 33 caught: a route number Idaho does not have,
    and a Nampa corridor described as spanning two cities."""
    text = " | ".join(f"{i.title} {i.group_title}" for i in vr.ROUTE_INFO.values())
    assert "SH-17" not in text
    assert "Caldwell" not in text
    assert "Nampa" in vr.ROUTE_INFO["Franklin EB"].group_title


def test_label_disagreements_catches_a_route_number_the_network_denies():
    descriptions = {"Garden Valley-HSB": {"RoadName": ("Banks Lowman Hwy", "ID-55"),
                                          "RoadNumber": ("55",)}}
    info = {"Garden Valley-HSB": vr.RouteInfo(
        "g", "Rural Highway (SH-17)", "Garden Valley", vr.RURAL)}
    got = vr.label_disagreements(descriptions, info)
    assert list(got["claimed"]) == ["SH-17"]
    assert got.iloc[0]["on_network"] == "55"


def test_label_disagreements_accepts_sh_and_id_as_the_same_route():
    """"SH-55" and "ID-55" are the same Idaho highway; only a different number
    is a disagreement."""
    descriptions = {"Eagle Rd NB": {"RoadName": ("S Eagle Rd",), "RoadNumber": ("55",)}}
    info = {"Eagle Rd NB": vr.RouteInfo("e", "Eagle Road (SH-55)", "Eagle Rd NB",
                                        vr.ARTERIAL)}
    assert vr.label_disagreements(descriptions, info).empty


def test_label_disagreements_is_clean_on_the_shipped_labels():
    descriptions = {
        "Eagle Rd NB": {"RoadName": ("S Eagle Rd",), "RoadNumber": ("55",)},
        "Franklin EB": {"RoadName": ("Franklin Rd",), "RoadNumber": ("20",)},
        "Cascade-HSB": {"RoadName": ("ID-55",), "RoadNumber": ("55",)},
    }
    assert vr.label_disagreements(descriptions).empty


# ---------------------------------------------------------------------------
# Gate flags (G3 / G4 / G7 / G8)
# ---------------------------------------------------------------------------
def _summary_of(*routes):
    return pd.DataFrame({ROUTE_COL: list(routes), "bias": [-1.0] * len(routes),
                         "delay_ratio": [0.5] * len(routes),
                         "sd_ratio": [0.5] * len(routes)})


def test_impossible_reference_data_excludes_a_route_and_notes_its_parent():
    nesting = pd.DataFrame([{"sub_route": SUB, "full_route": FULL, "n_shared_bins": 4524,
                             "mean_sub": 6.40, "mean_full": 5.68, "mean_diff": 0.72,
                             "frac_sub_exceeds_full": 0.9993, "max_excess": 5.6,
                             "violates": True}])
    flags = vr.gate_flags(_summary_of(SUB, FULL), nesting=nesting)
    assert flags[SUB].excluded and "internally impossible" in flags[SUB].reasons[0]
    assert "99.9" in flags[SUB].reasons[0]          # the rate is in the footnote
    assert not flags[FULL].excluded and flags[FULL].notes    # the parent is noted
    assert vr.headline_routes(flags) == [FULL]


def test_a_clean_nesting_pair_flags_nothing():
    nesting = pd.DataFrame([{"sub_route": SUB, "full_route": FULL, "n_shared_bins": 4880,
                             "mean_sub": 4.06, "mean_full": 5.51, "mean_diff": -1.44,
                             "frac_sub_exceeds_full": 0.0, "max_excess": -0.4,
                             "violates": False}])
    flags = vr.gate_flags(_summary_of(SUB, FULL), nesting=nesting)
    assert not any(f.excluded for f in flags.values())


def test_thin_and_partial_coverage_become_notes_not_exclusions():
    coverage = pd.DataFrame([{ROUTE_COL: "Eagle Rd NB", "n_days": 16, "n_obs": 352,
                              "first": pd.Timestamp("2026-01-02"),
                              "last": pd.Timestamp("2026-01-26"),
                              "window_covered_fraction": 0.07, "covers_window": False}])
    flags = vr.gate_flags(_summary_of("Eagle Rd NB"), coverage=coverage)
    f = flags["Eagle Rd NB"]
    assert not f.excluded
    assert any("16 days" in n for n in f.notes)
    assert any("7%" in n for n in f.notes)
    assert any("does not span the export window" in n for n in f.notes)


def test_a_chain_that_never_reached_its_target_is_excluded():
    """The INRIX side is then summing some other extent, so the row cannot carry
    a headline however good its statistics look."""
    chains = {"Garden Valley-HSB": _Chain(reached=False, stop="dead_end",
                                          miles=11.08, requested=11.08)}
    flags = vr.gate_flags(_summary_of("Garden Valley-HSB"), chains=chains)
    f = flags["Garden Valley-HSB"]
    assert f.excluded and "dead_end" in f.reasons[0] and "11.08" in f.reasons[0]


def test_overshoot_and_missing_members_are_notes(matched):
    chains = {"Eagle Rd NB": _Chain(miles=3.64, requested=3.01)}
    cov = pd.DataFrame({"Segment ID": [1, 2]})
    cov.attrs = {"n_missing": 3, "missing_miles": 0.15, "miles_covered_fraction": 0.90}
    flags = vr.gate_flags(_summary_of("Eagle Rd NB"), chains=chains,
                          chain_coverage={"Eagle Rd NB": cov},
                          extent_sources={"Eagle Rd NB": "snapped query points"})
    notes = " ".join(flags["Eagle Rd NB"].notes)
    assert "overshoot" in notes and "21%" in notes
    assert "3 chain member segment(s)" in notes and "0.15 mi" in notes
    assert not flags["Eagle Rd NB"].excluded


def test_an_operator_specified_extent_says_so():
    flags = vr.gate_flags(
        _summary_of("Cascade-HSB"),
        extent_sources={"Cascade-HSB": "operator-specified terminal segments"})
    assert any("operator-specified terminal segments" in n
               for n in flags["Cascade-HSB"].notes)


def test_a_lag_that_prefers_a_non_zero_offset_is_noted():
    lag = pd.DataFrame([{ROUTE_COL: "Eagle Rd NB", "best_lag_minutes": 30,
                         "sd_at_best": 0.7, "sd_at_zero": 1.0, "improvement": 0.3,
                         "n_at_zero": 100, "agrees_at_zero": False}])
    flags = vr.gate_flags(_summary_of("Eagle Rd NB"), lag=lag)
    assert any("best alignment at 30 min" in n for n in flags["Eagle Rd NB"].notes)


# ---------------------------------------------------------------------------
# Figures — shape and labels only
# ---------------------------------------------------------------------------
def test_every_builder_returns_a_placeholder_on_an_empty_frame():
    empty = pd.DataFrame(columns=[ROUTE_COL, "bias"])
    for fig in (vfig.bias_forest(empty), vfig.ratio_bars(empty),
                vfig.coverage_timeline(empty), vfig.scatter_1to1(empty, "R"),
                vfig.bland_altman(empty, "R"), vfig.diurnal_profile(empty, "R"),
                vfig.bias_grid(empty, "R"), vfig.daily_bias(empty, "R")):
        assert isinstance(fig, go.Figure) and not fig.data


def test_bias_forest_draws_both_intervals_and_marks_a_flagged_route(summary):
    fig = vfig.bias_forest(summary, labels={"Eagle Rd NB": "Eagle Rd northbound"},
                           flagged=["Cascade-HSB"])
    assert len(fig.data) == 2                      # day-blocked + the naive contrast
    names = list(fig.data[-1].y)
    assert "Eagle Rd northbound" in names          # the label mapping is applied
    assert any(n.startswith("⚠ ") for n in names)  # flagged, not silently dropped
    colors = list(fig.data[-1].marker.color)
    assert vfig.STATUS_CRITICAL in colors          # …and colour is never the only cue
    assert fig.layout.shapes                       # the zero line


def test_bias_forest_plots_the_interval_the_core_computed(summary):
    """A deliberately wrong CI in the frame must appear in the figure: a builder
    that recomputed the interval from the bins would not show it."""
    doctored = summary.copy()
    doctored.loc[doctored[ROUTE_COL] == "Eagle Rd NB", "bias_ci_high"] = 99.0
    fig = vfig.bias_forest(doctored)
    i = list(fig.data[-1].y).index("Eagle Rd NB")
    bias = float(doctored.set_index(ROUTE_COL).loc["Eagle Rd NB", "bias"])
    assert fig.data[-1].error_x.array[i] == pytest.approx(99.0 - bias)


def test_ratio_bars_show_both_ratios_against_parity(summary):
    fig = vfig.ratio_bars(summary)
    assert [t.name for t in fig.data] == ["Delay ratio", "SD ratio"]
    assert fig.layout.shapes and fig.layout.shapes[0].x0 == 1.0
    assert all(t.orientation == "h" for t in fig.data)


def test_scatter_has_no_fitted_line_and_quotes_the_summary_row(matched, row):
    """The retired report fitted an OLS slope inside the figure loop; this one
    draws the 1:1 line and prints the core's numbers."""
    doctored = row.copy()
    doctored["bias"] = -42.0
    fig = vfig.scatter_1to1(matched, "Eagle Rd NB", doctored)
    assert [t.name for t in fig.data] == ["1:1 agreement", "15-minute bin"]
    assert "OLS" not in fig.to_html(include_plotlyjs=False)
    assert "-42.00" in fig.layout.annotations[0].text
    assert fig.layout.yaxis.scaleanchor == "x"      # a 1:1 plot is square


def test_bland_altman_draws_the_limits_from_the_row_not_the_points(matched, row):
    doctored = row.copy()
    doctored["loa_low"], doctored["loa_high"] = -9.0, 1.0
    fig = vfig.bland_altman(matched, "Eagle Rd NB", doctored)
    lines = sorted(s.y0 for s in fig.layout.shapes)
    assert -9.0 in lines and 1.0 in lines


def test_diurnal_profile_draws_both_sources_from_the_profile_frame(matched):
    prof = agreement.profile(matched, by="time_of_day")
    fig = vfig.diurnal_profile(prof, "Eagle Rd NB")
    assert [t.name for t in fig.data] == [vfig.REF_LABEL, vfig.INRIX_LABEL]
    assert list(fig.data[0].x) == sorted(fig.data[0].x)      # ordered by clock
    assert len(fig.data[0].x) == prof[prof[ROUTE_COL] == "Eagle Rd NB"].shape[0]


def test_bias_grid_is_diverging_about_zero(matched):
    prof = agreement.profile(matched, by=["day_of_week", "time_of_day"])
    fig = vfig.bias_grid(prof, "Eagle Rd NB")
    heat = fig.data[0]
    assert heat.zmid == 0 and heat.zmin == pytest.approx(-heat.zmax)


def test_daily_bias_uses_the_day_means(matched):
    prof = agreement.profile(matched, by="date")
    fig = vfig.daily_bias(prof, "Eagle Rd NB")
    assert len(fig.data[0].x) == prof[prof[ROUTE_COL] == "Eagle Rd NB"].shape[0]


def test_stat_box_orders_effect_size_first_and_correlation_last(row):
    text = vfig.stat_box(row)
    assert text.index("Bias:") < text.index("Delay ratio:") < text.index("r:")


def test_missing_numbers_print_as_a_dash_never_nan(row):
    assert vfig._fmt(float("nan")) == "—"
    assert vr.fmt(None) == "—"


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------
@pytest.fixture
def frames(matched, summary):
    from inrix_tools import reference

    ref_obs = matched.rename(columns={agreement.REF_COL: TT_COL})[
        [ROUTE_COL, "Date Time", TT_COL]]
    return {
        "summary": summary,
        "matched": matched,
        "coverage": reference.coverage_gate(ref_obs, window=(matched["Date Time"].min(),
                                                             matched["Date Time"].max())),
        "nesting": pd.DataFrame(columns=["sub_route", "full_route", "n_shared_bins",
                                         "mean_sub", "mean_full", "mean_diff",
                                         "frac_sub_exceeds_full", "max_excess", "violates"]),
        "lag": pd.DataFrame(),
        "totals": agreement.independent_totals(matched),
        "profile_tod": agreement.profile(matched, by="time_of_day"),
        "profile_grid": agreement.profile(matched, by=["day_of_week", "time_of_day"]),
        "profile_date": agreement.profile(matched, by="date"),
        "chains": {"Eagle Rd NB": _Chain(), "Cascade-HSB": _Chain(miles=50.8, requested=50.8,
                                                                 snap=(float("nan"),) * 2)},
        "descriptions": {"Eagle Rd NB": {"RoadName": ("S Eagle Rd",), "RoadNumber": ("55",),
                                         "County": ("Ada",), "road_label": "S Eagle Rd (55)"}},
        "extent_sources": {"Eagle Rd NB": "snapped query points",
                           "Cascade-HSB": "operator-specified terminal segments"},
        "meta": {"tz": TZ, "n_ambiguous_dropped": 36, "workbook": "TT Logger.xlsx",
                 "export_window": (matched["Date Time"].min(), matched["Date Time"].max()),
                 "place_name_routes": ()},
    }


def _no_attrs(frame: pd.DataFrame) -> pd.DataFrame:
    """``compare`` hangs the matched frame on ``attrs``; pandas compares attrs on
    concat and a DataFrame there is not comparable. Test plumbing only."""
    out = frame.copy()
    out.attrs = {}
    return out


def test_build_report_writes_an_index_and_a_page_per_corridor_group(frames):
    pages = vr.build_report(frames)
    assert "index.html" in pages
    assert "eagle_rd.html" in pages and "sh55_mountain.html" in pages
    assert "franklin.html" not in pages              # no Franklin route in this run
    for html in pages.values():
        assert html.startswith("<!DOCTYPE html>") and html.rstrip().endswith("</html>")


def test_every_corridor_page_carries_that_route_s_own_coverage(frames):
    """Not one study-wide banner: each page prints the route's own window, day
    count and measured cadence (G4/G10)."""
    pages = vr.build_report(frames)
    for name in ("eagle_rd.html", "sh55_mountain.html"):
        assert "This route&#x27;s coverage:" in pages[name] or \
               "This route's coverage:" in pages[name]
        assert "median gap" in pages[name]
        assert "days sampled out of a" in pages[name]


def test_the_index_leads_with_bias_and_demotes_correlation(frames):
    html = vr.build_report(frames)["index.html"]
    head = html[html.index("<thead>"):html.index("</thead>")]
    assert head.index("Bias (min)") < head.index("Delay ratio") < head.index(">r<")


def test_an_excluded_route_keeps_its_page_and_carries_the_reason(frames):
    frames = dict(frames)
    frames["chains"] = {**frames["chains"],
                        "Cascade-HSB": _Chain(reached=False, stop="dead_end",
                                              miles=11.08, requested=11.08,
                                              snap=(float("nan"),) * 2)}
    pages = vr.build_report(frames)
    assert "Excluded from the headline" in pages["sh55_mountain.html"]
    assert "dead_end" in pages["sh55_mountain.html"]
    # …and the scorecard still lists it, marked rather than dropped.
    assert "gate failed" in pages["index.html"]


def test_the_report_states_the_measured_cadence_not_fifteen_minutes(frames):
    html = vr.build_report(frames)["index.html"]
    assert "it is not a 15-minute series" in html


def test_the_finding_is_written_from_this_run_s_numbers(frames):
    """The prose is interpolated from the frames, so it cannot drift from the
    tables beneath it. Since Item 32 the claim is the slope, and the worked
    before/after consequence is that slope times the example — both have to be
    this run's numbers, not a sentence carried over from a previous one."""
    html = vr.build_report(frames)["index.html"]
    row = frames["summary"].set_index(ROUTE_COL).loc["Eagle Rd NB"]   # the only arterial
    assert "slope" in html
    assert f"{row['delay_slope']:.3f}" in html
    assert f"{row['delay_ratio']:.2f}" in html            # kept, as the secondary
    assert f"{4.0 * row['delay_slope']:.1f} minutes" in html


def test_the_finding_leads_with_the_slope_and_the_level_gap_separately(frames):
    """Item 32's point: a single bias fuses two effects with opposite consequences
    for a before/after study, so the report must not lead with their sum."""
    html = vr.build_report(frames)["index.html"]
    assert html.index("Delay slope") < html.index("Bias (min)")
    assert html.index("Free-flow gap (min)") < html.index("Bias (min)")
    assert "does not cancel" in html and "cancels in a before/after" in html
    # The ratio survives, demoted and footnoted rather than silently redefined.
    assert "Delay ratio †" in html and "secondary statistic" in html


def test_the_scorecard_prints_the_intervals_the_core_computed(frames):
    html = vr.build_report(frames)["index.html"]
    row = frames["summary"].set_index(ROUTE_COL).loc["Eagle Rd NB"]
    assert f"[{row['delay_slope_ci_low']:.3f}, {row['delay_slope_ci_high']:.3f}]" in html
    assert f"[{row['free_flow_gap_ci_low']:+.2f}, {row['free_flow_gap_ci_high']:+.2f}]" in html


def test_the_delay_regression_figure_draws_the_rows_slope_not_a_refit(matched, row):
    """The load-bearing property of this layer: the fitted line is the core's
    coefficients evaluated, not a ``polyfit`` inside the figure loop — which is
    exactly what the retired report did. Hand it a row whose slope the data
    denies, and the line must follow the row."""
    lying = row.copy()
    lying["delay_slope"], lying["delay_intercept"] = 9.0, -2.0
    fig = vfig.delay_regression(matched, "Eagle Rd NB", lying)
    fitted = [t for t in fig.data if "Fitted in the core" in (t.name or "")]
    assert len(fitted) == 1
    x0, x1 = fitted[0].x
    y0, y1 = fitted[0].y
    assert (y1 - y0) / (x1 - x0) == pytest.approx(9.0)
    assert y0 == pytest.approx(-2.0)


def test_the_delay_regression_figure_refuses_a_row_it_cannot_place(matched):
    fig = vfig.delay_regression(matched, "Eagle Rd NB", None)
    assert "No delay regression" in fig.layout.annotations[0].text


def test_the_slope_and_gap_forests_place_the_summarys_own_values(summary):
    slope_fig = vfig.slope_forest(summary)
    gap_fig = vfig.level_gap_forest(summary)
    by_route = summary.set_index(ROUTE_COL)
    primary = [t for t in slope_fig.data if "Day-blocked" in (t.name or "")][0]
    assert sorted(primary.x) == pytest.approx(sorted(summary["delay_slope"]))
    gap_primary = [t for t in gap_fig.data if "Day-blocked" in (t.name or "")][0]
    assert set(gap_primary.x) == set(summary["free_flow_gap"])
    # …and the interval drawn is the day-blocked one, not the per-bin one.
    route = "Eagle Rd NB"
    i = list(primary.y).index(route)
    assert (primary.x[i] + primary.error_x.array[i]) == pytest.approx(
        by_route.loc[route, "delay_slope_ci_high"])


def test_the_forests_say_so_when_the_columns_are_missing(summary):
    """A summary built before Item 32 must produce a blank with a reason, not a
    traceback in the middle of a report build."""
    older = summary.drop(columns=[c for c in summary.columns if c.startswith("delay_slope")])
    assert "missing the delay-slope" in vfig.slope_forest(older).layout.annotations[0].text
    older = summary.drop(columns=["free_flow_gap"])
    assert "missing the free-flow gap" in vfig.level_gap_forest(older).layout.annotations[0].text


def test_the_route_page_reports_the_level_gap_beside_the_slope(frames):
    html = vr.build_report(frames)["eagle_rd.html"]
    row = frames["summary"].set_index(ROUTE_COL).loc["Eagle Rd NB"]
    assert "Free-flow level gap" in html and "Delay slope" in html
    assert f"{row['free_flow_gap']:+.2f} min" in html
    assert f"r&#178; {row['delay_r2']:.3f}" in html or f"r² {row['delay_r2']:.3f}" in html


def test_unknown_routes_are_shown_on_an_other_page_not_dropped(frames):
    frames = dict(frames)
    extra = frames["summary"].iloc[[0]].copy()
    extra[ROUTE_COL] = "Brand New Sheet"
    grown = pd.concat([frames["summary"].copy().pipe(_no_attrs), _no_attrs(extra)],
                      ignore_index=True)
    grown.attrs = dict(frames["summary"].attrs)
    frames["summary"] = grown
    pages = vr.build_report(frames)
    assert "other.html" in pages and "Brand New Sheet" in pages["other.html"]


# ---------------------------------------------------------------------------
# Splitting the level gap  (ROADMAP Item 33)
# ---------------------------------------------------------------------------
@pytest.fixture
def split_frames(frames):
    """The same run, with the reference carrying its own delay — so the level gap
    can be split into the reference's residual delay and the static remainder."""
    from inrix_tools.reference import EXTRA_TT_COL

    stamps = pd.date_range("2026-03-03 06:00", periods=36, freq="1h", tz=TZ)
    rng = np.random.default_rng(7)
    frames_i, frames_r = [], []
    for route, base in (("Eagle Rd NB", 8.0), ("Cascade-HSB", 50.0)):
        delay = 2.0 + np.abs(rng.normal(0, 0.3, len(stamps)))   # never free flow
        frames_i.append(pd.DataFrame({ROUTE_COL: route, "Date Time": stamps,
                                      TT_COL: base + 0.5 * delay}))
        frames_r.append(pd.DataFrame({ROUTE_COL: route, "Date Time": stamps,
                                      TT_COL: base + delay, EXTRA_TT_COL: delay}))
    matched = agreement.match_bins(pd.concat(frames_i, ignore_index=True),
                                   pd.concat(frames_r, ignore_index=True))
    out = dict(frames)
    out["matched"] = matched
    out["summary"] = agreement.compare(None, None, matched=matched)
    return out


def test_the_scorecard_splits_the_level_gap_beside_it(split_frames):
    """A gap quoted "at free flow" is only that if the reference is at free flow
    there. The two columns that say whether it was sit with the gap, not at the
    end of the row."""
    html = vr.scorecard(split_frames["summary"], {})
    assert "Ref delay at ff (min)" in html and "Static gap (min)" in html
    assert html.index("Gap 95% CI") < html.index("Ref delay at ff (min)") < html.index("Bias (min)")
    assert "Free-flow gap = Static gap &#x2212; Ref delay at ff" in html or \
           "Free-flow gap = Static gap − Ref delay at ff" in html


def test_the_scorecard_omits_the_split_when_the_run_cannot_support_it(summary):
    """No reference-side delay column, no split columns — and no footnote
    explaining a decomposition the table does not carry."""
    html = vr.scorecard(summary, {})
    assert "Static gap" not in html and "Ref delay at ff" not in html
    assert "Free-flow gap = Static gap" not in html


def test_the_route_page_splits_the_level_gap_in_the_kpi(split_frames):
    html = vr.build_report(split_frames)["eagle_rd.html"]
    row = split_frames["summary"].set_index(ROUTE_COL).loc["Eagle Rd NB"]
    assert f"static gap of {row['free_flow_gap_static']:+.2f} min" in html
    assert f"minus {row['ref_free_flow_delay']:.2f} min of delay the reference itself" in html


def _sh69_summary(*, split: bool) -> pd.DataFrame:
    """Two rows shaped like the SH-69 pair the finding is about."""
    rows = [{ROUTE_COL: "SH-69 NB", "free_flow_inrix": 9.13, "free_flow_ref": 9.95,
             "free_flow_gap": -0.82, "ref_no_traffic": 8.2667,
             "ref_free_flow_delay": 1.6833, "free_flow_gap_static": 0.8633},
            {ROUTE_COL: "SH-69 SB", "free_flow_inrix": 8.95, "free_flow_ref": 13.64,
             "free_flow_gap": -4.69, "ref_no_traffic": 11.3167,
             "ref_free_flow_delay": 2.325, "free_flow_gap_static": -2.3667}]
    out = pd.DataFrame(rows)
    return out if split else out.drop(columns=["ref_no_traffic", "ref_free_flow_delay",
                                               "free_flow_gap_static"])


def test_the_interchange_panel_states_what_item_33_found(summary):
    """The panel used to end "this report does not claim to know what it is". It
    can now say: the reference's own no-traffic duration is directional over
    chains of the same extent, which no measurement of the road can be."""
    chains = {"SH-69 NB": _Chain(miles=7.17, requested=7.11, snap=(18.5, 1.5)),
              "SH-69 SB": _Chain(miles=7.17, requested=7.11, snap=(63.9, 18.5))}
    html = vr.unsupported_explanation_panel(chains, _sh69_summary(split=True))

    assert "11.32 minutes against 8.27" in html
    assert "3.05-minute directional" in html
    assert "7.11 miles of pavement" in html
    assert "64 ft from the southbound roadway" in html
    assert "does not claim to know" not in html


def test_the_interchange_panel_does_not_guess_without_the_split(summary):
    """A run whose reference gave no delay column cannot show the finding, and the
    panel goes back to saying so rather than asserting it anyway."""
    chains = {"SH-69 NB": _Chain(snap=(18.5, 1.5)), "SH-69 SB": _Chain(snap=(63.9, 18.5))}
    html = vr.unsupported_explanation_panel(chains, _sh69_summary(split=False))
    assert "does not claim to know" in html
    assert "no-traffic duration" not in html
