"""Bang so lieu toan kenh (tieng Viet) tu studio/channel/<ngay>.json moi nhat trong cache.

Dung chung cho bang dieu khien (dashboard.json) va ban tom tat cho cac tac vu (kenh.md).
Moi ham chi doc tai lieu da parse (scripts/studio_channel.py), khong goi mang.
"""
from __future__ import annotations

import glob
import json
import os

# (khoa trong dashboard, tab, id the) cua cac chuoi 28 ngay theo tung ngay
SERIES = (
    ("luot_xem", "hub_content", "video_views"),
    ("hien_thi", "hub_content", "video_impressions"),
    ("ctr", "hub_content", "video_impressions_ctr"),
    ("xem_tb_giay", "hub_content", "video_average_view_duration"),
    ("dang_ky_moi", "hub_audience", "key_metric_subscribers"),
    ("doanh_thu", "hub_revenue", "key_metric_all"),
    ("khan_gia_thang", "hub_audience", "key_metric_monthly_audience"),
)
# Don vi tho cua bieu do Studio: thoi luong xem tinh bang mili giay, doanh thu bang phan nghin US$.
SERIES_SCALE = {"xem_tb_giay": 1000, "doanh_thu": 1000}
TOTALS = (
    ("luot_xem_28n", "hub_overview", "key_metric_views"),
    ("gio_xem_28n", "hub_overview", "simple_metric_watch_time"),
    ("dang_ky_moi_28n", "hub_overview", "simple_metric_subscribers"),
    ("doanh_thu_28n", "hub_overview", "simple_metric_revenue"),
    ("tong_dang_ky", "hub_overview", "realtime_subscribers"),
    ("view_48h", "hub_overview", "realtime_views.updating_live"),
    ("khan_gia_thang", "hub_audience", "key_metric_monthly_audience"),
    ("hien_thi_28n", "hub_content", "video_impressions"),
    ("ctr_28n", "hub_content", "video_impressions_ctr"),
    ("xem_tb_28n_giay", "hub_content", "video_average_view_duration"),
)
# (nhom hien tren trang, tab, id the, so dong toi da, la ty le %)
BREAKDOWNS = (
    ("Người xem tìm thấy video từ đâu", "hub_content", "how_viewers_find_your_videos#tong_the", 6, True),
    ("Từ khóa tìm kiếm trên YouTube", "hub_content", "how_viewers_find_your_videos#tim_kiem_youtube", 8, True),
    ("Video đề xuất dẫn người xem tới", "hub_content", "how_viewers_find_your_videos#video_de_xuat", 6, True),
    ("Trang web bên ngoài", "hub_content", "how_viewers_find_your_videos#ben_ngoai", 6, True),
    ("Người xem mới hay quay lại", "hub_audience", "audience_segments", 3, True),
    ("Độ tuổi", "hub_audience", "demographics_age", 7, True),
    ("Giới tính", "hub_audience", "demographics_gender", 3, True),
    ("Quốc gia", "hub_audience", "audience_top_geographies_all", 5, True),
    ("Thiết bị (theo giờ xem)", "hub_audience", "device_type", 4, True),
    ("Video giữ người xem tốt nhất ở giây 30", "hub_content", "audience_key_moments#doan_mo_dau", 7, True),
    ("Doanh thu theo tháng (US$)", "hub_revenue", "months_revenue", 6, False),
    ("Video kiếm nhiều tiền nhất, 28 ngày (US$)", "hub_revenue",
     "top_earning_videos_by_content_type_CONTENT_ANALYSIS_TYPE_ALL_CONTENT", 5, False),
    ("Kênh khác mà khán giả của bạn xem", "hub_audience", "competition", 5, False),
    ("Video khác mà khán giả của bạn xem (7 ngày)", "hub_audience", "interests", 5, False),
)
FEED_VI = {
    "research_outliers": "Nổi bật cùng ngách",
    "research_audience": "Khán giả của kênh đã xem",
    "research_recent": "Mới đăng cùng ngách",
}
_SKIP_ROW = ("tổng tỷ lệ lưu lượng truy cập", "các đoạn mở đầu hiệu quả hơn thông thường")


def latest_doc(cache_dir: str):
    files = sorted(glob.glob(os.path.join(cache_dir, "channel", "*.json")))
    for path in reversed(files):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh), os.path.basename(path)
        except (OSError, ValueError):
            continue
    return None, None


def _card(doc, tab, cid):
    for c in (doc.get("tabs") or {}).get(tab) or []:
        if c.get("id") == cid:
            return c
    return None


def _num(v):
    return v if isinstance(v, (int, float)) else None


def totals(doc) -> dict:
    row = {"ngay_doc": (doc.get("captured_at_utc") or "")[:10]}
    for key, tab, cid in TOTALS:
        c = _card(doc, tab, cid)
        row[key] = _num(c.get("value")) if c else None
    for k in ("ctr_28n",):
        if row.get(k) is not None:
            row[k + "_pct"] = round(row.pop(k) * 100, 2)
        else:
            row.pop(k, None)
            row[k + "_pct"] = None
    rpm = None
    c = _card(doc, "hub_revenue", "top_earning_content_by_format_CREATOR_CONTENT_TYPE_VIDEO_ON_DEMAND")
    for r in (c or {}).get("rows") or []:
        if "rpm" in (r.get("label") or "").lower():
            rpm = _num(r.get("value"))
    row["rpm"] = rpm
    return row


