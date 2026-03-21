# DecisionMakerFinder — V4 Upgrade Reference

> **How to use this file**: When you're ready to build V4, open a new Claude session,
> paste this file in, and say "implement everything in this UPGRADE.md file."
> Claude will have the full context to build it without re-explaining anything.

---

## What This Upgrade Is

The current V3 system finds the wrong person too often — an Office Manager or admin
instead of the Owner, CEO, or Founder. Email generation is solid. The bottleneck is
identifying the **correct human** before any email work begins.

V4 adds:
- **Free**: Yelp scraping, DuckDuckGo SERP search, MX record validation, better footer parsing
- **Paid toggles**: Apollo.io, Proxycurl (LinkedIn), Hunter.io, MillionVerifier

---

## How Hunter.io and AnyMailFinder Actually Work

### What they are NOT
They do NOT scrape websites in real time. They are primarily **databases** built from
years of public web crawling, with smart verification layered on top.

### Their real pipeline

**Step 1 — Massive historical web crawl (their unfair advantage)**
They have crawled hundreds of millions of web pages over 5–10 years and indexed
every email address found publicly — HTML, PDFs, GitHub repos, LinkedIn public
profiles, press releases, conference speaker lists, job boards.
When you search Hunter for `company.com`, they query their OWN database first.

**Step 2 — Pattern inference from collected emails**
For each domain, they collect all named emails they've ever seen:
```
mike@company.com
sarah.jones@company.com
r.brown@company.com
```
They infer the pattern (`first.last`, 2 votes) and score it with confidence.
Our V3 `detect_email_pattern()` does the same thing but only from one site visit.

**Step 3 — Person data cross-referencing**
They match email addresses with LinkedIn profiles, press releases, and About pages
to attach a name + title to each address in their database.

**Step 4 — MX Record check (free, 100ms DNS lookup)**
Confirm the domain has active mail servers before any SMTP work.
No MX records = domain cannot receive email = immediate invalid.

**Step 5 — SMTP Verification (their core technical edge)**
This is how they achieve 97%+ accuracy:
```
1. Connect to the company's mail server on port 25
2. EHLO hunter.io
3. MAIL FROM: verify@hunter.io
4. RCPT TO: sarah.jones@company.com
5. Server responds:
   → 250 OK  = mailbox exists ✅
   → 550/551 = mailbox does not exist ❌
6. QUIT  (no email ever sent)
```
This is called an SMTP handshake probe. Zero emails sent. Purely a delivery check.

**Why we can't replicate this for free:**
- Port 25 is blocked outbound by almost all ISPs and cloud providers (AWS, GCP, DO)
- Clean SMTP infrastructure requires dedicated IPs with good sender reputation
- Many mail servers return 250 for everything (catch-all) or block unknown IPs

**Step 6 — Catch-all detection**
Send a random fake address to the domain: `xyz_abc_123_notreal@company.com`.
If accepted = catch-all domain = SMTP verification unreliable for that domain.

**Honest reality**: We can reach ~65–75% of their accuracy with free methods.
Getting to 90%+ requires their API or a dedicated verification service.

---

## FREE Additions to Build

### 1. MX Record Validation
**File**: `verifier.py` (new file)
**Library**: `dnspython` (add to requirements.txt)
**What**: DNS lookup — does this domain have mail servers?
**Cost**: $0. ~100ms per domain.
**Impact**: Immediately eliminates all emails on dead/parked domains (~5–15% of lists).

```python
import dns.resolver

def check_mx(domain: str) -> dict:
    """
    Returns {"mx_valid": "yes"|"no"|"error", "mx_records": [...]}
    """
    try:
        records = dns.resolver.resolve(domain, "MX")
        hosts = [str(r.exchange).rstrip(".") for r in records]
        return {"mx_valid": "yes", "mx_records": hosts}
    except dns.resolver.NXDOMAIN:
        return {"mx_valid": "no", "mx_records": []}
    except dns.resolver.NoAnswer:
        return {"mx_valid": "no", "mx_records": []}
    except Exception as e:
        return {"mx_valid": "error", "mx_records": []}
```

**New output column**: `mx_valid`

---

