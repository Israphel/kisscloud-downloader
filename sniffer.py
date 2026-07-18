"""Sniffs a myasiantv.es episode page for its kisscloud video_id and the
stable master.txt URL.

The kisscloud video is embedded in an iframe, and the actual HLS stream
tokens (/m3/...) rotate per session, so we only capture the stable
/cdn/hls/<hash>/master.txt form here and re-resolve fresh tokens from it at
download time (see streaming.resolve_streams).
"""
import re

from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

MASTER_TXT_RE = re.compile(r"https://kisscloud\.online/cdn/hls/[0-9a-f]+/master\.txt")
VIDEO_ID_RE = re.compile(r"https://kisscloud\.online/video/([0-9a-f]+)")


def sniff_episode(page_url, timeout_ms=30000):
    """Returns (video_id, master_txt_url) for a myasiantv episode page."""
    master_txt = None

    def on_request(request):
        nonlocal master_txt
        if master_txt is None and MASTER_TXT_RE.search(request.url):
            master_txt = MASTER_TXT_RE.search(request.url).group(0)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.on("request", on_request)

        page.goto(page_url, wait_until="networkidle", timeout=timeout_ms)
        try:
            page.click("button.play, .play-button-outer, video", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(8000)

        iframes = page.eval_on_selector_all("iframe", "els => els.map(e => e.src)")
        browser.close()

    video_id = None
    for src in iframes:
        m = VIDEO_ID_RE.search(src)
        if m:
            video_id = m.group(1)
            break

    if not video_id:
        raise RuntimeError(f"Could not find kisscloud video_id in iframes for {page_url}: {iframes}")
    if not master_txt:
        raise RuntimeError(f"Could not capture master.txt request for {page_url}")

    return video_id, master_txt
