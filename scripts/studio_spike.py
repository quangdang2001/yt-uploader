"""Spike do do tuoi cua so lieu YouTube Studio va luu JSON body de Task 2 doc.

Chay:  python studio_spike.py --profile C:/yt/studio-profile --video <id> [--headless]
Chi doc. Khong dien form, khong go vao trang. Chi luu BODY cua response JSON
(khong luu header, cookie, token).
Exit: 0 ok, 3 gap trang dang nhap Google.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

TABS = ("tab-reach_viewers", "tab-interest_viewers")
KEY_RE = re.compile(r"impression|ctr|view|averageviewduration|retention", re.I)
SETTLE_MS = 8000


def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def wanted(url):
    host = urlparse(url).hostname or ""
    return host.endswith("studio.youtube.com") or "youtubei" in url


def slug(url):
    path = urlparse(url).path.strip("/").split("/")
    s = "_".join(p for p in path[-3:] if p) or "root"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", s)[:60]


def walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else k
            if KEY_RE.search(k) and not isinstance(v, (dict, list)):
                yield p, v
            yield from walk(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


def is_signin(page):
    host = urlparse(page.url).hostname or ""
    if host == "accounts.google.com":
        return True
    try:
        return page.locator("input[type=password], input[type=email]").count() > 0 \
            and "studio.youtube.com" not in host
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True, help="thu muc profile Chrome ben vung")
    ap.add_argument("--video", required=True, help="video id")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--out", default="scripts/fixtures/studio")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    saved = []  # (path, url)
    seen = {}

    def on_response(resp):
        try:
            if not wanted(resp.url):
                return
            body = resp.body()
            data = json.loads(body.decode("utf-8"))
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
                try:
                    page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    pass
            except Exception as e:
                print(f"loi mo trang: {e}")
            if is_signin(page):
                print("CHUA DANG NHAP: trang dang o accounts.google.com / form dang nhap. "
                      "Chay lai khong --headless va dang nhap tay trong cua so Chrome.")
                ctx.close()
                sys.exit(3)
            page.wait_for_timeout(SETTLE_MS)
            try:
                txt = page.inner_text("body")
                for line in txt.splitlines():
                    if re.search(r"updated|cap nhat", line, re.I):
                        print(f"studio-text[{tab}]: {line.strip()[:160]}")
            except Exception:
                pass
        ctx.close()

    print(f"\nda luu {len(saved)} file JSON vao {out}")
    print("\nendpoint  key  value")
    for path, url, data in saved:
        ep = urlparse(url).path
        for k, v in walk(data):
            print(f"{ep}  {k}  {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
