"""studio_channel: the doc chung tren JSON tu dung (khong chua chuoi cua ben thu ba)."""
import unittest

import studio_channel as sch

P = "analytics_hub.FEanalytics_hub_channel_overview."


def metric(text, label=None):
    vm = {"value": {"content": text}}
    if label:
        vm["subtitle"] = {"content": label}
    return {"simpleMetricViewModel": vm}


def item(title, trail=None):
    it = {"title": {"content": title}}
    if trail is not None:
        it["trailingLabel"] = {"content": trail}
    return {"listItemViewModel": it}


def card(aid, head=None, sub=None, items=(), extra=None):
    cv = {"bodyContent": {"linearLayoutViewModel": {"items": list(items)}}}
    if aid:
        cv["accessibilityId"] = aid
    if head:
        hdr = {"headline": {"content": head}}
        if sub:
            hdr["sublabel"] = {"content": sub}
        cv["topContent"] = {"sectionHeaderViewModel": hdr}
    if extra:
        cv["bodyContent"].update(extra)
    return {"cardViewModel": cv}


def chart(points, label="Views", annotations=None):
    lc = {"series": [{"label": label, "dataPoints": [{"domain": d, "measure": m} for d, m in points]}]}
    if annotations:
        lc["annotations"] = annotations
    return {"lineChartViewModel": lc}


class ValueTest(unittest.TestCase):
    def test_values(self):
        self.assertAlmostEqual(sch.parse_value("447,17 US$"), 447.17)
        self.assertEqual(sch.parse_value("+235"), 235)
        self.assertEqual(sch.parse_value("-3"), -3)
        self.assertAlmostEqual(sch.parse_value("6,9%"), 0.069)
        self.assertEqual(sch.parse_value("21 lần"), 21)
        self.assertEqual(sch.parse_value("3:38"), 218)
        self.assertEqual(sch.parse_value("129,2 N"), 129200)
        self.assertEqual(sch.parse_value("256 N người đăng ký"), 256000)
        self.assertIsNone(sch.parse_value("Rất cao"))
        self.assertIsNone(sch.parse_value(None))


class CardsTest(unittest.TestCase):
    def test_metric_series_rows_and_child_ids(self):
        views = card(P + "key_metric_views", "Lượt xem", items=[
            metric("129,2 N"),
            chart([(1792738800000, 3532), (1792825200000, 4209)],
                  annotations=[{"hovercard": {"hoverCardViewModel": {"items": [item("Video A")]}}}]),
        ])
        traffic = card(P + "how_viewers_find_videos", "Cách người xem tìm thấy", "‎⁨Lượt xem⁩ • ⁨28 ngày qua⁩",
                       items=[item("Duyệt xem", "89%"), item("Tìm kiếm", "4%")])
        cards = sch.parse_cards([{"x": [views, traffic]}])
        self.assertEqual([c["id"] for c in cards], ["key_metric_views", "how_viewers_find_videos"])
        v = cards[0]
        self.assertEqual(v["value"], 129200)
        self.assertEqual(v["rows"], [])          # chu thich tren bieu do khong thanh dong
        self.assertEqual(v["series"][0]["points"], [["2026-10-23", 3532], ["2026-10-24", 4209]])
        t = cards[1]
        self.assertEqual(t["period"], "Lượt xem • 28 ngày qua")
        self.assertEqual([(r["label"], r["value"]) for r in t["rows"]], [("Duyệt xem", 0.89), ("Tìm kiếm", 0.04)])

    def test_variants_named_and_duplicates_dropped(self):
        aid = "analytics_hub.FEanalytics_hub_channel_content.how_viewers_find_your_videos"
        a = card(aid, "Cách", items=[item("Duyệt xem", "89,7%")])
        b = card(aid, "Cách", items=[item("Google Search", "32,7%")])
        cards = sch.parse_cards([{"x": [a, a, b]}])
        self.assertEqual([c["id"] for c in cards],
                         ["how_viewers_find_your_videos#tong_the", "how_viewers_find_your_videos#ben_ngoai"])

    def test_live_card_keeps_latest(self):
        aid = "FEanalytics_hub_channel_overview.realtime_views.updating_live"
        cards = sch.parse_cards([{"x": [card(aid, "48 giờ", items=[metric("18.163")]),
                                        card(aid, "48 giờ", items=[metric("18.164")])]}])
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["value"], 18164)

    def test_card_without_id_uses_headline_and_funnel_rows(self):
        f = card(None, "Cách số lượt hiển thị tạo ra thời gian xem", extra={"funnelViewModel": {"rows": [
            {"label": {"content": "Số lượt hiển thị"}, "formattedValue": {"content": "551,0 N"}},
            {"label": {"content": "CTR"}, "formattedValue": {"content": "6,9%"}}]}})
        c = sch.parse_cards([f])[0]
        self.assertEqual(c["id"], "the_cach_so_luot_hien_thi_tao_ra_thoi_gian_xem")
        self.assertEqual([r["value"] for r in c["rows"]], [551000, 0.069])

    def test_heatmap(self):
        hm = {"audienceOnlineHeatmapViewModel": {"days": [{"dayLabelMessage": "CN", "cells": [
            {"color": {"ytSysColorBaselineReference": "YT_SYS_COLOR_BASELINE_REFERENCE_BLUE_7"}},
            {"color": {"ytSysColorBaselineReference": "YT_SYS_COLOR_BASELINE_REFERENCE_BLUE_1"}}]}]}}
        c = sch.parse_cards([card("audience_online", "Thời điểm", extra=hm)])[0]
        self.assertEqual(c["heatmap"], [["CN", 0, 7], ["CN", 1, 1]])

    def test_empty_card_skipped(self):
        self.assertEqual(sch.parse_cards([card(P + "x", "Không có gì")]), [])


