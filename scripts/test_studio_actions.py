#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit test cho studio_actions.py — Drive gia trong bo nho, khong goi mang.

Chay:
    cd scripts
    python -m unittest test_studio_actions -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
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


class StudioActionsTest(unittest.TestCase):
    def setUp(self):
        self.drive = FakeDrive()
        self.folders = ss.studio_folders(self.drive, "CHAN")
        self.done = self.drive._add("done", ["CHAN"], "application/vnd.google-apps.folder")
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.set_calls: list = []
        self.set_error = None
        self.sent: list = []

        def fake_set(youtube, video_id, path):
            if self.set_error is not None:
                raise self.set_error
            self.set_calls.append((video_id, Path(path).read_bytes()))

        patches = [
            mock.patch.object(yu, "set_thumbnail", fake_set),
            mock.patch.object(yu, "notify", self.sent.append),
            mock.patch.object(yu, "download_to", fake_download_to),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def request(self, name="vid1__20261119T010000Z.json", **over):
        req = {"video_id": "vid1", "base": "b1", "to_variant": "alt1",
               "from_variant": "main", "reason": "test",
               "requested_utc": "2026-11-19T01:00:00Z"}
        req.update(over)
        req = {k: v for k, v in req.items() if v is not None}
        return self.drive._add(name, [self.folders["actions_pending"]],
                               "application/json", json.dumps(req).encode())

    def alt(self, name="b1.alt1.jpg", data=b"ALT1"):
        return self.drive._add(name, [self.folders["thumb_alts"]], "image/jpeg", data)

    def run_it(self):
        return sa.run_actions(self.drive, None, "CHAN", self.done, self.tmpdir)

    def result_of(self, fid):
        return json.loads(self.drive.items[fid]["data"])["result"]

    def test_ok_moves_to_done_with_result(self):
        self.alt()
        fid = self.request()
        self.assertEqual(self.run_it(), (1, 0))
        self.assertEqual(self.set_calls, [("vid1", b"ALT1")])
        self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_done"]])
        res = self.result_of(fid)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["at_utc"].endswith("Z"))
        self.assertEqual(self.sent, ["Swap vid1 -> alt1: ok"])

    def test_missing_alt_moves_to_failed(self):
        fid = self.request()
        self.assertEqual(self.run_it(), (0, 1))
        self.assertEqual(self.set_calls, [])
        self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_failed"]])
        res = self.result_of(fid)
        self.assertEqual(res["status"], "error")
        self.assertIn("b1.alt1.jpg", res["detail"])
        self.assertTrue(self.sent[0].startswith("Swap vid1 -> alt1: FAILED"))

    def test_set_error_moves_to_failed_and_notifies(self):
        self.alt()
        fid = self.request()
        self.set_error = http_error(500)
        self.assertEqual(self.run_it(), (0, 1))
        self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_failed"]])
        self.assertEqual(self.result_of(fid)["status"], "error")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("FAILED", self.sent[0])

    def test_invalid_request_failed(self):
        a = self.request(name="a.json", to_variant="alt9")
        b = self.request(name="b.json", video_id=None)
        c = self.request(name="c.json", base=None)
        self.assertEqual(self.run_it(), (0, 3))
        self.assertEqual(self.set_calls, [])
        for fid in (a, b, c):
            self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_failed"]])
            self.assertEqual(self.result_of(fid)["status"], "error")
        self.assertIn("to_variant", self.result_of(a)["detail"])
        self.assertIn("video_id", self.result_of(b)["detail"])
        self.assertIn("base", self.result_of(c)["detail"])

    def test_unparseable_json_failed(self):
        fid = self.drive._add("bad.json", [self.folders["actions_pending"]],
                              "application/json", b"{not json")
        self.assertEqual(self.run_it(), (0, 1))
        self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_failed"]])
        self.assertEqual(self.result_of(fid)["status"], "error")

    def test_main_uses_done_jpg(self):
        self.drive._add("b1.jpg", [self.done], "image/jpeg", b"MAIN")
        self.alt()  # alt1 co mat nhung khong duoc dung
        fid = self.request(to_variant="main")
        self.assertEqual(self.run_it(), (1, 0))
        self.assertEqual(self.set_calls, [("vid1", b"MAIN")])
        self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_done"]])

    def test_order_oldest_first(self):
        self.alt()
        # ten sap xep nguoc voi requested_utc: phai theo requested_utc
        self.request(name="a.json", video_id="newer", requested_utc="2026-11-19T05:00:00Z")
        self.request(name="b.json", video_id="older", requested_utc="2026-11-19T01:00:00Z")
        self.run_it()
        self.assertEqual([c[0] for c in self.set_calls], ["older", "newer"])

    def test_same_utc_tie_broken_by_name(self):
        self.alt()
        self.request(name="z.json", video_id="zed")
        self.request(name="a.json", video_id="aye")
        self.run_it()
        self.assertEqual([c[0] for c in self.set_calls], ["aye", "zed"])

    def test_quota_error_leaves_pending(self):
        self.alt()
        first = self.request(name="a.json", video_id="one", requested_utc="2026-11-19T01:00:00Z")
        second = self.request(name="b.json", video_id="two", requested_utc="2026-11-19T02:00:00Z")
        body = json.dumps({"error": {"errors": [{"reason": "quotaExceeded"}]}}).encode()
        self.set_error = http_error(403, body)
        self.assertEqual(self.run_it(), (0, 0))
        for fid in (first, second):
            self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_pending"]])
            self.assertNotIn(b"result", self.drive.items[fid]["data"])
        self.assertEqual(self.drive.update_calls, [])

    def test_move_failure_does_not_count_ok(self):
        self.alt()
        fid = self.request()
        with mock.patch.object(ss, "move_json", side_effect=http_error(500)):
            self.assertEqual(self.run_it(), (0, 1))
        self.assertEqual(self.drive.items[fid]["parents"], [self.folders["actions_pending"]])


if __name__ == "__main__":
    unittest.main()
