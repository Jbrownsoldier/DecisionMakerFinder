# extractor.py
# Decision maker extraction using a scored, multi-page candidate pipeline.
#
# V3 upgrade: candidates are collected across ALL fetched pages, scored using
# configurable weights, and ranked before selection. Claude Haiku is only
# called when deterministic methods produce no candidate with sufficient score.
#
# Strategy pipeline per page:
#   1. schema.org JSON-LD  (confidence: high)   — early exit if Tier 1 found
#   2. HTML team card patterns  (confidence: medium)
#   3. Plain text regex sweep   (confidence: low)
#   4. Claude Haiku fallback    (confidence: varies) — only when needed

import re
import json
from dataclasses import dataclass, field
from bs4 import BeautifulSoup

import config
from cleaner import clean_name_for_display, clean_name_for_email, split_full_name


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExtractedPerson:
    full_name: str = ""
    title: str = ""
    source: str = ""            # schema_org | html_card | text_regex | claude_haiku | openrouter
    confidence: str = ""        # high | medium | low
    score: int = 0
    score_reason: list = field(default_factory=list)  # human-readable scoring log
    matched_page: str = ""      # page_path where found, e.g. "/about"
    matched_snippet: str = ""   # up to 150 chars of surrounding context text
    contact_seniority: str = "" # decision_maker | secondary_contact | unknown


@dataclass
class ExtractionResult:
    primary: "ExtractedPerson | None" = None
    backup: "ExtractedPerson | None" = None
    notes: str = ""
    all_candidates: list = field(default_factory=list)  # all scored candidates


# ---------------------------------------------------------------------------
# Role tier helpers
# ---------------------------------------------------------------------------

# Build lowercase sets for fast membership checks
_TIER1_SET = {r.lower() for r in config.TIER_1_ROLES}
_TIER2_SET = {r.lower() for r in config.TIER_2_ROLES}
_SUPPORT_SET = {r.lower() for r in config.SUPPORT_ADMIN_ROLES}

# Regex matching any role keyword as a whole phrase
_ROLE_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(r) for r in config.ROLE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Pattern for a plausible Western proper name: 2-4 title-cased words
_NAME_PATTERN = re.compile(
    r"\b([A-Z][a-zA-Z'\-]{1,20}(?:\s+[A-Z][a-zA-Z'\-]{1,20}){1,3})\b"
)

# ---------------------------------------------------------------------------
# Business-word blacklist — words that appear in company names but NEVER
# in real person names. Any matched name containing one of these is rejected.
# ---------------------------------------------------------------------------
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


def _is_plausible_person_name(name: str) -> bool:
    """
    Return True only if the string looks like a real person's name.

    Rejects:
    - Fewer than 2 words
    - Any word that is a known business / industry / boilerplate term
    - Strings containing digits  (e.g. "Suite 200")
    - Strings longer than 50 characters
    """
    if not name or len(name.split()) < 2:
        return False
    if re.search(r"\d", name):
        return False
    if len(name) > 50:
        return False
    words = [w.rstrip(".'s").lower() for w in name.split()]
    if any(w in _BUSINESS_WORDS for w in words):
        return False
    return True


def _classify_seniority(title: str) -> str:
    """Return contact_seniority based on title string."""
    t = title.lower()
    for role in config.TIER_1_ROLES:
        if role in t:
            return "decision_maker"
    for role in config.TIER_2_ROLES:
        if role in t:
            return "secondary_contact"
    return "unknown"


def _is_support_admin(title: str) -> bool:
    """Return True if the title matches a support/admin role to be penalised."""
    t = title.lower()
    return any(role in t for role in _SUPPORT_SET)


