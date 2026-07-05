"""Ethical exclusion screening.

Each category is a values judgment the USER makes, not the agent — this module
only mechanizes categories the user has explicitly confirmed. Categories are
matched against sector/industry/company-name text (case-insensitive substring).

To add a category: add an entry to EXCLUSION_CATEGORIES with keywords + the
reason tied to the user's Ethical Investment framework (scripts/Ethical Investment.txt).
"""
from __future__ import annotations

import pandas as pd

EXCLUSION_CATEGORIES: dict[str, dict] = {
    "tobacco": {
        "keywords": ["tobacco", "cigarette", "cigar", "gutkha", "bidi"],
        "reason": (
            "Product directly and severely harms consumers; addiction burden falls "
            "disproportionately on the poor. Fails the framework's primary test — "
            "does this primarily serve or harm the most vulnerable?"
        ),
    },
    "gambling": {
        "keywords": ["gambling", "casino", "lottery", "betting", "wagering"],
        "reason": (
            "Revenue is structurally dependent on compulsive loss by a small subset "
            "of addicted patrons (the good — profit — flows from the bad, failing "
            "principle 3), and problem gambling skews toward lower-income patrons "
            "(fails the primary vulnerable-harm test)."
        ),
    },
    # Additional categories (alcohol, weapons scope, adult entertainment, etc.) are
    # genuine judgment calls under the user's own framework — add here once the user
    # confirms, following the same shape.
    #
    # NOT included: "predatory_lending". Tried and reverted — keyword matching (payday
    # loan / microfinance / moneylending / pawnbroking) caught ONLY legitimate RBI-
    # regulated microfinance institutions (CreditAccess Grameen, Satin Creditcare,
    # Equitas Small Finance Bank, etc.) serving rural/unbanked populations, zero true
    # predatory lenders. Distinguishing genuine predatory practice from legitimate
    # financial-inclusion lending needs actual conduct data (rates, collection
    # practices, RBI enforcement actions) that isn't in this dataset — sector/business-
    # description text can't tell them apart. Do not re-add without that data source.
}


def _match_columns(df: pd.DataFrame) -> list[str]:
    # "about" (real business description) matters MORE than sector/industry tags:
    # conglomerates are often tagged "Diversified"/"FMCG" even when a excluded
    # product line is their largest business — e.g. ITC's sector tag is "Diversified
    # FMCG" with no "tobacco" substring, but its about-text says "largest cigarette
    # manufacturer". Tag-only matching would silently miss exactly this case.
    return [c for c in ("about", "sector", "industry", "nse_industry",
                        "screener_industry", "company_name") if c in df.columns]


def apply_exclusions(df: pd.DataFrame, categories: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split df into (kept, excluded). excluded gets an 'exclusion_reason' column.

    Unknown category names are ignored (not silently excluded-nothing — caller
    should surface a warning if a requested category isn't in EXCLUSION_CATEGORIES).
    """
    cols = _match_columns(df)
    excluded_mask = pd.Series(False, index=df.index)
    reasons = pd.Series("", index=df.index, dtype=object)

    for cat in categories:
        spec = EXCLUSION_CATEGORIES.get(cat)
        if not spec:
            continue
        cat_mask = pd.Series(False, index=df.index)
        for col in cols:
            text = df[col].astype(str).str.lower()
            for kw in spec["keywords"]:
                cat_mask |= text.str.contains(kw, na=False, regex=False)
        newly = cat_mask & ~excluded_mask
        reasons[newly] = f"[{cat}] {spec['reason']}"
        excluded_mask |= cat_mask

    kept = df[~excluded_mask].copy()
    excluded = df[excluded_mask].copy()
    excluded["exclusion_reason"] = reasons[excluded_mask]
    return kept, excluded


def known_categories() -> list[str]:
    return sorted(EXCLUSION_CATEGORIES)


def unknown_categories(requested: list[str]) -> list[str]:
    return [c for c in requested if c not in EXCLUSION_CATEGORIES]
