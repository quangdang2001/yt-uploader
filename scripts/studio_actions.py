#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thuc thi yeu cau doi thumbnail tu Drive (studio/actions/pending/).

May chu kenh tha file JSON vao pending/; script nay dat thumbnail qua YouTube
Data API, ghi "result" vao file roi chuyen sang done/ hoac failed/.

    python studio_actions.py --channel "Bare Skull" [--once]

Quy tac an toan: doi thumbnail video dang cong khai, nen moi duong loi deu phai
dua file sang failed/ kem result.status="error". Rieng loi quota thi KHONG dong
vao file (quota hoi lai hom sau, khong phai loi cua yeu cau) va dung han.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from googleapiclient.discovery import build

import studio_store as ss
import youtube_uploader as yu

log = logging.getLogger("studio_actions")

VARIANTS = ("alt1", "alt2", "main")


STALE_H = 24     # yeu cau cu hon (theo requested_utc) khong bao gio duoc thuc hien


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(text) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        d = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def _validate(req) -> str | None:
    """Tra ve chuoi mo ta loi, hoac None neu yeu cau hop le.

    Cat khoang trang cua video_id va base ngay tai cho (sua req).
    """
    if not isinstance(req, dict):
        return "request is not a JSON object"
    for field in ("video_id", "base"):
        v = req.get(field)
        if not isinstance(v, str) or not v.strip():
            return f"missing {field}"
        req[field] = v.strip()
    if not VIDEO_ID_RE.match(req["video_id"]):
        return f"invalid video_id {req['video_id']!r}"
    base = req["base"]
    if "/" in base or "\\" in base or ".." in base:
        return f"invalid base {base!r}"
    if req.get("to_variant") not in VARIANTS:
        return f"unknown to_variant {req.get('to_variant')!r}"
    return None


def _target_name(req: dict) -> str:
    if req["to_variant"] == "main":
        return f"{req['base']}.jpg"
    return f"{req['base']}.{req['to_variant']}.jpg"


def _load(drive, f: dict) -> dict:
    """Doc mot file pending. Loi tai (mang, 5xx) tach rieng khoi loi noi dung JSON:
    loi tai de nguyen file o pending/, chi JSON hong/khong phai object moi sang failed/."""
    item = {"file": f, "req": None, "parse_error": None, "read_error": None,
            "raw_text": None}
    try:
        text = ss.read_text(drive, f["id"])
    except Exception as exc:
        item["read_error"] = exc
        return item
    item["raw_text"] = text
    try:
        req = json.loads(text)
    except json.JSONDecodeError as exc:
        item["parse_error"] = f"unreadable JSON: {exc}"
        return item
    if not isinstance(req, dict):
        item["parse_error"] = "request is not a JSON object"
        return item
    item["req"] = req
    return item


def _sort_key(item: dict):
    req = item["req"]
    utc = req.get("requested_utc") if isinstance(req, dict) else None
    return (utc if isinstance(utc, str) else "", item["file"]["name"])


def _finish(drive, folders: dict, item: dict, status: str, detail: str, at_utc: str | None = None) -> bool:
    """Ghi result va chuyen file. Tra ve True neu da chuyen duoc. at_utc: luc thumbnail THAT SU doi
    (mac dinh bay gio); may chu kenh dung no de nhan hash moi la cua action."""
    req = item["req"]
    obj = dict(req) if isinstance(req, dict) else {"raw_text": item["raw_text"]}
    obj["result"] = {"status": status, "at_utc": at_utc or _utc(), "detail": detail}
    dest = folders["actions_done"] if status == "ok" else folders["actions_failed"]
    try:
        ss.move_json(drive, item["file"]["id"], folders["actions_pending"], dest, obj)
        return True
    except Exception as exc:
        log.error("Khong chuyen duoc %s sang %s: %s", item["file"]["name"],
                  "done" if status == "ok" else "failed", exc)
        return False


def new_state() -> dict:
    """Trang thai giua cac lan chay, ghi duoc ra JSON (studio_capture giu no trong
    .studio-state-<kenh>.json): file id da bao, ngay het quota, va yeu cau da dat thumbnail
    nhung chua chuyen duoc sang done/ (file id -> luc dat)."""
    return {"notified": [], "quota_day": None, "applied": {}}


