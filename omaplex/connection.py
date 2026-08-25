from __future__ import annotations

import contextlib
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from omaplex.activity import cache_home, load_snapshot, recent_snapshot, save_snapshot
from omaplex.client import PlexClient, validate_origin
from omaplex.common import (
    ConfigurationError,
    PlexError,
    atomic_json_write,
    clean_text,
    read_regular_file,
)
from omaplex.config import (
    SecretStore,
    config_home,
    load_config,
    save_config,
    validate_config,
)
from omaplex.constants import (
    MAX_CACHE_BYTES,
    MAX_CONFIG_BYTES,
    MAX_ENV_BYTES,
    MAX_SETUP_BYTES,
    SCHEMA_VERSION,
)


def parse_env_file(path: Path) -> dict[str, str]:
    try:
        payload = read_regular_file(path, MAX_ENV_BYTES).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigurationError("The env file is not valid UTF-8") from error
    accepted = {
        "PLEX_BASE_URL",
        "PLEX_TOKEN",
        "PLEX_MOVIES_SECTION_ID",
        "PLEX_TV_SECTION_ID",
    }
    values: dict[str, str] = {}
    for line in payload.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw = line.split("=", 1)
        name = name.strip()
        if name not in accepted:
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def requested_ids(value: str) -> list[str]:
    return [part for part in re.split(r"[\s,]+", value.strip()) if part]


def configure_from_env(path: Path, store: SecretStore | None = None) -> dict[str, Any]:
    values = parse_env_file(path)
    server = validate_origin(values.get("PLEX_BASE_URL"))
    token = values.get("PLEX_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,256}", token):
        raise ConfigurationError("PLEX_TOKEN is missing or invalid")
    client = PlexClient(server, token)
    libraries, machine_id = client.discover()
    available_movies = [item["id"] for item in libraries if item["type"] == "movie"]
    available_shows = [item["id"] for item in libraries if item["type"] == "show"]
    movies = requested_ids(values.get("PLEX_MOVIES_SECTION_ID", "")) or available_movies
    shows = requested_ids(values.get("PLEX_TV_SECTION_ID", "")) or available_shows
    if any(item not in available_movies for item in movies):
        raise ConfigurationError(
            "The configured movie library was not found on this Plex server"
        )
    if any(item not in available_shows for item in shows):
        raise ConfigurationError(
            "The configured TV library was not found on this Plex server"
        )
    config = validate_config(
        {
            "schemaVersion": SCHEMA_VERSION,
            "server": server,
            "movieSectionIds": movies,
            "tvSectionIds": shows,
            "machineIdentifier": machine_id,
        }
    )
    (store or SecretStore()).store(token)
    save_config(config)
    return {
        "configured": True,
        "server": server,
        "movieLibraries": [item for item in libraries if item["id"] in movies],
        "tvLibraries": [item for item in libraries if item["id"] in shows],
        "warning": "Plain HTTP exposes Plex traffic on the network; use it only on a trusted LAN."
        if urllib.parse.urlsplit(server).scheme == "http"
        else "",
    }


