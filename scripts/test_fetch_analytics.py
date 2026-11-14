#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit test cho fetch_analytics.py — khong goi mang, khong ton quota.

Chay:
    cd scripts
    python -m unittest test_fetch_analytics -v

Trong tam:
  - cua so ngay mac dinh dung 28 ngay TINH CA hom nay
  - gop analytics-daily.csv: ngay trong cua so bi ghi de, ngay cu duoc giu
  - kenh chua kiem tien (403) van lay duoc cac metric con lai
  - title video hong thi bao cao van ra, chi thieu cot title
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_analytics as fa  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class FakeResp:
    def __init__(self, status: int):
        self.status = status
        self.reason = "test"


def http_error(status: int, reason: str) -> HttpError:
    body = json.dumps({"error": {"code": status, "errors": [{"reason": reason}]}})
    return HttpError(FakeResp(status), body.encode())


def report(cols: list[str], rows: list[list]) -> dict:
    return {"columnHeaders": [{"name": c} for c in cols], "rows": rows}


class FakeAnalytics:
    """Gia lap analytics.reports().query(...).execute()."""

    def __init__(self, responses):
        # responses: list[dict | Exception], tra lan luot theo thu tu goi
        self.responses = list(responses)
        self.calls = []

    def reports(self):
        return self

    def query(self, **kw):
        self.calls.append(kw)
        item = self.responses.pop(0)
        return mock.Mock(execute=mock.Mock(
            side_effect=item if isinstance(item, Exception) else None,
            return_value=None if isinstance(item, Exception) else item))


class FakeYouTube:
    def __init__(self, items, error=None):
        self.items = items
        self.error = error
        self.calls = []

    def videos(self):
        return self

    def list(self, **kw):
        self.calls.append(kw)
        if self.error:
            return mock.Mock(execute=mock.Mock(side_effect=self.error))
        ids = kw["id"].split(",")
        return mock.Mock(execute=mock.Mock(return_value={
            "items": [i for i in self.items if i["id"] in ids]}))


# --------------------------------------------------------------- cua so ngay


