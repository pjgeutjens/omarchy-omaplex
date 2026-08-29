from __future__ import annotations

import os
import re
import stat
import time
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

from omaplex.client import HttpMethod, PlexClient
from omaplex.common import (
    ConfigurationError,
    PlexError,
    ResponseError,
    clean_text,
    finite_integer,
)
from omaplex.constants import MAX_SUBTITLE_BYTES, MAX_SUBTITLE_RESULTS, SCHEMA_VERSION


def subtitle_language(value: Any) -> str:
    language = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z]{2}", language):
        raise ConfigurationError("Subtitle search language must use two letters")
    return language


def subtitle_stream_key(value: Any) -> str:
    key = str(value or "")
    if not re.fullmatch(r"/library/streams/\d{1,96}", key):
        raise ConfigurationError("Invalid Plex subtitle result")
    return key


def subtitle_rating_key(value: Any) -> str:
    rating_key = str(value or "")
    if not re.fullmatch(r"\d{1,96}", rating_key):
        raise ConfigurationError("Invalid Plex rating key")
    return rating_key


def subtitle_format(value: Any) -> str:
    format_name = str(value or "srt").strip().lower()
    if format_name not in {"ass", "smi", "srt", "ssa", "sub", "vtt"}:
        raise ConfigurationError("Invalid Plex subtitle format")
    return format_name


def subtitle_flag(value: Any) -> bool:
    return value is True or finite_integer(value) == 1


def subtitle_display_text(value: Any, maximum: int) -> str:
    return re.sub(r"[{}\\]", "", clean_text(value, maximum))


def subtitle_label(raw: dict[str, Any]) -> str:
    label = subtitle_display_text(
        raw.get("extendedDisplayTitle")
        or raw.get("displayTitle")
        or raw.get("title")
        or raw.get("providerTitle")
        or "Subtitle",
        160,
    )
    return label or "Subtitle"


def selected_subtitle_streams(
    client: PlexClient, rating_key: str
) -> dict[str, dict[str, Any]]:
    document = client.request_json("/library/metadata/" + rating_key)
    metadata = document.get("MediaContainer", {}).get("Metadata", [])
    if (
        not isinstance(metadata, list)
        or not metadata
        or not isinstance(metadata[0], dict)
    ):
        raise ResponseError("Plex returned invalid subtitle metadata")
    media = metadata[0].get("Media", [])
    if not isinstance(media, list) or not media or not isinstance(media[0], dict):
        return {}
    parts = media[0].get("Part", [])
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
        return {}
    streams = parts[0].get("Stream", [])
    if not isinstance(streams, list) or len(streams) > 64:
        raise ResponseError("Plex returned invalid subtitle metadata")
    selected: dict[str, dict[str, Any]] = {}
    for raw in streams:
        if (
            not isinstance(raw, dict)
            or finite_integer(raw.get("streamType")) != 3
            or not subtitle_flag(raw.get("selected"))
        ):
            continue
        try:
            key = subtitle_stream_key(raw.get("key"))
            subtitle_format(raw.get("format") or raw.get("codec"))
        except ConfigurationError:
            continue
        selected[key] = raw
    return selected


def search_subtitles(
    client: PlexClient, rating_key: str, language: str
) -> dict[str, Any]:
    rating_key = subtitle_rating_key(rating_key)
    language = subtitle_language(language)
    query = urllib.parse.urlencode(
        {"language": language, "hearingImpaired": 0, "forced": 0}
    )
    document = client.request_json(
        "/library/metadata/" + rating_key + "/subtitles?" + query
    )
    container = document.get("MediaContainer", {})
    streams = container.get("Stream", []) if isinstance(container, dict) else []
    if not isinstance(streams, list) or len(streams) > MAX_SUBTITLE_RESULTS * 4:
        raise ResponseError("Plex returned an invalid subtitle search result")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        saved_streams = selected_subtitle_streams(client, rating_key)
    except PlexError:
        saved_streams = {}
    for key, raw in saved_streams.items():
        format_name = subtitle_format(raw.get("format") or raw.get("codec"))
        seen.add(key)
        items.append(
            {
                "key": key,
                "label": "Saved · " + subtitle_label(raw),
                "provider": subtitle_display_text(raw.get("providerTitle"), 80),
                "format": format_name,
                "language": clean_text(
                    raw.get("languageCode") or raw.get("language") or language, 12
                ),
                "hearingImpaired": subtitle_flag(raw.get("hearingImpaired")),
                "forced": subtitle_flag(raw.get("forced")),
                "perfectMatch": False,
                "score": 0,
            }
        )
    for raw in streams:
        if not isinstance(raw, dict):
            continue
        try:
            key = subtitle_stream_key(raw.get("key"))
            format_name = subtitle_format(raw.get("format") or raw.get("codec"))
        except ConfigurationError:
            continue
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "key": key,
                "label": subtitle_label(raw),
                "provider": subtitle_display_text(raw.get("providerTitle"), 80),
                "format": format_name,
                "language": clean_text(
                    raw.get("languageCode") or raw.get("language") or language, 12
                ),
                "hearingImpaired": subtitle_flag(raw.get("hearingImpaired")),
                "forced": subtitle_flag(raw.get("forced")),
                "perfectMatch": subtitle_flag(raw.get("perfectMatch")),
                "score": max(0, finite_integer(raw.get("score"))),
            }
        )
        if len(items) >= MAX_SUBTITLE_RESULTS:
            break
    return {
        "schemaVersion": SCHEMA_VERSION,
        "ratingKey": rating_key,
        "language": language,
        "items": items,
    }


