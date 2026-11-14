"""Unit tests that keep the README in step with the repository."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text()
# A path in code font that starts in one of the folders of the repository.
PATHS = sorted(set(re.findall(r"`((?:scripts|docs|config|data|db|src|tests)/[^`\s*<>]+)`", README)))


def test_the_readme_names_paths():
    assert len(PATHS) >= 15


@pytest.mark.parametrize("path", PATHS)
def test_every_path_in_the_readme_exists(path):
    assert (ROOT / path.rstrip(",.")).exists(), path


def test_the_figures_of_the_quick_start_are_those_that_the_tests_pin():
    # The same figures are pinned by the integration tests of the loaded stack.
    for figure in ("405,184", "405,171", "580", "579", "27", "14", "13"):
        assert figure in README
    integration = (ROOT / "tests" / "integration" / "test_loading.py").read_text()
    for name in ("STORED_READINGS = 405_171", "FLAGGED_READINGS = 580", "HOURLY_SUMMARIES = 579"):
        assert name in integration
    assert "DAILY_SUMMARIES = 27" in integration


def test_the_clone_command_names_the_repository_and_no_placeholder_remains():
    assert "git clone https://github.com/MianGo7/citysense-batch.git" in README
    assert "<the address" not in README


def test_the_readme_has_the_sections_that_the_backlog_asks_for():
    for heading in (
        "## Purpose",
        "## Users and what each reads",
        "## Where the system fits",
        "## Quick start",
        "## Verification",
        "## Configuration",
        "## Project structure",
        "## Data source",
        "## Limits",
    ):
        assert heading in README
    assert "```mermaid" in README
    assert "AVX" in README
