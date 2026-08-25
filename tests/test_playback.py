from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from omaplex import cli as cli_module
from omaplex import playback as playback_module
from omaplex import windowing as windowing_module
from omaplex.common import ConfigurationError, ResponseError
from omaplex.config import (
    config_home,
    load_window_geometry,
    save_window_geometry,
    validate_window_geometry,
)
from omaplex.constants import MAX_HYPR_BYTES, PLUGIN_ID
from omaplex.playback import PlaybackMode, TimelineState, WatchState
from tests.support import FakeClient


class PlexHelperTests(unittest.TestCase):
    def test_playback_metadata_and_mpv_argv_do_not_contain_token(self):
            client = FakeClient(
                {
                    "/library/metadata/42": {
                        "MediaContainer": {
                            "Metadata": [
                                {
                                    "viewOffset": 125000,
                                    "Media": [
                                        {
                                            "Part": [
                                                {
                                                    "key": "/library/parts/100/file.mkv",
                                                    "Stream": [
                                                        {
                                                            "streamType": 2,
                                                            "key": "/library/streams/audio",
                                                        },
                                                        {
                                                            "streamType": 3,
                                                            "key": "/library/streams/500",
                                                        },
                                                        {
                                                            "streamType": 3,
                                                            "key": "https://attacker.invalid/subtitle",
                                                        },
                                                    ],
                                                }
                                            ]
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                }
            )
            part, resume, duration, subtitles = playback_module.playback_metadata(
                client, "42"
            )
            self.assertEqual(part, "/library/parts/100/file.mkv")
            self.assertEqual(resume, 125)
            self.assertEqual(duration, 0)
            self.assertEqual(subtitles, ["/library/streams/500"])
            secret = "TokenThatMustStayPrivate"
            args = playback_module.mpv_arguments(
                PlaybackMode.WINDOWED,
                "http://127.0.0.1:32100/stream/random",
                resume,
                ["http://127.0.0.1:32100/subtitle/random/0"],
            )
            self.assertNotIn(secret, " ".join(args))
            self.assertIn("--autofit=960x540", args)
            self.assertIn("--osc=yes", args)
            self.assertIn("--input-default-bindings=yes", args)
            self.assertIn("--sub-file=http://127.0.0.1:32100/subtitle/random/0", args)
            fullscreen = playback_module.mpv_arguments(
                PlaybackMode.FULLSCREEN, "http://127.0.0.1:32100/stream/random", 0
            )
            self.assertIn("--fullscreen", fullscreen)
            self.assertIn("--wayland-app-id=" + PLUGIN_ID + ".player", fullscreen)
            geometry = {
                "schemaVersion": 1,
                "x": 2100,
                "y": 1300,
                "width": 1120,
                "height": 630,
            }
            restored = playback_module.mpv_arguments(
                PlaybackMode.WINDOWED,
                "http://127.0.0.1:32100/stream/random",
                0,
                window_geometry=geometry,
            )
            self.assertIn("--geometry=1120x630", restored)
            self.assertNotIn("--autofit=960x540", restored)
            geometry_script = windowing_module.hypr_geometry_script(12345, geometry)
            self.assertIn("w.pid == 12345", geometry_script)
            self.assertIn(
                "resize({ x = 1120, y = 630, relative = false, window = target })",
                geometry_script,
            )
            self.assertIn(
                "move({ x = 2100, y = 1300, relative = false, window = target })",
                geometry_script,
            )
            script = windowing_module.hypr_fullscreen_script(12345)
            self.assertIn("w.pid == 12345", script)
            self.assertIn("fullscreen_state({ internal = 2, client = 2 })", script)
            self.assertNotIn(secret, script)
            timeline = playback_module.timeline_path(
                "42", 125000, 300000, TimelineState.PLAYING
            )
            self.assertTrue(timeline.startswith("/:/timeline?"))
            self.assertIn("ratingKey=42", timeline)
            self.assertIn("time=125000", timeline)
            reporter = mock.Mock()
            playback_module.report_timeline(
                reporter, "42", 125000, 300000, TimelineState.PAUSED
            )
            reporter.request_empty.assert_called_once()
            self.assertIn("state=paused", reporter.request_empty.call_args.args[0])

    def test_watch_state_updates_use_scrobble_endpoints_and_validate_inputs(self):
            client = mock.Mock()
            playback_module.set_watch_state(client, "42", WatchState.WATCHED)
            self.assertTrue(
                client.request_empty.call_args.args[0].startswith("/:/scrobble?")
            )
            self.assertIn("key=42", client.request_empty.call_args.args[0])

            playback_module.set_watch_state(client, "42", WatchState.UNWATCHED)
            self.assertTrue(
                client.request_empty.call_args.args[0].startswith("/:/unscrobble?")
            )

            with self.assertRaises(ConfigurationError):
                playback_module.set_watch_state(client, "../42", WatchState.WATCHED)
            with self.assertRaises(ConfigurationError):
                playback_module.set_watch_state(client, "42", "maybe")

            args = cli_module.parser().parse_args(
                [
                    "mark",
                    "--rating-key",
                    "42",
                    "--state",
                    "unwatched",
                ]
            )
            self.assertEqual(
                (args.command, args.rating_key, args.state),
                ("mark", "42", WatchState.UNWATCHED),
            )

            command_client = mock.Mock()
            with mock.patch.object(
                cli_module, "client_from_saved", return_value=(command_client, {})
            ):
                self.assertEqual(
                    cli_module.main(
                        [
                            "mark",
                            "--rating-key",
                            "42",
                            "--state",
                            "watched",
                        ]
                    ),
                    0,
                )
            command_client.request_empty.assert_called_once()
            self.assertTrue(
                command_client.request_empty.call_args.args[0].startswith("/:/scrobble?")
            )

    def test_plex_web_urls_support_home_and_item_deep_links(self):
            config = {
                "server": "http://plex:32400",
                "machineIdentifier": "server-id",
            }
            self.assertEqual(
                playback_module.plex_web_url(config),
                "http://plex:32400/web/index.html",
            )
            item_url = playback_module.plex_web_url(config, "42")
            self.assertIn("#!/server/server-id/details", item_url)
            self.assertIn("%2Flibrary%2Fmetadata%2F42", item_url)
            self.assertEqual(
                cli_module.parser().parse_args(["open-web"]).rating_key,
                "",
            )
            with self.assertRaises(ConfigurationError):
                playback_module.plex_web_url(config, "../42")

    def test_player_geometry_is_private_bounded_and_read_from_own_pid(self):
            geometry = {
                "schemaVersion": 1,
                "x": -20,
                "y": 140,
                "width": 960,
                "height": 540,
            }
            with (
                tempfile.TemporaryDirectory() as directory,
                mock.patch.dict(
                    os.environ,
                    {
                        "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                    },
                    clear=False,
                ),
            ):
                save_window_geometry(geometry)
                path = config_home() / "player-window.json"
                self.assertEqual(load_window_geometry(), geometry)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            clients = [
                {
                    "pid": 12345,
                    "mapped": True,
                    "floating": True,
                    "fullscreen": 0,
                    "at": [2100, 1300],
                    "size": [1120, 630],
                    "title": "Untrusted title that is ignored",
                }
            ]
            with mock.patch.object(
                windowing_module,
                "run_bounded_output",
                return_value=(0, json.dumps(clients).encode("utf-8")),
            ) as command:
                self.assertEqual(
                    windowing_module.read_hypr_geometry(12345),
                    {
                        "schemaVersion": 1,
                        "x": 2100,
                        "y": 1300,
                        "width": 1120,
                        "height": 630,
                    },
                )
            command.assert_called_once_with(
                ["hyprctl", "-j", "clients"], maximum=MAX_HYPR_BYTES, timeout=2
            )
            monitors = [{"x": 2048, "y": 1224, "width": 1920, "height": 1080}]
            with mock.patch.object(
                windowing_module,
                "run_bounded_output",
                return_value=(0, json.dumps(monitors).encode("utf-8")),
            ):
                self.assertTrue(
                    windowing_module.geometry_is_visible(
                        {
                            "schemaVersion": 1,
                            "x": 2100,
                            "y": 1300,
                            "width": 1120,
                            "height": 630,
                        }
                    )
                )
                self.assertFalse(
                    windowing_module.geometry_is_visible(
                        {
                            "schemaVersion": 1,
                            "x": 90000,
                            "y": 90000,
                            "width": 1120,
                            "height": 630,
                        }
                    )
                )
            with self.assertRaises(ResponseError):
                validate_window_geometry(
                    {
                        "schemaVersion": 1,
                        "x": 0,
                        "y": 0,
                        "width": 1000000,
                        "height": 540,
                    }
                )