def _title_is_generic(title: str) -> bool:
    """Return True if the title has no recognisable role keyword."""
    return not bool(_ROLE_PATTERN.search(title))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_candidate(person: ExtractedPerson, page_path: str,
                     has_generic_email_nearby: bool,
                     in_heading_context: bool,
                     name_title_close: bool) -> ExtractedPerson:
    """
    Compute and attach a score + score_reason list to the candidate.
    Returns the same object with score and score_reason populated.
    """
    score = 0
    reasons = []
    w = config.SCORE_WEIGHTS

    if person.source == "schema_org":
        score += w["schema_org"]
        reasons.append(f"schema_org +{w['schema_org']}")

    title_lower = person.title.lower()

    tier1_match = any(role in title_lower for role in config.TIER_1_ROLES)
    tier2_match = any(role in title_lower for role in config.TIER_2_ROLES)

    if tier1_match:
        score += w["tier1_title"]
        reasons.append(f"tier1_title +{w['tier1_title']}")
    elif tier2_match:
        score += w["tier2_title"]
        reasons.append(f"tier2_title +{w['tier2_title']}")

    if page_path in config.PRIORITY_PAGES:
        score += w["priority_page"]
        reasons.append(f"priority_page({page_path}) +{w['priority_page']}")

    if person.source == "html_card":
        score += w["html_card"]
        reasons.append(f"html_card +{w['html_card']}")

    if in_heading_context:
        score += w["heading_context"]
        reasons.append(f"heading_context +{w['heading_context']}")

    if name_title_close:
        score += w["proximity"]
        reasons.append(f"proximity +{w['proximity']}")

    if _title_is_generic(person.title):
        score += w["generic_title"]   # negative
        reasons.append(f"generic_title {w['generic_title']}")

    if has_generic_email_nearby:
        score += w["generic_email_nearby"]   # negative
        reasons.append(f"generic_email_nearby {w['generic_email_nearby']}")

    if not split_full_name(clean_name_for_email(person.full_name))[1]:
        # No last name found
        score += w["first_name_only"]   # negative
        reasons.append(f"first_name_only {w['first_name_only']}")

    if _is_support_admin(person.title):
        score += w["support_admin"]   # negative
        reasons.append(f"support_admin {w['support_admin']}")

    person.score = score
    person.score_reason = reasons
    person.contact_seniority = _classify_seniority(person.title)
    return person


# ---------------------------------------------------------------------------
# Generic-email detector (used for nearby-email penalty)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _has_generic_email_nearby(text: str) -> bool:
    """
    Return True if any email address found in text has a generic prefix
    (info@, support@, etc.), suggesting this is a generic contact rather
    than a personal address.
    """
    for m in _EMAIL_RE.finditer(text):
        prefix = m.group(0).split("@")[0].lower()
        if prefix in config.GENERIC_EMAIL_PREFIXES:
            return True
    return False


# ---------------------------------------------------------------------------
# Strategy 1: schema.org JSON-LD
# ---------------------------------------------------------------------------

def _extract_schema_org(soup: BeautifulSoup, page_path: str) -> list[ExtractedPerson]:
    """
    Look for <script type="application/ld+json"> blocks containing
    @type: Person or Organisation member/employee arrays.
    Returns persons tagged with page_path.
    """
    persons = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        entries = data if isinstance(data, list) else [data]
        # Expand @graph
        flat = []
        for e in entries:
            if isinstance(e, dict) and "@graph" in e:
                flat.extend(e["@graph"])
            else:
                flat.append(e)

        for entry in flat:
            if not isinstance(entry, dict):
                continue

            schema_type = entry.get("@type", "")
            if isinstance(schema_type, list):
                schema_type = " ".join(schema_type)

            # Direct Person entry
            if "Person" in schema_type:
                name = entry.get("name", "").strip()
                title = (
                    entry.get("jobTitle", "")
                    or entry.get("title", "")
                    or entry.get("description", "")
                ).strip()
                if name and _ROLE_PATTERN.search(title):
                    p = ExtractedPerson(
                        full_name=clean_name_for_display(name),
                        title=title,
                        source="schema_org",
                        confidence="high",
                        matched_page=page_path,
                    )
                    persons.append(p)

            # Organisation with member/employee arrays
            for member_key in ("member", "employee", "foundingTeam", "founder"):
                members = entry.get(member_key, [])
                if isinstance(members, dict):
                    members = [members]
                for m in members:
                    if not isinstance(m, dict):
                        continue
                    name = m.get("name", "").strip()
                    title = (m.get("jobTitle", "") or m.get("title", "")).strip()
                    if name and _ROLE_PATTERN.search(title):
                        p = ExtractedPerson(
                            full_name=clean_name_for_display(name),
                            title=title,
                            source="schema_org",
                            confidence="high",
                            matched_page=page_path,
                        )
                        persons.append(p)

    return persons


# ---------------------------------------------------------------------------
# Strategy 2: HTML team card patterns
# ---------------------------------------------------------------------------

_NAME_TAGS = {"h1", "h2", "h3", "h4", "h5", "strong", "b", "span", "p", "div", "a"}
_TITLE_TAGS = {"p", "span", "div", "em", "small", "li", "h5", "h6"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "strong", "b"}

