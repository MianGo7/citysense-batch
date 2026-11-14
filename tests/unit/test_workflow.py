"""Unit tests that keep the continuous integration workflow in step with the repository."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
DOCKERFILE = (ROOT / "Dockerfile").read_text()


def test_the_workflow_uses_the_version_of_uv_of_the_image():
    # One version of the tool builds the image and runs the checks (ADR-0016).
    image = re.search(r"ghcr\.io/astral-sh/uv:(\S+) AS uv", DOCKERFILE)
    workflow = re.search(r'UV_VERSION: "([^"]+)"', WORKFLOW)
    assert image is not None
    assert workflow is not None
    assert workflow.group(1) == image.group(1)


def test_every_action_is_pinned_to_a_commit():
    # A tag can be moved after the fact, a full commit identifier cannot.
    uses = re.findall(r"uses: (\S+)", WORKFLOW)
    assert uses
    for reference in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", reference), reference


def test_the_workflow_has_a_job_for_the_unit_tests_and_one_for_the_stack():
    assert "uv run pytest tests/unit" in WORKFLOW
    assert "uv run ruff check ." in WORKFLOW
    assert "uv run ruff format --check ." in WORKFLOW
    assert "docker compose up --build --detach" in WORKFLOW
    assert 'uv run pytest -m "integration and not slow"' in WORKFLOW
    assert "uv run pytest -m integration -v" in WORKFLOW
    assert WORKFLOW.count("--durations=25") == 2
    assert WORKFLOW.count("faulthandler_timeout=900") == 2


def _job(name):
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", WORKFLOW, re.MULTILINE | re.DOTALL)
    assert match is not None, name
    return match.group(1)


def test_the_push_run_leaves_out_the_slow_tests_and_the_full_run_is_manual():
    assert "timeout-minutes: 30" in _job("integration")
    assert "!= 'workflow_dispatch'" in _job("integration")
    assert "not slow" in _job("integration")
    assert "timeout-minutes: 90" in _job("integration-full")
    assert "== 'workflow_dispatch'" in _job("integration-full")
    assert "not slow" not in _job("integration-full")
    assert re.search(r"^  workflow_dispatch:$", WORKFLOW, re.MULTILINE)


def test_a_push_cannot_cancel_a_run_started_by_hand():
    assert "${{ github.event_name }}" in re.search(
        r"concurrency:\n(.*?)\n\n", WORKFLOW, re.DOTALL
    ).group(1)


def test_the_slow_marker_is_registered_and_applied():
    assert "slow:" in (ROOT / "pyproject.toml").read_text()
    loading = (ROOT / "tests" / "integration" / "test_loading.py").read_text()
    database = (ROOT / "tests" / "integration" / "test_database.py").read_text()
    assert loading.count("pytest.mark.slow") == 10
    assert database.count("pytest.mark.slow") == 1


def test_the_workflow_only_reads_the_repository():
    assert re.search(r"^permissions:\n  contents: read$", WORKFLOW, re.MULTILINE)
