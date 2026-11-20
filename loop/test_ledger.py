"""Test loop/ledger.py: khong mang, khong ffmpeg that (chay tu goc repo):  python -m unittest loop.test_ledger -v"""
import csv
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "scripts"))

from loop import ledger as lg  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
VID1, VID2, VID3, VID4 = "AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC", "D-D_DDDDDDD"

BOARD = """# Bang

| ma | slug | cach | buoc | cho_ai | ngay_dang | du_doan_7d | view_7d | ghi_chu |
|---|---|---|---|---|---|---|---|---|
| 32 | lever_ranked | 1 | da-dang | quan-ly | 2026-11-18 | 5700-17000 | - | da dang (%s) 2026-11-18 |
| 33 | bear_guns_ranked | 2 | C-giao-dung | khau-dung | - | 1-2 | - | chua dang |
| 34 | other_film | truoc-agent | da-dang | - | 2026-11-10 | - | - | - |
"""


def snap(vid, age, impr=1000, ctr=0.05, views=100, cap="2026-11-20T06:00:00Z", **kw):
    row = {"captured_at_utc": cap, "video_id": vid, "age_hours": age, "period": "since_published",
           "impressions": impr, "ctr": ctr, "views": views, "thumb_hash": "h"}
    row.update(kw)
    return row


class ParseTest(unittest.TestCase):
    def test_hinh_line_with_comma_decimals(self):
        text = ("# x\n- Hinh: footage 7,1% | render 2.5D 49,3% | anh co chuyen dong 43,7% (47,8% than phim)\n")
        text = text.replace("Hinh", "Hình").replace("anh co", "ảnh có").replace("|", "·")
        self.assertEqual(lg.parse_hinh(text), {"footage_pct": 7.1, "render_pct": 49.3, "stills_pct": 43.7})

    def test_hinh_line_missing_leaves_empty(self):
        self.assertEqual(lg.parse_hinh("nothing here"),
                         {"footage_pct": None, "render_pct": None, "stills_pct": None})

    def test_hinh_integer_percent(self):
        got = lg.parse_hinh("- Hình: footage 4% · render 50% · ảnh 46%")
        self.assertEqual((got["footage_pct"], got["render_pct"], got["stills_pct"]), (4.0, 50.0, 46.0))

    def test_board_table(self):
        b = lg.parse_board(BOARD % VID1)
        self.assertEqual(b["lever_ranked"]["cach"], "1")
        self.assertIn(VID1, b["lever_ranked"]["ghi_chu"])
        self.assertEqual(set(b), {"lever_ranked", "bear_guns_ranked", "other_film"})

    def test_upload_title_block(self):
        text = "# UP\n\n## Title (locked)\n\n```\n10 Lever Guns Ranked\n```\n\n## Description\n```\nbody\n```\n"
        self.assertEqual(lg.parse_upload_title(text), "10 Lever Guns Ranked")
        self.assertIsNone(lg.parse_upload_title("## Description\n```\nx\n```"))

    def test_bold_titles(self):
        text = "**Main Title Here** (54 chars)\n- not bold\n**Second One**\n"
        self.assertEqual(lg.parse_bold_titles(text), ["Main Title Here", "Second One"])

    def test_words(self):
        self.assertEqual(lg.count_words("One two  three.\nFour"), 4)


class PackagingTest(unittest.TestCase):
    def test_rule(self):
        self.assertEqual(lg.packaging("10 Lever Guns Ranked From Worst To Best"), "xep-hang")
        self.assertEqual(lg.packaging("Top 5 Revolvers"), "xep-hang")
        self.assertEqual(lg.packaging("The $329 AR-15 That Killed Two"), "gia")
        self.assertEqual(lg.packaging("The $329 AR-15 Ranked"), "xep-hang")
        self.assertEqual(lg.packaging("The Gun Nobody Wanted"), "other")
        self.assertEqual(lg.packaging("Stopwatch gun"), "other")   # "Top" must be a word

    def test_title_flags(self):
        f = lg.title_flags("The $329 AR-15 Of 1964")
        self.assertEqual((f["title_has_number"], f["title_has_price"], f["title_has_year"]), (1, 1, 1))
        f = lg.title_flags("No Digits Here")
        self.assertEqual((f["title_chars"], f["title_has_number"], f["title_has_price"], f["title_has_year"]),
                         (14, 0, 0, 0))

    def test_shape_exact_then_substring(self):
        shapes = {"shapes": {"price-plea": "The $[gia] [sung] Gun Counters Pray You Never Find",
                             "every-x": "Every [X] Currently In [state]"},
                  "titles": {"The $109 Shotgun Every Gun Counter Prays You Never Find": "price-plea"}}
        self.assertEqual(lg.title_shape("the $109  shotgun every gun counter prays you never find", shapes),
                         "price-plea")
        self.assertEqual(lg.title_shape("Every Scope Currently In Stock", shapes), "every-x")
        self.assertEqual(lg.title_shape("Something Else", shapes), "")


