# Decision Maker Finder

A free, locally-run alternative to Hunter.io and AnyMailFinder. Upload a CSV of companies → get senior decision makers (Owner, CEO, Founder) + verified email candidates for each one — at $0/month.

![Flask](https://img.shields.io/badge/Flask-2.x-blue) ![Python](https://img.shields.io/badge/Python-3.9%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green)

---

## What It Does

Most email outreach tools charge $50–$200/month to find decision maker emails. This app replicates the same pipeline using 100% free sources:

1. **Upload** a CSV of companies (company name + domain or website)
2. **It searches** 15 data sources simultaneously to find the Owner/CEO/Founder
3. **It verifies** which email address actually exists on the company's mail server
4. **Download** a CSV with the confirmed decision maker name, verified email, and 90+ data columns

---

## Live Demo

Run it locally in under 2 minutes:

```bash
git clone https://github.com/Jbrownsoldier/DecisionMakerFinder.git
cd DecisionMakerFinder
pip3 install -r requirements.txt
python3 app.py
# Open http://localhost:5001
```

---

## Features Implemented

### 15 Decision Maker Sources

| Source | Method | Best For | Est. Hit Rate |
|---|---|---|---|
| **Website Scraping** | Schema.org JSON-LD, HTML team cards, text regex, footer/copyright | Any company website | ~40% |
| **Yelp Business Search** | Scrapes owner section on Yelp listing | US local service businesses | ~35% |
| **DuckDuckGo SERP** | Searches news/directories for owner mentions (7 results scanned) | Any business with online presence | ~20% |
| **BBB (Better Business Bureau)** | Verified principal name from accreditation record | US accredited businesses | ~50% when listed |
| **HomeStars.ca** 🇨🇦 | Canada's #1 trades directory — "Meet the Owner" sections | Canadian plumbers, roofers, electricians | ~30% |
| **YellowPages Canada** 🇨🇦 | YP.ca business listings contact name field | Canadian local service businesses | ~25% |
| **Google Maps Owner Responses** | Owner self-introductions in review replies ("Hi, I'm John…") | Any business with Google reviews | ~20% |
| **💼 LinkedIn via Google** | DDG `site:linkedin.com/in` + Jina profile fetch — no account needed | Any business with LinkedIn presence | ~40–60% |
| **🗺️ Google Business Profile** | Jina-rendered Google Knowledge Panel parses owner/contact from listing | Any Google-indexed business | ~25–35% |
| **📘 Facebook Business** | DDG finds Facebook page → Jina reads `/about` for owner mentions | Small local businesses with Facebook | ~20–30% |
| **📰 Press Release Mining** | DDG targets PRNewswire, BusinessWire, GlobeNewswire for exec announcements | Mid-market & larger companies | ~15–25% |
| **🚀 Crunchbase** | Jina-fetches organization page, parses Founders/Leadership section | Tech, SaaS, startups | ~30–50% |
| **Web Email Discovery** | DDG `"@domain.com" -site:domain.com` finds emails on 3rd-party sites | All companies | ~20–30% |
| **GitHub Email Search** | Finds work emails committed to public repos | Tech companies, agencies | ~10% |
| **WHOIS Lookup** | Registrant email when it's at the company's own domain | Small businesses, owner-registered domains | ~20–30% |
| **Claude Haiku (optional)** | AI reads the page when all free methods fail | ~$0.001/company as last resort | ~60% of remaining |

All sources feed a unified scoring pipeline — the highest-confidence result wins.

---

### Email Finding — 4 Paths

```
Path 1 — Direct email found (no guessing needed)
  Sources already found a named email matching the decision maker
  (WHOIS, web discovery, or GitHub returned john@acme.com)
  → Exact confirmed email, skip all guessing
  Coverage: ~15–25%

Path 2 — SMTP verification of candidates
  Generate 10 pattern candidates (first.last, flast, firstl, etc.)
  Connect to mail server on port 25, confirm which address exists
  → Single confirmed deliverable email
  Coverage: ~50–55% of non-catch-all domains
  (Full coverage requires VPS with port 25 open — see DEPLOY.md)

Path 3 — Catch-all domain detected
  Server accepts all addresses (Google Workspace, Office 365)
  → Best-pattern guess returned with catch_all_domain=yes flag
  Coverage: ~25–30%

Path 4 — No name found
  Falls back to info@/contact@ with pattern_confidence=none
  Coverage: ~5–10% (improved significantly by name validation fixes)
```

---

### Website Rendering — 3 Layers

| Layer | When It Triggers | Speed | Setup |
|---|---|---|---|
| **Static HTTP** | Always, first attempt | ~1–2 sec | None |
| **Jina.ai Reader** (free) | Static returns < 200 chars | ~2–4 sec | None — auto |
| **Playwright** (optional) | Jina fails, still sparse | ~5–8 sec | `pip install playwright && playwright install chromium` |

Jina.ai Reader automatically fixes React, Vue, Wix, and Squarespace sites that returned blank content before — no configuration required. It is also used to fetch LinkedIn profiles, Facebook About pages, Google Knowledge Panels, and Crunchbase organization pages.

---

### 90+ Output Columns

| Group | Columns | Contents |
|---|---|---|
| Company input | 5 | name, domain, website, city, state |
| Decision maker | 8 | name, title, confidence, source, score, page found on |
| Email | 15 | primary guess, SMTP verified email, direct email, pattern, catch-all flag, confidence |
| Candidates | 10 | candidate_1 through candidate_10 |
| Per-source (V4–V9) | 45 | Yelp, DDG, BBB, HomeStars, YellowPages, Google Maps, LinkedIn, Google Business, Facebook, Press Releases, Crunchbase, web email, GitHub, WHOIS |
| Technical | 8 | website status, js_rendered (static/jina/playwright), notes |

---

### Business-Word Blacklist

Prevents false positives like "By Inc", "Toronto Plumbing", or "All Rights Reserved" from being extracted as person names. 90+ words filtered including all entity types (Inc, Ltd, LLC), common trade words (Plumbing, Electrical, HVAC), and boilerplate terms.

Progressive copyright recovery: `"© 2024 John Smith Plumbing Inc"` → tries shorter prefixes until finding a valid person name (`"John Smith"`).

---

### Flask Web UI

- Drag-and-drop CSV upload
- Per-source toggle switches (enable/disable each source individually)
- Real-time progress bar with found/not-found counters
- One-click CSV download when complete
- Optional Claude Haiku AI toggle with API key field

---

### VPS Deployment

Full deployment guide in `DEPLOY.md` + automated `deploy.sh` script for Ubuntu 22.04.

**Why a VPS matters:** Home ISPs block outbound port 25, which disables SMTP verification. A $5/month VPS (Hetzner, DigitalOcean, Vultr) has port 25 open by default — unlocking confirmed email verification for ~70% of domains.

```bash
# One-command deploy to any Ubuntu 22.04 VPS
scp deploy.sh root@YOUR_VPS_IP:/root/
ssh root@YOUR_VPS_IP "chmod +x deploy.sh && ./deploy.sh"
```

---

## Accuracy vs Paid Services

| Metric | This App | AnyMailFinder / Hunter.io |
|---|---|---|
| Decision maker name found | ~70–80% | N/A (they take name as input) |
| Email candidate generated | 100% | 100% |
| Email SMTP verified | ~50–55%* | ~70–80% |
| Catch-all flagged | ✅ Yes | ✅ Yes |
| Cost per company | **$0** (or ~$0.001 with AI) | $0.05–$0.10 |
| Monthly cost | **$0** | $50–$200+ |

*Closes to ~70% when deployed on a VPS with port 25 open.

AnyMailFinder's "97% accuracy" means: *when they return a verified email, 97% of the time it's valid* — not that they find emails for 97% of companies. Their actual coverage rate is ~60–75%, the same as this app on a VPS.

---

## Input CSV Format

```csv
company_name,domain,website,city,state
Acme Plumbing,acmeplumbing.ca,https://acmeplumbing.ca,Toronto,Ontario
City Roofing Inc,cityroofing.com,,Vancouver,BC
```

Required: `company_name` + either `domain` or `website`
Optional but recommended: `city`, `state` (improves all search source accuracy)

---

## Installation

```bash
# Clone
git clone https://github.com/Jbrownsoldier/DecisionMakerFinder.git
cd DecisionMakerFinder

# Install dependencies
pip3 install flask requests beautifulsoup4 lxml dnspython python-whois tqdm anthropic

# Run
python3 app.py

# Open browser
open http://localhost:5001
```

### Optional: Playwright for heavy JS sites
```bash
pip3 install playwright
playwright install chromium
# Then enable the Playwright toggle in the UI
```

---

## Configuration

All settings are in `config.py`. Key options:

```python
# Decision maker sources
USE_YELP_SEARCH              = True   # Yelp business owner scraping
USE_DDG_SEARCH               = True   # DuckDuckGo web search (7 results/query)
USE_BBB_SEARCH               = True   # Better Business Bureau
USE_HOMESTARS_SEARCH         = True   # HomeStars.ca (Canada)
USE_YELLOWPAGES_CA_SEARCH    = True   # YellowPages Canada
USE_GOOGLE_MAPS_SEARCH       = True   # Google Maps owner responses
USE_LINKEDIN_SEARCH          = True   # LinkedIn via DDG
USE_LINKEDIN_JINA_FETCH      = True   # Fetch full LinkedIn profile via Jina
USE_GOOGLE_BUSINESS_SEARCH   = True   # Google Knowledge Panel via Jina
USE_FACEBOOK_SEARCH          = True   # Facebook Business About page
USE_PRESS_RELEASE_SEARCH     = True   # PRNewswire / BusinessWire / GlobeNewswire
USE_CRUNCHBASE_SEARCH        = True   # Crunchbase founders/CEO
# Email & verification
USE_WEB_EMAIL_SEARCH         = True   # Web email discovery
USE_GITHUB_EMAIL_SEARCH      = True   # GitHub code search
USE_WHOIS_LOOKUP             = True   # WHOIS registrant email
USE_JINA_READER              = True   # Jina.ai JS rendering (free)
USE_PLAYWRIGHT               = False  # Playwright (requires install)
USE_SMTP_VERIFY              = True   # SMTP email verification
USE_AI_FALLBACK              = False  # Claude Haiku (requires API key)
```

---

## Changelog

### V9 — New Sources + LinkedIn Profile Fetch
- **4 new decision maker sources:** Google Business Profile, Facebook Business, Press Release Mining, Crunchbase
- **LinkedIn Jina enhancement:** after finding a LinkedIn URL via DDG, fetches the full profile via Jina for more accurate name + title extraction
- **DDG_MAX_RESULTS 3→7:** checks 2× more SERP snippets per query across all DDG-based sources
- **12 new output columns** for the new sources
- **90+ total output columns** (up from 79)
- **15 total external sources** (up from 11)

### V8 — LinkedIn via Google
- LinkedIn personal profile discovery via DDG `site:linkedin.com/in` — no LinkedIn account or API needed

### V6–V7 — Canadian Sources + Direct Email Match + SMTP
- HomeStars.ca, YellowPages Canada, Google Maps owner response search
- Direct email match when WHOIS/web/GitHub return a named email for the decision maker
- SMTP verification with catch-all detection

### V5 — Jina.ai + BBB + Web Email Discovery
- Jina.ai Reader for JS-rendered websites
- Better Business Bureau principal name lookup
- Web email discovery via DDG third-party indexing
- GitHub email search

---

## Roadmap — Planned Improvements

### 🔬 AI Researcher Agent (Next Up)
An agentic Claude workflow where the AI autonomously searches the web using a custom system prompt, finds the owner name, actual email, and all social media profiles (LinkedIn, Instagram, Twitter, Facebook) in a single pass. Returns structured JSON. Each company costs ~$0.003–0.005 in Haiku tokens. This is the single biggest accuracy improvement remaining.

**System prompt design:**
> "You are a helpful expert researcher. Find the business owner of the given company. Use web search to find their name, email address, LinkedIn, Instagram, Twitter, and Facebook. Return only JSON."

### 📊 Confidence Score on Final Email
A composite confidence percentage on every output row so you can filter by threshold before sending. Formula: `direct_found=100%, smtp_verified=95%, pattern_high+name_found=70%, pattern_none=35%`. Lets you set a minimum confidence and only send to rows above it.

### 🔄 Bounce Feedback Loop
Feed bounce/open data back from your email sending tool to improve pattern ranking. If `first.last@` bounces 80% of the time for a certain domain provider, down-rank it. Over time the app learns which patterns are most reliable per industry and hosting type.

### 🌐 Bing Search API Fallback
DuckDuckGo rate-limits under heavy load, which affects multiple features simultaneously. Bing Search API has a free tier (1,000 calls/month) and can serve as a fallback when DDG returns empty results.

### 🔐 HTTPS + Auth for VPS
When running on a VPS accessible from the internet, add Nginx reverse proxy + Let's Encrypt TLS + simple password protection so the UI isn't open to the public. One-command setup with Certbot.

### 📱 Webhook / n8n Integration
POST results to a webhook URL as each company completes, instead of waiting for the full CSV. Enables real-time integration with n8n, Make (Integromat), Zapier, or any CRM without polling.

### 📬 Email Warmup Integration
After finding a verified email, automatically add it to an email warmup sequence (Instantly, Lemlist, Smartlead) via API. Closes the loop from discovery to outreach in one workflow.

---

## Project Structure

```
DecisionMakerFinder/
├── app.py              # Flask web server + job queue
├── main.py             # Pipeline orchestration (90+ output columns)
├── scraper.py          # Website fetcher + Jina.ai + Playwright
├── searcher.py         # All 15 external sources + Jina helper
├── extractor.py        # On-page name extraction (5 strategies + AI)
├── email_gen.py        # Email candidate generation + direct match
├── smtp_verify.py      # SMTP verification (check_smtp, detect_catch_all)
├── verifier.py         # MX record lookup + SMTP re-exports
├── cleaner.py          # Domain/URL normalisation utilities
├── config.py           # All feature flags and timeouts
├── templates/
│   └── index.html      # Web UI (toggles, progress, download)
├── DEPLOY.md           # VPS deployment guide
├── deploy.sh           # Automated Ubuntu 22.04 setup script
├── requirements.txt    # Python dependencies
├── input_example.csv   # Sample input format
└── output_example.csv  # Sample output format
```

---

## License

MIT — free to use, modify, and deploy commercially.

---

## Contributing

Pull requests welcome. If you add a new data source, follow the pattern in `searcher.py`:
1. Create `_empty_sourcename()` factory
2. Wrap everything in `try/except` returning the empty factory
3. Add config toggle in `config.py`
4. Wire into `main.py` OUTPUT_COLUMNS + `process_row()` + external candidates pool
5. Add UI toggle in `templates/index.html`
