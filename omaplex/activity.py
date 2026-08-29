from __future__ import annotations

import datetime as dt
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

from omaplex.client import PlexClient
from omaplex.common import (
    ResponseError,
    atomic_json_write,
    clean_text,
    finite_integer,
    isoformat,
    read_json_file,
    utc_now,
)
from omaplex.constants import (
    MAX_ACTIVITY_ITEMS,
    MAX_CACHE_BYTES,
    MAX_ITEMS,
    SCHEMA_VERSION,
    STALE_SECONDS,
)
from omaplex.media_items import (
    extract_metadata_items,
    format_episode_code,
    normalize_continue_item,
    normalize_media_item,
    sort_items_by_watch_state,
    to_public_item,
)


def cache_home() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "omaplex"


def recent_snapshot(
    client: PlexClient, config: dict[str, Any], now: dt.datetime | None = None
) -> dict[str, Any]:
    checked_at = now or utc_now()
    movie_raw: list[Any] = []
    series_raw: list[Any] = []
    query = urllib.parse.urlencode(
        {"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 30}
    )
    continue_rows = extract_metadata_items(
        client.request_json("/hubs/home/continueWatching?" + query)
    )
    on_deck = extract_metadata_items(client.request_json("/library/onDeck?" + query))
    continue_rows.extend(on_deck)
    continue_rows.sort(
        key=lambda item: (
            finite_integer(
                item.get("lastViewedAt") or item.get("updatedAt") or item.get("addedAt")
            )
            if isinstance(item, dict)
            else 0
        ),
        reverse=True,
    )
    continue_items: list[dict[str, Any]] = []
    seen_continue: set[str] = set()
    for row in continue_rows:
        item = normalize_continue_item(row, checked_at)
        if item is None or not isinstance(row, dict):
            continue
        if row.get("type") == "episode":
            group = (
                "show:"
                + str(
                    row.get("grandparentRatingKey") or row.get("grandparentTitle") or ""
                ).lower()
            )
        else:
            group = "movie:" + str(row.get("ratingKey") or "")
        if group in seen_continue:
            continue
        seen_continue.add(group)
        continue_items.append(item)
        if len(continue_items) >= MAX_ACTIVITY_ITEMS:
            break
    continuation: dict[str, dict[str, str]] = {}
    for candidate in on_deck:
        if not isinstance(candidate, dict) or candidate.get("type") != "episode":
            continue
        show_key = str(candidate.get("grandparentRatingKey") or "")
        rating_key = str(candidate.get("ratingKey") or "")
        if not re.fullmatch(r"\d{1,96}", show_key) or not re.fullmatch(
            r"\d{1,96}", rating_key
        ):
            continue
        code = format_episode_code(candidate.get("parentIndex"), candidate.get("index"))
        prefix = (
            "Resume " if finite_integer(candidate.get("viewOffset")) > 0 else "Next "
        )
        if show_key not in continuation:
            continuation[show_key] = {
                "ratingKey": rating_key,
                "hint": clean_text(prefix + (code or "episode"), 80),
            }
    for section in config["movieSectionIds"]:
        movie_raw.extend(
            extract_metadata_items(
                client.request_json(
                    "/library/sections/" + section + "/recentlyAdded?type=1&" + query
                )
            )
        )
    for section in config["tvSectionIds"]:
        series_raw.extend(
            extract_metadata_items(
                client.request_json(
                    "/library/sections/" + section + "/recentlyAdded?type=4&" + query
                )
            )
        )
    movie_normalized = [
        value
        for value in (normalize_media_item(item, checked_at) for item in movie_raw)
        if value is not None
    ]
    movie_normalized.sort(key=lambda item: item["addedEpoch"], reverse=True)
    movie_items = [
        to_public_item(item) for item in movie_normalized[:MAX_ACTIVITY_ITEMS]
    ]
    sort_items_by_watch_state(movie_items)
    series_normalized = [
        value
        for value in (normalize_media_item(item, checked_at) for item in series_raw)
        if value is not None
    ]
    series_normalized.sort(key=lambda item: item["addedEpoch"], reverse=True)
    seen: set[str] = set()
    series_items: list[dict[str, Any]] = []
    for item in series_normalized:
        if item["group"] in seen:
            continue
        seen.add(item["group"])
        if item["kind"] == "show" and item["showKey"] in continuation:
            next_item = continuation[item["showKey"]]
            item["playbackRatingKey"] = next_item["ratingKey"]
            item["playbackHint"] = next_item["hint"]
        series_items.append(to_public_item(item))
        if len(series_items) >= MAX_ACTIVITY_ITEMS:
            break
    sort_items_by_watch_state(series_items)
    items = movie_items + series_items
    items.sort(key=lambda item: item["addedAt"], reverse=True)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "configured": True,
        "sourceState": "updated" if items or continue_items else "empty",
        "stale": False,
        "items": items,
        "continueItems": continue_items,
        "movieItems": movie_items,
        "seriesItems": series_items,
        "newCount": sum(1 for item in items if item["isNew"]),
        "lastSuccessAt": isoformat(checked_at),
        "error": "",
    }


