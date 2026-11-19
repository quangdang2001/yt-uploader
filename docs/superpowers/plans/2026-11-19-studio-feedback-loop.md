# Studio Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read every public Bare Skull video's Studio numbers with Playwright on the VPS, save them to Drive, swap a
weak thumbnail through the YouTube Data API, and record each thumbnail's features so the weekly review can say why it
lost.

**Architecture:** Flat scripts in `scripts/`, the same as the uploader. Pure modules (`studio_parse`, `studio_rules`)
carry the logic and the tests. Thin I/O modules (`studio_store` for Drive CSVs, `studio_thumbs` for swaps) reuse
`youtube_uploader.py` helpers. `studio_capture.py` is the Task Scheduler entry point. The build repo adds the
alternates and the feature rows.

**Tech Stack:** Python 3.11+, Playwright for Python (`channel="chrome"`, persistent context), google-api-python-client,
`unittest` (repo convention: `python -m unittest test_x -v` from `scripts/`).

**Spec:** `docs/superpowers/specs/2026-11-19-studio-feedback-loop-design.md`

## Global Constraints

- Runs on the VPS, Windows Server 2016. Installed Google Chrome via `channel="chrome"`, never bundled Chromium.
  Headed, in the owner's RDP session, Task Scheduler "run only when user is logged on".
- Never fill a login form, never store a password. A sign-in page, CAPTCHA or "verify it's you" means: stop, write
  `FAIL auth` to `studio-health.txt`, Telegram via `notify()`, exit code 3, no retry.
- Schedule: every 2 h for videos public < 72 h; once a day from 06:00 VPS time for the rest; jitter 0–10 min per
  run; one video page at a time; 5–15 s pause between videos.
- Public videos only; age counts from the time the video became public. Never read or touch private, unlisted,
  scheduled or held videos. No Shorts.
- Thresholds: KEEP 5.7%; base bar 5.3%; once ≥ 10 videos have snapshots, bar = min(5.3%, 0.8 × channel median CTR
  at that age bucket); swap floor 2,000 impressions; no verdict under 6 h of age; ≥ 24 h and ≥ 2,000 new
  impressions since the last swap; swap only if the upper end of the 90% interval on CTR < bar; revert needs both
  variants ≥ 2,000 impressions and non-overlapping 90% intervals; ≤ 2 swaps + 1 revert per video.
- First 7 days after `go_live` (config) are alert-only: write "would swap", call no `thumbnails.set`.
- No swap while Test & Compare is running. A thumbnail change not made by the loop sets variant `manual` and pauses
  the loop on that video for 7 days.
- Never write a zero for a failed read. A failed read is `FAIL read`; two in a row for one video → Telegram.
- Titles are never changed by the loop (alert only).
- Drive files live in `YouTube-Upload/Bare Skull/`: `studio-snapshots.csv` (append), `studio-verdicts.csv`
  (rewrite), `thumbnail-swaps.csv` (append), `thumbnail-features.csv` (append, written by the build repo),
  `studio-health.txt`, and alternates in `thumb-alts/<base>.alt1.jpg|alt2.jpg`.
- Separate lock `studio.lock`; skip the run if the uploader's lock is held.
- Image reads in the build repo use `playbook/vision_probe.py` with the model from `playbook/vision_config.json`,
  never a Claude subagent.

## Review Focus

- **Studio shows rounded numbers ("1.2K", "5.4%") or a localized format ("5,4 %").** Expected: exact integers and
  fractions from JSON; when only text exists, parse both decimal separators, and refuse "K/M" rounding for the CTR
  math (→ `FAIL read`). Test in Task 2.
- **A video goes public late (uploaded private, flipped public days later).** Expected: age counts from the public
  time, so it gets the 2-hourly window after going public. Test in Task 6.
- **Two runs overlap (a slow run, or Task Scheduler fires again).** Expected: the second exits at once on
  `studio.lock` and writes nothing. Test in Task 6.
- **The owner swaps a thumbnail by hand mid-test.** Expected: variant becomes `manual`, 7-day pause, no revert to
  the loop's variant. Test in Task 3.
- **`thumbnails.set` fails (quota, 5xx) during a swap.** Expected: alternate not marked used, swap not logged as
  done, Telegram sent, next run may retry. Test in Task 5.

---

## Phase 1 — freshness spike (gate)

### Task 1: Freshness spike and RDP-disconnect test

Throwaway tool, no TDD. Its output decides whether Phase 2 starts.

