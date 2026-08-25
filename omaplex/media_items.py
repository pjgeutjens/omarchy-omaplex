from __future__ import annotations

import datetime as dt
import re
from typing import Any

from omaplex.common import ResponseError, clean_text, finite_integer, isoformat
from omaplex.constants import MAX_ITEMS, NEW_AGE_DAYS


def parse_plex_timestamp(value: Any) -> dt.datetime | None:
    seconds = finite_integer(value)
    if seconds <= 0:
        return None
    try:
        return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def format_episode_code(season_value: Any, episode_value: Any) -> str:
    season = finite_integer(season_value, -1)
    episode = finite_integer(episode_value, -1)
    season_code = "" if season < 0 else "S" + str(season).zfill(2)
    episode_code = "" if episode < 0 else "E" + str(episode).zfill(2)
    return season_code + episode_code


def derive_watch_state(item: dict[str, Any]) -> str:
    if finite_integer(item.get("viewCount")) > 0:
        return "watched"
    if finite_integer(item.get("viewOffset")) > 0:
        return "started"
    return "unwatched"


def format_added_label(added: dt.datetime, now: dt.datetime) -> str:
    local = added.astimezone()
    today = now.astimezone().date()
    if local.date() == today:
        return "Today · " + local.strftime("%H:%M")
    if local.date() == today - dt.timedelta(days=1):
        return "Yesterday"
    return local.strftime(
        "%d %b" if local.year == now.astimezone().year else "%d %b %Y"
    ).lstrip("0")


def format_played_label(value: Any, now: dt.datetime) -> str:
    viewed = parse_plex_timestamp(value)
    if viewed is None:
        return ""
    seconds = max(0, int((now - viewed).total_seconds()))
    if seconds < 60:
        return "Played just now"
    if seconds < 60 * 60:
        return "Played " + str(max(1, seconds // 60)) + "m ago"
    if seconds < 24 * 60 * 60:
        return "Played " + str(seconds // (60 * 60)) + "h ago"
    if seconds < 7 * 24 * 60 * 60:
        return "Played " + str(seconds // (24 * 60 * 60)) + "d ago"
    return "Played " + format_added_label(viewed, now)


def normalize_media_item(item: Any, now: dt.datetime) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    raw_kind = str(item.get("type") or "")
    if raw_kind not in {"movie", "episode"}:
        return None
    rating_key = str(item.get("ratingKey") or "")
    if not re.fullmatch(r"\d{1,96}", rating_key):
        return None
    added = parse_plex_timestamp(item.get("addedAt"))
    if added is None:
        return None
    episode = raw_kind == "episode"
    title = clean_text(item.get("grandparentTitle") if episode else item.get("title"))
    if not title:
        return None
    if episode:
        code = format_episode_code(item.get("parentIndex"), item.get("index"))
        subtitle = " · ".join(
            part for part in ["Show", code, clean_text(item.get("title"))] if part
        )
        show_key = clean_text(item.get("grandparentRatingKey"), 96)
        group = "show:" + (show_key or title.lower())
        kind = "show"
    else:
        year = finite_integer(item.get("year"), -1)
        subtitle = ("Movie · " + str(year)) if year > 0 else "Movie"
        group = "movie:" + rating_key
        kind = "movie"
        show_key = ""
    state = derive_watch_state(item)
    return {
        "ratingKey": rating_key,
        "group": group,
        "showKey": show_key,
        "kind": kind,
        "title": title,
        "subtitle": clean_text(subtitle),
        "addedAt": isoformat(added),
        "addedEpoch": int(added.timestamp()),
        "addedLabel": format_added_label(added, now),
        "watchState": state,
        "isNew": state == "unwatched"
        and now - added <= dt.timedelta(days=NEW_AGE_DAYS),
        "playbackRatingKey": rating_key,
        "playbackHint": "",
    }


def to_public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if key not in {"group", "showKey", "addedEpoch"}
    }


def normalize_continue_item(item: Any, now: dt.datetime) -> dict[str, Any] | None:
    normalized = normalize_media_item(item, now)
    if normalized is None:
        return None
    normalized["isNew"] = False
    normalized["addedLabel"] = format_played_label(
        item.get("lastViewedAt") or item.get("updatedAt")
        if isinstance(item, dict)
        else None,
        now,
    )
    offset = (
        max(0, finite_integer(item.get("viewOffset"))) if isinstance(item, dict) else 0
    )
    duration = (
        max(0, finite_integer(item.get("duration"))) if isinstance(item, dict) else 0
    )
    if offset > 0 and duration > 0:
        normalized["playbackHint"] = (
            "Resume " + str(min(99, round(offset * 100 / duration))) + "%"
        )
    elif normalized["kind"] == "show":
        normalized["playbackHint"] = "Next episode"
    return to_public_item(normalized)


def sort_items_by_watch_state(items: list[dict[str, Any]]) -> None:
    priority = {"started": 0, "unwatched": 1, "watched": 2}
    items.sort(key=lambda item: priority[item["watchState"]])


def extract_metadata_items(document: dict[str, Any]) -> list[Any]:
    value = document.get("MediaContainer", {}).get("Metadata", [])
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise ResponseError("Plex returned an invalid recently added list")
    return value
