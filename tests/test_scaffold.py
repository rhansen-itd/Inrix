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
    """Every module imports without third-party deps installed (heavy imports
    happen inside functions). All ROADMAP compute/export modules are now built —
    no stubs remain."""
    importlib.import_module(name)
