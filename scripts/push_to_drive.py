#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Day mot video len Google Drive de n8n tu dong upload len YouTube.

Thu tu upload la BAT BUOC: video -> thumbnail -> file .json.
Workflow n8n lang nghe file .json duoc tao trong folder pending/, nen file .json
phai la file cuoi cung. Neu dao thu tu, n8n se chay khi chua co video.

Vi du:
    python push_to_drive.py --channel ten-kenh --video D:/render/out/v001.mp4 \
        --thumb D:/render/out/v001.jpg --title "Tieu de video" \
        --description-file D:/render/out/v001.txt --tags "tag1,tag2" \
        --category 27 --publish-at 2026-11-05T12:00:00Z --synthetic

Hoac dua san mot file metadata JSON:
    python push_to_drive.py --channel ten-kenh --video v001.mp4 --meta v001.json
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
except ImportError:
    sys.exit(
        "Thieu thu vien. Chay:\n"
        "    pip install -r " + str(Path(__file__).with_name("requirements.txt"))
    )

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"
SUBFOLDERS = ("pending", "done", "failed")

# Chunk 8MB: du lon de nhanh, du nho de retry khong mat nhieu cong
CHUNK_SIZE = 8 * 1024 * 1024
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 6

SCRIPT_DIR = Path(__file__).resolve().parent


# --------------------------------------------------------------------- metadata


