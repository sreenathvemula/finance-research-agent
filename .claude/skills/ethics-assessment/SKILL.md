---
name: ethics-assessment
description: Assess whether a company is ethically investable using the user's own doctrine-of-double-effect framework (four principles + decision framework in scripts/Ethical Investment.txt) applied to what the company actually does and how it conducts itself, with explicit materiality bands for proportionality (segment revenue/EBIT share) and a proximate-vs-remote material-cooperation test for supply-chain/customer/lending links to an excluded activity the company doesn't itself run. Use when the user asks whether a company is "ethical", "moral", "OK to invest in on principle", asks to "apply the ethics framework", or requests an exclusion/values screen on a specific company.
---

# Ethics assessment

Apply the user's ethical-investment framework rigorously to a company's real businesses and
conduct — not a keyword guess. The judgement and the final investment decision remain the
user's; you provide a structured, evidence-based assessment.

## Procedure

1. **Load the framework (source of truth).** Read `scripts/Ethical Investment.txt` before
   assessing — the user may have edited it. Apply exactly the four principles and the decision
   framework it states (doctrine of double effect: act morally neutral/good; good effect
   intended not the bad; good not flowing from the bad; proportionality — plus the primary
   moral orientation, trajectory, alternatives and witness tests).

2. **Establish what the company actually does.** `business_profile` (revenue mix by segment —
   this is what reveals whether a "diversified" company derives revenue from an excluded
   activity, e.g. tobacco buried inside an FMCG), `company_overview` "about", and
   `search_documents` for segment detail. Do NOT judge on the sector label alone.

3. **Surface conduct & controversies.** `search_documents` on annual reports / ratings for
   governance and litigation; WebSearch (credible domains only) for controversies, regulatory
   actions, environmental/labour issues, and how the company presents itself. Include executive
   pay as a conduct signal: `search_documents(symbol=..., doc_types=["annual_report"], query=
   "ratio of remuneration of directors to median employee percentage increase")` — the
   mandatory KMP-pay-ratio-to-median-employee disclosure. A large, widening gap between
   leadership's raise and both the median employee's raise and actual company performance
   bears on proportionality/fairness; a modest, performance-linked ratio does not.

4. **Walk the framework explicitly, with a materiality band for proportionality — not a vague
   "some/a lot".** Where a company has both benign and problematic activities, quantify the
   excluded segment's share of revenue AND profit/EBIT (profit share can diverge sharply from
   revenue share — a high-margin excluded segment can dominate economics while looking small on
   revenue alone) from `business_profile`/`xbrl_quarterly` segment data, and place it on an
   explicit band:
   - **<5% of revenue and profit** — immaterial; note it but don't let it drive the verdict.
   - **5-15%** — material; proportionality must be argued explicitly (what's the offsetting good,
     is the exposure shrinking or growing over recent years via `shareholding_trends`-style
     trend reads on segment mix).
   - **>15%**, or growing meaningfully as a share of profit even if revenue share is flat —
     treat as dominant to the proportionality question; a clean pass here requires an
     unusually strong case, not a passing mention.
   Then go through each principle and decision-framework test in turn, stating how the actual
   businesses and conduct measure against it, with evidence.

4b. **Proximate vs remote material cooperation — for supply-chain, lending, or customer links
   to an excluded activity the company doesn't itself run.** The doctrine-of-double-effect
   tradition this framework is drawn from distinguishes a company's own excluded activity from
   *cooperating* with someone else's: a bank/NBFC lending to an excluded-category borrower, a
   logistics/packaging/ad-services firm serving an excluded-category customer, a chemicals firm
   supplying a commodity input used across many industries including an excluded one. Score this
   distinctly from direct involvement:
   - **Proximate cooperation** (the excluded activity is a named, concentrated, or dedicated
     customer/business line — e.g. a firm built substantially around servicing one excluded-
     category client) weighs close to direct involvement.
   - **Remote cooperation** (a fungible commodity input or general-purpose service supplied
     into a broad, diversified customer base where the excluded-category share is small and
     unconcentrated) weighs much lighter — flag it for the record, but don't treat it as
     equivalent to the company running the activity itself.
   Use `business_profile`/`supply_chain`/`search_documents` (customer concentration disclosures)
   to establish which situation actually applies; don't assume proximate cooperation without
   evidence of concentration.

## Output format

- A one-line verdict: **investable on these principles / excluded (reason) / flagged
  (contested, reason)**.
- The principle-by-principle walk-through, each with the specific evidence.
- Where relevant, the material figure on its band (e.g. "tobacco ≈ X% of revenue / Y% of EBIT —
  material band, 5-15%") and, if applicable, the proximate/remote cooperation classification with
  the evidence for which one applies.
- The open ethical questions the user must weigh personally.

## Rules

- Tobacco is an explicit exclusion; other categories are the user's own call — apply only
  categories the user has confirmed, and clearly separate "framework says exclude" from "your
  values call".
- Segment revenue beats sector tags: a company can be excluded for a minority activity, or
  cleared despite a scary-sounding label — check the actual mix.
- Balanced and evidenced; never moralise beyond the framework, and never convert the ethical
  read into a buy/sell instruction.
