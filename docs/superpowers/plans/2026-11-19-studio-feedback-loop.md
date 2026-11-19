# Studio Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read every public Bare Skull video's Studio numbers with Playwright on the VPS, save them to Drive, swap a
weak thumbnail through the YouTube Data API, and record each thumbnail's features so the weekly review can say why it
lost.

**Architecture:** (Revised 2026-11-19, spec §5.8: VPS collects and executes swaps; this PC fetches and analyses in `loop/`.) Flat scripts in `scripts/`, the same as the uploader. Pure modules (`studio_parse`, `studio_rules`)
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
- Drive layout (spec §5.8) under `YouTube-Upload/Bare Skull/studio/`: `snapshots/YYYY-MM-DD.csv`,
  `raw/YYYY-MM-DD/<video_id>__<utc>.json.gz`, `health.json`, `thumb-alts/`, `thumbnail-features.csv`,
  `verdicts.csv`, `actions/{pending,done,failed}/`. The VPS collects and executes; this PC analyses.
- Separate lock `studio.lock`; skip the run if the uploader's lock is held.
- Image reads in the build repo use `playbook/vision_probe.py` with the model from `playbook/vision_config.json`,
  never a Claude subagent.

## Review Focus

- **Studio shows rounded numbers ("1.2K", "5.4%") or a localized format ("5,4 %").** Expected: exact integers and
  fractions from JSON; when only text exists, parse both decimal separators, and refuse "K/M" rounding for the CTR
  math (→ `FAIL read`). Test in Task 2.
- **A video goes public late (uploaded private, flipped public days later).** Expected: age counts from the public
  time, so it gets the 2-hourly window after going public. Test in Task 5.
- **Two runs overlap (a slow run, or Task Scheduler fires again).** Expected: the second exits at once on
  `studio.lock` and writes nothing. Test in Task 5.
- **The owner swaps a thumbnail by hand mid-test.** Expected: variant becomes `manual`, 7-day pause, no revert to
  the loop's variant. Tests in Task 6 (pause) and Task 8 (detection).
- **`thumbnails.set` fails (quota, 5xx) during a swap.** Expected: alternate not marked used, swap not logged as
  done, Telegram sent, the request lands in `actions/failed/` so this PC can re-request. Test in Task 4.

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

## Phase 2 — the loop

Revision 2026-11-19 (spec §5.8): the VPS collects and executes, this PC analyses. Tasks 2, 5 and 6 depend on the
Studio JSON shapes from Task 1. They wait for the spike and the owner's go. Tasks 3, 4, 7, 8 and 9 do not depend
on where the numbers come from, so they can be built now.

### Task 2: Parse Studio responses into a snapshot (VPS side, shared parser) — waits for Task 1

**Files:**
- Create: `scripts/studio_parse.py`
- Test: `scripts/test_studio_parse.py`, fixtures from Task 1 copied to `scripts/fixtures/studio/parse/`

**Interfaces:**
- Produces: `class ParseError(Exception)`.
- Produces `SNAPSHOT_COLS: tuple[str, ...]`, in this exact order: `captured_at_utc, video_id, age_hours, period,
  impressions, ctr, views, unique_viewers, avd_s, apv_pct, ret_30s, ret_60s, src_browse, src_suggested, src_search,
  src_other, thumb_hash, test_running`. There is no `thumb_variant` column: the variant is derived on this PC.
- Produces `parse_snapshot(payloads: list[dict], video_id: str, period: str) -> dict`. Keys are the metric
  columns. `ctr`, `apv_pct`, `ret_*` and `src_*` are fractions from 0 to 1; counts are ints; `test_running` is a bool.
- Produces `parse_number(text: str) -> float`.
- The module is pure and must not import Playwright or Google libraries: this PC imports it by path to re-parse
  `raw/`.

- [ ] **Step 1: Write the failing tests**
  - `test_parses_reach_fixture`: the exact numbers recorded in the spike report.
  - `test_missing_metric_raises`: a missing metric raises `ParseError`, never returns 0.
  - `test_parse_number_locales`: `"5.4%"`→`0.054`, `"5,4 %"`→`0.054`, `"12,345"`→`12345`, `"1.2K"`→`ParseError`.
  - `test_test_running_flag`.
  - `test_module_has_no_heavy_imports`: importing it does not add `playwright` or `googleapiclient` to
    `sys.modules`.