def slugify(value: str) -> str:
    """Giu lai ky tu an toan cho ten file va cho query cua Drive API."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")


def rfc3339_utc(value: str) -> str:
    """Kiem tra va chuan hoa publishAt ve dang 2026-11-05T12:00:00Z."""
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise SystemExit(
            f"--publish-at khong doc duoc: {value!r}. "
            "Dung dang 2026-11-05T12:00:00Z hoac 2026-11-05T19:00:00+07:00"
        )
    if dt.tzinfo is None:
        raise SystemExit(
            f"--publish-at thieu mui gio: {value!r}. Them Z (UTC) hoac +07:00"
        )
    dt = dt.astimezone(timezone.utc)
    if dt <= datetime.now(timezone.utc):
        raise SystemExit(f"--publish-at da qua: {dt.isoformat()}")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_metadata(args: argparse.Namespace) -> dict:
    meta: dict = {}

    if args.meta:
        meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise SystemExit(f"{args.meta} phai chua mot object JSON")

    if args.title:
        meta["title"] = args.title
    if args.description:
        meta["description"] = args.description
    if args.description_file:
        meta["description"] = Path(args.description_file).read_text(encoding="utf-8")
    if args.tags:
        meta["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.category:
        meta["categoryId"] = str(args.category)
    if args.privacy:
        meta["privacyStatus"] = args.privacy
    if args.publish_at:
        meta["publishAt"] = rfc3339_utc(args.publish_at)
    if args.synthetic:
        meta["containsSyntheticMedia"] = True
    if args.made_for_kids:
        meta["madeForKids"] = True

    meta.setdefault("categoryId", "22")
    meta.setdefault("privacyStatus", "private")
    meta.setdefault("tags", [])
    meta.setdefault("description", "")

    # --- kiem tra truoc khi upload 1GB, de khong phat hien loi o phut thu 20 ---
    title = str(meta.get("title", "")).strip()
    if not title:
        raise SystemExit("Thieu title: dung --title hoac dat trong file --meta")
    if len(title) > 100:
        raise SystemExit(f"Title dai {len(title)} ky tu, YouTube chi cho 100")
    meta["title"] = title

    if len(str(meta["description"])) > 5000:
        raise SystemExit("Description vuot 5000 ky tu")

    if meta["privacyStatus"] not in ("private", "unlisted", "public"):
        raise SystemExit(f"privacyStatus khong hop le: {meta['privacyStatus']}")

    tags = meta["tags"]
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
        meta["tags"] = tags
    if len(",".join(tags)) > 480:
        raise SystemExit("Tong do dai tags vuot 480 ky tu")

    if meta.get("publishAt"):
        # YouTube chi cho hen gio khi video dang private
        meta["privacyStatus"] = "private"
        meta["publishAt"] = rfc3339_utc(str(meta["publishAt"]))

    return meta


# ------------------------------------------------------------------------ drive


def get_service(client_secret: Path, token: Path):
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not client_secret.exists():
            raise SystemExit(
                f"Khong thay {client_secret}.\n"
                "Tai OAuth client (loai Desktop app) tu Google Cloud Console "
                "roi luu vao duong dan nay."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
        creds = flow.run_local_server(port=0)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(token, 0o600)
    except OSError:
        pass
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def escape_q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_child(svc, name: str, parent: str, folder_only: bool = False):
    q = (
        f"name = '{escape_q(name)}' and '{escape_q(parent)}' in parents "
        f"and trashed = false"
    )
    if folder_only:
        q += f" and mimeType = '{FOLDER_MIME}'"
    res = (
        svc.files()
        .list(
            q=q,
            fields="files(id,name,size)",
            pageSize=10,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = res.get("files", [])
    return files[0] if files else None


def ensure_folder(svc, name: str, parent: str) -> str:
    found = find_child(svc, name, parent, folder_only=True)
    if found:
        return found["id"]
    created = (
        svc.files()
        .create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
            fields="id",
            supportsAllDrives=True,
        )
        .execute()
    )
    print(f"  + tao folder {name}")
    return created["id"]


def upload_file(svc, path: Path, name: str, parent: str, mime: str) -> dict:
    size = path.stat().st_size
    media = MediaFileUpload(
        str(path), mimetype=mime, chunksize=CHUNK_SIZE, resumable=True
    )
    request = svc.files().create(
        body={"name": name, "parents": [parent]},
        media_body=media,
        fields="id,name,size",
        supportsAllDrives=True,
    )

    response = None
    attempt = 0
    shown = -1
    while response is None:
        try:
            status, response = request.next_chunk(num_retries=3)
            attempt = 0
            if status:
                pct = int(status.progress() * 100)
                if pct != shown:
                    shown = pct
                    print(f"\r  {name}: {pct}%", end="", flush=True)
        except HttpError as exc:
            if exc.resp.status not in RETRY_STATUS or attempt >= MAX_RETRIES:
                raise
            attempt += 1
            wait = min(2**attempt, 60) + random.random()
            print(f"\n  loi {exc.resp.status}, thu lai sau {wait:.0f}s "
                  f"({attempt}/{MAX_RETRIES})")
            time.sleep(wait)
        except (TimeoutError, ConnectionError, OSError) as exc:
            if attempt >= MAX_RETRIES:
                raise
            attempt += 1
            wait = min(2**attempt, 60) + random.random()
            print(f"\n  loi mang ({exc}), thu lai sau {wait:.0f}s "
                  f"({attempt}/{MAX_RETRIES})")
            time.sleep(wait)

    print(f"\r  {name}: 100% ({int(response.get('size', size)):,} bytes)")

    uploaded = int(response.get("size", 0))
    if uploaded and uploaded != size:
        raise SystemExit(
            f"Kich thuoc khong khop sau khi upload {name}: "
            f"Drive bao {uploaded}, file goc {size}"
        )
    return response


def upload_text(svc, content: str, name: str, parent: str, mime: str) -> dict:
    """Upload noi dung nho (file .json) bang mot request duy nhat."""
    from googleapiclient.http import MediaInMemoryUpload

    media = MediaInMemoryUpload(content.encode("utf-8"), mimetype=mime)
    return (
        svc.files()
        .create(
            body={"name": name, "parents": [parent]},
            media_body=media,
            fields="id,name",
            supportsAllDrives=True,
        )
        .execute()
    )


# ------------------------------------------------------------------------- main


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Day video + thumbnail + metadata len Google Drive cho n8n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--channel", required=True, help="Ten kenh = ten folder trong Drive")
    p.add_argument("--video", required=True, help="Duong dan file .mp4")
    p.add_argument("--thumb", help="Duong dan thumbnail .jpg/.png (tuy chon)")
    p.add_argument("--id", dest="base_id", help="Ten goc cua bo file (mac dinh: ten file video)")

    p.add_argument("--meta", help="File JSON metadata co san, se duoc merge")
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--description-file", help="Doc description tu file .txt")
    p.add_argument("--tags", help="Phan cach bang dau phay")
    p.add_argument("--category", help="categoryId, vi du 27 = Education, 22 = People & Blogs")
    p.add_argument("--privacy", choices=("private", "unlisted", "public"))
    p.add_argument("--publish-at", help="Hen gio, vi du 2026-11-05T12:00:00Z")
    p.add_argument("--synthetic", action="store_true", help="Khai bao noi dung AI")
    p.add_argument("--made-for-kids", action="store_true")

    p.add_argument("--drive-root", default="YouTube-Upload", help="Folder goc tren Drive")
    p.add_argument(
        "--client-secret",
        default=str(SCRIPT_DIR / "client_secret.json"),
        help="OAuth client loai Desktop app",
    )
    p.add_argument(
        "--token",
        default=str(SCRIPT_DIR / "token-drive.json"),
        help="Noi luu token sau lan dang nhap dau",
    )
    p.add_argument("--overwrite", action="store_true", help="Xoa bo file cu cung ten trong pending/")
    p.add_argument("--dry-run", action="store_true", help="Chi kiem tra, khong upload")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    video = Path(args.video)
    if not video.is_file():
        raise SystemExit(f"Khong thay file video: {video}")
    thumb = Path(args.thumb) if args.thumb else None
    if thumb and not thumb.is_file():
        raise SystemExit(f"Khong thay thumbnail: {thumb}")

    base = slugify(args.base_id or video.stem)
    if not base:
        raise SystemExit("Khong suy ra duoc ten goc, dung --id")
    if base != (args.base_id or video.stem):
        print(f"Ten goc da duoc chuan hoa thanh: {base}")

    meta = build_metadata(args)
    meta_text = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"

    print(f"Kenh      : {args.channel}")
    print(f"Ten goc   : {base}")
    print(f"Video     : {video} ({video.stat().st_size:,} bytes)")
    print(f"Thumbnail : {thumb if thumb else '(khong co)'}")
    print(f"Title     : {meta['title']}")
    print(f"Privacy   : {meta['privacyStatus']}"
          + (f", hen gio {meta['publishAt']}" if meta.get("publishAt") else ""))
    print(f"Synthetic : {bool(meta.get('containsSyntheticMedia'))}")

    if args.dry_run:
        print("\n--dry-run: metadata hop le, khong upload gi.")
        print(meta_text)
        return 0

    svc = get_service(Path(args.client_secret), Path(args.token))

    print("\nChuan bi folder tren Drive...")
    root_id = ensure_folder(svc, args.drive_root, "root")
    channel_id = ensure_folder(svc, args.channel, root_id)
    folders = {name: ensure_folder(svc, name, channel_id) for name in SUBFOLDERS}
    pending = folders["pending"]

    # Khong de hai bo file cung ten: n8n se khong biet lay file nao
    names = [f"{base}.mp4", f"{base}.json"]
    if thumb:
        names.append(f"{base}{thumb.suffix.lower()}")
    for name in names:
        existing = find_child(svc, name, pending)
        if not existing:
            continue
        if not args.overwrite:
            raise SystemExit(
                f"\n{name} da co trong pending/. Doi --id khac, "
                f"hoac them --overwrite de xoa ban cu."
            )
        svc.files().delete(fileId=existing["id"], supportsAllDrives=True).execute()
        print(f"  - da xoa ban cu {name}")

    print("\nUpload video...")
    upload_file(svc, video, f"{base}.mp4", pending, "video/mp4")

    if thumb:
        print("Upload thumbnail...")
        mime = mimetypes.guess_type(thumb.name)[0] or "image/jpeg"
        upload_file(svc, thumb, f"{base}{thumb.suffix.lower()}", pending, mime)

    # File .json PHAI di sau cung: day la tin hieu trigger cho n8n
    print("Upload metadata (file cuoi cung, se kich hoat n8n)...")
    upload_text(svc, meta_text, f"{base}.json", pending, "application/json")

    print(f"\nXong. n8n se nhan file trong vong 1 phut.")
    print(f"Sau khi upload thanh cong, ca bo file se nam trong "
          f"{args.drive_root}/{args.channel}/done/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