_TEAM_CLASS_HINTS = re.compile(
    r"(team|staff|people|member|leader|founder|about|bio|card|profile|person)",
    re.IGNORECASE,
)


def _element_class_str(el) -> str:
    classes = el.get("class", [])
    return " ".join(classes) if isinstance(classes, list) else str(classes)


def _find_team_containers(soup: BeautifulSoup) -> list:
    containers = []
    for tag in soup.find_all(["section", "div", "article", "ul", "li"]):
        class_str = _element_class_str(tag).lower()
        id_str = str(tag.get("id", "")).lower()
        if _TEAM_CLASS_HINTS.search(class_str + " " + id_str):
            containers.append(tag)
    return containers


def _extract_html_cards(soup: BeautifulSoup, page_path: str,
                        page_text: str) -> list[ExtractedPerson]:
    """
    Look for repeating card-like structures where a name and role appear
    in nearby elements. Tags each candidate with page_path and heading_context.
    """
    persons = []
    containers = _find_team_containers(soup)
    search_roots = containers if containers else [soup]

    for root in search_roots:
        role_els = []
        for el in root.find_all(True):
            text = el.get_text(" ", strip=True)
            if _ROLE_PATTERN.search(text) and len(text) < 120:
                role_els.append(el)

        for role_el in role_els:
            role_text = role_el.get_text(" ", strip=True)
            if not _ROLE_PATTERN.search(role_text):
                continue
            title_str = role_text.strip()
            name_str = ""
            in_heading = role_el.name in _HEADING_TAGS

            # Check previous siblings for a name
            for sib in role_el.find_previous_siblings():
                sib_text = sib.get_text(" ", strip=True)
                m = _NAME_PATTERN.search(sib_text)
                if m and len(sib_text) < 80:
                    name_str = m.group(0)
                    if sib.name in _HEADING_TAGS:
                        in_heading = True
                    break

            # Check next siblings
            if not name_str:
                for sib in role_el.find_next_siblings():
                    sib_text = sib.get_text(" ", strip=True)
                    m = _NAME_PATTERN.search(sib_text)
                    if m and len(sib_text) < 80:
                        name_str = m.group(0)
                        if sib.name in _HEADING_TAGS:
                            in_heading = True
                        break

            # Check parent element for combined "Name - Title" text
            if not name_str and role_el.parent:
                parent_text = role_el.parent.get_text(" ", strip=True)
                remainder = _ROLE_PATTERN.sub("", parent_text).strip("- ,|:")
                m = _NAME_PATTERN.search(remainder)
                if m and len(remainder) < 100:
                    name_str = m.group(0)

            if name_str and _is_plausible_person_name(name_str):
                # Build a short snippet of surrounding context
                snippet = _build_snippet(page_text, name_str, title_str)
                generic_email_nearby = _has_generic_email_nearby(snippet)

                p = ExtractedPerson(
                    full_name=clean_name_for_display(name_str),
                    title=title_str[:100].strip(),
                    source="html_card",
                    confidence="medium",
                    matched_page=page_path,
                    matched_snippet=snippet,
                )
                _score_candidate(
                    p, page_path,
                    has_generic_email_nearby=generic_email_nearby,
                    in_heading_context=in_heading,
                    name_title_close=True,
                )
                persons.append(p)

    return _deduplicate_persons(persons)


# ---------------------------------------------------------------------------
# Strategy 3: Plain text regex sweep
# ---------------------------------------------------------------------------

# Name + separator + role
_PATTERN_A = re.compile(
    r"([A-Z][a-zA-Z'\-]{1,20}(?:\s+[A-Z][a-zA-Z'\-]{1,20}){1,3})"
    r"\s*[,|\-–—\/]\s*"
    r"([A-Za-z\s]{3,50})",
)

# Role + separator + name
_PATTERN_B = re.compile(
    r"([A-Za-z\s]{3,40})"
    r"\s*[:\-–—]\s*"
    r"([A-Z][a-zA-Z'\-]{1,20}(?:\s+[A-Z][a-zA-Z'\-]{1,20}){1,3})",
)


