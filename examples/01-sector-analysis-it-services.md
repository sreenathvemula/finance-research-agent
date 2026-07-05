# Example: sector analysis — "Is the Indian IT sector a falling knife or a buying opportunity?"

**Tools used:** `sector_analysis` → `screen_stocks` (sector-scoped) → `financial_health` /
`technicals_momentum` on the leaders. One sector call, then depth on the names that matter —
never a loop over 91 companies.

> Snapshots from a local data lake; illustrative, not investment advice.

## The sector at a glance (`sector_analysis`, "IT - Software")

- **91 companies**, ₹25.4 lakh crore total market cap, **median P/E 23.0**.
- Top 6 by market cap: TCS, Infosys, HCLTech, Wipro, Tech Mahindra, LTIMindtree.

## The tension: elite quality, brutal price action (`screen_stocks`, sector-scoped)

| Company | P/E | ROE | ROCE | Div yield | 1Y return | Off 52w high |
|---|---:|---:|---:|---:|---:|---:|
| TCS | **14.9** | 51.8% | **63.0%** | 3.0% | **−36.3%** | −37% |
| Infosys | 16.0 | 31.9% | 40.0% | 4.0% | −22.9% | −29% |
| HCLTech | 18.0 | 24.0% | 30.6% | 4.7% | −29.2% | −34% |
| Wipro | 14.5 | 15.5% | 17.9% | 6.1% | −19.9% | −27% |
| Tech Mahindra | 29.5 | 17.6% | 23.1% | 3.4% | −4.2% | −16% |
| LTIMindtree | 21.8 | 23.1% | 29.6% | 1.9% | −20.6% | −37% |

![IT majors — quality vs valuation](../docs/img/it_sector_quality_value.png)

## The read

- **Every major is below its 200-DMA and 20–37% off its 52-week high** — this is a
  sector-wide de-rating, not a single-company problem. The market is pricing a structural
  growth slowdown (discretionary-spend freeze + the "will AI cannibalise the services model?"
  question).
- **The quality hasn't gone anywhere.** TCS still earns 63% ROCE / 52% ROE and yields 3%; it now
  trades at **14.9× earnings — near the bottom of its historical range**. On the quality-vs-value
  map it sits alone in the top-left (cheap *and* best-in-class).
- **Tech Mahindra is the outlier the other way** — the highest P/E (29.5) for the lowest returns
  of the mega-caps, because it's an earnings-*recovery* bet, not a quality-at-a-discount one.
- **Dividend yields of 3–6%** put a floor under the thesis that isn't there for most of the market.

## What the agent will *not* do

It won't tell you to buy TCS. It lays out that the sector is high-quality, out of favour, and
cheap versus its own history — and flags the open question that decides everything: **is the
AI-driven revenue risk cyclical (buy the fear) or structural (value trap)?** That judgement, and
your required margin of safety, are yours.
