"""Load qBittorrent connection settings from the environment."""

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from qbit_core.config import (
    CONNECTION_VARIABLES,
    HOST_VARIABLE,
    PASSWORD_VARIABLE,
    USER_VARIABLE,
    QbitConfig,
)
from qbit_core.features.connection_setup import (
    MaskingSource,
    detect_masking_sources,
)

PROJECT_ENV_FILE = ".env"
ASCII_TITLES_VARIABLE = "QBIT_OPS_ASCII_TITLES"
APP_ENV_FILE_VARIABLE = "QBIT_OPS_ENV_FILE"
APP_CONFIG_DIR = "qbit-ops"

__all__ = [
    "QbitConfig",
    "ConfigError",
    "load_qbit_config",
    "get_user_env_file",
    "collect_masking_sources",
    "load_ascii_titles_preference",
]


class ConfigError(RuntimeError):
    """Report invalid or missing application configuration."""


def load_qbit_config() -> QbitConfig:
    """Load qBittorrent configuration from `.env` and environment variables."""
    _load_env_files()

    missing_variables: list[str] = []
    host = _read_required_env(HOST_VARIABLE, missing_variables)
    username = _read_required_env(USER_VARIABLE, missing_variables)
    password = _read_required_env(PASSWORD_VARIABLE, missing_variables)

    if missing_variables:
        variables = ", ".join(missing_variables)
        raise ConfigError(
            "Missing required environment variable(s): "
            f"{variables}. Run 'qbit-ops init' to create "
            f"{get_user_env_file()}, or set them in the environment."
        )

    return QbitConfig(host=host, username=username, password=password)


def load_ascii_titles_preference() -> bool:
    """Whether TUI window titles should skip Unicode small capitals.

    A setting, not a probe: no process can read the terminal's font, so
    the only honest switch is one the operator throws. Off by default --
    the small capitals are the intended look, and `qbit-ops doctor`
    explains when to turn them off.
    """
    _load_env_files()
    value = os.getenv(ASCII_TITLES_VARIABLE, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _load_env_files() -> None:
    """Load env files without overriding existing environment values."""
    explicit_env_file = os.getenv(APP_ENV_FILE_VARIABLE)
    if explicit_env_file:
        load_dotenv(
            dotenv_path=Path(explicit_env_file).expanduser(),
            override=False,
        )
        return

    for env_file in _get_default_env_files():
        load_dotenv(dotenv_path=env_file, override=False)


def _get_default_env_files() -> list[Path]:
    """Return default env files from local project and user config locations.

    Tests must isolate `HOME`/`XDG_CONFIG_HOME` and the working directory,
    or this can resolve a real `.env` and hit a live qBittorrent instance.
    """
    return [
        Path.cwd() / PROJECT_ENV_FILE,
        get_user_env_file(),
    ]


def get_user_env_file() -> Path:
    """Return the user-level qbit-ops env file path."""
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    config_home = (
        Path(xdg_config_home).expanduser()
        if xdg_config_home
        else Path.home() / ".config"
    )

    return config_home / APP_CONFIG_DIR / PROJECT_ENV_FILE


def collect_masking_sources(target: Path) -> tuple[MaskingSource, ...]:
    """Report which live configuration sources outrank `target`.

    The observation half of the precedence contract: reads the process
    environment and the current directory's `.env` (the two sources
    `_load_env_files` resolves before the user file), then hands the
    facts to `detect_masking_sources` for classification. A snapshot,
    not a prediction -- a shell opened later with different exports is
    not covered.
    """
    project_env_file: Path | None = None
    project_env_variables: tuple[str, ...] = ()

    if not (os.getenv(APP_ENV_FILE_VARIABLE) or "").strip():
        candidate = Path.cwd() / PROJECT_ENV_FILE
        if candidate.is_file():
            values = dotenv_values(candidate)
            project_env_file = candidate
            project_env_variables = tuple(
                variable
                for variable in CONNECTION_VARIABLES
                if (values.get(variable) or "").strip()
            )

    return detect_masking_sources(
        target,
        environment=os.environ,
        explicit_env_file_variable=APP_ENV_FILE_VARIABLE,
        project_env_file=project_env_file,
        project_env_variables=project_env_variables,
    )


def _read_required_env(name: str, missing_variables: list[str]) -> str:
    """Read an environment variable and track missing values."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        missing_variables.append(name)
        return ""

    return value.strip()
