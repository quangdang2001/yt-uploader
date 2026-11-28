from datetime import datetime, timedelta, timezone

from loop import apply_change as ac

NOW = datetime(2026, 11, 28, 9, 0, tzinfo=timezone.utc)
RULES = {"kill": [{"id": "manufacture", "patterns": ["how to build a gun"]}],
         "flag": [{"id": "nfa", "patterns": ["suppressor"]}]}


def video(**kw):
    v = {"video_id": "v1", "age_h": 90, "live_title": "Old title", "being_ridden": False, "slug": "s"}
    v.update(kw)
    return v


def done(vid, to, hours_ago, status="ok"):
    at = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"kind": "title", "video_id": vid, "to_title_variant": to, "_state": "done",
            "result": {"status": status, "at_utc": at}}


def run(title="A better title", v=None, nums=frozenset(), actions=(), pending=()):
    return ac.check(v or video(), title, set(nums), RULES, list(actions), list(pending), NOW)


def test_clean_change_goes_to_alt1():
    why, st = run()
    assert why == [] and st == {"from": "main", "to": "alt1"}


def test_refusals():
    assert "being ridden" in run(v=video(being_ridden=True))[0][0]
    assert any("older" in w for w in run(v=video(age_h=200))[0])
    assert any("same as the live" in w for w in run(title="Old title")[0])
    assert any("longer" in w for w in run(title="x" * 101)[0])
    assert any("link" in w for w in run(title="see site.com now")[0])
    assert any("number" in w for w in run(title="Top 10 guns")[0])
    assert run(title="Top 10 guns", nums={"10"})[0] == []
    assert any("policy" in w for w in run(title="A suppressor story")[0])
    assert any("pending" in w for w in run(pending=["v1__20261128T080000Z.json"])[0])


def test_gap_and_slots():
    assert any("24 h" in w for w in run(actions=[done("v1", "alt1", 5)])[0])
    why, st = run(actions=[done("v1", "alt1", 30)])
    assert why == [] and st == {"from": "alt1", "to": "alt2"}
    why, _ = run(actions=[done("v1", "alt1", 60), done("v1", "alt2", 30)])
    assert any("no spare" in w for w in why)


def test_owner_manual_edit_stops_it():
    manual = {"kind": "title", "video_id": "v1", "to_title_variant": "alt1", "_state": "failed",
              "result": {"status": "error", "detail": "title changed since request (live: 'x')"}}
    assert any("by hand" in w for w in run(actions=[manual])[0])
