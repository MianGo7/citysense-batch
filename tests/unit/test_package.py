"""Placeholder that proves the package installs and the test toolchain runs.

The second test already guards a rule of the architecture: the modules that hold
rules are tested without a database, so they must never import pymongo. The
placeholder is superseded by the tests of the pure rules from B4 onwards, the
import guard stays.
"""

import ast
import importlib
import importlib.util

import pytest

PURE_MODULES = ["identity", "validation", "batches", "alert_rules"]


def test_package_imports():
    assert importlib.import_module("citysense") is not None


@pytest.mark.parametrize("name", PURE_MODULES)
def test_rule_modules_do_not_import_pymongo(name):
    origin = importlib.util.find_spec(f"citysense.{name}").origin
    with open(origin, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "pymongo" not in imported
