"""
config.py — Single source of truth for all NLP/scoring/classification constants.
Port of config.ts (M3.4) with M3.5 additions.
"""
import os

# ── Models ────────────────────────────────────────────────────────────────────
HF_NLI_MODEL = "facebook/bart-large-mnli"          # DORMANT(M3.3)
HF_EMBED_MODEL = "BAAI/bge-base-en-v1.5"
HF_CROSS_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"  # DORMANT(M3.3)

HF_API_TOKEN: str = os.environ.get("HF_API_TOKEN", "")

# ── GSC accounts ──────────────────────────────────────────────────────────────
GSC_ACCOUNT_EMAILS: dict[str, str] = {
    "data": "data@nexusmarketing.com",
    "analytics": "analytics@nexusmarketing.com",
}

FFG_OWNED_DOMAINS: list[str] = [
    "sc-domain:doublethedonation.com", "sc-domain:ecardwidget.com",
    "sc-domain:npoinfo.com", "sc-domain:360matchpro.com",
    "sc-domain:nonprofitssource.com", "sc-domain:recharity.ca",
    "sc-domain:fundraisingip.com", "sc-domain:topnonprofits.com",
    "sc-domain:crowd101.com", "sc-domain:kwala.co",
    "sc-domain:nxunite.com", "sc-domain:schoolmoney.org",
    "sc-domain:bestfundraisingideas.com", "sc-domain:gettingattention.org",
]

# ── Excluded domains ──────────────────────────────────────────────────────────
# Two merged sets — both are required:
#
# Set A (M5 original): inactive / churned client domains that are still
# connected to the GSC accounts but should never appear in the picker.
# Source: M5 config.ts EXCLUDED_DOMAINS.
#
# Set B (M7 additions): generic platforms, competitors, and internal domains
# that are connected to the accounts but are not placement targets.
EXCLUDED_DOMAINS: set[str] = {
    # ── Set A: inactive client domains (from M5) ──────────────────────────────
    "meetliminal.com",
    "membershiptoolkit.com",
    "ngpvan.com",
    "nonprofiteasy.com",
    "peoplemetrics.com",
    "skyepack.com",
    "snowballfundraising.com",
    "thompsongrants.com",
    "tectonic.video",
    "catrescueclub.org",
    "cbo.io",
    "donationmatch.com",
    "lavu.com",
    "signupgenius.com",
    "laridaemc.com",
    "lever.co",
    # ── Set B: generic platforms / competitors / internal (M7) ───────────────
    "nexusmarketing.com",
    "fundly.com",
    "bloomerang.com",
    "neon-crm.com",
    "salesforce.com",
    "hubspot.com",
    "blackbaud.com",
    "qgiv.com",
    "paypal.com",
    "stripe.com",
    "donorbox.org",
    "networkforgood.com",
    "donorperfect.com",
    "classy.org",
    "rallyup.com",
    "mightycause.com",
    "givebutter.com",
    "flipcause.com",
    "virtuous.org",
    "bonterra.com",
    "charitynavigator.org",
    "guidestar.org",
}

