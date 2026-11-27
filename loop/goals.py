"""Goal ket qua cua kenh (muc-tieu.json trong vault `tim`) va tien do so voi goal. Chi stdlib, ham thuan.

    from loop.goals import load_goals, target_for_month, channel_daily, progress

Chu kenh dat file; trai tim, bang dieu khien, bao cao doc file nay. Khong ham nao nem loi voi du lieu thieu.
"""
from __future__ import annotations

import calendar
import json
import math
import statistics
from datetime import date, datetime
from pathlib import Path

from loop import channel_view as cv

WINDOW_DAYS = 28
PACE_DAYS = 7
BIG_VIDEO = 100_000


# ---------- doc goal ----------

def load_goals(path) -> dict | None:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("moc"), list) and d["moc"] else None


def _mi(ym: str) -> int:
    y, m = ym.split("-")[:2]
    return int(y) * 12 + int(m) - 1


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _lerp(a, b, t: float):
    if _is_num(a) and _is_num(b):
        x = a + (b - a) * t
        return round(x) if isinstance(a, int) and isinstance(b, int) else round(x, 2)
    return a


def _mix(a: dict, b: dict, t: float) -> dict:
    out = {}
    for k, v in a.items():
        if isinstance(v, dict) and isinstance(b.get(k), dict):
            out[k] = _mix(v, b[k], t)
        elif k in b:
            out[k] = _lerp(v, b[k], t)
        else:
            out[k] = v
    return out


def target_for_month(goals: dict | None, ym: str) -> dict:
    mocs = sorted((m for m in (goals or {}).get("moc", []) if m.get("thang")), key=lambda m: _mi(m["thang"]))
    if not mocs:
        return {}
    cur = _mi(ym)
    res = None
    for m in mocs:
        if _mi(m["thang"]) == cur:
            res = {**json.loads(json.dumps(m)), "noi_suy": False}
            break
    if res is None:
        if cur < _mi(mocs[0]["thang"]):
            res = {**json.loads(json.dumps(mocs[0])), "noi_suy": False, "sap_toi": True,
                   "thang_moc": mocs[0]["thang"]}
        elif cur > _mi(mocs[-1]["thang"]):
            res = {**json.loads(json.dumps(mocs[-1])), "noi_suy": False}
        else:
            for lo, hi in zip(mocs, mocs[1:]):
                if _mi(lo["thang"]) < cur < _mi(hi["thang"]):
                    t = (cur - _mi(lo["thang"])) / (_mi(hi["thang"]) - _mi(lo["thang"]))
                    res = {**_mix(lo, hi, t), "noi_suy": True}
                    break
    if res is None:
        return {}
    res["thang"] = ym
    floor, since = (goals or {}).get("san_view_thang"), (goals or {}).get("san_tu_thang")
    if res["noi_suy"] and _is_num(floor) and since and cur >= _mi(since) and _is_num(res.get("view_thang")):
        res["view_thang"] = max(res["view_thang"], floor)
    return res


# ---------- so lieu kenh theo ngay ----------

def channel_daily(cache_dir) -> list[dict]:
    """Hop cac dong theo ngay cua moi tai lieu kenh doc duoc (moi tai lieu 28 ngay); tai lieu moi thang."""
    by_day: dict = {}
    for _name, doc in cv.iter_docs(str(cache_dir)):
        try:
            rows = cv.daily(doc)
        except Exception:  # noqa: BLE001 - mot tai lieu hong khong lam mat cac tai lieu khac
            continue
        for r in rows:
            cur = by_day.setdefault(r["ngay"], {})
            for k, v in r.items():
                if v is not None or k not in cur:
                    cur[k] = v
    return [by_day[d] for d in sorted(by_day)]


# ---------- tien do ----------

def _num(v):
    try:
        return None if v in ("", None) else float(v)
    except (TypeError, ValueError):
        return None


