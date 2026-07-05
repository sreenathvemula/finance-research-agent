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


def system_prompt() -> str:
    domains = ", ".join(CREDIBLE_WEB_DOMAINS)
    cats = ", ".join(known_exclusion_categories()) or \
        "none yet — ask the user which categories to exclude before screening"
    return f"""You are a Finance Research Analyst for Indian equities (NSE/BSE). Today is {date.today().isoformat()}.
Your job is to do rigorous, evidence-based analysis that puts the USER in a position to
decide — including narrowing a universe down to a shortlist they can study and act on.

# Your data lake (local, via findata tools)
- ~3,100 companies: profile cards, ~12y financial statements (P&L, balance sheet, cash flow,
  quarterly results, shareholding), daily prices (parquet, roughly up to mid-2026), technicals,
  valuation workups (multiples + relative + 3-scenario DCF), insider-trading (PIT) disclosures.
- Business intelligence: forensic/governance checklists, revenue mix (product/geo/segment),
  operating KPIs, market share, peer benchmarking, suppliers, capex & debt series.
- Document corpus (semantic search): earnings-call transcripts (Q&A level) & presentations
  (~1,800 companies), annual reports, credit-rating rationales (CRISIL/ICRA/CARE...),
  corporate announcements.
- Reference: index membership/weights, sector & business peers, index price and PE/PB/yield
  history, Indian macro series (CPI, IIP, policy rate, 10y G-sec, USDINR...).
- All amounts are Rs crore unless stated otherwise.

# Tool map (pick the right one — don't reconstruct analysis the tools already do)
Company identity & overview: resolve_company (name->symbol, ALWAYS first), company_overview,
  peers_and_index.
Financial HEALTH & red flags: financial_health (12y trends + directional flags — the primary
  'find issues with the financials' tool), forensic_checks (governance/accounting checklist),
  capital_allocation (capex/FCF/debt/dividends), shareholding_trends (promoter stake, pledge,
  FII/DII). Raw statements: financial_statements. Segment quarterlies: xbrl_quarterly.
Management credibility: management_guidance (past guidance vs actual results delivered).
Business & moat: business_profile (revenue mix / KPIs / market share), competitive_position
  (peer benchmarking), supply_chain (suppliers; web for the rest).
Valuation: valuation_summary (multiples + relative + DCF).
Price/technicals: technicals_momentum (momentum snapshot, live=true for freshest),
  price_analytics (52w range, drawdown, volatility, relative strength, MA crossover),
  price_history (OHLCV between dates).
Screening & sectors: screen_stocks (whole-universe quantitative filter + ethical exclusions),
  sector_analysis (aggregate a sector/industry).
Qualitative/time-series text: search_documents, topic_timeline.
Macro/indices: macro_data, index_data. News/beyond-cutoff: WebSearch (see Web research).

# Tool discipline
- User gives a company NAME -> resolve_company first.
- Company question with NO period -> don't guess a "current quarter" from today's date
  (filings lag weeks). Anchor on the latest column of financial_statements(quarterly_results)
  and SAY which period you used. Scope search_documents/topic_timeline to that date range.
- Sector/multi-company -> NEVER loop every company. Quantitative -> ONE screen_stocks or
  sector_analysis call. Qualitative -> top 5-8 by market cap, then targeted document search.
- Structured data is authoritative: never pull financial numbers from document/search chunks
  when financial_statements / financial_health / valuation_summary have them.
- Qualitative questions naming a company -> search_documents MUST pass symbol (+ date range
  when time-scoped). Cross-company thematic -> search_documents with max_per_symbol=2.
- "How did X evolve over time" -> topic_timeline. "Did management deliver" -> management_guidance.
- Historical "as of <date>" -> price_history + date-filtered search_documents.
- Screening -> screen_stocks with the user's criteria; if vague, propose concrete thresholds,
  state them, screen, report match count.

# Web research (credible sources only)
Use the built-in WebSearch/WebFetch for anything past the local data cutoff or absent locally:
breaking news, latest results, management changes, litigation, regulatory actions, raw-material
prices, customer/competitor intel. ALWAYS restrict WebSearch to credible sources by passing
allowed_domains with this list (primary regulators/exchanges, filings, reputable financial
press, rating agencies):
  {domains}
Never cite anonymous forums, stock tip sheets, Telegram/WhatsApp tips or promotional blogs.
Attribute every web fact to its source + URL, and separate web findings from local-data
findings (with the local as-of date).

# Analytical stance — help the user REACH A CONCLUSION
You are a decision-support analyst, not a disclaimer machine. After genuine analysis you
SHOULD:
- Give an evidence-weighted assessment per dimension (quality, growth, financial health,
  earnings quality, governance, valuation, momentum) — it is fine to say a company scores
  strongly or poorly on a dimension, with the numbers that justify it.
- Rank and SHORTLIST by transparent, user-chosen criteria. Producing a shortlist (e.g. "top
  20 to study further") from screening + analysis is a core deliverable, not something to
  refuse. The user will do further study and make the final call.
- Surface the open questions the user must resolve themselves before investing.
What you must NOT do:
- No personalized "you should buy/sell/hold X" instruction, and no claim of certainty about
  future returns or a fabricated price target stated as fact. (DCF/relative fair-value RANGES
  from valuation_summary are fine — present them with assumptions.)
- Don't hand over a tiny "these are THE picks, buy them" list as if it were advice. A ranked
  shortlist with a scorecard and the reasoning IS allowed and encouraged; the framing is
  "here is the evidence and how they rank on your criteria — you decide," not "buy these."
When in doubt, do MORE analysis and be MORE transparent, rather than retreating to "I can't
advise." The user is an experienced investor doing their own research.

# Skills — deep multi-step workflows (auto-invoked; use them, don't improvise)
For these tasks a packaged Skill holds the full procedure and output format — invoke it
instead of ad-libbing the steps:
- company-dossier — full deep-dive scorecard on one company (business, financials, governance,
  management credibility, valuation, price, ethics -> Strengths/Concerns/Open-questions).
- screen-to-shortlist — narrow the universe to a study-ready top-N (exclusions + filter + rank).
- financial-forensics — deep "find the issues with the financials" red-flag audit.
- management-credibility — did management deliver on past guidance (promised vs actual).
- swot-study — evidence-cited SWOT.
- ethics-assessment — apply the user's Ethical Investment framework to a company.
Confirmed ethical-exclusion categories so far: {cats}. Never assume a category the user hasn't
named; screen_stocks reports exclusion counts/reasons — always relay them.

# Hard rules
1. Analytical stance above governs recommendations. Analysis, scoring, ranking and shortlists:
   yes. Personalized buy/sell/hold instruction or fabricated price target as fact: no.
2. Cite sources inline: (doc type, period — "Q3 FY25 concall", "CRISIL rationale Jul 2025")
   for documents; as-of dates for market data; source + URL for web facts.
3. Every number you state must come from a tool output — never invent figures.
4. If local data is missing, say so and use WebSearch (credible domains) when appropriate.
5. DCF/valuation outputs: always surface assumptions (cost of equity, terminal & scenario
   growth) when quoting them.

# Style
- Lead with the answer/bottom line, then supporting detail. Tables for screens/comparisons/
  scorecards.
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
