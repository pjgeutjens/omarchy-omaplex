from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from omaplex.activity import cache_home, load_snapshot, recent_snapshot, save_snapshot
from omaplex.client import HttpMethod, PlexClient, RefuseRedirects, validate_origin
from omaplex.common import (
    AuthenticationError,
    ConfigurationError,
    PlexError,
    ResponseError,
    clean_text,
    finite_integer,
    launch_detached,
    unlink_private_file,
)
from omaplex.config import (
    AUTH_MODE_PLEX,
    AccountTokenStore,
    DeviceKeyStore,
    PendingAccountTokenStore,
    PendingDeviceKeyStore,
    SecretStore,
    config_home,
    load_config,
    load_pending_auth,
    pending_auth_path,
    save_config,
    save_pending_auth,
    valid_token,
    validate_config,
)
from omaplex.constants import (
    CLIENT_PRODUCT,
    CLIENT_VERSION,
    MAX_API_BYTES,
    MAX_CACHE_BYTES,
    MAX_CONFIG_BYTES,
    MAX_PLEX_SERVERS,
    MAX_SERVER_CONNECTIONS,
    REQUEST_TIMEOUT,
    SCHEMA_VERSION,
)

PLEX_CLIENTS_ORIGIN = "https://clients.plex.tv"
PLEX_AUTH_URL = "https://app.plex.tv/auth#?"
PENDING_AUTH_LIFETIME = 15 * 60


def plex_headers(client_identifier: str, token: str = "") -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "X-Plex-Product": CLIENT_PRODUCT,
        "X-Plex-Version": CLIENT_VERSION,
        "X-Plex-Client-Identifier": client_identifier,
        "X-Plex-Device": "Linux",
        "X-Plex-Platform": "Linux",
    }
    if token:
        headers["X-Plex-Token"] = token
    return headers


class PlexCloudClient:
    def __init__(self, opener: Any | None = None) -> None:
        self.opener = opener or urllib.request.build_opener(RefuseRedirects())

    def request_json(
        self,
        path: str,
        client_identifier: str,
        *,
        token: str = "",
        method: HttpMethod = HttpMethod.GET,
        body: dict[str, Any] | None = None,
    ) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            raise ResponseError("Refusing an invalid Plex account path")
        payload = None
        headers = plex_headers(client_identifier, token)
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            PLEX_CLIENTS_ORIGIN + path,
            data=payload,
            headers=headers,
            method=method.value,
        )
        try:
            response = self.opener.open(request, timeout=REQUEST_TIMEOUT)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status in {401, 403, 498}:
                raise AuthenticationError("Plex sign-in was rejected") from error
            raise ResponseError("Plex sign-in returned HTTP " + str(status)) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ResponseError("Plex sign-in is unavailable") from error
        try:
            length = finite_integer(response.headers.get("Content-Length"), -1)
            if length > MAX_API_BYTES:
                raise ResponseError("Plex sign-in response exceeded the size limit")
            raw = response.read(MAX_API_BYTES + 1)
        finally:
            response.close()
        if len(raw) > MAX_API_BYTES:
            raise ResponseError("Plex sign-in response exceeded the size limit")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResponseError("Plex sign-in returned malformed JSON") from error


def validate_pending_auth(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise ConfigurationError("No Plex sign-in is pending")
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "clientIdentifier": clean_text(value.get("clientIdentifier"), 128),
        "pinId": finite_integer(value.get("pinId"), -1),
        "pinCode": clean_text(value.get("pinCode"), 256),
        "createdAt": finite_integer(value.get("createdAt"), -1),
    }
    if not re.fullmatch(r"[A-Za-z0-9._-]{16,128}", result["clientIdentifier"]):
        raise ConfigurationError("The pending Plex client identifier is invalid")
    if result["pinId"] < 1 or not re.fullmatch(
        r"[A-Za-z0-9_-]{4,256}", result["pinCode"]
    ):
        raise ConfigurationError("The pending Plex sign-in is invalid")
    if abs(int(time.time()) - result["createdAt"]) > PENDING_AUTH_LIFETIME:
        raise ConfigurationError("Plex sign-in expired; please try again")
    return result


def clear_pending_auth() -> None:
    PendingAccountTokenStore().clear()
    PendingDeviceKeyStore().clear()
    with contextlib.suppress(FileNotFoundError):
        unlink_private_file(pending_auth_path())


