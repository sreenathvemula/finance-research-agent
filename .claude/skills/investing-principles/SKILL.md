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
9. **Diversification is a count-vs-conviction trade-off, not a free lunch.** More holdings cut
   single-name (unsystematic) risk but also dilute the payoff from any one high-conviction idea,
   and market-wide (systematic) risk never goes away no matter how many names you hold. See
   Portfolio risk management below for the actual shape of that curve.
10. **Sector/theme spread matters as much as name count.** Ten stocks in one sector is not
    diversification; five stocks across uncorrelated sectors can be safer. Separately, judge
    which sectors have a genuine growth tailwind — rising capex/order-book/import-substitution
    data, policy support, rising FII allocation — via `sector_analysis`, `capital_allocation`
    trends across a sector's leaders, and WebSearch for capex-cycle/policy news; a good company
    in a shrinking or ex-growth sector still fights a headwind.
11. **Moat quality needs a competition-health check, not just a market-share number.** A widening
    moat built on genuine product/cost innovation is very different from one built on predatory
    pricing, regulatory capture, or supplier/distributor coercion that eventually invites
    antitrust or regulatory backlash. Cross-check `competitive_position`'s market-share trend
    against `business_profile`/`search_documents` for HOW share was won before crediting the moat.
12. **Alignment and exposure signals.** Prefer businesses with (a) lower discretionary exposure
    to government policy/regulation for their core revenue (check `business_profile`/10-K risk
    factors/WebSearch for licensing, price-control, or single-buyer-government dependence), (b)
    rising or stable FII ownership (`fii_stake_pct` trend via `shareholding_trends`) as a
    sophisticated-investor vote of confidence, and (c) promoter/management incentives aligned
    with minority shareholders rather than extractive — falling promoter stake, rising pledge, or
    heavy related-party transactions (`forensic_checks`) are the closest this data lake gets to
    "management greed"; this data lake has no direct CEO-pay-ratio field, so pull that from the
    annual report's MGT-9/remuneration disclosure or WebSearch (screener.in, annual report) if the
    user wants the literal number, and say plainly when you're citing a web figure vs a tool one.
13. **Government/policy exposure — quantify the dependency, don't speculate about connections.**
    For any company with meaningful government/PSU/regulated-sector exposure, establish: (a) what
    share of revenue is government/PSU-customer or licence-dependent (`business_profile` revenue
    mix, customer concentration; `search_documents` on `annual_report` for disclosed customer/
    segment detail), (b) which specific policies govern its economics — tariffs, import duty,
    price controls, subsidy/PLI-scheme eligibility, sector caps on FDI/ownership — and whether any
    are currently under review (WebSearch: PIB, ministry notifications, Budget documents, SEBI/RBI
    circulars, credible financial press), and (c) the company's own disclosed sensitivity to a
    named policy change (`search_documents` concalls/annual reports for management's own framing,
    e.g. "X% of margin is PLI-linked"). **Stick to disclosed, sourced facts** — government-contract
    share, named scheme eligibility, a minister's public statement on a sector, a credible-press
    report of a promoter's political donation (e.g. electoral-bond disclosures, which are public
    record) — and cite each. Do not infer or assert undisclosed political relationships,
    partisan favouritism, or "closeness to the ruling party" from circumstance; that is
    speculation, not evidence, and has no place in a decision-support analysis. A policy-heavy
    business isn't automatically a bad investment — regulatory certainty and a stable policy
    regime can be a moat too (licensed utilities, PSU banks with sovereign backing) — the point is
    to make the *dependency* visible, not to editorialise about *why* it exists.
