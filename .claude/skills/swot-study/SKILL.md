---
name: swot-study
description: Produce a rigorous, evidence-cited, timestamped SWOT for a company — internal Strengths tested against the VRIO framework (Valuable/Rare/Inimitable/Organised) so claimed moats aren't taken at face value, external Opportunities/Threats sized against a cited TAM and structured via Porter's Five Forces plus PESTEL macro factors, combining business mix and financials (internal) with sector, competitive and regulatory dynamics (external). Use when the user explicitly asks for a "SWOT", or for strengths-and-weaknesses / competitive-and-industry analysis of a company.
---

# SWOT study

A SWOT is a synthesis, not a single tool call — and a list of generic bullet points ("strong
brand", "competitive industry") is not a SWOT, it's a template. Internal factors (Strengths,
Weaknesses) come from the company's own business and financials, tested for durability, not just
asserted; external factors (Opportunities, Threats) come from its industry structure,
competitors and the macro/regulatory backdrop, structured so nothing obvious gets skipped. Every
point must be evidenced.

## Procedure

1. **Resolve** the symbol and establish what the company does — `business_profile` (revenue
   mix, KPIs, market share).

2. **Internal — Strengths & Weaknesses, tested with VRIO.**
   For every candidate Strength, run it through the **VRIO test** before it earns the label:
   - **V**aluable — does it actually let the company exploit an opportunity or neutralise a
     threat (show up in margin, growth, or market share — not just asserted)?
   - **R**are — do most competitors lack it, per `competitive_position`'s peer benchmarking?
   - **I**nimitable — how hard/costly would it be for a competitor to replicate (regulatory
     licence, patent, network effect, switching cost, scale economics) vs easily copyable (a
     current low price, a temporary capacity edge)?
   - **O**rganised to capture it — is the company's own capital allocation/execution actually
     converting the advantage into returns (`capital_allocation`, `financial_health` ROCE trend),
     or is the advantage real but poorly monetised?
   A claimed strength that fails Rare or Inimitable is a *temporary* advantage — label it that
   way, don't inflate it to a moat. Sources:
   - `financial_health` — durable growth, margins, ROCE, balance-sheet strength → candidate
     strengths; its concern/watch flags → weaknesses.
   - `competitive_position` — where it leads vs lags peers on operating metrics / market share
     (this is the Rare test's evidence).
   - `forensic_checks` + `shareholding_trends` — governance strengths or weaknesses.
   - `capital_allocation` — disciplined reinvestment / cash return (strength, and the
     Organised test's evidence) vs debt-funded or FCF-negative growth (weakness).

3. **External — Opportunities & Threats, structured with Porter's Five Forces + PESTEL.**
   Don't free-associate threats — walk all five forces explicitly so nothing structural gets
   missed, then layer macro (PESTEL) on top. Before the forces, size the playing field the way
   Anthropic's `equity-research/sector-overview` skill (Apache 2.0,
   `anthropics/financial-services`) does: state the **Total Addressable Market with a cited
   source** (WebSearch/annual report — never an invented figure) and the sector's recent growth
   rate, so Opportunities are sized against a real market ceiling rather than asserted as
   unbounded upside.
   - **Threat of new entrants** — capital intensity, licensing/regulatory barriers, brand/
     distribution moats (from `sector_analysis` company-count/concentration and
     `business_profile`).
   - **Bargaining power of suppliers** — `supply_chain` (concentration, switching cost,
     commoditised vs specialised inputs); WebSearch for raw-material pricing power dynamics not
     in local data.
   - **Bargaining power of buyers/customers** — customer concentration, contract structure,
     price sensitivity (from `business_profile` revenue mix and any customer-concentration
     disclosure); B2B/few-large-customer models score differently from diversified retail.
   - **Threat of substitutes** — alternate technologies/products/business models that could
     displace the offering (WebSearch for disruption risk, credible domains only).
   - **Rivalry among existing competitors** — `sector_analysis` (sector size, growth, company
     count, concentration) + `competitive_position` (market-share stability/erosion — a
     fragmenting share in a crowded field is a live Threat, not a Weakness).
   - **PESTEL overlay** — Political/regulatory (licensing, tariffs, PLI-type schemes),
     Economic (rate cycle, currency via `macro_data` if rate-/currency-sensitive), Social
     (demand-shift trends), Technological (disruption, already partly covered above),
     Environmental (compliance cost, ESG-linked financing), Legal (litigation, antitrust) — pull
     from `search_documents` on `annual_report`/`concall`/`credit_rating` for the company's own
     framing of these, and WebSearch (credible domains only) for the current state of each that
     isn't in local filings. **Political/regulatory specifically**: quantify the disclosed
     dependency (government/PSU revenue share, licence/tariff/subsidy exposure) and track whether
     any governing policy is currently under review (ministry notifications, Budget, SEBI/RBI
     circulars) — treat this as a Threat/Opportunity depending on direction, sourced and
     quantified, never as speculation about undisclosed political connections
     (`investing-principles` point 13 has the full guardrail).

## Output format

Four clearly-labelled quadrants (Strengths / Weaknesses / Opportunities / Threats). Every
Strength bullet states its VRIO read in parenthesis (e.g. "Distribution reach in rural markets
(Valuable, Rare vs peers per `competitive_position`, moderately Inimitable — 5-7yr to replicate,
well Organised — ROCE 24% sustained)"). Every Opportunity/Threat bullet names which of the Five
Forces or PESTEL category it falls under. Every bullet cites its source (tool + figure, doc +
period, or web source + URL). No generic filler ("strong brand") without the data behind it.
Close with a 2-3 line synthesis: which quadrant dominates the current picture, which Strengths
are genuine moats vs temporary edges, and the key uncertainties.

## Rules

- Keep internal vs external strictly separated — a competitor's strength is a Threat (Rivalry
  force) to this company, not one of its Weaknesses.
- A Strength that fails the VRIO Rare or Inimitable test must be labelled a temporary advantage,
  not a moat — don't let a real-but-fleeting edge read as durable.
- Distinguish structural factors (a Porter force, a regulatory barrier) from cyclical or seasonal
  ones (a current commodity price, a one-off demand spike, a routine festive-quarter jump) — a
  cyclical/seasonal tailwind is an Opportunity with an expiry date, say so
  (`investing-principles` point 14 has the cyclical/seasonal/secular classification).
- Cite everything; no unsupported assertions. Web findings clearly marked and separated from
  local data.
- **Timestamp it.** A SWOT — especially the External half — goes stale faster than a financial
  statement: TAM estimates, competitive positioning, and regulatory context all move. State the
  as-of date for every web-sourced external fact and for the local-data cutoff, and note this
  SWOT should be refreshed rather than assumed durable beyond a few quarters (sector-overview's
  own explicit caution, worth repeating here).