- [ ] **Step 2–4:** run `python -m unittest test_studio_parse -v` and see it FAIL, implement, run again and see it PASS.
- [ ] **Step 5: Commit** `feat(studio): parse Studio analytics JSON into snapshots`.

### Task 3: Drive layout writer and reader (shared)

**Files:**
- Create: `scripts/studio_store.py`
- Test: `scripts/test_studio_store.py` (fake Drive service, same style as `test_youtube_uploader.py`)

**Interfaces:**
- Consumes: `find_child`, `ensure_folder`, `download_text`, `_csv_media` from `youtube_uploader.py`.
- Produces: `STUDIO_DIR = "studio"`; `studio_folders(drive, chan_folder: str) -> dict[str, str]`. The keys are
  `studio`, `snapshots`, `raw`, `thumb_alts`, `actions_pending`, `actions_done` and `actions_failed`; each folder is
  created if missing.
- Produces `append_snapshot_rows(drive, folders: dict, day: str, cols: tuple, rows: list[dict]) -> None`, which
  writes `snapshots/<day>.csv` with a header on first write.
- Produces `put_raw(drive, folders: dict, day: str, video_id: str, utc: str, payloads: list[dict]) -> None`, which
  writes a gzip JSON array to `raw/<day>/<video_id>__<utc>.json.gz`.
- Produces `write_health(drive, folders: dict, status: str, detail: str, read: int, failed: int) -> None`, which
  writes `health.json`.
- Produces `list_files(drive, folder_id: str, since_name: str | None = None) -> list[dict]` (name-sorted) and
  `read_text(drive, file_id: str) -> str`.
- Produces `put_json(drive, folder_id: str, name: str, obj: dict) -> str` and
  `move_json(drive, file_id: str, src: str, dest: str, obj: dict) -> None`. `move_json` rewrites the content with
  `obj`, then moves the file.

- [ ] **Step 1: Write the failing tests**: `test_folders_created_once`, `test_snapshot_header_once`,
  `test_snapshot_append_keeps_rows`, `test_raw_is_gzip_json_roundtrip`, `test_health_json_fields`,
  `test_list_files_since_name`, `test_move_json_updates_and_moves`.
- [ ] **Step 2–4:** run, see FAIL, implement, run, see PASS.
- [ ] **Step 5: Commit** `feat(studio): Drive layout for snapshots, raw, health, actions`.

### Task 4: Action executor on the VPS

**Files:**
- Create: `scripts/studio_actions.py`
- Test: `scripts/test_studio_actions.py`

**Interfaces:**
- Consumes: `set_thumbnail(youtube, video_id, path)` and `notify(msg)` from `youtube_uploader.py`, and Task 3's
  `studio_folders`, `list_files`, `read_text`, `move_json`.
- Produces `run_actions(drive, youtube, chan_folder: str, done_folder: str, tmpdir: Path) -> tuple[int, int]`,
  returning (ok, failed). For each `actions/pending/*.json`, oldest first:
  - validate the request;
  - download the alt from `thumb-alts/<base>.<to_variant>.jpg`, or `done/<base>.jpg` for `main`;
  - call `set_thumbnail`;
  - add `result`;
  - move the file to `actions/done/` or `actions/failed/`;
  - send Telegram one line.
- CLI: `python studio_actions.py --channel "Bare Skull" [--once]`.

- [ ] **Step 1: Write the failing tests**:
  - `test_ok_moves_to_done_with_result`.
  - `test_missing_alt_moves_to_failed`: no `set_thumbnail` call.
  - `test_set_error_moves_to_failed_and_notifies`: `HttpError(500)`.
  - `test_invalid_request_failed`: an unknown `to_variant` or missing `video_id`.
  - `test_main_uses_done_jpg`.
  - `test_order_oldest_first`.
- [ ] **Step 2–4:** run, see FAIL, implement, run, see PASS.
- [ ] **Step 5: Commit** `feat(studio): execute thumbnail swap requests from Drive`.

