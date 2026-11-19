#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit test cho studio_store.py — Drive gia trong bo nho, khong goi mang.

Chay:
    cd scripts
    python -m unittest test_studio_store -v
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import studio_store as ss  # noqa: E402

FOLDER_MIME = "application/vnd.google-apps.folder"


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeDrive:
    """Du cho cac call cua studio_store: list/create/update/get_media."""

    def __init__(self, page_size: int | None = None):
        self.items: dict[str, dict] = {}
        self.n = 0
        self.page_size = page_size
        self.update_calls: list[dict] = []

    def files(self):
        return self

    def _add(self, name, parents, mime, data=b""):
        self.n += 1
        fid = f"id{self.n}"
        self.items[fid] = {"id": fid, "name": name, "parents": list(parents),
                           "mimeType": mime, "data": data, "trashed": False,
                           "modifiedTime": f"2026-11-19T00:00:{self.n:02d}Z"}
        return fid

    @staticmethod
    def _bytes(media):
        return media.getbytes(0, media.size()) if media is not None else b""

    def list(self, q, pageSize=100, pageToken=None, **kw):
        def run():
            parent = re.search(r"'([^']+)' in parents", q).group(1)
            m = re.search(r"name = '([^']*)'", q)
            hits = [f for f in self.items.values()
                    if parent in f["parents"] and not f["trashed"]
                    and (not m or f["name"] == m.group(1))
                    and ("mimeType" not in q or f["mimeType"] == FOLDER_MIME)]
            hits.sort(key=lambda f: f["name"])
            start = int(pageToken or 0)
            size = self.page_size or pageSize
            page = hits[start:start + size]
            res = {"files": [{k: f[k] for k in ("id", "name", "modifiedTime")} for f in page]}
            if start + size < len(hits):
                res["nextPageToken"] = str(start + size)
            return res
        return _Exec(run)

    def create(self, body, media_body=None, **kw):
        def run():
            fid = self._add(body["name"], body.get("parents", []),
                            body.get("mimeType", ""), self._bytes(media_body))
            return {"id": fid}
        return _Exec(run)

    def update(self, fileId, body=None, media_body=None, addParents=None,
               removeParents=None, **kw):
        def run():
            self.update_calls.append({"fileId": fileId, "addParents": addParents,
                                      "removeParents": removeParents,
                                      "has_media": media_body is not None})
            f = self.items[fileId]
            if media_body is not None:
                f["data"] = self._bytes(media_body)
            if addParents:
                f["parents"] = [p for p in f["parents"] if p != removeParents] + [addParents]
            return {"id": fileId}
        return _Exec(run)

    def get_media(self, fileId, **kw):
        return _Exec(lambda: self.items[fileId]["data"])

    def named(self, name, parent):
        return [f for f in self.items.values()
                if f["name"] == name and parent in f["parents"]]


