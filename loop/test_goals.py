"""Test loop/goals.py:  python -m pytest -q loop/test_goals.py"""
import json
from datetime import date

from loop import goals as g

GOALS = {
    "san_view_thang": 1000000, "san_tu_thang": "2026-12",
    "moc": [
        {"thang": "2026-12", "view_thang": 1000000, "sub_cuoi_thang": 8000, "doanh_thu_thang_usd": 4000,
         "video": {"view_7d_trung_vi": 10000, "so_video_100k_thang": 4, "ctr_24h_pct": 8.0, "giu_30s_pct": 72}},
        {"thang": "2027-03", "view_thang": 3000000, "sub_cuoi_thang": 26000, "doanh_thu_thang_usd": 12000,
         "video": {"view_7d_trung_vi": 25000, "so_video_100k_thang": 10, "ctr_24h_pct": 9.0, "giu_30s_pct": 75}},
    ],
    "video_troi": {"view_toi_thieu": 50000, "trong_ngay": 7, "so_chu_de_noi_tiep": 3, "han_gio": 48},
}


def test_load_goals_missing_or_broken(tmp_path):
    assert g.load_goals(tmp_path / "none.json") is None
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert g.load_goals(tmp_path / "bad.json") is None
    (tmp_path / "ok.json").write_text(json.dumps(GOALS), encoding="utf-8")
    assert g.load_goals(tmp_path / "ok.json")["san_view_thang"] == 1000000


def test_target_exact_month():
    t = g.target_for_month(GOALS, "2026-12")
    assert t["view_thang"] == 1000000 and t["noi_suy"] is False and t["thang"] == "2026-12"
    assert "sap_toi" not in t


def test_target_interpolates_linearly():
    t = g.target_for_month(GOALS, "2027-01")
    assert t["noi_suy"] is True
    assert abs(t["view_thang"] - 1666667) <= 1
    assert abs(t["sub_cuoi_thang"] - 14000) <= 1
    assert abs(t["video"]["view_7d_trung_vi"] - 15000) <= 1
    assert abs(t["video"]["ctr_24h_pct"] - 8.33) < 0.01
    t2 = g.target_for_month(GOALS, "2027-02")
    assert abs(t2["view_thang"] - 2333333) <= 1


def test_floor_applies_from_san_tu_thang():
    low = json.loads(json.dumps(GOALS))
    low["moc"][1]["view_thang"] = 500000   # interpolation would fall under the floor
    assert g.target_for_month(low, "2027-01")["view_thang"] == 1000000
    assert g.target_for_month(low, "2027-03")["view_thang"] == 500000   # an exact moc is taken as given


def test_month_before_first_moc_is_marked_upcoming():
    t = g.target_for_month(GOALS, "2026-11")
    assert t["sap_toi"] is True and t["view_thang"] == 1000000 and t["thang"] == "2026-11"


def test_month_after_last_moc_keeps_last():
    t = g.target_for_month(GOALS, "2027-06")
    assert t["view_thang"] == 3000000 and t["thang"] == "2027-06" and not t["noi_suy"]


def test_target_empty_goals():
    assert g.target_for_month(None, "2026-12") == {}


def _daily(start_day, views, subs=3, rev=10.0, month="2026-12"):
    return [{"ngay": f"{month}-{start_day + i:02d}", "luot_xem": v, "dang_ky_moi": subs, "doanh_thu": rev}
            for i, v in enumerate(views)]


def ledger_row(vid, pub, views_latest="", views_7d="", ctr="", ret="", title="t"):
    return {"video_id": vid, "live_title": title, "published_local": pub, "views_latest": str(views_latest),
            "views_7d": str(views_7d), "ctr_24h": str(ctr), "ret_30s": str(ret)}


def test_progress_pacing_and_need():
    daily = _daily(1, [30000] * 10)   # 10 days of 30k in December, today = Dec 11
    p = g.progress(GOALS, daily, {"tong_dang_ky": 5000}, [], date(2026, 12, 11))
    assert p["thang"] == "2026-12" and p["ngay_co_so"] == 10 and p["ngay_trong_thang"] == 31
    assert p["view_thang"] == 300000
    assert p["du_bao_view_thang"] == 930000
    assert p["trang_thai"] == "thieu"
    assert p["view_can_moi_ngay"] == 33334   # ceil(700000 / 21), 21 days left incl. Dec 11
    assert p["view_28_ngay"] == 300000
    assert p["sub_hien_tai"] == 5000 and p["du_bao_sub_cuoi_thang"] == 5000 + 3 * 21
    assert p["doanh_thu_thang"] == 100.0 and p["du_bao_doanh_thu_thang"] == 310.0
    assert p["rpm_7_ngay"] == round(70.0 / 210000 * 1000, 2)


