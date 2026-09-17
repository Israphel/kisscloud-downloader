"""Sniffs a myasiantv.es episode page for a supported video provider source.

KissCloud returns a stable video id/master.txt pair. Vidbasic returns its
already-decrypted JWPlayer HLS and subtitle URLs from the rendered player.
The kisscloud video is embedded in an iframe, and the actual HLS stream
tokens (/m3/...) rotate per session, so we only capture the stable
/cdn/hls/<hash>/master.txt form here and re-resolve fresh tokens from it at
download time (see streaming.resolve_streams).
"""
import re
from dataclasses import dataclass

from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

MASTER_TXT_RE = re.compile(r"https://kisscloud\.online/cdn/hls/[0-9a-f]+/master\.txt")
VIDEO_ID_RE = re.compile(r"https://kisscloud\.online/video/([0-9a-f]+)")
VIDBASIC_IFRAME_RE = re.compile(r"https?://vidbasic\.top/embed/[^\"']+")


@dataclass
class SniffedSource:
    """Provider-neutral media source returned by :func:`sniff_episode`."""

    provider: str
    referer: str
    video_id: str | None = None
    master_txt_url: str | None = None
    media_url: str | None = None
    subtitle_url: str | None = None
    subtitle_data: str | None = None

    def __iter__(self):
        """Preserve the old ``video_id, master_txt`` unpacking contract."""
        if self.provider != "kisscloud":
            raise TypeError("Only KissCloud sources support legacy tuple unpacking")
        yield self.video_id
        yield self.master_txt_url


def _sniff_vidbasic(page, page_url, timeout_ms):
    """Extract the already-decrypted JWPlayer source from Vidbasic.

    Vidbasic deliberately redirects its player to ``sandboxed.html`` when the
    PDF capability check fails in headless Chromium. The player itself can
    still initialize, so replace that diagnostic document with an empty 204
    response and read the resulting JWPlayer config. This avoids reimplementing
    Vidbasic's rotating AES/encryption scheme.
    """
    page.context.route(
        "**/sandboxed.html",
        lambda route: route.fulfill(status=204, body=""),
    )
    page.goto(page.url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(5000)

    for frame in page.frames:
        if "vidbasic.top/3rdplayer.html" not in frame.url:
            continue
        try:
            config = frame.evaluate(
                """() => {
                    if (typeof jwplayer === 'undefined') return null;
                    const player = jwplayer();
                    return player && player.getConfig ? player.getConfig() : null;
                }"""
            )
        except Exception:
            config = None
        if not config:
            continue
        media_url = config.get("file")
        tracks = config.get("tracks") or []
        subtitle_url = next(
            (track.get("file") for track in tracks if track.get("kind") == "captions"),
            None,
        )
        subtitle_data = None
        if subtitle_url:
            try:
                subtitle_data = frame.evaluate(
                    """async (url) => {
                        const encrypted = await fetch(url).then(response => response.text());
                        const key = CryptoJS.enc.Utf8.parse('9458829337' + '5053432799' + '2224455212' + '89');
                        const iv = CryptoJS.enc.Utf8.parse('5259228356' + '829423');
                        const lines = encrypted.split('\\n');
                        return lines.map(line => {
                            if (!/^[A-Za-z0-9+/]+={0,2}$/.test(line) || line.length < 20) return line;
                            try {
                                const decoded = CryptoJS.AES.decrypt(line, key, {iv}).toString(CryptoJS.enc.Utf8);
                                return decoded || line;
                            } catch (_) {
                                return line;
                            }
                        }).join('\\n');
                    }""",
                    subtitle_url,
                )
            except Exception:
                subtitle_data = None
        if media_url:
            return SniffedSource(
                provider="vidbasic",
                referer=frame.url,
                media_url=media_url,
                subtitle_url=subtitle_url,
                subtitle_data=subtitle_data,
            )
    raise RuntimeError(f"Could not extract Vidbasic JWPlayer source from {page_url}")


def sniff_episode(page_url, timeout_ms=30000):
    """Returns a provider-neutral :class:`SniffedSource` for an episode."""
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

        # Some movie pages keep analytics/advertising requests open forever,
        # so waiting for networkidle can time out before the player is useful.
        # The explicit wait below gives the player enough time to initialize
        # while keeping the existing network-request sniffing behavior.
        page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)
        iframe_urls = page.eval_on_selector_all("iframe", "els => els.map(e => e.src)")
        if any(VIDBASIC_IFRAME_RE.fullmatch(url) for url in iframe_urls):
            source = _sniff_vidbasic(page, page_url, timeout_ms)
            browser.close()
            return source
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

    return SniffedSource(
        provider="kisscloud",
        referer=f"https://kisscloud.online/video/{video_id}",
        video_id=video_id,
        master_txt_url=master_txt,
    )
