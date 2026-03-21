#!/usr/bin/env python3
# main.py
# Decision Maker Email Finder — core pipeline.
#
# V4 changes (free additions):
#   - MX record validation via verifier.check_mx()
#   - Yelp business owner search via searcher.search_yelp()
#   - DuckDuckGo SERP search via searcher.search_ddg()
#   - External source results merged into decision-maker selection
#   - 7 new output columns: mx_valid, yelp_owner_found, yelp_owner_name,
#     yelp_source_url, serp_person_found, serp_decision_maker, serp_snippet
#   - process_row() and run_pipeline() accept use_yelp / use_ddg flags
#
# CLI usage:
#   python main.py --input input.csv --output output.csv
#   python main.py --input input.csv --output output.csv --resume
#   python main.py --input input.csv --output output.csv --use-ai --api-key sk-ant-...
#   python main.py --input input.csv --output output.csv --no-yelp --no-ddg
#
# Also importable by app.py (Flask web UI) via run_pipeline().

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from tqdm import tqdm

import config
from cleaner import clean_row, deduplicate_rows, clean_name_for_email, split_full_name
from scraper import scrape_company
from extractor import extract_from_pages, create_person_from_external
from email_gen import (
    detect_email_pattern,
    generate_email_candidates,
    generate_generic_fallback_emails,
    candidates_to_columns,
    find_direct_email_match,
)
from verifier import check_mx, check_whois_email, detect_catch_all, verify_candidate_list
from searcher import (
    search_yelp, search_ddg, search_bbb,
    search_domain_emails_web, search_github_emails,
    search_homestars, search_yellowpages_ca, search_google_maps_owner,
    search_linkedin_google,
)


# ---------------------------------------------------------------------------
# Output column order
# Original 33 columns → V3 11 columns → V4 7 new columns → V5 10 new columns.
# ---------------------------------------------------------------------------

OUTPUT_COLUMNS = [
    # --- Original company fields ---
    "company_name",
    "domain",
    "domain_cleaned",
    "website",
    "city",
    "state",
    "country",
    "niche",
    # --- Scrape metadata ---
    "website_status",
    # --- Decision maker ---
    "decision_maker_full_name",
    "decision_maker_first_name",
    "decision_maker_last_name",
    "decision_maker_title",
    "decision_maker_source",
    "decision_maker_confidence",
    "full_name_cleaned",
    # --- Backup ---
    "backup_decision_maker",
    "backup_title",
    # --- Email candidates ---
    "candidate_1",
    "candidate_2",
    "candidate_3",
    "candidate_4",
    "candidate_5",
    "candidate_6",
    "candidate_7",
    "candidate_8",
    "candidate_9",
    "candidate_10",
    "primary_guess",
    # --- Status ---
    "processing_status",
    "notes",
    "generated_at",
    # --- V3 scoring columns ---
    "contact_seniority",
    "selected_score",
    "score_reason",
    "matched_strategy",
    "matched_page",
    "matched_snippet",
    "observed_email_pattern",
    "pattern_confidence",
    "primary_guess_reason",
    "should_verify",
    "verify_priority",
    # --- V4 free source columns ---
    "mx_valid",             # yes | no | error  (DNS MX check result)
    "yelp_owner_found",     # yes | no
    "yelp_owner_name",      # name found on Yelp listing
    "yelp_source_url",      # URL of the Yelp business page
    "serp_person_found",    # yes | no
    "serp_decision_maker",  # name found in DuckDuckGo SERP snippet
    "serp_snippet",         # raw snippet text where name was found
    # --- V5 JS rendering ---
    "js_rendered",          # yes | no  (was Playwright used to render any page)
    # --- V5 BBB ---
    "bbb_owner_found",      # yes | no
    "bbb_owner_name",       # verified principal name from BBB listing
    "bbb_source_url",       # URL of the BBB business listing page
    # --- V5 WHOIS ---
    "whois_email_found",    # yes | no
    "whois_email_hint",     # registrant email if at company's own domain
    "whois_inferred_pattern",  # email pattern inferred from WHOIS email
    # --- V5 web email discovery ---
    "web_email_found",      # yes | no  (emails found via DDG/GitHub domain search)
    "web_email_examples",   # comma-separated real emails found (up to 3)
    "web_inferred_pattern", # email pattern inferred from web-found emails
    # --- V6 Canadian + Google Maps sources ---
    "homestars_owner_found",      # yes | no
    "homestars_owner_name",       # owner name from HomeStars.ca listing
    "homestars_source_url",       # URL of the HomeStars business listing
    "yellowpages_owner_found",    # yes | no
    "yellowpages_owner_name",     # contact/owner name from YellowPages Canada
    "yellowpages_source_url",     # URL of the YP.ca business listing
    "google_maps_owner_found",    # yes | no
    "google_maps_owner_name",     # owner name from Google Maps review response
    "google_maps_snippet",        # snippet text where the owner name was found
    # --- V8 LinkedIn via Google ---
    "linkedin_owner_found",       # yes | no
    "linkedin_owner_name",        # name extracted from LinkedIn profile title
    "linkedin_owner_title",       # job title from LinkedIn profile title
    "linkedin_source_url",        # linkedin.com/in/ profile URL
    # --- V7 Direct email match ---
    "direct_email_found",         # yes | no  — discovered email matched decision maker name
    "direct_email",               # the actual email from WHOIS/web/GitHub (no guessing)
    # --- V7 SMTP verification ---
    "smtp_verified",              # yes | no | unverifiable | error | ""
    "smtp_verified_email",        # confirmed deliverable address (also in primary_guess)
    "catch_all_domain",           # yes | no | error | ""  — whether server accepts all addresses
    "smtp_checked_count",         # "0"–"5"  — how many candidates were SMTP-probed
]


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def read_input_csv(path: str) -> list[dict]:
    """Read input CSV and return list of row dicts."""
    if not os.path.exists(path):
        print(f"[ERROR] Input file not found: {path}")
        sys.exit(1)

    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))

    print(f"[INFO] Read {len(rows)} rows from {path}")
    return rows


