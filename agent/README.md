# Finance AI Agent

Claude Agent SDK agent for Indian-equities research over the local data lake
(`data/`), with RAG over documents, live technicals, valuation, screening,
macro/index data, and web search for anything newer than the local data.

## What it can do

| Capability | Backed by |
|---|---|
| General company Q&A | profile cards, financial CSVs, screener data |
| Deep research | RAG over concalls (Q&A-level), annual reports, credit ratings, announcements, XBRL + WebSearch |
| Stock screening (no advice) | ~3,100-company metrics table: valuation, quality, momentum filters |
| Company performance | 12y P&L/BS/CF, quarterly results, shareholding trends |
| Valuation | multiples, relative (peers / sector index / own history), 3-scenario DCF |
| Technicals & momentum | live recompute from parquet: DMA 50/200, RSI, MACD, returns 1d→3y, 52w-high distance |
| Ethics assessment | `scripts/Ethical Investment.txt` framework applied to company facts |
| Macro / index context | CPI, IIP, policy rate, G-sec, USDINR; index prices + PE/PB/yield history |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Segment quarterly results | `xbrl_quarterly` — de-cumulated NSE XBRL (fixed pipeline): P&L lines + business-segment revenue/result per quarter |
| Latest info | WebSearch / WebFetch built-in tools |
| On-demand data refresh | `refresh_company_data` — re-scrapes one company (screener/tijori/prices/xbrl) and promotes the result into `data/companies/<SYM>/`, since the data lake is a point-in-time snapshot, not live |
| On-demand data refresh | `refresh_company_data` — re-scrapes one company (screener/tijori/prices/xbrl) and promotes the result into `data/companies/<SYM>/`, since the data lake is a point-in-time snapshot, not live |

**Hard rule:** the agent screens and analyses but never gives buy/sell/hold advice.

## Setup (one-time)

```powershell
conda activate finance-ai            # env with all deps (see ../requirements.txt)
cd C:\path\to\finance-research-agent

# Build the RAG index — start small, go big later:
python -m agent.build_index --symbols RELIANCE,TCS,INFY
python -m agent.build_index --top 200        # top-200 by market cap
python -m agent.build_index --all            # full corpus (hours; resumable — rerun anytime)
python -m agent.build_index --stats
```

Indexing is **incremental**: a manifest of file mtimes means re-runs only embed
new/changed files. Interrupting is safe.

## Run

```powershell
python -m agent.finance_agent                          # interactive chat
python -m agent.finance_agent "Screen IT companies with ROE > 20 and PE < 30"
```

Model: uses your Claude Code default; override with `FINANCE_AGENT_MODEL=claude-opus-4-8`.

### Authentication (one-time — pick one)

**Option A — Claude subscription (Pro/Max):** run the SDK's bundled CLI once and log in:

```powershell
C:\path\to\anaconda3\envs\finance-ai\Lib\site-packages\claude_agent_sdk\_bundled\claude.exe
# type /login  ->  complete the browser sign-in  ->  exit with /quit
```

**Option B — API key (pay-per-use):** create a key at console.anthropic.com and put it in
`C:\path\to\finance-research-agent\.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

The agent loads `.env` automatically.

## Example prompts

- "What did Reliance management guide about new energy capex in the last two concalls?"
- "Screen for companies above their 200 DMA with ROCE > 20, debt/equity < 0.3, market cap > 10,000 cr"
- "Compare TCS and Infosys margins and revenue growth over the last 5 years"
- "Is Coal India ethical to invest in, per my framework?"
- "How is HDFC Bank valued vs its own history and peers?"
- "What was happening to Tata Motors around March 2024?" (date-scoped RAG + price history)
- "Latest news on Zydus USFDA observations" (web)

## Architecture

```
agent/
  config.py          paths + chunking/embedding constants
  data_access.py     entities, per-company JSON/CSV/parquet, macro, indices, peers
  rag.py             chunker (Q&A-aware for transcripts) + Chroma index + search
  build_index.py     CLI to build/refresh the index
  screener.py        metrics table (entities + technicals + valuation) + filter engine
  tools.py           MCP tools exposing all of the above (see ALL_TOOLS)
  finance_agent.py   system prompt, SDK wiring, REPL / one-shot
```

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

## What goes through RAG vs structured tools

RAG (vector search) is only for **unstructured text**. Structured data never gets
embedded — it is served losslessly by direct tools. This is deliberate: embedding
numeric tables is how RAG systems misquote numbers.

| Data | Served by | Why |
|---|---|---|
| Concall transcripts/presentations | RAG (Q&A-level chunks) | qualitative, needs semantic match |
| Annual reports, rating rationales, announcements | RAG (sliding windows) | qualitative |
| XBRL narrative | RAG (indexed, low priority) | numbers here are steered to structured tools |
| Financial statements (12y CSVs) | `financial_statements` | exact numbers, zero retrieval error |
| Prices / time series | `price_history`, `technicals_momentum` | computed, not retrieved |
| Valuation, screening metrics | `valuation_summary`, `screen_stocks` | computed, not retrieved |

Multi-quarter handling: `topic_timeline` runs one retrieval **per period** (newest
first) so "how did guidance evolve" questions cover every quarter instead of letting
global top-k cluster in one. Cross-company queries use `max_per_symbol` caps.
Date-scoped questions use `date_int` metadata filters (verified exact).

- Embeddings: `BAAI/bge-small-en-v1.5` on CUDA (RTX 3070), cosine, normalized.
- Vector store: ChromaDB at `data/index/chroma` — metadata filters on symbol,
  doc_type, and date (`date_int` as YYYYMMDD) enable date-scoped retrieval.
- Chunking: structured transcripts → one chunk per Q&A exchange + prepared-remark
  blocks (boundaries from the parse, mirroring `scripts/24_build_chunks.py`);
  everything else → ~800-token sliding windows with 120-token overlap; every chunk
  is prefixed with a context line (`[SYMBOL doc-type, period]`).
- Screener cache: `data/index/screener_metrics.parquet`, auto-rebuilt when >24h old.
