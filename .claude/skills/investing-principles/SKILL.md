---
name: investing-principles
description: The operating investment philosophy, best-practice checklists, ready-made screening strategies (Coffee Can, Greenblatt Magic Formula, Graham Defensive, QARP) AND the precise quantitative scoring frameworks (Altman Z''-Score, Piotroski F-Score, Sloan accrual ratio, 5-step DuPont, Graham Number, owner earnings) distilled from investing classics and academic accounting-fraud/quality research. CONSULT THIS whenever analysing a company for investment, screening/shortlisting, judging valuation or quality, or scoring earnings quality/distress risk. The other finance skills lean on this one for how to weigh evidence — treat it as the shared quantitative and qualitative rulebook, not just a mood.
---

# Investing principles & best practices

This is the "how a disciplined investor thinks" layer for Indian equities. Apply it on top of
the findata tools: the tools give the raw evidence, this gives the judgement AND the exact
formulas for turning that evidence into falsifiable scores. Distilled from The Intelligent
Investor (Graham), The Little Book That Beats the Market (Greenblatt), The Little Book of
Valuation (Damodaran), Coffee Can Investing (Mukherjea/Ambit — India-specific), The Four Pillars
of Investing (Bernstein), The Behavioral Investor (Crosby), A Random Walk Down Wall Street
(Malkiel), plus the academic accounting-quality/fraud-detection literature (Sloan 1996,
Piotroski 2000, Altman 1968/2017 EM variant) that every serious forensic screen in the
industry is actually built on. These are frameworks, not guarantees — markets are largely
efficient, so edge comes from discipline and from catching what others don't check, not from
certainty.

Some methodology below (peer-comp quality discipline, DD severity taxonomy, screening-verdict
framing) is adapted from Anthropic's own public reference implementation for financial-services
agents, [anthropics/financial-services](https://github.com/anthropics/financial-services)
(Apache 2.0) — its `equity-research`, `private-equity` and `financial-analysis` skill verticals
cover the same ground as this project at institutional-desk scale; adapted here for the local
Indian-equities data lake rather than the Excel/DOCX-workbook pipeline that repo builds toward.

**Ground rule for everything below:** every score here is a *computed composite of tool-sourced
inputs* — never a number pulled from memory, and never a number with a silently-assumed input.
If `screen_stocks`/`valuation_summary` already expose a column directly, use it; where a score
needs a ratio the tools don't pre-compute (Altman/Piotroski/Sloan), pull the raw lines from
`financial_statements` (profit_loss/balance_sheet/cash_flow, 2+ years) and show the inputs
plugged into the formula, not just the final number. State which figures came from which tool
call. **If a formula needs an input this data lake genuinely doesn't expose (no placeholder, no
"assume unchanged"), say the score can't be computed and why — don't publish a number built on
an assumed component.**

## Core philosophy (apply in every analysis)
1. **Quality first.** A great business earns returns on capital well above its cost of capital,
   consistently, funded by its own cash. High, stable ROCE/ROE is the single best quality tell.
