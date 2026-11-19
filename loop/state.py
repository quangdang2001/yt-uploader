"""Dung VideoState cho tung video tu snapshot + action da xong (thuan, khong Drive, khong mang).

Quy tac (spec 5.7): chi dung dong period == since_published; bien the doi theo action done
co result.status == ok; hash doi ma khong co action nao thi la nguoi doi tay (manual).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loop.rules import MANUAL_PAUSE_D, VideoState

ALTS = ("alt1", "alt2")


def parse_utc(text) -> datetime | None:
    if not text:
        return None
    t = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def since_published(snapshots: list) -> dict:
    """video_id -> cac dong since_published co impressions, sap theo thoi gian."""
    out: dict = {}
    for s in snapshots:
        if s.get("period") != "since_published":
            continue
        if s.get("impressions") is None or s.get("ctr") is None or s.get("age_hours") is None:
            continue
        if not s.get("video_id") or not s.get("captured_at_utc"):
            continue
        out.setdefault(s["video_id"], []).append(s)
    for rows in out.values():
        rows.sort(key=lambda r: r["captured_at_utc"])
    return out


def ok_actions(actions: list) -> dict:
    """video_id -> action done thanh cong, sap theo result.at_utc."""
    out: dict = {}
    for a in actions:
        res = a.get("result") or {}
        if a.get("_state") != "done" or res.get("status") != "ok":
            continue
        at = parse_utc(res.get("at_utc"))
        if at is None or not a.get("video_id") or not a.get("to_variant"):
            continue
        out.setdefault(a["video_id"], []).append((at, a))
    for lst in out.values():
        lst.sort(key=lambda p: p[0])
    return out


def _build_one(snaps: list, acts: list, feature_variants: set, pending: bool,
               now: datetime) -> VideoState:
    variant, before_last = "main", None
    swaps = reverts = 0
    last_swap = None
    impr_last = 0
    manual_until = None
    used: set = set()
    stats: dict = {}
    prev_impr, prev_clicks, prev_hash = 0, 0.0, None
    ai = 0

    def apply(at, act):
        nonlocal variant, before_last, swaps, reverts, last_swap
        to = act["to_variant"]
        if to == "main" or (before_last is not None and to == before_last):
            reverts += 1
        elif to in ALTS:
            swaps += 1
        used.add(to)
        before_last, variant, last_swap = variant, to, at

    for s in snaps:
        t = parse_utc(s["captured_at_utc"])
        h = s.get("thumb_hash") or None
        switched = False
        while ai < len(acts) and acts[ai][0] <= t:
            apply(*acts[ai])
            ai += 1
            switched = True
        if switched:
            impr_last = s["impressions"]
        elif prev_hash and h and h != prev_hash:
            variant = "manual"
            manual_until = t + timedelta(days=MANUAL_PAUSE_D)
        impr = s["impressions"]
        clicks = (s.get("ctr") or 0.0) * impr
        if impr > prev_impr:
            cur = stats.setdefault(variant, [0, 0.0])
            cur[0] += impr - prev_impr
            cur[1] += max(0.0, clicks - prev_clicks)
        prev_impr, prev_clicks = impr, clicks
        prev_hash = h or prev_hash
    while ai < len(acts) and acts[ai][0] <= now:
        apply(*acts[ai])
        ai += 1
        impr_last = prev_impr
    return VideoState(
        variant=variant, swaps=swaps, reverts=reverts, last_swap_utc=last_swap,
        impr_at_last_swap=impr_last,
        variant_stats={v: (n, c / n) for v, (n, c) in stats.items() if n > 0},
        manual_until=manual_until,
        alts_left=[v for v in ALTS if v in feature_variants and v not in used],
        pending_action=pending)


def build_states(snapshots: list, actions: list, features: list, now: datetime,
                 bases: dict | None = None, pending=()) -> dict:
    """video_id -> VideoState. `bases` = video_id -> base (upload-history), `pending` = ten file cho."""
    now = aware(now)
    bases = bases or {}
    fvars: dict = {}
    for f in features:
        fvars.setdefault(f.get("base"), set()).add(f.get("variant"))
    done = ok_actions(actions)
    out = {}
    for vid, snaps in since_published(snapshots).items():
        has_pending = any(str(n).startswith(vid + "__") for n in pending)
        out[vid] = _build_one(snaps, done.get(vid, []), fvars.get(bases.get(vid), set()),
                              has_pending, now)
    return out
