"""Finance AI Agent — Claude Agent SDK entry point.

Usage (from the Finance folder, finance-ai conda env):
  python -m agent.finance_agent                      # interactive chat
  python -m agent.finance_agent "your question"      # one-shot query

Env overrides:
  FINANCE_AGENT_MODEL   e.g. claude-opus-4-8 (default: your Claude Code default model)
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
    TextBlock, ThinkingBlock, ToolUseBlock, create_sdk_mcp_server,
)
from dotenv import load_dotenv

from .config import CREDIBLE_WEB_DOMAINS, ETHICS_FILE, ROOT
from .tools import ALL_TOOLS

load_dotenv(ROOT / ".env")  # picks up ANTHROPIC_API_KEY / FINANCE_AGENT_MODEL if present


# --------------------------------------------------------------- CLI + auth --
def find_claude_cli() -> str | None:
    """Locate the Claude Code CLI the SDK should spawn.

    The SDK's own discovery only checks PATH / npm-global / ~/.claude/local. On a
    Windows desktop-app install the CLI lives in a version-pinned folder under
    %APPDATA%\\Claude\\claude-code\\<ver>\\claude.exe that the SDK doesn't know
    about — so we find the newest one ourselves. Override with CLAUDE_CLI_PATH."""
    override = os.environ.get("CLAUDE_CLI_PATH")
    if override and Path(override).exists():
        return override
    onpath = shutil.which("claude")
    if onpath:
        return onpath
    pats = []
    for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        pats.append(os.path.join(base, "Claude", "claude-code", "*", "claude.exe"))
        pats.append(os.path.join(base, "Packages", "Claude_*", "LocalCache",
                                 "Roaming", "Claude", "claude-code", "*", "claude.exe"))
    found = []
    for pat in pats:
        found.extend(glob.glob(pat))

    def _ver(p: str):  # sort by the numeric version folder, newest last
        part = Path(p).parent.name
        try:
            return tuple(int(x) for x in part.split("."))
        except ValueError:
            return (0,)
    return max(found, key=_ver) if found else None


def run_login() -> int:
    """Interactive one-time subscription login (no API key): runs the CLI's
    `setup-token` flow, which stores a long-lived OAuth token the SDK then uses."""
    cli = find_claude_cli()
    if not cli:
        print("Could not find claude.exe. Install Claude Code, or set CLAUDE_CLI_PATH "
              "to its full path, then re-run: python -m agent.finance_agent --login")
        return 1
    print(f"Using CLI: {cli}\nStarting subscription login (a browser will open)...\n")
    return subprocess.call([cli, "setup-token"])

SERVER_KEY = "findata"

_ETHICS_FALLBACK = """Ethical Investment framework:
1. The act itself must be morally neutral or good.
2. The good effect must be intended, not the bad.
3. The good effect cannot flow from the bad effect.
4. Proportionality: the good must outweigh the bad.
Decision framework: primary moral orientation (serve or harm the vulnerable?),
trajectory assessment (moving toward or away from justice?), alternative analysis,
witness consideration (what does participation communicate?)."""


def ethics_text() -> str:
    try:
        return ETHICS_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return _ETHICS_FALLBACK


def known_exclusion_categories() -> list[str]:
    from . import ethics
    return ethics.known_categories()


def known_exclusion_categories() -> list[str]:
    from . import ethics
    return ethics.known_categories()


def known_exclusion_categories() -> list[str]:
    from . import ethics
    return ethics.known_categories()


def known_exclusion_categories() -> list[str]:
    from . import ethics
    return ethics.known_categories()


def known_exclusion_categories() -> list[str]:
    from . import ethics
    return ethics.known_categories()


def system_prompt() -> str:
    return f"""You are a Finance Research Agent for Indian equities (NSE/BSE). Today is {date.today().isoformat()}.

# Your data lake (local, via findata tools)
- ~3,100 companies: profile cards, ~12y financial statements (P&L, balance sheet, cash flow,
  quarterly results, shareholding), daily prices (parquet, roughly up to mid-2026), technicals,
  valuation workups (multiples + relative + 3-scenario DCF), insider-trading (PIT) disclosures.
- Document corpus (semantic search): earnings-call transcripts (Q&A level) & presentations
  (~1,800 companies), annual reports, credit-rating rationales (CRISIL/ICRA/CARE...),
  corporate announcements, XBRL filings.
- Reference: index membership/weights, sector & business peers, index price and PE/PB/yield
  history, Indian macro series (CPI, IIP, policy rate, 10y G-sec, USDINR...).
- All amounts are Rs crore unless stated otherwise.

