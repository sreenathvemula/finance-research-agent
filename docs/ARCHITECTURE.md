# Architecture & setup

## How it fits together

```
                    ┌──────────────────────────────────────────┐
   Claude (harness) │  CLAUDE.md  +  .claude/skills/ (workflows)│
        │           └──────────────────────────────────────────┘
        │ MCP (stdio)
        ▼
   agent/mcp_server.py ──► agent/tools.py  (28 findata tools)
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
   data_access.py         fundamentals.py           rag.py
   (statements,           (health, forensics,       (Chroma vector index,
    prices, macro)         valuation, DuPont…)        Q&A-aware chunking)
        │                       │                       │
        ▼                       ▼                       ▼
   ┌─────────────────────  data/  (LOCAL ONLY — gitignored)  ─────────────────────┐
   │ companies/<SYM>/ screener.json · tijori.json · prices · concalls · filings   │
   │ structured/ XBRL · index/ chroma + parquet caches · reference/ entities      │
   └─────────────────────────────────────────────────────────────────────────────┘
        ▲
   scripts/   numbered ETL pipeline (01–36): scrape → parse → convert → index
```

## The tool suite (`agent/tools.py`)

| Area | Tools |
|---|---|
| Identity / overview | `resolve_company`, `company_overview`, `peers_and_index` |
| Statements & health | `financial_statements`, `financial_health`, `forensic_checks`, `capital_allocation`, `shareholding_trends`, `xbrl_quarterly` |
| Valuation & price | `valuation_summary`, `technicals_momentum`, `price_analytics`, `price_history` |
| Business & moat | `business_profile`, `competitive_position`, `supply_chain`, `management_guidance` |
| Screening / sectors | `screen_stocks`, `screen_by_year`, `screen_consistency`, `sector_analysis` |
| Documents (RAG) | `search_documents`, `topic_timeline` |
| Reference / macro | `macro_data`, `index_data`, `insider_trading` |
| Data freshness | `refresh_company_data` — re-scrape one company on demand |

## The skills (`.claude/skills/`)

Deep, multi-step research workflows encoded as procedural skills (not improvised):

`investing-principles` (the shared rulebook) · `company-dossier` · `financial-forensics`
· `management-credibility` · `swot-study` · `screen-to-shortlist` · `ethics-assessment`
· `risk-profile-screen`.

## Design decisions worth calling out

- **Structured vs. RAG by design.** Numbers are served losslessly by direct tools;
  vector search is used *only* for unstructured text. Embedding numeric tables is how RAG
  systems misquote figures — this pipeline never does it.
- **Forensic scores computed, not looked up.** Altman Z''-Score, Piotroski F-Score and the
  Sloan accrual ratio are calculated from raw multi-year statements with every input
  ratio shown. Where a score's inputs genuinely aren't in the data (the Beneish M-Score
  needs receivables/COGS/SG&A lines this source doesn't expose), the agent **refuses to
  approximate** rather than publish a number built on assumed inputs.
- **RAG chunking is measured, not guessed.** Chunk sizes set from the corpus's actual
  token distribution under the real tokenizer (median 526, p95 915 tokens), natural-unit
  first (one concall Q&A / one slide / one rating paragraph stays whole when it fits).
- **Embedding model chosen to fix a real bug.** Moved from `bge-small` (512-token hard
  limit, silently truncating ~half the long chunks) to `nomic-embed-text-v1.5`
  (8192-token context) with Matryoshka 256-dim truncation.
- **Incremental indexing.** A file-mtime manifest means re-runs only embed new/changed
  files; interrupting is safe.
- **Quarterly XBRL de-cumulation.** NSE XBRL reports year-to-date figures; the pipeline
  de-cumulates to true standalone quarters, cross-checks against screener, and surfaces
  business-segment revenue/result the screener CSVs don't carry.

## Setup

Requires Python 3.11 and the deps in [`../requirements.txt`](../requirements.txt) (a CUDA
GPU helps for embedding but isn't required).

```bash
conda create -n finance-ai python=3.11 && conda activate finance-ai
pip install -r requirements.txt

cp .env.template .env            # add your OWN screener/tijori cookies (optional)
cp .mcp.json.example .mcp.json   # point `command`/`cwd` at your env + repo path
```

Build a **small** local data set for the companies you want to study (not the whole
universe — see [`../DISCLAIMER.md`](../DISCLAIMER.md)):

```bash
python scripts/04_screener_scraper.py     --symbol DEEPAKNTR
python scripts/03_yfinance_prices.py      --symbol DEEPAKNTR
python scripts/02_nse_xbrl_quarterly.py   --symbol DEEPAKNTR
python scripts/09_tijori_scraper.py       --symbol DEEPAKNTR   # needs TIJORI_SESSION_ID
python -m agent.build_index --symbols DEEPAKNTR                # index docs for RAG
```

Register the MCP server (`.mcp.json`) in Claude Code / Claude Desktop and the tools +
skills + `CLAUDE.md` guidance load natively. Or run the bundled agent directly:

```bash
python -m agent.finance_agent "Screen chemical companies with ROCE > 20 and D/E < 0.3"
```