### 2. Yelp Scraping for Local Businesses
**File**: `searcher.py` (new file)
**Library**: `requests` + `BeautifulSoup` (already installed)
**What**: Search Yelp for the company by name + city, scrape the business owner field
from the Yelp business profile page.
**Cost**: $0.
**Speed**: 1–2 seconds per company.
**Accuracy**: ~40–50% hit rate for local service businesses (plumbers, roofers,
electricians, law firms, restaurants). Low for B2B/tech companies.

```python
# Pseudocode — full implementation needed
def search_yelp(company_name: str, city: str = "", state: str = "") -> dict:
    """
    Returns {
        "yelp_owner_name": str,
        "yelp_owner_title": str,
        "yelp_source_url": str,
        "yelp_found": bool
    }
    """
    # 1. GET https://www.yelp.com/search?find_desc={company_name}&find_loc={city}+{state}
    # 2. Find first result business URL
    # 3. GET the business profile page
    # 4. Look for owner name in:
    #    - "Business owner" section
    #    - "Meet the Business Owner" section
    #    - Schema.org LocalBusiness owner field
    # 5. Return name if found
```

**New output columns**: `yelp_owner_found`, `yelp_owner_name`, `yelp_source_url`

---

### 3. DuckDuckGo SERP Snippet Search
**File**: `searcher.py` (same new file)
**Library**: `requests` + `BeautifulSoup`
**What**: Query DuckDuckGo HTML results for `"company name" owner OR CEO OR founder`
and extract name + title from search result snippets.
**Cost**: $0. No API key. More permissive than Google.
**Speed**: 1–2 seconds per company.
**Risk**: Fragile — DDG HTML structure can change. Use as supplementary signal only.

```python
def search_ddg(company_name: str, city: str = "") -> dict:
    """
    Returns {
        "serp_person_found": bool,
        "serp_decision_maker": str,
        "serp_title": str,
        "serp_snippet": str,
        "serp_source": str
    }
    """
    # 1. GET https://html.duckduckgo.com/html/?q="company+name"+owner+CEO+founder
    # 2. Parse first 3 result snippets
    # 3. Apply name + role regex patterns to each snippet
    # 4. Return best match found
```

**New output columns**: `serp_person_found`, `serp_decision_maker`, `serp_snippet`

---

### 4. Better Footer / Copyright / Image-Alt Parsing
**File**: `extractor.py` (modify existing)
**What**: Many small business sites put the owner's name in places we don't currently
check. Add these parsing patterns to `_extract_html_cards()` and add a new strategy:

Patterns to add:
- Footer copyright: `© 2024 John Smith's Plumbing` → extract "John Smith"
- Contact block email signatures: `John Smith | Owner | john@company.com`
- Image alt text: `alt="John Smith, Owner of Acme Roofing"`
- Google Maps schema embedded in page: `"author": {"name": "John Smith"}`
- BBB-style "Principals" sections
- LinkedIn badge widgets embedded on site

---

## PAID Additions (Toggle-Based in UI)

### Paid Option 1 — Proxycurl LinkedIn API ⭐ BEST FOR PERSON FINDING
**Best for**: Any business with a LinkedIn company page (B2B, medium/large businesses)
**Cost**: $49/month for 100 credits. ~$0.39–$0.80 per additional credit.
**Accuracy**: Very high — LinkedIn data is self-reported by the professional.

**How to use**:
```python
import requests

def lookup_proxycurl(domain: str, api_key: str) -> dict:
    """
    Find senior employees at a company via LinkedIn.
    Returns best Tier 1 decision maker found.
    """
    # Step 1: Find LinkedIn company URL from domain
    r = requests.get(
        "https://nubela.co/proxycurl/api/linkedin/company/resolve",
        params={"company_domain": domain},
        headers={"Authorization": f"Bearer {api_key}"}
    )
    linkedin_url = r.json().get("url", "")
    if not linkedin_url:
        return {}

    # Step 2: Get employees filtered by senior role
    r2 = requests.get(
        "https://nubela.co/proxycurl/api/linkedin/company/employees",
        params={
            "linkedin_company_url": linkedin_url,
            "role_search": "owner|founder|CEO|president|managing director",
            "page_size": 5
        },
        headers={"Authorization": f"Bearer {api_key}"}
    )
    employees = r2.json().get("employees", [])
    # Return the first Tier 1 result
    # ...
```

**New output columns**: `proxycurl_name`, `proxycurl_title`, `proxycurl_linkedin_url`

---