def _extract_text_regex(text: str, page_path: str) -> list[ExtractedPerson]:
    """
    Scan plain page text for patterns like:
      "John Smith, CEO"  /  "Owner: Mary Johnson"  /  "Jane Doe | Founder"
    """
    persons = []

    for m in _PATTERN_A.finditer(text):
        name_part = m.group(1).strip()
        role_part = m.group(2).strip()
        if _ROLE_PATTERN.search(role_part) and _is_plausible_person_name(name_part):
            start = max(0, m.start() - 80)
            end = min(len(text), m.end() + 80)
            snippet = text[start:end].strip()
            p = ExtractedPerson(
                full_name=clean_name_for_display(name_part),
                title=role_part[:80],
                source="text_regex",
                confidence="low",
                matched_page=page_path,
                matched_snippet=snippet[:150],
            )
            _score_candidate(
                p, page_path,
                has_generic_email_nearby=_has_generic_email_nearby(snippet),
                in_heading_context=False,
                name_title_close=True,
            )
            persons.append(p)

    for m in _PATTERN_B.finditer(text):
        role_part = m.group(1).strip()
        name_part = m.group(2).strip()
        if _ROLE_PATTERN.search(role_part) and _is_plausible_person_name(name_part):
            start = max(0, m.start() - 80)
            end = min(len(text), m.end() + 80)
            snippet = text[start:end].strip()
            p = ExtractedPerson(
                full_name=clean_name_for_display(name_part),
                title=role_part[:80],
                source="text_regex",
                confidence="low",
                matched_page=page_path,
                matched_snippet=snippet[:150],
            )
            _score_candidate(
                p, page_path,
                has_generic_email_nearby=_has_generic_email_nearby(snippet),
                in_heading_context=False,
                name_title_close=True,
            )
            persons.append(p)

    return _deduplicate_persons(persons)


# ---------------------------------------------------------------------------
# Strategy 5: Footer / copyright / image-alt parsing
# ---------------------------------------------------------------------------

# Copyright line: "© 2024 John Smith Plumbing" or "Copyright 2023 Jane Doe LLC"
_COPYRIGHT_RE = re.compile(
    r"(?:©|\bCopyright\b)\s*\d{4}\s+([A-Z][a-zA-Z'\-]{1,20}(?:\s+[A-Z][a-zA-Z'\-]{1,20}){1,3})",
    re.IGNORECASE,
)

# Contact-block signature: "John Smith | Owner | john@company.com"
_SIGNATURE_RE = re.compile(
    r"([A-Z][a-zA-Z'\-]{1,20}(?:\s+[A-Z][a-zA-Z'\-]{1,20}){1,3})"
    r"\s*[\|]\s*"
    r"([A-Za-z\s]{5,40})"
    r"\s*[\|]\s*"
    r"[a-zA-Z0-9._%+\-]+@",
)

# "Founded by John Smith" / "Owned by Jane Doe" in any body text
_FOUNDED_BY_RE = re.compile(
    r"(?:founded|owned|started|established|run)\s+by\s+"
    r"([A-Z][a-zA-Z'\-]{1,20}(?:\s+[A-Z][a-zA-Z'\-]{1,20}){1,3})",
    re.IGNORECASE,
)


