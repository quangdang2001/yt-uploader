import json
import tempfile
import unittest
from pathlib import Path

from loop import channel_view as cv


def card(cid, value=None, rows=(), series=None, period=None, heatmap=None):
    c = {"id": cid, "headline": None, "period": period, "value_text": None, "value": value,
         "metrics": [], "rows": [{"label": l, "text": t, "value": v} for l, t, v in rows],
         "series": [{"label": "x", "points": series}] if series else []}
    if heatmap:
        c["heatmap"] = heatmap
    return c


DOC = {
    "version": 1, "captured_at_utc": "2026-11-21T11:00:00Z",
    "tabs": {
        "hub_overview": [card("key_metric_views", 1000), card("simple_metric_revenue", 12.5),
                         card("realtime_subscribers", 4877)],
        "hub_content": [
            card("video_views", 1000, series=[["2026-11-19", 400], ["2026-11-20", 600]]),
            card("video_impressions_ctr", 0.069, series=[["2026-11-20", 0.07]]),
            card("video_average_view_duration", 218, series=[["2026-11-20", 218500]]),
            card("how_viewers_find_your_videos#tong_the", period="28 ngày qua",
                 rows=[("Duyệt xem", "89,7%", 0.897), ("Tìm kiếm", "4,2%", 0.042)]),
            card("how_viewers_find_your_videos#tim_kiem_youtube",
                 rows=[("Tổng tỷ lệ lưu lượng truy cập", "4,2%", 0.042), ("ruger harrier", "1,6%", 0.016)]),
        ],
        "hub_revenue": [
            card("key_metric_all", 12.5, series=[["2026-11-20", 12500]]),
            card("months_revenue", rows=[("Tháng 10", "739,36 US$", 739.36)]),
            card("top_earning_content_by_format_CREATOR_CONTENT_TYPE_VIDEO_ON_DEMAND", 12.5,
                 rows=[("Doanh thu mỗi nghìn lượt xem (RPM) có chủ đích", "6,96 US$", 6.96)]),
        ],
        "hub_audience": [card("audience_online", heatmap=[["CN", 0, 7]])],
    },
    "research": {"research_outliers": [
        {"video_id": "a", "title": "Low", "channel": "C1", "subs": 1, "views": 10, "outlier": 2, "age_text": "1 tháng trước",
         "duration": "1:00"},
        {"video_id": "b", "title": "High", "channel": "C2", "subs": 1, "views": 99, "outlier": 30, "age_text": "2 tháng trước",
         "duration": "2:00"}]},
}


class ChannelViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name) / "channel"
        d.mkdir()
        (d / "2026-11-20.json").write_text("{broken", encoding="utf-8")
        (d / "2026-11-21.json").write_text(json.dumps(DOC), encoding="utf-8")
        self.view = cv.build(self.tmp.name)

    def test_latest_readable_doc(self):
        self.assertEqual(self.view["channel_file"], "2026-11-21.json")

    def test_totals(self):
        t = self.view["kenh_tong"][0]
        self.assertEqual((t["ngay_doc"], t["luot_xem_28n"], t["doanh_thu_28n"], t["tong_dang_ky"], t["rpm"]),
                         ("2026-11-21", 1000, 12.5, 4877, 6.96))
        self.assertEqual(t["ctr_28n_pct"], 6.9)

    def test_daily_units(self):
        d = {r["ngay"]: r for r in self.view["kenh_ngay"]}
        self.assertEqual(d["2026-11-20"]["xem_tb_giay"], 218.5)      # mili giay -> giay
        self.assertEqual(d["2026-11-20"]["doanh_thu"], 12.5)         # phan nghin US$ -> US$
        self.assertEqual(d["2026-11-20"]["ctr_pct"], 7.0)
        self.assertIsNone(d["2026-11-19"]["doanh_thu"])

    def test_breakdowns_skip_total_row_and_scale_percent(self):
        rows = self.view["kenh_phan_bo"]
        search = [r for r in rows if r["nhom"].startswith("Từ khóa")]
        self.assertEqual([(r["muc"], r["gia_tri"]) for r in search], [("ruger harrier", 1.6)])
        money = [r for r in rows if r["nhom"].startswith("Doanh thu theo tháng")]
        self.assertEqual((money[0]["gia_tri"], money[0]["don_vi"]), (739.36, ""))
        self.assertEqual(len({r["khoa"] for r in rows}), len(rows))

    def test_research_sorted_by_outlier(self):
        self.assertEqual([r["tieu_de"] for r in self.view["nghien_cuu"]], ["High", "Low"])
        self.assertEqual(self.view["nghien_cuu"][0]["link"], "https://www.youtube.com/watch?v=b")

    def test_heatmap_and_markdown(self):
        self.assertEqual(self.view["gio_online"], [{"thu": "CN", "gio": 0, "muc": 7}])
        md = cv.markdown(self.view)
        self.assertIn("Doanh thu ước tính: 12,50 US$", md)
        self.assertIn("30.0× · High — C2", md.replace("30×", "30.0×"))

    def test_no_data(self):
        with tempfile.TemporaryDirectory() as empty:
            v = cv.build(empty)
        self.assertEqual(v["kenh_tong"], [])
        self.assertIn("Chưa có dữ liệu", cv.markdown(v))


class MonthRevenue(unittest.TestCase):
    def test_month_rows_get_their_year_from_the_read_date(self):
        doc = {"captured_at_utc": "2027-01-05T11:00:00Z", "tabs": {"hub_revenue": [card("months_revenue", rows=[
            ("Tháng 1 (đang diễn ra)", "40,00 US$", 40.0), ("Tháng 12", "900 US$", 900.0),
            ("Tháng 11", "437,37 US$", 437.37), ("Tổng", "x", None)])]}}
        self.assertEqual(cv.month_revenue(doc), {"2027-01": 40.0, "2026-12": 900.0, "2026-11": 437.37})

    def test_missing_card_or_date_gives_nothing(self):
        self.assertEqual(cv.month_revenue({"captured_at_utc": "2026-11-23T11:00:00Z", "tabs": {}}), {})
        self.assertEqual(cv.month_revenue({"tabs": {"hub_revenue": [card("months_revenue", rows=[("Tháng 11", "", 1.0)])]}}), {})


if __name__ == "__main__":
    unittest.main()
