#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lay so lieu analytics cua kenh YouTube va ghi ra CSV tren Google Drive.

Dung YouTube Analytics API (reports.query) — quota rieng, KHONG dung vao
10.000 units/ngay cua Data API dang danh cho videos.insert.

Mac dinh lay cua so 28 ngay tinh den hom nay, ghi ra 2 file trong
YouTube-Upload/<kenh>/:

    analytics-daily.csv   mot dong moi ngay, GOP voi du lieu cu
                          -> lich su tich luy dan, mo bang Google Sheets
    analytics-videos.csv  mot dong moi video trong cua so, GHI DE moi lan

Chay mot lan (dung cho Task Scheduler):
    python fetch_analytics.py --channel ten-kenh

Doi cua so:
    python fetch_analytics.py --channel ten-kenh --days 90
    python fetch_analytics.py --channel ten-kenh --start-date 2026-01-01 \
                              --end-date 2026-01-31

LUU Y so lieu:
  - YouTube chot so cham 2-3 ngay, doanh thu con cham hon. Vi vay cua so 28
    ngay duoc lay lai TOAN BO moi lan chay va ghi de len dong cu cung ngay —
    so cua hom qua se tu duoc sua lai khi Google chot xong.
  - Hai cot "So luot hien thi hinh thu nho" va "Ty le nhap" trong YouTube
    Studio KHONG co trong bat ky API cong khai nao. Muon lay phai xuat tay
    tu Studio.

Lan dau chay se mo trinh duyet mot lan de cap quyen doc analytics. Token
Drive va YouTube cu van dung lai duoc, khong phai dang nhap lai.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    sys.exit(
        "Thieu thu vien. Chay:\n"
        "    pip install -r " + str(Path(__file__).with_name("requirements.txt"))
    )

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from youtube_uploader import (  # noqa: E402  (phai sau sys.path)
    Lock, _csv_media, download_text, ensure_folder, find_child,
    get_credentials, log, notify, setup_logging,
)

ANALYTICS_SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
]
# Doc title video: token-youtube.json san co da bao gom scope nay
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

DAILY_NAME = "analytics-daily.csv"
VIDEOS_NAME = "analytics-videos.csv"

# Metric khong can quyen xem tien
BASE_METRICS = [
    "views", "estimatedMinutesWatched", "averageViewDuration",
    "averageViewPercentage", "subscribersGained", "subscribersLost",
    "likes", "comments", "shares",
]
# Metric can scope yt-analytics-monetary + kenh da bat kiem tien
MONEY_METRICS = ["estimatedRevenue"]

# Ten cot tieng Viet cho de doc trong Google Sheets
VN = {
    "day": "ngay",
    "video": "video_id",
    "views": "luot_xem",
    "estimatedMinutesWatched": "phut_xem",
    "averageViewDuration": "tg_xem_tb_giay",
    "averageViewPercentage": "ty_le_xem_phan_tram",
    "subscribersGained": "dang_ky_moi",
    "subscribersLost": "dang_ky_huy",
    "likes": "thich",
    "comments": "binh_luan",
    "shares": "chia_se",
    "estimatedRevenue": "doanh_thu_usd",
}

# Toi da 200 dong cho mot query co dimensions=video (gioi han cua API)
VIDEO_ROWS = 200
# videos.list nhan toi da 50 id moi lan goi
TITLE_BATCH = 50

DEFAULT_DAYS = 28


# ------------------------------------------------------------------ tien ich


def date_range(days: int, end: str | None = None, start: str | None = None,
               today: date | None = None) -> tuple[str, str]:
    """Tra ve (startDate, endDate) dang YYYY-MM-DD.

    Mac dinh: cua so `days` ngay TINH CA hom nay, nen days=28 cho ra
    hom nay - 27 ... hom nay.
    """
    today = today or date.today()
    end_d = date.fromisoformat(end) if end else today
    if start:
        start_d = date.fromisoformat(start)
    else:
        if days < 1:
            raise ValueError(f"days phai >= 1, nhan duoc {days}")
        start_d = end_d - timedelta(days=days - 1)
    if start_d > end_d:
        raise ValueError(f"start-date ({start_d}) sau end-date ({end_d})")
    return start_d.isoformat(), end_d.isoformat()


def so_duong(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"phai la so nguyen: {value!r}")
    if n < 1:
        raise argparse.ArgumentTypeError(f"phai >= 1, nhan duoc {n}")
    return n


def ngay_hop_le(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"phai dang YYYY-MM-DD: {value!r}")


def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def is_forbidden(exc: BaseException) -> bool:
    return isinstance(exc, HttpError) and exc.resp.status == 403


# ------------------------------------------------------------------- bao cao


def parse_report(res: dict) -> list[dict]:
    """Doi {columnHeaders, rows} cua API thanh list dict theo ten cot."""
    cols = [h["name"] for h in res.get("columnHeaders", [])]
    return [dict(zip(cols, row)) for row in res.get("rows") or []]


