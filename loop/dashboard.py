"""Build the channel dashboard's data file (Vietnamese labels) from the loop's outputs.

Reads only local files: the ledger and latest studio report in report_dir, the loop cache
(snapshots, health.json), alerts.md, and the improvement agent's reports in ../cai-tien.
Writes loop/cache/dashboard.json: one JSON object whose keys are the dashboard's datasets
(videos, daily, curves, health, patterns, changes). Uploading it is the scheduled task's job.

Usage: python -m loop.dashboard [--config loop/config.json] [--out loop/cache/dashboard.json]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import re
from datetime import datetime, timezone, timedelta

try:
    from loop import channel_view
except ImportError:          # chay truc tiep: python loop/dashboard.py
    import channel_view

HERE = os.path.dirname(os.path.abspath(__file__))

VERDICT_VI = {
    "KEEP": "Tốt",
    "WATCH": "Tạm ổn",
    "CHANGE_TITLE": "Yếu, nên đổi",
    "NOT_PACKAGING": "Ít được giới thiệu",
    "TOO_EARLY": "Mới đăng",
    "SWAP": "Đang thử thumbnail khác",
    "REVERT": "Đã trả lại thumbnail cũ",
    "PAUSED": "Tạm dừng (vừa đổi tay)",
}
PACKAGING_VI = {"gia": "Một khẩu, có giá", "lineup": "Súng mới của một hãng", "xep-hang": "Xếp hạng",
                "other": "Khác"}


def _vn_num(s: str) -> str:
    """'5,263' -> '5.263'; '3.5' -> '3,5'."""
    s = s.replace(",", "")
    if "." in s:
        a, b = s.split(".", 1)
        return f"{int(a):,}".replace(",", ".") + "," + b
    return f"{int(s):,}".replace(",", ".")


def reason_plain(code: str, raw: str) -> str:
    """Viet lai ly do cua analyse.py thanh mot cau de hieu."""
    raw = raw or ""
    def grab(pat):
        m = re.search(pat, raw)
        return _vn_num(m.group(1)) if m else None
    ctr, bar = grab(r"CTR ([\d.]+)%"), grab(r"bar ([\d.]+)%")
    impr = grab(r"at ([\d,]+) impr") or grab(r", ([\d,]+) impr \(")
    typical, age = grab(r"typical ([\d,]+) impr"), grab(r"age ([\d.]+) h")
    if code == "KEEP" and ctr and bar:
        return f"Cứ 100 người thấy thumbnail thì {ctr} người bấm xem, đạt mức cần có {bar}%."
    if code == "WATCH" and ctr and bar:
        return f"Cứ 100 người thấy thì {ctr} người bấm, sát mức cần có {bar}%. Chờ thêm số rồi mới kết luận."
    if code == "CHANGE_TITLE" and ctr and bar:
        end = ("Video đã quá 7 ngày: để nguyên, không sửa." if ", over 7 days: left as is" in raw
               else "Nên thử tiêu đề hoặc thumbnail khác.")
        return (f"Cứ 100 người thấy chỉ {ctr} người bấm, dưới mức cần có {bar}% (đã đủ {impr} lượt hiển thị để "
                f"chắc chắn). {end}")
    if code == "NOT_PACKAGING" and ctr and typical:
        return (f"Người thấy vẫn bấm nhiều ({ctr}%), nhưng YouTube mới hiện video {impr} lần, chưa tới một nửa "
                f"mức thường của kênh ({typical}). Vấn đề nằm ở chủ đề hoặc độ giữ chân, không phải tiêu đề.")
    if code == "TOO_EARLY" and age:
        return f"Mới đăng {age} giờ, {impr} lượt hiển thị. Cần ít nhất 6 giờ và 2.000 lượt hiển thị mới đánh giá được."
    return reason_vi(raw)


def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


def _pct(x, nd=1):
    v = _f(x)
    return None if v is None else round(v * 100, nd)


def reason_vi(s: str) -> str:
    s = s or ""
    for a, b in [
        ("needs 6 h or 1,500 impr", "cần 6 giờ hoặc 1.500 lượt hiển thị"),
        ("needs 6 h and 2,000 impr", "cần 6 giờ và 2.000 lượt hiển thị"),   # reasons written before 2026-11-28
        ("no swap left", "hết lượt đổi thumbnail"),
        (" impr", " lượt hiển thị"),
        ("typical", "mức thường"),
        ("bar", "vạch"),
        (" at ", " với "),
        ("age ", "tuổi "),
        (" h,", " giờ,"),
    ]:
        s = s.replace(a, b)
    # 5,263 -> 5.263 ; 3.5% -> 3,5%
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", ".", s)
    s = re.sub(r"(\d)\.(\d)(%| giờ)", r"\1,\2\3", s)
    return s


def latest(pattern):
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def read_report_reasons(report_dir):
    path = latest(os.path.join(report_dir, "[0-9]" * 4 + "-*.md"))
    out = {}
    if not path:
        return out, None
    for line in open(path, encoding="utf-8"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"[A-Za-z0-9_-]{11}", cells[0]):
            out[cells[0]] = reason_plain(cells[2], cells[3])
    return out, os.path.basename(path)


def build_videos(report_dir):
    reasons, report_name = read_report_reasons(report_dir)
    rows = []
    with open(os.path.join(report_dir, "ledger.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            avd, rt = _f(r.get("avd_s")), _f(r.get("runtime_s"))
            v = r.get("verdict") or ""
            rows.append({
                "video_id": r["video_id"],
                "tieu_de": r.get("live_title") or "",
                "link": f"https://www.youtube.com/watch?v={r['video_id']}",
                "ngay_dang": (r.get("published_local") or "")[:16],
                "kieu": PACKAGING_VI.get(r.get("packaging") or "", r.get("packaging") or ""),
                "tuoi_ngay": None if _f(r.get("age_h_latest")) is None else round(_f(r["age_h_latest"]) / 24, 1),
                "ctr_pct": _pct(r.get("ctr_latest")),
                "luot_hien_thi": None if _f(r.get("impr_latest")) is None else int(_f(r["impr_latest"])),
                "view": None if _f(r.get("views_latest")) is None else int(_f(r["views_latest"])),
                "ctr_24h_pct": _pct(r.get("ctr_24h")),
                "ctr_7d_pct": _pct(r.get("ctr_7d")),
                "xem_tb_giay": None if avd is None else int(avd),
                "pct_xem": None if (avd is None or not rt) else round(avd / rt * 100, 1),
                "giu_30s_pct": _pct(r.get("ret_30s")),
                "footage_pct": _f(r.get("footage_pct")),
                "do_sang_thumb": _f(r.get("thumb_brightness")),
                "phan_quyet_ma": v,
                "phan_quyet": VERDICT_VI.get(v, v),
                "ly_do": reasons.get(r["video_id"], ""),
            })
    rows.sort(key=lambda x: x["ngay_dang"], reverse=True)
    return rows, report_name


def read_snapshots(cache_dir):
    snaps = []
    for path in sorted(glob.glob(os.path.join(cache_dir, "snapshots", "*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("period") and r["period"] != "since_published":
                    continue
                snaps.append(r)
    return snaps


def build_daily(snaps, tz_offset_h=7):
    """Per local day: the newest capture of EVERY video known by the end of that day, summed.

    A day's run may read only a few videos (the fresh ones), so a video not read that day keeps
    its last known numbers; summing only that day's reads would make the total collapse."""
    tz = timezone(timedelta(hours=tz_offset_h))
    by_day = {}
    for r in snaps:
        t = datetime.strptime(r["captured_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        day = t.astimezone(tz).strftime("%Y-%m-%d")
        by_day.setdefault(day, []).append(r)
    rows, known = [], {}
    for day in sorted(by_day):
        for r in sorted(by_day[day], key=lambda x: x["captured_at_utc"]):
            known[r["video_id"]] = r
        vids = known.values()
        views = sum(int(_f(v["views"]) or 0) for v in vids)
        impr = sum(int(_f(v["impressions"]) or 0) for v in vids)
        clicks = sum((_f(v["impressions"]) or 0) * (_f(v["ctr"]) or 0) for v in vids)
        rows.append({
            "ngay": day,
            "tong_view": views,
            "tong_hien_thi": impr,
            "ctr_kenh_pct": round(clicks / impr * 100, 2) if impr else None,
            "so_video": len(known),
            "so_video_doc_moi": len({r["video_id"] for r in by_day[day]}),
        })
    return rows


def build_curves(snaps, titles, max_age_h=14 * 24):
    rows = []
    for r in snaps:
        age = _f(r.get("age_hours"))
        if age is None or age > max_age_h:
            continue
        rows.append({
            "video_id": r["video_id"],
            "tieu_de": titles.get(r["video_id"], r["video_id"]),
            "tuoi_gio": round(age, 1),
            "luot_hien_thi": int(_f(r["impressions"]) or 0),
            "ctr_pct": _pct(r.get("ctr")),
            "view": int(_f(r["views"]) or 0),
        })
    rows.sort(key=lambda x: (x["video_id"], x["tuoi_gio"]))
    return rows


def build_health(cache_dir, report_dir):
    rows = []
    hp = os.path.join(cache_dir, "health.json")
    if os.path.exists(hp):
        h = json.load(open(hp, encoding="utf-8"))
        ok = h.get("status") == "PASS"
        if h.get("videos_read"):
            detail = f"Đọc được {h['videos_read']} video, lỗi {h.get('videos_failed', 0)}."
        elif (h.get("detail") or "") == "nothing due":
            detail = "Lần gần nhất không có video nào cần đọc."
        else:
            detail = h.get("detail") or ""
        m = re.search(r"channel ok \((\d+) cards, (\d+) research videos\)", h.get("detail") or "")
        if m:
            detail = f"{detail} Trang kênh: {m.group(1)} thẻ số, {m.group(2)} video ở tab Nghiên cứu.".strip()
        elif "channel" in (h.get("detail") or ""):
            detail = f"{detail} Trang kênh có lỗi: {h['detail'][:200]}".strip()
        if not ok:
            detail = f"Có lỗi, cần mở VPS kiểm tra. Chi tiết: {detail}"
        try:
            at = datetime.strptime(h.get("at_utc") or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            at_s = at.astimezone(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            at_s = h.get("at_utc") or ""
        rows.append({
            "thoi_diem": at_s,
            "loai": "Máy chủ VPS đọc YouTube Studio",
            "trang_thai": "Ổn" if ok else "Lỗi",
            "noi_dung": detail,
        })
    ap = os.path.join(report_dir, "alerts.md")
    if os.path.exists(ap):
        lines = [l.strip()[2:] for l in open(ap, encoding="utf-8") if l.startswith("- ")]
        for l in lines[-15:][::-1]:
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\S*\s+(.*)", l)
            rows.append({
                "thoi_diem": m.group(1) if m else "",
                "loai": "Máy này phân tích số liệu",
                "trang_thai": "Lỗi",
                "noi_dung": (m.group(2) if m else l)[:240],
            })
    return rows


_PATTERN_WORDS = [
    (r"\s*\(?`?thumb_brightness`?\)?", ""),
    (r"\(avd_s ÷ runtime_s\)", "(thời gian xem trung bình chia độ dài phim)"),
    (r"`?packaging`? xếp hạng vs giá", "Dạng xếp hạng so với dạng một khẩu có giá"),
    (r"\bxep-hang\b", "xếp hạng"),
    (r"\bvs gia\b", "vs một khẩu có giá"),
    (r"CTR → PHÂN PHỐI \(lượt hiển thị\)", "Tỉ lệ bấm cao thì YouTube có giới thiệu video nhiều hơn không"),
    (r"NỘI DUNG: ", "Nội dung: "),
    (r"\bCTR\b", "tỉ lệ bấm"),
    (r"chỉ video > (\d+) giờ", r"chỉ tính video đăng hơn \1 giờ"),
    (r"\bnhóm A\b", "nhóm đầu"),
    (r"\bnhóm B\b", "nhóm sau"),
    (r" vs ", " so với "),
    (r"\bvạch\b", "mức"),
    (r"[*`]", ""),
]


def plain_pattern(s: str) -> str:
    for pat, rep in _PATTERN_WORDS:
        s = re.sub(pat, rep, s)
    s = s.strip()
    return s[:1].upper() + s[1:]


def build_patterns(improve_dir):
    path = latest(os.path.join(improve_dir, "[0-9]" * 4 + "-*.md"))
    rows = []
    if not path:
        return rows, None
    in_table = False
    for line in open(path, encoding="utf-8"):
        if line.startswith("## ") and "Quy luật" in line:
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if in_table and line.startswith("| Q"):
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) >= 7:
                tin = re.sub(r"\*", "", c[6])
                word = tin.split(" ")[0].split("(")[0].strip()
                if "thấp" in word and "vừa" in word:
                    ma, nhan = "thap", "Chưa chắc lắm"
                elif "cao" in word:
                    ma, nhan = "cao", "Chắc chắn"
                elif "vừa" in word:
                    ma, nhan = "vua", "Khá chắc"
                else:
                    ma, nhan = "thap", "Chưa chắc"
                rows.append({
                    "ma": c[0], "noi_dung": plain_pattern(c[1]), "so_sanh": plain_pattern(c[2]),
                    "so_video": c[3].replace(" vs ", " và "), "ket_qua": plain_pattern(c[4]),
                    "do_tin": nhan, "do_tin_ma": ma, "ghi_chu": tin,
                })
    return rows, os.path.basename(path)


def build_changes(improve_dir):
    """Entries of thay-doi.md: each '## TD-...' or '### TD-...' heading or a table row with a TD id."""
    path = os.path.join(improve_dir, "thay-doi.md")
    rows = []
    if not os.path.exists(path):
        return rows
    text = open(path, encoding="utf-8").read()
    for block in re.split(r"\n(?=#{2,3} )", text):
        m = re.match(r"#{2,3} .*?(TD-\d{4}-\d{2}-\d{2}-\d+)(.*)", block)
        if not m:
            continue
        def field(name):
            # "- **File:** `x`" va "- **File 1:** `x`" (nhieu file); dau ** dong sau dau hai cham, gia tri co the
            # xuong dong (dong thut vao, khong phai gach dau dong con). Bo dau markdown: bang chi hien chu.
            mm = re.search(rf"(?:^[-*]\s*|·\s*)\**{name}(?:\s+\d+)?\**\s*[:：]\s*\**\s*(.+(?:\n {{2,}}(?![-*] ).+)*)",
                           block, re.M | re.I)
            if not mm:
                return ""
            val = re.sub(r"\s+", " ", mm.group(1)).split(" · ")[0]
            return re.sub(r"[*`]", "", val).strip()[:200]
        rows.append({
            "ma": m.group(1),
            "ngay": m.group(1)[3:13],
            "file": field("file") or field("tệp"),
            "noi_dung": (re.sub(r"[*`]", "", m.group(2)).strip(" ·—-:") or field("tóm tắt"))[:200],
            "trang_thai": field("trạng thái") or "đang chạy",
        })
    return rows


VAR_VI = {
    "dong": "Kiểu video", "khuon_tieu_de": "Khuôn tiêu đề", "ten_quen": "Tên quen trong tiêu đề",
    "thumb_sang": "Thumbnail sáng hay tối", "thumb_chu": "Số chữ trên thumbnail",
    "hook": "Kiểu mở đầu", "gio_dang": "Giờ đăng",
}
STATE_VI = {"thang": "Đang thắng", "dang-thu": "Đang thử", "loai": "Đã loại", "chua-du": "Chưa đủ số"}
KIND_VI = {"khai-thac": "Theo cái đang thắng", "thu": "Thử cái mới"}
RESULT_VI = {"trong": "Đúng dự đoán", "tren": "Hơn dự đoán", "duoi": "Thấp hơn dự đoán", "": "Chưa có số"}
TN_ROWS = 14
_EMPTY_TN = {"tn_nhanh": [], "tn_so": [], "tn_ke_tiep": []}


def _tn_value(do: str, med) -> str:
    if med is None:
        return ""
    if do == "apv_pct":
        return f"{med * 100:.1f}".replace(".", ",") + "% thời lượng xem"
    if do == "ctr_24h":
        return f"{med * 100:.1f}".replace(".", ",") + "% tỉ lệ bấm 24 giờ"
    return f"{round(math.exp(med)):,} lượt xem".replace(",", ".")


def _tn_read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _tn_arms(directive: dict, cfg: dict) -> list:
    """Mot dong cho moi (bien, nhanh) cua cau hinh. chi-thi.json chi giu nhanh thang cua moi bien,
    nen n / trung vi chi co o nhanh thang; cac nhanh khac de trong."""
    wins = directive.get("nhanh_thang") or {}
    using = {}
    for m in directive.get("muc") or []:
        for v, a in (m.get("gia_tri") or {}).items():
            using.setdefault(v, set()).add(a)
    rows = []
    # Chi bien trai tim THAT SU thu (bat va `thu` khong tat): bien giu mac dinh co dinh khong phai "dang thu",
    # va nhanh da go khoi cau hinh (chi-thi.json cu) khong hien nua.
    try:
        from loop import heart_config as hc
    except ImportError:
        import heart_config as hc
    tested = set(hc.testable_vars(cfg)) if cfg.get("bien") else None
    full = directive.get("thong_ke_nhanh")
    if isinstance(full, dict):
        for var, by_arm in full.items():
            spec = cfg.get("bien", {}).get(var)
            if tested is not None and var not in tested:
                continue
            by_arm = {a: s for a, s in by_arm.items() if not spec or a in spec.get("nhanh", ())}
            for arm, s in by_arm.items():
                do = s.get("do") or (spec or {}).get("do", "views_7d")
                rows.append({
                    "bien": VAR_VI.get(var, var), "nhanh": arm, "n": s.get("n") or 0,
                    "trung_vi": _tn_value(do, s.get("trung_vi")),
                    "trang_thai": STATE_VI.get(s.get("trang_thai"), "Chưa đủ số"),
                    "dang_dung": arm in using.get(var, ()),
                })
        return rows
    for var, w in wins.items():
        spec = cfg.get("bien", {}).get(var)
        if not spec or (tested is not None and var not in tested):
            continue
        for arm in spec["nhanh"]:
            is_win = arm == w.get("nhanh")
            n = w.get("n") if is_win else ""
            state = ("thang" if w.get("du_so") else ("dang-thu" if n else "chua-du")) if is_win else "chua-du"
            rows.append({
                "bien": VAR_VI.get(var, var), "nhanh": arm, "n": n,
                "trung_vi": _tn_value(spec["do"], w.get("trung_vi")) if is_win else "",
                "trang_thai": STATE_VI[state], "dang_dung": arm in using.get(var, ()),
            })
    return rows


def _tn_next(directive: dict) -> list:
    out = []
    for m in directive.get("muc") or []:
        out.append({
            "slug": m.get("slug", ""),
            "bien_thu": VAR_VI.get(m.get("bien_thu"), m.get("bien_thu") or ""),
            "nhanh_thu": m.get("nhanh_thu", ""),
            "kieu": KIND_VI.get(m.get("kieu"), m.get("kieu") or ""),
            "du_doan_7d": m.get("du_doan_7d", ""),
            "con_han_toi": m.get("con_han_toi", ""),
            "gia_tri": {VAR_VI.get(v, v): a for v, a in (m.get("gia_tri") or {}).items()},
        })
    return out


def _tn_latest(tim) -> list:
    from pathlib import Path
    try:
        from loop import book as bk
    except ImportError:
        import book as bk
    rows = bk.read(Path(tim) / bk.BOOK_NAME)
    merged = bk.merged(rows)
    when = {}
    for r in rows:
        if r["tac_vu"] == "dung" and r["slug"]:
            when[r["slug"]] = max(r["thoi_diem"], when.get(r["slug"], ""))
    results = {}
    try:
        with open(Path(tim) / "ket-qua.csv", encoding="utf-8", newline="") as fh:
            results = {r["slug"]: r for r in csv.DictReader(fh)}
    except OSError:
        pass
    out = []
    for slug in sorted(when, key=lambda s: when[s], reverse=True)[:TN_ROWS]:
        m, res = merged.get(slug, {}), results.get(slug, {})
        try:
            views = int(float(res.get("views_7d") or ""))
        except ValueError:
            views = None
        warn = (res.get("canh_bao") or "")
        out.append({
            "slug": slug, "ngay": when[slug][:10],
            "bien_thu": VAR_VI.get(m.get("bien_thu"), m.get("bien_thu", "")),
            "nhanh_thu": m.get("nhanh_thu", ""),
            "kieu": KIND_VI["thu" if m.get("nhanh_thu") else "khai-thac"],
            "du_doan_7d": m.get("du_doan_7d", ""), "views_7d": views,
            "ket_qua": RESULT_VI.get(res.get("ket_qua", ""), "Chưa có số"),
            "canh_bao": [w for w in warn.split(";") if w],
        })
    return out


def build_experiment(tim) -> dict:
    """Ba danh sach 'Thu nghiem' cua bang dieu khien. Thieu file hay hong thi danh sach rong, khong bao gio nem loi."""
    from pathlib import Path
    out = {k: [] for k in _EMPTY_TN}
    try:
        directive = _tn_read_json(Path(tim) / "chi-thi.json")
        if directive:
            try:
                from loop import heart_config as hc
            except ImportError:
                import heart_config as hc
            cfg = hc.load_config(Path(HERE) / "heart_config.json")
            out["tn_nhanh"] = _tn_arms(directive, cfg)
            out["tn_ke_tiep"] = _tn_next(directive)
    except Exception as e:  # noqa: BLE001 - bang dieu khien khong duoc lam hong vong lap
        print(f"experiment directive skipped: {type(e).__name__}: {e}")
    try:
        out["tn_so"] = _tn_latest(tim)
    except Exception as e:  # noqa: BLE001
        print(f"experiment book skipped: {type(e).__name__}: {e}")
    return out


def build_goal(tim) -> dict:
    """Khoi `muc_tieu` cua bang dieu khien, doc tu chi-thi.json. Thieu file / khong co khoi goal -> {}."""
    from pathlib import Path
    try:
        d = _tn_read_json(Path(tim) / "chi-thi.json")
        g = d.get("muc_tieu")
        if not isinstance(g, dict) or not g:
            return {}
        return {"thang": g.get("thang"), "trang_thai": g.get("trang_thai"), "che_do": d.get("che_do"),
                "chi_so": g.get("chi_so") or [], "nhan_ban": d.get("nhan_ban") or [],
                "view_can_moi_ngay": g.get("view_can_moi_ngay"),
                "sap_toi": bool((g.get("muc_tieu") or {}).get("sap_toi")),
                "ngay_co_so": g.get("ngay_co_so"), "ngay_trong_thang": g.get("ngay_trong_thang"),
                "thang_muc_tieu": (g.get("muc_tieu") or {}).get("thang_moc") or g.get("thang")}
    except Exception as e:  # noqa: BLE001 - bang dieu khien khong duoc lam hong vong lap
        print(f"goal directive skipped: {type(e).__name__}: {e}")
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "cache", "dashboard.json"))
    a = ap.parse_args(argv)
    cfg = json.load(open(a.config, encoding="utf-8"))
    report_dir = cfg["report_dir"]
    improve_dir = os.path.join(os.path.dirname(report_dir), "cai-tien")
    cache_dir = os.path.join(HERE, "cache")

    videos, report_name = build_videos(report_dir)
    titles = {v["video_id"]: v["tieu_de"] for v in videos}
    snaps = read_snapshots(cache_dir)
    patterns, improve_name = build_patterns(improve_dir)
    data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "studio_report": report_name,
        "improve_report": improve_name,
        "videos": videos,
        "daily": build_daily(snaps),
        "curves": build_curves(snaps, titles),
        "health": build_health(cache_dir, report_dir),
        "patterns": patterns,
        "changes": build_changes(improve_dir),
    }
    view = channel_view.build(cache_dir)
    data.update(view)
    from pathlib import Path
    data.update(build_experiment(Path(report_dir).parents[1] / "tim"))
    data["muc_tieu"] = build_goal(Path(report_dir).parents[1] / "tim")
    try:
        with open(os.path.join(report_dir, "kenh.md"), "w", encoding="utf-8") as fh:
            fh.write(channel_view.markdown(view))
    except OSError as e:
        print(f"kenh.md not written: {e}")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, a.out)
    print(f"dashboard.json ok: {len(videos)} videos, {len(data['daily'])} days, "
          f"{len(data['curves'])} curve points, {len(data['health'])} health rows, "
          f"{len(patterns)} patterns, {len(data['changes'])} changes -> {a.out}")


if __name__ == "__main__":
    main()
