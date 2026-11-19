"""Keo du lieu studio tu Drive ve cache cuc bo (chay tren may chu kenh).

    python -m loop.fetch --channel "Bare Skull" [--cache loop/cache] [--drive-root YouTube-Upload]

Cache:
    snapshots/<ngay>.csv   tai lai khi modifiedTime tren Drive doi (so voi .state.json)
    actions/done|failed/   JSON bat bien, chi tai neu chua co
    actions/pending.txt    ten file dang cho, mot ten mot dong
    thumbnail-features.csv, health.json
    .state.json            {ten: modifiedTime}
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import studio_store as ss  # noqa: E402

DEFAULT_CACHE = REPO / "loop" / "cache"
FEATURES_NAME = "thumbnail-features.csv"

INT_COLS = {"impressions", "views", "unique_viewers"}
FLOAT_COLS = {"age_hours", "ctr", "avd_s", "apv_pct", "ret_30s", "ret_60s"}
BOOL_COLS = {"test_running"}


def _write(path: Path, text: str) -> None:
    """Ghi nguyen tu: ghi <ten>.tmp roi os.replace, crash giua chung khong de file cut."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def _parses(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (OSError, ValueError):
        return False


def _load_state(cache: Path) -> dict:
    try:
        return json.loads((cache / ".state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fetch(drive, chan_folder: str, cache: Path) -> dict:
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    folders = ss.studio_folders(drive, chan_folder)
    state = _load_state(cache)

    snaps = 0
    for f in ss.list_files(drive, folders["snapshots"]):
        if not f["name"].endswith(".csv"):
            continue
        key = f"snapshots/{f['name']}"
        dest = cache / key
        if dest.exists() and state.get(key) == f["modifiedTime"]:
            continue
        _write(dest, ss.read_text(drive, f["id"]))
        state[key] = f["modifiedTime"]
        snaps += 1

    acts = 0
    for sub, fid in (("done", folders["actions_done"]), ("failed", folders["actions_failed"])):
        for f in ss.list_files(drive, fid):
            if not f["name"].endswith(".json"):
                continue
            dest = cache / "actions" / sub / f["name"]
            if dest.exists() and _parses(dest):
                continue
            _write(dest, ss.read_text(drive, f["id"]))
            acts += 1

    pending = [f["name"] for f in ss.list_files(drive, folders["actions_pending"])
               if f["name"].endswith(".json")]
    _write(cache / "actions" / "pending.txt", "".join(n + "\n" for n in pending))

    unreadable = sum(1 for sub in ("done", "failed")
                     for p in (cache / "actions" / sub).glob("*.json") if not _parses(p))

    health_status = "missing"
    root_files = {f["name"]: f for f in ss.list_files(drive, folders["studio"])}
    for name in (FEATURES_NAME, ss.HEALTH_NAME):
        f = root_files.get(name)
        if not f:
            continue
        dest = cache / name
        if not dest.exists() or state.get(name) != f["modifiedTime"]:
            _write(dest, ss.read_text(drive, f["id"]))
            state[name] = f["modifiedTime"]
    health = cache / ss.HEALTH_NAME
    if ss.HEALTH_NAME in root_files and health.exists():
        try:
            obj = json.loads(health.read_text(encoding="utf-8"))
            health_status = str(obj["status"]) if isinstance(obj, dict) and "status" in obj else "corrupt"
        except (ValueError, OSError):
            health_status = "corrupt"

    _write(cache / ".state.json", json.dumps(state, indent=2))
    return {"snapshots_downloaded": snaps, "actions_downloaded": acts,
            "actions_unreadable": unreadable,
            "pending": len(pending), "health_status": health_status}


def _typed(col: str, val: str):
    if val is None or val.strip() == "":
        return None
    v = val.strip()
    if col in BOOL_COLS:
        return v.lower() in ("true", "1")
    if col in INT_COLS:
        return int(float(v))
    if col in FLOAT_COLS or col.startswith("src_"):
        return float(v)
    return v


def load_snapshots(cache: Path) -> list:
    rows = []
    for p in sorted((Path(cache) / "snapshots").glob("*.csv")):
        with p.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append({k: _typed(k, v) for k, v in r.items() if k is not None})
    rows.sort(key=lambda r: r.get("captured_at_utc") or "")
    return rows


def load_actions(cache: Path) -> list:
    out = []
    for state in ("done", "failed"):
        for p in sorted((Path(cache) / "actions" / state).glob("*.json")):
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                log.warning("bo qua action khong doc duoc %s: %s", p, exc)
                continue
            if not isinstance(obj, dict):
                log.warning("bo qua action khong phai object: %s", p)
                continue
            obj["_state"] = state
            out.append(obj)
    out.sort(key=lambda a: a.get("requested_utc") or "")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Keo du lieu studio tu Drive ve cache.")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--drive-root", default="YouTube-Upload")
    ap.add_argument("--client-secret", default=str(SCRIPTS / "client_secret.json"))
    ap.add_argument("--token-drive", default=str(SCRIPTS / "token-drive-link.json"))
    args = ap.parse_args(argv)

    import youtube_uploader as yu
    from googleapiclient.discovery import build

    creds = yu.get_credentials(Path(args.client_secret), Path(args.token_drive),
                               yu.DRIVE_SCOPES, "Drive")
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    chan = yu.resolve_folders(drive, args.drive_root, args.channel)["channel"]
    res = fetch(drive, chan, Path(args.cache))
    print(json.dumps(res, indent=2))
    if res["health_status"] != "ok":
        print(f"CANH BAO: health = {res['health_status']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