def query(analytics, start: str, end: str, metrics: list[str],
          dimensions: str | None = None, sort: str | None = None,
          max_results: int | None = None) -> list[dict]:
    kw = dict(ids="channel==MINE", startDate=start, endDate=end,
              metrics=",".join(metrics))
    if dimensions:
        kw["dimensions"] = dimensions
    if sort:
        kw["sort"] = sort
    if max_results:
        kw["maxResults"] = max_results
    return parse_report(analytics.reports().query(**kw).execute())


def query_with_money(analytics, start: str, end: str, **kw) -> tuple[list[dict], list[str]]:
    """Thu lay ca doanh thu; kenh chua bat kiem tien thi API tra 403.

    Khong co nhanh nay thi mot kenh chua kiem tien se khong lay duoc GI CA,
    du cac metric con lai hoan toan doc duoc.
    """
    metrics = BASE_METRICS + MONEY_METRICS
    try:
        return query(analytics, start, end, metrics, **kw), metrics
    except HttpError as exc:
        if not is_forbidden(exc):
            raise
        log.warning("Khong doc duoc doanh thu (kenh chua bat kiem tien hoac "
                    "thieu quyen), bo qua cot doanh_thu_usd.")
        return query(analytics, start, end, BASE_METRICS, **kw), BASE_METRICS


def fetch_titles(youtube, video_ids: list[str]) -> dict[str, dict]:
    """Analytics API chi tra video id, title phai hoi Data API (1 unit/lan)."""
    out: dict[str, dict] = {}
    for batch in chunked(video_ids, TITLE_BATCH):
        res = youtube.videos().list(part="snippet", id=",".join(batch),
                                    maxResults=TITLE_BATCH).execute()
        for item in res.get("items", []):
            snip = item.get("snippet", {})
            out[item["id"]] = {
                "title": snip.get("title", ""),
                "ngay_dang": (snip.get("publishedAt") or "")[:10],
            }
    return out


# ----------------------------------------------------------------------- csv


def to_csv(cols: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for row in rows:
        w.writerow(["" if row.get(c) is None else row.get(c, "") for c in cols])
    return buf.getvalue()


def merge_daily(old_text: str, cols: list[str], rows: list[dict]) -> str:
    """Gop dong moi vao file cu, khoa theo cot dau (ngay).

    Ngay nao co trong lan chay nay thi GHI DE dong cu — YouTube chot so cham
    vai ngay nen so cu co the sai. Ngay cu hon cua so van duoc giu nguyen,
    nho vay lich su tich luy dan qua moi lan chay.
    """
    key = cols[0]
    merged: dict[str, dict] = {}

    reader = csv.reader(io.StringIO(old_text))
    try:
        header = next(reader)
    except StopIteration:
        header = []
    if header == cols:
        for row in reader:
            if row and row[0]:
                merged[row[0]] = dict(zip(cols, row))
    elif header:
        log.warning("%s co cot khac (%s), ghi lai tu dau.", DAILY_NAME, ",".join(header))

    for row in rows:
        merged[str(row[key])] = row

    return to_csv(cols, [merged[k] for k in sorted(merged)])


def put_csv(drive, folder: str, name: str, text: str) -> None:
    found = find_child(drive, name, folder)
    if found:
        drive.files().update(fileId=found["id"], media_body=_csv_media(text),
                             fields="id", supportsAllDrives=True).execute()
        log.info("  da cap nhat %s", name)
    else:
        drive.files().create(
            body={"name": name, "parents": [folder], "mimeType": "text/csv"},
            media_body=_csv_media(text), fields="id", supportsAllDrives=True,
        ).execute()
        log.info("  da tao %s", name)


# ------------------------------------------------------------------ xu ly mot


def build_daily(analytics, start: str, end: str) -> tuple[list[str], list[dict]]:
    rows, metrics = query_with_money(analytics, start, end,
                                     dimensions="day", sort="day")
    cols = [VN["day"]] + [VN[m] for m in metrics]
    out = [{VN["day"]: r["day"], **{VN[m]: r.get(m) for m in metrics}} for r in rows]
    return cols, out


def build_videos(analytics, youtube, start: str, end: str) -> tuple[list[str], list[dict]]:
    rows, metrics = query_with_money(analytics, start, end, dimensions="video",
                                     sort="-views", max_results=VIDEO_ROWS)
    ids = [r["video"] for r in rows if r.get("video")]
    try:
        info = fetch_titles(youtube, ids) if ids else {}
    except Exception as exc:
        # Thieu title thi bang van dung duoc, dung vi the ma bo ca bao cao
        log.warning("Khong lay duoc title video: %s", exc)
        info = {}

    cols = (["video_id", "title", "ngay_dang", "tu_ngay", "den_ngay"]
            + [VN[m] for m in metrics])
    out = []
    for r in rows:
        vid = r.get("video", "")
        meta = info.get(vid, {})
        out.append({
            "video_id": vid,
            "title": meta.get("title", ""),
            "ngay_dang": meta.get("ngay_dang", ""),
            "tu_ngay": start,
            "den_ngay": end,
            **{VN[m]: r.get(m) for m in metrics},
        })
    return cols, out


def run_once(args, drive, analytics, youtube) -> int:
    start, end = date_range(args.days, args.end_date, args.start_date)
    log.info("Lay analytics tu %s den %s", start, end)

    daily_cols, daily_rows = build_daily(analytics, start, end)
    video_cols, video_rows = build_videos(analytics, youtube, start, end)
    log.info("Duoc %d ngay, %d video.", len(daily_rows), len(video_rows))

    tong = sum(int(r.get(VN["views"]) or 0) for r in daily_rows)
    log.info("Tong luot xem trong ky: %s", f"{tong:,}".replace(",", "."))

    daily_text = to_csv(daily_cols, daily_rows)
    videos_text = to_csv(video_cols, video_rows)

    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / DAILY_NAME).write_text(daily_text, encoding="utf-8")
        (out / VIDEOS_NAME).write_text(videos_text, encoding="utf-8")
        log.info("  da ghi vao %s", out)

    if args.dry_run:
        log.info("--dry-run: khong ghi len Drive.")
        print(daily_text)
        return len(video_rows)

    root = ensure_folder(drive, args.drive_root, "root")
    chan = ensure_folder(drive, args.channel, root)

    found = find_child(drive, DAILY_NAME, chan)
    old = download_text(drive, found["id"]) if found else ""
    put_csv(drive, chan, DAILY_NAME, merge_daily(old or "", daily_cols, daily_rows))
    put_csv(drive, chan, VIDEOS_NAME, videos_text)
    return len(video_rows)


