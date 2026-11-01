#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quet folder pending/ tren Google Drive va upload video len YouTube.

Chay tren VPS. Thay the hoan toan cho n8n.

Mot vong xu ly:
    pending/<id>.json  -> doc metadata
    pending/<id>.mp4   -> tai ve, upload resumable len YouTube
    pending/<id>.jpg   -> dat lam thumbnail (neu co)
    -> chuyen ca bo sang done/ (thanh cong) hoac failed/ (that bai)

Chay mot lan (dung cho Task Scheduler):
    python youtube_uploader.py --channel ten-kenh --once

Chay thuong tru, tu kiem tra moi 5 phut:
    python youtube_uploader.py --channel ten-kenh --watch 300

Lan dau chay se mo trinh duyet 2 lan de dang nhap: mot cho Drive, mot cho
YouTube. Hai tai khoan co the khac nhau.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import logging.handlers
import os
import random
import re
import socket
import ssl
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
except ImportError:
    sys.exit(
        "Thieu thu vien. Chay:\n"
        "    pip install -r " + str(Path(__file__).with_name("requirements.txt"))
    )

SCRIPT_DIR = Path(__file__).resolve().parent

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

FOLDER_MIME = "application/vnd.google-apps.folder"
SUBFOLDERS = ("pending", "done", "failed")
THUMB_EXTS = (".jpg", ".jpeg", ".png")

UPLOAD_CHUNK = 8 * 1024 * 1024
DOWNLOAD_CHUNK = 8 * 1024 * 1024
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 8
NETWORK_ERRORS = (
    socket.timeout,
    socket.error,
    ssl.SSLError,
    ConnectionError,
    TimeoutError,
    OSError,
)

# Quota ngay bi can: khong phai loi cua rieng file nao, va thu lai ngay lap tuc
# cung vo nghia. Khac han cac loi trong RETRY_STATUS.
QUOTA_REASONS = (b"quotaExceeded", b"uploadLimitExceeded", b"dailyLimitExceeded")

# Lam gi voi bo file sau khi video da len YouTube
ON_SUCCESS = ("move", "trash", "delete")

log = logging.getLogger("uploader")


class QuotaExceeded(Exception):
    """Het quota ngay. File phai duoc de nguyen trong pending/ cho lan sau."""


def is_quota_error(exc: BaseException) -> bool:
    if not isinstance(exc, HttpError) or exc.resp.status not in (403, 429):
        return False
    content = exc.content or b""
    if isinstance(content, str):
        content = content.encode("utf-8", "replace")
    return any(reason in content for reason in QUOTA_REASONS)


# ------------------------------------------------------------------- tien ich


def setup_logging(log_file: Path, verbose: bool) -> None:
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    log.addHandler(stream)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    rotating = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    rotating.setFormatter(fmt)
    log.addHandler(rotating)


def backoff(attempt: int) -> float:
    return min(2**attempt, 64) + random.random()


def rfc3339_utc(value: str) -> str:
    raw = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError(f"publishAt thieu mui gio: {value!r}")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def escape_q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class Lock:
    """Chan hai tien trinh cung chay (Task Scheduler goi chong len nhau)."""

    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            age = time.time() - self.path.stat().st_mtime
            if age < 6 * 3600:
                log.info("Mot tien trinh khac dang chay (%s), thoat.", self.path.name)
                sys.exit(0)
            log.warning("Lock cu %.0f gio, coi nhu tien trinh truoc da chet.", age / 3600)
            self.path.unlink(missing_ok=True)
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(self.fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)


class State:
    """Nho video da upload xong de khong upload lai khi script chet giua chung.

    Truong hop that: upload xong (ton 1600 quota) roi mat dien truoc khi kip
    chuyen file sang done/. Lan chay sau file van con trong pending/, neu khong
    co state thi se upload lan hai -> trung video.
    """

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, base: str) -> dict | None:
        return self.data.get(base)

    def set(self, base: str, **kw) -> None:
        self.data[base] = {**self.data.get(base, {}), **kw}
        self._flush()

    def clear(self, base: str) -> None:
        self.data.pop(base, None)
        self._flush()

    def _flush(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


# ----------------------------------------------------------------------- auth


def get_credentials(client_secret: Path, token: Path, scopes: list[str], label: str):
    creds = None
    if token.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token), scopes)
        except ValueError:
            log.warning("%s: token cu khong doc duoc, se dang nhap lai.", label)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            log.warning("%s: refresh token hong (%s), dang nhap lai.", label, exc)
            creds = None
    if not creds or not creds.valid:
        if not client_secret.exists():
            sys.exit(
                f"Khong thay {client_secret}.\n"
                "Tai OAuth client loai 'Desktop app' tu Google Cloud Console ve day."
            )
        log.info("%s: can dang nhap, dang mo trinh duyet...", label)
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes)
        creds = flow.run_local_server(port=0)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(token, 0o600)
    except OSError:
        pass
    return creds


