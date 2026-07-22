# Example: forensic audit + growth + SWOT — Deepak Nitrite

**Prompt:** *"comprehensive financial forensics for deepak nitrite — what is its past/
current/future growth? is it good to invest currently? SWOT."*

This exercises the `financial-forensics`, `investing-principles` and `swot-study` skills.
The agent resolves the symbol, pulls 12 years of statements + quarterly XBRL (with
segments), computes the named quality scores from the raw numbers, and lays out the
evidence — **without** issuing a buy/sell call.

> Figures below are as-of the run date and sourced from the local data lake
> (screener/Tijori/NSE XBRL snapshots). Illustrative of tool output, not investment
> advice.

## Forensic scorecard (computed, not looked up)

| Score | Value | Read |
|---|---|---|
| **Altman Z''-Score (EM)** | **9.84** (FY26), 10.55 (FY25) | Deep in the safe zone (>2.6). *A-term (working capital) estimated from Other Assets − Other Liabilities — stated as an estimate.* |
| **Piotroski F-Score** | **3 / 8 computable** | Low — genuine YoY deterioration (ROA↓, CFO<NI, leverage↑, margin↓, turnover↓). Current-ratio test not computable (no current/non-current split) → scored out of 8, not silently assumed. |
| **Sloan accrual ratio** | ≈ **+0.15%** (FY26) | Clean — CFO tracks PAT; corroborates the 1.0x cumulative CFO/PAT strength flag. |
| **Beneish M-Score** | **Not computed** | Four of eight inputs (receivables, COGS, current-assets split, SG&A) aren't exposed by this data lake — the agent refuses to approximate it rather than publish a number built on assumed inputs. |

## Findings (ranked by materiality)

**Notable**
- FCF negative two years running (FY25 −₹494cr, FY26 −₹644cr); net debt swung from
  −₹713cr (net cash) to +₹1,258cr as capex ramped ₹764→1,183cr while CFO fell
  ₹874→538cr. *(Confirmed independently by `financial_health` and `capital_allocation`.)*
- Three straight years of PAT decline: ₹852cr (FY23) → ₹551cr (FY26), −35%; OPM 16%→12%.
- The segment absorbing 55% of FY26 capex (Advanced Intermediates) runs only 2–6%
  segment margin vs Phenolics' 9–20% — the growth bet is in the least-proven segment.
- Valuation rich vs a deteriorating trend: PE 40.6 vs peer median 29.1 and own-10y median
  28.9 (+40% on both).

**Minor**
- Other income spiked to 14% of PBT in FY24 (one-off) vs a 3–8% norm; normalised by FY26.
- Mar-2026 quarter's jump (OPM 11%→18% QoQ) traced to a real Phenolics spread widening +
  Q4 seasonality — checked and resolved as commercial/cyclical, not an accounting anomaly.

**Raw statements checked:** FY2015–FY2026 annual P&L/BS/CF; 13 quarters of quarterly
results; 8 quarters of XBRL with segment revenue/result; shareholding Jun-2023→Mar-2026;
PIT insider disclosures.

## Growth & "is it good to invest currently"

- **Past (FY15–22):** genuine hyper-growth — sales CAGR 17.6%, PAT CAGR 23.7%.
- **Current (FY23–26):** stalled/reversed — sales flat, PAT −35%, ROCE ~halved to 11.5%.
- **Priced-in:** DCF base case implies a return to ~20.8% growth — above what the last
  three years delivered. The bull case (capex diversification + spread recovery) is
  plausible but unproven; the bear case is equally supported by the same data.

**Framing (not a recommendation):** clean governance/earnings quality + a real historical
record, set against three years of deteriorating fundamentals and a 40% valuation
premium. Whether the Mar-2026 quarter is a true inflection is the single fact that would
move this either way — *the buy/sell decision is the user's.*

## SWOT (abbreviated — VRIO-tested strengths, Porter/PESTEL-framed externals)

- **Strengths:** ~42% domestic Phenol/Acetone share; zero promoter pledge + active
  promoter buying; clean earnings quality; long-run compounding record.
- **Weaknesses:** commodity-spread-driven core (margins swing 9–20% QoQ); 3y profit
  decline; thin-margin new segment getting the bulk of capex; negative FCF, rising debt.
- **Opportunities:** ₹3,070cr capex into higher-value chemistry; India specialty-chem
  import-substitution tailwind.
- **Threats:** global phenol/acetone capacity compressing spreads; execution/payback risk
  on the capex; de-rating risk from a 40% premium.
