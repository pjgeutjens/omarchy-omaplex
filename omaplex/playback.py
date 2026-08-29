from __future__ import annotations

import contextlib
import http.server
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from omaplex.client import HttpMethod, PlexClient
from omaplex.common import (
    ConfigurationError,
    PlexError,
    ResponseError,
    clean_text,
    finite_integer,
    wall_deadline,
)
from omaplex.config import (
    load_window_geometry,
    save_window_geometry,
    validate_window_geometry,
)
from omaplex.connection import client_from_saved
from omaplex.constants import MAX_PLAY_QUEUE_ITEMS, PLUGIN_ID
from omaplex.subtitles import subtitle_language
from omaplex.windowing import (
    ensure_hypr_fullscreen,
    read_hypr_geometry,
    restore_hypr_geometry,
)


class PlaybackMode(StrEnum):
    WINDOWED = "windowed"
    FULLSCREEN = "fullscreen"


class TimelineState(StrEnum):
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"


class WatchState(StrEnum):
    WATCHED = "watched"
    UNWATCHED = "unwatched"


@dataclass(frozen=True, slots=True)
class PlaybackItem:
    rating_key: str
    media_type: str
    part_key: str
    resume_seconds: int
    duration_ms: int
    subtitle_paths: tuple[str, ...]