def read_setup(stream: Any | None = None) -> tuple[str, str]:
    source = stream or sys.stdin.buffer
    raw = source.readline(MAX_SETUP_BYTES + 1)
    if len(raw) > MAX_SETUP_BYTES:
        raise ConfigurationError("Setup input exceeded the size limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigurationError("Setup input is not valid JSON") from error
    if not isinstance(value, dict) or set(value) - {"server", "token"}:
        raise ConfigurationError("Setup input is invalid")
    server = validate_origin(value.get("server"))
    token = str(value.get("token") or "")
    if token and not re.fullmatch(r"[A-Za-z0-9_-]{10,256}", token):
        raise ConfigurationError("The Plex token has an invalid format")
    return server, token


def connection_info(
    config: dict[str, Any] | None, libraries: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    if config is None:
        return {"server": "", "movieLibraries": [], "seriesLibraries": []}
    values = libraries or []
    movies = [
        {"id": item["id"], "title": clean_text(item.get("title"), 128)}
        for item in values
        if item.get("type") == "movie" and item.get("id") in config["movieSectionIds"]
    ]
    shows = [
        {"id": item["id"], "title": clean_text(item.get("title"), 128)}
        for item in values
        if item.get("type") == "show" and item.get("id") in config["tvSectionIds"]
    ]
    if not values:
        movies = [{"id": item, "title": ""} for item in config["movieSectionIds"]]
        shows = [{"id": item, "title": ""} for item in config["tvSectionIds"]]
    return {
        "server": config["server"],
        "movieLibraries": movies,
        "seriesLibraries": shows,
    }


def with_connection(
    document: dict[str, Any],
    config: dict[str, Any] | None,
    libraries: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    value = dict(document)
    value["connection"] = connection_info(config, libraries)
    return value


def restore_file(path: Path, value: dict[str, Any] | None, maximum: int) -> None:
    if value is None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
    else:
        atomic_json_write(path, value, maximum)


def configure_connection(
    server: str,
    token: str,
    store: SecretStore | None = None,
) -> dict[str, Any]:
    secret_store = store or SecretStore()
    saved_token = secret_store.lookup()
    requested_token = token or saved_token or ""
    if not requested_token:
        raise ConfigurationError("Enter a Plex token")
    client = PlexClient(server, requested_token)
    libraries, machine_id = client.discover()
    movies = [item["id"] for item in libraries if item["type"] == "movie"]
    shows = [item["id"] for item in libraries if item["type"] == "show"]
    config = validate_config(
        {
            "schemaVersion": SCHEMA_VERSION,
            "server": server,
            "movieSectionIds": movies,
            "tvSectionIds": shows,
            "machineIdentifier": machine_id,
        }
    )
    snapshot = recent_snapshot(client, config)
    old_config = load_config()
    old_snapshot = load_snapshot()
    try:
        if token:
            secret_store.store(requested_token)
        save_config(config)
        save_snapshot(snapshot)
    except Exception:
        with contextlib.suppress(PlexError):
            if saved_token:
                secret_store.store(saved_token)
            elif token:
                secret_store.clear()
        with contextlib.suppress(PlexError, OSError):
            restore_file(config_home() / "config.json", old_config, MAX_CONFIG_BYTES)
        with contextlib.suppress(PlexError, OSError):
            restore_file(cache_home() / "recent.json", old_snapshot, MAX_CACHE_BYTES)
        raise
    return with_connection(snapshot, config, libraries)


def clear_configuration(store: SecretStore | None = None) -> dict[str, Any]:
    (store or SecretStore()).clear()
    for path in (config_home() / "config.json", cache_home() / "recent.json"):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
    return with_connection(status_document(), None)


def client_from_saved(
    store: SecretStore | None = None,
) -> tuple[PlexClient, dict[str, Any]]:
    config = load_config()
    if config is None:
        raise ConfigurationError("Omaplex is not configured")
    token = (store or SecretStore()).lookup()
    if not token:
        raise ConfigurationError(
            "No Plex token was found in the desktop secret service"
        )
    return PlexClient(config["server"], token), config


def status_document() -> dict[str, Any]:
    config = load_config()
    if config is None:
        return with_connection(
            {
                "schemaVersion": SCHEMA_VERSION,
                "configured": False,
                "sourceState": "unconfigured",
                "stale": True,
                "items": [],
                "continueItems": [],
                "movieItems": [],
                "seriesItems": [],
                "newCount": 0,
                "lastSuccessAt": "",
                "error": "",
            },
            None,
        )
    snapshot = load_snapshot()
    if snapshot is not None:
        return with_connection(snapshot, config)
    return with_connection(
        {
            "schemaVersion": SCHEMA_VERSION,
            "configured": True,
            "sourceState": "empty",
            "stale": True,
            "items": [],
            "continueItems": [],
            "movieItems": [],
            "seriesItems": [],
            "newCount": 0,
            "lastSuccessAt": "",
            "error": "",
        },
        config,
    )
