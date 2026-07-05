"""Central paths and constants for the Finance AI Agent."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # C:\...\Finance
DATA = ROOT / "data"
COMPANIES = DATA / "companies"
REFERENCE = DATA / "reference"
STRUCTURED = DATA / "structured"
SCRIPTS = ROOT / "scripts"

# RAG index artifacts
# v3 = nomic-embed-text-v1.5 (8192-token context, fixes the bge-small 512-token
# silent-truncation bug — 50.8% of v2 chunks measured over the limit) + real-tokenizer
# natural-unit-first chunking (was a len//4 approximation that caused the bug).
INDEX_VERSION = 3
INDEX_DIR = DATA / "index"
CHROMA_DIR = INDEX_DIR / "chroma"
MANIFEST_PATH = INDEX_DIR / f"rag_manifest_v{INDEX_VERSION}.json"
SCREENER_CACHE = INDEX_DIR / "screener_metrics.parquet"
HIST_FUNDAMENTALS_CACHE = INDEX_DIR / "historical_fundamentals.parquet"
HIST_PRICE_RETURNS_CACHE = INDEX_DIR / "historical_price_returns.parquet"

INDEX_DIR.mkdir(parents=True, exist_ok=True)

# Embeddings
EMBED_MODEL = "nomic-ai/nomic-embed-text-v1.5"
EMBED_TRUST_REMOTE_CODE = True
EMBED_TRUNCATE_DIM = 256          # Matryoshka: truncates 768->256, no retraining needed;
                                  # smaller per-vector than the old 384-dim bge-small
EMBED_MAX_SEQ_LENGTH = 8192       # real hard limit — vs bge-small's 512
QUERY_PREFIX = "search_query: "   # nomic requires a task prefix on BOTH sides
DOC_PREFIX = "search_document: "
COLLECTION_NAME = f"finance_docs_v{INDEX_VERSION}"
EMBED_BATCH = 32                  # smaller than v2's 128: attention memory grows ~
                                  # quadratically with sequence length, and chunks can
                                  # now run much longer (up to ~800 real tokens by design,
                                  # occasionally more) than bge-small's hard-capped 512

# Chunking — REAL tokenizer units now (see agent.tokenization), not chars/4 approximation.
# target/max sized from the actual measured corpus distribution under BGE's real
# tokenizer (median 526, p95 915, max 1650 real tokens per a 3000-chunk sample) with a
# natural-unit-first policy: one Q&A exchange / one slide-block / one rating paragraph
# stays whole whenever it fits under TARGET_TOKENS_MAX; overlap only applies to
# sliding-window content (presentations/reports/ratings/weak-transcripts), never to
# already-complete Q&A exchanges.
TARGET_TOKENS = 400
TARGET_TOKENS_MAX = 800
OVERLAP_TOKENS = 70

ETHICS_FILE = SCRIPTS / "Ethical Investment.txt"

# xbrl intentionally excluded: audit found mixed quarterly/cumulative values,
# ghost columns, and stale coverage — quarterly numbers come from screener CSVs.
DOC_TYPES = [
    "concall_transcript",
    "concall_presentation",
    "annual_report",
    "credit_rating",
    "announcement",
]

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# Credible-source allow-list for web research. The agent passes this to the
# built-in WebSearch tool's allowed_domains so news/verification only comes from
# primary regulators, exchanges, the company itself, or reputable financial media
# — never anonymous forums, tip sheets or promotional blogs.
CREDIBLE_WEB_DOMAINS = [
    # regulators & exchanges (primary sources)
    "nseindia.com", "bseindia.com", "sebi.gov.in", "rbi.org.in",
    "mca.gov.in", "trai.gov.in", "ibbi.gov.in",
    # data / filings
    "screener.in", "trendlyne.com", "annualreports.com",
    # reputable financial press
    "moneycontrol.com", "economictimes.indiatimes.com", "business-standard.com",
    "livemint.com", "thehindubusinessline.com", "financialexpress.com",
    "reuters.com", "bloomberg.com", "cnbctv18.com", "ndtvprofit.com",
    # rating agencies
    "crisil.com", "icra.in", "careratings.com",
]
