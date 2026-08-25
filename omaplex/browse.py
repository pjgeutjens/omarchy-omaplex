from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
import urllib.parse
from enum import StrEnum
from typing import Any

from omaplex.client import PlexClient
from omaplex.common import (
    ConfigurationError,
    ResponseError,
    clean_text,
    finite_integer,
    isoformat,
    stop_process_group,
    utc_now,
)
from omaplex.constants import (
    MAX_BROWSE_ITEMS,
    MAX_EPISODES_PER_SERIES,
    MAX_EXPANDED_SERIES,
    MAX_FZF_BYTES,
    MAX_SEARCH_CANDIDATES,
    MAX_SEARCH_PAGE_SIZE,
    MAX_SEARCH_REQUESTS,
    SCHEMA_VERSION,
)
from omaplex.media_items import (
    normalize_media_item,
    parse_plex_timestamp,
    to_public_item,
)


class BrowseKind(StrEnum):
    MOVIES = "movies"
    SHOWS = "shows"
    EPISODES = "episodes"
    SEARCH = "search"


class SearchScope(StrEnum):
    MOVIES = "movies"
    SHOWS = "shows"


def browse_item(raw: Any, now: dt.datetime) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    raw_type = str(raw.get("type") or "")
    if raw_type in {"movie", "episode"}:
        normalized = normalize_media_item(raw, now)
        if normalized is None:
            return None
        item = to_public_item(normalized)
        item["isNew"] = False
        item["addedLabel"] = ""
        item["playable"] = True
        return item
    if raw_type != "show":
        return None
    rating_key = str(raw.get("ratingKey") or "")
    title = clean_text(raw.get("title"))
    if not re.fullmatch(r"\d{1,96}", rating_key) or not title:
        return None
    leaf_count = max(0, finite_integer(raw.get("leafCount")))
    viewed_count = max(0, finite_integer(raw.get("viewedLeafCount")))
    state = (
        "watched"
        if leaf_count > 0 and viewed_count >= leaf_count
        else ("started" if viewed_count > 0 else "unwatched")
    )
    added = parse_plex_timestamp(raw.get("addedAt")) or now
    episode_label = (
        str(leaf_count) + (" episode" if leaf_count == 1 else " episodes")
        if leaf_count
        else "Show"
    )
    return {
        "ratingKey": rating_key,
        "kind": "show",
        "title": title,
        "subtitle": clean_text("Show · " + episode_label),
        "addedAt": isoformat(added),
        "addedLabel": "",
        "watchState": state,
        "isNew": False,
        "playbackRatingKey": rating_key,
        "playbackHint": "Open episodes",
        "playable": False,
    }


def paged_library_rows(
    client: PlexClient,
    paths: list[str],
    maximum: int,
    request_budget: list[int],
) -> list[Any]:
    rows: list[Any] = []
    for base_path in paths:
        start = 0
        while len(rows) < maximum and request_budget[0] > 0:
            size = min(MAX_SEARCH_PAGE_SIZE, maximum - len(rows))
            separator = "&" if "?" in base_path else "?"
            path = (
                base_path
                + separator
                + urllib.parse.urlencode(
                    {
                        "X-Plex-Container-Start": start,
                        "X-Plex-Container-Size": size,
                    }
                )
            )
            request_budget[0] -= 1
            document = client.request_json(path)
            container = document.get("MediaContainer", {})
            page = container.get("Metadata", []) if isinstance(container, dict) else []
            if not isinstance(page, list) or len(page) > size:
                raise ResponseError("Plex returned an invalid search page")
            rows.extend(page)
            total = max(
                len(page), finite_integer(container.get("totalSize"), len(page))
            )
            start += len(page)
            if not page or start >= total:
                break
    return rows


def fallback_fuzzy_rank(
    items: list[dict[str, Any]], query: str
) -> list[dict[str, Any]]:
    tokens = [token for token in re.split(r"\s+", query.casefold()) if token]

    def score(item: dict[str, Any]) -> tuple[int, int, str] | None:
        haystack = (
            str(item.get("title") or "") + " " + str(item.get("subtitle") or "")
        ).casefold()
        total_gap = 0
        first = len(haystack)
        for token in tokens:
            cursor = -1
            positions: list[int] = []
            for character in token:
                cursor = haystack.find(character, cursor + 1)
                if cursor < 0:
                    return None
                positions.append(cursor)
            first = min(first, positions[0])
            total_gap += positions[-1] - positions[0] + 1 - len(token)
        return total_gap, first, haystack

    scored = [(value, item) for item in items if (value := score(item)) is not None]
    scored.sort(key=lambda pair: pair[0])
    return [item for _, item in scored]


