"""loop/state.py + loop/rules.py together: hash timelines, noise, and whole swap/revert sequences.

Chay tu goc repo:  python -m unittest loop.test_state -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from loop.rules import Verdict, decide, revert_target
from loop.state import build_states

T0 = datetime(2026, 11, 1, 6, 10, tzinfo=timezone.utc)
H, D = timedelta(hours=2), timedelta(days=1)
FEATS = [{"base": "b", "variant": v} for v in ("main", "alt1", "alt2")]
BASES = {"vidAAAAAA": "b"}


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def snap(t, h, impr=10000, ctr=0.05, age=100, rounded=False):
    return {"captured_at_utc": iso(t), "video_id": "vidAAAAAA", "period": "since_published",
            "impressions": impr, "ctr": ctr, "age_hours": age, "thumb_hash": h, "impr_rounded": rounded}


def act(t, to="alt1", frm="main"):
    return {"_state": "done", "video_id": "vidAAAAAA", "to_variant": to, "from_variant": frm,
            "requested_utc": iso(t), "result": {"status": "ok", "at_utc": iso(t)}}


def state(snaps, acts, now, released=None):
    return build_states(snaps, acts, FEATS, now, bases=BASES, released=released)["vidAAAAAA"]


class HashWindowTest(unittest.TestCase):
    """I3: a swap is never read as the owner's manual change."""

    def test_daily_video_swap_first_seen_at_24h20m_is_ours(self):
        snaps = [snap(T0, "A"), snap(T0 + D + timedelta(minutes=30), "B", 11000), snap(T0 + 2 * D, "B", 12000)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 2 * D + H)
        self.assertEqual(st.variant, "alt1")
        self.assertIsNone(st.manual_until)

    def test_daily_video_with_one_hashless_snapshot_after_swap_is_ours(self):
        snaps = [snap(T0, "A"), snap(T0 + D, None, 11000), snap(T0 + 2 * D, "B", 12000),
                 snap(T0 + 3 * D, "B", 13000)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 3 * D + H)
        self.assertEqual(st.variant, "alt1")
        self.assertIsNone(st.manual_until)

    def test_hashless_rows_do_not_use_up_the_window(self):
        snaps = [snap(T0, "A"), snap(T0 + D, None, 11000), snap(T0 + 2 * D, None, 12000),
                 snap(T0 + 3 * D, "B", 13000), snap(T0 + 4 * D, "B", 14000)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 4 * D + H)
        self.assertEqual(st.variant, "alt1")

    def test_two_hourly_swap_is_ours(self):
        snaps = [snap(T0, "A"), snap(T0 + H, "B", 11000), snap(T0 + 2 * H, "B", 12000)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 3 * H)
        self.assertEqual(st.variant, "alt1")

    def test_window_closes_on_first_confirmed_change(self):
        # our swap shows and is confirmed; a second change right after is the owner's
        snaps = [snap(T0, "A"), snap(T0 + H, "B", 11000), snap(T0 + 2 * H, "B", 12000),
                 snap(T0 + 3 * H, "M", 13000), snap(T0 + 4 * H, "M", 14000)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 5 * H)
        self.assertEqual(st.variant, "manual")

    def test_two_hourly_first_seen_at_snapshot_2_is_ours(self):
        snaps = [snap(T0, "A")] + [snap(T0 + i * H, "A" if i < 2 else "B", 10000 + 1000 * i) for i in range(1, 5)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 5 * H)
        self.assertEqual(st.variant, "alt1")
        self.assertIsNone(st.manual_until)

    def test_two_hourly_first_seen_at_snapshot_4_is_ours(self):
        # CDN slack: ~8 h after the action, confirmed at snapshot 5 (10 h): still inside the 24 h window
        snaps = [snap(T0, "A")] + [snap(T0 + i * H, "A" if i < 4 else "B", 10000 + 1000 * i) for i in range(1, 7)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 7 * H)
        self.assertEqual(st.variant, "alt1")
        self.assertIsNone(st.manual_until)

    def test_daily_first_seen_at_25h_is_ours(self):
        snaps = [snap(T0, "A"), snap(T0 + D + H, "B", 11000), snap(T0 + 2 * D + H, "B", 12000)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 3 * D)
        self.assertEqual(st.variant, "alt1")
        self.assertIsNone(st.manual_until)

    def test_different_new_hash_30h_after_action_is_manual(self):
        snaps = [snap(T0, "A")] + [snap(T0 + i * H, "A", 10000 + 1000 * i) for i in range(1, 16)] + \
               [snap(T0 + 16 * H, "M", 30000), snap(T0 + 17 * H, "M", 31000)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 18 * H)
        self.assertEqual(st.variant, "manual")           # M first seen 32 h after the action, nothing new in between

    def test_second_different_hash_after_ours_confirmed_is_manual(self):
        # B (ours) is confirmed at 4 h; the owner's M is confirmed at 8 h, still inside 24 h of the action
        snaps = [snap(T0, "A"), snap(T0 + 1 * H, "B", 11000), snap(T0 + 2 * H, "B", 12000),
                 snap(T0 + 3 * H, "M", 13000), snap(T0 + 4 * H, "M", 14000)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 5 * H)
        self.assertEqual(st.variant, "manual")
        self.assertEqual(st.manual_until, T0 + 3 * H + timedelta(days=7))

    def test_owner_change_seen_before_ours_is_confirmed_is_manual(self):
        # both new hashes confirmed inside the window: B is ours, C (the later one) is the owner's
        snaps = [snap(T0, "A"), snap(T0 + 1 * H, "B", 11000), snap(T0 + 2 * H, "B", 12000),
                 snap(T0 + 3 * H, "C", 13000), snap(T0 + 4 * H, "C", 14000)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 5 * H)
        self.assertEqual(st.variant, "manual")
        self.assertEqual(st.manual_until, T0 + 3 * H + timedelta(days=7))

    def test_blip_inside_window_does_not_make_own_swap_manual(self):
        # A, X, A, B, B: X is a one-snapshot blip, never confirmed; B is the loop's own swap
        snaps = [snap(T0, "A"), snap(T0 + 1 * H, "X", 11000), snap(T0 + 2 * H, "A", 12000),
                 snap(T0 + 3 * H, "B", 13000), snap(T0 + 4 * H, "B", 14000)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 5 * H)
        self.assertEqual(st.variant, "alt1")
        self.assertIsNone(st.manual_until)

    def test_change_with_no_action_is_manual(self):
        snaps = [snap(T0, "A"), snap(T0 + D, "M", 11000), snap(T0 + 2 * D, "M", 12000)]
        self.assertEqual(state(snaps, [], T0 + 2 * D + H).variant, "manual")


