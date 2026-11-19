"""Verdict rules for the Studio feedback loop (spec 5.4, 5.7, 5.8). Pure, standard library only."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from math import sqrt
from statistics import median

KEEP_CTR = 0.057
BASE_BAR = 0.053
MEDIAN_FACTOR = 0.8
MIN_VIDEOS_FOR_MEDIAN = 10
MIN_IMPR = 2000
MIN_AGE_H = 6
MIN_GAP_H = 24
MAX_SWAPS = 2
MAX_REVERTS = 1
MANUAL_PAUSE_D = 7
Z90 = 1.645

_BUCKETS = ((6, "0-6"), (12, "6-12"), (24, "12-24"), (48, "24-48"), (72, "48-72"))


class Verdict(Enum):
    TOO_EARLY = "TOO_EARLY"
    KEEP = "KEEP"
    WATCH = "WATCH"
    SWAP = "SWAP"
    REVERT = "REVERT"
    CHANGE_TITLE = "CHANGE_TITLE"
    NOT_PACKAGING = "NOT_PACKAGING"
    OPENING = "OPENING"
    PAUSED = "PAUSED"


@dataclass
class VideoState:
    variant: str
    swaps: int
    reverts: int
    last_swap_utc: datetime | None
    impr_at_last_swap: int
    variant_stats: dict[str, tuple[int, float]] = field(default_factory=dict)
    manual_until: datetime | None = None
    alts_left: list[str] = field(default_factory=list)
    pending_action: bool = False


def ctr_interval(ctr: float, impressions: int, z: float = Z90) -> tuple[float, float]:
    """Wilson score interval for a click-through rate."""
    n = impressions
    if n <= 0:
        return (0.0, 1.0)
    z2 = z * z
    denom = 1 + z2 / n
    centre = (ctr + z2 / (2 * n)) / denom
    half = z * sqrt(ctr * (1 - ctr) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def age_bucket(age_hours: float) -> str:
    for limit, name in _BUCKETS:
        if age_hours < limit:
            return name
    return "72+"


def swap_bar(age_hours: float, history: list[dict]) -> float:
    """5.3% until 10+ videos have a snapshot in this age bucket; then min(5.3%, 0.8 x channel median)."""
    bucket = age_bucket(age_hours)
    latest: dict[str, dict] = {}
    for s in history:
        if age_bucket(s["age_hours"]) != bucket:
            continue
        cur = latest.get(s["video_id"])
        if cur is None or s["age_hours"] >= cur["age_hours"]:
            latest[s["video_id"]] = s
    if len(latest) < MIN_VIDEOS_FOR_MEDIAN:
        return BASE_BAR
    med = median(s["ctr"] for s in latest.values())
    return min(BASE_BAR, MEDIAN_FACTOR * med)


def _fmt(snap: dict, bar: float) -> str:
    return "CTR %.1f%% < bar %.1f%% at %s impr" % (snap["ctr"] * 100, bar * 100, format(snap["impressions"], ","))


def _fmt_ok(snap: dict, bar: float) -> str:
    return "CTR %.1f%% >= bar %.1f%% at %s impr" % (snap["ctr"] * 100, bar * 100, format(snap["impressions"], ","))


def _should_revert(snap: dict, state: VideoState) -> str | None:
    if state.reverts >= MAX_REVERTS:
        return None
    cur = state.variant_stats.get(state.variant, (snap["impressions"], snap["ctr"]))
    if cur[0] < MIN_IMPR:
        return None
    cur_hi = ctr_interval(cur[1], cur[0])[1]
    best = None
    for name, (n, c) in state.variant_stats.items():
        if name == state.variant or n < MIN_IMPR:
            continue
        if best is None or c > best[2]:
            best = (name, n, c)
    if best is None:
        return None
    if cur_hi < ctr_interval(best[2], best[1])[0]:
        return "%s CTR %.1f%% at %s impr is below %s CTR %.1f%% at %s impr" % (
            state.variant, cur[1] * 100, format(cur[0], ","), best[0], best[2] * 100, format(best[1], ","))
    return None


def _swap_allowed(snap: dict, state: VideoState, now: datetime) -> bool:
    if state.swaps >= MAX_SWAPS or not state.alts_left or state.pending_action or snap["test_running"]:
        return False
    if state.last_swap_utc is None:
        return True
    old_enough = now - state.last_swap_utc >= timedelta(hours=MIN_GAP_H)
    return old_enough and snap["impressions"] - state.impr_at_last_swap >= MIN_IMPR


def decide(snap: dict, state: VideoState, bar: float, now: datetime,
           typical_impr: float | None, median_ret_30s: float | None) -> tuple[Verdict, str]:
    impr, ctr = snap["impressions"], snap["ctr"]
    if state.manual_until is not None and state.manual_until > now:
        return Verdict.PAUSED, "manual thumbnail change, paused until %s" % state.manual_until.isoformat()
    if snap["age_hours"] < MIN_AGE_H or impr < MIN_IMPR:
        return Verdict.TOO_EARLY, "age %.1f h, %s impr (needs %d h and %s impr)" % (
            snap["age_hours"], format(impr, ","), MIN_AGE_H, format(MIN_IMPR, ","))
    rev = _should_revert(snap, state)
    if rev:
        return Verdict.REVERT, rev
    if ctr >= KEEP_CTR:
        return Verdict.KEEP, _fmt_ok(snap, bar)
    clearly_low = ctr_interval(ctr, impr)[1] < bar
    if clearly_low:
        if _swap_allowed(snap, state, now):
            return Verdict.SWAP, _fmt(snap, bar)
        if state.swaps >= MAX_SWAPS or not state.alts_left:
            return Verdict.CHANGE_TITLE, _fmt(snap, bar) + ", no swap left"
    if ctr >= bar:
        if typical_impr is not None and impr < 0.5 * typical_impr:
            return Verdict.NOT_PACKAGING, "%s, typical %s impr" % (_fmt_ok(snap, bar), format(round(typical_impr), ","))
        ret = snap.get("ret_30s")
        if median_ret_30s is not None and ret is not None and ret < median_ret_30s:
            return Verdict.OPENING, "%s, 30s retention %.1f%% < median %.1f%%" % (
                _fmt_ok(snap, bar), ret * 100, median_ret_30s * 100)
    return Verdict.WATCH, "CTR %.1f%% at %s impr, bar %.1f%%" % (ctr * 100, format(impr, ","), bar * 100)
