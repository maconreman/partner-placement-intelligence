"""
config.py — Single source of truth for all NLP/scoring/classification constants.
Port of config.ts (M3.4) with M3.5 additions.
"""
import os
import json

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

# ── Ops-editable data files (M8.1) ────────────────────────────────────────────
# EXCLUDED_DOMAINS and CLIENT_VERTICAL_MAP moved out of this file into
# backend/data/*.json so the partnerships/ops team can edit domain lists via a
# one-line Git diff without touching Python. This file keeps engineer-owned
# constants only (weights, thresholds, vocabularies).
#
# FAIL CLOSED: a missing or malformed data file raises at import time. It must
# never degrade to an empty mapping — an empty EXCLUDED_DOMAINS silently
# restores all ~107 raw GSC properties to the picker, and an empty vertical map
# collapses every client into "Other".
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _normalize_host(value: str) -> str:
    """Reduce a hostname to the same bare form routers/domains.py::_to_host emits."""
    s = str(value).lower().strip()
    for prefix in ("sc-domain:", "https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    if s.startswith("www."):
        s = s[4:]
    return s.split("/")[0]


def _load_data_file(filename: str) -> dict:
    path = os.path.join(_DATA_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Required data file is missing: {path}. "
            "It ships with the repo and is copied into the image by "
            "`COPY backend/ ./backend/` in the Dockerfile."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{path} is not valid JSON ({exc}). Fix the syntax and redeploy; "
            "the app refuses to boot rather than run with an empty mapping."
        ) from exc
    if not isinstance(data, dict) or not data:
        raise RuntimeError(f"{path} must contain a non-empty JSON object.")
    return data


# ── Excluded domains ──────────────────────────────────────────────────────────
# backend/data/excluded_domains.json — two keys, both required:
#   inactive_clients   Set A (from M5): churned/inactive client domains still
#                      connected to the GSC accounts.
#   generic_platforms  Set B (M7): generic platforms, competitors, internal.
# Both sets are unioned. Historically these were merged incorrectly (Set B
# replaced Set A), which is why the provenance is preserved as separate keys.
_EXCLUDED_RAW = _load_data_file("excluded_domains.json")
for _key in ("inactive_clients", "generic_platforms"):
    if _key not in _EXCLUDED_RAW or not isinstance(_EXCLUDED_RAW[_key], list):
        raise RuntimeError(
            f"excluded_domains.json must define a list under '{_key}'. "
            "Both sets are required — dropping one re-opens the picker to "
            "domains that should never appear."
        )
EXCLUDED_DOMAINS: set[str] = {
    _normalize_host(d)
    for _key in ("inactive_clients", "generic_platforms")
    for d in _EXCLUDED_RAW[_key]
    if str(d).strip()
}

# ── Client vertical map ───────────────────────────────────────────────────────
# backend/data/client_verticals.json — one key per vertical, each an
# alphabetized array of bare hostnames. Vertical-keyed arrays keep additions to
# a single-line diff and make merge conflicts unlikely.
# Flattened here to hostname → vertical, which is what routers/domains.py wants.
# FFG-owned domains are tagged "FFG" via the isFfg flag, not through this map.
_VERTICALS_RAW = _load_data_file("client_verticals.json")
CLIENT_VERTICAL_MAP: dict[str, str] = {}
for _vertical, _hosts in _VERTICALS_RAW.items():
    if not isinstance(_hosts, list):
        raise RuntimeError(
            f"client_verticals.json: '{_vertical}' must map to a list of hostnames."
        )
    for _host in _hosts:
        _h = _normalize_host(_host)
        if not _h:
            continue
        _existing = CLIENT_VERTICAL_MAP.get(_h)
        if _existing and _existing != _vertical:
            raise RuntimeError(
                f"client_verticals.json: '{_h}' appears under both "
                f"'{_existing}' and '{_vertical}'. Each hostname belongs to "
                "exactly one vertical."
            )
        CLIENT_VERTICAL_MAP[_h] = _vertical

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
GATE_SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days

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

# ── Non-placement exclusions (M9.2) ───────────────────────────────────────────
# Pages that are never valid placements and must be dropped from the ranked
# results entirely, no matter how they were categorized or how well they match.
# This is the single authoritative list; quickmatch.py::quick_match_candidates
# enforces it as the one choke point every result passes through before scoring.
#
# Two gates, because non-placements arrive two different ways:
#   1. By category — pages classify.py assigned to a structural, non-content
#      category. "Hub" was already excluded in quickmatch; Homepage and Contact
#      are added here (a site's front door and its contact page are not
#      placements). These are matched against PageRow.page_category.
#   2. By URL — "Category" and "Tag" are NOT categories in this system; they are
#      archive/taxonomy URLs (/category/..., /tag/...). classify.py never labels
#      them, so they slip through as "Other" and can rank. They are excluded by
#      matching the URL path instead. crawl.py already skips these for metadata
#      via _SKIP_URL_PATTERNS; this makes "not crawled" and "not a placement"
#      agree by driving the placement decision from a declared list here.
NON_PLACEMENT_CATEGORIES: set[str] = {"Hub", "Homepage", "Contact"}

# URL path fragments that mark an archive/taxonomy page rather than a placement.
# Matched case-insensitively against the lowercased URL path.
NON_PLACEMENT_URL_PATTERNS: tuple[str, ...] = (
    "/category/", "/categories/", "/tag/", "/tags/",
)

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
