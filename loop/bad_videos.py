"""Fact pack for the daily "bad video -> proposal" step (owner 2026-11-28: when a video has a bad result, analyse it and
propose a title / thumbnail change; scheduler step 3c of bareskull-studio-loop).

Deterministic and read-only: it picks the candidates from the ledger and gathers the facts the analyst needs, so the
list is never typed by hand. It proposes nothing and writes nothing but the pack.

    python -m loop.bad_videos [--out DIR] [--bar 0.07] [--max-age-h 168]

Candidate = ledger verdict is not KEEP, CTR below the bar, age <= max-age-h, and judged by the same gate as
loop.rules (>= 6 h old OR >= 1,500 impr). A video the heart is riding is listed with being_ridden=true (do not propose
edits for it).
"""
import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

BRAIN = Path(r"D:\Youtube\video_footage_re_create\Bare Skull - 2nd brain\van-hanh")
STUDIO = BRAIN / "bao-cao" / "studio"
PROJECTS = Path(r"D:\Youtube\video_footage_re_create\projects")
CACHE = Path(__file__).resolve().parent / "cache"
BAR = 0.07
MIN_AGE_H, MIN_IMPR_FIRST, MAX_AGE_H = 6, 1500, 168
WINNER_CTR = 0.057


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_ledger(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def trend(vid: str, snap_dir: Path) -> list[dict]:
    out = []
    for p in sorted(snap_dir.glob("*.csv")):
        with p.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("video_id") == vid and r.get("period") == "since_published":
                    out.append({"age_h": _f(r.get("age_hours")), "impr": _f(r.get("impressions")),
                                "ctr": _f(r.get("ctr")), "avd_s": _f(r.get("avd_s")), "thumb_hash": (r.get("thumb_hash") or "")[:8]})
    return out


def locked_title_and_alts(upload_md: Path) -> tuple[str | None, list[str]]:
    if not upload_md.exists():
        return None, []
    text = upload_md.read_text(encoding="utf-8")
    blocks = {m.group(1).strip(): m.group(2).strip()
              for m in re.finditer(r"^## (Title[^\n]*)\n(?:[^\n`]*\n)*?```\n(.*?)\n```", text, re.M | re.S)}
    locked = next((v for k, v in blocks.items() if not re.search(r"alt", k, re.I)), None)
    alts = [v for k, v in blocks.items() if re.search(r"alt", k, re.I)]
    return locked, alts


def project_facts(slug: str, live_title: str) -> dict:
    d = PROJECTS / slug
    if not slug or not d.is_dir():
        return {"project": None}
    locked, alts = locked_title_and_alts(d / "UPLOAD.md")
    final = d / "output" / "final"
    thumbs = sorted(str(p) for p in final.glob("*.jpg")) if final.is_dir() else []
    return {"project": str(d), "locked_title": locked, "title_alts": alts,
            "title_changed_from_locked": bool(locked) and locked.strip() != live_title.strip(),
            "thumbnails": thumbs,
            "thumbnail_note": str(d / "THUMBNAIL.md") if (d / "THUMBNAIL.md").exists() else None,
            "script": str(d / "script_narration.txt") if (d / "script_narration.txt").exists() else None}


def candidates(rows: list[dict], bar: float, max_age_h: float, riding_text: str) -> list[dict]:
    out = []
    for r in rows:
        age, ctr, impr = _f(r.get("age_h_latest")), _f(r.get("ctr_latest")), _f(r.get("impr_latest"))
        if None in (age, ctr, impr) or r.get("verdict") == "KEEP":
            continue
        if age > max_age_h or ctr >= bar or not (age >= MIN_AGE_H or impr >= MIN_IMPR_FIRST):
            continue
        out.append(r)
    return out


def winners(rows: list[dict], lane: str, packaging: str, n: int = 3) -> list[dict]:
    pool = [r for r in rows if (_f(r.get("ctr_latest")) or 0) >= WINNER_CTR and (_f(r.get("impr_latest")) or 0) >= 5000
            and r.get("slug")]
    pool.sort(key=lambda r: (r.get("packaging") == packaging, r.get("lane") == lane, _f(r.get("ctr_latest"))), reverse=True)
    res = []
    for r in pool[:n]:
        fin = PROJECTS / r["slug"] / "output" / "final" / "thumbnail.jpg"
        res.append({"video_id": r["video_id"], "slug": r["slug"], "title": r["live_title"], "ctr": _f(r["ctr_latest"]),
                    "impr": _f(r["impr_latest"]), "packaging": r.get("packaging"),
                    "thumbnail": str(fin) if fin.exists() else None})
    return res


def build(ledger: Path, snap_dir: Path, bar: float, max_age_h: float, riding_text: str) -> list[dict]:
    rows = read_ledger(ledger)
    pack = []
    for r in candidates(rows, bar, max_age_h, riding_text):
        vid = r["video_id"]
        pack.append({
            "video_id": vid, "live_title": r["live_title"], "slug": r["slug"], "packaging": r.get("packaging"),
            "lane": r.get("lane"), "title_shape": r.get("title_shape"), "published_local": r["published_local"],
            "age_h": _f(r["age_h_latest"]), "ctr": _f(r["ctr_latest"]), "impr": _f(r["impr_latest"]),
            "ctr_24h": _f(r.get("ctr_24h")), "avd_s": _f(r.get("avd_s")), "runtime_s": _f(r.get("runtime_s")),
            "ret_30s": _f(r.get("ret_30s")), "src_browse": _f(r.get("src_browse")),
            "thumb": {k: _f(r.get(k)) for k in ("thumb_brightness", "thumb_saturation", "thumb_colourfulness", "thumb_warmth")},
            "verdict": r.get("verdict"), "being_ridden": vid in riding_text,
            "trend": trend(vid, snap_dir),
            **project_facts(r["slug"], r["live_title"]),
            "winners_to_compare": winners(rows, r.get("lane", ""), r.get("packaging", "")),
        })
    return pack


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(STUDIO / "de-xuat"))
    ap.add_argument("--bar", type=float, default=BAR)
    ap.add_argument("--max-age-h", type=float, default=MAX_AGE_H)
    a = ap.parse_args(argv)
    chi_thi = BRAIN / "tim" / "chi-thi.json"
    riding = chi_thi.read_text(encoding="utf-8") if chi_thi.exists() else ""
    pack = build(STUDIO / "ledger.csv", CACHE / "snapshots", a.bar, a.max_age_h, riding)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / ("%s-du-lieu.json" % date.today().isoformat())
    dest.write_text(json.dumps({"bar": a.bar, "videos": pack}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("bad_videos: %d candidate(s) -> %s" % (len(pack), dest))
    for v in pack:
        print("  %s %s ctr %.1f%% impr %d age %.0f h%s" % (v["video_id"], v["slug"] or "-", (v["ctr"] or 0) * 100,
              v["impr"] or 0, v["age_h"] or 0, " RIDDEN" if v["being_ridden"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