def _pub_date(r: dict):
    try:
        return datetime.strptime(str(r.get("published_local", ""))[:16], "%Y-%m-%d %H:%M").date()
    except ValueError:
        try:
            return date.fromisoformat(str(r.get("published_local", ""))[:10])
        except ValueError:
            return None


def _views(r: dict):
    xs = [x for x in (_num(r.get("views_latest")), _num(r.get("views_7d"))) if x is not None]
    return max(xs) if xs else None


def _median(xs):
    return statistics.median(xs) if xs else None


def _row(nhom, ma, ten, hien_tai, du_bao, muc_tieu):
    probe = du_bao if nhom == "kenh" else hien_tai
    dat = None if probe is None or muc_tieu is None else probe >= muc_tieu
    return {"nhom": nhom, "ma": ma, "ten": ten, "hien_tai": hien_tai, "du_bao": du_bao,
            "muc_tieu": muc_tieu, "dat": dat}


def _pct(v):
    """CTR ve phan so: 0,084 giu nguyen, 8,4 -> 0,084."""
    x = _num(v)
    return None if x is None else (x / 100 if x > 1 else x)


def _channel_ctr_base(rows, ref, vn: dict, skip=None) -> dict:
    """Trung vi CTR 24h cua cac video 28 ngay gan nhat (tru `skip`); None neu < `nen_toi_thieu_video` video."""
    xs = []
    for r in rows or []:
        d = _pub_date(r)
        if d is None or r.get("video_id") == skip or not 0 <= (ref - d).days <= WINDOW_DAYS:
            continue
        c = _pct(r.get("ctr_24h"))
        if c is not None:
            xs.append(c)
    return {"ctr": _median(xs), "n": len(xs)} if len(xs) >= vn.get("nen_toi_thieu_video", 5) else {}


def _pushed_reading(r: dict, snaps: dict | None, vn: dict, base_ctr: float | None = None):
    """Luot doc dau tien trong `trong_gio` gio dau ma CTR >= nguong va hien thi >= nguong (chu kenh 2026-11-22:
    "video dang de xuat"). Snapshot since_published truoc; khong co thi doc ledger (latest neu con trong cua so,
    roi moc 24 gio). Tra (ctr, hien_thi, gio) hoac None."""
    win, c_min, i_min = vn.get("trong_gio", 48), vn.get("ctr_toi_thieu", 0.07), vn.get("hien_thi_toi_thieu", 30000)
    win_i = vn.get("trong_gio_hien_thi", 24)  # dieu kien 2: hien thi trong 24 gio dau
    i_24 = vn.get("hien_thi_24h_toi_thieu", 20000)
    reads = []
    for s in (snaps or {}).get(r.get("video_id", ""), []):
        reads.append((_pct(s.get("ctr")), _num(s.get("impressions")), _num(s.get("age_hours"))))
    if not reads:
        age = _num(r.get("age_h_latest"))
        if age is not None and age <= win:
            reads.append((_pct(r.get("ctr_latest")), _num(r.get("impr_latest")), age))
        reads.append((_pct(r.get("ctr_24h")), _num(r.get("impr_24h")), 24.0))
    for c, i, h in reads:
        if c is None or h is None:
            continue
        # Dieu kien 1: CTR >= nguong VA hien thi >= nguong, trong 48 gio. Dieu kien 2: hien thi >= 20.000 trong 24 gio.
        ok_1 = h <= win and c >= c_min and i is not None and i >= i_min
        ok_2 = i is not None and h <= win_i and i >= i_24
        # Dieu kien 3 (chu kenh 2026-11-27, "troi so voi kenh"): CTR >= k lan trung vi CTR 24h cua kenh va hien thi
        # >= san nho, trong 48 gio. Chi bat khi co du video lam nen (base_ctr khac None).
        ok_3 = (base_ctr is not None and h <= win and c >= vn.get("ctr_tuong_doi", 2.0) * base_ctr
                and i is not None and i >= vn.get("hien_thi_tuong_doi_toi_thieu", 5000))
        if ok_1 or ok_2 or ok_3:
            return round(c * 100, 1), (round(i) if i is not None else None), round(h, 1)
    return None


