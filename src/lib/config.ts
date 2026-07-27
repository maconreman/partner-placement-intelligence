// ─────────────────────────────────────────────────────────────────────────────
// config.ts  —  Port of CELL 1 (Configuration · M3.4)
//
// Single source of truth for every NLP / scoring / classification constant.
// Values are carried over verbatim from the Colab notebook. Dormant constants
// are kept (tagged DORMANT) so the revive paths remain documented, exactly as
// in the notebook's dormant register.
// ─────────────────────────────────────────────────────────────────────────────

// ── Models ──────────────────────────────────────────────────────────────────
export const HF_NLI_MODEL = "facebook/bart-large-mnli"; // DORMANT(M3.3) · revive→ future NLI stage
export const HF_EMBED_MODEL = "BAAI/bge-base-en-v1.5";
export const HF_CROSS_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"; // DORMANT(M3.3) · revive→ cross-encoder rerank

// HF token is read from the environment (never hardcoded in the deployed surface).
export const HF_API_TOKEN = process.env.HF_API_TOKEN ?? "";

// ── GSC accounts ────────────────────────────────────────────────────────────
export const GSC_ACCOUNT_EMAILS: Record<string, string> = {
  data: "data@nexusmarketing.com",
  analytics: "analytics@nexusmarketing.com",
};

export const FFG_OWNED_DOMAINS = [
  "sc-domain:doublethedonation.com", "sc-domain:ecardwidget.com",
  "sc-domain:npoinfo.com", "sc-domain:360matchpro.com",
  "sc-domain:nonprofitssource.com", "sc-domain:recharity.ca",
  "sc-domain:fundraisingip.com", "sc-domain:topnonprofits.com",
  "sc-domain:crowd101.com", "sc-domain:kwala.co",
  "sc-domain:nxunite.com", "sc-domain:schoolmoney.org",
  "sc-domain:bestfundraisingideas.com",
];

// ── Excluded domains ─────────────────────────────────────────────────────────
// Domains in this set are filtered out of the domain selection grid before it
// renders, so users can never select them. Match is hostname-based with the
// `sc-domain:` prefix and any leading `www.` stripped — every URL variant of
// the same site collapses to one entry (e.g. signupgenius.com/how-to-use/fundly
// and signupgenius.com/ both resolve to "signupgenius.com").
export const EXCLUDED_DOMAINS = new Set<string>([
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
]);

// ── Scoring scale / tiers ───────────────────────────────────────────────────
export const SCORE_MIN = 0.0;
export const SCORE_MAX = 10.0;
export const TIER_PRIORITY = 7.0;
export const TIER_STRONG = 4.0;

export const SEO_WEIGHT_CLICKS = 0.5;
export const SEO_WEIGHT_IMPRESSIONS = 0.3;
export const SEO_WEIGHT_POSITION = 0.2;

// Composite rank split (Stage 9).
export const COMPOSITE_WEIGHT_RELEVANCE = 0.7;
export const COMPOSITE_WEIGHT_SEO = 0.3;

// ── NLP / relevance config ──────────────────────────────────────────────────
export const NLP_SHORTLIST_CAP = 1000;
export const NLP_BATCH_SIZE = 250;
export const NLP_EMBED_WORKERS = 4;
export const RELEVANCE_MIN_THRESHOLD = 5.0;
export const QUERY_ONLY_MIN_THRESHOLD = 6.5; // escalated floor for Query-only matches
export const CONTRASTIVE_WEIGHT = 0.15; // hard-negative subtraction multiplier
export const META_CACHE_STALENESS = 90; // quarterly (days)
export const _DRIFT_NEGATIVES = [
  "writing", "writer", "grant writing", "ai writer", "proposal software", "grant proposal",
];

// HF request tuning (live values are inline; these mirror the notebook's dormant register).
export const HF_REQUEST_TIMEOUT = 120; // DORMANT · live: 90/60
export const HF_RETRY_WAIT = 20; // DORMANT · live: sleep 15

// ── GSC fetch ───────────────────────────────────────────────────────────────
export const GSC_ROW_LIMIT = 25_000;
export const GSC_MAX_WORKERS = 8;

// ── Sheets caches ───────────────────────────────────────────────────────────
export const GSC_CACHE_SHEET_NAME = "FFG Universe GSC Cache";
export const GSC_CACHE_STALENESS = 7; // days
export const META_CACHE_SHEET_NAME = "FFG Universe Metadata Cache";

