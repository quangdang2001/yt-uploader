"""Bo nao hang dem cua "trai tim feedback": thong ke nhanh, chon bien thu, du doan, viet chi thi.

    python -m loop.heart --config loop/config.json

Ghi vao <vault>/van-hanh/tim/: ket-qua.csv, chi-thi.json, chi-thi.md. Chi stdlib. Khong bao gio dung phim:
loi thi thoat 1 va run.ps1 van tiep tuc.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from loop import book as bk
from loop import fetch, heart_score
from loop import heart_config as hc
from loop import goals as goals_mod
from loop.channel_view import latest_doc
from loop.channel_view import totals as channel_totals
from loop.state import since_published

HERE = Path(__file__).resolve().parent
SEARCH_CARD = "how_viewers_find_your_videos#tim_kiem_youtube"
SEARCH_SKIP = "tổng tỷ lệ lưu lượng truy cập"
QUEUE_ENTRIES = 3
WEEK_VIDEOS = 7
WEEK_SPLIT = 0.7
GOC_MIN_ROWS = 3
_RANGE = re.compile(r"^\d+-\d+$")
GOC_AGE_H = (7 * 24, 8 * 7 * 24)
# spec 5.1, doan duoi bang cac bien
CONSTRAINTS = (
    "Xoay loại súng: không 3 phim cùng loại liên tiếp.",
    "Không lặp chủ đề trong 8 tuần.",
    "Khuôn tiêu đề tối đa 2 lần trong 8 tuần.",
    "Ba trần hình.",
    "Footage từ 50% trở lên và giữ nhịp cảnh.",
)


# ---------- thong ke ----------

def _num(v):
    try:
        return None if v in ("", None) else float(v)
    except (TypeError, ValueError):
        return None


CTR_MIN_IMPR = 300   # ti le bam tren it luot hien thi hon the nay la nhieu, chua cham


def _metric(cfg: dict, var: str, row: dict):
    do = cfg["bien"][var]["do"]
    if do == "apv_pct":
        return _num(row.get("apv_pct"))
    if do == "ctr_24h":
        # Ti le bam o anh chup 18-36 gio (heart_score.CTR_WINDOW). Chu kenh 2026-11-24: "Yes, do it" - cham bang CTR
        # 24 gio thay vi view 7 ngay. Can cu: 29 video qua 7 ngay, CTR xep hang view gan nhu trung khop (Spearman
        # 0,95); CTR >= 7% trung vi 48.750 view (8 video), < 5,7% trung vi 1.300 (13 video).
        ctr, impr = _num(row.get("ctr_24h")), _num(row.get("impr_24h"))
        return ctr if ctr is not None and impr is not None and impr >= CTR_MIN_IMPR else None
    v = _num(row.get("views_7d"))
    return None if v is None else math.log(max(v, 1.0))


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def arm_stats(scored: list[dict], merged: dict, cfg: dict, rows: list[dict] | None = None) -> dict:
    """var -> nhanh -> {n, trung_vi, p25, p75, trang_thai, lan_thu_cuoi}. Chi dong da cham moi tinh."""
    vals = {v: {a: [] for a in hc.arms(cfg, v)} for v in cfg["bien"]}
    warned: set = set()
    for r in scored:
        m = merged.get(r.get("slug"))
        if not m:
            continue
        for var in cfg["bien"]:
            arm = m.get(var, "")
            if not arm:
                continue
            if arm not in vals[var]:
                if (var, arm) not in warned:
                    warned.add((var, arm))
                    print(f"heart: nhánh lạ {var}={arm} (bỏ qua)")
                continue
            x = _metric(cfg, var, r)
            if cfg["bien"][var]["do"] == "apv_pct" and _num(r.get("views_7d")) is None:
                x = None   # chua toi moc 7 ngay thi chua cham
            if x is not None:
                vals[var][arm].append(x)
    used: dict = {}
    # moi lan dung mot nhanh (thu hay khai thac) deu tinh la "da thu"
    for r in rows or []:
        if r.get("tac_vu") != "dung":
            continue
        try:
            d = date.fromisoformat(str(r.get("thoi_diem", ""))[:10])
        except ValueError:
            continue
        for var in cfg["bien"]:
            arm = r.get(var, "")
            if arm in vals[var]:
                used[(var, arm)] = max(d, used.get((var, arm), d))
    stats: dict = {}
    for var, byarm in vals.items():
        stats[var] = {}
        for arm, xs in byarm.items():
            st = {"n": len(xs), "trung_vi": None, "p25": None, "p75": None, "trang_thai": "chua-du",
                  "lan_thu_cuoi": used.get((var, arm))}
            if xs:
                st.update(trung_vi=statistics.median(xs), p25=_pct(xs, 0.25), p75=_pct(xs, 0.75),
                          trang_thai="dang-thu")
            stats[var][arm] = st
        win = _winner(stats[var], cfg)
        if win is None:
            continue
        stats[var][win]["trang_thai"] = "thang"
        ref = stats[var][win]["p25"]
        for arm, st in stats[var].items():
            if arm != win and st["n"] >= cfg["n_loai"] and st["trung_vi"] < ref:
                st["trang_thai"] = "loai"
    return stats


def _winner(var_stats: dict, cfg: dict):
    best = None
    for arm, st in var_stats.items():
        if st["n"] >= cfg["n_thang"] and (best is None or st["trung_vi"] > var_stats[best]["trung_vi"]):
            best = arm
    return best


def winners(stats: dict, cfg: dict) -> dict[str, str]:
    return {var: _winner(stats[var], cfg) or hc.default(cfg, var) for var in cfg["bien"]}


# ---------- chon bien thu ----------

TU_CHON = "_tu-chon"


def seed(slug: str, today: date | None = None) -> int:
    """sha256 cua slug; rieng `_tu-chon` (khong co ke hoach) them ngay chi thi de moi ngay boc mot lan khac."""
    key = f"{slug}:{today.isoformat()}" if slug == TU_CHON and today is not None else slug
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)


def _gap(var_stats: dict, valid: list[str]) -> float:
    meds = sorted((var_stats[a]["trung_vi"] for a in valid if var_stats[a]["trung_vi"] is not None),
                  reverse=True)
    return meds[0] - meds[1] if len(meds) >= 2 else math.inf


def choose_test(slug: str, stats: dict, cfg: dict, last_tested: dict, today: date, cam: dict,
                has_plan: bool = False, ti_le_thu: float | None = None, dong: str | None = None) -> dict:
    """Biet slug da co dong `ke-hoach` (has_plan) thi chi boc trong cac bien build quyet: bien `chu-de`
    (dong, ten_quen) da chot o buoc chu de, khong lam bien thu. `ti_le_thu` (neu co) thay cfg["ti_le_thu"]
    cho lan chay nay."""
    rng = random.Random(seed(slug, today))
    # `dong` (kieu video cua slug nay): chi boc nhanh di duoc voi kieu do (heart_config.compatible)
    valid = {v: [a for a in hc.arms(cfg, v) if a not in cam.get(v, ()) and a not in hc.suspended(cfg, v)
                 and hc.compatible(cfg, v, a, dong)]
             for v in hc.testable_vars(cfg)
             if not (has_plan and cfg["bien"][v].get("ai_quyet") == "chu-de")}
    cands = [v for v, a in valid.items() if len(a) >= 2]
    if not cands:
        return {"bien": "", "nhanh": "", "kieu": "khai-thac"}
    limit = cfg["bo_qua_toi_da_ngay"]
    overdue = [v for v in cands if last_tested.get(v) is None or (today - last_tested[v]).days > limit]
    if overdue:
        bien = min(overdue, key=lambda v: (last_tested.get(v) is not None,
                                           last_tested.get(v) or date.min, cands.index(v)))
    else:
        def key(v):
            vs = stats[v]
            live = [vs[a]["n"] for a in valid[v] if vs[a]["trang_thai"] != "loai"]
            return (min(live) if live else math.inf, _gap(vs, valid[v]), cands.index(v))
        bien = min(cands, key=key)
    vs = stats[bien]
    win = winners(stats, cfg)[bien]
    if win not in valid[bien]:
        win = valid[bien][0]
    if rng.random() < (cfg["ti_le_thu"] if ti_le_thu is None else ti_le_thu):
        weights = {}
        for a in valid[bien]:
            if a == win:
                continue
            st = vs[a]
            if st["trang_thai"] == "loai":
                ld = st["lan_thu_cuoi"]
                w = cfg["san_thu"] if ld is None or (today - ld).days >= cfg["thu_lai_ngay"] else 0.0
            elif st["n"] < cfg["n_loai"]:
                w = 1.0
            else:
                w = cfg["san_thu"]
            if w > 0:
                weights[a] = w
        total = sum(weights.values())
        if total > 0:
            x = rng.random() * total
            for a, w in weights.items():
                x -= w
                if x < 0:
                    return {"bien": bien, "nhanh": a, "kieu": "thu"}
            return {"bien": bien, "nhanh": list(weights)[-1], "kieu": "thu"}
    return {"bien": bien, "nhanh": win, "kieu": "khai-thac"}


# ---------- du doan va goc ----------

def _round_views(x: float) -> int:
    step = 100
    return max(int(round(x / step)) * step, 0)


def predict(stats: dict, cfg: dict, bien: str, nhanh: str, goc, plan: str = "") -> str:
    """Du doan 7 ngay. Du mau: p25-p75 cua nhanh. Chua du: du doan cua hang-cho.md (`plan`), roi goc x 0,5-1,5."""
    st = (stats.get(bien) or {}).get(nhanh)
    if st and st["n"] >= cfg["n_thang"] and cfg["bien"][bien]["do"] == "views_7d":
        lo, hi = math.exp(st["p25"]), math.exp(st["p75"])
    elif plan and _RANGE.match(plan):
        return plan
    elif goc:
        lo, hi = goc * 0.5, goc * 1.5
    else:
        return ""
    lo, hi = _round_views(lo), _round_views(hi)
    return f"{lo}-{max(hi, lo + 1)}"


def goc_view(scored: list[dict], ledger_rows: list[dict], today: date):
    """(trung vi, nguon): nguon = da-cham | tam | khong-co."""
    v7 = [x for x in (_num(r.get("views_7d")) for r in scored) if x is not None]
    if len(v7) >= GOC_MIN_ROWS:
        return float(statistics.median(v7)), "da-cham"
    vals = []
    for r in ledger_rows:
        age, views = _num(r.get("age_h_latest")), _num(r.get("views_latest"))
        if age is not None and views is not None and GOC_AGE_H[0] < age <= GOC_AGE_H[1]:
            vals.append(views)
    if vals:
        return float(statistics.median(vals)), "tam"
    return None, "khong-co"


# ---------- chi thi ----------

def _week_mix(var: str, stats: dict, cfg: dict) -> dict:
    arms = hc.arms(cfg, var)
    win = winners(stats, cfg)[var]
    main = round(WEEK_VIDEOS * WEEK_SPLIT)
    live = [a for a in arms if a not in hc.suspended(cfg, var)]      # nhanh tam ngung: 0 phim trong tuan
    others = [a for a in live if a != win and stats[var][a]["trang_thai"] != "loai"] or \
             [a for a in live if a != win]
    mix = {a: 0 for a in arms}
    mix[win] = main if others else WEEK_VIDEOS
    for i in range(WEEK_VIDEOS - mix[win]):
        mix[others[i % len(others)]] += 1
    return mix


def build_directive(queue: list[str], stats: dict, cfg: dict, today: date, signals: dict, phien_ban: str,
                    last_tested: dict | None = None, cam: dict | None = None, goc=(None, "khong-co"),
                    plan: dict | None = None, planned: dict | None = None,
                    book_rows: list | None = None, goal: dict | None = None,
                    goals_cfg: dict | None = None, now: datetime | None = None,
                    seen: dict | None = None, hot: set | None = None) -> dict:
    """`goal` = tien do so voi goal (loop.goals.progress). Co goal: `che_do` va ti le thu theo goal,
    them khoi `muc_tieu` va `nhan_ban`. Khong co: chi thi nhu cu. `hot` = khuon mien tran (hot_shapes)."""
    che_do = mode_for(goal)
    rate = cfg.get("ti_le_thu_san", cfg["ti_le_thu"]) if che_do == "san-video-troi" else None
    win = winners(stats, cfg)
    cam = cam or {}
    for v in win:   # nhanh thang bi cam thi thay bang nhanh hop le dau tien
        if win[v] in cam.get(v, ()):
            ok = [a for a in hc.arms(cfg, v) if a not in cam[v]]
            if ok:
                win[v] = ok[0]
    on = hc.enabled_vars(cfg)
    last = dict(last_tested or {})
    slugs = list(queue[:QUEUE_ENTRIES]) or ["_tu-chon"]
    muc = []
    for slug in slugs:
        # moi slug trong hang cho da qua buoc chu de (dong, ten_quen da chot, co hay chua co dong ke-hoach);
        # chi `_tu-chon` (hang cho trong) chua co chu de
        has_plan = slug != TU_CHON
        slug_dong = ((planned or {}).get(slug) or {}).get("dong") or win.get("dong")
        if slug_dong not in hc.arms(cfg, "dong"):
            slug_dong = win.get("dong")
        t = choose_test(slug, stats, cfg, last, today, cam or {}, has_plan=has_plan, ti_le_thu=rate,
                        dong=slug_dong if "dong" in on else None)
        gia_tri = {v: win[v] for v in on}
        het_suat = False
        for v, val in ((planned or {}).get(slug) or {}).items():
            # Bien dang theo doi: nhanh thu, hoac nhanh thang DA DO, moi de len ke hoach. Chua nhanh nao du so thi
            # "nhanh thang" chi la mac dinh cua config - giu khuon cua ke hoach (2026-11-24: may giao price-sacrifice
            # cho moi phim gia, hai phim dau deu phai lech vi khuon do noi sai ve khau sung).
            measured = (stats.get(v) or {}).get(win.get(v), {}).get("trang_thai") == "thang"
            if v not in gia_tri or (v == t["bien"] and (t["kieu"] == "thu" or measured)):
                continue
            if cfg["bien"][v].get("ai_quyet") != "chu-de" and v != "khuon_tieu_de":
                continue
            others = forbidden_arms(book_rows or [], today, exclude=slug, hot=hot)
            if val == "moi" and v == "khuon_tieu_de" and val in others.get(v, ()) and not measured:
                # Chu kenh 2026-11-24 ("agree both"): het suat khuon moi ma chua khuon nao du so thi noi thang
                # "khuon moi, het suat" - khong gan mac dinh chua do (price-sacrifice) cho mot tieu de khac khuon.
                gia_tri[v] = val
                het_suat = True
                print(f"heart: kế hoạch {slug}: khuôn mới, suất khuôn mới đã hết; giữ `moi` và ghi rõ trong chỉ thị")
            elif val in hc.arms(cfg, v) and val in others.get(v, ()):
                print(f"heart: kế hoạch {slug}: {v}={val} đã có 2 video khác dùng, giữ nhánh thắng")
            elif val in hc.arms(cfg, v):
                gia_tri[v] = val
            else:
                print(f"heart: kế hoạch {slug}: {v}={val} không phải nhánh (bỏ qua)")
        if t["kieu"] == "thu":
            gia_tri[t["bien"]] = t["nhanh"]
        # nhanh thang / mac dinh khong di duoc voi kieu video cua slug nay -> nhanh hop le dau tien (khong bi cam)
        for v in list(gia_tri):
            if v != "dong" and not hc.compatible(cfg, v, gia_tri[v], gia_tri.get("dong")):
                ok = [a for a in hc.arms(cfg, v)
                      if hc.compatible(cfg, v, a, gia_tri.get("dong")) and a not in cam.get(v, ())] or \
                     [a for a in hc.arms(cfg, v) if hc.compatible(cfg, v, a, gia_tri.get("dong"))]
                if ok:
                    gia_tri[v] = ok[0]
        track = t["bien"] if t["bien"] in gia_tri else ("dong" if "dong" in gia_tri else None)
        if t["bien"]:
            last[t["bien"]] = today
        muc.append({
            "slug": slug, "gia_tri": gia_tri, "bien_thu": t["bien"],
            "nhanh_thu": t["nhanh"] if t["kieu"] == "thu" else "", "kieu": t["kieu"],
            "du_doan_7d": predict(stats, cfg, track, gia_tri[track], goc[0],
                                  (plan or {}).get(slug, "")) if track else "",
            "con_han_toi": (today + timedelta(days=cfg["chi_thi_han_ngay"])).isoformat(),
        })
        if het_suat and gia_tri.get("khuon_tieu_de") == "moi":
            muc[-1]["khuon_het_suat"] = True
    out = {
        "ngay": today.isoformat(), "phien_ban": phien_ban,
        "nhanh_thang": {v: {"nhanh": win[v], "n": stats[v][win[v]]["n"],
                            "trung_vi": stats[v][win[v]]["trung_vi"],
                            "du_so": stats[v][win[v]]["trang_thai"] == "thang"} for v in on},
        "thong_ke_nhanh": {v: {a: {"n": s["n"], "trung_vi": s["trung_vi"], "p25": s["p25"], "p75": s["p75"],
                                   "trang_thai": s["trang_thai"], "do": cfg["bien"][v]["do"]}
                               for a, s in stats[v].items()} for v in on},
        "bien_bat": on, "muc": muc,
        "co_cau_tuan": {v: _week_mix(v, stats, cfg) for v in ("dong", "ten_quen") if v in on},
        "goc": {"gia_tri": goc[0], "nguon": goc[1]},
        "tin_hieu": {"cum": signals.get("cum"), "tu_khoa": signals.get("tu_khoa")},
        "rang_buoc": list(CONSTRAINTS),
    }
    if hot:
        out["khuon_mien_tran"] = sorted(hot)
        out["rang_buoc"].append(
            "Miễn trần khuôn (chủ kênh 2026-11-24): khuôn có phim tối đa 7 ngày tuổi đang được đẩy (CTR từ 7%, từ 30.000 "
            "lượt hiển thị) không tính trần 2 lần/8 tuần — hiện là: " + ", ".join(sorted(hot)) + ".")
    if goal:
        out["muc_tieu"] = goal
        out["che_do"] = che_do
        out["nhan_ban"] = replication_list(goal, goals_cfg or {}, book_rows or [], now or datetime.combine(
            today, datetime.min.time()), seen=seen)
    return out


# ---------- goal ket qua ----------

def mode_for(goal: dict | None) -> str | None:
    """`thieu` -> san-video-troi (thu nhieu hon de tim video no); con lai theo-thang; khong co goal -> None."""
    if not goal:
        return None
    return "san-video-troi" if goal.get("trang_thai") == "thieu" else "theo-thang"


SEEN_NAME = "nhan-ban-da-thay.json"


def load_seen(tim: Path) -> dict:
    """{video_id: {"lan_dau": ISO}}: lan dau trai tim thay mot video troi/noi. Loi -> {}."""
    try:
        d = json.loads((Path(tim) / SEEN_NAME).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record_seen(tim: Path, items: list, alerts_path: Path, now: datetime) -> None:
    """Video moi vao danh sach nhan ban: ghi lan dau thay (de han khong troi theo moi lan chay) va MOT dong
    canh bao trong alerts.md. Khong bao gio lam trai tim loi."""
    try:
        seen = load_seen(tim)
        new = [x for x in items if x.get("video_id") and x["video_id"] not in seen]
        if not new:
            return
        for x in new:
            seen[x["video_id"]] = {"lan_dau": now.isoformat(timespec="minutes")}
        (Path(tim) / SEEN_NAME).write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
        alerts_path = Path(alerts_path)
        head = "" if alerts_path.is_file() and alerts_path.stat().st_size else "# Studio loop alerts\n\n"
        with alerts_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(head)
            for x in new:
                why = (f"CTR {x['ctr_pct']}%, {x.get('hien_thi')} hiển thị" if x.get("ly_do") == "ctr"
                       else f"{x.get('view')} view sau {x.get('tuoi_ngay')} ngày")
                f.write(f"- {now:%Y-%m-%d %H:%M} heart: video đang được đẩy \"{x.get('tieu_de')}\" "
                        f"({x['video_id']}): {why}. Cần {x.get('so_chu_de')} chủ đề nối tiếp trước {x.get('han')}.\n")
    except Exception as e:  # noqa: BLE001 - so ghi chep, khong dung trai tim
        print(f"heart: không ghi được danh sách video trội đã thấy ({type(e).__name__}: {e})")


def replication_list(goal: dict, goals_cfg: dict, book_rows: list, now: datetime,
                     seen: dict | None = None) -> list[dict]:
    """Video troi chua co dong `nhan-ban` (slug `video:<id>`) trong so quyet dinh. Han tinh tu lan dau thay
    (`seen`), khong tu `now`, de han khong lui dan khi trai tim chay nhieu lan trong ngay."""
    vt = goals_cfg.get("video_troi") or {}
    vn = goals_cfg.get("video_noi") or {}
    done = {r.get("slug") for r in book_rows if r.get("tac_vu") == "nhan-ban"}
    seen = seen or {}
    out = []
    for v in goal.get("video_troi") or []:
        if f"video:{v['video_id']}" in done:
            continue
        cfg = vn if v.get("ly_do") == "ctr" else vt
        try:
            base = datetime.fromisoformat((seen.get(v["video_id"]) or {}).get("lan_dau") or "")
        except (TypeError, ValueError):
            base = now
        e = {"video_id": v["video_id"], "tieu_de": v["tieu_de"], "view": v["view"], "tuoi_ngay": v["tuoi_ngay"],
             "so_chu_de": cfg.get("so_chu_de_noi_tiep", 3),
             "han": (base + timedelta(hours=cfg.get("han_gio", 48))).isoformat(timespec="minutes")}
        for k in ("ly_do", "ctr_pct", "hien_thi", "gio_doc"):
            if k in v:
                e[k] = v[k]
        out.append(e)
    return out


def _goal_md(d: dict) -> list[str]:
    g = d.get("muc_tieu")
    if not g:
        return []

    def n(v, nd=0):
        if v is None:
            return "—"
        return f"{v:,.{nd}f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    ym = g["thang"]
    mm = f"{ym[5:7]}/{ym[:4]}"
    tv = (g.get("muc_tieu") or {}).get("view_thang")
    L = [f"## Goal tháng {mm}", ""]
    tgt = g.get("muc_tieu") or {}
    if tgt.get("sap_toi"):
        mo = tgt.get("thang_moc") or ym
        L += [f"Tháng {mm} chưa có goal riêng; so với mục tiêu tháng {mo[5:7]}/{mo[:4]} ({n(tv)}).", ""]
    if g.get("trang_thai") == "chua-co-so":
        L += ["Chưa có số liệu kênh theo ngày để so với goal.", ""]
    else:
        if g["trang_thai"] == "dung-huong":
            verdict = "đúng hướng"
        else:
            verdict = f"thiếu {n(max(0, (tv or 0) - g['du_bao_view_thang']))}"
        L.append(f"- View tháng {n(g['view_thang'])} / mục tiêu {n(tv)} (dự báo {n(g['du_bao_view_thang'])}, {verdict}).")
        L.append(f"- Cần {n(g['view_can_moi_ngay'])} view mỗi ngày còn lại của tháng.")
        L.append(f"- Người đăng ký: {n(g['sub_hien_tai'])} (dự báo cuối tháng {n(g['du_bao_sub_cuoi_thang'])}); "
                 f"doanh thu tháng {n(g['doanh_thu_thang'], 2)} US$ (dự báo {n(g['du_bao_doanh_thu_thang'], 2)}).")
        for r in g.get("chi_so") or []:
            if r["nhom"] != "video":
                continue
            mark = "chưa có số" if r["dat"] is None else ("đạt" if r["dat"] else "chưa đạt")
            L.append(f"- {r['ten']}: {n(r['hien_tai'], 1 if isinstance(r['hien_tai'], float) else 0)} / "
                     f"mục tiêu {n(r['muc_tieu'], 1 if isinstance(r['muc_tieu'], float) else 0)}: {mark}.")
        L.append("")
    che = d.get("che_do")
    if che == "san-video-troi":
        L.append("Chế độ: săn video trội (đang thiếu so với goal nên thử nhánh mới nhiều hơn).")
    elif che:
        L.append("Chế độ: theo tháng (đúng hướng, giữ nhịp thử thường lệ).")
    nb = d.get("nhan_ban") or []
    if nb:
        L.append("")
        def why(x):
            if "ctr_pct" not in x:
                return f"{n(x['view'])} view"
            push = (f"đang được đề xuất: CTR {n(x['ctr_pct'], 1)}%, {n(x['hien_thi'])} hiển thị ở giờ "
                    f"{n(x['gio_doc'], 1)}")
            return push + (f", {n(x['view'])} view" if x.get("ly_do") == "view+ctr" else "")
        L.append("Video trội cần nhân bản trong 48 giờ: " + "; ".join(
            f"{x['tieu_de']} ({why(x)}, {x['tuoi_ngay']} ngày; {x['so_chu_de']} chủ đề nối tiếp, "
            f"hạn {x['han']})" for x in nb) + ".")
    L.append("")
    return L


def _show(cfg: dict, var: str, med: float) -> str:
    if cfg["bien"][var]["do"] == "apv_pct":
        return f"{med * 100:.1f}".replace(".", ",") + "% thời lượng xem"
    if cfg["bien"][var]["do"] == "ctr_24h":
        return f"{med * 100:.1f}".replace(".", ",") + "% tỉ lệ bấm 24 giờ"
    return f"{round(math.exp(med)):,} lượt xem".replace(",", ".")


def render_md(d: dict, cfg: dict) -> str:
    L = [f"# Chỉ thị ngày {d['ngay']}", ""] + _goal_md(d) + [
         f"Phiên bản `{d['phien_ban']}`. Dùng cho video kế tiếp của hàng chờ; còn hạn "
         f"{cfg['chi_thi_han_ngay']} ngày.", "", "## Các lựa chọn hiện tại", ""]
    for v in d["bien_bat"]:
        w = d["nhanh_thang"][v]
        if w["du_so"]:
            L.append(f"- `{v}`: đang dùng `{w['nhanh']}` vì {w['n']} video, trung vị {_show(cfg, v, w['trung_vi'])}.")
        else:
            L.append(f"- `{v}`: chưa đủ số, dùng mặc định `{w['nhanh']}` vì {cfg['bien'][v]['can_cu']}")
    for v, b in cfg["bien"].items():
        if v not in d["bien_bat"]:
            L.append(f"- `{v}`: tạm không dùng.")
    L += ["", "## Video kế tiếp", ""]
    for e in d["muc"]:
        L.append(f"### {e['slug']}")
        if e["kieu"] == "thu":
            L.append(f"- Thử một nhánh mới: biến `{e['bien_thu']}` = `{e['nhanh_thu']}` (các biến khác dùng nhánh thắng).")
        elif e["bien_thu"]:
            L.append(f"- Dùng nhánh thắng của mọi biến; chỉ theo dõi biến `{e['bien_thu']}`.")
        else:
            L.append("- Dùng nhánh thắng của mọi biến.")
        L.append("- Giá trị: " + ", ".join(f"{k}=`{v}`" for k, v in e["gia_tri"].items()))
        if e.get("khuon_het_suat"):
            L.append("- Khuôn tiêu đề: khuôn mới (`moi`), nhưng suất khuôn mới (2 phim / 7 ngày) đã hết suất. Chưa khuôn "
                     "nào đủ số để thay; giữ tiêu đề đã lên kế hoạch và ghi khuôn thật vào sổ khi dựng.")
        L.append(f"- Dự đoán lượt xem 7 ngày: {e['du_doan_7d'] or 'chưa đủ số để đoán'}")
        L.append(f"- Chỉ thị còn hạn tới {e['con_han_toi']}.")
        L.append("")
    g = d["goc"]
    if g["gia_tri"] is not None:
        L.append(f"Gốc dự đoán: {round(g['gia_tri'])} lượt" + (" (gốc tạm)" if g["nguon"] == "tam" else "") + ".")
        L.append("")
    if d["co_cau_tuan"]:
        L += ["Cơ cấu 7 video tuần tới (cho tác vụ chọn chủ đề):"]
        for v, mix in d["co_cau_tuan"].items():
            L.append(f"- `{v}`: " + ", ".join(f"{a} {n}" for a, n in mix.items()))
        L.append("")
    L += ["## Tín hiệu khán giả và thị trường", ""]
    cum, tk = d["tin_hieu"]["cum"], d["tin_hieu"]["tu_khoa"]
    L.append("Cụm chủ đề nóng: " + (", ".join(cum) if cum else "chưa có (thiếu nghien-cuu.md)") + ".")
    L.append("Từ khoá người xem tìm: " + (", ".join(tk) if tk else "chưa có (thiếu số liệu kênh)") + ".")
    L += ["", "## Ràng buộc luôn áp", ""] + [f"- {c}" for c in d["rang_buoc"]] + [""]
    return "\n".join(L)


def write_directive(tim: Path, directive: dict, cfg: dict | None = None) -> None:
    tim = Path(tim)
    tim.mkdir(parents=True, exist_ok=True)
    cfg = cfg or hc.load_config(HERE / "heart_config.json")
    for name, text in (("chi-thi.json", json.dumps(directive, ensure_ascii=False, indent=1)),
                       ("chi-thi.md", render_md(directive, cfg))):
        tmp = tim / (name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, tim / name)


# ---------- doc dau vao ----------

def read_queue(path: Path) -> list[str]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("|"):
            continue
        cell = ln.strip("|").split("|")[0].strip()
        if cell and cell.lower() != "slug" and not set(cell) <= set("-: "):
            out.append(cell)
    return out


def read_queue_plan(path: Path) -> dict:
    """slug -> du_doan_7d cua hang-cho.md (cot `du_doan_7d`), chi lay gia tri dang lo-hi."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    out, col = {}, None
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if cells[0].lower() == "slug":
            col = cells.index("du_doan_7d") if "du_doan_7d" in cells else None
        elif col is not None and col < len(cells) and _RANGE.match(cells[col]):
            out[cells[0]] = cells[col]
    return out


