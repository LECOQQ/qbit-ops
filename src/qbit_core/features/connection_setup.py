"""Guided qBittorrent connection setup, shared by the CLI and the TUI.

Owns the whole contract behind `qbit-ops init` and the TUI's first-run
form: validating three values, testing them, writing them at `0600`, and
naming whatever already takes precedence over the file just written.
Prompting and rendering stay in the two façades.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from qbit_core.config import (
    CONNECTION_VARIABLES,
    HOST_VARIABLE,
    PASSWORD_VARIABLE,
    USER_VARIABLE,
    QbitConfig,
)
from qbit_core.errors import (
    InvalidInputError,
    QbitAuthenticationError,
    QbitConnectionError,
    QbitCoreError,
    require_non_blank,
)
from qbit_core.qbit.client import create_qbit_client

DEFAULT_HOST = "http://localhost:8080"
DEFAULT_USERNAME = "admin"

ENV_FILE_MODE = 0o600
ENV_DIRECTORY_MODE = 0o700

# `${` is expanded by the env-file loader when the file is read back, and
# no escaping survives it -- a value carrying one would load as something
# else, or as nothing. A line break would end the assignment outright.
_UNREPRESENTABLE_SUBSTRINGS: tuple[tuple[str, str], ...] = (
    ("\n", "a line break"),
    ("\r", "a line break"),
    ("${", "'${'"),
)


class EnvFileExistsError(QbitCoreError):
    """Report that the target env file is already present.

    Carries `path` so a façade can name the file it refused to
    overwrite without re-deriving it.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(f"{path} already exists.")
        self.path = path


def build_connection_config(
    *, host: str, username: str, password: str
) -> QbitConfig:
    """Normalize three raw inputs into a writable `QbitConfig`.

    Strips each value the way `load_qbit_config` does on read, and
    rejects anything that would not survive the write/read round trip.
    """
    return QbitConfig(
        host=_normalize(HOST_VARIABLE, host),
        username=_normalize(USER_VARIABLE, username),
        password=_normalize(PASSWORD_VARIABLE, password),
    )


def _normalize(variable: str, value: str) -> str:
    normalized = require_non_blank(value, field_name=variable)
    for substring, description in _UNREPRESENTABLE_SUBSTRINGS:
        if substring in normalized:
            raise InvalidInputError(
                f"{variable} must not contain {description}: the value "
                "would not be read back unchanged."
            )
    return normalized


@dataclass(frozen=True)
class ConnectionAttempt:
    """One login attempt made with values not yet written anywhere.

    `detail` carries the failure reason to show, and stays `None` on
    success.
    """

    ok: bool
    detail: str | None = None


def try_connection(
    config: QbitConfig,
    *,
    client_factory: Callable[[QbitConfig], Any] | None = None,
) -> ConnectionAttempt:
    """Attempt one login, reporting failure instead of raising.

    Only the recognized unreachable/rejected outcomes become a failed
    attempt; anything else propagates as the programming defect it is,
    never as a connection problem. The factory is resolved at call time
    so both façades share one patchable seam.
    """
    factory = client_factory or create_qbit_client
    try:
        factory(config)
    except (QbitAuthenticationError, QbitConnectionError, OSError) as error:
        return ConnectionAttempt(ok=False, detail=str(error))
    return ConnectionAttempt(ok=True)


def render_env_file(config: QbitConfig) -> str:
    """Render the exact env-file body for `config`."""
    values = (config.host, config.username, config.password)
    lines = [
        f"{variable}={_encode_value(value)}"
        for variable, value in zip(CONNECTION_VARIABLES, values, strict=True)
    ]
    return "\n".join(lines) + "\n"


def _encode_value(value: str) -> str:
    """Quote a value so the env-file loader reads it back unchanged."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_connection_env_file(
    target: Path, config: QbitConfig, *, force: bool = False
) -> Path:
    """Write `config` to `target` at `0600`, refusing to clobber.

    Raises `EnvFileExistsError` unless `force`. The explicit `chmod` is
    not redundant: `O_CREAT`'s mode only applies to a file being
    created, so an overwritten file would otherwise keep whatever
    permissions it already had.
    """
    if target.exists() and not force:
        raise EnvFileExistsError(target)

    target.parent.mkdir(mode=ENV_DIRECTORY_MODE, parents=True, exist_ok=True)
    descriptor = os.open(
        target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, ENV_FILE_MODE
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(render_env_file(config))
    os.chmod(target, ENV_FILE_MODE)
    return target


class MaskingKind(StrEnum):
    """How a configuration source outranks the file just written."""

    ENVIRONMENT = "environment"
    PROJECT_ENV_FILE = "project_env_file"
    EXPLICIT_ENV_FILE = "explicit_env_file"


@dataclass(frozen=True)
class MaskingSource:
    """One source that wins over the freshly written user env file."""

    kind: MaskingKind
    variable: str
    origin: str

    @property
    def message(self) -> str:
        """Name the winning source and what to do about it."""
        if self.kind is MaskingKind.ENVIRONMENT:
            return (
                f"{self.variable} is exported in this shell and takes "
                "precedence. This file will not be used for it until you "
                "unset it."
            )
        if self.kind is MaskingKind.PROJECT_ENV_FILE:
            return (
                f"{self.variable} is set in {self.origin} and takes "
                "precedence while you run qbit-ops from this directory."
            )
        return (
            f"{self.variable} points at {self.origin}, so qbit-ops reads "
            "that file instead of this one. Unset it to use the file just "
            "written."
        )


def detect_masking_sources(
    target: Path,
    *,
    environment: Mapping[str, str],
    explicit_env_file_variable: str,
    project_env_file: Path | None = None,
    project_env_variables: Sequence[str] = (),
) -> tuple[MaskingSource, ...]:
    """Report every source that outranks `target`, most decisive first.

    Pure: the caller supplies the environment and the variables the
    project `.env` defines, this only classifies. An exported variable
    wins over both files, so it is reported even when an env-file
    override already bypasses `target` entirely.
    """
    sources: list[MaskingSource] = []

    explicit = (environment.get(explicit_env_file_variable) or "").strip()
    if explicit and Path(explicit).expanduser() != target:
        sources.append(
            MaskingSource(
                kind=MaskingKind.EXPLICIT_ENV_FILE,
                variable=explicit_env_file_variable,
                origin=explicit,
            )
        )

    for variable in CONNECTION_VARIABLES:
        if (environment.get(variable) or "").strip():
            sources.append(
                MaskingSource(
                    kind=MaskingKind.ENVIRONMENT,
                    variable=variable,
                    origin="this shell",
                )
            )

    if project_env_file is not None and project_env_file != target:
        for variable in CONNECTION_VARIABLES:
            if variable in project_env_variables:
                sources.append(
                    MaskingSource(
                        kind=MaskingKind.PROJECT_ENV_FILE,
                        variable=variable,
                        origin=str(project_env_file),
                    )
                )

    return tuple(sources)
