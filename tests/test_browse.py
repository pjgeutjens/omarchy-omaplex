from __future__ import annotations

import unittest
from unittest import mock

from omaplex import browse as browse_module
from omaplex.browse import BrowseKind, SearchScope, browse_document
from tests.support import NOW, FakeClient, container


class PlexHelperTests(unittest.TestCase):
    def test_browse_normalizes_shows_and_pages(self):
            client = FakeClient(
                {
                    "/library/sections/2/all": {
                        "MediaContainer": {
                            "totalSize": 1,
                            "Metadata": [
                                {
                                    "type": "show",
                                    "ratingKey": "50",
                                    "title": "Series",
                                    "leafCount": 10,
                                    "viewedLeafCount": 3,
                                    "addedAt": int(NOW.timestamp()),
                                }
                            ],
                        }
                    }
                }
            )
            document = browse_document(
                client,
                {"movieSectionIds": [], "tvSectionIds": ["2"]},
                BrowseKind.SHOWS,
                "",
                0,
                40,
            )
            self.assertEqual(document["total"], 1)
            self.assertEqual(document["items"][0]["watchState"], "started")
            self.assertFalse(document["items"][0]["playable"])

    def test_fuzzy_search_respects_scope_and_expands_season_codes(self):
            client = FakeClient(
                {
                    "/library/sections/3/all?": container(
                        {
                            "type": "movie",
                            "ratingKey": "40",
                            "title": "Alone Together",
                            "addedAt": int(NOW.timestamp()),
                        }
                    ),
                    "/library/sections/2/all?": container(
                        {
                            "type": "show",
                            "ratingKey": "50",
                            "title": "Alone",
                            "leafCount": 3,
                            "addedAt": int(NOW.timestamp()),
                        }
                    ),
                    "/library/metadata/50/allLeaves?": container(
                        {
                            "type": "episode",
                            "ratingKey": "52",
                            "grandparentTitle": "Alone",
                            "parentIndex": 1,
                            "index": 2,
                            "title": "Second",
                            "addedAt": int(NOW.timestamp()),
                        },
                        {
                            "type": "episode",
                            "ratingKey": "51",
                            "grandparentTitle": "Alone",
                            "parentIndex": 1,
                            "index": 1,
                            "title": "First",
                            "addedAt": int(NOW.timestamp()),
                        },
                        {
                            "type": "episode",
                            "ratingKey": "53",
                            "grandparentTitle": "Alone",
                            "parentIndex": 2,
                            "index": 1,
                            "title": "Later",
                            "addedAt": int(NOW.timestamp()),
                        },
                    ),
                }
            )
            config = {"movieSectionIds": ["3"], "tvSectionIds": ["2"]}
            with mock.patch.object(browse_module.shutil, "which", return_value=None):
                movies = browse_document(
                    client,
                    config,
                    BrowseKind.SEARCH,
                    "Alne",
                    0,
                    40,
                    search_scope=SearchScope.MOVIES,
                )
                shows = browse_document(
                    client,
                    config,
                    BrowseKind.SEARCH,
                    "Alne",
                    0,
                    40,
                    search_scope=SearchScope.SHOWS,
                )
                season = browse_document(
                    client,
                    config,
                    BrowseKind.SEARCH,
                    "Alone S01E",
                    0,
                    40,
                    search_scope=SearchScope.SHOWS,
                )
                episode = browse_document(
                    client,
                    config,
                    BrowseKind.SEARCH,
                    "Alone S01E02",
                    0,
                    40,
                    search_scope=SearchScope.SHOWS,
                )
            self.assertEqual([item["kind"] for item in movies["items"]], ["movie"])
            self.assertEqual([item["kind"] for item in shows["items"]], ["show"])
            self.assertEqual(season["total"], 2)
            self.assertEqual([item["ratingKey"] for item in season["items"]], ["51", "52"])
            self.assertTrue(all("S01" in item["subtitle"] for item in season["items"]))
            self.assertTrue(all(item["playable"] for item in season["items"]))
            self.assertEqual([item["ratingKey"] for item in episode["items"]], ["52"])

    def test_episode_browser_filters_codes_titles_and_filtered_pages(self):
            client = FakeClient(
                {
                    "/library/metadata/50/allLeaves?": container(
                        {
                            "type": "episode",
                            "ratingKey": "401",
                            "grandparentTitle": "Test Series",
                            "parentIndex": 4,
                            "index": 1,
                            "title": "Fourth One",
                            "addedAt": int(NOW.timestamp()),
                        },
                        {
                            "type": "episode",
                            "ratingKey": "402",
                            "grandparentTitle": "Test Series",
                            "parentIndex": 4,
                            "index": 2,
                            "title": "Fourth Two",
                            "addedAt": int(NOW.timestamp()),
                        },
                        {
                            "type": "episode",
                            "ratingKey": "301",
                            "grandparentTitle": "Test Series",
                            "parentIndex": 3,
                            "index": 1,
                            "title": "Third One",
                            "addedAt": int(NOW.timestamp()),
                        },
                        {
                            "type": "episode",
                            "ratingKey": "302",
                            "grandparentTitle": "Test Series",
                            "parentIndex": 3,
                            "index": 2,
                            "title": "Searchable Target",
                            "addedAt": int(NOW.timestamp()),
                        },
                    ),
                }
            )
            config = {"movieSectionIds": [], "tvSectionIds": ["2"]}
            with mock.patch.object(browse_module.shutil, "which", return_value=None):
                episode = browse_document(
                    client, config, BrowseKind.EPISODES, "s04e01", 0, 40, "50"
                )
                season_page = browse_document(
                    client, config, BrowseKind.EPISODES, "S03E", 1, 1, "50"
                )
                title = browse_document(
                    client, config, BrowseKind.EPISODES, "srch trgt", 0, 40, "50"
                )
            self.assertEqual(episode["total"], 1)
            self.assertEqual([item["ratingKey"] for item in episode["items"]], ["401"])
            self.assertEqual(season_page["total"], 2)
            self.assertEqual([item["ratingKey"] for item in season_page["items"]], ["302"])
            self.assertEqual([item["ratingKey"] for item in title["items"]], ["302"])
            self.assertTrue(all("title.value" not in path for path in client.paths))
