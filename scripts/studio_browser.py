"""Ham trinh duyet dung chung cho studio_spike.py va studio_capture.py.

Chi doc. Khong dien form, khong go vao trang. Chi giu BODY cua response JSON
(khong header, cookie, token; key nhay cam bi thay bang "<scrubbed>").
Playwright chi duoc import trong open_browser(), nen module nay import nhe.
"""
import json
import logging
import re
import time
import unicodedata
from contextlib import contextmanager
from urllib.parse import quote, urlparse

log = logging.getLogger("uploader.studio_browser")

AUDIENCE_TAB = "tab-build_audience"
TABS = ("tab-reach_viewers", "tab-interest_viewers", AUDIENCE_TAB)
OPTIONAL_TABS = (AUDIENCE_TAB,)   # loi o tab nay khong lam hong video
SCROLL_STEP_PX = 1200
SCROLL_STEP_MS = 1500
SCROLL_MAX_STEPS = 12
SCROLL_CAP_S = 25                 # ca pha cuon: cuon + cho mang yen
WHEEL_MAX_STEPS = 6               # lan chuot khong biet vi tri, nen gioi han so buoc
SCROLL_IDLE_TIMEOUT_MS = 10000
# %d = so pixel moi buoc. Tra ve vi tri cuon sau buoc (scrollTop cua phan tu cuon).
SCROLL_JS = """(() => {
  const step = %d;
  const tall = (el) => el && el.scrollHeight > el.clientHeight + 1;
  const room = (e) => e.scrollHeight - e.clientHeight;
  let el = document.scrollingElement;
  if (!tall(el)) {
    el = null;
    let best = 0;
    const walk = (root) => {
      for (const e of root.querySelectorAll('*')) {
        const oy = getComputedStyle(e).overflowY;
        if ((oy === 'auto' || oy === 'scroll') && tall(e) && room(e) > best) {
          best = room(e); el = e;
        }
        if (e.shadowRoot) walk(e.shadowRoot);
      }
    };
    walk(document);
  }
  if (!el) return 0;
  el.scrollBy(0, step);
  return el.scrollTop;
})()"""
SCRUB_RE = re.compile(r"token|xsrf|session|auth|cookie|visitordata", re.I)
URL_BAD_RE = re.compile(r"/sorry/|challenge|signin|servicelogin|consent", re.I)
# Studio cua chu kenh co the hien tieng Viet: cung cac thu thach do, bang tieng Viet (NFC).
TEXT_BAD_VI = ("xác minh đó là bạn", "tôi không phải là người máy", "lưu lượng truy cập bất thường",
               "đăng nhập để tiếp tục", "chọn tài khoản")
TEXT_BAD_RE = re.compile(
    r"verify it.?s you|confirm it.?s you|unusual traffic|not a robot|captcha|"
    r"sign in to continue|choose an account|"
    + "|".join(re.escape(unicodedata.normalize("NFC", p)) for p in TEXT_BAD_VI), re.I)
SETTLE_MS = 8000
REPOLL_MS = 3000
GOTO_TIMEOUT_MS = 60000
IDLE_TIMEOUT_MS = 30000
UNREADABLE = "could not read page"


class Blocked(Exception):
    """Trang dang nhap, CAPTCHA hoac "verify it's you": dung ca lan chay."""


class NavError(Exception):
    """Khong mo duoc / khong doc duoc trang cua mot video."""


def tab_url(video_id, tab):
    return f"https://studio.youtube.com/video/{video_id}/analytics/{tab}/period-default"


def wanted(url):
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host == "studio.youtube.com":
        return True
    return host in ("youtube.com", "www.youtube.com") and u.path.startswith("/youtubei/")


