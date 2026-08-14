# Packaging only: the container runs exactly the `qbit-ops` CLI the
# Python package installs, with no Docker-specific mode or wrapper.
#
# Two stages so Poetry, the build backend and the wheel never reach the
# runtime image.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir "poetry-core>=1.9" "build>=1.2"

WORKDIR /build

# README.md and LICENSE are declared in pyproject.toml; poetry-core fails
# the build without them.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m build --wheel --outdir /wheels


FROM python:3.12-slim AS runtime

ARG VERSION="0+unknown"
ARG REVISION="unknown"
ARG CREATED=""

LABEL org.opencontainers.image.title="qbit-ops" \
      org.opencontainers.image.description="A tiny qBittorrent CLI and TUI for people who don't want to nuke their seedbox by accident." \
      org.opencontainers.image.source="https://github.com/LECOQQ/qbit-ops" \
      org.opencontainers.image.url="https://github.com/LECOQQ/qbit-ops" \
      org.opencontainers.image.documentation="https://github.com/LECOQQ/qbit-ops/blob/main/docs/COMMANDS.md" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The `tui` extra ships on purpose: without it `qbit-ops tui` fails with
# a remediation that can only suggest uv/pipx/poetry, none of which
# apply inside a container. The image is the whole product.
RUN --mount=from=builder,source=/wheels,target=/wheels \
    pip install --no-cache-dir "$(printf '%s' /wheels/*.whl)[tui]"

# Non-root by default. UID/GID 1000 matches the usual first desktop user,
# so a bind-mounted ./data is writable without extra flags in the common
# case; see the README for `--user` when it is not.
RUN groupadd --gid 1000 qbitops \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin qbitops \
    && mkdir -p /data \
    && chown 1000:1000 /data

# `/data` is the conventional mount point for files the user hands to or
# takes from qbit-ops. It is also the working directory, so relative
# paths in commands resolve there -- and so a `.env` placed in the mount
# is picked up by qbit-ops's existing cwd lookup, not by a Docker-only
# rule.
VOLUME ["/data"]
WORKDIR /data

USER 1000:1000

ENTRYPOINT ["qbit-ops"]
CMD ["--help"]
