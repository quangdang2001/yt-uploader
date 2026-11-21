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
from loop.channel_view import latest_doc
from loop.state import since_published

HERE = Path(__file__).resolve().parent
SEARCH_CARD = "how_viewers_find_your_videos#tim_kiem_youtube"
SEARCH_SKIP = "tổng tỷ lệ lưu lượng truy cập"
QUEUE_ENTRIES = 3
WEEK_VIDEOS = 7
WEEK_SPLIT = 0.7
GOC_MIN_ROWS = 3
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


def _metric(cfg: dict, var: str, row: dict):
    if cfg["bien"][var]["do"] == "apv_pct":
        return _num(row.get("apv_pct"))
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

def seed(slug: str) -> int:
    return int(hashlib.sha256(slug.encode()).hexdigest()[:12], 16)


def _gap(var_stats: dict, valid: list[str]) -> float:
    meds = sorted((var_stats[a]["trung_vi"] for a in valid if var_stats[a]["trung_vi"] is not None),
                  reverse=True)
    return meds[0] - meds[1] if len(meds) >= 2 else math.inf


def choose_test(slug: str, stats: dict, cfg: dict, last_tested: dict, today: date, cam: dict) -> dict:
    rng = random.Random(seed(slug))
    valid = {v: [a for a in hc.arms(cfg, v) if a not in cam.get(v, ())] for v in hc.enabled_vars(cfg)}
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
    if rng.random() < cfg["ti_le_thu"]:
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
    step = 100 if x >= 1000 else 10
    return max(int(round(x / step)) * step, 0)


def predict(stats: dict, cfg: dict, bien: str, nhanh: str, goc) -> str:
    st = (stats.get(bien) or {}).get(nhanh)
    if st and st["n"] >= cfg["n_thang"] and cfg["bien"][bien]["do"] == "views_7d":
        lo, hi = math.exp(st["p25"]), math.exp(st["p75"])
    elif goc:
        lo, hi = goc * 0.5, goc * 2.0
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
    others = [a for a in arms if a != win and stats[var][a]["trang_thai"] != "loai"] or \
             [a for a in arms if a != win]
    mix = {a: 0 for a in arms}
    mix[win] = main if others else WEEK_VIDEOS
    for i in range(WEEK_VIDEOS - mix[win]):
        mix[others[i % len(others)]] += 1
    return mix


def build_directive(queue: list[str], stats: dict, cfg: dict, today: date, signals: dict, phien_ban: str,
                    last_tested: dict | None = None, cam: dict | None = None, goc=(None, "khong-co")) -> dict:
    win = winners(stats, cfg)
    on = hc.enabled_vars(cfg)
    last = dict(last_tested or {})
    slugs = list(queue[:QUEUE_ENTRIES]) or ["_tu-chon"]
    muc = []
    for slug in slugs:
        t = choose_test(slug, stats, cfg, last, today, cam or {})
        gia_tri = {v: win[v] for v in on}
        if t["kieu"] == "thu":
            gia_tri[t["bien"]] = t["nhanh"]
        track = t["bien"] if t["bien"] in gia_tri else ("dong" if "dong" in gia_tri else None)
        if t["bien"]:
            last[t["bien"]] = today
        muc.append({
            "slug": slug, "gia_tri": gia_tri, "bien_thu": t["bien"],
            "nhanh_thu": t["nhanh"] if t["kieu"] == "thu" else "", "kieu": t["kieu"],
            "du_doan_7d": predict(stats, cfg, track, gia_tri[track], goc[0]) if track else "",
            "con_han_toi": (today + timedelta(days=cfg["chi_thi_han_ngay"])).isoformat(),
        })
    return {
        "ngay": today.isoformat(), "phien_ban": phien_ban,
        "nhanh_thang": {v: {"nhanh": win[v], "n": stats[v][win[v]]["n"],
                            "trung_vi": stats[v][win[v]]["trung_vi"],
                            "du_so": stats[v][win[v]]["trang_thai"] == "thang"} for v in on},
        "bien_bat": on, "muc": muc,
        "co_cau_tuan": {v: _week_mix(v, stats, cfg) for v in ("dong", "ten_quen") if v in on},
        "goc": {"gia_tri": goc[0], "nguon": goc[1]},
        "tin_hieu": {"cum": signals.get("cum"), "tu_khoa": signals.get("tu_khoa")},
        "rang_buoc": list(CONSTRAINTS),
    }


def _show(cfg: dict, var: str, med: float) -> str:
    if cfg["bien"][var]["do"] == "apv_pct":
        return f"{med:.2f} % thời lượng xem"
    return f"{round(math.exp(med)):,} lượt xem".replace(",", ".")


def render_md(d: dict, cfg: dict) -> str:
    L = [f"# Chỉ thị ngày {d['ngay']}", "",
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


def publish_rejected(cache: Path) -> bool:
    try:
        with open(Path(cache) / fetch.HISTORY_NAME, encoding="utf-8", newline="") as f:
            return any("publishAt rejected" in (r.get("ghi_chu") or "") for r in csv.DictReader(f))
    except OSError:
        return False


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
        if publish_rejected(cache):
            print("heart: cảnh báo, publishAt bị YouTube từ chối; gio_dang tạm không dùng")
            cfg = apply_publish_rejected(cfg)
        today = datetime.now().date()
        rows = bk.read(tim / bk.BOOK_NAME)
        merged = bk.merged(rows)
        dung = {r["slug"] for r in rows if r["tac_vu"] == "dung"}
        ledger = _read_ledger(report_dir)
        snaps = since_published(fetch.load_snapshots(cache))
        scored = heart_score.score(merged, ledger, snaps)
        heart_score.write_results(tim / heart_score.RESULT_NAME, scored)
        graded = [r for r in scored if r["slug"] in dung]
        stats = arm_stats(graded, {s: m for s, m in merged.items() if s in dung}, cfg, rows=rows)
        last = {}
        for r in rows:
            if r["tac_vu"] == "dung" and r["bien_thu"]:
                try:
                    d = date.fromisoformat(r["thoi_diem"][:10])
                except ValueError:
                    continue
                last[r["bien_thu"]] = max(d, last.get(r["bien_thu"], d))
        signals = {"cum": hot_clusters(report_dir), "tu_khoa": search_terms(cache)}
        queue = read_queue(vault / "van-hanh" / "ke-hoach" / "hang-cho.md")
        directive = build_directive(queue, stats, cfg, today, signals,
                                    datetime.now().strftime("%Y-%m-%d-%H%M"), last_tested=last,
                                    goc=goc_view(graded, ledger, today))
        write_directive(tim, directive, cfg)
    except Exception as e:  # noqa: BLE001 - khong bao gio dung phim, chi bao loi
        print(f"heart: lỗi {type(e).__name__}: {e}")
        return 1
    for v in cfg["bien"]:
        print(f"heart: {v}: " + ", ".join(f"{k} n={s['n']} {s['trang_thai']}" for k, s in stats[v].items()))
    print(f"heart: chỉ thị {directive['phien_ban']} cho {', '.join(e['slug'] for e in directive['muc'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
