from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from omaplex import authentication
from omaplex.authentication import (
    begin_sign_in,
    complete_sign_in,
    poll_sign_in,
    resource_servers,
    token_for_config,
)
from omaplex.common import ConfigurationError
from omaplex.config import load_config, load_pending_auth
from tests.support import FakeStore


class FakeCloud:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def request_json(self, path, client_identifier, **kwargs):
        self.requests.append((path, client_identifier, kwargs))
        for needle, response in self.responses.items():
            if needle in path:
                return response
        raise AssertionError("unexpected Plex cloud path: " + path)


class FakeResponse:
    def __init__(self):
        self.status = 201
        self.headers = {"Content-Length": "22"}

    def read(self, _maximum):
        return b'{"id":44,"code":"ok"}'

    def close(self):
        return None


class FakeOpener:
    def __init__(self):
        self.request = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return FakeResponse()


def resource_document():
    return [
        {
            "name": "Living Room Plex",
            "provides": "server",
            "clientIdentifier": "server-machine-id",
            "owned": True,
            "presence": True,
            "accessToken": "serverToken_1234",
            "connections": [
                {
                    "uri": "http://192.0.2.4:32400",
                    "local": True,
                    "relay": False,
                }
            ],
        }
    ]


def pending_document():
    return {
        "schemaVersion": 1,
        "clientIdentifier": "omaplex-1234567890abcdef",
        "pinId": 44,
        "pinCode": "strong-code-1234",
        "createdAt": int(time.time()),
    }


