# scraper.py
# Fetches website pages and returns HTML + status metadata.
# Uses requests + BeautifulSoup (static HTML only).
# JS-rendered pages are detected and flagged rather than crashed on.

import re
import time
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, field

import config
from cleaner import domain_to_base_url, clean_website


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PageResult:
    url: str = ""
    page_path: str = ""         # e.g. "/about", "/team", "/" for homepage
    html: str = ""
    text: str = ""              # Visible text stripped of tags
    status_code: int = 0
    website_status: str = ""
    error: str = ""
    js_rendered: bool = False   # V5: True when Playwright was used to render this page
    jina_used: bool = False     # V8: True when Jina.ai Reader was used to render this page


@dataclass
class ScrapeResult:
    pages: list = field(default_factory=list)   # List[PageResult] — all successfully fetched pages
    best_page: "PageResult | None" = None       # Kept for backward compatibility
    website_status: str = ""
    notes: str = ""
    playwright_used: bool = False  # V5: True if Playwright rendered at least one page
    jina_used: bool = False        # V8: True if Jina.ai Reader rendered at least one page


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    return session


def _extract_visible_text(html: str) -> str:
    """Strip HTML tags and return clean visible text."""
    try:
        soup = BeautifulSoup(html, "lxml")
        # Remove scripts and styles from text extraction
        for tag in soup(["script", "style", "noscript", "meta", "head"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception:
        return ""


def _looks_js_rendered(text: str) -> bool:
    """Return True if the page text is too short to contain real content."""
    return len(text) < config.JS_RENDER_TEXT_THRESHOLD


def _render_with_playwright(url: str, page_path: str = "") -> PageResult:
    """
    Fetch a URL using a real Chromium browser via Playwright.
    Used when static HTTP returns near-empty text (JS-rendered site).

    Requires one-time setup:
        pip3 install playwright
        playwright install chromium

    If Playwright is not installed or fails, returns an empty PageResult so the
    caller can fall back to the original static HTML result silently.
    """
    result = PageResult(url=url, page_path=page_path)
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=config.USER_AGENT)
            page.goto(
                url,
                timeout=config.REQUEST_TIMEOUT_SECONDS * 1000,
                wait_until="networkidle",
            )
            # Extra wait for late JS rendering (React, Vue etc.)
            page.wait_for_timeout(config.PLAYWRIGHT_WAIT_MS)
            html = page.content()
            browser.close()

        result.html = html
        result.text = _extract_visible_text(html)
        result.status_code = 200
        result.js_rendered = True

        if _looks_js_rendered(result.text):
            # Playwright fetched the page but JS still produced too little text
            # (login wall, captcha, heavy SPA that needs more time)
            result.website_status = "js_rendered_blocked"
        else:
            result.website_status = "ok"

        return result

    except Exception as e:
        # Playwright not installed, browser crash, timeout, etc. — fail silently
        result.website_status = "js_rendered_likely"
        result.error = f"playwright_error: {e}"
        return result


def _fetch_with_jina(url: str, page_path: str = "") -> PageResult:
    """
    Fetch a URL via Jina.ai Reader API (https://r.jina.ai/<url>).

    Jina Reader strips ads, nav, and boilerplate and returns clean readable text
    from any URL — including React/Vue/Wix/Squarespace JS-rendered sites.
    It is FREE (no API key required) and typically 3–5× faster than Playwright.

    V8: Used automatically when static scrape returns sparse text and Playwright
    is not installed or not enabled. Falls back silently on any error.

    Returns a PageResult with js_rendered=True and jina_used=True on success,
    or an empty result with website_status="error" if Jina fails.
    """
    result = PageResult(url=url, page_path=page_path)
    try:
        jina_url = f"https://r.jina.ai/{url}"
        resp = requests.get(
            jina_url,
            headers={
                "User-Agent": config.USER_AGENT,
                "Accept": "text/plain, text/markdown, */*",
                "X-Return-Format": "text",
            },
            timeout=config.JINA_REQUEST_TIMEOUT,
        )

        if resp.status_code != 200 or not resp.text.strip():
            result.website_status = "error"
            result.error = f"jina_http_{resp.status_code}"
            return result

        text = resp.text.strip()
        result.html       = text   # Jina returns clean text/markdown — use as both fields
        result.text       = text
        result.status_code = 200
        result.js_rendered = True
        result.jina_used   = True

        if _looks_js_rendered(text):
            # Jina returned something but it's still sparse (login wall, captcha, etc.)
            result.website_status = "js_rendered_likely"
        else:
            result.website_status = "ok"

        return result

    except Exception as e:
        result.website_status = "error"
        result.error = f"jina_error: {e}"
        return result


def _fetch_url(session: requests.Session, url: str, page_path: str = "") -> PageResult:
    """
    Fetch a single URL and return a PageResult.
    Retries up to MAX_RETRIES times on connection/timeout errors.
    page_path is stored on the result for use by the scoring system.
    """
    result = PageResult(url=url, page_path=page_path)
    attempts = 0

    while attempts <= config.MAX_RETRIES:
        try:
            resp = session.get(
                url,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
                allow_redirects=True,
            )
            result.status_code = resp.status_code

            if resp.status_code == 200:
                result.html = resp.text
                result.text = _extract_visible_text(resp.text)
                if _looks_js_rendered(result.text):
                    # V8: Try Jina.ai Reader first — free, fast, no setup required
                    if config.USE_JINA_READER:
                        jina_result = _fetch_with_jina(url, page_path)
                        if jina_result.website_status == "ok":
                            return jina_result
                    # V5: Fall back to Playwright if Jina failed or is disabled
                    if config.USE_PLAYWRIGHT:
                        pw_result = _render_with_playwright(url, page_path)
                        if pw_result.website_status == "ok":
                            return pw_result
                    result.website_status = "js_rendered_likely"
                else:
                    result.website_status = "ok"
            elif resp.status_code in (403, 429, 503):
                result.website_status = "blocked"
                result.error = f"HTTP {resp.status_code}"
            elif resp.status_code == 404:
                result.website_status = "not_found"
                result.error = "404"
            else:
                result.website_status = "error"
                result.error = f"HTTP {resp.status_code}"

            return result

        except requests.exceptions.Timeout:
            attempts += 1
            result.error = "timeout"
            if attempts > config.MAX_RETRIES:
                result.website_status = "timeout"
                return result
            time.sleep(1)

        except requests.exceptions.SSLError:
            # Try http:// fallback once
            if url.startswith("https://"):
                http_url = "http://" + url[8:]
                result.url = http_url
                url = http_url
                attempts += 1
                continue
            result.website_status = "ssl_error"
            result.error = "ssl_error"
            return result

        except requests.exceptions.ConnectionError as e:
            attempts += 1
            result.error = f"connection_error: {e}"
            if attempts > config.MAX_RETRIES:
                result.website_status = "connection_error"
                return result
            time.sleep(1)

        except Exception as e:
            result.website_status = "error"
            result.error = str(e)
            return result

    result.website_status = "error"
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_company(domain: str = "", website: str = "") -> ScrapeResult:
    """
    Given a company's domain and/or website URL, fetch the most promising
    pages (about, team, contact, etc.) and return a ScrapeResult containing
    all successfully fetched pages and the overall status.

    All pages are returned in result.pages so the extractor can scan them all
    and track which page each candidate was found on.

    result.best_page is also set for backward compatibility.
    """
    result = ScrapeResult()

    # Build base URL
    if website:
        base_url = clean_website(website).rstrip("/")
    elif domain:
        base_url = domain_to_base_url(domain)
    else:
        result.website_status = "missing_domain"
        result.notes = "No domain or website provided"
        return result

    session = _make_session()

    # Fetch candidate sub-pages
    candidate_pages: list[PageResult] = []
    site_unreachable = False

    for path in config.PAGES_TO_TRY:
        url = base_url + path
        page = _fetch_url(session, url, page_path=path)
        if page.website_status == "ok" and page.text:
            candidate_pages.append(page)
        # If the site is actively blocking or unreachable, stop hammering it
        if page.website_status in ("blocked", "timeout", "connection_error"):
            result.website_status = page.website_status
            result.notes = f"Site unreachable: {page.error}"
            site_unreachable = True
            break

    # Always try the homepage regardless (often has useful footer/about text)
    home = _fetch_url(session, base_url, page_path="/")
    if home.website_status == "ok" and home.text:
        candidate_pages.append(home)
    elif not candidate_pages:
        # Nothing worked at all
        if home.website_status in ("blocked", "timeout", "connection_error", "ssl_error"):
            result.website_status = home.website_status
            result.notes = f"Homepage unreachable: {home.error}"
            result.best_page = home
            return result

    result.pages = candidate_pages
    result.playwright_used = any(p.js_rendered and not p.jina_used for p in candidate_pages)
    result.jina_used       = any(p.jina_used for p in candidate_pages)

    if not candidate_pages:
        result.website_status = "no_team_page"
        result.notes = "No accessible pages found"
        return result

    # Pick the best single page for backward compatibility:
    # prioritise pages with role keywords, break ties by text length.
    role_kw_lower = [r.lower() for r in config.ROLE_KEYWORDS]

    def page_score(p: PageResult) -> int:
        text_lower = p.text.lower()
        role_hits = sum(1 for kw in role_kw_lower if kw in text_lower)
        return role_hits * 10000 + len(p.text)

    best = max(candidate_pages, key=page_score)
    result.best_page = best

    # Use site_unreachable status if set; otherwise use the best page's status
    if not site_unreachable:
        result.website_status = best.website_status

    return result