def _extract_footer_copyright(soup: BeautifulSoup, text: str,
                               page_path: str) -> list[ExtractedPerson]:
    """
    Strategy 5: scan for owner names in places the main strategies miss:
      - Footer copyright lines:  "© 2024 John Smith's Plumbing"
      - Contact block signatures: "John Smith | Owner | john@company.com"
      - Image alt text:           alt="John Smith, Owner"
      - "Founded by / Owned by" phrases in body text

    Returns persons with source="footer_copyright", "contact_signature",
    "founded_by", or "image_alt" — confidence set to "medium".
    """
    persons = []

    # --- A: Footer copyright ---
    footer_el = soup.find("footer")
    footer_text = footer_el.get_text(" ", strip=True) if footer_el else ""

    for m in _COPYRIGHT_RE.finditer(footer_text or text[:2000]):
        raw_name = m.group(1).strip()
        # Try progressively shorter prefixes to recover "John Smith" from
        # "John Smith Plumbing Inc" — stop at the first plausible person name.
        words = raw_name.split()
        name = ""
        for end in range(len(words), 1, -1):
            candidate = " ".join(words[:end])
            if _is_plausible_person_name(candidate):
                name = candidate
                break
        if not name:
            continue
        p = ExtractedPerson(
            full_name=clean_name_for_display(name),
            title="Business Owner",   # inferred from copyright authorship
            source="footer_copyright",
            confidence="medium",
            matched_page=page_path,
            matched_snippet=m.group(0)[:150],
        )
        _score_candidate(
            p, page_path,
            has_generic_email_nearby=False,
            in_heading_context=False,
            name_title_close=True,
        )
        persons.append(p)

    # --- B: Contact block email signatures ---
    for m in _SIGNATURE_RE.finditer(text):
        name = m.group(1).strip()
        title = m.group(2).strip()
        if _ROLE_PATTERN.search(title) and _is_plausible_person_name(name):
            snippet = m.group(0)[:150]
            p = ExtractedPerson(
                full_name=clean_name_for_display(name),
                title=title,
                source="contact_signature",
                confidence="medium",
                matched_page=page_path,
                matched_snippet=snippet,
            )
            _score_candidate(
                p, page_path,
                has_generic_email_nearby=False,
                in_heading_context=False,
                name_title_close=True,
            )
            persons.append(p)

    # --- C: "Founded by / Owned by" phrases ---
    for m in _FOUNDED_BY_RE.finditer(text[:5000]):
        raw_name = m.group(1).strip()
        # Try shorter prefixes to handle "John Smith Plumbing" → "John Smith"
        words = raw_name.split()
        name = ""
        for end in range(len(words), 1, -1):
            candidate = " ".join(words[:end])
            if _is_plausible_person_name(candidate):
                name = candidate
                break
        if name:
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 40)
            snippet = text[start:end].strip()
            p = ExtractedPerson(
                full_name=clean_name_for_display(name),
                title="Founder",   # inferred from "founded by" language
                source="founded_by",
                confidence="medium",
                matched_page=page_path,
                matched_snippet=snippet[:150],
            )
            _score_candidate(
                p, page_path,
                has_generic_email_nearby=_has_generic_email_nearby(snippet),
                in_heading_context=False,
                name_title_close=True,
            )
            persons.append(p)

    # --- D: Image alt text ---
    for img in soup.find_all("img", alt=True):
        alt = img.get("alt", "").strip()
        if not alt or len(alt) > 120:
            continue
        # Must contain both a role keyword and a plausible name
        if not _ROLE_PATTERN.search(alt):
            continue
        m = _NAME_PATTERN.search(alt)
        if not m:
            continue
        name = m.group(0)
        if len(name.split()) < 2:
            continue
        role_m = _ROLE_PATTERN.search(alt)
        title = role_m.group(0) if role_m else ""
        p = ExtractedPerson(
            full_name=clean_name_for_display(name),
            title=title,
            source="image_alt",
            confidence="medium",
            matched_page=page_path,
            matched_snippet=alt[:150],
        )
        _score_candidate(
            p, page_path,
            has_generic_email_nearby=False,
            in_heading_context=False,
            name_title_close=True,
        )
        persons.append(p)

    return _deduplicate_persons(persons)


# ---------------------------------------------------------------------------
# Strategy 4: Claude Haiku AI fallback
# ---------------------------------------------------------------------------

def _build_haiku_text(pages: list) -> str:
    """
    Extract the most relevant page text to send to Haiku.
    Prefers priority pages (/about, /team, /leadership) over homepage.
    Caps at HAIKU_TEXT_LIMIT characters.
    """
    # Prefer priority pages
    priority_texts = []
    other_texts = []
    for page in pages:
        if not page.text:
            continue
        if page.page_path in config.PRIORITY_PAGES:
            priority_texts.append(page.text)
        else:
            other_texts.append(page.text)

    combined = " ".join(priority_texts + other_texts)
    return combined[:config.HAIKU_TEXT_LIMIT]