def normalize_state(state) -> dict:
    """Sua state doc tu file (hoac ban cu dung set) cho dung dang new_state()."""
    if not isinstance(state, dict):
        state = {}
    notified = state.get("notified")
    state["notified"] = sorted(notified) if isinstance(notified, (set, list, tuple)) else []
    if not isinstance(state.get("applied"), dict):
        state["applied"] = {}
    state.setdefault("quota_day", None)
    return state


def _notify_once(state: dict, key: str, message: str) -> None:
    if key in state["notified"]:
        return
    state["notified"].append(key)
    yu.notify(message)


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def run_actions(drive, youtube, chan_folder: str, done_folder: str,
                tmpdir: Path, state: dict | None = None) -> tuple[int, int]:
    """Xu ly moi yeu cau trong actions/pending/, cu nhat truoc. Tra ve (ok, failed).

    `state` (new_state()) duoc sua tai cho; ben goi giu no giua cac lan chay."""
    state = normalize_state(state if state is not None else new_state())
    folders = ss.studio_folders(drive, chan_folder)
    files = [f for f in ss.list_files(drive, folders["actions_pending"])
             if f["name"].lower().endswith(".json")]
    # chi nho file con o pending/: state khong lon dan
    live_ids = {f["id"] for f in files}
    state["notified"] = [k for k in state["notified"] if k in live_ids]
    state["applied"] = {k: v for k, v in state["applied"].items() if k in live_ids}
    items = sorted((_load(drive, f) for f in files), key=_sort_key)
    ok = failed = 0
    counter = 0

    def quota_stop(left: int, what: str) -> tuple[int, int]:
        log.error("Het quota, de nguyen %d yeu cau trong pending/ va dung.", left)
        if state["quota_day"] != _today():
            state["quota_day"] = _today()
            yu.notify(f"Swap requests: quota exceeded ({what}), "
                      f"{left} request(s) left in pending/")
        return ok, failed

    for idx, item in enumerate(items):
        fid = item["file"]["id"]
        left = len(items) - idx

        if item["read_error"] is not None:
            exc = item["read_error"]
            if yu.is_quota_error(exc):
                return quota_stop(left, "reading request")
            log.warning("Khong doc duoc %s, de nguyen o pending/: %s",
                        item["file"]["name"], exc)
            _notify_once(state, fid, f"Swap {item['file']['name']}: could not read "
                                     f"request ({exc}), left in pending/")
            continue

        req = item["req"]
        label = (f"Swap {req.get('video_id', '?')} -> {req.get('to_variant', '?')}"
                 if isinstance(req, dict) else f"Swap {item['file']['name']}")

        if fid in state["applied"] and isinstance(req, dict) and not _validate(req):
            # Thumbnail da dat o lan truoc, chi chuyen file chua duoc: chi thu chuyen lai,
            # khong dat lai (va giu luc dat that).
            if _finish(drive, folders, item, "ok", f"set thumbnail from {_target_name(req)}",
                       at_utc=state["applied"][fid]):
                state["applied"].pop(fid, None)
                ok += 1
                yu.notify(f"{label}: ok (request file moved on retry)")
            else:
                failed += 1
            continue

        err = item["parse_error"] or _validate(req)
        if not err:
            requested = _parse_utc(req.get("requested_utc"))
            if requested is None:
                err = "invalid requested_utc"
            elif (_now() - requested).total_seconds() > STALE_H * 3600:
                err = "stale request"

        if err:
            if _finish(drive, folders, item, "error", err):
                yu.notify(f"{label}: FAILED {err}")
            else:
                _notify_once(state, fid, f"{label}: FAILED {err}, file not moved")
            failed += 1
            continue

        name = _target_name(req)
        src_folder = done_folder if req["to_variant"] == "main" else folders["thumb_alts"]
        counter += 1
        local = Path(tmpdir) / f"swap{counter}.jpg"
        try:
            found = yu.find_child(drive, name, src_folder)
            if not found:
                raise FileNotFoundError(f"{name} not found in Drive")
            yu.download_to(drive, found["id"], local, name)
            yu.set_thumbnail(youtube, req["video_id"], local)
        except Exception as exc:
            if yu.is_quota_error(exc):
                return quota_stop(left, label)
            detail = f"{type(exc).__name__}: {exc}"
            if _finish(drive, folders, item, "error", detail):
                yu.notify(f"{label}: FAILED {detail}")
            else:
                _notify_once(state, fid, f"{label}: FAILED {detail}, file not moved")
            failed += 1
            continue
        finally:
            local.unlink(missing_ok=True)

        set_at = _utc()
        if _finish(drive, folders, item, "ok", f"set thumbnail from {name}", at_utc=set_at):
            ok += 1
            yu.notify(f"{label}: ok")
        else:
            # Thumbnail da doi nhung file van o pending/: nho file id (va luc dat), lan sau
            # chi thu chuyen file, khong dat lai thumbnail. Chi bao mot lan.
            state["applied"][fid] = set_at
            failed += 1
            _notify_once(state, fid,
                         f"{label}: set OK but request file not moved, stays in pending/")
    return ok, failed


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Thuc thi yeu cau doi thumbnail tu Drive")
    p.add_argument("--channel", required=True, help="Ten kenh = ten folder trong Drive")
    p.add_argument("--drive-root", default="YouTube-Upload")
    p.add_argument("--client-secret", default=str(yu.SCRIPT_DIR / "client_secret.json"))
    p.add_argument("--token-drive", default=str(yu.SCRIPT_DIR / "token-drive.json"))
    p.add_argument("--token-youtube", default=str(yu.SCRIPT_DIR / "token-youtube.json"))
    p.add_argument("--log-file", default=str(yu.SCRIPT_DIR / "logs" / "studio_actions.log"))
    p.add_argument("--temp-dir", default=None)
    p.add_argument("--once", action="store_true", help="Chay mot lan roi thoat")
    p.add_argument("--watch", type=int, metavar="GIAY", help="Chay lap, nghi N giay giua cac lan")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    if not args.once and not args.watch:
        args.once = True
    return args


