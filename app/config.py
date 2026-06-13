"""Load qBittorrent connection settings from the environment."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ENV_FILE = ".env"
APP_ENV_FILE_VARIABLE = "QBIT_OPS_ENV_FILE"
APP_CONFIG_DIR = "qbit-ops"


@dataclass(frozen=True)
class QbitConfig:
    """Store qBittorrent connection settings."""

    host: str
    username: str
    password: str


class ConfigError(RuntimeError):
    """Report invalid or missing application configuration."""


def load_qbit_config() -> QbitConfig:
    """Load qBittorrent configuration from `.env` and environment variables."""
    _load_env_files()

    missing_variables: list[str] = []
    host = _read_required_env("QBIT_HOST", missing_variables)
    username = _read_required_env("QBIT_USER", missing_variables)
    password = _read_required_env("QBIT_PASSWORD", missing_variables)

    if missing_variables:
        variables = ", ".join(missing_variables)
        raise ConfigError(
            "Missing required environment variable(s): "
            f"{variables}. Create a .env file from .env.example, or create "
            f"{_get_user_env_file()} for an installed application."
        )

    return QbitConfig(host=host, username=username, password=password)


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
    """Return default env files from local project and user config locations."""
    return [
        Path.cwd() / PROJECT_ENV_FILE,
        _get_user_env_file(),
    ]


def _get_user_env_file() -> Path:
    """Return the user-level qbit-ops env file path."""
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    config_home = (
        Path(xdg_config_home).expanduser()
        if xdg_config_home
        else Path.home() / ".config"
    )

    return config_home / APP_CONFIG_DIR / PROJECT_ENV_FILE


def _read_required_env(name: str, missing_variables: list[str]) -> str:
    """Read an environment variable and track missing values."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        missing_variables.append(name)
        return ""

    return value.strip()
