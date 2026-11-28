import unittest
from datetime import datetime, timedelta, timezone

from loop import rules
from loop.rules import Verdict, VideoState, decide, ctr_interval, swap_bar, age_bucket

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def snap(ctr=0.040, impr=4000, age=30.0, ret=None, running=False, vid="v1"):
    return {"video_id": vid, "age_hours": age, "impressions": impr, "ctr": ctr,
            "ret_30s": ret, "test_running": running}


def state(**kw):
    base = dict(variant="main", swaps=0, reverts=0, last_swap_utc=None, impr_at_last_swap=0,
                variant_stats={}, manual_until=None, alts_left=["alt1", "alt2"], pending_action=False)
    base.update(kw)
    return VideoState(**base)


def run(s, st, bar=0.053, typical=None, med_ret=None):
    return decide(s, st, bar, NOW, typical, med_ret)


class RulesTest(unittest.TestCase):
    def test_too_early_needs_both_young_and_few_impressions(self):
        v, reason = run(snap(impr=1499, age=5.9), state())
        self.assertEqual(v, Verdict.TOO_EARLY)
        self.assertIn("needs 6 h or 1,500 impr", reason)

    def test_judged_once_old_enough_even_with_few_impressions(self):
        self.assertNotEqual(run(snap(impr=300, age=10), state())[0], Verdict.TOO_EARLY)

    def test_judged_once_1500_impressions_even_when_young(self):
        self.assertNotEqual(run(snap(impr=1500, age=2), state())[0], Verdict.TOO_EARLY)
        self.assertNotEqual(run(snap(impr=50000, age=5.9), state())[0], Verdict.TOO_EARLY)

    def test_keep(self):
        self.assertEqual(run(snap(ctr=0.057, impr=3000), state())[0], Verdict.KEEP)

    def test_swap(self):
        v, reason = run(snap(ctr=0.040, impr=4000), state())
        self.assertEqual(v, Verdict.SWAP)
        self.assertIn("CTR 4.0% < bar 5.3% at 4,000 impr", reason)

    def test_no_swap_when_interval_reaches_bar(self):
        self.assertEqual(run(snap(ctr=0.050, impr=2000), state())[0], Verdict.WATCH)

    def test_no_swap_during_test(self):
        self.assertEqual(run(snap(running=True), state())[0], Verdict.WATCH)

    def test_no_second_request_while_pending(self):
        self.assertEqual(run(snap(), state(pending_action=True))[0], Verdict.WATCH)

    def test_gap_after_swap(self):
        last = NOW - timedelta(hours=23)
        st = state(swaps=1, last_swap_utc=last, impr_at_last_swap=2000, alts_left=["alt2"])
        self.assertEqual(run(snap(impr=6000), st)[0], Verdict.WATCH)
        st = state(swaps=1, last_swap_utc=NOW - timedelta(hours=24), impr_at_last_swap=2000,
                   alts_left=["alt2"])
        self.assertEqual(run(snap(impr=4000), st)[0], Verdict.SWAP)
        self.assertEqual(run(snap(impr=3999), st)[0], Verdict.WATCH)

    def test_revert(self):
        st = state(variant="alt1", swaps=1, last_swap_utc=NOW - timedelta(hours=30),
                   impr_at_last_swap=5000, alts_left=["alt2"],
                   variant_stats={"main": (5000, 0.052), "alt1": (2500, 0.030)},
                   main_baseline=(5000, 0.052))
        self.assertEqual(run(snap(ctr=0.030, impr=7500), st)[0], Verdict.REVERT)
        st.reverts = 1      # I5: after the one revert, no more swaps even with alt2 left
        v, reason = run(snap(ctr=0.030, impr=7500), st)
        self.assertEqual(v, Verdict.CHANGE_TITLE)
        self.assertIn("no swap left", reason)

    def test_revert_still_fires_after_168_hours(self):
        st = state(variant="alt1", swaps=1, last_swap_utc=NOW - timedelta(hours=30),
                   impr_at_last_swap=5000, alts_left=["alt2"],
                   variant_stats={"main": (5000, 0.052), "alt1": (2500, 0.030)},
                   main_baseline=(5000, 0.052))
        self.assertEqual(run(snap(ctr=0.030, impr=7500, age=200.0), st)[0], Verdict.REVERT)

    # -- C1: the owner's Test & Compare wins

    def test_test_running_blocks_revert(self):
        st = state(variant="alt1", swaps=1, last_swap_utc=NOW - timedelta(hours=30),
                   impr_at_last_swap=5000, alts_left=["alt2"],
                   variant_stats={"alt1": (2500, 0.030)}, main_baseline=(5000, 0.052))
        self.assertEqual(run(snap(ctr=0.030, impr=7500), st)[0], Verdict.REVERT)
        self.assertEqual(run(snap(ctr=0.030, impr=7500, running=True), st), (Verdict.WATCH, "test running"))

    def test_test_running_blocks_swap(self):
        self.assertEqual(run(snap(), state())[0], Verdict.SWAP)
        self.assertEqual(run(snap(running=True), state()), (Verdict.WATCH, "test running"))

    # -- C2: the owner's thumbnail is judged for the report only

    def test_manual_after_pause_is_alert_only(self):
        st = state(variant="manual", manual_until=NOW - timedelta(seconds=1), alts_left=["alt1"],
                   variant_stats={"manual": (2500, 0.030)}, main_baseline=(5000, 0.052))
        v, reason = run(snap(ctr=0.030, impr=7500), st)
        self.assertEqual(v, Verdict.REVERT)                 # computed as usual, for the report
        self.assertTrue(reason.endswith(" (manual - alert only)"), reason)
        st.variant_stats = {"manual": (7500, 0.060)}
        v, reason = run(snap(ctr=0.060, impr=7500), st)
        self.assertEqual(v, Verdict.KEEP)
        self.assertTrue(reason.endswith(" (manual - alert only)"), reason)
        st.manual_until = NOW + timedelta(days=1)
        v, reason = run(snap(ctr=0.030, impr=7500), st)
        self.assertEqual(v, Verdict.PAUSED)
        self.assertNotIn("alert only", reason)

    # -- I4: noise

    def test_swap_interval_widened_by_display_step(self):
        self.assertEqual(rules.CTR_DISPLAY_HALF_STEP, 0.0005)
        # Wilson hi of 4.70% at 4,000 is 5.282%: under the 5.3% bar, but not by half a display step
        self.assertLess(ctr_interval(0.047, 4000)[1], 0.053)
        self.assertEqual(run(snap(ctr=0.047, impr=4000), state())[0], Verdict.WATCH)
        self.assertEqual(run(snap(ctr=0.046, impr=4000), state())[0], Verdict.SWAP)

    def test_revert_interval_widened_by_display_step(self):
        st = state(variant="alt1", swaps=1, last_swap_utc=NOW - timedelta(hours=30), impr_at_last_swap=5000,
                   alts_left=["alt2"], variant_stats={"alt1": (5000, 0.0423), "alt2": (20000, 0.05)})
        # raw intervals do not overlap (gap 0.03 pt), the widened ones do
        self.assertLess(ctr_interval(0.0423, 5000)[1], ctr_interval(0.05, 20000)[0])
        self.assertNotEqual(run(snap(ctr=0.045, impr=30000), st)[0], Verdict.REVERT)
        st.variant_stats["alt1"] = (5000, 0.040)
        self.assertEqual(run(snap(ctr=0.045, impr=30000), st)[0], Verdict.REVERT)

    def test_revert_uses_main_baseline_not_lifetime(self):
        # main's lifetime looks great, but its last 3 intervals before the swap hold too few impressions
        st = state(variant="alt1", swaps=1, last_swap_utc=NOW - timedelta(hours=30), impr_at_last_swap=5000,
                   alts_left=["alt2"], variant_stats={"main": (50000, 0.09), "alt1": (5000, 0.030)},
                   main_baseline=(1999, 0.09))
        self.assertEqual(rules.revert_candidates(st), {})
        self.assertNotEqual(run(snap(ctr=0.030, impr=55000), st)[0], Verdict.REVERT)
        st.main_baseline = (2000, 0.09)
        self.assertEqual(run(snap(ctr=0.030, impr=55000), st)[0], Verdict.REVERT)
        st.main_baseline = None
        self.assertNotEqual(run(snap(ctr=0.030, impr=55000), st)[0], Verdict.REVERT)

    # -- I5: one revert, then stop

    def test_worst_alt2_reverts_to_best_not_change_title(self):
        st = state(variant="alt2", swaps=2, reverts=0, last_swap_utc=NOW - timedelta(hours=30),
                   impr_at_last_swap=30000, alts_left=[],
                   variant_stats={"main": (20000, 0.045), "alt1": (8000, 0.048), "alt2": (5000, 0.020)},
                   main_baseline=(6000, 0.045))
        v, reason = run(snap(ctr=0.04, impr=40000, age=300), st)
        self.assertEqual(v, Verdict.REVERT)
        self.assertIn("below alt1", reason)
        self.assertEqual(rules.revert_target(st), "alt1")

    def test_no_swap_after_revert(self):
        st = state(variant="main", swaps=1, reverts=1, last_swap_utc=NOW - timedelta(days=3),
                   impr_at_last_swap=2000, alts_left=["alt2"])
        self.assertEqual(run(snap(), st)[0], Verdict.CHANGE_TITLE)

    def test_no_swap_after_7_days(self):
        v, reason = run(snap(ctr=0.040, impr=4000, age=169), state())
        self.assertEqual(v, Verdict.CHANGE_TITLE)
        self.assertTrue(reason.endswith(", over 7 days: left as is"), reason)

    def test_swap_still_allowed_at_100h(self):
        self.assertEqual(run(snap(ctr=0.040, impr=4000, age=100), state())[0], Verdict.SWAP)
        self.assertEqual(run(snap(ctr=0.040, impr=4000, age=168), state())[0], Verdict.SWAP)

    def test_change_title_after_alts(self):
        v, reason = run(snap(), state(swaps=2, alts_left=[]))
        self.assertEqual(v, Verdict.CHANGE_TITLE)
        self.assertIn("4,000", reason)
        self.assertEqual(run(snap(), state(swaps=0, alts_left=[]))[0], Verdict.CHANGE_TITLE)
        self.assertEqual(run(snap(), state(swaps=2, alts_left=["alt2"]))[0], Verdict.CHANGE_TITLE)

    def test_manual_pause(self):
        st = state(variant="manual", manual_until=NOW + timedelta(days=3), alts_left=["alt1"],
                   variant_stats={"main": (5000, 0.052), "manual": (2500, 0.030)}, main_baseline=(5000, 0.052))
        self.assertEqual(run(snap(ctr=0.030, impr=7500), st)[0], Verdict.PAUSED)
        st.manual_until = NOW - timedelta(seconds=1)
        self.assertEqual(run(snap(ctr=0.030, impr=7500), st)[0], Verdict.REVERT)

    def test_not_packaging(self):
        v, reason = run(snap(ctr=0.060, impr=2500), state(), typical=10000)
        self.assertEqual(v, Verdict.NOT_PACKAGING)
        self.assertIn("CTR 6.0% >= bar 5.3% at 2,500 impr", reason)
        self.assertIn("10,000", reason)
        v, _ = run(snap(ctr=0.055, impr=2500), state(), typical=10000)
        self.assertEqual(v, Verdict.NOT_PACKAGING)
        v, _ = run(snap(ctr=0.055, impr=5000), state(), typical=10000)
        self.assertEqual(v, Verdict.WATCH)

    def test_opening(self):
        v, reason = run(snap(ctr=0.055, impr=5000, ret=0.40), state(), med_ret=0.50)
        self.assertEqual(v, Verdict.OPENING)
        self.assertIn("CTR 5.5% >= bar 5.3% at 5,000 impr", reason)
        self.assertIn("40.0%", reason)
        self.assertIn("50.0%", reason)
        v, _ = run(snap(ctr=0.055, impr=5000, ret=0.60), state(), med_ret=0.50)
        self.assertEqual(v, Verdict.WATCH)

    def test_opening_margin(self):
        v, _ = run(snap(ctr=0.055, impr=5000, ret=0.58), state(), med_ret=0.60)
        self.assertNotEqual(v, Verdict.OPENING)     # 0.58 >= 0.85 x 0.60 = 0.51
        v, _ = run(snap(ctr=0.055, impr=5000, ret=0.51), state(), med_ret=0.60)
        self.assertNotEqual(v, Verdict.OPENING)     # bien: 0.85 x 0.60 = 0.51, strict <
        v, _ = run(snap(ctr=0.055, impr=5000, ret=0.5099), state(), med_ret=0.60)
        self.assertEqual(v, Verdict.OPENING)
        v, reason = run(snap(ctr=0.055, impr=5000, ret=0.50), state(), med_ret=0.60)
        self.assertEqual(v, Verdict.OPENING)
        self.assertIn("50.0%", reason)
        self.assertIn("60.0%", reason)

    def test_opening_on_good_ctr(self):
        v, _ = run(snap(ctr=0.060, impr=5000, ret=0.40), state(), typical=5000, med_ret=0.50)
        self.assertEqual(v, Verdict.OPENING)

    def test_keep_when_no_alert(self):
        v, _ = run(snap(ctr=0.060, impr=5000, ret=0.60), state(), typical=5000, med_ret=0.50)
        self.assertEqual(v, Verdict.KEEP)

    def test_swap_bar_is_fixed_at_7_percent(self):
        # owner 2026-11-28: the bar is 7%, whatever the channel median is (it used to be min(5.3%, 0.8 x median))
        low = [snap(ctr=0.010, age=30, vid="v%d" % i) for i in range(12)]
        high = [snap(ctr=0.100, age=30, vid="w%d" % i) for i in range(12)]
        for hist in ([], low, high):
            self.assertAlmostEqual(swap_bar(30, hist), 0.07)

    def test_s_and_w_357_case_is_swapped_at_the_7_percent_bar(self):
        # ZkbhVEiZmtc 2026-11-28: CTR 2.2% at 1,985 impr, 17.7 h. Under the old 2.5% bar the Wilson top (2.86%) kept it WATCH.
        v, reason = run(snap(ctr=0.022, impr=1985, age=17.7), state(), bar=swap_bar(17.7, []))
        self.assertEqual(v, Verdict.SWAP)
        self.assertIn("bar 7.0%", reason)

    def test_between_keep_and_bar_is_still_keep(self):
        self.assertEqual(run(snap(ctr=0.062, impr=20000), state(), bar=0.07)[0], Verdict.KEEP)

    def test_age_bucket(self):
        self.assertEqual([age_bucket(h) for h in (0, 6, 12, 24, 48, 72, 500)],
                         ["0-6", "6-12", "12-24", "24-48", "48-72", "72+", "72+"])

    def test_wilson_known_value(self):
        lo, hi = ctr_interval(0.05, 1000)
        self.assertAlmostEqual(lo, 0.0398, places=4)
        self.assertAlmostEqual(hi, 0.0626, places=4)
        self.assertEqual(ctr_interval(0.0, 0), (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