def _extract_with_haiku(pages: list, api_key: str) -> list[ExtractedPerson]:
    """
    Use Claude Haiku to extract decision maker candidates from page text.
    Only called when use_ai=True and no strong deterministic candidate was found.

    Returns a list of ExtractedPerson objects that enter the normal scoring pool.
    """
    if not api_key:
        return []

    text = _build_haiku_text(pages)
    if len(text) < config.HAIKU_MIN_TEXT_LENGTH:
        return []

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = (
            "Look at this company website text and identify the most senior decision makers "
            "(Owner, Founder, CEO, President, Managing Director, Partner, etc.).\n\n"
            "Return ONLY valid JSON — no other text. Use this exact format:\n"
            "{\n"
            '  "candidates": [\n'
            '    {"name": "Full Name", "title": "Job Title", "confidence": "high|medium|low", '
            '"reason": "one sentence why"}\n'
            "  ],\n"
            '  "best_choice": {"name": "Full Name", "title": "Job Title", '
            '"confidence": "high|medium|low", "reason": "one sentence why"}\n'
            "}\n\n"
            "Rules:\n"
            "- Only include real named people with leadership titles\n"
            "- Do not include support staff, developers, or admin roles\n"
            "- If no decision maker is found, return empty candidates list and "
            'best_choice with empty name and title\n'
            "- Maximum 3 candidates\n\n"
            "Website text:\n" + text
        )

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()

        # Strip markdown code fences if present
        raw = raw.strip("`").strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

        data = json.loads(raw)
        candidates_data = data.get("candidates", [])

        # Also include best_choice if not already in candidates
        best = data.get("best_choice", {})
        if best.get("name") and not any(
            c.get("name", "").lower() == best["name"].lower()
            for c in candidates_data
        ):
            candidates_data.append(best)

        persons = []
        for c in candidates_data:
            name = str(c.get("name", "")).strip()
            title = str(c.get("title", "")).strip()
            haiku_confidence = str(c.get("confidence", "medium")).strip()
            reason = str(c.get("reason", "")).strip()

            if name and len(name.split()) >= 2 and title:
                p = ExtractedPerson(
                    full_name=clean_name_for_display(name),
                    title=title,
                    source="claude_haiku",
                    confidence=haiku_confidence,
                    matched_page="haiku_fallback",
                    matched_snippet=reason[:150],
                )
                # Score using the best priority page we have
                best_priority_path = next(
                    (pg.page_path for pg in pages if pg.page_path in config.PRIORITY_PAGES),
                    "/"
                )
                _score_candidate(
                    p, best_priority_path,
                    has_generic_email_nearby=False,
                    in_heading_context=False,
                    name_title_close=True,
                )
                persons.append(p)

        return persons

    except Exception:
        # Silently fail — never crash the whole pipeline on a Haiku error
        return []


def _build_openrouter_text(pages: list) -> str:
    """
    Extract the most relevant page text to send to OpenRouter.
    Uses a stricter character cap than Haiku to reduce token cost.
    """
    priority_texts = []
    other_texts = []
    for page in pages:
        if not page.text:
            continue
        if page.page_path in config.PRIORITY_PAGES:
            priority_texts.append(page.text)
        else:
            other_texts.append(page.text)

    combined = " ".join(priority_texts + other_texts)
    return combined[:config.OPENROUTER_TEXT_LIMIT]


def _extract_with_openrouter(pages: list, api_key: str) -> list[ExtractedPerson]:
    """
    Use OpenRouter GPT-4o Mini to extract decision maker candidates from page text.
    Uses an OpenAI-compatible client pointed at https://openrouter.ai/api/v1.
    Only called when use_ai=True, ai_provider="openrouter", and score is below threshold.
    Returns a list of ExtractedPerson objects that enter the normal scoring pool.
    """
    if not api_key:
        return []

    text = _build_openrouter_text(pages)
    if len(text) < config.HAIKU_MIN_TEXT_LENGTH:
        return []

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )

        prompt = (
            "Look at this company website text and identify the most senior decision makers "
            "(Owner, Founder, CEO, President, Managing Director, Partner, etc.).\n\n"
            "Return ONLY valid JSON — no other text. Use this exact format:\n"
            "{\n"
            '  "candidates": [\n'
            '    {"name": "Full Name", "title": "Job Title", "confidence": "high|medium|low", '
            '"reason": "one sentence why"}\n'
            "  ],\n"
            '  "best_choice": {"name": "Full Name", "title": "Job Title", '
            '"confidence": "high|medium|low", "reason": "one sentence why"}\n'
            "}\n\n"
            "Rules:\n"
            "- Only include real named people with leadership titles\n"
            "- Do not include support staff, developers, or admin roles\n"
            "- If no decision maker is found, return empty candidates list and "
            'best_choice with empty name and title\n'
            "- Maximum 3 candidates\n\n"
            "Website text:\n" + text
        )

        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        raw = raw.strip("`").strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

        data = json.loads(raw)
        candidates_data = data.get("candidates", [])

        best = data.get("best_choice", {})
        if best.get("name") and not any(
            c.get("name", "").lower() == best["name"].lower()
            for c in candidates_data
        ):
            candidates_data.append(best)

        persons = []
        for c in candidates_data:
            name = str(c.get("name", "")).strip()
            title = str(c.get("title", "")).strip()
            or_confidence = str(c.get("confidence", "medium")).strip()
            reason = str(c.get("reason", "")).strip()

            if name and len(name.split()) >= 2 and title:
                p = ExtractedPerson(
                    full_name=clean_name_for_display(name),
                    title=title,
                    source="openrouter",
                    confidence=or_confidence,
                    matched_page="openrouter_fallback",
                    matched_snippet=reason[:150],
                )
                best_priority_path = next(
                    (pg.page_path for pg in pages if pg.page_path in config.PRIORITY_PAGES),
                    "/"
                )
                _score_candidate(
                    p, best_priority_path,
                    has_generic_email_nearby=False,
                    in_heading_context=False,
                    name_title_close=True,
                )
                persons.append(p)

        return persons

    except Exception:
        # Silently fail — never crash the whole pipeline on an OpenRouter error
        return []


