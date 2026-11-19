"""Test loop/fetch.py voi Drive gia, khong goi mang.

Chay tu goc repo:  python -m unittest loop.test_fetch -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.append(str(SCRIPTS))

import studio_store as ss  # noqa: E402
from test_studio_store import FakeDrive  # noqa: E402

from loop import fetch as lf  # noqa: E402

HEADER = ("video_id,captured_at_utc,age_hours,impressions,ctr,views,unique_viewers,"
          "avd_s,apv_pct,ret_30s,ret_60s,src_browse,test_running\n")


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.drive = FakeDrive()
        self.f = ss.studio_folders(self.drive, "CHAN")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "cache"

    def put(self, name, folder, text):
        return self.drive._add(name, [folder], "text/plain", text.encode("utf-8"))

    def day(self, name="2026-11-19.csv", rows=None):
        rows = rows if rows is not None else [
            "v1,2026-11-19T10:00:00Z,30.5,4000,0.041,160,120,55.5,40.2,0.71,,0.3,True\n"]
        return self.put(name, self.f["snapshots"], HEADER + "".join(rows))

    def test_first_fetch_downloads_everything(self):
        self.day()
        self.put("a1.json", self.f["actions_done"],
                 json.dumps({"requested_utc": "2026-11-19T02:00:00Z"}))
        self.put("a2.json", self.f["actions_failed"],
                 json.dumps({"requested_utc": "2026-11-19T01:00:00Z"}))
        self.put("p1.json", self.f["actions_pending"], "{}")
        self.put("thumbnail-features.csv", self.f["studio"], "a,b\n1,2\n")
        self.put("health.json", self.f["studio"], json.dumps({"status": "ok"}))
        res = lf.fetch(self.drive, "CHAN", self.cache)
        self.assertEqual(res, {"snapshots_downloaded": 1, "actions_downloaded": 2,
                               "actions_unreadable": 0,
                               "pending": 1, "health_status": "ok"})
        self.assertEqual((self.cache / "actions" / "pending.txt").read_text().split(), ["p1.json"])
        self.assertTrue((self.cache / "thumbnail-features.csv").exists())
        acts = lf.load_actions(self.cache)
        self.assertEqual([a["_state"] for a in acts], ["failed", "done"])

    def test_incremental_skips_unchanged(self):
        self.day()
        self.put("a1.json", self.f["actions_done"], "{}")
        lf.fetch(self.drive, "CHAN", self.cache)
        res = lf.fetch(self.drive, "CHAN", self.cache)
        self.assertEqual(res["snapshots_downloaded"], 0)
        self.assertEqual(res["actions_downloaded"], 0)

    def test_redownloads_changed_day(self):
        fid = self.day()
        lf.fetch(self.drive, "CHAN", self.cache)
        self.drive.items[fid]["data"] = (HEADER + "v1,2026-11-19T11:00:00Z,31,5000,0.05,1,1,1,1,,,,0\n").encode()
        self.drive.items[fid]["modifiedTime"] = "2026-11-19T23:59:59Z"
        res = lf.fetch(self.drive, "CHAN", self.cache)
        self.assertEqual(res["snapshots_downloaded"], 1)
        self.assertEqual(lf.load_snapshots(self.cache)[0]["impressions"], 5000)

    def test_load_snapshots_types(self):
        self.day(rows=[
            "v1,2026-11-19T10:00:00Z,30.5,4000,0.041,160,120,55.5,40.2,0.71,,0.3,True\n",
            "v0,2026-11-19T09:00:00Z,,,,,,,,,,,\n"])
        lf.fetch(self.drive, "CHAN", self.cache)
        rows = lf.load_snapshots(self.cache)
        self.assertEqual([r["video_id"] for r in rows], ["v0", "v1"])  # sap theo captured_at_utc
        r = rows[1]
        self.assertIsInstance(r["impressions"], int)
        self.assertEqual(r["impressions"], 4000)
        self.assertIsInstance(r["views"], int)
        self.assertEqual(r["age_hours"], 30.5)
        self.assertEqual(r["ctr"], 0.041)
        self.assertEqual(r["ret_30s"], 0.71)
        self.assertIsNone(r["ret_60s"])
        self.assertEqual(r["src_browse"], 0.3)
        self.assertIs(r["test_running"], True)
        e = rows[0]
        for k in ("impressions", "ctr", "age_hours", "ret_30s", "src_browse", "test_running"):
            self.assertIsNone(e[k], k)

    def test_health_fail_surfaces_in_counts(self):
        self.put("health.json", self.f["studio"], json.dumps({"status": "fail"}))
        self.assertEqual(lf.fetch(self.drive, "CHAN", self.cache)["health_status"], "fail")

    def test_partial_action_file_is_refetched_or_skipped(self):
        self.put("a1.json", self.f["actions_done"], json.dumps({"requested_utc": "x"}))
        self.put("bad.json", self.f["actions_failed"], '{"requested_')  # hong ngay tren Drive
        partial = self.cache / "actions" / "done" / "a1.json"
        partial.parent.mkdir(parents=True)
        partial.write_text('{"requested_', encoding="utf-8")  # file cut do crash
        res = lf.fetch(self.drive, "CHAN", self.cache)
        self.assertEqual(res["actions_downloaded"], 2)       # a1 duoc tai lai
        self.assertEqual(res["actions_unreadable"], 1)       # bad.json van hong
        acts = lf.load_actions(self.cache)
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["_state"], "done")
        self.assertFalse(list(self.cache.rglob("*.tmp")))

    def test_health_corrupt(self):
        self.put("health.json", self.f["studio"], '{"stat')
        self.assertEqual(lf.fetch(self.drive, "CHAN", self.cache)["health_status"], "corrupt")
        self.drive.items[next(i for i, f in self.drive.items.items()
                              if f["name"] == "health.json")]["data"] = b"{}"
        self.drive.items[next(i for i, f in self.drive.items.items()
                              if f["name"] == "health.json")]["modifiedTime"] = "2026-12-01T00:00:00Z"
        self.assertEqual(lf.fetch(self.drive, "CHAN", self.cache)["health_status"], "corrupt")

    def test_health_non_dict(self):
        self.put("health.json", self.f["studio"], "[1, 2]")
        self.assertEqual(lf.fetch(self.drive, "CHAN", self.cache)["health_status"], "corrupt")

    def test_health_missing(self):
        self.assertEqual(lf.fetch(self.drive, "CHAN", self.cache)["health_status"], "missing")


if __name__ == "__main__":
    unittest.main()
