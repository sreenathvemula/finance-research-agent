---
name: management-credibility
description: Audit whether a company's management does what it says — pulls past earnings-call guidance/targets (revenue, margin, capex, outlook), checks them against results actually delivered, and scores the result with a precise hit-rate/variance-band methodology plus a concall tone/evasiveness read (hedging language, dropped targets, blame patterns). Use when the user asks "did they deliver on guidance", "can I trust this management", "are they credible", "do they meet their targets", or wants management quality/track-record assessed.
---

# Management credibility

Trust is earned by delivery. This skill compares what management PROMISED against what the
company REPORTED, scores the gap with a precise, repeatable methodology (not a vibe), and reads
the language around misses for evasiveness patterns — so the user can judge credibility on
evidence, not tone alone.

## Procedure

1. **Resolve** the symbol; note the company type — guidance style differs materially (IT
   services guide revenue-growth ranges and margin bands; manufacturers guide capex and
   capacity; banks guide credit growth, NIM and credit-cost).

2. **Pull both sides** — `management_guidance` (default `lookback_periods=6`; use 8-10 for a
   fuller track record). It returns:
   - dated forward-looking / guidance statements retrieved from past concalls
     (revenue-growth, margin, capex, outlook themes), and
   - the actual reported quarterly results (Sales, OPM %, Net Profit, EPS).
   If concalls aren't indexed for the company, it says so and still returns the actuals —
   note the limitation and don't force a credibility verdict off actuals alone.

3. **Score each guidance instance with a precise variance band**, not a loose "close enough":
   `Variance % = (Actual - Guided) / |Guided| × 100` (for a guided range, compare against the
   nearer band edge; for a directional guide with no number — e.g. "margins should improve" —
   score it qualitatively as met/missed against the reported change, and say you did).
   - **Beat**: actual exceeds guidance by >5%
   - **Met**: actual within ±5% of guidance
   - **Modest miss**: actual below guidance by 5-15%
   - **Large miss**: actual below guidance by >15%, or guidance quietly dropped/revised down
     without being flagged as such by management
   Tabulate every instance found — don't cherry-pick the clean ones.

4. **Compute the summary scores:**
   - **Hit rate** = (instances scored Met or Beat) / (total instances) over the lookback window
     — state as e.g. "6/8 quarters (75%)".
   - **Directional accuracy vs magnitude accuracy** — separate "called the right direction but
     missed the size" (e.g. guided "modest growth", delivered 2% when they meant closer to 8%)
     from "called the wrong direction entirely" (guided growth, delivered a decline). The latter
     is materially worse for credibility than the former.
   - **Sandbagging vs over-promising pattern** — check whether misses cluster as chronic
     over-promising (repeated large misses, same direction every time) vs chronic sandbagging
     (guidance consistently beaten by a wide, suspiciously consistent margin — itself a credibility
     flag, since it implies guidance is being lowballed rather than genuinely forecast).

5. **Trace specific commitments** — `topic_timeline(query="guidance/outlook", symbol=...)` to
   follow one concrete commitment (a margin target, a capex number, a capacity-addition date, a
   demand call) across consecutive quarters and see whether it was hit, quietly revised, or
   dropped from the narrative entirely without acknowledgement — a target that simply stops being
   mentioned once it's clearly going to be missed is itself a finding.

6. **Read the language, not just the numbers — a concall tone/evasiveness checklist.** When
   pulling the concall text for a miss, check for:
   - **Ownership vs deflection**: does management own a miss plainly, or attribute it entirely to
     external factors even when peers in the same sector didn't see the same hit
     (cross-check with `sector_analysis`/peer commentary where feasible)?
   - **Hedging density around a topic**: repeated qualifiers ("subject to," "broadly," "we
     remain cautiously optimistic") with no specific number, especially on a topic that was
     given a specific number last quarter — a sign a firm commitment is being walked back
     informally before it's formally revised.
   - **Non-answers to analyst follow-ups**: does management answer the direct question asked, or
     pivot to a prepared talking point? Repeated pivoting on the same line of questioning across
     quarters is a pattern, not a one-off.
   - **Silent target abandonment**: a specific guided number (margin target, revenue milestone)
     that appeared in 2-3 consecutive calls and then is simply never mentioned again, with no
     explicit "we are revising/dropping this" — flag it explicitly as silent abandonment, which
     is a materially worse credibility signal than an openly revised target.

7. **Corroborate** — `search_documents` on later concalls to see whether management
   acknowledged misses or reframed them; `credit_rating` rationales sometimes independently flag
   repeated guidance shortfalls (ratings agencies see this pattern across cycles). WebSearch
   (credible domains) for any well-known guidance controversy or restatement.

## Output format

A **promise-vs-delivered table**: date guided | what was promised (with the actual guided
number/range) | subsequent actual | **variance %** | verdict (beat / met / modest miss / large
miss / too-early-to-tell / silently walked back). Follow with the **summary scorecard**: hit
rate (X/Y, %), directional-accuracy note, sandbagging-vs-over-promising read, and the tone/
evasiveness observations from step 6 with the specific quarter(s) they came from. Close with a
short read on the overall pattern — consistently conservative-and-delivers, chronically
over-promises, sandbags for an easy beat, or mixed — with the specific instances that support
it. Note data gaps honestly (no concalls, few quarters, ranges too vague to score numerically).

## Rules

- This is a judgement the tool sets up but you and the user make — surface the evidence, state
  the pattern you see, don't launder it into a buy/sell call.
- Be fair: a single miss in a tough macro quarter, openly explained at the time, is not
  "untrustworthy". Look for repeated, self-inflicted misses, misses not explained until asked,
  or targets that disappear quietly — vs external shocks management flagged in advance and owned.
- Every variance % and every language observation cites its concall by period — no unattributed
  "management seemed evasive."
