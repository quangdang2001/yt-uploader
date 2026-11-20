"""Test cho studio_parse: fixture that cua kenh, da cat bot (xem fixtures/studio/trim_fixture.py)."""
import json
import os
import subprocess
import sys
import unittest

import studio_parse as sp

HERE = os.path.dirname(os.path.abspath(__file__))
PARSE_DIR = os.path.join(HERE, "fixtures", "studio", "parse")
RAW_DIR = os.path.join(HERE, "fixtures", "studio", "raw_spike")

RUGER = "mwKhntLxOlk__20261120T041236Z"
LEVER = "5q637XpLagg__20261120T040910Z"


def load(stem, suffix, folder=PARSE_DIR):
    path = os.path.join(folder, "%s__youtubei_v1_browse%s.json" % (stem, suffix))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def both_tabs(stem, folder=PARSE_DIR):
    return [load(stem, "", folder), load(stem, "_1", folder)]


def card(aid, content, *children):
    """Dung nut gia: mot node co accessibilityId va mot simpleMetricViewModel."""
    items = [{"simpleMetricViewModel": {"value": {"content": content}}}]
    items.extend(children)
    return {"items": items, "accessibilityId": aid}


def with_period(payloads, value):
    """Ban sao sau cua payloads voi moi time_period doi thanh value."""
    copy = json.loads(json.dumps(payloads))
    n = 0
    for p in copy:
        for node in sp._nodes(p):
            if node.get("key") == "time_period":
                node["value"] = value
                n += 1
    assert n, "fixture khong co time_period"
    return copy


def synthetic(impr="159", ctr="10,1%", views="114", uniq="0", avd="3:02", extra=None, series=None,
              time_period="since_publish"):
    reach = [
        {"routeParams": [{"key": "time_period", "value": time_period}]} if time_period else {},
        card("analytics_hub.VideoReachKeyMetricImpressionsCard.metric_values", impr),
        card("analytics_hub.video_reach_views.left_section", views),
        card("analytics_hub.video_reach_ctr.left_section", ctr),
        card("analytics_hub.video_reach_unique_viewers.left_section", uniq),
    ]
    if series is not None:
        chart = {
            "accessibilityId": "analytics_hub.VideoReachKeyMetricImpressionsCard.body",
            "lineChartViewModel": {"series": [{"dataPoints": [{"domain": i, "measure": m} for i, m in enumerate(series)]}]},
        }
        reach[1] = {"accessibilityId": "analytics_hub.VideoReachKeyMetricImpressionsCard", "items": [reach[1], chart]}
    eng = [card("analytics_hub.FEanalytics_hub_video_engagement.key_metrics_average_view_duration.left_section", avd)]
    if extra:
        eng.append(extra)
    return [{"contents": reach}, {"contents": eng}]


class ParseNumberTest(unittest.TestCase):
    def test_parse_number_locales(self):
        cases = {
            "159": 159, "12,345": 12345, "12.345": 12345, "1,234,567": 1234567,
            "10,1%": 0.101, "10.1%": 0.101, "5,4 %": 0.054, "5.4%": 0.054,
            "1,6 N": 1600, "1.6K": 1600, "2,3 Tr": 2300000, "1.2M": 1200000,
            "3:02": 182.0, "1:02:03": 3723.0,
        }
        for text, want in cases.items():
            self.assertAlmostEqual(sp.parse_number(text), want, places=9, msg=text)

    def test_parse_number_non_breaking_spaces(self):
        self.assertAlmostEqual(sp.parse_number("1,6 N"), 1600, places=9)
        self.assertAlmostEqual(sp.parse_number("1,6 N"), 1600, places=9)
        self.assertAlmostEqual(sp.parse_number("5,4 %"), 0.054, places=9)

    def test_parse_number_rejects_blank_and_dash(self):
        for text in ("—", "-", "", "  ", "abc"):
            with self.assertRaises(sp.ParseError, msg=repr(text)):
                sp.parse_number(text)

    def test_is_abbreviated(self):
        self.assertTrue(sp.is_abbreviated("1,6 N"))
        self.assertTrue(sp.is_abbreviated("1.2K"))
        self.assertFalse(sp.is_abbreviated("159"))
        self.assertFalse(sp.is_abbreviated("12,345"))


