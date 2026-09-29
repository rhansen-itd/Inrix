"""Tests for ROADMAP Item 66 — corridor types in one tagged catalogue.

The load-bearing checks:

- a later type's facility whose core lies mostly inside an earlier facility's core or
  Tier 2 merges into it (one corridor, both tags), and one that does not stands on
  its own, flagged where it shares pavement;
- an id taken by an earlier type is suffixed, and flags naming a renamed or merged
  facility follow it;
- the class reads the congestion profile, so a commute corridor that merely also
  queues on a summer weekend stays ``commute``, and commute + retail is
  ``urban_hybrid``;
- end to end on a toy network: two types, one catalogue that parses.
"""
from __future__ import annotations

import pytest

from inrix_tools import corridors, extents, screen

from test_extents import _baseline, _linear_chain  # noqa: E402  (tests/ on sys.path)


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
class TestPresets:
    def test_the_three_types(self):
        t = extents.CORRIDOR_TYPES
        assert t["commute"].window_names == ("am", "pm")
        assert t["recreational"].window_names == ("fri_summer", "sat_summer", "sun_summer")
        assert all(w.season == screen.SEASONS["summer"] for w in t["recreational"].windows)
        assert t["retail"].window_names == ("midday", "sat_midday")
        assert extents.DEFAULT_CORRIDOR_TYPES == ("commute", "recreational", "retail")

    def test_baseline_windows_add_the_night_and_the_fallback(self):
        rec = extents.CORRIDOR_TYPES["recreational"].baseline_windows()
        assert list(rec) == ["fri_summer", "sat_summer", "sun_summer", "night", "weekday"]
        assert list(extents.CORRIDOR_TYPES["commute"].baseline_windows()) == \
            list(screen.BASELINE_WINDOWS)

    def test_every_type_bin_screens_cleanly(self):
        for t in extents.CORRIDOR_TYPES.values():
            screen.check_window_cells(t.windows)

    def test_resolve(self):
        got = extents.resolve_corridor_types("recreational, commute")
        assert [t.name for t in got] == ["recreational", "commute"]
        with pytest.raises(KeyError):
            extents.resolve_corridor_types("tourist")
        with pytest.raises(ValueError):
            extents.resolve_corridor_types(["commute", "commute"])
        with pytest.raises(ValueError):
            extents.resolve_corridor_types("")


class TestClass:
    def test_found_by_classes(self):
        assert extents.corridor_class(["commute"]) == "commute"
        assert extents.corridor_class(["commute", "retail"]) == "urban_hybrid"
        assert extents.corridor_class(["retail", "commute", "recreational"]) == \
            "urban_hybrid+recreational"
        assert extents.corridor_class(["commute", "recreational"]) == "commute+recreational"

    def test_profile_keeps_only_the_comparably_congested_types(self):
        freeway = {"commute": {"peak_ratio": 2.2}, "recreational": {"peak_ratio": 1.3}}
        assert extents.profile_class(["commute", "recreational"], freeway) == \
            ("commute", "commute")
        arterial = {"commute": {"peak_ratio": 1.6}, "retail": {"peak_ratio": 1.4},
                    "recreational": {"peak_ratio": 1.3}}
        assert extents.profile_class(["commute", "retail", "recreational"], arterial) == \
            ("commute", "urban_hybrid")
        mountain = {"commute": {"peak_ratio": 1.05}, "recreational": {"peak_ratio": 1.4}}
        assert extents.profile_class(["commute", "recreational"], mountain) == \
            ("recreational", "recreational")

    def test_only_finding_types_can_join(self):
        # Retail is congested here but did not find the corridor (its floors failed).
        prof = {"commute": {"peak_ratio": 1.6}, "retail": {"peak_ratio": 1.6}}
        assert extents.profile_class(["commute"], prof) == ("commute", "commute")

    def test_retail_covers_recreational_unless_it_is_primary(self):
        """Owner, 2026-09-29: a hybrid is busy on weekends by definition; the summer
        weekend adds to the class only where it is the strongest signal."""
        eagle = {"commute": {"peak_ratio": 1.51}, "recreational": {"peak_ratio": 1.459},
                 "retail": {"peak_ratio": 1.462}}
        assert extents.profile_class(["commute", "recreational", "retail"], eagle) == \
            ("commute", "urban_hybrid")
        driggs = {"commute": {"peak_ratio": 1.307}, "recreational": {"peak_ratio": 1.343},
                  "retail": {"peak_ratio": 1.225}}
        assert extents.profile_class(["commute", "recreational", "retail"], driggs) == \
            ("recreational", "urban_hybrid+recreational")
        mccall = {"commute": {"peak_ratio": 1.153}, "recreational": {"peak_ratio": 1.264},
                  "retail": {"peak_ratio": 1.225}}
        assert extents.profile_class(["commute", "recreational", "retail"], mccall) == \
            ("recreational", "recreational+retail")
        garrity = {"recreational": {"peak_ratio": 1.466}, "retail": {"peak_ratio": 1.544}}
        assert extents.profile_class(["recreational", "retail"], garrity) == \
            ("retail", "retail")
        # Without retail in the class, recreational joins as before.
        freeway = {"commute": {"peak_ratio": 1.5}, "recreational": {"peak_ratio": 1.4}}
        assert extents.profile_class(["commute", "recreational"], freeway) == \
            ("commute", "commute+recreational")
        # The same cover without a profile.
        assert extents.profile_class(["commute", "retail", "recreational"], None) == \
            ("commute", "urban_hybrid")

    def test_no_profile_is_every_finding_type(self):
        assert extents.profile_class(["commute", "retail"], None) == \
            ("commute", "urban_hybrid")
        assert extents.profile_class(["commute", "retail"],
                                     {"commute": {"peak_ratio": None}}) == \
            ("commute", "urban_hybrid")


