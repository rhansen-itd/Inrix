"""inrix_tools — load, explore, and analyze INRIX segment travel-time data.

Pure-Python compute core. No plotting or GUI imports live here (see CLAUDE.md);
figures and the Dash app in ``gui/`` are a thin shell over these modules.

Modules are scaffolded as stubs until built per ROADMAP.md:
    io          — load INRIX data.csv/zip + metadata.csv (Item 1)
    timebins    — day-group + time-of-day binning (Item 2)
    speed       — speed / travel-time aggregation (Item 3)
    decompose   — thin adapter over traffic_anomaly.decompose (Item 4)
    beforeafter — decomposition-based before/after + t-test baseline (Item 4)
    changepoint — thin adapter over traffic_anomaly.changepoint (Item 5)
    geometry    — segment polylines from the INRIX XD shapefile (Item 8)
    corridors   — corridor chain assembly: snap, walk, trim, account (Item 28)
                  + the corridor catalogue: endpoint pairs, resolved (Item 36)
    reference   — external travel-time reference: load + gates (Item 29)
    agreement   — INRIX vs reference agreement statistics (Item 29)
    kml         — segment geometry -> KML (Item 6)
    names       — friendly, user-editable segment names (Item 10)
    aadt        — AADT volume layer + volume weighting (Item 18)
    store       — DuckDB-backed storage & ingest of exports + GIS join (Item 21)
    screen      — district-wide corridor screening + ranking (Item 35)
    couplets    — statewide one-way couplet detection & pairing (Item 45)
    extents     — corridor split criteria & multi-scale tiers (Item 45)
"""

__version__ = "0.1.0"
