#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bo cuc Drive cua studio/ — dung chung cho VPS (ghi) va may chu kenh (doc).

    YouTube-Upload/<kenh>/studio/
        snapshots/<ngay>.csv        # so lieu theo ngay, them dong
        raw/<ngay>/<video>__<utc>.json.gz
        thumb-alts/
        actions/pending|done|failed # yeu cau hanh dong va ket qua
        health.json                 # trang thai lan doc gan nhat

Chi dung lai cac ham Drive cua youtube_uploader.py, khong goi mang rieng.
"""
from __future__ import annotations

import gzip
import io
import json
from datetime import datetime, timezone

from googleapiclient.http import MediaIoBaseUpload

import youtube_uploader as yu

STUDIO_DIR = "studio"
HEALTH_NAME = "health.json"


def _media(data: bytes, mimetype: str):
    return MediaIoBaseUpload(io.BytesIO(data), mimetype=mimetype, resumable=False)


def studio_folders(drive, chan_folder: str) -> dict:
    """Tao (neu thieu) cay studio/ duoi folder kenh, tra ve id tung folder."""
    studio = yu.ensure_folder(drive, STUDIO_DIR, chan_folder)
    actions = yu.ensure_folder(drive, "actions", studio)
    return {
        "studio": studio,
        "snapshots": yu.ensure_folder(drive, "snapshots", studio),
        "raw": yu.ensure_folder(drive, "raw", studio),
        "thumb_alts": yu.ensure_folder(drive, "thumb-alts", studio),
        "actions_pending": yu.ensure_folder(drive, "pending", actions),
        "actions_done": yu.ensure_folder(drive, "done", actions),
        "actions_failed": yu.ensure_folder(drive, "failed", actions),
    }


def _upsert(drive, folder_id: str, name: str, data: bytes, mimetype: str) -> str:
    """Tao file moi hoac ghi de file cung ten trong folder. Tra ve id."""
    found = yu.find_child(drive, name, folder_id)
    if found:
        drive.files().update(
            fileId=found["id"], media_body=_media(data, mimetype),
            fields="id", supportsAllDrives=True,
        ).execute()
        return found["id"]
    created = drive.files().create(
        body={"name": name, "parents": [folder_id], "mimeType": mimetype},
        media_body=_media(data, mimetype), fields="id", supportsAllDrives=True,
    ).execute()
    return created["id"]


def append_snapshot_rows(drive, folders: dict, day: str, cols: tuple, rows: list) -> None:
    """Them dong vao snapshots/<day>.csv; ghi header o lan dau.

    Drive khong co API append: tai ve, noi them, day len lai (nhu append_history).
    Chi mot nguoi ghi (VPS), khong co khoa, giong append_history.
    """
    if not rows:
        return
    lines = "".join(
        ",".join(yu.csv_cell(r.get(c)) for c in cols) + "\n" for r in rows
    )
    name = f"{day}.csv"
    found = yu.find_child(drive, name, folders["snapshots"])
    if not found:
        text = ",".join(cols) + "\n" + lines
        drive.files().create(
            body={"name": name, "parents": [folders["snapshots"]], "mimeType": "text/csv"},
            media_body=yu._csv_media(text), fields="id", supportsAllDrives=True,
        ).execute()
        return
    old = yu.download_text(drive, found["id"]) or (",".join(cols) + "\n")
    if old and not old.endswith("\n"):
        old += "\n"
    drive.files().update(
        fileId=found["id"], media_body=yu._csv_media(old + lines),
        fields="id", supportsAllDrives=True,
    ).execute()


def put_raw(drive, folders: dict, day: str, video_id: str, utc: str, payloads: list) -> None:
    """Ghi mang payload JSON nen gzip vao raw/<day>/<video_id>__<utc>.json.gz."""
    day_folder = yu.ensure_folder(drive, day, folders["raw"])
    data = gzip.compress(json.dumps(payloads, ensure_ascii=False).encode("utf-8"))
    _upsert(drive, day_folder, f"{video_id}__{utc}.json.gz", data, "application/gzip")


def write_health(drive, folders: dict, status: str, detail: str, read: int, failed: int) -> None:
    body = {
        "status": status,
        "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "detail": detail,
        "videos_read": read,
        "videos_failed": failed,
    }
    put_json(drive, folders["studio"], HEALTH_NAME, body)


def list_files(drive, folder_id: str, since_name: str | None = None) -> list:
    """File khong o thung rac trong folder, xep theo ten; chi ten > since_name."""
    q = f"'{yu.escape_q(folder_id)}' in parents and trashed = false"
    out: list = []
    token = None
    while True:
        res = drive.files().list(
            q=q, fields="nextPageToken,files(id,name,modifiedTime)",
            orderBy="name", pageSize=100, pageToken=token,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        out.extend(res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            break
    if since_name is not None:
        out = [f for f in out if f["name"] > since_name]
    out.sort(key=lambda f: f["name"])
    return [{"id": f["id"], "name": f["name"], "modifiedTime": f.get("modifiedTime")}
            for f in out]


def read_text(drive, file_id: str) -> str:
    return yu.download_text(drive, file_id)


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def put_json(drive, folder_id: str, name: str, obj: dict) -> str:
    return _upsert(drive, folder_id, name, _json_bytes(obj), "application/json")


def put_text(drive, folder_id: str, name: str, text: str, mimetype: str = "text/csv") -> str:
    """Tao hoac ghi de file van ban (mac dinh CSV) trong folder. Tra ve id."""
    return _upsert(drive, folder_id, name, text.encode("utf-8"), mimetype)


def move_json(drive, file_id: str, src: str, dest: str, obj: dict) -> None:
    """Ghi lai noi dung bang `obj` roi chuyen file tu src sang dest, mot lan update."""
    drive.files().update(
        fileId=file_id, media_body=_media(_json_bytes(obj), "application/json"),
        addParents=dest, removeParents=src,
        fields="id,parents", supportsAllDrives=True,
    ).execute()