class NoiseTest(unittest.TestCase):
    """I4a: rounded impressions never enter the per-variant deltas."""

    def test_rounded_rows_excluded_from_deltas(self):
        snaps = [snap(T0, "A", 10000, 0.05), snap(T0 + H, "A", 12000, 0.10, rounded=True),
                 snap(T0 + 2 * H, "A", 14000, 0.05)]
        st = state(snaps, [], T0 + 3 * H)
        self.assertEqual(st.variant_stats["main"][0], 14000)
        self.assertAlmostEqual(st.variant_stats["main"][1], 0.05)
        # the interval across a swap: the rounded row is skipped, the exact rows on both sides still count
        snaps = [snap(T0, "A", 10000, 0.05), snap(T0 + H, "B", 12000, 0.02, rounded=True),
                 snap(T0 + 2 * H, "B", 14000, (500 + 80) / 14000)]
        st = state(snaps, [act(T0 + timedelta(minutes=10))], T0 + 3 * H)
        self.assertEqual(st.variant_stats["alt1"], (4000, 80 / 4000))


class MainBaselineTest(unittest.TestCase):
    """I4c: main's comparison stats are its last 3 intervals before the first swap."""

    def test_baseline_is_last_three_intervals_before_first_swap(self):
        snaps = [snap(T0 + k * H, "A", 1000 * (k + 1), 0.10 if k < 3 else (300 + 40 * (k - 2)) / (1000 * (k + 1)))
                 for k in range(6)]
        # main: 1000 @10% from publish, then 1000 @10%, 1000 @10%, then three intervals at 4% each
        swap_at = T0 + 5 * H + timedelta(minutes=10)
        snaps += [snap(T0 + 6 * H, "B", 7000, 0.04), snap(T0 + 7 * H, "B", 8000, 0.04)]
        st = state(snaps, [act(swap_at)], T0 + 8 * H)
        self.assertEqual(st.main_baseline[0], 3000)
        self.assertAlmostEqual(st.main_baseline[1], 0.04)
        self.assertEqual(st.variant_stats["main"][0], 6000)    # lifetime totals differ

    def test_baseline_frozen_at_first_departure(self):
        snaps = [snap(T0 + k * H, "A", 2000 * (k + 1), 0.05) for k in range(3)]
        snaps += [snap(T0 + 3 * H, "B", 8000, 0.04), snap(T0 + 4 * H, "B", 10000, 0.035),
                  snap(T0 + 5 * H, "A", 12000, 0.04), snap(T0 + 6 * H, "A", 20000, 0.06)]
        acts = [act(T0 + 2 * H + timedelta(minutes=10)), act(T0 + 4 * H + timedelta(minutes=10), "main", "alt1")]
        st = state(snaps, acts, T0 + 7 * H)
        self.assertEqual(st.main_baseline, (6000, 0.05))