// ── BigQuery warehouse (DORMANT) ────────────────────────────────────────────
// BigQuery is the PRIMARY read path when provisioned; the Google Sheets cache
// is the automatic fallback. Activation is driven by the environment so a
// deployment without a warehouse degrades gracefully (no code change needed):
//   • Set BQ_PROJECT_ID + BQ_DATASET_ID + GCP_SERVICE_ACCOUNT_JSON → BigQuery on.
//   • Leave any of them unset → the tool uses the Sheets cache as before.
// See BIGQUERY_SETUP.md for the one-time provisioning steps.
export const BQ_PROJECT_ID = process.env.BQ_PROJECT_ID ?? "";
export const BQ_DATASET_ID = process.env.BQ_DATASET_ID ?? "";
export const BQ_TABLE_ID = process.env.BQ_TABLE_ID ?? "gsc_data";
export const BQ_META_TABLE_ID = process.env.BQ_META_TABLE_ID ?? "page_metadata";

// True only when every BigQuery prerequisite is present. BigQuery is now the
// SOLE storage layer — there is no Google Sheets fallback. If these are unset
// the tool runs live-only (fetch + crawl every run) and persists nothing.
export const USE_BIGQUERY = Boolean(
  BQ_PROJECT_ID && BQ_DATASET_ID && process.env.GCP_SERVICE_ACCOUNT_JSON
);

export const DRIVE_EXPORT_FOLDER = "FFG Universe";

// ── M3.4 — Page classification ──────────────────────────────────────────────
// Layer 2: slug vocabulary per category (token-based, supplements CATEGORY_PATTERNS).
export const CATEGORY_VOCAB: Record<string, Set<string>> = {
  Blog: new Set([
    "guide", "guides", "tutorial", "tips", "learn", "insights", "knowledge",
    "article", "articles", "post", "posts", "overview", "introduction",
    "explained", "what", "ultimate", "examples", "ideas", "faq", "checklist",
    "template", "templates", "statistics", "trends", "blog", "story", "stories",
  ]),
  "Product / Service": new Set([
    "software", "platform", "solution", "solutions", "pricing", "plans",
    "demo", "features", "feature", "integration", "integrations", "api",
    "tool", "tools", "service", "services", "product", "products", "capabilities",
  ]),
  "Landing Page": new Set([
    "download", "offer", "free", "start", "signup", "register",
    "quote", "trial", "webinar", "ebook",
  ]),
};

// Min distinct vocab hits for Layer 2 to commit a category. A single weak hit
// (e.g. 'partnerships' in a non-Blog URL) stays 'Other' for Layer 3 / review.
export const LAYER2_MIN_VOCAB_HITS = 2;

// Parent paths with >= this many 'Other' children are re-tagged (programmatic series).
export const PROGRAMMATIC_SERIES_MIN = 10;

// Terms generic within FFG's vertical — filtered from quick-match distinct tokens.
export const _DOMAIN_GENERICS = new Set([
  "fundraising", "fundraise", "fundraiser", "fundraisers",
  "nonprofit", "nonprofits", "donation", "donations", "donate",
  "donor", "donors", "giving", "charitable", "charity",
]);

// Layer 1: ordered structural patterns. Insertion order matters (Blog first).
export const CATEGORY_PATTERNS: Record<string, string[]> = {
  Blog: ["/blog/", "/news/", "/insights/", "/resources/", "/articles/", "/posts/", "/updates/", "/learn/", "/guides/", "/tips/", "/resource/", "/article/", "/post/", "/guide/"],
  "Landing Page": ["/lp/", "/landing/", "/campaign/", "/offer/", "/trial/", "/demo/", "/free/"],
  "Product / Service": ["/services/", "/solutions/", "/platform/", "/features/", "/products/", "/pricing/", "/plans/", "/product/", "/service/"],
  Contact: ["/contact/", "/contact-us/", "/get-in-touch/", "/reach-us/"],
};

export const FEEDBACK_VERTICAL_OPTIONS = [
  "Nonprofit", "Healthcare", "Education", "Association", "Faith", "Community", "Others",
];

export const FEEDBACK_SHEET_NAME = "FFG Universe Feedback";
export const BQ_FEEDBACK_TABLE_ID = process.env.BQ_FEEDBACK_TABLE_ID ?? "feedback";

export const HARD_NEGATIVES = [
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
];

// Output column labels (export header order is defined in rank.ts).
export const EXPORT_LABELS = {
  rank: "Rank",
  relevance: "Topic Relevance",
  seo: "SEO Score",
  type: "Page Type",
  title: "Title Tag",
  metaDesc: "Meta Description",
  h1: "H1 (All)",
  h2: "H2 (All)",
};

// GSC reports lag ~2-3 days; preset windows end this many days back.
export const GSC_LAG_DAYS = 3;