# ---------------------------------------------------------------------------
# merge_typed_catalogues on hand-built catalogues
# ---------------------------------------------------------------------------
def _facility(fid, core, reach=(), *, direction="NB", flags=(), companion=None,
              vhd=10.0):
    """A generated facility: a core tier and (if ``reach``) a commuter tier."""
    entries, groups = [], []
    for tier, ids in (("core", core), ("commuter", reach)):
        if not ids:
            continue
        gid = f"{fid}-{tier}"
        entries.append({"id": f"{gid}-{direction.lower()}", "name": gid,
                        "start_latlon": [43.6, -116.2], "end_latlon": [43.7, -116.2],
                        "description": "x", "corridor": gid, "direction": direction,
                        "_tier": tier, "_facility": fid, "_segment_ids": list(ids)})
        g = {"id": gid, "name": gid, "description": "x", "_tier": tier,
             "_facility": fid, "_ranked": tier == "core",
             "_core": {"vhd": vhd, "miles": len(core)}, "_flags": list(flags)}
        if companion:
            g["_companion"] = companion
        groups.append(g)
    return entries, groups


def _catalogue(*facilities, loose=()):
    cat = {"_generated": {"peak_windows": ["am", "pm"], "n_chains": 3, "n_facilities":
                          len(facilities), "thresholds": {"x": 1}},
           "corridors": [], "reporting_corridors": []}
    for e, g in facilities:
        cat["corridors"] += e
        cat["reporting_corridors"] += g
    for gid in loose:
        cat["reporting_corridors"].append({"id": gid, "name": gid, "description": "c",
                                           "one_way_couplet": True})
        cat["corridors"].append({"id": f"{gid}-leg", "name": gid, "start_latlon": [0, 0],
                                 "end_latlon": [0, 1], "description": "c",
                                 "corridor": gid, "direction": "NB"})
    return cat


def _core_group(cat, facility):
    return next(g for g in cat["reporting_corridors"]
                if g.get("_facility") == facility and g["_tier"] == "core")