2. **Value / margin of safety (Graham's central idea).** Never overpay. Buy at a discount to a
   conservatively-estimated intrinsic value so that being wrong still isn't ruinous.
3. **Quality × Value together (Greenblatt).** A good business at a fair price beats a fair
   business at any price and a great business at a crazy price. Rank on BOTH.
4. **Circle of competence.** Only conclude on businesses whose economics you can actually explain.
   Flag when a business model is outside easy understanding.
5. **Long horizon (Coffee Can).** The compounding case assumes years of holding. Judge companies
   on 10-year durability, not the next quarter.
6. **Behavioural discipline (Crosby).** Pre-commit to written criteria; don't chase momentum,
   anchor on purchase price, or extrapolate recent results. Treat market volatility (Mr. Market)
   as opportunity, not instruction.
7. **Humility & diversification (Bernstein/Malkiel).** No screen is a crystal ball; costs, taxes
   and concentration risk are real. A shortlist is a study list, not a certainty.
8. **Trust but verify (Sloan/Piotroski).** A reported number is an accounting choice, not
   a fact of nature. Every "quality" read earns a second pass through an earnings-quality lens
   before it's accepted — see the Quantitative scoring section below.

## Ready-made strategy screens (map each to the tools)
Use these as transparent starting filters, then verify per name. State which strategy you used.

**Coffee Can (India quality-compounder — the flagship for this universe)**
- Non-financials: revenue growth **≥10% every year for the last 10 years — or every year of
  however much history is on file if fewer than 10 years are available (say which)** AND
  **pre-tax ROCE ≥15% every year** on the same basis. Financials (banks/NBFCs): **ROE ≥15%**
  and **loan growth ≥15%**.
- Extremely selective by design (Ambit found ~9 of ~1300 firms passed; this universe's own
  historical data reproduces that order of magnitude — 124/3145 clear ROCE alone, 16/3145 clear
  sales growth alone, only 5/3145 clear BOTH every year for 10 years).
- How to run: `screen_consistency(metric="roce_or_roe", min_value=15, n_years=10,
  max_violations=0)` (applies ROCE to non-financials, ROE to banks/NBFCs per company — not a
  separate metric, just picks the right real one; the output's metric_used column says which)
  AND
  `screen_consistency(metric="sales_yoy_growth_pct", min_value=10, n_years=10,
  max_violations=0)`, then intersect the returned symbols — this checks EVERY year for the
  WHOLE universe in one pass each, not a coarse latest-value cut. State n_years actually used
  per company (younger companies are evaluated on however much history they have, not
  penalised) and max_violations if you loosen from strict.
- **Overlay before finalising:** run the Piotroski F-Score and Altman Z''-Score (below) on each
  survivor, and check the Sloan accrual ratio trend. A stock that clears the 10-year ROCE/growth
  bar on the back of aggressive accounting is not a real Coffee Can name — it's a name that
  hasn't been caught yet.

**Greenblatt Magic Formula (good business + cheap)**
- Rank the universe on two factors and combine ranks: **Return on Capital = EBIT/(net working
  capital + net fixed assets)** and **Earnings Yield = EBIT/Enterprise Value**.
- Proxy with tools: use `roce` as the return-on-capital proxy and the earnings yield / EV-EBITDA
  from `valuation_summary`/`screen_stocks(min={"roce":..}, sort_by="ev_ebitda", ascending=true)`.
  Note the proxy vs the exact tangible-capital definition when you present it.

**Graham Defensive (safety-first value)**
- Adequate size; strong balance sheet (low `debt_equity`, ample liquidity); 10y earnings
  stability and a dividend record; **P/E ≤ 15**, **P/B ≤ 1.5**, and **P/E × P/B ≤ 22.5**.
- Run: `screen_stocks(max={"pe":15,"pb":1.5,"debt_equity":0.5})` then confirm earnings/dividend
  consistency via `financial_statements`.
- **Graham Number cross-check** (below) gives the same P/E×P/B=22.5 discipline as a single
  per-share ceiling — use it as the quick sanity check on any individual name.

**Quality at a Reasonable Price (QARP — the pragmatic default)**
- High, durable `roce`/`roe` + reasonable valuation vs the company's OWN history and its peers
  (not just an absolute P/E). Use `valuation_summary`'s relative block (vs peer median and own
  10y median) — a quality compounder at a discount to its own history is the sweet spot.

## Quantitative scoring frameworks — precise formulas

These turn "does this look clean / cheap / high-quality" into a specific, falsifiable,
reproducible number. Always show the component inputs and their source, not just the score.
Caveat up front: Altman was calibrated on US GAAP filings and US default data — treat the exact
cutoffs as directional, not certificates, on Ind-AS filings; say so when you present them.

### DuPont decomposition (ROE, 5-step) — the quality diagnostic
`ROE = Tax Burden × Interest Burden × Operating Margin × Asset Turnover × Financial Leverage`
- Tax Burden = Net Income / Pretax Income
- Interest Burden = Pretax Income / EBIT
- Operating Margin = EBIT / Sales — from `screen_stocks` `opm_pct` or `financial_statements`
- Asset Turnover = Sales / Total Assets — `screen_stocks` `asset_turnover`
- Financial Leverage = Total Assets / Equity — `screen_stocks` `equity_multiplier`
Use this to answer *why* ROE is high: a margin-and-turnover-driven ROE (operating excellence) is
higher quality than one driven mainly by Financial Leverage (a levered balance sheet propping up
an otherwise average business) — the same headline ROE can hide very different risk. Most inputs
are already columns in `screen_stocks`/`company_overview`; pull `financial_statements(profit_loss)`
only for the tax/interest burden split if not already surfaced.

### Piotroski F-Score (0-9) — is the fundamental trend actually improving?
Nine binary tests (1 point each), needs 2 years of `financial_statements` (profit_loss,
balance_sheet, cash_flow):
- **Profitability (4):** ROA > 0 this year; CFO > 0 this year; ROA higher than last year; CFO >
  Net Income this year (accrual quality — cash-backed earnings)
- **Leverage/liquidity (3):** long-term debt/assets lower than last year; current ratio higher
  than last year; no new shares issued this year (no dilution)
- **Operating efficiency (2):** gross margin higher than last year; asset turnover higher than
  last year
Score **≥7-8**: strong fundamental improvement (a "getting better" quality signal, distinct from
just "already high ROCE"). Score **≤2-3**: fundamentals deteriorating on multiple fronts —
treat as a hard flag, not a footnote, especially when paired with a "cheap" valuation (a low
Magic-Formula/Graham price and a low F-Score together is the classic value trap pattern).

### Altman Z''-Score (Emerging Markets variant) — distress/solvency
Use the EM-calibrated variant, not the original 1968 manufacturing formula, given Ind-AS filings
and this universe's mix of sectors:
`Z'' = 3.25 + 6.56·A + 3.26·B + 6.72·C + 1.05·D`
- A = Working Capital / Total Assets
- B = Retained Earnings / Total Assets
- C = EBIT / Total Assets
- D = Book Value of Equity / Total Liabilities (or market value of equity if you want the
  market-priced version — state which you used)
All four from `financial_statements(balance_sheet)` + `(profit_loss)`, single year (trend it
across a few years for a distress *trajectory*, not just a snapshot). Zones: **Z'' > 2.6** safe,
**1.1-2.6** grey/watch, **< 1.1** distress zone. Skip for banks/NBFCs (their working-capital and
leverage structure makes this formula meaningless) — use capital-adequacy/asset-quality reads
instead.
**Data-honesty note on A:** this data lake's balance sheet has no current/non-current split
(assets other than Fixed Assets/CWIP/Investments are lumped into one "Other Assets" line, same
for liabilities into "Other Liabilities"), so true Working Capital isn't directly available
either. A can still be estimated from real reported lines —
`(Other Assets − Other Liabilities) / Total Assets` — since most of what sits in those two
buckets for a non-financial company genuinely is current. State plainly that A is an estimate on
this basis (not the textbook current-assets-minus-current-liabilities figure) whenever you show
this score; B, C and D are exact from directly reported lines.

### Sloan accrual ratio — the simplest, best-evidenced earnings-quality check
`Accrual Ratio = (Net Income - Cash Flow from Operations) / Average Total Assets`
Academically the single most robust predictor that high-accrual firms (large gap between
reported profit and actual cash generated) subsequently underperform and are more likely to
restate. A clean company should show CFO tracking or exceeding NI over multiple years — this is
the same underlying idea as `financial_health`'s CFO-vs-PAT flag, but expressed as one precise
ratio you can rank companies by, rather than a qualitative flag. Compute across the same 12y
window `financial_statements` provides; a ratio that's persistently high (or trending up even
if not yet extreme) is worth flagging before it becomes an obvious "concern" in the trend tool.

