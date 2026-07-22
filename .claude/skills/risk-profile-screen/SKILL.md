---
name: risk-profile-screen
description: Capture the user's risk appetite (risk tolerance, market-cap comfort, valuation discipline, time horizon) via a short set of questions, translate it into concrete screen_stocks/screen_consistency thresholds, run the screen, show BOTH the shortlist and what was left out (the funnel + near-misses), then flag portfolio-level concentration risk (sector clustering) across the shortlist and give the arithmetic of drawdown recovery so the strictness of the profile is visible in real terms, not just labels. Use when the user asks about "risk appetite", "risk tolerance", "risk profile", wants a screen personalised to how much risk they're comfortable with, or asks "what got left out" / "what did I miss" on a screen.
---

# Risk-appetite screen

Turn a vague "how much risk am I comfortable with" into concrete, transparent screening
criteria — and show what those criteria actually cost (who got excluded, and by how much),
not just the final list. This is still pure screening: evidence and ranking, never advice.

## Procedure

1. **Elicit the profile.** Ask up to 4 questions in ONE `AskUserQuestion` call (its limit is
   4 questions/call). If the user has already stated preferences in plain text, don't re-ask —
   map what they said directly. If they decline/dismiss the question UI, fall back to a
   sensible default (Moderate / Large+Mid cap / Reasonable growth premium / Long-term) and say
   so plainly, rather than blocking.

   - **Risk tolerance**: Conservative / Moderate (recommended default) / Aggressive
   - **Market-cap comfort**: Large-cap only / Large + Mid cap (recommended default) / All caps
     including small & micro
   - **Valuation discipline**: Strict margin of safety / Reasonable growth premium OK
     (recommended default) / Growth-at-any-reasonable-price
   - **Time horizon**: Long-term compounding, 5-10y (recommended default) / Medium-term, 1-3y /
     Short-term / momentum-aware

2. **Translate to concrete thresholds** — combine independently, don't cross-multiply into a
   giant table:

   | Risk tolerance | max debt_equity | max annualized_volatility_pct | max_drawdown_pct floor | min avg_daily_value_cr |
   |---|---|---|---|---|
   | Conservative | 0.3 | 25 | -40 | 5 |
   | Moderate | 0.75 | 40 | -55 | 2 |
   | Aggressive | 2.0 | 70 | (none) | 0.5 |

   **Make the drawdown floor concrete, not abstract — show the recovery arithmetic.** A drawdown
   is not symmetric: recovering from a **-40%** drawdown needs a **+67%** gain to get back to par;
   from **-55%**, a **+122%** gain; from **-70%**, a **+233%** gain (`recovery% = drawdown% /
   (1-drawdown%)`). State this once, next to whichever `max_drawdown_pct` floor the profile lands
   on, so "I'm fine with Aggressive" is chosen with the real math in view, not just a label.

   | Market-cap comfort | min market_cap_cr |
   |---|---|
   | Large-cap only | 20000 |
   | Large + Mid cap | 5000 |
   | All caps | 200 |

   | Valuation discipline | max pe | max pb |
   |---|---|---|
   | Strict margin of safety | 20 | 3 |
   | Reasonable growth premium | 35 | — |
   | Growth-at-any-reasonable-price | — | — |

   Time horizon doesn't add a numeric filter — it picks the SCREENING TOOL:
   - Long-term -> back the screen with `screen_consistency` (ROCE-or-ROE and, if the user wants
     growth too, sales_yoy_growth_pct, both `n_years=10`, `max_violations=0`) as the quality
     backbone, then apply the risk/cap/valuation thresholds as additional filters on the result.
   - Medium-term -> `screen_stocks` directly with the combined min/max thresholds.
   - Short-term/momentum -> `screen_stocks` with the same thresholds PLUS
     `above_dma50=True, above_dma200=True, macd_bullish=True`; explicitly note the behavioural
     caution from `investing-principles` (momentum chasing is a named bias, not a strategy).

3. **Run it and report both sides.** Call `screen_stocks` (or the consistency+filter
   combination for long-term) with `near_miss_tolerance_pct` left at its default (15%) so the
   near-miss list populates. Present:
   - The shortlist, ranked, with the criteria stated plainly.
   - The **funnel** — how many companies each single criterion actually dropped, in order. If
     one criterion is doing almost all the work (e.g. volatility cap alone drops 80%), say so.
   - The **near misses** — companies excluded by a small margin on exactly one criterion. This
     is literally "the companies you left out" — show it, don't just report a final count.

4. **Offer to relax.** If the shortlist is very small or the near-misses look attractive, offer
   to loosen the tightest criterion (usually visible from the funnel) and re-run, rather than
   silently living with an overly strict profile.

5. **Check portfolio-level concentration on the shortlist itself — single-stock risk metrics
   don't capture this.** A shortlist where 12 of 20 names are the same sector (or all
   correlated to the same commodity/rate cycle) is riskier as a *portfolio* than the individual
   volatility numbers suggest, even if every name individually cleared the risk thresholds.
   Group the shortlist by `sector`/`nse_industry` and report the distribution (e.g. "8/20 IT
   services, 5/20 banks — this shortlist is not sector-diversified regardless of the per-stock
   risk filters passing"). Flag it; don't silently let it pass because each row individually
   cleared the bar. Sector labels are a proxy for correlation, not the thing itself — if the user
   gives you actual holdings/weights for an existing portfolio (not just a fresh screen), call
   the `portfolio_risk` tool (see `investing-principles`'s Portfolio risk management section) to
   get the real correlation matrix, portfolio volatility, Sharpe/Sortino, historical VaR/CVaR and
   effective-position-count from actual price history, rather than stopping at the sector count.

## Rules

- This produces a shortlist and a transparent trade-off — never a recommendation. Same framing
  as `screen-to-shortlist`: "here's how they rank on your stated risk tolerance — you decide."
- Don't fabricate a "risk score" number; every threshold traces to a real column (debt_equity,
  annualized_volatility_pct, max_drawdown_pct, avg_daily_value_cr, market_cap_cr, pe/pb) the
  user can see and question.
- Apply ethical exclusions (per `screen-to-shortlist`'s rules) in the same `screen_stocks` call
  if the user has confirmed any — risk profiling and ethics screening compose, they don't replace
  each other.
- **On position sizing:** if the user asks how much to put into a name, you can explain the
  *concepts* (inverse-volatility weighting, a hard per-position cap as a simpler alternative,
  why the Kelly criterion is theoretically the "optimal" bet size but requires a reliable edge/
  win-probability estimate that equity investing essentially never provides cleanly — so a
  literal Kelly calculation on a stock pick is more precision than the inputs support) but do not
  compute or state a personalised allocation number for the user's actual portfolio — that
  crosses from decision-support into personalised advice.
