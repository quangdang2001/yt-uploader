# Studio feedback loop — design

Date: 2026-11-19 · Channel: Bare Skull (`UCDBRc2lIFJ0ylsGbTJd5C5g`) · Repo: yt-uploader (`D:/Youtube/n8n-upload`),
plus small pieces in the build repo (`D:/Youtube/video_footage_re_create`).

## 1. Intent

The owner wants views to go up by catching weak packaging early. The first 24–48 hours decide whether YouTube
keeps showing a video, and the API's impressions/CTR arrive about two days late. So the loop must:

1. read each video's numbers from YouTube Studio within hours, all through the life of the video;
2. **swap a weak thumbnail automatically** through the YouTube Data API (owner 2026-11-19: "if you can update the
   thumbnail through API … I think we should update it");
3. **learn why** a thumbnail did badly, so the next videos' thumbnails improve (owner: "learn this thumbnail not
   have the good result, and findout why then we can improve in other video").

Evidence it matters: on the owner's Studio export of 10-15 → 11-11 (14 videos, no exception), CTR ≥ 5.7% got
17,000+ impressions; CTR < 5.3% got under 7,400 (`playbook/channel_daily_report_note/2026-11-12.md` in the build
repo). Nothing today turns CTR into a decision: the old `youtube_report_builder` has been failing auth since 10-08,
and `fetch_analytics.py` has no impressions or CTR.

## 2. Goals, non-goals, anti-goals

Goals
- Hourly-scale snapshot history per video in Drive, never overwritten.
- Automatic thumbnail swap with prepared alternates, on clear rules, with a Telegram message each time.
- A per-thumbnail feature table joined to the CTR each variant earned, and a weekly "why" summary that feeds the
  thumbnail form.
- Title verdicts (keep / change) as alerts.

Non-goals
- Changing titles automatically. A title must agree with the recorded narration (build-repo memory
  `title-must-agree-with-the-film`), so a title change is the owner's call.
- Replacing `fetch_analytics.py`. It keeps running as it does today.
- Comments, likes, community posts, uploads: untouched.

Anti-goals (a failure even if it works)
- **The channel's Google account gets challenged, locked or flagged.** Every design choice below that limits
  Studio page loads exists for this.
- A swap that makes CTR worse and stays live.
- A thumbnail that has not passed the policy read going public. The channel is one violation away from a strike.
- Typing the account password anywhere, or storing it.

## 3. Constraints

- Runs on the VPS (Windows Server 2016, next to the uploader, Task Scheduler, Telegram via `notify()` in
  `youtube_uploader.py`). Server 2016 support ends 2027-01-13; keep the script portable.
- Studio is read with **Playwright on a persistent Chromium profile** that the owner signs into once by hand.
  The script never fills a login form. If Studio shows a sign-in page, a CAPTCHA or a "verify it's you" check, the
  run stops, sends Telegram, writes `FAIL auth` to a health file, and does not retry until the owner signs in again.
- The uploader and the capture must not drive Studio or the same profile at once: separate lock file, and the
  capture skips a run while an upload is in progress.
- YouTube Data API quota: `thumbnails.set` = 50 units of 10,000/day. Uploads have their own bucket.
- Drive layout already exists: `YouTube-Upload/Bare Skull/{pending,done,failed}`, `upload-history.csv`.
- Image reads for policy run on the vision model configured in the build repo (`playbook/qwen_screen.py`),
  never a Claude subagent (owner 2026-11-17).

## 4. Approaches considered

| | how | trade-off |
|---|---|---|
| A. Playwright for every video | Studio pages, all videos, on a schedule | freshest data everywhere; most Studio page loads, so the most account risk; breaks when Studio's layout changes |
| B. Mix | Playwright for videos < 72 h, Reporting API for the rest | a tenth of the page loads; older videos two days late |
| C. API only | Analytics API + Reporting API `channel_reach_basic_a1` | safest; always about two days late |

**Owner picked A** (2026-11-19: "Playwright for all", because Studio is real time). The design limits A's risk with
the schedule in §5.1.

## 5. Design

### 5.1 Capture — `scripts/studio_capture.py` (yt-uploader)

Schedule (my call, to keep page loads low):
- every 2 hours for videos published less than 72 hours ago;
- once a day (06:00 VPS time) for all other public videos;
- random 0–10 min jitter on each run; at most one video page open at a time; a 5–15 s pause between videos.

Per video, per run, read:
- impressions, impressions CTR, views, unique viewers, average view duration, average percentage viewed;
- retention at 30 s and 60 s (from the audience-retention curve);
- traffic-source split of impressions/views (Browse, Suggested, Search, other);
- whether a Test & Compare is running, and its result if finished;
- the thumbnail currently live (its URL hash or `i.ytimg.com` etag) so each reading is tied to a variant.

Studio's period selector: read both "since published" and, for videos ≥ 3 days old, "last 48 hours". This gives
both a running total and a recent rate.

How to read the page is the builder's choice. **Recommendation:** capture Studio's own JSON responses
(Playwright `page.on("response")`) rather than scraping text from the page. JSON survives visual redesigns better,
and gives exact numbers instead of "1.2K".

### 5.2 Storage on Drive (`YouTube-Upload/Bare Skull/`)

- `studio-snapshots.csv`: append-only, one row per video per run:
  `captured_at_utc, video_id, age_hours, period, impressions, ctr, views, unique_viewers, avd_s, apv_pct,
  ret_30s, ret_60s, src_browse, src_suggested, src_search, src_other, thumb_variant, thumb_hash, test_running`.
- `studio-verdicts.csv`: rewritten each run, one row per video: current verdict, reason, numbers behind it.
- `thumbnail-swaps.csv`: append-only log of every swap (time, video, from, to, CTR and impressions at decision).
- `thumbnail-features.csv`: one row per thumbnail variant (see 5.5).
- `studio-health.txt`: last run time and PASS / FAIL with the reason.

### 5.3 Alternates shipped with each upload

Alternates go to a separate Drive folder, `YouTube-Upload/Bare Skull/thumb-alts/`, named `<base>.alt1.jpg` and
`<base>.alt2.jpg`, where `<base>` is the name of the `.json` in `pending/` (the project slug). The uploader is not
changed: its `find_media` looks only for `<base>.mp4` and `<base>.jpg`, and it already records `ten_goc` (= base)
and `video_id` in `upload-history.csv`, which is how the capture maps a video to its alternates. (Planning change,
2026-11-19: a separate folder means the uploader needs no change.)

Build-repo side: the topic staff (`nv-chu-de`) makes the main thumbnail plus two alternates that each differ in
**one** named feature (for example: background, gun angle, text). The difference is recorded so a swap teaches
something. All three pass `playbook/thumb_gate.py` and the Qwen policy read before `push_to_pending.py` sends them.
A video without alternates is still captured and judged; it just cannot be swapped.

### 5.4 Verdicts and the automatic swap

Starting thresholds come from the 11-12 measurement (5.7% / 5.3%). Once the snapshot file holds 10+ videos, the
"normal" CTR for a video's age becomes the channel's own median at that age, and the swap rule uses whichever
bar is lower: 5.3% or 0.8 × that median.

Rules, checked each run (my calls on the numbers):
- **Too early:** fewer than 1,000 impressions, or younger than 6 hours → no verdict.
- **Keep:** CTR ≥ 5.7%.
- **Swap thumbnail:** CTR under the bar, ≥ 1,000 impressions, no Test & Compare running, an unused alternate
  exists, and ≥ 24 h and ≥ 1,000 new impressions have passed since the last swap → `thumbnails.set` with the next
  alternate, log to `thumbnail-swaps.csv`, Telegram the owner.
- **Revert:** after a swap, if the new variant's CTR over its own impressions is lower than the old one's, set
  the better variant back. At most two swaps plus one revert per video.
- **Change title (alert only):** CTR still under the bar after all alternates were tried → Telegram with numbers.
  The owner decides.
- **Not packaging:** impressions well below the channel's normal for that age while CTR ≥ the bar → alert
  "not packaging"; no swap.
- **Opening problem:** CTR fine but 30 s retention below the channel median → alert to the script side.

### 5.5 Learning why

`thumbnail-features.csv`: one row per variant, written when it is pushed (build repo):
- measured by `thumb_gate.py`: colourfulness, warmth, brightness, saturation;
- read by the vision model from a fixed question list: background type (outdoor/studio/other), gun share of width,
  gun angle, props, word count, the words, accent colour, text size share;
- `differs_from_main` for alternates.

Each variant's earned CTR = its clicks over its impressions while it was live (from snapshots, by `thumb_hash`).

Weekly, the channel manager (build repo, `quan-ly-kenh` review) reads features × earned CTR and writes:
- which features the top-CTR variants share and the bottom ones lack, with counts;
- per weak thumbnail, a one-line "why" naming the feature that differed from the channel's winners;
- a proposed change to `raw/form-thumbnail.md` when a feature wins 3+ times, for the owner to approve.

A swap that wins is the clearest lesson: the two variants differed in one recorded feature.

### 5.6 Failure handling

- Sign-in page, CAPTCHA, "verify it's you" → stop, Telegram, `FAIL auth`, no retry.
- A Studio page that returns no numbers for a video → `FAIL read` for that video; two failed runs in a row →
  Telegram. Never write zeros for a failed read.
- `thumbnails.set` error → log, Telegram, do not mark the alternate as used.

## 5.7 Decisions after the builder check (my calls unless marked)

- **Two phases.** Phase 1 is the freshness spike (§6). If Studio's impressions/CTR are not clearly fresher than the
  Reporting API's (about 48 h), stop and report to the owner before phase 2. The owner chose Playwright for its
  freshness, so a stale result reopens that choice.
- **Where it runs: the VPS, Windows Server 2016** (owner 2026-11-19: "will running it in vps window server 2016"),
  next to the uploader. Mitigations for the sign-in risk:
  - Use Google Chrome installed on the VPS (`channel="chrome"`), not Playwright's bundled Chromium, which Google
    often refuses at sign-in ("this browser may not be secure").
  - The owner signs in once over RDP, inside that profile, on the VPS itself, so Google sees the login from the
    same IP the script will use. Expect one "verify it's you" prompt on the owner's phone the first time.
  - Chrome runs headed in the owner's RDP session. Task Scheduler is set to "run only when user is logged on", and
    the RDP session is **disconnected, never signed out** (signing out stops the runs). Confirm early in phase 1
    that headed Chrome keeps working after the RDP window is disconnected; if not, test headless with the same
    profile.
  - Server 2016 support ends 2027-01-13. Keep the profile path and Task Scheduler entries in config, so the move
    to a newer machine is a copy.
- **Session expiry runbook.** A `FAIL auth` sends Telegram with one line: open the profile, sign in, done. If it
  happens more than twice a month, the owner is told the approach is costing more than it saves.
- **Scope of the plan: both repos.** yt-uploader gets the capture, verdicts, swap and the Drive files.
  The build repo gets the alternates (`nv-chu-de`), the alt handling in `thumb_gate.py` and `push_to_pending.py`,
  the feature rows, and the weekly review.
- **Join key.** `video_id` + `variant` (`main`, `alt1`, `alt2`, `manual`). The capture knows which variant is live,
  because it made every swap. If the live thumbnail hash changes without a logged swap, the variant becomes
  `manual`. The build repo writes feature rows keyed by slug + variant + image sha256 into
  `thumbnail-features.csv`. The uploader's `upload-history.csv` maps slug to video id. The capture script computes
  earned CTR per variant.
- **Weak evidence.** The swap floor rises to **2,000 impressions**. A swap also needs the upper end of a 90%
  interval on CTR to sit below the bar, so a 5.5% reading at low volume does not trigger it. A revert needs both
  variants at ≥ 2,000 impressions and a difference outside both intervals. **The first 7 days run alert-only**
  ("would swap") so the rules are checked against real numbers before `thumbnails.set` goes live.
- **Owner's manual tests win.** No swap while a Test & Compare is running. A manual thumbnail change pauses the loop
  on that video for 7 days.
- **Back catalog.** Already-published videos are captured and judged but get no alternates. The six held films
  (2026-11-19) ship with alternates when they are re-cut.
- **Coverage.** Public videos only. Age counts from the moment the video became public. Private, unlisted,
  scheduled and held videos are never read or touched. No Shorts (the channel makes none).

## 6. Left to the builder

- The exact Studio URLs and the JSON endpoints to listen to (verify against the live Studio).
- **First task, a spike:** on a video under 24 hours old, measure how fresh Studio's impressions and CTR really
  are, compared with views. If they lag as much as the API, report it before building the rest.
- Profile path, lock file, health file format, Task Scheduler entries.
- Tests: parsing from saved Studio JSON fixtures, the verdict rules as pure functions, swap and revert sequences.