class TestDateRange(unittest.TestCase):
    TODAY = date(2026, 11, 14)

    def test_mac_dinh_28_ngay_tinh_ca_hom_nay(self):
        start, end = fa.date_range(fa.DEFAULT_DAYS, today=self.TODAY)
        self.assertEqual(end, "2026-11-14")
        self.assertEqual(start, "2026-10-18")
        # 18/10 .. 14/11 ke ca hai dau = dung 28 ngay
        self.assertEqual(
            (date.fromisoformat(end) - date.fromisoformat(start)).days + 1, 28)

    def test_mac_dinh_la_28(self):
        self.assertEqual(fa.DEFAULT_DAYS, 28)
        self.assertEqual(fa.parse_args(["--channel", "k"]).days, 28)

    def test_mot_ngay(self):
        self.assertEqual(fa.date_range(1, today=self.TODAY),
                         ("2026-11-14", "2026-11-14"))

    def test_end_date_tu_dat(self):
        self.assertEqual(fa.date_range(7, end="2026-01-31"),
                         ("2026-01-25", "2026-01-31"))

    def test_start_date_de_bep_days(self):
        self.assertEqual(
            fa.date_range(28, end="2026-01-31", start="2026-01-01"),
            ("2026-01-01", "2026-01-31"))

    def test_start_sau_end_thi_loi(self):
        with self.assertRaises(ValueError):
            fa.date_range(7, end="2026-01-01", start="2026-02-01")

    def test_days_khong_duong_thi_loi(self):
        with self.assertRaises(ValueError):
            fa.date_range(0, today=self.TODAY)

    def test_argparse_chan_days_khong_duong(self):
        for bad in ("0", "-1"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    fa.parse_args(["--channel", "k", "--days", bad])

    def test_argparse_chan_ngay_sai_dinh_dang(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                fa.parse_args(["--channel", "k", "--start-date", "31/01/2026"])


# -------------------------------------------------------------------- parse


class TestParseReport(unittest.TestCase):
    def test_ghep_cot_voi_dong(self):
        res = report(["day", "views"], [["2026-11-01", 10], ["2026-11-02", 20]])
        self.assertEqual(fa.parse_report(res),
                         [{"day": "2026-11-01", "views": 10},
                          {"day": "2026-11-02", "views": 20}])

    def test_khong_co_dong_nao(self):
        # API bo han field "rows" khi rong — khong duoc vo o day
        self.assertEqual(fa.parse_report({"columnHeaders": [{"name": "day"}]}), [])
        self.assertEqual(fa.parse_report({}), [])
        self.assertEqual(fa.parse_report({"columnHeaders": [], "rows": None}), [])


# ------------------------------------------------------------ doanh thu 403


class TestQueryWithMoney(unittest.TestCase):
    def test_co_quyen_thi_lay_ca_doanh_thu(self):
        an = FakeAnalytics([report(
            ["day"] + fa.BASE_METRICS + fa.MONEY_METRICS,
            [["2026-11-01"] + [1] * (len(fa.BASE_METRICS) + 1)])])
        rows, metrics = fa.query_with_money(an, "2026-11-01", "2026-11-01",
                                            dimensions="day")
        self.assertIn("estimatedRevenue", metrics)
        self.assertEqual(len(an.calls), 1)
        self.assertEqual(rows[0]["estimatedRevenue"], 1)

    def test_403_thi_thu_lai_khong_co_doanh_thu(self):
        an = FakeAnalytics([
            http_error(403, "forbidden"),
            report(["day"] + fa.BASE_METRICS,
                   [["2026-11-01"] + [2] * len(fa.BASE_METRICS)]),
        ])
        rows, metrics = fa.query_with_money(an, "2026-11-01", "2026-11-01",
                                            dimensions="day")
        self.assertEqual(metrics, fa.BASE_METRICS)
        self.assertNotIn("estimatedRevenue", metrics)
        self.assertEqual(len(an.calls), 2)
        self.assertNotIn("estimatedRevenue", an.calls[1]["metrics"])
        self.assertEqual(rows[0]["views"], 2)

    def test_loi_khac_403_thi_nem_ra(self):
        an = FakeAnalytics([http_error(500, "backendError")])
        with self.assertRaises(HttpError):
            fa.query_with_money(an, "2026-11-01", "2026-11-01", dimensions="day")


# ---------------------------------------------------------------------- csv


class TestToCsv(unittest.TestCase):
    def test_escape_dau_phay_va_nhay(self):
        text = fa.to_csv(["a", "b"], [{"a": 'co, phay va "nhay"', "b": 1}])
        self.assertEqual(text.splitlines()[1], '"co, phay va ""nhay""",1')

    def test_thieu_cot_thi_de_rong(self):
        self.assertEqual(fa.to_csv(["a", "b"], [{"a": 1}]), "a,b\n1,\n")

    def test_none_thanh_rong(self):
        self.assertEqual(fa.to_csv(["a", "b"], [{"a": None, "b": 1}]), "a,b\n,1\n")


class TestMergeDaily(unittest.TestCase):
    COLS = ["ngay", "luot_xem"]

    def test_file_rong_thi_chi_co_dong_moi(self):
        out = fa.merge_daily("", self.COLS, [{"ngay": "2026-11-01", "luot_xem": 5}])
        self.assertEqual(out, "ngay,luot_xem\n2026-11-01,5\n")

    def test_ngay_trung_bi_ghi_de(self):
        # YouTube chot so cham vai ngay: so cu PHAI bi thay, khong duoc giu
        old = "ngay,luot_xem\n2026-11-01,5\n"
        out = fa.merge_daily(old, self.COLS, [{"ngay": "2026-11-01", "luot_xem": 99}])
        self.assertEqual(out, "ngay,luot_xem\n2026-11-01,99\n")

    def test_ngay_cu_ngoai_cua_so_duoc_giu(self):
        old = "ngay,luot_xem\n2026-01-01,1\n2026-11-01,5\n"
        out = fa.merge_daily(old, self.COLS, [{"ngay": "2026-11-02", "luot_xem": 7}])
        self.assertEqual(out.splitlines(),
                         ["ngay,luot_xem", "2026-01-01,1", "2026-11-01,5",
                          "2026-11-02,7"])

    def test_sap_xep_theo_ngay(self):
        old = "ngay,luot_xem\n2026-11-03,3\n"
        out = fa.merge_daily(old, self.COLS, [{"ngay": "2026-11-01", "luot_xem": 1}])
        self.assertEqual([l.split(",")[0] for l in out.splitlines()[1:]],
                         ["2026-11-01", "2026-11-03"])

    def test_header_cu_khac_thi_viet_lai_tu_dau(self):
        # Doi bo metric giua chung: gop nham cot con te hon la mat lich su cu
        old = "ngay,views,likes\n2026-11-01,5,1\n"
        out = fa.merge_daily(old, self.COLS, [{"ngay": "2026-11-02", "luot_xem": 7}])
        self.assertEqual(out, "ngay,luot_xem\n2026-11-02,7\n")

    def test_du_lieu_cu_co_dau_phay_van_doc_duoc(self):
        cols = ["ngay", "ghi_chu"]
        old = fa.to_csv(cols, [{"ngay": "2026-11-01", "ghi_chu": "a,b"}])
        out = fa.merge_daily(old, cols, [{"ngay": "2026-11-02", "ghi_chu": "c"}])
        self.assertIn('2026-11-01,"a,b"', out)


# ------------------------------------------------------------------- titles


class TestFetchTitles(unittest.TestCase):
    def test_cat_theo_lo_50(self):
        ids = [f"v{i}" for i in range(120)]
        yt = FakeYouTube([{"id": i, "snippet": {"title": i.upper(),
                                                "publishedAt": "2026-01-02T03:04:05Z"}}
                          for i in ids])
        out = fa.fetch_titles(yt, ids)
        self.assertEqual(len(yt.calls), 3)
        self.assertEqual(len(out), 120)
        self.assertEqual(out["v7"], {"title": "V7", "ngay_dang": "2026-01-02"})

    def test_thieu_snippet_khong_vo(self):
        yt = FakeYouTube([{"id": "v1", "snippet": {}}])
        self.assertEqual(fa.fetch_titles(yt, ["v1"]),
                         {"v1": {"title": "", "ngay_dang": ""}})


class TestBuildVideos(unittest.TestCase):
    def _an(self):
        return FakeAnalytics([report(
            ["video"] + fa.BASE_METRICS + fa.MONEY_METRICS,
            [["v1"] + [3] * (len(fa.BASE_METRICS) + 1)])])

    def test_ghep_title_va_cua_so(self):
        yt = FakeYouTube([{"id": "v1", "snippet": {"title": "Khau sung 199$",
                                                   "publishedAt": "2026-10-20T00:00:00Z"}}])
        cols, rows = fa.build_videos(self._an(), yt, "2026-10-18", "2026-11-14")
        self.assertEqual(cols[:5],
                         ["video_id", "title", "ngay_dang", "tu_ngay", "den_ngay"])
        self.assertEqual(rows[0]["title"], "Khau sung 199$")
        self.assertEqual(rows[0]["ngay_dang"], "2026-10-20")
        self.assertEqual(rows[0]["tu_ngay"], "2026-10-18")
        self.assertEqual(rows[0]["den_ngay"], "2026-11-14")
        self.assertEqual(rows[0]["luot_xem"], 3)

    def test_title_hong_thi_bao_cao_van_ra(self):
        # Mat title dat hon han mat ca bao cao -> chi de trong cot title
        yt = FakeYouTube([], error=http_error(500, "backendError"))
        _, rows = fa.build_videos(self._an(), yt, "2026-10-18", "2026-11-14")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "")
        self.assertEqual(rows[0]["luot_xem"], 3)

    def test_sap_xep_giam_dan_theo_luot_xem(self):
        an = self._an()
        fa.build_videos(an, FakeYouTube([]), "2026-10-18", "2026-11-14")
        self.assertEqual(an.calls[0]["sort"], "-views")
        self.assertEqual(an.calls[0]["dimensions"], "video")
        self.assertEqual(an.calls[0]["maxResults"], fa.VIDEO_ROWS)


class TestBuildDaily(unittest.TestCase):
    def test_cot_tieng_viet_va_sort_theo_ngay(self):
        an = FakeAnalytics([report(
            ["day"] + fa.BASE_METRICS + fa.MONEY_METRICS,
            [["2026-11-01"] + [4] * (len(fa.BASE_METRICS) + 1)])])
        cols, rows = fa.build_daily(an, "2026-10-18", "2026-11-14")
        self.assertEqual(cols[0], "ngay")
        self.assertIn("doanh_thu_usd", cols)
        self.assertEqual(rows[0]["ngay"], "2026-11-01")
        self.assertEqual(an.calls[0]["sort"], "day")
        self.assertEqual(an.calls[0]["ids"], "channel==MINE")
        self.assertEqual(an.calls[0]["startDate"], "2026-10-18")
        self.assertEqual(an.calls[0]["endDate"], "2026-11-14")


class TestScopes(unittest.TestCase):
    def test_co_scope_analytics(self):
        self.assertIn("https://www.googleapis.com/auth/yt-analytics.readonly",
                      fa.ANALYTICS_SCOPES)
        self.assertIn(
            "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
            fa.ANALYTICS_SCOPES)

    def test_token_analytics_rieng_khong_de_len_token_cu(self):
        args = fa.parse_args(["--channel", "k"])
        self.assertNotEqual(args.token_analytics, args.token_youtube)
        self.assertNotEqual(args.token_analytics, args.token_drive)


if __name__ == "__main__":
    unittest.main(verbosity=2)