class TestMerge:
    def test_a_mostly_covered_core_merges(self):
        commute = _catalogue(_facility("us-95-a", core=[1, 2, 3], reach=[1, 2, 3, 4, 5]))
        rec = _catalogue(_facility("us-95-a-weekend", core=[3, 4, 5], vhd=4.0))
        out = extents.merge_typed_catalogues({"commute": commute, "recreational": rec})
        assert {g["_facility"] for g in out["reporting_corridors"]} == {"us-95-a"}
        core = _core_group(out, "us-95-a")
        assert core["_types"] == ["commute", "recreational"]
        assert core["_class"] == "commute+recreational"      # no profile: found-by
        assert core["_type_cores"]["recreational"][0]["facility"] == "us-95-a-weekend"
        assert core["_type_cores"]["recreational"][0]["share_in"] == 1.0
        assert all(e["_types"] == ["commute", "recreational"] for e in out["corridors"])
        info = out["_generated"]["types"]
        assert (info["recreational"]["n_merged"], info["recreational"]["n_standalone"]) \
            == (1, 0)
        corridors.parse_catalogue(out)

    def test_a_distant_core_stands_on_its_own(self):
        commute = _catalogue(_facility("sh-55-a", core=[1, 2, 3]))
        rec = _catalogue(_facility("sh-55-b", core=[20, 21, 22]))
        out = extents.merge_typed_catalogues({"commute": commute, "recreational": rec})
        assert _core_group(out, "sh-55-b")["_types"] == ["recreational"]
        assert _core_group(out, "sh-55-b")["_class"] == "recreational"
        assert _core_group(out, "sh-55-a")["_types"] == ["commute"]

    def test_a_partial_overlap_stands_flagged(self):
        commute = _catalogue(_facility("us-30-a", core=[1, 2]))
        rec = _catalogue(_facility("us-95-a", core=[2, 3, 4, 5]))
        out = extents.merge_typed_catalogues({"commute": commute, "recreational": rec})
        flags = _core_group(out, "us-95-a")["_flags"]
        assert flags == ["shares 1.00 mi with us-30-a (commute)"]

    def test_miles_decide_the_share(self):
        """By count 2 of 4 segments (50%) lie inside; by miles only 0.2 of 2.2."""
        commute = _catalogue(_facility("a", core=[1, 2]))
        rec = _catalogue(_facility("b", core=[1, 2, 3, 4]))
        by_count = extents.merge_typed_catalogues({"c": commute, "r": rec})
        assert {g["_facility"] for g in by_count["reporting_corridors"]} == {"a"}
        miles = {1: 0.1, 2: 0.1, 3: 1.0, 4: 1.0}
        by_miles = extents.merge_typed_catalogues({"c": commute, "r": rec}, miles=miles)
        assert {g["_facility"] for g in by_miles["reporting_corridors"]} == {"a", "b"}

    def test_a_taken_id_is_suffixed_and_references_follow(self):
        commute = _catalogue(_facility("i-84-boise", core=[1, 2, 3]))
        rec = _catalogue(
            _facility("i-84-boise", core=[30, 31, 32]),
            _facility("i-84-boise-2", core=[40, 41], flags=["shares 0.50 mi with i-84-boise"],
                      companion="SB here is in the extent of i-84-boise"),
        )
        out = extents.merge_typed_catalogues({"commute": commute, "recreational": rec})
        facs = {g["_facility"] for g in out["reporting_corridors"]}
        assert facs == {"i-84-boise", "i-84-boise-recreational", "i-84-boise-2"}
        renamed = _core_group(out, "i-84-boise-recreational")
        assert renamed["id"] == "i-84-boise-recreational-core"
        entry = next(e for e in out["corridors"]
                     if e["_facility"] == "i-84-boise-recreational")
        assert (entry["id"], entry["corridor"]) == ("i-84-boise-recreational-core-nb",
                                                    "i-84-boise-recreational-core")
        other = _core_group(out, "i-84-boise-2")
        assert other["_flags"] == ["shares 0.50 mi with i-84-boise-recreational"]
        assert other["_companion"] == "SB here is in the extent of i-84-boise-recreational"
        corridors.parse_catalogue(out)
        corridors.parse_reporting_corridors(out)

    def test_a_flag_naming_a_merged_facility_names_its_host(self):
        commute = _catalogue(_facility("host", core=[1, 2, 3], reach=[1, 2, 3, 4]))
        rec = _catalogue(_facility("absorbed", core=[2, 3, 4]),
                         _facility("beside", core=[50, 51], flags=["shares 1.00 mi with absorbed"]))
        out = extents.merge_typed_catalogues({"commute": commute, "recreational": rec})
        assert _core_group(out, "beside")["_flags"] == ["shares 1.00 mi with host"]

    def test_a_type_never_merges_into_itself(self):
        """Two facilities of one type are already disjoint by construction; a later
        type's facility may merge into an earlier type's, not its own."""
        commute = _catalogue(_facility("a", core=[1, 2]))
        rec = _catalogue(_facility("b", core=[10, 11], reach=[10, 11, 12, 13]),
                         _facility("c", core=[12, 13]))
        out = extents.merge_typed_catalogues({"commute": commute, "recreational": rec})
        assert {g["_facility"] for g in out["reporting_corridors"]} == {"a", "b", "c"}

    def test_three_types_and_the_hybrid(self):
        commute = _catalogue(_facility("eagle", core=[1, 2, 3, 4]))
        retail = _catalogue(_facility("eagle-mid", core=[2, 3, 4]))
        rec = _catalogue(_facility("eagle-sat", core=[1, 2, 3]))
        out = extents.merge_typed_catalogues(
            {"commute": commute, "retail": retail, "recreational": rec})
        core = _core_group(out, "eagle")
        assert core["_types"] == ["commute", "retail", "recreational"]
        # Found by all three; retail covers the weekend, so it reads as the hybrid.
        assert core["_class"] == "urban_hybrid"

    def test_loose_groups_are_carried_once(self):
        commute = _catalogue(_facility("a", core=[1]), loose=["couplet-x"])
        rec = _catalogue(_facility("b", core=[9]), loose=["couplet-x"])
        out = extents.merge_typed_catalogues({"commute": commute, "recreational": rec})
        assert [g["id"] for g in out["reporting_corridors"]].count("couplet-x") == 1
        assert [e["id"] for e in out["corridors"]].count("couplet-x-leg") == 1

    def test_generated(self):
        types = extents.resolve_corridor_types("commute,recreational")
        out = extents.merge_typed_catalogues(
            {"commute": _catalogue(_facility("a", core=[1])),
             "recreational": _catalogue(_facility("b", core=[2]))}, types=types)
        gen = out["_generated"]
        assert gen["type_order"] == ["commute", "recreational"]
        assert gen["n_facilities"] == 2
        assert gen["types"]["recreational"]["windows"]["sat_summer"]["season"] == \
            list(screen.SEASONS["summer"])
        assert gen["thresholds"] == {"x": 1}
        assert gen["classes"] == {"urban_hybrid": ["commute", "retail"]}
        with pytest.raises(ValueError):
            extents.merge_typed_catalogues({})