def fzf_rank(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    if not items or not query:
        return items
    lines: list[bytes] = []
    size = 0
    for index, item in enumerate(items):
        searchable = clean_text(
            str(item.get("title") or "") + " " + str(item.get("subtitle") or ""), 600
        )
        line = (str(index) + "\t" + searchable + "\n").encode("utf-8")
        if size + len(line) > MAX_FZF_BYTES:
            break
        lines.append(line)
        size += len(line)
    if shutil.which("fzf") is None:
        return fallback_fuzzy_rank(items[: len(lines)], query)
    process = subprocess.Popen(
        [
            "fzf",
            "--filter",
            query,
            "--ignore-case",
            "--delimiter",
            "\t",
            "--nth",
            "2..",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(b"".join(lines), timeout=3)
    except subprocess.TimeoutExpired:
        stop_process_group(process)
        return fallback_fuzzy_rank(items[: len(lines)], query)
    if len(output) > size:
        raise ResponseError("fzf returned an invalid search result")
    ranked: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw_line in output.splitlines():
        raw_index = raw_line.split(b"\t", 1)[0]
        try:
            index = int(raw_index)
        except ValueError:
            continue
        if 0 <= index < len(items) and index not in seen:
            seen.add(index)
            ranked.append(items[index])
    return ranked


def season_query(value: str) -> tuple[str, int | None, int | None]:
    match = re.search(r"(?i)(?:^|\s)s(\d{1,3})(?:e(\d{0,3}))?(?=\s|$)", value)
    if match is None:
        return value, None, None
    title_query = (value[: match.start()] + " " + value[match.end() :]).strip()
    return (
        title_query,
        int(match.group(1)),
        int(match.group(2)) if match.group(2) else None,
    )


def search_document(
    client: PlexClient,
    config: dict[str, Any],
    query: str,
    offset: int,
    limit: int,
    scope: SearchScope,
) -> dict[str, Any]:
    if not isinstance(scope, SearchScope):
        raise ConfigurationError("Search scope must be movies or shows")
    title_query, season_number, episode_number = season_query(query)
    if scope is SearchScope.SHOWS and season_number is not None and not title_query:
        raise ConfigurationError("Add a show name before the season code")
    request_budget = [MAX_SEARCH_REQUESTS]
    movie_paths = [
        "/library/sections/" + section + "/all?type=1&sort=titleSort%3Aasc"
        for section in config["movieSectionIds"]
    ]
    show_paths = [
        "/library/sections/" + section + "/all?type=2&sort=titleSort%3Aasc"
        for section in config["tvSectionIds"]
    ]
    movie_rows = (
        []
        if scope is not SearchScope.MOVIES
        else paged_library_rows(
            client, movie_paths, MAX_SEARCH_CANDIDATES, request_budget
        )
    )
    show_rows = (
        []
        if scope is not SearchScope.SHOWS
        else paged_library_rows(
            client, show_paths, MAX_SEARCH_CANDIDATES, request_budget
        )
    )
    now = utc_now()
    movies = [
        value
        for value in (browse_item(row, now) for row in movie_rows)
        if value is not None
    ]
    shows = [
        value
        for value in (browse_item(row, now) for row in show_rows)
        if value is not None
    ]
    if scope is SearchScope.MOVIES:
        ranked = fzf_rank(movies, query)
    elif season_number is None:
        ranked = fzf_rank(shows, query)
    else:
        matching_shows = fzf_rank(shows, title_query)[:MAX_EXPANDED_SERIES]
        episode_rows: list[tuple[int, int, int, dict[str, Any]]] = []
        for show_rank, show in enumerate(matching_shows):
            key = str(show.get("ratingKey") or "")
            if not re.fullmatch(r"\d{1,96}", key):
                continue
            leaves = paged_library_rows(
                client,
                ["/library/metadata/" + key + "/allLeaves"],
                MAX_EPISODES_PER_SERIES,
                request_budget,
            )
            for raw in leaves:
                if (
                    not isinstance(raw, dict)
                    or finite_integer(raw.get("parentIndex"), -1) != season_number
                ):
                    continue
                if (
                    episode_number is not None
                    and finite_integer(raw.get("index"), -1) != episode_number
                ):
                    continue
                item = browse_item(raw, now)
                if item is not None:
                    episode_rows.append(
                        (
                            show_rank,
                            finite_integer(raw.get("parentIndex")),
                            finite_integer(raw.get("index")),
                            item,
                        )
                    )
        episode_rows.sort(
            key=lambda row: (row[0], row[1], row[2], row[3]["title"].casefold())
        )
        ranked = [item for _, _, _, item in episode_rows]
    total = len(ranked)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "search",
        "scope": scope.value,
        "query": query,
        "offset": offset,
        "limit": limit,
        "total": total,
        "items": ranked[offset : offset + limit],
    }


def episode_browse_document(
    client: PlexClient,
    parent_rating_key: str,
    query: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    if not re.fullmatch(r"\d{1,96}", parent_rating_key):
        raise ConfigurationError("Invalid Plex show key")
    rows = paged_library_rows(
        client,
        ["/library/metadata/" + parent_rating_key + "/allLeaves"],
        MAX_EPISODES_PER_SERIES,
        [MAX_SEARCH_REQUESTS],
    )
    title_query, season_number, episode_number = season_query(query)
    episodes: list[tuple[int, int, dict[str, Any]]] = []
    now = utc_now()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        season = finite_integer(raw.get("parentIndex"), -1)
        episode = finite_integer(raw.get("index"), -1)
        if season_number is not None and season != season_number:
            continue
        if episode_number is not None and episode != episode_number:
            continue
        item = browse_item(raw, now)
        if item is not None:
            episodes.append((season, episode, item))
    episodes.sort(key=lambda row: (row[0], row[1], row[2]["title"].casefold()))
    ranked = [item for _, _, item in episodes]
    if title_query:
        ranked = fzf_rank(ranked, title_query)
    total = len(ranked)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "episodes",
        "query": query,
        "offset": offset,
        "limit": limit,
        "total": total,
        "items": ranked[offset : offset + limit],
    }


def browse_document(
    client: PlexClient,
    config: dict[str, Any],
    kind: BrowseKind,
    query: str,
    offset: int,
    limit: int,
    parent_rating_key: str = "",
    search_scope: SearchScope = SearchScope.MOVIES,
) -> dict[str, Any]:
    if not isinstance(kind, BrowseKind):
        raise ConfigurationError(
            "Browse kind must be movies, shows, episodes, or search"
        )
    query = clean_text(query, 80)
    if offset < 0 or offset > 100000 or limit < 1 or limit > MAX_BROWSE_ITEMS:
        raise ConfigurationError("Invalid Plex browse page")
    if kind is BrowseKind.SEARCH:
        if not query:
            raise ConfigurationError("Search requires a query")
        return search_document(client, config, query, offset, limit, search_scope)
    if kind is BrowseKind.EPISODES and query:
        return episode_browse_document(client, parent_rating_key, query, offset, limit)
    base_parameters = {
        "X-Plex-Container-Start": offset,
        "X-Plex-Container-Size": limit,
    }
    documents: list[dict[str, Any]] = []
    if kind is BrowseKind.EPISODES:
        if not re.fullmatch(r"\d{1,96}", parent_rating_key):
            raise ConfigurationError("Invalid Plex show key")
        path = (
            "/library/metadata/"
            + parent_rating_key
            + "/allLeaves?"
            + urllib.parse.urlencode(base_parameters)
        )
        documents.append(client.request_json(path))
    else:
        media_type = "1" if kind is BrowseKind.MOVIES else "2"
        sections = (
            config["movieSectionIds"]
            if kind is BrowseKind.MOVIES
            else config["tvSectionIds"]
        )
        for section in sections:
            parameters = dict(base_parameters)
            parameters["type"] = media_type
            if query:
                parameters["query"] = query
                path = (
                    "/library/sections/"
                    + section
                    + "/search?"
                    + urllib.parse.urlencode(parameters)
                )
            else:
                parameters["sort"] = "titleSort:asc"
                path = (
                    "/library/sections/"
                    + section
                    + "/all?"
                    + urllib.parse.urlencode(parameters)
                )
            documents.append(client.request_json(path))
    raw_rows: list[Any] = []
    total = 0
    for document in documents:
        container = document.get("MediaContainer", {})
        rows = container.get("Metadata", []) if isinstance(container, dict) else []
        if not isinstance(rows, list) or len(rows) > MAX_BROWSE_ITEMS:
            raise ResponseError("Plex returned an invalid library page")
        raw_rows.extend(rows)
        total += max(len(rows), finite_integer(container.get("totalSize"), len(rows)))
    normalized = [
        value
        for value in (browse_item(row, utc_now()) for row in raw_rows)
        if value is not None
    ]
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for item in normalized:
        if item["ratingKey"] in seen:
            continue
        seen.add(item["ratingKey"])
        items.append(item)
        if len(items) >= limit:
            break
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": kind.value,
        "query": query,
        "offset": offset,
        "limit": limit,
        "total": total,
        "items": items,
    }