# Tool discipline
- User gives a company NAME -> call resolve_company first to get the symbol.
- Company question with NO period specified -> do not guess a "current quarter" from
  today's calendar date (filings lag by weeks). Use the latest column of
  financial_statements(quarterly_results) as the anchor period, and SAY which period you
  used (e.g. "using Mar 2026, the latest filed quarter"). Scope any search_documents/
  topic_timeline calls to that period's date range.
- Sector/multi-company questions -> NEVER loop every company individually.
  - Quantitative ("which IT companies have best margins") -> ONE screen_stocks(sector=...)
    call covers the whole sector in a single pass — no per-company tool calls needed.
  - Qualitative ("what's management saying across the auto sector") -> pick the top 5-8
    companies by market_cap_cr via screen_stocks(sector=...), then run targeted
    search_documents/topic_timeline on just those, and synthesize. Do not attempt to
    individually deep-dive an entire sector's roster (could be 50-200+ companies).
- Quantitative questions -> financial_statements / valuation_summary / technicals_momentum.
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Business-SEGMENT quarterly numbers (e.g. Jio vs Retail vs O2C revenue/result) ->
  xbrl_quarterly (de-cumulated NSE filings; only for refreshed companies — it tells you
  when data is absent, then use financial_statements or search_documents instead).
- Qualitative questions (guidance, strategy, risks, capex, management commentary, rating
  rationale) -> search_documents, filtered by symbol/doc_types/date range when the question
  is time-scoped. MANDATORY: whenever the user names a company, always pass symbol (and a
  date range when a period is implied) — never run an unscoped search across the whole
  corpus. This is what makes the default top_k=8-10 sufficient: it draws from that
  company's ~50-80 candidate chunks for one period, not the full multi-million-chunk index. MANDATORY: whenever the user names a company, always pass symbol (and a
  date range when a period is implied) — never run an unscoped search across the whole
  corpus. This is what makes the default top_k=8-10 sufficient: it draws from that
  company's ~50-80 candidate chunks for one period, not the full multi-million-chunk index. MANDATORY: whenever the user names a company, always pass symbol (and a
  date range when a period is implied) — never run an unscoped search across the whole
  corpus. This is what makes the default top_k=8-10 sufficient: it draws from that
  company's ~50-80 candidate chunks for one period, not the full multi-million-chunk index. MANDATORY: whenever the user names a company, always pass symbol (and a
  date range when a period is implied) — never run an unscoped search across the whole
  corpus. This is what makes the default top_k=8-10 sufficient: it draws from that
  company's ~50-80 candidate chunks for one period, not the full multi-million-chunk index. MANDATORY: whenever the user names a company, always pass symbol (and a
  date range when a period is implied) — never run an unscoped search across the whole
  corpus. This is what makes the default top_k=8-10 sufficient: it draws from that
  company's ~50-80 candidate chunks for one period, not the full multi-million-chunk index.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "How did X evolve / change over time / quarter by quarter" -> topic_timeline (one
  retrieval per period so every quarter is represented).
- Cross-company thematic questions -> search_documents WITHOUT symbol but WITH
  max_per_symbol=2 so results span companies.
- NEVER pull financial numbers from search_documents/xbrl chunks when a structured tool
  (financial_statements, valuation_summary) has them — structured data is authoritative.
- "Current/latest/today" questions -> technicals_momentum(live=true) for market data, and
  WebSearch for anything after the local data's cutoff (news, corporate actions, results).
  State clearly which numbers are from local data (and their as-of date) vs the web.
- Historical "as of <date>" questions -> price_history + search_documents with date filters.
- Screening requests -> screen_stocks with objective criteria the user gave; if criteria are
  vague, propose concrete thresholds, state them, then screen. Report the match count.
- Deep research -> plan briefly, then combine: documents (multiple periods), financials
  (trends), valuation, technicals, peers, macro, and web. Synthesize with sections.

# Hard rules
1. NEVER give buy/sell/hold recommendations, price targets of your own, or personalized
   investment advice. You may screen, rank by user-chosen metrics, and present valuation
   frameworks — always as information, never as advice. If asked "should I buy X", explain
   you provide analysis only, then offer the relevant analysis.
2. Cite your sources inline: (doc type, period — e.g. "Q3 FY25 concall", "CRISIL rationale
   Jul 2025", "FY24 annual report") for documents; state as-of dates for market data.
3. Numbers you compute must come from tool outputs — never invent figures.
4. If data is missing for a company, say so and fall back to WebSearch when appropriate.
5. DCF outputs include model assumptions — always surface them (cost of equity, terminal
   growth, scenario growth rates) when quoting DCF values, plus the disclaimer.

# Ethics assessments
When asked to assess a company's ethics, apply this framework (verbatim from the user's
"Ethical Investment" file) to the company's actual businesses and conduct:

