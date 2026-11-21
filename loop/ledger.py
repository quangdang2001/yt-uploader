"""So cai theo tung video: cai da lam (tieu de, thumbnail, kich ban, footage, nhip cat) noi voi cai da dat
(CTR, impressions, retention tu Studio). Chay tren may chu kenh sau loop.fetch va loop.analyse.

    python -m loop.ledger [--config loop/config.json] [--no-video-scan]

Doc: cache (loop.fetch), build repo (CHI DOC, khoa build_repo trong config), yt-dlp (toi da mot lan moi lan
chay, cache 24 gio). Ghi: <report_dir>/ledger.csv va ledger.md (nguyen tu). Thoat 2 khi thieu build repo
hoac cache.

Noi video_id -> slug, lay nguon dau tien khop (cot match_source):
  history   studio/upload-history.csv (cot ten_goc la slug)
  videos-json  playbook/channel_daily_report_note/videos.json (config videos_json); giu slug du thu muc da xoa
  id-in-md  ma 11 ky tu nam trong projects/<slug>/*.md hoac ghi_chu cua slug tren bang cong viec
  title     tieu de live == tieu de trong UPLOAD.md hoac mot dong in dam trong tieu-de.md
  none      khong khop: van co dong, phan "da lam" de trong. Khong bao gio doan long.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from loop import analyse as an
from loop import book
from loop import fetch as lf
from loop.rules import Verdict, decide, swap_bar
from loop.state import aware, build_states, since_published

DEFAULT_BUILD_REPO = "D:/Youtube/video_footage_re_create"
CHANNEL_ID = "UCDBRc2lIFJ0ylsGbTJd5C5g"
CHANNEL_URL = "https://www.youtube.com/channel/%s/videos" % CHANNEL_ID
LIVE_NAME = "live-titles.json"
VIDEO_CACHE_NAME = "ledger-video-cache.json"
LIVE_TTL_H = 24
SCENE_THRESHOLD = 0.25
BOARD_REL = "Bare Skull - 2nd brain/van-hanh/ke-hoach/bang-cong-viec.md"
SHAPES_REL = "topic_engine/ctr/own_shapes.json"
THUMB_GATE_REL = "playbook/thumb_gate.py"
READ_FIRST = "0 - ĐỌC TRƯỚC.md"
EVIDENCE_FILES = ("UPLOAD.md", READ_FIRST, "hoi-cuu.md", "ghi-chu-duyet.md")   # file ghi nhan dang bai
VIDEOS_JSON_REL = "playbook/channel_daily_report_note/videos.json"      # config videos_json
HISTORY_MD_REL = "Bare Skull - 2nd brain/wiki/analysis/lich-su-video-da-dang.md"   # config history_md

IDS = ["video_id", "live_title", "slug", "match_source", "published_local", "weekday", "hour"]
MADE = ["lane", "packaging", "title_shape", "gun_1", "title_chars", "title_has_number", "title_has_price",
        "title_has_year", "thumb_brightness", "thumb_saturation", "thumb_colourfulness", "thumb_warmth",
        "script_words", "runtime_s", "footage_pct", "render_pct", "stills_pct", "sec_per_cut"]
DID = ["ctr_24h", "impr_24h", "views_24h", "ctr_7d", "impr_7d", "views_7d", "ctr_latest", "impr_latest",
       "views_latest", "age_h_latest", "avd_s", "apv_pct", "ret_30s", "ret_60s", "src_browse",
       "src_suggested", "src_search", "src_other", "verdict"]
COLUMNS = IDS + MADE + DID
MD_COLUMNS = ["published_local", "video_id", "live_title", "slug", "match_source", "lane", "packaging",
              "runtime_s", "footage_pct", "sec_per_cut", "ctr_24h", "impr_24h", "ctr_7d", "impr_latest",
              "ret_30s", "verdict"]

_HINH = re.compile(r"H\u00ecnh:(.*)")
_NUM = r"(\d+(?:[.,]\d+)?)\s*%"


# ---------- phan tich cu phap (thuan) ----------

def _pct(text: str, word: str):
    m = re.search(word + r"[^%\u00b7]*?" + _NUM, text, re.I)
    return float(m.group(1).replace(",", ".")) if m else None


def parse_hinh(text: str) -> dict:
    """Dong 'Hình: footage X% · render ... Y% · ảnh ... Z%' trong 0 - ĐỌC TRƯỚC.md; dau thap phan la dau phay."""
    m = _HINH.search(text or "")
    line = m.group(1) if m else ""
    return {"footage_pct": _pct(line, "footage"), "render_pct": _pct(line, "render"),
            "stills_pct": _pct(line, "\u1ea3nh")}


def parse_board(text: str) -> dict:
    """slug -> {cach, buoc, ghi_chu} tu bang markdown cua bang-cong-viec.md."""
    out = {}
    for line in (text or "").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 9 or cells[0] in ("ma", "") or set(cells[0]) <= set("-: "):
            continue
        out[cells[1]] = {"cach": cells[2], "buoc": cells[3], "ghi_chu": " | ".join(cells[8:])}
    return out


def parse_videos_json(text: str) -> dict:
    """video_id -> slug tu videos.json ({"project": "projects/<slug>"})."""
    try:
        obj = json.loads(text or "{}")
    except ValueError:
        return {}
    out = {}
    for vid, v in (obj.items() if isinstance(obj, dict) else []):
        proj = str(v.get("project") or "") if isinstance(v, dict) else ""
        slug = proj.replace("\\", "/").rstrip("/").split("/")[-1]
        if slug:
            out[vid] = slug
    return out


def parse_history_md(text: str) -> dict:
    """video_id -> {shape, gun_1} tu bang lich-su-video-da-dang.md (cot 2 la `id`, cot 4 khuon, cot 5 sung #1).
    Khong doc cot view / CTR: so lieu ket qua lay tu Studio."""
    out = {}
    for line in (text or "").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = re.fullmatch(r"`([A-Za-z0-9_-]{11})`", cells[1]) if len(cells) >= 5 else None
        if not m:
            continue
        shape = cells[3] if re.fullmatch(r"[a-z][a-z0-9-]*", cells[3]) else ""
        gun = cells[4] if cells[4] not in ("", "-", "—") else ""
        out[m.group(1)] = {"shape": shape, "gun_1": gun}
    return out


def parse_upload_title(text: str):
    """Khoi code ngay duoi tieu de co chu Title trong UPLOAD.md; None neu khong co."""
    m = re.search(r"^#+[^\n]*Title[^\n]*\n+```[^\n]*\n(.*?)\n```", text or "", re.S | re.M)
    return m.group(1).strip() if m and m.group(1).strip() else None


def parse_bold_titles(text: str) -> list:
    """Cac doan **...** tren tung dong cua tieu-de.md."""
    return [m.group(1).strip() for m in re.finditer(r"\*\*([^*\n]+?)\*\*", text or "") if m.group(1).strip()]


def count_words(text: str) -> int:
    return len((text or "").split())


def norm_title(t: str) -> str:
    return " ".join((t or "").lower().split())


# ---------- tieu de ----------

def packaging(title: str) -> str:
    """Quy tac dong goi (tu tieu de live), theo thu tu:
    'gia'      co dau $
    'lineup'   '(<so> Guns)' o cuoi tieu de, hoac 'going crazy'
    'xep-hang' Ranked, Top <so>, No.1, best/worst/toughest, 'the only <so>'
    'other'    con lai."""
    t = title or ""
    if "$" in t:
        return "gia"
    if (re.search(r"\(\s*\d+\s+\w*\s*(guns?|rifles?|pistols?|revolvers?|shotguns?)\s*\)", t, re.I)
            or re.search(r"going crazy", t, re.I)):
        return "lineup"
    if (re.search(r"\branked\b|\btop\s*\d+|\bno\.?\s*1\b|\b(best|worst|toughest)\b|the only \d+", t, re.I)):
        return "xep-hang"
    return "other"


def title_flags(title: str) -> dict:
    t = title or ""
    return {"title_chars": len(t), "title_has_number": int(bool(re.search(r"\d", t))),
            "title_has_price": int("$" in t),
            "title_has_year": int(bool(re.search(r"\b(?:19|20)\d{2}\b", t)))}


def title_shape(title: str, shapes: dict) -> str:
    """own_shapes.titles khop nguyen van (khong phan biet hoa thuong), neu khong thi khuon trong own_shapes.shapes
    ma moi doan chu co dinh (bo cac [..] va $[..]) deu la chuoi con cua tieu de; khuon dai nhat thang."""
    shapes = shapes or {}
    key = norm_title(title)
    for t, name in (shapes.get("titles") or {}).items():
        if norm_title(t) == key:
            return name
    best, best_len = "", 0
    for name, pat in (shapes.get("shapes") or {}).items():
        frags = [f.strip(" ()-") for f in re.sub(r"\$?\[[^\]]*\]", "\x00", pat).split("\x00")]
        frags = [f.lower() for f in frags if f]
        total = sum(len(f) for f in frags)
        if frags and total >= 8 and all(f in key for f in frags) and total > best_len:
            best, best_len = name, total
    return best


# ---------- snapshot ----------

def pick_snapshot(rows: list, target_h: float, lo: float, hi: float):
    """Snapshot co age_hours gan target nhat trong [lo, hi] (bao gom hai dau); None neu khong co."""
    best = None
    for r in rows:
        a = r.get("age_hours")
        if a is None or not lo <= a <= hi:
            continue
        if best is None or abs(a - target_h) < abs(best["age_hours"] - target_h):
            best = r
    return best


def _blank(v):
    return "" if v is None else v


# ---------- nguon ngoai ----------

def _default_yt_runner(url: str) -> str:
    # --encoding utf-8: on Windows a piped yt-dlp prints in the console code page (cp1252), so curly quotes
    # and dashes in titles would decode as U+FFFD.
    p = subprocess.run(["yt-dlp", "--encoding", "utf-8", "--flat-playlist", "--print", "%(id)s|%(title)s", url],
                       capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300)
    if p.returncode != 0:
        raise OSError("yt-dlp exit %d: %s" % (p.returncode, p.stderr.strip()[-200:]))
    return p.stdout


def live_titles(cache: Path, now: datetime, runner=None) -> dict:
    """video_id -> tieu de live. Goi yt-dlp toi da mot lan moi 24 gio (cache/live-titles.json); loi thi dung
    ban cu, khong co ban cu thi {}."""
    cache, now = Path(cache), aware(now)
    path = cache / LIVE_NAME
    old = an._read_json(path, {})
    old = old if isinstance(old, dict) else {}
    titles = old.get("titles") if isinstance(old.get("titles"), dict) else {}
    try:
        at = datetime.fromisoformat(str(old.get("fetched_utc")).replace("Z", "+00:00"))
    except ValueError:
        at = None
    if at is not None and titles and now - aware(at) < timedelta(hours=LIVE_TTL_H):
        return titles
    try:
        out = (runner or _default_yt_runner)(CHANNEL_URL)
    except (OSError, subprocess.SubprocessError) as exc:
        print("ledger: yt-dlp failed (%s); using %s" % (exc, "the old list" if titles else "no live titles"),
              file=sys.stderr)
        return titles
    fresh = {}
    for line in out.splitlines():
        vid, sep, title = line.partition("|")
        if sep and vid.strip():
            fresh[vid.strip()] = title.strip()
    if not fresh:
        return titles
    lf._write(path, json.dumps({"fetched_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "titles": fresh},
                               ensure_ascii=False, indent=1))
    return fresh


def _default_probe(path: Path):
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(path)], capture_output=True, text=True, timeout=120)
        return float(p.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _default_scan(path: Path, runtime):
    """So lan cat: ffmpeg scene detection tren ban 5 fps nho (~1 phut moi phim)."""
    try:
        p = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-vf",
                            "fps=5,scale=256:-2,select='gt(scene,%s)',showinfo" % SCENE_THRESHOLD, "-an", "-f",
                            "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=1800)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return len(re.findall(r"pts_time:", p.stderr))


def measure_thumb(path: Path, build: Path):
    """(brightness, saturation, colourfulness, warmth) bang playbook/thumb_gate.measure; None neu loi."""
    try:
        spec = importlib.util.spec_from_file_location("thumb_gate_ro", Path(build) / THUMB_GATE_REL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from PIL import Image
        with Image.open(path) as im:
            return tuple(round(float(x), 4) for x in mod.measure(im))
    except Exception as exc:        # lazy: khong co PIL / thumb_gate / anh hong thi de trong
        print("ledger: cannot measure %s (%s)" % (path, exc), file=sys.stderr)
        return None


# ---------- build repo ----------

def _read(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _id_re(vid: str):
    return re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(vid) + r"(?![A-Za-z0-9_-])")


def _resolve_base(base: str, slugs: set):
    b = re.sub(r"\.(mp4|mov|mkv)$", "", (base or "").strip(), flags=re.I)
    for cand in (b, b.lower(), re.sub(r"[\s-]+", "_", b.lower())):
        if cand in slugs:
            return cand
    return None


def _final_mp4(proj: Path):
    f = sorted((proj / "output" / "final").glob("*.mp4"))
    return f[0] if f else None


def _video_cache(cache: Path) -> dict:
    d = an._read_json(Path(cache) / VIDEO_CACHE_NAME, {})
    return d if isinstance(d, dict) else {}


def compute_verdicts(cache: Path, cfg: dict, now: datetime) -> dict:
    """video_id -> verdict, tinh lai bang cac ham co san cua analyse (khong ghi, khong gui tin)."""
    snaps = lf.load_snapshots(cache)
    actions = lf.load_actions(cache)
    features = an.load_features(cache)
    bases = lf.load_history(cache)
    states = build_states(snaps, actions, features, now, bases=bases)
    by_vid = since_published(snaps)
    history = [s for rows in by_vid.values() for s in rows]
    latest = {v: rows[-1] for v, rows in by_vid.items()}
    out = {}
    for vid, snap in latest.items():
        try:
            verdict, _ = decide(snap, states[vid], swap_bar(snap["age_hours"], history), now,
                                an._peer_typical_impr(vid, snap["age_hours"], by_vid),
                                an._peer_median(vid, snap, latest, "ret_30s"))
        except Exception as exc:    # mot video loi khong lam mat verdict cac video khac
            print("ledger: verdict failed for %s (%s)" % (vid, exc), file=sys.stderr)
            continue
        out[vid] = verdict.value
    return out


BOOK_MEASURES = ("footage_pct", "sec_per_cut", "render_pct", "thumb_brightness", "script_words", "runtime_s")


def build_ledger(cache: Path, cfg: dict, now: datetime, live_titles: dict, probe=None, scan=None) -> list:
    """Mot dong moi video cong khai. `scan` None = bo qua quet cat (sec_per_cut de trong)."""
    cache, now = Path(cache), aware(now)
    build = Path(cfg.get("build_repo") or DEFAULT_BUILD_REPO)
    tz = ZoneInfo(cfg.get("tz") or an.DEFAULT_TZ)
    probe = probe or _default_probe

    snaps = lf.load_snapshots(cache)
    by_vid = since_published(snaps)
    history = lf.load_history(cache)
    features = an.load_features(cache)
    board = parse_board(_read(build / BOARD_REL))
    shapes = an._read_json(build / SHAPES_REL, {})
    try:
        verdicts = compute_verdicts(cache, cfg, now)
    except Exception as exc:        # mot loi phan quyet khong lam hong ca so cai
        print("ledger: verdicts unavailable (%s)" % exc, file=sys.stderr)
        verdicts = {}

    vjson = parse_videos_json(_read(Path(cfg.get("videos_json") or build / VIDEOS_JSON_REL)))
    hist_md = parse_history_md(_read(Path(cfg.get("history_md") or build / HISTORY_MD_REL)))
    proj_root = build / "projects"
    slugs = {p.name for p in proj_root.iterdir() if p.is_dir()} if proj_root.is_dir() else set()
    slugs |= set(board)
    texts = {s: "\n".join(_read(f) for f in sorted((proj_root / s).glob("*.md")))
             for s in slugs if (proj_root / s).is_dir()}
    evidence = {s: "\n".join(_read(proj_root / s / n) for n in EVIDENCE_FILES)
                for s in slugs if (proj_root / s).is_dir()}
    note = {s: board[s]["ghi_chu"] for s in board}
    titles_of: dict = {}
    for s in slugs:
        cands = [parse_upload_title(_read(proj_root / s / "UPLOAD.md"))]
        cands += parse_bold_titles(_read(proj_root / s / "tieu-de.md"))
        for t in cands:
            if t:
                titles_of.setdefault(norm_title(t), set()).add(s)

    measured: dict = {}
    if cfg.get("report_dir"):
        try:
            measured = book.merged(book.read(Path(cfg["report_dir"]).parents[1] / "tim" / book.BOOK_NAME))
        except Exception as exc:        # so hong khong lam hong ca so cai
            print("ledger: decision book unreadable (%s)" % exc, file=sys.stderr)

    vcache = _video_cache(cache)
    dirty = False
    rows = []
    vids = [v for v in by_vid if not live_titles or v in live_titles]
    for vid in vids:
        live = live_titles.get(vid, "")
        slug, source = "", "none"
        base = _resolve_base(history.get(vid, ""), slugs) if vid in history else None
        if base:
            slug, source = base, "history"
        if not slug and vid in vjson:
            slug, source = vjson[vid], "videos-json"      # giu slug du thu muc da bi xoa
        if not slug:
            # chi tinh khi ma (tran hoac trong URL watch?v= / youtu.be/) nam trong file ghi nhan dang bai:
            # UPLOAD.md, 0 - DOC TRUOC.md, hoi-cuu.md, ghi-chu-duyet.md, hoac ghi_chu bang cong viec.
            # BUILD_LOG.md va file khac co the nhac toi video khac.
            rx = _id_re(vid)
            hit = sorted(s for s in slugs if rx.search(evidence.get(s, "")) or rx.search(note.get(s, "")))
            if len(hit) > 1:
                hit = [s for s in hit if rx.search(note.get(s, ""))] or hit
            if len(hit) == 1:
                slug, source = hit[0], "id-in-md"
        if not slug and live:
            hit = titles_of.get(norm_title(live), set())
            if len(hit) == 1:
                slug, source = next(iter(hit)), "title"

        srows = by_vid[vid]
        last = srows[-1]
        pub = parse_pub(last, tz)
        r = {c: "" for c in COLUMNS}
        r.update({"video_id": vid, "live_title": live, "slug": slug, "match_source": source,
                  "published_local": pub.strftime("%Y-%m-%d %H:%M"), "weekday": pub.strftime("%a"),
                  "hour": pub.hour, "packaging": packaging(live)})
        r.update(title_flags(live) if live else {})
        r["title_shape"] = (title_shape(live, shapes) if live else "") or hist_md.get(vid, {}).get("shape", "")
        r["gun_1"] = hist_md.get(vid, {}).get("gun_1", "")

        if slug:
            proj = proj_root / slug
            r["lane"] = board.get(slug, {}).get("cach", "")
            for k, v in parse_hinh(_read(proj / "0 - \u0110\u1eccC TR\u01af\u1edaC.md")).items():
                r[k] = _blank(v)
            script = proj / "script_narration.txt"
            if script.is_file():
                r["script_words"] = count_words(_read(script))
            fb = history.get(vid) and _resolve_base(history[vid], slugs) or slug
            frow = next((f for f in features if f.get("variant") == "main" and f.get("base") in (fb, slug)), None)
            vals = None
            if frow and all((frow.get(k) or "") != "" for k in ("brightness", "saturation", "colourfulness",
                                                                 "warmth")):
                vals = tuple(float(frow[k]) for k in ("brightness", "saturation", "colourfulness", "warmth"))
            elif (proj / "output" / "final" / "thumbnail.jpg").is_file():
                vals = measure_thumb(proj / "output" / "final" / "thumbnail.jpg", build)
            if vals:
                (r["thumb_brightness"], r["thumb_saturation"], r["thumb_colourfulness"],
                 r["thumb_warmth"]) = vals
            mp4 = _final_mp4(proj)
            if mp4 is not None:
                mt = mp4.stat().st_mtime_ns
                ent = vcache.get(slug) if isinstance(vcache.get(slug), dict) else {}
                if ent.get("mtime") != mt:
                    ent = {"mtime": mt}
                # chi luu gia tri that: None (loi ffprobe/ffmpeg) khong vao cache, lan sau thu lai
                if ent.get("runtime_s") is None:
                    v = probe(mp4)
                    if v is not None:
                        ent["runtime_s"] = v
                        dirty = True
                if scan is not None and ent.get("runtime_s") and ent.get("cuts") is None:
                    v = scan(mp4, ent["runtime_s"])
                    if v is not None:
                        ent["cuts"] = v
                        dirty = True
                vcache[slug] = ent
                r["runtime_s"] = _blank(round(ent["runtime_s"], 2) if ent.get("runtime_s") else None)
                if ent.get("cuts") is not None and ent.get("runtime_s"):
                    r["sec_per_cut"] = round(ent["runtime_s"] / (ent["cuts"] + 1), 2)
            for k, raw in measured.get(slug, {}).items():     # so quyet dinh thang file du an
                if k in BOOK_MEASURES and raw not in ("", None):
                    try:
                        r[k] = float(raw)
                    except ValueError:
                        pass

        for tag, target, lo, hi in (("24h", 24, 12, 48), ("7d", 168, 120, 240)):
            s = pick_snapshot(srows, target, lo, hi)
            if s:
                r["ctr_" + tag], r["impr_" + tag], r["views_" + tag] = (_blank(s.get("ctr")),
                                                                      _blank(s.get("impressions")),
                                                                      _blank(s.get("views")))
        r["ctr_latest"], r["impr_latest"], r["views_latest"] = (_blank(last.get("ctr")),
                                                                _blank(last.get("impressions")),
                                                                _blank(last.get("views")))
        r["age_h_latest"] = _blank(last.get("age_hours"))
        for k in ("avd_s", "apv_pct", "ret_30s", "ret_60s", "src_browse", "src_suggested", "src_search",
                  "src_other"):
            r[k] = _blank(last.get(k))
        r["verdict"] = verdicts.get(vid, "")
        rows.append(r)

    if dirty:
        lf._write(cache / VIDEO_CACHE_NAME, json.dumps(vcache, indent=1))
    return rows


def parse_pub(last: dict, tz: ZoneInfo) -> datetime:
    from loop.state import parse_utc
    return (parse_utc(last["captured_at_utc"]) - timedelta(hours=last["age_hours"])).astimezone(tz)


# ---------- ghi ----------

def _csv_text(rows: list) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _md_text(rows: list) -> str:
    lines = ["# Per-video ledger", "", "| " + " | ".join(MD_COLUMNS) + " |",
             "|" + "---|" * len(MD_COLUMNS)]
    for r in rows:
        lines.append("| " + " | ".join(str(r[c]).replace("|", "/") for c in MD_COLUMNS) + " |")
    return "\n".join(lines) + "\n"


def write_outputs(rows: list, report_dir: Path) -> None:
    """ledger.csv (UTF-8, co header) va ledger.md, sap theo published_local giam dan; ghi nguyen tu."""
    rows = sorted(rows, key=lambda r: r["published_local"], reverse=True)
    report_dir = Path(report_dir)
    lf._write(report_dir / "ledger.csv", _csv_text(rows))
    lf._write(report_dir / "ledger.md", _md_text(rows))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="So cai theo tung video: da lam gi, dat bao nhieu.")
    ap.add_argument("--config", default=str(an.DEFAULT_CONFIG))
    ap.add_argument("--cache", default=str(lf.DEFAULT_CACHE))
    ap.add_argument("--no-video-scan", action="store_true", help="bo qua ffmpeg scene detection")
    args = ap.parse_args(argv)

    cfg = an._read_json(Path(args.config), {})
    cfg = cfg if isinstance(cfg, dict) else {}
    cfg.setdefault("build_repo", DEFAULT_BUILD_REPO)
    cache = Path(args.cache)
    if not (Path(cfg["build_repo"]) / "projects").is_dir():
        print("ledger: build repo not found at %s (config key build_repo)" % cfg["build_repo"], file=sys.stderr)
        return 2
    if not list((cache / "snapshots").glob("*.csv")):
        print("ledger: no snapshots in the cache %s; run loop.fetch first" % cache, file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    live = live_titles(cache, now)
    rows = build_ledger(cache, cfg, now, live, scan=None if args.no_video_scan else _default_scan)
    write_outputs(rows, Path(cfg.get("report_dir") or an.DEFAULT_REPORT_DIR))
    counts: dict = {}
    for r in rows:
        counts[r["match_source"]] = counts.get(r["match_source"], 0) + 1
    pk: dict = {}
    for r in rows:
        pk[r["packaging"]] = pk.get(r["packaging"], 0) + 1
    print("ledger: %d videos; match %s; packaging %s%s" % (
        len(rows), json.dumps(counts, sort_keys=True), json.dumps(pk, sort_keys=True),
        "" if live else "; live titles: unavailable"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