def playback_item_from_metadata(item: Any) -> PlaybackItem:
    if not isinstance(item, dict):
        raise ResponseError("Plex returned no playable metadata")
    rating_key = str(item.get("ratingKey") or "")
    if not re.fullmatch(r"\d{1,96}", rating_key):
        raise ResponseError("Plex returned an invalid media identifier")
    media_type = str(item.get("type") or "")
    if media_type not in {"episode", "movie"}:
        raise ResponseError("Plex returned unsupported playable media")
    media = item.get("Media", [])
    if not isinstance(media, list) or not media or not isinstance(media[0], dict):
        raise ResponseError("Plex returned no playable media")
    parts = media[0].get("Part", [])
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
        raise ResponseError("Plex returned no playable part")
    part = parts[0]
    part_key = str(part.get("key") or "")
    if (
        len(part_key) > 512
        or not part_key.startswith("/library/parts/")
        or urllib.parse.urlsplit(part_key).scheme
    ):
        raise ResponseError("Plex returned an invalid media path")
    subtitle_paths: list[str] = []
    streams = part.get("Stream", [])
    if isinstance(streams, list):
        for stream in streams[:64]:
            if (
                not isinstance(stream, dict)
                or finite_integer(stream.get("streamType")) != 3
            ):
                continue
            subtitle_path = str(stream.get("key") or "")
            if not subtitle_path:
                continue
            parsed = urllib.parse.urlsplit(subtitle_path)
            if (
                len(subtitle_path) <= 512
                and parsed.scheme == ""
                and parsed.netloc == ""
                and parsed.path.startswith("/library/streams/")
                and parsed.query == ""
                and parsed.fragment == ""
            ):
                subtitle_paths.append(subtitle_path)
            if len(subtitle_paths) >= 16:
                break
    return PlaybackItem(
        rating_key=rating_key,
        media_type=media_type,
        part_key=part_key,
        resume_seconds=max(0, finite_integer(item.get("viewOffset")) // 1000),
        duration_ms=max(0, finite_integer(item.get("duration"))),
        subtitle_paths=tuple(subtitle_paths),
    )


def single_playback_item(client: PlexClient, rating_key: str) -> PlaybackItem:
    if not re.fullmatch(r"\d{1,96}", rating_key):
        raise ConfigurationError("Invalid Plex rating key")
    document = client.request_json("/library/metadata/" + rating_key)
    metadata = document.get("MediaContainer", {}).get("Metadata", [])
    if not isinstance(metadata, list) or not metadata:
        raise ResponseError("Plex returned no playable metadata")
    item = playback_item_from_metadata(metadata[0])
    if item.rating_key != rating_key:
        raise ResponseError("Plex returned the wrong playable item")
    return item


def playback_metadata(
    client: PlexClient, rating_key: str
) -> tuple[str, int, int, list[str]]:
    item = single_playback_item(client, rating_key)
    return (
        item.part_key,
        item.resume_seconds,
        item.duration_ms,
        list(item.subtitle_paths),
    )


def play_queue_path(machine_id: str, rating_key: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", machine_id):
        raise ConfigurationError("The Plex server identifier is unavailable")
    if not re.fullmatch(r"\d{1,96}", rating_key):
        raise ConfigurationError("Invalid Plex rating key")
    metadata_path = "/library/metadata/" + rating_key
    uri = "server://" + machine_id + "/com.plexapp.plugins.library" + metadata_path
    query = urllib.parse.urlencode(
        {
            "type": "video",
            "uri": uri,
            "key": metadata_path,
            "continuous": 1,
            "shuffle": 0,
            "repeat": 0,
        }
    )
    return "/playQueues?" + query


def queued_playback_items(
    client: PlexClient, machine_id: str, rating_key: str
) -> list[PlaybackItem]:
    document = client.request_json(
        play_queue_path(machine_id, rating_key), method=HttpMethod.POST
    )
    container = document.get("MediaContainer", {})
    metadata = container.get("Metadata", []) if isinstance(container, dict) else []
    if not isinstance(metadata, list) or not metadata:
        raise ResponseError("Plex returned an empty play queue")
    selected_item_id = str(container.get("playQueueSelectedItemID") or "")
    selected_index = -1
    for index, raw in enumerate(metadata[: MAX_PLAY_QUEUE_ITEMS * 2]):
        if not isinstance(raw, dict):
            continue
        if (
            selected_item_id
            and str(raw.get("playQueueItemID") or "") == selected_item_id
        ):
            selected_index = index
            break
        if selected_index < 0 and str(raw.get("ratingKey") or "") == rating_key:
            selected_index = index
    if selected_index < 0:
        raise ResponseError("Plex play queue omitted the selected episode")
    result: list[PlaybackItem] = []
    for raw in metadata[selected_index : selected_index + MAX_PLAY_QUEUE_ITEMS]:
        item = playback_item_from_metadata(raw)
        if not result and item.rating_key != rating_key:
            raise ResponseError("Plex play queue selected the wrong item")
        if result and item.media_type != "episode":
            break
        result.append(item)
    if not result:
        raise ResponseError("Plex returned an empty play queue")
    if result[0].media_type != "episode":
        return result[:1]
    return result


def playback_items(
    client: PlexClient,
    config: dict[str, Any],
    rating_key: str,
    auto_play_next: bool,
) -> list[PlaybackItem]:
    if auto_play_next:
        machine_id = str(config.get("machineIdentifier") or "")
        if machine_id:
            try:
                return queued_playback_items(client, machine_id, rating_key)
            except PlexError:
                pass
    return [single_playback_item(client, rating_key)]


def timeline_path(
    rating_key: str,
    position_ms: int,
    duration_ms: int,
    state: TimelineState,
) -> str:
    if not re.fullmatch(r"\d{1,96}", rating_key) or not isinstance(
        state, TimelineState
    ):
        raise ConfigurationError("Invalid Plex playback timeline")
    query = urllib.parse.urlencode(
        {
            "ratingKey": rating_key,
            "key": "/library/metadata/" + rating_key,
            "identifier": "com.plexapp.plugins.library",
            "state": state.value,
            "time": max(0, position_ms),
            "duration": max(0, duration_ms),
        }
    )
    return "/:/timeline?" + query


def report_timeline(
    client: PlexClient,
    rating_key: str,
    position_ms: int,
    duration_ms: int,
    state: TimelineState,
) -> None:
    client.request_empty(timeline_path(rating_key, position_ms, duration_ms, state))


def set_watch_state(client: PlexClient, rating_key: str, state: WatchState) -> None:
    if not re.fullmatch(r"\d{1,96}", rating_key):
        raise ConfigurationError("Invalid Plex rating key")
    if not isinstance(state, WatchState):
        raise ConfigurationError("Invalid Plex watch state")
    query = urllib.parse.urlencode(
        {
            "key": rating_key,
            "identifier": "com.plexapp.plugins.library",
        }
    )
    endpoint = "scrobble" if state is WatchState.WATCHED else "unscrobble"
    client.request_empty("/:/" + endpoint + "?" + query)


def mpv_status(socket_path: str) -> tuple[int, bool, int] | None:
    requests = (
        b'{"command":["get_property","time-pos"],"request_id":1}\n'
        b'{"command":["get_property","pause"],"request_id":2}\n'
        b'{"command":["get_property","playlist-pos"],"request_id":3}\n'
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.5)
            connection.connect(socket_path)
            connection.sendall(requests)
            payload = bytearray()
            while payload.count(b"\n") < 3 and len(payload) <= 8192:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                payload.extend(chunk)
    except (TimeoutError, FileNotFoundError, ConnectionRefusedError, OSError):
        return None
    if len(payload) > 8192:
        return None
    values: dict[int, Any] = {}
    for line in bytes(payload).splitlines()[:8]:
        try:
            document = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(document, dict) and document.get("error") == "success":
            values[finite_integer(document.get("request_id"), -1)] = document.get(
                "data"
            )
    try:
        seconds = float(values[1])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not (seconds >= 0 and seconds < 10**9):
        return None
    playlist_position = finite_integer(values.get(3), -1)
    if playlist_position < 0 or playlist_position >= MAX_PLAY_QUEUE_ITEMS:
        return None
    return int(seconds * 1000), values.get(2) is True, playlist_position


class ThreadedServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.request_slots = threading.BoundedSemaphore(4)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()


def proxy_handler(
    client: PlexClient, routes: dict[str, str]
) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_HEAD(self) -> None:
            self.proxy(HttpMethod.HEAD)

        def do_GET(self) -> None:
            self.proxy(HttpMethod.GET)

        def proxy(self, method: HttpMethod) -> None:
            expected_host = "127.0.0.1:" + str(self.server.server_address[1])
            if self.headers.get("Host", "") != expected_host or self.headers.get(
                "Origin"
            ):
                self.send_error(403)
                return
            parsed_path = urllib.parse.urlsplit(self.path)
            upstream_path = routes.get(parsed_path.path)
            if upstream_path is None or parsed_path.query:
                self.send_error(404)
                return
            range_header = self.headers.get("Range", "")
            if range_header and not re.fullmatch(
                r"bytes=\d*-\d*(?:,\d*-\d*)*", range_header
            ):
                self.send_error(416)
                return
            try:
                response = client.open(
                    upstream_path, method=method, range_header=range_header
                )
            except urllib.error.HTTPError as error:
                status = error.code
                error.close()
                self.send_error(status if 400 <= status <= 599 else 502)
                return
            except PlexError:
                self.send_error(502)
                return
            try:
                self.send_response(int(getattr(response, "status", response.getcode())))
                for name in [
                    "Content-Type",
                    "Content-Length",
                    "Content-Range",
                    "Accept-Ranges",
                    "Last-Modified",
                    "ETag",
                ]:
                    value = response.headers.get(name)
                    if value:
                        self.send_header(name, clean_text(value, 512))
                self.send_header("Connection", "close")
                self.end_headers()
                if method is HttpMethod.GET:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                response.close()

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def mpv_playlist_arguments(
    mode: PlaybackMode,
    entries: list[tuple[str, int, list[str]]],
    ipc_socket: str = "",
    window_geometry: dict[str, int] | None = None,
    subtitle_script: str = "",
    helper_command: str = "",
    rating_keys: list[str] | None = None,
    subtitle_search_language: str = "en",
    subtitle_output_directory: str = "",
) -> list[str]:
    if not isinstance(mode, PlaybackMode):
        raise ConfigurationError("Playback mode must be windowed or fullscreen")
    if not entries or len(entries) > MAX_PLAY_QUEUE_ITEMS:
        raise ConfigurationError("Invalid Plex playback queue")
    arguments = [
        "mpv",
        "--no-config",
        "--no-ytdl",
        "--really-quiet",
        "--keep-open=no",
        "--force-window=yes",
        "--osc=yes",
        "--input-default-bindings=yes",
        "--osd-level=1",
        "--title=Omaplex",
    ]
    if mode is PlaybackMode.FULLSCREEN:
        arguments.extend(
            [
                "--wayland-app-id=" + PLUGIN_ID + ".player",
                "--fullscreen",
            ]
        )
    elif window_geometry is not None:
        geometry = validate_window_geometry(window_geometry)
        arguments.append(
            "--geometry=" + str(geometry["width"]) + "x" + str(geometry["height"])
        )
    else:
        arguments.extend(["--autofit=960x540", "--geometry=50%:50%"])
    if ipc_socket:
        if not ipc_socket.startswith("/tmp/") or len(ipc_socket) > 512:
            raise ConfigurationError("Invalid player IPC path")
        arguments.append("--input-ipc-server=" + ipc_socket)
    subtitle_options = [
        subtitle_script,
        helper_command,
        ":".join(rating_keys or []),
        subtitle_output_directory,
    ]
    if any(subtitle_options):
        if not all(subtitle_options):
            raise ConfigurationError("Incomplete subtitle search configuration")
        if (
            not subtitle_script.startswith("/")
            or not helper_command.startswith("/")
            or len(subtitle_script) > 512
            or len(helper_command) > 512
            or not subtitle_output_directory.startswith("/tmp/omaplex-player-")
            or len(subtitle_output_directory) > 512
        ):
            raise ConfigurationError("Invalid subtitle search configuration")
        if len(rating_keys or []) != len(entries) or any(
            not re.fullmatch(r"\d{1,96}", key) for key in (rating_keys or [])
        ):
            raise ConfigurationError("Invalid subtitle search media identifiers")
        language = subtitle_language(subtitle_search_language)
        arguments.extend(
            [
                "--script=" + subtitle_script,
                "--script-opt=omaplex_subtitles-helper=" + helper_command,
                "--script-opt=omaplex_subtitles-rating_keys="
                + ":".join(rating_keys or []),
                "--script-opt=omaplex_subtitles-language=" + language,
                "--script-opt=omaplex_subtitles-output_directory="
                + subtitle_output_directory,
            ]
        )
    for url, resume_seconds, subtitle_urls in entries:
        if not url.startswith("http://127.0.0.1:") or len(url) > 1024:
            raise ConfigurationError("Invalid local playback URL")
        arguments.append("--{")
        if resume_seconds > 0:
            arguments.append("--start=" + str(resume_seconds))
        for subtitle_url in subtitle_urls[:16]:
            if (
                not subtitle_url.startswith("http://127.0.0.1:")
                or len(subtitle_url) > 1024
            ):
                raise ConfigurationError("Invalid local subtitle URL")
            arguments.append("--sub-file=" + subtitle_url)
        arguments.extend([url, "--}"])
    return arguments


def mpv_arguments(
    mode: PlaybackMode,
    url: str,
    resume_seconds: int,
    subtitle_urls: list[str] | None = None,
    ipc_socket: str = "",
    window_geometry: dict[str, int] | None = None,
) -> list[str]:
    return mpv_playlist_arguments(
        mode,
        [(url, resume_seconds, subtitle_urls or [])],
        ipc_socket,
        window_geometry,
    )


def finish_playback_item(
    client: PlexClient, item: PlaybackItem, position_ms: int
) -> None:
    with contextlib.suppress(PlexError):
        report_timeline(
            client,
            item.rating_key,
            position_ms,
            item.duration_ms,
            TimelineState.STOPPED,
        )
    if item.duration_ms > 0 and position_ms >= int(item.duration_ms * 0.9):
        with contextlib.suppress(PlexError):
            set_watch_state(client, item.rating_key, WatchState.WATCHED)


def play(
    rating_key: str,
    mode: PlaybackMode,
    auto_play_next: bool = False,
    subtitle_search_language: str = "en",
) -> int:
    if not isinstance(mode, PlaybackMode):
        raise ConfigurationError("Playback mode must be windowed or fullscreen")
    language = subtitle_language(subtitle_search_language)
    with wall_deadline(20, "Plex playback setup exceeded twenty seconds"):
        client, config = client_from_saved()
        items = playback_items(client, config, rating_key, auto_play_next)
    nonce = secrets.token_urlsafe(24)
    routes: dict[str, str] = {}
    public_items: list[tuple[str, list[str]]] = []
    for item_index, item in enumerate(items):
        public_path = "/stream/" + nonce + "/" + str(item_index)
        routes[public_path] = item.part_key
        subtitle_public_paths: list[str] = []
        for subtitle_index, subtitle_path in enumerate(item.subtitle_paths):
            subtitle_public_path = (
                "/subtitle/" + nonce + "/" + str(item_index) + "/" + str(subtitle_index)
            )
            routes[subtitle_public_path] = subtitle_path
            subtitle_public_paths.append(subtitle_public_path)
        public_items.append((public_path, subtitle_public_paths))
    server = ThreadedServer(("127.0.0.1", 0), proxy_handler(client, routes))
    port = int(server.server_address[1])
    thread = threading.Thread(
        target=server.serve_forever, name="plex-stream-proxy", daemon=True
    )
    thread.start()
    local_origin = "http://127.0.0.1:" + str(port)
    entries = [
        (
            local_origin + public_path,
            item.resume_seconds,
            [local_origin + path for path in subtitle_paths],
        )
        for item, (public_path, subtitle_paths) in zip(items, public_items, strict=True)
    ]
    current_index = 0
    last_position_ms = items[0].resume_seconds * 1000
    saved_geometry: dict[str, int] | None = None
    if mode is PlaybackMode.WINDOWED:
        with contextlib.suppress(PlexError, OSError):
            saved_geometry = load_window_geometry()
    try:
        with tempfile.TemporaryDirectory(
            prefix="omaplex-player-", dir="/tmp"
        ) as ipc_directory:
            os.chmod(ipc_directory, 0o700)
            ipc_socket = str(Path(ipc_directory) / "mpv.sock")
            plugin_root = Path(__file__).resolve().parents[1]
            subtitle_script = str(plugin_root / "assets" / "omaplex_subtitles.lua")
            helper_command = str(plugin_root / "bin" / "omaplex")
            player = subprocess.Popen(
                mpv_playlist_arguments(
                    mode,
                    entries,
                    ipc_socket,
                    saved_geometry,
                    subtitle_script,
                    helper_command,
                    [item.rating_key for item in items],
                    language,
                    ipc_directory,
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if mode is PlaybackMode.FULLSCREEN:
                ensure_hypr_fullscreen(player.pid)
            elif saved_geometry is not None:
                restore_hypr_geometry(player.pid, saved_geometry)
            next_report = 0.0
            next_geometry_check = 0.0
            geometry_candidate: dict[str, int] | None = None
            geometry_stable_checks = 0
            latest_geometry: dict[str, int] | None = None
            return_code = player.poll()
            while return_code is None:
                now = time.monotonic()
                status = mpv_status(ipc_socket)
                if status is not None:
                    position_ms, paused, playlist_position = status
                    if playlist_position < len(items):
                        if playlist_position != current_index:
                            finish_playback_item(
                                client, items[current_index], last_position_ms
                            )
                            current_index = playlist_position
                            next_report = 0.0
                        last_position_ms = position_ms
                        if now >= next_report:
                            with contextlib.suppress(PlexError):
                                report_timeline(
                                    client,
                                    items[current_index].rating_key,
                                    last_position_ms,
                                    items[current_index].duration_ms,
                                    TimelineState.PAUSED
                                    if paused
                                    else TimelineState.PLAYING,
                                )
                            next_report = now + 10
                if mode is PlaybackMode.WINDOWED and now >= next_geometry_check:
                    captured_geometry = read_hypr_geometry(player.pid)
                    if captured_geometry is not None:
                        latest_geometry = captured_geometry
                        if captured_geometry == geometry_candidate:
                            geometry_stable_checks += 1
                        else:
                            geometry_candidate = captured_geometry
                            geometry_stable_checks = 1
                        if (
                            geometry_stable_checks >= 2
                            and captured_geometry != saved_geometry
                        ):
                            try:
                                save_window_geometry(captured_geometry)
                                saved_geometry = captured_geometry
                            except (PlexError, OSError):
                                pass
                    next_geometry_check = now + 2
                time.sleep(0.5)
                return_code = player.poll()
            if (
                mode is PlaybackMode.WINDOWED
                and latest_geometry is not None
                and latest_geometry != saved_geometry
            ):
                with contextlib.suppress(PlexError, OSError):
                    save_window_geometry(latest_geometry)
            finish_playback_item(client, items[current_index], last_position_ms)
    except FileNotFoundError as error:
        raise ConfigurationError("mpv is not installed") from error
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    if return_code != 0:
        raise ResponseError("mpv could not play this Plex item")
    return return_code


def plex_web_url(config: dict[str, Any], rating_key: str = "") -> str:
    if rating_key == "":
        return str(config["server"]) + "/web/index.html"
    if not re.fullmatch(r"\d{1,96}", rating_key):
        raise ConfigurationError("Invalid Plex rating key")
    if not config.get("machineIdentifier"):
        raise ConfigurationError("The Plex server identifier is unavailable")
    key = urllib.parse.quote("/library/metadata/" + rating_key, safe="")
    machine = urllib.parse.quote(str(config["machineIdentifier"]), safe="")
    return (
        config["server"] + "/web/index.html#!/server/" + machine + "/details?key=" + key
    )
