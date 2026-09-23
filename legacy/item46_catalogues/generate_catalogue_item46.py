"""The Item 46 catalogue generator, superseded by ROADMAP Item 50 (Session 69).

Kept for diffing, per CLAUDE.md ("superseded code goes to legacy/"). It cored a
facility on peak TTI against INRIX's ``Ref Speed`` (>= 1.20 per segment over
>= 0.75 mi), mirrored the lead direction's extents onto the opposing carriageway
without checking that direction's own data, and emitted Tier 3 as the whole chain.
The catalogues it last produced (Item 49) are next to this file.

Not importable as-is: it relied on module-level names from
``inrix_tools.extents`` at commit c7d4c08.
"""
def _delay_proxy(segment_ids: Sequence[int], net_idx: pd.DataFrame,
                 tti: pd.DataFrame, window: str) -> float:
    """Rough vehicle-hours of delay over an extent, for *ordering facilities only*.

    ``miles x (TTI - 1) x AADT`` when a volume is joined, ``miles x (TTI - 1)``
    without one. It is deliberately not reported anywhere: the real VHD comes
    from `screen.rank_corridors` once the catalogue is screened. This only
    decides which facilities are worth cataloguing at all.
    """
    col = f"{window}_mean_tti"
    if tti.empty or col not in tti.columns:
        return 0.0
    total = 0.0
    for sid in segment_ids:
        if sid not in net_idx.index or sid not in tti.index:
            continue
        row = net_idx.loc[sid]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        miles = row.get("Miles", 0.0)
        val = tti.loc[sid, col]
        if isinstance(val, pd.Series):
            val = val.iloc[0]
        if pd.isna(miles) or pd.isna(val):
            continue
        excess = max(float(val) - 1.0, 0.0)
        aadt = row.get("AADT")
        weight = float(aadt) if pd.notna(aadt) else 1.0
        total += float(miles) * excess * weight
    return total


