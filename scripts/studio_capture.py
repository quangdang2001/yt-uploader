#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Doc so lieu YouTube Studio cua cac video cong khai va ghi len Drive (chay tren VPS).

Task Scheduler goi moi 30 phut:
    python studio_capture.py --channel "Bare Skull" --once

Mot lan chay:
    lock rieng -> jitter 0-10 phut -> bo qua neu uploader dang chay
    -> token Google (chi doc, refresh trong bo nho, khong bao gio mo trinh duyet OAuth)
    -> Data API: danh sach video cong khai (bo Shorts, rieng tu, khong cong khai, hen gio)
    -> video den han: < 72 h moi 2 gio, con lai moi ngay mot lan tu daily_hour (gio VPS);
       video doc loi cung cho het chu ky tinh tu lan THU cuoi
    -> Chrome that, profile da dang nhap: mo tab Reach + Engagement cua tung video,
       nghi 5-15 s giua cac video, doc JSON cua Studio
    -> studio/snapshots/<ngay UTC>.csv (mot lan ghi moi file ngay), studio/raw/,
       studio/health.json, studio/upload-history.csv
    -> chay bo thuc thi doi thumbnail (studio_actions.run_actions) mot lan.

Khong bao gio dien form dang nhap. Gap trang dang nhap / thu thach: dung ca lan chay,
FAIL auth, Telegram, exit 3, va khong mo lai Studio cho toi khi profile thay doi
(chu kenh da mo profile de dang nhap lai).

Exit: 0 ok (ke ca bo qua vi uploader dang chay), 1 loi API/Drive / watchdog 50 phut,
2 lock dang bi giu, 3 dang nhap / thu thach / token hong, 4 khong mo duoc trang hoac
trinh duyet.
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

from googleapiclient.errors import HttpError

import studio_actions as sa
from google_creds import TokenError, load_creds  # noqa: F401  (sc.TokenError / sc.load_creds)
import studio_browser as sb
import studio_channel as sch
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
VIDEOS_CACHE_TTL = timedelta(hours=6)  # danh sach video cong khai giu trong state 6 gio
ALL_PARSE_FAILED = "all reads failed twice - possible challenge page"
SHORTS_MAX_S = 60
JITTER_S = 600
PAUSE_S = (5, 15)
WATCHDOG_S = 50 * 60
CAPTURE_CAP_S = 35 * 60                # tinh tu luc khoi dong (gom jitter): sau moc nay khong mo video moi
WATCHDOG_MARGIN_S = 15 * 60            # watchdog = han doc + 15 phut (doi han bang capture_cap_min trong config)
MAX_CONSECUTIVE_FAILS = 3              # 3 video loi lien tiep -> dung, phan con lai de lan sau
LOCK_STALE_S = 6 * 3600                # giong yu.Lock
UPLOADER_MIDRUN_MIN_AGE_S = 60         # giua chung chi dung neu uploader da chay > 60 s
THUMB_TIMEOUT_S = 20
THUMB_URL = "https://i.ytimg.com/vi/{}/{}"
THUMB_RENDITION = "hqdefault.jpg"      # mot ban co dinh cho moi video, moi lan
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")

AUTH_MSG = "Studio sign-in needed on the VPS: run studio_spike.py --login"
TOKEN_MSG = "Google API token on the VPS needs re-auth: run the uploader once by hand"

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


def actions_lock_path(home, channel: str) -> Path:
    return Path(home) / f".studio-actions-{channel}.lock"   # cung ten voi studio_actions.main()


def uploader_lock_path(home, channel: str) -> Path:
    return Path(home) / f".uploader-{channel}.lock"   # ten trong youtube_uploader.main()


def state_path(home, channel: str) -> Path:
    return Path(home) / f".studio-state-{channel}.json"


def uploader_busy(home, channel: str, min_age: float = 0) -> bool:
    """Lock cua uploader con moi (< 6 h, cung nguong voi yu.Lock) va da giu it nhat min_age giay."""
    p = uploader_lock_path(home, channel)
    try:
        age = time.time() - p.stat().st_mtime
    except OSError:
        return False
    return min_age <= age < LOCK_STALE_S


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


