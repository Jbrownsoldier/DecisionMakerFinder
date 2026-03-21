# config.py
# Central configuration for the Decision Maker Email Finder.
# Edit these values to tune scraping behaviour, role priorities, and scoring.

# ---------------------------------------------------------------------------
# HTTP / Scraping settings
# ---------------------------------------------------------------------------

# Seconds to wait between company requests (polite crawling)
REQUEST_DELAY_SECONDS = 1.5

# Per-request timeout in seconds
REQUEST_TIMEOUT_SECONDS = 10

# Retry count on connection error or timeout (not on 4xx/5xx)
MAX_RETRIES = 2

# Save partial progress every N rows (crash-safe resume)
PARTIAL_SAVE_EVERY = 25

# Minimum visible text length (chars) before we consider a page JS-rendered
JS_RENDER_TEXT_THRESHOLD = 200

# Browser-like user agent to reduce bot blocking
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Pages to try when looking for team/contact info
# Tried in order; all accessible pages are collected and passed to the extractor.
# ---------------------------------------------------------------------------
PAGES_TO_TRY = [
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/leadership",
    "/management",
    "/meet-the-team",
    "/staff",
    "/people",
    "/who-we-are",
    "/contact",
    "/contact-us",
]

# Pages whose path earns a +20 scoring bonus (strong signal for decision maker content)
PRIORITY_PAGES = {
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/leadership",
    "/management",
    "/meet-the-team",
}

# ---------------------------------------------------------------------------
# Contact tiering
# Tier 1 = true decision makers (owners, founders, C-suite, partners)
# Tier 2 = secondary contacts (directors, managers, coordinators)
# Used by the scoring system to weight candidates appropriately.
# ---------------------------------------------------------------------------

TIER_1_ROLES = [
    "owner",
    "founder",
    "co-founder",
    "cofounder",
    "ceo",
    "chief executive officer",
    "president",
    "managing partner",
    "managing director",
    "partner",
    "principal",
    "proprietor",
]

TIER_2_ROLES = [
    "director",
    "general manager",
    "gm",
    "operations manager",
    "office manager",
    "marketing manager",
    "manager",
    "administrator",
    "coordinator",
]

# Combined keyword list used by regex/HTML scanning strategies
# (union of tiers plus common variants)
ROLE_KEYWORDS = TIER_1_ROLES + TIER_2_ROLES + [
    "md",           # managing director abbreviation
    "vp",
    "vice president",
    "chief operating officer",
    "coo",
    "chief marketing officer",
    "cmo",
]

# Legacy alias kept for any code that still references ROLE_PRIORITY
ROLE_PRIORITY = TIER_1_ROLES + TIER_2_ROLES

# Roles that signal a support/admin contact — penalised during scoring
SUPPORT_ADMIN_ROLES = [
    "customer service",
    "customer support",
    "support",
    "receptionist",
    "assistant",
    "secretary",
    "bookkeeper",
    "accountant",
    "clerk",
    "intern",
    "hr",
    "human resources",
    "payroll",
    "it manager",
    "it support",
    "web developer",
    "developer",
    "designer",
]

# Generic email prefixes — nearby presence penalises a candidate's score
GENERIC_EMAIL_PREFIXES = {
    "info", "contact", "hello", "support", "admin",
    "office", "sales", "enquiries", "enquiry", "mail",
    "help", "noreply", "no-reply", "webmaster", "accounts",
}

# ---------------------------------------------------------------------------
# Scoring weights
# All values are additive. Final score determines status and verify_priority.
# ---------------------------------------------------------------------------

SCORE_WEIGHTS = {
    "schema_org":            50,   # found in structured JSON-LD data
    "tier1_title":           35,   # title is a Tier 1 decision-maker role
    "tier2_title":           15,   # title is a Tier 2 secondary role
    "priority_page":         20,   # found on /about /team /leadership etc.
    "html_card":             10,   # found in a team/staff card pattern
    "heading_context":       10,   # name or title in a heading/bold element
    "proximity":             10,   # name and title appear close together
    "generic_title":        -25,   # title is vague or non-leadership
    "generic_email_nearby": -30,   # a generic email (info@, support@) nearby
    "first_name_only":      -20,   # could only extract a first name
    "support_admin":        -20,   # role matches support/admin keywords
}

# Thresholds for processing_status
SCORE_THRESHOLD_STRONG = 50   # → processing_status = "ok"
SCORE_THRESHOLD_WEAK   = 20   # → processing_status = "ok_weak"
# Below SCORE_THRESHOLD_WEAK → "no_decision_maker_found"

# ---------------------------------------------------------------------------
# Free data source settings (V4)
# ---------------------------------------------------------------------------

# Check DNS MX records before generating email candidates.
# "no" = domain has no mail servers = all candidates guaranteed invalid.
# Costs nothing — pure DNS lookup, ~100ms per domain.
CHECK_MX_RECORDS = True

# Search Yelp for the business owner name.
# Works best for local service businesses with Yelp listings.
# Can be toggled per-run via the UI.
USE_YELP_SEARCH = True

# Search DuckDuckGo HTML for decision maker mentions in news/directories.
# Uses the static html.duckduckgo.com endpoint — no API key required.
# Can be toggled per-run via the UI.
USE_DDG_SEARCH = True

# Request timeout in seconds for Yelp and DDG HTTP calls
YELP_REQUEST_TIMEOUT = 8
DDG_REQUEST_TIMEOUT  = 8

# How many DuckDuckGo result snippets to scan per company
DDG_MAX_RESULTS = 3

