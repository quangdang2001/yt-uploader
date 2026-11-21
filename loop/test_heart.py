"""Test loop/heart.py (chay tu goc repo):  python -m pytest -q loop/test_heart.py"""
import copy
import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from loop import heart as h
from loop import heart_config as hc

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 22)


@pytest.fixture
def cfg():
    return hc.load_config(ROOT / "loop" / "heart_config.json")


def scored_rows(var, arm, values, start=0, metric="views_7d"):
    """Tra ve (scored, merged) cho cac video cua mot nhanh."""
    scored, merged = [], {}
    for i, v in enumerate(values):
        slug = f"{var}-{arm}-{start + i}"
        row = {"slug": slug, "video_id": slug, "views_7d": "", "apv_pct": ""}
        row[metric] = v
        scored.append(row)
        merged[slug] = {var: arm}
    return scored, merged


def combine(*parts):
    scored, merged = [], {}
    for s, m in parts:
        scored += s
        merged.update(m)
    return scored, merged


def empty_stats(cfg):
    return h.arm_stats([], {}, cfg)


def test_stats_ignore_unscored_and_unknown_arms(cfg, capsys):
    s1, m1 = scored_rows("thumb_sang", "sang", [1000, 2000, 3000])
    s2, m2 = scored_rows("thumb_sang", "sang", ["", ""], start=10)   # chua cham
    s3, m3 = scored_rows("thumb_sang", "xanh-la", [500], start=20)   # nhanh khong co trong config
    scored, merged = combine((s1, m1), (s2, m2), (s3, m3))
    stats = h.arm_stats(scored, merged, cfg)
    assert stats["thumb_sang"]["sang"]["n"] == 3
    assert "xanh-la" not in stats["thumb_sang"]
    assert stats["thumb_sang"]["toi"]["n"] == 0
    assert stats["thumb_sang"]["toi"]["trang_thai"] == "chua-du"
    out = capsys.readouterr().out
    assert "heart: nhánh lạ thumb_sang=xanh-la (bỏ qua)" in out


def test_stats_use_apv_for_hook(cfg):
    scored, merged = scored_rows("hook", "su-viec-hang", [0.2, 0.3, 0.4], metric="apv_pct")
    st = h.arm_stats(scored, merged, cfg)["hook"]["su-viec-hang"]
    assert st["n"] == 3 and st["trung_vi"] == pytest.approx(0.3)
    scored, merged = scored_rows("thumb_sang", "sang", [100, 1000, 10000])
    st = h.arm_stats(scored, merged, cfg)["thumb_sang"]["sang"]
    assert st["trung_vi"] == pytest.approx(math.log(1000))


def test_winner_needs_n_thang(cfg):
    two, m2 = scored_rows("thumb_sang", "toi", [50000, 60000])
    three, m3 = scored_rows("thumb_sang", "sang", [100, 200, 300])
    stats = h.arm_stats(*combine((two, m2), (three, m3)), cfg)
    assert h.winners(stats, cfg)["thumb_sang"] == "sang"   # toi cao hon nhung n=2
    assert stats["thumb_sang"]["sang"]["trang_thai"] == "thang"
    assert stats["thumb_sang"]["toi"]["trang_thai"] == "dang-thu"
    assert h.winners(empty_stats(cfg), cfg)["thumb_sang"] == hc.default(cfg, "thumb_sang")


def test_kill_rule_rank_based(cfg):
    win, mw = scored_rows("thumb_sang", "sang", [1000, 2000, 3000, 4000, 5000])
    for n, expect in ((6, "loai"), (5, "dang-thu")):
        lose, ml = scored_rows("thumb_sang", "toi", [10] * n)
        stats = h.arm_stats(*combine((win, mw), (lose, ml)), cfg)
        assert stats["thumb_sang"]["toi"]["trang_thai"] == expect
    # n=6 nhung trung vi khong duoi p25 cua nhanh thang -> khong loai
    near, mn = scored_rows("thumb_sang", "toi", [2500] * 6)
    stats = h.arm_stats(*combine((win, mw), (near, mn)), cfg)
    assert stats["thumb_sang"]["toi"]["trang_thai"] == "dang-thu"


def _stats_with(cfg, spec):
    """spec: {var: {arm: [values]}}"""
    parts = []
    for var, arms in spec.items():
        for arm, vals in arms.items():
            parts.append(scored_rows(var, arm, vals, metric=cfg["bien"][var]["do"]))
    return h.arm_stats(*combine(*parts), cfg)


def draw(cfg, slug, stats=None, last=None, cam=None, today=TODAY):
    stats = stats if stats is not None else empty_stats(cfg)
    last = last if last is not None else {v: today for v in cfg["bien"]}
    return h.choose_test(slug, stats, cfg, last, today, cam or {})