### Task 5: Capture entry point on the VPS — waits for Task 1

**Files:**
- Create: `scripts/studio_capture.py`, `scripts/studio_config.example.json`
- Create: `docs/studio-capture.md` (VPS runbook)
- Test: `scripts/test_studio_capture.py`

**Interfaces:**
- Consumes: Tasks 2 and 3; `Lock` and `get_credentials` from `youtube_uploader.py`; the sign-in and challenge
  detection from `studio_spike.py`. Move `blocked_reason`, `wanted` and `scrub` into `scripts/studio_browser.py`;
  the spike and the capture both import them.
- Produces the CLI `python studio_capture.py --channel "Bare Skull" --config studio_config.json [--dry-run] [--once]`.
  Config keys: `profile_dir`, `daily_hour` (6), `channel`.
- Produces `due_videos(videos: list[dict], last_by_video: dict[str, datetime], now: datetime, daily_hour: int) -> list[str]`.
- Exit codes: 0 ok, 2 lock held, 3 auth or challenge, 4 navigation failure.
- After each capture run it calls `run_actions` (Task 4) once.

- [ ] **Step 1: Write the failing tests**:
  - `test_due_fresh_every_2h`.
  - `test_due_daily_after_hour`.
  - `test_late_public_counts_from_public_time`.
  - `test_private_unlisted_scheduled_skipped`.
  - `test_lock_held_exits_2_writes_nothing`.
  - `test_uploader_lock_held_skips`.
  - `test_auth_exit_3_health_fail_notify`.
  - `test_failed_read_twice_notifies_no_zero_rows`.
  - `test_transient_unreadable_page_repolled_once`: the deferred minor from Task 1.
- [ ] **Step 3: Implement.**
  - Video list and public time come from the Data API (uploads playlist plus `videos.list status,snippet`).
  - `thumb_hash` is the sha256 of the `maxresdefault.jpg` bytes.
  - Launch with Playwright `launch_persistent_context(profile_dir, channel="chrome", headless=False)`.
  - Jitter the start by 0–600 s and pause 5–15 s between videos.
  - Read periods `since_published`, plus `last_48h` for videos at least 3 days old.
  - Write `put_raw` and `append_snapshot_rows`, then `write_health`.
- [ ] **Step 4:** run, see PASS.
- [ ] **Step 5: Runbook** `docs/studio-capture.md`. It covers:
  - Chrome and `pip install`;
  - the one-time sign-in (`studio_spike.py --login`);
  - disconnecting RDP without signing out;
  - the Task Scheduler entry: every 30 min, `--once`, "run only when user is logged on";
  - what each health status and Telegram message means.
- [ ] **Step 6: Dry run on the VPS** with `--dry-run --once`.
- [ ] **Step 7: Commit** `feat(studio): VPS capture entry point`.

### Task 6: Verdict rules (this PC, pure)

**Files:**
- Create: `loop/rules.py`, `loop/__init__.py`
- Test: `loop/test_rules.py`

**Interfaces:**
- Produces the constants `KEEP_CTR=0.057`, `BASE_BAR=0.053`, `MEDIAN_FACTOR=0.8`, `MIN_VIDEOS_FOR_MEDIAN=10`,
  `MIN_IMPR=2000`, `MIN_AGE_H=6`, `MIN_GAP_H=24`, `MAX_SWAPS=2`, `MAX_REVERTS=1`, `MANUAL_PAUSE_D=7` and `Z90=1.645`.
- Produces `ctr_interval(ctr: float, impressions: int, z: float = Z90) -> tuple[float, float]` (Wilson).
- Produces `age_bucket(age_hours) -> str`, with the buckets `"0-6","6-12","12-24","24-48","48-72","72+"`.
- Produces `swap_bar(age_hours: float, history: list[dict]) -> float`.
- Produces `@dataclass VideoState(variant: str, swaps: int, reverts: int, last_swap_utc: datetime | None,
  impr_at_last_swap: int, variant_stats: dict[str, tuple[int, float]], manual_until: datetime | None,
  alts_left: list[str], pending_action: bool)`.
