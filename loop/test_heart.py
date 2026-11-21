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
        if metric == "apv_pct" and v != "":
            row["views_7d"] = 500   # da toi moc 7 ngay
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


def test_directive_carries_full_arm_stats(cfg):
    win, mw = scored_rows("thumb_sang", "sang", [1000, 2000, 3000, 4000, 5000])
    lose, ml = scored_rows("thumb_sang", "toi", [10] * 6)
    stats = h.arm_stats(*combine((win, mw), (lose, ml)), cfg)
    d = h.build_directive(["a"], stats, cfg, TODAY, {}, "v")
    tk = d["thong_ke_nhanh"]
    assert set(tk) == set(d["bien_bat"])
    for v in d["bien_bat"]:
        assert set(tk[v]) == set(hc.arms(cfg, v))
    assert tk["thumb_sang"]["toi"]["trang_thai"] == "loai"
    assert tk["thumb_sang"]["sang"]["trang_thai"] == "thang"
    assert tk["thumb_sang"]["toi"]["n"] == 6 and tk["thumb_sang"]["toi"]["do"] == "views_7d"
    assert tk["hook"][hc.arms(cfg, "hook")[0]] == {"n": 0, "trung_vi": None, "p25": None, "p75": None,
                                                   "trang_thai": "chua-du", "do": "apv_pct"}
    json.dumps(d)


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


HIST_HEAD = "thoi_gian,kenh,ten_goc,title,video_id,url,trang_thai,ghi_chu\n"


def write_history(cache, *rows):
    """rows: (thoi_gian, video_id, ghi_chu)"""
    lines = [f"{t},k,a,t,{vid},u,OK,{note}\n" for t, vid, note in rows]
    (cache / "upload-history.csv").write_text(HIST_HEAD + "".join(lines), encoding="utf-8")


def _enabled_gio_dang(cfg):
    on = copy.deepcopy(cfg)
    on["bien"]["gio_dang"]["bat"] = True
    on["bien"]["gio_dang"]["khong_kha_dung"] = False
    return on


def test_gio_dang_off_when_publish_rejected(cfg, tmp_path):
    on = _enabled_gio_dang(cfg)
    assert "gio_dang" in hc.enabled_vars(on)
    write_history(tmp_path, ("2026-11-20 10:00:00", "vid1", "publishAt rejected: invalidPublishAt; done/"))
    assert h.publish_rejected(tmp_path, TODAY) is True
    off = h.apply_publish_rejected(on)
    assert "gio_dang" not in hc.enabled_vars(off)
    assert "gio_dang" in hc.enabled_vars(on)   # ban goc khong bi sua
    d = h.build_directive(["a"], empty_stats(off), off, TODAY, {}, "v")
    assert "gio_dang" not in d["muc"][0]["gia_tri"]
    assert all(draw(off, f"g{i}")["bien"] != "gio_dang" for i in range(100))
    write_history(tmp_path)
    assert h.publish_rejected(tmp_path, TODAY) is False
    assert h.publish_rejected(tmp_path / "khong-co", TODAY) is False


def test_refusal_lasts_seven_days_then_gio_dang_returns(cfg, tmp_path):
    write_history(tmp_path, ("2026-11-20 10:00:00", "vid1", "publishAt rejected: x; done/"))
    assert h.publish_rejected(tmp_path, date(2026, 11, 26)) is True       # day 6
    assert h.publish_rejected(tmp_path, date(2026, 11, 27)) is False      # day 7: back on
    assert h.publish_rejected(tmp_path, TODAY + timedelta(days=8)) is False


def test_publish_at_passed_is_not_a_refusal(tmp_path):
    write_history(tmp_path, ("2026-11-21 10:00:00", "vid1", "publishAt passed: 2026-11-21T01:00:00Z; done/"),
                  ("2026-11-21 11:00:00", "vid2", "done/ and mentions publishAt rejected later"))
    assert h.publish_rejected(tmp_path, TODAY) is False
    assert h.publish_refusals(tmp_path) == []