def last_tested(rows: list[dict], merged: dict) -> dict:
    """bien -> ngay gan nhat mot phim that su dung nhanh thu cua no. Dong `dung` ghi nham bien_thu roi duoc sua
    bang dong sau (gia tri gop cua slug khac nhanh_thu) khong tinh: so chi them dong, khong xoa duoc dau cu."""
    last: dict = {}
    for r in rows:
        if r.get("tac_vu") != "dung" or not r.get("bien_thu"):
            continue
        var = r["bien_thu"]
        if (merged.get(r.get("slug")) or {}).get(var) != r.get("nhanh_thu"):
            continue
        try:
            d = date.fromisoformat(str(r.get("thoi_diem", ""))[:10])
        except ValueError:
            continue
        last[var] = max(d, last.get(var, d))
    return last


def planned_values(rows: list[dict], cfg: dict) -> dict:
    """slug -> {bien: nhanh} tu cac dong `ke-hoach` cua so (dong sau de dong truoc, bo o rong)."""
    out: dict = {}
    for r in rows:
        if r.get("tac_vu") != "ke-hoach" or not r.get("slug"):
            continue
        for v in cfg["bien"]:
            if r.get(v):
                out.setdefault(r["slug"], {})[v] = r[v]
    return out


HOT_DAYS = 7      # mac dinh; lay `video_noi.trong_ngay` cua muc-tieu.json neu co