def open_private_output_directory(path: str) -> int:
    if not path.startswith("/tmp/omaplex-player-") or len(path) > 512:
        raise ConfigurationError("Invalid player subtitle directory")
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ConfigurationError(
            "The player subtitle directory is unavailable"
        ) from error
    details = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise ConfigurationError("The player subtitle directory is unsafe")
    return descriptor


def read_downloaded_subtitle(
    client: PlexClient, key: str, deadline: float | None = None
) -> bytes:
    download_deadline = deadline or time.monotonic() + 15
    while True:
        try:
            response = client.open(key)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status == 404 and time.monotonic() < download_deadline:
                time.sleep(0.25)
                continue
            raise ResponseError("Plex could not download the subtitle") from error
        try:
            length = finite_integer(response.headers.get("Content-Length"), -1)
            if length > MAX_SUBTITLE_BYTES:
                raise ResponseError("Plex subtitle exceeded the size limit")
            payload = response.read(MAX_SUBTITLE_BYTES + 1)
        finally:
            response.close()
        if not payload or len(payload) > MAX_SUBTITLE_BYTES:
            raise ResponseError("Plex subtitle exceeded the size limit")
        if b"\x00" in payload[:4096]:
            raise ResponseError("Plex returned an invalid subtitle file")
        return payload


def wait_for_selected_subtitle(
    client: PlexClient,
    rating_key: str,
    previous_keys: set[str],
    deadline: float,
) -> tuple[str, str]:
    while time.monotonic() < deadline:
        selected = selected_subtitle_streams(client, rating_key)
        for key, raw in selected.items():
            if key not in previous_keys:
                return key, subtitle_format(raw.get("format") or raw.get("codec"))
        time.sleep(0.25)
    raise ResponseError("Plex did not finish saving the selected subtitle")


def download_subtitle(
    client: PlexClient,
    rating_key: str,
    stream_key: str,
    format_name: str,
    output_directory: str,
) -> dict[str, str]:
    rating_key = subtitle_rating_key(rating_key)
    stream_key = subtitle_stream_key(stream_key)
    format_name = subtitle_format(format_name)
    directory = open_private_output_directory(output_directory)
    try:
        deadline = time.monotonic() + 15
        selected_before = selected_subtitle_streams(client, rating_key)
        if stream_key in selected_before:
            selected_key = stream_key
            selected_format = subtitle_format(
                selected_before[stream_key].get("format")
                or selected_before[stream_key].get("codec")
                or format_name
            )
        else:
            query = urllib.parse.urlencode({"key": stream_key})
            client.request_empty(
                "/library/metadata/" + rating_key + "/subtitles?" + query,
                method=HttpMethod.PUT,
            )
            selected_key, selected_format = wait_for_selected_subtitle(
                client, rating_key, set(selected_before), deadline
            )
        payload = read_downloaded_subtitle(client, selected_key, deadline)
        filename = "subtitle-" + selected_key.rsplit("/", 1)[1] + "." + selected_format
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(filename, flags, 0o600, dir_fd=directory)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ConfigurationError("Could not save the selected subtitle") from error
    finally:
        os.close(directory)
    return {"path": str(Path(output_directory) / filename)}
