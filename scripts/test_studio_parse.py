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


def synthetic(impr="159", ctr="10,1%", views="114", uniq="0", avd="3:02", extra=None, series=None):
    reach = [
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
        reach[0] = {"accessibilityId": "analytics_hub.VideoReachKeyMetricImpressionsCard", "items": [reach[0], chart]}
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
            "1,6 N": 1600, "1,6 N": 1600,
            "3:02": 182.0, "1:02:03": 3723.0,
        }
        for text, want in cases.items():
            self.assertAlmostEqual(sp.parse_number(text), want, places=9, msg=text)

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
        row = sp.parse_snapshot(both_tabs(RUGER, folder), "mwKhntLxOlk", "since_publish")
        self.assertEqual(row["impressions"], 159)
        self.assertIs(row["impr_rounded"], False)
        self.assertAlmostEqual(row["ctr"], 0.101)
        self.assertEqual(row["views"], 114)
        self.assertEqual(row["unique_viewers"], 0)
        self.assertEqual(row["avd_s"], 182.0)
        return row

    def check_lever(self, folder):
        row = sp.parse_snapshot(both_tabs(LEVER, folder), "5q637XpLagg", "since_publish")
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
        self.assertEqual(row["period"], "since_publish")
        self.check_lever(PARSE_DIR)

    def test_tab_order_does_not_matter(self):
        a = sp.parse_snapshot(both_tabs(RUGER), "v", "p")
        b = sp.parse_snapshot(list(reversed(both_tabs(RUGER))), "v", "p")
        self.assertEqual(a, b)

    def test_columns_and_unparsed_are_none(self):
        row = sp.parse_snapshot(both_tabs(RUGER), "v", "p")
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
        payloads[0]["contents"].pop(1)  # bo views
        with self.assertRaises(sp.ParseError) as cm:
            sp.parse_snapshot(payloads, "v", "p")
        self.assertIn("views", str(cm.exception))

    def test_empty_payloads_raise(self):
        with self.assertRaises(sp.ParseError):
            sp.parse_snapshot([], "v", "p")

    def test_dash_value_raises_and_names_it(self):
        with self.assertRaises(sp.ParseError) as cm:
            sp.parse_snapshot(synthetic(uniq="—"), "v", "p")
        self.assertIn("unique_viewers", str(cm.exception))

    def test_zero_is_a_real_value(self):
        row = sp.parse_snapshot(synthetic(uniq="0"), "v", "p")
        self.assertEqual(row["unique_viewers"], 0)

    def test_abbreviated_impressions_use_series_end_when_close(self):
        row = sp.parse_snapshot(synthetic(impr="1,6 N", series=[0, 900, 1684]), "v", "p")
        self.assertEqual(row["impressions"], 1684)
        self.assertIs(row["impr_rounded"], False)

    def test_abbreviated_impressions_without_series_are_flagged(self):
        row = sp.parse_snapshot(synthetic(impr="1,6 N"), "v", "p")
        self.assertEqual(row["impressions"], 1600)
        self.assertIs(row["impr_rounded"], True)

    def test_abbreviated_impressions_far_series_is_flagged(self):
        row = sp.parse_snapshot(synthetic(impr="1.6K", series=[0, 900, 1200]), "v", "p")
        self.assertEqual(row["impressions"], 1600)
        self.assertIs(row["impr_rounded"], True)

    def test_exact_impressions_ignore_series(self):
        row = sp.parse_snapshot(synthetic(impr="159", series=[0, 5000]), "v", "p")
        self.assertEqual(row["impressions"], 159)
        self.assertIs(row["impr_rounded"], False)

    def test_test_running_flag(self):
        extra = {"accessibilityId": "analytics_hub.thumbnail_test_compare_card"}
        row = sp.parse_snapshot(synthetic(extra=extra), "v", "p")
        self.assertIs(row["test_running"], True)
        extra = {"accessibilityId": "analytics_hub.video_likes_comparison"}
        self.assertIs(sp.parse_snapshot(synthetic(extra=extra), "v", "p")["test_running"], False)


class ImportTest(unittest.TestCase):
    def test_module_has_no_heavy_imports(self):
        code = ("import sys; import studio_parse; "
                "bad = [m for m in sys.modules if m.split('.')[0] in ('playwright', 'googleapiclient', 'google')]; "
                "sys.exit(1 if bad else 0)")
        r = subprocess.run([sys.executable, "-c", code], cwd=HERE)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
