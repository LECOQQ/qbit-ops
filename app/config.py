"""Load qBittorrent connection settings from the environment."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


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
    load_dotenv()

    missing_variables: list[str] = []
    host = _read_required_env("QBIT_HOST", missing_variables)
    username = _read_required_env("QBIT_USER", missing_variables)
    password = _read_required_env("QBIT_PASSWORD", missing_variables)

    if missing_variables:
        variables = ", ".join(missing_variables)
        raise ConfigError(
            "Missing required environment variable(s): "
            f"{variables}. Create a .env file from .env.example."
        )

    return QbitConfig(host=host, username=username, password=password)


def _read_required_env(name: str, missing_variables: list[str]) -> str:
    """Read an environment variable and track missing values."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        missing_variables.append(name)
        return ""

    return value.strip()
