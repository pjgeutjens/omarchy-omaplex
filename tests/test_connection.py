from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from omaplex import connection as connection_module
from omaplex.activity import cache_home, save_snapshot
from omaplex.client import validate_origin
from omaplex.common import AuthenticationError, ConfigurationError, isoformat
from omaplex.config import config_home, load_config, save_config, validate_config
from omaplex.connection import (
    clear_configuration,
    configure_connection,
    parse_env_file,
    read_setup,
)
from omaplex.constants import MAX_SETUP_BYTES
from tests.support import NOW, FakeStore


class PlexHelperTests(unittest.TestCase):
    def test_setup_input_is_bounded_and_strict(self):
        server, token = read_setup(
            io.BytesIO(b'{"server":"http://plex:32400","token":"safeToken_123456"}\n')
        )
        self.assertEqual(server, "http://plex:32400")
        self.assertEqual(token, "safeToken_123456")
        with self.assertRaises(ConfigurationError):
            read_setup(io.BytesIO(b'{"server":"http://plex:32400","extra":true}\n'))
        with self.assertRaises(ConfigurationError):
            read_setup(io.BytesIO(b"x" * (MAX_SETUP_BYTES + 1)))

    def test_configure_discovers_all_video_libraries_and_saves_secret(self):
        libraries = [
            {"id": "3", "type": "movie", "title": "Movies"},
            {"id": "7", "type": "movie", "title": "Documentaries"},
            {"id": "2", "type": "show", "title": "TV Shows"},
            {"id": "8", "type": "artist", "title": "Music"},
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
            "lastSuccessAt": isoformat(NOW),
            "error": "",
        }
        client = mock.Mock()
        client.discover.return_value = (libraries, "server-id", "Living Room Plex")
        store = FakeStore()
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(
                os.environ,
                {
                    "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                    "XDG_CACHE_HOME": str(Path(directory) / "cache"),
                },
                clear=False,
            ),
            mock.patch.object(connection_module, "PlexClient", return_value=client),
            mock.patch.object(
                connection_module, "recent_snapshot", return_value=snapshot
            ),
        ):
            document = configure_connection(
                "http://plex:32400", "safeToken_123456", store
            )
            config = load_config()
        self.assertEqual(store.stored, ["safeToken_123456"])
        self.assertEqual(config["movieSectionIds"], ["3", "7"])
        self.assertEqual(config["tvSectionIds"], ["2"])
        self.assertEqual(config["serverName"], "Living Room Plex")
        self.assertEqual(document["connection"]["serverName"], "Living Room Plex")
        self.assertEqual(
            document["connection"]["movieLibraries"][1]["title"], "Documentaries"
        )
        self.assertEqual(
            document["connection"]["seriesLibraries"][0]["title"], "TV Shows"
        )
        self.assertNotIn("safeToken_123456", json.dumps(document))

    def test_configure_can_reuse_saved_token(self):
        client = mock.Mock()
        client.discover.return_value = (
            [{"id": "2", "type": "show", "title": "Series"}],
            "server-id",
            "new-plex",
        )
        store = FakeStore("existingToken_1234")
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
            "lastSuccessAt": isoformat(NOW),
            "error": "",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(
                os.environ,
                {
                    "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                    "XDG_CACHE_HOME": str(Path(directory) / "cache"),
                },
                clear=False,
            ),
            mock.patch.object(
                connection_module, "PlexClient", return_value=client
            ) as constructor,
            mock.patch.object(
                connection_module, "recent_snapshot", return_value=snapshot
            ),
        ):
            configure_connection("http://new-plex:32400", "", store)
        constructor.assert_called_once_with(
            "http://new-plex:32400", "existingToken_1234"
        )
        self.assertEqual(store.stored, [])

    def test_failed_connection_test_keeps_existing_settings(self):
        store = FakeStore("existingToken_1234")
        client = mock.Mock()
        client.discover.side_effect = AuthenticationError(
            "Plex rejected the configured token"
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(
                os.environ,
                {
                    "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                    "XDG_CACHE_HOME": str(Path(directory) / "cache"),
                },
                clear=False,
            ),
        ):
            original = {
                "schemaVersion": 1,
                "server": "http://old-plex:32400",
                "movieSectionIds": ["3"],
                "tvSectionIds": ["2"],
                "machineIdentifier": "old-server-id",
                "serverName": "old-plex",
                "authMode": "manual",
                "clientIdentifier": "",
            }
            save_config(original)
            with (
                mock.patch.object(connection_module, "PlexClient", return_value=client),
                self.assertRaises(AuthenticationError),
            ):
                configure_connection("http://new-plex:32400", "badToken_12345", store)
            self.assertEqual(load_config(), original)
        self.assertEqual(store.token, "existingToken_1234")
        self.assertEqual(store.stored, [])

    def test_clear_configuration_removes_files_and_secret(self):
        store = FakeStore("existingToken_1234")
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(
                os.environ,
                {
                    "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                    "XDG_CACHE_HOME": str(Path(directory) / "cache"),
                },
                clear=False,
            ),
        ):
            save_config(
                {
                    "schemaVersion": 1,
                    "server": "http://plex:32400",
                    "movieSectionIds": ["3"],
                    "tvSectionIds": ["2"],
                    "machineIdentifier": "server-id",
                }
            )
            save_snapshot(
                {
                    "schemaVersion": 1,
                    "configured": True,
                    "sourceState": "empty",
                    "stale": True,
                    "items": [],
                    "continueItems": [],
                    "movieItems": [],
                    "seriesItems": [],
                    "newCount": 0,
                    "lastSuccessAt": "",
                    "error": "",
                }
            )
            document = clear_configuration(store)
            self.assertFalse((config_home() / "config.json").exists())
            self.assertFalse((cache_home() / "recent.json").exists())
        self.assertTrue(store.cleared)
        self.assertFalse(document["configured"])
        self.assertEqual(document["connection"]["server"], "")
        self.assertEqual(document["connection"]["serverName"], "")

    def test_config_rejects_paths_and_bad_sections(self):
        with self.assertRaises(ConfigurationError):
            validate_origin("http://plex:32400/web")
        with self.assertRaises(ConfigurationError):
            validate_config(
                {
                    "schemaVersion": 1,
                    "server": "http://plex:32400",
                    "movieSectionIds": ["../3"],
                    "tvSectionIds": [],
                }
            )

    def test_env_parser_does_not_execute_shell_text(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            marker = Path(directory) / "executed"
            env_file.write_text(
                "PLEX_BASE_URL=http://plex:32400\n"
                "PLEX_TOKEN='safeToken_123456'\n"
                "PLEX_MOVIES_SECTION_ID=3\n"
                "UNRELATED=$(touch " + str(marker) + ")\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            values = parse_env_file(env_file)
            self.assertEqual(values["PLEX_TOKEN"], "safeToken_123456")
            self.assertFalse(marker.exists())