class PickTest(unittest.TestCase):
    def test_24h_window_edges(self):
        rows = [snap(VID1, 11.9), snap(VID1, 12.0, impr=5), snap(VID1, 48.0, impr=6), snap(VID1, 48.1)]
        self.assertEqual(lg.pick_snapshot(rows, 24, 12, 48)["impressions"], 5)
        self.assertIsNone(lg.pick_snapshot([snap(VID1, 11.9), snap(VID1, 48.1)], 24, 12, 48))

    def test_closest_wins(self):
        rows = [snap(VID1, 20, impr=1), snap(VID1, 25, impr=2), snap(VID1, 40, impr=3)]
        self.assertEqual(lg.pick_snapshot(rows, 24, 12, 48)["impressions"], 2)

    def test_7d_window(self):
        rows = [snap(VID1, 119.9), snap(VID1, 120.0, impr=7), snap(VID1, 240.0, impr=8)]
        self.assertEqual(lg.pick_snapshot(rows, 168, 120, 240)["impressions"], 7)  # |120-168|=48 < 72
        self.assertEqual(lg.pick_snapshot(rows[:1], 168, 120, 240), None)


class Env(unittest.TestCase):
    """Mot build repo va mot cache gia trong thu muc tam."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.build = self.root / "build"
        self.cache = self.root / "cache"
        self.report = self.root / "report"
        (self.cache / "snapshots").mkdir(parents=True)
        board = self.build / "Bare Skull - 2nd brain" / "van-hanh" / "ke-hoach"
        board.mkdir(parents=True)
        (board / "bang-cong-viec.md").write_text(BOARD % VID1, encoding="utf-8")
        ctr = self.build / "topic_engine" / "ctr"
        ctr.mkdir(parents=True)
        (ctr / "own_shapes.json").write_text(json.dumps({"shapes": {}, "titles": {}}), encoding="utf-8")
        for slug in ("lever_ranked", "bear_guns_ranked", "other_film"):
            (self.build / "projects" / slug).mkdir(parents=True)
        self.cfg = {"build_repo": str(self.build), "report_dir": str(self.report), "tz": "Asia/Ho_Chi_Minh"}

    def write(self, rel, text):
        p = self.build / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def write_snaps(self, rows):
        cols = ["captured_at_utc", "video_id", "age_hours", "period", "impressions", "impr_rounded", "ctr", "views",
                "unique_viewers", "avd_s", "apv_pct", "ret_30s", "ret_60s", "src_browse", "src_suggested",
                "src_search", "src_other", "thumb_hash", "test_running"]
        with (self.cache / "snapshots" / "2026-11-20.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, cols)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})

    def write_history(self, pairs):
        with (self.cache / "upload-history.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["thoi_gian", "kenh", "ten_goc", "title", "video_id", "url", "trang_thai", "ghi_chu"])
            for vid, base in pairs:
                w.writerow(["", "", base, "", vid, "", "", ""])

    def build_rows(self, live, scan=None, **kw):
        return lg.build_ledger(self.cache, self.cfg, NOW, live_titles=live, probe=kw.get("probe", lambda p: 600.0),
                               scan=scan)


class JoinTest(Env):
    def setUp(self):
        super().setUp()
        self.write_snaps([snap(VID1, 20), snap(VID2, 30), snap(VID3, 40), snap(VID4, 50)])

    def by_id(self, rows):
        return {r["video_id"]: r for r in rows}

    def test_source_1_history(self):
        self.write_history([(VID1, "lever_ranked")])
        r = self.by_id(self.build_rows({VID1: "x"}))[VID1]
        self.assertEqual((r["slug"], r["match_source"]), ("lever_ranked", "history"))

    def test_source_2_id_in_project_md(self):
        self.write("projects/bear_guns_ranked/BUILD_LOG.md", "published as %s ok" % VID2)
        r = self.by_id(self.build_rows({VID2: "x"}))[VID2]
        self.assertEqual((r["slug"], r["match_source"]), ("bear_guns_ranked", "id-in-md"))

    def test_source_2_id_in_board_note_and_word_boundary(self):
        # VID1 is in the board ghi_chu of lever_ranked
        r = self.by_id(self.build_rows({VID1: "x"}))[VID1]
        self.assertEqual((r["slug"], r["match_source"]), ("lever_ranked", "id-in-md"))
        # an id that merely contains VID3 as a substring does not match
        self.write("projects/other_film/notes.md", "see x%sy and %s-z" % (VID3, VID3))
        r = self.by_id(self.build_rows({VID3: "x"}))[VID3]
        self.assertEqual(r["match_source"], "none")

    def test_source_3_title(self):
        self.write("projects/other_film/UPLOAD.md", "## Title\n\n```\nThe Gun Nobody Wanted\n```\n")
        r = self.by_id(self.build_rows({VID3: "the  GUN nobody wanted"}))[VID3]
        self.assertEqual((r["slug"], r["match_source"]), ("other_film", "title"))
        self.write("projects/bear_guns_ranked/tieu-de.md", "**Another Bold Title** (20)\n")
        r = self.by_id(self.build_rows({VID4: "Another Bold Title"}))[VID4]
        self.assertEqual((r["slug"], r["match_source"]), ("bear_guns_ranked", "title"))

    def test_unmatched_has_empty_made_side(self):
        r = self.by_id(self.build_rows({VID3: "Nothing Matches"}))[VID3]
        self.assertEqual(r["match_source"], "none")
        for col in ("slug", "lane", "script_words", "runtime_s", "footage_pct", "sec_per_cut", "thumb_warmth"):
            self.assertEqual(r[col], "", col)
        self.assertEqual(r["live_title"], "Nothing Matches")
        self.assertEqual(r["packaging"], "other")        # from the live title

    def test_ambiguous_id_falls_through(self):
        self.write("projects/bear_guns_ranked/a.md", VID3)
        self.write("projects/other_film/a.md", VID3)
        r = self.by_id(self.build_rows({VID3: "x"}))[VID3]
        self.assertEqual(r["match_source"], "none")

    def test_not_public_video_is_skipped_when_list_known(self):
        rows = self.build_rows({VID1: "x"})
        self.assertEqual([r["video_id"] for r in rows], [VID1])


class MadeSideTest(Env):
    def setUp(self):
        super().setUp()
        self.write_snaps([snap(VID1, 30)])
        self.write_history([(VID1, "lever_ranked")])
        self.write("projects/lever_ranked/script_narration.txt", "one two three four five")
        self.write("projects/lever_ranked/0 - ĐỌC TRƯỚC.md",
                   "- Hình: footage 15,5% · render 2.5D 41,6% · ảnh có chuyển động 43,0% (x)\n")
        self.mp4 = self.write("projects/lever_ranked/output/final/lever_ranked.mp4", "fake")

    def test_made_fields(self):
        row = self.build_rows({VID1: "10 Lever Guns Ranked"}, probe=lambda p: 860.0)[0]
        self.assertEqual((row["script_words"], row["runtime_s"]), (5, 860.0))
        self.assertEqual((row["footage_pct"], row["render_pct"], row["stills_pct"]), (15.5, 41.6, 43.0))
        self.assertEqual((row["lane"], row["packaging"], row["title_has_number"]), ("1", "xep-hang", 1))

    def test_thumb_from_features_csv_main(self):
        with (self.cache / "thumbnail-features.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["base", "variant", "brightness", "saturation", "colourfulness", "warmth"])
            w.writerow(["lever_ranked", "alt1", "0.1", "0.1", "0.1", "0.1"])
            w.writerow(["lever_ranked", "main", "0.5", "0.4", "0.2", "0.07"])
        row = self.build_rows({VID1: "x"})[0]
        self.assertEqual((row["thumb_brightness"], row["thumb_warmth"]), (0.5, 0.07))

    def test_thumb_measured_when_no_features(self):
        self.write("projects/lever_ranked/output/final/thumbnail.jpg", "fake")
        with mock.patch.object(lg, "measure_thumb", return_value=(0.4, 0.3, 0.2, 0.1)) as m:
            row = self.build_rows({VID1: "x"})[0]
        self.assertTrue(m.called)
        self.assertEqual((row["thumb_brightness"], row["thumb_saturation"], row["thumb_colourfulness"],
                          row["thumb_warmth"]), (0.4, 0.3, 0.2, 0.1))

    def test_thumb_failure_leaves_empty(self):
        self.write("projects/lever_ranked/output/final/thumbnail.jpg", "fake")
        with mock.patch.object(lg, "measure_thumb", return_value=None):
            row = self.build_rows({VID1: "x"})[0]
        self.assertEqual(row["thumb_brightness"], "")

    def test_scan_cache_reused_until_mtime_changes(self):
        calls = []

        def scan(path, runtime):
            calls.append(path)
            return 99

        rows = self.build_rows({VID1: "x"}, scan=scan, probe=lambda p: 100.0)
        self.assertEqual(rows[0]["sec_per_cut"], 1.0)           # 100 / (99 + 1)
        self.build_rows({VID1: "x"}, scan=scan, probe=lambda p: 100.0)
        self.assertEqual(len(calls), 1)
        os.utime(self.mp4, (1, 1))                               # mtime moved: new scan
        self.build_rows({VID1: "x"}, scan=scan, probe=lambda p: 100.0)
        self.assertEqual(len(calls), 2)

    def test_no_scan_leaves_sec_per_cut_empty(self):
        self.assertEqual(self.build_rows({VID1: "x"})[0]["sec_per_cut"], "")

    def test_probe_cached_per_mtime(self):
        calls = []
        self.build_rows({VID1: "x"}, probe=lambda p: calls.append(p) or 10.0)
        self.build_rows({VID1: "x"}, probe=lambda p: calls.append(p) or 10.0)
        self.assertEqual(len(calls), 1)


class DidTest(Env):
    def test_outcome_columns(self):
        self.write_snaps([
            snap(VID1, 10, impr=100, ctr=0.01),
            snap(VID1, 26, impr=2000, ctr=0.06, views=130, cap="2026-11-19T06:00:00Z"),
            snap(VID1, 170, impr=9000, ctr=0.05, views=900),
            snap(VID1, 200, impr=10000, ctr=0.045, views=1000, avd_s=250.0, apv_pct=30.0, ret_30s=0.6,
                 ret_60s=0.5, src_browse=0.5, src_suggested=0.25, src_search=0.1, src_other=0.15)])
        row = self.build_rows({VID1: "x"})[0]
        self.assertEqual((row["impr_24h"], row["ctr_24h"], row["views_24h"]), (2000, 0.06, 130))
        self.assertEqual((row["impr_7d"], row["ctr_7d"]), (9000, 0.05))
        self.assertEqual((row["impr_latest"], row["age_h_latest"]), (10000, 200))
        self.assertEqual((row["avd_s"], row["ret_30s"], row["src_browse"]), (250.0, 0.6, 0.5))
        self.assertIn(row["verdict"], {v.value for v in lg.Verdict})

    def test_missing_stays_empty_not_zero(self):
        self.write_snaps([snap(VID1, 5, impr=100, ctr=0.01)])
        row = self.build_rows({VID1: "x"})[0]
        for col in ("impr_24h", "ctr_7d", "avd_s", "src_search", "ret_30s"):
            self.assertEqual(row[col], "", col)

    def test_published_local_weekday_hour(self):
        # captured 06:00Z on 2026-11-20 minus 20 h = 2026-11-19 10:00Z = 17:00 Ho Chi Minh (Thursday)
        self.write_snaps([snap(VID1, 20)])
        row = self.build_rows({VID1: "x"})[0]
        self.assertEqual((row["published_local"], row["weekday"], row["hour"]), ("2026-11-19 17:00", "Thu", 17))


class LiveTitlesTest(Env):
    def test_runner_called_once_then_cached_24h(self):
        calls = []

        def runner(url):
            calls.append(url)
            return "%s|Title One\n%s|Title | With Pipe\n" % (VID1, VID2)

        got = lg.live_titles(self.cache, NOW, runner)
        self.assertEqual(got, {VID1: "Title One", VID2: "Title | With Pipe"})
        self.assertIn("UCDBRc2lIFJ0ylsGbTJd5C5g", calls[0])
        lg.live_titles(self.cache, datetime(2026, 11, 21, 7, 0, tzinfo=timezone.utc), runner)
        self.assertEqual(len(calls), 1)                       # 23 h later: cached
        lg.live_titles(self.cache, datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc), runner)
        self.assertEqual(len(calls), 2)                       # 25 h later: refresh

    def test_failure_returns_stale_cache_or_empty(self):
        def boom(url):
            raise OSError("no yt-dlp")

        self.assertEqual(lg.live_titles(self.cache, NOW, boom), {})
        lg.live_titles(self.cache, NOW, lambda u: "%s|T\n" % VID1)
        later = datetime(2026, 11, 25, tzinfo=timezone.utc)
        self.assertEqual(lg.live_titles(self.cache, later, boom), {VID1: "T"})


class OutputTest(Env):
    def setUp(self):
        super().setUp()
        self.write_snaps([snap(VID1, 20), snap(VID2, 60, cap="2026-11-20T07:00:00Z")])

    def test_csv_and_md_written_sorted_desc(self):
        rows = self.build_rows({VID1: "Older", VID2: "Newer"})
        lg.write_outputs(rows, self.report)
        with (self.report / "ledger.csv").open(encoding="utf-8", newline="") as fh:
            read = list(csv.DictReader(fh))
        self.assertEqual(list(read[0].keys()), lg.COLUMNS)
        self.assertEqual(len(read), 2)
        md = (self.report / "ledger.md").read_text(encoding="utf-8")
        self.assertLess(md.index(VID1), md.index(VID2))       # VID1 published later (age 20 vs 60)
        self.assertFalse(list(self.report.glob("*.tmp")))

    def test_atomic_write_keeps_old_file_on_failure(self):
        rows = self.build_rows({VID1: "Older"})
        lg.write_outputs(rows, self.report)
        before = (self.report / "ledger.csv").read_text(encoding="utf-8")
        with mock.patch("os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                lg.write_outputs(self.build_rows({VID1: "Changed Title"}), self.report)
        self.assertEqual((self.report / "ledger.csv").read_text(encoding="utf-8"), before)


class CliTest(Env):
    def run_main(self, args):
        cfg_path = self.root / "config.json"
        cfg_path.write_text(json.dumps(self.cfg), encoding="utf-8")
        err = []
        with mock.patch.object(sys, "stderr") as e:
            e.write = err.append
            code = lg.main(["--config", str(cfg_path), "--cache", str(self.cache)] + args)
        return code, "".join(err)

    def test_missing_build_repo_exits_2(self):
        self.cfg["build_repo"] = str(self.root / "nope")
        code, err = self.run_main(["--no-video-scan"])
        self.assertEqual(code, 2)
        self.assertIn("build repo", err)

    def test_missing_cache_exits_2(self):
        for p in (self.cache / "snapshots").glob("*"):
            p.unlink()
        (self.cache / "snapshots").rmdir()
        code, err = self.run_main(["--no-video-scan"])
        self.assertEqual(code, 2)
        self.assertIn("cache", err)

    def test_runs_and_writes(self):
        self.write_snaps([snap(VID1, 20)])
        with mock.patch.object(lg, "live_titles", return_value={VID1: "T"}):
            code, _ = self.run_main(["--no-video-scan"])
        self.assertEqual(code, 0)
        self.assertTrue((self.report / "ledger.csv").exists())


class RunPs1Test(unittest.TestCase):
    def test_run_ps1_has_ledger_step_after_analyse(self):
        text = (REPO / "loop" / "run.ps1").read_text(encoding="utf-8")
        self.assertLess(text.index("python -m loop.analyse"), text.index("python -m loop.ledger"))
        block = re.search(r"python -m loop\.ledger --config \$config\n(.*?)\n\}", text, re.S).group(1)
        self.assertIn("$code = $LASTEXITCODE", block)
        self.assertIn("python -m loop.notify_fail ledger $code", block)
        self.assertIn("exit $code", block)


if __name__ == "__main__":
    unittest.main()
