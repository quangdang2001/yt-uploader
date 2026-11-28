"""Test loop/heart_score.py (chay tu goc repo):  python -m pytest -q loop/test_heart_score.py"""
import csv
from pathlib import Path

from loop import heart_config as hc
from loop import heart_score as hs

ROOT = Path(__file__).resolve().parent.parent


def snap(age, ctr=0.05, impr=1000, views=500):
    return {"age_hours": age, "ctr": ctr, "impressions": impr, "views": views}


def led(vid="v1", slug="s1", title="Top Ten Guns", bright="0.7", **kw):
    r = {"video_id": vid, "slug": slug, "live_title": title, "thumb_brightness": bright,
         "verdict": "ok", "apv_pct": "40", "ret_30s": "70"}
    r.update(kw)
    return r


def book(slug="s1", **kw):
    m = {"tieu_de": "Top Ten Guns", "du_doan_7d": "1000-5000", "thumb_sang": "sang"}
    m.update(kw)
    return {slug: m}


def one(merged, ledger, snaps):
    out = hs.score(merged, ledger, snaps)
    assert len(out) == 1
    return out[0]


def test_constants_match_config():
    cfg = hc.load_config(ROOT / "loop" / "heart_config.json")
    assert list(hs.CTR_WINDOW) == cfg["cua_so_ctr_gio"]
    assert list(hs.VIEW_WINDOW) == cfg["cua_so_view_gio"]


def test_windows_pick_only_inside():
    r = one(book(), [led()], {"v1": [snap(40, ctr=0.09)]})
    assert r["ctr_24h"] == "" and r["impr_24h"] == ""
    assert "lo-moc-24h" in r["canh_bao"].split(";")
    r = one(book(), [led()], {"v1": [snap(20, ctr=0.06, impr=900), snap(40, ctr=0.09)]})
    assert r["ctr_24h"] == 0.06 and r["impr_24h"] == 900
    assert "lo-moc-24h" not in r["canh_bao"]
    r = one(book(), [led()], {"v1": [snap(230, views=9)]})
    assert r["views_7d"] == "" and "lo-moc-7d" in r["canh_bao"].split(";")
    r = one(book(), [led()], {"v1": [snap(10)]})  # chua toi han: khong canh bao
    assert r["canh_bao"] == ""


def test_match_by_slug_then_title():
    ledger = [led(vid="a", slug="s1", title="Other"), led(vid="b", slug="", title="  top TEN guns ")]
    snaps = {"a": [snap(24)], "b": [snap(24, ctr=0.02)]}
    assert one(book(), ledger, snaps)["video_id"] == "a"
    r = one(book(), [led(vid="b", slug="", title="  top TEN guns ")], snaps)
    assert r["video_id"] == "b" and "chua-co-video" not in r["canh_bao"]


def test_abandoned_slug_not_scored():
    r = one(book(), [led(vid="x", slug="other", title="Else")], {})
    assert r["canh_bao"] == "chua-co-video"
    for c in ("video_id", "ctr_24h", "impr_24h", "views_7d", "apv_pct", "ret_30s", "verdict", "ket_qua"):
        assert r[c] == ""
    assert one(book(), [], {})["canh_bao"] == "chua-co-video"


def test_live_title_changed_still_matched_by_slug():
    r = one(book(), [led(title="New Title")], {"v1": [snap(24)]})
    assert r["video_id"] == "v1"
    assert "lech-tieu-de" in r["canh_bao"].split(";")


def test_retitled_video_is_scored_under_the_style_of_its_live_title():
    # Chu kenh 2026-11-28: colt_python_cz ghi `gia-chuyen-hang` trong so, tieu de that tren kenh la lineup
    # ("... (10 Guns)") -> dem cho lineup, khong cho nhanh da len ke hoach.
    r = one(book(dong="gia-chuyen-hang"), [led(title="New Title", packaging="lineup")], {"v1": [snap(24)]})
    assert r["dong_that"] == "lineup"
    r = one(book(dong="gia-chuyen-hang"), [led(title="New Title", packaging="gia")], {"v1": [snap(24)]})
    assert r["dong_that"] == "gia-chuyen-hang"
    # tieu de doi sang kieu khong phai nhanh nao: khong biet kieu that -> khong dem cho bien dong
    r = one(book(dong="lineup"), [led(title="New Title", packaging="other")], {"v1": [snap(24)]})
    assert r["dong_that"] == ""
    # tieu de khong doi: giu nhanh cua so
    r = one(book(dong="lineup"), [led(packaging="gia")], {"v1": [snap(24)]})
    assert "dong_that" not in r
    assert "dong_that" not in hs.RESULT_COLUMNS


def test_ket_qua_bands():
    def kq(views, dd="1000-5000"):
        return one(book(du_doan_7d=dd), [led()], {"v1": [snap(168, views=views)]})["ket_qua"]
    assert kq(5001) == "tren" and kq(5000) == "trong" and kq(1000) == "trong" and kq(999) == "duoi"
    assert one(book(), [led()], {"v1": [snap(24)]})["ket_qua"] == ""
    assert kq(300, dd="") == ""


def test_thumb_mismatch_flagged():
    r = one(book(thumb_sang="sang"), [led(bright="0.2")], {"v1": [snap(24)]})
    assert "lech-thumb" in r["canh_bao"].split(";")
    r = one(book(thumb_sang="toi"), [led(bright="0.2")], {"v1": [snap(24)]})
    assert "lech-thumb" not in r["canh_bao"]
    r = one(book(thumb_sang="toi"), [led(bright="0.9")], {"v1": [snap(24)]})
    assert "lech-thumb" in r["canh_bao"]
    r = one(book(thumb_sang="sang"), [led(bright="")], {"v1": [snap(24)]})
    assert "lech-thumb" not in r["canh_bao"]


def test_write_results(tmp_path):
    rows = hs.score(book(), [led()], {"v1": [snap(24)]})
    p = tmp_path / hs.RESULT_NAME
    hs.write_results(p, rows)
    got = list(csv.DictReader(p.open(encoding="utf-8", newline="")))
    assert list(got[0]) == list(hs.RESULT_COLUMNS) and got[0]["slug"] == "s1"


def test_hook_apv_comes_from_the_7_day_snapshot_not_the_latest():
    ledger = [led(apv_pct="0.99")]                                  # the ledger holds the LATEST snapshot's apv
    snaps = {"v1": [snap(170, views=900) | {"apv_pct": 0.21}, snap(290, views=1500) | {"apv_pct": 0.33}]}
    r = one(book(), ledger, snaps)
    assert r["views_7d"] == 900
    assert r["apv_pct"] == 0.21


def test_hook_apv_blank_when_7_day_snapshot_has_none():
    r = one(book(), [led(apv_pct="0.99")], {"v1": [snap(170, views=900)]})
    assert r["apv_pct"] == ""


def test_title_fallback_skips_ledger_row_of_another_slug():
    other = led(vid="v9", slug="other-slug", title="Top Ten Guns")
    out = hs.score(book(), [other], {"v9": [snap(170)]})
    assert out[0]["canh_bao"] == "chua-co-video"
    unset = led(vid="v8", slug="", title="Top Ten Guns")
    assert one(book(), [unset], {"v8": [snap(170)]})["video_id"] == "v8"