def test_studio_month_revenue_replaces_a_short_daily_sum():
    # Studio fills the newest revenue days late: 8 of 10 December days have revenue (80), Studio's month says 140.
    daily = _daily(1, [30000] * 10)
    daily[-1]["doanh_thu"] = daily[-2]["doanh_thu"] = None
    p = g.progress(GOALS, daily, {}, [], date(2026, 12, 11), month_revenue={"2026-12": 140.0, "2026-11": 999.0})
    assert p["doanh_thu_thang"] == 140.0 and p["du_bao_doanh_thu_thang"] == 434.0   # 140 / 10 view days * 31
    rows = {r["ma"]: r for r in p["chi_so"]}
    assert rows["doanh_thu_thang"]["hien_tai"] == 140.0
    # a smaller or missing Studio figure never lowers the daily sum
    low = g.progress(GOALS, daily, {}, [], date(2026, 12, 11), month_revenue={"2026-12": 50.0})
    assert low["doanh_thu_thang"] == 80.0
    assert g.progress(GOALS, daily, {}, [], date(2026, 12, 11))["doanh_thu_thang"] == 80.0


def test_need_per_day_is_ceiling():
    p = g.progress(GOALS, _daily(1, [30000] * 10), {}, [], date(2026, 12, 11))
    assert p["view_can_moi_ngay"] == 33334


def test_on_track_when_forecast_meets_target():
    p = g.progress(GOALS, _daily(1, [40000] * 10), {"tong_dang_ky": 1}, [], date(2026, 12, 11))
    assert p["du_bao_view_thang"] == 1240000 and p["trang_thai"] == "dung-huong"
    rows = {r["ma"]: r for r in p["chi_so"]}
    assert rows["view_thang"]["dat"] is True and rows["view_thang"]["nhom"] == "kenh"
    assert rows["sub_cuoi_thang"]["dat"] is False


def test_falls_back_to_last_month_with_data():
    p = g.progress(GOALS, _daily(25, [20000] * 6, month="2026-11"), {}, [], date(2026, 12, 1))
    assert p["thang"] == "2026-11" and p["ngay_co_so"] == 6 and p["ngay_trong_thang"] == 30
    assert p["view_thang"] == 120000


def test_rpm_none_without_views():
    d = [{"ngay": "2026-12-01", "luot_xem": 0, "dang_ky_moi": 0, "doanh_thu": 0.0}]
    assert g.progress(GOALS, d, {}, [], date(2026, 12, 2))["rpm_7_ngay"] is None


def test_ledger_medians_100k_and_window():
    today = date(2026, 12, 20)
    rows = [
        ledger_row("a", "2026-12-18 09:00", 12000, 0, 0.08, 0.70),                 # too young for 7d
        ledger_row("b", "2026-12-05 09:00", 150000, 120000, 0.10, 0.74),
        ledger_row("c", "2026-12-08 09:00", 9000, 9000, 0.06, 0.66),
        ledger_row("d", "2026-12-10 09:00", 20000, 20000, 0.09, 0.80),
        ledger_row("e", "2026-10-01 09:00", 900000, 800000, 0.30, 0.90),            # outside 28 days
    ]
    rows[0]["views_7d"] = ""
    p = g.progress(GOALS, _daily(1, [30000] * 19), {}, rows, today)
    assert p["view_7d_trung_vi"] == 20000                  # median(120000, 9000, 20000)
    assert p["so_video_100k_thang"] == 1                   # only b, published this month
    assert p["ctr_24h_trung_vi"] == 8.5                    # median(.08,.10,.06,.09)
    assert p["giu_30s_trung_vi"] == 72.0                   # median(.70,.74,.66,.80)
    r = {x["ma"]: x for x in p["chi_so"]}
    assert r["so_video_100k_thang"]["dat"] is False and r["so_video_100k_thang"]["muc_tieu"] == 4
    assert r["ctr_24h_trung_vi"]["dat"] is True and r["view_7d_trung_vi"]["dat"] is True
    assert r["giu_30s_trung_vi"]["dat"] is True and r["giu_30s_trung_vi"]["du_bao"] is None


