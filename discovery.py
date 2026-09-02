"""Discovers a title, year, and page URLs from myasiantv.es.

Series pages (the /tv/<slug>/ page) return their ordered episode URLs. Movie
pages (the /movies/<slug>/ page) return the movie page itself as a one-item
list, so callers can use the same discovery/download pipeline for both.
"""
import re
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from sniffer import USER_AGENT

TITLE_RE = re.compile(r"^(.*?)\s*\((\d{4})\)")
EPISODE_NUM_RE = re.compile(r"Episode\s*(\d+)", re.IGNORECASE)
# myasiantv appends an arbitrary short disambiguation suffix (-aa, -ab, -ac,
# ...) to URLs whenever a slug would otherwise collide with another show -
# independently on the show page AND on each individual episode page, so
# they don't correlate (one episode might be "-ac", another "-aa", another
# has no suffix at all). Match on the slug up through the year instead of
# the full trailing slug.
CORE_SLUG_RE = re.compile(r"^(.*-\d{4})(?:-[a-z]{1,3})?$")


def is_movie_url(url):
    """Whether *url* is a myasiantv movie page."""
    parts = [p.lower() for p in urlparse(url).path.split("/") if p]
    return bool(parts and parts[0] == "movies")


def show_slug(show_url):
    """The last non-empty path segment, e.g. 'love-beyond-dreams-2026'."""
    parts = [p for p in urlparse(show_url).path.split("/") if p]
    return parts[-1] if parts else ""


def core_slug(slug):
    """Strip a trailing disambiguation suffix, keeping through the year -
    e.g. 'petrichor-2024-ac' -> 'petrichor-2024', 'love-beyond-dreams-2026'
    unchanged (it never had a suffix to strip)."""
    m = CORE_SLUG_RE.match(slug)
    return m.group(1) if m else slug


def episode_url_path_slug(href):
    """The last non-empty path segment of an episode URL."""
    parts = [p for p in urlparse(href).path.split("/") if p]
    return parts[-1] if parts else ""


def slugify(name):
    """series name -> repo-style slug, e.g. 'Love beyond Dreams' -> 'love_beyond_dreams'."""
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s


def discover_series(show_url, timeout_ms=30000):
    """Returns (series_name, year, [episode_page_url, ...]) in ascending episode order.

    Accepts a show page (/tv/<slug>/), an individual episode page
    (/ep/<slug>/), or a movie page (/movies/<slug>/). A movie is returned as
    one URL (it is not treated as a series with a fictional episode 1).
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.goto(show_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(3000)

        title = page.title()
        m = TITLE_RE.match(title)
        if not m:
            browser.close()
            raise RuntimeError(f"Could not parse series name/year from page title: {title!r}")
        series_name, year = m.group(1).strip(), m.group(2)

        if is_movie_url(show_url):
            browser.close()
            return series_name, year, [show_url]

        if "/ep/" in urlparse(show_url).path:
            expected_text = f"{series_name} ({year})"
            tv_href = page.eval_on_selector_all(
                "a[href*='/tv/']",
                "(els, text) => { const m = els.find(e => e.textContent.trim() === text); return m ? m.href : null; }",
                expected_text,
            )
            if not tv_href:
                browser.close()
                raise RuntimeError(
                    f"Could not find show page link (expected link text {expected_text!r}) from episode page {show_url}"
                )
            show_url = tv_href
            page.goto(show_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)

        links = page.eval_on_selector_all(
            "a", "els => els.map(e => ({href: e.href, text: e.textContent}))"
        )
        browser.close()

    core = core_slug(show_slug(show_url))
    expected_prefix = f"{core}-episode-"
    episodes = {}
    for link in links:
        href = link["href"]
        text = link["text"] or ""
        if "/ep/" not in href or not episode_url_path_slug(href).startswith(expected_prefix):
            continue
        m = EPISODE_NUM_RE.search(text)
        if not m:
            continue
        ep_num = int(m.group(1))
        episodes.setdefault(ep_num, href)

    if not episodes:
        raise RuntimeError(f"No episode links found on {show_url} matching prefix {expected_prefix!r}")

    ordered_urls = [episodes[n] for n in sorted(episodes)]
    return series_name, year, ordered_urls