def generate_catalogue(
    network: gpd.GeoDataFrame,
    *,
    screen_data: pd.DataFrame | None = None,
    recurrence: pd.DataFrame | None = None,
    window: str = "pm",
    tiers: Sequence[ExtentTier] = DEFAULT_TIERS,
    min_chain_miles: float = MIN_CHAIN_MILES,
    min_core_miles: float = MIN_CORE_MILES,
    core_gap_tolerance: int = 2,
    max_facilities: int | None = None,
    observed: set[int] | None = None,
    note: str = "",
) -> dict:
    """Build a whole corridor catalogue from a district network, generatively.

    This is the function ROADMAP Item 46 exists to provide: the replacement for a
    hand-written list of lat/lon hints. It walks the network's numbered mainlines
    (:func:`enumerate_mainline_chains`), pairs the carriageways
    (:func:`pair_chains`), analyses each pair's leading direction
    (:func:`analyse_chain`), mirrors the resulting extents onto the opposing
    direction (:func:`mirror_extent`), and emits the Tier 1/2/3 alternatives as
    catalogue entries whose ``description`` states the split that ended them.

    Args:
        network: the district's XD network, **with link repairs already applied**
            and ideally with ``AADT`` / ``aadt_desc`` joined (``aadt.join_aadt``)
            — the AADT step criterion and the endpoint naming both read them.
        screen_data: a `screen.segment_screen` frame; converted to TTI by
            :func:`tti_frame`. Either this or ``recurrence`` is required for a
            Tier 1 core to be found empirically.
        recurrence: a `screen.segment_recurrence` frame, used in preference to
            ``screen_data`` when both are given.
        window: the peak window the cores are read from.
        tiers: which tiers to emit. Dropping ``REGIONAL`` gives a catalogue of
            bottlenecks only.
        min_core_miles: the floor under a Tier 1 core (below it, a signal queue).
        max_facilities: keep only the N facilities with the largest core delay
            proxy. ``None`` keeps every facility that has a qualifying core.
        observed: segment ids present in the export. A facility whose core is not
            observed cannot be screened, so it is not catalogued.
        note: text for the catalogue's ``_note`` provenance key.

    Returns:
        ``{"_note": ..., "corridors": [...], "reporting_corridors": [...]}`` —
        ready for ``corridors.parse_catalogue`` and ``resolve_catalogue``.
    """
    net_idx = _ensure_indexed(network)
    try:
        metric_crs = network.estimate_utm_crs()
    except Exception:
        metric_crs = "EPSG:3857"

    label, source_windows = resolve_window(window)
    if recurrence is not None and not recurrence.empty:
        tti = recurrence
    else:
        tti = tti_frame(screen_data if screen_data is not None else pd.DataFrame(),
                        source_windows)
    if tti is None:
        tti = pd.DataFrame()
    if not tti.empty and len(source_windows) > 1:
        tti = worst_window_tti(tti, source_windows, label)
    window = label

    chains = enumerate_mainline_chains(network, min_miles=min_chain_miles)
    pairs = pair_chains(chains, network, metric_crs=metric_crs)
    # Once, not once per chain: this is a property of the network (Item 47).
    junctions = incoming_route_map(network)

    def _analyse(chain: MainlineChain) -> dict | None:
        analysis = analyse_chain(
            list(chain.segment_ids), network,
            route_number=chain.route_number, bearing=chain.bearing,
            recurrence=tti if not tti.empty else None,
            window=window,
            min_core_miles=min_core_miles,
            core_gap_tolerance=core_gap_tolerance,
            incoming_routes=junctions,
        )
        # A core found by the *fallback* (no congestion data at all) is a guess, not
        # a measurement; the whole point of Item 43/45 is not to catalogue those.
        empirical = [c for c in analysis.tiers.get(ExtentTier.CORE, [])
                     if "bottleneck core" in c.split_rationale
                     or "congestion run" in c.split_rationale]
        if not empirical:
            return None
        core = empirical[0]
        if observed is not None and not any(s in observed for s in core.segment_ids):
            return None
        return {"chain": chain, "analysis": analysis, "core": core,
                "score": _delay_proxy(core.segment_ids, net_idx, tti, window)}

    facilities: list[dict] = []
    for chain, counterpart in pairs:
        # Both directions are analysed and the **stronger core leads**. Leading with
        # whichever chain the enumeration happened to list first loses real
        # bottlenecks: I-90 through Coeur d'Alene has no PM core eastbound and four
        # congested segments westbound, and EB is 0.01 mi the longer chain.
        candidates = [c for c in (_analyse(chain),
                                  _analyse(counterpart) if counterpart is not None else None)
                      if c is not None]
        if not candidates:
            continue
        lead = max(candidates, key=lambda c: c["score"])
        other = chain if lead["chain"] is counterpart else counterpart
        lead["counterpart"] = other
        facilities.append(lead)

    facilities.sort(key=lambda f: -f["score"])
    if max_facilities is not None:
        facilities = facilities[:max_facilities]

    entries: list[dict] = []
    groups: list[dict] = []
    used_ids: set[str] = set()

    for fac in facilities:
        chain: MainlineChain = fac["chain"]
        counterpart: MainlineChain | None = fac["counterpart"]
        county = (chain.counties[0] if chain.counties else "").strip()
        label = facility_label(chain)
        base = _slug(f"{label}-{county or 'idaho'}")
        facility_id, n = base, 1
        while facility_id in used_ids:
            n += 1
            facility_id = f"{base}-{n}"
        used_ids.add(facility_id)
        facility_name = f"{label}: {county} County" if county else label
        if n > 1:
            # One route can run through one county as several separate chains (US-95
            # crosses Latah twice). The ids already differ; the *name* has to as
            # well, or the tier comparison collapses two facilities into one row.
            facility_name = f"{facility_name} ({n})"

        # Two tiers that cut the same segments are one extent under two names, and
        # the dilution comparison would report a 100% retention that means nothing.
        # Where they coincide the **widest** tier is the one that survives: a
        # commuter extent that reaches both ends of the chain *is* the regional
        # baseline, and calling it "Tier 2" would understate what was measured.
        chosen: dict[ExtentTier, ExtentAlternative] = {}
        for tier in tiers:
            alts = fac["analysis"].tiers.get(tier, [])
            if alts:
                chosen[tier] = fac["core"] if tier is ExtentTier.CORE else alts[0]
        keep: dict[ExtentTier, ExtentAlternative] = {}
        seen_extents: set[tuple[int, ...]] = set()
        for tier in reversed(list(tiers)):
            alt = chosen.get(tier)
            if alt is None or alt.segment_ids in seen_extents:
                continue
            seen_extents.add(alt.segment_ids)
            keep[tier] = alt

        for tier in tiers:
            alt = keep.get(tier)
            if alt is None:
                continue
            mirrored = (mirror_extent(alt, counterpart.segment_ids, network,
                                      metric_crs=metric_crs)
                        if counterpart is not None else None)
            tier_entries, group = extent_catalogue_entries(
                alt, chain, mirrored, counterpart, network,
                facility_id=facility_id, facility_name=facility_name,
            )
            if not tier_entries:
                continue
            entries.extend(tier_entries)
            groups.append(group)

    return {
        "_note": note or (
            "Generated by inrix_tools.extents.generate_catalogue (ROADMAP Item 46) — "
            "mainline chains walked from the XD topology, extents cut at detected "
            "split points. Do not hand-edit; re-run the builder."
        ),
        "_generated": {
            "window": window,
            "source_windows": list(source_windows),
            "n_chains": len(chains),
            "n_facilities": len(facilities),
            "min_core_miles": min_core_miles,
            "tiers": [t.value for t in tiers],
        },
        "corridors": entries,
        "reporting_corridors": groups,
    }
