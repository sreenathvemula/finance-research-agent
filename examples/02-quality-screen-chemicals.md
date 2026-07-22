# Example: quality screen with a transparent funnel

**Prompt:** *"Screen chemical companies with ROCE > 20, ROE > 15, sales growth > 10% and
debt/equity < 0.4."*

This exercises `screen_stocks`. Note the **funnel** (how many companies survive each
filter) and the **near-miss** reporting (names that failed exactly one criterion by a
small margin) — both are built in, so the screen shows its work instead of just handing
back a list.

> Snapshot figures, illustrative of tool output. Not investment advice.

```
20 companies matched; showing 10 (sorted by roce).
Funnel: universe (post ethics) 3117 → sector~'Chemicals' 204 → roce ≥ 20: 45
        → roe ≥ 15: 41 → sales_growth ≥ 10: 30 → debt_equity ≤ 0.4: 20
Near misses (failed one criterion narrowly): GSPCROP (roce by 5.0%),
        SPLPETRO (roe by 5.3%), PUNJABCHEM (roce by 7.0%), BAYERCROP (growth by 10.0%)
```

| Symbol | Company | PE | ROE | ROCE | D/E | Sales growth |
|---|---|---:|---:|---:|---:|---:|
| ALKYLAMINE | Alkyl Amines Chemicals | 48.2 | 41.4 | 41.3 | 0.16 | 17.4% |
| JUBLCPL | Jubilant Agri & Consumer | 21.0 | 33.2 | 39.9 | 0.11 | 22.7% |
| SOLARINDS | Solar Industries India | 97.8 | 31.5 | 36.8 | 0.24 | 30.5% |
| PIDILITIND | Pidilite Industries | 60.4 | 23.9 | 31.0 | 0.04 | 11.1% |
| SHARDACROP | Sharda Cropchem | 11.5 | 24.2 | 30.4 | 0.00 | 21.9% |
| DHANUKA | Dhanuka Agritech | 16.6 | 22.0 | 28.3 | 0.05 | 15.7% |

The agent would then note the obvious quality-vs-value tension (SOLARINDS/PIDILITIND are
high-quality but richly priced; SHARDACROP/DHANUKA screen cheaper) and could overlay a
Piotroski/Altman quality pass on the survivors before shortlisting — but it stops at
evidence and ranking, leaving the decision to the user.