# ---------------------------------------------------------------------------
# End to end: two types on a toy chain, profile-based class
# ---------------------------------------------------------------------------
WEEKEND = extents.CorridorType(
    "weekend", "Weekend",
    (screen.WEEKEND_WINDOWS["sat"].with_season(screen.SEASONS["summer"],
                                               name="sat_summer"),))


def _typed_baseline(ids, commute_ratios, weekend_ratios):
    """The commute baseline screen with a ``sat_summer`` window beside it."""
    b = _baseline(ids, commute_ratios)
    b["sat_summer_travel_time"] = [weekend_ratios.get(s, 1.0) for s in ids]
    b["sat_summer_n_obs"] = 500
    b["sat_summer_realtime_share"] = 0.99
    return b


class TestEndToEnd:
    COMMUTE = {1001: 2.0, 1002: 2.0, 1003: 2.0}
    # A mild summer-Saturday queue on the commute core, and a strong one far away.
    WEEKEND = {1002: 1.3, 1003: 1.3, 1004: 1.3, 1011: 1.6, 1012: 1.6, 1013: 1.6}

    def _build(self):
        net = _linear_chain(16)
        ids = list(net.index)
        base = _typed_baseline(ids, self.COMMUTE, self.WEEKEND)
        types = [extents.CORRIDOR_TYPES["commute"], WEEKEND]
        cats, cong = {}, {}
        for t in types:
            cong[t.name] = extents.segment_congestion(base, net,
                                                      peak_windows=t.window_names)
            cats[t.name] = extents.generate_catalogue(
                net, base, peak_windows=t.window_names, observed=set(ids),
                congestion=cong[t.name])
        return net, extents.merge_typed_catalogues(
            cats, miles=net["Miles"].to_dict(), types=types, congestion=cong), cats

    def test_congestion_passthrough_is_what_it_would_compute(self):
        net = _linear_chain(16)
        ids = list(net.index)
        base = _typed_baseline(ids, self.COMMUTE, self.WEEKEND)
        plain = extents.generate_catalogue(net, base, observed=set(ids))
        given = extents.generate_catalogue(
            net, base, observed=set(ids),
            congestion=extents.segment_congestion(base, net))
        assert plain == given

    def test_one_catalogue_two_types(self):
        net, out, cats = self._build()
        cores = [g for g in out["reporting_corridors"] if g["_tier"] == "core"]
        assert len(cores) == 2
        by_types = {tuple(g["_types"]): g for g in cores}
        assert set(by_types) == {("commute", "weekend"), ("weekend",)}
        # The commute core also queues on summer Saturdays, mildly: it is a commute
        # corridor that the weekend builder found too, not a weekend one.
        both = by_types[("commute", "weekend")]
        assert both["_class"] == "commute" and both["_primary_type"] == "commute"
        assert both["_profile"]["commute"]["peak_ratio"] == pytest.approx(2.0)
        assert both["_profile"]["weekend"]["peak_ratio"] == pytest.approx(1.2)
        alone = by_types[("weekend",)]
        assert alone["_class"] == "weekend"
        assert alone["_profile"]["weekend"]["peak_ratio"] == pytest.approx(1.6)
        assert out["_generated"]["class_secondary_share"] == \
            extents.CLASS_SECONDARY_SHARE
        entries = corridors.parse_catalogue(out)
        assert {e.corridor for e in entries} == \
            {g.id for g in corridors.parse_reporting_corridors(out)}

    def test_the_commute_type_alone_is_the_old_catalogue(self):
        """``--types commute`` rebuilds the pre-Item 66 catalogue, tags aside."""
        net, _, cats = self._build()
        only = extents.merge_typed_catalogues({"commute": cats["commute"]})
        strip = lambda rows: [{k: v for k, v in r.items()
                               if k not in ("_types", "_class", "_primary_type",
                                            "_type_cores")} for r in rows]
        assert strip(only["corridors"]) == cats["commute"]["corridors"]
        assert strip(only["reporting_corridors"]) == cats["commute"]["reporting_corridors"]

    def test_profile_reads_every_type(self):
        net = _linear_chain(8)
        ids = list(net.index)
        base = _typed_baseline(ids, {1002: 1.5}, {1002: 1.25})
        cong = {"commute": extents.segment_congestion(base, net),
                "weekend": extents.segment_congestion(base, net,
                                                      peak_windows=("sat_summer",))}
        prof = extents.type_profile([1002], cong)
        assert prof["commute"]["peak_ratio"] == pytest.approx(1.5)
        assert prof["weekend"]["peak_ratio"] == pytest.approx(1.25)
        assert set(prof["commute"]) == {"peak_ratio", "vhd_per_mile", "delay_per_mile",
                                        "vhd"}