def load_already_processed(output_path: str) -> set[str]:
    """
    When resuming, read the output CSV and return a set of already-processed
    company_name+domain_cleaned combos.
    """
    processed = set()
    if not os.path.exists(output_path):
        return processed

    with open(output_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row.get("company_name", "") + "|" + row.get("domain_cleaned", "")).lower()
            processed.add(key)

    print(f"[INFO] Resume mode: {len(processed)} companies already in output, skipping them.")
    return processed


def open_output_csv(path: str, resume: bool) -> tuple[csv.DictWriter, object]:
    """
    Open (or append to) the output CSV.
    Returns (writer, file_handle).
    """
    file_exists = os.path.exists(path)
    mode = "a" if resume and file_exists else "w"
    write_header = not (resume and file_exists)

    f = open(path, mode, newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
    if write_header:
        writer.writeheader()

    return writer, f


# ---------------------------------------------------------------------------
# Verify priority helper
# ---------------------------------------------------------------------------

def _compute_verify_priority(
    contact_seniority: str,
    score: int,
    pattern_confidence: str,
    processing_status: str,
) -> tuple[str, str]:
    """
    Returns (should_verify, verify_priority) based on result quality.

    should_verify:  "yes" | "no"
    verify_priority: "high" | "medium" | "low"
    """
    if processing_status in ("no_decision_maker_found", "missing_domain",
                              "blocked", "timeout", "connection_error",
                              "ssl_error", "error"):
        return "yes", "high"

    if processing_status == "ok_weak":
        return "yes", "high"

    # Strong result (ok)
    if contact_seniority == "decision_maker":
        if pattern_confidence in ("high", "medium"):
            return "no", "low"
        return "yes", "medium"

    if contact_seniority == "secondary_contact":
        return "yes", "high"

    return "yes", "medium"


# ---------------------------------------------------------------------------
# Row processor
# ---------------------------------------------------------------------------

def process_row(
    row: dict,
    use_ai: bool = False,
    api_key: str = "",
    use_yelp: bool = True,
    use_ddg: bool = True,
    use_bbb: bool = True,
    use_web_email: bool = True,
    use_github_email: bool = True,
    use_whois: bool = True,
    use_homestars: bool = True,
    use_yellowpages: bool = True,
    use_google_maps: bool = True,
    use_linkedin: bool = True,
    use_smtp_verify: bool = True,
) -> dict:
    """
    Process a single company row end-to-end.
    Returns a fully-populated output dict (all OUTPUT_COLUMNS present).

    use_ai:            if True, Claude Haiku is called as a final fallback.
    api_key:           Anthropic API key (only used when use_ai=True).
    use_yelp:          if True, search Yelp for the business owner name.
    use_ddg:           if True, search DuckDuckGo for decision maker mentions.
    use_bbb:           if True, scrape BBB for verified US business principal name.
    use_web_email:     if True, search DDG for domain emails on third-party sites.
    use_github_email:  if True, search GitHub code for domain emails.
    use_whois:         if True, look up WHOIS registrant email for pattern hints.
    use_homestars:     if True, search HomeStars.ca (Canadian trades directory).
    use_yellowpages:   if True, search YellowPages Canada.
    use_google_maps:   if True, search for owner via Google Maps review responses.
    """
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Step 1: Clean input data ---
    row = clean_row(row)
    domain = row.get("domain_cleaned", "")
    website = row.get("website", "")

    output = {col: "" for col in OUTPUT_COLUMNS}

    # Copy original company fields
    for field_name in ("company_name", "domain", "domain_cleaned", "website",
                       "city", "state", "country", "niche"):
        output[field_name] = row.get(field_name, "")

    output["generated_at"] = now_utc

    # --- Step 2: Validate minimum required data ---
    if not domain and not website:
        output["processing_status"] = "missing_domain"
        output["notes"] = "No domain or website provided — cannot scrape"
        output["should_verify"] = "yes"
        output["verify_priority"] = "high"
        output["mx_valid"] = "error"
        return output

    # --- Step 3: MX record validation (free, ~100ms) ---
    if config.CHECK_MX_RECORDS and domain:
        mx_result = check_mx(domain)
    else:
        mx_result = {"mx_valid": "not_checked", "mx_host": ""}
    output["mx_valid"] = mx_result.get("mx_valid", "")

    # --- Step 4: Scrape company website (all pages) ---
    scrape = scrape_company(domain=domain, website=website)
    output["website_status"] = scrape.website_status
    scrape_notes = scrape.notes or ""

    # --- Step 5: JS rendering flag ---
    # "jina" = Jina.ai Reader used (V8)  |  "yes" = Playwright used  |  "no" = static only
    if scrape.jina_used:
        output["js_rendered"] = "jina"
    elif scrape.playwright_used:
        output["js_rendered"] = "yes"
    else:
        output["js_rendered"] = "no"

    # --- Step 5a: WHOIS email lookup (free, ~200ms) ---
    whois_result = {
        "whois_email_found": "no", "whois_email_hint": "",
        "whois_inferred_pattern": "", "whois_name_hint": "", "_raw_emails": [],
    }
    if use_whois and config.USE_WHOIS_LOOKUP and domain:
        whois_result = check_whois_email(domain)

    output["whois_email_found"]       = whois_result.get("whois_email_found", "no")
    output["whois_email_hint"]        = whois_result.get("whois_email_hint", "")
    output["whois_inferred_pattern"]  = whois_result.get("whois_inferred_pattern", "")

    # --- Step 5b: Web email discovery — DDG domain search ---
    web_email_result = {
        "web_email_found": "no", "web_email_examples": "",
        "web_inferred_pattern": "", "_raw_emails": [],
    }
    if use_web_email and config.USE_WEB_EMAIL_SEARCH and domain:
        web_email_result = search_domain_emails_web(domain)

    output["web_email_found"]     = web_email_result.get("web_email_found", "no")
    output["web_email_examples"]  = web_email_result.get("web_email_examples", "")
    output["web_inferred_pattern"] = web_email_result.get("web_inferred_pattern", "")

    # --- Step 5c: GitHub email search ---
    github_emails: list[str] = []
    if use_github_email and config.USE_GITHUB_EMAIL_SEARCH and domain:
        github_emails = search_github_emails(domain)

    # --- Step 5d: Collect all discovered extra emails for pattern detection ---
    extra_emails: list[str] = []
    extra_emails.extend(whois_result.get("_raw_emails", []))
    extra_emails.extend(web_email_result.get("_raw_emails", []))
    extra_emails.extend(github_emails)

    # --- Step 5e: Detect email pattern from real emails on site + extra sources ---
    pattern_info = {"pattern": "", "confidence": "none", "examples": [], "reason": ""}
    if scrape.pages or extra_emails:
        pattern_info = detect_email_pattern(
            scrape.pages,
            extra_emails=extra_emails if extra_emails else None,
        )

    observed_pattern   = pattern_info.get("pattern", "")
    pattern_confidence = pattern_info.get("confidence", "none")
    output["observed_email_pattern"] = observed_pattern
    output["pattern_confidence"]     = pattern_confidence

    # --- Step 6: Extract decision maker from website (Strategies 1–5 + optional Haiku) ---
    extraction = extract_from_pages(
        pages=scrape.pages,
        use_ai=use_ai,
        api_key=api_key,
    )

    # --- Step 7: Yelp search for business owner name (free) ---
    yelp_result = {"yelp_owner_found": "no", "yelp_owner_name": "",
                   "yelp_owner_title": "", "yelp_source_url": "", "yelp_snippet": ""}
    if use_yelp and config.USE_YELP_SEARCH:
        yelp_result = search_yelp(
            company_name=row.get("company_name", ""),
            city=row.get("city", ""),
            state=row.get("state", ""),
        )

    output["yelp_owner_found"] = yelp_result.get("yelp_owner_found", "no")
    output["yelp_owner_name"]  = yelp_result.get("yelp_owner_name", "")
    output["yelp_source_url"]  = yelp_result.get("yelp_source_url", "")

    # --- Step 8: DuckDuckGo SERP search (free) ---
    ddg_result = {"serp_person_found": "no", "serp_decision_maker": "",
                  "serp_title": "", "serp_snippet": "", "serp_source_url": ""}
    if use_ddg and config.USE_DDG_SEARCH:
        ddg_result = search_ddg(
            company_name=row.get("company_name", ""),
            city=row.get("city", ""),
            state=row.get("state", ""),
        )

    output["serp_person_found"]   = ddg_result.get("serp_person_found", "no")
    output["serp_decision_maker"] = ddg_result.get("serp_decision_maker", "")
    output["serp_snippet"]        = ddg_result.get("serp_snippet", "")

    # --- Step 8b: BBB business principal search (free, US businesses) ---
    bbb_result = {"bbb_owner_found": "no", "bbb_owner_name": "", "bbb_source_url": ""}
    if use_bbb and config.USE_BBB_SEARCH:
        bbb_result = search_bbb(
            company_name=row.get("company_name", ""),
            city=row.get("city", ""),
            state=row.get("state", ""),
        )

    output["bbb_owner_found"] = bbb_result.get("bbb_owner_found", "no")
    output["bbb_owner_name"]  = bbb_result.get("bbb_owner_name", "")
    output["bbb_source_url"]  = bbb_result.get("bbb_source_url", "")

    # --- Step 8c: HomeStars.ca search (Canadian trades directory) ---
    homestars_result = {"homestars_owner_found": "no", "homestars_owner_name": "", "homestars_source_url": ""}
    if use_homestars and config.USE_HOMESTARS_SEARCH:
        homestars_result = search_homestars(
            company_name=row.get("company_name", ""),
            city=row.get("city", ""),
            state=row.get("state", ""),
        )

    output["homestars_owner_found"] = homestars_result.get("homestars_owner_found", "no")
    output["homestars_owner_name"]  = homestars_result.get("homestars_owner_name", "")
    output["homestars_source_url"]  = homestars_result.get("homestars_source_url", "")

    # --- Step 8d: YellowPages Canada search ---
    yellowpages_result = {"yellowpages_owner_found": "no", "yellowpages_owner_name": "", "yellowpages_source_url": ""}
    if use_yellowpages and config.USE_YELLOWPAGES_CA_SEARCH:
        yellowpages_result = search_yellowpages_ca(
            company_name=row.get("company_name", ""),
            city=row.get("city", ""),
            state=row.get("state", ""),
        )

    output["yellowpages_owner_found"] = yellowpages_result.get("yellowpages_owner_found", "no")
    output["yellowpages_owner_name"]  = yellowpages_result.get("yellowpages_owner_name", "")
    output["yellowpages_source_url"]  = yellowpages_result.get("yellowpages_source_url", "")

    # --- Step 8e: Google Maps owner response search ---
    google_maps_result = {"google_maps_owner_found": "no", "google_maps_owner_name": "", "google_maps_snippet": ""}
    if use_google_maps and config.USE_GOOGLE_MAPS_SEARCH:
        google_maps_result = search_google_maps_owner(
            company_name=row.get("company_name", ""),
            city=row.get("city", ""),
            state=row.get("state", ""),
        )

    output["google_maps_owner_found"] = google_maps_result.get("google_maps_owner_found", "no")
    output["google_maps_owner_name"]  = google_maps_result.get("google_maps_owner_name", "")
    output["google_maps_snippet"]     = google_maps_result.get("google_maps_snippet", "")

    # --- Step 8f: LinkedIn via Google search (V8) ---
    linkedin_result = {"linkedin_owner_found": "no", "linkedin_owner_name": "",
                       "linkedin_owner_title": "", "linkedin_source_url": ""}
    if use_linkedin and config.USE_LINKEDIN_SEARCH:
        linkedin_result = search_linkedin_google(
            company_name=row.get("company_name", ""),
            city=row.get("city", ""),
            state=row.get("state", ""),
        )

    output["linkedin_owner_found"] = linkedin_result.get("linkedin_owner_found", "no")
    output["linkedin_owner_name"]  = linkedin_result.get("linkedin_owner_name", "")
    output["linkedin_owner_title"] = linkedin_result.get("linkedin_owner_title", "")
    output["linkedin_source_url"]  = linkedin_result.get("linkedin_source_url", "")

    # --- Step 9: Select the best person from all sources ---
    # Priority rules:
    #   A. If website extraction found a Tier 1 decision maker with strong score → use it
    #   B. If extraction found nothing OR only weak/Tier 2 → prefer external Tier 1 source
    #   C. External source order preference: Yelp > DDG (Yelp is more reliable)

    extraction_score = extraction.primary.score if extraction.primary else 0
    extraction_is_tier1 = (
        extraction.primary is not None
        and extraction.primary.contact_seniority == "decision_maker"
    )

    person = extraction.primary  # default: use the website extraction result

    # Build external candidates (only if extraction didn't already win cleanly)
    if not extraction_is_tier1 or extraction_score < config.SCORE_THRESHOLD_STRONG:
        external_candidates = []

        if yelp_result.get("yelp_owner_found") == "yes" and yelp_result.get("yelp_owner_name"):
            yelp_person = create_person_from_external(
                name=yelp_result["yelp_owner_name"],
                title=yelp_result.get("yelp_owner_title", "Business Owner"),
                source="yelp_search",
                snippet=yelp_result.get("yelp_snippet", ""),
            )
            external_candidates.append(yelp_person)

        if ddg_result.get("serp_person_found") == "yes" and ddg_result.get("serp_decision_maker"):
            ddg_person = create_person_from_external(
                name=ddg_result["serp_decision_maker"],
                title=ddg_result.get("serp_title", ""),
                source="ddg_search",
                snippet=ddg_result.get("serp_snippet", ""),
            )
            external_candidates.append(ddg_person)

        # V5: BBB principal name (high confidence — BBB verifies during accreditation)
        if bbb_result.get("bbb_owner_found") == "yes" and bbb_result.get("bbb_owner_name"):
            bbb_person = create_person_from_external(
                name=bbb_result["bbb_owner_name"],
                title="Business Owner",
                source="bbb_search",
                snippet=f"BBB verified principal: {bbb_result['bbb_owner_name']}",
            )
            external_candidates.append(bbb_person)

        # V6: HomeStars.ca Canadian directory
        if homestars_result.get("homestars_owner_found") == "yes" and homestars_result.get("homestars_owner_name"):
            hs_person = create_person_from_external(
                name=homestars_result["homestars_owner_name"],
                title="Business Owner",
                source="homestars_search",
                snippet=f"HomeStars listing: {homestars_result['homestars_owner_name']}",
            )
            external_candidates.append(hs_person)

        # V6: YellowPages Canada
        if yellowpages_result.get("yellowpages_owner_found") == "yes" and yellowpages_result.get("yellowpages_owner_name"):
            yp_person = create_person_from_external(
                name=yellowpages_result["yellowpages_owner_name"],
                title="Business Owner",
                source="yellowpages_search",
                snippet=f"YellowPages CA: {yellowpages_result['yellowpages_owner_name']}",
            )
            external_candidates.append(yp_person)

        # V6: Google Maps owner response
        if google_maps_result.get("google_maps_owner_found") == "yes" and google_maps_result.get("google_maps_owner_name"):
            gm_person = create_person_from_external(
                name=google_maps_result["google_maps_owner_name"],
                title="Business Owner",
                source="google_maps_search",
                snippet=google_maps_result.get("google_maps_snippet", ""),
            )
            external_candidates.append(gm_person)

        # V8: LinkedIn via Google — highest-confidence external source
        if linkedin_result.get("linkedin_owner_found") == "yes" and linkedin_result.get("linkedin_owner_name"):
            li_person = create_person_from_external(
                name=linkedin_result["linkedin_owner_name"],
                title=linkedin_result.get("linkedin_owner_title", "Business Owner") or "Business Owner",
                source="linkedin_search",
                snippet=f"LinkedIn profile: {linkedin_result.get('linkedin_source_url', '')}",
            )
            external_candidates.append(li_person)

        if external_candidates:
            # Pick the highest-scoring external candidate
            best_external = max(external_candidates, key=lambda p: p.score)

            # Use external if:
            #   - website extraction found nothing, OR
            #   - external found a Tier 1 and website found only Tier 2 / nothing
            if person is None:
                person = best_external
            elif (
                best_external.contact_seniority == "decision_maker"
                and not extraction_is_tier1
            ):
                person = best_external

    # --- Step 9.5: Direct email match ---
    # Check whether any email already discovered (WHOIS/web/GitHub) contains
    # the decision maker's name. If so, we have the real email — no guessing needed.
    direct_email_found = "no"
    direct_email_val   = ""

    if person and extra_emails:
        _name_cleaned = clean_name_for_email(person.full_name)
        _first_dm, _last_dm = split_full_name(_name_cleaned)
        if _first_dm and _last_dm:
            _direct = find_direct_email_match(_first_dm, _last_dm, extra_emails)
            if _direct:
                direct_email_found = "yes"
                direct_email_val   = _direct

    output["direct_email_found"] = direct_email_found
    output["direct_email"]       = direct_email_val

    # --- Step 10: Build output from the selected person ---
    notes_parts = []
    if scrape_notes:
        notes_parts.append(scrape_notes)
    if extraction.notes:
        notes_parts.append(extraction.notes)

    if person:
        cleaned_for_email = clean_name_for_email(person.full_name)
        first_clean, last_clean = split_full_name(cleaned_for_email)

        output["decision_maker_full_name"]  = person.full_name
        output["decision_maker_first_name"] = first_clean.capitalize() if first_clean else ""
        output["decision_maker_last_name"]  = last_clean.capitalize() if last_clean else ""
        output["decision_maker_title"]      = person.title
        output["decision_maker_source"]     = person.source
        output["decision_maker_confidence"] = person.confidence
        output["full_name_cleaned"]         = cleaned_for_email

        # V3 scoring fields
        output["contact_seniority"] = person.contact_seniority
        output["selected_score"]    = str(person.score)
        output["score_reason"]      = " | ".join(person.score_reason)
        output["matched_strategy"]  = person.source
        output["matched_page"]      = person.matched_page
        output["matched_snippet"]   = person.matched_snippet[:150] if person.matched_snippet else ""

        # Backup: use extraction.backup if available and we didn't already override with external
        if extraction.backup and extraction.backup.full_name.lower() != person.full_name.lower():
            output["backup_decision_maker"] = extraction.backup.full_name
            output["backup_title"]          = extraction.backup.title

        # Generate email candidates
        email_result = generate_email_candidates(
            first=first_clean,
            last=last_clean,
            domain=domain,
            detected_pattern=observed_pattern,
        )
        candidates = email_result.get("candidates", [])
        output.update(candidates_to_columns(candidates))
        output["primary_guess"]        = email_result.get("primary_guess", "")
        output["primary_guess_reason"] = email_result.get("primary_guess_reason", "")

        # --- Step 10.5: Direct match override + SMTP verification ---
        if direct_email_found == "yes" and direct_email_val:
            # Real email already found from public source — skip SMTP entirely
            output["primary_guess"]        = direct_email_val
            output["primary_guess_reason"] = (
                "Direct match — email found on public source containing decision maker name"
            )
            output["smtp_verified"]       = "yes"
            output["smtp_verified_email"] = direct_email_val
            output["catch_all_domain"]    = ""
            output["smtp_checked_count"]  = "0"

        elif (
            use_smtp_verify
            and config.USE_SMTP_VERIFY
            and candidates
            and mx_result.get("mx_valid") == "yes"
            and mx_result.get("mx_host")
        ):
            _mx_host = mx_result["mx_host"]
            _is_catch_all = detect_catch_all(domain, _mx_host, config.SMTP_TIMEOUT)

            if _is_catch_all:
                output["smtp_verified"]       = "unverifiable"
                output["smtp_verified_email"] = ""
                output["catch_all_domain"]    = "yes"
                output["smtp_checked_count"]  = "0"
                notes_parts.append(
                    "SMTP: catch-all mail server detected — cannot verify individual addresses"
                )
            else:
                output["catch_all_domain"] = "no"
                _top = candidates[:config.SMTP_MAX_CANDIDATES]
                _smtp_results = verify_candidate_list(
                    _top, domain, _mx_host, config.SMTP_TIMEOUT, config.SMTP_MAX_WORKERS
                )
                output["smtp_checked_count"] = str(len(_smtp_results))

                _verified = next(
                    (r for r in _smtp_results if r["smtp_status"] == "verified"), None
                )
                if _verified:
                    output["smtp_verified"]        = "yes"
                    output["smtp_verified_email"]  = _verified["email"]
                    output["primary_guess"]        = _verified["email"]
                    output["primary_guess_reason"] = (
                        f"SMTP verified — server confirmed {_verified['email']} exists"
                    )
                elif _smtp_results and all(
                    r["smtp_status"] == "rejected" for r in _smtp_results
                ):
                    output["smtp_verified"]       = "no"
                    output["smtp_verified_email"] = ""
                    notes_parts.append(
                        f"SMTP: all {len(_smtp_results)} candidate(s) rejected by mail server"
                    )
                else:
                    output["smtp_verified"]       = "error"
                    output["smtp_verified_email"] = ""
                    notes_parts.append(
                        "SMTP: could not connect to mail server (port 25 may be blocked on this network)"
                    )
        else:
            # SMTP not attempted (toggle off, no MX, or no candidates)
            output["smtp_verified"]       = ""
            output["smtp_verified_email"] = ""
            output["catch_all_domain"]    = ""
            output["smtp_checked_count"]  = ""

        # Determine processing_status from score
        if person.score >= config.SCORE_THRESHOLD_STRONG:
            output["processing_status"] = "ok"
        else:
            output["processing_status"] = "ok_weak"

        # Note when an external source was used
        if person.source in ("yelp_search", "ddg_search", "bbb_search",
                              "homestars_search", "yellowpages_search", "google_maps_search",
                              "linkedin_search"):
            notes_parts.append(
                f"Decision maker sourced from {person.source} — website scrape did not find a Tier 1 contact"
            )

    else:
        # No decision maker found anywhere — use generic fallback emails
        generic = generate_generic_fallback_emails(domain)
        output.update(candidates_to_columns(generic))
        output["primary_guess"]        = generic[0] if generic else ""
        output["primary_guess_reason"] = "no named decision maker found — generic fallback"
        output["processing_status"]    = "no_decision_maker_found"
        output["contact_seniority"]    = "unknown"
        output["selected_score"]       = "0"
        notes_parts.append("No named decision maker found via any source — generic role emails only")

    # Override status if site was unreachable (takes priority over extraction result)
    if scrape.website_status in ("blocked", "timeout", "connection_error",
                                  "ssl_error", "missing_domain"):
        output["processing_status"] = scrape.website_status

    # Add MX warning to notes when domain has no mail servers
    if mx_result.get("mx_valid") == "no":
        notes_parts.append("MX check: domain has no mail servers — all emails will likely bounce")

    output["notes"] = " | ".join(notes_parts)

    # --- Step 11: Compute verification priority ---
    should_verify, verify_priority = _compute_verify_priority(
        contact_seniority=output.get("contact_seniority", ""),
        score=int(output.get("selected_score", "0") or "0"),
        pattern_confidence=output.get("pattern_confidence", "none"),
        processing_status=output.get("processing_status", ""),
    )
    output["should_verify"]   = should_verify
    output["verify_priority"] = verify_priority

    return output


# ---------------------------------------------------------------------------
# Shared pipeline (used by both CLI and Flask web UI)
# ---------------------------------------------------------------------------

def run_pipeline(
    rows: list[dict],
    output_path: str,
    use_ai: bool = False,
    api_key: str = "",
    use_yelp: bool = True,
    use_ddg: bool = True,
    use_bbb: bool = True,
    use_web_email: bool = True,
    use_github_email: bool = True,
    use_whois: bool = True,
    use_playwright: bool = False,
    use_homestars: bool = True,
    use_yellowpages: bool = True,
    use_google_maps: bool = True,
    use_linkedin: bool = True,
    use_smtp_verify: bool = True,
    resume: bool = False,
    dedup: bool = True,
    progress_callback: Optional[Callable[[int, int, str, int, int], None]] = None,
) -> dict:
    """
    Run the full processing pipeline on a list of rows.

    progress_callback(current, total, company_name, found_count, not_found_count)
      Called after each row is processed. Used by the Flask UI for live updates.
      Leave as None for CLI usage.

    Returns a summary dict: {processed, found, not_found, output_path}
    """
    # Apply per-run Playwright toggle (overrides config.py default)
    config.USE_PLAYWRIGHT = use_playwright
    if dedup:
        before = len(rows)
        rows = [clean_row(r) for r in rows]
        rows = deduplicate_rows(rows)
        removed = before - len(rows)
        if removed:
            print(f"[INFO] Removed {removed} duplicate domain(s)")

    already_done: set[str] = set()
    if resume:
        already_done = load_already_processed(output_path)

    writer, out_file = open_output_csv(output_path, resume)

    pending = []
    for row in rows:
        key = (
            row.get("company_name", "") + "|" +
            row.get("domain_cleaned", row.get("domain", ""))
        ).lower()
        if key not in already_done:
            pending.append(row)

    total        = len(pending)
    saved_count  = 0
    found_count  = 0
    not_found_count = 0

    try:
        for i, row in enumerate(pending):
            company_name = row.get("company_name", "(unknown)")

            try:
                result = process_row(
                    row,
                    use_ai=use_ai,
                    api_key=api_key,
                    use_yelp=use_yelp,
                    use_ddg=use_ddg,
                    use_bbb=use_bbb,
                    use_web_email=use_web_email,
                    use_github_email=use_github_email,
                    use_whois=use_whois,
                    use_homestars=use_homestars,
                    use_yellowpages=use_yellowpages,
                    use_google_maps=use_google_maps,
                    use_linkedin=use_linkedin,
                    use_smtp_verify=use_smtp_verify,
                )
            except Exception as e:
                result = {col: "" for col in OUTPUT_COLUMNS}
                result["company_name"]      = row.get("company_name", "")
                result["domain"]            = row.get("domain_cleaned", row.get("domain", ""))
                result["processing_status"] = "error"
                result["notes"]             = f"Unexpected error: {e}"
                result["generated_at"]      = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                result["should_verify"]     = "yes"
                result["verify_priority"]   = "high"

            writer.writerow(result)
            saved_count += 1

            status = result.get("processing_status", "")
            if status in ("ok", "ok_weak"):
                found_count += 1
            else:
                not_found_count += 1

            # Flush to disk periodically for crash safety
            if saved_count % config.PARTIAL_SAVE_EVERY == 0:
                out_file.flush()

            # Notify progress (Flask UI)
            if progress_callback:
                progress_callback(saved_count, total, company_name, found_count, not_found_count)

            # Polite delay between companies
            if i < total - 1:
                time.sleep(config.REQUEST_DELAY_SECONDS)

    finally:
        out_file.flush()
        out_file.close()

    return {
        "processed":   saved_count,
        "found":       found_count,
        "not_found":   not_found_count,
        "output_path": output_path,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Decision Maker Email Finder — finds decision makers and generates email candidates."
    )
    parser.add_argument("--input",   required=True, help="Path to input CSV file")
    parser.add_argument("--output",  required=True, help="Path to output CSV file")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a previous run — skip companies already in the output file",
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="Disable deduplication of companies by domain",
    )
    parser.add_argument(
        "--use-ai",
        action="store_true",
        help="Enable Claude Haiku AI fallback for name extraction (~$0.001-$0.003/company)",
    )
    parser.add_argument(
        "--api-key",
        default=config.ANTHROPIC_API_KEY,
        help="Anthropic API key (required if --use-ai is set)",
    )
    parser.add_argument(
        "--no-yelp",
        action="store_true",
        help="Disable Yelp business owner search",
    )
    parser.add_argument(
        "--no-ddg",
        action="store_true",
        help="Disable DuckDuckGo SERP search",
    )
    parser.add_argument(
        "--no-bbb",
        action="store_true",
        help="Disable BBB business principal search",
    )
    parser.add_argument(
        "--no-web-email",
        action="store_true",
        help="Disable DuckDuckGo domain email discovery",
    )
    parser.add_argument(
        "--no-github-email",
        action="store_true",
        help="Disable GitHub code email search",
    )
    parser.add_argument(
        "--no-whois",
        action="store_true",
        help="Disable WHOIS registrant email lookup",
    )
    parser.add_argument(
        "--playwright",
        action="store_true",
        help="Enable Playwright JS rendering (requires: pip3 install playwright && playwright install chromium)",
    )
    parser.add_argument(
        "--no-homestars",
        action="store_true",
        help="Disable HomeStars.ca Canadian trades directory search",
    )
    parser.add_argument(
        "--no-yellowpages",
        action="store_true",
        help="Disable YellowPages Canada search",
    )
    parser.add_argument(
        "--no-google-maps",
        action="store_true",
        help="Disable Google Maps owner response search",
    )
    parser.add_argument(
        "--no-linkedin",
        action="store_true",
        help="Disable LinkedIn via Google decision maker search",
    )
    parser.add_argument(
        "--no-smtp-verify",
        action="store_true",
        help="Disable SMTP email verification (faster but no confirmed emails)",
    )
    args = parser.parse_args()

    if args.use_ai and not args.api_key:
        print("[ERROR] --use-ai requires an --api-key. Get one at https://console.anthropic.com/")
        sys.exit(1)

    rows = read_input_csv(args.input)

    total_to_process = len(rows)
    pbar = tqdm(total=total_to_process, unit="company", dynamic_ncols=True)

    def cli_progress(current, total, company_name, found, not_found):
        pbar.set_description(f"{company_name[:40]}")
        pbar.update(1)

    use_yelp         = not args.no_yelp
    use_ddg          = not args.no_ddg
    use_bbb          = not args.no_bbb
    use_web_email    = not args.no_web_email
    use_github_email = not args.no_github_email
    use_whois        = not args.no_whois
    use_playwright   = args.playwright
    use_homestars    = not args.no_homestars
    use_yellowpages  = not args.no_yellowpages
    use_google_maps  = not args.no_google_maps
    use_linkedin     = not args.no_linkedin
    use_smtp_verify  = not args.no_smtp_verify

    print(f"[INFO] Processing {total_to_process} compan{'y' if total_to_process == 1 else 'ies'}...")
    if args.use_ai:
        print("[INFO] AI fallback (Claude Haiku) is ENABLED")
    print(
        f"[INFO] Sources: Yelp={'ON' if use_yelp else 'OFF'} | "
        f"DDG={'ON' if use_ddg else 'OFF'} | "
        f"BBB={'ON' if use_bbb else 'OFF'} | "
        f"HomeStars={'ON' if use_homestars else 'OFF'} | "
        f"YP.ca={'ON' if use_yellowpages else 'OFF'} | "
        f"GMaps={'ON' if use_google_maps else 'OFF'} | "
        f"WebEmail={'ON' if use_web_email else 'OFF'} | "
        f"GitHub={'ON' if use_github_email else 'OFF'} | "
        f"WHOIS={'ON' if use_whois else 'OFF'} | "
        f"Playwright={'ON' if use_playwright else 'OFF'} | "
        f"LinkedIn={'ON' if use_linkedin else 'OFF'} | "
        f"SMTP={'ON' if use_smtp_verify else 'OFF'} | "
        f"MX={'ON' if config.CHECK_MX_RECORDS else 'OFF'}"
    )

    summary = run_pipeline(
        rows=rows,
        output_path=args.output,
        use_ai=args.use_ai,
        api_key=args.api_key,
        use_yelp=use_yelp,
        use_ddg=use_ddg,
        use_bbb=use_bbb,
        use_web_email=use_web_email,
        use_github_email=use_github_email,
        use_whois=use_whois,
        use_playwright=use_playwright,
        use_homestars=use_homestars,
        use_yellowpages=use_yellowpages,
        use_google_maps=use_google_maps,
        use_linkedin=use_linkedin,
        use_smtp_verify=use_smtp_verify,
        resume=args.resume,
        dedup=not args.no_dedup,
        progress_callback=cli_progress,
    )

    pbar.close()
    print(f"\n[DONE] Output written to: {args.output}")
    print(
        f"[DONE] {summary['processed']} rows processed — "
        f"{summary['found']} found, {summary['not_found']} not found."
    )


if __name__ == "__main__":
    main()