14. **Cyclical vs seasonal vs secular — classify the industry before judging any trend.** These
    are three different shapes and conflating them misreads a perfectly normal pattern as a red
    flag (or vice versa):
    - **Cyclical**: performance swings with the broader economic/credit/commodity cycle over
      multi-year spans — cement, steel, capital goods, real estate, autos, most commodity chemicals,
      and (via the credit cycle) banks/NBFCs. A cyclical company's ROCE/margin peak at the top of
      its cycle is not the same quality signal as a structurally-improving business — check
      `sector_analysis` and `macro_data` (capex/IIP/commodity-price/rate-cycle series) for where
      the cycle currently sits, and look at the company's OWN multi-year ROCE/margin swing in
      `financial_health`/`financial_statements` to see the amplitude of its own cycle before
      extrapolating a good year forward. This is also why the Coffee Can screen's "every single
      year" consistency bar naturally filters out purely cyclical names — a business swinging with
      the commodity cycle structurally cannot clear it, which is a feature of the screen, not a
      gap in it.
    - **Seasonal**: predictable *intra-year* pattern tied to the calendar — FMCG/consumer
      durables (festive quarters), fertilizers/agri-inputs (sowing season), ACs/beverages (summer),
      sugar (crushing season), education (admission cycles), travel/hospitality (holiday season).
      For a seasonal business, judge growth/margin **YoY same-quarter**, never QoQ — a QoQ "decline"
      is routine seasonality, not deterioration. Establish the pattern from several years of
      `xbrl_quarterly`/`financial_statements(quarterly_results)` before reading any single quarter.
    - **Secular/structural**: growth driven by a multi-year structural shift (formalisation,
      digitisation, financialisation of savings, demographic/penetration catch-up) that is largely
      independent of the short economic cycle — the closest thing to a "true" Coffee Can compounder
      backdrop, though even these have their own slower-moving sensitivities (e.g. IT services to
      client budget cycles).
    State explicitly which of the three (or which mix) applies to a company/sector before drawing
    a conclusion from a growth, margin, or ROCE number — this classification belongs in every
    sector screen (`sector_analysis`) and every company dossier's business-context framing, not
    just forensics.

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

## Follow the money — the cash and related-party trail
A company can show clean growth/margin trends and still be leaking value through where cash
actually goes once it's generated. Don't stop at "is PAT growing" — trace the flow:
1. **Cash-flow waterfall**: operating cash flow → how it's actually deployed (`capital_allocation`
   + `financial_statements(cash_flow)`) — capex (growth vs maintenance), investments in
   subsidiaries/JVs, loans/advances given, debt raised vs repaid, dividends/buybacks. A company
   whose operating cash consistently gets diverted into growing "investments in subsidiaries" or
   "loans & advances" rather than into capex, debt reduction, or shareholder returns deserves a
   harder look at WHO those subsidiaries/counterparties are.
2. **Related-party transactions (RPT)**: scale of RPT revenue/expense/loans/guarantees relative to
   total revenue/assets (`forensic_checks`, annual-report notes via `search_documents`) — a
   growing or opaque RPT book, especially loans/guarantees to unlisted promoter-linked entities, is
   the single most common vehicle for value leaking off the P&L investors actually see.
3. **Use-of-proceeds check**: when a company has raised capital (IPO/QIP/rights/debt — visible as
   a financing-activity spike in `financial_statements(cash_flow)`), check whether the actual
   capex/investment that followed matches the stated purpose (prospectus/annual report via
   `search_documents`, or WebSearch) — proceeds diverted from their stated use is a classic fraud
   pattern.
4. **Promoter pledge → where did that money go**: pledged shares raise cash for the pledgor, not
   the company — `shareholding_trends`/`forensic_checks` shows the pledge; whether that cash served
   a legitimate purpose (funding a rights issue, an unrelated venture) or signals promoter-level
   financial stress is a WebSearch/concall question, not something the pledge number alone answers.
5. **Circular patterns**: the same counterparty appearing as both a major customer and a major
   supplier, or debtor/inventory growth persistently outrunning sales growth (channel-stuffing
   feeding fictitious revenue rather than real cash collection).
This is exactly the depth the `financial-forensics` skill's raw-line-item pass is built to run —
treat this list as the conceptual checklist and hand off there for the full procedure (RPT
notes-level detail, cash-flow waterfall by year, proceeds-usage tracing) on any company where
growth looks real on the surface but you want to confirm the cash backing it actually stayed in
the business and reached shareholders.

## Portfolio risk management — sizing, diversification math, correlation

The tools above score one company at a time; a portfolio's risk is not just the average of its
holdings' individual risk scores. Use this section whenever the user asks "how risky is my
portfolio", "how many stocks should I hold", "how do I size a position", or invokes stop-loss/
holding-period reasoning.

