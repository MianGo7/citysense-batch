"""Unit tests of the demonstration scripts that need neither Docker nor a database.

The demonstrations themselves reset the stack and take minutes, so they are run by
hand and their output is kept in `docs/evidence/`. What can be checked cheaply is
that the shell files parse, that the entry points can be run, and that the query
file changes data only on request.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
DEMONSTRATIONS = ["demo_crash_rerun.sh", "demo_failover.sh", "demo_queries.sh"]
BASH = shutil.which("bash")


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
@pytest.mark.parametrize("name", [*DEMONSTRATIONS, "demo_common.sh"])
def test_every_shell_script_parses(name):
    result = subprocess.run([BASH, "-n", str(SCRIPTS / name)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", DEMONSTRATIONS)
def test_a_demonstration_can_be_run_directly(name):
    assert os.access(SCRIPTS / name, os.X_OK)
    assert (SCRIPTS / name).read_text().startswith("#!/usr/bin/env bash\n")


@pytest.mark.parametrize("name", DEMONSTRATIONS)
def test_a_demonstration_keeps_its_output_in_the_evidence_folder(name):
    assert "docs/evidence/" in (SCRIPTS / name).read_text()


def test_the_queries_write_only_when_an_alert_is_named():
    text = (SCRIPTS / "queries.js").read_text()
    writes = [line for line in text.splitlines() if "updateOne" in line or "deleteMany" in line]
    assert len(writes) == 1
    guarded = text.index("if (!target)")
    assert text.index("updateOne") > guarded
    assert 'status: "open"' in text
