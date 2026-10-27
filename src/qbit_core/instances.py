"""Resolve one or several named qBittorrent instances from an environment.

Pure: the caller passes the mapping (typically `os.environ`); nothing
here reads the process environment or any file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from qbit_core.config import (
    CONNECTION_VARIABLES,
    QbitConfig,
)
from qbit_core.errors import InvalidInputError

__all__ = [
    "DEFAULT_INSTANCE_NAME",
    "INSTANCES_VARIABLE",
    "QbitInstance",
    "instances_from_environment",
    "validate_instance_name",
]

INSTANCES_VARIABLE = "QBIT_INSTANCES"
DEFAULT_INSTANCE_NAME = "default"

_NAME_PATTERN = re.compile(r"[a-z0-9_-]{1,32}")
_FIELDS = ("HOST", "USER", "PASSWORD")


@dataclass(frozen=True)
class QbitInstance:
    """A named qBittorrent connection."""

    name: str
    config: QbitConfig


def validate_instance_name(name: str) -> str:
    """Return `name` when it is 1 to 32 of `a-z 0-9 - _`, else raise."""
    if _NAME_PATTERN.fullmatch(name) is None:
        raise InvalidInputError(
            f"Invalid instance name {name!r}: use 1 to 32 characters among "
            "lowercase letters, digits, '-' and '_'."
        )
    return name


def instances_from_environment(
    environ: Mapping[str, str],
) -> tuple[QbitInstance, ...]:
    """Build the instances declared by `QBIT_INSTANCES`, in order.

    `QBIT_INSTANCES="main,second"` reads `QBIT_MAIN_HOST/USER/PASSWORD`
    and `QBIT_SECOND_*` (name uppercased, `-` becomes `_`). A blank or
    absent `QBIT_INSTANCES` yields one `default` instance read from
    `QBIT_HOST/QBIT_USER/QBIT_PASSWORD`. Raises `InvalidInputError` for a
    bad or duplicate name, or any variable missing or blank; messages
    name variables, never their values.
    """
    declared = environ.get(INSTANCES_VARIABLE, "")
    if declared.strip() == "":
        return (
            _build_instance(
                DEFAULT_INSTANCE_NAME, CONNECTION_VARIABLES, environ
            ),
        )

    names = [
        validate_instance_name(part.strip()) for part in declared.split(",")
    ]
    _reject_duplicates(names)
    return tuple(
        _build_instance(
            name,
            tuple(f"QBIT_{_env_token(name)}_{field}" for field in _FIELDS),
            environ,
        )
        for name in names
    )


def _env_token(name: str) -> str:
    return name.upper().replace("-", "_")


def _reject_duplicates(names: list[str]) -> None:
    seen: dict[str, str] = {}
    for name in names:
        token = _env_token(name)
        if token in seen:
            clash = (
                "is listed twice"
                if seen[token] == name
                else f"maps to the same variables as {seen[token]!r}"
            )
            raise InvalidInputError(
                f"Instance {name!r} in {INSTANCES_VARIABLE} {clash}; "
                "names must be unique."
            )
        seen[token] = name


def _build_instance(
    name: str, variables: tuple[str, ...], environ: Mapping[str, str]
) -> QbitInstance:
    missing = [
        variable
        for variable in variables
        if environ.get(variable, "").strip() == ""
    ]
    if missing:
        raise InvalidInputError(
            f"Instance {name!r} is missing {', '.join(missing)}: set each "
            "to a non-empty value."
        )
    host, username, password = (environ[variable] for variable in variables)
    return QbitInstance(
        name=name,
        config=QbitConfig(host=host, username=username, password=password),
    )
