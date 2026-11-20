#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Doc so lieu YouTube Studio cua cac video cong khai va ghi len Drive (chay tren VPS).

Task Scheduler goi moi 30 phut:
    python studio_capture.py --channel "Bare Skull" --once

Mot lan chay:
    lock rieng -> jitter 0-10 phut -> bo qua neu uploader dang chay
    -> Data API: danh sach video cong khai (bo Shorts, rieng tu, khong cong khai, hen gio)
    -> video den han: < 72 h moi 2 gio, con lai moi ngay mot lan tu daily_hour (gio VPS)
    -> Chrome that, profile da dang nhap: mo tab Reach + Engagement cua tung video,
       nghi 5-15 s giua cac video, doc JSON cua Studio
    -> studio/snapshots/<ngay UTC>.csv (mot lan ghi moi file ngay), studio/raw/,
       studio/health.json, studio/upload-history.csv
    -> chay bo thuc thi doi thumbnail (studio_actions.run_actions) mot lan.

Khong bao gio dien form dang nhap. Gap trang dang nhap / thu thach: dung ca lan chay,
FAIL auth, Telegram, exit 3, va khong mo lai Studio cho toi khi profile thay doi
(chu kenh da mo profile de dang nhap lai).

Exit: 0 ok (ke ca bo qua vi uploader dang chay), 1 loi / watchdog 50 phut,
2 lock dang bi giu, 3 dang nhap / thu thach, 4 khong mo duoc trang hoac trinh duyet.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import studio_actions as sa
import studio_browser as sb
import studio_parse as sp
import studio_store as ss
import youtube_uploader as yu

log = logging.getLogger("uploader.studio_capture")

DEFAULT_PROFILE = r"C:\yt\studio-profile"
DEFAULT_DAILY_HOUR = 6
CONFIG_NAME = "studio_config.json"

FRESH_HOURS = 72
FRESH_EVERY = timedelta(hours=2)
FRESH_SLACK = timedelta(minutes=10)    # bu cho jitter: 1 gio 50 van tinh la den han
UPLOADS_CAP = 200
SHORTS_MAX_S = 60
JITTER_S = 600
PAUSE_S = (5, 15)
WATCHDOG_S = 50 * 60
CAPTURE_BUDGET_S = 30 * 60             # sau moc nay khong mo video moi, de lai lan sau
LOCK_STALE_S = 6 * 3600                # giong yu.Lock
THUMB_TIMEOUT_S = 20
THUMB_URL = "https://i.ytimg.com/vi/{}/{}"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")

AUTH_MSG = "Studio sign-in needed on the VPS: run studio_spike.py --login"

_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


# ---------------------------------------------------------------- tien ich


