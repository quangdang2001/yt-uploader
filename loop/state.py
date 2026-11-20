"""Dung VideoState cho tung video tu snapshot + action da xong (thuan, khong Drive, khong mang).

Quy tac (spec 5.7): chi dung dong period == since_published; bien the doi theo action done
co result.status == ok; hash doi ma khong co action nao thi la nguoi doi tay (manual).

Sau mot action ok, hash doi duoc xac nhan (2 snapshot lien tiep) la cua action neu xac nhan
roi vao cua so cho. Cua so dong o lan xac nhan dau tien, hoac khi DA qua ca 3 snapshot co hash
sau action VA 24 gio ke tu action (cai nao muon hon): nhip 2 gio can 24 gio de CDN kip,
nhip hang ngay can 3 snapshot. Dong khong co hash bi bo qua (khong dem, khong cat chuoi).
Hai hash moi KHAC NHAU trong cua so: cai dau la cua action, cai sau la nguoi doi tay (manual).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loop.rules import MANUAL_PAUSE_D, VideoState

ALTS = ("alt1", "alt2")
EXPECT_SNAPS = 3        # cua so "cho thay doi" sau action: so snapshot co hash ...
EXPECT_HOURS = 24     # ... va so gio; cua so dong khi DA qua ca hai
MAIN_BASELINE_IVS = 3   # main de so sanh REVERT: 3 khoang snapshot cuoi truoc lan swap dau


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
        try:
            parse_utc(s["captured_at_utc"])
        except ValueError:
            continue
        out.setdefault(s["video_id"], []).append(s)
    for rows in out.values():
        rows.sort(key=lambda r: parse_utc(r["captured_at_utc"]))
    return out


def ok_actions(actions: list) -> dict:
    """video_id -> action done thanh cong, sap theo result.at_utc."""
    out: dict = {}
    for a in actions:
        res = a.get("result") or {}
        if a.get("_state") != "done" or res.get("status") != "ok":
            continue
        try:
            at = parse_utc(res.get("at_utc"))
        except ValueError:
            at = None
        if at is None or not a.get("video_id") or not a.get("to_variant"):
            continue
        out.setdefault(a["video_id"], []).append((at, a))
    for lst in out.values():
        lst.sort(key=lambda p: p[0])
    return out


def _build_one(snaps: list, acts: list, feature_variants: set, pending: bool,
               now: datetime, release_at: datetime | None = None) -> VideoState:
    variant, before_last = "main", None
    swaps = reverts = 0
    last_swap = None
    impr_last = 0
    manual_until = None
    used: set = set()
    stats: dict = {}
    # prev_impr None = chua co moc (sau khi tha mot video manual ma dong do bi lam tron)
    prev_impr, prev_clicks, prev_hash = 0, 0.0, None
    main_ivs: list = []           # (impr, clicks) cua tung khoang snapshot khi main dang chay
    main_baseline = None
    main_frozen = False
    released = release_at is None
    ai = 0
    win_at = None                 # luc action ok cuoi cung neu cua so cho dang mo
    win_n = 0                     # so snapshot co hash tu luc do
    win_seen: set = set()         # cac hash moi khac nhau da thay (chua xac nhan) trong cua so
    cand_hash, cand_count, cand_first = None, 0, None

    def leave_main() -> None:
        nonlocal main_baseline, main_frozen
        if variant == "main" and not main_frozen:
            last = main_ivs[-MAIN_BASELINE_IVS:]
            n = sum(x[0] for x in last)
            c = sum(x[1] for x in last)
            main_baseline = (n, c / n if n > 0 else 0.0)
            main_frozen = True

    def apply(at, act) -> bool:
        nonlocal variant, before_last, swaps, reverts, last_swap, win_at, win_n, win_seen
        to = act["to_variant"]
        if to == variant:           # action trung lap: khong doi gi
            return False
        if to == "main" or (before_last is not None and to == before_last):
            reverts += 1
        elif to in ALTS:
            swaps += 1
        used.add(to)
        leave_main()
        before_last, variant, last_swap = variant, to, at
        win_at, win_n, win_seen = at, 0, set()
        return True

    for s in snaps:
        t = parse_utc(s["captured_at_utc"])
        h = s.get("thumb_hash") or None
        rounded = bool(s.get("impr_rounded"))
        impr = s["impressions"]
        clicks = (s.get("ctr") or 0.0) * impr
        switched = False
        while ai < len(acts) and acts[ai][0] <= t:
            switched = apply(*acts[ai]) or switched
            ai += 1
        if switched:
            impr_last = impr
        if not released and t >= release_at:
            released = True
            if variant == "manual":
                # Chu kenh tha video: tu luc nay coi nhu main, so lieu dem lai tu dau.
                variant, manual_until, stats = "main", None, {}
                main_ivs, main_baseline, main_frozen = [], None, False
                win_at, cand_hash, cand_count = None, None, 0
                prev_hash = h or prev_hash
                prev_impr, prev_clicks = (None, 0.0) if rounded else (impr, clicks)
                continue
        if h:
            in_window = win_at is not None
            if prev_hash is None or h == prev_hash:
                prev_hash, cand_hash, cand_count = prev_hash or h, None, 0
            else:
                # CDN tre: hash moi phai giu 2 snapshot lien tiep moi tinh la doi that
                if cand_hash == h:
                    cand_count += 1
                else:
                    cand_hash, cand_count, cand_first = h, 1, t
                if in_window:
                    win_seen.add(h)
                if cand_count >= 2:
                    if in_window and win_seen <= {h}:
                        win_at = None           # thay doi nay la cua action
                    else:
                        # ngoai cua so, hoac hai hash moi khac nhau trong cua so: cai sau la tay chu kenh
                        leave_main()
                        variant = "manual"
                        manual_until = cand_first + timedelta(days=MANUAL_PAUSE_D)
                        win_at = None
                    prev_hash, cand_hash, cand_count = h, None, 0
            if win_at is not None:
                win_n += 1
                if win_n >= EXPECT_SNAPS and t >= win_at + timedelta(hours=EXPECT_HOURS):
                    win_at = None
        # Dong lam tron ("1.2K") khong vao delta: khoang ke tiep tinh tu dong chinh xac truoc do.
        if rounded:
            continue
        if prev_impr is None:
            prev_impr, prev_clicks = impr, clicks
            continue
        # Khoang giua hai snapshot ma action roi vao giua duoc tinh cho bien the MOI, co chu y:
        # VPS thuc hien ngay sau mot lan chup, nen khoang do gan nhu toan bo la thumbnail moi.
        if impr > prev_impr:
            dn, dc = impr - prev_impr, max(0.0, clicks - prev_clicks)
            cur = stats.setdefault(variant, [0, 0.0])
            cur[0] += dn
            cur[1] += dc
            # Khoang dau tinh tu 0 luc dang (video moi: snapshot dau chi vai gio sau khi dang).
            if variant == "main" and not main_frozen:
                main_ivs.append((dn, dc))
        prev_impr, prev_clicks = impr, clicks
    while ai < len(acts):           # action done luon dung, ke ca at_utc sau `now` (lech dong ho)
        if apply(*acts[ai]):
            impr_last = prev_impr or 0
        ai += 1
    return VideoState(
        variant=variant, swaps=swaps, reverts=reverts, last_swap_utc=last_swap,
        impr_at_last_swap=impr_last,
        variant_stats={v: (n, c / n) for v, (n, c) in stats.items() if n > 0},
        manual_until=manual_until,
        alts_left=[v for v in ALTS if v in feature_variants and v not in used],
        pending_action=pending,
        main_baseline=main_baseline)


def build_states(snapshots: list, actions: list, features: list, now: datetime,
                 bases: dict | None = None, pending=(), released: dict | None = None) -> dict:
    """video_id -> VideoState. `bases` = video_id -> base (upload-history), `pending` = ten file cho,
    `released` = video_id -> luc chu kenh tha video manual (config release_manual)."""
    now = aware(now)
    bases = bases or {}
    released = released or {}
    fvars: dict = {}
    for f in features:
        fvars.setdefault(f.get("base"), set()).add(f.get("variant"))
    done = ok_actions(actions)
    out = {}
    for vid, snaps in since_published(snapshots).items():
        has_pending = any(str(n).startswith(vid + "__") for n in pending)
        out[vid] = _build_one(snaps, done.get(vid, []), fvars.get(bases.get(vid), set()),
                              has_pending, now, released.get(vid))
    return out