def hot_shapes(ledger_rows: list[dict], merged: dict, cfg: dict, goals_cfg: dict | None) -> set:
    """Khuon tieu de mien tran: co it nhat mot phim toi da 7 ngay tuoi dat nguong "video dang duoc day" cua
    muc-tieu.json (video_noi: CTR >= 7%, >= 30.000 luot hien thi, trong_ngay 7), tinh tren so moi nhat cua ledger.
    Chu kenh 2026-11-24 ("Yes"): 7 ngay, khong phai 8 tuan - phim trung tu thang truoc khong con duoc day, va tran
    co la vi khuon mon di (khuon "Gun Counters Pray" dung lai con 487-8.523 view).

    Chu kenh 2026-11-24 ("agree both"): tran 2 lan/8 tuan chan dung khuon dang thang (lineup-nam sau sw_2027 va
    taurus_2027), nguoc voi luat cuoi theo video dang duoc day. Khuon cua phim: so quyet dinh (slug) -> `phim_cu`
    cua config (phim truoc khi co so) -> cot title_shape cua ledger. `moi` khong bao gio mien tran."""
    vn = (goals_cfg or {}).get("video_noi") or {}
    ctr_min, impr_min = vn.get("ctr_toi_thieu", 0.07), vn.get("hien_thi_toi_thieu", 30000)
    window_h = vn.get("trong_ngay", HOT_DAYS) * 24
    var = cfg["bien"].get("khuon_tieu_de") or {}
    old, arms = var.get("phim_cu") or {}, set(var.get("nhanh") or [])
    out = set()
    for r in ledger_rows or []:
        age, ctr, impr = _num(r.get("age_h_latest")), _num(r.get("ctr_latest")), _num(r.get("impr_latest"))
        if age is None or age > window_h or ctr is None or impr is None or ctr < ctr_min or impr < impr_min:
            continue
        slug = r.get("slug") or ""
        shape = (merged.get(slug) or {}).get("khuon_tieu_de") or old.get(slug) or r.get("title_shape") or ""
        if shape in arms and shape != "moi":
            out.add(shape)
    return out