def test_refusal_alert_written_once_per_refusal(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    write_history(cache, ("2026-11-20 10:00:00", "vid1", "publishAt rejected: x; done/"))
    alerts = tmp_path / "alerts.md"
    h.alert_refusals(cache, TODAY, alerts)
    h.alert_refusals(cache, TODAY, alerts)                                  # next night: no repeat
    assert alerts.read_text(encoding="utf-8").splitlines() == [
        "- 2026-11-20 heart: gio_dang tắt 7 ngày vì YouTube từ chối publishAt (vid1)"]
    write_history(cache, ("2026-11-20 10:00:00", "vid1", "publishAt rejected: x; done/"),
                  ("2026-11-22 09:00:00", "vid2", "publishAt rejected: y; done/"))
    h.alert_refusals(cache, TODAY, alerts)
    lines = alerts.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and lines[1].endswith("(vid2)")


def test_no_alert_for_expired_refusal_or_missing_history(tmp_path):
    alerts = tmp_path / "alerts.md"
    h.alert_refusals(tmp_path / "none", TODAY, alerts)
    write_history(tmp_path, ("2026-11-01 10:00:00", "vid1", "publishAt rejected: x; done/"))
    h.alert_refusals(tmp_path, TODAY, alerts)
    assert not alerts.exists()


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


def test_predict_uses_queue_plan_then_goc_band(cfg):
    st = empty_stats(cfg)
    assert h.predict(st, cfg, "thumb_sang", "sang", 4000.0, "4000-12000") == "4000-12000"
    assert h.predict(st, cfg, "thumb_sang", "sang", 4000.0, "") == "2000-6000"
    assert h.predict(st, cfg, "thumb_sang", "sang", 4000.0, "abc") == "2000-6000"
    d = h.build_directive(["a", "_tu-chon"], st, cfg, TODAY, {}, "v", goc=(4000.0, "tam"),
                          plan={"a": "4000-12000"})
    assert d["muc"][0]["du_doan_7d"] == "4000-12000"
    assert d["muc"][1]["du_doan_7d"] == "2000-6000"


def test_read_queue_plan(tmp_path):
    q = tmp_path / "hang-cho.md"
    q.write_text("| slug | cach | du_doan_7d |\n|---|---|---|\n| a | 2 | 4000-12000 |\n| b | 1 | x |\n",
                 encoding="utf-8")
    assert h.read_queue_plan(q) == {"a": "4000-12000"}


def test_hook_not_counted_before_7_day_mark(cfg):
    scored, merged = scored_rows("hook", "su-viec-hang", [0.3, 0.3, 0.3], metric="apv_pct")
    for r in scored:
        r["views_7d"] = ""
    assert h.arm_stats(scored, merged, cfg)["hook"]["su-viec-hang"]["n"] == 0
    for r in scored:
        r["views_7d"] = 500
    assert h.arm_stats(scored, merged, cfg)["hook"]["su-viec-hang"]["n"] == 3


def test_md_shows_apv_as_percent(cfg, tmp_path):
    scored, merged = scored_rows("hook", "su-viec-hang", [0.34, 0.34, 0.34], metric="apv_pct")
    for r in scored:
        r["views_7d"] = 500
    stats = h.arm_stats(scored, merged, cfg)
    d = h.build_directive(["a"], stats, cfg, TODAY, {}, "v")
    assert "34,0% thời lượng xem" in h.render_md(d, cfg)


def row(slug, khuon, days_ago, tac_vu="dung"):
    return {"tac_vu": tac_vu, "slug": slug, "khuon_tieu_de": khuon,
            "thoi_diem": (TODAY - timedelta(days=days_ago)).isoformat() + "T06:10:00"}


def test_forbidden_arms_from_book():
    rows = [row("a", "price-plea", 10), row("a", "price-plea", 10, "ke-hoach"),   # cung video: 1 lan
            row("b", "price-copy", 5), row("c", "price-copy", 40),               # 2 lan/56 ngay
            row("d", "price-spec", 5), row("e", "price-spec", 60),               # mot lan trong 56 ngay
            row("f", "moi", 3), row("g", "moi", 6)]                              # moi 2 lan/7 ngay
    cam = h.forbidden_arms(rows, TODAY)
    assert cam == {"khuon_tieu_de": {"price-copy", "moi"}}
    assert h.forbidden_arms([row("f", "moi", 3)], TODAY) == {}
    assert h.forbidden_arms([row("f", "moi", 3), row("g", "moi", 23)], TODAY) == {}   # 20 ngay cach nhau


def test_directive_substitutes_forbidden_winner(cfg):
    cam = {"khuon_tieu_de": {"price-sacrifice"}}
    d = h.build_directive(["a", "b", "c"], empty_stats(cfg), cfg, TODAY, {}, "v", cam=cam)
    for e in d["muc"]:
        assert e["gia_tri"]["khuon_tieu_de"] != "price-sacrifice"
        assert e["gia_tri"]["khuon_tieu_de"] in hc.arms(cfg, "khuon_tieu_de")


def _only_overdue(cfg, var):
    last = {v: TODAY for v in cfg["bien"]}
    last[var] = TODAY - timedelta(days=30)
    return last


def test_plan_sets_topic_decided_variable(cfg):
    d = h.build_directive(["a"], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested=_only_overdue(cfg, "thumb_sang"),
                          planned={"a": {"dong": "xep-hang", "khuon_tieu_de": "price-copy", "thumb_chu": "4-tu"}})
    e = d["muc"][0]
    assert e["bien_thu"] == "thumb_sang"
    assert e["gia_tri"]["dong"] == "xep-hang" and e["gia_tri"]["khuon_tieu_de"] == "price-copy"
    assert e["gia_tri"]["thumb_chu"] == hc.default(cfg, "thumb_chu")   # ai_quyet=dung: khong theo ke hoach
    d = h.build_directive(["b"], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested=_only_overdue(cfg, "thumb_sang"),
                          planned={"a": {"dong": "xep-hang"}})
    assert d["muc"][0]["gia_tri"]["dong"] == hc.default(cfg, "dong")


def test_plan_ignores_non_arm_with_warning(cfg, capsys):
    d = h.build_directive(["a"], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested=_only_overdue(cfg, "thumb_sang"),
                          planned={"a": {"dong": "khong-co-nhanh"}})
    assert d["muc"][0]["gia_tri"]["dong"] == hc.default(cfg, "dong")
    assert "heart: kế hoạch a: dong=khong-co-nhanh không phải nhánh (bỏ qua)" in capsys.readouterr().out


def test_test_arm_beats_plan(cfg):
    last = _only_overdue(cfg, "khuon_tieu_de")
    for i in range(200):
        slug = f"t{i}"
        d = h.build_directive([slug], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested=last,
                              planned={slug: {"khuon_tieu_de": "price-copy"}})
        e = d["muc"][0]
        if e["kieu"] == "thu":
            assert e["bien_thu"] == "khuon_tieu_de" and e["gia_tri"]["khuon_tieu_de"] == e["nhanh_thu"]
            return
    pytest.fail("khong co lan thu nao")


def test_config_marks_topic_and_build_variables(cfg):
    ai = {v: b["ai_quyet"] for v, b in cfg["bien"].items()}
    assert ai["dong"] == "chu-de" and ai["ten_quen"] == "chu-de"
    assert ai["khuon_tieu_de"] == "dung" and ai["hook"] == "dung" and ai["gio_dang"] == "dang"


def test_planned_slug_never_tests_topic_variable(cfg):
    plan = {"dong": "lineup", "ten_quen": "hang-vua"}
    for i in range(300):
        slug = f"p{i}"
        for last in (None, {}, {v: TODAY - timedelta(days=30) for v in cfg["bien"]}):
            d = h.build_directive([slug], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested=last,
                                  planned={slug: dict(plan)})
            e = d["muc"][0]
            assert e["bien_thu"] not in ("dong", "ten_quen"), (slug, e)
            assert e["gia_tri"]["dong"] == "lineup" and e["gia_tri"]["ten_quen"] == "hang-vua"


def test_book_row_without_values_still_counts_as_plan(cfg):
    for i in range(100):
        d = h.build_directive([f"q{i}"], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested={}, book_rows=[
            {"tac_vu": "ke-hoach", "slug": f"q{i}", "thoi_diem": TODAY.isoformat()}])
        assert d["muc"][0]["bien_thu"] not in ("dong", "ten_quen")


def test_exploit_mode_keeps_plan_dong(cfg):
    # winner of dong is gia-chuyen-hang; the plan says lineup; dong is never the test variable of a plan slug
    stats = _stats_with(cfg, {"dong": {"gia-chuyen-hang": [8.0] * 3, "lineup": [5.0] * 3, "xep-hang": [4.0] * 3}})
    for i in range(100):
        slug = f"x{i}"
        d = h.build_directive([slug], stats, cfg, TODAY, {}, "v", last_tested=_only_overdue(cfg, "dong"),
                              planned={slug: {"dong": "lineup"}})
        e = d["muc"][0]
        assert e["bien_thu"] != "dong" and e["gia_tri"]["dong"] == "lineup"


def test_forced_variable_only_among_eligible(cfg):
    last = {v: TODAY - timedelta(days=30) for v in cfg["bien"]}     # every variable overdue
    d = h.build_directive(["a"], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested=last,
                          planned={"a": {"dong": "lineup"}})
    assert d["muc"][0]["bien_thu"] not in ("dong", "ten_quen")


def test_tu_chon_without_plan_can_test_dong(cfg):
    last = _only_overdue(cfg, "dong")
    assert h.choose_test("_tu-chon", empty_stats(cfg), cfg, last, TODAY, {})["bien"] == "dong"
    d = h.build_directive([], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested=last)
    assert d["muc"][0]["slug"] == "_tu-chon" and d["muc"][0]["bien_thu"] == "dong"


def test_forbidden_plan_still_falls_back_for_planned_khuon(cfg, capsys):
    rows = [row("a", "price-copy", 5, "ke-hoach"), row("b", "price-copy", 6), row("c", "price-copy", 7)]
    d = h.build_directive(["a"], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested=_only_overdue(cfg, "thumb_sang"),
                          planned={"a": {"khuon_tieu_de": "price-copy", "dong": "lineup"}}, book_rows=rows)
    assert d["muc"][0]["gia_tri"]["khuon_tieu_de"] == hc.default(cfg, "khuon_tieu_de")
    assert d["muc"][0]["gia_tri"]["dong"] == "lineup"


# --- item 4: _tu-chon seeds by date

def test_tu_chon_draw_changes_with_the_date(cfg):
    draws = set()
    kinds = set()
    for k in range(10):
        day = TODAY + timedelta(days=k)
        d = h.build_directive([], empty_stats(cfg), cfg, day, {}, "v", last_tested={v: day for v in cfg["bien"]})
        e = d["muc"][0]
        kinds.add(e["kieu"])
        draws.add((e["kieu"], e["bien_thu"], e["nhanh_thu"]))
    assert kinds == {"thu", "khai-thac"}
    assert len(draws) > 2


def test_tu_chon_same_day_is_stable_and_slug_seed_unchanged(cfg):
    a = h.build_directive([], empty_stats(cfg), cfg, TODAY, {}, "v")["muc"][0]
    b = h.build_directive([], empty_stats(cfg), cfg, TODAY, {}, "v")["muc"][0]
    assert a == b
    assert h.seed("henry_h015") == int(__import__("hashlib").sha256(b"henry_h015").hexdigest()[:12], 16)
    assert h.seed("_tu-chon", TODAY) != h.seed("_tu-chon", TODAY + timedelta(days=1))
    assert h.seed("henry_h015", TODAY) == h.seed("henry_h015", TODAY + timedelta(days=1))


def test_planned_values_from_book_rows(cfg):
    rows = [{"tac_vu": "ke-hoach", "slug": "a", "dong": "lineup"}, {"tac_vu": "ke-hoach", "slug": "a", "dong": "xep-hang"},
            {"tac_vu": "dung", "slug": "a", "dong": "gia-chuyen-hang", "hook": "gia-truoc"}]
    assert h.planned_values(rows, cfg) == {"a": {"dong": "xep-hang"}}


def test_plan_kept_when_held_by_self_and_one_other(cfg, capsys):
    rows = [row("a", "price-copy", 5, "ke-hoach"), row("b", "price-copy", 6)]
    d = h.build_directive(["a"], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested=_only_overdue(cfg, "thumb_sang"),
                          planned={"a": {"khuon_tieu_de": "price-copy"}}, book_rows=rows)
    assert d["muc"][0]["gia_tri"]["khuon_tieu_de"] == "price-copy"
    assert "đã có 2 video khác" not in capsys.readouterr().out


def test_plan_rejected_when_held_by_two_others(cfg, capsys):
    rows = [row("a", "price-copy", 5, "ke-hoach"), row("b", "price-copy", 6), row("c", "price-copy", 7)]
    d = h.build_directive(["a"], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested=_only_overdue(cfg, "thumb_sang"),
                          planned={"a": {"khuon_tieu_de": "price-copy"}}, book_rows=rows)
    assert d["muc"][0]["gia_tri"]["khuon_tieu_de"] == hc.default(cfg, "khuon_tieu_de")
    assert "heart: kế hoạch a: khuon_tieu_de=price-copy đã có 2 video khác dùng" in capsys.readouterr().out


def test_queued_slug_without_book_row_never_tests_topic_variable(cfg):
    for i in range(100):
        d = h.build_directive([f"n{i}"], empty_stats(cfg), cfg, TODAY, {}, "v", last_tested={})
        assert d["muc"][0]["bien_thu"] not in ("dong", "ten_quen")
