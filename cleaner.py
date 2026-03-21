# cleaner.py
# Data cleaning utilities for domains, websites, and person names.

import re
import unicodedata


# ---------------------------------------------------------------------------
# Domain / URL cleaning
# ---------------------------------------------------------------------------

def clean_domain(raw: str) -> str:
    """
    Normalise a raw domain or URL to a plain domain string.

    Examples:
        "https://www.company.com/"  →  "company.com"
        " COMPANY.COM "             →  "company.com"
        "http://company.com/about"  →  "company.com"
    """
    if not raw:
        return ""
    domain = raw.strip().lower()
    # Strip protocol
    domain = re.sub(r"^https?://", "", domain)
    # Strip www.
    domain = re.sub(r"^www\.", "", domain)
    # Strip path, query, fragment
    domain = domain.split("/")[0].split("?")[0].split("#")[0]
    # Remove spaces inside the domain
    domain = domain.replace(" ", "")
    return domain


def derive_domain_from_website(website: str) -> str:
    """Extract just the domain from a full website URL."""
    return clean_domain(website)


def clean_website(raw: str) -> str:
    """
    Return a normalised https:// URL without trailing slash.
    Used when building page URLs to scrape.
    """
    if not raw:
        return ""
    url = raw.strip()
    # Remove trailing slash
    url = url.rstrip("/")
    # Ensure it has a scheme
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url


def domain_to_base_url(domain: str) -> str:
    """Turn a plain domain into a base https:// URL."""
    domain = clean_domain(domain)
    if not domain:
        return ""
    return "https://" + domain


# ---------------------------------------------------------------------------
# Name cleaning
# ---------------------------------------------------------------------------

def clean_name_for_display(raw: str) -> str:
    """
    Return a properly title-cased name for display.

    "  john   smith " → "John Smith"
    """
    if not raw:
        return ""
    return " ".join(raw.strip().split()).title()


def clean_name_for_email(raw: str) -> str:
    """
    Return a fully normalised name fragment suitable for email generation.

    Rules:
    - Lowercase
    - Remove apostrophes              ("O'Neil"      → "oneil")
    - Collapse hyphens into nothing   ("Smith-Jones" → "smithjones")
    - Strip non-alphanumeric chars
    - Collapse whitespace

    Returns "" if nothing usable remains.
    """
    if not raw:
        return ""

    name = raw.strip().lower()

    # Normalise unicode (é → e, ü → u, etc.)
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")

    # Remove apostrophes
    name = name.replace("'", "")

    # Collapse hyphens (Smith-Jones → smithjones)
    name = name.replace("-", "")

    # Remove any remaining non-alphanumeric, non-space characters
    name = re.sub(r"[^a-z0-9 ]", "", name)

    # Collapse whitespace
    name = " ".join(name.split())

    return name


def split_full_name(full_name_cleaned: str) -> tuple[str, str]:
    """
    Split a cleaned (lowercased, normalised) full name into (first, last).

    For names with more than 2 parts, takes the first part as first name
    and the last part as last name (ignores middle names/initials).

    Returns ("", "") if input is empty or unparseable.
    """
    if not full_name_cleaned:
        return ("", "")
    parts = full_name_cleaned.split()
    if len(parts) == 0:
        return ("", "")
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], parts[-1])


# ---------------------------------------------------------------------------
# Row / field cleaning
# ---------------------------------------------------------------------------

def clean_row(row: dict) -> dict:
    """
    Return a copy of the row with all string fields trimmed.
    Domain and website fields are fully normalised.
    """
    cleaned = {}
    for key, value in row.items():
        if isinstance(value, str):
            cleaned[key] = value.strip()
        else:
            cleaned[key] = value

    # Special handling for domain and website
    raw_domain = cleaned.get("domain", "")
    raw_website = cleaned.get("website", "")

    domain_cleaned = clean_domain(raw_domain) if raw_domain else clean_domain(raw_website)
    cleaned["domain"] = domain_cleaned
    cleaned["domain_cleaned"] = domain_cleaned

    if raw_website:
        cleaned["website"] = clean_website(raw_website)

    return cleaned


def deduplicate_rows(rows: list[dict], key: str = "domain_cleaned") -> list[dict]:
    """
    Remove duplicate rows based on `key` field.
    Keeps the first occurrence. Blank-key rows are always kept.
    """
    seen = set()
    result = []
    for row in rows:
        k = row.get(key, "").strip().lower()
        if not k:
            result.append(row)
        elif k not in seen:
            seen.add(k)
            result.append(row)
    return result