class ReleaseTest(unittest.TestCase):
    """C2: a released manual video is main from the release time, with fresh stats."""

    def test_release_resets_variant_and_stats(self):
        snaps = [snap(T0, "A", 20000, 0.06), snap(T0 + D, "M", 21000, 0.06), snap(T0 + 2 * D, "M", 22000, 0.06),
                 snap(T0 + 10 * D, "M", 30000, 0.05), snap(T0 + 11 * D, "M", 32000, (1500 + 60) / 32000)]
        now = T0 + 12 * D
        st = state(snaps, [], now)
        self.assertEqual(st.variant, "manual")
        st = state(snaps, [], now, released={"vidAAAAAA": T0 + 9 * D})
        self.assertEqual((st.variant, st.manual_until), ("main", None))
        self.assertEqual(st.variant_stats, {"main": (2000, 0.03)})
        self.assertIsNone(st.main_baseline)
        # a release before the manual change does not pre-approve it
        st = state(snaps, [], now, released={"vidAAAAAA": T0 + timedelta(hours=1)})
        self.assertEqual(st.variant, "manual")


class SequenceTest(unittest.TestCase):
    """I5: run the loop every 2 h on a simulated video and follow its requests."""

    def simulate(self, ctr_of, steps=120, per_step=1500):
        hashes = {"main": "HM", "alt1": "H1", "alt2": "H2"}
        live, impr, clicks = "main", 0, 0.0
        snaps, acts, log = [], [], []
        for k in range(1, steps + 1):
            t = T0 + k * H
            impr += per_step
            clicks += per_step * ctr_of[live]
            snaps.append(snap(t, hashes[live], impr, clicks / impr, age=2 * k))
            st = state(snaps, acts, t)
            verdict, _ = decide(snaps[-1], st, 0.053, t, None, None)
            target = st.alts_left[0] if verdict is Verdict.SWAP else (
                revert_target(st) if verdict is Verdict.REVERT else None)
            if target:
                acts.append(act(t + timedelta(minutes=10), target, st.variant))
                log.append(target)
                live = target
        st = state(snaps, acts, T0 + (steps + 1) * H)
        return log, st, verdict

    def test_worse_alt1_tries_alt2_then_reverts_to_the_better_of_the_two_and_stops(self):
        # Since 2026-11-28 a video is judged at 6 h OR 1,500 impr (rules.MIN_IMPR_FIRST). main is then judged on only
        # 1,500 impr, too few for the revert check to call alt1 clearly worse, so the 24 h gap lets alt2 go first;
        # the revert then lands on alt1 (2.5%), the better of the two worse variants.
        log, st, verdict = self.simulate({"main": 0.040, "alt1": 0.025, "alt2": 0.020})
        self.assertEqual(log, ["alt1", "alt2", "alt1"])
        self.assertEqual((st.variant, st.swaps, st.reverts), ("alt1", 2, 1))
        self.assertEqual(verdict, Verdict.CHANGE_TITLE)

    def test_worst_alt2_reverts_to_best_of_three_not_change_title(self):
        log, st, verdict = self.simulate({"main": 0.040, "alt1": 0.046, "alt2": 0.020})
        self.assertEqual(log, ["alt1", "alt2", "alt1"])
        self.assertEqual((st.variant, st.swaps, st.reverts), ("alt1", 2, 1))
        self.assertEqual(verdict, Verdict.CHANGE_TITLE)


if __name__ == "__main__":
    unittest.main()