- Produces `Verdict` (`TOO_EARLY, KEEP, WATCH, SWAP, REVERT, CHANGE_TITLE, NOT_PACKAGING, OPENING, PAUSED`).
- Produces `decide(snap: dict, state: VideoState, bar: float, now: datetime, typical_impr: float | None,
  median_ret_30s: float | None) -> tuple[Verdict, str]`.

- [ ] **Step 1: Write the failing tests**:
  - `test_too_early_age`: 5.9 h with 50,000 impressions gives `TOO_EARLY`.
  - `test_too_early_impressions`: 10 h with 1,999 impressions gives `TOO_EARLY`.
  - `test_keep`: CTR 0.057 with 3,000 impressions gives `KEEP`.
  - `test_swap`: CTR 0.040, 4,000 impressions, alternates left, no test running → `SWAP`.
  - `test_no_swap_when_interval_reaches_bar`: CTR 0.050 with 2,000 impressions gives `WATCH`.
  - `test_no_swap_during_test`.
  - `test_no_second_request_while_pending`: `pending_action=True` gives `WATCH`.
  - `test_gap_after_swap`: 23 h gives `WATCH`; 24 h plus 2,000 more impressions can give `SWAP`.
  - `test_revert`: main (5,000, 0.052) against alt1 (2,500, 0.030) gives `REVERT`.
  - `test_change_title_after_alts`.
  - `test_manual_pause`: gives `PAUSED`, with no revert to a variant the loop set.
  - `test_not_packaging`.
  - `test_opening`.
  - `test_swap_bar_median`: 10 videos at a 0.050 median give 0.040; 9 videos give 0.053.
  - `test_wilson_known_value`.
- [ ] **Step 3: Implement.** Check in this order: PAUSED, TOO_EARLY, REVERT, KEEP, SWAP, CHANGE_TITLE,
  NOT_PACKAGING, OPENING, WATCH. NOT_PACKAGING applies when impressions are below 0.5 × the channel median for the
  same age bucket.
- [ ] **Step 2/4:** run `python -m unittest loop.test_rules -v` from the repo root, see FAIL, then PASS.
- [ ] **Step 5: Commit** `feat(loop): verdict and swap rules`.

### Task 7: Fetch from Drive into a local cache (this PC)

**Files:**
- Create: `loop/fetch.py`
- Test: `loop/test_fetch.py`

**Interfaces:**
- Consumes Task 3's `studio_folders`, `list_files` and `read_text`, imported from `scripts/`. Uses its own Drive
  token on this PC, `loop/token-drive.json`, which is git-ignored, with the existing `client_secret.json` flow.
- Produces `fetch(drive, chan_folder: str, cache: Path) -> dict`. It downloads every `snapshots/*.csv` that is new or
  changed (by modifiedTime), every `actions/done|failed/*.json`, the names of `actions/pending/*.json`,
  `thumbnail-features.csv` and `health.json` into `cache/` (default `loop/cache/`, git-ignored). It returns counts.
- Produces `load_snapshots(cache: Path) -> list[dict]` (typed values) and `load_actions(cache: Path) -> list[dict]`.
- Produces the CLI `python -m loop.fetch --channel "Bare Skull"`.

- [ ] **Step 1: Write the failing tests**: `test_incremental_skips_unchanged`, `test_redownloads_changed_day`,
  `test_load_snapshots_types` (ints, floats, bools, empty → None, never 0), `test_health_fail_surfaces_in_counts`.
- [ ] **Step 2–4:** run, see FAIL, implement, run, see PASS.
- [ ] **Step 5: Commit** `feat(loop): fetch studio data from Drive`.

### Task 8: Analyse, report and request swaps (this PC)

**Files:**
- Create: `loop/analyse.py`, `loop/config.example.json`
- Test: `loop/test_analyse.py`
- Modify: `.gitignore` (add `loop/cache/`, `loop/token-*.json`, `loop/config.json`)

**Interfaces:**
- Consumes Tasks 6 and 7, Task 3's `put_json`, and `notify` from `scripts/youtube_uploader.py`.
- Config keys: `go_live` (date), `alert_only_days` (7), `report_dir` (default
  `D:/Youtube/video_footage_re_create/Bare Skull - 2nd brain/van-hanh/bao-cao/studio`).
