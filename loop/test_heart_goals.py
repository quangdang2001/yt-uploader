"""Goal ket qua trong trai tim:  python -m pytest -q loop/test_heart_goals.py"""
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from loop import book as bk
from loop import heart as h
from loop import heart_config as hc

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 12, 11)
NOW = datetime(2026, 12, 11, 6, 10)

GOALS = {
    "san_view_thang": 1000000, "san_tu_thang": "2026-12",
    "moc": [{"thang": "2026-12", "view_thang": 1000000, "sub_cuoi_thang": 8000, "doanh_thu_thang_usd": 4000,
             "video": {"view_7d_trung_vi": 10000, "so_video_100k_thang": 4, "ctr_24h_pct": 8.0, "giu_30s_pct": 72}}],
    "video_troi": {"view_toi_thieu": 50000, "trong_ngay": 7, "so_chu_de_noi_tiep": 3, "han_gio": 48},
}


@pytest.fixture
def cfg():
    return hc.load_config(ROOT / "loop" / "heart_config.json")


def prog(state, troi=()):
    return {"thang": "2026-12", "muc_tieu": {"view_thang": 1000000}, "trang_thai": state, "ngay_co_so": 10,
            "ngay_trong_thang": 31, "view_thang": 300000, "du_bao_view_thang": 930000, "view_28_ngay": 300000,
            "view_can_moi_ngay": 33334, "sub_hien_tai": 5000, "du_bao_sub_cuoi_thang": 5063,
            "doanh_thu_thang": 100.0, "du_bao_doanh_thu_thang": 310.0, "rpm_7_ngay": 0.33,
            "chi_so": [{"nhom": "video", "ma": "ctr_24h_trung_vi", "ten": "Tỉ lệ bấm 24 giờ (%)",
                        "hien_tai": 8.5, "du_bao": None, "muc_tieu": 8.0, "dat": True}],
            "video_troi": list(troi)}


HOT = {"video_id": "abc123", "tieu_de": "Hot one", "view": 80000, "tuoi_ngay": 3}


def directive(cfg, state, rows=(), troi=(HOT,), n=1):
    return h.build_directive([f"s{i}" for i in range(n)], h.arm_stats([], {}, cfg), cfg, TODAY, {}, "v",
                             last_tested={}, goal=prog(state, troi), goals_cfg=GOALS, now=NOW,
                             book_rows=list(rows))


def test_shipped_config_has_san_rate(cfg):
    assert cfg["ti_le_thu_san"] == 0.4


def test_older_config_without_san_rate_does_not_crash(cfg, tmp_path):
    old = {k: v for k, v in cfg.items() if k != "ti_le_thu_san"}
    p = tmp_path / "old.json"
    p.write_text(json.dumps(old), encoding="utf-8")
    loaded = hc.load_config(p)
    assert "ti_le_thu_san" not in loaded
    d = h.build_directive(["a"], h.arm_stats([], {}, loaded), loaded, TODAY, {}, "v", last_tested={},
                          goal=prog("thieu"), goals_cfg=GOALS, now=NOW)
    assert d["che_do"] == "san-video-troi"


def test_md_says_plainly_when_month_has_no_own_goal(cfg):
    g = prog("thieu")
    g.update(thang="2026-11", muc_tieu={"view_thang": 1000000, "sap_toi": True, "thang": "2026-11",
                                        "thang_moc": "2026-12"})
    d = h.build_directive(["a"], h.arm_stats([], {}, cfg), cfg, TODAY, {}, "v", last_tested={},
                          goal=g, goals_cfg=GOALS, now=NOW)
    md = h.render_md(d, cfg)
    assert "Tháng 11/2026 chưa có goal riêng; so với mục tiêu tháng 12/2026 (1.000.000)" in md
    assert "chưa có goal riêng" not in h.render_md(directive(cfg, "thieu"), cfg)


def test_mode_switches_on_thieu(cfg):
    assert directive(cfg, "thieu")["che_do"] == "san-video-troi"
    assert directive(cfg, "dung-huong")["che_do"] == "theo-thang"
    assert directive(cfg, "chua-co-so")["che_do"] == "theo-thang"


def test_rate_passed_to_choose_test(cfg, monkeypatch):
    seen = []
    real = h.choose_test

    def spy(*a, **k):
        seen.append(k.get("ti_le_thu"))
        return real(*a, **k)
    monkeypatch.setattr(h, "choose_test", spy)
    directive(cfg, "thieu")
    directive(cfg, "dung-huong")
    h.build_directive(["x"], h.arm_stats([], {}, cfg), cfg, TODAY, {}, "v", last_tested={})
    assert seen == [0.4, None, None]
    assert cfg["ti_le_thu"] == 0.3   # config is never mutated