### How many names actually reduces risk
Unsystematic (company-specific) risk falls fast with the first few names added and flattens out;
systematic (market-wide) risk never diversifies away regardless of count. The classic finding
(Evans & Archer 1968; reproduced many times since, incl. on Indian portfolios) is that roughly
**15-25 stocks** captures most of the practical diversification benefit — a portfolio of 40
uncorrelated-in-theory names is rarely meaningfully safer than one of 20, it's just harder to
track with conviction. State this shape plainly rather than treating "more names = more safety"
as monotonic: below ~10-12 names, each additional name usually helps a lot; above ~25-30, it
mostly just dilutes your best ideas' contribution to the total return. **Correlation, not count,
is what actually does the work** — see below.

### Portfolio variance — the actual formula
For two positions with weights w1, w2, volatilities σ1, σ2 (use `annualized_volatility_pct` from
`screen_stocks`/`price_analytics` as the σ input) and correlation ρ12 between their returns:

`σp² = w1²σ1² + w2²σ2² + 2·w1·w2·ρ12·σ1·σ2`

Generalises to n assets as a full weight-vector × covariance-matrix × weight-vector product. The
practical takeaway: **if ρ12 is close to +1 (two IT-services names, two PSU banks), the portfolio
barely diversifies — you're carrying two tickets on the same bet.** If ρ12 is low or negative,
the combined volatility can be meaningfully below either name's own volatility even at equal
weight. This is the rigorous version of "diversify across sectors" — sector labels are a proxy
for correlation, not the thing itself; two companies in different official sectors can still move
together (e.g. anything geared to the same commodity or the same rate cycle).

