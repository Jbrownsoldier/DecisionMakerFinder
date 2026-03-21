# verifier.py
# Free domain validation using DNS MX record checks + WHOIS email lookup.
#
# check_mx(): Checks whether a domain has active mail servers before email
#   candidates are generated. Dead/parked domains return "no" immediately,
#   saving Lumlist credits by flagging guaranteed-invalid emails upfront.
#   Requires: dnspython  (pip install dnspython)
#
# check_whois_email(): Looks up the domain WHOIS registrant email. If the
#   registrant used a company-domain email (e.g. jsmith@acme.com), we extract
#   it as a real named email example to improve pattern detection.
#   Requires: python-whois  (pip3 install python-whois)
#   Hit rate: ~20-30% for small businesses without WHOIS privacy protection.

import dns.resolver
import dns.exception


def check_mx(domain: str) -> dict:
    """
    Check whether a domain has active MX records (mail servers configured).

    Returns:
        {
            "mx_valid":  "yes" | "no" | "error",
            "mx_host":   str   # primary MX hostname, or "" if none
        }

    "yes"   = at least one MX record found → domain can receive email
    "no"    = domain exists but has no MX records → emails will bounce
    "error" = DNS lookup failed (timeout, NXDOMAIN, network issue, etc.)

    Never raises — all exceptions are caught and returned as {"mx_valid": "error"}.
    Speed: ~100ms per call (pure DNS, no HTTP).
    """
    if not domain or not isinstance(domain, str):
        return {"mx_valid": "error", "mx_host": ""}

    domain = domain.strip().lower().rstrip(".")

    # Reject obviously invalid inputs
    if "." not in domain or len(domain) < 4:
        return {"mx_valid": "error", "mx_host": ""}

    try:
        records = dns.resolver.resolve(domain, "MX", lifetime=5.0)

        # Sort by preference number (lowest = highest priority mail server)
        sorted_recs = sorted(records, key=lambda r: r.preference)
        primary_host = str(sorted_recs[0].exchange).rstrip(".")

        return {"mx_valid": "yes", "mx_host": primary_host}

    except dns.resolver.NXDOMAIN:
        # Domain does not exist in DNS at all
        return {"mx_valid": "no", "mx_host": ""}

    except dns.resolver.NoAnswer:
        # Domain exists but has no MX records published
        return {"mx_valid": "no", "mx_host": ""}

    except dns.resolver.Timeout:
        return {"mx_valid": "error", "mx_host": ""}

    except dns.exception.DNSException:
        return {"mx_valid": "error", "mx_host": ""}

    except Exception:
        return {"mx_valid": "error", "mx_host": ""}


def check_whois_email(domain: str) -> dict:
    """
    Look up the domain WHOIS registrant/admin email address.

    When someone registers a domain they provide a contact email. For many
    small businesses this is a personal or business email at their own domain
    (e.g. jsmith@acmeplumbing.com). Extracting this gives us:
      - A real named email example to improve detect_email_pattern()
      - A local-part name hint (e.g. "jsmith" → first initial + last name)
      - An inferred email pattern (e.g. "flast")

    Args:
        domain: The company domain to look up (e.g. "acmeplumbing.com")

    Returns:
        {
            "whois_email_found":      "yes" | "no",
            "whois_email_hint":       str   # e.g. "jsmith@acmeplumbing.com"
            "whois_inferred_pattern": str   # e.g. "flast"
            "whois_name_hint":        str   # local part e.g. "jsmith"
            "_raw_emails":            list  # internal: list of found email strings
        }

    "no"  = domain has WHOIS privacy or registrant used a generic/third-party email
    Never raises — all exceptions caught and returned as empty result.
    Requires: pip3 install python-whois
    """
    empty = {
        "whois_email_found": "no",
        "whois_email_hint": "",
        "whois_inferred_pattern": "",
        "whois_name_hint": "",
        "_raw_emails": [],
    }

    if not domain or not isinstance(domain, str):
        return empty

    domain = domain.strip().lower().rstrip(".")

    if "." not in domain or len(domain) < 4:
        return empty

    try:
        import whois  # type: ignore  # pip3 install python-whois

        w = whois.whois(domain)

        raw_emails = w.emails or []
        if isinstance(raw_emails, str):
            raw_emails = [raw_emails]

        # Look for an email address at the company's OWN domain
        # (not privacy@godaddy.com or similar registrar proxies)
        company_emails = []
        for email in raw_emails:
            if not email or "@" not in email:
                continue
            email = email.strip().lower()
            local, email_domain = email.split("@", 1)
            email_domain = email_domain.rstrip(".")
            if email_domain == domain:
                company_emails.append(email)

        if not company_emails:
            return empty

        # Use the first company-domain email found
        best_email = company_emails[0]
        local = best_email.split("@")[0]

        # Infer pattern from the local part
        from email_gen import _classify_email_pattern  # local import avoids circular dep
        pattern = _classify_email_pattern(local)

        return {
            "whois_email_found": "yes",
            "whois_email_hint": best_email,
            "whois_inferred_pattern": pattern if pattern != "unknown" else "",
            "whois_name_hint": local,
            "_raw_emails": company_emails,
        }

    except Exception:
        return empty


# V7: Re-export SMTP verification functions so callers only need one import
from smtp_verify import check_smtp, detect_catch_all, verify_candidate_list  # noqa: F401, E402
