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


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate(req) -> str | None:
    """Tra ve chuoi mo ta loi, hoac None neu yeu cau hop le."""
    if not isinstance(req, dict):
        return "request is not a JSON object"
    for field in ("video_id", "base"):
        v = req.get(field)
        if not isinstance(v, str) or not v.strip():
            return f"missing {field}"
    if req.get("to_variant") not in VARIANTS:
        return f"unknown to_variant {req.get('to_variant')!r}"
    return None


def _target_name(req: dict) -> str:
    if req["to_variant"] == "main":
        return f"{req['base']}.jpg"
    return f"{req['base']}.{req['to_variant']}.jpg"


def _load(drive, f: dict) -> dict:
    """Doc mot file pending thanh dict de xu ly; file hong van tra ve de dua sang failed/."""
    try:
        req = json.loads(ss.read_text(drive, f["id"]))
        return {"file": f, "req": req, "parse_error": None}
    except Exception as exc:
        return {"file": f, "req": None, "parse_error": f"unreadable JSON: {exc}"}


def _sort_key(item: dict):
    req = item["req"]
    utc = req.get("requested_utc") if isinstance(req, dict) else None
    return (utc if isinstance(utc, str) else "", item["file"]["name"])


def _finish(drive, folders: dict, item: dict, status: str, detail: str) -> bool:
    """Ghi result va chuyen file. Tra ve True neu da chuyen duoc."""
    req = item["req"]
    obj = dict(req) if isinstance(req, dict) else {"raw_unparsed": True}
    obj["result"] = {"status": status, "at_utc": _utc(), "detail": detail}
    dest = folders["actions_done"] if status == "ok" else folders["actions_failed"]
    try:
        ss.move_json(drive, item["file"]["id"], folders["actions_pending"], dest, obj)
        return True
    except Exception as exc:
        log.error("Khong chuyen duoc %s sang %s: %s", item["file"]["name"],
                  "done" if status == "ok" else "failed", exc)
        return False


def run_actions(drive, youtube, chan_folder: str, done_folder: str,
                tmpdir: Path) -> tuple[int, int]:
    """Xu ly moi yeu cau trong actions/pending/, cu nhat truoc. Tra ve (ok, failed)."""
    folders = ss.studio_folders(drive, chan_folder)
    files = [f for f in ss.list_files(drive, folders["actions_pending"])
             if f["name"].lower().endswith(".json")]
    items = sorted((_load(drive, f) for f in files), key=_sort_key)
    ok = failed = 0

    for item in items:
        req = item["req"]
        err = item["parse_error"] or _validate(req)
        label = (f"Swap {req.get('video_id', '?')} -> {req.get('to_variant', '?')}"
                 if isinstance(req, dict) else f"Swap {item['file']['name']}")

        if err:
            _finish(drive, folders, item, "error", err)
            failed += 1
            yu.notify(f"{label}: FAILED {err}")
            continue

        name = _target_name(req)
        src_folder = done_folder if req["to_variant"] == "main" else folders["thumb_alts"]
        local = Path(tmpdir) / f"{req['video_id']}_{req['to_variant']}.jpg"
        try:
            found = yu.find_child(drive, name, src_folder)
            if not found:
                raise FileNotFoundError(f"{name} not found in Drive")
            yu.download_to(drive, found["id"], local, name)
            yu.set_thumbnail(youtube, req["video_id"], local)
        except Exception as exc:
            if yu.is_quota_error(exc):
                log.error("Het quota, de nguyen %d yeu cau trong pending/ va dung.",
                          len(items) - ok - failed)
                yu.notify(f"Swap {req['video_id']} -> {req['to_variant']}: quota exceeded, "
                          f"{len(items) - ok - failed} request(s) left in pending/")
                return ok, failed
            detail = f"{type(exc).__name__}: {exc}"
            _finish(drive, folders, item, "error", detail)
            failed += 1
            yu.notify(f"{label}: FAILED {detail}")
            continue
        finally:
            local.unlink(missing_ok=True)

        if _finish(drive, folders, item, "ok", f"set thumbnail from {name}"):
            ok += 1
            yu.notify(f"{label}: ok")
        else:
            # Thumbnail da doi nhung file van o pending/: lan sau se doi lai
            # cung anh (vo hai), nhung bao de chu kenh biet.
            failed += 1
            yu.notify(f"{label}: set OK but request file not moved, stays in pending/")
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


def _one_pass(args, drive, youtube, folders) -> tuple[int, int]:
    with tempfile.TemporaryDirectory(prefix="studioact-", dir=args.temp_dir or None) as td:
        return run_actions(drive, youtube, folders["channel"], folders["done"], Path(td))


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

        while True:
            try:
                _one_pass(args, drive, youtube, folders)
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
