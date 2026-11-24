"""Test loop/fetch.py voi Drive gia, khong goi mang.

Chay tu goc repo:  python -m unittest loop.test_fetch -v
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertEqual(res, {"snapshots_downloaded": 1, "channel_downloaded": 0, "actions_downloaded": 2,
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
        with self.assertLogs("loop.fetch", level="WARNING"):
            res = lf.fetch(self.drive, "CHAN", self.cache)
            acts = lf.load_actions(self.cache)
        self.assertEqual(res["actions_downloaded"], 2)       # a1 duoc tai lai
        self.assertEqual(res["actions_unreadable"], 1)       # bad.json van hong
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

    def test_history_downloaded_and_latest_row_wins(self):
        text = ("thoi_gian,kenh,ten_goc,title,video_id,url,trang_thai,ghi_chu\n"
                "t1,Bare Skull,gunA,T1,v1,u,ok,\n"
                "t2,Bare Skull,gunB,T2,,u,error,\n"
                "t3,Bare Skull,gunC,T3,v1,u,ok,\n"
                "t4,Bare Skull,gunD,T4,v2,u,ok,\n")
        self.put("upload-history.csv", "CHAN", text)
        lf.fetch(self.drive, "CHAN", self.cache)
        self.assertEqual(lf.load_history(self.cache), {"v1": "gunC", "v2": "gunD"})
        self.assertFalse(list(self.cache.rglob("*.tmp")))

    def test_pending_listed_before_done(self):
        order = []
        real = ss.list_files

        def spy(drive, folder_id, since_name=None):
            order.append(folder_id)
            return real(drive, folder_id, since_name)
        with mock.patch.object(ss, "list_files", spy):
            lf.fetch(self.drive, "CHAN", self.cache)
        self.assertLess(order.index(self.f["actions_pending"]), order.index(self.f["actions_done"]))
        self.assertLess(order.index(self.f["actions_pending"]), order.index(self.f["actions_failed"]))

    def test_history_missing_is_empty(self):
        lf.fetch(self.drive, "CHAN", self.cache)
        self.assertEqual(lf.load_history(self.cache), {})

    def test_bases_fall_back_to_trusted_ledger_rows_only(self):
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / "upload-history.csv").write_text("ten_goc,video_id\nfrom_history,v1\n", encoding="utf-8")
        rep = self.cache / "report"
        rep.mkdir()
        (rep / "ledger.csv").write_text(
            "video_id,slug,match_source\n"
            "v1,ledger_says_other,id-in-md\n"       # the history wins
            "v2,by_id,id-in-md\n"
            "v3,by_title,title\n"
            "v4,old_guess,videos-json\n"            # not an exact source: stays unmapped
            "v5,,none\n"
            ",no_video,title\n", encoding="utf-8")
        self.assertEqual(lf.load_bases(self.cache, rep), {"v1": "from_history", "v2": "by_id", "v3": "by_title"})
        self.assertEqual(lf.load_bases(self.cache), {"v1": "from_history"})                  # no report dir
        self.assertEqual(lf.load_bases(self.cache, self.cache / "nowhere"), {"v1": "from_history"})

    def test_by_id_reads_history_from_studio_and_never_touches_channel(self):
        drive = FakeDrive()
        studio = drive._add("studio", ["SHARED"], "application/vnd.google-apps.folder")
        drive._add("upload-history.csv", [studio], "text/csv",
                   b"ten_goc,video_id\ngunA,v1\n")
        drive._add("upload-history.csv", ["CHAN"], "text/csv", b"ten_goc,video_id\nOLD,v9\n")
        n = len(drive.items)
        with mock.patch.object(ss, "studio_folders", side_effect=AssertionError("no name mode")):
            lf.fetch(drive, None, self.cache, studio_id=studio)
        self.assertEqual(lf.load_history(self.cache), {"v1": "gunA"})
        self.assertEqual([f["name"] for f in drive.items.values() if "SHARED" in f["parents"]], ["studio"])
        self.assertEqual(len(drive.items), n + 8)

    def test_by_id_without_history_does_not_fall_back(self):
        drive = FakeDrive()
        studio = drive._add("studio", ["SHARED"], "application/vnd.google-apps.folder")
        drive._add("upload-history.csv", ["CHAN"], "text/csv", b"ten_goc,video_id\nOLD,v9\n")
        lf.fetch(drive, "CHAN", self.cache, studio_id=studio)
        self.assertEqual(lf.load_history(self.cache), {})

    def test_name_mode_falls_back_to_channel_history(self):
        self.put("upload-history.csv", "CHAN", "ten_goc,video_id\ngunA,v1\n")
        lf.fetch(self.drive, "CHAN", self.cache)
        self.assertEqual(lf.load_history(self.cache), {"v1": "gunA"})

    def test_name_mode_prefers_studio_history(self):
        self.put("upload-history.csv", "CHAN", "ten_goc,video_id\nOLD,v1\n")
        self.put("upload-history.csv", self.f["studio"], "ten_goc,video_id\nNEW,v1\n")
        lf.fetch(self.drive, "CHAN", self.cache)
        self.assertEqual(lf.load_history(self.cache), {"v1": "NEW"})

    def test_fetch_main_by_id_never_resolves_channel_folder(self):
        cfg = Path(self.tmp.name) / "c.json"
        cfg.write_text(json.dumps({"studio_folder_id": "STUDIO1", "channel": "Bare Skull"}),
                       encoding="utf-8")
        import youtube_uploader as yu
        with mock.patch("google_creds.load_creds", return_value="CREDS"), \
                mock.patch.object(yu, "get_credentials", side_effect=AssertionError("interactive")), \
                mock.patch("googleapiclient.discovery.build", return_value="DRIVE"), \
                mock.patch.object(yu, "resolve_folders", side_effect=AssertionError("no")), \
                mock.patch.object(lf, "fetch", return_value={"health_status": "PASS"}) as fe, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lf.main(["--config", str(cfg), "--cache", str(self.cache)]), 0)
        fe.assert_called_once_with("DRIVE", None, self.cache, studio_id="STUDIO1")

    def test_fetch_main_without_id_or_channel_exits_2(self):
        cfg = Path(self.tmp.name) / "c.json"
        cfg.write_text(json.dumps({"channel": "Bare Skull"}), encoding="utf-8")
        import youtube_uploader as yu
        with mock.patch("google_creds.load_creds", return_value="CREDS"), \
                mock.patch("googleapiclient.discovery.build", return_value="DRIVE"), \
                mock.patch.object(yu, "resolve_folders", side_effect=AssertionError("no")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lf.main(["--config", str(cfg), "--cache", str(self.cache)]), 2)

    def test_bad_token_exits_3_never_prompts_one_telegram_a_day(self):
        # I6b: the PC loader is non-interactive; a broken token is a clear exit 3 and one Telegram a day
        import youtube_uploader as yu
        from loop import analyse as an
        cfg = Path(self.tmp.name) / "c.json"
        cfg.write_text(json.dumps({"studio_folder_id": "STUDIO1"}), encoding="utf-8")
        tok = Path(self.tmp.name) / "token-drive-link.json"
        tok.write_text("{not json", encoding="utf-8")
        sent = []
        no_prompt = AssertionError("OAuth browser flow must never start")
        with mock.patch.object(yu, "get_credentials", side_effect=no_prompt), \
                mock.patch.object(yu.InstalledAppFlow, "from_client_secrets_file", side_effect=no_prompt), \
                mock.patch.object(yu, "notify", sent.append), mock.patch.object(an, "notify", sent.append):
            for _ in range(2):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = lf.main(["--config", str(cfg), "--cache", str(self.cache), "--token-drive", str(tok)])
                self.assertEqual(rc, 3)
                self.assertIn("Drive token on this PC cannot be used without a sign-in", err.getvalue())
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = an.main(["--config", str(cfg), "--cache", str(self.cache), "--token-drive", str(tok)])
                self.assertEqual(rc, 3)
        self.assertEqual(len(sent), 2)              # fetch once, analyse once, for the day
        self.assertTrue(all("exit code 3" in m and "token-drive-link.json" in m for m in sent))
        self.assertEqual(tok.read_text(encoding="utf-8"), "{not json")   # never rewritten

    def test_health_ok_values(self):
        for s in ("PASS", "pass", "ok", "OK"):
            self.assertTrue(lf.health_ok(s), s)
        for s in ("missing", "corrupt", "FAIL auth", "fail"):
            self.assertFalse(lf.health_ok(s), s)

    def test_impr_rounded_is_bool(self):
        self.assertIs(lf._typed("impr_rounded", "True"), True)
        self.assertIs(lf._typed("impr_rounded", "False"), False)
        self.assertIs(lf._typed("impr_rounded", "0"), False)

    def test_health_missing(self):
        self.assertEqual(lf.fetch(self.drive, "CHAN", self.cache)["health_status"], "missing")


if __name__ == "__main__":
    unittest.main()
