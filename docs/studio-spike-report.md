# Studio spike report

Status: SKELETON. Steps 2-6 need the channel owner, the VPS and real uploads.

## How to run

1. Install: `pip install -r scripts/requirements.txt` then `python -m playwright install chrome` (or have Chrome installed).
2. Sign in once over RDP (headed, owner signs in by hand):
   `python scripts/studio_spike.py --profile C:\yt\studio-profile --video <id>`
   Re-run the same command: it must reach Studio without a prompt. Exit 3 means still signed out.
3. Freshness runs, after the next upload goes public, at about 2, 6, 12, 24 h, plus once on a video over 7 days old:
   `python scripts/studio_spike.py --profile C:\yt\studio-profile --video <id>`
   `yt-dlp --print view_count https://www.youtube.com/watch?v=<id>`
   Fill the table below from the printed `endpoint  key  value` lines.
4. RDP-disconnect test: disconnect RDP (do not sign out). Run from Task Scheduler as "run only when user is logged on".
   If headed Chrome does not complete, repeat with `--headless`.

## Freshness table

| Run time (UTC) | Age (hours) | Studio impressions | Studio CTR | Studio views | yt-dlp views | Reporting API latest day |
|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |

## Endpoints found

| Endpoint (path) | Carries (impressions / CTR / views / AVD / retention / traffic / Test & Compare) | Fixture file |
|---|---|---|
|  |  |  |

## RDP-disconnect result

| Mode | Completed? | Notes |
|---|---|---|
| Headed |  |  |
| Headless |  |  |

## Security prompts seen

(none recorded yet)

## Gate

Phase 2 starts only if Studio impressions/CTR were clearly fresher than about 48 h and the owner says go.