**`beta` and `annualized_volatility_pct` in `screen_stocks`/`price_analytics` are real, precise,
tool-computed figures** — not estimates or proxies. They're derived directly from each stock's
own actual daily-return history (volatility = std of trailing-1y daily returns × √252; beta =
covariance with Nifty50 daily returns ÷ Nifty50's variance, over up to 3y). Nothing crude about
them at the single-stock level. What they DON'T capture is the interaction BETWEEN holdings —
two names can each show moderate individual volatility and still combine into a portfolio that's
barely diversified at all, if their return series move together. That's what needs a dedicated
multi-asset computation, not a per-stock column.

**Use the `portfolio_risk` tool for this — it computes via `empyrical` (empyrical-reloaded, the
maintained fork of Quantopian's open-source risk-stats library), not a hand-derived
approximation.** Give it the holdings + weights and it pulls each symbol's real daily price
history (the same parquet `price_history` reads), aligns them on a common date window, and
computes, from the empirical data directly:
- the full pairwise **correlation matrix** (always show this, not just a final number — a single
  "portfolio risk: X%" figure hides which pair is actually driving it)
- **portfolio annualized volatility** via the Markowitz w′Σw matrix form, cross-checked against
  directly computing it from the realized portfolio return series (both should agree almost
  exactly — the tool reports both so the number is verifiable, not asserted)
- **portfolio CAGR, max drawdown, and Calmar ratio** from the actual compounded daily series
- **Sharpe and Sortino ratios**, using a real risk-free rate (10y G-Sec yield from `macro_data`,
  or a rate the user supplies)
- **historical (empirical-percentile) VaR and CVaR at 95%/99%** — this is the fix for the crude
  version: instead of assuming returns are normally distributed and estimating a bad day as
  `volatility × z-score`, it takes the actual worst 5%/1% of days that really happened in the
  window and reports their real magnitude (VaR) and average severity (CVaR/expected shortfall).
  No normality assumption.
- **portfolio beta vs a benchmark**, cross-checked against the weighted average of the
  individual betas (these should match almost exactly since covariance is linear in weights —
  a large mismatch flags that one holding's history is much shorter and is distorting the
  common window)
- **Herfindahl-index-based "effective number of positions"** (1/HHI) — a concentration measure
  that's lower than the raw holding count whenever weights are uneven (e.g. three names at
  70/15/15 behave like ~1.9 equal-weighted positions, not 3), the concrete answer to "how many
  stocks do I really have, risk-wise."

State the lookback window used (correlations and beta are not stable — a 1-year window right
after a shared shock reads differently from a 5-year window) and that all of this is
historical/backward-looking, not a forecast.

### Position sizing
See `risk-profile-screen`'s position-sizing note for the boundary on personalised allocation
advice (concepts only, never a number for the user's actual portfolio). Concepts to explain when
asked: equal-weight (simplest, ignores conviction and risk differences), conviction-weight (more
in higher-conviction ideas, concentrates risk), inverse-volatility weight (size down the shakier
names so each contributes similar risk, not similar capital), and a hard per-position/per-sector
cap as a simpler practical guardrail than any of the above.

### Holding period, stop-losses, and momentum — reconciling with the philosophy above
A few common trading heuristics are real, evidence-backed effects — but they pull in a different
direction from the quality-compounding/Coffee-Can stance this skill leads with. Present both,
flag the tension, don't silently blend them:
- **Momentum is a documented factor** (Jegadeesh & Titman 1993): stocks trending up over 3-12
  months tend to keep outperforming over the next 3-12 months, and vice versa for downtrends —
  "don't catch a falling knife" has real evidence behind it at that horizon. But over multi-year
  horizons the evidence flips toward mean-reversion (De Bondt & Thaler) — extreme past winners
  tend to underperform and past losers outperform over 3-5y. **Which one applies depends on your
  holding period** — a momentum rule for a 3-12 month trade and a Coffee-Can "hold a quality
  compounder through volatility" thesis for a 5-10y position are two different strategies; say
  which one governs the decision at hand rather than applying "let winners run" logic to a
  name you're holding on a 10-year quality thesis, or vice versa.
- **A mechanical stop-loss (e.g. sell at -10% to -15%) is a capital-preservation and
  behavioural-discipline tool**, not a quality signal — it prevents anchoring on cost basis and
  "hoping" through a broken thesis. But applied literally to a quality-compounder position it can
  force selling a good business into a market-wide, non-thesis-breaking drawdown (Coffee Can
  names have had >20% drawdowns in perfectly intact multi-year compounding runs). The
  higher-fidelity version: treat a -10-15% move as a trigger to **re-underwrite the thesis**
  (re-run `financial_health`/`management_guidance`/recent `search_documents` for what actually
  changed), not an automatic sell — unless the user has explicitly stated they're running a
  trading/momentum strategy rather than a long-horizon quality strategy, in which case the
  mechanical rule is the strategy and should be honoured as stated.
- **Don't try to time the exact bottom or top** — trying to nail either means waiting for
  certainty the market never provides (the Mr. Market idea above). The practical antidote is
  staged entry/exit (buying or selling in tranches over time) rather than an all-at-once bet on a
  single price level.
- **Holding period and diversification are two DIFFERENT risk axes — don't conflate them.**
  "Hold longer" reduces exposure to *market-wide volatility timing* (the chance that the point
  you happen to look is mid-drawdown): for a diversified basket, the range of annualized outcomes
  narrows with time, so a bad 1-year stretch is far more common than a bad 10-year stretch, and
  expected cumulative return still compounds up — longer holding isn't inherently lower-profit,
  it mainly lowers the probability of realising a loss at the moment you check. **But that effect
  is about time, and does nothing for single-name concentration risk.** Holding ONE stock for 20
  years is not diversified at any horizon — the company can suffer permanent, business-specific
  impairment (disruption, fraud, a lost patent cliff, a failed product cycle, even outright
  bankruptcy) at year 19 exactly as it could at year 1, and time held provides zero protection
  against that because it's a single point of failure, not a volatility-timing problem. Time
  diversification shrinks *systematic* outcome dispersion for a basket; only holding multiple,
  genuinely uncorrelated names shrinks *idiosyncratic* single-company risk (see "How many names"
  above) — you need both a long horizon AND real cross-sectional diversification; one does not
  substitute for the other.

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
  behavioural caution (works with the `company-dossier` skill). Always frame the read against the
  company's cyclical/seasonal/secular classification and its government/policy exposure (points
  13-14 above) before calling a trend good or bad.
- Auditing one company for problems → the quantitative scores ARE the audit's backbone; hand off
  to `financial-forensics` for the full raw-line-item procedure AND the money-trail (cash-flow
  waterfall + related-party) procedure.
- Assessing a portfolio (existing holdings, not a fresh screen) → call `portfolio_risk` on the
  actual holdings/weights for the real correlation/volatility/Sharpe/Sortino/VaR/beta numbers,
  framed by the Portfolio risk management section above: name-count vs correlation trade-off,
  position-sizing concepts, and the momentum/stop-loss/holding-period nuances. Works with
  `risk-profile-screen`'s concentration check for a screened shortlist.
- Always end with: this is evidence, scoring and ranking on stated criteria — the buy/sell/
  allocation decision, and whether it fits your circle and required return, is the user's.
