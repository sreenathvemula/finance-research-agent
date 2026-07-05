# Example: valuation deep-dive — "Is Asian Paints still worth 57× earnings?"

**Tools used:** `valuation_summary` (multiples + relative + 3-scenario DCF) cross-checked against
`financial_health` and the Graham Number. Every DCF assumption is surfaced; the fair value is a
**range**, never a single "target."

> Snapshots from a local data lake; illustrative, not investment advice.

## Where it trades (`valuation_summary`, ASIANPAINT)

| Multiple | Asian Paints | Anchor |
|---|---:|---|
| P/E | **57.4** | peer median 33.5 · own 10y median **57.4** |
| P/B | 11.9 | — |
| EV/EBITDA | 32.7 | — |
| ROE / ROCE | 21.8% / 26.3% | still high-quality |
| Debt/equity | 0.18 | conservative |

So it's **+71% vs peers but bang on its own 10-year median** — the market has always paid up for
this franchise. The question isn't "is the multiple high" (it always is); it's "does the growth
still justify it."

## The DCF says the growth no longer does

![Asian Paints — DCF fair value vs price](../docs/img/asianpaint_dcf.png)

Assumptions surfaced: cost of equity 12.34%, terminal growth 5%, 10-yr G-sec 6.84%.

| Scenario | Assumed growth | Fair value | vs ₹2,655 |
|---|---:|---:|---:|
| Bear | 5.8% | ₹685 | −74% |
| Base | 10.8% | ₹983 | −63% |
| Bull | 15.8% | ₹1,412 | −47% |

- **All three scenarios land below the market price.** To justify ₹2,655 the DCF has to assume
  **~24.6% growth for a decade** — versus the **~10% EPS CAGR and ~9% sales CAGR actually
  delivered** over the last decade (`hist_eps_cagr` / `hist_sales_cagr`).
- **Graham Number cross-check:** √(22.5 × EPS × BVPS) sits far below the price too — expected for a
  compounder, but it quantifies how far outside classic value territory this is.

## The thing the multiple is ignoring

Asian Paints built its moat on distribution depth and pricing power. The valuation still prices a
near-monopoly — but a well-capitalised new entrant attacking exactly that distribution advantage
is the kind of structural change a backward-looking multiple doesn't see. *(The agent would take
this to `search_documents` on recent concalls + WebSearch on credible domains for the competitive
latest; flagged here as the open question, not asserted as fact.)*

## The read

Genuinely high-quality business (21.8% ROE, low debt, cash-generative) — but the price bakes in a
growth rate **~2.5× what it has delivered**, with all DCF scenarios below the current quote and a
new competitive threat the multiple isn't discounting. That's the definition of *priced for
perfection*. Whether that's still worth owning is a margin-of-safety call — **yours, not the
agent's.**