def daily(doc) -> list:
    by_day: dict = {}
    for key, tab, cid in SERIES:
        c = _card(doc, tab, cid)
        scale = SERIES_SCALE.get(key, 1)
        for s in (c or {}).get("series") or []:
            for day, val in s.get("points") or []:
                by_day.setdefault(day, {"ngay": day})[key] = round(val / scale, 2) if scale != 1 else val
            break
    rows = []
    for day in sorted(by_day):
        r = {k: by_day[day].get(k) for k, _, _ in SERIES}
        r["ngay"] = day
        if r.get("ctr") is not None:
            r["ctr_pct"] = round(r["ctr"] * 100 if r["ctr"] <= 1 else r["ctr"], 2)
        else:
            r["ctr_pct"] = None
        r.pop("ctr", None)
        rows.append(r)
    return rows


def breakdowns(doc) -> list:
    rows = []
    for gi, (group, tab, cid, limit, is_pct) in enumerate(BREAKDOWNS):
        c = _card(doc, tab, cid)
        if not c:
            continue
        n = 0
        for r in c.get("rows") or []:
            label = (r.get("label") or "").strip()
            if not label or label.lower() in _SKIP_ROW:
                continue
            v = _num(r.get("value"))
            rows.append({
                "khoa": f"{gi:02d}-{n:02d}",
                "nhom": group,
                "ky": c.get("period") or "",
                "muc": label,
                "gia_tri": (round(v * 100, 2) if is_pct and v is not None else v),
                "don_vi": "%" if is_pct else "",
                "chu": r.get("text") or "",
            })
            n += 1
            if n >= limit:
                break
    return rows


def research(doc) -> list:
    rows = []
    for feed, items in (doc.get("research") or {}).items():
        for it in items:
            vid = it.get("video_id")
            rows.append({
                "khoa": f"{feed}:{vid}",
                "luong": FEED_VI.get(feed, feed),
                "video_id": vid,
                "tieu_de": it.get("title"),
                "kenh": it.get("channel"),
                "dang_ky": it.get("subs"),
                "view": it.get("views"),
                "boi_so": it.get("outlier"),
                "tuoi": it.get("age_text"),
                "do_dai": it.get("duration"),
                "link": f"https://www.youtube.com/watch?v={vid}" if vid else None,
            })
    rows.sort(key=lambda r: (r["luong"], -(r["boi_so"] or 0)))
    return rows


def heatmap(doc) -> list:
    c = _card(doc, "hub_audience", "audience_online")
    return [{"thu": d, "gio": h, "muc": lv} for d, h, lv in (c or {}).get("heatmap") or []]


def build(cache_dir: str) -> dict:
    doc, name = latest_doc(cache_dir)
    if doc is None:
        return {"channel_file": None, "kenh_tong": [], "kenh_ngay": [], "kenh_phan_bo": [],
                "nghien_cuu": [], "gio_online": []}
    return {
        "channel_file": name,
        "kenh_tong": [totals(doc)],
        "kenh_ngay": daily(doc),
        "kenh_phan_bo": breakdowns(doc),
        "nghien_cuu": research(doc),
        "gio_online": heatmap(doc),
    }


def markdown(view: dict) -> str:
    """Ban tom tat cho tac vu cai tien / bao cao toi / chon chu de."""
    if not view.get("kenh_tong"):
        return "# Toàn kênh\n\nChưa có dữ liệu trang phân tích kênh (studio/channel/).\n"
    t = view["kenh_tong"][0]

    def f(v, nd=0):
        if v is None:
            return "—"
        return f"{v:,.{nd}f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    out = [f"# Toàn kênh — đọc ngày {t['ngay_doc']} (28 ngày qua)", "",
           f"- Lượt xem: {f(t['luot_xem_28n'])}; giờ xem: {f(t['gio_xem_28n'])}; người đăng ký mới: "
           f"{f(t['dang_ky_moi_28n'])}; tổng người đăng ký: {f(t['tong_dang_ky'])}",
           f"- Lượt hiển thị: {f(t['hien_thi_28n'])}; tỉ lệ bấm: {f(t['ctr_28n_pct'], 1)}%; "
           f"xem trung bình: {f(t['xem_tb_28n_giay'])} giây",
           f"- Doanh thu ước tính: {f(t['doanh_thu_28n'], 2)} US$; RPM: {f(t['rpm'], 2)} US$; "
           f"view 48 giờ: {f(t['view_48h'])}", ""]
    group = None
    for r in view["kenh_phan_bo"]:
        if r["nhom"] != group:
            group = r["nhom"]
            out += ["", f"## {group}" + (f" ({r['ky']})" if r["ky"] else "")]
        out.append(f"- {r['muc']}: {r['chu']}")
    feeds: dict = {}
    for r in view["nghien_cuu"]:
        feeds.setdefault(r["luong"], []).append(r)
    for feed, rows in feeds.items():
        out += ["", f"## Nghiên cứu: {feed} (top 15 theo bội số so với kênh của họ)"]
        for r in rows[:15]:
            out.append(f"- {r['boi_so'] or '—'}× · {r['tieu_de']} — {r['kenh']} · {f(r['view'])} view · {r['tuoi']}")
    return "\n".join(out) + "\n"