# ── Client vertical map ───────────────────────────────────────────────────────
# Maps bare client hostnames to their Nexus vertical tag.
# Source: Nexus Client Tags spreadsheet.
# Used by /api/domains to add a `vertical` field to each domain, which powers
# the accordion grouping in the Step 1 domain picker.
# FFG-owned domains are implicitly tagged "FFG" via the isFfg flag.
CLIENT_VERTICAL_MAP: dict[str, str] = {
    # Nonprofit
    "32auctions.com": "Nonprofit",
    "pursuant.com": "Nonprofit",
    "givingdna.com": "Nonprofit",
    "teamallegiance.com": "Nonprofit",
    "betterimpact.com": "Nonprofit",
    "bonfire.com": "Nonprofit",
    "bwf.com": "Nonprofit",
    "capitalcampaignpro.com": "Nonprofit",
    "cfoleverage.com": "Nonprofit",
    "charityengine.net": "Nonprofit",
    "chazinandcompany.com": "Nonprofit",
    "convergentnonprofit.com": "Nonprofit",
    "cornershopcreative.com": "Nonprofit",
    "deepwhydesign.com": "Nonprofit",
    "dnlomnimedia.com": "Nonprofit",
    "doubleknot.com": "Nonprofit",
    "fionta.com": "Nonprofit",
    "foundant.com": "Nonprofit",
    "501c3.org": "Nonprofit",
    "freewill.com": "Nonprofit",
    "funds2orgs.com": "Nonprofit",
    "sneakers4funds.com": "Nonprofit",
    "golfstatus.org": "Nonprofit",
    "dormienetworkfoundation.org": "Nonprofit",
    "grahampelton.com": "Nonprofit",
    "handbid.com": "Nonprofit",
    "teamheller.com": "Nonprofit",
    "infinitegiving.com": "Nonprofit",
    "jacksonriver.com": "Nonprofit",
    "jitasagroup.com": "Nonprofit",
    "kanopi.com": "Nonprofit",
    "meyerpartners.com": "Nonprofit",
    "mogli.com": "Nonprofit",
    "omnially.com": "Nonprofit",
    "onecause.com": "Nonprofit",
    "orrgroup.com": "Nonprofit",
    "socialimpactsolutions.com": "Nonprofit",
    "tatango.com": "Nonprofit",
    "uncommongiving.com": "Nonprofit",
    "upmetrics.com": "Nonprofit",
    "winspireme.com": "Nonprofit",
    "yptc.com": "Nonprofit",
    "bloomerang.co": "Nonprofit",
    "kindful.com": "Nonprofit",
    # Education
    "99pledges.com": "Education",
    "abcfundraising.com": "Education",
    "brightmontacademy.com": "Education",
    "donorsearch.net": "Education",
    "insightfulphilanthropy.com": "Education",
    "omegafi.com": "Education",
    "read-a-thon.com": "Education",
    "topclasslms.com": "Education",
    # Association
    "mya2zevents.com": "Association",
    "clowder.com": "Association",
    "higherlogic.com": "Association",
    "imis.com": "Association",
    "kellencompany.com": "Association",
    "getopenwater.com": "Association",
    "strategicassociationsolutions.com": "Association",
    "fonteva.com": "Association",
    "protechassociates.com": "Association",
    # Healthcare
    "arcadia.io": "Healthcare",
    "practicesuite.com": "Healthcare",
    # Community
    "astronsolutions.net": "Community",
    "bottlepos.com": "Community",
    "circuitree.com": "Community",
    "communitypass.net": "Community",
    "info.dancestudio-pro.com": "Community",
    "accudata.com": "Community",
    "alumnifinder.com": "Community",
    "deepsync.com": "Community",
    "etailpet.io": "Community",
    "gingrapp.com": "Community",
    "juvare.com": "Community",
    "massagebook.com": "Community",
    "smartwaiver.com": "Community",
    "thriftcart.com": "Community",
    "unwrapit.com": "Community",
    "jazzhr.com": "Community",
    "www.jazzhr.com": "Community",
    "jobvite.com": "Community",
    "www.jobvite.com": "Community",
    "www.lever.co": "Community",
    # Faith
    "shulware.com": "Faith",
    "wonderink.org": "Faith",
}

# ── Scoring scale / tiers ─────────────────────────────────────────────────────
SCORE_MIN = 0.0
SCORE_MAX = 10.0
TIER_PRIORITY = 7.0
TIER_STRONG = 4.0

SEO_WEIGHT_CLICKS = 0.5
SEO_WEIGHT_IMPRESSIONS = 0.3
SEO_WEIGHT_POSITION = 0.2

COMPOSITE_WEIGHT_RELEVANCE = 0.7
COMPOSITE_WEIGHT_SEO = 0.3

# ── NLP / relevance config ────────────────────────────────────────────────────
NLP_SHORTLIST_CAP = 1000
NLP_BATCH_SIZE = 250
NLP_EMBED_WORKERS = 4
RELEVANCE_MIN_THRESHOLD = 5.0
QUERY_ONLY_MIN_THRESHOLD = 6.5
CONTRASTIVE_WEIGHT = 0.15
META_CACHE_STALENESS = 90  # days

_DRIFT_NEGATIVES: list[str] = [
    "writing", "writer", "grant writing", "ai writer", "proposal software", "grant proposal",
]

# ── GSC fetch ─────────────────────────────────────────────────────────────────
GSC_ROW_LIMIT = 25_000
# M7: env-tunable so ops can push concurrency to 24 on a large account set
# without a code change. Defaults to 16 (raised from M5's conservative 8).
GSC_MAX_WORKERS = int(os.environ.get("GSC_MAX_WORKERS", "16"))
GSC_LAG_DAYS = 3

# ── App access gate (M7 — replaces M5 Basic Auth middleware) ──────────────────
# A single shared username + password that protects every route. Credentials
# live ONLY in these env vars. Leave BOTH unset to disable the gate (local dev).
# When set, every visitor must sign in at /login before the app loads.
GATE_USER: str = os.environ.get("APP_GATE_USER", "")
GATE_PASS: str = os.environ.get("APP_GATE_PASS", "")
# Secret used to sign the gate session cookie (HMAC). Required when the gate is
# active; a stable value keeps sessions valid across restarts.
GATE_SECRET: str = os.environ.get("APP_GATE_SECRET", "")
GATE_ENABLED: bool = bool(GATE_USER and GATE_PASS)
GATE_SESSION_MAX_AGE = 8 * 3600  # 8 hours — one working session

