"""Mot Telegram khi mot buoc cua loop/run.ps1 thoat khac 0 (chay tren may chu kenh).

    python -m loop.notify_fail <step> <code> [--cache loop/cache] [--config loop/config.json]

Toi da mot tin moi ngay (gio dia phuong) cho moi cap buoc + ma thoat. Trang thai nam trong
<cache>/.notified.json. fetch/analyse dung cung khoa "<step>:<code>" khi tu bao loi token,
nen run.ps1 goi lai sau do khong gui tin thu hai.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from loop import fetch as lf

NOTIFIED_NAME = ".notified.json"
DEFAULT_TZ = "Asia/Ho_Chi_Minh"
HINTS = {
    1: "an unexpected error; read the console output of loop/run.ps1",
    2: "loop/config.json is missing or has no studio_folder_id (docs/loop-pc.md)",
    3: "the Drive token on this PC (scripts/token-drive-link.json) needs a new sign-in (docs/loop-pc.md)",
}


def local_day(now: datetime, tz_name: str | None) -> str:
    return now.astimezone(ZoneInfo(tz_name or DEFAULT_TZ)).strftime("%Y-%m-%d")


def notify_once(cache: Path, key: str, message: str, now: datetime | None = None,
                tz_name: str | None = None, send=None) -> bool:
    """Gui `message` neu khoa `key` chua gui hom nay (gio dia phuong). Tra ve True neu da gui."""
    now = now or datetime.now(timezone.utc)
    day = local_day(now, tz_name)
    path = Path(cache) / NOTIFIED_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if data.get(key) == day:
        return False
    data[key] = day
    lf._write(path, json.dumps(data, indent=2))
    if send is None:
        import youtube_uploader as yu   # scripts/ da co trong sys.path qua loop.fetch
        send = yu.notify
    send(message)
    return True


def failure_message(step: str, code: int) -> str:
    hint = HINTS.get(code, "read the console output of loop/run.ps1")
    return "Studio loop on this PC: step %s failed with exit code %d - %s" % (step, code, hint)


def main(argv=None, send=None, now=None) -> int:
    ap = argparse.ArgumentParser(description="Telegram khi mot buoc cua loop/run.ps1 loi.")
    ap.add_argument("step")
    ap.add_argument("code", type=int)
    ap.add_argument("--cache", default=str(lf.DEFAULT_CACHE))
    ap.add_argument("--config", default=str(lf.REPO / "loop" / "config.json"))
    args = ap.parse_args(argv)
    try:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    tz_name = cfg.get("tz") if isinstance(cfg, dict) else None
    notify_once(Path(args.cache), "%s:%d" % (args.step, args.code), failure_message(args.step, args.code),
                now=now, tz_name=tz_name, send=send)
    return 0


if __name__ == "__main__":
    sys.exit(main())
