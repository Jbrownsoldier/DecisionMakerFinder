# email_gen.py
# V3: Generates professional email candidates with real pattern detection.
#
# New in V3:
#   - detect_email_pattern(): scans page HTML/text for real named emails
#     and infers the company's likely email format
#   - generate_email_candidates(): now accepts a detected_pattern argument
#     and puts the detected format first in the candidate list
#   - Returns a dict with metadata fields (pattern, confidence, reason)

import re
from typing import Optional
from config import GENERIC_FALLBACK_EMAILS, GENERIC_EMAIL_PREFIXES


# ---------------------------------------------------------------------------
# Email pattern detection
# ---------------------------------------------------------------------------

# Matches any email address in text
_EMAIL_RE = re.compile(r"\b([a-zA-Z0-9._%+\-]+)@([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b")


def _classify_email_pattern(local: str) -> str:
    """
    Given the local part of an email (before @), return a pattern name.

    Examples:
        "john"          → "first"
        "john.smith"    → "first.last"
        "johnsmith"     → "firstlast"
        "jsmith"        → "flast"
        "j.smith"       → "f.last"
        "johns"         → "firstl"  (ambiguous — first + last initial)
        "smith"         → "last"
        "smith.john"    → "last.first"
        "john_smith"    → "first_last"
        "john-smith"    → "first-last"
    """
    local = local.lower().strip()

    if "." in local:
        parts = local.split(".", 1)
        a, b = parts[0], parts[1]
        if len(a) == 1 and len(b) > 1:
            return "f.last"           # j.smith
        if len(a) > 1 and len(b) == 1:
            return "firstl"           # john.s
        if len(a) > 1 and len(b) > 1:
            # Could be first.last or last.first — first.last is far more common
            return "first.last"

    if "_" in local:
        parts = local.split("_", 1)
        if len(parts) == 2 and len(parts[0]) > 1 and len(parts[1]) > 1:
            return "first_last"

    if "-" in local:
        parts = local.split("-", 1)
        if len(parts) == 2 and len(parts[0]) > 1 and len(parts[1]) > 1:
            return "first-last"

    # No separator — classify by length heuristics
    if len(local) <= 2:
        return "unknown"
    if len(local) <= 6:
        # Could be "jsmith" (flast) or short first name
        # flast typically = 1 char + surname (>= 4 chars total)
        if len(local) >= 4:
            return "flast"
        return "first"
    # Longer: probably firstlast concatenation
    return "firstlast"


