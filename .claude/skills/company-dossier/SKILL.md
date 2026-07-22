---
name: company-dossier
description: Build a full investment-research dossier / scorecard for a single Indian-equity company (NSE/BSE) — business & moat with cyclical/seasonal/secular industry classification and government/policy exposure, financial health with DuPont ROE decomposition, earnings quality (Piotroski/Altman quick checks), governance, management credibility, valuation, price context, ethics status, ending in Strengths / Concerns / Open-questions with an explicit per-dimension score. Use whenever the user asks to "analyse", "study", "do a deep dive on", "give me a full picture of", or "should I look at" a specific company for potential investment.
---

# Company dossier

A complete, evidence-based deep dive on one company that puts the user in a position to
decide. This is decision-support: you rank, score and lay out evidence — you never issue a
personalised buy/sell instruction or a fabricated price target as fact.

Consult the **investing-principles** skill for the quality checklist, the named quantitative
scores (DuPont, Piotroski F, Altman Z'', Sloan accrual, Graham Number), valuation discipline,
red-flag list and behavioural cautions — this dossier is where you apply all of it, not just the
qualitative checklist. A dossier that skips the quantitative scores is a summary, not a dossier.

## Procedure

1. **Resolve & frame.** If given a name, call `resolve_company` for the symbol. Establish the
   anchor period from `financial_statements(quarterly_results)` latest column — state it. Note
   company type (bank/NBFC vs non-financial) since it changes which checks below apply.

2. **Business & moat** — `business_profile` (what it does, revenue mix by product/geo/segment,
   operating KPIs, market share) + `competitive_position` (peer benchmarking, market-share
   trend). Apply a **VRIO read** on any claimed advantage (brand, distribution, cost position,
   licence/regulatory moat): is it Valuable, Rare, hard to Imitate, and is the Organisation
   actually capturing it in margins/returns — a "strength" that fails Imitability (any
   competitor could replicate it in 2-3 years) is weaker than one claimed. For raw-material/
   customer/competitor colour not in local data, `supply_chain` then WebSearch (credible domains
   only). **Classify the industry** cyclical / seasonal / secular (`investing-principles` point
   14) — this frames how to read every growth/margin number in steps 3 onward — and note
   **government/policy exposure**: disclosed government-contract/PSU-customer share, licence or
   subsidy/PLI dependency, and any policy currently under review (point 13; stay sourced, never
   speculate about undisclosed political ties).

3. **Financial health & quality — quantitative, not just directional.**
   - `financial_health` (12y trends + concern/watch/strength flags) + `capital_allocation`
     (capex vs FCF vs debt vs dividends).
   - **DuPont 5-step decomposition** of ROE (Tax Burden × Interest Burden × Operating Margin ×
     Asset Turnover × Financial Leverage — exact formula in `investing-principles`): state
     *which* factor is driving the headline ROE/ROCE. A margin/turnover-driven ROE is higher
     quality than a leverage-driven one at the same headline number — this is the single most
     common thing a shallow "ROE looks good" read misses.
   - **Piotroski F-Score** (0-9) as the fundamental-trend-direction check, and **Altman
     Z''-Score** (EM variant, non-financials only) as the distress/solvency check — both quick
     to compute from 1-2 years of `financial_statements` and both flag things a pure trend-line
     read can miss (see `investing-principles` for exact formulas and cutoffs).
   - Pull raw lines from `financial_statements` whenever a specific number needs verifying, not
     only when a flag already fired.
   - If anything here looks off (weak accrual quality, low F-Score, deteriorating Z''), that is
     the cue to run the full `financial-forensics` skill rather than patching over it here.

4. **Earnings quality & governance** — `forensic_checks` (accounting/governance checklist +
   pledge), `shareholding_trends` (promoter stake direction, pledge, FII/DII flows),
   `insider_trading` (PIT disclosures). **Sloan accrual ratio** ((NI-CFO)/Average Total Assets,
   trended across available years) as the precise earnings-quality number to sit alongside the
   qualitative accruals flag. Note the scale of related-party transactions from `forensic_checks`
   at a glance; if it looks material or opaque, that's the cue to hand off to the full
   `financial-forensics` money-trail procedure rather than digging deeper here.

5. **Management credibility** — `management_guidance` (past guidance vs actually-delivered
   results). If concalls aren't indexed, say so and rely on the actuals it returns. For a full
   promise-vs-delivered table, hand off to the `management-credibility` skill; here, one line is
   enough (pattern + 1-2 supporting instances).

6. **Valuation** — `valuation_summary` (multiples vs own history & peers, 3-scenario DCF) +
   **Graham Number** (√(22.5 × EPS × Book Value/Share)) as a quick classic-value ceiling
   cross-check. Always surface the DCF assumptions (cost of equity, terminal & scenario growth)
   and compare implied growth to what the company has actually delivered
   (`hist_sales_cagr`/`hist_eps_cagr` vs `implied_growth_10y`).

7. **Price context** — `price_analytics` (52w range, drawdown, volatility, relative strength
   vs NIFTY, MA structure).

8. **Ethics status** — one line: pass, or flagged-with-reason. For a full ethical judgement,
   use the `ethics-assessment` skill.

## Output format

Lead with a 2-3 line bottom line (where it scores well vs poorly) and one line stating the
industry classification (cyclical / seasonal / secular — or a stated mix) since it frames how
every number below should be read. Then a compact **scorecard table** with an EXPLICIT
per-dimension score, not just a reading:

| Dimension | Reading | Signal |
|---|---|---|
| Business & moat (VRIO) | ... | strong / mixed / weak |
| Government/policy exposure | e.g. "12% revenue PSU-tender, no live policy review found" | low / moderate / high dependency |
| ROE quality (DuPont driver) | e.g. "margin-driven, 18% opm, leverage 1.3x" | strong / mixed / weak |
| Fundamental trend (Piotroski F/9) | e.g. "7/9" | strong (≥7) / mixed (4-6) / weak (≤3) |
| Solvency (Altman Z'', non-fin only) | e.g. "3.4" | strong (>2.6) / mixed (1.1-2.6) / weak (<1.1) |
| Earnings quality (Sloan accrual) | e.g. "-2% of assets, stable" | strong / mixed / weak |
| Governance | ... | strong / mixed / weak |
| Management credibility | ... | strong / mixed / weak |
| Valuation (vs own history/peers/Graham Number) | ... | cheap / fair / rich |
| Price/momentum context | ... | strong / mixed / weak |

Then short sections per dimension with the numbers that justify each call. Close with three
lists:

- **Strengths** — evidenced, with figures.
- **Concerns** — evidenced, ranked by materiality.
- **Open questions** — what the user must resolve themselves before investing (e.g. "promoter
  stake fell 3 quarters running — find out why").

## Rules

- Every number comes from a tool. Cite documents inline (doc type + period); give as-of dates
  for market data; source + URL for web facts. Separate local-data findings from web findings.
- Don't substitute a qualitative "strong/weak" for a computable score when the inputs are
  available — if you can compute the DuPont split, Piotroski F, Altman Z'', Sloan ratio, or
  Graham Number from data already pulled, compute it; "ROCE looks healthy" without the DuPont
  breakdown is the shallow version of this dossier, not the complete one.
- Banks/NBFCs: `financial_health` already suppresses cash-conversion / interest-coverage /
  working-capital flags, and Altman Z'' does not apply — lean on NIM, growth, ROE (with its own
  DuPont-style split into margin/leverage), asset quality and governance instead.
- End with a one-line data-freshness note (local prices ~mid-2026; name the latest filed
  quarter used).
