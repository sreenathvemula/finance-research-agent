---
name: financial-forensics
description: Deep "find the issues with the financials" audit for one company — computes the named quantitative fraud/quality scores this data lake can actually support exactly (Altman Z''-Score, Piotroski F-Score, Sloan accrual ratio) from raw multi-year statements, hunts for accounting red flags, earnings-quality problems, governance concerns and balance-sheet risk, traces the cash-flow and related-party money trail (where cash actually goes, RPT loans/guarantees, use-of-proceeds), cross-checks against concalls and credit ratings, then flags what a pure-accounting view misses via a commercial/legal/operational/policy-dependency risk pass (adapted from Anthropic's PE due-diligence workstream taxonomy). Use when the user asks to "find problems/issues/red flags", "check the accounting quality", "is anything wrong with the financials", "trace where the money is going", "stress-test the numbers", or "how trustworthy are the financials".
---

# Financial forensics

A skeptic's audit — not a summary of pre-built flags. The goal is to surface everything
questionable — accounting quality, earnings quality, leverage, working-capital, dilution,
governance — using the same named, formula-precise tests professional forensic accountants use
(Altman, Piotroski, Sloan — all defined with exact formulas in `investing-principles`), computed
from raw statement pulls, then sanity-checked against what management and rating agencies say.
A "the flags look clean" conclusion without having actually run these numbers is not a completed
audit.

## Procedure

Pre-computed flags (`financial_health`, `forensic_checks`) are a **starting hypothesis list,
not the audit**. They're multi-year trend detectors — the exact kind of thing clever accounting
is designed to slide past (a one-off gain smoothed into a 12y trend, a bad quarter buried inside
a good year, a related-party deal that never shows up as a "flag" at all). Steps 3b and 3c below
are mandatory on every run, whether or not steps 2-3 fired anything — a clean flag read is not a
clean company, it's an unconfirmed one.

1. **Resolve** the symbol; note the company type (bank/NBFC vs non-financial) — it changes
   which flags apply.

2. **Trend flags** — `financial_health`. Read its concern/watch flags first; these seed the
   hypothesis list:
   - earnings quality: cumulative operating cash flow vs profit (accruals red flag if CFO
     lags PAT badly)
   - margin compression, interest-coverage weakness, doubled borrowings
   - genuine per-share dilution (EPS lagging PAT), debtor-day creep, ROCE decline
   (For banks/NBFCs these cash/coverage/WC flags are auto-suppressed — pivot to asset quality,
   NIM, growth vs capital.)

3. **Forensic checklist** — `forensic_checks`. Read the negatives in each topic bucket
   (Accounting Quality, Promoter & Governance, Balance Sheet & Debt, Growth & Returns,
   Valuation & Sentiment) and the explicit promoter-pledge reading.

3b. **Named quantitative scores — mandatory, computed not eyeballed.** Pull 2+ years of
   `financial_statements(profit_loss)` + `(balance_sheet)` + `(cash_flow)` and compute, using the
   exact formulas defined in `investing-principles`' Quantitative scoring section:
   - **Altman Z''-Score (EM variant)** (distress/solvency, non-financials only) — flag if < 2.6,
     hard flag if < 1.1. Skip for banks/NBFCs. State plainly that the A-term (working capital) is
     an estimate from Other Assets − Other Liabilities, not the textbook figure — see
     `investing-principles`' data-honesty note.
   - **Piotroski F-Score** (0-9 fundamental-trend score) — flag if ≤ 3, especially alongside a
     "statistically cheap" valuation (value-trap signature). If the current-ratio test isn't
     computable (same current/non-current split gap as Altman's A-term), say so and score out of
     8 rather than silently treating it as a pass or fail.
   - **Sloan accrual ratio** ((NI-CFO)/Avg Total Assets) — trend it across as many of the 12
     years as `financial_statements` covers; flag a persistently high or rising ratio even before
     `financial_health`'s qualitative CFO-vs-PAT flag would trigger.
   Show every input ratio (the five Altman components; the eight or nine Piotroski tests
   pass/fail) — not just the final score — so the computation is auditable, not asserted. State
   the two (or more) fiscal years/quarters used for each score explicitly.

3c. **Raw line-item deep dive — mandatory, not conditional on 3b's scores flagging anything.**
   Beyond the formula scores, pull `xbrl_quarterly` (segment + full P&L line detail, last ~8
   quarters) and re-read the same profit_loss/balance_sheet/cash_flow/quarterly_results pulled in
   3b line-by-line — don't stop at the tool's summary — for the anomaly types a formula score can
   still miss because it looks at ratios, not the underlying story:
   - **Other income / exceptional items propping up PAT**: is "other income" or "exceptional
     items" an unusually large share of PBT in any year/quarter, and does reported PAT growth
     survive stripping it out?
   - **Tax-rate anomalies**: any year/quarter with an effective tax rate far below the statutory
     band — often a one-off deferred-tax credit or MAT-credit reversal inflating PAT without
     matching operating improvement.
   - **Quarter-to-quarter jumps unexplained by seasonality**: sequential (QoQ) swings in
     revenue/margin/PAT in `xbrl_quarterly` that don't match the company's known seasonal
     pattern or peer quarters in the same period — flag and ask why.
   - **Segment-level divergence hidden in the consolidated number**: use `xbrl_quarterly`'s
     segment revenue/result to check whether one strong segment is masking a weak or
     deteriorating one in the consolidated total — the trend tools only ever see the blended
     figure.
   - **Balance-sheet items that don't reconcile with the P&L story**: e.g. inventory/debtors
     growing much faster than sales (channel stuffing risk), or capex not showing up as
     depreciation growth a few years later.
   State explicitly which raw statements/quarters were actually read, not just which flags fired
   — "checked FY21-FY26 quarterly P&L and 8 quarters of xbrl_quarterly; no other-income spike or
   tax anomaly found" is a real finding even when it's negative.

3d. **Follow the money — mandatory cash-flow and related-party trail.** Clean-looking P&L growth
   can still mask value leaking out through where the cash actually goes once generated. This is
   the deepest layer of the audit — don't skip it even when 3b/3c come back clean.
   - **Cash-flow waterfall, year by year**: pull `financial_statements(cash_flow)` +
     `capital_allocation` and trace operating cash flow → capex (split growth vs maintenance where
     disclosed) → investments in subsidiaries/JVs/associates → loans & advances given → debt
     raised vs repaid → dividends/buybacks paid. Flag if a rising share of operating cash is
     routed into "investments" or "loans & advances" rather than capex, debt paydown, or
     shareholder returns — that's the single biggest tell that cash is leaving the business for
     somewhere other than its own operations.
   - **Related-party transactions (RPT)**: pull the scale of RPT revenue/expense/loans/guarantees
     from `forensic_checks` and, for notes-level detail (counterparty names, nature, amounts),
     `search_documents(symbol=..., doc_types=["annual_report"], query="related party
     transactions loans advances guarantees")`. Size RPT loans/guarantees as a % of net worth or
     total assets, not just an absolute number — a growing or opaque RPT book, especially loans/
     guarantees extended to unlisted promoter-linked entities, is the classic vehicle for shifting
     value off the P&L investors see. Name the counterparty type (subsidiary, JV, promoter-owned
     private entity) whenever the notes disclose it.
   - **Investments in subsidiaries — opacity check**: from `financial_statements(balance_sheet)`,
     track the "Investments" line's growth and, via `search_documents(annual_report)`, identify
     which subsidiaries it's flowing into — unlisted, offshore/tax-haven, or JVs whose business
     purpose isn't clearly tied to the parent's stated strategy warrant explicit mention even
     absent proof of wrongdoing; say what's known and what remains opaque from local data.
   - **Guarantees/collateral for group entities**: contingent liabilities (guarantees given on
     behalf of subsidiaries or other group companies) don't hit the P&L but are real money-flow
     risk if called — check the annual report's contingent-liability note via `search_documents`.
   - **Use-of-proceeds check**: when `financial_statements(cash_flow)` shows a financing-activity
     spike (IPO/QIP/rights issue/large debt raise), check whether the capex/investment that
     followed matches the stated purpose (prospectus or annual report via `search_documents`, or
     WebSearch) — proceeds diverted from their disclosed use is a recognised fraud pattern, not a
     hypothetical one.
   - **Promoter pledge — where the pledged cash went**: `shareholding_trends`/`forensic_checks`
     shows the pledge and its trend; whether that borrowing served a disclosed legitimate purpose
     or signals promoter-level financial stress unrelated to the company is a `search_documents`/
     WebSearch question — say plainly when this can't be determined from local data.
   - **Circular-flow check**: does the same counterparty appear as both a major customer and a
     major supplier in segment/RPT disclosures? Does debtor/inventory growth persistently outrun
     sales growth (`financial_health`/`xbrl_quarterly`) in a way consistent with channel-stuffing
     rather than real cash collection?
   State explicitly which cash-flow years and which annual-report RPT/contingent-liability notes
   were actually read — "traced FY22-FY26 cash-flow waterfall and FY26 annual report RPT/
   guarantee notes; no material undisclosed-purpose related-party flow found" is a real, reportable
   finding even when negative.

4. **Capital & ownership** — `capital_allocation` (is growth debt-funded, is FCF chronically
   negative) + `shareholding_trends` (promoter stake falling? pledge rising? = governance
   warning). `insider_trading` for PIT sell patterns.

4b. **Executive pay (greed/governance signal)** — `search_documents(symbol=..., doc_types=
   ["annual_report"], query="ratio of remuneration of directors to median employee KMP
   percentage increase")`. Indian annual reports MUST disclose (Companies Act Rule 5(1)) each
   whole-time director/KMP's remuneration ratio to median employee pay and their YoY % increase,
   plus the company-wide median employee increase — pull the most recent year and compare: is
   leadership's raise far outpacing the median employee's and the company's actual performance
   (profit growth from `financial_health`)? A large, growing gap during flat/declining earnings
   is a legitimate red flag; a modest ratio in line with performance is not. Absolute Rs-crore
   pay figures are sometimes also stated — report them when present, ratio when not.

4b. **Executive pay (greed/governance signal)** — `search_documents(symbol=..., doc_types=
   ["annual_report"], query="ratio of remuneration of directors to median employee KMP
   percentage increase")`. Indian annual reports MUST disclose (Companies Act Rule 5(1)) each
   whole-time director/KMP's remuneration ratio to median employee pay and their YoY % increase,
   plus the company-wide median employee increase — pull the most recent year and compare: is
   leadership's raise far outpacing the median employee's and the company's actual performance
   (profit growth from `financial_health`)? A large, growing gap during flat/declining earnings
   is a legitimate red flag; a modest ratio in line with performance is not. Absolute Rs-crore
   pay figures are sometimes also stated — report them when present, ratio when not.

4b. **Executive pay (greed/governance signal)** — `search_documents(symbol=..., doc_types=
   ["annual_report"], query="ratio of remuneration of directors to median employee KMP
   percentage increase")`. Indian annual reports MUST disclose (Companies Act Rule 5(1)) each
   whole-time director/KMP's remuneration ratio to median employee pay and their YoY % increase,
   plus the company-wide median employee increase — pull the most recent year and compare: is
   leadership's raise far outpacing the median employee's and the company's actual performance
   (profit growth from `financial_health`)? A large, growing gap during flat/declining earnings
   is a legitimate red flag; a modest ratio in line with performance is not. Absolute Rs-crore
   pay figures are sometimes also stated — report them when present, ratio when not.

5. **Cross-check against narrative** — `search_documents` (symbol-scoped) on concalls for how
   management explains any flagged item (receivables, debt, related-party, contingent
   liabilities), and on `credit_rating` docs for the agencies' view of leverage/liquidity.
   A flag that management and raters also flag is corroborated; one they explain away, note as
   contested.

6. **Beyond local data** — for auditor changes, qualifications, litigation, SEBI actions, use
   WebSearch (credible domains only).

7. **Beyond the accounts — commercial/legal/operational context.** A "clean financials" verdict
   from steps 2-6 is still only the accounting workstream. Adapted from Anthropic's
   `private-equity/dd-checklist` skill (Apache 2.0, `anthropics/financial-services`), which
   frames diligence as seven workstreams (Financial, Commercial, Legal, Operational, HR/People,
   IT, Environmental/ESG) — pull in what's checkable from this data lake and flag what isn't:
   - **Commercial**: customer/revenue concentration (`business_profile` segment mix,
     `xbrl_quarterly` where a single segment or a named large customer dominates) — a
     concentrated customer base is a real risk even with spotless accounting.
   - **Legal/regulatory**: litigation, regulatory action, SEBI/RBI orders — WebSearch (credible
     domains), since this isn't in local structured data.
   - **Government/policy dependency**: what share of revenue/margin is government-contract,
     licence, tariff, or subsidy/PLI-scheme dependent (`business_profile`, `search_documents` on
     annual reports/concalls for management's own disclosed sensitivity), and is any relevant
     policy currently under review (WebSearch: ministry notifications, Budget documents, SEBI/RBI
     circulars). Report only disclosed, sourced dependency — see `investing-principles` point 13
     for why this must stay evidence-based and not speculate about undisclosed political ties.
   - **Cyclical/seasonal context**: before calling any margin or growth swing a "concern," check
     whether it's explained by where the industry sits in its cycle or its normal seasonal pattern
     (`investing-principles` point 14) — a commodity-cycle peak or a routine festive-quarter jump
     is a business-cycle fact, not an accounting red flag, and conflating the two miscalibrates
     the audit's own findings.
   - **Operational**: key-person/management dependency (cross-check `management_guidance` for
     signs of a single-founder-dependent narrative), vendor/supplier concentration
     (`supply_chain`).
   - **HR/People and IT/ESG**: generally outside this data lake's coverage — say so explicitly
     rather than silently skipping; point to WebSearch or note as an open question for the user.
   This step is intentionally lighter than steps 2-6 — the goal is to flag that "financials look
   clean" isn't the same claim as "no risk exists," not to duplicate a full commercial/legal DD.

## Output format

Lead with a **scorecard line**: Altman Z'' / Piotroski F / Sloan accrual, each with its number
and flag status, before the narrative findings. Then rank findings by materiality:
**Critical / Notable / Minor** — read these as **Critical = deal-breaker-grade** (would
materially change the investment thesis, or signals possible fraud/going-concern risk),
**Notable = significant** (a real concern needing monitoring or management explanation, not
necessarily thesis-changing on its own), **Minor = manageable** (worth recording, unlikely to
matter alone) — each a one-line claim + the number(s) + the source. Separate "confirmed by
multiple sources" from "single-source / needs verification". Add a short **"Raw statements
checked"** line listing exactly which statements/periods were read line-by-line in steps 3b/3c
(this is what distinguishes this audit from reading `financial_health`'s summary and stopping), a
**"Money trail"** line summarising what step 3d's cash-flow waterfall and RPT/contingent-liability
notes pass found (or didn't — a clean trail is a reportable finding too), and a **"Beyond
accounts"** line noting what step 7 covered and what it explicitly couldn't (HR/IT/ESG gaps). End
with the 2-3 questions the user should put to management or dig into next.

## Rules

- Distinguish a real red flag from an artefact (a bonus issue is not dilution; a bank's low
  CFO/PAT is not an accruals problem). `financial_health` already handles these — don't
  re-flag them.
- Never treat a clean `financial_health`/`forensic_checks` read as sufficient on its own — steps
  3b (named scores), 3c (raw-line-item pass), and 3d (money trail) are what catch the things
  multi-year trend flags are structurally blind to (a single bad quarter inside a good year, a
  one-off gain, a related-party shift, a distress trajectory that only shows up once
  Altman/Piotroski/Sloan are actually computed, cash quietly rerouted into an opaque related
  entity). Skipping any of the three is skipping the audit, not shortening it.
- Every claim carries its figure and source. Don't manufacture alarm; if the financials look
  clean after the score pass, the raw-line-item pass, AND the money-trail pass, say so plainly
  with the evidence from all three — a "score clean, line-items clean, money trail clean"
  conclusion is worth exactly as much as a well-founded "here's what's wrong" one.
- Government/policy-dependency findings and RPT/promoter-connection findings must stay
  evidence-based and sourced — quantify disclosed dependency or cite a credible report; never
  assert an undisclosed political or personal connection from inference alone
  (`investing-principles` point 13).