def _extract_with_ai(pages: list, api_key: str, ai_provider: str) -> list[ExtractedPerson]:
    """
    Dispatcher: calls the correct AI backend based on ai_provider.
    ai_provider: "claude" → Anthropic Haiku  |  "openrouter" → OpenRouter GPT-4o Mini
    """
    if ai_provider == "openrouter":
        return _extract_with_openrouter(pages, api_key)
    return _extract_with_haiku(pages, api_key)


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

def _deduplicate_persons(persons: list[ExtractedPerson]) -> list[ExtractedPerson]:
    """Remove duplicate names (case-insensitive), keeping first occurrence."""
    seen = set()
    result = []
    for p in persons:
        key = p.full_name.lower().strip()
        if key and key not in seen:
            seen.add(key)
            result.append(p)
    return result


def _build_snippet(page_text: str, name: str, title: str, max_len: int = 150) -> str:
    """
    Build a short context snippet around where name or title appears in the page text.
    Falls back to empty string if neither found.
    """
    target = name if name else title
    idx = page_text.lower().find(target.lower())
    if idx == -1:
        idx = page_text.lower().find(title.lower())
    if idx == -1:
        return ""
    start = max(0, idx - 60)
    end = min(len(page_text), idx + max_len)
    return page_text[start:end].strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_from_pages(
    pages: list,
    use_ai: bool = False,
    api_key: str = "",
    ai_provider: str = "claude",
) -> ExtractionResult:
    """
    Run the full extraction pipeline across all fetched pages.

    For each page, runs strategies 1–3 in order. If schema.org finds a Tier 1
    person, returns immediately without checking further pages (early exit for
    high-confidence structured data).

    After all deterministic strategies complete, if no candidate meets the
    strong score threshold AND use_ai=True, the configured AI provider is called
    once as a final fallback.

    Returns an ExtractionResult with the highest-scoring primary candidate,
    an optional backup, and a full list of all candidates for debugging.
    """
    result = ExtractionResult()
    all_candidates: list[ExtractedPerson] = []

    if not pages:
        result.notes = "No pages to analyse"
        return result

    # -----------------------------------------------------------------------
    # Phase 1: deterministic extraction across all pages
    # -----------------------------------------------------------------------
    for page in pages:
        if not page.html and not page.text:
            continue

        try:
            soup = BeautifulSoup(page.html, "lxml") if page.html else None
        except Exception:
            soup = None

        page_path = page.page_path or "/"

        # Strategy 1: schema.org JSON-LD
        if soup:
            schema_persons = _extract_schema_org(soup, page_path)
            for sp in schema_persons:
                _score_candidate(
                    sp, page_path,
                    has_generic_email_nearby=_has_generic_email_nearby(page.text),
                    in_heading_context=True,   # structured data = high context
                    name_title_close=True,
                )
                all_candidates.append(sp)

            # Early exit: if schema.org found a Tier 1 decision maker, use it now
            tier1_schema = [
                p for p in schema_persons
                if p.contact_seniority == "decision_maker"
                and p.score >= config.SCORE_THRESHOLD_STRONG
            ]
            if tier1_schema:
                best = max(tier1_schema, key=lambda p: p.score)
                result.primary = best
                result.all_candidates = [best]
                return result

        # Strategy 2: HTML team cards
        if soup:
            card_persons = _extract_html_cards(soup, page_path, page.text)
            for cp in card_persons:
                if not any(p.full_name.lower() == cp.full_name.lower() for p in all_candidates):
                    all_candidates.append(cp)

        # Strategy 3: Plain text regex
        if page.text:
            regex_persons = _extract_text_regex(page.text, page_path)
            existing_names = {p.full_name.lower() for p in all_candidates}
            for rp in regex_persons:
                if rp.full_name.lower() not in existing_names:
                    all_candidates.append(rp)

        # Strategy 5: Footer / copyright / image-alt / "founded by" parsing
        if soup and page.text:
            footer_persons = _extract_footer_copyright(soup, page.text, page_path)
            existing_names = {p.full_name.lower() for p in all_candidates}
            for fp in footer_persons:
                if fp.full_name.lower() not in existing_names:
                    all_candidates.append(fp)

    # -----------------------------------------------------------------------
    # Phase 2: AI fallback (only if no strong deterministic match found)
    # -----------------------------------------------------------------------
    best_deterministic_score = max((p.score for p in all_candidates), default=0)
    if use_ai and api_key and best_deterministic_score < config.SCORE_THRESHOLD_AI:
        ai_persons = _extract_with_ai(pages, api_key, ai_provider)
        existing_names = {p.full_name.lower() for p in all_candidates}
        for ap in ai_persons:
            if ap.full_name.lower() not in existing_names:
                all_candidates.append(ap)

    # -----------------------------------------------------------------------
    # Phase 3: select primary and backup from all scored candidates
    # -----------------------------------------------------------------------
    if not all_candidates:
        result.notes = "No decision maker found via any strategy"
        result.all_candidates = []
        return result

    # Sort by score descending
    all_candidates.sort(key=lambda p: p.score, reverse=True)
    result.all_candidates = all_candidates

    primary = all_candidates[0]

    # Only assign a result if score clears the minimum threshold
    if primary.score >= config.SCORE_THRESHOLD_WEAK:
        result.primary = primary
        if len(all_candidates) > 1:
            result.backup = all_candidates[1]
    else:
        result.notes = (
            f"Best candidate score ({primary.score}) below threshold "
            f"({config.SCORE_THRESHOLD_WEAK}) — not confident enough to use"
        )

    return result