def validate_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise ResponseError("Saved Plex data has an unsupported format")
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or len(raw_items) > MAX_ITEMS:
        raise ResponseError("Saved Plex data has an invalid item list")

    def validate_items(source: Any, maximum: int = MAX_ITEMS) -> list[dict[str, Any]]:
        if not isinstance(source, list) or len(source) > maximum:
            raise ResponseError("Saved Plex data has an invalid item list")
        validated: list[dict[str, Any]] = []
        for raw in source:
            item = validate_item(raw)
            if item is not None:
                validated.append(item)
        return validated

    def validate_item(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        key = str(raw.get("ratingKey") or "")
        playback_key = str(raw.get("playbackRatingKey") or key)
        kind = str(raw.get("kind") or "")
        state = str(raw.get("watchState") or "")
        added_at = str(raw.get("addedAt") or "")
        if (
            not re.fullmatch(r"\d{1,96}", key)
            or not re.fullmatch(r"\d{1,96}", playback_key)
            or kind not in {"movie", "show"}
            or state not in {"unwatched", "started", "watched"}
        ):
            return None
        try:
            dt.datetime.fromisoformat(added_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        title = clean_text(raw.get("title"))
        if not title:
            return None
        return {
            "ratingKey": key,
            "kind": kind,
            "title": title,
            "subtitle": clean_text(raw.get("subtitle")),
            "addedAt": added_at,
            "addedLabel": clean_text(raw.get("addedLabel"), 80),
            "watchState": state,
            "isNew": raw.get("isNew") is True,
            "playbackRatingKey": playback_key,
            "playbackHint": clean_text(raw.get("playbackHint"), 80),
        }

    items = validate_items(raw_items)
    continue_items = validate_items(value.get("continueItems", []), MAX_ACTIVITY_ITEMS)
    movie_items = validate_items(
        value.get("movieItems", [item for item in items if item["kind"] == "movie"]),
        MAX_ACTIVITY_ITEMS,
    )
    series_items = validate_items(
        value.get("seriesItems", [item for item in items if item["kind"] == "show"]),
        MAX_ACTIVITY_ITEMS,
    )
    last_success = str(value.get("lastSuccessAt") or "")
    try:
        dt.datetime.fromisoformat(last_success.replace("Z", "+00:00"))
    except ValueError as error:
        raise ResponseError("Saved Plex data has an invalid timestamp") from error
    return {
        "schemaVersion": SCHEMA_VERSION,
        "configured": True,
        "sourceState": "saved" if items else "empty",
        "stale": cache_is_stale(last_success),
        "items": items,
        "continueItems": continue_items,
        "movieItems": movie_items,
        "seriesItems": series_items,
        "newCount": sum(1 for item in items if item["isNew"]),
        "lastSuccessAt": last_success,
        "error": "",
    }


def cache_is_stale(timestamp_value: str, now: dt.datetime | None = None) -> bool:
    try:
        saved = dt.datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (now or utc_now()) - saved > dt.timedelta(seconds=STALE_SECONDS)


def load_snapshot() -> dict[str, Any] | None:
    value = read_json_file(cache_home() / "recent.json", MAX_CACHE_BYTES)
    return None if value is None else validate_snapshot(value)


def save_snapshot(value: dict[str, Any]) -> None:
    stored = dict(value)
    stored.pop("error", None)
    atomic_json_write(cache_home() / "recent.json", stored, MAX_CACHE_BYTES)
