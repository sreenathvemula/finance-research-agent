# Disclaimer & Data-Use Policy

**Read this before using the code or the data pipeline.**

## 1. Not investment advice

This is a research and software-engineering project. It produces **decision-support
analysis** — scores, rankings, evidence summaries — and deliberately **never issues a
personalised buy / sell / hold instruction or a price target stated as fact.** Nothing
here is investment advice, a research-analyst recommendation under SEBI (Research
Analysts) Regulations, or a solicitation to transact in any security. Do your own
diligence and/or consult a SEBI-registered adviser before investing. The authors accept
no liability for any decision made using this software or its output.

## 2. This repository does NOT redistribute third-party data

The agent analyses data pulled from **screener.in**, **Tijori Finance**, NSE/BSE
filings, company concall transcripts, annual reports and credit-rating rationales. The
Terms of Use of those sources permit **personal, non-commercial use only** and prohibit
redistribution, republication or public display of their content. Company documents
(concalls, annual reports) are additionally the copyright of their respective owners.

Because of that, **the scraped/derived data lake (`data/`) is intentionally excluded
from this repository** (see `.gitignore`). What ships here is *code* — you point it at
your **own** accounts and it builds a **local, personal** copy on your machine.

## 3. You are responsible for how you use the scrapers

- Use **your own** logged-in sessions. The scrapers read your own account cookies
  (`SCREENER_SESSION_ID`, `TIJORI_SESSION_ID`) from a local `.env` you create — no shared
  or bundled credentials exist.
- **Fetch only the few companies you actually intend to analyse.** Do not bulk-scrape the
  entire site. Systematic extraction of a whole third-party database, even "for personal
  use," may breach the source's Terms of Use and is not the intended use of these tools.
- Respect each site's rate limits, `robots.txt`, and Terms of Use. Compliance is **your**
  responsibility, not the author's.
- Do not re-publish, redistribute or sell any data you collect with these tools.

## 4. Accuracy

Data may be stale, incomplete or wrong (the sources are snapshots, and scrapers break).
Structured tools are only as good as the underlying source. Verify anything material
against primary filings (NSE/BSE/SEBI/MCA) before relying on it.

*By using this software you acknowledge you have read and accepted the above.*
