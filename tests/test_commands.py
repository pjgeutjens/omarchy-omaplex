from __future__ import annotations

import unittest

from omaplex.commands import scan_libraries
from tests.support import FakeClient


class PlexHelperTests(unittest.TestCase):
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
