"""Apply a title change at once (owner 2026-11-28: "you will make the decision to apply immediately when you find
something that needs to change"). Scheduler step 3c of bareskull-studio-loop calls this after it has written its
proposals. It does not decide anything: it checks the guards below and, if they all hold, writes the same request file
the loop writes (studio/actions/pending/<video>__<utc>.json); the VPS applies it on its next read.

    python -m loop.apply_change title --video ID --title "New title" --reason "why" [--dry-run]

Guards (any one failing = refuse, exit 1, nothing written):
  - the video is in today's bad_videos pack, is not being ridden, and is at most 168 h old;
  - the new title is at most 100 characters, has no link or < >, and differs from the live title;
  - every number in it already stands in the film's locked Title section (title_alts.numbers): the narration froze them;
  - it trips no pattern of topic_engine/policy_rules.json (kill or flag);
  - no change (thumbnail or title) to this video in the last 24 h, no request still pending, the owner has not
    changed the title by hand, and a spare slot (alt1 / alt2) is left.
Thumbnails: a new image cannot be made here. A thumbnail that needs one is left as a proposal.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loop import fetch as lf
from loop import titles as tl
from loop.bad_videos import PROJECTS, STUDIO
from loop.titles import ALTS

TITLE_MAX = 100
GAP_H = 24
MAX_AGE_H = 168
POLICY_RULES = Path(r"D:\Youtube\video_footage_re_create\topic_engine\policy_rules.json")
UPLOAD_TOOLS = Path(r"D:\Youtube\video_footage_re_create\playbook\upload_tools")



def policy_hits(title: str, rules: dict) -> list[str]:
    low = title.lower()
    return [r["id"] for kind in ("kill", "flag") for r in rules.get(kind, [])
            if any(re.search(p, low) for p in r.get("patterns", []))]


def last_change_utc(actions: list, vid: str) -> datetime | None:
    best = None
    for a in actions:
        if a.get("video_id") != vid:
            continue
        res = a.get("result") or {}
        if a.get("_state") != "done" or res.get("status") != "ok":
            continue
        try:
            at = tl.parse_utc(res.get("at_utc"))
        except ValueError:
            at = None
        if at is not None and (best is None or at > best):
            best = at
    return best


def check(video: dict, new_title: str, locked_numbers: set, rules: dict, actions: list, pending: list,
          now: datetime) -> tuple[list[str], dict | None]:
    """Refusals (empty = go) and the title state the request needs: {'from': variant, 'to': variant}."""
    why = []
    vid = video["video_id"]
    if video.get("being_ridden"):
        why.append("video is being ridden (a winner)")
    if (video.get("age_h") or 0) > MAX_AGE_H:
        why.append("older than %d h" % MAX_AGE_H)
    t = " ".join(new_title.split())
    if not t or len(t) > TITLE_MAX:
        why.append("title empty or longer than %d characters" % TITLE_MAX)
    if "<" in t or ">" in t or re.search(r"https?://|www\.|\.com\b", t, re.I):
        why.append("title has a link or < >")
    if t == (video.get("live_title") or "").strip():
        why.append("same as the live title")
    extra = sorted(_title_alts().numbers(t) - locked_numbers)
    if extra:
        why.append("states number(s) the film does not pay: %s" % ", ".join(extra))
    hits = policy_hits(t, rules)
    if hits:
        why.append("policy pattern(s): %s" % ", ".join(hits))
    if vid in tl.manual_videos(actions):
        why.append("owner changed the title by hand")
    if any(p.startswith(vid + "__") for p in pending):
        why.append("a request for this video is still pending")
    last = last_change_utc(actions, vid)
    if last is not None and now - last < timedelta(hours=GAP_H):
        why.append("changed %.1f h ago (< %d h)" % ((now - last).total_seconds() / 3600, GAP_H))
    used = [a["to_title_variant"] for _, a in tl.ok_actions(actions).get(vid, [])]
    free = [v for v in ALTS if v not in used]
    if not free:
        why.append("no spare title slot left (alt1 and alt2 used)")
    state = {"from": used[-1] if used else "main", "to": free[0] if free else None}
    return why, state


def _title_alts():
    """The same module push_to_pending uses for spare titles, so both read numbers the same way ('$1,489' -> '1489')."""
    if str(UPLOAD_TOOLS) not in sys.path:
        sys.path.insert(0, str(UPLOAD_TOOLS))
    import title_alts
    return title_alts


def locked_numbers(slug: str) -> set:
    md = PROJECTS / slug / "UPLOAD.md"
    ta = _title_alts()
    return ta.numbers(ta.title_section(md.read_text(encoding="utf-8"))) if md.exists() else set()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("kind", choices=["title"])
    ap.add_argument("--video", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cache", default=str(lf.DEFAULT_CACHE))
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    ap.add_argument("--pack", default=None, help="bad_videos pack (default: today's)")
    ap.add_argument("--token-drive", default=str(lf.SCRIPTS / "token-drive-link.json"))
    a = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    pack_path = Path(a.pack) if a.pack else STUDIO / "de-xuat" / ("%s-du-lieu.json" % datetime.now().strftime("%Y-%m-%d"))
    pack = {v["video_id"]: v for v in json.loads(pack_path.read_text(encoding="utf-8"))["videos"]}
    video = pack.get(a.video)
    if video is None:
        print("REFUSED: %s is not in %s" % (a.video, pack_path.name))
        return 1
    cache = Path(a.cache)
    actions = lf.load_actions(cache)
    pf = cache / "actions" / "pending.txt"
    pending = pf.read_text(encoding="utf-8").split() if pf.exists() else []
    rules = json.loads(POLICY_RULES.read_text(encoding="utf-8")) if POLICY_RULES.exists() else {}
    why, st = check(video, a.title, locked_numbers(video.get("slug") or ""), rules, actions, pending, now)
    if why:
        print("REFUSED %s: %s" % (a.video, "; ".join(why)))
        return 1
    title = " ".join(a.title.split())
    rq = {"kind": tl.KIND, "video_id": a.video, "base": video.get("slug") or "", "to_title_variant": st["to"],
          "to_title": title, "expect_title": video["live_title"].strip(), "from_title_variant": st["from"],
          "reason": a.reason, "requested_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    name = "%s__%s.json" % (a.video, now.strftime("%Y%m%dT%H%M%SZ"))
    if a.dry_run:
        print("DRY-RUN would write %s:\n%s" % (name, json.dumps(rq, ensure_ascii=False, indent=2)))
        return 0
    from googleapiclient.discovery import build
    import studio_store as ss
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    creds = lf.token_or_exit("apply_change", Path(a.token_drive), cache, cfg, send=print)
    if creds is None:
        return 3
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    folders = ss.studio_folders_by_id(drive, cfg["studio_folder_id"])
    ss.put_json(drive, folders["actions_pending"], name, rq)
    log = STUDIO / "de-xuat" / "da-ap-dung.md"
    line = "- %s %s %s: \"%s\" -> \"%s\" (%s) — %s\n" % (now.strftime("%Y-%m-%d %H:%M UTC"), a.video, video.get("slug") or "-",
                                                      rq["expect_title"], title, st["to"], a.reason)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        if log.stat().st_size == 0:
            fh.write("# Thay đổi đã áp dụng từ bước đề xuất (loop.apply_change)\n\n")
        fh.write(line)
    print("WROTE %s (applies on the VPS's next read)" % name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
