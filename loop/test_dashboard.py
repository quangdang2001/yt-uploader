import unittest

from loop import dashboard as db


class ReasonTest(unittest.TestCase):
    def test_numbers_and_words(self):
        s = db.reason_vi("CTR 7.9% >= bar 4.8% at 3,730,114 impr")
        self.assertEqual(s, "CTR 7,9% >= vạch 4,8% với 3.730.114 lượt hiển thị")

    def test_too_early(self):
        s = db.reason_vi("age 4.9 h, 15,153 impr (needs 6 h and 2,000 impr)")
        self.assertEqual(s, "tuổi 4,9 giờ, 15.153 lượt hiển thị (cần 6 giờ và 2.000 lượt hiển thị)")


class PlainReasonTest(unittest.TestCase):
    def test_change_title(self):
        s = db.reason_plain("CHANGE_TITLE", "CTR 4.0% < bar 5.3% at 3,097 impr, no swap left")
        self.assertIn("chỉ 4,0 người bấm", s)
        self.assertIn("5,3%", s)
        self.assertIn("3.097", s)

    def test_change_title_over_7_days(self):
        s = db.reason_plain("CHANGE_TITLE", "CTR 4.0% < bar 5.3% at 3,097 impr, over 7 days: left as is")
        self.assertTrue(s.endswith("Video đã quá 7 ngày: để nguyên, không sửa."), s)
        self.assertNotIn("Nên thử", s)

    def test_not_packaging(self):
        s = db.reason_plain("NOT_PACKAGING", "CTR 6.1% >= bar 4.8% at 14,403 impr, typical 39,677 impr")
        self.assertIn("14.403", s)
        self.assertIn("39.677", s)

    def test_unknown_code_falls_back(self):
        self.assertEqual(db.reason_plain("SWAP", "CTR 3.5% at 2,100 impr"), "CTR 3,5% với 2.100 lượt hiển thị")


class DailyTest(unittest.TestCase):
    def test_last_capture_per_video_per_local_day(self):
        snaps = [
            {"captured_at_utc": "2026-11-20T01:00:00Z", "video_id": "a", "views": "10", "impressions": "100", "ctr": "0.05"},
            {"captured_at_utc": "2026-11-20T09:00:00Z", "video_id": "a", "views": "20", "impressions": "200", "ctr": "0.05"},
            {"captured_at_utc": "2026-11-20T09:00:00Z", "video_id": "b", "views": "5", "impressions": "100", "ctr": "0.10"},
            # 18:00 UTC is 01:00 next day in Vietnam
            {"captured_at_utc": "2026-11-20T18:00:00Z", "video_id": "a", "views": "30", "impressions": "300", "ctr": "0.05"},
        ]
        rows = db.build_daily(snaps)
        self.assertEqual([r["ngay"] for r in rows], ["2026-11-20", "2026-11-21"])
        self.assertEqual(rows[0]["tong_view"], 25)
        self.assertEqual(rows[0]["tong_hien_thi"], 300)
        self.assertAlmostEqual(rows[0]["ctr_kenh_pct"], 6.67)
        # ngay 2 chi doc lai video a: video b giu so cu, tong khong bi tut
        self.assertEqual((rows[1]["so_video"], rows[1]["so_video_doc_moi"]), (2, 1))
        self.assertEqual(rows[1]["tong_view"], 35)
        self.assertEqual(rows[1]["tong_hien_thi"], 400)


if __name__ == "__main__":
    unittest.main()
