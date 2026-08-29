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
    MAX_AUTH_BYTES,
    MAX_CONFIG_BYTES,
    MAX_GEOMETRY_BYTES,
    MAX_PRIVATE_KEY_BYTES,
    MAX_SECTIONS,
    MAX_TOKEN_BYTES,
    PLUGIN_ID,
    SCHEMA_VERSION,
)

AUTH_MODE_MANUAL = "manual"
AUTH_MODE_PLEX = "plex"


def valid_token(value: Any) -> str:
    token = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{10," + str(MAX_TOKEN_BYTES) + r"}", token):
        raise ConfigurationError("The Plex token has an invalid format")
    return token


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
    server_name = clean_text(value.get("serverName"), 128)
    auth_mode = str(value.get("authMode") or AUTH_MODE_MANUAL)
    if auth_mode not in {AUTH_MODE_MANUAL, AUTH_MODE_PLEX}:
        raise ConfigurationError("The saved Plex authentication method is invalid")
    client_identifier = clean_text(value.get("clientIdentifier"), 128)
    if auth_mode == AUTH_MODE_PLEX:
        if not re.fullmatch(r"[A-Za-z0-9._-]{16,128}", client_identifier):
            raise ConfigurationError("The saved Plex client identifier is invalid")
    else:
        client_identifier = ""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "server": validate_origin(value.get("server")),
        "movieSectionIds": movies,
        "tvSectionIds": shows,
        "machineIdentifier": machine_id,
        "serverName": server_name,
        "authMode": auth_mode,
        "clientIdentifier": client_identifier,
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
    label = "Omaplex token"
    maximum = MAX_TOKEN_BYTES
    description = "Plex token"

    def validate(self, value: str) -> str:
        return valid_token(value)

    def lookup(self) -> str | None:
        try:
            return_code, output = run_bounded_output(
                ["secret-tool", "lookup", *self.attributes],
                maximum=self.maximum + 1,
                timeout=10,
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
            raise ConfigurationError(
                "The saved " + self.description + " is invalid"
            ) from error
        if not token:
            return None
        try:
            return self.validate(token)
        except ConfigurationError as error:
            raise ConfigurationError(
                "The saved " + self.description + " is invalid"
            ) from error

    def store(self, token: str) -> None:
        token = self.validate(token)
        try:
            return_code = run_no_output(
                ["secret-tool", "store", "--label=" + self.label, *self.attributes],
                input_bytes=token.encode("utf-8"),
                timeout=15,
            )
        except FileNotFoundError as error:
            raise ConfigurationError(
                "The desktop secret service is unavailable"
            ) from error
        if return_code != 0:
            raise ConfigurationError(
                "The desktop secret service could not save the " + self.description
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
                "The desktop secret service could not clear the " + self.description
            )


class AccountTokenStore(SecretStore):
    attributes = ("service", PLUGIN_ID, "kind", "account-token")
    label = "Omaplex Plex account token"
    description = "Plex account token"


class DeviceKeyStore(SecretStore):
    attributes = ("service", PLUGIN_ID, "kind", "device-key")
    label = "Omaplex device key"
    maximum = MAX_PRIVATE_KEY_BYTES
    description = "Plex device key"

    def validate(self, value: str) -> str:
        if (
            len(value.encode("utf-8")) > self.maximum
            or not value.startswith("-----BEGIN PRIVATE KEY-----\n")
            or not value.rstrip().endswith("-----END PRIVATE KEY-----")
        ):
            raise ConfigurationError("The Plex device key has an invalid format")
        return value


class PendingAccountTokenStore(AccountTokenStore):
    attributes = ("service", PLUGIN_ID, "kind", "pending-account-token")
    label = "Omaplex pending Plex account token"
    description = "pending Plex account token"


class PendingDeviceKeyStore(DeviceKeyStore):
    attributes = ("service", PLUGIN_ID, "kind", "pending-device-key")
    label = "Omaplex pending device key"
    description = "pending Plex device key"


def pending_auth_path() -> Path:
    return config_home() / "pending-auth.json"


def load_pending_auth() -> Any:
    return read_json_file(pending_auth_path(), MAX_AUTH_BYTES)


def save_pending_auth(value: dict[str, Any]) -> None:
    atomic_json_write(pending_auth_path(), value, MAX_AUTH_BYTES)