class FixtureTest(unittest.TestCase):
    def check_ruger(self, folder):
        row = sp.parse_snapshot(both_tabs(RUGER, folder), "mwKhntLxOlk", None)
        self.assertEqual(row["impressions"], 159)
        self.assertIs(row["impr_rounded"], False)
        self.assertAlmostEqual(row["ctr"], 0.101)
        self.assertEqual(row["views"], 114)
        self.assertEqual(row["unique_viewers"], 0)
        self.assertEqual(row["avd_s"], 182.0)
        return row

    def check_lever(self, folder):
        row = sp.parse_snapshot(both_tabs(LEVER, folder), "5q637XpLagg", None)
        self.assertEqual(row["impressions"], 1684)  # cuoi chuoi tich luy, trong 10% cua "1,6 N"
        self.assertIs(row["impr_rounded"], False)
        self.assertAlmostEqual(row["ctr"], 0.025)
        self.assertEqual(row["views"], 214)
        self.assertEqual(row["unique_viewers"], 67)
        self.assertEqual(row["avd_s"], 195.0)
        return row

    def test_parses_reach_fixture(self):
        row = self.check_ruger(PARSE_DIR)
        self.assertEqual(row["video_id"], "mwKhntLxOlk")
        self.assertEqual(row["period"], "since_published")
        self.check_lever(PARSE_DIR)

    def test_period_from_payload_and_argument(self):
        pl = both_tabs(RUGER)
        self.assertEqual(sp.parse_snapshot(pl, "v")["period"], "since_published")
        self.assertEqual(sp.parse_snapshot(pl, "v", "since_published")["period"], "since_published")
        with self.assertRaises(sp.ParseError):
            sp.parse_snapshot(pl, "v", "last_28_days")
        edited = with_period(pl, "last_28_days")
        self.assertEqual(sp.parse_snapshot(edited, "v")["period"], "last_28_days")
        self.assertEqual(sp.parse_snapshot(edited, "v", "last_28_days")["period"], "last_28_days")
        self.assertEqual(sp.parse_snapshot(with_period(pl, "LAST_90_DAYS"), "v")["period"], "last_90_days")

    def test_period_neither_available_raises(self):
        with self.assertRaises(sp.ParseError) as cm:
            sp.parse_snapshot(synthetic(time_period=None), "v")
        self.assertIn("period", str(cm.exception))
        # tham so dung thay cho payload khong co time_period
        self.assertEqual(sp.parse_snapshot(synthetic(time_period=None), "v", "since_published")["period"],
                         "since_published")

    def test_sanity_guards(self):
        with self.assertRaises(sp.ParseError) as cm:
            sp.parse_snapshot(synthetic(ctr="1,234%"), "v")
        self.assertIn("ctr", str(cm.exception))
        with self.assertRaises(sp.ParseError):
            sp.parse_snapshot(synthetic(avd="-3:02"), "v")

    def test_tab_order_does_not_matter(self):
        a = sp.parse_snapshot(both_tabs(RUGER), "v")
        b = sp.parse_snapshot(list(reversed(both_tabs(RUGER))), "v")
        self.assertEqual(a, b)

    def test_columns_and_unparsed_are_none(self):
        row = sp.parse_snapshot(both_tabs(RUGER), "v")
        self.assertEqual(tuple(row), sp.SNAPSHOT_COLS)
        for col in ("captured_at_utc", "age_hours", "thumb_hash", "apv_pct", "ret_30s", "ret_60s",
                    "src_browse", "src_suggested", "src_search", "src_other"):
            self.assertIsNone(row[col], col)
        self.assertIs(row["test_running"], False)

    def test_snapshot_cols_order(self):
        self.assertEqual(sp.SNAPSHOT_COLS, (
            "captured_at_utc", "video_id", "age_hours", "period", "impressions", "impr_rounded", "ctr",
            "views", "unique_viewers", "avd_s", "apv_pct", "ret_30s", "ret_60s", "src_browse",
            "src_suggested", "src_search", "src_other", "thumb_hash", "test_running"))

    @unittest.skipUnless(os.path.isdir(RAW_DIR), "raw_spike khong co tren may nay")
    def test_raw_captures_parse_too(self):
        self.check_ruger(RAW_DIR)
        self.check_lever(RAW_DIR)


