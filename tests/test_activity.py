from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from omaplex.activity import recent_snapshot, save_snapshot
from omaplex.common import (
    ResponseError,
    atomic_json_write,
    isoformat,
    read_regular_file,
)
from omaplex.config import save_config
from omaplex.connection import status_document
from omaplex.media_items import normalize_media_item
from tests.support import NOW, FakeClient, container


class PlexHelperTests(unittest.TestCase):
    def test_normalizes_movie_and_watch_states(self):
        movie = normalize_media_item(
            {
                "type": "movie",
                "ratingKey": "20",
                "title": "Dune: Part Two",
                "year": 2024,
                "addedAt": int((NOW - timedelta(days=2)).timestamp()),
                "viewOffset": 120,
            },
            NOW,
        )
        self.assertEqual(movie["kind"], "movie")
        self.assertEqual(movie["subtitle"], "Movie · 2024")
        self.assertEqual(movie["watchState"], "started")
        self.assertFalse(movie["isNew"])

        watched = normalize_media_item(
            {
                "type": "movie",
                "ratingKey": "21",
                "title": "Arrival",
                "addedAt": int(NOW.timestamp()),
                "viewCount": 1,
            },
            NOW,
        )
        self.assertEqual(watched["watchState"], "watched")
        self.assertFalse(watched["isNew"])

    def test_merges_sorts_and_deduplicates_continue_watching(self):
        client = FakeClient(
            {
                "/hubs/home/continueWatching": container(
                    {
                        "type": "episode",
                        "ratingKey": "30",
                        "grandparentRatingKey": "6",
                        "grandparentTitle": "Continue show",
                        "parentIndex": 1,
                        "index": 3,
                        "title": "Episode",
                        "addedAt": int((NOW - timedelta(days=5)).timestamp()),
                        "lastViewedAt": int((NOW - timedelta(hours=2)).timestamp()),
                        "viewOffset": 30000,
                        "duration": 100000,
                    },
                    {
                        "type": "movie",
                        "ratingKey": "31",
                        "title": "More recent viewing",
                        "addedAt": int((NOW - timedelta(days=10)).timestamp()),
                        "lastViewedAt": int((NOW - timedelta(hours=1)).timestamp()),
                        "viewOffset": 10000,
                        "duration": 100000,
                    },
                ),
                "/library/onDeck": container(
                    {
                        "type": "episode",
                        "ratingKey": "30",
                        "grandparentRatingKey": "6",
                        "grandparentTitle": "Continue show",
                        "parentIndex": 1,
                        "index": 3,
                        "title": "Episode",
                        "addedAt": int((NOW - timedelta(days=5)).timestamp()),
                        "lastViewedAt": int((NOW - timedelta(hours=2)).timestamp()),
                        "viewOffset": 30000,
                        "duration": 100000,
                    },
                    {
                        "type": "episode",
                        "ratingKey": "9",
                        "grandparentRatingKey": "5",
                        "grandparentTitle": "On deck show",
                        "parentIndex": 2,
                        "index": 1,
                        "title": "Next",
                        "addedAt": int((NOW - timedelta(days=7)).timestamp()),
                        "lastViewedAt": int((NOW - timedelta(minutes=30)).timestamp()),
                    },
                ),
                "type=1": container(
                    {
                        "type": "movie",
                        "ratingKey": "20",
                        "title": "Movie",
                        "addedAt": int((NOW - timedelta(hours=2)).timestamp()),
                    }
                ),
                "type=4": container(
                    {
                        "type": "episode",
                        "ratingKey": "11",
                        "grandparentRatingKey": "5",
                        "grandparentTitle": "Silo",
                        "parentIndex": 2,
                        "index": 2,
                        "title": "Order",
                        "addedAt": int((NOW - timedelta(hours=1)).timestamp()),
                    },
                    {
                        "type": "episode",
                        "ratingKey": "10",
                        "grandparentRatingKey": "5",
                        "grandparentTitle": "Silo",
                        "parentIndex": 2,
                        "index": 1,
                        "title": "The Engineer",
                        "addedAt": int((NOW - timedelta(hours=3)).timestamp()),
                    },
                ),
            }
        )
        snapshot = recent_snapshot(
            client, {"movieSectionIds": ["3"], "tvSectionIds": ["2"]}, NOW
        )
        self.assertEqual(
            [item["title"] for item in snapshot["items"]], ["Silo", "Movie"]
        )
        self.assertEqual(snapshot["items"][0]["subtitle"], "Show · S02E02 · Order")
        self.assertEqual(snapshot["items"][0]["playbackRatingKey"], "9")
        self.assertEqual(snapshot["items"][0]["playbackHint"], "Next S02E01")
        self.assertEqual(
            [item["ratingKey"] for item in snapshot["continueItems"]],
            ["9", "31", "30"],
        )
        self.assertEqual(snapshot["continueItems"][0]["addedLabel"], "Played 30m ago")
        self.assertEqual(snapshot["continueItems"][0]["playbackHint"], "Next episode")
        self.assertEqual(snapshot["continueItems"][1]["addedLabel"], "Played 1h ago")
        self.assertEqual(snapshot["continueItems"][2]["playbackHint"], "Resume 30%")
        self.assertEqual(len(snapshot["movieItems"]), 1)
        self.assertEqual(len(snapshot["seriesItems"]), 1)
        self.assertEqual(snapshot["newCount"], 2)

    def test_new_requires_unwatched_and_thirty_day_window(self):
        recent = normalize_media_item(
            {
                "type": "movie",
                "ratingKey": "1",
                "title": "Recent",
                "addedAt": int((NOW - timedelta(days=30)).timestamp()),
            },
            NOW,
        )
        old = normalize_media_item(
            {
                "type": "movie",
                "ratingKey": "2",
                "title": "Old",
                "addedAt": int((NOW - timedelta(days=31)).timestamp()),
            },
            NOW,
        )
        self.assertTrue(recent["isNew"])
        self.assertFalse(old["isNew"])

    def test_unfinished_items_precede_newer_watched_items(self):
        client = FakeClient(
            {
                "/hubs/home/continueWatching": container(),
                "/library/onDeck": container(),
                "type=1": container(
                    {
                        "type": "movie",
                        "ratingKey": "1",
                        "title": "Newest watched",
                        "addedAt": int(NOW.timestamp()),
                        "viewCount": 1,
                    },
                    {
                        "type": "movie",
                        "ratingKey": "2",
                        "title": "Older unwatched",
                        "addedAt": int((NOW - timedelta(days=1)).timestamp()),
                    },
                    {
                        "type": "movie",
                        "ratingKey": "3",
                        "title": "Oldest started",
                        "addedAt": int((NOW - timedelta(days=2)).timestamp()),
                        "viewOffset": 1000,
                    },
                ),
            }
        )
        snapshot = recent_snapshot(
            client, {"movieSectionIds": ["3"], "tvSectionIds": []}, NOW
        )
        self.assertEqual(
            [item["watchState"] for item in snapshot["movieItems"]],
            ["started", "unwatched", "watched"],
        )

    def test_cache_is_atomic_private_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "recent.json"
            atomic_json_write(target, {"ok": True}, 1024)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")), {"ok": True}
            )
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            link = Path(directory) / "link.json"
            link.symlink_to(target)
            with self.assertRaises(ResponseError):
                read_regular_file(link, 1024)

    def test_saved_status_keeps_last_successful_items(self):
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
            snapshot = {
                "schemaVersion": 1,
                "configured": True,
                "sourceState": "updated",
                "stale": False,
                "items": [
                    {
                        "ratingKey": "20",
                        "kind": "movie",
                        "title": "Saved",
                        "subtitle": "Movie",
                        "addedAt": isoformat(NOW),
                        "addedLabel": "Today",
                        "watchState": "unwatched",
                        "isNew": True,
                        "playbackRatingKey": "20",
                        "playbackHint": "",
                    }
                ],
                "continueItems": [],
                "movieItems": [],
                "seriesItems": [],
                "newCount": 1,
                "lastSuccessAt": isoformat(NOW),
                "error": "",
            }
            save_snapshot(snapshot)
            status_doc = status_document()
            self.assertEqual(status_doc["items"][0]["title"], "Saved")
            self.assertEqual(status_doc["newCount"], 1)
