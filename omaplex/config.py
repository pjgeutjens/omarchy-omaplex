from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from omaplex.client import validate_origin
from omaplex.common import (
    ConfigurationError,
    ResponseError,
    atomic_json_write,
    clean_text,
    read_json_file,
    run_bounded_output,
    run_no_output,
)
from omaplex.constants import (
    MAX_CONFIG_BYTES,
    MAX_GEOMETRY_BYTES,
    MAX_SECTIONS,
    PLUGIN_ID,
    SCHEMA_VERSION,
)


def config_home() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "omaplex"


def section_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_SECTIONS:
        raise ConfigurationError("Plex library selection is invalid")
    result: list[str] = []
    for raw in value:
        item = str(raw or "")
        if not re.fullmatch(r"\d{1,12}", item):
            raise ConfigurationError(
                "Plex library selection contains an invalid section ID"
            )
        if item not in result:
            result.append(item)
    return result


def validate_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise ConfigurationError("Omaplex is not configured")
    movies = section_ids(value.get("movieSectionIds"))
    shows = section_ids(value.get("tvSectionIds"))
    if not movies and not shows:
        raise ConfigurationError("Select at least one movie or TV library")
    machine_id = clean_text(value.get("machineIdentifier"), 128)
    if machine_id and not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", machine_id):
        raise ConfigurationError("The saved Plex server identifier is invalid")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "server": validate_origin(value.get("server")),
        "movieSectionIds": movies,
        "tvSectionIds": shows,
        "machineIdentifier": machine_id,
    }


def load_config() -> dict[str, Any] | None:
    value = read_json_file(config_home() / "config.json", MAX_CONFIG_BYTES)
    return None if value is None else validate_config(value)


def save_config(config: dict[str, Any]) -> None:
    atomic_json_write(
        config_home() / "config.json", validate_config(config), MAX_CONFIG_BYTES
    )


def validate_window_geometry(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise ResponseError("Saved player geometry has an unsupported format")
    result: dict[str, int] = {"schemaVersion": SCHEMA_VERSION}
    limits = {
        "x": (-100000, 100000),
        "y": (-100000, 100000),
        "width": (160, 16384),
        "height": (90, 16384),
    }
    for name, (minimum, maximum) in limits.items():
        raw = value.get(name)
        if (
            isinstance(raw, bool)
            or not isinstance(raw, int)
            or raw < minimum
            or raw > maximum
        ):
            raise ResponseError("Saved player geometry is invalid")
        result[name] = raw
    return result


def load_window_geometry() -> dict[str, int] | None:
    value = read_json_file(config_home() / "player-window.json", MAX_GEOMETRY_BYTES)
    return None if value is None else validate_window_geometry(value)


def save_window_geometry(value: dict[str, int]) -> None:
    atomic_json_write(
        config_home() / "player-window.json",
        validate_window_geometry(value),
        MAX_GEOMETRY_BYTES,
    )


class SecretStore:
    attributes = ("service", PLUGIN_ID)

    def lookup(self) -> str | None:
        try:
            return_code, output = run_bounded_output(
                ["secret-tool", "lookup", *self.attributes], maximum=257, timeout=10
            )
        except FileNotFoundError as error:
            raise ConfigurationError(
                "The desktop secret service is unavailable"
            ) from error
        if return_code != 0:
            return None
        try:
            token = output.decode("utf-8").rstrip("\n")
        except UnicodeDecodeError as error:
            raise ConfigurationError("The saved Plex token is invalid") from error
        if token and not re.fullmatch(r"[A-Za-z0-9_-]{10,256}", token):
            raise ConfigurationError("The saved Plex token is invalid")
        return token or None

    def store(self, token: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,256}", token):
            raise ConfigurationError("The Plex token has an invalid format")
        try:
            return_code = run_no_output(
                ["secret-tool", "store", "--label=Omaplex token", *self.attributes],
                input_bytes=token.encode("utf-8"),
                timeout=15,
            )
        except FileNotFoundError as error:
            raise ConfigurationError(
                "The desktop secret service is unavailable"
            ) from error
        if return_code != 0:
            raise ConfigurationError(
                "The desktop secret service could not save the Plex token"
            )

    def clear(self) -> None:
        try:
            return_code = run_no_output(
                ["secret-tool", "clear", *self.attributes],
                timeout=10,
            )
        except FileNotFoundError as error:
            raise ConfigurationError(
                "The desktop secret service is unavailable"
            ) from error
        if return_code not in {0, 1}:
            raise ConfigurationError(
                "The desktop secret service could not clear the Plex token"
            )
