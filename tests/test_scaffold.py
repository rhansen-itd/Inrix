"""Scaffold sanity checks. Real per-module tests arrive with each ROADMAP item."""
import importlib

import pytest

MODULES = [
    "inrix_tools",
    "inrix_tools.io",
    "inrix_tools.timebins",
    "inrix_tools.speed",
    "inrix_tools.decompose",
    "inrix_tools.beforeafter",
    "inrix_tools.changepoint",
    "inrix_tools.geometry",
    "inrix_tools.kml",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    """Every module imports without third-party deps installed (stubs are
    import-clean; heavy imports happen inside functions)."""
    importlib.import_module(name)


def test_stub_raises_not_implemented():
    # a module still awaiting its ROADMAP item raises a clear NotImplementedError
    from inrix_tools import speed

    with pytest.raises(NotImplementedError):
        speed.segment_summary(None)
