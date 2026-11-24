"""analyse + duong tieu de (loop/titles.py), cache gia va Drive gia.  python -m pytest -q loop/test_analyse_titles.py"""
import json
import unittest

from loop import analyse as an  # noqa: F401
from loop.test_analyse import AnalyseTest, feat, snap, utc


class AnalyseTitlesTest(unittest.TestCase):
    """Dung lai khung cua AnalyseTest (cache gia, Drive gia) ma khong chay lai cac test cua no."""
    NOW = utc("2026-11-19T12:00:00Z")
    setUp = AnalyseTest.setUp
    write = AnalyseTest.write
    run_an = AnalyseTest.run_an
    report = AnalyseTest.report
    pending_files = AnalyseTest.pending_files

    def low_video(self, age=48, alts=True, thumb_alts=False):
        """v1: CTR ro rang duoi vach, khong co thumbnail du phong (-> CHANGE_TITLE "no swap left")."""
        self.snaps = [snap("v1", "2026-11-18T00:00:00Z", age - 36, 3000, 0.03),
                      snap("v1", "2026-11-19T10:00:00Z", age, 6000, 0.03)]
        self.feats = [feat("gunA", "main")] + ([feat("gunA", "alt1")] if thumb_alts else [])
        self.hist = [("v1", "gunA")]
        self.write()
        if alts:
            d = self.cache / "title-alts"
            d.mkdir(parents=True, exist_ok=True)
            (d / "gunA.json").write_text(json.dumps(
                {"base": "gunA", "main": "Old Title $499", "alt1": "New Title $499"}), encoding="utf-8")

    def action(self, state, name, obj):
        d = self.cache / "actions" / state
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps(obj), encoding="utf-8")

    def pending_reqs(self):
        return [json.loads(f["data"]) for f in self.pending_files()]

    def test_title_request_is_written_when_no_thumbnail_is_left(self):
        self.low_video()
        rows = self.run_an()
        self.assertEqual(rows[0]["verdict"], "CHANGE_TITLE")
        self.assertTrue(rows[0]["requested"])
        self.assertIn("title swap to alt1", rows[0]["reason"])
        reqs = self.pending_reqs()
        self.assertEqual(len(reqs), 1)
        rq = reqs[0]
        self.assertEqual((rq["kind"], rq["to_title_variant"], rq["to_title"], rq["expect_title"]),
                         ("title", "alt1", "New Title $499", "Old Title $499"))
        self.assertNotIn("to_variant", rq)
        self.assertIn("title swap to alt1", self.report())

    def test_alert_only_period_writes_no_title_request(self):
        self.cfg["go_live"] = "2026-11-17"
        self.low_video()
        rows = self.run_an()
        self.assertEqual(self.pending_files(), [])
        self.assertIn("would title swap to alt1", rows[0]["reason"])

    def test_no_title_request_before_36_hours_or_without_alternates(self):
        self.low_video(age=30)
        self.run_an()
        self.assertEqual(self.pending_files(), [])
        (self.cache / "title-alts" / "gunA.json").unlink()                # same video at 48 h, no spare title
        self.low_video(alts=False)
        rows = self.run_an()
        self.assertEqual(self.pending_files(), [])
        self.assertTrue(rows[0]["reason"].endswith("no swap left"))

    def test_thumbnail_goes_first_while_a_spare_thumbnail_is_left(self):
        self.low_video(thumb_alts=True)
        rows = self.run_an()
        self.assertEqual(rows[0]["verdict"], "SWAP")
        reqs = self.pending_reqs()
        self.assertEqual([r.get("to_variant") for r in reqs], ["alt1"])
        self.assertNotIn("kind", reqs[0])

    def test_pending_or_recently_failed_title_request_is_not_repeated(self):
        self.low_video()
        (self.cache / "actions").mkdir(parents=True, exist_ok=True)
        (self.cache / "actions" / "pending.txt").write_text("v1__20261119T110000Z.json\n", encoding="utf-8")
        self.run_an()
        self.assertEqual(self.pending_files(), [])
        (self.cache / "actions" / "pending.txt").write_text("", encoding="utf-8")
        self.action("failed", "v1__a.json", {"kind": "title", "video_id": "v1", "to_title_variant": "alt1",
                                             "requested_utc": "2026-11-19T09:00:00Z",
                                             "result": {"status": "error", "at_utc": "2026-11-19T09:30:00Z",
                                                        "detail": "HttpError 500"}})
        rows = self.run_an()
        self.assertEqual(self.pending_files(), [])
        self.assertIn("failed within 24 h", rows[0]["reason"])

    def test_owner_changed_title_by_hand_stops_title_requests(self):
        self.low_video()
        self.action("failed", "v1__a.json", {"kind": "title", "video_id": "v1", "to_title_variant": "alt1",
                                             "requested_utc": "2026-11-17T09:00:00Z",
                                             "result": {"status": "error", "at_utc": "2026-11-17T09:30:00Z",
                                                        "detail": "title changed since request (live: 'Mine')"}})
        rows = self.run_an()
        self.assertEqual(self.pending_files(), [])
        self.assertTrue(rows[0]["reason"].endswith("no swap left"))

    def test_after_a_title_change_no_thumbnail_request_is_written(self):
        self.low_video(thumb_alts=True)
        self.action("done", "v1__t.json", {"kind": "title", "video_id": "v1", "to_title_variant": "alt1",
                                           "requested_utc": "2026-11-19T00:00:00Z",
                                           "result": {"status": "ok", "at_utc": "2026-11-19T00:30:00Z"}})
        rows = self.run_an()
        self.assertEqual(rows[0]["verdict"], "SWAP")                      # the thumbnail track still says so ...
        self.assertEqual(self.pending_files(), [])                        # ... but nothing is requested
        self.assertIn("title changed: thumbnail left as is", rows[0]["reason"])

    def test_done_title_action_does_not_break_the_weekly_why_section(self):
        self.low_video()
        self.action("done", "v1__t.json", {"kind": "title", "video_id": "v1", "to_title_variant": "alt1",
                                           "requested_utc": "2026-11-19T00:00:00Z",
                                           "result": {"status": "ok", "at_utc": "2026-11-19T00:30:00Z"}})
        self.cfg["why"] = True
        self.run_an()
        self.assertIn("v1", self.report())


del AnalyseTest       # khong de pytest thu thap lai lop goc trong file nay