# ---------------------------------------------------------------------- drive


def find_child(drive, name: str, parent: str, folder_only: bool = False):
    q = f"name = '{escape_q(name)}' and '{escape_q(parent)}' in parents and trashed = false"
    if folder_only:
        q += f" and mimeType = '{FOLDER_MIME}'"
    res = (
        drive.files()
        .list(q=q, fields="files(id,name,size)", pageSize=5,
              supportsAllDrives=True, includeItemsFromAllDrives=True)
        .execute()
    )
    files = res.get("files", [])
    return files[0] if files else None


def ensure_folder(drive, name: str, parent: str) -> str:
    found = find_child(drive, name, parent, folder_only=True)
    if found:
        return found["id"]
    created = (
        drive.files()
        .create(body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
                fields="id", supportsAllDrives=True)
        .execute()
    )
    log.info("Da tao folder %s", name)
    return created["id"]


def resolve_folders(drive, root_name: str, channel: str) -> dict:
    root = ensure_folder(drive, root_name, "root")
    chan = ensure_folder(drive, channel, root)
    return {n: ensure_folder(drive, n, chan) for n in SUBFOLDERS}


def list_pending_json(drive, pending: str) -> list[dict]:
    q = (f"'{escape_q(pending)}' in parents and trashed = false "
         f"and name contains '.json'")
    res = (
        drive.files()
        .list(q=q, fields="files(id,name,createdTime)", orderBy="createdTime",
              pageSize=100, supportsAllDrives=True, includeItemsFromAllDrives=True)
        .execute()
    )
    return [f for f in res.get("files", []) if f["name"].lower().endswith(".json")]


def find_media(drive, pending: str, base: str) -> tuple[dict | None, dict | None]:
    names = [f"{base}.mp4"] + [f"{base}{e}" for e in THUMB_EXTS]
    clauses = " or ".join(f"name = '{escape_q(n)}'" for n in names)
    q = f"'{escape_q(pending)}' in parents and trashed = false and ({clauses})"
    res = (
        drive.files()
        .list(q=q, fields="files(id,name,size)", pageSize=10,
              supportsAllDrives=True, includeItemsFromAllDrives=True)
        .execute()
    )
    video = thumb = None
    for f in res.get("files", []):
        low = f["name"].lower()
        if low == f"{base.lower()}.mp4":
            video = f
        elif low.endswith(THUMB_EXTS):
            thumb = f
    return video, thumb


def download_text(drive, file_id: str) -> str:
    return drive.files().get_media(fileId=file_id, supportsAllDrives=True).execute().decode("utf-8")


def download_to(drive, file_id: str, dest: Path, label: str) -> None:
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    attempt = 0
    shown = -1
    with dest.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=DOWNLOAD_CHUNK)
        done = False
        while not done:
            try:
                status, done = downloader.next_chunk(num_retries=3)
                attempt = 0
                if status:
                    pct = int(status.progress() * 100)
                    if pct >= shown + 10:
                        shown = pct
                        log.info("  tai %s: %d%%", label, pct)
            except HttpError as exc:
                if exc.resp.status not in RETRY_STATUS or attempt >= MAX_RETRIES:
                    raise
                attempt += 1
                wait = backoff(attempt)
                log.warning("  tai %s loi %s, thu lai sau %.0fs (%d/%d)",
                            label, exc.resp.status, wait, attempt, MAX_RETRIES)
                time.sleep(wait)
            except NETWORK_ERRORS as exc:
                if attempt >= MAX_RETRIES:
                    raise
                attempt += 1
                wait = backoff(attempt)
                log.warning("  tai %s loi mang (%s), thu lai sau %.0fs (%d/%d)",
                            label, exc, wait, attempt, MAX_RETRIES)
                time.sleep(wait)


def move_file(drive, file_id: str, src: str, dest: str) -> None:
    drive.files().update(
        fileId=file_id, addParents=dest, removeParents=src,
        fields="id,parents", supportsAllDrives=True,
    ).execute()


def trash_file(drive, file_id: str) -> None:
    """Vao thung rac Drive — con hoi phuc duoc 30 ngay."""
    drive.files().update(
        fileId=file_id, body={"trashed": True}, fields="id", supportsAllDrives=True,
    ).execute()