**Files:**
- Create: `scripts/studio_spike.py`
- Create: `docs/studio-spike-report.md` (results)
- Create: `scripts/fixtures/studio/` (raw JSON captures kept for Task 2)

**Interfaces:**
- Produces: raw Studio JSON captures named `<video_id>__<utc>__<endpoint>.json`, and the list of endpoint URL
  patterns that carry impressions, CTR, views, AVD, retention, traffic sources, Test & Compare state.

- [ ] **Step 1: Write `studio_spike.py --profile <dir> --video <id> [--headless]`**: open a persistent Chrome
  context, go to the video's Studio analytics Reach tab and Engagement tab, save every JSON response body under
  `fixtures/studio/`, print each number found and Studio's own "updated" text. Detect a sign-in page by URL host
  `accounts.google.com` and exit 3.
- [ ] **Step 2: Owner signs in once** over RDP: `python studio_spike.py --profile C:\yt\studio-profile --video <any>`.
  A Chrome window opens; the owner signs in by hand. Re-run: it must reach Studio without a prompt.
- [ ] **Step 3: Freshness runs.** On the next upload, run at about 2, 6, 12 and 24 h after it goes public, plus once
  on a video over 7 days old. Record in the report: Studio impressions/CTR/views per run, public views from
  `yt-dlp --print view_count`, and the Reporting API's latest day for the same video (if available).
- [ ] **Step 4: RDP-disconnect test.** Disconnect the RDP window (do not sign out). Run the spike from Task Scheduler
  as "run only when user is logged on". Record whether headed Chrome completes. If not, repeat with `--headless`.
- [ ] **Step 5: Write `docs/studio-spike-report.md`**: freshness table, endpoints, disconnect result, any security
  prompt seen.
- [ ] **Step 6: Gate.** Stop and report to the owner. Phase 2 starts only if Studio's impressions/CTR were clearly
  fresher than about 48 h, and the owner says go.
- [ ] **Step 7: Commit**

```bash
git add scripts/studio_spike.py scripts/fixtures/studio docs/studio-spike-report.md
git commit -m "spike(studio): measure Studio freshness and RDP-disconnect behaviour"
```

Fixtures may contain the channel's numbers but must not contain cookies or auth headers: save response bodies only.

## Phase 2 — the loop (after the owner's go)

### Task 2: Parse Studio responses into a snapshot

**Files:**
- Create: `scripts/studio_parse.py`
- Test: `scripts/test_studio_parse.py`, fixtures from Task 1 copied to `scripts/fixtures/studio/parse/`

**Interfaces:**
- Produces: `class ParseError(Exception)`; `SNAPSHOT_COLS: tuple[str, ...]` (exact spec order:
  `captured_at_utc, video_id, age_hours, period, impressions, ctr, views, unique_viewers, avd_s, apv_pct, ret_30s,
  ret_60s, src_browse, src_suggested, src_search, src_other, thumb_variant, thumb_hash, test_running`);
  `parse_snapshot(payloads: list[dict], video_id: str, period: str) -> dict` (keys = the metric columns; `ctr`,
  `apv_pct`, `ret_*`, `src_*` as fractions 0–1; ints for counts; `test_running: bool`);
  `parse_number(text: str) -> float` for text fallbacks.

- [ ] **Step 1: Write the failing tests**
  - `test_parses_reach_fixture`: for each fixture, the dict equals the numbers recorded in the spike report
    (copy exact values into the test).
  - `test_missing_metric_raises`: a fixture with the CTR key removed → `ParseError`, never `0`.
  - `test_parse_number_locales`: `"5.4%"`→`0.054`, `"5,4 %"`→`0.054`, `"12,345"`→`12345`, `"1.2K"` → `ParseError`.
  - `test_test_running_flag`: fixture with an active Test & Compare → `True`.
- [ ] **Step 2: Run** `python -m unittest test_studio_parse -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `studio_parse.py` against the endpoint shapes found in Task 1.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(studio): parse Studio analytics JSON into snapshots`.

### Task 3: Verdict and swap rules

**Files:**
- Create: `scripts/studio_rules.py`
- Test: `scripts/test_studio_rules.py`