### Paid Option 2 — Apollo.io API ⭐ BEST VALUE FOR B2B VOLUME
**Best for**: Established B2B companies with 10+ employees
**Cost**: $49/month Basic = 10,000 people searches/year (~833/month = $0.06/lookup)
**Not great for**: Very small local businesses (sole traders, tradespeople)

**How to use**:
```python
def lookup_apollo(domain: str, api_key: str) -> dict:
    """
    Search Apollo contact database for senior person at domain.
    """
    r = requests.post(
        "https://api.apollo.io/v1/mixed_people/search",
        headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
        json={
            "api_key": api_key,
            "q_organization_domains": domain,
            "person_titles": ["owner", "founder", "ceo", "president",
                              "managing director", "managing partner"],
            "page": 1,
            "per_page": 3
        }
    )
    people = r.json().get("people", [])
    # Return first match with name + title
    # ...
```

**New output columns**: `apollo_name`, `apollo_title`, `apollo_email` (if available)

---

### Paid Option 3 — Hunter.io API
**Best for**: Checking if an email is already in Hunter's indexed database
**Cost**: $49/month = 500 email finder searches ($0.10 each)
**Two useful endpoints**:

```python
# After finding a person's name via scraping/Apollo/Proxycurl,
# check if Hunter knows their email:
def lookup_hunter_email(first: str, last: str, domain: str, api_key: str) -> dict:
    r = requests.get(
        "https://api.hunter.io/v2/email-finder",
        params={
            "domain": domain,
            "first_name": first,
            "last_name": last,
            "api_key": api_key
        }
    )
    data = r.json().get("data", {})
    return {
        "hunter_email": data.get("email", ""),
        "hunter_score": data.get("score", 0),
        "hunter_verified": "yes" if data.get("verification", {}).get("status") == "valid" else "no"
    }

# Or search all known emails at a domain (no name needed):
def search_hunter_domain(domain: str, api_key: str) -> dict:
    r = requests.get(
        "https://api.hunter.io/v2/domain-search",
        params={"domain": domain, "api_key": api_key, "limit": 10}
    )
    # Returns all known people + emails at domain
    # Filter for Tier 1 titles
    # ...
```

**New output columns**: `hunter_email`, `hunter_score`, `hunter_verified`

---

### Paid Option 4 — MillionVerifier (Bulk Email Verification)
**Best for**: Pre-filtering email candidates BEFORE sending to Lumlist (saves Lumlist credits)
**Cost**: $27 for 5,000 verifications (one-time packs, no subscription)
**Accuracy**: ~98% on non-catch-all domains
**Use case**: After generating 10 candidates per company × 500 companies = 5,000 emails.
Verify all, keep only `valid` ones.

```python
def verify_email_millionverifier(email: str, api_key: str) -> dict:
    r = requests.get(
        "https://api.millionverifier.com/api/v3/",
        params={"api": api_key, "email": email}
    )
    data = r.json()
    return {
        "result": data.get("result", "unknown"),  # ok | fail | unknown | catch_all
        "quality": data.get("quality", "unknown"),  # valid | invalid | risky | unknown
        "free": data.get("free", False),  # True = free/disposable email provider
        "role": data.get("role", False)   # True = role address (info@, admin@)
    }
```

**New output column**: `verification_result` (valid | invalid | catch-all | unknown | not_run)

---

## Full Architecture When Everything Is Enabled

```
Input CSV
    │
    ▼
Layer 1: Clean + MX validation (FREE)
    │  mx_valid = no → skip email gen, flag immediately
    │
    ▼
Layer 2: Website scraping V3 (FREE, existing)
    │  Decision maker from schema.org, HTML cards, text regex
    │
    ▼
Layer 3: Yelp scraping (FREE, new)
    │  If no Tier 1 found yet → try Yelp for owner name
    │
    ▼
Layer 4: DuckDuckGo SERP (FREE, new)
    │  If still no Tier 1 → try SERP snippet for name
    │
    ▼
Layer 5: Apollo.io OR Proxycurl (PAID TOGGLE)
    │  If no Tier 1 found via free methods → hit API
    │  Returns correct name + title from database
    │
    ▼
Layer 6: Claude Haiku fallback (PAID TOGGLE, existing)
    │  If still nothing → AI reads page text
    │
    ▼
Layer 7: Email generation V3 (existing)
    │  Uses detected pattern → 10 candidates
    │
    ▼
Layer 8: Hunter.io email check (PAID TOGGLE)
    │  If Hunter has this person in DB → use their verified email
    │  Overrides candidate_1 if score >= 80
    │
    ▼
Layer 9: MillionVerifier bulk check (PAID TOGGLE)
    │  Verify all 10 candidates → keep valid ones only
    │
    ▼
Output CSV (ready for Lumlist or direct outreach)
```

