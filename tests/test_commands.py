from __future__ import annotations

import unittest
from unittest import mock

from omaplex import commands as commands_module
from omaplex.commands import command_refresh, scan_libraries
from tests.support import FakeClient


class PlexHelperTests(unittest.TestCase):
    def test_refresh_backfills_and_returns_the_server_name(self):
            client = mock.Mock()
            client.fetch_server_name.return_value = "pgs-plex"
            config = {
                "schemaVersion": 1,
                "server": "http://plex:32400",
                "serverName": "",
                "movieSectionIds": ["3"],
                "tvSectionIds": ["2"],
                "machineIdentifier": "server-id",
            }
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
                "lastSuccessAt": "2026-08-25T12:00:00Z",
                "error": "",
            }
            with (
                mock.patch.object(
                    commands_module, "client_from_saved", return_value=(client, config)
                ),
                mock.patch.object(
                    commands_module, "recent_snapshot", return_value=snapshot
                ),
                mock.patch.object(commands_module, "save_config") as save_config,
                mock.patch.object(commands_module, "save_snapshot"),
                mock.patch.object(commands_module, "print_json") as print_json,
            ):
                self.assertEqual(command_refresh(), 0)
            self.assertEqual(save_config.call_args.args[0]["serverName"], "pgs-plex")
            self.assertEqual(
                print_json.call_args.args[0]["connection"]["serverName"],
                "pgs-plex",
            )

    def test_scan_discovers_all_video_sections_dynamically(self):
            client = FakeClient(
                {
                    "/library/sections": {
                        "MediaContainer": {
                            "Directory": [
                                {"type": "movie", "key": "7", "title": "Films"},
                                {"type": "show", "key": "42", "title": "Television"},
                                {"type": "artist", "key": "8", "title": "Music"},
                                {"type": "show", "key": "../bad", "title": "Invalid"},
                            ]
                        }
                    }
                }
            )
            result = scan_libraries(client)
            self.assertEqual(result["sectionCount"], 2)
            self.assertEqual(result["movieSections"], 1)
            self.assertEqual(result["seriesSections"], 1)
            self.assertIn("/library/sections/7/refresh", client.paths)
            self.assertIn("/library/sections/42/refresh", client.paths)
            self.assertNotIn("/library/sections/8/refresh", client.paths)