**Interfaces:**
- Consumes: snapshot dicts from Task 2.
- Produces:
  - constants `KEEP_CTR=0.057`, `BASE_BAR=0.053`, `MEDIAN_FACTOR=0.8`, `MIN_VIDEOS_FOR_MEDIAN=10`,
    `MIN_IMPR=2000`, `MIN_AGE_H=6`, `MIN_GAP_H=24`, `MAX_SWAPS=2`, `MAX_REVERTS=1`, `MANUAL_PAUSE_D=7`, `Z90=1.645`;
  - `ctr_interval(ctr: float, impressions: int, z: float = Z90) -> tuple[float, float]` (Wilson score interval);
  - `age_bucket(age_hours: float) -> str` (`"0-6"`, `"6-12"`, `"12-24"`, `"24-48"`, `"48-72"`, `"72+"`);
  - `swap_bar(age_hours: float, history: list[dict]) -> float`;
  - `@dataclass VideoState(variant: str, swaps: int, reverts: int, last_swap_utc: datetime | None,
    impr_at_last_swap: int, variant_stats: dict[str, tuple[int, float]], manual_until: datetime | None,
    alts_left: list[str])`;
  - `Verdict` enum: `TOO_EARLY, KEEP, WATCH, SWAP, REVERT, CHANGE_TITLE, NOT_PACKAGING, OPENING, PAUSED`;
  - `decide(snap: dict, state: VideoState, bar: float, now: datetime, typical_impr: float | None,
    median_ret_30s: float | None) -> tuple[Verdict, str]` (verdict, one-line reason with the numbers).

- [ ] **Step 1: Write the failing tests** (one per rule, exact values):
  - `test_too_early_age`: age 5.9 h, 50,000 impr → `TOO_EARLY`.
  - `test_too_early_impressions`: age 10 h, 1,999 impr → `TOO_EARLY`.
  - `test_keep`: CTR 0.057, 3,000 impr → `KEEP`.
  - `test_swap`: CTR 0.040, 4,000 impr, alts left, no test → `SWAP`.
  - `test_no_swap_when_interval_reaches_bar`: CTR 0.050, 2,000 impr (upper 90% bound ≥ 0.053) → `WATCH`.
  - `test_no_swap_during_test`: `test_running=True` → never `SWAP`.
  - `test_gap_after_swap`: last swap 23 h ago → `WATCH`; 24 h and +2,000 impr → may `SWAP`.
  - `test_revert`: variant stats main (5,000, 0.052) vs alt1 (2,500, 0.030), intervals apart → `REVERT`.
  - `test_change_title_after_alts`: no alts left, CTR 0.040 → `CHANGE_TITLE`.
  - `test_manual_pause`: `manual_until` in the future → `PAUSED`, and no `REVERT` to a loop variant.
  - `test_not_packaging`: CTR 0.060, impressions < 0.5 × `typical_impr` → `NOT_PACKAGING`.
  - `test_opening`: CTR 0.060, `ret_30s` < `median_ret_30s` → `OPENING`.
  - `test_swap_bar_median`: 10 videos with median 0.050 at bucket → `0.040`; with 9 videos → `0.053`.
  - `test_wilson_known_value`: `ctr_interval(0.05, 2000)` matches a hand-computed Wilson value to 1e-4.
