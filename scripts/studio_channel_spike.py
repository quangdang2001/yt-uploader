"""Spike: chup JSON cua moi tab con trong trang phan tich KENH (analytics/hub_*) de viet bo doc.

Chay tren VPS (profile Chrome da dang nhap, khong co lan capture nao dang chay):
    python studio_channel_spike.py --config studio_config.json [--upload]

Chi doc: mo URL, cuon, khong bam, khong go. Luu body JSON da scrub (khong header/cookie/token)
vao --out/<tab>.json va chu hien tren trang vao --out/<tab>.txt. --upload ghi them mot ban gzip
vao Drive studio/raw/channel-spike/ de may chu kenh doc.
Exit: 0 ok, 3 gap dang nhap/thu thach, 4 khong mo duoc trang chu Studio.
"""
import argparse
import gzip
import json
import re
import sys
import threading
import os
from datetime import datetime, timezone
from pathlib import Path

import studio_browser as sb

# Ung vien neu khong tim thay link tab trong DOM; tab nao khong ton tai thi Studio chuyen ve tong quan.
CANDIDATE_TABS = ("hub_overview", "hub_content", "hub_audience", "hub_revenue", "hub_research",
                  "hub_inspiration", "hub_trends")
LINK_JS = """(() => {
  const out = new Set();
  const walk = (root) => {
    for (const e of root.querySelectorAll('*')) {
      const h = e.getAttribute && (e.getAttribute('href') || '');
      if (h && h.includes('/analytics/')) out.add(h);
      if (e.shadowRoot) walk(e.shadowRoot);
    }
  };
  walk(document);
  return Array.from(out);
})()"""
CHANNEL_RE = re.compile(r"/channel/(UC[A-Za-z0-9_-]{22})")
WATCHDOG_S = 30 * 60


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="studio_config.json")
    ap.add_argument("--out", default=r"C:\yt\spike\channel")
    ap.add_argument("--upload", action="store_true")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8")) if Path(a.config).exists() else {}
    profile = cfg.get("profile_dir") or r"C:\yt\studio-profile"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    wd = threading.Timer(WATCHDOG_S, lambda: (print("WATCHDOG", flush=True), os._exit(1)))
    wd.daemon = True
    wd.start()

    results = {}
    with sb.open_browser(profile) as page:
        try:
            page.goto("https://studio.youtube.com/", wait_until="domcontentloaded", timeout=sb.GOTO_TIMEOUT_MS)
            page.wait_for_load_state("networkidle", timeout=sb.IDLE_TIMEOUT_MS)
        except Exception as e:
            print(f"canh bao mo trang chu: {e}")
        why = sb.blocked_reason(page)
        if why:
            print("BLOCKED: " + why)
            return 3
        m = CHANNEL_RE.search(page.url)
        if not m:
            page.wait_for_timeout(5000)
            m = CHANNEL_RE.search(page.url)
        if not m:
            print(f"khong thay channel id trong URL {page.url[:120]}")
            return 4
        cid = m.group(1)
        base = f"https://studio.youtube.com/channel/{cid}/analytics/"
        page.goto(base + "hub_overview", wait_until="domcontentloaded", timeout=sb.GOTO_TIMEOUT_MS)
        try:
            page.wait_for_load_state("networkidle", timeout=sb.IDLE_TIMEOUT_MS)
        except Exception:
            pass
        page.wait_for_timeout(sb.SETTLE_MS)
        links = page.evaluate(LINK_JS) or []
        slugs = []
        for h in links:
            mm = re.search(r"/analytics/([A-Za-z0-9_-]+)", h)
            if mm and mm.group(1) not in slugs:
                slugs.append(mm.group(1))
        print("link tab trong DOM:", slugs)
        for s in CANDIDATE_TABS:
            if s not in slugs:
                slugs.append(s)

        for tab in slugs:
            bodies = []

            def on_response(resp, bodies=bodies):
                try:
                    if resp.status != 200 or not sb.wanted(resp.url):
                        return
                    if "json" not in (resp.headers.get("content-type") or "").lower():
                        return
                    bodies.append({"url": resp.url.split("?")[0],
                                   "body": sb.scrub(json.loads(resp.body().decode("utf-8")))})
                except Exception:
                    return

            page.on("response", on_response)
            try:
                page.goto(base + tab, wait_until="domcontentloaded", timeout=sb.GOTO_TIMEOUT_MS)
                try:
                    page.wait_for_load_state("networkidle", timeout=sb.IDLE_TIMEOUT_MS)
                except Exception:
                    pass
                page.wait_for_timeout(sb.SETTLE_MS)
                sb.check_page(page)
                landed = page.url
                sb.scroll_phase(page)
                text = page.inner_text("body")
            except sb.Blocked as e:
                print("BLOCKED: " + str(e))
                return 3
            except Exception as e:
                print(f"tab {tab} loi: {type(e).__name__}: {e}")
                landed, text = "", ""
            finally:
                page.remove_listener("response", on_response)
            (out / f"{tab}.json").write_text(json.dumps({"landed": landed, "bodies": bodies},
                                                        ensure_ascii=False, indent=1), encoding="utf-8")
            (out / f"{tab}.txt").write_text(text, encoding="utf-8")
            results[tab] = {"landed": landed, "bodies": bodies, "text": text}
            print(f"tab {tab}: {len(bodies)} body JSON, trang {landed[-60:]}")
    wd.cancel()

    if a.upload:
        import studio_capture as sc
        import studio_store as ss
        import youtube_uploader as yu
        ns = argparse.Namespace(token_drive=str(yu.SCRIPT_DIR / "token-drive.json"),
                                token_youtube=str(yu.SCRIPT_DIR / "token-youtube.json"))
        drive, _ = sc.connect(ns)
        folders = yu.resolve_folders(drive, "YouTube-Upload", cfg.get("channel") or "Bare Skull")
        sf = ss.studio_folders(drive, folders["channel"])
        day = yu.ensure_folder(drive, "channel-spike", sf["raw"])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        data = gzip.compress(json.dumps(results, ensure_ascii=False).encode("utf-8"))
        ss._upsert(drive, day, f"channel__{stamp}.json.gz", data, "application/gzip")
        print(f"da ghi Drive studio/raw/channel-spike/channel__{stamp}.json.gz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