def progress(goals, daily, totals, ledger_rows, today: date, snaps: dict | None = None,
             month_revenue: dict | None = None) -> dict:
    """`snaps` = video_id -> snapshot since_published (loop.state.since_published), cho nguong video dang de xuat.
    `month_revenue` = {"YYYY-MM": US$} tu bang doanh thu hang thang cua Studio (channel_view.month_revenue)."""
    month_revenue_by = month_revenue or {}
    with_data = [r for r in daily or [] if _num(r.get("luot_xem")) is not None and r.get("ngay")]
    with_data.sort(key=lambda r: r["ngay"])
    if not with_data:
        ym = today.strftime("%Y-%m")
        return {"thang": ym, "muc_tieu": target_for_month(goals, ym), "trang_thai": "chua-co-so",
                "ngay_co_so": 0, "chi_so": [], "video_troi": []}
    ym = today.strftime("%Y-%m")
    if not any(r["ngay"].startswith(ym) for r in with_data):
        ym = with_data[-1]["ngay"][:7]
    y, m = int(ym[:4]), int(ym[5:7])
    dim = calendar.monthrange(y, m)[1]
    target = target_for_month(goals, ym)
    month_revenue = month_revenue_by.get(ym)
    mine =[r for r in with_data if r["ngay"].startswith(ym)]
    n_days = len(mine)
    view_m = round(sum(_num(r["luot_xem"]) for r in mine))
    forecast = round(view_m / n_days * dim)
    if (today.year, today.month) == (y, m):
        left = dim - today.day + 1
    else:
        left = max(1, dim - int(mine[-1]["ngay"][8:10]))
    tv = target.get("view_thang")
    need = math.ceil(max(0, tv - view_m) / max(1, left)) if _is_num(tv) else None
    view_28 = round(sum(_num(r["luot_xem"]) for r in with_data[-WINDOW_DAYS:]))

    sub_now = _num((totals or {}).get("tong_dang_ky"))
    last7 = [_num(r.get("dang_ky_moi")) for r in with_data[-PACE_DAYS:]]
    last7 = [x for x in last7 if x is not None]
    sub_fc = None if sub_now is None else round(sub_now + (sum(last7) / len(last7) if last7 else 0) * left)

    rev_days = [r for r in mine if _num(r.get("doanh_thu")) is not None]
    rev_m = round(sum(_num(r["doanh_thu"]) for r in rev_days), 2) if rev_days else None
    rev_fc = round(rev_m / len(rev_days) * dim, 2) if rev_days else None
    # Studio dien doanh thu theo ngay cham 1-2 ngay (va la nhung ngay moi nhat, thuong lon nhat), nen tong cac
    # ngay thieu so that. So thang cua chinh Studio (bang "Doanh thu hang thang") day du hon: lon hon thi dung no.
    if _is_num(month_revenue) and (rev_m is None or month_revenue > rev_m):
        rev_m = round(month_revenue, 2)
        rev_fc = round(rev_m / n_days * dim, 2)
    rev7 = [r for r in with_data if _num(r.get("doanh_thu")) is not None][-PACE_DAYS:]
    v7 = sum(_num(r["luot_xem"]) for r in rev7)
    rpm = round(sum(_num(r["doanh_thu"]) for r in rev7) / v7 * 1000, 2) if rev7 and v7 > 0 else None

    ref = today if (today.year, today.month) == (y, m) else date.fromisoformat(mine[-1]["ngay"])
    window, month_vids, vt = [], [], []
    vng =target.get("video_noi") or (goals or {}).get("video_noi") or {}
    base = _channel_ctr_base(ledger_rows, ref, vng)
    for r in ledger_rows or []:
        d = _pub_date(r)
        if d is None:
            continue
        age = (ref - d).days
        if 0 <= age <= WINDOW_DAYS:
            window.append(r)
        if (d.year, d.month) == (y, m):
            month_vids.append(r)
        v = _views(r)
        age_d = (today - d).days
        # Video da duoc day (dat nguong trong 48 gio dau) van giu co hieu luc sau khi tuoi hom nay vuot `trong_ngay`:
        # Ruger 2027 dat nguong luc 13,9 gio (20/11) nhung bi roi khoi danh sach ngay 27/11 vi tuoi hom nay la 10 ngay.
        rel = base.get("ctr") if base and 0 <= age_d <= vng.get("trong_ngay", 7) else None
        if rel is not None:   # nen = cac video KHAC (trung vi khong tinh chinh no)
            rel = _channel_ctr_base(ledger_rows, ref, vng, skip=r.get("video_id")).get("ctr")
        pushed = _pushed_reading(r, snaps, vng, rel) if vng else None
        if pushed:
            e = {"video_id": r.get("video_id", ""), "tieu_de": r.get("live_title", ""),
                 "view": round(v or 0), "tuoi_ngay": age_d, "ly_do": "ctr", "ctr_pct": pushed[0],
                 "hien_thi": pushed[1], "gio_doc": pushed[2]}
            vt.append(e)
    vt.sort(key=lambda x: -x["view"])
    m7 = _median([x for x in (_num(r.get("views_7d")) for r in window) if x is not None])
    ctr = _median([x for x in (_num(r.get("ctr_24h")) for r in window) if x is not None])
    ret = _median([x for x in (_num(r.get("ret_30s")) for r in window) if x is not None])
    n100 = sum(1 for r in month_vids if (_views(r) or 0) >= BIG_VIDEO) if ledger_rows else None
    ctr = None if ctr is None else round(ctr * 100 if ctr <= 1 else ctr, 2)
    ret = None if ret is None else round(ret * 100 if ret <= 1 else ret, 1)
    tvid = target.get("video") or {}

    out = {
        "thang": ym, "muc_tieu": target, "ngay_co_so": n_days, "ngay_trong_thang": dim,
        "view_thang": view_m, "du_bao_view_thang": forecast, "view_28_ngay": view_28,
        "view_can_moi_ngay": need,
        "sub_hien_tai": None if sub_now is None else round(sub_now), "du_bao_sub_cuoi_thang": sub_fc,
        "doanh_thu_thang": rev_m, "du_bao_doanh_thu_thang": rev_fc, "rpm_7_ngay": rpm,
        "view_7d_trung_vi": None if m7 is None else round(m7), "so_video_100k_thang": n100,
        "ctr_24h_trung_vi": ctr, "giu_30s_trung_vi": ret,
        "trang_thai": "chua-co-so" if tv is None else ("dung-huong" if forecast >= tv else "thieu"),
        "video_troi": vt,
    }
    out["chi_so"] = [
        _row("kenh", "view_thang", "Lượt xem trong tháng", view_m, forecast, tv),
        _row("kenh", "sub_cuoi_thang", "Người đăng ký cuối tháng", out["sub_hien_tai"], sub_fc,
             target.get("sub_cuoi_thang")),
        _row("kenh", "doanh_thu_thang", "Doanh thu tháng (US$)", rev_m, rev_fc, target.get("doanh_thu_thang_usd")),
        _row("video", "view_7d_trung_vi", "View 7 ngày của video mức giữa", out["view_7d_trung_vi"], None,
             tvid.get("view_7d_trung_vi")),
        _row("video", "so_video_100k_thang", "Video từ 100.000 view trong tháng", n100, None,
             tvid.get("so_video_100k_thang")),
        _row("video", "ctr_24h_trung_vi", "Tỉ lệ bấm 24 giờ (%)", ctr, None, tvid.get("ctr_24h_pct")),
        _row("video", "giu_30s_trung_vi", "Giữ người xem tới giây 30 (%)", ret, None, tvid.get("giu_30s_pct")),
    ]
    return out