# ----------------------------------------------------------------------- main


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Lay analytics kenh YouTube, ghi CSV len Google Drive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--channel", required=True, help="Ten kenh = ten folder trong Drive")
    p.add_argument("--drive-root", default="YouTube-Upload")
    p.add_argument(
        "--days", type=so_duong, default=DEFAULT_DAYS, metavar="N",
        help=f"Cua so bao nhieu ngay tinh den hom nay (mac dinh {DEFAULT_DAYS}). "
             "Bi bo qua neu co --start-date",
    )
    p.add_argument("--start-date", type=ngay_hop_le, default=None, metavar="YYYY-MM-DD")
    p.add_argument("--end-date", type=ngay_hop_le, default=None, metavar="YYYY-MM-DD",
                   help="Mac dinh: hom nay")
    p.add_argument("--client-secret", default=str(SCRIPT_DIR / "client_secret.json"))
    p.add_argument("--token-drive", default=str(SCRIPT_DIR / "token-drive.json"))
    p.add_argument("--token-youtube", default=str(SCRIPT_DIR / "token-youtube.json"))
    p.add_argument("--token-analytics", default=str(SCRIPT_DIR / "token-analytics.json"))
    p.add_argument("--log-file", default=str(SCRIPT_DIR / "logs" / "analytics.log"))
    p.add_argument("--out-dir", default=None, help="Ghi them ban sao CSV vao thu muc nay")
    p.add_argument("--dry-run", action="store_true", help="In ra man hinh, khong ghi Drive")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(Path(args.log_file), args.verbose)

    with Lock(SCRIPT_DIR / f".analytics-{args.channel}.lock"):
        log.info("Lay analytics cho kenh '%s'", args.channel)
        try:
            an_creds = get_credentials(Path(args.client_secret),
                                       Path(args.token_analytics),
                                       ANALYTICS_SCOPES, "Analytics")
            yt_creds = get_credentials(Path(args.client_secret),
                                       Path(args.token_youtube),
                                       YOUTUBE_SCOPES, "YouTube")
            analytics = build("youtubeAnalytics", "v2", credentials=an_creds,
                              cache_discovery=False)
            youtube = build("youtube", "v3", credentials=yt_creds,
                            cache_discovery=False)
            drive = None
            if not args.dry_run:
                dr_creds = get_credentials(
                    Path(args.client_secret), Path(args.token_drive),
                    ["https://www.googleapis.com/auth/drive"], "Drive")
                drive = build("drive", "v3", credentials=dr_creds,
                              cache_discovery=False)

            n = run_once(args, drive, analytics, youtube)
        except Exception as exc:
            # Khong co cho nay thi script chet la im lang hoan toan: token het
            # han hay mat quyen Drive cung khong ai biet.
            log.exception("Lay analytics that bai")
            notify(f"[{args.channel}] Analytics LOI: {exc}")
            return 1

    log.info("Xong. %d video trong bao cao.", n)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