def test_choose_test_deterministic_per_slug(cfg):
    a = draw(cfg, "henry_h015")
    for _ in range(5):
        assert draw(cfg, "henry_h015") == a
    kinds = {draw(cfg, f"slug{i}")["bien"] for i in range(40)}
    assert len(kinds) >= 1
    assert set(a) == {"bien", "nhanh", "kieu"} and a["kieu"] in ("khai-thac", "thu")


def test_explore_share_close_to_30pct_over_1000_slugs(cfg):
    n = sum(draw(cfg, f"s{i}")["kieu"] == "thu" for i in range(1000))
    assert 250 <= n <= 350


def test_forced_variable_after_7_days(cfg):
    last = {v: TODAY for v in cfg["bien"]}
    last["hook"] = TODAY - timedelta(days=8)
    last["thumb_chu"] = TODAY - timedelta(days=20)     # bi bo lau nhat
    for i in range(30):
        assert draw(cfg, f"f{i}", last=last)["bien"] == "thumb_chu"
    last["thumb_chu"] = TODAY - timedelta(days=7)       # dung 7 ngay: chua bi ep
    assert draw(cfg, "f0", last=last)["bien"] == "hook"
    # chua thu lan nao la bi bo lau nhat
    assert draw(cfg, "f0", last={})["bien"] in hc.enabled_vars(cfg)


def test_min_n_picks_least_sampled_variable(cfg):
    spec = {v: {a: [1000, 2000, 3000] for a in hc.arms(cfg, v)} for v in hc.enabled_vars(cfg)}
    spec["thumb_chu"] = {"2-3-tu": [1000, 2000, 3000], "4-tu": [1000]}
    stats = _stats_with(cfg, spec)
    assert draw(cfg, "x1", stats=stats)["bien"] == "thumb_chu"


def test_killed_arm_only_after_30_days(cfg):
    win, mw = scored_rows("thumb_sang", "sang", [1000, 2000, 3000, 4000, 5000])
    lose, ml = scored_rows("thumb_sang", "toi", [10] * 6)
    scored, merged = combine((win, mw), (lose, ml))
    only = dict(cfg)
    only["bien"] = {"thumb_sang": cfg["bien"]["thumb_sang"]}
    rows = [{"tac_vu": "dung", "thoi_diem": (TODAY - timedelta(days=29)).isoformat() + "T06:10:00",
             "slug": "thumb_sang-toi-0", "thumb_sang": "toi", "bien_thu": "thumb_sang", "nhanh_thu": "toi"}]
    stats = h.arm_stats(scored, merged, only, rows=rows)
    assert stats["thumb_sang"]["toi"]["trang_thai"] == "loai"
    last = {"thumb_sang": TODAY}
    thu29 = [draw(only, f"k{i}", stats=stats, last=last) for i in range(300)]
    assert all(d["nhanh"] == "sang" for d in thu29)           # trong 29 ngay: chua duoc thu lai
    rows[0]["thoi_diem"] = (TODAY - timedelta(days=30)).isoformat() + "T06:10:00"
    stats = h.arm_stats(scored, merged, only, rows=rows)
    thu30 = [draw(only, f"k{i}", stats=stats, last=last) for i in range(300)]
    assert any(d["nhanh"] == "toi" and d["kieu"] == "thu" for d in thu30)


def test_forbidden_arm_never_drawn(cfg):
    cam = {"thumb_sang": {"toi"}, "hook": {"gia-truoc"}}
    for i in range(300):
        d = draw(cfg, f"c{i}", cam=cam)
        assert d["nhanh"] not in cam.get(d["bien"], set())
    # mot bien chi con 1 nhanh hop le thi khong duoc chon lam bien thu
    cam = {v: set(hc.arms(cfg, v)[1:]) for v in hc.enabled_vars(cfg) if v != "hook"}
    for i in range(50):
        assert draw(cfg, f"c{i}", cam=cam)["bien"] == "hook"


def test_directive_has_tu_chon_when_queue_empty(cfg):
    d = h.build_directive([], empty_stats(cfg), cfg, TODAY, {}, "2026-11-22-0610")
    assert [e["slug"] for e in d["muc"]] == ["_tu-chon"]
    d = h.build_directive(["a", "b", "c", "d"], empty_stats(cfg), cfg, TODAY, {}, "v")
    assert [e["slug"] for e in d["muc"]] == ["a", "b", "c"]
    e = d["muc"][0]
    assert e["con_han_toi"] == "2026-11-25"
    assert set(e) >= {"gia_tri", "bien_thu", "nhanh_thu", "kieu", "du_doan_7d", "con_han_toi"}
    for en in d["muc"]:
        for v, arm in en["gia_tri"].items():
            if not (en["kieu"] == "thu" and v == en["bien_thu"]):
                assert arm == hc.default(cfg, v)
    for v in ("dong", "ten_quen"):
        assert sum(d["co_cau_tuan"][v].values()) == 7


