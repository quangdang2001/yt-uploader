#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test yeu cau doi TIEU DE cua studio_actions.py (chu kenh 2026-11-24: "build the same way to update the title also").

    python -m unittest test_studio_actions_title
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from googleapiclient.errors import HttpError  # noqa: E402

import studio_actions as sa  # noqa: E402
import studio_store as ss  # noqa: E402
import youtube_uploader as yu  # noqa: E402
from test_studio_store import FakeDrive  # noqa: E402


class _Resp:
    def __init__(self, status):
        self.status = status
        self.reason = "x"


def http_error(status, body=b"{}"):
    return HttpError(_Resp(status), body)


def fake_download_to(drive, file_id, dest, label):
    Path(dest).write_bytes(drive.items[file_id]["data"])


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeTitleYouTube:
    """videos().list / videos().update over one in-memory snippet per video."""

    def __init__(self, snippets):
        self.snippets = {k: dict(v) for k, v in snippets.items()}
        self.updates: list = []
        self.update_error = None
        self.drop_on_update = ()          # fields YouTube "forgets" on write (simulated)

    def videos(self):
        return self

    def list(self, part, id):
        assert part == "snippet"
        found = id in self.snippets
        return _Call(lambda: {"items": [{"id": id, "snippet": dict(self.snippets[id])}] if found else []})

    def update(self, part, body):
        def run():
            if self.update_error is not None:
                raise self.update_error
            self.updates.append((part, json.loads(json.dumps(body))))
            snip = dict(body["snippet"])
            for k in self.drop_on_update:
                snip.pop(k, None)
            # like the real API: a mutable property left out of the body is deleted
            self.snippets[body["id"]] = snip
            return {"id": body["id"], "snippet": snip}
        return _Call(run)


SNIP = {"title": "Old Title $499", "description": "Line one\nLine two", "tags": ["ruger", "rifle"],
        "categoryId": "27", "defaultLanguage": "en", "defaultAudioLanguage": "en",
        "channelId": "UCxxx", "publishedAt": "2026-11-20T12:00:00Z", "thumbnails": {"default": {"url": "u"}},
        "channelTitle": "Bare Skull", "liveBroadcastContent": "none", "localized": {"title": "Old Title $499"}}