def begin_sign_in(
    cloud: PlexCloudClient | None = None,
    *,
    open_browser: bool = True,
) -> dict[str, Any]:
    clear_pending_auth()
    client_identifier = "omaplex-" + uuid.uuid4().hex
    client = cloud or PlexCloudClient()
    document = client.request_json(
        "/api/v2/pins",
        client_identifier,
        method=HttpMethod.POST,
        body={"strong": True},
    )
    if not isinstance(document, dict):
        raise ResponseError("Plex returned an invalid sign-in request")
    pending = validate_pending_auth(
        {
            "schemaVersion": SCHEMA_VERSION,
            "clientIdentifier": client_identifier,
            "pinId": document.get("id"),
            "pinCode": document.get("code"),
            "createdAt": int(time.time()),
        }
    )
    try:
        save_pending_auth(pending)
        query = urllib.parse.urlencode(
            {
                "clientID": client_identifier,
                "code": pending["pinCode"],
                "context[device][product]": CLIENT_PRODUCT,
            }
        )
        if open_browser:
            launch_detached(["xdg-open", PLEX_AUTH_URL + query])
    except Exception:
        clear_pending_auth()
        raise
    return {"state": "pending", "browserOpened": open_browser}


def truthy(value: Any) -> bool:
    return value is True or value in {1, "1", "true"}


def resource_servers(document: Any) -> list[dict[str, Any]]:
    resources = document.get("resources") if isinstance(document, dict) else document
    if not isinstance(resources, list) or len(resources) > MAX_PLEX_SERVERS * 4:
        raise ResponseError("Plex returned an invalid server list")
    servers: list[dict[str, Any]] = []
    for raw in resources:
        if not isinstance(raw, dict):
            continue
        provides = [item.strip() for item in str(raw.get("provides") or "").split(",")]
        if "server" not in provides:
            continue
        machine_id = clean_text(raw.get("clientIdentifier"), 128)
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", machine_id):
            continue
        name = clean_text(raw.get("name"), 128) or "Plex Media Server"
        token = str(raw.get("accessToken") or "")
        try:
            token = valid_token(token)
        except ConfigurationError:
            continue
        raw_connections = raw.get("connections")
        if (
            not isinstance(raw_connections, list)
            or len(raw_connections) > MAX_SERVER_CONNECTIONS
        ):
            continue
        connections: list[dict[str, Any]] = []
        for raw_connection in raw_connections:
            if not isinstance(raw_connection, dict):
                continue
            try:
                uri = validate_origin(raw_connection.get("uri"))
            except ConfigurationError:
                continue
            connections.append(
                {
                    "uri": uri,
                    "local": truthy(raw_connection.get("local")),
                    "relay": truthy(raw_connection.get("relay")),
                }
            )
        if not connections:
            continue
        servers.append(
            {
                "machineIdentifier": machine_id,
                "name": name,
                "owned": truthy(raw.get("owned")),
                "presence": truthy(raw.get("presence")),
                "accessToken": token,
                "connections": connections,
            }
        )
        if len(servers) > MAX_PLEX_SERVERS:
            raise ResponseError("Plex returned too many media servers")
    return servers


def fetch_resources(
    cloud: PlexCloudClient,
    client_identifier: str,
    account_token: str,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {"includeHttps": "1", "includeRelay": "1", "includeIPv6": "1"}
    )
    document = cloud.request_json(
        "/api/v2/resources?" + query,
        client_identifier,
        token=account_token,
    )
    return resource_servers(document)


def public_server_list(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "machineIdentifier": item["machineIdentifier"],
            "name": item["name"],
            "owned": item["owned"],
            "available": item["presence"],
        }
        for item in servers
    ]


def poll_sign_in(cloud: PlexCloudClient | None = None) -> dict[str, Any]:
    pending = validate_pending_auth(load_pending_auth())
    account_store = PendingAccountTokenStore()
    account_token = account_store.lookup()
    client = cloud or PlexCloudClient()
    if not account_token:
        document = client.request_json(
            "/api/v2/pins/" + str(pending["pinId"]),
            pending["clientIdentifier"],
        )
        if not isinstance(document, dict):
            raise ResponseError("Plex returned an invalid sign-in response")
        raw_token = document.get("authToken") or document.get("auth_token")
        if not raw_token:
            return {"state": "pending"}
        account_token = valid_token(raw_token)
        account_store.store(account_token)
    servers = fetch_resources(client, pending["clientIdentifier"], account_token)
    if not servers:
        raise ConfigurationError("No Plex Media Server is available for this account")
    return {"state": "servers", "servers": public_server_list(servers)}