def delete_file(drive, file_id: str) -> None:
    """Xoa vinh vien, KHONG qua thung rac. Khong hoi phuc duoc."""
    drive.files().delete(fileId=file_id, supportsAllDrives=True).execute()


def apply_all(items: list[dict], action, verb: str) -> list[str]:
    """Lam `action` cho tung file, bo khoi `items` nhung cai da xong.

    Tra ve ten cac file that bai. Vi `items` bi thu nho lai, goi lan hai chi
    thu lai dung nhung file con ket, khong lam trung cai da xong.
    """
    stuck: list[str] = []
    for f in list(items):
        try:
            action(f)
            items.remove(f)
        except Exception as exc:
            stuck.append(f["name"])
            log.error("  khong %s duoc %s: %s", verb, f["name"], exc)
    return stuck


def move_all(drive, items: list[dict], src: str, dest: str) -> list[str]:
    return apply_all(items, lambda f: move_file(drive, f["id"], src, dest), "chuyen")


def finish_all(drive, items: list[dict], pending: str, done: str, mode: str) -> tuple[list[str], str]:
    """Doc bo file sau khi upload thanh cong, theo `mode`.

    Tra ve (danh sach file con ket, mo ta de ghi log).
    """
    if mode == "trash":
        return apply_all(items, lambda f: trash_file(drive, f["id"]), "bo vao thung rac"), "thung rac Drive"
    if mode == "delete":
        return apply_all(items, lambda f: delete_file(drive, f["id"]), "xoa"), "da xoa vinh vien"
    return move_all(drive, items, pending, done), "done/"


# -------------------------------------------------------------------- youtube


def build_body(meta: dict) -> dict:
    title = str(meta.get("title", "")).strip()
    if not title:
        raise ValueError('Thieu truong "title"')
    title = title[:100]

    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = tags.split(",")
    tags = [str(t).strip() for t in tags if str(t).strip()]
    while tags and len(",".join(tags)) > 480:
        tags.pop()

    privacy = meta.get("privacyStatus")
    if privacy not in ("private", "unlisted", "public"):
        privacy = "private"

    status = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": meta.get("madeForKids") is True,
    }
    if meta.get("publishAt"):
        # YouTube chi cho hen gio khi video dang private
        status["privacyStatus"] = "private"
        status["publishAt"] = rfc3339_utc(meta["publishAt"])
    if meta.get("containsSyntheticMedia") is True:
        status["containsSyntheticMedia"] = True

    return {
        "snippet": {
            "title": title,
            "description": str(meta.get("description", ""))[:5000],
            "tags": tags,
            "categoryId": str(meta.get("categoryId", "22")),
        },
        "status": status,
    }


def upload_video(youtube, path: Path, body: dict) -> str:
    """Upload resumable. Khac n8n: loi giua chung van resume dung cho dang do."""
    size = path.stat().st_size
    media = MediaFileUpload(str(path), chunksize=UPLOAD_CHUNK, resumable=True,
                            mimetype="video/mp4")
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media, notifySubscribers=False
    )

    response = None
    attempt = 0
    shown = -1
    log.info("  upload %s (%s bytes)", path.name, f"{size:,}")
    while response is None:
        try:
            status, response = request.next_chunk(num_retries=3)
            attempt = 0
            if status:
                pct = int(status.progress() * 100)
                if pct >= shown + 10:
                    shown = pct
                    log.info("  upload: %d%%", pct)
        except HttpError as exc:
            if exc.resp.status not in RETRY_STATUS or attempt >= MAX_RETRIES:
                raise
            attempt += 1
            wait = backoff(attempt)
            log.warning("  upload loi %s, thu lai sau %.0fs (%d/%d)",
                        exc.resp.status, wait, attempt, MAX_RETRIES)
            time.sleep(wait)
        except NETWORK_ERRORS as exc:
            if attempt >= MAX_RETRIES:
                raise
            attempt += 1
            wait = backoff(attempt)
            log.warning("  upload loi mang (%s), tiep tuc tu cho dang do sau %.0fs (%d/%d)",
                        exc, wait, attempt, MAX_RETRIES)
            time.sleep(wait)

    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube khong tra ve video id: {response}")
    return video_id


def set_thumbnail(youtube, video_id: str, path: Path) -> None:
    youtube.thumbnails().set(
        videoId=video_id, media_body=MediaFileUpload(str(path))
    ).execute()


# ----------------------------------------------------------------- thong bao