# ---------------------------------------------------------------- lock dang giu
#
# Watchdog chay o thread khac va thoat bang os._exit, bo qua moi __exit__. Tren
# Windows khong xoa duoc file con fd mo (WinError 32), nen watchdog can chinh cac
# Lock dang giu de dong fd truoc khi xoa. run() va _run_actions dang ky o day.

_HELD: dict = {}                   # Path -> yu.Lock dang giu
_HELD_GUARD = threading.Lock()


def acquire_lock(path: Path):
    """yu.Lock da giu (va dang ky cho watchdog), hoac None neu tien trinh khac dang giu."""
    lock = yu.Lock(Path(path))
    try:
        lock.__enter__()
    except SystemExit:             # yu.Lock thoat 0 khi lock dang bi giu
        return None
    with _HELD_GUARD:
        _HELD[Path(path)] = lock
    return lock


def release_lock(lock) -> None:
    with _HELD_GUARD:
        _HELD.pop(Path(lock.path), None)
    lock.__exit__(None, None, None)


def error_kind(exc: BaseException) -> str:
    if yu.is_quota_error(exc):
        return "quota"
    if isinstance(exc, HttpError):
        return f"http{getattr(exc.resp, 'status', '?')}"
    return type(exc).__name__


# ---------------------------------------------------------------- trang thai


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for key in ("last", "last_attempt", "failed", "notified"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def last_by_video(state: dict) -> dict:
    """Lan doc gan nhat cua moi video: lan thanh cong, hoac lan thu loi neu muon hon."""
    out = {}
    for key in ("last", "last_attempt"):
        for vid, text in state.get(key, {}).items():
            d = parse_time(text)
            if d is not None and (vid not in out or d > out[vid]):
                out[vid] = d
    return out


def notify_daily(deps, state: dict, kind: str, message: str) -> None:
    """Gui Telegram toi da mot lan moi loai loi moi ngay UTC (nho trong state)."""
    day = utc_day(deps.now())
    if state["notified"].get(kind) == day:
        log.info("Da bao loi %s hom nay, khong bao lai.", kind)
        return
    state["notified"][kind] = day
    deps.notify(message)


# ---------------------------------------------------------------- token
# TokenError / load_creds song o google_creds.py, dung chung voi loop/ tren may chu kenh.


def connect(args):
    from googleapiclient.discovery import build
    dc = load_creds(Path(args.token_drive), yu.DRIVE_SCOPES, "Drive")
    drive = build("drive", "v3", credentials=dc, cache_discovery=False)
    try:
        yc = load_creds(Path(args.token_youtube), yu.YOUTUBE_SCOPES, "YouTube")
    except Exception as e:
        e.drive = drive            # de noi goi van ghi duoc health
        raise
    return drive, build("youtube", "v3", credentials=yc, cache_discovery=False)


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


def channel_due(state: dict, now: datetime, daily_hour: int, tz=None) -> bool:
    """Trang phan tich KENH: mot lan moi ngay, tu daily_hour (gio `tz`, mac dinh gio may)."""
    last = parse_time(state.get("channel_last"))
    if last is None:
        return True
    local = now.astimezone(tz) if tz is not None else now.astimezone()
    boundary = local.replace(hour=daily_hour, minute=0, second=0, microsecond=0)
    if local < boundary:
        boundary -= timedelta(days=1)
    return last < boundary


def channel_id(youtube, state: dict) -> str:
    cid = state.get("channel_id")
    if isinstance(cid, str) and cid.startswith("UC"):
        return cid
    items = youtube.channels().list(mine=True, part="id").execute().get("items") or []
    if not items:
        raise RuntimeError("Data API: khong thay kenh cua token")
    state["channel_id"] = items[0]["id"]
    return state["channel_id"]


def _capture_channel(args, deps, page, youtube, state, drive, sf, notes) -> str | None:
    """Doc cac tab phan tich kenh + Nghien cuu, ghi studio/channel/<ngay UTC>.json va raw/.
    Loi cua phan nay chi thanh mot ghi chu trong health; Blocked tra ve ly do (dung ca lan chay)."""
    try:
        cid = channel_id(youtube, state)
        bodies, errors = deps.capture_channel(page, cid)
    except sb.Blocked as e:
        return str(e)
    except Exception as e:
        notes.append(f"channel: {type(e).__name__}: {e}"[:300])
        log.warning("Doc trang phan tich kenh loi: %s", e)
        return None
    cap = deps.now()
    doc = sch.parse_channel(bodies, iso_z(cap))
    n = sch.card_count(doc)
    research = sum(len(v) for v in doc["research"].values())
    if errors:
        notes.append("channel tabs failed: " + ", ".join(sorted(errors)))
    log.info("Trang kenh: %d the, %d video Nghien cuu, %d tab loi", n, research, len(errors))
    if args.dry_run:
        print(f"CHANNEL {n} cards, {research} research videos, errors={sorted(errors)}")
        return None
    if n == 0:
        notes.append("channel: no cards read")
        return None
    day = utc_day(cap)
    ss.put_json(drive, sf["channel"], f"{day}.json", doc)
    try:
        ss.put_raw(drive, sf, day, "channel", stamp_z(cap), bodies)
    except Exception as e:
        log.warning("Khong ghi duoc raw/ cua trang kenh: %s", e)
    state["channel_last"] = iso_z(cap)
    notes.append(f"channel ok ({n} cards, {research} research videos)")
    return None


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
            old.append((last, v["video_id"]))
    # Video cu doc lau nhat truoc (chua doc bao gio dung dau): luot nao bi cat giua chung thi
    # luot sau bat dau tu nhung video bi bo, khong video nao doi mai.
    old.sort(key=lambda t: (t[0] is not None, t[0] or now))
    return fresh + [vid for _, vid in old]


# ---------------------------------------------------------------- thumbnail


fetch_url = sa.fetch_url


def thumb_hash(video_id: str, fetch=None) -> str | None:
    """Xem sa.thumb_hash: cung ham, cung URL (hqdefault + ?sqp=<giay unix>) cho capture va bo thuc thi."""
    return sa.thumb_hash(video_id, fetch)


# ---------------------------------------------------------------- phu thuoc


class Deps:
    """Moi thu cham vao mang, trinh duyet, dong ho; test thay bang ban gia."""

    def __init__(self, **kw):
        self.drive = None            # None -> connect(args)
        self.youtube = None
        self.connect = connect
        self.open_browser = sb.open_browser
        self.capture = sb.capture_tabs
        self.capture_channel = sb.capture_channel
        self.now = lambda: datetime.now(timezone.utc)
        self.sleep = time.sleep
        self.notify = yu.notify
        self.fetch_thumb = fetch_url
        self.run_actions = sa.run_actions
        self.home = yu.SCRIPT_DIR    # noi dat lock va state
        self.tz = None               # None -> gio may (gio VPS)
        self.uniform = random.uniform
        self.launched_at = None      # None -> luc run() bat dau
        for k, v in kw.items():
            if not hasattr(self, k):
                raise TypeError(f"unknown dep {k}")
            setattr(self, k, v)


# ---------------------------------------------------------------- mot lan chay


def run(args, deps: Deps) -> int:
    if deps.launched_at is None:
        deps.launched_at = deps.now()
    lock = acquire_lock(lock_path(deps.home, args.channel))
    if lock is None:
        log.info("studio_capture dang chay o tien trinh khac, thoat (exit 2).")
        return 2
    try:
        return _run_locked(args, deps)
    finally:
        release_lock(lock)


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

    def persist():
        if not args.dry_run:
            save_state(spath, state)

    held = state.get("auth_blocked")
    hold = False
    if held and not manual:
        hold = profile_stamp(args.profile) == held.get("profile_stamp")
        if not hold:
            log.info("Profile da doi tu luc FAIL auth (%s), thu doc lai.", held.get("at_utc"))

    if deps.drive is None or deps.youtube is None:
        try:
            deps.drive, deps.youtube = deps.connect(args)
        except TokenError as e:
            return _token_failed(args, deps, state, persist, e)
        except Exception as e:     # vd TransportError khi refresh: loi mang, khong phai token
            return _api_failed(args, deps, state, persist, getattr(e, "drive", None), None, e)
    drive, youtube = deps.drive, deps.youtube

    sf = None
    try:
        folders = yu.resolve_folders(drive, args.drive_root, ch)
        if not args.dry_run:
            sf = ss.studio_folders(drive, folders["channel"])
        if hold:
            log.warning("Van FAIL auth tu %s, profile chua doi: khong mo Studio. %s",
                        held.get("at_utc"), AUTH_MSG)
            if not args.dry_run:
                copy_history(drive, folders["channel"], sf)
                if not args.no_actions:
                    _run_actions(deps, drive, youtube, folders, ch, state, persist)
            return 3
        return _capture_phase(args, deps, state, persist, drive, youtube, folders, sf)
    except Exception as e:
        return _api_failed(args, deps, state, persist, drive, sf, e)


def _api_failed(args, deps, state, persist, drive, sf, e: BaseException) -> int:
    """Loi Data API / Drive / mang: FAIL api (neu co Drive), Telegram mot lan moi loai
    moi ngay UTC, exit 1."""
    kind = error_kind(e)
    detail = f"{kind}: {type(e).__name__}: {e}"
    log.error("Loi API/Drive (%s)", kind, exc_info=e)
    if args.dry_run:
        print(f"FAIL api: {detail}")
        return 1
    if drive is not None:
        try:
            if sf is None:
                folders = yu.resolve_folders(drive, args.drive_root, args.channel)
                sf = ss.studio_folders(drive, folders["channel"])
            ss.write_health(drive, sf, "FAIL api", detail[:2000], 0, 0)
        except Exception as e2:
            log.warning("Khong ghi duoc health: %s", e2)
    notify_daily(deps, state, f"api:{kind}",
                 f"[{args.channel}] Studio capture ERROR: {detail}"[:1000])
    persist()
    return 1


def _token_failed(args, deps, state, persist, e: TokenError) -> int:
    reason = str(e)
    log.error("Token Google khong dung duoc: %s", reason)
    if args.dry_run:
        print(f"FAIL auth: token: {reason}")
        return 3
    if e.drive is not None:
        try:
            folders = yu.resolve_folders(e.drive, args.drive_root, args.channel)
            sf = ss.studio_folders(e.drive, folders["channel"])
            ss.write_health(e.drive, sf, "FAIL auth", f"token: {reason}"[:2000], 0, 0)
        except Exception as e2:
            log.warning("Khong ghi duoc health: %s", e2)
    notify_daily(deps, state, "token", TOKEN_MSG)
    persist()
    return 3


def cached_videos(youtube, state: dict, now: datetime, need: str | None = None) -> list[dict]:
    """list_videos, giu trong state 6 gio (moi lan list ton ~9 don vi quota, chay moi 30 phut).
    Lam moi som hon chi khi lan doc can mot video ma cache khong biet (--only <id>)."""
    cache = state.get("videos_cache")
    at = parse_time(cache.get("at_utc")) if isinstance(cache, dict) else None
    known = None
    if at is not None and timedelta(0) <= now - at < VIDEOS_CACHE_TTL:
        try:
            known = [dict(v, public_at=parse_time(v.get("public_at"))) for v in cache["videos"]]
        except (KeyError, TypeError, AttributeError):
            known = None
    if known is not None and (need is None or any(v["video_id"] == need for v in known)):
        return known
    videos = list_videos(youtube)
    state["videos_cache"] = {"at_utc": iso_z(now), "videos": [
        dict(v, public_at=iso_z(v["public_at"]) if v.get("public_at") else None) for v in videos]}
    return videos


def _capture_phase(args, deps, state, persist, drive, youtube, folders, sf) -> int:
    ch = args.channel
    now = deps.now()
    videos = cached_videos(youtube, state, now, need=args.only)
    by_id = {v["video_id"]: v for v in videos}
    if args.only:
        if args.only not in by_id:
            log.error("%s khong co trong %d video cong khai moi nhat (hoac la Short), khong doc.",
                      args.only, UPLOADS_CAP)
            return 1
        due = [args.only]
    else:
        due = due_videos(videos, last_by_video(state), now, args.daily_hour, tz=deps.tz)
    want_channel = not args.only and channel_due(state, now, args.daily_hour, tz=deps.tz)
    log.info("%d video cong khai, %d den han, trang kenh %s", len(videos), len(due),
             "den han" if want_channel else "da doc hom nay")

    rows: list[dict] = []
    failures: dict[str, tuple[str, bool]] = {}   # video_id -> (ly do, la loi dieu huong)
    attempts: dict[str, str] = {}                # video_id -> luc thu (cho video loi)
    notes: list[str] = []
    blocked = None
    browser_error = None
    if due or want_channel:
        started = False
        try:
            with deps.open_browser(args.profile) as page:
                started = True
                if want_channel:
                    blocked = _capture_channel(args, deps, page, youtube, state, drive, sf, notes)
                    persist()
                if due and not blocked:
                    blocked = _capture_loop(args, deps, page, due, by_id, rows, failures,
                                            attempts, notes, sf)
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

    # Moi video doc trong lan nay deu loi parse (khong doc duoc dong nao, khong loi dieu huong):
    # hai lan chay lien tiep nhu vay thi coi nhu trang thu thach, giu nhu FAIL auth.
    # Chi tinh khi lan chay thu >= 2 video khac nhau va tat ca deu loi: mot video moi dang
    # (hien "—" ngay sau khi dang) loi mot minh khong bao gio gay giu.
    parse_hold = False
    if not blocked and not browser_error and not rows and len(failures) >= 2 and \
            not any(nav for _, nav in failures.values()):
        n = int(state.get("parse_fail_runs", 0)) + 1
        state["parse_fail_runs"] = n
        if n >= 2:
            blocked, parse_hold = ALL_PARSE_FAILED, True
    elif rows or (failures and len(failures) < 2):
        state.pop("parse_fail_runs", None)

    # So sach video loi TRUOC khi ghi dong: neu ghi Drive loi (raise ra _run_locked ->
    # FAIL api, state van duoc luu) thi lan thu va dem loi cua lan nay khong bi mat.
    reached = []
    for vid, (reason, _) in failures.items():
        n = int(state["failed"].get(vid, 0)) + 1
        state["failed"][vid] = n
        state["last_attempt"][vid] = attempts[vid]
        if n == 2:
            reached.append(f"{vid} ({reason[:120]})")
    if reached and not parse_hold:      # tin dang nhap ben duoi da du
        deps.notify(f"[{ch}] Studio read failed twice in a row for {len(reached)} video(s): "
                    + "; ".join(reached))

    # Ghi tung file ngay; moi file ghi xong moi cap nhat "last" cua cac dong trong do.
    _write_rows(drive, sf, rows, state)
    if browser_error:
        n = int(state.get("browser_failed", 0)) + 1
        state["browser_failed"] = n
        if n == 2:
            deps.notify(f"[{ch}] Studio capture: Chrome did not start twice in a row: "
                        f"{browser_error}")
    elif due or want_channel:
        state.pop("browser_failed", None)

    if blocked:
        state["auth_blocked"] = {"at_utc": iso_z(deps.now()), "detail": blocked,
                                 "profile_stamp": profile_stamp(args.profile)}
        state.pop("parse_fail_runs", None)
        persist()
        ss.write_health(drive, sf, "FAIL auth", blocked, len(rows), len(failures))
        deps.notify(AUTH_MSG)
        log.error("FAIL auth: %s", blocked)
        return 3
    if (due or want_channel) and not browser_error:
        state.pop("auth_blocked", None)
    persist()

    bad = [f"{v}: {r}" for v, (r, _) in failures.items()]
    if browser_error:
        bad.insert(0, browser_error)
    status = "FAIL read" if bad else "PASS"
    detail = "; ".join(bad + ([f"{len(rows)} read"] if due else []) + notes) or "nothing due"
    ss.write_health(drive, sf, status, detail[:2000], len(rows), len(failures))
    log.info("Health %s: %d doc, %d loi", status, len(rows), len(failures))

    copy_history(drive, folders["channel"], sf)
    if not args.no_actions:
        _run_actions(deps, drive, youtube, folders, ch, state, persist)

    return 4 if browser_error or any(nav for _, nav in failures.values()) else 0


def _page_gone(page, exc: BaseException) -> bool:
    """Trang / trinh duyet da dong: doc tiep chi ra loi hang loat."""
    if type(exc).__name__ == "TargetClosedError" or "has been closed" in str(exc):
        return True
    is_closed = getattr(page, "is_closed", None)
    if is_closed is None:
        return False
    try:
        return bool(is_closed())
    except Exception:
        return True


MISSING_CARD = "missing metric "


def _read_video(deps, page, vid, sf):
    """Chup + parse mot video. The bat buoc VANG HAN trong body (khong phai gia tri "—")
    thi chup lai mot lan ngay: 11-23 mot video da doc tot 3 ngay mat ca the Impressions
    trong mot lan chay. Lan hai van thieu thi ghi payload loi vao raw/ (FAIL_<video>) de
    xem lai, va them so body / so the vao ly do. Blocked, NavError van nem ra nhu cu."""
    for attempt in (1, 2):
        payloads = deps.capture(page, vid)
        try:
            return sp.parse_snapshot(payloads, vid), payloads
        except sp.ParseError as e:
            if not str(e).startswith(MISSING_CARD):
                raise
            diag = sp.card_census(payloads)
            if attempt == 1:
                log.warning("  %s: %s [%s], chup lai mot lan", vid, e, diag)
                continue
            if sf is not None:
                cap = deps.now()
                try:
                    ss.put_raw(deps.drive, sf, utc_day(cap), "FAIL_" + vid, stamp_z(cap), payloads)
                except Exception as we:
                    log.warning("  %s: khong ghi duoc raw/ cua lan loi (%s)", vid, we)
            raise sp.ParseError(f"{e} [twice; {diag}]") from e


def _capture_loop(args, deps, page, due, by_id, rows, failures, attempts, notes, sf) -> str | None:
    """Doc lan luot. Tra ve ly do neu gap dang nhap/thu thach (dung ngay), nguoc lai None."""
    streak = 0
    for i, vid in enumerate(due):
        left = len(due) - i
        if i:
            deps.sleep(deps.uniform(*PAUSE_S))
        cap_s = getattr(args, "capture_cap_s", CAPTURE_CAP_S)
        if (deps.now() - deps.launched_at).total_seconds() > cap_s:
            notes.append(f"time cap: {left} video(s) left for next run")
            log.warning("Qua %d phut tu luc khoi dong, de %d video cho lan sau.",
                        cap_s // 60, left)
            break
        if uploader_busy(deps.home, args.channel, min_age=UPLOADER_MIDRUN_MIN_AGE_S):
            notes.append(f"uploader running: {left} video(s) left for next run")
            log.info("Uploader dang chay, dung doc; %d video de lan sau.", left)
            break
        log.info("Doc %s (%d/%d)", vid, i + 1, len(due))
        tried = deps.now()
        try:
            row, payloads = _read_video(deps, page, vid, sf)
        except sb.Blocked as e:
            return str(e)
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"[:300]
            failures[vid] = (reason, not isinstance(e, sp.ParseError))
            attempts[vid] = iso_z(tried)
            log.warning("  %s: khong doc duoc, khong ghi dong: %s", vid, reason)
            streak += 1
            if _page_gone(page, e):
                notes.append(f"browser closed: {left - 1} video(s) left for next run")
                log.error("Trang/trinh duyet da dong, dung doc.")
                break
            if streak >= MAX_CONSECUTIVE_FAILS:
                notes.append(f"{streak} failures in a row: {left - 1} video(s) left for next run")
                log.error("%d video loi lien tiep, dung doc.", streak)
                break
            continue
        streak = 0
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


def _write_rows(drive, sf, rows: list[dict], state: dict) -> None:
    """Mot lan append cho moi file ngay (UTC theo captured_at_utc). Moi file ngay ghi
    xong thi cap nhat ngay state["last"] cua cac video trong do, de loi o file sau
    khong lam doc lai (va ghi trung) cac dong da len Drive."""
    by_day: dict[str, list] = {}
    for r in rows:
        by_day.setdefault(r["captured_at_utc"][:10], []).append(r)
    for day in sorted(by_day):
        ss.append_snapshot_rows(drive, sf, day, sp.SNAPSHOT_COLS, by_day[day])
        for r in by_day[day]:
            vid = r["video_id"]
            state["last"][vid] = r["captured_at_utc"]
            state["failed"].pop(vid, None)
            state["last_attempt"].pop(vid, None)


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


def _run_actions(deps, drive, youtube, folders, ch, state: dict, persist) -> None:
    """Bo thuc thi doi thumbnail. Loi o day khong doi exit code cua lan doc.

    Trang thai cua bo thuc thi (file da bao, ngay het quota, yeu cau da dat nhung chua chuyen)
    nam trong state["actions"] cua .studio-state-<kenh>.json, nen "bao mot lan" dung qua nhieu lan chay."""
    alock = acquire_lock(actions_lock_path(deps.home, ch))
    if alock is None:
        log.info("studio_actions dang chay rieng, bo qua lan nay.")
        return
    state["actions"] = sa.normalize_state(state.get("actions"))
    try:
        with tempfile.TemporaryDirectory(prefix="studiocap-") as td:
            ok, failed = deps.run_actions(drive, youtube, folders["channel"], folders["done"],
                                          Path(td), state=state["actions"])
        log.info("Swap requests: %d ok, %d failed", ok, failed)
    except Exception as e:
        log.exception("Bo thuc thi doi thumbnail loi")
        deps.notify(f"[{ch}] Swap executor error: {type(e).__name__}: {e}")
    finally:
        release_lock(alock)
        persist()


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
    # Chay moi ngay mot lan thi mot luot phai doc het kenh: dat capture_cap_min lon hon (vd 120).
    try:
        cap_min = int(cfg.get("capture_cap_min", CAPTURE_CAP_S // 60))
    except (TypeError, ValueError):
        p.error("capture_cap_min trong config phai la so phut 10-300")
    if not 10 <= cap_min <= 300:
        p.error("capture_cap_min trong config phai la so phut 10-300")
    a.capture_cap_s = cap_min * 60
    if a.only and not VIDEO_ID_RE.match(a.only):
        p.error(f"video id khong hop le: {a.only!r}")
    return a


def watchdog_fire(notify, seconds: int = WATCHDOG_S, exit_fn=os._exit) -> None:
    """Bao, go cac lock ma tien trinh nay dang giu (os._exit bo qua finally), thoat 1.

    Dong fd cua tung Lock truoc khi xoa file: Windows khong xoa duoc file con mo.
    Chi xoa file con ghi PID cua tien trinh nay."""
    log.error("WATCHDOG: qua %d phut, thoat.", seconds // 60)
    try:
        notify(f"Studio capture: watchdog after {seconds // 60} min, run killed")
    except Exception:
        pass
    me = str(os.getpid())
    with _HELD_GUARD:
        held = list(_HELD.items())
        _HELD.clear()
    for path, lock in held:
        try:
            mine = path.read_text().strip() == me
        except OSError:
            mine = False
        fd, lock.fd = lock.fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if not mine:
            log.warning("WATCHDOG: %s khong con ghi PID %s, khong xoa.", path.name, me)
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            log.error("WATCHDOG: khong xoa duoc %s: %s", path.name, e)
    exit_fn(1)


def start_watchdog(notify, seconds: int = WATCHDOG_S) -> threading.Timer:
    t = threading.Timer(seconds, watchdog_fire, args=(notify, seconds))
    t.daemon = True
    t.start()
    return t


def main(argv=None) -> int:
    launched = datetime.now(timezone.utc)
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
    deps = Deps(launched_at=launched)
    wd = start_watchdog(deps.notify, max(WATCHDOG_S, args.capture_cap_s + WATCHDOG_MARGIN_S))
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
