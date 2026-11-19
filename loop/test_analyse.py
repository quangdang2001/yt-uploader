"""Test loop/analyse.py voi cache gia va Drive gia, khong goi mang.

Chay tu goc repo:  python -m unittest loop.test_analyse -v
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.append(str(SCRIPTS))

import studio_store as ss  # noqa: E402
from test_studio_store import FakeDrive  # noqa: E402

from loop import analyse as an  # noqa: E402
from loop.rules import Verdict, VideoState, decide  # noqa: E402

SNAP_COLS = ("captured_at_utc,video_id,age_hours,period,impressions,ctr,views,unique_viewers,avd_s,"
             "apv_pct,ret_30s,ret_60s,src_browse,src_suggested,src_search,src_other,thumb_hash,"
             "test_running").split(",")
FEAT_COLS = ("base,variant,sha256,differs_from_main,brightness,saturation,colourfulness,warmth,"
             "background,gun_width_share,gun_angle,props,word_count,words,accent_colour,"
             "text_height_share,pushed_utc").split(",")
HIST_COLS = "thoi_gian,kenh,ten_goc,title,video_id,url,trang_thai,ghi_chu".split(",")


def utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def snap(vid, t, age, impr, ctr, h="h0", period="since_published", ret=0.6):
    return {"captured_at_utc": t, "video_id": vid, "age_hours": age, "period": period,
            "impressions": impr, "ctr": ctr, "ret_30s": ret, "thumb_hash": h, "test_running": False}


def done(vid, to, frm, at, status="ok", base="g"):
    return {"video_id": vid, "base": base, "to_variant": to, "from_variant": frm,
            "requested_utc": at, "result": {"status": status, "at_utc": at}, "_state": "done"}


def feat(base, variant, **kw):
    return {"base": base, "variant": variant, **kw}


class BuildStatesTest(unittest.TestCase):
    NOW = utc("2026-11-19T12:00:00Z")

    def test_variant_stats_split_by_swap_time(self):
        snaps = [snap("v1", "2026-11-18T10:00:00Z", 10, 1000, 0.04),
                 snap("v1", "2026-11-18T20:00:00Z", 20, 3000, 0.04),
                 snap("v1", "2026-11-19T10:00:00Z", 40, 5000, 0.072, h="h1"),
                 snap("v1", "2026-11-19T10:00:00Z", 40, 9, 0.5, period="last_48h", h="zz")]
        acts = [done("v1", "alt1", "main", "2026-11-19T01:00:00Z"),
                done("v1", "alt2", "alt1", "2026-11-19T02:00:00Z", status="error")]
        st = an.build_states(snaps, acts, [feat("g", "alt1"), feat("g", "alt2")], self.NOW,
                             bases={"v1": "g"})["v1"]
        self.assertEqual(st.variant, "alt1")
        self.assertEqual(st.swaps, 1)
        self.assertEqual(st.reverts, 0)
        self.assertEqual(st.last_swap_utc, utc("2026-11-19T01:00:00Z"))
        self.assertEqual(st.impr_at_last_swap, 5000)
        self.assertIsNone(st.manual_until)
        self.assertEqual(st.variant_stats["main"][0], 3000)
        self.assertAlmostEqual(st.variant_stats["main"][1], 0.04)
        self.assertEqual(st.variant_stats["alt1"][0], 2000)
        self.assertAlmostEqual(st.variant_stats["alt1"][1], (360 - 120) / 2000)
        self.assertEqual(st.alts_left, ["alt2"])

    def test_manual_change_detected(self):
        snaps = [snap("v1", "2026-11-18T10:00:00Z", 10, 1000, 0.04),
                 snap("v1", "2026-11-18T20:00:00Z", 20, 3000, 0.04, h="other"),
                 snap("v1", "2026-11-18T22:00:00Z", 22, 3500, 0.04, h="other")]
        st = an.build_states(snaps, [], [], self.NOW)["v1"]
        self.assertEqual(st.variant, "manual")
        self.assertEqual(st.manual_until, utc("2026-11-25T20:00:00Z"))

    def test_hash_change_with_done_action_is_not_manual(self):
        snaps = [snap("v1", "2026-11-18T10:00:00Z", 10, 1000, 0.04),
                 snap("v1", "2026-11-18T20:00:00Z", 20, 3000, 0.04, h="h1")]
        st = an.build_states(snaps, [done("v1", "alt1", "main", "2026-11-18T15:00:00Z")], [], self.NOW)["v1"]
        self.assertEqual(st.variant, "alt1")
        self.assertIsNone(st.manual_until)

    def test_revert_counted_and_pending(self):
        snaps = [snap("v1", "2026-11-18T10:00:00Z", 10, 1000, 0.04),
                 snap("v1", "2026-11-19T10:00:00Z", 40, 5000, 0.04, h="h1"),
                 snap("v1", "2026-11-19T11:00:00Z", 41, 6000, 0.04, h="h0")]
        acts = [done("v1", "alt1", "main", "2026-11-18T15:00:00Z"),
                done("v1", "main", "alt1", "2026-11-19T10:30:00Z")]
        st = an.build_states(snaps, acts, [feat("g", "alt1")], self.NOW, bases={"v1": "g"},
                             pending=["v1__20261119T100000Z.json"])["v1"]
        self.assertEqual((st.variant, st.swaps, st.reverts), ("main", 1, 1))
        self.assertEqual(st.alts_left, [])
        self.assertTrue(st.pending_action)


class HashTimelineTest(unittest.TestCase):
    NOW = utc("2026-11-20T12:00:00Z")

    def states(self, snaps, acts=(), pending=()):
        return an.build_states(snaps, list(acts), [feat("g", "alt1"), feat("g", "alt2")], self.NOW,
                               bases={"v1": "g"}, pending=pending)["v1"]

    def test_cdn_lag_after_action_is_not_manual(self):
        snaps = [snap("v1", "2026-11-19T10:00:00Z", 10, 1000, 0.04),
                 snap("v1", "2026-11-19T12:00:00Z", 12, 2000, 0.04),
                 snap("v1", "2026-11-19T14:00:00Z", 14, 3000, 0.04, h="h1"),
                 snap("v1", "2026-11-19T16:00:00Z", 16, 4000, 0.04, h="h1")]
        st = self.states(snaps, [done("v1", "alt1", "main", "2026-11-19T11:00:00Z")])
        self.assertEqual(st.variant, "alt1")
        self.assertIsNone(st.manual_until)

    def test_expectation_lapses_after_24h(self):
        snaps = [snap("v1", "2026-11-19T10:00:00Z", 10, 1000, 0.04),
                 snap("v1", "2026-11-20T14:00:00Z", 38, 3000, 0.04, h="h1"),
                 snap("v1", "2026-11-20T16:00:00Z", 40, 4000, 0.04, h="h1")]
        st = self.states(snaps, [done("v1", "alt1", "main", "2026-11-19T11:00:00Z")])
        self.assertEqual(st.variant, "manual")
        self.assertEqual(st.manual_until, utc("2026-11-27T14:00:00Z"))  # tu lan thay dau tien

    def test_one_snapshot_blip_is_not_manual(self):
        snaps = [snap("v1", "2026-11-19T10:00:00Z", 10, 1000, 0.04),
                 snap("v1", "2026-11-19T12:00:00Z", 12, 2000, 0.04, h="blip"),
                 snap("v1", "2026-11-19T14:00:00Z", 14, 3000, 0.04)]
        st = self.states(snaps)
        self.assertEqual(st.variant, "main")
        self.assertIsNone(st.manual_until)

    def test_two_consecutive_new_hashes_are_manual(self):
        snaps = [snap("v1", "2026-11-19T10:00:00Z", 10, 1000, 0.04),
                 snap("v1", "2026-11-19T12:00:00Z", 12, 2000, 0.04, h="x"),
                 snap("v1", "2026-11-19T14:00:00Z", 14, 3000, 0.04, h="x")]
        st = self.states(snaps)
        self.assertEqual(st.variant, "manual")
        self.assertEqual(st.manual_until, utc("2026-11-26T12:00:00Z"))

    def test_duplicate_ok_action_counts_once(self):
        snaps = [snap("v1", "2026-11-19T10:00:00Z", 10, 1000, 0.04),
                 snap("v1", "2026-11-19T20:00:00Z", 20, 3000, 0.04, h="h1")]
        acts = [done("v1", "alt1", "main", "2026-11-19T11:00:00Z"),
                done("v1", "alt1", "main", "2026-11-19T11:30:00Z")]
        st = self.states(snaps, acts)
        self.assertEqual((st.swaps, st.reverts), (1, 0))
        self.assertEqual(st.last_swap_utc, utc("2026-11-19T11:00:00Z"))

    def test_action_after_now_is_still_applied(self):
        snaps = [snap("v1", "2026-11-19T10:00:00Z", 10, 1000, 0.04)]
        st = self.states(snaps, [done("v1", "alt1", "main", "2026-11-20T12:00:05Z")])
        self.assertEqual((st.variant, st.swaps), ("alt1", 1))
        self.assertEqual(st.alts_left, ["alt2"])

    def test_snapshots_sorted_by_time_not_string(self):
        snaps = [snap("v1", "2026-11-19T10:00:00+07:00", 10, 1000, 0.04),
                 snap("v1", "2026-11-19T05:00:00Z", 9, 500, 0.04)]
        self.assertEqual(an.since_published(snaps)["v1"][0]["impressions"], 1000)


class AnalyseTest(unittest.TestCase):
    NOW = utc("2026-11-19T12:00:00Z")  # thu Nam

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / "cache"
        self.cfg = {"go_live": "2026-11-01", "alert_only_days": 7,
                    "report_dir": str(self.root / "reports")}
        self.drive = FakeDrive()
        self.folders = ss.studio_folders(self.drive, "CHAN")
        self.snaps, self.feats, self.hist = [], [], []
        self.health = {"status": "ok"}
        self.notify = mock.patch.object(an, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def write(self):
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / "snapshots").mkdir(exist_ok=True)

        def csv_text(cols, rows):
            return ",".join(cols) + "\n" + "".join(
                ",".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + "\n" for r in rows)
        (self.cache / "snapshots" / "2026-11-19.csv").write_text(csv_text(SNAP_COLS, self.snaps), encoding="utf-8")
        (self.cache / "thumbnail-features.csv").write_text(csv_text(FEAT_COLS, self.feats), encoding="utf-8")
        (self.cache / "upload-history.csv").write_text(
            csv_text(HIST_COLS, [{"ten_goc": b, "video_id": v} for v, b in self.hist]), encoding="utf-8")
        (self.cache / "health.json").write_text(json.dumps(self.health), encoding="utf-8")

    def low_video(self):
        self.snaps = [snap("v1", "2026-11-19T10:00:00Z", 30, 6000, 0.03)]
        self.feats = [feat("gunA", "main"), feat("gunA", "alt1")]
        self.hist = [("v1", "gunA")]
        self.write()

    def run_an(self, now=None, **kw):
        return an.analyse(self.cache, self.cfg, now or self.NOW, self.drive, self.folders, **kw)

    def report(self, now=None):
        return (self.root / "reports" / ((now or self.NOW).strftime("%Y-%m-%d") + ".md")).read_text(encoding="utf-8")

    def pending_files(self):
        return [f for f in self.drive.items.values() if self.folders["actions_pending"] in f["parents"]]

    def test_alert_only_writes_no_requests(self):
        self.cfg["go_live"] = "2026-11-17"
        self.low_video()
        rows = self.run_an()
        self.assertEqual(rows[0]["verdict"], "SWAP")
        self.assertEqual(self.pending_files(), [])
        self.assertIn("would swap", self.report())

    def test_missing_go_live_is_alert_only(self):
        del self.cfg["go_live"]
        self.low_video()
        self.run_an()
        self.assertEqual(self.pending_files(), [])

    def test_live_writes_request_json_shape(self):
        self.low_video()
        self.run_an()
        files = self.pending_files()
        self.assertEqual([f["name"] for f in files], ["v1__20261119T120000Z.json"])
        body = json.loads(files[0]["data"].decode())
        self.assertEqual(set(body), {"video_id", "base", "to_variant", "from_variant", "reason",
                                     "requested_utc"})
        self.assertEqual((body["video_id"], body["base"], body["to_variant"], body["from_variant"],
                          body["requested_utc"]), ("v1", "gunA", "alt1", "main", "2026-11-19T12:00:00Z"))
        v = self.drive.named("verdicts.csv", self.folders["studio"])[0]["data"].decode().splitlines()
        self.assertEqual(v[0], "video_id,base,verdict,reason,ctr,impressions,bar,variant,updated_utc")
        self.assertTrue(v[1].startswith("v1,gunA,SWAP,"))

    def test_dry_run_touches_neither_drive_nor_telegram(self):
        self.low_video()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_an(dry_run=True)
        self.assertIn("report:", out.getvalue())
        self.notify.assert_not_called()
        self.assertEqual(self.pending_files(), [])
        self.assertEqual(self.drive.named("verdicts.csv", self.folders["studio"]), [])
        self.assertTrue((self.root / "reports" / "2026-11-19.md").exists())

    def test_pending_blocks_revert_request(self):
        self.snaps = [snap("v1", "2026-11-18T10:00:00Z", 10, 4000, 0.09),
                      snap("v1", "2026-11-19T10:00:00Z", 40, 8000, 0.03, h="h1")]
        self.feats = [feat("gunA", "alt1")]
        self.hist = [("v1", "gunA")]
        self.write()
        a = done("v1", "alt1", "main", "2026-11-18T12:00:00Z", base="gunA")
        (self.cache / "actions" / "done").mkdir(parents=True)
        (self.cache / "actions" / "done" / "a.json").write_text(json.dumps(a), encoding="utf-8")
        (self.cache / "actions" / "pending.txt").write_text("v1__x.json\n", encoding="utf-8")
        rows = self.run_an()
        self.assertEqual(rows[0]["verdict"], "REVERT")
        self.assertEqual(self.pending_files(), [])

    def test_revert_never_targets_manual(self):
        st = VideoState(variant="alt1", swaps=1, reverts=0, last_swap_utc=None, impr_at_last_swap=0,
                        variant_stats={"alt1": (5000, 0.02), "manual": (6000, 0.09)})
        self.assertIsNone(an._revert_target(st))
        snap_ = snap("v1", "2026-11-19T10:00:00Z", 40, 11000, 0.05)
        verdict, _ = decide(snap_, st, 0.053, self.NOW, None, None)
        self.assertNotEqual(verdict, Verdict.REVERT)
        st.variant_stats["main"] = (4000, 0.06)
        self.assertEqual(an._revert_target(st), "main")

    def test_failed_request_not_repeated_within_24h(self):
        self.low_video()
        failed = done("v1", "alt1", "main", "2026-11-19T02:00:00Z", status="error", base="gunA")
        failed["_state"] = "failed"
        (self.cache / "actions" / "failed").mkdir(parents=True)
        (self.cache / "actions" / "failed" / "f.json").write_text(
            json.dumps({k: v for k, v in failed.items() if k != "_state"}), encoding="utf-8")
        rows = self.run_an()
        self.assertEqual(self.pending_files(), [])
        self.assertIn("failed within 24 h", rows[0]["reason"])
        self.run_an(now=utc("2026-11-20T04:00:00Z"))  # 26 h sau: duoc thu lai
        self.assertEqual(len(self.pending_files()), 1)

    def test_report_uses_local_date_and_monday(self):
        sunday_night = utc("2026-11-22T20:00:00Z")   # thu Hai 03:00 tai Ho Chi Minh
        self.low_video()
        self.run_an(now=sunday_night)
        self.assertIn("## Why", self.report_local("2026-11-23"))
        tuesday = utc("2026-11-23T20:00:00Z")        # thu Ba 03:00 local
        self.run_an(now=tuesday)
        self.assertNotIn("## Why", self.report_local("2026-11-24"))

    def report_local(self, day):
        return (self.root / "reports" / (day + ".md")).read_text(encoding="utf-8")

    def test_go_live_accepts_full_datetime(self):
        self.cfg["go_live"] = "2026-11-12T00:00:00Z"
        self.cfg["alert_only_days"] = 7
        self.assertFalse(an.alert_only(self.cfg, self.NOW))
        self.cfg["go_live"] = "2026-11-13T00:00:00Z"
        self.assertTrue(an.alert_only(self.cfg, self.NOW))

    def test_missing_health_notified_once_per_local_day(self):
        self.low_video()
        (self.cache / "health.json").unlink()
        self.run_an()
        self.run_an(now=utc("2026-11-19T13:00:00Z"))
        msgs = [c.args[0] for c in self.notify.call_args_list if "missing" in c.args[0]]
        self.assertEqual(len(msgs), 1)
        self.run_an(now=utc("2026-11-20T13:00:00Z"))
        msgs = [c.args[0] for c in self.notify.call_args_list if "missing" in c.args[0]]
        self.assertEqual(len(msgs), 2)

    def test_notify_once_per_verdict_change(self):
        self.low_video()
        n = self.notify
        self.run_an()
        self.assertEqual(n.call_count, 1)
        self.run_an()
        self.assertEqual(n.call_count, 1)
        self.snaps = [snap("v1", "2026-11-19T11:00:00Z", 31, 7000, 0.07)]
        self.write()
        self.run_an()
        self.assertEqual(n.call_count, 1)  # KEEP khong bao
        self.low_video()
        self.run_an()
        self.assertEqual(n.call_count, 2)  # SWAP lai sau khi doi

    def test_health_fail_auth_reported_first(self):
        self.health = {"status": "fail_auth", "detail": "token expired"}
        self.low_video()
        self.run_an()
        n = self.notify
        self.assertIn("fail_auth", n.call_args_list[0].args[0])
        rep = self.report()
        self.assertLess(rep.index("fail_auth"), rep.index("| video_id"))

    def test_why_section_names_differing_feature(self):
        monday = utc("2026-11-23T12:00:00Z")
        red = dict(background="red", gun_angle="side", props="none", accent_colour="yellow",
                   colourfulness="0.5", warmth="0.6", gun_width_share="0.4", word_count="3")
        blue = dict(red, background="blue")
        self.snaps, self.feats, self.hist = [], [], []
        for i in (1, 2):
            self.snaps.append(snap("w%d" % i, "2026-11-22T10:00:00Z", 40, 5000, 0.08))
            self.feats.append(feat("W%d" % i, "main", **red))
            self.hist.append(("w%d" % i, "W%d" % i))
        self.snaps += [snap("s", "2026-11-21T10:00:00Z", 20, 3000, 0.03),
                       snap("s", "2026-11-22T10:00:00Z", 40, 6000, 0.065, h="h1")]
        self.feats += [feat("S", "main", **blue), feat("S", "alt1", **red)]
        self.hist.append(("s", "S"))
        self.write()
        (self.cache / "actions" / "done").mkdir(parents=True)
        (self.cache / "actions" / "done" / "a.json").write_text(
            json.dumps(done("s", "alt1", "main", "2026-11-21T20:00:00Z", base="S")), encoding="utf-8")
        self.run_an(now=monday)
        rep = self.report(monday)
        self.assertIn("## Why", rep)
        self.assertIn("S main", rep)
        self.assertIn("background blue (winners: red)", rep)
        self.assertIn("S: alt1 (differs in background) 10.0% vs main 3.0%", rep)
        self.assertNotIn("## Why", self.render_thursday())

    def render_thursday(self):
        self.run_an()
        return self.report()

    def test_why_not_enough_winners(self):
        self.low_video()
        self.run_an(now=utc("2026-11-23T12:00:00Z"))
        self.assertIn("not enough winners yet", self.report(utc("2026-11-23T12:00:00Z")))


if __name__ == "__main__":
    unittest.main()