def detect_email_pattern(pages: list, extra_emails: Optional[list] = None) -> dict:
    """
    Scan all fetched pages for real email addresses, filter out generic ones,
    and infer the company's most likely email pattern from named addresses.

    V5: `extra_emails` accepts additional real email strings discovered via
    web search (DDG domain search), GitHub code search, or WHOIS lookup.
    These supplement on-site scanning to dramatically improve pattern detection
    for companies that don't expose staff emails on their own website.

    Args:
        pages:        list of PageResult objects from scraper.py
        extra_emails: list of real email strings, e.g. ["jsmith@acme.com"]
                      Pass None or [] to use only on-site emails (V4 behaviour).

    Returns:
        {
            "pattern": str,       # e.g. "first.last", "flast", "" if unknown
            "confidence": str,    # "high" (3+), "medium" (2), "low" (1), "none"
            "examples": list,     # up to 3 real emails found as evidence
            "reason": str,        # human-readable explanation
        }
    """
    pattern_votes: dict[str, int] = {}
    examples: list[str] = []

    def _process_email(local: str, domain_part: str) -> None:
        """Score a single email's local part and record its pattern."""
        full_email = f"{local}@{domain_part}"
        if local in GENERIC_EMAIL_PREFIXES:
            return
        if any(x in local for x in ("noreply", "no-reply", "webmaster", "bounce")):
            return
        if len(local) < 2:
            return
        pattern = _classify_email_pattern(local)
        if pattern == "unknown":
            return
        pattern_votes[pattern] = pattern_votes.get(pattern, 0) + 1
        if len(examples) < 3 and full_email not in examples:
            examples.append(full_email)

    # Scan on-site pages (HTML catches mailto: links; text catches plain-text emails)
    for page in pages:
        content = (page.html or "") + " " + (page.text or "")
        for m in _EMAIL_RE.finditer(content):
            _process_email(m.group(1).lower(), m.group(2).lower())

    # V5: Process extra emails from web/WHOIS/GitHub discovery
    if extra_emails:
        for email in extra_emails:
            if "@" not in email:
                continue
            parts = email.lower().split("@", 1)
            _process_email(parts[0], parts[1])

    if not pattern_votes:
        no_reason = "No named emails found on site"
        if extra_emails:
            no_reason = "No named emails found on site or via web/WHOIS search"
        return {"pattern": "", "confidence": "none", "examples": [], "reason": no_reason}

    # Pick the most common pattern
    best_pattern = max(pattern_votes, key=lambda k: pattern_votes[k])
    vote_count = pattern_votes[best_pattern]

    if vote_count >= 3:
        confidence = "high"
    elif vote_count == 2:
        confidence = "medium"
    else:
        confidence = "low"

    # Describe where the evidence came from
    on_site_count = sum(pattern_votes.values()) - (len(extra_emails or []))
    source_desc = "on site"
    if extra_emails and on_site_count <= 0:
        source_desc = "via web/WHOIS search"
    elif extra_emails:
        source_desc = "on site + web/WHOIS search"

    reason = (
        f"Detected pattern '{best_pattern}' from {vote_count} named email(s) {source_desc} "
        f"(e.g. {examples[0]})"
        if examples else f"Detected pattern '{best_pattern}' from {vote_count} email(s)"
    )

    return {
        "pattern": best_pattern,
        "confidence": confidence,
        "examples": examples,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Email candidate generation
# ---------------------------------------------------------------------------

# Map pattern names to the index in the standard 10-pattern list
# so we can reorder candidates to put the detected pattern first.
_PATTERN_INDEX = {
    "first":      0,   # john@domain.com
    "first.last": 1,   # john.smith@domain.com
    "firstlast":  2,   # johnsmith@domain.com
    "flast":      3,   # jsmith@domain.com
    "firstl":     4,   # johns@domain.com
    "last":       5,   # smith@domain.com
    "last.first": 6,   # smith.john@domain.com
    "first_last": 7,   # john_smith@domain.com
    "f.last":     8,   # j.smith@domain.com
    "first-last": 9,   # john-smith@domain.com
}


def generate_email_candidates(
    first: str,
    last: str,
    domain: str,
    detected_pattern: str = "",
) -> dict:
    """
    Generate up to 10 email address candidates from cleaned name parts and domain.

    `first` and `last` must already be lowercased and cleaned (no special chars).
    `domain` must be a plain domain like "company.com" (no https://).
    `detected_pattern` is the pattern name returned by detect_email_pattern().

    Returns a dict:
    {
        "candidates": list[str],        # up to 10 email candidates
        "primary_guess": str,           # the single best guess
        "primary_guess_reason": str,    # why this was chosen as primary
    }
    """
    if not domain:
        return {"candidates": [], "primary_guess": "", "primary_guess_reason": "no domain"}

    if not first:
        return {"candidates": [], "primary_guess": "", "primary_guess_reason": "no name"}

    # Build the standard 10 patterns
    if first and last:
        f, l = first, last
        fi = f[0]   # first initial
        li = l[0]   # last initial

        standard_patterns = [
            f"{f}@{domain}",            # 0: first
            f"{f}.{l}@{domain}",        # 1: first.last
            f"{f}{l}@{domain}",         # 2: firstlast
            f"{fi}{l}@{domain}",        # 3: flast
            f"{f}{li}@{domain}",        # 4: firstl
            f"{l}@{domain}",            # 5: last
            f"{l}.{f}@{domain}",        # 6: last.first
            f"{f}_{l}@{domain}",        # 7: first_last
            f"{fi}.{l}@{domain}",       # 8: f.last
            f"{f}-{l}@{domain}",        # 9: first-last
        ]
    else:
        # First name only
        f = first
        standard_patterns = [
            f"{f}@{domain}",
            f"info@{domain}",
            f"contact@{domain}",
        ]
        # No detected pattern logic for single names
        candidates = _dedup_list(standard_patterns)[:10]
        primary = candidates[0] if candidates else ""
        return {
            "candidates": candidates,
            "primary_guess": primary,
            "primary_guess_reason": "first name only — limited patterns available",
        }

    # Reorder so the detected pattern appears first
    primary_guess_reason = ""
    if detected_pattern and detected_pattern in _PATTERN_INDEX:
        idx = _PATTERN_INDEX[detected_pattern]
        # Move the detected pattern to the front
        detected_email = standard_patterns[idx]
        reordered = [detected_email] + [
            e for i, e in enumerate(standard_patterns) if i != idx
        ]
        primary_guess_reason = f"pattern '{detected_pattern}' detected from real emails on site"
    else:
        reordered = standard_patterns
        # Default: first.last (index 1) is the most common professional pattern
        primary_guess_reason = "default — first.last is most common business pattern"

    candidates = _dedup_list(reordered)[:10]
    primary = candidates[0] if candidates else ""

    return {
        "candidates": candidates,
        "primary_guess": primary,
        "primary_guess_reason": primary_guess_reason,
    }


def _dedup_list(lst: list[str]) -> list[str]:
    """Deduplicate a list preserving order."""
    seen = set()
    result = []
    for item in lst:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# Generic fallback emails (no named person found)
# ---------------------------------------------------------------------------

def generate_generic_fallback_emails(domain: str) -> list[str]:
    """
    Return generic role-based addresses when no named decision maker was found.
    These are clearly labelled in `notes` by the caller.
    """
    if not domain:
        return []
    return [f"{prefix}@{domain}" for prefix in GENERIC_FALLBACK_EMAILS]


# ---------------------------------------------------------------------------
# Column helpers (kept for output compatibility)
# ---------------------------------------------------------------------------

def find_direct_email_match(
    first: str,
    last: str,
    emails: list,
) -> Optional[str]:
    """
    V7: Check whether any email in `emails` contains the decision maker's name.

    Strips separators (. _ - +) from the email local part before comparing, so
    "john.smith@acme.com", "johnsmith@acme.com", and "j.smith@acme.com" all match
    a decision maker named "John Smith".

    Supported match patterns (first=john, last=smith):
        john.smith   johnsmith   j.smith   jsmith   johns
        (and all the above without separators)

    Args:
        first:  Decision maker's first name (already cleaned/lowercased)
        last:   Decision maker's last name  (already cleaned/lowercased)
        emails: List of real email strings from WHOIS / web discovery / GitHub

    Returns:
        The matched email (lowercased), or None if no match found.
    """
    import re as _re

    if not first or not last or not emails:
        return None

    # Normalise: letters only, lowercase
    f = _re.sub(r"[^a-z]", "", first.lower())
    l = _re.sub(r"[^a-z]", "", last.lower())  # noqa: E741

    if not f or not l or len(f) < 2 or len(l) < 2:
        return None

    fi = f[0]  # first initial

    # Canonical local-part patterns we accept (separators stripped during comparison)
    valid_patterns_stripped = {
        f"{f}{l}",       # johnsmith
        f"{fi}{l}",      # jsmith
        f"{f}{l[0]}",    # johns
        f"{l}{f}",       # smithjohn (uncommon but valid)
    }

    for email in emails:
        if not email or "@" not in email:
            continue
        local = email.lower().split("@")[0]
        # Strip common separators to normalise (john.smith → johnsmith)
        local_stripped = _re.sub(r"[._\-+]", "", local)

        if local_stripped in valid_patterns_stripped:
            return email.lower()

    return None


def pick_primary_guess(candidates: list[str]) -> str:
    """
    Legacy helper. Returns candidates[1] (first.last pattern) as primary,
    or candidates[0] if fewer than 2.
    New code should use the primary_guess field from generate_email_candidates().
    """
    if not candidates:
        return ""
    if len(candidates) >= 2:
        return candidates[1]
    return candidates[0]


def candidates_to_columns(candidates: list[str], n: int = 10) -> dict:
    """
    Convert a flat list of candidate emails into a dict of
    candidate_1 … candidate_N columns, padding with "" if fewer than N.
    """
    result = {}
    for i in range(1, n + 1):
        result[f"candidate_{i}"] = candidates[i - 1] if i <= len(candidates) else ""
    return result