class TitleActionsTest(unittest.TestCase):
    def setUp(self):
        self.drive = FakeDrive()
        self.folders = ss.studio_folders(self.drive, "CHAN")
        self.done = self.drive._add("done", ["CHAN"], "application/vnd.google-apps.folder")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.yt = FakeTitleYouTube({"vid001": SNIP})
        self.sent: list = []
        self.thumbs: list = []
        self.now = datetime(2026, 11, 19, 2, 0, tzinfo=timezone.utc)
        for p in (mock.patch.object(yu, "notify", self.sent.append),
                  mock.patch.object(yu, "set_thumbnail", lambda *a: self.thumbs.append(a)),
                  mock.patch.object(yu, "download_to", fake_download_to),
                  mock.patch.object(sa, "_now", lambda: self.now)):
            p.start()
            self.addCleanup(p.stop)

    def request(self, name="vid001__20261119T010000Z.json", **over):
        req = {"kind": "title", "video_id": "vid001", "base": "b1", "to_title_variant": "alt1",
               "to_title": "New Title $499", "expect_title": "Old Title $499", "from_title_variant": "main",
               "reason": "test", "requested_utc": "2026-11-19T01:00:00Z"}
        req.update(over)
        req = {k: v for k, v in req.items() if v is not None}
        return self.drive._add(name, [self.folders["actions_pending"]], "application/json", json.dumps(req).encode())

    def run_it(self):
        return sa.run_actions(self.drive, self.yt, "CHAN", self.done, Path(self.tmp.name))

    def result_of(self, fid):
        return json.loads(self.drive.items[fid]["data"])["result"]

    def test_title_is_set_and_everything_else_in_the_snippet_survives(self):
        fid = self.request()
        self.assertEqual(self.run_it(), (1, 0))
        part, body = self.yt.updates[0]
        self.assertEqual(part, "snippet")                                  # never the status part
        self.assertEqual(body, {"id": "vid001", "snippet": {
            "title": "New Title $499", "description": "Line one\nLine two", "tags": ["ruger", "rifle"],
            "categoryId": "27", "defaultLanguage": "en", "defaultAudioLanguage": "en"}})
        self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_done"]])
        res = self.result_of(fid)
        self.assertEqual(res["status"], "ok")
        self.assertIn("New Title $499", res["detail"])
        self.assertNotIn("WARNING", res["detail"])
        self.assertEqual(self.sent, ["Title vid001 -> alt1: ok"])
        self.assertEqual(self.thumbs, [])                                  # a title request never touches the thumbnail

    def test_owner_changed_the_title_since_the_request(self):
        self.yt.snippets["vid001"]["title"] = "Owner's Own Title"
        fid = self.request()
        self.assertEqual(self.run_it(), (0, 1))
        self.assertEqual(self.yt.updates, [])
        self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_failed"]])
        self.assertIn("title changed since request", self.result_of(fid)["detail"])

    def test_already_set_is_ok_without_a_second_write(self):
        self.yt.snippets["vid001"]["title"] = "New Title $499"         # e.g. the write landed, the connection dropped
        fid = self.request()
        self.assertEqual(self.run_it(), (1, 0))
        self.assertEqual(self.yt.updates, [])
        self.assertIn("already", self.result_of(fid)["detail"])

    def test_bad_title_requests_are_refused_before_any_call(self):
        cases = [({"to_title": ""}, "to_title"), ({"to_title": "x" * 101}, "100"),
                 ({"to_title": "Buy at http://a.b"}, "link"), ({"to_title": "A <b> title"}, "<"),
                 ({"expect_title": None}, "expect_title"), ({"to_title_variant": "alt9"}, "to_title_variant"),
                 ({"to_variant": "alt1"}, "to_variant"), ({"video_id": "bad id!"}, "video_id"),
                 ({"kind": "banner"}, "kind")]
        for i, (over, word) in enumerate(cases):
            with self.subTest(over=over):
                fid = self.request(name=f"vid001__2026111{i}T010000Z.json", **over)
                self.run_it()
                self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_failed"]])
                self.assertIn(word, self.result_of(fid)["detail"])
        self.assertEqual(self.yt.updates, [])
        self.assertEqual(self.thumbs, [])

    def test_video_not_found_and_missing_category_fail(self):
        fid = self.request(video_id="vid404")
        self.assertEqual(self.run_it(), (0, 1))
        self.assertIn("not found", self.result_of(fid)["detail"])
        del self.yt.snippets["vid001"]["categoryId"]
        fid = self.request(name="vid001__20261119T013000Z.json")
        self.assertEqual(self.run_it(), (0, 1))
        self.assertIn("categoryId", self.result_of(fid)["detail"])
        self.assertEqual(self.yt.updates, [])

    def test_write_that_loses_a_field_is_reported(self):
        self.yt.drop_on_update = ("tags",)
        fid = self.request()
        self.assertEqual(self.run_it(), (1, 0))                            # the title did change: the loop must know
        self.assertIn("WARNING", self.result_of(fid)["detail"])
        self.assertIn("tags", self.result_of(fid)["detail"])
        self.assertTrue(any("tags" in m for m in self.sent))

    def test_stale_read_right_after_the_write_is_not_a_warning(self):
        # 2026-11-24, live: videos.list straight after videos.update still returned the old title 4 times out of 7
        real_update = self.yt.update

        def update_then_serve_stale(part, body):
            call = real_update(part, body)

            def run():
                old = dict(self.yt.snippets[body["id"]])
                resp = call.execute()
                self.yt.snippets[body["id"]] = old            # later reads see the pre-write snippet
                return resp
            return _Call(run)
        self.yt.update = update_then_serve_stale
        fid = self.request()
        self.assertEqual(self.run_it(), (1, 0))
        self.assertNotIn("WARNING", self.result_of(fid)["detail"])
        self.assertEqual(self.sent, ["Title vid001 -> alt1: ok"])

    def test_update_error_moves_to_failed_and_quota_leaves_pending(self):
        self.yt.update_error = http_error(400, b'{"error": {"errors": [{"reason": "invalidTitle"}]}}')
        fid = self.request()
        self.assertEqual(self.run_it(), (0, 1))
        self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_failed"]])
        self.yt.update_error = http_error(403, b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}')
        fid = self.request(name="vid001__20261119T013000Z.json")
        self.assertEqual(self.run_it(), (0, 0))
        self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_pending"]])

    def test_stale_title_request_is_not_executed(self):
        fid = self.request(requested_utc="2026-11-17T01:00:00Z")
        self.assertEqual(self.run_it(), (0, 1))
        self.assertEqual(self.yt.updates, [])
        self.assertIn("stale", self.result_of(fid)["detail"])

    def test_thumbnail_request_without_kind_still_works(self):
        self.drive._add("b1.alt1.jpg", [self.folders["thumb_alts"]], "image/jpeg", b"ALT1")
        req = {"video_id": "vid001", "base": "b1", "to_variant": "alt1", "requested_utc": "2026-11-19T01:00:00Z"}
        self.drive._add("vid001__20261119T010000Z.json", [self.folders["actions_pending"]], "application/json",
                        json.dumps(req).encode())
        self.assertEqual(self.run_it(), (1, 0))
        self.assertEqual(len(self.thumbs), 1)
        self.assertEqual(self.yt.updates, [])


if __name__ == "__main__":
    unittest.main()
