"""Verdict rules for the Studio feedback loop (spec 5.4, 5.7, 5.8). Pure, standard library only."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from math import sqrt
from statistics import median

KEEP_CTR = 0.057
BASE_BAR = 0.053
MEDIAN_FACTOR = 0.8
OPENING_FACTOR = 0.85   # OPENING chi khi ret_30s < 0.85 x trung vi (chu kenh/controller 2026-11-20)
MIN_VIDEOS_FOR_MEDIAN = 10
MIN_IMPR = 2000
MIN_AGE_H = 6
MIN_GAP_H = 24
MAX_SWAPS = 2
MAX_REVERTS = 1
MANUAL_PAUSE_D = 7
Z90 = 1.645
CTR_DISPLAY_HALF_STEP = 0.0005  # Studio hien CTR toi 0.1 diem %: mo rong khoang moi ben nua buoc
REVERT_TARGETS = ("main", "alt1", "alt2")  # "manual" khong phai file de VPS dat lai
MANUAL_SUFFIX = " (manual - alert only)"

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
    # main's deltas over its last 3 snapshot intervals before it first stopped being live (first swap or manual
    # change). The REVERT comparison uses this, never main's lifetime totals. None: main has no comparison stats.
    main_baseline: tuple[int, float] | None = None


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


def wide_interval(ctr: float, impressions: int) -> tuple[float, float]:
    """The Wilson interval widened by half Studio's display step on each side (the CTR read is rounded to 0.1 pt).
    Every interval behind a SWAP or a REVERT uses this one."""
    lo, hi = ctr_interval(ctr, impressions)
    return (lo - CTR_DISPLAY_HALF_STEP, hi + CTR_DISPLAY_HALF_STEP)


def revert_candidates(state: VideoState) -> dict[str, tuple[int, float]]:
    """Variants a revert may target: main/alt1/alt2 (never manual), not the live one, with >= MIN_IMPR. main counts
    only through main_baseline (its last 3 intervals before the first swap)."""
    out = {}
    for name in REVERT_TARGETS:
        if name == state.variant:
            continue
        stats = state.main_baseline if name == "main" else state.variant_stats.get(name)
        if stats is not None and stats[0] >= MIN_IMPR:
            out[name] = stats
    return out


def revert_target(state: VideoState) -> str | None:
    """The best of revert_candidates by CTR (ties: the later name, as before)."""
    cands = revert_candidates(state)
    return max((c, name) for name, (n, c) in cands.items())[1] if cands else None


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
    name = revert_target(state)
    if name is None:
        return None
    best = revert_candidates(state)[name]
    if wide_interval(cur[1], cur[0])[1] < wide_interval(best[1], best[0])[0]:
        return "%s CTR %.1f%% at %s impr is below %s CTR %.1f%% at %s impr" % (
            state.variant, cur[1] * 100, format(cur[0], ","), name, best[1] * 100, format(best[0], ","))
    return None


def _swap_allowed(snap: dict, state: VideoState, now: datetime) -> bool:
    # After the one revert no more swaps: main -> alt1 -> (revert) -> stop, or main -> alt1 -> alt2 -> (revert) -> stop
    if state.swaps >= MAX_SWAPS or state.reverts > 0 or not state.alts_left or state.pending_action:
        return False
    if state.last_swap_utc is None:
        return True
    old_enough = now - state.last_swap_utc >= timedelta(hours=MIN_GAP_H)
    return old_enough and snap["impressions"] - state.impr_at_last_swap >= MIN_IMPR


def decide(snap: dict, state: VideoState, bar: float, now: datetime,
           typical_impr: float | None, median_ret_30s: float | None) -> tuple[Verdict, str]:
    """The verdict for one video. A video on the owner's manual thumbnail (after its pause) is judged as usual, for
    the report only: the reason carries MANUAL_SUFFIX and analyse writes no request for it."""
    verdict, reason = _decide(snap, state, bar, now, typical_impr, median_ret_30s)
    if state.variant == "manual" and verdict is not Verdict.PAUSED:
        reason += MANUAL_SUFFIX
    return verdict, reason


def _decide(snap: dict, state: VideoState, bar: float, now: datetime,
            typical_impr: float | None, median_ret_30s: float | None) -> tuple[Verdict, str]:
    impr, ctr = snap["impressions"], snap["ctr"]
    if state.manual_until is not None and state.manual_until > now:
        return Verdict.PAUSED, "manual thumbnail change, paused until %s" % state.manual_until.isoformat()
    if snap["age_hours"] < MIN_AGE_H or impr < MIN_IMPR:
        return Verdict.TOO_EARLY, "age %.1f h, %s impr (needs %d h and %s impr)" % (
            snap["age_hours"], format(impr, ","), MIN_AGE_H, format(MIN_IMPR, ","))
    if snap.get("test_running"):     # the owner's Test & Compare wins: no SWAP, no REVERT
        return Verdict.WATCH, "test running"
    rev = _should_revert(snap, state)
    if rev:
        return Verdict.REVERT, rev
    if ctr >= bar:
        if typical_impr is not None and impr < 0.5 * typical_impr:
            return Verdict.NOT_PACKAGING, "%s, typical %s impr" % (_fmt_ok(snap, bar), format(round(typical_impr), ","))
        ret = snap.get("ret_30s")
        if median_ret_30s is not None and ret is not None and ret < OPENING_FACTOR * median_ret_30s:
            return Verdict.OPENING, "%s, 30s retention %.1f%% < median %.1f%%" % (
                _fmt_ok(snap, bar), ret * 100, median_ret_30s * 100)
    if ctr >= KEEP_CTR:
        return Verdict.KEEP, _fmt_ok(snap, bar)
    clearly_low = wide_interval(ctr, impr)[1] < bar
    if clearly_low:
        if _swap_allowed(snap, state, now):
            return Verdict.SWAP, _fmt(snap, bar)
        if state.swaps >= MAX_SWAPS or state.reverts > 0 or not state.alts_left:
            return Verdict.CHANGE_TITLE, _fmt(snap, bar) + ", no swap left"
    return Verdict.WATCH, "CTR %.1f%% at %s impr, bar %.1f%%" % (ctr * 100, format(impr, ","), bar * 100)
