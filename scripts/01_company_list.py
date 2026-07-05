"""
NSE + BSE Main-Board Company Master List
=========================================
Downloads all companies listed on NSE main board and BSE main board.
SME platforms (NSE Emerge, BSE SME) are explicitly excluded.

Sources:
  NSE : archives.nseindia.com/content/equities/EQUITY_L.csv
        Series filter: EQ, BE, BT  (excludes SM = NSE Emerge)
  BSE : api.bseindia.com  (ListofScripData endpoint, all active equities)
        Group filter: excludes X / XC / XD / XT  (BSE SME groups)

Output:
  data/structured/company_master.json   ← primary output (all fields)
  data/structured/nse_equity_list.csv   ← NSE-only, for backward compat with other scripts
  data/structured/bse_scrip_master.csv  ← BSE-only

Usage:
  python 01_company_list.py
"""

import json, time, logging
from io import StringIO
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

OUT = Path(__file__).parent.parent / "data" / "structured"
OUT.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0",
    "Accept": "*/*",
}

# BSE SME groups to exclude
BSE_SME_GROUPS = {"X", "XC", "XD", "XT", "XI"}

# NSE SME series to exclude
NSE_SME_SERIES = {"SM", "MF", "GC", "IL", "IV", "IS"}

# NSE main-board series to keep
NSE_KEEP_SERIES = {"EQ", "BE", "BT", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8"}


# ── NSE ───────────────────────────────────────────────────────────────────────

def fetch_nse() -> pd.DataFrame:
    """
    Downloads NSE equity master list.
    Filters to main-board series (EQ, BE, BT) — excludes SM (NSE Emerge / SME).
    """
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    log.info(f"Fetching NSE equity list: {url}")

    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df.columns = df.columns.str.strip()

    before = len(df)
    if "SERIES" in df.columns:
        sme_rows = df[df["SERIES"].isin(NSE_SME_SERIES)]
        if not sme_rows.empty:
            log.info(f"  NSE SME (Emerge) excluded: {len(sme_rows)} rows "
                     f"(series: {sme_rows['SERIES'].unique().tolist()})")
        df = df[~df["SERIES"].isin(NSE_SME_SERIES)].copy()

    log.info(f"  NSE main board: {len(df)} companies (from {before} total rows)")

    # Normalise column names
    col_map = {
        "SYMBOL":          "nse_symbol",
        "NAME OF COMPANY": "company_name",
        "SERIES":          "nse_series",
        "DATE OF LISTING": "nse_listing_date",
        "PAID UP VALUE":   "face_value",
        "MARKET LOT":      "market_lot",
        "ISIN NUMBER":     "isin",
        "FACE VALUE":      "face_value",
    }
    df = df.rename(columns={c: col_map[c] for c in df.columns if c in col_map})

    # Keep only useful columns
    keep = [c for c in ["nse_symbol", "company_name", "isin", "nse_series",
                         "nse_listing_date", "face_value"] if c in df.columns]
    df = df[keep].drop_duplicates(subset=["nse_symbol"])
    df["exchange"] = "NSE"

    out = OUT / "nse_equity_list.csv"
    df.to_csv(out, index=False)
    log.info(f"  Saved NSE list → {out}")
    return df


# ── BSE ───────────────────────────────────────────────────────────────────────

def fetch_bse_api() -> pd.DataFrame | None:
    """
    BSE API endpoint — returns all active equity scrips including group info.
    Group X/XC/XD/XT/XI = BSE SME platform → excluded.
    """
    url = ("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
           "?Group=&Scode=&scripname=&industry=&segment=Equity&status=Active")
    log.info(f"Fetching BSE scrip list via API: {url}")
    try:
        r = requests.get(url, headers={**HEADERS, "Referer": "https://www.bseindia.com/"}, timeout=30)
        r.raise_for_status()
        raw = r.json()
        table = raw if isinstance(raw, list) else raw.get("Table", raw.get("data", []))
        if not table:
            log.warning("BSE API returned empty data")
            return None
        df = pd.DataFrame(table)
        return df
    except Exception as e:
        log.warning(f"BSE API failed: {e}")
        return None


def fetch_bse_scrip_csv() -> pd.DataFrame | None:
    """
    Fallback: BSE provides a downloadable CSV from their 'List of Scrips' page.
    Tries two known URL patterns.
    """
    urls = [
        "https://www.bseindia.com/corporates/List_Scrips.aspx",
        "https://api.bseindia.com/BseIndiaAPI/api/ddlbymktcap/w?listtype=0",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers={**HEADERS, "Referer": "https://www.bseindia.com/"}, timeout=20)
            if r.status_code == 200:
                if "text/csv" in r.headers.get("content-type", "") or r.text.startswith("Security Code"):
                    df = pd.read_csv(StringIO(r.text))
                    return df
        except Exception as e:
            log.debug(f"BSE CSV fallback {url}: {e}")
    return None


def fetch_bse() -> pd.DataFrame:
    """
    Downloads BSE scrip master, excludes SME groups.
    Returns normalised DataFrame.
    """
    df = fetch_bse_api()
    if df is None:
        df = fetch_bse_scrip_csv()
    if df is None:
        log.warning("Could not download BSE list — skipping BSE")
        return pd.DataFrame()

    log.info(f"  BSE raw rows: {len(df)} | Columns: {df.columns.tolist()[:8]}")

    # Find the group column (case-insensitive)
    group_col = next((c for c in df.columns if "group" in c.lower()), None)
    if group_col:
        before = len(df)
        sme_rows = df[df[group_col].isin(BSE_SME_GROUPS)]
        log.info(f"  BSE SME excluded: {len(sme_rows)} scrips "
                 f"(groups: {sorted(sme_rows[group_col].unique().tolist())})")
        df = df[~df[group_col].isin(BSE_SME_GROUPS)].copy()
        log.info(f"  BSE main board: {len(df)} companies (from {before} total)")
    else:
        log.warning("  No 'Group' column found in BSE data — cannot filter SME")

    # Normalise column names — case-insensitive, handles BSE API variants
    # Actual columns seen: SCRIP_CD, Scrip_Name, Status, GROUP, FACE_VALUE, ISIN_NUMBER, INDUSTRY, scrip_id
    def _find_col(df, *keywords):
        """Return first column whose lowercase name contains any keyword."""
        for kw in keywords:
            for c in df.columns:
                if kw in c.lower():
                    return c
        return None

    rename = {}
    c = _find_col(df, "scrip_cd", "scripcode", "scrip_code", "securitycode", "security_code")
    if c: rename[c] = "bse_code"
    c = _find_col(df, "scrip_name", "scripname", "securityname", "security_name", "company_name")
    if c: rename[c] = "company_name"
    c = _find_col(df, "isin")          # matches ISIN_NUMBER, ISIN_CODE, ISIN, isin
    if c: rename[c] = "isin"
    c = _find_col(df, "group")         # matches GROUP, Group, bse_group
    if c: rename[c] = "bse_group"
    c = _find_col(df, "status")
    if c: rename[c] = "bse_status"
    c = _find_col(df, "industry")
    if c: rename[c] = "industry"
    c = _find_col(df, "datelisted", "datescrip", "listingdate")
    if c: rename[c] = "bse_listing_date"
    c = _find_col(df, "nse_symbol", "nsesymbol", "nse_scrip")
    if c: rename[c] = "nse_symbol"

    log.info(f"  BSE column rename map: {rename}")
    df = df.rename(columns=rename)

    keep = [c for c in ["bse_code", "company_name", "isin", "bse_group",
                         "bse_status", "bse_listing_date", "nse_symbol", "industry"]
            if c in df.columns]
    df = df[keep].drop_duplicates(subset=["bse_code"])
    df["exchange"] = "BSE"

    out = OUT / "bse_scrip_master.csv"
    df.to_csv(out, index=False)
    log.info(f"  Saved BSE list → {out}")
    return df


# ── Merge ─────────────────────────────────────────────────────────────────────

def merge_nse_bse(nse: pd.DataFrame, bse: pd.DataFrame) -> pd.DataFrame:
    """
    Merge NSE and BSE lists into one master using ISIN as the key.

    Companies listed on both exchanges → one row with nse_symbol + bse_code.
    NSE-only companies → nse_symbol, bse_code = None.
    BSE-only companies → bse_code, nse_symbol = None.
    """
    if nse.empty and bse.empty:
        return pd.DataFrame()

    if nse.empty:
        master = bse.copy()
        master["listed_on"] = "BSE"
        return master

    if bse.empty:
        master = nse.copy()
        master["listed_on"] = "NSE"
        return master

    # Both available — outer join on ISIN
    nse2 = nse.copy()
    bse2 = bse.copy()

    # Harmonise company_name column
    if "company_name" not in nse2.columns:
        nse2["company_name"] = ""
    if "company_name" not in bse2.columns:
        bse2["company_name"] = ""

    # Merge on ISIN
    master = pd.merge(
        nse2, bse2,
        on="isin", how="outer",
        suffixes=("_nse", "_bse")
    )

    # Consolidate company_name
    if "company_name_nse" in master.columns and "company_name_bse" in master.columns:
        master["company_name"] = master["company_name_nse"].fillna(master["company_name_bse"])
        master.drop(columns=["company_name_nse", "company_name_bse"], inplace=True)
    elif "company_name_nse" in master.columns:
        master.rename(columns={"company_name_nse": "company_name"}, inplace=True)
    elif "company_name_bse" in master.columns:
        master.rename(columns={"company_name_bse": "company_name"}, inplace=True)

    # Consolidate exchange flags from merge suffixes
    for col in ["exchange_nse", "exchange_bse"]:
        if col in master.columns:
            master.drop(columns=[col], inplace=True)

    # listed_on flag
    has_nse = master["nse_symbol"].notna() if "nse_symbol" in master.columns else pd.Series(False, index=master.index)
    has_bse = master["bse_code"].notna()   if "bse_code"  in master.columns else pd.Series(False, index=master.index)
    master["listed_on"] = "NSE+BSE"
    master.loc[has_nse & ~has_bse, "listed_on"] = "NSE"
    master.loc[~has_nse & has_bse, "listed_on"] = "BSE"

    log.info(f"  Both (NSE+BSE): {(master['listed_on']=='NSE+BSE').sum()}")
    log.info(f"  NSE only:       {(master['listed_on']=='NSE').sum()}")
    log.info(f"  BSE only:       {(master['listed_on']=='BSE').sum()}")
    log.info(f"  Total master:   {len(master)}")

    master = master.reset_index(drop=True)
    return master


def save_master(master: pd.DataFrame):
    """Save master list as JSON (primary) and also as CSV for backward compat."""
    if master.empty:
        log.error("Master list is empty — nothing to save")
        return

    # Drop any duplicate columns created by merge (e.g. exchange_nse / exchange_bse / face_value dupes)
    master = master.loc[:, ~master.columns.duplicated()].copy()

    # JSON output (primary)
    records = master.where(master.notna(), other=None).to_dict(orient="records")
    json_path = OUT / "company_master.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    log.info(f"Saved company master JSON → {json_path} ({len(records)} companies)")

    # CSV for backward compat (other scripts use nse_equity_list.csv)
    # Already saved per-exchange above; also save combined CSV
    csv_path = OUT / "company_master.csv"
    master.to_csv(csv_path, index=False)
    log.info(f"Saved company master CSV  → {csv_path}")


def print_summary(nse: pd.DataFrame, bse: pd.DataFrame, master: pd.DataFrame):
    print("\n" + "=" * 60)
    print("  COMPANY LIST SUMMARY")
    print("=" * 60)

    if not nse.empty:
        series_counts = nse["nse_series"].value_counts() if "nse_series" in nse.columns else None
        print(f"\nNSE Main Board : {len(nse):,} companies")
        if series_counts is not None:
            print(f"  Series breakdown: {dict(series_counts.head(5))}")

    if not bse.empty:
        group_counts = bse["bse_group"].value_counts() if "bse_group" in bse.columns else None
        print(f"\nBSE Main Board : {len(bse):,} companies")
        if group_counts is not None:
            print(f"  Top groups: {dict(group_counts.head(10))}")

    if not master.empty:
        print(f"\nMerged Master  : {len(master):,} unique companies")
        if "listed_on" in master.columns:
            for label, count in master["listed_on"].value_counts().items():
                print(f"  {label:10s}: {count:,}")

        print("\nSample (first 10):")
        show_cols = [c for c in ["company_name", "nse_symbol", "bse_code",
                                  "isin", "listed_on", "bse_group"] if c in master.columns]
        print(master[show_cols].head(10).to_string(index=False))

    print()


if __name__ == "__main__":
    nse    = fetch_nse()
    time.sleep(1)
    bse    = fetch_bse()
    master = merge_nse_bse(nse, bse)
    save_master(master)
    print_summary(nse, bse, master)
