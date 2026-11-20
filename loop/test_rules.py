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
    def test_too_early_age(self):
        self.assertEqual(run(snap(impr=50000, age=5.9), state())[0], Verdict.TOO_EARLY)

    def test_too_early_impressions(self):
        self.assertEqual(run(snap(impr=1999, age=10), state())[0], Verdict.TOO_EARLY)

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
                   variant_stats={"main": (5000, 0.052), "alt1": (2500, 0.030)})
        self.assertEqual(run(snap(ctr=0.030, impr=7500), st)[0], Verdict.REVERT)
        st.reverts = 1
        self.assertEqual(run(snap(ctr=0.030, impr=7500), st)[0], Verdict.SWAP)

    def test_change_title_after_alts(self):
        v, reason = run(snap(), state(swaps=2, alts_left=[]))
        self.assertEqual(v, Verdict.CHANGE_TITLE)
        self.assertIn("4,000", reason)
        self.assertEqual(run(snap(), state(swaps=0, alts_left=[]))[0], Verdict.CHANGE_TITLE)
        self.assertEqual(run(snap(), state(swaps=2, alts_left=["alt2"]))[0], Verdict.CHANGE_TITLE)

    def test_manual_pause(self):
        st = state(variant="manual", manual_until=NOW + timedelta(days=3), alts_left=["alt1"],
                   variant_stats={"main": (5000, 0.052), "manual": (2500, 0.030)})
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

    def test_swap_bar_median(self):
        def hist(n):
            return [snap(ctr=0.050, age=30, vid="v%d" % i) for i in range(n)]
        self.assertAlmostEqual(swap_bar(30, hist(10)), 0.040)
        self.assertAlmostEqual(swap_bar(30, hist(9)), 0.053)
        self.assertAlmostEqual(swap_bar(30, hist(10) + [snap(ctr=0.5, age=4, vid="x")]), 0.040)

    def test_swap_bar_latest_per_video(self):
        h = [snap(ctr=0.01, age=30, vid="v%d" % i) for i in range(10)]
        h += [snap(ctr=0.100, age=36, vid="v%d" % i) for i in range(10)]  # later snapshots
        self.assertAlmostEqual(swap_bar(30, h), 0.053)  # 0.8*0.1 = 0.08 > 0.053

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