- [ ] **Step 2: Run** `python -m unittest test_studio_rules -v` → FAIL.
- [ ] **Step 3: Implement** `studio_rules.py`. Order of checks: PAUSED → TOO_EARLY → REVERT → KEEP → SWAP →
  CHANGE_TITLE → NOT_PACKAGING → OPENING → WATCH. `typical_impr` = channel median impressions at the same age
  bucket; NOT_PACKAGING when below 0.5 × that.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(studio): verdict and swap rules`.

### Task 4: Drive CSV store and health file

**Files:**
- Create: `scripts/studio_store.py`
- Test: `scripts/test_studio_store.py` (fake Drive service, the same style as `test_youtube_uploader.py`)

**Interfaces:**
- Consumes: `find_child`, `ensure_folder`, `download_text`, `_csv_media` from `youtube_uploader.py`;
  `SNAPSHOT_COLS` from Task 2.
- Produces: `VERDICT_COLS = ("video_id","base","verdict","reason","ctr","impressions","bar","variant","updated_utc")`;
  `SWAP_COLS = ("time_utc","video_id","base","from_variant","to_variant","ctr","impressions","mode","result")`;
  `read_rows(drive, folder: str, name: str) -> list[dict]`;
  `append_rows(drive, folder: str, name: str, cols: tuple, rows: list[dict]) -> None` (creates the file with
  header if missing);
  `rewrite_rows(drive, folder: str, name: str, cols: tuple, rows: list[dict]) -> None`;
  `write_health(drive, folder: str, status: str, detail: str) -> None` (`studio-health.txt`:
  `PASS|FAIL auth|FAIL read` + UTC time + detail).

- [ ] **Step 1: Write the failing tests**: `test_append_creates_with_header`, `test_append_keeps_old_rows`,
  `test_rewrite_replaces`, `test_read_roundtrip_commas_and_quotes` (a title cell with `,` and `"`),
  `test_health_text`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(studio): Drive CSV store for snapshots, verdicts, swaps, health`.

### Task 5: Thumbnail swap

**Files:**
- Create: `scripts/studio_thumbs.py`
- Test: `scripts/test_studio_thumbs.py`

**Interfaces:**
- Consumes: `set_thumbnail(youtube, video_id, path)`, `notify(msg)` from `youtube_uploader.py`; `append_rows`,
  `SWAP_COLS` from Task 4.
- Produces: `base_by_video(history_rows: list[dict]) -> dict[str, str]` (from `upload-history.csv`, rows with
  a `video_id`, `ten_goc` → base); `find_alts(drive, alts_folder: str, base: str) -> dict[str, dict]` (`alt1`,
  `alt2` → Drive file); `apply_swap(drive, youtube, chan_folder: str, video_id: str, base: str, to_variant: str,
  from_variant: str, snap: dict, alert_only: bool, tmpdir: Path) -> bool` (True only if the thumbnail was set and
  logged).

- [ ] **Step 1: Write the failing tests**:
  - `test_alert_only_never_calls_set`: `alert_only=True` → `set_thumbnail` not called, swap row `mode=alert-only`,
    Telegram text starts `Would swap`.
  - `test_live_swap_calls_set_and_logs`: → called once with the downloaded alt path, row `result=ok`.
  - `test_set_failure_not_marked_used`: `set_thumbnail` raises `HttpError(500)` → returns False, row `result=error`,
    Telegram sent.
  - `test_base_by_video`: history rows → mapping; rows without `video_id` skipped.
  - `test_revert_uses_main_jpg`: `to_variant="main"` downloads `done/<base>.jpg`.
- [ ] **Step 2–4:** run → FAIL, implement, run → PASS.
- [ ] **Step 5: Commit** `feat(studio): thumbnail swap with alert-only mode`.

### Task 6: Capture entry point, schedule, lock, auth

**Files:**
- Create: `scripts/studio_capture.py`, `scripts/studio_config.example.json`
- Modify: `scripts/requirements.txt` (add `playwright>=1.47`)
- Create: `docs/studio-capture.md` (runbook)
- Test: `scripts/test_studio_capture.py`

**Interfaces:**
- Consumes: everything above; `Lock` and `get_credentials` from `youtube_uploader.py`.
- Produces: CLI `python studio_capture.py --channel "Bare Skull" --config studio_config.json [--dry-run] [--once]`;
  config keys `profile_dir`, `go_live` (date), `alert_only_days` (7), `daily_hour` (6), `channel`;
  `due_videos(videos: list[dict], last_by_video: dict[str, datetime], now: datetime, daily_hour: int) -> list[str]`;
  `class AuthRequired(Exception)`; exit codes 0 ok, 2 lock held, 3 auth.

- [ ] **Step 1: Write the failing tests**:
  - `test_due_fresh_every_2h`: public 10 h ago, last capture 1 h 59 m ago → not due; 2 h → due.
  - `test_due_daily_after_hour`: public 5 days ago, last capture yesterday 06:30, now 06:05 → due; already
    captured today → not due.
  - `test_late_public_counts_from_public_time`: uploaded 9 days ago, public 20 h ago → in the 2-hourly window.
  - `test_private_unlisted_scheduled_skipped`.
  - `test_lock_held_exits_2_writes_nothing`.
  - `test_uploader_lock_held_skips`.
  - `test_auth_page_exit_3_health_fail_notify` (fake page whose URL host is `accounts.google.com`).
  - `test_failed_read_twice_notifies`: `ParseError` on two consecutive runs for one video → one Telegram; no
    snapshot row with zeros.
  - `test_manual_thumbnail_change_sets_manual`: live hash ≠ last hash and no swap logged → variant `manual`,
    `manual_until` = now + 7 d.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Video list and public time from the Data API (uploads playlist + `videos.list` with
  `status,snippet`; `publishedAt` is the public time for scheduled videos). Thumbnail hash = sha256 of the
  `maxresdefault.jpg` bytes. Playwright: `chromium.launch_persistent_context(profile_dir, channel="chrome",
  headless=False)`, one page, a random 5–15 s pause between videos, jitter `random.uniform(0, 600)` s at start.
  Per video: read the snapshot for period `since_published`, and also `last_48h` for videos ≥ 3 days old (two rows);
  record a finished Test & Compare's winner in the verdict reason; append, rebuild `VideoState` from snapshots + swaps, `decide`, rewrite verdicts,
  `apply_swap` on `SWAP`/`REVERT` (alert-only while `now < go_live + alert_only_days`), Telegram for
  `CHANGE_TITLE`, `NOT_PACKAGING`, `OPENING` (once per verdict change, not every run).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Write `docs/studio-capture.md`**: install Chrome on the VPS; `pip install -r requirements.txt`; the
  one-time RDP sign-in (`python studio_capture.py --channel "Bare Skull" --config studio_config.json --dry-run`);
  disconnect RDP without signing out; the Task Scheduler entry (every 30 min, `--once`, "run only when user is logged
  on"; the script itself decides what is due); what each Telegram message means; how to sign in again after
  `FAIL auth`.
- [ ] **Step 6: Dry run on the VPS** with `--dry-run --once`: snapshots print, nothing is written to Drive.
- [ ] **Step 7: Commit** `feat(studio): capture entry point, schedule, lock and auth handling`.

## Phase 2b — build repo (`D:/Youtube/video_footage_re_create`), can run alongside Tasks 2–6

### Task 7: Thumbnail features and alternates in the push

**Files:**
- Create: `playbook/thumb_features.py`
- Modify: `playbook/upload_tools/push_to_pending.py` (upload alternates, append feature rows)
- Test: `playbook/tests/test_thumb_features.py`

**Interfaces:**
- Consumes: `thumb_gate.measure(im)`, `thumb_gate.check(src)`; `vision_probe.call(model, path, key, prompt)`,
  `vision_probe.parse(text)`, `vision_probe.get_key()`, `vision_probe.CONFIG`.
- Produces: `FEATURE_COLS = ("base","variant","sha256","differs_from_main","brightness","saturation",
  "colourfulness","warmth","background","gun_width_share","gun_angle","props","word_count","words","accent_colour",
  "text_height_share","pushed_utc")`; `features(path: Path, base: str, variant: str, differs: str) -> dict`.
  `push_to_pending.py` looks for `output/final/<base>.alt1.jpg` / `.alt2.jpg`. It refuses to push if any of the three
  thumbnails fails `thumb_gate.check`. It uploads the alternates to `thumb-alts/`, and appends three rows to
  `thumbnail-features.csv`.

- [ ] **Step 1: Write the failing tests** (vision call mocked): `test_measures_match_thumb_gate`,
  `test_vision_answers_mapped` (a canned JSON answer → background/gun_width_share/words columns),
  `test_vision_failure_marks_unread` (exception → those columns `unread`, never guessed),
  `test_push_refuses_failing_alt`.
- [ ] **Step 2–4:** run `python -m unittest playbook.tests.test_thumb_features -v` → FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat(thumbs): feature rows and alternates in the Drive push`.

