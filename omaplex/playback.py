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
from omaplex.constants import PLUGIN_ID
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


def playback_metadata(
    client: PlexClient, rating_key: str
) -> tuple[str, int, int, list[str]]:
    if not re.fullmatch(r"\d{1,96}", rating_key):
        raise ConfigurationError("Invalid Plex rating key")
    document = client.request_json("/library/metadata/" + rating_key)
    metadata_value = document.get("MediaContainer", {}).get("Metadata", [])
    if (
        not isinstance(metadata_value, list)
        or not metadata_value
        or not isinstance(metadata_value[0], dict)
    ):
        raise ResponseError("Plex returned no playable metadata")
    item = metadata_value[0]
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
    resume_seconds = max(0, finite_integer(item.get("viewOffset")) // 1000)
    duration_ms = max(0, finite_integer(item.get("duration")))
    return part_key, resume_seconds, duration_ms, subtitle_paths


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


def set_watch_state(
    client: PlexClient, rating_key: str, state: WatchState
) -> None:
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


def mpv_status(socket_path: str) -> tuple[int, bool] | None:
    requests = (
        b'{"command":["get_property","time-pos"],"request_id":1}\n'
        b'{"command":["get_property","pause"],"request_id":2}\n'
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.5)
            connection.connect(socket_path)
            connection.sendall(requests)
            payload = bytearray()
            while payload.count(b"\n") < 2 and len(payload) <= 8192:
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
    return int(seconds * 1000), values.get(2) is True


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


def mpv_arguments(
    mode: PlaybackMode,
    url: str,
    resume_seconds: int,
    subtitle_urls: list[str] | None = None,
    ipc_socket: str = "",
    window_geometry: dict[str, int] | None = None,
) -> list[str]:
    if not isinstance(mode, PlaybackMode):
        raise ConfigurationError("Playback mode must be windowed or fullscreen")
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
    if resume_seconds > 0:
        arguments.append("--start=" + str(resume_seconds))
    if ipc_socket:
        if not ipc_socket.startswith("/tmp/") or len(ipc_socket) > 512:
            raise ConfigurationError("Invalid player IPC path")
        arguments.append("--input-ipc-server=" + ipc_socket)
    for subtitle_url in (subtitle_urls or [])[:16]:
        if not subtitle_url.startswith("http://127.0.0.1:") or len(subtitle_url) > 1024:
            raise ConfigurationError("Invalid local subtitle URL")
        arguments.append("--sub-file=" + subtitle_url)
    arguments.append(url)
    return arguments


def play(rating_key: str, mode: PlaybackMode) -> int:
    if not isinstance(mode, PlaybackMode):
        raise ConfigurationError("Playback mode must be windowed or fullscreen")
    with wall_deadline(20, "Plex playback setup exceeded twenty seconds"):
        client, _ = client_from_saved()
        part_key, resume_seconds, duration_ms, subtitle_paths = playback_metadata(
            client, rating_key
        )
    nonce = secrets.token_urlsafe(24)
    public_path = "/stream/" + nonce
    routes = {public_path: part_key}
    subtitle_public_paths: list[str] = []
    for index, subtitle_path in enumerate(subtitle_paths):
        subtitle_public_path = "/subtitle/" + nonce + "/" + str(index)
        routes[subtitle_public_path] = subtitle_path
        subtitle_public_paths.append(subtitle_public_path)
    server = ThreadedServer(("127.0.0.1", 0), proxy_handler(client, routes))
    port = int(server.server_address[1])
    thread = threading.Thread(
        target=server.serve_forever, name="plex-stream-proxy", daemon=True
    )
    thread.start()
    url = "http://127.0.0.1:" + str(port) + public_path
    subtitle_urls = [
        "http://127.0.0.1:" + str(port) + path for path in subtitle_public_paths
    ]
    last_position_ms = resume_seconds * 1000
    saved_geometry: dict[str, int] | None = None
    if mode is PlaybackMode.WINDOWED:
        with contextlib.suppress(PlexError, OSError):
            saved_geometry = load_window_geometry()
    try:
        with tempfile.TemporaryDirectory(
            prefix="plex-recent-player-", dir="/tmp"
        ) as ipc_directory:
            os.chmod(ipc_directory, 0o700)
            ipc_socket = str(Path(ipc_directory) / "mpv.sock")
            player = subprocess.Popen(
                mpv_arguments(
                    mode,
                    url,
                    resume_seconds,
                    subtitle_urls,
                    ipc_socket,
                    saved_geometry,
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
                if now >= next_report:
                    status = mpv_status(ipc_socket)
                    if status is not None:
                        last_position_ms, paused = status
                        with contextlib.suppress(PlexError):
                            report_timeline(
                                client,
                                rating_key,
                                last_position_ms,
                                duration_ms,
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
            with contextlib.suppress(PlexError):
                report_timeline(
                    client,
                    rating_key,
                    last_position_ms,
                    duration_ms,
                    TimelineState.STOPPED,
                )
            if duration_ms > 0 and last_position_ms >= int(duration_ms * 0.9):
                with contextlib.suppress(PlexError):
                    set_watch_state(client, rating_key, WatchState.WATCHED)
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