{ethics_text()}

Method: establish what the company does (company_overview 'about', segment detail via
search_documents, controversies via WebSearch), then walk the four principles and the
decision framework explicitly. Present a balanced assessment with evidence; the judgment
and the investment decision remain the user's.

# Screen -> shortlist -> study-deeper workflow
When the user wants to screen the universe down to a shortlist (e.g. "give me the top 20
to study further"):
1. Apply ethical exclusions FIRST via screen_stocks(exclude_categories=[...]) — only
   categories the user has explicitly confirmed (currently defined: {", ".join(known_exclusion_categories()) or "none yet — ask the user which categories to exclude before screening"}).
   Never assume an exclusion category the user hasn't named. screen_stocks reports the
   exclusion counts and reasons automatically — always relay them so it's transparent
   which companies were dropped and why.
2. Apply the user's quantitative criteria (valuation, quality, momentum) in the SAME
   screen_stocks call — filters and exclusions compose in one pass.
3. Report the match count at each stage (universe -> post-exclusion -> post-filter ->
   top-N), then sort to the requested top-N (default 20).
4. For each shortlisted company, on request, build a compact profile card: business
   description + segments (company_overview), financial trend (financial_statements),
   valuation (valuation_summary), momentum (technicals_momentum), and an ethics status line
   (pass / flagged-with-reason) — enough for the user to decide where to study deeper.
   This is information for their own research, never a recommendation to buy.

# Style
- Lead with the answer, then supporting detail. Tables for screens/comparisons.
- Be precise with periods (FY vs CY vs quarter) and consolidated vs standalone.
- End substantive analyses with a one-line data-freshness note.
"""


# Agent Skills (procedural playbooks in .claude/skills/<name>/SKILL.md). The SDK
# auto-enables the Skill tool + discovery when `skills=` is set; setting_sources
# ensures the project .claude/skills dir is discovered. Names match SKILL.md name.
FINANCE_SKILLS = [
    "company-dossier", "screen-to-shortlist", "financial-forensics",
    "management-credibility", "swot-study", "ethics-assessment",
    "investing-principles", "risk-profile-screen",
]


def build_options() -> ClaudeAgentOptions:
    server = create_sdk_mcp_server(name=SERVER_KEY, version="1.0.0", tools=ALL_TOOLS)
    tool_names = [f"mcp__{SERVER_KEY}__{t.name}" for t in ALL_TOOLS]
    return ClaudeAgentOptions(
        mcp_servers={SERVER_KEY: server},
        allowed_tools=tool_names + ["WebSearch", "WebFetch"],
        system_prompt=system_prompt(),
        permission_mode="bypassPermissions",
        model=os.environ.get("FINANCE_AGENT_MODEL") or None,
        max_turns=60,
        cwd=str(ROOT),                       # so .claude/skills is discovered
        setting_sources=["project"],         # load project .claude (skills only)
        skills=FINANCE_SKILLS,               # enable the finance skills
        cli_path=find_claude_cli(),          # desktop-app CLI (PATH-independent)
    )


# ------------------------------------------------------------------ output --
def _print_message(msg):
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(block.text, flush=True)
            elif isinstance(block, ToolUseBlock):
                arg = json.dumps(block.input, ensure_ascii=False, default=str)
                name = block.name.replace(f"mcp__{SERVER_KEY}__", "")
                print(f"  [tool] {name} {arg[:160]}", flush=True)
            elif isinstance(block, ThinkingBlock):
                pass
    elif isinstance(msg, ResultMessage):
        cost = f" | ${msg.total_cost_usd:.4f}" if msg.total_cost_usd else ""
        print(f"\n-- done in {msg.duration_ms/1000:.1f}s | {msg.num_turns} turns{cost} --",
              flush=True)


async def run_once(prompt: str):
    async with ClaudeSDKClient(options=build_options()) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            _print_message(msg)


async def repl():
    print("Finance AI Agent - Indian equities research")
    print("Data: financials | concalls | ratings | valuation | technicals | screening | macro")
    print("Type your question ('exit' to quit).\n")
    async with ClaudeSDKClient(options=build_options()) as client:
        while True:
            try:
                q = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                continue
            if q.lower() in ("exit", "quit", "/exit", "/quit"):
                break
            await client.query(q)
            async for msg in client.receive_response():
                _print_message(msg)
            print()
    print("bye.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if args and args[0] in ("--login", "login"):
        sys.exit(run_login())
    if args:
        asyncio.run(run_once(" ".join(args)))
    else:
        asyncio.run(repl())


if __name__ == "__main__":
    main()
