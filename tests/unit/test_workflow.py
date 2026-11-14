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
    assert "uv run pytest -m integration" in WORKFLOW


def test_the_workflow_only_reads_the_repository():
    assert re.search(r"^permissions:\n  contents: read$", WORKFLOW, re.MULTILINE)