def forbidden_arms(rows: list[dict], today: date, exclude: str = "", hot=()) -> dict:
    """Khuon toi da 2 lan/56 ngay; `moi` toi da 2 lan/7 ngay (moi video tinh mot lan); `moi` chi theo gioi han 7 ngay.
    Chu de da bo (dong `bo`) tra lai suat; chu de doi khuon trong hang cho chi tinh dong `ke-hoach` moi nhat.
    `hot` (hot_shapes): khuon co phim dang duoc day, khong tinh tran."""
    gone = bk.dropped(rows)
    last_plan = {}
    for i, r in enumerate(rows):
        if r.get("tac_vu") == "ke-hoach" and r.get("khuon_tieu_de") and r.get("slug"):
            last_plan[r["slug"]] = i
    # Phim da dung: chi dong `dung` moi nhat co khuon moi tinh (so chi them dong; phim ghi `moi` roi gan lai nhan
    # truoc day van giu mot suat `moi`), va dong `ke-hoach` cua no khong tinh nua.
    last_built = {}
    for i, r in enumerate(rows):
        if r.get("tac_vu") == "dung" and r.get("khuon_tieu_de") and r.get("slug"):
            last_built[r["slug"]] = i
    seen: dict = {}
    for i, r in enumerate(rows):
        arm = r.get("khuon_tieu_de") or ""
        if r.get("tac_vu") not in ("ke-hoach", "dung") or not arm or not r.get("slug") or r["slug"] == exclude:
            continue
        if r["tac_vu"] == "ke-hoach" and (r["slug"] in gone or last_plan.get(r["slug"]) != i
                                          or r["slug"] in last_built):
            continue
        if r["tac_vu"] == "dung" and last_built.get(r["slug"]) != i:
            continue
        try:
            age = (today - date.fromisoformat(str(r.get("thoi_diem", ""))[:10])).days
        except ValueError:
            continue
        seen.setdefault(arm, {})[r["slug"]] = min(age, seen.get(arm, {}).get(r["slug"], age))
    hot = hot or ()
    bad = {a for a, by in seen.items()
           if a != "moi" and a not in hot and sum(1 for g in by.values() if g <= 56) >= 2}
    if sum(1 for g in seen.get("moi", {}).values() if g <= 7) >= 2:
        bad.add("moi")
    return {"khuon_tieu_de": bad} if bad else {}