---

## Cost Summary

| What | Tool | Cost | Per Company (500/mo) |
|---|---|---|---|
| Dead domain filter | dnspython | $0 | $0 |
| Local biz owner names | Yelp scraping | $0 | $0 |
| Web search names | DuckDuckGo | $0 | $0 |
| AI page reading | Claude Haiku | ~$0.001–$0.003 | ~$0.50–$1.50 |
| LinkedIn person lookup | Proxycurl | $0.40–$0.80/lookup | $200–$400 |
| B2B person database | Apollo.io | $49/mo flat | $49 |
| Email from indexed DB | Hunter.io | $49/mo (500 searches) | $49 |
| Bulk email verification | MillionVerifier | $27/5k verifications | $27 |

**Recommended minimum paid stack** (best ROI for 500 companies/month):
- Apollo.io $49 + MillionVerifier $27 = **$76/month**
- Expected outcome: ~85–90% correct Tier 1 decision maker, 1–2 verified emails each

---

## Files to Create / Modify

| File | Status | What Changes |
|---|---|---|
| `verifier.py` | NEW | MX check, MillionVerifier API, Hunter.io API |
| `searcher.py` | NEW | Yelp scraping, DuckDuckGo SERP search |
| `extractor.py` | MODIFY | Footer/copyright/image-alt parsing; accept searcher results |
| `main.py` | MODIFY | Wire in verifier + searcher; new output columns |
| `config.py` | MODIFY | New API key settings, feature toggles |
| `templates/index.html` | MODIFY | New API toggle section in UI |
| `requirements.txt` | MODIFY | Add `dnspython` |

## New Output Columns to Add

```
mx_valid                 — yes | no | error
yelp_owner_found         — yes | no
yelp_owner_name          — name found on Yelp
yelp_source_url          — Yelp business URL
serp_person_found        — yes | no
serp_decision_maker      — name found in SERP snippet
serp_snippet             — raw snippet text
apollo_name              — name from Apollo (if used)
apollo_title             — title from Apollo (if used)
proxycurl_name           — name from Proxycurl/LinkedIn (if used)
proxycurl_title          — title from Proxycurl/LinkedIn (if used)
hunter_email             — email from Hunter DB (if found)
hunter_score             — 0-100 confidence score from Hunter
hunter_verified          — yes | no
verification_result      — valid | invalid | catch-all | unknown | not_run
```

---

## UI Additions (new section in index.html)

```
┌─ Data Sources ──────────────────────────────────────┐
│  🔍 Yelp Search         [ON]  — free                │
│  🌐 Web Search (DDG)    [ON]  — free                │
│  🔗 Apollo.io           [OFF] — ~$0.006/company     │
│     API Key: [________________________________]     │
│  🔗 Proxycurl (LinkedIn)[OFF] — ~$0.40–0.80/company │
│     API Key: [________________________________]     │
│  ✉  Hunter.io           [OFF] — ~$0.10/company      │
│     API Key: [________________________________]     │
│  ✅ MillionVerifier      [OFF] — ~$0.005/email       │
│     API Key: [________________________________]     │
└─────────────────────────────────────────────────────┘
```

Each toggle shows/hides its API key field (same pattern as existing Haiku toggle).
Each API key is passed to the backend per-run, never stored.

---

## Honest Limitations After V4

- **Very small sole traders** with no web presence, no Yelp, no LinkedIn: still difficult without manual research
- **JS-rendered websites**: still not handled — requires Playwright/Selenium (out of scope)
- **SMTP verification free**: genuinely not possible without dedicated clean-IP infrastructure
- **Catch-all domains** (~15–20% of business domains): verification returns "catch-all" not "valid/invalid" for these, regardless of which tool you use
- **Apollo local business accuracy**: Apollo is weak for sole traders and micro-businesses — Yelp + Proxycurl are better for that segment
- **Proxycurl + small local businesses**: Many tradespeople have no LinkedIn — Yelp works better for them