### Graham Number & owner earnings — the value-side cross-checks
- **Graham Number** = √(22.5 × EPS × Book Value per Share) — a conservative intrinsic-value
  ceiling under Graham's P/E≤15, P/B≤1.5 discipline (15×1.5=22.5). EPS from price/pe or
  `financial_statements`; book_value_per_share is a direct `screen_stocks` column. Price above
  this number doesn't mean "overvalued" (growth names routinely exceed it) but it quantifies how
  far outside classic defensive-value territory the price sits.
- **Owner earnings** (Buffett) ≈ Net Income + D&A + other non-cash charges − *maintenance* capex
  (not total capex) − incremental working capital. Approximate maintenance capex as
  depreciation itself, or the trailing multi-year average capex during low-growth periods, when
  the company doesn't split maintenance vs growth capex explicitly (most Indian filings don't).
  State the approximation used. Compare owner earnings, not headline PAT, to price for a
  cash-based earnings yield — this is the FCF-yield discipline (`fcf_yield_pct` in
  `screen_stocks` is the closest direct proxy; owner earnings is the hand-computed refinement
  when the proxy looks off).

### Which score answers which question
| Question | Score | Data need |
|---|---|---|
| Is the ROE high because of real operating quality or leverage? | DuPont 5-step | 1yr, mostly direct columns |
| Is the trend actually improving, fundamentally? | Piotroski F-Score | 2yr raw statements |
| Is this company at risk of financial distress? | Altman Z''-Score | 1yr (trend for trajectory) |
| Is reported profit backed by real cash? | Sloan accrual ratio | multi-year (12y available) |
| Is the price disciplined by classic value math? | Graham Number | 1yr, mostly direct columns |
| What's the real cash the owner could extract? | Owner earnings | multi-year cash flow |

