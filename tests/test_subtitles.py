from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from omaplex import cli as cli_module
from omaplex import playback as playback_module
from omaplex import subtitles as subtitles_module
from omaplex.client import HttpMethod
from omaplex.common import ConfigurationError
from omaplex.playback import PlaybackMode
from tests.support import FakeClient


class ByteResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.headers = {"Content-Length": str(len(payload))}
        self.status = 200
        self.closed = False

    def read(self, maximum: int) -> bytes:
        return self.payload[:maximum]

    def close(self) -> None:
        self.closed = True


class SubtitleTests(unittest.TestCase):
    def test_search_normalizes_bounded_plex_results(self):
        client = FakeClient(
            {
                "/subtitles?": {
                    "MediaContainer": {
                        "Stream": [
                            {
                                "key": "/library/streams/700",
                                "extendedDisplayTitle": "English {unsafe}",
                                "providerTitle": "OpenSubtitles",
                                "format": "srt",
                                "languageCode": "eng",
                                "hearingImpaired": True,
                                "perfectMatch": "1",
                                "score": "99",
                            },
                            {
                                "key": "https://attacker.invalid/subtitle",
                                "displayTitle": "Bad result",
                            },
                        ]
                    }
                },
                "/library/metadata/42": {
                    "MediaContainer": {"Metadata": [{"Media": []}]}
                },
            }
        )
        document = subtitles_module.search_subtitles(client, "42", "EN")
        self.assertEqual(document["language"], "en")
        self.assertEqual(len(document["items"]), 1)
        item = document["items"][0]
        self.assertEqual(item["key"], "/library/streams/700")
        self.assertEqual(item["label"], "English unsafe")
        self.assertTrue(item["hearingImpaired"])
        self.assertTrue(item["perfectMatch"])
        self.assertEqual(item["score"], 99)
        self.assertIn("language=en", client.paths[0])

        with self.assertRaises(ConfigurationError):
            subtitles_module.search_subtitles(client, "../42", "en")
        with self.assertRaises(ConfigurationError):
            subtitles_module.search_subtitles(client, "42", "english")

    def test_download_uses_put_and_private_player_directory(self):
        client = mock.Mock()
        response = ByteResponse(b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        client.open.return_value = response
        client.request_json.side_effect = [
            {"MediaContainer": {"Metadata": [{"Media": [{"Part": [{"Stream": []}]}]}]}},
            {
                "MediaContainer": {
                    "Metadata": [
                        {
                            "Media": [
                                {
                                    "Part": [
                                        {
                                            "Stream": [
                                                {
                                                    "streamType": 3,
                                                    "selected": True,
                                                    "key": "/library/streams/701",
                                                    "format": "srt",
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            },
        ]
        with tempfile.TemporaryDirectory(
            prefix="omaplex-player-", dir="/tmp"
        ) as directory:
            result = subtitles_module.download_subtitle(
                client,
                "42",
                "/library/streams/700",
                "srt",
                directory,
            )
            output = Path(result["path"])
            self.assertEqual(output.name, "subtitle-701.srt")
            self.assertEqual(output.read_bytes(), response.payload)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(output.parent, Path(directory))
        request = client.request_empty.call_args
        self.assertTrue(request.args[0].startswith("/library/metadata/42/subtitles?"))
        self.assertEqual(request.kwargs["method"], HttpMethod.PUT)
        client.open.assert_called_once_with("/library/streams/701")
        self.assertTrue(response.closed)

        with (
            tempfile.TemporaryDirectory() as unsafe_directory,
            self.assertRaises(ConfigurationError),
        ):
            subtitles_module.download_subtitle(
                client,
                "42",
                "/library/streams/700",
                "srt",
                unsafe_directory,
            )

    def test_already_selected_saved_subtitle_skips_plex_put(self):
        client = mock.Mock()
        client.request_json.return_value = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "Media": [
                            {
                                "Part": [
                                    {
                                        "Stream": [
                                            {
                                                "streamType": 3,
                                                "selected": 1,
                                                "key": "/library/streams/701",
                                                "format": "srt",
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        }
        response = ByteResponse(b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        client.open.return_value = response
        with tempfile.TemporaryDirectory(
            prefix="omaplex-player-", dir="/tmp"
        ) as directory:
            result = subtitles_module.download_subtitle(
                client,
                "42",
                "/library/streams/701",
                "srt",
                directory,
            )
            self.assertTrue(Path(result["path"]).is_file())
        client.request_empty.assert_not_called()
        client.open.assert_called_once_with("/library/streams/701")

    def test_player_arguments_load_search_script_without_a_token(self):
        args = playback_module.mpv_playlist_arguments(
            PlaybackMode.WINDOWED,
            [("http://127.0.0.1:32000/stream/item", 0, [])],
            "/tmp/omaplex-player-test/mpv.sock",
            None,
            "/plugin/assets/omaplex_subtitles.lua",
            "/plugin/bin/omaplex",
            ["42"],
            "nl",
            "/tmp/omaplex-player-test",
        )
        self.assertIn("--script=/plugin/assets/omaplex_subtitles.lua", args)
        self.assertIn("--script-opt=omaplex_subtitles-language=nl", args)
        self.assertIn("--script-opt=omaplex_subtitles-rating_keys=42", args)
        self.assertNotIn("PlexToken", " ".join(args))

        parsed = cli_module.parser().parse_args(
            [
                "subtitle-search",
                "--rating-key",
                "42",
                "--language",
                "en",
            ]
        )
        self.assertEqual(parsed.command, "subtitle-search")
        self.assertEqual(parsed.language, "en")


if __name__ == "__main__":
    unittest.main()