# ---------------------------------------------------------------------------
# AI Fallback settings (Claude Haiku)
# ---------------------------------------------------------------------------

# Default state — overridden per-run by the UI toggle.
# Set to True here if you want it always on when using the CLI.
USE_AI_FALLBACK = False

# Your Anthropic API key. Can be set here or entered in the web UI each run.
ANTHROPIC_API_KEY = ""

# Only call Haiku if page text is at least this many characters
HAIKU_MIN_TEXT_LENGTH = 300

# Max characters of page text sent to Haiku (keeps token cost low)
HAIKU_TEXT_LIMIT = 3000

# ---------------------------------------------------------------------------
# V5 Free additions
# ---------------------------------------------------------------------------

# Playwright headless browser — re-fetches JS-rendered pages using real Chromium.
# Off by default. One-time setup required:
#   pip3 install playwright
#   playwright install chromium
# Slows processing to ~5-8 sec per JS page. Biggest single improvement for find rate.
USE_PLAYWRIGHT = False

# Milliseconds to wait after Playwright page load (lets JS finish rendering)
PLAYWRIGHT_WAIT_MS = 2000

# Scrape the BBB (Better Business Bureau) for verified US business principal names.
# Best for US local businesses. Hit rate ~25-40% for BBB-listed companies.
# Can be toggled per-run via the UI.
USE_BBB_SEARCH = True

# Search DuckDuckGo for real email addresses mentioned on third-party sites:
# Query: "@domain.com" -site:domain.com
# Replicates Hunter.io's indexed database approach in real-time.
# Can be toggled per-run via the UI.
USE_WEB_EMAIL_SEARCH = True

# Search GitHub code for domain emails committed to public repos.
# Best for tech companies, agencies, SaaS firms.
# Can be toggled per-run via the UI.
USE_GITHUB_EMAIL_SEARCH = True

# Look up WHOIS registrant email — free, ~20-30% hit rate for small businesses
# without privacy protection. Requires: pip3 install python-whois
# Can be toggled per-run via the UI.
USE_WHOIS_LOOKUP = True

# Request timeouts (seconds) for new V5 sources
BBB_REQUEST_TIMEOUT    = 8
GITHUB_REQUEST_TIMEOUT = 8

# ---------------------------------------------------------------------------
# V6 Canadian + Google Maps sources
# ---------------------------------------------------------------------------

# Search HomeStars.ca for Canadian business owner names.
# Canada's #1 home services directory — plumbers, roofers, electricians etc.
# Best for Canadian local trades. Can be toggled per-run via the UI.
USE_HOMESTARS_SEARCH = True

# Search YellowPages Canada (yellowpages.ca) for business contact/owner names.
# Better Canadian local coverage than Yelp. Can be toggled per-run via the UI.
USE_YELLOWPAGES_CA_SEARCH = True

# Search for owner names via Google Maps review responses indexed on the web.
# Business owners often reply to Google reviews signing their name.
# Uses DuckDuckGo — no API key needed. Can be toggled per-run via the UI.
USE_GOOGLE_MAPS_SEARCH = True

# Request timeouts (seconds) for V6 sources
HOMESTARS_REQUEST_TIMEOUT    = 8
YELLOWPAGES_REQUEST_TIMEOUT  = 8

# ---------------------------------------------------------------------------
# V8: LinkedIn via Google + Jina.ai Reader
# ---------------------------------------------------------------------------

# Search DuckDuckGo for the decision maker's LinkedIn profile.
# Works by querying: site:linkedin.com/in "Company Name" (owner OR ceo OR founder ...)
# Returns name + title directly from the result title — no LinkedIn account needed.
# Best source for finding C-suite and founders. Can be toggled per-run via the UI.
USE_LINKEDIN_SEARCH = True

# Fetch sparse/JS-rendered pages via Jina.ai Reader (r.jina.ai/url).
# Returns clean readable text from any URL including JS-rendered React/Vue sites.
# FREE — no API key required. Faster than Playwright, works everywhere.
# Auto-triggered when static scrape returns < JS_RENDER_TEXT_THRESHOLD chars.
USE_JINA_READER = True

# Per-request timeout in seconds for Jina.ai Reader calls
JINA_REQUEST_TIMEOUT = 15

# ---------------------------------------------------------------------------
# V7: SMTP email verification + direct email matching
# ---------------------------------------------------------------------------

# Attempt SMTP verification of generated email candidates.
# Connects to the company's mail server on port 25 — no email is ever sent.
# Works on ~70% of domains (catch-all servers like Google Workspace cannot be verified).
# NOTE: Port 25 may be blocked on some home ISPs. Run on a VPS for full coverage.
USE_SMTP_VERIFY     = True

# Seconds to wait per SMTP connection before giving up
SMTP_TIMEOUT        = 10

# Maximum number of candidates to probe (tests the highest-confidence patterns first)
SMTP_MAX_CANDIDATES = 5

# Parallel SMTP connections per company (3 is safe and avoids most rate-limits)
SMTP_MAX_WORKERS    = 3

# Dummy MAIL FROM address for SMTP probes (never actually sends email)
SMTP_VERIFY_FROM    = "verify@gmail.com"

# ---------------------------------------------------------------------------
# Generic fallback emails (used when no named decision maker is found)
# ---------------------------------------------------------------------------
GENERIC_FALLBACK_EMAILS = [
    "info",
    "contact",
    "sales",
    "office",
    "hello",
    "admin",
    "support",
    "enquiries",
    "enquiry",
    "mail",
]
