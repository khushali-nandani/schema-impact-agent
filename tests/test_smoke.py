import importlib

import pytest

import schema_impact


def test_version_is_set():
    assert schema_impact.__version__  # its value is checked in test_packaging


@pytest.mark.parametrize("name", ["core", "agent", "adapters", "evals"])
def test_subpackages_import(name):
    importlib.import_module(f"schema_impact.{name}")
