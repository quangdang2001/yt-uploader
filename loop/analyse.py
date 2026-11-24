"""Phan tich snapshot, ghi phan quyet, bao cao va yeu cau swap (chay tren may chu kenh).

    python -m loop.analyse --channel "Bare Skull" [--dry-run] [--why]

Doc cache do loop.fetch keo ve. Ghi: studio/verdicts.csv (Drive), <report_dir>/YYYY-MM-DD.md,
actions/pending/<video>__<utc>.json (tru khi con alert-only), Telegram.

Telegram (khong bao gio dong loat):
- health: khi trang thai doi, cong mot lan nhac moi ngay khi chua PASS/ok;
- du lieu cu (health.json > 26 h hoac snapshot moi nhat > 26 h; VPS doc 1 lan/ngay): mot lan moi ngay;
- lan chay dau (chua co .last_verdicts.json): MOT tin tong hop, khong tin rieng tung video;
- tung video: mot loai phan quyet bao toi da mot lan moi 24 h, va chi khi giu 2 lan chay lien tiep;
  rieng SWAP/REVERT o che do live thi bao ngay khi ghi yeu cau.
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
from loop import titles as tl
from zoneinfo import ZoneInfo

from loop.rules import (BASE_BAR, KEEP_CTR, MIN_IMPR, Verdict, age_bucket, decide, revert_target,
                        swap_bar)
from loop.state import ALTS, aware, build_states, ok_actions, parse_utc, since_published  # noqa: F401

import studio_store as ss  # noqa: E402  (fetch da them scripts/ vao sys.path)
import youtube_uploader as yu  # noqa: E402

DEFAULT_REPORT_DIR = "D:/Youtube/video_footage_re_create/Bare Skull - 2nd brain/van-hanh/bao-cao/studio"
DEFAULT_TZ = "Asia/Ho_Chi_Minh"
DEFAULT_CONFIG = lf.REPO / "loop" / "config.json"
NOTIFY_VERDICTS = {Verdict.SWAP, Verdict.REVERT, Verdict.CHANGE_TITLE, Verdict.NOT_PACKAGING,
                   Verdict.OPENING, Verdict.PAUSED}
VERDICT_COLS = ("video_id", "base", "verdict", "reason", "ctr", "impressions", "bar", "variant",
                "updated_utc")
CATEGORICAL = ("background", "gun_angle", "props", "accent_colour")
NUMERIC = ("colourfulness", "warmth", "gun_width_share", "word_count")
LAST_VERDICTS = ".last_verdicts.json"
STALE_HEALTH_H = 26         # health.json cu hon -> VPS khong chay (VPS doc Studio 1 lan/ngay luc 18:00 VN tu 2026-11-20)
STALE_SNAPSHOT_H = 26       # snapshot moi nhat cu hon -> VPS khong doc duoc gi (video cu doc moi ngay)
ALERT_GAP_H = 24            # mot loai phan quyet cua mot video: toi da mot tin moi 24 h
PEER_AGE_TOL = 0.25         # NOT_PACKAGING: snapshot cua video khac co tuoi trong +-25% tuoi video nay
MIN_PEERS = 3


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


def _health(cache: Path) -> dict:
    obj = _read_json(Path(cache) / ss.HEALTH_NAME, None)
    if not isinstance(obj, dict) or "status" not in obj:
        return {"status": "missing" if obj is None else "corrupt", "detail": "", "at": None}
    try:
        at = parse_utc(obj.get("at_utc"))
    except ValueError:
        at = None
    return {"status": str(obj["status"]), "detail": str(obj.get("detail") or ""), "at": at}


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
    """Trung vi `key` cua snapshot moi nhat cac video khac cung nhom tuoi (dung cho ret_30s / OPENING)."""
    bucket = age_bucket(snap["age_hours"])
    vals = [s[key] for v, s in latest.items()
            if v != vid and s.get(key) is not None and age_bucket(s["age_hours"]) == bucket]
    return median(vals) if len(vals) >= MIN_PEERS else None


def _peer_typical_impr(vid: str, age: float, by_vid: dict) -> float | None:
    """NOT_PACKAGING: voi moi video khac, snapshot co tuoi gan tuoi video nay nhat (trong +-25%); video khong co
    snapshot nhu vay bi bo. It hon 3 video -> None."""
    vals = []
    for v, rows in by_vid.items():
        if v == vid:
            continue
        best = None
        for r in rows:
            d = abs(r["age_hours"] - age)
            if d <= PEER_AGE_TOL * age and (best is None or d < best[0]):
                best = (d, r["impressions"])
        if best is not None:
            vals.append(best[1])
    return median(vals) if len(vals) >= MIN_PEERS else None


def _revert_target(state) -> str | None:
    return revert_target(state)


def _fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _stale_line(health_at: datetime | None, snaps_all: list, now: datetime) -> str | None:
    """'Studio loop: no fresh data from the VPS since <time>' khi health.json > 26 h hoac snapshot moi nhat
    > 26 h; nguoc lai None. <time> = snapshot moi nhat neu no cu, khong thi luc ghi health."""
    newest = None
    for s in snaps_all:
        try:
            t = parse_utc(s.get("captured_at_utc"))
        except ValueError:
            continue
        if t is not None and (newest is None or t > newest):
            newest = t
    if newest is not None and now - newest > timedelta(hours=STALE_SNAPSHOT_H):
        since = newest
    elif health_at is not None and now - health_at > timedelta(hours=STALE_HEALTH_H):
        since = health_at
    else:
        return None
    return "Studio loop: no fresh data from the VPS since %s" % _fmt_utc(since)


def _report(rows: list, health: tuple, now: datetime, why: list | None, stale: str | None = None,
            other_periods: int = 0) -> str:
    status, detail = health
    lines = ["# Studio loop report %s" % now.strftime("%Y-%m-%d"), ""]
    if stale:
        lines += [stale, ""]
    lines += ["Health: %s%s" % (status, " - " + detail if detail else ""), "",
              "Snapshot rows not since_published (not used): %d" % other_periods, "",
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


def _released(cfg: dict, stored: dict, now: datetime) -> dict:
    """video_id -> luc tha (chuoi UTC). Lan dau thay id trong release_manual thi ghi luc nay; id bi bo khoi
    config thi quen (them lai = mot lan tha moi)."""
    ids = cfg.get("release_manual") or []
    if not isinstance(ids, list):
        return {}
    stored = stored if isinstance(stored, dict) else {}
    return {str(v): stored.get(str(v)) or _fmt_utc(now) for v in ids if str(v).strip()}


def _gap_ok(last: str | None, now: datetime) -> bool:
    try:
        at = parse_utc(last)
    except ValueError:
        at = None
    return at is None or now - at >= timedelta(hours=ALERT_GAP_H)


def _digest(rows: list, written: int) -> str:
    counts: dict = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    parts = ["%d videos" % len(rows)]
    parts += ["%d %s" % (counts[v.value], v.value) for v in Verdict if v.value in counts]
    if written:
        parts.append("%d requests written" % written)
    return "Studio loop started: " + ", ".join(parts)


def analyse(cache: Path, cfg: dict, now: datetime, drive=None, folders: dict | None = None,
            dry_run: bool = False) -> list:
    cache, now = Path(cache), aware(now)
    last_path = cache / LAST_VERDICTS
    first_run = not last_path.exists()
    last_obj = _read_json(last_path, {})
    if not isinstance(last_obj, dict):
        last_obj = {}
    released = _released(cfg, last_obj.get("released"), now)

    snaps_all = lf.load_snapshots(cache)
    actions = lf.load_actions(cache)
    features = load_features(cache)
    bases = lf.load_bases(cache, cfg.get("report_dir"))
    pending_file = cache / "actions" / "pending.txt"
    pending = pending_file.read_text(encoding="utf-8").split() if pending_file.exists() else []
    states = build_states(snaps_all, actions, features, now, bases=bases, pending=pending,
                          released={v: parse_utc(t) for v, t in released.items()})
    by_vid = since_published(snaps_all)
    history = [s for rows in by_vid.values() for s in rows]
    latest = {v: rows[-1] for v, rows in by_vid.items()}
    other_periods = sum(1 for s in snaps_all if s.get("period") != "since_published")
    alert = alert_only(cfg, now)
    live = drive is not None and folders is not None and not dry_run and not alert

    health = _health(cache)
    stale = _stale_line(health["at"], snaps_all, now)   # du lieu cu: van phan quyet, khong ghi yeu cau
    fmap_all = {(f.get("base"), f.get("variant")) for f in features}
    title_alts = tl.load_alts(cache)
    title_done, title_manual = tl.ok_actions(actions), tl.manual_videos(actions)
    rows, bars, requests = [], {}, []
    for vid in sorted(latest):
        snap, st = latest[vid], states[vid]
        bar = swap_bar(snap["age_hours"], history)
        bars[vid] = bar
        verdict, reason = decide(snap, st, bar, now, _peer_typical_impr(vid, snap["age_hours"], by_vid),
                                 _peer_median(vid, snap, latest, "ret_30s"))
        target = None
        if st.variant == "manual":
            target = None       # the owner's thumbnail: never a request, decide marked it alert-only
        elif verdict is Verdict.SWAP:
            target = st.alts_left[0]
        elif verdict is Verdict.REVERT:
            target = revert_target(st)
        # Duong tieu de (loop/titles.py). Sau mot lan doi tieu de thi khong ghi yeu cau thumbnail nua: so sau do la
        # cua tieu de moi, khong phai cua thumbnail dang chay.
        talts = title_alts.get(bases.get(vid))
        tstate = None
        if talts or vid in title_done:
            tstate = tl.build_state(by_vid[vid], title_done.get(vid, []), talts, st.last_swap_utc,
                                    manual=vid in title_manual)
            if tstate.touched and target:
                target = None
                reason += " (title changed: thumbnail left as is)"
        title_move = None
        if tstate is not None and not target and st.variant != "manual":
            title_move = tl.decide(snap, tstate, verdict, reason, now, st.last_swap_utc, st.pending_action,
                                   bool(snap.get("test_running")))
        requested = False
        if title_move:
            kind, to, why = title_move
            word = "title swap" if kind == "swap" else "title revert"
            rq = tl.request(vid, bases.get(vid, ""), talts, tstate, to, "%s to %s: %s" % (word, to, why), _fmt_utc(now))
            if rq is None:
                reason = "%s (%s to %s: no text for it)" % (reason, word, to)
            elif alert:
                reason = "would %s to %s: %s" % (word, to, why)
            elif stale:
                reason = "%s to %s: %s (stale data - no request)" % (word, to, why)
            elif tl.failed_recently(actions, vid, to, now):
                reason = "%s to %s failed within 24 h, not retried: %s" % (word, to, why)
            else:
                reason = rq["reason"]
                requests.append(rq)
                requested = live
        elif target and verdict is Verdict.SWAP and (bases.get(vid), target) not in fmap_all:
            reason += " (no alt available)"     # khong co dong dac trung cho alt: khong ghi yeu cau
        elif target:
            word = "swap" if verdict is Verdict.SWAP else "revert"
            if alert:
                reason = "would %s to %s: %s" % (word, target, reason)
            elif stale:
                reason = "%s to %s: %s (stale data - no request)" % (word, target, reason)
            elif st.pending_action:
                reason = "%s to %s already requested: %s" % (word, target, reason)
            elif _failed_recently(actions, vid, target, now):
                reason = "%s to %s failed within 24 h, not retried: %s" % (word, target, reason)
            else:
                reason = "%s to %s: %s" % (word, target, reason)
                expect = next((r.get("thumb_hash") for r in reversed(by_vid[vid]) if r.get("thumb_hash")), "")
                requests.append({"video_id": vid, "base": bases.get(vid, ""), "to_variant": target,
                                 "from_variant": st.variant, "reason": reason,
                                 "requested_utc": _fmt_utc(now), "expect_hash": expect})
                requested = live
        rows.append({"video_id": vid, "base": bases.get(vid, ""), "verdict": verdict.value,
                     "reason": reason, "ctr": round(snap["ctr"] or 0.0, 4),
                     "impressions": snap["impressions"], "bar": round(bar, 4), "variant": st.variant,
                     "updated_utc": _fmt_utc(now), "age_hours": snap["age_hours"], "requested": requested})

    why = None
    local = now.astimezone(_tz(cfg))
    if local.weekday() == 0 or cfg.get("why"):
        why = _why(rows, states, features, bases, ok_actions(actions), bars)
    report = _report(rows, (health["status"], health["detail"]), local, why, stale, other_periods)
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

    today = local.strftime("%Y-%m-%d")
    now_s = _fmt_utc(now)

    # health: khi trang thai doi, cong mot lan nhac moi ngay khi chua PASS/ok
    hprev = last_obj.get("health") if isinstance(last_obj.get("health"), dict) else {}
    status, hday = health["status"], hprev.get("day")
    same_status = status == hprev.get("status")
    h_sent = bool(hprev.get("sent", True)) if same_status else False    # da gui canh bao cho trang thai nay chua
    h_runs = int(hprev.get("runs") or 0) + 1 if same_status else 1
    if not _healthy(status):
        # FAIL read chi bao khi giu 2 lan analyse lien tiep (mot video doc loi khong lam on); FAIL auth/api bao ngay
        debounced = status.lower().startswith("fail read") and h_runs < 2
        if not debounced and (not h_sent or hday != today):
            notify("Studio loop health: %s%s" % (status, " " + health["detail"] if health["detail"] else ""))
            hday, h_sent = today, True
    elif hprev.get("status") is not None and not _healthy(str(hprev.get("status"))):
        if bool(hprev.get("sent", True)):       # chi bao "PASS" khi da that su gui canh bao FAIL
            notify("Studio loop health: %s (was %s)" % (status, hprev.get("status")))
        h_sent = False

    stale_day = last_obj.get("stale_notified")
    if stale and stale_day != today:
        notify(stale)
        stale_day = today

    prev_videos = last_obj.get("videos") if isinstance(last_obj.get("videos"), dict) else {}
    legacy = last_obj.get("verdicts") if isinstance(last_obj.get("verdicts"), dict) else {}
    videos = {}
    for r in rows:
        vid, kind = r["video_id"], r["verdict"]
        p = prev_videos.get(vid)
        if not isinstance(p, dict):
            # file cu chi co {"verdicts"}: no da bao khi phan quyet doi, coi nhu da bao
            p = {"verdict": legacy[vid], "streak": 1, "alerted": True} if vid in legacy else {}
        same = p.get("verdict") == kind
        streak = int(p.get("streak") or 0) + 1 if same else 1
        alerted = bool(p.get("alerted")) if same else False
        last_alert = dict(p.get("last_alert") or {}) if isinstance(p.get("last_alert"), dict) else {}
        msg = "%s (%s): %s - %s" % (r["base"] or vid, vid, kind, r["reason"])
        if first_run:
            alerted = True              # the digest covers it
            if Verdict(kind) in NOTIFY_VERDICTS:
                last_alert[kind] = now_s
        elif r["requested"]:
            notify(msg)
            alerted, last_alert[kind] = True, now_s
        elif (Verdict(kind) in NOTIFY_VERDICTS and streak >= 2 and not alerted
              and _gap_ok(last_alert.get(kind), now)):
            notify(msg)
            alerted, last_alert[kind] = True, now_s
        videos[vid] = {"verdict": kind, "streak": streak, "alerted": alerted, "last_alert": last_alert}
    if first_run:
        notify(_digest(rows, len(requests) if live else 0))

    lf._write(last_path, json.dumps({"verdicts": {r["video_id"]: r["verdict"] for r in rows},
                                     "videos": videos, "health": {"status": status, "day": hday, "runs": h_runs, "sent": h_sent},
                                     "stale_notified": stale_day, "released": released}, indent=2))
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
    ap.add_argument("--token-drive", default=str(lf.SCRIPTS / "token-drive-link.json"))
    args = ap.parse_args(argv)

    cfg = _read_json(Path(args.config), {})
    if not isinstance(cfg, dict):
        cfg = {}
    channel = args.channel      # chi --channel moi cho phep che do theo ten, config thi khong
    studio_id = args.studio_folder_id or cfg.get("studio_folder_id")
    if not channel and not studio_id:
        print("thieu --channel hoac --studio-folder-id", file=sys.stderr)
        return 2
    cfg["why"] = args.why
    drive = folders = None
    if not args.dry_run:
        from googleapiclient.discovery import build
        creds = lf.token_or_exit("analyse", Path(args.token_drive), Path(args.cache), cfg, send=notify)
        if creds is None:
            return 3
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