class PlexAuthenticationTests(unittest.TestCase):
    def test_cloud_client_serializes_pin_request_body(self):
        opener = FakeOpener()
        document = authentication.PlexCloudClient(opener).request_json(
            "/api/v2/pins",
            "omaplex-1234567890abcdef",
            method=authentication.HttpMethod.POST,
            body={"strong": True},
        )
        self.assertEqual(document, {"id": 44, "code": "ok"})
        self.assertIsInstance(opener.request, urllib.request.Request)
        self.assertEqual(opener.request.data, b'{"strong":true}')

    def test_begin_sign_in_uses_traditional_pin_without_replacing_credentials(self):
        pending_account = FakeStore()
        pending_key = FakeStore("old pending key")
        stable_token = FakeStore("existingToken_1234")
        cloud = FakeCloud({"/api/v2/pins": {"id": 44, "code": "strong-code-1234"}})
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(
                os.environ, {"XDG_CONFIG_HOME": str(Path(directory) / "config")}
            ),
            mock.patch.object(
                authentication,
                "PendingAccountTokenStore",
                return_value=pending_account,
            ),
            mock.patch.object(
                authentication, "PendingDeviceKeyStore", return_value=pending_key
            ),
            mock.patch.object(authentication, "SecretStore", return_value=stable_token),
            mock.patch.object(authentication, "launch_detached") as launch,
        ):
            result = begin_sign_in(cloud)
            pending = load_pending_auth()
        self.assertEqual(result, {"state": "pending", "browserOpened": True})
        self.assertEqual(stable_token.token, "existingToken_1234")
        self.assertTrue(pending_key.cleared)
        self.assertEqual(pending["pinId"], 44)
        self.assertNotIn("keyId", pending)
        request = cloud.requests[0]
        self.assertEqual(request[2]["body"], {"strong": True})
        self.assertEqual(launch.call_args.args[0][0], "xdg-open")
        self.assertTrue(
            launch.call_args.args[0][1].startswith(authentication.PLEX_AUTH_URL)
        )

    def test_poll_returns_server_choices_without_tokens_or_addresses(self):
        pending_account = FakeStore()
        cloud = FakeCloud(
            {
                "/api/v2/pins/44": {"authToken": "accountToken_1234"},
                "/api/v2/resources": resource_document(),
            }
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(
                os.environ, {"XDG_CONFIG_HOME": str(Path(directory) / "config")}
            ),
            mock.patch.object(
                authentication,
                "PendingAccountTokenStore",
                return_value=pending_account,
            ),
        ):
            authentication.save_pending_auth(pending_document())
            document = poll_sign_in(cloud)
        serialized = json.dumps(document)
        self.assertEqual(document["state"], "servers")
        self.assertEqual(document["servers"][0]["name"], "Living Room Plex")
        self.assertEqual(pending_account.token, "accountToken_1234")
        self.assertEqual(cloud.requests[0][0], "/api/v2/pins/44")
        self.assertNotIn("accountToken", serialized)
        self.assertNotIn("serverToken", serialized)
        self.assertNotIn("192.0.2.4", serialized)

    def test_server_without_scoped_access_token_is_not_used(self):
        document = resource_document()
        document[0].pop("accessToken")
        self.assertEqual(resource_servers(document), [])

    def test_complete_sign_in_commits_selected_server_and_clears_legacy_key(self):
        pending_account = FakeStore("accountToken_1234")
        pending_key = FakeStore()
        token_store = FakeStore("existingToken_1234")
        account_store = FakeStore()
        key_store = FakeStore("old device key")
        cloud = FakeCloud({"/api/v2/resources": resource_document()})
        libraries = [
            {"id": "3", "type": "movie", "title": "Movies"},
            {"id": "2", "type": "show", "title": "Shows"},
        ]
        snapshot = {
            "schemaVersion": 1,
            "configured": True,
            "sourceState": "updated",
            "stale": False,
            "items": [],
            "continueItems": [],
            "movieItems": [],
            "seriesItems": [],
            "newCount": 0,
            "lastSuccessAt": "2026-08-29T12:00:00Z",
            "error": "",
        }
        plex = mock.Mock(server="http://192.0.2.4:32400")
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(
                os.environ,
                {
                    "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                    "XDG_CACHE_HOME": str(Path(directory) / "cache"),
                },
            ),
            mock.patch.object(
                authentication,
                "PendingAccountTokenStore",
                return_value=pending_account,
            ),
            mock.patch.object(
                authentication, "PendingDeviceKeyStore", return_value=pending_key
            ),
            mock.patch.object(authentication, "SecretStore", return_value=token_store),
            mock.patch.object(
                authentication, "AccountTokenStore", return_value=account_store
            ),
            mock.patch.object(authentication, "DeviceKeyStore", return_value=key_store),
            mock.patch.object(
                authentication,
                "connect_to_resource",
                return_value=(plex, libraries, "Living Room Plex"),
            ),
            mock.patch.object(authentication, "recent_snapshot", return_value=snapshot),
        ):
            authentication.save_pending_auth(pending_document())
            returned_snapshot, config, returned_libraries = complete_sign_in(
                "server-machine-id", cloud
            )
            saved = load_config()
            self.assertIsNone(load_pending_auth())
        self.assertEqual(returned_snapshot, snapshot)
        self.assertEqual(returned_libraries, libraries)
        self.assertEqual(config["authMode"], "plex")
        self.assertEqual(saved["machineIdentifier"], "server-machine-id")
        self.assertNotIn("keyId", saved)
        self.assertEqual(token_store.token, "serverToken_1234")
        self.assertEqual(account_store.token, "accountToken_1234")
        self.assertTrue(key_store.cleared)

    def test_token_for_config_uses_saved_server_token(self):
        token_store = FakeStore("serverToken_1234")
        with mock.patch.object(authentication, "SecretStore", return_value=token_store):
            token = token_for_config({"authMode": "plex"})
        self.assertEqual(token, "serverToken_1234")

    def test_token_for_config_requires_saved_server_token(self):
        with (
            mock.patch.object(authentication, "SecretStore", return_value=FakeStore()),
            self.assertRaisesRegex(
                ConfigurationError, "Plex sign-in credentials are incomplete"
            ),
        ):
            token_for_config({"authMode": "plex"})


if __name__ == "__main__":
    unittest.main()