# ---------------------------------------------------------------------------
# External source helper — creates a scored person from Yelp / DDG results
# ---------------------------------------------------------------------------

def create_person_from_external(
    name: str,
    title: str,
    source: str,
    snippet: str = "",
) -> ExtractedPerson:
    """
    Create a scored ExtractedPerson from an external data source (Yelp, DDG, etc.).
    Used by main.py to integrate searcher results into the candidate selection.

    External results are scored like a text-regex hit on a non-priority page:
      - Tier 1 title (owner/CEO/founder) → score ~45  → ok_weak
      - Tier 2 title (manager/director)  → score ~25  → ok_weak
    This is intentional — external sources are useful but warrant verification.

    Args:
        name:    Full name string (will be cleaned for display)
        title:   Job title string
        source:  Label for the source, e.g. "yelp_search" or "ddg_search"
        snippet: Short text context where the name was found (optional)

    Returns an ExtractedPerson with score, seniority, and score_reason set.
    Returns a very low-scored sentinel (score=-999) if the name fails person
    validation — so callers can safely add it to the candidate pool and it
    will never be selected as the primary result.
    """
    if not _is_plausible_person_name(name):
        sentinel = ExtractedPerson(
            full_name="",
            title="",
            source=source,
            confidence="low",
            matched_page=source,
        )
        sentinel.score = -999
        sentinel.score_reason = ["rejected: name failed person validation"]
        return sentinel

    p = ExtractedPerson(
        full_name=clean_name_for_display(name),
        title=title,
        source=source,
        confidence="medium",
        matched_page=source,       # e.g. "yelp_search" / "ddg_search"
        matched_snippet=snippet[:150] if snippet else "",
    )
    # Score as if found on a non-priority page (no +20 bonus)
    _score_candidate(
        p,
        page_path="/",             # neutral — not a priority page
        has_generic_email_nearby=False,
        in_heading_context=False,
        name_title_close=True,     # name + title appeared close together
    )
    return p


# ---------------------------------------------------------------------------
# Legacy shim — keeps app.py / any old callers working
# ---------------------------------------------------------------------------

def extract_decision_maker(
    html: str,
    text: str,
    use_ai: bool = False,
    api_key: str = "",
    ai_provider: str = "claude",
) -> ExtractionResult:
    """
    Backward-compatibility wrapper around extract_from_pages().
    Creates a single synthetic PageResult from the provided html/text.
    New code should call extract_from_pages() directly.
    """
    from scraper import PageResult
    page = PageResult(url="", page_path="/", html=html, text=text, website_status="ok")
    return extract_from_pages([page], use_ai=use_ai, api_key=api_key, ai_provider=ai_provider)