def test_gio_dang_off_when_publish_rejected(cfg, tmp_path):
    on = copy.deepcopy(cfg)
    on["bien"]["gio_dang"]["bat"] = True
    on["bien"]["gio_dang"]["khong_kha_dung"] = False
    assert "gio_dang" in hc.enabled_vars(on)
    hist = tmp_path / "upload-history.csv"
    hist.write_text("thoi_gian,kenh,ten_goc,title,video_id,url,trang_thai,ghi_chu\n"
                    "2026-11-20,k,a,t,v,u,ok,publishAt rejected by YouTube\n", encoding="utf-8")
    assert h.publish_rejected(tmp_path) is True
    off = h.apply_publish_rejected(on)
    assert "gio_dang" not in hc.enabled_vars(off)
    assert "gio_dang" in hc.enabled_vars(on)   # ban goc khong bi sua
    d = h.build_directive(["a"], empty_stats(off), off, TODAY, {}, "v")
    assert "gio_dang" not in d["muc"][0]["gia_tri"]
    assert all(draw(off, f"g{i}")["bien"] != "gio_dang" for i in range(100))
    hist.write_text("thoi_gian,kenh,ten_goc,title,video_id,url,trang_thai,ghi_chu\n", encoding="utf-8")
    assert h.publish_rejected(tmp_path) is False
    assert h.publish_rejected(tmp_path / "khong-co") is False


def test_directive_md_mentions_every_enabled_var(cfg, tmp_path):
    d = h.build_directive(["henry_h015"], empty_stats(cfg), cfg, TODAY,
                          {"cum": ["Súng trường", "Súng ngắn"], "tu_khoa": ["ruger"]}, "2026-11-22-0610")
    h.write_directive(tmp_path, d)
    md = (tmp_path / "chi-thi.md").read_text(encoding="utf-8")
    js = json.loads((tmp_path / "chi-thi.json").read_text(encoding="utf-8"))
    assert js["phien_ban"] == "2026-11-22-0610"
    for v in hc.enabled_vars(cfg):
        assert v in md
    assert "chưa đủ số, dùng mặc định" in md
    for part in ("Video kế tiếp", "Tín hiệu khán giả và thị trường", "Ràng buộc luôn áp",
                 "Súng trường", "ruger", "henry_h015"):
        assert part in md


def test_signals_missing_says_so(cfg, tmp_path):
    d = h.build_directive([], empty_stats(cfg), cfg, TODAY, {"cum": None, "tu_khoa": None}, "v")
    h.write_directive(tmp_path, d)
    md = (tmp_path / "chi-thi.md").read_text(encoding="utf-8")
    assert "chưa có" in md


def test_predict_and_goc(cfg):
    win, mw = scored_rows("thumb_sang", "sang", [1000, 2000, 4000])
    stats = h.arm_stats(win, mw, cfg)
    lo, hi = h.predict(stats, cfg, "thumb_sang", "sang", None).split("-")
    assert int(lo) < int(hi)
    assert h.predict(empty_stats(cfg), cfg, "thumb_sang", "sang", None) == ""
    lo, hi = h.predict(empty_stats(cfg), cfg, "thumb_sang", "sang", 4000.0).split("-")
    assert int(lo) < 4000 < int(hi)


def test_goc_from_scored_or_ledger():
    scored = [{"views_7d": v} for v in (100, 200, 300)]
    assert h.goc_view(scored, [], TODAY) == (200.0, "da-cham")
    ledger = [{"published_local": "2026-10-20 10:00", "age_h_latest": "800", "views_latest": "1000"},
              {"published_local": "2026-10-10 10:00", "age_h_latest": "1000", "views_latest": "3000"},
              {"published_local": "2026-11-21 10:00", "age_h_latest": "30", "views_latest": "99999"}]
    assert h.goc_view(scored[:2], ledger, TODAY) == (2000.0, "tam")
    assert h.goc_view([], [], TODAY) == (None, "khong-co")


def test_queue_parse(tmp_path):
    q = tmp_path / "hang-cho.md"
    q.write_text("# x\n\ntext\n\n| slug | cach |\n|---|---|\n| henry_h015 | 2 |\n| colt | 2 |\n\nDa bo\n",
                 encoding="utf-8")
    assert h.read_queue(q) == ["henry_h015", "colt"]
    assert h.read_queue(tmp_path / "none.md") == []