class RulesTest(unittest.TestCase):
    def test_missing_metric_raises_and_names_it(self):
        payloads = synthetic()
        payloads[0]["contents"].pop(2)  # bo views
        with self.assertRaises(sp.ParseError) as cm:
            sp.parse_snapshot(payloads, "v")
        self.assertIn("views", str(cm.exception))

    def test_empty_payloads_raise(self):
        with self.assertRaises(sp.ParseError):
            sp.parse_snapshot([], "v")

    def test_dash_value_raises_and_names_it(self):
        with self.assertRaises(sp.ParseError) as cm:
            sp.parse_snapshot(synthetic(uniq="—"), "v")
        self.assertIn("unique_viewers", str(cm.exception))

    def test_zero_is_a_real_value(self):
        row = sp.parse_snapshot(synthetic(uniq="0"), "v")
        self.assertEqual(row["unique_viewers"], 0)

    def test_abbreviated_impressions_use_series_end_when_close(self):
        row = sp.parse_snapshot(synthetic(impr="1,6 N", series=[0, 900, 1684]), "v")
        self.assertEqual(row["impressions"], 1684)
        self.assertIs(row["impr_rounded"], False)

    def test_abbreviated_impressions_without_series_are_flagged(self):
        row = sp.parse_snapshot(synthetic(impr="1,6 N"), "v")
        self.assertEqual(row["impressions"], 1600)
        self.assertIs(row["impr_rounded"], True)

    def test_abbreviated_impressions_far_series_is_flagged(self):
        row = sp.parse_snapshot(synthetic(impr="1.6K", series=[0, 900, 1200]), "v")
        self.assertEqual(row["impressions"], 1600)
        self.assertIs(row["impr_rounded"], True)

    def test_exact_impressions_ignore_series(self):
        row = sp.parse_snapshot(synthetic(impr="159", series=[0, 5000]), "v")
        self.assertEqual(row["impressions"], 159)
        self.assertIs(row["impr_rounded"], False)


OLD = "D9awB0r8K5g__20261120T093441Z"    # video cu, nhieu so lieu (3 tab)
YOUNG = "mwKhntLxOlk__20261120T093118Z"  # video 6 gio tuoi, phan lon "Khong du du lieu"


def three_tabs(stem, folder=PARSE_DIR):
    return [load(stem, "", folder), load(stem, "_1", folder), load(stem, "_2", folder)]


def replace_text(payloads, old, new):
    """Ban sao sau, doi moi chuoi chua old thanh new (tra ve (ban sao, so lan doi))."""
    copy = json.loads(json.dumps(payloads))
    n = 0
    for p in copy:
        for node in sp._nodes(p):
            c = node.get("content")
            if isinstance(c, str) and old in c:
                node["content"] = c.replace(old, new)
                n += 1
    return copy, n


def table(headline, total=None, empty=False, sublabel="Lượt xem • Từ khi xuất bản", total_label="Tổng tỷ lệ lưu lượng truy cập"):
    """Dung the basic_table_card gia: headline, tuy chon dong tong, hoac 'Khong du du lieu'."""
    head = {"headline": {"content": headline}}
    if sublabel:
        head["sublabel"] = {"content": sublabel}
    items = []
    if empty:
        items.append({"basicContentViewModel": {"paragraphs": [{"text": {"content": "Không đủ dữ liệu"}}]}})
    elif total is not None:
        items.append({"listItemViewModel": {"title": {"content": total_label}, "trailingLabel": {"content": total}}})
        items.append({"listItemViewModel": {"title": {"content": "mot muc"}, "trailingLabel": {"content": "9,9%"}}})
    return {"accessibilityId": "analytics_hub.basic_table_card",
            "topContent": {"sectionHeaderViewModel": head},
            "bodyContent": {"linearLayoutViewModel": {"items": items}}}


def with_cards(*cards, ret=()):
    """synthetic() them cac the vao tab Reach, va cac the retention vao tab Engagement."""
    pl = synthetic()
    pl[0]["contents"].extend(cards)
    pl[1]["contents"].extend(ret)
    return pl


