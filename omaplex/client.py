from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from enum import StrEnum
from typing import Any

from omaplex.common import (
    AuthenticationError,
    ConfigurationError,
    ResponseError,
    clean_text,
    finite_integer,
)
from omaplex.constants import (
    CLIENT_ID,
    MAX_API_BYTES,
    MAX_SECTIONS,
    MAX_TOKEN_BYTES,
    REQUEST_TIMEOUT,
)


class HttpMethod(StrEnum):
    GET = "GET"
    HEAD = "HEAD"
    POST = "POST"
    PUT = "PUT"


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        raise ResponseError("Plex tried to redirect an authenticated request")


class PlexClient:
    def __init__(
        self,
        server: str,
        token: str,
        opener: Any | None = None,
        client_identifier: str = CLIENT_ID,
    ) -> None:
        self.server = validate_origin(server)
        if not token or len(token) > MAX_TOKEN_BYTES:
            raise ConfigurationError("The saved Plex token is missing or invalid")
        self.token = token
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,128}", client_identifier):
            raise ConfigurationError("The Plex client identifier is invalid")
        self.client_identifier = client_identifier
        self.opener = opener or urllib.request.build_opener(RefuseRedirects())
        parsed = urllib.parse.urlsplit(self.server)
        self.origin = (
            parsed.scheme.lower(),
            (parsed.hostname or "").lower(),
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )

    def url(self, path: str) -> str:
        if not path.startswith("/") or path.startswith("//"):
            raise ResponseError("Refusing an invalid Plex path")
        url = self.server + path
        parsed = urllib.parse.urlsplit(url)
        candidate = (
            parsed.scheme.lower(),
            (parsed.hostname or "").lower(),
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
        if candidate != self.origin:
            raise ResponseError("Refusing a Plex request outside the configured origin")
        return url

    def open(
        self,
        path: str,
        *,
        method: HttpMethod = HttpMethod.GET,
        range_header: str = "",
    ) -> Any:
        if not isinstance(method, HttpMethod):
            raise ConfigurationError("Invalid Plex HTTP method")
        headers = {
            "Accept": "application/json",
            "X-Plex-Token": self.token,
            "X-Plex-Client-Identifier": self.client_identifier,
            "User-Agent": CLIENT_ID + "/0.2",
        }
        if range_header:
            headers["Range"] = range_header
        request = urllib.request.Request(
            self.url(path), headers=headers, method=method.value
        )
        try:
            return self.opener.open(request, timeout=REQUEST_TIMEOUT)
        except urllib.error.HTTPError as error:
            if error.code == 401:
                error.close()
                raise AuthenticationError(
                    "Plex rejected the configured token"
                ) from error
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ResponseError("Plex is unavailable") from error

    def request_json(
        self, path: str, *, method: HttpMethod = HttpMethod.GET
    ) -> dict[str, Any]:
        try:
            response = self.open(path, method=method)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise ResponseError("Plex returned HTTP " + str(status)) from error
        try:
            length = finite_integer(response.headers.get("Content-Length"), -1)
            if length > MAX_API_BYTES:
                raise ResponseError("Plex response exceeded the size limit")
            payload = response.read(MAX_API_BYTES + 1)
        finally:
            response.close()
        if len(payload) > MAX_API_BYTES:
            raise ResponseError("Plex response exceeded the size limit")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResponseError("Plex returned malformed JSON") from error
        if not isinstance(value, dict):
            raise ResponseError("Plex returned an invalid document")
        return value

    def request_empty(self, path: str, *, method: HttpMethod = HttpMethod.GET) -> None:
        try:
            response = self.open(path, method=method)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise ResponseError("Plex returned HTTP " + str(status)) from error
        response.close()

    def fetch_server_name(self) -> str:
        container = self.request_json("/").get("MediaContainer", {})
        if not isinstance(container, dict):
            raise ResponseError("Plex returned an invalid server identity")
        return clean_text(container.get("friendlyName"), 128)

    def discover(self) -> tuple[list[dict[str, str]], str, str]:
        document = self.request_json("/library/sections")
        directories = document.get("MediaContainer", {}).get("Directory", [])
        if not isinstance(directories, list) or len(directories) > MAX_SECTIONS:
            raise ResponseError("Plex returned an invalid library list")
        libraries: list[dict[str, str]] = []
        for raw in directories:
            if not isinstance(raw, dict) or raw.get("type") not in {"movie", "show"}:
                continue
            key = str(raw.get("key") or "")
            if not re.fullmatch(r"\d{1,12}", key):
                continue
            libraries.append(
                {
                    "id": key,
                    "type": str(raw["type"]),
                    "title": clean_text(raw.get("title"), 128)
                    or ("Movies" if raw["type"] == "movie" else "TV Shows"),
                }
            )
        identity = self.request_json("/identity").get("MediaContainer", {})
        machine_id = (
            clean_text(identity.get("machineIdentifier"), 128)
            if isinstance(identity, dict)
            else ""
        )
        if machine_id and not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", machine_id):
            machine_id = ""
        return libraries, machine_id, self.fetch_server_name()


def validate_origin(value: Any) -> str:
    origin = clean_text(value, 512).rstrip("/")
    parsed = urllib.parse.urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("The Plex server must be an HTTP or HTTPS origin")
    if (
        parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            "The Plex server must not contain credentials, a path, query, or fragment"
        )
    try:
        _ = parsed.port
    except ValueError as error:
        raise ConfigurationError("The Plex server has an invalid port") from error
    return origin
