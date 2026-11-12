# Development journal

A chronological record of the work, one entry per session, written on the
same day. Each entry states what was built, what was decided, which problems
occurred and how they were resolved. These entries are the main source for
the personal reflection in phase 3, so difficulties are recorded as they
occurred rather than summarised comfortably afterwards.

Template:

    ## YYYY-MM-DD

    What was worked on. What was decided and why. What broke and how it was
    solved. What comes next.

---

## 2026-11-11

The concept for phase 1 had been submitted and the tutor's feedback had
arrived. The feedback named one contradiction, planners looking for long term
trends in a data set that covers a single week, and asked for the users to be
thought through concretely, so that failure cases such as a broken system, a
full disk, network problems and false data become visible, and for indexes
and derived data to be planned explicitly. The concept was revised
accordingly: the week is treated as the start of a continuous feed loaded in
daily batches, the failure cases are named with figures from the data set,
and the summary, latest value and alert collections as well as the indexes
are part of the design. The lecture on alerts the tutor pointed to was
watched, and its points were turned into the alert model in ADR-0007.

The repository was set up with its documentation: the technical concept, the
requirements, the backlog and the first seven decisions. The licence of the
data set was checked and is CC0, so the sample data can be committed. The
replica set was moved from a documented option into the prototype, because a
reliability claim that can be demonstrated by stopping the primary during a
load is stronger than one that is only described.

Next: B0, the repository skeleton and toolchain.

---

## 2026-11-12

B0, the repository skeleton and toolchain, was taken. The item adds
dependencies, so a plan was stated first and the build backend, the
dependency ranges and the interpreter version were put to the student before
any file was written. The decisions were uv_build as the build backend,
compatible ranges in `pyproject.toml` with exact versions only in `uv.lock`,
and Python 3.13 instead of the 3.12 named in the backlog, because 3.13 is
already installed on the development machine. They are recorded in ADR-0008,
and the text of B0 in the backlog was changed to match.

The package `src/citysense/` now holds the nine modules of the planned layout
with docstrings only, `tests/unit/` and `tests/integration/` exist with the
`integration` marker registered, and a `.dockerignore` keeps the history,
caches, documents and tests out of the later image. The placeholder unit test
does more than prove that pytest runs: it parses the four rule modules and
fails if one of them imports pymongo, so the architecture rule is enforced
from the first commit. The first version of that test searched the source text
for the word and needed a string replacement to exclude its own docstring,
which was fragile, so it was rewritten to inspect the import statements.

Resolved versions in `uv.lock` are pymongo 4.19.0, pytest 9.2.0 and ruff
0.17.5.

Next: B1, the sample data in the repository.
