"""Cham so quyet dinh voi so that cua YouTube Studio o moc 24 gio va 7 ngay.

    from loop.heart_score import score, write_results, RESULT_NAME, RESULT_COLUMNS

score(merged, ledger_rows, snaps_by_vid) tra mot dong cho moi slug trong so. Phim bi bo (khong co video)
chi co canh_bao 'chua-co-video'. Chi stdlib.
"""
from __future__ import annotations

import csv
from pathlib import Path

from loop.ledger import norm_title, pick_snapshot

RESULT_NAME = "ket-qua.csv"
RESULT_COLUMNS = ("slug", "video_id", "ctr_24h", "impr_24h", "views_7d", "apv_pct", "ret_30s",
                  "verdict", "ket_qua", "canh_bao")

# bang cua so trong heart_config.json (cua_so_ctr_gio, cua_so_view_gio); test giu cho khop
CTR_WINDOW = (18, 36)
VIEW_WINDOW = (144, 216)
CTR_TARGET_H = 24
VIEW_TARGET_H = 168
BRIGHT_CUT = 0.5
# ledger `packaging` (tu tieu de live, loop.ledger.packaging) -> nhanh cua bien `dong`. 'other' khong phai nhanh nao.
PACKAGING_DONG = {"gia": "gia-chuyen-hang", "lineup": "lineup", "xep-hang": "xep-hang"}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _find_video(slug: str, tieu_de: str, ledger_rows: list[dict]):
    for r in ledger_rows:
        if r.get("slug") == slug and r.get("video_id"):
            return r
    nt = norm_title(tieu_de)
    if nt:
        for r in ledger_rows:
            if r.get("slug") and r["slug"] != slug:      # video cua slug khac: khong dem hai lan
                continue
            if r.get("video_id") and norm_title(r.get("live_title", "")) == nt:
                return r
    return None


def _band(views, du_doan: str) -> str:
    if views is None or "-" not in (du_doan or ""):
        return ""
    lo, hi = (_f(x) for x in du_doan.split("-", 1))
    if lo is None or hi is None:
        return ""
    return "tren" if views > hi else "duoi" if views < lo else "trong"


def score(merged: dict, ledger_rows: list[dict], snaps_by_vid: dict) -> list[dict]:
    out = []
    for slug, m in merged.items():
        row = {c: "" for c in RESULT_COLUMNS}
        row["slug"] = slug
        led = _find_video(slug, m.get("tieu_de", ""), ledger_rows)
        if led is None:
            row["canh_bao"] = "chua-co-video"
            out.append(row)
            continue
        vid = led["video_id"]
        warns = []
        snaps = snaps_by_vid.get(vid, [])
        oldest = max((s["age_hours"] for s in snaps if s.get("age_hours") is not None), default=None)
        s24 = pick_snapshot(snaps, CTR_TARGET_H, *CTR_WINDOW)
        s7 = pick_snapshot(snaps, VIEW_TARGET_H, *VIEW_WINDOW)
        if s24:
            row["ctr_24h"], row["impr_24h"] = s24.get("ctr", ""), s24.get("impressions", "")
        elif oldest is not None and oldest > CTR_WINDOW[1]:
            warns.append("lo-moc-24h")
        if s7:
            row["views_7d"] = s7.get("views", "")
        elif oldest is not None and oldest > VIEW_WINDOW[1]:
            warns.append("lo-moc-7d")
        # hook cham o moc 7 ngay: apv_pct cua anh chup dung cho cua so 7 ngay, khong phai anh moi nhat cua ledger
        apv = s7.get("apv_pct", "") if s7 else led.get("apv_pct", "")
        row.update(video_id=vid, apv_pct="" if apv is None else apv, ret_30s=led.get("ret_30s", ""),
                   verdict=led.get("verdict", ""))
        row["ket_qua"] = _band(_f(row["views_7d"]), m.get("du_doan_7d", ""))
        if m.get("tieu_de") and norm_title(led.get("live_title", "")) != norm_title(m["tieu_de"]):
            warns.append("lech-tieu-de")
            # Chu kenh 2026-11-28: tieu de da doi thi kieu video la kieu cua tieu de dang hien, khong phai nhanh
            # da len ke hoach (colt_python_cz: so ghi gia-chuyen-hang, tieu de that la lineup). Khong ro kieu -> "".
            if m.get("dong"):
                row["dong_that"] = PACKAGING_DONG.get(led.get("packaging", ""), "")
        b = _f(led.get("thumb_brightness"))
        sang = m.get("thumb_sang", "")
        if b is not None and sang in ("sang", "toi") and (b >= BRIGHT_CUT) != (sang == "sang"):
            warns.append("lech-thumb")
        row["canh_bao"] = ";".join(warns)
        out.append(row)
    return out


def write_results(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in RESULT_COLUMNS})
