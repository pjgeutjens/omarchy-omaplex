from __future__ import annotations

from datetime import datetime, timezone

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.paths = []

    def request_json(self, path, **kwargs):
        self.paths.append(path)
        for needle, response in self.responses.items():
            if needle in path:
                return response
        raise AssertionError("unexpected path: " + path)

    def request_empty(self, path, **kwargs):
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