REFUSAL_NOTE = "publishAt rejected:"
REFUSAL_DAYS = 7


def publish_refusals(cache: Path) -> list[tuple[date, str]]:
    """(ngay, video_id) cua cac dong lich su co ghi chu BAT DAU bang `publishAt rejected:`. `publishAt passed` khong tinh."""
    out = []
    try:
        with open(Path(cache) / fetch.HISTORY_NAME, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if not (r.get("ghi_chu") or "").startswith(REFUSAL_NOTE):
                    continue
                try:
                    d = date.fromisoformat(str(r.get("thoi_gian", ""))[:10])
                except ValueError:
                    continue
                out.append((d, (r.get("video_id") or "").strip()))
    except OSError:
        return []
    return out


def _active_refusals(cache: Path, today: date) -> list[tuple[date, str]]:
    return [(d, v) for d, v in publish_refusals(cache) if 0 <= (today - d).days < REFUSAL_DAYS]


def publish_rejected(cache: Path, today: date | None = None) -> bool:
    """gio_dang tat 7 ngay ke tu ngay YouTube tu choi publishAt (khong tat vinh vien)."""
    return bool(_active_refusals(cache, today or datetime.now().date()))


def alert_refusals(cache: Path, today: date, alerts_path: Path) -> None:
    """Moi lan tu choi dang con hieu luc ghi dung mot dong vao alerts.md (khoa theo video_id, khong lap moi dem)."""
    active = _active_refusals(cache, today)
    if not active:
        return
    alerts_path = Path(alerts_path)
    try:
        have = alerts_path.read_text(encoding="utf-8") if alerts_path.is_file() else ""
    except OSError:
        have = ""
    done = {ln for ln in have.splitlines() if "heart: gio_dang tắt 7 ngày" in ln}
    new = []
    for d, vid in active:
        line = f"- {d.isoformat()} heart: gio_dang tắt 7 ngày vì YouTube từ chối publishAt ({vid})"
        if line not in done and line not in new:
            new.append(line)
    if not new:
        return
    alerts_path.parent.mkdir(parents=True, exist_ok=True)
    with alerts_path.open("a", encoding="utf-8", newline="\n") as f:
        lead = "" if not have or have.endswith("\n") else "\n"
        f.write(lead + "\n".join(new) + "\n")


def apply_publish_rejected(cfg: dict) -> dict:
    out = copy.deepcopy(cfg)
    if "gio_dang" in out["bien"]:
        out["bien"]["gio_dang"]["khong_kha_dung"] = True
    return out


def hot_clusters(report_dir: Path, limit: int = 5):
    try:
        lines = (Path(report_dir) / "nghien-cuu.md").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    out, inside = [], False
    for ln in lines:
        if ln.startswith("## "):
            if inside:
                break
            inside = ln.startswith("## 2.")
            continue
        if inside and ln.strip().startswith("|"):
            cell = ln.strip().strip("|").split("|")[0].strip()
            if cell and cell != "Cụm" and not set(cell) <= set("-: "):
                out.append(cell)
    return out[:limit] or None


def search_terms(cache: Path, limit: int = 8):
    doc, _ = latest_doc(str(cache))
    if not doc:
        return None
    for c in (doc.get("tabs") or {}).get("hub_content") or []:
        if c.get("id") == SEARCH_CARD:
            terms = [(r.get("label") or "").strip() for r in c.get("rows") or []]
            terms = [t for t in terms if t and t.lower() != SEARCH_SKIP]
            return terms[:limit] or None
    return None


def _read_ledger(report_dir: Path) -> list[dict]:
    try:
        with open(Path(report_dir) / "ledger.csv", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


GOALS_NAME = "muc-tieu.json"


def goal_progress(tim: Path, cache: Path, ledger: list[dict], today: date, snaps: dict | None = None):
    """(tien do, goal) tu <tim>/muc-tieu.json; loi gi cung chi canh bao va tra (None, None): trai tim khong
    bao gio hong vi goal."""
    try:
        goals = goals_mod.load_goals(Path(tim) / GOALS_NAME)
        if goals is None:
            print(f"heart: cảnh báo, không đọc được {GOALS_NAME}; chỉ thị không có khối goal")
            return None, None
        doc, _ = latest_doc(str(cache))
        totals = channel_totals(doc) if doc else {}
        prog = goals_mod.progress(goals, goals_mod.channel_daily(cache), totals, ledger, today, snaps=snaps)
        json.dumps(prog)
        return prog, goals
    except Exception as e:  # noqa: BLE001
        print(f"heart: cảnh báo, tính goal lỗi {type(e).__name__}: {e}; chỉ thị không có khối goal")
        return None, None


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--heart-config", default=str(HERE / "heart_config.json"))
    a = ap.parse_args(argv)
    try:
        loop_cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
        report_dir = Path(loop_cfg["report_dir"])
        tim = report_dir.parents[1] / "tim"
        vault = report_dir.parents[2]
        cache = HERE / "cache"
        cfg = hc.load_config(Path(a.heart_config))
        today = datetime.now().date()
        if publish_rejected(cache, today):
            print("heart: cảnh báo, publishAt bị YouTube từ chối; gio_dang tắt 7 ngày")
            cfg = apply_publish_rejected(cfg)
            try:
                alert_refusals(cache, today, report_dir / "alerts.md")
            except OSError as e:
                print(f"heart: không ghi được alerts.md ({e})")
        rows = bk.read(tim / bk.BOOK_NAME)
        merged = bk.merged(rows)
        dung = {r["slug"] for r in rows if r["tac_vu"] == "dung"}
        ledger = _read_ledger(report_dir)
        snaps = since_published(fetch.load_snapshots(cache))
        scored = heart_score.score(merged, ledger, snaps)
        heart_score.write_results(tim / heart_score.RESULT_NAME, scored)
        graded = [r for r in scored if r["slug"] in dung]
        stats = arm_stats(graded, {s: m for s, m in merged.items() if s in dung}, cfg, rows=rows)
        last = last_tested(rows, merged)
        signals = {"cum": hot_clusters(report_dir), "tu_khoa": search_terms(cache)}
        qpath = vault / "van-hanh" / "ke-hoach" / "hang-cho.md"
        queue = read_queue(qpath)
        goal, goals = goal_progress(tim, cache, ledger, today, snaps)
        now = datetime.now()
        hot = hot_shapes(ledger, merged, cfg, goals)
        directive = build_directive(queue, stats, cfg, today, signals,
                                    now.strftime("%Y-%m-%d-%H%M"), last_tested=last,
                                    goc=goc_view(graded, ledger, today), hot=hot,
                                    cam=forbidden_arms(rows, today, hot=hot), plan=read_queue_plan(qpath),
                                    planned=planned_values(rows, cfg), book_rows=rows,
                                    goal=goal, goals_cfg=goals, now=now, seen=load_seen(tim))
        write_directive(tim, directive, cfg)
        record_seen(tim, directive.get("nhan_ban") or [], report_dir / "alerts.md", now)
    except Exception as e:  # noqa: BLE001 - khong bao gio dung phim, chi bao loi
        print(f"heart: lỗi {type(e).__name__}: {e}")
        return 1
    for v in cfg["bien"]:
        print(f"heart: {v}: " + ", ".join(f"{k} n={s['n']} {s['trang_thai']}" for k, s in stats[v].items()))
    print(f"heart: chỉ thị {directive['phien_ban']} cho {', '.join(e['slug'] for e in directive['muc'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