def iso_z(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp_z(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_day(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d")


def parse_time(text) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        d = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_duration(text) -> int | None:
    """ISO 8601 cua contentDetails.duration ('PT1H2M3S') -> giay; khong doc duoc -> None."""
    m = _DURATION_RE.match(text or "") if isinstance(text, str) else None
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def lock_path(home, channel: str) -> Path:
    return Path(home) / f".studio-capture-{channel}.lock"


def uploader_lock_path(home, channel: str) -> Path:
    return Path(home) / f".uploader-{channel}.lock"   # ten trong youtube_uploader.main()


def state_path(home, channel: str) -> Path:
    return Path(home) / f".studio-state-{channel}.json"


def uploader_busy(home, channel: str) -> bool:
    """Lock cua uploader con moi (< 6 h, cung nguong voi yu.Lock) = dang upload."""
    p = uploader_lock_path(home, channel)
    try:
        return time.time() - p.stat().st_mtime < LOCK_STALE_S
    except OSError:
        return False


def profile_stamp(profile_dir) -> float:
    """mtime moi nhat cua file cookie / Local State trong profile Chrome (0 neu khong co).
    Doi khi co nguoi mo profile (vd chu kenh dang nhap lai)."""
    base = Path(profile_dir)
    best = 0.0
    for rel in ("Default/Network/Cookies", "Default/Cookies", "Local State"):
        try:
            best = max(best, (base / rel).stat().st_mtime)
        except OSError:
            pass
    return best


# ---------------------------------------------------------------- trang thai


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("last"), dict):
        data["last"] = {}
    if not isinstance(data.get("failed"), dict):
        data["failed"] = {}
    return data


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def last_by_video(state: dict) -> dict:
    out = {}
    for vid, text in state.get("last", {}).items():
        d = parse_time(text)
        if d is not None:
            out[vid] = d
    return out


# ---------------------------------------------------------------- Data API


def list_videos(youtube, cap: int = UPLOADS_CAP) -> list[dict]:
    """Video cong khai, khong phai Short, trong `cap` video moi nhat cua playlist uploads.

    Moi muc: video_id, privacy, public_at (snippet.publishedAt), duration_s, title."""
    ch = youtube.channels().list(mine=True, part="contentDetails").execute()
    items = ch.get("items") or []
    if not items:
        raise RuntimeError("channels.list(mine=True) khong tra ve kenh nao")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    ids: list[str] = []
    token = None
    while len(ids) < cap:
        res = youtube.playlistItems().list(
            playlistId=uploads, part="contentDetails", maxResults=50, pageToken=token,
        ).execute()
        for it in res.get("items", []):
            vid = (it.get("contentDetails") or {}).get("videoId")
            if vid and vid not in ids:
                ids.append(vid)
        token = res.get("nextPageToken")
        if not token:
            break
    ids = ids[:cap]

    by_id = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        res = youtube.videos().list(
            id=",".join(chunk), part="status,snippet,contentDetails", maxResults=50,
        ).execute()
        for r in res.get("items", []):
            by_id[r["id"]] = r

    out = []
    for vid in ids:
        r = by_id.get(vid)
        if r is None:
            continue
        v = {
            "video_id": vid,
            "privacy": (r.get("status") or {}).get("privacyStatus"),
            "public_at": parse_time((r.get("snippet") or {}).get("publishedAt")),
            "duration_s": parse_duration((r.get("contentDetails") or {}).get("duration")),
            "title": (r.get("snippet") or {}).get("title", ""),
        }
        if _eligible(v, None):
            out.append(v)
    return out


def _is_short(v: dict) -> bool:
    d = v.get("duration_s")
    return d is not None and d <= SHORTS_MAX_S


def _eligible(v: dict, now: datetime | None) -> bool:
    """Chi video cong khai co gio cong khai, khong phai Short (va da cong khai truoc now)."""
    if v.get("privacy") != "public" or v.get("public_at") is None or _is_short(v):
        return False
    return now is None or v["public_at"] <= now


def due_videos(videos: list[dict], last_by_video: dict, now: datetime, daily_hour: int,
               tz=None) -> list[str]:
    """Video can doc lan nay. Tuoi tinh tu luc cong khai (public_at).

    - < 72 h: den han neu chua doc, hoac lan doc truoc >= 2 gio (tru 10 phut jitter);
    - con lai: mot lan moi ngay, tu daily_hour theo gio `tz` (mac dinh gio may).
    Video moi truoc, roi video cu. Rieng tu / khong cong khai / hen gio / Short bi bo."""
    local = now.astimezone(tz) if tz is not None else now.astimezone()
    boundary = local.replace(hour=daily_hour, minute=0, second=0, microsecond=0)
    if local < boundary:
        boundary -= timedelta(days=1)
    fresh, old = [], []
    for v in videos:
        if not _eligible(v, now):
            continue
        last = last_by_video.get(v["video_id"])
        if now - v["public_at"] < timedelta(hours=FRESH_HOURS):
            if last is None or now - last >= FRESH_EVERY - FRESH_SLACK:
                fresh.append(v["video_id"])
        elif last is None or last < boundary:
            old.append(v["video_id"])
    return fresh + old


# ---------------------------------------------------------------- thumbnail


def fetch_url(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=THUMB_TIMEOUT_S) as r:
        return r.read()


def thumb_hash(video_id: str, fetch=None) -> str | None:
    """sha256 cua maxresdefault.jpg (404 -> hqdefault.jpg). Loi -> None, khong bao gio raise."""
    fetch = fetch or fetch_url
    for name in ("maxresdefault.jpg", "hqdefault.jpg"):
        url = THUMB_URL.format(video_id, name)
        try:
            data = fetch(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            log.warning("  thumb %s: HTTP %s, thumb_hash de trong", video_id, e.code)
            return None
        except Exception as e:
            log.warning("  thumb %s: %s, thumb_hash de trong", video_id, e)
            return None
        return hashlib.sha256(data).hexdigest()
    return None


# ---------------------------------------------------------------- phu thuoc


def connect(args):
    from googleapiclient.discovery import build
    dc = yu.get_credentials(Path(args.client_secret), Path(args.token_drive),
                            yu.DRIVE_SCOPES, "Drive")
    yc = yu.get_credentials(Path(args.client_secret), Path(args.token_youtube),
                            yu.YOUTUBE_SCOPES, "YouTube")
    return (build("drive", "v3", credentials=dc, cache_discovery=False),
            build("youtube", "v3", credentials=yc, cache_discovery=False))


class Deps:
    """Moi thu cham vao mang, trinh duyet, dong ho; test thay bang ban gia."""

    def __init__(self, **kw):
        self.drive = None            # None -> connect(args)
        self.youtube = None
        self.connect = connect
        self.open_browser = sb.open_browser
        self.capture = sb.capture_tabs
        self.now = lambda: datetime.now(timezone.utc)
        self.sleep = time.sleep
        self.notify = yu.notify
        self.fetch_thumb = fetch_url
        self.run_actions = sa.run_actions
        self.home = yu.SCRIPT_DIR    # noi dat lock va state
        self.tz = None               # None -> gio may (gio VPS)
        self.uniform = random.uniform
        for k, v in kw.items():
            if not hasattr(self, k):
                raise TypeError(f"unknown dep {k}")
            setattr(self, k, v)


# ---------------------------------------------------------------- mot lan chay


def run(args, deps: Deps) -> int:
    lock = yu.Lock(lock_path(deps.home, args.channel))
    try:
        lock.__enter__()
    except SystemExit:
        # yu.Lock thoat 0 khi lock dang bi giu; o day la exit 2, khong ghi gi
        log.info("studio_capture dang chay o tien trinh khac, thoat (exit 2).")
        return 2
    try:
        return _run_locked(args, deps)
    finally:
        lock.__exit__(None, None, None)


def _run_locked(args, deps: Deps) -> int:
    ch = args.channel
    if not args.no_jitter:
        wait = deps.uniform(0, JITTER_S)
        log.info("Jitter %.0f s", wait)
        deps.sleep(wait)
    if uploader_busy(deps.home, ch):
        log.info("Uploader dang chay (%s), bo qua lan doc nay; uu tien upload.",
                 uploader_lock_path(deps.home, ch).name)
        return 0

    manual = bool(args.only or args.dry_run)
    spath = state_path(deps.home, ch)
    state = load_state(spath)
    held = state.get("auth_blocked")
    if held and not manual:
        if profile_stamp(args.profile) == held.get("profile_stamp"):
            log.warning("Van FAIL auth tu %s, profile chua doi: khong mo Studio. %s",
                        held.get("at_utc"), AUTH_MSG)
            return 3
        log.info("Profile da doi tu luc FAIL auth (%s), thu doc lai.", held.get("at_utc"))

    if deps.drive is None or deps.youtube is None:
        deps.drive, deps.youtube = deps.connect(args)
    drive, youtube = deps.drive, deps.youtube
    folders = yu.resolve_folders(drive, args.drive_root, ch)
    sf = None if args.dry_run else ss.studio_folders(drive, folders["channel"])

    now = deps.now()
    videos = list_videos(youtube)
    by_id = {v["video_id"]: v for v in videos}
    if args.only:
        if args.only not in by_id:
            log.error("%s khong co trong %d video cong khai moi nhat (hoac la Short), khong doc.",
                      args.only, UPLOADS_CAP)
            return 1
        due = [args.only]
    else:
        due = due_videos(videos, last_by_video(state), now, args.daily_hour, tz=deps.tz)
    log.info("%d video cong khai, %d den han", len(videos), len(due))

    rows: list[dict] = []
    failures: dict[str, tuple[str, bool]] = {}   # video_id -> (ly do, la loi dieu huong)
    notes: list[str] = []
    blocked = None
    browser_error = None
    if due:
        started = False
        try:
            with deps.open_browser(args.profile) as page:
                started = True
                blocked = _capture_loop(args, deps, page, due, by_id, rows, failures, notes, sf)
        except Exception as e:
            if started:
                raise
            browser_error = f"browser did not start: {type(e).__name__}: {e}"
            log.error(browser_error)

    if args.dry_run:
        _print_rows(rows)
        for vid, (reason, _) in failures.items():
            print(f"FAILED {vid}: {reason}")
        if blocked:
            print(f"BLOCKED: {blocked}")
            return 3
        if browser_error:
            print(browser_error)
        return 4 if browser_error or any(nav for _, nav in failures.values()) else 0

    _write_rows(drive, sf, rows)          # loi -> raise, khong cap nhat state
    for r in rows:
        state["last"][r["video_id"]] = r["captured_at_utc"]
        state["failed"].pop(r["video_id"], None)
    for vid, (reason, _) in failures.items():
        n = int(state["failed"].get(vid, 0)) + 1
        state["failed"][vid] = n
        if n == 2:
            deps.notify(f"[{ch}] Studio read failed twice in a row for {vid}: {reason}")
    if browser_error:
        n = int(state.get("browser_failed", 0)) + 1
        state["browser_failed"] = n
        if n == 2:
            deps.notify(f"[{ch}] Studio capture: Chrome did not start twice in a row: "
                        f"{browser_error}")
    elif due:
        state.pop("browser_failed", None)

    if blocked:
        state["auth_blocked"] = {"at_utc": iso_z(deps.now()), "detail": blocked,
                                 "profile_stamp": profile_stamp(args.profile)}
        save_state(spath, state)
        ss.write_health(drive, sf, "FAIL auth", blocked, len(rows), len(failures))
        deps.notify(AUTH_MSG)
        log.error("FAIL auth: %s", blocked)
        return 3
    if due and not browser_error:
        state.pop("auth_blocked", None)
    save_state(spath, state)

    bad = [f"{v}: {r}" for v, (r, _) in failures.items()]
    if browser_error:
        bad.insert(0, browser_error)
    status = "FAIL read" if bad else "PASS"
    detail = "; ".join(bad + notes) or (f"{len(rows)} read" if due else "nothing due")
    ss.write_health(drive, sf, status, detail[:2000], len(rows), len(failures))
    log.info("Health %s: %d doc, %d loi", status, len(rows), len(failures))

    copy_history(drive, folders["channel"], sf)
    if not args.no_actions:
        _run_actions(deps, drive, youtube, folders, ch)

    return 4 if browser_error or any(nav for _, nav in failures.values()) else 0


def _capture_loop(args, deps, page, due, by_id, rows, failures, notes, sf) -> str | None:
    """Doc lan luot. Tra ve ly do neu gap dang nhap/thu thach (dung ngay), nguoc lai None."""
    t0 = deps.now()
    for i, vid in enumerate(due):
        if i:
            deps.sleep(deps.uniform(*PAUSE_S))
        if (deps.now() - t0).total_seconds() > CAPTURE_BUDGET_S:
            notes.append(f"time budget: {len(due) - i} video(s) left for next run")
            log.warning("Het %d phut, de %d video cho lan sau.", CAPTURE_BUDGET_S // 60, len(due) - i)
            break
        if uploader_busy(deps.home, args.channel):
            notes.append(f"uploader started: {len(due) - i} video(s) left for next run")
            log.info("Uploader vua chay, dung doc; %d video de lan sau.", len(due) - i)
            break
        log.info("Doc %s (%d/%d)", vid, i + 1, len(due))
        try:
            payloads = deps.capture(page, vid)
            row = sp.parse_snapshot(payloads, vid)
        except sb.Blocked as e:
            return str(e)
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"[:300]
            failures[vid] = (reason, not isinstance(e, sp.ParseError))
            log.warning("  %s: khong doc duoc, khong ghi dong: %s", vid, reason)
            continue
        cap = deps.now()
        row["captured_at_utc"] = iso_z(cap)
        row["age_hours"] = round((cap - by_id[vid]["public_at"]).total_seconds() / 3600, 2)
        row["thumb_hash"] = thumb_hash(vid, deps.fetch_thumb)
        rows.append(row)
        log.info("  %s: %s impr, ctr %s, %s views (%s)", vid, row["impressions"], row["ctr"],
                 row["views"], row["period"])
        if sf is not None:
            try:
                ss.put_raw(deps.drive, sf, utc_day(cap), vid, stamp_z(cap), payloads)
            except Exception as e:
                log.warning("  %s: khong ghi duoc raw/ (%s), dong van giu", vid, e)
    return None


def _write_rows(drive, sf, rows: list[dict]) -> None:
    """Mot lan append cho moi file ngay (UTC theo captured_at_utc)."""
    by_day: dict[str, list] = {}
    for r in rows:
        by_day.setdefault(r["captured_at_utc"][:10], []).append(r)
    for day, day_rows in by_day.items():
        ss.append_snapshot_rows(drive, sf, day, sp.SNAPSHOT_COLS, day_rows)


def _print_rows(rows: list[dict]) -> None:
    print(",".join(sp.SNAPSHOT_COLS))
    for r in rows:
        print(",".join(yu.csv_cell(r.get(c)) for c in sp.SNAPSHOT_COLS))


def copy_history(drive, chan_folder: str, sf: dict) -> None:
    """Chep upload-history.csv cua folder kenh vao studio/ (may chu kenh chi thay studio/)."""
    try:
        found = yu.find_child(drive, yu.HISTORY_NAME, chan_folder)
        if not found:
            log.info("Chua co %s trong folder kenh, bo qua.", yu.HISTORY_NAME)
            return
        ss.put_text(drive, sf["studio"], yu.HISTORY_NAME, yu.download_text(drive, found["id"]))
    except Exception as e:
        log.warning("Khong chep duoc %s vao studio/: %s", yu.HISTORY_NAME, e)


def _run_actions(deps, drive, youtube, folders, ch) -> None:
    """Bo thuc thi doi thumbnail. Loi o day khong doi exit code cua lan doc."""
    alock = yu.Lock(Path(deps.home) / f".studio-actions-{ch}.lock")
    try:
        alock.__enter__()
    except SystemExit:
        log.info("studio_actions dang chay rieng, bo qua lan nay.")
        return
    try:
        with tempfile.TemporaryDirectory(prefix="studiocap-") as td:
            ok, failed = deps.run_actions(drive, youtube, folders["channel"], folders["done"],
                                          Path(td))
        log.info("Swap requests: %d ok, %d failed", ok, failed)
    except Exception as e:
        log.exception("Bo thuc thi doi thumbnail loi")
        deps.notify(f"[{ch}] Swap executor error: {type(e).__name__}: {e}")
    finally:
        alock.__exit__(None, None, None)


# ---------------------------------------------------------------- CLI


def _load_config(path: str | None, parser) -> dict:
    if path is None:
        default = yu.SCRIPT_DIR / CONFIG_NAME
        if not default.exists():
            return {}
        path = str(default)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        parser.error(f"khong doc duoc config {path}: {e}")
    if not isinstance(data, dict):
        parser.error(f"config {path} phai la JSON object")
    return data


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Doc so lieu YouTube Studio len Drive (VPS)")
    p.add_argument("--channel", help="Ten kenh = ten folder trong Drive (hoac 'channel' trong config)")
    p.add_argument("--config", help=f"JSON: profile_dir, daily_hour, channel (mac dinh {CONFIG_NAME} neu co)")
    p.add_argument("--profile", help=f"Profile Chrome da dang nhap (mac dinh {DEFAULT_PROFILE})")
    p.add_argument("--drive-root", default="YouTube-Upload")
    p.add_argument("--client-secret", default=str(yu.SCRIPT_DIR / "client_secret.json"))
    p.add_argument("--token-drive", default=str(yu.SCRIPT_DIR / "token-drive.json"))
    p.add_argument("--token-youtube", default=str(yu.SCRIPT_DIR / "token-youtube.json"))
    p.add_argument("--log-file", default=str(yu.SCRIPT_DIR / "logs" / "studio_capture.log"))
    p.add_argument("--once", action="store_true", help="Chay mot lan roi thoat (luon nhu vay)")
    p.add_argument("--dry-run", action="store_true",
                   help="In dong ra man hinh, khong ghi Drive, khong ghi state")
    p.add_argument("--no-jitter", action="store_true", help="Bo cho ngau nhien 0-10 phut luc dau")
    p.add_argument("--no-actions", action="store_true", help="Khong chay bo thuc thi doi thumbnail")
    p.add_argument("--only", metavar="VIDEO_ID", help="Chi doc mot video, bo qua lich")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args(argv)
    cfg = _load_config(a.config, p)
    a.channel = a.channel or cfg.get("channel")
    if not a.channel:
        p.error("can --channel (hoac 'channel' trong config)")
    a.profile = a.profile or cfg.get("profile_dir") or DEFAULT_PROFILE
    try:
        a.daily_hour = int(cfg.get("daily_hour", DEFAULT_DAILY_HOUR))
    except (TypeError, ValueError):
        p.error("daily_hour trong config phai la so 0-23")
    if not 0 <= a.daily_hour <= 23:
        p.error("daily_hour trong config phai la so 0-23")
    if a.only and not VIDEO_ID_RE.match(a.only):
        p.error(f"video id khong hop le: {a.only!r}")
    return a


def start_watchdog(lock_file: Path, notify, seconds: int = WATCHDOG_S) -> threading.Timer:
    """Qua `seconds` thi bao, go lock cua minh (os._exit bo qua finally) va thoat 1."""
    def fire():
        log.error("WATCHDOG: qua %d phut, thoat.", seconds // 60)
        try:
            notify(f"Studio capture: watchdog after {seconds // 60} min, run killed")
        except Exception:
            pass
        try:
            if lock_file.read_text().strip() == str(os.getpid()):
                lock_file.unlink()
        except OSError:
            pass
        os._exit(1)

    t = threading.Timer(seconds, fire)
    t.daemon = True
    t.start()
    return t


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args(argv)
    yu.setup_logging(Path(args.log_file), args.verbose)
    alog = logging.getLogger("studio_actions")
    alog.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    for h in yu.log.handlers:
        alog.addHandler(h)
    deps = Deps()
    wd = start_watchdog(lock_path(deps.home, args.channel), deps.notify)
    try:
        return run(args, deps)
    except Exception as e:
        log.exception("studio_capture loi")
        deps.notify(f"[{args.channel}] Studio capture ERROR: {type(e).__name__}: {e}")
        return 1
    finally:
        wd.cancel()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
