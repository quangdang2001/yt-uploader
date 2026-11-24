"""Test loop/titles.py (chay tu goc repo):  python -m pytest -q loop/test_titles.py"""
import json
from datetime import datetime, timedelta, timezone

from loop import titles as tl
from loop.rules import Verdict
from loop.state import ok_actions as thumb_ok_actions

T0 = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
ALTS = {"main": "Old Title $499", "alt1": "New Title $499", "alt2": "Third Title $499"}
LOW = "CTR 2.5% < bar 5.3% at 4,000 impr, no swap left"


def snap(hours, impr, ctr, rounded=False):
    return {"captured_at_utc": (T0 + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"), "age_hours": hours,
            "impressions": impr, "ctr": ctr, "impr_rounded": rounded, "period": "since_published", "video_id": "v"}


def act(hours, to, state="done", status="ok", detail="", vid="v"):
    return {"kind": "title", "video_id": vid, "to_title_variant": to, "_state": state,
            "result": {"status": status, "at_utc": (T0 + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "detail": detail}}


def now(hours):
    return T0 + timedelta(hours=hours)


def state(snaps, actions=(), alts=ALTS, **kw):
    return tl.build_state(snaps, tl.ok_actions(list(actions)).get("v", []), alts, **kw)


# ---------- alternates file ----------

def test_load_alts_reads_good_files_and_skips_broken_ones(tmp_path):
    d = tmp_path / "title-alts"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"base": "a", "main": "Main  A", "alt1": "Alt A"}), encoding="utf-8")
    (d / "b.json").write_text("{not json", encoding="utf-8")
    (d / "c.json").write_text(json.dumps({"base": "c", "alt1": "no main"}), encoding="utf-8")
    assert tl.load_alts(tmp_path) == {"a": {"main": "Main A", "alt1": "Alt A"}}
    assert tl.load_alts(tmp_path / "missing") == {}


# ---------- the two tracks never read each other's actions ----------

def test_title_actions_are_invisible_to_the_thumbnail_track():
    a = act(40, "alt1")
    assert "to_variant" not in a
    assert thumb_ok_actions([a]) == {}
    thumb = {"video_id": "v", "to_variant": "alt1", "_state": "done", "result": {"status": "ok", "at_utc": "2026-11-21T00:00:00Z"}}
    assert tl.ok_actions([thumb]) == {}
    assert list(tl.ok_actions([a, thumb])) == ["v"]


# ---------- state ----------

def test_state_counts_swaps_reverts_and_leftover_alternates():
    snaps = [snap(10, 1000, 0.03), snap(40, 3000, 0.03), snap(70, 6000, 0.035), snap(100, 9000, 0.03)]
    st = state(snaps)
    assert (st.variant, st.swaps, st.reverts, st.alts_left, st.touched) == ("main", 0, 0, ["alt1", "alt2"], False)
    st = state(snaps, [act(41, "alt1")])
    assert (st.variant, st.before, st.swaps, st.alts_left) == ("alt1", "main", 1, ["alt2"])
    assert st.last_change_utc == now(41)
    st = state(snaps, [act(41, "alt1"), act(71, "main")])
    assert (st.variant, st.swaps, st.reverts) == ("main", 1, 1)
    st = state(snaps, [act(41, "alt1"), act(71, "alt2")])
    assert (st.variant, st.before, st.swaps, st.alts_left) == ("alt2", "alt1", 2, [])
    assert state(snaps, alts={"main": "x"}).alts_left == []
    assert state(snaps, alts=None).alts_left == []


def test_stats_are_per_title_from_snapshot_intervals():
    snaps = [snap(10, 1000, 0.030), snap(40, 3000, 0.030), snap(70, 6000, 0.040), snap(100, 9000, 0.040)]
    st = state(snaps, [act(41, "alt1")])
    n, ctr = st.stats["main"]
    assert n == 3000 and abs(ctr - 0.030) < 1e-9
    n, ctr = st.stats["alt1"]                      # 9000*0.04 - 3000*0.03 = 270 clicks on 6000 impressions
    assert n == 6000 and abs(ctr - 0.045) < 1e-9


def test_main_baseline_ignores_intervals_before_the_last_thumbnail_change():
    snaps = [snap(10, 1000, 0.02), snap(20, 3000, 0.02), snap(40, 6000, 0.03)]
    st = state(snaps, thumb_change_at=now(20))
    n, ctr = st.stats["main"]                      # only the 20 h -> 40 h interval: 120 clicks on 3000
    assert n == 3000 and abs(ctr - 0.04) < 1e-9


def test_rounded_rows_do_not_enter_the_deltas():
    snaps = [snap(10, 1000, 0.03), snap(20, 1200, 0.03, rounded=True), snap(40, 3000, 0.03)]
    assert state(snaps).stats["main"][0] == 3000


# ---------- decide ----------