### Task 8: Staff instructions for alternates and the weekly "why" review

**Files:**
- Modify: `Bare Skull - 2nd brain/van-hanh/so-tay/nv-chu-de.md` (thumbnail section)
- Modify: `.claude/skills/quan-ly-kenh/references/hoi-cuu.md` (weekly review)

- [ ] **Step 1: nv-chu-de handbook.** For every video: main thumbnail plus `alt1` and `alt2`, each differing from
  main in **one** named feature (background, gun angle or text). Record it in `THUMBNAIL.md` as
  `alt1: differs in <feature>`. All three must pass `thumb_gate.py` and the vision policy read.
- [ ] **Step 2: Weekly review in hoi-cuu.md.**
  - Inputs: `studio-verdicts.csv`, `thumbnail-swaps.csv`, `thumbnail-features.csv` and `studio-snapshots.csv` from
    Drive.
  - Output in `van-hanh/bao-cao/tuan/`: the features shared by the top-CTR variants and missing from the bottom ones
    (with counts); one "why" line per weak thumbnail; each swap's result.
  - A proposal to edit `raw/form-thumbnail.md` when a feature has won 3+ times, for the owner to approve.
- [ ] **Step 3: Check** that `kiem_bang.py` still exits 0 and the skill file reads cleanly.
- [ ] **Step 4:** Do not commit in the build repo unless the owner asks (repo rule).
