from __future__ import annotations

import json
import re
from typing import Any

from omaplex.activity import recent_snapshot, save_snapshot
from omaplex.client import PlexClient
from omaplex.common import (
    ConfigurationError,
    PlexError,
    ResponseError,
    clean_text,
    isoformat,
    utc_now,
    wall_deadline,
)
from omaplex.connection import client_from_saved, status_document
from omaplex.constants import MAX_CACHE_BYTES, MAX_SECTIONS


def print_json(value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_CACHE_BYTES:
        raise ResponseError("Plex output exceeded the size limit")
    print(payload)


def command_refresh() -> int:
    try:
        with wall_deadline(25, "Plex refresh exceeded twenty-five seconds"):
            client, config = client_from_saved()
            snapshot = recent_snapshot(client, config)
        save_snapshot(snapshot)
        print_json(snapshot)
        return 0
    except PlexError as error:
        saved = status_document()
        saved["sourceState"] = "offline" if saved["items"] else saved["sourceState"]
        saved["stale"] = True
        saved["error"] = clean_text(error, 220)
        print_json(saved)
        return 1


def scan_libraries(client: PlexClient) -> dict[str, Any]:
    document = client.request_json("/library/sections")
    directories = document.get("MediaContainer", {}).get("Directory", [])
    if not isinstance(directories, list) or len(directories) > MAX_SECTIONS:
        raise ResponseError("Plex returned an invalid library list")
    sections: list[tuple[str, str]] = []
    for raw in directories:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "")
        section = str(raw.get("key") or "")
        if kind not in {"movie", "show"} or not re.fullmatch(r"\d{1,12}", section):
            continue
        if any(existing == section for existing, _ in sections):
            continue
        sections.append((section, kind))
    if not sections:
        raise ConfigurationError("Plex has no movie or show libraries to scan")
    for section, _ in sections:
        client.request_empty("/library/sections/" + section + "/refresh")
    return {
        "accepted": True,
        "sectionCount": len(sections),
        "movieSections": sum(1 for _, kind in sections if kind == "movie"),
        "seriesSections": sum(1 for _, kind in sections if kind == "show"),
        "requestedAt": isoformat(utc_now()),
    }


def command_scan() -> int:
    with wall_deadline(25, "Plex library scan exceeded twenty-five seconds"):
        client, _ = client_from_saved()
        result = scan_libraries(client)
    print_json(result)
    return 0