def test_video_troi():
    today = date(2026, 12, 20)
    rows = [ledger_row("hot", "2026-12-17 09:00", 80000, title="Hot one"),
            ledger_row("old", "2026-12-01 09:00", 900000),
            ledger_row("meh", "2026-12-19 09:00", 49999)]
    p = g.progress(GOALS, _daily(1, [30000] * 19), {}, rows, today)
    assert p["video_troi"] == []  # quy tac 50.000 view da bo (chu kenh 2026-11-27)


def test_empty_inputs_are_no_base_not_errors():
    p = g.progress(GOALS, [], {}, [], date(2026, 12, 1))
    assert p["trang_thai"] == "chua-co-so"
    assert g.progress(None, [], None, None, date(2026, 12, 1))["trang_thai"] == "chua-co-so"
    p = g.progress(GOALS, [{"ngay": "2026-12-01", "luot_xem": None}], {}, [], date(2026, 12, 2))
    assert p["trang_thai"] == "chua-co-so"


def _doc(days):
    pts = lambda f: [[d, f(d, v)] for d, v in days]   # noqa: E731
    card = {"id": "video_views", "value": 1, "rows": [], "metrics": [],
            "series": [{"label": "x", "points": [[d, v] for d, v in days]}]}
    return {"captured_at_utc": "2026-11-21T11:00:00Z", "tabs": {"hub_content": [card]}}


def test_channel_daily_unions_docs_newest_wins(tmp_path):
    ch = tmp_path / "channel"
    ch.mkdir()
    (ch / "2026-11-20.json").write_text(json.dumps(_doc([("2026-11-18", 100), ("2026-11-19", 200)])), encoding="utf-8")
    (ch / "2026-11-21.json").write_text(json.dumps(_doc([("2026-11-19", 250), ("2026-11-20", 300)])), encoding="utf-8")
    (ch / "2026-11-22.json").write_text("{broken", encoding="utf-8")
    rows = g.channel_daily(tmp_path)
    assert [(r["ngay"], r["luot_xem"]) for r in rows] == [("2026-11-18", 100), ("2026-11-19", 250),
                                                          ("2026-11-20", 300)]
    assert g.channel_daily(tmp_path / "nothing") == []


VN = {**GOALS, "video_noi": {"ctr_toi_thieu": 0.07, "hien_thi_toi_thieu": 30000, "trong_gio": 48, "trong_ngay": 7,
                             "so_chu_de_noi_tiep": 2, "han_gio": 24}}


def snap(age, impr, ctr):
    return {"age_hours": age, "impressions": impr, "ctr": ctr}


def test_pushed_video_by_snapshot():
    today = date(2026, 12, 20)
    rows = [ledger_row("hot", "2026-12-19 10:00", 21300, title="Ruger"),
            ledger_row("lowctr", "2026-12-19 10:00", 9000),
            ledger_row("late", "2026-12-17 10:00", 9000),
            ledger_row("old", "2026-12-10 10:00", 9000)]
    snaps = {"hot": [snap(13.9, 43494, 0.078), snap(31.6, 87015, 0.084)],
             "lowctr": [snap(40, 90000, 0.069)],
             "late": [snap(40, 29999, 0.06), snap(60, 50000, 0.06)],     # CTR < 7%; 30k only after 24 h
             "old": [snap(20, 9000, 0.05)]}                               # khong dat nguong
    p = g.progress(VN, _daily(1, [30000] * 19), {}, rows, today, snaps=snaps)
    assert [x["video_id"] for x in p["video_troi"]] == ["hot"]
    assert p["video_troi"][0] == {"video_id": "hot", "tieu_de": "Ruger", "view": 21300, "tuoi_ngay": 1,
                                  "ly_do": "ctr", "ctr_pct": 7.8, "hien_thi": 43494, "gio_doc": 13.9}