def _one_pass(args, drive, youtube, folders, state=None) -> tuple[int, int]:
    if state is not None and state["quota_day"] == _today():
        return 0, 0   # da het quota hom nay, cho sang ngay UTC moi
    with tempfile.TemporaryDirectory(prefix="studioact-", dir=args.temp_dir or None) as td:
        return run_actions(drive, youtube, folders["channel"], folders["done"], Path(td), state)


def main(argv=None) -> int:
    args = parse_args(argv)
    yu.setup_logging(Path(args.log_file), args.verbose)
    lock_file = yu.SCRIPT_DIR / f".studio-actions-{args.channel}.lock"

    with yu.Lock(lock_file):
        drive_creds = yu.get_credentials(
            Path(args.client_secret), Path(args.token_drive), yu.DRIVE_SCOPES, "Drive")
        yt_creds = yu.get_credentials(
            Path(args.client_secret), Path(args.token_youtube), yu.YOUTUBE_SCOPES, "YouTube")
        drive = build("drive", "v3", credentials=drive_creds, cache_discovery=False)
        youtube = build("youtube", "v3", credentials=yt_creds, cache_discovery=False)
        folders = yu.resolve_folders(drive, args.drive_root, args.channel)

        if args.once:
            try:
                ok, failed = _one_pass(args, drive, youtube, folders)
            except Exception as exc:
                log.exception("studio_actions loi")
                yu.notify(f"[{args.channel}] studio_actions LOI: {exc}")
                return 1
            log.info("Xong. %d ok, %d failed.", ok, failed)
            return 0

        state = new_state()
        while True:
            try:
                _one_pass(args, drive, youtube, folders, state)
            except KeyboardInterrupt:
                return 0
            except Exception as exc:
                log.exception("Loi ngoai vong xu ly, se thu lai o lan sau.")
                yu.notify(f"[{args.channel}] studio_actions LOI: {exc}")
            time.sleep(args.watch)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
