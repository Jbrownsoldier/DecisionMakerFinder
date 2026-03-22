# searcher.py
# Free decision-maker discovery via Yelp business listings and DuckDuckGo SERP.
#
# search_yelp(): searches Yelp for the business, extracts owner name from the listing.
#   Best for: local service businesses (plumbers, roofers, electricians, restaurants).
#   Hit rate: ~35-50% for businesses with Yelp listings that show owner info.
#
# search_ddg(): searches DuckDuckGo HTML for the company + role keywords.
#   Best for: businesses mentioned in news, press releases, or local directories.
#   Hit rate: ~15-25% additional coverage on top of website scraping.
#
# Both functions:
#   - Never raise exceptions to the caller (fail gracefully, return empty result)
#   - Return a consistent dict structure (same keys regardless of outcome)
#   - Use polite delays and browser-like headers to reduce bot detection

import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

import config


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

# Matches a plausible Western proper name: 2-4 title-cased words
_NAME_RE = re.compile(
    r"\b([A-Z][a-zA-Z'\-]{1,20}(?:\s+[A-Z][a-zA-Z'\-]{1,20}){1,3})\b"
)

# Role keywords from config
_ROLE_RE = re.compile(
    r"\b(" + "|".join(re.escape(r) for r in config.ROLE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Common strings that look like proper names but are not people
_FALSE_POSITIVE_NAMES = {
    "united states", "new york", "los angeles", "san francisco", "las vegas",
    "new jersey", "new mexico", "north carolina", "south carolina",
    "the owner", "the founder", "the ceo", "the president",
    "home page", "about us", "contact us", "privacy policy",
    "terms of service", "all rights", "google maps", "yelp elite",
    "business owner", "managing director", "general manager",
    "read more", "learn more", "click here", "view all",
}

# Words that appear in company/business names but never in real person names.
# Mirrors the set in extractor.py — kept in sync manually.
_BUSINESS_WORDS = {
    # Trades & industry
    "plumbing", "plumber", "plumbers", "drain", "drains", "drainage",
    "pipe", "pipes", "pipelines", "sewer", "sewage",
    "roofing", "roofer", "roofers", "roof",
    "heating", "cooling", "hvac", "ventilation", "refrigeration",
    "electrical", "electrician", "electricians", "wiring",
    "renovation", "renovations", "remodeling", "remodelling",
    "construction", "contractor", "contractors", "contracting",
    "landscaping", "landscaper", "painting", "flooring",
    "cleaning", "restoration", "waterproofing", "insulation",
    "mechanical", "maintenance", "handyman", "technician",
    # Business suffixes / entity types
    "inc", "ltd", "llc", "corp", "corporation",
    "company", "co", "group", "enterprises", "enterprise",
    "solutions", "solution", "systems", "system",
    "industries", "industry", "partners", "associates",
    "holdings", "ventures", "management", "agency",
    # Common business descriptor words
    "services", "service", "repair", "repairs",
    "professional", "professionals", "quality", "premier", "priority",
    "advanced", "pro", "master", "masters", "elite", "expert", "experts",
    "reliable", "trusted", "certified", "licensed",
    # Boilerplate / copyright / navigation words
    "all", "rights", "reserved", "about", "contact", "home",
    "privacy", "policy", "terms", "sitemap",
    "the", "your", "our", "new",
    # Prepositions / conjunctions that appear in company names
    "by", "and", "or", "for",
    # Descriptive words common in SMB names
    "local", "fast", "quick", "emergency", "budget", "affordable",
    "door", "job", "jobs",
    # Canadian geography
    "toronto", "ontario", "canada", "canadian",
    "york", "etobicoke", "scarborough", "mississauga",
    "brampton", "markham", "vaughan", "oakville", "burlington",
    "ottawa", "montreal", "calgary", "edmonton", "vancouver",
    # US geography that commonly appears in company names
    "america", "american", "national", "united",
}


def _fetch_via_jina(url: str, timeout: int = None) -> str:
    """
    Fetch a URL via Jina.ai Reader (r.jina.ai) and return plain text.
    Jina renders JS-heavy pages using a headless browser, which is needed
    for LinkedIn, Google Maps, and Facebook pages.
    Returns '' on any failure.
    """
    try:
        resp = requests.get(
            f"https://r.jina.ai/{url}",
            headers={**_HEADERS, "Accept": "text/plain"},
            timeout=timeout or config.JINA_REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.text[:10000]
    except Exception:
        pass
    return ""


def _is_plausible_person_name(name: str) -> bool:
    """
    Return True only if the string looks like a real person's name.

    Rejects:
    - Fewer than 2 words
    - Known full-phrase false positives ("about us", "all rights", etc.)
    - Any word that is a known business / industry / boilerplate term
    - Strings containing digits  (e.g. "Suite 200")
    - Strings longer than 50 characters
    """
    if not name or len(name.split()) < 2:
        return False
    if name.lower() in _FALSE_POSITIVE_NAMES:
        return False
    # Reject strings containing digits
    if re.search(r"\d", name):
        return False
    # Reject very long strings
    if len(name) > 50:
        return False
    # Reject if ANY word matches a known business/boilerplate term
    words = [w.rstrip(".'s").lower() for w in name.split()]
    if any(w in _BUSINESS_WORDS for w in words):
        return False
    return True


# ---------------------------------------------------------------------------
# Empty result factories
# ---------------------------------------------------------------------------

def _empty_yelp() -> dict:
    return {
        "yelp_owner_found": "no",
        "yelp_owner_name": "",
        "yelp_owner_title": "",
        "yelp_source_url": "",
        "yelp_snippet": "",
    }


def _empty_ddg() -> dict:
    return {
        "serp_person_found": "no",
        "serp_decision_maker": "",
        "serp_title": "",
        "serp_snippet": "",
        "serp_source_url": "",
    }


# ---------------------------------------------------------------------------
# Yelp search
# ---------------------------------------------------------------------------

def search_yelp(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Search Yelp for a business and attempt to extract the owner's name.

    Strategy:
      1. Search yelp.com for company + location
      2. Follow the first business /biz/ link
      3. Scan the business page text near "business owner", "meet the owner",
         "founded by", etc. for a proper name

    Args:
        company_name: The business name to search for
        city: Optional city name to narrow the search
        state: Optional state to narrow the search

    Returns dict with keys:
        yelp_owner_found  (yes | no)
        yelp_owner_name   (person name string)
        yelp_owner_title  ("Business Owner" when found from context)
        yelp_source_url   (URL of the Yelp business listing)
        yelp_snippet      (surrounding text context where name was found)

    Fails gracefully — never raises an exception.
    """
    if not company_name:
        return _empty_yelp()

    try:
        # --- Step 1: Search Yelp ---
        location = " ".join(filter(None, [city, state])).strip() or "United States"

        search_resp = requests.get(
            "https://www.yelp.com/search",
            params={"find_desc": company_name, "find_loc": location},
            headers=_HEADERS,
            timeout=config.YELP_REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        if search_resp.status_code != 200:
            return _empty_yelp()

        search_soup = BeautifulSoup(search_resp.text, "lxml")

        # Find the first /biz/... link in the results (without query params)
        biz_url = None
        for a in search_soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/biz/") and "?" not in href:
                biz_url = "https://www.yelp.com" + href
                break

        if not biz_url:
            return _empty_yelp()

        # --- Step 2: Visit the business page ---
        time.sleep(0.5)  # polite delay between requests

        biz_resp = requests.get(
            biz_url,
            headers=_HEADERS,
            timeout=config.YELP_REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        if biz_resp.status_code != 200:
            return _empty_yelp()

        biz_text = BeautifulSoup(biz_resp.text, "lxml").get_text(" ", strip=True)

        # --- Step 3: Find owner name near ownership context keywords ---
        # These phrases typically appear just before/after the owner's name on Yelp
        context_phrases = [
            "business owner",
            "meet the business owner",
            "meet the owner",
            "about the owner",
            "owner of",
            "founded by",
            "proprietor",
        ]

        owner_name = ""
        snippet = ""

        for phrase in context_phrases:
            idx = biz_text.lower().find(phrase)
            if idx == -1:
                continue

            # Expand a window of text around the keyword
            window_start = max(0, idx - 50)
            window_end = min(len(biz_text), idx + 300)
            window = biz_text[window_start:window_end]

            # Search for a proper name in the window
            for m in _NAME_RE.finditer(window):
                candidate = m.group(0)
                if _is_plausible_person_name(candidate):
                    owner_name = candidate
                    snippet = window[:200].strip()
                    break

            if owner_name:
                break

        if not owner_name:
            return _empty_yelp()

        return {
            "yelp_owner_found": "yes",
            "yelp_owner_name": owner_name,
            "yelp_owner_title": "Business Owner",  # inferred from context
            "yelp_source_url": biz_url,
            "yelp_snippet": snippet,
        }

    except Exception:
        # Yelp may block, change HTML structure, or timeout — always fail safely
        return _empty_yelp()


# ---------------------------------------------------------------------------
# DuckDuckGo SERP search
# ---------------------------------------------------------------------------

def search_ddg(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Search DuckDuckGo's HTML interface for the company's decision maker.

    DuckDuckGo's html.duckduckgo.com endpoint returns static HTML (not
    JavaScript-rendered), making it reliably parseable. It's less aggressive
    with rate-limiting than Google.

    Strategy:
      1. Build a targeted query: "Company Name" city (owner OR CEO OR founder ...)
      2. Parse the first DDG_MAX_RESULTS result snippets
      3. Find the first snippet that contains a role keyword + a proper name
      4. Return that name as the decision maker

    Args:
        company_name: The business name to search for
        city: Optional city to narrow the search
        state: Optional state to narrow the search

    Returns dict with keys:
        serp_person_found    (yes | no)
        serp_decision_maker  (person name string)
        serp_title           (role keyword found in snippet, title-cased)
        serp_snippet         (raw snippet text up to 200 chars)
        serp_source_url      (URL of the result where the name was found)

    Fails gracefully — never raises an exception.
    """
    if not company_name:
        return _empty_ddg()

    try:
        # Build a targeted query
        location_part = f' "{city}"' if city else ""
        query = (
            f'"{company_name}"{location_part} '
            f"(owner OR CEO OR founder OR president OR "
            f'"managing director" OR proprietor)'
        )

        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                **_HEADERS,
                "Referer": "https://duckduckgo.com/",
            },
            timeout=config.DDG_REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return _empty_ddg()

        soup = BeautifulSoup(resp.text, "lxml")

        # DuckDuckGo HTML result structure:
        # <div class="web-result">
        #   <div class="result__body">
        #     <h2 class="result__title">...</h2>
        #     <div class="result__snippet">snippet text</div>
        #   </div>
        # </div>
        result_divs = soup.find_all("div", class_=re.compile(r"result", re.I))

        checked = 0
        for result in result_divs:
            if checked >= config.DDG_MAX_RESULTS:
                break

            # Try to get the snippet text — class name contains "snippet"
            snippet_el = result.find(class_=re.compile(r"snippet", re.I))
            if not snippet_el:
                # Fallback: look for any paragraph-like text inside the result
                snippet_el = result.find("td")
            if not snippet_el:
                continue

            snippet_text = snippet_el.get_text(" ", strip=True)
            if len(snippet_text) < 20:
                continue

            checked += 1

            # Only process snippets that contain a leadership role keyword
            if not _ROLE_RE.search(snippet_text):
                continue

            # Search for a proper person name in the snippet
            for m in _NAME_RE.finditer(snippet_text):
                candidate = m.group(0)
                if not _is_plausible_person_name(candidate):
                    continue

                # Extract the role keyword for the title field
                role_m = _ROLE_RE.search(snippet_text)
                role = role_m.group(0).title() if role_m else ""

                # Get the source URL from the result's link
                link_el = result.find("a", href=True)
                source_url = link_el["href"] if link_el else ""
                # DDG sometimes prepends their redirect URL — strip it if needed
                if source_url.startswith("//duckduckgo.com/l/?uddg="):
                    from urllib.parse import unquote, urlparse, parse_qs
                    try:
                        parsed = parse_qs(urlparse(source_url).query)
                        source_url = unquote(parsed.get("uddg", [""])[0])
                    except Exception:
                        pass

                return {
                    "serp_person_found": "yes",
                    "serp_decision_maker": candidate,
                    "serp_title": role,
                    "serp_snippet": snippet_text[:200],
                    "serp_source_url": source_url,
                }

        return _empty_ddg()

    except Exception:
        # DDG may change structure, timeout, or rate-limit — always fail safely
        return _empty_ddg()


# ---------------------------------------------------------------------------
# BBB (Better Business Bureau) business principal search  [V5]
# ---------------------------------------------------------------------------

def _empty_bbb() -> dict:
    return {
        "bbb_owner_found": "no",
        "bbb_owner_name": "",
        "bbb_source_url": "",
    }


def search_bbb(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Search the Better Business Bureau for a verified business principal/owner name.

    The BBB collects and verifies "Principal" contact names during business
    accreditation. This data is publicly visible on BBB listing pages and is
    one of the most reliable free sources of decision-maker names for US local
    businesses.

    Strategy:
      1. Search bbb.org for the company + location
      2. Follow the first business listing link
      3. Look for "Principal:" or "Contact:" followed by a proper name

    Args:
        company_name: The business name to search for
        city: Optional city to narrow the search
        state: Optional state to narrow the search

    Returns dict with keys:
        bbb_owner_found  (yes | no)
        bbb_owner_name   (verified principal name from BBB)
        bbb_source_url   (URL of the BBB business listing)

    Fails gracefully — never raises an exception.
    """
    if not company_name:
        return _empty_bbb()

    try:
        params = {
            "find_country": "USA",
            "find_text": company_name,
        }
        location = " ".join(filter(None, [city, state])).strip()
        if location:
            params["find_loc"] = location

        search_resp = requests.get(
            "https://www.bbb.org/search",
            params=params,
            headers=_HEADERS,
            timeout=config.BBB_REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        if search_resp.status_code != 200:
            return _empty_bbb()

        search_soup = BeautifulSoup(search_resp.text, "lxml")

        # Find first BBB business listing link
        biz_url = None
        for a in search_soup.find_all("a", href=True):
            href = a["href"]
            if "/profile/" in href or "/us/" in href:
                if href.startswith("/"):
                    biz_url = "https://www.bbb.org" + href
                elif href.startswith("https://www.bbb.org"):
                    biz_url = href
                if biz_url:
                    break

        if not biz_url:
            return _empty_bbb()

        time.sleep(0.5)

        biz_resp = requests.get(
            biz_url,
            headers=_HEADERS,
            timeout=config.BBB_REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        if biz_resp.status_code != 200:
            return _empty_bbb()

        biz_text = BeautifulSoup(biz_resp.text, "lxml").get_text(" ", strip=True)

        # Look for "Principal:" or similar labels followed by a proper name
        principal_patterns = [
            r"Principal[s]?\s*[:\-]\s*([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){1,3})",
            r"Contact[s]?\s*[:\-]\s*([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){1,3})",
            r"Owner[:\-]\s*([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){1,3})",
        ]

        for pat in principal_patterns:
            m = re.search(pat, biz_text)
            if m:
                candidate = m.group(1).strip()
                if _is_plausible_person_name(candidate):
                    return {
                        "bbb_owner_found": "yes",
                        "bbb_owner_name": candidate,
                        "bbb_source_url": biz_url,
                    }

        return _empty_bbb()

    except Exception:
        return _empty_bbb()


# ---------------------------------------------------------------------------
# DuckDuckGo domain email discovery  [V5]
# ---------------------------------------------------------------------------

def _empty_web_email() -> dict:
    return {
        "web_email_found": "no",
        "web_email_examples": "",
        "web_inferred_pattern": "",
    }


# Compiled inside the function to allow domain substitution
_GENERIC_PREFIXES = None  # will reference config.GENERIC_EMAIL_PREFIXES at call time


def search_domain_emails_web(domain: str) -> dict:
    """
    Search DuckDuckGo for real email addresses at `domain` mentioned on
    THIRD-PARTY sites (not the company's own site).

    Query: "@domain.com" -site:domain.com

    This is the same core technique Hunter.io uses — indexing publicly
    mentioned emails across the web. We do it in real-time via DDG.

    Returns dict with keys:
        web_email_found       (yes | no)
        web_email_examples    (comma-separated real emails found, up to 3)
        web_inferred_pattern  (email pattern inferred from found emails)

    Also returns the raw list of found emails via the special key
    "_raw_emails" (used internally by main.py to feed detect_email_pattern).

    Fails gracefully — never raises an exception.
    """
    if not domain:
        return _empty_web_email()

    email_at_domain_re = re.compile(
        r"\b([a-zA-Z0-9._%+\-]+)@" + re.escape(domain.lower()) + r"\b",
        re.IGNORECASE,
    )

    try:
        query = f'"@{domain}" -site:{domain}'

        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={**_HEADERS, "Referer": "https://duckduckgo.com/"},
            timeout=config.DDG_REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return _empty_web_email()

        page_text = BeautifulSoup(resp.text, "lxml").get_text(" ", strip=True)

        found_emails: list[str] = []
        generic = config.GENERIC_EMAIL_PREFIXES

        for m in email_at_domain_re.finditer(page_text):
            local = m.group(1).lower()
            if local in generic:
                continue
            if any(x in local for x in ("noreply", "no-reply", "webmaster", "bounce")):
                continue
            email = f"{local}@{domain}"
            if email not in found_emails:
                found_emails.append(email)
            if len(found_emails) >= 5:
                break

        if not found_emails:
            return _empty_web_email()

        # Infer pattern from found emails
        from email_gen import _classify_email_pattern  # local import avoids circular dep
        pattern_votes: dict[str, int] = {}
        for email in found_emails:
            local = email.split("@")[0]
            pat = _classify_email_pattern(local)
            if pat != "unknown":
                pattern_votes[pat] = pattern_votes.get(pat, 0) + 1

        best_pattern = max(pattern_votes, key=lambda k: pattern_votes[k]) if pattern_votes else ""

        result = {
            "web_email_found": "yes",
            "web_email_examples": ", ".join(found_emails[:3]),
            "web_inferred_pattern": best_pattern,
            "_raw_emails": found_emails,  # internal use: passed to detect_email_pattern()
        }
        return result

    except Exception:
        return _empty_web_email()


# ---------------------------------------------------------------------------
# GitHub code search for domain email addresses  [V5]
# ---------------------------------------------------------------------------

def search_github_emails(domain: str) -> list:
    """
    Search GitHub code for email addresses at `domain` committed to public repos.

    Many developers commit code using their work email. GitHub's public code
    search for "@domain.com" finds these in README files, package.json, git
    config, etc.

    Best for: tech companies, agencies, SaaS companies, software firms.
    Not useful for: local tradespeople, restaurants, brick-and-mortar retail.

    Args:
        domain: The company domain to search for (e.g. "acme.com")

    Returns:
        list of email strings found (e.g. ["john@acme.com", "j.smith@acme.com"])
        Empty list if nothing found or on any error.

    Fails gracefully — never raises an exception.
    """
    if not domain:
        return []

    email_at_domain_re = re.compile(
        r"\b([a-zA-Z0-9._%+\-]+)@" + re.escape(domain.lower()) + r"\b",
        re.IGNORECASE,
    )

    try:
        resp = requests.get(
            "https://github.com/search",
            params={"q": f'"@{domain}"', "type": "code"},
            headers={
                **_HEADERS,
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=config.GITHUB_REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        if resp.status_code != 200:
            return []

        page_text = BeautifulSoup(resp.text, "lxml").get_text(" ", strip=True)
        generic = config.GENERIC_EMAIL_PREFIXES
        found: list[str] = []

        for m in email_at_domain_re.finditer(page_text):
            local = m.group(1).lower()
            if local in generic:
                continue
            if any(x in local for x in ("noreply", "no-reply", "webmaster", "bounce")):
                continue
            email = f"{local}@{domain}"
            if email not in found:
                found.append(email)
            if len(found) >= 5:
                break

        return found

    except Exception:
        return []


# ---------------------------------------------------------------------------
# HomeStars.ca — Canadian home services directory  [V6]
# ---------------------------------------------------------------------------

def _empty_homestars() -> dict:
    return {
        "homestars_owner_found": "no",
        "homestars_owner_name": "",
        "homestars_source_url": "",
    }


def search_homestars(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Search HomeStars.ca for a Canadian business owner name.

    HomeStars is Canada's #1 home services directory — plumbers, roofers,
    electricians, and other trades list there, often with owner info visible
    in their profile's "About the Business" or "Meet the Owner" sections.

    Strategy:
      1. Search DDG for the company on homestars.com (avoids HomeStars bot detection)
      2. Follow the first homestars.com/companies/ link
      3. Scan the listing page for owner name near ownership context keywords

    Args:
        company_name: Business name to search for
        city:         City to narrow the search
        state:        Province/state to narrow the search

    Returns dict with keys: homestars_owner_found, homestars_owner_name, homestars_source_url
    Fails gracefully — never raises an exception.
    """
    if not company_name:
        return _empty_homestars()

    try:
        # Use DDG to find the right HomeStars listing URL — avoids HomeStars bot blocks
        location_part = f' "{city}"' if city else ""
        query = f'site:homestars.com/companies "{company_name}"{location_part}'

        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={**_HEADERS, "Referer": "https://duckduckgo.com/"},
            timeout=config.DDG_REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return _empty_homestars()

        soup = BeautifulSoup(resp.text, "lxml")

        # Find the first homestars.com/companies/ link in DDG results
        biz_url = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "homestars.com/companies/" in href:
                # Strip DDG redirect wrapper if present
                if "uddg=" in href:
                    from urllib.parse import unquote, urlparse, parse_qs
                    try:
                        parsed = parse_qs(urlparse(href).query)
                        href = unquote(parsed.get("uddg", [""])[0])
                    except Exception:
                        pass
                if href.startswith("https://homestars.com/companies/"):
                    biz_url = href
                    break

        if not biz_url:
            return _empty_homestars()

        time.sleep(0.5)

        biz_resp = requests.get(
            biz_url,
            headers=_HEADERS,
            timeout=config.HOMESTARS_REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        if biz_resp.status_code != 200:
            return _empty_homestars()

        biz_text = BeautifulSoup(biz_resp.text, "lxml").get_text(" ", strip=True)

        # Scan for owner name near ownership context keywords
        context_phrases = [
            "meet the owner",
            "about the owner",
            "business owner",
            "owner of",
            "founded by",
            "owned by",
            "started by",
            "proprietor",
        ]

        owner_name = ""
        for phrase in context_phrases:
            idx = biz_text.lower().find(phrase)
            if idx == -1:
                continue
            window_start = max(0, idx - 30)
            window_end = min(len(biz_text), idx + 250)
            window = biz_text[window_start:window_end]
            for m in _NAME_RE.finditer(window):
                candidate = m.group(0)
                if _is_plausible_person_name(candidate):
                    owner_name = candidate
                    break
            if owner_name:
                break

        if not owner_name:
            return _empty_homestars()

        return {
            "homestars_owner_found": "yes",
            "homestars_owner_name": owner_name,
            "homestars_source_url": biz_url,
        }

    except Exception:
        return _empty_homestars()


# ---------------------------------------------------------------------------
# YellowPages Canada (yellowpages.ca)  [V6]
# ---------------------------------------------------------------------------

def _empty_yellowpages_ca() -> dict:
    return {
        "yellowpages_owner_found": "no",
        "yellowpages_owner_name": "",
        "yellowpages_source_url": "",
    }


def search_yellowpages_ca(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Search YellowPages Canada for a business contact or owner name.

    YellowPages.ca has better Canadian local trade coverage than Yelp,
    with contact name fields that sometimes show the business owner.

    Strategy:
      1. Search DDG for the company on yellowpages.ca (more reliable than direct search)
      2. Follow the first yellowpages.ca/bus/ link
      3. Scan the listing for contact/owner name patterns

    Args:
        company_name: Business name to search for
        city:         City to narrow the search
        state:        Province to narrow the search

    Returns dict with keys: yellowpages_owner_found, yellowpages_owner_name, yellowpages_source_url
    Fails gracefully — never raises an exception.
    """
    if not company_name:
        return _empty_yellowpages_ca()

    try:
        # Use DDG to find the right YP.ca listing
        location_part = f' "{city}"' if city else ""
        query = f'site:yellowpages.ca "{company_name}"{location_part}'

        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={**_HEADERS, "Referer": "https://duckduckgo.com/"},
            timeout=config.DDG_REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return _empty_yellowpages_ca()

        soup = BeautifulSoup(resp.text, "lxml")

        # Find first yellowpages.ca business listing link
        biz_url = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "yellowpages.ca" in href and ("/bus/" in href or "/find/" in href):
                if "uddg=" in href:
                    from urllib.parse import unquote, urlparse, parse_qs
                    try:
                        parsed = parse_qs(urlparse(href).query)
                        href = unquote(parsed.get("uddg", [""])[0])
                    except Exception:
                        pass
                if "yellowpages.ca" in href:
                    biz_url = href
                    break

        if not biz_url:
            return _empty_yellowpages_ca()

        time.sleep(0.5)

        biz_resp = requests.get(
            biz_url,
            headers=_HEADERS,
            timeout=config.YELLOWPAGES_REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        if biz_resp.status_code != 200:
            return _empty_yellowpages_ca()

        biz_text = BeautifulSoup(biz_resp.text, "lxml").get_text(" ", strip=True)

        # Scan for contact/owner name patterns
        context_phrases = [
            "contact name",
            "contact:",
            "owner:",
            "owner of",
            "managed by",
            "founded by",
            "owned by",
            "proprietor",
        ]

        owner_name = ""
        for phrase in context_phrases:
            idx = biz_text.lower().find(phrase)
            if idx == -1:
                continue
            window_start = max(0, idx - 20)
            window_end = min(len(biz_text), idx + 180)
            window = biz_text[window_start:window_end]
            for m in _NAME_RE.finditer(window):
                candidate = m.group(0)
                if _is_plausible_person_name(candidate):
                    owner_name = candidate
                    break
            if owner_name:
                break

        if not owner_name:
            return _empty_yellowpages_ca()

        return {
            "yellowpages_owner_found": "yes",
            "yellowpages_owner_name": owner_name,
            "yellowpages_source_url": biz_url,
        }

    except Exception:
        return _empty_yellowpages_ca()


# ---------------------------------------------------------------------------
# Google Maps owner response search  [V6]
# ---------------------------------------------------------------------------

def _empty_google_maps() -> dict:
    return {
        "google_maps_owner_found": "no",
        "google_maps_owner_name": "",
        "google_maps_snippet": "",
    }

# Owner self-introduction patterns in Google review responses
# e.g. "Response from the owner: Hi, I'm John Smith the owner..."
_OWNER_INTRO_RE = re.compile(
    r"(?:i'?m|my name is|this is|hi[,!]?\s+i'?m|hello[,!]?\s+i'?m)\s+"
    r"([A-Z][a-zA-Z'\-]{1,20}(?:\s+[A-Z][a-zA-Z'\-]{1,20}){0,2})",
    re.IGNORECASE,
)


def search_google_maps_owner(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Find the business owner's name via Google Maps review responses indexed by DDG.

    Business owners frequently respond to Google reviews and sign with their name:
    "Response from the owner: Hi, I'm John Smith. Thank you for the kind words..."
    These responses get indexed by search engines and appear in DDG snippets.

    Strategy:
      1. Search DDG for '[company] [city] "response from the owner"'
      2. Parse DDG result snippets for owner self-introduction patterns
         ("I'm John Smith", "My name is Jane", "Hi, I'm Mike the owner")
      3. Validate found name against business-word blacklist

    Args:
        company_name: Business name to search for
        city:         City to narrow the search
        state:        Province/state to narrow the search

    Returns dict with keys: google_maps_owner_found, google_maps_owner_name, google_maps_snippet
    Fails gracefully — never raises an exception.
    """
    if not company_name:
        return _empty_google_maps()

    try:
        location_part = f' "{city}"' if city else ""
        query = f'"{company_name}"{location_part} "response from the owner"'

        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={**_HEADERS, "Referer": "https://duckduckgo.com/"},
            timeout=config.DDG_REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return _empty_google_maps()

        soup = BeautifulSoup(resp.text, "lxml")
        result_divs = soup.find_all("div", class_=re.compile(r"result", re.I))

        checked = 0
        for result in result_divs:
            if checked >= config.DDG_MAX_RESULTS * 2:
                break

            snippet_el = result.find(class_=re.compile(r"snippet", re.I))
            if not snippet_el:
                snippet_el = result.find("td")
            if not snippet_el:
                continue

            snippet_text = snippet_el.get_text(" ", strip=True)
            if len(snippet_text) < 20:
                continue

            checked += 1

            # Only process snippets that contain the key phrase
            if "response from the owner" not in snippet_text.lower():
                continue

            # Strategy A: look for owner self-introduction ("I'm John", "my name is Jane")
            m = _OWNER_INTRO_RE.search(snippet_text)
            if m:
                candidate = m.group(1).strip()
                if _is_plausible_person_name(candidate):
                    return {
                        "google_maps_owner_found": "yes",
                        "google_maps_owner_name": candidate,
                        "google_maps_snippet": snippet_text[:200],
                    }

            # Strategy B: find any plausible name in a window around the response phrase
            idx = snippet_text.lower().find("response from the owner")
            window = snippet_text[max(0, idx - 20): min(len(snippet_text), idx + 300)]
            for nm in _NAME_RE.finditer(window):
                candidate = nm.group(0)
                if _is_plausible_person_name(candidate):
                    return {
                        "google_maps_owner_found": "yes",
                        "google_maps_owner_name": candidate,
                        "google_maps_snippet": snippet_text[:200],
                    }

        return _empty_google_maps()

    except Exception:
        return _empty_google_maps()


# ---------------------------------------------------------------------------
# LinkedIn via Google (DDG)  [V8]
# ---------------------------------------------------------------------------

def _empty_linkedin() -> dict:
    return {
        "linkedin_owner_found": "no",
        "linkedin_owner_name": "",
        "linkedin_owner_title": "",
        "linkedin_source_url": "",
    }


def search_linkedin_google(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Find the business decision maker via LinkedIn profile results in DDG.

    LinkedIn personal profiles appear in DDG when searched with site:linkedin.com/in.
    Result titles follow predictable formats:
        "John Smith - Owner at Acme Plumbing | LinkedIn"
        "Mary Johnson - CEO · Acme Corp · Toronto · LinkedIn"
        "Mike Davis - Founder & President, Acme Services | LinkedIn"

    No LinkedIn account or API is needed — this reads public search snippets only.

    Strategy:
      1. Query DDG: site:linkedin.com/in "Company Name" "City" (owner OR ceo OR founder ...)
      2. Find result links pointing to linkedin.com/in/ personal profile URLs
      3. Parse the result title to extract name (text before first separator) and title
      4. Validate name with _is_plausible_person_name()

    Args:
        company_name: Business name to search for
        city:         City to narrow the search (important — reduces false matches)
        state:        Province/state to narrow the search

    Returns dict: linkedin_owner_found, linkedin_owner_name, linkedin_owner_title,
                  linkedin_source_url
    Fails gracefully — never raises an exception.
    Hit rate: ~35–55% for businesses with a LinkedIn presence.
    """
    if not company_name:
        return _empty_linkedin()

    try:
        location_part = f' "{city}"' if city else ""
        query = (
            f'site:linkedin.com/in "{company_name}"{location_part} '
            f'(owner OR ceo OR founder OR president OR "managing director" OR proprietor)'
        )

        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={**_HEADERS, "Referer": "https://duckduckgo.com/"},
            timeout=config.DDG_REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return _empty_linkedin()

        soup = BeautifulSoup(resp.text, "lxml")
        result_divs = soup.find_all("div", class_=re.compile(r"result", re.I))

        for result in result_divs[: config.DDG_MAX_RESULTS * 3]:
            # Find the clickable title link
            title_el = (
                result.find("a", class_=re.compile(r"result__a", re.I))
                or result.find("a", href=True)
            )
            if not title_el:
                continue

            title_text = title_el.get_text(" ", strip=True)
            href = title_el.get("href", "")

            # Unwrap DDG redirect (uddg= parameter)
            if "uddg=" in href:
                from urllib.parse import unquote, urlparse, parse_qs
                try:
                    parsed = parse_qs(urlparse(href).query)
                    href = unquote(parsed.get("uddg", [""])[0])
                except Exception:
                    pass

            # Only accept personal LinkedIn profile URLs (/in/username)
            if "linkedin.com/in/" not in href.lower():
                continue

            # Strip trailing " | LinkedIn" or "- LinkedIn" from title
            clean_title = re.sub(r"\s*[\|\-–—]\s*LinkedIn\s*$", "", title_text, flags=re.I).strip()

            # Try splitting on common LinkedIn title separators: " - ", " | ", " · ", " — "
            owner_name = ""
            owner_role = ""
            for sep in [" - ", " | ", " · ", " — ", " – "]:
                if sep in clean_title:
                    parts = [p.strip() for p in clean_title.split(sep)]
                    if len(parts) >= 2:
                        candidate_name = parts[0].strip()
                        if _is_plausible_person_name(candidate_name):
                            owner_name = candidate_name
                            # Extract role from second segment, strip "at Company" suffix
                            raw_role = parts[1]
                            raw_role = re.sub(r"\s+at\s+.+$", "", raw_role, flags=re.I).strip()
                            raw_role = re.sub(r"\s+@\s+.+$", "", raw_role).strip()
                            raw_role = re.sub(r"\s+[,|·\-]\s+.+$", "", raw_role).strip()
                            if raw_role and len(raw_role) < 80:
                                owner_role = raw_role
                            break

            # Fallback: scan title text for first plausible name via regex
            if not owner_name:
                for m in _NAME_RE.finditer(clean_title):
                    candidate = m.group(0)
                    if _is_plausible_person_name(candidate):
                        owner_name = candidate
                        break

            if not owner_name:
                continue

            # --- Jina profile fetch: validate and improve name/title ---
            if config.USE_LINKEDIN_JINA_FETCH and href:
                jina_text = _fetch_via_jina(href, timeout=config.JINA_REQUEST_TIMEOUT)
                if jina_text and len(jina_text) > 80:
                    lines = [ln.strip() for ln in jina_text.split("\n") if ln.strip()]
                    # First non-empty line from a LinkedIn profile is usually the full name
                    if lines and _is_plausible_person_name(lines[0]):
                        owner_name = lines[0]
                    # Second line is often "Title at Company" or "Title | Company"
                    if len(lines) > 1:
                        role_line = lines[1]
                        role_match = re.match(
                            r"^([^|@\n,]{3,60})(?:\s+at\s+|\s*\|\s*|\s*,\s*).+",
                            role_line, re.I
                        )
                        if role_match:
                            candidate_role = role_match.group(1).strip()
                            if _ROLE_RE.search(candidate_role) and len(candidate_role) < 80:
                                owner_role = candidate_role

            return {
                "linkedin_owner_found": "yes",
                "linkedin_owner_name": owner_name,
                "linkedin_owner_title": owner_role,
                "linkedin_source_url": href,
            }

        return _empty_linkedin()

    except Exception:
        return _empty_linkedin()


# ---------------------------------------------------------------------------
# Google Business Profile / Knowledge Panel search  [V9]
# ---------------------------------------------------------------------------

def _empty_google_business() -> dict:
    return {
        "google_business_owner_found": "no",
        "google_business_owner_name": "",
        "google_business_snippet": "",
    }


def search_google_business(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Find the business owner via Google's Knowledge Panel / Business Profile.

    Fetches google.com/search?q=company+city via Jina.ai to get the rendered
    Knowledge Panel, which often contains the business description and owner name.

    Falls back to DDG snippet parsing if Jina fails.

    Args:
        company_name: The business name to search for
        city:         City to narrow the search
        state:        Province/state to narrow the search

    Returns dict: google_business_owner_found, google_business_owner_name,
                  google_business_snippet
    Fails gracefully — never raises an exception.
    """
    if not company_name:
        return _empty_google_business()

    try:
        query_parts = [company_name]
        if city:
            query_parts.append(city)
        if state:
            query_parts.append(state)
        query_str = " ".join(query_parts)

        # Strategy A: Jina-rendered Google search (gets Knowledge Panel)
        google_url = f"https://www.google.com/search?q={quote(query_str)}"
        jina_text = _fetch_via_jina(google_url, timeout=config.GOOGLE_BUSINESS_REQUEST_TIMEOUT)

        context_phrases = [
            "owner:",
            "founded by",
            "owned by",
            "managed by",
            "proprietor",
            "meet the owner",
            "business owner",
        ]

        owner_name = ""
        snippet = ""

        if jina_text and len(jina_text) > 100:
            text_lower = jina_text.lower()
            for phrase in context_phrases:
                idx = text_lower.find(phrase)
                if idx == -1:
                    continue
                window_start = max(0, idx - 20)
                window_end   = min(len(jina_text), idx + 250)
                window = jina_text[window_start:window_end]
                for m in _NAME_RE.finditer(window):
                    candidate = m.group(0)
                    if _is_plausible_person_name(candidate):
                        owner_name = candidate
                        snippet = window[:200].strip()
                        break
                if owner_name:
                    break

        if owner_name:
            return {
                "google_business_owner_found": "yes",
                "google_business_owner_name": owner_name,
                "google_business_snippet": snippet,
            }

        # Strategy B: DDG search for Google Business listing snippets
        location_part = f' "{city}"' if city else ""
        ddg_query = (
            f'"{company_name}"{location_part} '
            f'(owner OR founder OR CEO OR president) '
            f'site:maps.google.com OR site:google.com/maps'
        )

        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": ddg_query},
            headers={**_HEADERS, "Referer": "https://duckduckgo.com/"},
            timeout=config.DDG_REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return _empty_google_business()

        soup = BeautifulSoup(resp.text, "lxml")
        result_divs = soup.find_all("div", class_=re.compile(r"result", re.I))

        for result in result_divs[:config.DDG_MAX_RESULTS]:
            snippet_el = result.find(class_=re.compile(r"snippet", re.I))
            if not snippet_el:
                snippet_el = result.find("td")
            if not snippet_el:
                continue
            snippet_text = snippet_el.get_text(" ", strip=True)
            if len(snippet_text) < 20 or not _ROLE_RE.search(snippet_text):
                continue
            for nm in _NAME_RE.finditer(snippet_text):
                candidate = nm.group(0)
                if _is_plausible_person_name(candidate):
                    return {
                        "google_business_owner_found": "yes",
                        "google_business_owner_name": candidate,
                        "google_business_snippet": snippet_text[:200],
                    }

        return _empty_google_business()

    except Exception:
        return _empty_google_business()


# ---------------------------------------------------------------------------
# Facebook Business page owner search  [V9]
# ---------------------------------------------------------------------------

def _empty_facebook() -> dict:
    return {
        "facebook_owner_found": "no",
        "facebook_owner_name": "",
        "facebook_source_url": "",
    }


def search_facebook_business(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Find the business owner from the company's Facebook Business page.

    Many small business owners manage their own Facebook page and mention
    their name in the About section or pinned posts.

    Strategy:
      1. DDG search: site:facebook.com "company" "city"
      2. Find first facebook.com/pg/ or facebook.com/{slug} URL
      3. Fetch the /about section via Jina.ai
      4. Look for owner/founder name near ownership context keywords

    Args:
        company_name: The business name to search for
        city:         City to narrow the search
        state:        Province/state to narrow the search

    Returns dict: facebook_owner_found, facebook_owner_name, facebook_source_url
    Fails gracefully — never raises an exception.
    """
    if not company_name:
        return _empty_facebook()

    try:
        location_part = f' "{city}"' if city else ""
        query = f'site:facebook.com "{company_name}"{location_part}'

        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={**_HEADERS, "Referer": "https://duckduckgo.com/"},
            timeout=config.DDG_REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return _empty_facebook()

        soup = BeautifulSoup(resp.text, "lxml")
        fb_url = None

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "uddg=" in href:
                from urllib.parse import unquote, urlparse, parse_qs
                try:
                    parsed = parse_qs(urlparse(href).query)
                    href = unquote(parsed.get("uddg", [""])[0])
                except Exception:
                    pass
            if "facebook.com/" in href and not any(x in href for x in [
                "facebook.com/login", "facebook.com/signup", "facebook.com/search",
                "facebook.com/share", "facebook.com/sharer",
            ]):
                if re.search(r"facebook\.com/[A-Za-z0-9._-]{3,}", href):
                    fb_url = href.split("?")[0]  # strip query params
                    break

        if not fb_url:
            return _empty_facebook()

        # Fetch the About page via Jina
        about_url = fb_url.rstrip("/") + "/about"
        jina_text = _fetch_via_jina(about_url, timeout=config.FACEBOOK_REQUEST_TIMEOUT)

        if not jina_text or len(jina_text) < 50:
            return _empty_facebook()

        context_phrases = [
            "founded by",
            "owned by",
            "meet the owner",
            "owner:",
            "business owner",
            "managed by",
            "started by",
            "proprietor",
        ]

        owner_name = ""
        for phrase in context_phrases:
            idx = jina_text.lower().find(phrase)
            if idx == -1:
                continue
            window_start = max(0, idx - 20)
            window_end   = min(len(jina_text), idx + 250)
            window = jina_text[window_start:window_end]
            for m in _NAME_RE.finditer(window):
                candidate = m.group(0)
                if _is_plausible_person_name(candidate):
                    owner_name = candidate
                    break
            if owner_name:
                break

        if not owner_name:
            return _empty_facebook()

        return {
            "facebook_owner_found": "yes",
            "facebook_owner_name": owner_name,
            "facebook_source_url": fb_url,
        }

    except Exception:
        return _empty_facebook()


# ---------------------------------------------------------------------------
# Press release mining (PRNewswire / BusinessWire / GlobeNewswire)  [V9]
# ---------------------------------------------------------------------------

def _empty_press_release() -> dict:
    return {
        "press_release_owner_found": "no",
        "press_release_owner_name": "",
        "press_release_owner_title": "",
        "press_release_snippet": "",
    }


def search_press_releases(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Search press release sites for executive appointment announcements.

    Companies announce new CEOs, founders, and presidents via press releases on
    PRNewswire, BusinessWire, and GlobeNewswire — all indexed by DDG.

    Strategy:
      1. DDG: "Company" (CEO OR founder OR president OR "named" OR "appoints")
         site:prnewswire.com OR site:businesswire.com OR site:globenewswire.com
      2. Parse result snippets for role keyword + proper name

    Args:
        company_name: The business name to search for
        city:         City (optional, improves precision)
        state:        State/province (optional)

    Returns dict: press_release_owner_found, press_release_owner_name,
                  press_release_owner_title, press_release_snippet
    Fails gracefully — never raises an exception.
    """
    if not company_name:
        return _empty_press_release()

    try:
        location_part = f' "{city}"' if city else ""
        query = (
            f'"{company_name}"{location_part} '
            f'(CEO OR founder OR president OR "named" OR "appoints" OR owner) '
            f'(site:prnewswire.com OR site:businesswire.com OR site:globenewswire.com)'
        )

        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={**_HEADERS, "Referer": "https://duckduckgo.com/"},
            timeout=config.PRESS_RELEASE_REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return _empty_press_release()

        soup = BeautifulSoup(resp.text, "lxml")
        result_divs = soup.find_all("div", class_=re.compile(r"result", re.I))

        for result in result_divs[:config.DDG_MAX_RESULTS]:
            snippet_el = result.find(class_=re.compile(r"snippet", re.I))
            if not snippet_el:
                snippet_el = result.find("td")
            if not snippet_el:
                continue

            snippet_text = snippet_el.get_text(" ", strip=True)
            if len(snippet_text) < 20 or not _ROLE_RE.search(snippet_text):
                continue

            role_m = _ROLE_RE.search(snippet_text)
            role = role_m.group(0).title() if role_m else ""

            for nm in _NAME_RE.finditer(snippet_text):
                candidate = nm.group(0)
                if _is_plausible_person_name(candidate):
                    return {
                        "press_release_owner_found": "yes",
                        "press_release_owner_name": candidate,
                        "press_release_owner_title": role,
                        "press_release_snippet": snippet_text[:200],
                    }

        return _empty_press_release()

    except Exception:
        return _empty_press_release()


# ---------------------------------------------------------------------------
# Crunchbase founder/CEO search  [V9]
# ---------------------------------------------------------------------------

def _empty_crunchbase() -> dict:
    return {
        "crunchbase_owner_found": "no",
        "crunchbase_owner_name": "",
        "crunchbase_owner_title": "",
        "crunchbase_source_url": "",
    }


def search_crunchbase(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Search Crunchbase for the company's founder or CEO.

    Crunchbase lists founders, CEOs, and board members for millions of companies.
    Most relevant for tech, SaaS, and startup companies.

    Strategy:
      1. DDG: site:crunchbase.com "company" (founder OR CEO OR owner)
      2. Follow first crunchbase.com/organization/ link
      3. Fetch via Jina.ai and parse the "Founders" / "Leadership" section
      4. Fallback: parse DDG title directly for name + role

    Args:
        company_name: The business name to search for
        city:         City (optional)
        state:        State/province (optional)

    Returns dict: crunchbase_owner_found, crunchbase_owner_name,
                  crunchbase_owner_title, crunchbase_source_url
    Fails gracefully — never raises an exception.
    """
    if not company_name:
        return _empty_crunchbase()

    try:
        location_part = f' "{city}"' if city else ""
        query = f'site:crunchbase.com/organization "{company_name}"{location_part}'

        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={**_HEADERS, "Referer": "https://duckduckgo.com/"},
            timeout=config.DDG_REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return _empty_crunchbase()

        soup = BeautifulSoup(resp.text, "lxml")
        cb_url = None

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "uddg=" in href:
                from urllib.parse import unquote, urlparse, parse_qs
                try:
                    parsed = parse_qs(urlparse(href).query)
                    href = unquote(parsed.get("uddg", [""])[0])
                except Exception:
                    pass
            if "crunchbase.com/organization/" in href:
                cb_url = href.split("?")[0]
                break

        if not cb_url:
            return _empty_crunchbase()

        # Fetch Crunchbase organization page via Jina
        jina_text = _fetch_via_jina(cb_url, timeout=config.CRUNCHBASE_REQUEST_TIMEOUT)

        if jina_text and len(jina_text) > 100:
            founder_labels = ["founder", "co-founder", "ceo", "chief executive", "owner"]
            lines = [ln.strip() for ln in jina_text.split("\n") if ln.strip()]

            for i, line in enumerate(lines):
                line_lower = line.lower()
                if any(lbl in line_lower for lbl in founder_labels):
                    # Check adjacent lines for a plausible person name
                    for offset in [-1, 1, -2, 2]:
                        idx = i + offset
                        if 0 <= idx < len(lines):
                            candidate = lines[idx].strip()
                            if _is_plausible_person_name(candidate):
                                # Extract title from the label line
                                role_m = _ROLE_RE.search(line)
                                title = role_m.group(0).title() if role_m else "Founder"
                                return {
                                    "crunchbase_owner_found": "yes",
                                    "crunchbase_owner_name": candidate,
                                    "crunchbase_owner_title": title,
                                    "crunchbase_source_url": cb_url,
                                }

        # Fallback: parse the DDG result title for name + role
        for result in soup.find_all("div", class_=re.compile(r"result", re.I)):
            title_el = result.find("a", class_=re.compile(r"result__a", re.I)) or result.find("a", href=True)
            if not title_el:
                continue
            title_text = title_el.get_text(" ", strip=True)
            if not _ROLE_RE.search(title_text):
                continue
            for nm in _NAME_RE.finditer(title_text):
                candidate = nm.group(0)
                if _is_plausible_person_name(candidate):
                    role_m = _ROLE_RE.search(title_text)
                    return {
                        "crunchbase_owner_found": "yes",
                        "crunchbase_owner_name": candidate,
                        "crunchbase_owner_title": role_m.group(0).title() if role_m else "Founder",
                        "crunchbase_source_url": cb_url or "",
                    }

        return _empty_crunchbase()

    except Exception:
        return _empty_crunchbase()