def decide(st, hours=48, verdict=Verdict.CHANGE_TITLE, reason=LOW, last_thumb=None, pending=False, test=False):
    return tl.decide({"age_hours": hours, "impressions": 4000, "ctr": 0.025}, st, verdict, reason, now(hours),
                     last_thumb, pending, test)


def test_first_title_swap_needs_low_ctr_no_thumbnail_left_and_the_right_age():
    st = state([snap(10, 1000, 0.025), snap(40, 4000, 0.025)])
    assert decide(st) == ("swap", "alt1", LOW)
    assert decide(st, hours=35) is None                                    # heart still grading the title shape
    assert decide(st, hours=169) is None
    assert decide(st, verdict=Verdict.WATCH, reason="CTR 4.9% at 4,000 impr, bar 5.3%") is None
    assert decide(st, reason="CTR 2.5% < bar 5.3% at 4,000 impr, over 7 days: left as is") is None
    assert decide(st, verdict=Verdict.SWAP, reason="CTR 2.5% < bar 5.3% at 4,000 impr") is None   # thumbnails first
    assert decide(st, pending=True) is None
    assert decide(st, test=True) is None
    assert decide(state([snap(40, 4000, 0.025)], alts={"main": "x"})) is None


def test_gap_of_24_hours_after_any_change():
    st = state([snap(10, 1000, 0.025), snap(40, 4000, 0.025)])
    assert decide(st, hours=48, last_thumb=now(30)) is None
    assert decide(st, hours=55, last_thumb=now(30))[0] == "swap"


def test_second_swap_waits_for_2000_impressions_on_the_running_title():
    snaps = [snap(10, 1000, 0.025), snap(40, 4000, 0.025), snap(70, 5000, 0.025)]
    st = state(snaps, [act(41, "alt1")])
    assert decide(st, hours=70) is None                                    # alt1 has 1,000 impressions
    snaps.append(snap(100, 7000, 0.025))
    st = state(snaps, [act(41, "alt1")])
    assert decide(st, hours=100) == ("swap", "alt2", LOW)
    st = state(snaps, [act(41, "alt1"), act(71, "alt2")])
    assert decide(st, hours=120) is None                                   # both alternates used


def test_revert_when_the_new_title_is_clearly_worse_and_only_once():
    snaps = [snap(10, 3000, 0.040), snap(40, 6000, 0.040), snap(70, 9000, 0.030), snap(100, 12000, 0.0255)]
    st = state(snaps, [act(41, "alt1")])                                   # main 4.0% on 6000; alt1 1.1% on 6000
    kind, to, why = decide(st, hours=100, verdict=Verdict.WATCH, reason="x")
    assert (kind, to) == ("revert", "main") and "below title main" in why
    st = state(snaps, [act(41, "alt1"), act(101, "main")])
    assert decide(st, hours=130) is None                                   # after the one revert: nothing more
    close = [snap(10, 3000, 0.040), snap(40, 6000, 0.040), snap(70, 9000, 0.040), snap(100, 12000, 0.039)]
    assert decide(state(close, [act(41, "alt1")]), hours=100, verdict=Verdict.WATCH, reason="x") is None


def test_owner_changed_the_title_stops_the_track():
    refused = act(50, "alt1", state="failed", status="error", detail="title changed since request (live: 'Mine')")
    assert tl.manual_videos([refused, act(50, "alt1", state="failed", status="error", detail="HttpError 500")]) == {"v"}
    st = state([snap(40, 4000, 0.025)], manual=True)
    assert decide(st) is None


def test_failed_recently_is_per_title_variant():
    failed = act(50, "alt1", state="failed", status="error", detail="HttpError 500")
    assert tl.failed_recently([failed], "v", "alt1", now(60))
    assert not tl.failed_recently([failed], "v", "alt2", now(60))
    assert not tl.failed_recently([failed], "v", "alt1", now(80))
    thumb_failed = {"video_id": "v", "to_variant": "alt1", "_state": "failed",
                    "result": {"status": "error", "at_utc": now(50).strftime("%Y-%m-%dT%H:%M:%SZ")}}
    assert not tl.failed_recently([thumb_failed], "v", "alt1", now(60))


# ---------- request ----------

def test_request_never_carries_to_variant_and_names_the_expected_live_title():
    st = state([snap(40, 4000, 0.025)], [act(41, "alt1")])
    rq = tl.request("v", "b1", ALTS, st, "alt2", "why", "2026-11-22T00:00:00Z")
    assert rq == {"kind": "title", "video_id": "v", "base": "b1", "to_title_variant": "alt2",
                  "to_title": "Third Title $499", "expect_title": "New Title $499", "from_title_variant": "alt1",
                  "reason": "why", "requested_utc": "2026-11-22T00:00:00Z"}
    assert "to_variant" not in rq
    assert tl.request("v", "b1", {"main": "x"}, st, "alt2", "why", "t") is None