- Produces `build_states(snapshots, actions, features, now) -> dict[str, VideoState]`.
  - The variant comes from `actions/done`.
  - A `thumb_hash` change with no done action gives `manual`, with `manual_until` set to now + 7 days.
  - `variant_stats` come from each variant's impressions and clicks while it was live.
- Produces `analyse(cache: Path, cfg: dict, now: datetime, drive=None) -> list[dict]`. It writes `studio/verdicts.csv`
  to Drive, and `<report_dir>/YYYY-MM-DD.md` (a verdicts table plus a weekly "why" section on Mondays). It sends
  Telegram once per verdict change, and writes swap requests unless alert-only applies.
- Produces the CLI `python -m loop.analyse --channel "Bare Skull" [--dry-run]`, plus a wrapper `loop/run.ps1` that
  runs fetch and then analyse for Task Scheduler on this PC (every 2 h).

- [ ] **Step 1: Write the failing tests**:
  - `test_alert_only_writes_no_requests`: the report contains "would swap".
  - `test_live_writes_request_json_shape`: exact keys per spec §5.8.
  - `test_manual_change_detected`.
  - `test_variant_stats_split_by_swap_time`.
  - `test_notify_once_per_verdict_change`.
  - `test_why_section_names_differing_feature`: given feature rows where alt1 differs in background and won.
  - `test_health_fail_auth_reported_first`.
- [ ] **Step 2–4:** run, see FAIL, implement, run, see PASS.
- [ ] **Step 5: Commit** `feat(loop): analyse snapshots, write verdicts, report and swap requests`.

## Phase 2b — build repo (`D:/Youtube/video_footage_re_create`), not committed unless the owner asks

### Task 9: Thumbnail features and alternates in the push

**Files:**
- Create: `playbook/thumb_features.py`
- Modify: `playbook/upload_tools/push_to_pending.py`
- Test: `playbook/tests/test_thumb_features.py`

**Interfaces:**
- Consumes `thumb_gate.measure(im)` and `thumb_gate.check(src)`, plus `vision_probe.call(model, path, key, prompt)`,
  `vision_probe.parse(text)`, `vision_probe.get_key()` and `vision_probe.CONFIG`.
- Produces `FEATURE_COLS = ("base","variant","sha256","differs_from_main","brightness","saturation","colourfulness",
  "warmth","background","gun_width_share","gun_angle","props","word_count","words","accent_colour",
  "text_height_share","pushed_utc")` and `features(path: Path, base: str, variant: str, differs: str) -> dict`.
- `push_to_pending.py` changes:
  - It looks for `output/final/<base>.alt1.jpg` and `<base>.alt2.jpg`.
  - It refuses to push if any of the three thumbnails fails `thumb_gate.check`.
  - It uploads the alternates to `studio/thumb-alts/`.
  - It appends three rows to `studio/thumbnail-features.csv`.

- [ ] **Step 1: Write the failing tests** (vision mocked): `test_measures_match_thumb_gate`,
  `test_vision_answers_mapped`, `test_vision_failure_marks_unread`, `test_push_refuses_failing_alt`.
- [ ] **Step 2–4:** run `python -m unittest playbook.tests.test_thumb_features -v`, see FAIL, implement, see PASS.

### Task 10: Staff instructions for alternates and the weekly review

**Files:**
- Modify: `Bare Skull - 2nd brain/van-hanh/so-tay/nv-chu-de.md` and `.claude/skills/quan-ly-kenh/references/hoi-cuu.md`.

- [ ] **Step 1: nv-chu-de.** Make the main thumbnail plus `alt1` and `alt2`. Each alternate differs from the main in
  one named feature, recorded in `THUMBNAIL.md` as `alt1: differs in <feature>`. All three pass `thumb_gate.py` and
  the vision policy read.
- [ ] **Step 2: hoi-cuu.md.** At the 48 h and 7-day reviews and every Monday, read
  `bao-cao/studio/YYYY-MM-DD.md` (written by `loop/analyse.py`). Carry its "why" lines into the weekly report.
  Propose an edit to `raw/form-thumbnail.md` when a feature has won 3 or more times.
- [ ] **Step 3: Check** that `kiem_bang.py` still exits 0.
