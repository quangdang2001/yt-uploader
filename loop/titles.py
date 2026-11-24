"""Doi tieu de theo so (chu kenh 2026-11-24: "build the same way to update the title also"). Thuan, chi stdlib.

Duong rieng, song song voi thumbnail (loop/state.py, loop/rules.py khong doi):

  - Tieu de du phong: cache/title-alts/<base>.json = {"base", "main", "alt1"?, "alt2"?} (push_to_pending ghi len
    studio/title-alts/, loop.fetch keo ve).
  - Yeu cau: file JSON trong actions/pending/ co kind = "title" va KHONG co to_variant (executor cu doc to_variant
    la doi thumbnail; loop.state.ok_actions bo qua dong khong co to_variant nen duong thumbnail khong thay no).
  - Thu tu: thumbnail truoc. Chi doi tieu de khi phan quyet thumbnail la CHANGE_TITLE "no swap left" (CTR ro rang
    duoi vach, het thumbnail du phong hoac khong co), video 36-168 gio tuoi, va da >= 24 gio ke tu lan doi gan nhat
    (thumbnail hay tieu de). 36 gio: trai tim cham khuon tieu de bang CTR 18-36 gio, khong duoc doi truoc do.
  - Lan doi thu hai va lenh hoan lai can >= 2.000 luot hien thi tren tieu de dang chay. Do 2026-11-24: 3/7 video co
    them 2.000 luot sau gio 36 (1/5 video CTR thap) -> hai nhanh nay hiem khi chay.
  - Hoan lai: tieu de dang chay ro rang kem tieu de truoc no (khoang Wilson rong khong chong nhau), toi da 1 lan,
    sau do khong doi nua.
  - Chu kenh doi tay: VPS tu choi yeu cau voi "title changed since request" -> video do thoi han duong tieu de.

Sau mot lan doi tieu de, analyse khong ghi yeu cau thumbnail cho video do nua: so sau do la cua tieu de moi.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from loop.rules import MIN_IMPR, Verdict, wide_interval
from loop.state import parse_utc

log = logging.getLogger("loop.titles")

KIND = "title"
ALTS = ("alt1", "alt2")
VARIANTS = ("main",) + ALTS
ALTS_DIR = "title-alts"
MIN_AGE_H = 36          # sau cua so cham CTR 24 gio cua trai tim (18-36 gio)
MAX_AGE_H = 168         # nhu thumbnail: chi trong 7 ngay dau
MIN_GAP_H = 24
MAX_SWAPS = 2
MAX_REVERTS = 1
FAIL_RETRY_H = 24
MANUAL_MARK = "title changed since request"
NO_SWAP_LEFT = "no swap left"


@dataclass
class TitleState:
    variant: str = "main"
    before: str | None = None                 # tieu de ngay truoc tieu de dang chay (dich cua lenh hoan lai)
    swaps: int = 0
    reverts: int = 0
    last_change_utc: datetime | None = None
    stats: dict = field(default_factory=dict)     # variant -> (luot hien thi, ctr) tinh tu cac khoang snapshot
    alts_left: list = field(default_factory=list)
    manual: bool = False

    @property
    def touched(self) -> bool:
        return self.swaps + self.reverts > 0


def load_alts(cache: Path) -> dict:
    """base -> {"main": .., "alt1": .., "alt2": ..}. File hong hoac thieu main thi bo qua, khong raise."""
    out = {}
    for p in sorted((Path(cache) / ALTS_DIR).glob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("bo qua title-alts khong doc duoc %s: %s", p, exc)
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("main"), str) or not doc["main"].strip():
            continue
        base = str(doc.get("base") or p.stem)
        out[base] = {v: " ".join(doc[v].split()) for v in VARIANTS if isinstance(doc.get(v), str) and doc[v].strip()}
    return out


def is_title(action: dict) -> bool:
    return isinstance(action, dict) and action.get("kind") == KIND


def ok_actions(actions: list) -> dict:
    """video_id -> [(luc dat, action)] cac lan doi tieu de THANH CONG, cu truoc."""
    out: dict = {}
    for a in actions:
        res = a.get("result") or {}
        if not is_title(a) or a.get("_state") != "done" or res.get("status") != "ok":
            continue
        try:
            at = parse_utc(res.get("at_utc"))
        except ValueError:
            at = None
        if at is None or not a.get("video_id") or a.get("to_title_variant") not in VARIANTS:
            continue
        out.setdefault(a["video_id"], []).append((at, a))
    for lst in out.values():
        lst.sort(key=lambda p: p[0])
    return out


def manual_videos(actions: list) -> set:
    """Video ma VPS tu choi vi tieu de live khac tieu de mong doi: chu kenh da doi tay."""
    return {a.get("video_id") for a in actions
            if is_title(a) and a.get("_state") == "failed"
            and MANUAL_MARK in str((a.get("result") or {}).get("detail") or "")}


def failed_recently(actions: list, vid: str, variant: str, now: datetime) -> bool:
    for a in actions:
        if not is_title(a) or a.get("_state") != "failed" or a.get("video_id") != vid:
            continue
        if a.get("to_title_variant") != variant:
            continue
        try:
            at = parse_utc((a.get("result") or {}).get("at_utc"))
        except ValueError:
            at = None
        if at is not None and now - at < timedelta(hours=FAIL_RETRY_H):
            return True
    return False


def build_state(snaps: list, acts: list, alts: dict | None, thumb_change_at: datetime | None = None,
                manual: bool = False) -> TitleState:
    """`snaps`: dong since_published cua mot video (cu truoc). `acts`: ok_actions()[video]. `alts`: load_alts()[base].
    `thumb_change_at`: luc doi thumbnail gan nhat - cac khoang truoc do khong tinh cho tieu de main (so do con mang
    thumbnail cu, khong lam moc so sanh duoc)."""
    st = TitleState(manual=manual)
    acc: dict = {}
    used: set = set()
    ai = 0
    prev_impr, prev_clicks = 0, 0.0

    def apply(at, act) -> None:
        to = act["to_title_variant"]
        if to == st.variant:
            return
        if to == "main" or (st.before is not None and to == st.before):
            st.reverts += 1
        else:
            st.swaps += 1
        used.add(to)
        st.before, st.variant, st.last_change_utc = st.variant, to, at

    for s in snaps:
        t = parse_utc(s["captured_at_utc"])
        while ai < len(acts) and acts[ai][0] <= t:
            apply(*acts[ai])
            ai += 1
        if s.get("impr_rounded"):
            continue
        impr = s["impressions"]
        clicks = (s.get("ctr") or 0.0) * impr
        if impr > prev_impr:
            counts = not (st.variant == "main" and not st.touched
                          and thumb_change_at is not None and t <= thumb_change_at)
            if counts:
                cur = acc.setdefault(st.variant, [0, 0.0])
                cur[0] += impr - prev_impr
                cur[1] += max(0.0, clicks - prev_clicks)
        prev_impr, prev_clicks = impr, clicks
    while ai < len(acts):
        apply(*acts[ai])
        ai += 1
    st.stats = {v: (n, c / n) for v, (n, c) in acc.items() if n > 0}
    st.alts_left = [v for v in ALTS if v in (alts or {}) and v not in used]
    return st


def _revert_reason(st: TitleState) -> str | None:
    if st.reverts >= MAX_REVERTS or st.before is None or st.variant == st.before:
        return None
    cur, old = st.stats.get(st.variant), st.stats.get(st.before)
    if cur is None or old is None or cur[0] < MIN_IMPR or old[0] < MIN_IMPR:
        return None
    if wide_interval(cur[1], cur[0])[1] < wide_interval(old[1], old[0])[0]:
        return "title %s CTR %.1f%% at %s impr is below title %s CTR %.1f%% at %s impr" % (
            st.variant, cur[1] * 100, format(cur[0], ","), st.before, old[1] * 100, format(old[0], ","))
    return None


def decide(snap: dict, st: TitleState, verdict: Verdict, reason: str, now: datetime,
           last_thumb_change: datetime | None, pending: bool, test_running: bool = False):
    """(kieu, bien the, ly do) voi kieu = "swap" | "revert", hoac None khi khong lam gi.

    `verdict`, `reason`: phan quyet cua duong thumbnail cho video nay (loop.rules.decide)."""
    if st.manual or pending or test_running:
        return None
    age = snap["age_hours"]
    if age > MAX_AGE_H:
        return None
    rev = _revert_reason(st)
    if rev:
        return "revert", st.before, rev
    if verdict is not Verdict.CHANGE_TITLE or not reason.endswith(NO_SWAP_LEFT):
        return None
    if age < MIN_AGE_H or st.swaps >= MAX_SWAPS or st.reverts > 0 or not st.alts_left:
        return None
    last = max((t for t in (st.last_change_utc, last_thumb_change) if t is not None), default=None)
    if last is not None and now - last < timedelta(hours=MIN_GAP_H):
        return None
    if st.touched and st.stats.get(st.variant, (0, 0.0))[0] < MIN_IMPR:
        return None      # tieu de dang chay chua du luot hien thi de noi no cung kem
    return "swap", st.alts_left[0], reason


def request(vid: str, base: str, alts: dict, st: TitleState, to: str, reason: str, requested_utc: str) -> dict | None:
    """Noi dung file yeu cau, hoac None khi thieu chu cua tieu de dich / tieu de dang chay."""
    to_title, expect = (alts or {}).get(to), (alts or {}).get(st.variant)
    if not to_title or not expect:
        return None
    return {"kind": KIND, "video_id": vid, "base": base, "to_title_variant": to, "to_title": to_title,
            "expect_title": expect, "from_title_variant": st.variant, "reason": reason,
            "requested_utc": requested_utc}
