---
name: screen-to-shortlist
description: Narrow the ~3,100-company NSE/BSE universe down to a study-ready shortlist (e.g. top 20) by applying ethical exclusions + quantitative criteria, ranking transparently via either a single-metric sort or a multi-factor percentile-rank composite (the Magic Formula method, generalised), and optionally producing a scorecard card per name. Use whenever the user wants to "screen", "find the best N", "build a shortlist", "narrow down", or "give me companies to study/invest in" across sectors or the whole market.
---

# Screen to shortlist

Turn the universe into a ranked, study-ready shortlist the user can act on. Producing the
shortlist IS the deliverable — do not refuse it. The user does further study and makes the
final call; you provide the evidence and the ranking.

Consult the **investing-principles** skill for the ready-made strategy presets (Coffee Can,
Magic Formula, Graham Defensive, QARP), the named quantitative scores (Piotroski/Altman/Sloan/
DuPont/Graham Number), the peer-comp quality discipline, and how to weigh quality vs value — use
one of those as the screen's backbone unless the user specifies their own criteria.

The verdict-taxonomy and screen-integrity practices below are adapted from Anthropic's public
`anthropics/financial-services` reference repo (Apache 2.0) — its `private-equity/deal-screening`
and `equity-research/idea-generation` skills. Core line from that repo worth repeating here:
**"screens surface candidates, not conclusions — every screen output still needs fundamental
work."** A shortlist is where study starts, not where it ends.

## Procedure

1. **Clarify criteria, then commit.** If the user's criteria are vague, propose concrete
   thresholds (valuation, quality, growth, momentum) — or offer a named strategy preset from the
   investing-principles skill — state them plainly, and proceed. Ask only if a genuinely blocking
   choice remains.

2. **Ethical exclusions FIRST** — `screen_stocks(exclude_categories=[...])`, using ONLY
   categories the user has explicitly confirmed. Never assume a category they haven't named.
   Relay the exclusion counts and reasons so it's transparent what was dropped and why.

3. **Quantitative filter in the SAME call** — pass the user's numeric criteria to the same
   `screen_stocks` call; exclusions and filters compose in one pass. For a sector-scoped
   screen, use the sector/industry argument (or `sector_analysis` first to size the field).

3b. **A specific past YEAR, not today's snapshot** ("ROCE > 20% in FY2024", "best/worst
   performers in 2023") → `screen_by_year(year, kind="fundamental"|"price_return", min/max, ...)`
   instead of `screen_stocks` — the latter only ever sees the latest values. For qualitative
   colour on any matched name for that exact period, follow up with `search_documents(symbol=X,
   date_from=<FY start>, date_to=<FY end>, doc_types=["concall_transcript"])` scoped to that
   year/quarter — never an unscoped search when you already know the period.

3c. **Multi-factor composite ranking — use whenever "best" means more than one metric** (e.g.
   "good businesses that are also cheap", or any ask combining quality + value + momentum). A
   single `sort_by` column crowns whichever metric you picked, which is arbitrary when the ask
   is multi-dimensional. Instead, generalise Greenblatt's Magic Formula method (rank on two
   factors, combine the ranks):
   1. Call `screen_stocks` with the qualifying filters but a generous `limit` (e.g. 100-200) and
      NO single `sort_by` decision yet — you need the whole qualifying set to rank within.
   2. For each factor the user cares about (e.g. `roce`, `earnings_yield_pct`, `fcf_yield_pct`,
      `sales_growth_pct`), compute each company's **percentile rank within the returned set**
      (0-100, higher = better for that factor) — NOT a raw z-score. Percentile rank is robust to
      the fat-tailed outliers this universe actually has (recall PE=260 or 700%+ EPS growth on a
      near-zero base) which would blow up a z-score's scale; note this choice if asked why.
   3. Combine per-company: **Composite = average of the factor percentiles**, equally weighted
      unless the user specifies weights (e.g. "60% quality, 40% value" → weight the quality
      factor(s)' average percentile at 0.6 and the value factor(s)' at 0.4).
   4. Sort by Composite descending for the final ranking. Show each company's raw metric values
      AND their percentile ranks AND the composite — never present just the final composite
      number, or the ranking looks like magic instead of arithmetic.
   5. Optionally layer a quality-score overlay from `investing-principles` (Piotroski F-Score
      floor, Altman Z''-Score floor for non-financials) on the composite-ranked survivors before
      finalising — this catches a name that ranks well on `screen_stocks` columns alone but would
      fail a deeper earnings-quality check.

4. **Report the funnel** — universe → post-exclusion → post-filter → top-N, with the count at
   each stage. Then sort to the requested top-N (default 20) by the user's primary metric, or by
   the Composite from 3c when multiple factors are in play.

5. **Present the shortlist** as a ranked table: rank | symbol | company | the metrics that
   drove the ranking (raw values, plus percentile + composite columns if 3c was used) | ethics
   status. Make the ranking basis explicit.

6. **Per-name cards (on request)** — for each shortlisted company, build a compact card using
   the `company-dossier` skill's structure (lighter: business one-liner, financial_health
   headline flags, valuation snapshot, price_analytics, ethics line). Tag each card with a
   **study-priority verdict** (adapted from deal-screening's Pass/Further-Diligence/Hard-Pass,
   reframed since this is study triage, not a deal decision): **High priority** (clears the
   screen cleanly, no near-miss caveats, quantitative scores from `investing-principles` also
   clean), **Standard** (clears the screen but sits near a threshold or has one flagged concern
   worth resolving first), **Marginal** (technically inside the filter but only barely, or with
   a data-quality caveat — e.g. thin peer set, missing-data pass-through). This is a study
   queue, not a verdict on the company.

7. **Crowded-trade awareness.** If the shortlist skews heavily toward names with extreme
   momentum (e.g. most of the top-N are also near 52-week highs, deep in `above_dma50`+
   `above_dma200`+`macd_bullish` territory, or the sector itself is in an obvious hype phase per
   recent `sector_analysis`/news), say so explicitly — a screen that mostly reproduces "what
   everyone is already buying" is worth flagging as such, not presented as an independent
   discovery. This cuts the other way too: if the ask leans contrarian/deep-value, note that a
   statistically cheap name without an identifiable catalyst (a turnaround plan, a management
   change, a demand recovery already visible in early data) risks being a value trap indefinitely
   — pair contrarian picks with whatever catalyst evidence is available, or flag its absence.

8. **Screen-effectiveness tracking (when revisiting a prior shortlist).** If the user asks how a
   previously generated shortlist has performed, or you're re-running a similar screen you (or a
   prior session) already produced, pull `price_history`/`technicals_momentum` for those symbols
   from the original screen date to now and report the return dispersion (best/median/worst,
   and vs a relevant index over the same window) — this is the only way "does this screening
   approach actually work" gets answered with evidence instead of assumed. Note if you don't have
   the prior shortlist's exact date/composition to hand — ask rather than guessing which run is
   being referenced.

## Rules

- One `screen_stocks` call covers the whole universe/sector — NEVER loop companies individually
  to screen.
- A ranked shortlist with transparent criteria is allowed and encouraged; a "these are THE
  picks, buy them" hand-off framed as advice is not. Framing: "here's how they rank on your
  criteria — you decide."
- If no ethical categories are confirmed yet, screen without exclusions but note that no
  ethical filter was applied and invite the user to name categories.