class TestReclassify:
    def test_classes_are_recomputed_from_the_stored_profile(self):
        commute = _catalogue(_facility("eagle", core=[1, 2, 3, 4], reach=[1, 2, 3, 4, 5]))
        rec = _catalogue(_facility("eagle-sat", core=[1, 2, 3]))
        retail = _catalogue(_facility("eagle-mid", core=[2, 3, 4]))
        cat = extents.merge_typed_catalogues(
            {"commute": commute, "recreational": rec, "retail": retail})
        profile = {"commute": {"peak_ratio": 1.51}, "recreational": {"peak_ratio": 1.46},
                   "retail": {"peak_ratio": 1.46}}
        for g in cat["reporting_corridors"]:
            g["_profile"] = profile
            g["_class"] = "stale"
        for e in cat["corridors"]:
            e["_class"] = "stale"
        before = [dict(g) for g in cat["reporting_corridors"]]
        out = extents.reclassify_catalogue(cat)
        assert {g["_class"] for g in out["reporting_corridors"]} == {"urban_hybrid"}
        assert {e["_class"] for e in out["corridors"]} == {"urban_hybrid"}
        assert {e["_primary_type"] for e in out["corridors"]} == {"commute"}
        assert out["_generated"]["class_subsumed"] == {"recreational": "retail"}
        # Only the class fields change, and the input is left alone.
        assert [dict(g, _class="stale") for g in out["reporting_corridors"]] == before
        assert {g["_class"] for g in cat["reporting_corridors"]} == {"stale"}
        corridors.parse_catalogue(out)

    def test_an_untyped_catalogue_passes_through(self):
        cat = _catalogue(_facility("a", core=[1]))
        assert extents.reclassify_catalogue(cat)["reporting_corridors"] == \
            cat["reporting_corridors"]
