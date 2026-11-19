"""Spike do do tuoi cua so lieu YouTube Studio va luu JSON body de Task 2 doc.

Chay:  python studio_spike.py --profile C:/yt/studio-profile --video <id> [--headless]
Chi doc. Khong dien form, khong go vao trang. Chi luu BODY cua response JSON
(khong luu header, cookie, token; cac key nhay cam bi thay bang "<scrubbed>").
Dang nhap mot lan: --login (co cua so, doi toi 15 phut, khong go/bam gi).
Exit: 0 ok, 3 gap trang dang nhap / thu thach Google, 4 khong mo duoc trang,
1 watchdog het gio (300 s).
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

TABS = ("tab-reach_viewers", "tab-interest_viewers")
KEY_RE = re.compile(r"impression|ctr|view|averageviewduration|retention", re.I)
SCRUB_RE = re.compile(r"token|xsrf|session|auth|cookie|visitordata", re.I)
URL_BAD_RE = re.compile(r"/sorry/|challenge|signin|servicelogin|consent", re.I)
TEXT_BAD_RE = re.compile(
    r"verify it.?s you|confirm it.?s you|unusual traffic|not a robot|captcha|"
    r"sign in to continue|choose an account", re.I)
SETTLE_MS = 8000
WATCHDOG_S = 300
LOGIN_WAIT_S = 900


def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def wanted(url):
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host == "studio.youtube.com":
        return True
    return host in ("youtube.com", "www.youtube.com") and u.path.startswith("/youtubei/")


def slug(url):
    path = urlparse(url).path.strip("/").split("/")
    s = "_".join(p for p in path[-3:] if p) or "root"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", s)[:60]


def scrub(node):
    if isinstance(node, dict):
        return {k: ("<scrubbed>" if SCRUB_RE.search(k) else scrub(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [scrub(v) for v in node]
    return node


def walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else k
            if KEY_RE.search(k):
                if isinstance(v, (dict, list)):
                    yield p, json.dumps(v, ensure_ascii=False, separators=(",", ":"))[:200]
                else:
                    yield p, v
            yield from walk(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


def blocked_reason(page):
    """Tra ve ly do neu trang khong phai Studio binh thuong, nguoc lai None."""
    host = (urlparse(page.url).hostname or "").lower()
    if host != "studio.youtube.com":
        return f"host la {host or '(trong)'}, khong phai studio.youtube.com ({page.url[:120]})"
    if URL_BAD_RE.search(page.url):
        return f"URL co dau hieu dang nhap/thu thach ({page.url[:120]})"
    try:
        if page.locator("input[type=password]").count() > 0:
            return "trang co o nhap mat khau"
        txt = page.inner_text("body")
        m = TEXT_BAD_RE.search(txt)
        if m:
            return f"noi dung trang co '{m.group(0)}'"
    except Exception:
        pass
    return None


def bail(ctx, reason):
    print("CHUA DANG NHAP / BI THU THACH: " + reason + ". "
          "Chay lai khong --headless va dang nhap hoac xac minh tay trong cua so Chrome.")
    try:
        ctx.close()
    except Exception:
        pass
    sys.exit(3)


def login(profile):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(profile, channel="chrome", headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"canh bao mo trang: {e}")
        print("Sign in by hand in the Chrome window; waiting up to 15 min", flush=True)
        end = time.time() + LOGIN_WAIT_S
        while time.time() < end:
            try:
                if blocked_reason(page) is None:
                    print("Signed in; profile saved")
                    ctx.close()
                    return 0
            except Exception:
                pass
            time.sleep(5)
        try:
            ctx.close()
        except Exception:
            pass
    print("Het 15 phut ma chua dang nhap.")
    return 3


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True, help="thu muc profile Chrome ben vung")
    ap.add_argument("--video", help="video id (bat buoc tru khi --login)")
    ap.add_argument("--login", action="store_true", help="dang nhap tay mot lan, luu profile")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--out", default="scripts/fixtures/studio")
    a = ap.parse_args()
    if a.login:
        if a.headless:
            ap.error("--login can cua so that, khong dung duoc voi --headless")
        return login(a.profile)
    if not a.video:
        ap.error("--video la bat buoc (tru khi --login)")

    from playwright.sync_api import sync_playwright

    def _timeout():
        print(f"WATCHDOG: qua {WATCHDOG_S} s, thoat.", flush=True)
        os._exit(1)

    wd = threading.Timer(WATCHDOG_S, _timeout)
    wd.daemon = True
    wd.start()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    saved = []  # (path, url, data)
    seen = {}

    def on_response(resp):
        try:
            if resp.status != 200 or not wanted(resp.url):
                return
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype:
                return
            data = scrub(json.loads(resp.body().decode("utf-8")))
        except Exception:
            return  # khong phai JSON, bo qua
        n = seen.get(resp.url, 0)
        seen[resp.url] = n + 1
        name = f"{a.video}__{stamp}__{slug(resp.url)}{'' if not n else '_' + str(n)}.json"
        base = out / name
        k = 1
        while base.exists():
            base = out / f"{base.stem}_{k}.json"
            k += 1
        base.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        saved.append((base, resp.url, data))

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(a.profile, channel="chrome", headless=a.headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("response", on_response)
        for tab in TABS:
            url = f"https://studio.youtube.com/video/{a.video}/analytics/{tab}/period-default"
            print(f"mo {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"LOI mo trang {url}: {e}")
                try:
                    ctx.close()
                except Exception:
                    pass
                sys.exit(4)
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            why = blocked_reason(page)
            if why:
                bail(ctx, why)
            page.wait_for_timeout(SETTLE_MS)
            why = blocked_reason(page)  # chuyen huong muon sau khi cho
            if why:
                bail(ctx, why)
            try:
                txt = page.inner_text("body")
                for line in txt.splitlines():
                    if re.search(r"updated|cap nhat", line, re.I):
                        print(f"studio-text[{tab}]: {line.strip()[:160]}")
            except Exception:
                pass
        ctx.close()
    wd.cancel()

    print(f"\nda luu {len(saved)} file JSON vao {out}")
    print("\nendpoint  key  value")
    for path, url, data in saved:
        ep = urlparse(url).path
        for k, v in walk(data):
            print(f"{ep}  {k}  {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