class StudioStoreTest(unittest.TestCase):
    def setUp(self):
        self.drive = FakeDrive()
        self.folders = ss.studio_folders(self.drive, "CHAN")

    def test_folders_created_once(self):
        before = len(self.drive.items)
        again = ss.studio_folders(self.drive, "CHAN")
        self.assertEqual(len(self.drive.items), before)
        self.assertEqual(again, self.folders)
        self.assertEqual(set(self.folders), {
            "studio", "snapshots", "raw", "thumb_alts",
            "actions_pending", "actions_done", "actions_failed"})
        # studio + snapshots + raw + thumb-alts + actions + 3 con = 8 folder
        self.assertEqual(before, 8)
        actions = self.drive.items[self.folders["actions_pending"]]["parents"][0]
        self.assertEqual(self.drive.items[actions]["name"], "actions")
        self.assertEqual(self.drive.items[self.folders["actions_done"]]["parents"], [actions])

    def test_snapshot_header_once(self):
        cols = ("video_id", "views")
        ss.append_snapshot_rows(self.drive, self.folders, "2026-11-19", cols,
                                [{"video_id": "a", "views": 1}])
        ss.append_snapshot_rows(self.drive, self.folders, "2026-11-19", cols,
                                [{"video_id": "b", "views": 2}])
        f = self.drive.named("2026-11-19.csv", self.folders["snapshots"])
        self.assertEqual(len(f), 1)
        text = f[0]["data"].decode()
        self.assertEqual(text.count("video_id,views"), 1)
        self.assertTrue(text.startswith("video_id,views\n"))

    def test_existing_empty_csv_gets_header(self):
        cols = ("video_id", "views")
        self.drive._add("d.csv", [self.folders["snapshots"]], "text/csv", b"")
        ss.append_snapshot_rows(self.drive, self.folders, "d", cols,
                                [{"video_id": "a", "views": 1}])
        text = self.drive.named("d.csv", self.folders["snapshots"])[0]["data"].decode()
        self.assertEqual(text, "video_id,views\na,1\n")

    def test_snapshot_append_keeps_rows(self):
        cols = ("video_id", "title")
        ss.append_snapshot_rows(self.drive, self.folders, "d", cols,
                                [{"video_id": "a", "title": "x, \"y\""}])
        ss.append_snapshot_rows(self.drive, self.folders, "d", cols,
                                [{"video_id": "b", "title": "z"}, {"video_id": "c"}])
        text = self.drive.named("d.csv", self.folders["snapshots"])[0]["data"].decode()
        self.assertEqual(text.splitlines(), [
            "video_id,title", 'a,"x, ""y"""', "b,z", "c,"])

    def test_raw_is_gzip_json_roundtrip(self):
        payloads = [{"kind": "stats", "n": 1}, {"kind": "t", "s": "caf\u00e9"}]
        ss.put_raw(self.drive, self.folders, "2026-11-19", "vid1",
                   "20261119T010203Z", payloads)
        day = self.drive.named("2026-11-19", self.folders["raw"])
        self.assertEqual(len(day), 1)
        f = self.drive.named("vid1__20261119T010203Z.json.gz", day[0]["id"])
        self.assertEqual(len(f), 1)
        self.assertEqual(json.loads(gzip.decompress(f[0]["data"]).decode("utf-8")), payloads)
        ss.put_raw(self.drive, self.folders, "2026-11-19", "vid2", "t", [])
        self.assertEqual(len(self.drive.named("2026-11-19", self.folders["raw"])), 1)

    def test_health_json_fields(self):
        ss.write_health(self.drive, self.folders, "ok", "fine", 5, 1)
        ss.write_health(self.drive, self.folders, "degraded", "late", 4, 2)
        f = self.drive.named("health.json", self.folders["studio"])
        self.assertEqual(len(f), 1)
        h = json.loads(f[0]["data"].decode("utf-8"))
        self.assertEqual(set(h), {"status", "at_utc", "detail", "videos_read", "videos_failed"})
        self.assertEqual((h["status"], h["videos_read"], h["videos_failed"]),
                         ("degraded", 4, 2))
        self.assertRegex(h["at_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_list_files_since_name(self):
        d = FakeDrive(page_size=2)
        fid = "F"
        for n in ["c.json", "a.json", "e.json", "b.json", "d.json"]:
            d._add(n, [fid], "application/json")
        trashed = d._add("zz.json", [fid], "application/json")
        d.items[trashed]["trashed"] = True
        names = [f["name"] for f in ss.list_files(d, fid)]
        self.assertEqual(names, ["a.json", "b.json", "c.json", "d.json", "e.json"])
        after = ss.list_files(d, fid, since_name="c.json")
        self.assertEqual([f["name"] for f in after], ["d.json", "e.json"])
        self.assertEqual(set(after[0]), {"id", "name", "modifiedTime"})

    def test_move_json_updates_and_moves(self):
        fid = ss.put_json(self.drive, self.folders["actions_pending"], "r.json", {"a": 1})
        ss.move_json(self.drive, fid, self.folders["actions_pending"],
                     self.folders["actions_done"], {"a": 1, "result": "ok"})
        f = self.drive.items[fid]
        self.assertEqual(f["parents"], [self.folders["actions_done"]])
        self.assertEqual(json.loads(f["data"].decode()), {"a": 1, "result": "ok"})
        call = self.drive.update_calls[-1]
        self.assertTrue(call["has_media"])
        self.assertEqual(call["addParents"], self.folders["actions_done"])
        self.assertEqual(call["removeParents"], self.folders["actions_pending"])
        self.assertEqual(ss.read_text(self.drive, fid).count('"result"'), 1)


if __name__ == "__main__":
    unittest.main()