def notify(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        import urllib.parse
        import urllib.request

        data = urllib.parse.urlencode({"chat_id": chat, "text": message}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20
        ).read()
    except Exception as exc:
        log.warning("Khong gui duoc Telegram: %s", exc)


# ------------------------------------------------------------------ xu ly mot


def process_one(drive, youtube, folders: dict, item: dict, state: State,
                tmpdir: Path, channel: str, on_success: str = "move") -> bool:
    """Tra ve True neu upload thanh cong."""
    base = re.sub(r"\.json$", "", item["name"], flags=re.IGNORECASE)
    pending, done, failed = folders["pending"], folders["done"], folders["failed"]
    log.info("=" * 60)
    log.info("Xu ly %s", item["name"])

    moved: list[dict] = [{"id": item["id"], "name": item["name"]}]
    video_id = None
    local_video = local_thumb = None

    try:
        saved = state.get(base) or {}
        video_id = saved.get("videoId")

        meta = json.loads(download_text(drive, item["id"]))
        if not isinstance(meta, dict):
            raise ValueError("File JSON khong phai object")
        body = build_body(meta)

        video, thumb = find_media(drive, pending, base)
        if not video:
            raise FileNotFoundError(
                f"Khong tim thay {base}.mp4 trong pending/. "
                "Pipeline phai upload video TRUOC file .json."
            )
        moved.append({"id": video["id"], "name": video["name"]})
        if thumb:
            moved.append({"id": thumb["id"], "name": thumb["name"]})

        if video_id:
            log.warning("  %s da upload tu lan chay truoc (id %s), bo qua buoc upload.",
                        base, video_id)
        else:
            local_video = tmpdir / f"{base}.mp4"
            download_to(drive, video["id"], local_video, base + ".mp4")
            actual = local_video.stat().st_size
            expected = int(video.get("size") or 0)
            if expected and actual != expected:
                raise IOError(f"Tai thieu: {actual:,}/{expected:,} bytes")

            video_id = upload_video(youtube, local_video, body)
            state.set(base, videoId=video_id, at=datetime.now(timezone.utc).isoformat())
            log.info("  DA LEN YOUTUBE: https://youtu.be/%s", video_id)

        if thumb and not saved.get("thumbDone"):
            local_thumb = tmpdir / thumb["name"]
            download_to(drive, thumb["id"], local_thumb, thumb["name"])
            try:
                set_thumbnail(youtube, video_id, local_thumb)
                state.set(base, thumbDone=True)
                log.info("  da dat thumbnail")
            except HttpError as exc:
                # khong fatal: video da len roi
                log.error("  dat thumbnail that bai (%s). Kenh da xac minh SDT chua?",
                          exc.resp.status)

        total = len(moved)
        stuck, where_ok = finish_all(drive, moved, pending, done, on_success)
        if stuck:
            raise IOError(f"khong doc duoc bo file ({where_ok}): " + ", ".join(stuck))
        log.info("  %d file -> %s", total, where_ok)
        state.clear(base)

        notify(f"[{channel}] {base}\nDa len YouTube: https://youtu.be/{video_id}")
        return True

    except Exception as exc:
        # Het quota khi chua upload duoc gi: file khong he co loi, day sang
        # failed/ chi lam mat cong be tay ve. De nguyen trong pending/ va bao
        # ra ngoai de dung ca vong lap.
        if video_id is None and is_quota_error(exc):
            log.error("  het quota: de %s o lai pending/ cho lan chay sau.", item["name"])
            raise QuotaExceeded(str(exc)) from exc

        # Da upload roi thi KHONG day sang failed/: de o do se gay hieu lam la
        # video chua len, upload lai se trung video va ton them 1600 quota.
        dest = done if video_id else failed
        where = "done/" if video_id else "failed/"
        log.exception("  LOI: %s", exc)
        total = len(moved)
        stuck = move_all(drive, moved, pending, dest)
        log.info("  da chuyen %d/%d file sang %s", total - len(stuck), total, where)
        if video_id and stuck:
            # Giu state: con file trong pending/ nen lan sau script se nhat lai
            # bo nay, va no phai biet video DA len roi de khong upload lan hai.
            log.error("  giu state cho %s: %d file con trong pending/ (%s)",
                      base, len(stuck), ", ".join(stuck))
        elif video_id:
            state.clear(base)
        prefix = (f"Video DA len YouTube (id {video_id}) nhung buoc sau loi"
                  if video_id else "Upload that bai")
        notify(f"[{channel}] {base}\n{prefix}: {exc}\nFile da chuyen sang {where}")
        return False

    finally:
        for p in (local_video, local_thumb):
            if p and p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass


# ------------------------------------------------------------------- vong lap


def run_once(args, drive, youtube, folders, state, channel) -> int:
    items = list_pending_json(drive, folders["pending"])
    if not items:
        log.debug("pending/ khong co file .json nao.")
        return 0

    log.info("Tim thay %d file .json trong pending/", len(items))
    if args.dry_run:
        for i in items:
            log.info("  [dry-run] se xu ly %s", i["name"])
        return 0

    ok = 0
    with tempfile.TemporaryDirectory(prefix="ytup-", dir=args.temp_dir or None) as td:
        tmpdir = Path(td)
        batch = items[: args.max_per_run]
        for n, item in enumerate(batch):
            try:
                if process_one(drive, youtube, folders, item, state, tmpdir, channel,
                               on_success=args.on_success):
                    ok += 1
            except QuotaExceeded as exc:
                conlai = len(batch) - n
                log.error("Het quota hom nay, dung lai. %d file con o pending/.", conlai)
                notify(f"[{channel}] Het quota hom nay ({exc}).\n"
                       f"{conlai} file con nguyen trong pending/, se tu lam lai sau.")
                break
    return ok


def so_duong(value: str) -> int:
    """Chan `--max-per-run 0` (im lang khong lam gi) va so am.

    `items[:n]` voi n am cat tu dau den gan cuoi, nen -1 se xu ly gan het
    danh sach — nguoc han y nghia "gioi han".
    """
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"phai la so nguyen: {value!r}")
    if n < 1:
        raise argparse.ArgumentTypeError(f"phai >= 1, nhan duoc {n}")
    return n


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Upload video tu Google Drive len YouTube",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--channel", required=True, help="Ten kenh = ten folder trong Drive")
    p.add_argument("--drive-root", default="YouTube-Upload")
    p.add_argument("--client-secret", default=str(SCRIPT_DIR / "client_secret.json"))
    p.add_argument("--token-drive", default=str(SCRIPT_DIR / "token-drive.json"))
    p.add_argument("--token-youtube", default=str(SCRIPT_DIR / "token-youtube.json"))
    p.add_argument("--log-file", default=str(SCRIPT_DIR / "logs" / "uploader.log"))
    p.add_argument("--state-file", default=None, help="Mac dinh: .uploader-state-<kenh>.json")
    p.add_argument("--temp-dir", default=None, help="Noi tai video tam (mac dinh: temp he thong)")
    p.add_argument(
        "--max-per-run", type=so_duong, default=1, metavar="N",
        help="Toi da bao nhieu video moi lan chay (mac dinh 1). "
             "Video 1GB mat hang chuc phut, de 1 thi moi lan chay ngan, "
             "giu lock it, va hong giua chung thi mat it tien do",
    )
    p.add_argument(
        "--on-success", choices=ON_SUCCESS, default="move",
        help="Sau khi video len YouTube: move = chuyen sang done/ (mac dinh); "
             "trash = bo vao thung rac Drive, hoi phuc duoc 30 ngay; "
             "delete = xoa vinh vien, KHONG hoi phuc duoc",
    )
    p.add_argument("--once", action="store_true", help="Chay mot lan roi thoat")
    p.add_argument("--watch", type=int, metavar="GIAY", help="Chay lap, nghi N giay giua cac lan")
    p.add_argument("--dry-run", action="store_true", help="Chi liet ke, khong upload")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    if not args.once and not args.watch:
        args.once = True
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(Path(args.log_file), args.verbose)

    state_file = Path(args.state_file) if args.state_file else (
        SCRIPT_DIR / f".uploader-state-{args.channel}.json"
    )
    lock_file = SCRIPT_DIR / f".uploader-{args.channel}.lock"

    with Lock(lock_file):
        log.info("Khoi dong uploader cho kenh '%s'", args.channel)
        drive_creds = get_credentials(
            Path(args.client_secret), Path(args.token_drive), DRIVE_SCOPES, "Drive")
        yt_creds = get_credentials(
            Path(args.client_secret), Path(args.token_youtube), YOUTUBE_SCOPES, "YouTube")

        drive = build("drive", "v3", credentials=drive_creds, cache_discovery=False)
        youtube = build("youtube", "v3", credentials=yt_creds, cache_discovery=False)

        folders = resolve_folders(drive, args.drive_root, args.channel)
        state = State(state_file)

        if args.once:
            n = run_once(args, drive, youtube, folders, state, args.channel)
            log.info("Xong. %d video upload thanh cong.", n)
            return 0

        log.info("Che do watch: kiem tra moi %d giay. Ctrl+C de dung.", args.watch)
        while True:
            try:
                run_once(args, drive, youtube, folders, state, args.channel)
            except KeyboardInterrupt:
                log.info("Dung theo yeu cau.")
                return 0
            except Exception:
                log.exception("Loi ngoai vong xu ly, se thu lai o lan sau.")
            time.sleep(args.watch)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