def scrub(node):
    if isinstance(node, dict):
        return {k: ("<scrubbed>" if SCRUB_RE.search(k) else scrub(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [scrub(v) for v in node]
    return node


def blocked_reason(page):
    """Tra ve ly do neu trang khong phai Studio binh thuong, nguoc lai None."""
    try:
        host = (urlparse(page.url).hostname or "").lower()
    except Exception as e:
        return f"{UNREADABLE}: {type(e).__name__}"
    if host != "studio.youtube.com":
        return f"host la {host or '(trong)'}, khong phai studio.youtube.com ({page.url[:120]})"
    if URL_BAD_RE.search(page.url):
        return f"URL co dau hieu dang nhap/thu thach ({page.url[:120]})"
    try:
        if page.locator("input[type=password]").count() > 0:
            return "trang co o nhap mat khau"
        txt = unicodedata.normalize("NFC", page.inner_text("body"))
        m = TEXT_BAD_RE.search(txt)
        if m:
            return f"noi dung trang co '{m.group(0)}'"
    except Exception as e:
        return f"{UNREADABLE}: {type(e).__name__}"  # fail closed
    return None


def check_page(page):
    """Blocked neu gap dang nhap/thu thach. Trang tam thoi khong doc duoc (dang
    chuyen trang) thi cho REPOLL_MS va doc lai mot lan; van khong doc duoc ->
    NavError (loi doc cua video nay, khong phai loi dang nhap)."""
    why = blocked_reason(page)
    if why and why.startswith(UNREADABLE):
        page.wait_for_timeout(REPOLL_MS)
        why = blocked_reason(page)
        if why and why.startswith(UNREADABLE):
            raise NavError(why)
    if why:
        raise Blocked(why)


def scroll_position(page):
    """Cuon mot buoc xuong va tra ve vi tri hien tai (so), hoac None neu khong doc duoc.
    Studio cuon mot phan tu ben trong, khong phai window: thu scrollingElement truoc,
    roi phan tu dau tien co overflow-y auto|scroll va scrollHeight > clientHeight."""
    try:
        pos = page.evaluate(SCROLL_JS % SCROLL_STEP_PX)
    except Exception:
        return None
    return pos if isinstance(pos, (int, float)) else None


def scroll_to_bottom(page, max_steps=SCROLL_MAX_STEPS, step_wait_ms=SCROLL_STEP_MS,
                     cap_s=SCROLL_CAP_S - SCROLL_IDLE_TIMEOUT_MS // 1000, clock=time.monotonic):
    """Cuon tung buoc de the tai cham (retention, traffic) hien ra. Dung khi vi tri
    khong doi, sau max_steps buoc, hoac qua cap_s giay. Chi cuon, khong bam, khong go.
    Tra ve so buoc da cuon."""
    start = clock()
    last = None
    steps = 0
    method = "element"
    while steps < max_steps and clock() - start < cap_s:
        if method == "element":
            pos = scroll_position(page)
            steps += 1
            if steps == 1 and not pos:
                # khong tim thay phan tu cuon (vd nam trong shadow root dong): doi sang lan chuot
                method = "wheel"
                if not _wheel_step(page):
                    method = "none"
                    page.wait_for_timeout(step_wait_ms)
                    break
                page.wait_for_timeout(step_wait_ms)
                continue
            page.wait_for_timeout(step_wait_ms)
            if pos is None or pos == last:
                break
            last = pos
        else:
            # lan chuot khong doc duoc vi tri: chay so buoc con lai nhung khong qua WHEEL_MAX_STEPS
            if steps >= WHEEL_MAX_STEPS or not _wheel_step(page):
                break
            steps += 1
            page.wait_for_timeout(step_wait_ms)
    log.info("scroll: %d buoc, cach=%s, vi tri cuoi=%s", steps, method,
             last if method == "element" else "khong biet")
    return steps


def _wheel_step(page):
    """Di chuot vao giua khung nhin roi lan chuot mot buoc. Chi doc: khong bam."""
    try:
        vp = page.viewport_size or {"width": 1280, "height": 720}
        page.mouse.move(vp["width"] / 2, vp["height"] / 2)
        page.mouse.wheel(0, SCROLL_STEP_PX)
        return True
    except Exception:
        return False


def scroll_phase(page, clock=time.monotonic):
    """Cuon het tab, doi mang yen, roi kiem tra lai dang nhap/thu thach.
    Ca hai nam trong ngan sach SCROLL_CAP_S (cuon + toi da 10 s cho mang yen)."""
    start = clock()
    scroll_to_bottom(page, clock=clock)
    left_ms = int((SCROLL_CAP_S - (clock() - start)) * 1000)
    idle_ms = min(SCROLL_IDLE_TIMEOUT_MS, left_ms)
    if idle_ms > 0:
        try:
            page.wait_for_load_state("networkidle", timeout=idle_ms)
        except Exception:
            pass
    check_page(page)


def _open_tab(page, video_id, tab, settle_ms):
    _open_url(page, tab_url(video_id, tab), settle_ms)


def channel_tab_urls(channel_id):
    """{khoa: URL} cua cac tab phan tich KENH: 4 tab hub_* va 3 luong video cua tab Nghien cuu."""
    import studio_channel as sch
    base = f"https://studio.youtube.com/channel/{channel_id}/analytics/"
    urls = {tab: base + tab for tab in sch.CHANNEL_TABS}
    for key, feed in sch.RESEARCH_FEEDS.items():
        f = json.dumps({"contentTypeFilter": "CONTENT_TYPE_FILTER_VIDEOS", "landingPageFeedType": feed},
                       separators=(",", ":"))
        urls[key] = base + "hub_research?filter=" + quote(f, safe="")
    return urls


def capture_channel(page, channel_id, settle_ms=SETTLE_MS):
    """Mo tung tab phan tich kenh, cuon, tra ve ({khoa: [body JSON]}, {khoa: loi}).
    Tab loi thi ghi vao dict loi va di tiep; Blocked (dang nhap/thu thach) thi dung ngay."""
    out, errors = {}, {}
    for key, url in channel_tab_urls(channel_id).items():
        bodies = []

        def on_response(resp, bodies=bodies):
            try:
                if resp.status != 200 or not wanted(resp.url):
                    return
                if "json" not in (resp.headers.get("content-type") or "").lower():
                    return
                bodies.append(scrub(json.loads(resp.body().decode("utf-8"))))
            except Exception:
                return

        page.on("response", on_response)
        try:
            _open_url(page, url, settle_ms)
        except Blocked:
            raise
        except Exception as e:
            errors[key] = f"{type(e).__name__}: {e}"[:300]
            log.warning("tab kenh %s loi: %s", key, errors[key])
        finally:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass
        out[key] = bodies
    return out, errors


def _open_url(page, url, settle_ms):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
    except Exception as e:
        raise NavError(f"khong mo duoc {url}: {type(e).__name__}: {e}") from e
    try:
        page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_MS)
    except Exception:
        pass
    check_page(page)
    page.wait_for_timeout(settle_ms)
    check_page(page)  # chuyen huong muon sau khi cho
    scroll_phase(page)


def capture_tabs(page, video_id, settle_ms=SETTLE_MS):
    """Mo cac tab Reach, Engagement (bat buoc) va Audience (tuy chon) cua video, cuon
    tung tab de the tai cham hien ra, tra ve cac body JSON da scrub. Tab tuy chon loi
    thi ghi log va bo qua (Blocked van dung ca lan chay)."""
    bodies = []

    def on_response(resp):
        try:
            if resp.status != 200 or not wanted(resp.url):
                return
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype:
                return
            bodies.append(scrub(json.loads(resp.body().decode("utf-8"))))
        except Exception:
            return  # khong phai JSON, bo qua

    page.on("response", on_response)
    try:
        for tab in TABS:
            if tab in OPTIONAL_TABS:
                try:
                    _open_tab(page, video_id, tab, settle_ms)
                except Blocked:
                    raise
                except Exception as e:
                    log.warning("tab tuy chon %s loi, bo qua: %s: %s", tab, type(e).__name__, e)
            else:
                _open_tab(page, video_id, tab, settle_ms)
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass
    return bodies


@contextmanager
def open_browser(profile_dir, headless=False):
    """Chrome that (channel="chrome") tren profile ben vung, co cua so. Tra ve page."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile_dir), channel="chrome", headless=headless)
        try:
            yield ctx.pages[0] if ctx.pages else ctx.new_page()
        finally:
            try:
                ctx.close()
            except Exception:
                pass