def test_explore_share_rises_in_hunt_mode(cfg):
    def share(state):
        t = 0
        for i in range(600):
            d = h.build_directive([f"q{i}"], h.arm_stats([], {}, cfg), cfg, TODAY, {}, "v", last_tested={},
                                  goal=prog(state), goals_cfg=GOALS, now=NOW)
            t += d["muc"][0]["kieu"] == "thu"
        return t / 600
    assert share("thieu") > share("dung-huong") + 0.03


def test_queued_slug_still_never_tests_topic_variables_in_hunt_mode(cfg):
    for i in range(100):
        d = directive(cfg, "thieu", n=1)
        assert d["muc"][0]["bien_thu"] not in ("dong", "ten_quen")


def test_nhan_ban_lists_hot_videos_and_dedupes_via_book(cfg):
    d = directive(cfg, "thieu")
    assert d["nhan_ban"] == [{"video_id": "abc123", "tieu_de": "Hot one", "view": 80000, "tuoi_ngay": 3,
                              "so_chu_de": 3, "han": "2026-12-13T06:10"}]
    done = [{"tac_vu": "nhan-ban", "slug": "video:abc123", "thoi_diem": "2026-12-10T06:00:00"}]
    assert directive(cfg, "thieu", rows=done)["nhan_ban"] == []
    other = [{"tac_vu": "ke-hoach", "slug": "video:abc123", "thoi_diem": "2026-12-10T06:00:00"}]
    assert len(directive(cfg, "thieu", rows=other)["nhan_ban"]) == 1


def test_nhan_ban_is_a_valid_book_action(cfg, tmp_path):
    assert "nhan-ban" in bk.TAC_VU
    book = tmp_path / bk.BOOK_NAME
    bk.append(book, {"tac_vu": "nhan-ban", "slug": "video:abc123"}, cfg, NOW)
    assert bk.read(book)[0]["tac_vu"] == "nhan-ban"
    assert "video:abc123" not in bk.merged(bk.read(book))


def test_directive_without_goal_has_no_goal_keys(cfg):
    d = h.build_directive(["a"], h.arm_stats([], {}, cfg), cfg, TODAY, {}, "v", last_tested={})
    assert not {"muc_tieu", "che_do", "nhan_ban"} & set(d)
    assert "Goal tháng" not in h.render_md(d, cfg)


def test_md_has_goal_section_first_and_json_safe(cfg, tmp_path):
    d = directive(cfg, "thieu")
    json.dumps(d)
    md = h.render_md(d, cfg)
    assert md.index("## Goal tháng 12/2026") < md.index("## Các lựa chọn hiện tại")
    sec = md.split("## Các lựa chọn hiện tại")[0]
    for part in ("300.000 / mục tiêu 1.000.000", "dự báo 930.000, thiếu 70.000", "33.334 view mỗi ngày",
                 "Người đăng ký: 5.000", "đạt", "săn video trội",
                 "Video trội cần nhân bản trong 48 giờ: Hot one"):
        assert part in sec, part
    assert "đúng hướng" in h.render_md(directive(cfg, "dung-huong"), cfg)
    h.write_directive(tmp_path, d, cfg)
    assert json.loads((tmp_path / "chi-thi.json").read_text(encoding="utf-8"))["che_do"] == "san-video-troi"


def test_goal_progress_missing_file_is_no_block_no_error(tmp_path, capsys):
    assert h.goal_progress(tmp_path, tmp_path / "cache", [], TODAY) == (None, None)
    assert "cảnh báo" in capsys.readouterr().out


def test_goal_progress_from_files(tmp_path):
    (tmp_path / "muc-tieu.json").write_text(json.dumps(GOALS), encoding="utf-8")
    ch = tmp_path / "cache" / "channel"
    ch.mkdir(parents=True)
    pts = [[f"2026-12-{i:02d}", 30000] for i in range(1, 11)]
    card = {"id": "video_views", "value": 1, "rows": [], "metrics": [], "series": [{"label": "x", "points": pts}]}
    doc = {"captured_at_utc": "2026-12-10T11:00:00Z", "tabs": {"hub_content": [card], "hub_overview": [
        {"id": "realtime_subscribers", "value": 5000, "rows": [], "metrics": [], "series": []}]}}
    (ch / "2026-12-10.json").write_text(json.dumps(doc), encoding="utf-8")
    p, goals = h.goal_progress(tmp_path, tmp_path / "cache", [], TODAY)
    assert p["trang_thai"] == "thieu" and p["view_thang"] == 300000 and p["sub_hien_tai"] == 5000
    assert goals["video_troi"]["han_gio"] == 48


def test_goal_progress_swallows_errors(tmp_path, monkeypatch, capsys):
    (tmp_path / "muc-tieu.json").write_text(json.dumps(GOALS), encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(h.goals_mod, "channel_daily", boom)
    assert h.goal_progress(tmp_path, tmp_path, [], TODAY) == (None, None)
    assert "RuntimeError" in capsys.readouterr().out
