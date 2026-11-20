"""Phan tich snapshot, ghi phan quyet, bao cao va yeu cau swap (chay tren may chu kenh).

    python -m loop.analyse --channel "Bare Skull" [--dry-run] [--why]

Doc cache do loop.fetch keo ve. Ghi: studio/verdicts.csv (Drive), <report_dir>/YYYY-MM-DD.md,
actions/pending/<video>__<utc>.json (tru khi con alert-only), Telegram khi phan quyet doi.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from loop import fetch as lf
from zoneinfo import ZoneInfo

from loop.rules import (BASE_BAR, KEEP_CTR, MIN_IMPR, REVERT_TARGETS, Verdict, age_bucket, decide,
                        swap_bar)
from loop.state import ALTS, aware, build_states, ok_actions, parse_utc, since_published  # noqa: F401

import studio_store as ss  # noqa: E402  (fetch da them scripts/ vao sys.path)
import youtube_uploader as yu  # noqa: E402

DEFAULT_REPORT_DIR = "D:/Youtube/video_footage_re_create/Bare Skull - 2nd brain/van-hanh/bao-cao/studio"
DEFAULT_TZ = "Asia/Ho_Chi_Minh"
DEFAULT_CONFIG =lf.REPO / "loop" / "config.json"
NOTIFY_VERDICTS = {Verdict.SWAP, Verdict.REVERT, Verdict.CHANGE_TITLE, Verdict.NOT_PACKAGING,
                   Verdict.OPENING, Verdict.PAUSED}
VERDICT_COLS = ("video_id", "base", "verdict", "reason", "ctr", "impressions", "bar", "variant",
                "updated_utc")
CATEGORICAL = ("background", "gun_angle", "props", "accent_colour")
NUMERIC = ("colourfulness", "warmth", "gun_width_share", "word_count")
LAST_VERDICTS = ".last_verdicts.json"


def notify(msg: str) -> None:
    yu.notify(msg)


def load_features(cache: Path) -> list:
    p = Path(cache) / lf.FEATURES_NAME
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _health(cache: Path) -> tuple[str, str]:
    obj = _read_json(Path(cache) / ss.HEALTH_NAME, None)
    if not isinstance(obj, dict) or "status" not in obj:
        return ("missing" if obj is None else "corrupt"), ""
    return str(obj["status"]), str(obj.get("detail") or "")


def _healthy(status: str) -> bool:
    return status.lower() in ("ok", "pass")


def alert_only(cfg: dict, now: datetime) -> bool:
    """Truoc go_live + alert_only_days thi khong ghi yeu cau. Thieu go_live thi mai mai alert-only."""
    gl = cfg.get("go_live")
    if not gl:
        return True
    text = str(gl).strip()
    if len(text) <= 10:     # chi ngay: 00:00 gio dia phuong
        start = datetime.combine(date.fromisoformat(text), datetime.min.time(), tzinfo=_tz(cfg))
    else:
        start = parse_utc(text)
    return now < start + timedelta(days=int(cfg.get("alert_only_days", 7)))


def _tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg.get("tz") or DEFAULT_TZ)


def _failed_recently(actions: list, vid: str, target: str, now: datetime) -> bool:
    for a in actions:
        if a.get("_state") != "failed" or a.get("video_id") != vid or a.get("to_variant") != target:
            continue
        try:
            at = parse_utc((a.get("result") or {}).get("at_utc") or a.get("requested_utc"))
        except ValueError:
            continue
        if at is not None and now - at < timedelta(hours=24):
            return True
    return False


def _peer_median(vid: str, snap: dict, latest: dict, key: str) -> float | None:
    bucket = age_bucket(snap["age_hours"])
    vals = [s[key] for v, s in latest.items()
            if v != vid and s.get(key) is not None and age_bucket(s["age_hours"]) == bucket]
    return median(vals) if len(vals) >= 3 else None


def _revert_target(state) -> str | None:
    others = [(c, v) for v, (n, c) in state.variant_stats.items()
              if v != state.variant and v in REVERT_TARGETS and n >= MIN_IMPR]
    return max(others)[1] if others else None


def _fmt_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pct(x: float) -> str:
    return "%.1f%%" % (x * 100)


def _why(rows: list, states: dict, features: list, bases: dict, done: dict, bars: dict) -> list:
    fmap = {(f.get("base"), f.get("variant")): f for f in features}
    entries = []
    for vid, st in states.items():
        for v, (n, c) in st.variant_stats.items():
            f = fmap.get((bases.get(vid), v))
            if f is not None and n >= MIN_IMPR:
                entries.append((vid, v, n, c, f))
    winners = [e for e in entries if e[3] >= KEEP_CTR]
    weak = [e for e in entries if e[3] < bars.get(e[0], BASE_BAR)]
    out = ["", "## Why", ""]
    if len(winners) < 3:
        out.append("not enough winners yet (%d, need 3)" % len(winners))
    else:
        mode = {}
        for col in CATEGORICAL:
            vals = [e[4].get(col) for e in winners if e[4].get(col) not in (None, "")]
            mode[col] = max(set(vals), key=vals.count) if vals else None
        rng = {}
        for col in NUMERIC:
            nums = [_num(e[4].get(col)) for e in winners]
            nums = [x for x in nums if x is not None]
            rng[col] = (min(nums), max(nums)) if nums else None
        for vid, v, n, c, f in weak:
            diffs = []
            for col in CATEGORICAL:
                if mode[col] is not None and f.get(col) not in (None, "") and f.get(col) != mode[col]:
                    diffs.append("%s %s (winners: %s)" % (col, f.get(col), mode[col]))
            for col in NUMERIC:
                x = _num(f.get(col))
                if rng[col] and x is not None and not rng[col][0] <= x <= rng[col][1]:
                    diffs.append("%s %s (winners: %s-%s)" % (col, f.get(col), rng[col][0], rng[col][1]))
            out.append("- %s %s %s at %s impr differs from winners in: %s" % (
                bases.get(vid) or vid, v, _pct(c), format(n, ","), ", ".join(diffs) or "nothing found"))
    swaps = []
    for vid, lst in done.items():
        st = states.get(vid)
        if st is None:
            continue
        for _, a in lst:
            to, frm = a["to_variant"], a.get("from_variant")
            a_n, b_n = st.variant_stats.get(to), st.variant_stats.get(frm)
            if not a_n or not b_n or a_n[0] < MIN_IMPR or b_n[0] < MIN_IMPR:
                continue
            fa, fb = fmap.get((bases.get(vid), to)), fmap.get((bases.get(vid), frm))
            diff = sorted(k for k in CATEGORICAL + NUMERIC + ("words",)
                          if fa and fb and fa.get(k) != fb.get(k)) if fa and fb else []
            swaps.append("- %s: %s (differs in %s) %s vs %s %s" % (
                bases.get(vid) or vid, to, ", ".join(diff) or "unknown", _pct(a_n[1]), frm, _pct(b_n[1])))
    if swaps:
        out += ["", "Finished swaps:", ""] + swaps
    return out


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _report(rows: list, health: tuple, now: datetime, why: list | None) -> str:
    status, detail = health
    lines = ["# Studio loop report %s" % now.strftime("%Y-%m-%d"), "",
             "Health: %s%s" % (status, " - " + detail if detail else ""), "",
             "| video_id | base | verdict | reason | variant | age_h |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        cells = [r["video_id"], r["base"], r["verdict"], r["reason"], r["variant"], "%.1f" % r["age_hours"]]
        lines.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
    if why is not None:
        lines += why
    return "\n".join(lines) + "\n"


def _verdicts_csv(rows: list) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(VERDICT_COLS)
    for r in rows:
        w.writerow([r[c] for c in VERDICT_COLS])
    return buf.getvalue()


def analyse(cache: Path, cfg: dict, now: datetime, drive=None, folders: dict | None = None,
            dry_run: bool = False) -> list:
    cache, now = Path(cache), aware(now)
    snaps_all = lf.load_snapshots(cache)
    actions = lf.load_actions(cache)
    features = load_features(cache)
    bases = lf.load_history(cache)
    pending_file = cache / "actions" / "pending.txt"
    pending = pending_file.read_text(encoding="utf-8").split() if pending_file.exists() else []
    states = build_states(snaps_all, actions, features, now, bases=bases, pending=pending)
    by_vid = since_published(snaps_all)
    history = [s for rows in by_vid.values() for s in rows]
    latest = {v: rows[-1] for v, rows in by_vid.items()}
    alert = alert_only(cfg, now)
    live = drive is not None and folders is not None and not dry_run and not alert

    fmap_all = {(f.get("base"), f.get("variant")) for f in features}
    rows, bars, requests = [], {}, []
    for vid in sorted(latest):
        snap, st = latest[vid], states[vid]
        bar = swap_bar(snap["age_hours"], history)
        bars[vid] = bar
        verdict, reason = decide(snap, st, bar, now, _peer_median(vid, snap, latest, "impressions"),
                                 _peer_median(vid, snap, latest, "ret_30s"))
        target = None
        if verdict is Verdict.SWAP:
            target = st.alts_left[0]
        elif verdict is Verdict.REVERT:
            target = _revert_target(st)
        if target and verdict is Verdict.SWAP and (bases.get(vid), target) not in fmap_all:
            reason += " (no alt available)"     # khong co dong dac trung cho alt: khong ghi yeu cau
        elif target:
            word = "swap" if verdict is Verdict.SWAP else "revert"
            if alert:
                reason = "would %s to %s: %s" % (word, target, reason)
            elif st.pending_action:
                reason = "%s to %s already requested: %s" % (word, target, reason)
            elif _failed_recently(actions, vid, target, now):
                reason = "%s to %s failed within 24 h, not retried: %s" % (word, target, reason)
            else:
                reason = "%s to %s: %s" % (word, target, reason)
                requests.append({"video_id": vid, "base": bases.get(vid, ""), "to_variant": target,
                                 "from_variant": st.variant, "reason": reason,
                                 "requested_utc": _fmt_utc(now)})
        rows.append({"video_id": vid, "base": bases.get(vid, ""), "verdict": verdict.value,
                     "reason": reason, "ctr": round(snap["ctr"] or 0.0, 4),
                     "impressions": snap["impressions"], "bar": round(bar, 4), "variant": st.variant,
                     "updated_utc": _fmt_utc(now), "age_hours": snap["age_hours"]})

    health = _health(cache)
    why = None
    local = now.astimezone(_tz(cfg))
    if local.weekday() == 0 or cfg.get("why"):
        why = _why(rows, states, features, bases, ok_actions(actions), bars)
    report = _report(rows, health, local, why)
    report_dir = Path(cfg.get("report_dir") or DEFAULT_REPORT_DIR)
    report_path = report_dir / ("%s.md" % local.strftime("%Y-%m-%d"))
    lf._write(report_path, report)

    if dry_run:
        print("report: %s" % report_path)
        for r in rows:
            print("%s %s %s" % (r["video_id"], r["verdict"], r["reason"]))
        return rows
    if live:
        for rq in requests:
            name = "%s__%s.json" % (rq["video_id"], now.strftime("%Y%m%dT%H%M%SZ"))
            ss.put_json(drive, folders["actions_pending"], name, rq)
    if drive is not None and folders is not None:
        ss.put_text(drive, folders["studio"], "verdicts.csv", _verdicts_csv(rows))

    last_path = cache / LAST_VERDICTS
    last_obj = _read_json(last_path, {})
    last = last_obj.get("verdicts", {}) if isinstance(last_obj, dict) else {}
    missing_day = last_obj.get("missing_notified") if isinstance(last_obj, dict) else None
    today = local.strftime("%Y-%m-%d")
    if not _healthy(health[0]):
        if health[0] != "missing":
            notify("Studio loop health: %s %s" % health)
        elif missing_day != today:      # "missing" nhieu khi chi la chua co file: moi ngay mot lan
            notify("Studio loop health: missing")
            missing_day = today
    for r in rows:
        if r["verdict"] != last.get(r["video_id"]) and Verdict(r["verdict"]) in NOTIFY_VERDICTS:
            notify("%s (%s): %s - %s" % (r["base"] or r["video_id"], r["video_id"], r["verdict"], r["reason"]))
    lf._write(last_path, json.dumps({"verdicts": {r["video_id"]: r["verdict"] for r in rows},
                                     "missing_notified": missing_day}, indent=2))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phan tich snapshot, ghi bao cao va yeu cau swap.")
    ap.add_argument("--channel", default=None)
    ap.add_argument("--cache", default=str(lf.DEFAULT_CACHE))
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--why", action="store_true")
    ap.add_argument("--studio-folder-id", default=None)
    ap.add_argument("--drive-root", default="YouTube-Upload")
    ap.add_argument("--client-secret", default=str(lf.SCRIPTS / "client_secret.json"))
    ap.add_argument("--token-drive", default=str(lf.SCRIPTS / "token-drive-link.json"))
    args = ap.parse_args(argv)

    cfg = _read_json(Path(args.config), {})
    channel = args.channel      # chi --channel moi cho phep che do theo ten, config thi khong
    studio_id = args.studio_folder_id or cfg.get("studio_folder_id")
    if not channel and not studio_id:
        print("thieu --channel hoac --studio-folder-id", file=sys.stderr)
        return 2
    cfg["why"] = args.why
    drive = folders = None
    if not args.dry_run:
        from googleapiclient.discovery import build
        creds = yu.get_credentials(Path(args.client_secret), Path(args.token_drive),
                                   yu.DRIVE_SCOPES, "Drive")
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        if studio_id:       # may chu kenh chi thay studio/: khong bao gio resolve_folders
            folders = ss.studio_folders_by_id(drive, studio_id)
        else:
            chan = yu.resolve_folders(drive, args.drive_root, channel)["channel"]
            folders = ss.studio_folders(drive, chan)
    analyse(Path(args.cache), cfg, datetime.now(timezone.utc), drive, folders, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
