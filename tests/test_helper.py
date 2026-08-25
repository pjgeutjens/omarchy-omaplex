from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("plex_recently_added", str(ROOT / "bin" / "omaplex"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
plex = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(plex)


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.paths = []

    def request_json(self, path):
        self.paths.append(path)
        for needle, response in self.responses.items():
            if needle in path:
                return response
        raise AssertionError("unexpected path: " + path)

    def request_empty(self, path):
        self.paths.append(path)


class FakeStore:
    def __init__(self, token=None):
        self.token = token
        self.stored = []
        self.cleared = False

    def lookup(self):
        return self.token

    def store(self, token):
        self.token = token
        self.stored.append(token)

    def clear(self):
        self.token = None
        self.cleared = True


def container(*items):
    return {"MediaContainer": {"Metadata": list(items)}}


class PlexHelperTests(unittest.TestCase):
    def test_setup_input_is_bounded_and_strict(self):
        server, token = plex.read_setup(io.BytesIO(
            b'{"server":"http://plex:32400","token":"safeToken_123456"}\n'
        ))
        self.assertEqual(server, "http://plex:32400")
        self.assertEqual(token, "safeToken_123456")
        with self.assertRaises(plex.ConfigurationError):
            plex.read_setup(io.BytesIO(b'{"server":"http://plex:32400","extra":true}\n'))
        with self.assertRaises(plex.ConfigurationError):
            plex.read_setup(io.BytesIO(b"x" * (plex.MAX_SETUP_BYTES + 1)))

    def test_configure_discovers_all_video_libraries_and_saves_secret(self):
        libraries = [
            {"id": "3", "type": "movie", "title": "Movies"},
            {"id": "7", "type": "movie", "title": "Documentaries"},
            {"id": "2", "type": "show", "title": "TV Shows"},
            {"id": "8", "type": "artist", "title": "Music"},
        ]
        snapshot = {
            "schemaVersion": 1, "configured": True, "sourceState": "updated",
            "stale": False, "items": [], "continueItems": [], "movieItems": [],
            "seriesItems": [], "newCount": 0, "lastSuccessAt": plex.isoformat(NOW),
            "error": "",
        }
        client = mock.Mock()
        client.discover.return_value = (libraries, "server-id")
        store = FakeStore()
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(Path(directory) / "config"),
            "XDG_CACHE_HOME": str(Path(directory) / "cache"),
        }, clear=False), mock.patch.object(plex, "PlexClient", return_value=client), \
                mock.patch.object(plex, "recent_snapshot", return_value=snapshot):
            document = plex.configure_connection("http://plex:32400", "safeToken_123456", store)
            config = plex.load_config()
        self.assertEqual(store.stored, ["safeToken_123456"])
        self.assertEqual(config["movieSectionIds"], ["3", "7"])
        self.assertEqual(config["tvSectionIds"], ["2"])
        self.assertEqual(document["connection"]["movieLibraries"][1]["title"], "Documentaries")
        self.assertEqual(document["connection"]["seriesLibraries"][0]["title"], "TV Shows")
        self.assertNotIn("safeToken_123456", json.dumps(document))

    def test_configure_can_reuse_saved_token(self):
        client = mock.Mock()
        client.discover.return_value = ([{"id": "2", "type": "show", "title": "Series"}], "server-id")
        store = FakeStore("existingToken_1234")
        snapshot = {
            "schemaVersion": 1, "configured": True, "sourceState": "updated",
            "stale": False, "items": [], "continueItems": [], "movieItems": [],
            "seriesItems": [], "newCount": 0, "lastSuccessAt": plex.isoformat(NOW),
            "error": "",
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(Path(directory) / "config"),
            "XDG_CACHE_HOME": str(Path(directory) / "cache"),
        }, clear=False), mock.patch.object(plex, "PlexClient", return_value=client) as constructor, \
                mock.patch.object(plex, "recent_snapshot", return_value=snapshot):
            plex.configure_connection("http://new-plex:32400", "", store)
        constructor.assert_called_once_with("http://new-plex:32400", "existingToken_1234")
        self.assertEqual(store.stored, [])

    def test_failed_connection_test_keeps_existing_settings(self):
        store = FakeStore("existingToken_1234")
        client = mock.Mock()
        client.discover.side_effect = plex.AuthenticationError("Plex rejected the configured token")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(Path(directory) / "config"),
            "XDG_CACHE_HOME": str(Path(directory) / "cache"),
        }, clear=False):
            original = {
                "schemaVersion": 1, "server": "http://old-plex:32400",
                "movieSectionIds": ["3"], "tvSectionIds": ["2"],
                "machineIdentifier": "old-server-id",
            }
            plex.save_config(original)
            with mock.patch.object(plex, "PlexClient", return_value=client):
                with self.assertRaises(plex.AuthenticationError):
                    plex.configure_connection("http://new-plex:32400", "badToken_12345", store)
            self.assertEqual(plex.load_config(), original)
        self.assertEqual(store.token, "existingToken_1234")
        self.assertEqual(store.stored, [])

    def test_clear_configuration_removes_files_and_secret(self):
        store = FakeStore("existingToken_1234")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(Path(directory) / "config"),
            "XDG_CACHE_HOME": str(Path(directory) / "cache"),
        }, clear=False):
            plex.save_config({
                "schemaVersion": 1, "server": "http://plex:32400",
                "movieSectionIds": ["3"], "tvSectionIds": ["2"],
                "machineIdentifier": "server-id",
            })
            plex.save_snapshot({
                "schemaVersion": 1, "configured": True, "sourceState": "empty",
                "stale": True, "items": [], "continueItems": [], "movieItems": [],
                "seriesItems": [], "newCount": 0, "lastSuccessAt": "", "error": "",
            })
            document = plex.clear_configuration(store)
            self.assertFalse((plex.config_home() / "config.json").exists())
            self.assertFalse((plex.cache_home() / "recent.json").exists())
        self.assertTrue(store.cleared)
        self.assertFalse(document["configured"])
        self.assertEqual(document["connection"]["server"], "")

    def test_normalizes_movie_and_watch_states(self):
        movie = plex.normalize_item({
            "type": "movie", "ratingKey": "20", "title": "Dune: Part Two",
            "year": 2024, "addedAt": int((NOW - timedelta(days=2)).timestamp()),
            "viewOffset": 120,
        }, NOW)
        self.assertEqual(movie["kind"], "movie")
        self.assertEqual(movie["subtitle"], "Movie · 2024")
        self.assertEqual(movie["watchState"], "started")
        self.assertFalse(movie["isNew"])

        watched = plex.normalize_item({
            "type": "movie", "ratingKey": "21", "title": "Arrival",
            "addedAt": int(NOW.timestamp()), "viewCount": 1,
        }, NOW)
        self.assertEqual(watched["watchState"], "watched")
        self.assertFalse(watched["isNew"])

    def test_collapses_episodes_and_merges_newest_first(self):
        client = FakeClient({
            "/hubs/home/continueWatching": container(
                {
                    "type": "episode", "ratingKey": "30", "grandparentRatingKey": "6",
                    "grandparentTitle": "Continue show", "parentIndex": 1, "index": 3,
                    "title": "Episode", "addedAt": int((NOW - timedelta(days=5)).timestamp()),
                    "lastViewedAt": int((NOW - timedelta(hours=2)).timestamp()),
                    "viewOffset": 30000, "duration": 100000,
                },
                {
                    "type": "movie", "ratingKey": "31", "title": "More recent viewing",
                    "addedAt": int((NOW - timedelta(days=10)).timestamp()),
                    "lastViewedAt": int((NOW - timedelta(hours=1)).timestamp()),
                    "viewOffset": 10000, "duration": 100000,
                },
            ),
            "/library/onDeck": container(
                {
                    "type": "episode", "ratingKey": "30", "grandparentRatingKey": "6",
                    "grandparentTitle": "Continue show", "parentIndex": 1, "index": 3,
                    "title": "Episode", "addedAt": int((NOW - timedelta(days=5)).timestamp()),
                    "lastViewedAt": int((NOW - timedelta(hours=2)).timestamp()),
                    "viewOffset": 30000, "duration": 100000,
                },
                {
                    "type": "episode", "ratingKey": "9", "grandparentRatingKey": "5",
                    "grandparentTitle": "On deck show", "parentIndex": 2, "index": 1,
                    "title": "Next", "addedAt": int((NOW - timedelta(days=7)).timestamp()),
                    "lastViewedAt": int((NOW - timedelta(minutes=30)).timestamp()),
                },
            ),
            "type=1": container({
                "type": "movie", "ratingKey": "20", "title": "Movie",
                "addedAt": int((NOW - timedelta(hours=2)).timestamp()),
            }),
            "type=4": container(
                {
                    "type": "episode", "ratingKey": "11", "grandparentRatingKey": "5",
                    "grandparentTitle": "Silo", "parentIndex": 2, "index": 2,
                    "title": "Order", "addedAt": int((NOW - timedelta(hours=1)).timestamp()),
                },
                {
                    "type": "episode", "ratingKey": "10", "grandparentRatingKey": "5",
                    "grandparentTitle": "Silo", "parentIndex": 2, "index": 1,
                    "title": "The Engineer", "addedAt": int((NOW - timedelta(hours=3)).timestamp()),
                },
            ),
        })
        snapshot = plex.recent_snapshot(client, {
            "movieSectionIds": ["3"], "tvSectionIds": ["2"]
        }, NOW)
        self.assertEqual([item["title"] for item in snapshot["items"]], ["Silo", "Movie"])
        self.assertEqual(snapshot["items"][0]["subtitle"], "Series · S02E02 · Order")
        self.assertEqual(snapshot["items"][0]["playbackRatingKey"], "9")
        self.assertEqual(snapshot["items"][0]["playbackHint"], "Next S02E01")
        self.assertEqual([item["ratingKey"] for item in snapshot["continueItems"]], ["9", "31", "30"])
        self.assertEqual(snapshot["continueItems"][0]["addedLabel"], "Played 30m ago")
        self.assertEqual(snapshot["continueItems"][0]["playbackHint"], "Next episode")
        self.assertEqual(snapshot["continueItems"][1]["addedLabel"], "Played 1h ago")
        self.assertEqual(snapshot["continueItems"][2]["addedLabel"], "Played 2h ago")
        self.assertEqual(snapshot["continueItems"][2]["playbackHint"], "Resume 30%")
        self.assertEqual(len(snapshot["movieItems"]), 1)
        self.assertEqual(len(snapshot["seriesItems"]), 1)
        self.assertEqual(snapshot["newCount"], 2)

    def test_new_requires_unwatched_and_thirty_day_window(self):
        recent = plex.normalize_item({
            "type": "movie", "ratingKey": "1", "title": "Recent",
            "addedAt": int((NOW - timedelta(days=30)).timestamp()),
        }, NOW)
        old = plex.normalize_item({
            "type": "movie", "ratingKey": "2", "title": "Old",
            "addedAt": int((NOW - timedelta(days=31)).timestamp()),
        }, NOW)
        self.assertTrue(recent["isNew"])
        self.assertFalse(old["isNew"])

    def test_unfinished_items_precede_newer_watched_items(self):
        client = FakeClient({
            "/hubs/home/continueWatching": container(),
            "/library/onDeck": container(),
            "type=1": container(
                {
                    "type": "movie", "ratingKey": "1", "title": "Newest watched",
                    "addedAt": int(NOW.timestamp()), "viewCount": 1,
                },
                {
                    "type": "movie", "ratingKey": "2", "title": "Older unwatched",
                    "addedAt": int((NOW - timedelta(days=1)).timestamp()),
                },
                {
                    "type": "movie", "ratingKey": "3", "title": "Oldest started",
                    "addedAt": int((NOW - timedelta(days=2)).timestamp()), "viewOffset": 1000,
                },
            ),
        })
        snapshot = plex.recent_snapshot(client, {
            "movieSectionIds": ["3"], "tvSectionIds": []
        }, NOW)
        self.assertEqual(
            [item["watchState"] for item in snapshot["movieItems"]],
            ["started", "unwatched", "watched"],
        )

    def test_browse_normalizes_shows_and_pages(self):
        client = FakeClient({"/library/sections/2/all": {
            "MediaContainer": {
                "totalSize": 1,
                "Metadata": [{
                    "type": "show", "ratingKey": "50", "title": "Series",
                    "leafCount": 10, "viewedLeafCount": 3,
                    "addedAt": int(NOW.timestamp()),
                }],
            }
        }})
        document = plex.browse_document(client, {
            "movieSectionIds": [], "tvSectionIds": ["2"]
        }, "shows", "", 0, 40)
        self.assertEqual(document["total"], 1)
        self.assertEqual(document["items"][0]["watchState"], "started")
        self.assertFalse(document["items"][0]["playable"])

    def test_fuzzy_search_respects_scope_and_expands_season_codes(self):
        client = FakeClient({
            "/library/sections/3/all?": container({
                "type": "movie", "ratingKey": "40", "title": "Alone Together",
                "addedAt": int(NOW.timestamp()),
            }),
            "/library/sections/2/all?": container({
                "type": "show", "ratingKey": "50", "title": "Alone",
                "leafCount": 3, "addedAt": int(NOW.timestamp()),
            }),
            "/library/metadata/50/allLeaves?": container(
                {
                    "type": "episode", "ratingKey": "52", "grandparentTitle": "Alone",
                    "parentIndex": 1, "index": 2, "title": "Second",
                    "addedAt": int(NOW.timestamp()),
                },
                {
                    "type": "episode", "ratingKey": "51", "grandparentTitle": "Alone",
                    "parentIndex": 1, "index": 1, "title": "First",
                    "addedAt": int(NOW.timestamp()),
                },
                {
                    "type": "episode", "ratingKey": "53", "grandparentTitle": "Alone",
                    "parentIndex": 2, "index": 1, "title": "Later",
                    "addedAt": int(NOW.timestamp()),
                },
            ),
        })
        config = {"movieSectionIds": ["3"], "tvSectionIds": ["2"]}
        with mock.patch.object(plex.shutil, "which", return_value=None):
            movies = plex.browse_document(client, config, "search", "Alne", 0, 40, search_scope="movies")
            shows = plex.browse_document(client, config, "search", "Alne", 0, 40, search_scope="shows")
            season = plex.browse_document(client, config, "search", "Alone S01E", 0, 40, search_scope="shows")
            episode = plex.browse_document(client, config, "search", "Alone S01E02", 0, 40, search_scope="shows")
        self.assertEqual([item["kind"] for item in movies["items"]], ["movie"])
        self.assertEqual([item["kind"] for item in shows["items"]], ["show"])
        self.assertEqual(season["total"], 2)
        self.assertEqual([item["ratingKey"] for item in season["items"]], ["51", "52"])
        self.assertTrue(all("S01" in item["subtitle"] for item in season["items"]))
        self.assertTrue(all(item["playable"] for item in season["items"]))
        self.assertEqual([item["ratingKey"] for item in episode["items"]], ["52"])

    def test_episode_browser_filters_codes_titles_and_filtered_pages(self):
        client = FakeClient({
            "/library/metadata/50/allLeaves?": container(
                {
                    "type": "episode", "ratingKey": "401", "grandparentTitle": "Test Series",
                    "parentIndex": 4, "index": 1, "title": "Fourth One",
                    "addedAt": int(NOW.timestamp()),
                },
                {
                    "type": "episode", "ratingKey": "402", "grandparentTitle": "Test Series",
                    "parentIndex": 4, "index": 2, "title": "Fourth Two",
                    "addedAt": int(NOW.timestamp()),
                },
                {
                    "type": "episode", "ratingKey": "301", "grandparentTitle": "Test Series",
                    "parentIndex": 3, "index": 1, "title": "Third One",
                    "addedAt": int(NOW.timestamp()),
                },
                {
                    "type": "episode", "ratingKey": "302", "grandparentTitle": "Test Series",
                    "parentIndex": 3, "index": 2, "title": "Searchable Target",
                    "addedAt": int(NOW.timestamp()),
                },
            ),
        })
        config = {"movieSectionIds": [], "tvSectionIds": ["2"]}
        with mock.patch.object(plex.shutil, "which", return_value=None):
            episode = plex.browse_document(client, config, "episodes", "s04e01", 0, 40, "50")
            season_page = plex.browse_document(client, config, "episodes", "S03E", 1, 1, "50")
            title = plex.browse_document(client, config, "episodes", "srch trgt", 0, 40, "50")
        self.assertEqual(episode["total"], 1)
        self.assertEqual([item["ratingKey"] for item in episode["items"]], ["401"])
        self.assertEqual(season_page["total"], 2)
        self.assertEqual([item["ratingKey"] for item in season_page["items"]], ["302"])
        self.assertEqual([item["ratingKey"] for item in title["items"]], ["302"])
        self.assertTrue(all("title.value" not in path for path in client.paths))

    def test_scan_discovers_all_video_sections_dynamically(self):
        client = FakeClient({
            "/library/sections": {
                "MediaContainer": {"Directory": [
                    {"type": "movie", "key": "7", "title": "Films"},
                    {"type": "show", "key": "42", "title": "Television"},
                    {"type": "artist", "key": "8", "title": "Music"},
                    {"type": "show", "key": "../bad", "title": "Invalid"},
                ]}
            }
        })
        result = plex.scan_libraries(client)
        self.assertEqual(result["sectionCount"], 2)
        self.assertEqual(result["movieSections"], 1)
        self.assertEqual(result["seriesSections"], 1)
        self.assertIn("/library/sections/7/refresh", client.paths)
        self.assertIn("/library/sections/42/refresh", client.paths)
        self.assertNotIn("/library/sections/8/refresh", client.paths)

    def test_config_rejects_paths_and_bad_sections(self):
        with self.assertRaises(plex.ConfigurationError):
            plex.validate_origin("http://plex:32400/web")
        with self.assertRaises(plex.ConfigurationError):
            plex.validate_config({
                "schemaVersion": 1,
                "server": "http://plex:32400",
                "movieSectionIds": ["../3"],
                "tvSectionIds": [],
            })

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
            values = plex.parse_env_file(env_file)
            self.assertEqual(values["PLEX_TOKEN"], "safeToken_123456")
            self.assertFalse(marker.exists())

    def test_cache_is_atomic_private_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "recent.json"
            plex.atomic_json_write(target, {"ok": True}, 1024)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"ok": True})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            link = Path(directory) / "link.json"
            link.symlink_to(target)
            with self.assertRaises(plex.ResponseError):
                plex.read_regular_file(link, 1024)

    def test_playback_metadata_and_mpv_argv_do_not_contain_token(self):
        client = FakeClient({"/library/metadata/42": {
            "MediaContainer": {"Metadata": [{
                "viewOffset": 125000,
                "Media": [{"Part": [{
                    "key": "/library/parts/100/file.mkv",
                    "Stream": [
                        {"streamType": 2, "key": "/library/streams/audio"},
                        {"streamType": 3, "key": "/library/streams/500"},
                        {"streamType": 3, "key": "https://attacker.invalid/subtitle"},
                    ],
                }]}],
            }]}
        }})
        part, resume, duration, subtitles = plex.playback_metadata(client, "42")
        self.assertEqual(part, "/library/parts/100/file.mkv")
        self.assertEqual(resume, 125)
        self.assertEqual(duration, 0)
        self.assertEqual(subtitles, ["/library/streams/500"])
        secret = "TokenThatMustStayPrivate"
        args = plex.mpv_arguments(
            "windowed",
            "http://127.0.0.1:32100/stream/random",
            resume,
            ["http://127.0.0.1:32100/subtitle/random/0"],
        )
        self.assertNotIn(secret, " ".join(args))
        self.assertIn("--autofit=960x540", args)
        self.assertIn("--osc=yes", args)
        self.assertIn("--input-default-bindings=yes", args)
        self.assertIn("--sub-file=http://127.0.0.1:32100/subtitle/random/0", args)
        fullscreen = plex.mpv_arguments("fullscreen", "http://127.0.0.1:32100/stream/random", 0)
        self.assertIn("--fullscreen", fullscreen)
        self.assertIn("--wayland-app-id=" + plex.PLUGIN_ID + ".player", fullscreen)
        geometry = {
            "schemaVersion": 1, "x": 2100, "y": 1300,
            "width": 1120, "height": 630,
        }
        restored = plex.mpv_arguments(
            "windowed", "http://127.0.0.1:32100/stream/random", 0,
            window_geometry=geometry,
        )
        self.assertIn("--geometry=1120x630", restored)
        self.assertNotIn("--autofit=960x540", restored)
        geometry_script = plex.hypr_geometry_script(12345, geometry)
        self.assertIn("w.pid == 12345", geometry_script)
        self.assertIn("resize({ x = 1120, y = 630, relative = false, window = target })", geometry_script)
        self.assertIn("move({ x = 2100, y = 1300, relative = false, window = target })", geometry_script)
        script = plex.hypr_fullscreen_script(12345)
        self.assertIn("w.pid == 12345", script)
        self.assertIn("fullscreen_state({ internal = 2, client = 2 })", script)
        self.assertNotIn(secret, script)
        timeline = plex.timeline_path("42", 125000, 300000, "playing")
        self.assertTrue(timeline.startswith("/:/timeline?"))
        self.assertIn("ratingKey=42", timeline)
        self.assertIn("time=125000", timeline)
        reporter = mock.Mock()
        plex.report_timeline(reporter, "42", 125000, 300000, "paused")
        reporter.request_empty.assert_called_once()
        self.assertIn("state=paused", reporter.request_empty.call_args.args[0])
        plex.mark_watched(reporter, "42")
        self.assertTrue(reporter.request_empty.call_args.args[0].startswith("/:/scrobble?"))

    def test_watch_state_updates_use_scrobble_endpoints_and_validate_inputs(self):
        client = mock.Mock()
        plex.set_watch_state(client, "42", "watched")
        self.assertTrue(client.request_empty.call_args.args[0].startswith("/:/scrobble?"))
        self.assertIn("key=42", client.request_empty.call_args.args[0])

        plex.set_watch_state(client, "42", "unwatched")
        self.assertTrue(client.request_empty.call_args.args[0].startswith("/:/unscrobble?"))

        with self.assertRaises(plex.ConfigurationError):
            plex.set_watch_state(client, "../42", "watched")
        with self.assertRaises(plex.ConfigurationError):
            plex.set_watch_state(client, "42", "maybe")

        args = plex.parser().parse_args([
            "mark", "--rating-key", "42", "--state", "unwatched",
        ])
        self.assertEqual((args.command, args.rating_key, args.state), ("mark", "42", "unwatched"))

        command_client = mock.Mock()
        with mock.patch.object(plex, "client_from_saved", return_value=(command_client, {})):
            self.assertEqual(plex.main([
                "mark", "--rating-key", "42", "--state", "watched",
            ]), 0)
        command_client.request_empty.assert_called_once()
        self.assertTrue(command_client.request_empty.call_args.args[0].startswith("/:/scrobble?"))

    def test_player_geometry_is_private_bounded_and_read_from_own_pid(self):
        geometry = {
            "schemaVersion": 1, "x": -20, "y": 140,
            "width": 960, "height": 540,
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(Path(directory) / "config"),
        }, clear=False):
            plex.save_window_geometry(geometry)
            path = plex.config_home() / "player-window.json"
            self.assertEqual(plex.load_window_geometry(), geometry)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        clients = [{
            "pid": 12345, "mapped": True, "floating": True, "fullscreen": 0,
            "at": [2100, 1300], "size": [1120, 630],
            "title": "Untrusted title that is ignored",
        }]
        with mock.patch.object(
            plex, "run_bounded_output",
            return_value=(0, json.dumps(clients).encode("utf-8")),
        ) as command:
            self.assertEqual(plex.read_hypr_geometry(12345), {
                "schemaVersion": 1, "x": 2100, "y": 1300,
                "width": 1120, "height": 630,
            })
        command.assert_called_once_with(
            ["hyprctl", "-j", "clients"], maximum=plex.MAX_HYPR_BYTES, timeout=2
        )
        monitors = [{"x": 2048, "y": 1224, "width": 1920, "height": 1080}]
        with mock.patch.object(
            plex, "run_bounded_output",
            return_value=(0, json.dumps(monitors).encode("utf-8")),
        ):
            self.assertTrue(plex.geometry_is_visible({
                "schemaVersion": 1, "x": 2100, "y": 1300,
                "width": 1120, "height": 630,
            }))
            self.assertFalse(plex.geometry_is_visible({
                "schemaVersion": 1, "x": 90000, "y": 90000,
                "width": 1120, "height": 630,
            }))
        with self.assertRaises(plex.ResponseError):
            plex.validate_window_geometry({
                "schemaVersion": 1, "x": 0, "y": 0,
                "width": 1000000, "height": 540,
            })

    def test_saved_status_keeps_last_successful_items(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {
                "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                "XDG_CACHE_HOME": str(Path(directory) / "cache"),
            }, clear=False):
                plex.save_config({
                    "schemaVersion": 1,
                    "server": "http://plex:32400",
                    "movieSectionIds": ["3"],
                    "tvSectionIds": ["2"],
                    "machineIdentifier": "server-id",
                })
                snapshot = {
                    "schemaVersion": 1,
                    "configured": True,
                    "sourceState": "updated",
                    "stale": False,
                    "items": [{
                        "ratingKey": "20", "kind": "movie", "title": "Saved",
                        "subtitle": "Movie", "addedAt": plex.isoformat(NOW),
                        "addedLabel": "Today", "watchState": "unwatched", "isNew": True,
                        "playbackRatingKey": "20", "playbackHint": "",
                    }],
                    "newCount": 1,
                    "lastSuccessAt": plex.isoformat(NOW),
                    "error": "",
                }
                plex.save_snapshot(snapshot)
                status_doc = plex.status_document()
                self.assertEqual(status_doc["items"][0]["title"], "Saved")
                self.assertEqual(status_doc["newCount"], 1)


if __name__ == "__main__":
    unittest.main()