## Quality checklist (score in every dossier)
- ROCE/ROE consistently > ~15% (and > cost of capital) — `financial_health`, cross-checked with
  DuPont to see *why* it's high.
- Earnings convert to cash: cumulative CFO ≈ or > cumulative PAT — `financial_health` +
  Sloan accrual ratio for the precise version.
- Low/manageable leverage; comfortable interest coverage — `financial_health`, corroborated by
  Altman Z''-Score trend for non-financials.
- Reinvestment runway: growing at good returns without chronic dilution or negative FCF —
  `capital_allocation`, Piotroski's no-dilution test.
- Moat evidence: pricing power, market-share stability, durable niche — `business_profile`,
  `competitive_position`.

## Valuation discipline
- Every valuation carries its assumptions (cost of equity, terminal & scenario growth) —
  `valuation_summary` DCF already does; always surface them. State explicitly what
  differentiates the bear/base/bull scenarios in narrative terms (which growth/margin
  assumption changes, not just the resulting number) — a scenario table without the
  differentiating assumption spelled out is decoration, not analysis.
- Anchor on a RANGE, not a point. Compare price to the DCF range AND to own-history/peer
  multiples AND the Graham Number. Demand a margin of safety before calling something "cheap".
- Don't pay a rich multiple for merely-average growth; check the implied growth the price
  requires vs what the company has actually delivered (`valuation_summary`'s
  `implied_growth_10y` vs `hist_sales_cagr`/`hist_eps_cagr`).
- **Peer-comp quality discipline** (adapted from Anthropic's comps-analysis skill): "better to
  have 3 genuinely comparable peers than 6 questionable ones." Before leaning on
  `valuation_summary`'s `peer_median_pe`, sanity-check the peer SET itself — are they truly
  analogous in business model and scale, or does `peer_count` include a distressed name, a
  pre-profit micro-cap, or a wildly diversified conglomerate that drags the median around? A
  peer set of 3 that mixes a ₹3-lakh-crore leader with a ₹200-crore illiquid micro-cap (as
  happened when screening the Tobacco sector — GODFRYPHLP/VSTIND/ELITECON) is a "6 questionable
  comps" problem even at n=3 — say so rather than treating peer_median_pe as automatically
  authoritative. When `peer_count` is small (≤3-4) and heterogeneous, weight the own-10y-history
  anchor more heavily than the peer anchor, and say why.
- When a peer set is large enough to matter, look at the **spread, not just the median** —
  where does the subject sit between the peer set's cheap end and expensive end, not just above
  or below a single midpoint. A name at the peer median can still be the most expensive of the
  three genuinely comparable names if the other "peers" in the set are noise.

## Red-flag checklist (from Graham + forensic practice)
- Profits not converting to cash; other-income-propped earnings; aggressive depreciation —
  quantify with the Sloan accrual ratio and DEPI (depreciation-rate trend), not just a
  qualitative read.
- Rising debt / weak interest coverage / deteriorating Altman Z''; chronic negative free cash
  flow.
- Promoter pledge, falling promoter stake, insider selling — `forensic_checks`,
  `shareholding_trends`, `insider_trading`.
- Guidance repeatedly missed — `management_guidance`.
- Low or falling Piotroski F-Score alongside a "statistically cheap" valuation — the value-trap
  signature.
Route these through the `financial-forensics` skill for a full audit — that skill's raw
line-item pass is where the Sloan/Piotroski/Altman inputs actually get pulled and computed.

## Behavioural discipline (Crosby)
When presenting conclusions, actively counter the four biases: ego (overconfidence — show the
bear case), emotion (don't let a good story override weak numbers), attention (a hot stock isn't
a good one), conservatism (don't anchor on past prices or ignore disconfirming data). Recommend
the user pre-write their buy criteria and required return.

## How to use this
- Screening/shortlisting → pick a strategy preset, state it, run it, verify per name, overlay a
  Piotroski/Altman pass on the survivors, rank transparently (works with the
  `screen-to-shortlist` skill).
- Analysing one company → score it against the Quality checklist + Valuation discipline +
  Red-flags + the full quantitative scoring section, then give the margin-of-safety read and the
  behavioural caution (works with the `company-dossier` skill).
- Auditing one company for problems → the quantitative scores ARE the audit's backbone; hand off
  to `financial-forensics` for the full raw-line-item procedure.
- Always end with: this is evidence, scoring and ranking on stated criteria — the buy/sell/
  allocation decision, and whether it fits your circle and required return, is the user's.