# ── Cron auto-sync secret (M7) ────────────────────────────────────────────────
# Secret key required by GET /api/admin/auto-sync/{secret_key}. The endpoint is
# unauthenticated (no session cookie) so it can be called by a cron bot. The
# secret in the URL is the only security control. Generate with:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
AUTO_SYNC_SECRET: str = os.environ.get("AUTO_SYNC_SECRET", "")

# ── BigQuery warehouse ────────────────────────────────────────────────────────
BQ_PROJECT_ID: str = os.environ.get("BQ_PROJECT_ID", "")
BQ_DATASET_ID: str = os.environ.get("BQ_DATASET_ID", "")
BQ_TABLE_ID: str = os.environ.get("BQ_TABLE_ID", "gsc_data")
BQ_META_TABLE_ID: str = os.environ.get("BQ_META_TABLE_ID", "page_metadata")
BQ_FEEDBACK_TABLE_ID: str = os.environ.get("BQ_FEEDBACK_TABLE_ID", "feedback")

USE_BIGQUERY: bool = bool(
    BQ_PROJECT_ID and BQ_DATASET_ID and os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
)

DRIVE_EXPORT_FOLDER = "FFG Universe"

# ── Page classification ───────────────────────────────────────────────────────
CATEGORY_VOCAB: dict[str, set[str]] = {
    "Blog": {
        "guide", "guides", "tutorial", "tips", "learn", "insights", "knowledge",
        "article", "articles", "post", "posts", "overview", "introduction",
        "explained", "what", "ultimate", "examples", "ideas", "faq", "checklist",
        "template", "templates", "statistics", "trends", "blog", "story", "stories",
    },
    "Product / Service": {
        "software", "platform", "solution", "solutions", "pricing", "plans",
        "demo", "features", "feature", "integration", "integrations", "api",
        "tool", "tools", "service", "services", "product", "products", "capabilities",
    },
    "Landing Page": {
        "download", "offer", "free", "start", "signup", "register",
        "quote", "trial", "webinar", "ebook",
    },
}

LAYER2_MIN_VOCAB_HITS = 2
PROGRAMMATIC_SERIES_MIN = 10

_DOMAIN_GENERICS: set[str] = {
    "fundraising", "fundraise", "fundraiser", "fundraisers",
    "nonprofit", "nonprofits", "donation", "donations", "donate",
    "donor", "donors", "giving", "charitable", "charity",
}

CATEGORY_PATTERNS: dict[str, list[str]] = {
    "Blog": [
        "/blog/", "/news/", "/insights/", "/resources/", "/articles/", "/posts/",
        "/updates/", "/learn/", "/guides/", "/tips/", "/resource/", "/article/",
        "/post/", "/guide/",
    ],
    "Landing Page": ["/lp/", "/landing/", "/campaign/", "/offer/", "/trial/", "/demo/", "/free/"],
    "Product / Service": [
        "/services/", "/solutions/", "/platform/", "/features/", "/products/",
        "/pricing/", "/plans/", "/product/", "/service/",
    ],
    "Contact": ["/contact/", "/contact-us/", "/get-in-touch/", "/reach-us/"],
}

FEEDBACK_VERTICAL_OPTIONS: list[str] = [
    "Nonprofit", "Healthcare", "Education", "Association", "Faith", "Community", "Others",
]

HARD_NEGATIVES: list[str] = [
    "google ads", "google adwords", "paid advertising", "ppc management", "ad management software",
    "google ad grants", "ad grant agency", "google grant agency", "campaign management software",
    "digital advertising agency", "media buying", "sorority", "fraternity", "greek life",
    "greek management software", "alumni management software", "campus management",
    "employee payroll", "payroll software", "bookkeeping", "accounting software", "hr software",
    "human resources software", "restaurant management software", "hotel management software",
    "retail management software", "inventory management software", "point of sale", "pos software",
    "property management software", "real estate software", "it management software",
    "project management software", "customer relationship management", "crm software",
    "summer camp", "sleepaway camp", "overnight camp", "day camp", "camp counselor",
    "outdoor education", "wilderness camp", "youth camp", "sports camp", "music camp",
    "art camp", "best camp", "top camp", "camp activities", "camp programs", "camping software",
    "campground management", "rv park management", "best gym", "top gym", "gym membership",
    "fitness classes", "personal trainer", "spin class", "yoga classes", "pilates",
    "dog boarding", "pet hotel", "dog walker",
]

EXPORT_LABELS: dict[str, str] = {
    "rank": "Rank",
    "relevance": "Topic Relevance",
    "seo": "SEO Score",
    "type": "Page Type",
    "title": "Title Tag",
    "metaDesc": "Meta Description",
    "h1": "H1 (All)",
    "h2": "H2 (All)",
    "content_type": "Content Type",
}