class ResearchTest(unittest.TestCase):
    def lockup(self, vid, title, badge="21 lần"):
        return {"lockupViewModel": {
            "contentId": vid, "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "contentImage": {"thumbnailViewModel": {"overlays": [{"thumbnailBottomOverlayViewModel": {
                "badges": [{"thumbnailBadgeViewModel": {"text": "10:14"}}]}}]}},
            "metadata": {"lockupMetadataViewModel": {"title": {"content": title}, "metadata": {
                "contentMetadataViewModel": {"metadataRows": [
                    {"metadataParts": [{"text": {"content": "Channel X"}}, {"text": {"content": "256 N người đăng ký"}}]},
                    {"metadataParts": [{"text": {"content": "152 N lượt xem"}}, {"text": {"content": "2 tháng trước"}}],
                     "badges": [{"badgeViewModel": {"badgeText": badge}}]}]}}}}}}

    def test_items(self):
        items = sch.parse_research([{"g": [self.lockup("aaaaaaaaaaa", "T1"), self.lockup("aaaaaaaaaaa", "T1"),
                                           self.lockup("bbbbbbbbbbb", "T2", "3 lần")]}])
        self.assertEqual([i["video_id"] for i in items], ["aaaaaaaaaaa", "bbbbbbbbbbb"])
        i = items[0]
        self.assertEqual((i["channel"], i["subs"], i["views"], i["age_text"], i["outlier"], i["duration_s"]),
                         ("Channel X", 256000, 152000, "2 tháng trước", 21, 614))


class DocTest(unittest.TestCase):
    def test_parse_channel_skips_missing_tabs(self):
        doc = sch.parse_channel({"hub_overview": [card(P + "key_metric_views", "Lượt xem", items=[metric("1")])],
                                 "hub_content": [], "research_outliers": []}, "2026-11-21T11:00:00Z")
        self.assertEqual(list(doc["tabs"]), ["hub_overview"])
        self.assertEqual(doc["research"], {})
        self.assertEqual(sch.card_count(doc), 1)

    def test_urls(self):
        import studio_browser as sb
        urls = sb.channel_tab_urls("UCabcdefghijklmnopqrstuv")
        self.assertEqual(set(urls), set(sch.CHANNEL_TABS) | set(sch.RESEARCH_FEEDS))
        self.assertTrue(urls["hub_audience"].endswith("/channel/UCabcdefghijklmnopqrstuv/analytics/hub_audience"))
        self.assertIn("LANDING_PAGE_FEED_TYPE_CHANNEL_AUDIENCE_OVERLAP", urls["research_audience"])
        self.assertNotIn('"', urls["research_audience"])


if __name__ == "__main__":
    unittest.main()
