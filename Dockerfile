# The loader image, see ADR-0012 for the choices made here.
#
# Two stages, so that the final image holds the Python environment and the data
# but neither uv nor a compiler. Both base images carry an exact version, so that
# a fresh clone builds the image that was tested.

# The uv binary is copied from its own image. Installing it with pip in the
# builder was rejected, because the version would then follow the package index
# instead of the tag.
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.13.18-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
# The interpreter of the image is used, never a downloaded one. The links are
# copies, because the environment is moved to another stage.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
WORKDIR /app
# The dependencies come first, so that a change of the source does not install
# them again. `--locked` fails if uv.lock does not match pyproject.toml, so the
# image never resolves versions on its own.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-dev --no-install-project --no-editable
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13.18-slim-trixie
# A fixed identifier, so that files of a mounted volume have a stable owner.
RUN groupadd --system --gid 10001 loader \
 && useradd --system --uid 10001 --gid loader --no-create-home --home-dir /nonexistent loader
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY data/raw ./data/raw
# Output without a buffer, so that `docker compose logs` shows a batch as soon as
# it is completed.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER loader
ENTRYPOINT ["python", "-m", "citysense"]
