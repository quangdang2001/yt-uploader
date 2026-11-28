import json
import math
import unittest

from loop import dashboard as db


class ReasonTest(unittest.TestCase):
    def test_numbers_and_words(self):
        s = db.reason_vi("CTR 7.9% >= bar 4.8% at 3,730,114 impr")
        self.assertEqual(s, "CTR 7,9% >= vạch 4,8% với 3.730.114 lượt hiển thị")

    def test_too_early(self):
        s = db.reason_vi("age 4.9 h, 1,100 impr (needs 6 h or 1,500 impr)")
        self.assertEqual(s, "tuổi 4,9 giờ, 1.100 lượt hiển thị (cần 6 giờ hoặc 1.500 lượt hiển thị)")

    def test_too_early_old_wording_still_translates(self):
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


class ExperimentDataTest(unittest.TestCase):
    def _tim(self, directive=None, results=None, book=None):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp()) / "tim"
        d.mkdir()
        if directive is not None:
            (d / "chi-thi.json").write_text(json.dumps(directive), encoding="utf-8")
        if results is not None:
            (d / "ket-qua.csv").write_text(results, encoding="utf-8")
        if book is not None:
            (d / "so-quyet-dinh.csv").write_text(book, encoding="utf-8")
        return d

    def test_dashboard_has_experiment_keys_when_heart_files_missing(self):
        import tempfile
        from pathlib import Path
        out = db.build_experiment(Path(tempfile.mkdtemp()) / "khong-co")
        self.assertEqual(out, {"tn_nhanh": [], "tn_so": [], "tn_ke_tiep": []})
        out = db.build_experiment(self._tim())
        self.assertEqual(out, {"tn_nhanh": [], "tn_so": [], "tn_ke_tiep": []})

    def test_tn_nhanh_labels_vietnamese(self):
        directive = {
            "nhanh_thang": {
                "dong": {"nhanh": "lineup", "n": 4, "trung_vi": 0.064, "du_so": True},   # dong cham bang ctr_24h
                "hook": {"nhanh": "su-viec-hang", "n": 3, "trung_vi": 0.42, "du_so": True},
                "thumb_sang": {"nhanh": "sang", "n": 0, "trung_vi": None, "du_so": False},
            },
            "muc": [{"slug": "a", "gia_tri": {"dong": "lineup", "hook": "su-viec-hang", "thumb_sang": "sang"},
                     "bien_thu": "thumb_sang", "nhanh_thu": "toi", "kieu": "thu",
                     "du_doan_7d": "3000-6000", "con_han_toi": "2026-11-25"}],
        }
        out = db.build_experiment(self._tim(directive))
        rows = out["tn_nhanh"]
        # only the variables the heart really tests (heart_config `thu` not false); fixed defaults are left out
        self.assertEqual({r["bien"] for r in rows}, {"Kiểu video"})
        win = [r for r in rows if r["bien"] == "Kiểu video" and r["nhanh"] == "lineup"][0]
        self.assertEqual((win["n"], win["trung_vi"], win["trang_thai"], win["dang_dung"]),
                         (4, "6,4% tỉ lệ bấm 24 giờ", "Đang thắng", True))
        other = [r for r in rows if r["bien"] == "Kiểu video" and r["nhanh"] != "lineup"][0]
        self.assertEqual(other["trang_thai"], "Chưa đủ số")
        self.assertFalse(other["dang_dung"])
        nxt = out["tn_ke_tiep"][0]
        self.assertEqual((nxt["bien_thu"], nxt["kieu"]), ("Thumbnail sáng hay tối", "Thử cái mới"))

    def test_tn_nhanh_reads_full_arm_stats(self):
        directive = {
            "nhanh_thang": {"dong": {"nhanh": "lineup", "n": 5, "trung_vi": 0.07, "du_so": True}},
            "thong_ke_nhanh": {
                "dong": {
                    "lineup": {"n": 5, "trung_vi": 0.07, "p25": 0, "p75": 0, "trang_thai": "thang", "do": "ctr_24h"},
                    "xep-hang": {"n": 6, "trung_vi": 0.031, "p25": 0, "p75": 0, "trang_thai": "loai", "do": "ctr_24h"},
                    # an arm since removed from heart_config (an older chi-thi.json) is not shown
                    "kieu-da-go": {"n": 2, "trung_vi": 0.05, "p25": 0, "p75": 0, "trang_thai": "chua-du", "do": "ctr_24h"},
                },
                # a variable the heart keeps on its default and never tests is not shown
                "thumb_sang": {"sang": {"n": 5, "trung_vi": math.log(3000), "p25": 0, "p75": 0,
                                        "trang_thai": "thang", "do": "views_7d"}},
            },
            "muc": [],
        }
        rows = db.build_experiment(self._tim(directive))["tn_nhanh"]
        loai = [r for r in rows if r["nhanh"] == "xep-hang"][0]
        self.assertEqual((loai["trang_thai"], loai["n"], loai["trung_vi"]), ("Đã loại", 6, "3,1% tỉ lệ bấm 24 giờ"))
        self.assertEqual({(r["bien"], r["nhanh"]) for r in rows}, {("Kiểu video", "lineup"), ("Kiểu video", "xep-hang")})

    def test_changes_strip_markdown_and_read_numbered_files(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        (d / "thay-doi.md").write_text(
            "\n".join([
                "# Nhật ký", "",
                "## TD-2026-11-21-1 · Luật hình: `ship_gate` thành cảnh báo", "",
                "- **Ngày:** 2026-11-21 · **Quyền:** chủ kênh",
                "- **File:** `a/ship_gate.py` (khối `ADVISORY`, vòng chấm",
                "  trong `main`); `a/SKILL.md`.",
                "- **Đoạn trước:** x",
                "- **Ngày đánh giá:** sau 5 video · **Trạng thái:** đang chạy", "",
                "## TD-2026-11-22-1 · Tạm ngưng kiểu xếp hạng", "",
                "- **File 1:** `raw/ho-so-kenh.md`, bảng chọn chủ đề.",
                "  - Đoạn trước: y",
                "- **File 2:** `tim/chi-thi.md`", ""]), encoding="utf-8")
        a, b = db.build_changes(str(d))
        self.assertEqual(a["noi_dung"], "Luật hình: ship_gate thành cảnh báo")
        self.assertEqual(a["file"], "a/ship_gate.py (khối ADVISORY, vòng chấm trong main); a/SKILL.md.")
        self.assertEqual(a["trang_thai"], "đang chạy")
        self.assertEqual(b["file"], "raw/ho-so-kenh.md, bảng chọn chủ đề.")
        self.assertEqual(b["trang_thai"], "đang chạy")
        for r in (a, b):
            self.assertNotRegex(" ".join(r.values()), r"[*`]")

    def test_tn_so_latest_14_with_results(self):
        book_head = "id,thoi_diem,tac_vu,slug,bien_thu,nhanh_thu,du_doan_7d\n"
        lines = "".join(f"{i},2026-11-{i:02d}T06:00:00,dung,s{i},hook,x,3000-6000\n" for i in range(1, 17))
        res = ("slug,video_id,ctr_24h,impr_24h,views_7d,apv_pct,ret_30s,verdict,ket_qua,canh_bao\n"
               "s16,v,,,4500,,,,trong,lech-tieu-de\n")
        out = db.build_experiment(self._tim(book=book_head + lines, results=res))
        self.assertEqual(len(out["tn_so"]), 14)
        self.assertEqual(out["tn_so"][0]["slug"], "s16")
        self.assertEqual(out["tn_so"][0]["ket_qua"], "Đúng dự đoán")
        self.assertEqual(out["tn_so"][0]["views_7d"], 4500)
        self.assertEqual(out["tn_so"][1]["ket_qua"], "Chưa có số")


if __name__ == "__main__":
    unittest.main()