def ret_cards(apv="22%", apv_label="Tỷ lệ phần trăm đã xem trung bình", r30="65,4%", r30_label="Vẫn xem tiếp vào giây thứ 30"):
    def metric(aid, value, label):
        return {"accessibilityId": aid, "items": [
            {"simpleMetricViewModel": {"value": {"content": "3:23"}, "subtitle": {"content": "Thời lượng xem trung bình"}}},
            {"simpleMetricViewModel": {"value": {"content": value}, "subtitle": {"content": label}}}]}
    return (metric("analytics_hub.video_audience_retention_highlights_card.metrics", apv, apv_label),
            metric("analytics_hub.video_audience_retention_highlights_card.tab_metrics", r30, r30_label))


class StudioNewFieldsTest(unittest.TestCase):
    def test_old_video_full_fields(self):
        row = sp.parse_snapshot(three_tabs(OLD), "D9awB0r8K5g")
        self.assertAlmostEqual(row["apv_pct"], 0.22)
        self.assertAlmostEqual(row["ret_30s"], 0.654)
        self.assertIsNone(row["ret_60s"])
        self.assertAlmostEqual(row["src_search"], 0.003)
        self.assertAlmostEqual(row["src_suggested"], 0.012)
        self.assertEqual(row["src_other"], 0.0)
        self.assertIsNotNone(row["src_other"])
        self.assertIsNone(row["src_browse"])
        self.assertIs(row["test_running"], False)  # thu nghiem A/B da hoan tat

    def test_young_video_all_new_fields_none(self):
        row = sp.parse_snapshot(three_tabs(YOUNG), "mwKhntLxOlk")
        for col in ("apv_pct", "ret_30s", "ret_60s", "src_browse", "src_suggested", "src_search", "src_other"):
            self.assertIsNone(row[col], col)
        self.assertIs(row["test_running"], False)

    def test_missing_audience_tab_is_fine(self):
        row = sp.parse_snapshot(both_tabs(OLD), "v")
        self.assertAlmostEqual(row["apv_pct"], 0.22)

    def test_ab_status_running_vs_completed(self):
        pl = three_tabs(OLD)
        running, n = replace_text(pl, "Đã hoàn tất", "Đang chạy")
        self.assertEqual(n, 1)
        self.assertIs(sp.parse_snapshot(running, "v")["test_running"], True)
        for done in ("Completed", "Đã dừng", "Stopped"):
            edited, _ = replace_text(pl, "Đã hoàn tất", done)
            self.assertIs(sp.parse_snapshot(edited, "v")["test_running"], False, done)

    def test_ab_unknown_status_counts_as_running(self):
        pl = with_cards(table("Thử nghiệm A/B", sublabel="Tỷ lệ thời gian xem • Trạng thái lạ • 22 – 23 thg 9, 2026"))
        self.assertIs(sp.parse_snapshot(pl, "v")["test_running"], True)

    def test_ab_english_labels(self):
        done = with_cards(table("A/B test", sublabel="Watch time share • Completed • Sep 22 – 23, 2026"))
        self.assertIs(sp.parse_snapshot(done, "v")["test_running"], False)
        live = with_cards(table("Test & compare", sublabel="Watch time share • Running • Sep 22 – 23, 2026"))
        self.assertIs(sp.parse_snapshot(live, "v")["test_running"], True)

    def test_ab_absent_is_false(self):
        self.assertIs(sp.parse_snapshot(synthetic(), "v")["test_running"], False)

    def test_old_accessibility_id_guess_is_gone(self):
        extra = {"accessibilityId": "analytics_hub.thumbnail_test_compare_card"}
        self.assertIs(sp.parse_snapshot(synthetic(extra=extra), "v")["test_running"], False)

    def test_not_enough_data_card_gives_none(self):
        pl = with_cards(table("Cụm từ tìm kiếm trên YouTube", empty=True),
                        table("Nội dung giúp người xem tìm thấy video này", empty=True),
                        table("Trang web hoặc ứng dụng bên ngoài", empty=True))
        row = sp.parse_snapshot(pl, "v")
        for col in ("src_search", "src_suggested", "src_other"):
            self.assertIsNone(row[col], col)

    def test_traffic_totals_synthetic(self):
        pl = with_cards(table("Cụm từ tìm kiếm trên YouTube", "0,3%"),
                        table("Nội dung giúp người xem tìm thấy video này", "1,2%"),
                        table("Trang web hoặc ứng dụng bên ngoài", "0,0%"),
                        table("Danh sách phát có video này", "7,7%"))
        row = sp.parse_snapshot(pl, "v")
        self.assertAlmostEqual(row["src_search"], 0.003)
        self.assertAlmostEqual(row["src_suggested"], 0.012)
        self.assertEqual(row["src_other"], 0.0)
        self.assertIsNone(row["src_browse"])

    def test_traffic_english_labels(self):
        pl = with_cards(table("YouTube search terms", "4.5%", total_label="Total traffic share"),
                        table("Suggested videos", "1.0%", total_label="Total traffic share"),
                        table("External websites and apps", "2.0%", total_label="Total traffic share"))
        row = sp.parse_snapshot(pl, "v")
        self.assertAlmostEqual(row["src_search"], 0.045)
        self.assertAlmostEqual(row["src_suggested"], 0.01)
        self.assertAlmostEqual(row["src_other"], 0.02)

    def test_traffic_other_period_gives_none(self):
        pl = with_cards(table("Cụm từ tìm kiếm trên YouTube", "0,3%", sublabel="Lượt xem • 28 ngày qua"),
                        table("Suggested videos", "1.0%", sublabel="Views • Since published", total_label="Total traffic share"))
        row = sp.parse_snapshot(pl, "v")
        self.assertIsNone(row["src_search"])
        self.assertAlmostEqual(row["src_suggested"], 0.01)

    def test_headline_must_match_at_start_not_substring(self):
        pl = with_cards(table("Danh sách: Cụm từ tìm kiếm trên YouTube", "0,3%"),
                        table("Phân tích Thử nghiệm A/B cũ", sublabel="x • Đang chạy"))
        row = sp.parse_snapshot(pl, "v")
        self.assertIsNone(row["src_search"])
        self.assertIs(row["test_running"], False)

    def test_committed_fixtures_have_no_third_party_strings(self):
        blob = ""
        for stem in (OLD, YOUNG):
            for suf in ("", "_1", "_2"):
                with open(os.path.join(PARSE_DIR, "%s__youtubei_v1_browse%s.json" % (stem, suf)), encoding="utf-8") as fh:
                    blob += fh.read()
        for bad in ("dottifinder", "messaging", "Messenger", "elon", "ytimg", "LETHAL", "INSANE", "http"):
            self.assertNotIn(bad, blob, bad)

    def test_card_without_total_row_gives_none(self):
        pl = with_cards(table("Cụm từ tìm kiếm trên YouTube", "0,3%", total_label="Một nhãn khác"))
        self.assertIsNone(sp.parse_snapshot(pl, "v")["src_search"])

    def test_retention_synthetic_and_english(self):
        pl = with_cards(ret=ret_cards())
        row = sp.parse_snapshot(pl, "v")
        self.assertAlmostEqual(row["apv_pct"], 0.22)
        self.assertAlmostEqual(row["ret_30s"], 0.654)
        self.assertIsNone(row["ret_60s"])
        en = with_cards(ret=ret_cards("31.5%", "Average percentage viewed", "70.0%", "Still watching at 30 seconds"))
        row = sp.parse_snapshot(en, "v")
        self.assertAlmostEqual(row["apv_pct"], 0.315)
        self.assertAlmostEqual(row["ret_30s"], 0.7)

    def test_retention_unreadable_value_gives_none_not_error(self):
        row = sp.parse_snapshot(with_cards(ret=ret_cards(apv="—", r30="Không đủ dữ liệu")), "v")
        self.assertIsNone(row["apv_pct"])
        self.assertIsNone(row["ret_30s"])

    def test_retention_wrong_label_is_not_guessed(self):
        row = sp.parse_snapshot(with_cards(ret=ret_cards(apv_label="Nhãn lạ", r30_label="Vẫn xem tiếp vào giây thứ 60")), "v")
        self.assertIsNone(row["apv_pct"])
        self.assertIsNone(row["ret_30s"])


class ImportTest(unittest.TestCase):
    def test_module_has_no_heavy_imports(self):
        code = ("import sys; import studio_parse; "
                "bad = [m for m in sys.modules if m.split('.')[0] in ('playwright', 'googleapiclient', 'google')]; "
                "sys.exit(1 if bad else 0)")
        r = subprocess.run([sys.executable, "-c", code], cwd=HERE)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