def connection_priority(connection: dict[str, Any]) -> tuple[int, int, int]:
    return (
        1 if connection["relay"] else 0,
        0 if str(connection["uri"]).startswith("https://") else 1,
        0 if connection["local"] else 1,
    )


def connect_to_resource(
    resource: dict[str, Any], client_identifier: str
) -> tuple[PlexClient, list[dict[str, str]], str]:
    last_error: PlexError | None = None
    for connection in sorted(resource["connections"], key=connection_priority):
        client = PlexClient(
            connection["uri"],
            resource["accessToken"],
            client_identifier=client_identifier,
        )
        try:
            libraries, machine_id, server_name = client.discover()
        except PlexError as error:
            last_error = error
            continue
        if machine_id and machine_id != resource["machineIdentifier"]:
            last_error = AuthenticationError("Plex returned a different media server")
            continue
        return client, libraries, server_name or resource["name"]
    if last_error:
        raise ResponseError("The selected Plex server is unavailable") from last_error
    raise ResponseError("The selected Plex server has no usable connection")


def restore_file(path: Path, value: dict[str, Any] | None, maximum: int) -> None:
    from omaplex.common import atomic_json_write

    if value is None:
        with contextlib.suppress(FileNotFoundError):
            unlink_private_file(path)
    else:
        atomic_json_write(path, value, maximum)


def restore_secret(store: SecretStore, value: str | None) -> None:
    if value:
        store.store(value)
    else:
        store.clear()


def complete_sign_in(
    machine_identifier: str,
    cloud: PlexCloudClient | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", machine_identifier):
        raise ConfigurationError("Select a valid Plex Media Server")
    pending = validate_pending_auth(load_pending_auth())
    pending_account_token = PendingAccountTokenStore().lookup()
    if not pending_account_token:
        raise ConfigurationError("Complete Plex sign-in before selecting a server")
    client = cloud or PlexCloudClient()
    servers = fetch_resources(
        client, pending["clientIdentifier"], pending_account_token
    )
    resource = next(
        (item for item in servers if item["machineIdentifier"] == machine_identifier),
        None,
    )
    if resource is None:
        raise ConfigurationError(
            "The selected Plex Media Server is no longer available"
        )
    plex, libraries, server_name = connect_to_resource(
        resource, pending["clientIdentifier"]
    )
    movies = [item["id"] for item in libraries if item["type"] == "movie"]
    shows = [item["id"] for item in libraries if item["type"] == "show"]
    config = validate_config(
        {
            "schemaVersion": SCHEMA_VERSION,
            "server": plex.server,
            "movieSectionIds": movies,
            "tvSectionIds": shows,
            "machineIdentifier": machine_identifier,
            "serverName": server_name,
            "authMode": AUTH_MODE_PLEX,
            "clientIdentifier": pending["clientIdentifier"],
        }
    )
    snapshot = recent_snapshot(plex, config)
    token_store = SecretStore()
    account_store = AccountTokenStore()
    key_store = DeviceKeyStore()
    old_token = token_store.lookup()
    old_account_token = account_store.lookup()
    old_key = key_store.lookup()
    old_config = load_config()
    old_snapshot = load_snapshot()
    try:
        token_store.store(resource["accessToken"])
        account_store.store(pending_account_token)
        key_store.clear()
        clear_pending_auth()
        save_config(config)
        save_snapshot(snapshot)
    except Exception:
        with contextlib.suppress(PlexError):
            restore_secret(token_store, old_token)
        with contextlib.suppress(PlexError):
            restore_secret(account_store, old_account_token)
        with contextlib.suppress(PlexError):
            restore_secret(key_store, old_key)
        with contextlib.suppress(PlexError, OSError):
            restore_file(config_home() / "config.json", old_config, MAX_CONFIG_BYTES)
        with contextlib.suppress(PlexError, OSError):
            restore_file(cache_home() / "recent.json", old_snapshot, MAX_CACHE_BYTES)
        raise
    return snapshot, config, libraries


def token_for_config(config: dict[str, Any]) -> str:
    token_store = SecretStore()
    server_token = token_store.lookup()
    if not server_token:
        message = (
            "Plex sign-in credentials are incomplete"
            if config.get("authMode") == AUTH_MODE_PLEX
            else "No Plex token was found in the desktop secret service"
        )
        raise ConfigurationError(message)
    return server_token