def test_pushed_video_stays_flagged_after_it_ages_past_7_days():
    # Ruger 2027 (mwKhntLxOlk): dat nguong 20/11 luc 13,9 gio; ngay 27/11 da 10 ngay tuoi van phai nam trong nhan_ban
    today = date(2026, 12, 20)
    rows = [ledger_row("ruger", "2026-12-10 10:00", 88200, title="Ruger")]
    snaps = {"ruger": [snap(13.9, 43494, 0.078), snap(31.6, 87015, 0.084)]}
    p = g.progress(VN, _daily(1, [30000] * 19), {}, rows, today, snaps=snaps)
    assert [x["video_id"] for x in p["video_troi"]] == ["ruger"]
    assert p["video_troi"][0]["tuoi_ngay"] == 10 and p["video_troi"][0]["gio_doc"] == 13.9


def test_pushed_by_impressions_within_24h_or_ctr_within_48h():
    today = date(2026, 12, 20)
    rows = [ledger_row("impr24", "2026-12-19 10:00", 9000), ledger_row("impr30", "2026-12-19 10:00", 9000),
            ledger_row("ctr_and", "2026-12-19 10:00", 9000)]
    snaps = {"impr24": [snap(20, 25000, 0.05)],     # 20k+ trong 24 gio -> dang de xuat (dieu kien 2)
             "impr30": [snap(30, 40000, 0.05)],     # 30k sau 24 gio, CTR < 7% -> khong
             "ctr_and": [snap(30, 25000, 0.09)]}    # CTR 9% nhung 25k < 30k trong 48 gio -> khong (dieu kien 1 la VA)
    p = g.progress(VN, _daily(1, [30000] * 19), {}, rows, today, snaps=snaps)
    assert [x["video_id"] for x in p["video_troi"]] == ["impr24"]


def test_pushed_video_ledger_fallback():
    today = date(2026, 12, 20)
    hot = {**ledger_row("hot", "2026-12-19 10:00", 21300), "age_h_latest": "31.63", "ctr_latest": "0.084",
           "impr_latest": "87015", "ctr_24h": "", "impr_24h": ""}
    big = {**ledger_row("big", "2026-12-16 10:00", 80000), "ctr_24h": "0.08", "impr_24h": "40000"}
    p = g.progress(VN, _daily(1, [30000] * 19), {}, [hot, big], today)
    by = {x["video_id"]: x for x in p["video_troi"]}
    assert by["hot"]["ly_do"] == "ctr" and by["hot"]["hien_thi"] == 87015
    assert by["big"]["ly_do"] == "ctr" and by["big"]["gio_doc"] == 24.0


def test_no_video_noi_config_keeps_old_behaviour():
    today = date(2026, 12, 20)
    rows = [ledger_row("hot", "2026-12-19 10:00", 21300)]
    p = g.progress(GOALS, _daily(1, [30000] * 19), {}, rows, today, snaps={"hot": [snap(30, 90000, 0.09)]})
    assert p["video_troi"] == []


def test_pushed_relative_to_channel_ctr():
    # Mossberg 2027: CTR 5,9% < 7% va 9k hien thi < 20k, nhung ~2 lan trung vi CTR kenh (2,6%) -> troi so voi kenh
    today = date(2026, 12, 20)
    base = [{**ledger_row(f"b{i}", "2026-12-12 10:00", 500), "ctr_24h": "0.026", "impr_24h": "3000"} for i in range(6)]
    rel = {**ledger_row("rel", "2026-12-19 10:00", 5700), "ctr_24h": "0.059", "impr_24h": "9276"}
    meh = {**ledger_row("meh", "2026-12-19 10:00", 900), "ctr_24h": "0.04", "impr_24h": "9000"}      # 1,5 lan
    small = {**ledger_row("small", "2026-12-19 10:00", 90), "ctr_24h": "0.08", "impr_24h": "1200"}   # duoi san hien thi
    vn = {**VN["video_noi"], "ctr_tuong_doi": 2.0, "hien_thi_tuong_doi_toi_thieu": 5000}
    p = g.progress({**VN, "video_noi": vn}, _daily(1, [30000] * 19), {}, base + [rel, meh, small], today)
    assert [x["video_id"] for x in p["video_troi"]] == ["rel"]
    p = g.progress(VN, _daily(1, [30000] * 19), {}, base[:3] + [rel], today)   # < 5 video lam nen: khong bat
    assert p["video_troi"] == []
