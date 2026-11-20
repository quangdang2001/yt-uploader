#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit test cho studio_capture.py va studio_browser.py — khong trinh duyet, khong mang.

Chay:
    cd scripts
    python -m unittest test_studio_capture -v
"""
from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import studio_browser as sb  # noqa: E402
import studio_capture as sc  # noqa: E402
import studio_parse as sp  # noqa: E402
import studio_store as ss  # noqa: E402
import youtube_uploader as yu  # noqa: E402
from test_studio_store import FakeDrive  # noqa: E402

HERE = Path(__file__).resolve().parent
PARSE_DIR = HERE / "fixtures" / "studio" / "parse"
RUGER = "mwKhntLxOlk__20261120T041236Z"
CHANNEL = "Bare Skull"
UTC = timezone.utc

_QUIET = logging.NullHandler()


def setUpModule():
    # log canh bao/traceback co chu dich trong test: khong in ra man hinh
    for name in ("uploader", "studio_actions"):
        logging.getLogger(name).addHandler(_QUIET)


def tearDownModule():
    for name in ("uploader", "studio_actions"):
        logging.getLogger(name).removeHandler(_QUIET)


def fixture_payloads(stem=RUGER):
    out = []
    for suffix in ("", "_1"):
        path = PARSE_DIR / f"{stem}__youtubei_v1_browse{suffix}.json"
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def with_period(payloads, value):
    copy = json.loads(json.dumps(payloads))
    for p in copy:
        for node in sp._nodes(p):
            if node.get("key") == "time_period":
                node["value"] = value
    return copy


def dt(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def vid(video_id, public_at, privacy="public"):
    return {"video_id": video_id, "privacy": privacy, "public_at": public_at,
            "duration_s": 600, "title": video_id}


# ------------------------------------------------------------- due_videos


class DueVideosTest(unittest.TestCase):
    def due(self, videos, last, now, hour=6):
        return sc.due_videos(videos, last, now, hour, tz=UTC)

    def test_due_fresh_every_2h(self):
        now = dt("2026-11-20T12:00:00Z")
        v = [vid("a", now - timedelta(hours=10))]
        self.assertEqual(self.due(v, {}, now), ["a"])
        self.assertEqual(self.due(v, {"a": now - timedelta(hours=1)}, now), [])
        self.assertEqual(self.due(v, {"a": now - timedelta(hours=2)}, now), ["a"])
        # cho phep le jitter: 1 gio 55 phut van tinh la den han
        self.assertEqual(self.due(v, {"a": now - timedelta(minutes=115)}, now), ["a"])
        self.assertEqual(self.due(v, {"a": now - timedelta(minutes=100)}, now), [])

    def test_due_daily_after_hour(self):
        old = vid("o", dt("2026-11-01T00:00:00Z"))
        last = {"o": dt("2026-11-19T06:30:00Z")}
        self.assertEqual(self.due([old], last, dt("2026-11-20T05:59:00Z")), [])
        self.assertEqual(self.due([old], last, dt("2026-11-20T06:01:00Z")), ["o"])
        # da doc sau 06:00 hom nay -> khong doc lai trong ngay
        last = {"o": dt("2026-11-20T06:05:00Z")}
        self.assertEqual(self.due([old], last, dt("2026-11-20T20:00:00Z")), [])
        # chua tung doc -> den han ngay
        self.assertEqual(self.due([old], {}, dt("2026-11-20T03:00:00Z")), ["o"])

    def test_old_videos_least_recently_read_first(self):
        # mot luot moi ngay bi cat giua chung: luot sau phai bat dau tu video bi bo, khong doi mai
        now = dt("2026-11-20T12:00:00Z")
        vs = [vid(x, dt("2026-11-01T00:00:00Z")) for x in ("a", "b", "c", "d")]
        last = {"a": dt("2026-11-19T07:00:00Z"), "b": dt("2026-11-17T07:00:00Z"), "d": dt("2026-11-18T07:00:00Z")}
        self.assertEqual(self.due(vs, last, now), ["c", "b", "d", "a"])

    def test_due_daily_hour_is_local_time(self):
        plus7 = timezone(timedelta(hours=7))
        old = vid("o", dt("2026-11-01T00:00:00Z"))
        last = {"o": dt("2026-11-19T00:00:00Z")}   # 07:00 gio +7 ngay 19
        now = dt("2026-11-19T23:30:00Z")          # 06:30 gio +7 ngay 20
        self.assertEqual(sc.due_videos([old], last, now, 6, tz=plus7), ["o"])
        self.assertEqual(sc.due_videos([old], last, now - timedelta(hours=1), 6, tz=plus7), [])

    def test_late_public_counts_from_public_time(self):
        # tai len 10 ngay truoc o che do rieng tu, cong khai 5 gio truoc -> lich 2 gio
        now = dt("2026-11-20T12:00:00Z")
        v = [vid("late", now - timedelta(hours=5))]
        last = {"late": now - timedelta(hours=3)}
        self.assertEqual(self.due(v, last, now), ["late"])
        # cung video nhung tinh tu luc tai len thi da > 72 h, va da doc sau 06:00 -> khong den han
        v_old = [vid("late", now - timedelta(days=10))]
        self.assertEqual(self.due(v_old, last, now), [])

    def test_private_unlisted_scheduled_skipped(self):
        now = dt("2026-11-20T12:00:00Z")
        v = [vid("p", now - timedelta(hours=5), "private"),
             vid("u", now - timedelta(hours=5), "unlisted"),
             vid("s", None, "private"),
             vid("future", now + timedelta(hours=3)),
             dict(vid("short", now - timedelta(hours=5)), duration_s=45),
             vid("ok", now - timedelta(hours=5))]
        self.assertEqual(self.due(v, {}, now), ["ok"])

    def test_fresh_videos_come_first(self):
        now = dt("2026-11-20T12:00:00Z")
        v = [vid("old", now - timedelta(days=9)), vid("new", now - timedelta(hours=3))]
        self.assertEqual(self.due(v, {}, now), ["new", "old"])


# ------------------------------------------------------------- Data API


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeYouTube:
    """channels / playlistItems / videos .list(...).execute(), chia trang 50."""

    def __init__(self, resources):
        self.resources = resources   # thu tu moi nhat truoc
        self.calls = []

    def channels(self):
        yt = self

        class C:
            def list(self, **kw):
                yt.calls.append(("channels", kw))
                return _Exec({"items": [{"id": "UCabcdefghijklmnopqrstuv", "contentDetails": {
                    "relatedPlaylists": {"uploads": "UUx"}}}]})
        return C()

    def playlistItems(self):
        yt = self

        class P:
            def list(self, playlistId, maxResults=50, pageToken=None, **kw):
                yt.calls.append(("playlistItems", pageToken))
                start = int(pageToken or 0)
                page = yt.resources[start:start + maxResults]
                res = {"items": [{"contentDetails": {"videoId": r["id"]}} for r in page]}
                if start + maxResults < len(yt.resources):
                    res["nextPageToken"] = str(start + maxResults)
                return _Exec(res)
        return P()

    def videos(self):
        yt = self

        class V:
            def list(self, id, **kw):
                ids = id.split(",")
                yt.calls.append(("videos", len(ids)))
                return _Exec({"items": [r for r in yt.resources if r["id"] in ids]})
        return V()


def resource(video_id, privacy="public", published="2026-11-19T10:00:00Z",
             duration="PT10M2S", publish_at=None):
    status = {"privacyStatus": privacy}
    if publish_at:
        status["publishAt"] = publish_at
    return {"id": video_id, "status": status,
            "snippet": {"publishedAt": published, "title": "T " + video_id},
            "contentDetails": {"duration": duration}}


class ListVideosTest(unittest.TestCase):
    def test_shorts_and_unlisted_skipped(self):
        yt = FakeYouTube([
            resource("pub1"),
            resource("short", duration="PT45S"),
            resource("minute", duration="PT1M"),
            resource("unl", privacy="unlisted"),
            resource("priv", privacy="private"),
            resource("sched", privacy="private", publish_at="2026-11-25T10:00:00Z"),
            resource("pub2", duration="PT1H2M3S", published="2026-11-01T00:00:00Z"),
        ])
        got = sc.list_videos(yt)
        self.assertEqual([v["video_id"] for v in got], ["pub1", "pub2"])
        self.assertEqual(got[0]["public_at"], dt("2026-11-19T10:00:00Z"))
        self.assertEqual(got[1]["duration_s"], 3723)

    def test_uploads_capped_at_200_and_paged(self):
        yt = FakeYouTube([resource(f"v{i:03d}") for i in range(230)])
        got = sc.list_videos(yt)
        self.assertEqual(len(got), 200)
        self.assertEqual(got[0]["video_id"], "v000")
        self.assertTrue(all(n <= 50 for kind, n in yt.calls if kind == "videos"))

    def test_parse_duration(self):
        self.assertEqual(sc.parse_duration("PT59S"), 59)
        self.assertEqual(sc.parse_duration("PT1M"), 60)
        self.assertEqual(sc.parse_duration("P1DT2S"), 86402)
        self.assertIsNone(sc.parse_duration("garbage"))


# ------------------------------------------------------------- browser helpers


class FakeLocator:
    def __init__(self, n):
        self.n = n

    def count(self):
        return self.n


class FakeResp:
    def __init__(self, url, body, status=200, ctype="application/json; charset=utf-8"):
        self.url = url
        self.status = status
        self.headers = {"content-type": ctype}
        self._body = body

    def body(self):
        return json.dumps(self._body).encode("utf-8")


class FakePage:
    def __init__(self, bodies_per_goto, url_fail=0, redirect_to=None, text="Kenh"):
        self.bodies = list(bodies_per_goto)
        self.url_fail = url_fail
        self.redirect_to = redirect_to
        self.text = text
        self.handlers = []
        self._url = "about:blank"
        self.gotos = []
        self.waits = []

    @property
    def url(self):
        if self.url_fail > 0:
            self.url_fail -= 1
            raise RuntimeError("page is navigating")
        return self._url

    def on(self, event, fn):
        self.handlers.append(fn)

    def remove_listener(self, event, fn):
        self.handlers.remove(fn)

    def goto(self, url, **kw):
        self.gotos.append(url)
        self._url = self.redirect_to or url
        for body in (self.bodies.pop(0) if self.bodies else []):
            for h in list(self.handlers):
                h(FakeResp("https://studio.youtube.com/youtubei/v1/browse", body))
        # mot response khong phai Studio, phai bi bo qua
        for h in list(self.handlers):
            h(FakeResp("https://www.google.com/x", {"a": 1}))

    def wait_for_load_state(self, *a, **kw):
        pass

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def locator(self, sel):
        return FakeLocator(0)

    def inner_text(self, sel):
        return self.text

    # cuon: positions la danh sach vi tri tra ve moi buoc; het thi tang mai (khong bao gio dung)
    scroll_positions = None
    scrolls = 0
    text_after_scroll = None

    def evaluate(self, js):
        self.scrolls += 1
        if self.text_after_scroll is not None:
            self.text = self.text_after_scroll
        if self.scroll_positions is None:
            return 0                      # trang khong cuon: vi tri khong doi
        if self.scroll_positions:
            return self.scroll_positions.pop(0)
        return self.scrolls * 1200


class BrowserTest(unittest.TestCase):
    def test_capture_tabs_visits_all_tabs_and_scrubs(self):
        page = FakePage([[{"a": 1, "xsrfToken": "secret"}], [{"b": 2}]])
        got = sb.capture_tabs(page, "abc123")
        self.assertEqual(got, [{"a": 1, "xsrfToken": "<scrubbed>"}, {"b": 2}])
        self.assertEqual(page.gotos, [
            "https://studio.youtube.com/video/abc123/analytics/tab-reach_viewers/period-default",
            "https://studio.youtube.com/video/abc123/analytics/tab-interest_viewers/period-default",
            "https://studio.youtube.com/video/abc123/analytics/tab-build_audience/period-default",
        ])
        self.assertEqual(page.handlers, [])            # listener go ra sau khi xong
        self.assertIn(sb.SETTLE_MS, page.waits)

    def test_transient_unreadable_page_repolled_once(self):
        page = FakePage([[{"a": 1}], [{"b": 2}]], url_fail=1)
        self.assertEqual(sb.capture_tabs(page, "abc123"), [{"a": 1}, {"b": 2}])
        self.assertIn(sb.REPOLL_MS, page.waits)
        # doc khong duoc hai lan lien -> loi doc (NavError), khong phai dang nhap
        page = FakePage([[{"a": 1}], [{"b": 2}]], url_fail=2)
        with self.assertRaises(sb.NavError):
            sb.capture_tabs(page, "abc123")

    def test_capture_tabs_sign_in_raises_blocked(self):
        page = FakePage([[{"a": 1}]], redirect_to="https://accounts.google.com/ServiceLogin")
        with self.assertRaises(sb.Blocked):
            sb.capture_tabs(page, "abc123")
        page = FakePage([[{"a": 1}]], text="Verify it's you")
        with self.assertRaises(sb.Blocked):
            sb.capture_tabs(page, "abc123")

    def test_vietnamese_challenge_text_is_blocked(self):
        import unicodedata
        for text in ("Xác minh đó là bạn", "Đăng nhập để tiếp tục đến YouTube", "Tôi không phải là người máy",
                     "Chọn tài khoản", "Lưu lượng truy cập bất thường"):
            for form in ("NFC", "NFD"):         # a page can hand back decomposed accents
                page = FakePage([[{"a": 1}]], text="Studio\n" + unicodedata.normalize(form, text))
                with self.assertRaises(sb.Blocked, msg=f"{form} {text}"):
                    sb.capture_tabs(page, "abc123")
        self.assertEqual(sb.capture_tabs(FakePage([[{"a": 1}], []], text="Kênh của bạn - Số liệu phân tích"),
                                         "abc123"), [{"a": 1}])

    def test_vietnamese_menu_and_banner_text_is_not_blocked(self):
        text = "Kênh của bạn\nĐăng nhập\nCảnh báo: hoạt động bất thường của video đã được xem xét\nSố liệu"
        page = FakePage([[{"a": 1}], []], text=text)
        self.assertEqual(sb.capture_tabs(page, "abc123"), [{"a": 1}])

    def test_navigation_error_raises_nav_error(self):
        page = FakePage([])

        def boom(url, **kw):
            raise TimeoutError("net::ERR_TIMED_OUT")
        page.goto = boom
        with self.assertRaises(sb.NavError):
            sb.capture_tabs(page, "abc123")

    def test_scroll_stops_when_position_stops_changing(self):
        page = FakePage([])
        page.scroll_positions = [1200, 2400, 3000, 3000, 9999]
        steps = sb.scroll_to_bottom(page)
        self.assertEqual(steps, 4)                      # buoc 4 lap lai 3000 -> dung
        self.assertEqual(page.scrolls, 4)
        self.assertEqual(page.waits.count(sb.SCROLL_STEP_MS), 4)

    def test_scroll_stops_at_twelve_steps(self):
        page = FakePage([])
        page.scroll_positions = []                      # luon cuon duoc, khong bao gio toi day
        self.assertEqual(sb.scroll_to_bottom(page), 12)
        self.assertEqual(page.scrolls, 12)

    def test_scroll_phase_capped_by_seconds(self):
        page = FakePage([])
        page.scroll_positions = []
        ticks = iter(range(0, 1000, 10))               # moi lan doi dong ho tang 10 s
        steps = sb.scroll_to_bottom(page, cap_s=25, clock=lambda: next(ticks))
        self.assertLess(steps, 12)

    def test_scroll_survives_evaluate_errors(self):
        page = FakePage([])

        def boom(js):
            raise RuntimeError("context destroyed")
        page.evaluate = boom
        self.assertEqual(sb.scroll_to_bottom(page), 1)

    def test_no_scroller_falls_back_to_wheel_and_logs(self):
        calls = []

        class Mouse:
            def move(self, x, y):
                calls.append(("move", x, y))

            def wheel(self, dx, dy):
                calls.append(("wheel", dx, dy))
        page = FakePage([])
        page.mouse = Mouse()
        page.viewport_size = {"width": 1000, "height": 600}
        page.scroll_positions = []
        page.evaluate = lambda js: 0                     # khong tim thay phan tu cuon
        with self.assertLogs("uploader.studio_browser", level="INFO") as cm:
            steps = sb.scroll_to_bottom(page)
        self.assertEqual(steps, sb.WHEEL_MAX_STEPS)
        self.assertEqual(calls[0], ("move", 500, 300))
        self.assertEqual([c for c in calls if c[0] == "wheel"],
                         [("wheel", 0, sb.SCROLL_STEP_PX)] * sb.WHEEL_MAX_STEPS)
        self.assertEqual(len(cm.records), 1)
        self.assertIn("cach=wheel", cm.records[0].getMessage())
        # khong co chuot -> cach=none
        page = FakePage([])
        page.evaluate = lambda js: 0
        with self.assertLogs("uploader.studio_browser", level="INFO") as cm:
            sb.scroll_to_bottom(page)
        self.assertIn("cach=none", cm.records[0].getMessage())

    def test_idle_wait_counts_inside_scroll_budget(self):
        page = FakePage([])
        page._url = "https://studio.youtube.com/video/x"
        timeouts = []
        page.wait_for_load_state = lambda state, timeout=None: timeouts.append(timeout)
        page.scroll_positions = [1200, 1200]
        # da dung 20 s cho cuon: chi con 5 s cho mang yen
        seq = iter([0, 0, 20, 20, 20, 20, 20, 20])
        sb.scroll_phase(page, clock=lambda: next(seq, 20))
        self.assertEqual(timeouts, [5000])

    def test_third_tab_failure_does_not_fail_capture(self):
        page = FakePage([[{"a": 1}], [{"b": 2}]])
        real = page.goto

        def goto(url, **kw):
            if "build_audience" in url:
                raise TimeoutError("net::ERR_TIMED_OUT")
            return real(url, **kw)
        page.goto = goto
        self.assertEqual(sb.capture_tabs(page, "abc123"), [{"a": 1}, {"b": 2}])
        self.assertEqual(page.handlers, [])
        # tab bat buoc loi thi van la NavError
        page = FakePage([])

        def boom(url, **kw):
            raise TimeoutError("x")
        page.goto = boom
        with self.assertRaises(sb.NavError):
            sb.capture_tabs(page, "abc123")

    def test_third_tab_challenge_still_blocks(self):
        page = FakePage([[{"a": 1}], [{"b": 2}]])
        real = page.goto

        def goto(url, **kw):
            real(url, **kw)
            if "build_audience" in url:
                page._url = "https://accounts.google.com/ServiceLogin"
        page.goto = goto
        with self.assertRaises(sb.Blocked):
            sb.capture_tabs(page, "abc123")

    def test_blocked_check_runs_after_scrolling(self):
        page = FakePage([[{"a": 1}]])
        page.text_after_scroll = "Verify it's you"      # thu thach hien ra khi cuon
        with self.assertRaises(sb.Blocked):
            sb.capture_tabs(page, "abc123")
        self.assertGreaterEqual(page.scrolls, 1)

    def test_capture_scrolls_every_tab(self):
        page = FakePage([])
        page.scroll_positions = []
        sb.capture_tabs(page, "abc123")
        self.assertGreaterEqual(page.scrolls, 3 * 2)

    def test_spike_uses_shared_helpers(self):
        import studio_spike
        for name in ("wanted", "scrub", "blocked_reason"):
            self.assertIs(getattr(studio_spike, name), getattr(sb, name))
        self.assertEqual(studio_spike.TABS, sb.TABS)
        res = subprocess.run([sys.executable, str(HERE / "studio_spike.py"), "--help"],
                             capture_output=True, text=True, cwd=str(HERE))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("--login", res.stdout)


# ------------------------------------------------------------- run()


class Clock:
    def __init__(self, start):
        self.t = start
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += timedelta(seconds=s)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.cfg = self.home / "cfg.json"
        self.cfg.write_text(json.dumps({"channel": CHANNEL, "daily_hour": 6,
                                        "profile_dir": str(self.home / "profile")}),
                            encoding="utf-8")
        self.drive = FakeDrive()
        # playlist uploads: moi nhat truoc
        self.yt = FakeYouTube([resource("vidB0000002", published="2026-11-20T09:00:00Z"),
                               resource("vidA0000001", published="2026-11-20T08:00:00Z")])
        self.clock = Clock(dt("2026-11-20T12:00:00Z"))
        self.sent = []
        self.captured = []
        self.capture_plan = {}     # video_id -> list cua (payloads | Exception), lan luot
        self.browser_opened = 0
        self.actions_calls = []
        self.actions_states = []
        self.actions_error = None
        self.thumb_error = None
        self.channel_reads = []
        self.channel_result = None   # None -> mot the gia; Exception -> nem ra

    # -- fakes

    @contextmanager
    def fake_open_browser(self, profile_dir):
        self.browser_opened += 1
        yield "PAGE"

    def fake_capture(self, page, video_id):
        self.captured.append(video_id)
        plan = self.capture_plan.get(video_id)
        item = plan.pop(0) if plan else fixture_payloads()
        if isinstance(item, Exception):
            raise item
        return item

    def fake_capture_channel(self, page, channel_id):
        self.channel_reads.append(channel_id)
        if isinstance(self.channel_result, Exception):
            raise self.channel_result
        if self.channel_result is not None:
            return self.channel_result
        card = {"cardViewModel": {"accessibilityId": "analytics_hub.FEanalytics_hub_channel_overview.key_metric_views",
                "bodyContent": {"simpleMetricViewModel": {"value": {"content": "1,2 N"}}}}}
        return {"hub_overview": [card]}, {}

    def fake_actions(self, drive, youtube, chan, done, tmpdir, state=None):
        self.actions_calls.append((chan, done))
        self.actions_states.append(state)
        if self.actions_error:
            raise self.actions_error
        return 0, 0

    def fake_fetch(self, url):
        if self.thumb_error:
            raise self.thumb_error
        return b"JPEG:" + url.encode()

    def deps(self, **over):
        d = sc.Deps(drive=self.drive, youtube=self.yt,
                    open_browser=self.fake_open_browser, capture=self.fake_capture,
                    capture_channel=self.fake_capture_channel,
                    now=self.clock.now, sleep=self.clock.sleep, notify=self.sent.append,
                    fetch_thumb=self.fake_fetch, run_actions=self.fake_actions,
                    home=self.home, tz=UTC, uniform=lambda a, b: a)
        for k, v in over.items():
            setattr(d, k, v)
        return d

    def args(self, *extra):
        return sc.parse_args(["--channel", CHANNEL, "--config", str(self.cfg),
                              "--once", "--no-jitter", *extra])

    def run_it(self, *extra, **over):
        with redirect_stdout(io.StringIO()) as out:
            rc = sc.run(self.args(*extra), self.deps(**over))
        self.out = out.getvalue()
        return rc

    # -- helpers

    def folders(self):
        f = yu.resolve_folders(self.drive, "YouTube-Upload", CHANNEL)
        return f, ss.studio_folders(self.drive, f["channel"])

    def snapshot_text(self, day="2026-11-20"):
        _, sf = self.folders()
        hits = self.drive.named(f"{day}.csv", sf["snapshots"])
        return hits[0]["data"].decode("utf-8") if hits else None

    def health(self):
        _, sf = self.folders()
        hits = self.drive.named("health.json", sf["studio"])
        return json.loads(hits[0]["data"]) if hits else None

    def state(self):
        p = self.home / f".studio-state-{CHANNEL}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    # -- tests

    def channel_doc(self, day="2026-11-20"):
        _, sf = self.folders()
        hits = self.drive.named(f"{day}.json", sf["channel"])
        return json.loads(hits[0]["data"]) if hits else None

    def test_channel_read_once_a_day_before_videos(self):
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(self.channel_reads, ["UCabcdefghijklmnopqrstuv"])
        doc = self.channel_doc()
        self.assertEqual(doc["tabs"]["hub_overview"][0]["value"], 1200)
        self.assertEqual(doc["captured_at_utc"], "2026-11-20T12:00:00Z")
        self.assertEqual(self.state()["channel_last"], "2026-11-20T12:00:00Z")
        self.assertIn("channel ok (1 cards", self.health()["detail"])
        # cung ngay: khong doc lai; sau daily_hour hom sau: doc lai
        self.clock.t = dt("2026-11-20T20:00:00Z")
        self.run_it()
        self.assertEqual(len(self.channel_reads), 1)
        self.clock.t = dt("2026-11-21T07:00:00Z")
        self.run_it()
        self.assertEqual(len(self.channel_reads), 2)
        self.assertIsNotNone(self.channel_doc("2026-11-21"))

    def test_channel_error_is_a_note_and_videos_still_read(self):
        self.channel_result = RuntimeError("tab broke")
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(self.captured, ["vidB0000002", "vidA0000001"])
        h = self.health()
        self.assertEqual(h["status"], "PASS")
        self.assertIn("channel: RuntimeError: tab broke", h["detail"])
        self.assertNotIn("channel_last", self.state())

    def test_channel_blocked_stops_the_run(self):
        self.channel_result = sb.Blocked("trang co o nhap mat khau")
        self.assertEqual(self.run_it(), 3)
        self.assertEqual(self.captured, [])
        self.assertEqual(self.health()["status"], "FAIL auth")

    def test_channel_with_no_cards_is_not_written(self):
        self.channel_result = ({"hub_overview": []}, {"hub_content": "NavError: x"})
        self.assertEqual(self.run_it(), 0)
        self.assertIsNone(self.channel_doc())
        detail = self.health()["detail"]
        self.assertIn("channel tabs failed: hub_content", detail)
        self.assertIn("channel: no cards read", detail)

    def test_only_mode_skips_channel(self):
        self.run_it("--only", "vidA0000001")
        self.assertEqual(self.channel_reads, [])

    def test_happy_path_rows_raw_health_state(self):
        rc = self.run_it()
        self.assertEqual(rc, 0)
        self.assertEqual(self.captured, ["vidB0000002", "vidA0000001"])
        text = self.snapshot_text()
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], ",".join(sp.SNAPSHOT_COLS))
        self.assertEqual(len(lines), 3)
        row = dict(zip(sp.SNAPSHOT_COLS, lines[1].split(",")))
        self.assertEqual(row["video_id"], "vidB0000002")
        self.assertEqual(row["captured_at_utc"], "2026-11-20T12:00:00Z")
        self.assertEqual(row["age_hours"], "3.0")
        self.assertEqual(row["impressions"], "159")
        self.assertEqual(len(row["thumb_hash"]), 64)
        h = self.health()
        self.assertEqual((h["status"], h["videos_read"], h["videos_failed"]), ("PASS", 2, 0))
        st = self.state()
        self.assertEqual(set(st["last"]), {"vidA0000001", "vidB0000002"})
        _, sf = self.folders()
        day = self.drive.named("2026-11-20", sf["raw"])
        self.assertEqual(len(day), 1)
        raws = [f for f in self.drive.items.values() if day[0]["id"] in f["parents"]]
        self.assertEqual(len(raws), 3)   # 2 video + trang kenh
        self.assertEqual(self.browser_opened, 1)
        self.assertEqual(len(self.actions_calls), 1)
        # lan chay thu hai ngay sau: khong video nao den han
        self.captured.clear()
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(self.captured, [])
        self.assertEqual(self.browser_opened, 1)   # khong mo trinh duyet khi khong co gi

    def test_pause_between_videos_and_jitter(self):
        self.run_it()
        self.assertEqual(self.clock.slept, [5])            # mot lan nghi giua hai video
        self.clock = Clock(dt("2026-11-20T15:00:00Z"))
        with redirect_stdout(io.StringIO()):
            sc.run(sc.parse_args(["--channel", CHANNEL, "--config", str(self.cfg), "--once"]),
                   self.deps(uniform=lambda a, b: b))
        self.assertEqual(self.clock.slept, [600, 15])

    def test_rows_batched_one_append_per_day(self):
        calls = []
        real = ss.append_snapshot_rows

        def spy(drive, folders, day, cols, rows):
            calls.append((day, [r["video_id"] for r in rows]))
            return real(drive, folders, day, cols, rows)
        with mock.patch.object(ss, "append_snapshot_rows", spy):
            self.assertEqual(self.run_it(), 0)
        self.assertEqual(calls, [("2026-11-20", ["vidB0000002", "vidA0000001"])])
        # qua nua dem UTC giua hai video -> mot lan cho moi ngay
        calls.clear()
        self.clock = Clock(dt("2026-11-20T23:59:58Z"))
        (self.home / f".studio-state-{CHANNEL}.json").unlink()
        with mock.patch.object(ss, "append_snapshot_rows", spy):
            self.assertEqual(self.run_it(), 0)
        self.assertEqual(calls, [("2026-11-20", ["vidB0000002"]),
                                 ("2026-11-21", ["vidA0000001"])])

    def test_period_comes_from_payload(self):
        self.capture_plan = {"vidB0000002": [with_period(fixture_payloads(), "last_28_days")]}
        self.assertEqual(self.run_it(), 0)
        lines = self.snapshot_text().strip().splitlines()[1:]
        periods = {dict(zip(sp.SNAPSHOT_COLS, ln.split(",")))["video_id"]:
                   dict(zip(sp.SNAPSHOT_COLS, ln.split(",")))["period"] for ln in lines}
        self.assertEqual(periods, {"vidB0000002": "last_28_days",
                                   "vidA0000001": "since_published"})

    def test_thumb_hash_failure_keeps_row(self):
        self.thumb_error = urllib.error.URLError("down")
        self.assertEqual(self.run_it(), 0)
        lines = self.snapshot_text().strip().splitlines()
        self.assertEqual(len(lines), 3)
        row = dict(zip(sp.SNAPSHOT_COLS, lines[1].split(",")))
        self.assertEqual(row["thumb_hash"], "")
        self.assertEqual(row["impressions"], "159")

    def test_thumb_hash_always_hqdefault(self):
        # one fixed rendition: a maxres image appearing later can never flip the hash
        seen = []

        def fetch(url):
            seen.append(url)
            return b"MAX" if "maxres" in url else b"HQ"
        import hashlib
        for _ in range(2):
            self.assertEqual(sc.thumb_hash("abc", fetch), hashlib.sha256(b"HQ").hexdigest())
        self.assertEqual([u.split("?")[0] for u in seen], ["https://i.ytimg.com/vi/abc/hqdefault.jpg"] * 2)
        # the capture itself hashes that URL
        self.thumb_urls = []
        self.assertEqual(self.run_it(fetch_thumb=lambda url: self.thumb_urls.append(url) or b"HQ"), 0)
        self.assertEqual(sorted(u.split("?")[0] for u in self.thumb_urls),
                         ["https://i.ytimg.com/vi/vidA0000001/hqdefault.jpg",
                          "https://i.ytimg.com/vi/vidB0000002/hqdefault.jpg"])

        def missing(url):
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        self.assertIsNone(sc.thumb_hash("abc", missing))       # no fallback to another rendition

        def bad(url):
            raise urllib.error.HTTPError(url, 500, "x", {}, None)
        self.assertIsNone(sc.thumb_hash("abc", bad))

    def test_lock_held_exits_2_writes_nothing(self):
        (self.home / f".studio-capture-{CHANNEL}.lock").write_text("123")
        self.assertEqual(self.run_it(), 2)
        self.assertEqual(self.drive.items, {})
        self.assertEqual(self.captured, [])
        self.assertIsNone(self.state())
        self.assertTrue((self.home / f".studio-capture-{CHANNEL}.lock").exists())

    def test_uploader_lock_held_skips(self):
        (self.home / f".uploader-{CHANNEL}.lock").write_text("123")
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(self.captured, [])
        self.assertEqual(self.browser_opened, 0)
        self.assertEqual(self.drive.items, {})
        self.assertFalse((self.home / f".studio-capture-{CHANNEL}.lock").exists())

    def test_uploader_lock_skips_capture(self):
        # lock cua uploader cu hon 6 gio = tien trinh da chet -> van doc
        lock = self.home / f".uploader-{CHANNEL}.lock"
        lock.write_text("123")
        old = time.time() - 7 * 3600
        os.utime(lock, (old, old))
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(len(self.captured), 2)
        # uploader bat dau giua chung va da chay > 60 s -> dung doc video tiep theo,
        # van ghi dong da co
        self.captured.clear()
        (self.home / f".studio-state-{CHANNEL}.json").unlink()

        def capture_then_upload(page, video_id, age=120):
            lock.write_text("456")
            t = time.time() - age
            os.utime(lock, (t, t))
            return self.fake_capture(page, video_id)
        self.assertEqual(self.run_it(capture=capture_then_upload), 0)
        self.assertEqual(self.captured, ["vidB0000002"])
        self.assertIn("vidB0000002", self.snapshot_text())

    def test_midrun_uploader_lock_under_60s_does_not_stop(self):
        lock = self.home / f".uploader-{CHANNEL}.lock"

        def capture_then_quick_scan(page, video_id):
            lock.write_text("456")       # moi tao: uploader chi quet pending/ vai giay
            return self.fake_capture(page, video_id)
        self.assertEqual(self.run_it(capture=capture_then_quick_scan), 0)
        self.assertEqual(self.captured, ["vidB0000002", "vidA0000001"])

    def test_auth_exit_3_health_fail_notify(self):
        self.capture_plan = {"vidB0000002": [sb.Blocked("URL co dau hieu dang nhap")]}
        self.assertEqual(self.run_it(), 3)
        h = self.health()
        self.assertEqual(h["status"], "FAIL auth")
        self.assertIn("dang nhap", h["detail"])
        self.assertEqual(self.sent, [sc.AUTH_MSG])
        self.assertEqual(self.captured, ["vidB0000002"])   # dung ngay, khong doc video sau
        self.assertEqual(self.actions_calls, [])
        self.assertIsNone(self.snapshot_text())

    def test_auth_block_holds_until_profile_changes(self):
        prof = self.home / "profile" / "Default"
        prof.mkdir(parents=True)
        cookies = prof / "Cookies"
        cookies.write_bytes(b"x")
        self.capture_plan = {"vidB0000002": [sb.Blocked("signin")]}
        self.assertEqual(self.run_it(), 3)
        self.assertEqual(self.actions_calls, [])
        # lan sau: khong mo trinh duyet, khong bao lai; van chep history va chay bo thuc thi
        f, sf = self.folders()
        self.drive._add(yu.HISTORY_NAME, [f["channel"]], "text/csv", b"h\n")
        self.assertEqual(self.run_it(), 3)
        self.assertEqual(self.browser_opened, 1)
        self.assertEqual(self.sent, [sc.AUTH_MSG])
        self.assertEqual(len(self.actions_calls), 1)
        self.assertEqual(len(self.drive.named(yu.HISTORY_NAME, sf["studio"])), 1)
        self.assertEqual(self.health()["status"], "FAIL auth")
        # chu kenh dang nhap lai -> profile doi -> doc lai binh thuong
        later = time.time() + 60
        os.utime(cookies, (later, later))
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(self.health()["status"], "PASS")
        self.assertNotIn("auth_blocked", self.state())

    def test_failed_read_twice_notifies_no_zero_rows(self):
        bad = sp.ParseError("missing metric ctr")
        self.capture_plan = {"vidA0000001": [bad, bad, bad]}
        self.assertEqual(self.run_it(), 0)
        h = self.health()
        self.assertEqual((h["status"], h["videos_read"], h["videos_failed"]), ("FAIL read", 1, 1))
        self.assertIn("vidA0000001", h["detail"])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.state()["failed"], {"vidA0000001": 1})
        self.assertNotIn("vidA0000001", self.state()["last"])
        self.assertNotIn("vidA0000001", self.snapshot_text())
        # lan hai (het chu ky 2 gio): video A van den han (khong co dong), bao Telegram mot lan
        self.clock.t += timedelta(hours=2)
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("vidA0000001", self.sent[0])
        # lan ba: khong bao lai
        self.clock.t += timedelta(hours=2)
        self.run_it()
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("vidA0000001", self.snapshot_text())
        # doc duoc -> xoa dem va lan thu
        self.clock.t += timedelta(hours=2)
        self.run_it()
        self.assertNotIn("vidA0000001", self.state()["failed"])
        self.assertNotIn("vidA0000001", self.state()["last_attempt"])
        self.assertIn("vidA0000001", self.snapshot_text())

    def test_navigation_error_exit_4_no_row(self):
        self.capture_plan = {"vidA0000001": [sb.NavError("net::ERR_TIMED_OUT")]}
        self.assertEqual(self.run_it(), 4)
        self.assertEqual(self.health()["status"], "FAIL read")
        self.assertNotIn("vidA0000001", self.snapshot_text())
        self.assertEqual(len(self.actions_calls), 1)

    def test_browser_start_failure(self):
        @contextmanager
        def broken(profile):
            raise RuntimeError("profile in use")
            yield
        self.assertEqual(self.run_it(open_browser=broken), 4)
        self.assertEqual(self.health()["status"], "FAIL read")
        self.assertIn("profile in use", self.health()["detail"])
        self.assertEqual(self.sent, [])                  # lan dau: co the chu kenh dang mo profile
        self.assertEqual(len(self.actions_calls), 1)     # bo thuc thi van chay
        self.assertEqual(self.run_it(open_browser=broken), 4)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("profile in use", self.sent[0])
        self.assertEqual(self.run_it(), 0)
        self.assertNotIn("browser_failed", self.state())

    def test_history_copied_into_studio(self):
        f, sf = self.folders()
        self.drive._add(yu.HISTORY_NAME, [f["channel"]], "text/csv", b"thoi_gian,kenh\n1,2\n")
        self.assertEqual(self.run_it(), 0)
        hits = self.drive.named(yu.HISTORY_NAME, sf["studio"])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["data"], b"thoi_gian,kenh\n1,2\n")

    def test_history_missing_is_not_an_error(self):
        self.assertEqual(self.run_it(), 0)
        _, sf = self.folders()
        self.assertEqual(self.drive.named(yu.HISTORY_NAME, sf["studio"]), [])

    def test_executor_error_does_not_change_exit(self):
        self.actions_error = RuntimeError("drive 500")
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(len(self.actions_calls), 1)
        self.assertTrue(any("drive 500" in m for m in self.sent))
        self.actions_error = None
        self.assertEqual(self.run_it("--no-actions"), 0)
        self.assertEqual(len(self.actions_calls), 1)

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.run_it("--dry-run"), 0)
        self.assertIsNone(self.state())
        self.assertIsNone(self.snapshot_text())
        self.assertIsNone(self.health())
        self.assertEqual(self.actions_calls, [])
        self.assertIn("vidB0000002", self.out)
        self.assertIn("vidA0000001", self.out)

    def test_only_ignores_schedule(self):
        self.run_it()
        self.captured.clear()
        self.assertEqual(self.run_it("--only", "vidA0000001"), 0)
        self.assertEqual(self.captured, ["vidA0000001"])
        # --only khong doc video khong cong khai
        self.yt.resources.append(resource("privX000001", privacy="private"))
        self.captured.clear()
        self.assertEqual(self.run_it("--only", "privX000001"), 1)
        self.assertEqual(self.captured, [])

    def test_budget_stops_new_videos(self):
        def slow_capture(page, video_id):
            self.clock.t += timedelta(seconds=sc.CAPTURE_CAP_S + 1)
            return self.fake_capture(page, video_id)
        self.assertEqual(self.run_it(capture=slow_capture), 0)
        self.assertEqual(self.captured, ["vidB0000002"])
        self.assertIn("vidB0000002", self.snapshot_text())
        self.assertIn("time cap", self.health()["detail"])

    def test_cap_counts_jitter(self):
        # jitter 10 phut + 26 phut doc video dau = 36 phut tu luc khoi dong > 35 -> dung
        def capture_26min(page, video_id):
            self.clock.t += timedelta(minutes=26)
            return self.fake_capture(page, video_id)
        args = sc.parse_args(["--channel", CHANNEL, "--config", str(self.cfg), "--once"])
        with redirect_stdout(io.StringIO()):
            rc = sc.run(args, self.deps(capture=capture_26min, uniform=lambda a, b: b))
        self.assertEqual(rc, 0)
        self.assertEqual(self.captured, ["vidB0000002"])
        # khong jitter: 26 phut + nghi < 35 -> doc ca hai
        self.captured.clear()
        (self.home / f".studio-state-{CHANNEL}.json").unlink()
        self.assertEqual(self.run_it(capture=capture_26min), 0)
        self.assertEqual(self.captured, ["vidB0000002", "vidA0000001"])

    def test_watchdog_releases_actions_lock(self):
        # Lock that (fd con mo, nhu luc watchdog ban that). Tren Windows khong dong fd
        # thi unlink loi WinError 32 va lock nam lai 6 gio.
        cap = sc.acquire_lock(sc.lock_path(self.home, CHANNEL))
        act = sc.acquire_lock(sc.actions_lock_path(self.home, CHANNEL))
        self.assertIsNotNone(cap)
        self.assertIsNotNone(act)
        self.assertIsNotNone(act.fd)
        other = self.home / ".studio-actions-Other.lock"   # cua tien trinh khac, khong dang ky
        other.write_text("999999")
        exits = []
        try:
            sc.watchdog_fire(self.sent.append, exit_fn=exits.append)
        finally:
            for lk in (cap, act):
                sc.release_lock(lk)
        self.assertEqual(exits, [1])
        self.assertFalse(sc.lock_path(self.home, CHANNEL).exists())
        self.assertFalse(sc.actions_lock_path(self.home, CHANNEL).exists())
        self.assertTrue(other.exists())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(sc._HELD, {})

    def test_watchdog_keeps_lock_with_other_pid(self):
        act = sc.acquire_lock(sc.actions_lock_path(self.home, CHANNEL))
        try:
            os.lseek(act.fd, 0, os.SEEK_SET)
            os.write(act.fd, b"999999")          # file khong con ghi PID nay
            sc.watchdog_fire(self.sent.append, exit_fn=lambda code: None)
            self.assertIsNone(act.fd)            # fd van duoc dong
            self.assertTrue(sc.actions_lock_path(self.home, CHANNEL).exists())
        finally:
            sc.release_lock(act)

    def test_watchdog_during_run_frees_both_locks(self):
        exits = []

        def actions_then_watchdog(drive, youtube, chan, done, tmpdir, state=None):
            self.assertTrue(sc.lock_path(self.home, CHANNEL).exists())
            self.assertTrue(sc.actions_lock_path(self.home, CHANNEL).exists())
            sc.watchdog_fire(self.sent.append, exit_fn=exits.append)
            self.assertFalse(sc.lock_path(self.home, CHANNEL).exists())
            self.assertFalse(sc.actions_lock_path(self.home, CHANNEL).exists())
            return 0, 0
        self.assertEqual(self.run_it(run_actions=actions_then_watchdog), 0)
        self.assertEqual(exits, [1])
        self.assertEqual(sc._HELD, {})

    # -- token

    def write_token(self, name, text):
        p = self.home / name
        p.write_text(text, encoding="utf-8")
        return p

    def token_args(self, drive_tok, yt_tok):
        return sc.parse_args(["--channel", CHANNEL, "--config", str(self.cfg), "--once",
                              "--no-jitter", "--token-drive", str(drive_tok),
                              "--token-youtube", str(yt_tok)])

    def test_bad_token_never_prompts_and_exits_3(self):
        bad = self.write_token("token-drive.json", "{not json")
        expired = json.dumps({
            "token": "x", "refresh_token": "r", "client_id": "c", "client_secret": "s",
            "token_uri": "https://oauth2.googleapis.com/token",
            "expiry": "2020-01-01T00:00:00Z", "scopes": yu.YOUTUBE_SCOPES})
        exp = self.write_token("token-youtube.json", expired)
        before = (bad.read_bytes(), exp.read_bytes())
        from google.auth.exceptions import RefreshError
        from google.oauth2.credentials import Credentials

        def no_prompt(*a, **kw):
            raise AssertionError("OAuth browser flow must never start")
        with mock.patch.object(yu.InstalledAppFlow, "from_client_secrets_file", no_prompt), \
             mock.patch.object(yu, "get_credentials", no_prompt), \
             mock.patch.object(Credentials, "refresh", side_effect=RefreshError("invalid_grant")):
            # token Drive hong
            with redirect_stdout(io.StringIO()):
                rc = sc.run(self.token_args(bad, exp), self.deps(drive=None, youtube=None,
                                                                 connect=sc.connect))
            self.assertEqual(rc, 3)
            self.assertEqual(self.sent, [sc.TOKEN_MSG])
            # token YouTube het han, refresh loi: cung khong mo trinh duyet
            with self.assertRaises(sc.TokenError) as cm:
                sc.load_creds(exp, yu.YOUTUBE_SCOPES, "YouTube")
            self.assertIn("refresh failed", str(cm.exception))
            # cung ngay: khong bao lai
            with redirect_stdout(io.StringIO()):
                rc = sc.run(self.token_args(bad, exp), self.deps(drive=None, youtube=None,
                                                                 connect=sc.connect))
            self.assertEqual(rc, 3)
            self.assertEqual(self.sent, [sc.TOKEN_MSG])
        self.assertEqual((bad.read_bytes(), exp.read_bytes()), before)   # khong ghi lai token
        self.assertEqual(self.captured, [])
        self.assertEqual(self.actions_calls, [])

    def test_token_failure_writes_health_when_drive_available(self):
        def connect(args):
            raise sc.TokenError("YouTube: refresh failed (invalid_grant)", drive=self.drive)
        self.assertEqual(self.run_it(drive=None, youtube=None, connect=connect), 3)
        h = self.health()
        self.assertEqual(h["status"], "FAIL auth")
        self.assertEqual(h["detail"], "token: YouTube: refresh failed (invalid_grant)")
        self.assertEqual(self.sent, [sc.TOKEN_MSG])

    def test_network_blip_is_api_not_token(self):
        from google.auth.exceptions import TransportError
        from google.oauth2.credentials import Credentials
        expired = json.dumps({
            "token": "x", "refresh_token": "r", "client_id": "c", "client_secret": "s",
            "token_uri": "https://oauth2.googleapis.com/token",
            "expiry": "2020-01-01T00:00:00Z", "scopes": yu.DRIVE_SCOPES})
        dtok = self.write_token("token-drive.json", expired)
        ytok = self.write_token("token-youtube.json", expired)
        with mock.patch.object(Credentials, "refresh",
                               side_effect=TransportError("connection reset")):
            with self.assertRaises(TransportError):          # khong bi doi thanh TokenError
                sc.load_creds(dtok, yu.DRIVE_SCOPES, "Drive")
            with redirect_stdout(io.StringIO()):
                rc = sc.run(self.token_args(dtok, ytok),
                            self.deps(drive=None, youtube=None, connect=sc.connect))
            self.assertEqual(rc, 1)
            with redirect_stdout(io.StringIO()):
                sc.run(self.token_args(dtok, ytok),
                       self.deps(drive=None, youtube=None, connect=sc.connect))
        self.assertEqual(len(self.sent), 1)                  # mot lan moi ngay
        self.assertNotIn(sc.TOKEN_MSG, self.sent)
        self.assertIn("ERROR", self.sent[0])
        self.assertIn("TransportError", self.sent[0])
        self.assertIn("api:TransportError", self.state()["notified"])
        # Drive da ket noi, chi refresh YouTube loi mang -> FAIL api trong health

        def connect(args):
            e = TransportError("timeout")
            e.drive = self.drive
            raise e
        self.clock.t += timedelta(days=1)
        self.assertEqual(self.run_it(drive=None, youtube=None, connect=connect), 1)
        self.assertEqual(self.health()["status"], "FAIL api")
        self.assertIn("TransportError", self.health()["detail"])

    def test_write_failure_keeps_failure_bookkeeping(self):
        self.capture_plan = {"vidA0000001": [sp.ParseError("missing metric ctr")]}

        def broken(drive, folders, day, cols, rows):
            raise ConnectionResetError("drive reset")
        with mock.patch.object(ss, "append_snapshot_rows", broken):
            self.assertEqual(self.run_it(), 1)
        st = self.state()
        self.assertEqual(st["failed"], {"vidA0000001": 1})
        self.assertIn("vidA0000001", st["last_attempt"])
        self.assertEqual(st["last"], {})                      # dong cua B chua len Drive
        self.assertEqual(self.health()["status"], "FAIL api")

    # -- backoff va gioi han Telegram

    def five_videos(self):
        self.yt.resources = [resource(f"vid{c}000000{i}", published="2026-11-20T09:00:00Z")
                             for i, c in enumerate("CDEFG")]
        return [r["id"] for r in self.yt.resources]

    def test_many_failures_one_message(self):
        bad = sp.ParseError("missing metric ctr")
        self.yt.resources.append(resource("vidC0000003", published="2026-11-20T07:00:00Z"))  # reads fine
        self.capture_plan = {"vidB0000002": [bad, bad], "vidA0000001": [bad, bad]}
        self.run_it()
        self.assertEqual(self.sent, [])
        self.clock.t += timedelta(hours=2)
        self.run_it()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("vidB0000002", self.sent[0])
        self.assertIn("vidA0000001", self.sent[0])
        self.assertIn("2 video(s)", self.sent[0])

    # -- I9: every read failing to parse, twice in a row, is a challenge page

    def test_all_parse_failures_twice_is_auth_hold(self):
        prof = self.home / "profile" / "Default"
        prof.mkdir(parents=True)
        (prof / "Cookies").write_bytes(b"x")
        bad = sp.ParseError("missing metric ctr")
        self.capture_plan = {"vidB0000002": [bad, bad, bad], "vidA0000001": [bad, bad, bad]}
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(self.health()["status"], "FAIL read")
        self.assertEqual(self.sent, [])
        self.clock.t += timedelta(hours=2)
        self.assertEqual(self.run_it(), 3)
        h = self.health()
        self.assertEqual((h["status"], h["detail"]), ("FAIL auth", "all reads failed twice - possible challenge page"))
        self.assertEqual(self.sent, [sc.AUTH_MSG])           # the sign-in message only, no read-failure one
        self.assertIn("auth_blocked", self.state())
        # the existing auth hold applies: no Studio until the profile changes
        self.clock.t += timedelta(hours=2)
        self.captured.clear()
        self.assertEqual(self.run_it(), 3)
        self.assertEqual(self.captured, [])
        self.assertEqual(self.sent, [sc.AUTH_MSG])

    def test_parse_failures_with_one_good_read_are_not_auth(self):
        bad = sp.ParseError("missing metric ctr")
        self.capture_plan = {"vidB0000002": [bad, bad]}
        self.run_it()
        self.clock.t += timedelta(hours=2)
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(self.health()["status"], "FAIL read")
        self.assertNotIn("auth_blocked", self.state())

    def test_single_fresh_video_failing_never_arms_hold(self):
        # a video that shows "—" right after publish fails alone: not a challenge page
        self.yt.resources = [resource("vidB0000002", published="2026-11-20T09:00:00Z")]
        bad = sp.ParseError("missing metric ctr")
        self.capture_plan = {"vidB0000002": [bad, bad, bad, bad]}
        for _ in range(4):
            self.assertEqual(self.run_it(), 0)
            self.assertNotIn("auth_blocked", self.state())
            self.assertNotIn(sc.AUTH_MSG, self.sent)
            self.clock.t += timedelta(hours=2)
        self.assertNotEqual(self.health()["status"], "FAIL auth")

    def test_two_previously_read_videos_failing_in_two_runs_arm_hold(self):
        self.assertEqual(self.run_it(), 0)                    # both read cleanly: both in state["last"]
        self.assertEqual(set(self.state()["last"]), {"vidA0000001", "vidB0000002"})
        bad = sp.ParseError("missing metric ctr")
        self.capture_plan = {"vidB0000002": [bad, bad], "vidA0000001": [bad, bad]}
        self.clock.t += timedelta(hours=2)
        self.assertEqual(self.run_it(), 0)
        self.clock.t += timedelta(hours=2)
        self.assertEqual(self.run_it(), 3)
        self.assertEqual(self.health()["status"], "FAIL auth")
        self.assertIn("auth_blocked", self.state())

    def test_thumb_fetch_url_is_cache_busted(self):
        seen = []
        sc.thumb_hash("abc", lambda url: seen.append(url) or b"x")
        self.assertRegex(seen[0], r"^https://i\.ytimg\.com/vi/abc/hqdefault\.jpg\?sqp=\d{10}$")

    # -- I7d / m1: the executor's notify state lives in the state file

    def test_executor_state_persists_across_runs(self):
        f, sf = self.folders()
        self.drive._add("b1.alt1.jpg", [sf["thumb_alts"]], "image/jpeg", b"ALT1")
        req = {"video_id": "vid001", "base": "b1", "to_variant": "alt1", "from_variant": "main",
               "reason": "t", "requested_utc": "2026-11-20T11:00:00Z"}
        fid = self.drive._add("vid001__x.json", [sf["actions_pending"]], "application/json",
                              json.dumps(req).encode())
        sets = []
        move_ok = [False]
        real_move = ss.move_json

        def move(drive, file_id, src, dest, obj):
            if not move_ok[0]:
                raise ConnectionResetError("drive reset")
            return real_move(drive, file_id, src, dest, obj)
        import studio_actions as sa
        with mock.patch.object(yu, "set_thumbnail", lambda yt, v, p: sets.append(v)), \
                mock.patch.object(yu, "download_to", lambda d, i, dest, label: Path(dest).write_bytes(b"x")), \
                mock.patch.object(yu, "notify", self.sent.append), \
                mock.patch.object(ss, "move_json", move), \
                mock.patch.object(sa, "_now", self.clock.now):
            for _ in range(3):
                self.assertEqual(self.run_it(run_actions=sa.run_actions), 0)
                self.clock.t += timedelta(hours=2)
            self.assertEqual(sets, ["vid001"])                 # set once, never re-set
            self.assertEqual(len([m for m in self.sent if "not moved" in m]), 1)   # one alert over 3 runs
            self.assertIn(fid, self.state()["actions"]["applied"])
            move_ok[0] = True
            self.run_it(run_actions=sa.run_actions)
        self.assertEqual(sets, ["vid001"])
        self.assertEqual(self.drive.items[fid]["parents"], [sf["actions_done"]])
        res = json.loads(self.drive.items[fid]["data"])["result"]
        self.assertEqual((res["status"], res["at_utc"]), ("ok", "2026-11-20T12:00:05Z"))  # the real set time
        self.assertEqual(self.state()["actions"]["applied"], {})

    # -- m4: the video list is cached for 6 h

    def channel_calls(self):
        return sum(1 for kind, kw in self.yt.calls if kind == "channels" and kw.get("part") == "contentDetails")

    def test_video_list_cached_6h(self):
        self.run_it()
        self.clock.t += timedelta(hours=2)
        self.run_it()
        self.assertEqual(self.channel_calls(), 1)
        self.assertEqual(self.captured, ["vidB0000002", "vidA0000001"] * 2)
        self.clock.t += timedelta(hours=4)                  # 6 h after the list was read
        self.run_it()
        self.assertEqual(self.channel_calls(), 2)
        # a due check that needs a video the cache does not know refreshes at once
        self.yt.resources.insert(0, resource("vidN0000009", published="2026-11-20T15:00:00Z"))
        self.clock.t += timedelta(minutes=30)
        self.assertEqual(self.run_it("--only", "vidN0000009"), 0)
        self.assertEqual(self.channel_calls(), 3)
        self.assertIn("vidN0000009", self.captured)

    def test_stops_after_three_consecutive_failures(self):
        ids = self.five_videos()
        bad = sb.NavError("net::ERR_TIMED_OUT")
        self.capture_plan = {v: [bad] for v in ids}
        self.assertEqual(self.run_it(), 4)
        self.assertEqual(self.captured, ids[:3])
        st = self.state()
        self.assertEqual(set(st["last_attempt"]), set(ids[:3]))   # hai video sau van den han
        self.assertIn("3 failures in a row", self.health()["detail"])
        # trang / trinh duyet da dong -> dung ngay sau video do
        self.captured.clear()
        (self.home / f".studio-state-{CHANNEL}.json").unlink()
        self.capture_plan = {ids[0]: [RuntimeError(
            "Target page, context or browser has been closed")]}
        self.assertEqual(self.run_it(), 4)
        self.assertEqual(self.captured, ids[:1])
        self.assertIn("browser closed", self.health()["detail"])
        # mot lan thanh cong o giua thi dem lai tu dau
        self.captured.clear()
        (self.home / f".studio-state-{CHANNEL}.json").unlink()
        self.capture_plan = {ids[0]: [bad], ids[1]: [bad], ids[3]: [bad], ids[4]: [bad]}
        self.run_it()
        self.assertEqual(self.captured, ids)

    def test_failed_video_backs_off(self):
        self.capture_plan = {"vidA0000001": [sb.NavError("timeout")]}
        self.run_it()
        self.assertEqual(self.state()["last_attempt"], {"vidA0000001": "2026-11-20T12:00:05Z"})
        # 30 phut sau: chua den han lai (tinh tu lan thu, khong tu lan thanh cong)
        self.captured.clear()
        self.clock.t += timedelta(minutes=30)
        self.run_it()
        self.assertEqual(self.captured, [])
        # het chu ky 2 gio: thu lai
        self.clock.t += timedelta(hours=1, minutes=30)
        self.run_it()
        self.assertEqual(self.captured, ["vidB0000002", "vidA0000001"])
        # video cu (doc hang ngay) loi sau 06:00 -> doi toi 06:00 hom sau
        old = [vid("o", dt("2026-11-01T00:00:00Z"))]
        state = {"last": {"o": "2026-11-19T07:00:00Z"}, "last_attempt": {"o": "2026-11-20T07:00:00Z"}}
        last = sc.last_by_video(state)
        self.assertEqual(sc.due_videos(old, last, dt("2026-11-20T20:00:00Z"), 6, tz=UTC), [])
        self.assertEqual(sc.due_videos(old, last, dt("2026-11-21T06:30:00Z"), 6, tz=UTC), ["o"])

    # -- loi API / Drive

    def quota_youtube(self):
        from googleapiclient.errors import HttpError

        class _Resp:
            status = 403
            reason = "Forbidden"

        class C:
            def list(self, **kw):
                return _Exec(HttpError(_Resp(), b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}'))

        yt = FakeYouTube([])
        yt.channels = lambda: C()
        return yt

    def test_api_failure_writes_fail_api(self):
        self.assertEqual(self.run_it(youtube=self.quota_youtube()), 1)
        h = self.health()
        self.assertEqual(h["status"], "FAIL api")
        self.assertIn("quota", h["detail"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("quota", self.sent[0])
        self.assertEqual(self.captured, [])

    def test_api_error_notified_once_per_day(self):
        yt = self.quota_youtube()
        self.assertEqual(self.run_it(youtube=yt), 1)
        self.clock.t += timedelta(hours=1)
        self.assertEqual(self.run_it(youtube=yt), 1)
        self.assertEqual(len(self.sent), 1)
        # loai loi khac cung ngay -> bao rieng
        broken = FakeYouTube([])
        broken.channels = lambda: (_ for _ in ()).throw(ConnectionResetError("reset"))
        self.assertEqual(self.run_it(youtube=broken), 1)
        self.assertEqual(len(self.sent), 2)
        # ngay UTC moi -> bao lai
        self.clock.t += timedelta(days=1)
        self.assertEqual(self.run_it(youtube=yt), 1)
        self.assertEqual(len(self.sent), 3)

    def test_partial_append_keeps_first_day(self):
        # qua nua dem: file ngay 20 ghi xong, file ngay 21 loi -> chi video ngay 20 co "last"
        self.clock = Clock(dt("2026-11-20T23:59:58Z"))
        real = ss.append_snapshot_rows

        def flaky(drive, folders, day, cols, rows):
            if day == "2026-11-21":
                raise ConnectionResetError("drive reset")
            return real(drive, folders, day, cols, rows)
        with mock.patch.object(ss, "append_snapshot_rows", flaky):
            self.assertEqual(self.run_it(), 1)
        st = self.state()
        self.assertEqual(set(st["last"]), {"vidB0000002"})
        self.assertEqual(self.health()["status"], "FAIL api")
        # lan sau chi doc lai video chua len Drive -> khong trung dong
        self.captured.clear()
        self.clock.t += timedelta(minutes=30)
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(self.captured, ["vidA0000001"])
        self.assertEqual(self.snapshot_text("2026-11-20").count("vidB0000002"), 1)

    def test_runbook_task_keeps_exit_code(self):
        text = (HERE.parent / "docs" / "studio-capture.md").read_text(encoding="utf-8")
        self.assertIn("--once; exit $LASTEXITCODE", text)

    def test_config_and_cli(self):
        a = sc.parse_args(["--config", str(self.cfg)])
        self.assertEqual((a.channel, a.daily_hour, a.profile),
                         (CHANNEL, 6, str(self.home / "profile")))
        a = sc.parse_args(["--config", str(self.cfg), "--channel", "X", "--profile", "P"])
        self.assertEqual((a.channel, a.profile), ("X", "P"))
        ex = json.loads((HERE / "studio_config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(set(ex), {"channel", "profile_dir", "daily_hour", "capture_cap_min"})

    def test_capture_cap_from_config(self):
        self.assertEqual(sc.parse_args(["--config", str(self.cfg)]).capture_cap_s, sc.CAPTURE_CAP_S)
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        cfg["capture_cap_min"] = 120
        self.cfg.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(sc.parse_args(["--config", str(self.cfg)]).capture_cap_s, 7200)
        cfg["capture_cap_min"] = 5
        self.cfg.write_text(json.dumps(cfg), encoding="utf-8")
        with self.assertRaises(SystemExit):
            sc.parse_args(["--config", str(self.cfg)])


if __name__ == "__main__":
    unittest.main()
