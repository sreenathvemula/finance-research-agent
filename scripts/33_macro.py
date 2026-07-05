#!/usr/bin/env python3
"""
33_macro.py — Thin macro context layer (keyless sources).

FRED `fredgraph.csv` (no API key) for monthly India series, World Bank API (no key)
for annual. Gives the cheap macro backdrop that de-naive-ifies answers: inflation,
industrial production, rates, FX, GDP growth.

Outputs (data/reference/macro/):
  cpi_yoy.csv, iip.csv, gsec10y.csv, rate3m.csv, policy_rate.csv, usdinr.csv
  macro_monthly.csv   aligned monthly panel
  macro_annual.csv    GDP growth + CPI inflation (World Bank)
Usage: python 33_macro.py
"""
import io, logging, time
from pathlib import Path

import pandas as pd
from curl_cffi import requests as cffi

ROOT = Path(__file__).parent.parent
REF  = ROOT / "data" / "reference" / "macro"
REF.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("macro")

FRED = {
    "cpi_index":   "INDCPIALLMINMEI",   # CPI index, monthly -> compute YoY
    "iip":         "INDPROINDMISMEI",   # industrial production index, monthly
    "gsec10y":     "INDIRLTLT01STM",    # 10Y govt bond yield, monthly %
    "rate3m":      "INDIR3TIB01STM",    # 3M interbank rate, monthly %
    "policy_rate": "INTDSRINM193N",     # central bank discount/policy rate
}


def usdinr_yf():
    """FRED DEXINUS is flaky from here; yfinance INR=X is reliable. Month-end INR/USD."""
    try:
        import yfinance as yf
        h = yf.Ticker("INR=X").history(period="max", auto_adjust=False)
        if len(h):
            df = h.reset_index()[["Date", "Close"]].rename(columns={"Date": "date", "Close": "usdinr"})
            df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
            return df.set_index("date").resample("ME").last().reset_index()
    except Exception:
        pass
    return pd.DataFrame(columns=["date", "usdinr"])


def fred(s, sid):
    for _ in range(3):
        try:
            r = s.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=30)
            if r.status_code == 200:
                df = pd.read_csv(io.StringIO(r.text))
                df.columns = ["date", "value"]
                df["value"] = pd.to_numeric(df["value"], errors="coerce")
                return df.dropna()
        except Exception:
            time.sleep(3)
    return pd.DataFrame(columns=["date", "value"])


def main():
    s = cffi.Session(impersonate="chrome")
    series = {}
    for label, sid in FRED.items():
        df = fred(s, sid)
        series[label] = df
        log.info(f"  FRED {label:12} {sid:16} rows={len(df)}  last={df.iloc[-1].to_dict() if len(df) else None}")

    # CPI YoY inflation from the CPI index (monthly)
    cpi = series["cpi_index"].copy()
    if len(cpi):
        cpi["date"] = pd.to_datetime(cpi["date"])
        cpi = cpi.sort_values("date")
        cpi["cpi_yoy"] = cpi["value"].pct_change(12) * 100
        cpi[["date", "cpi_yoy"]].dropna().to_csv(REF / "cpi_yoy.csv", index=False)

    # save individual series + build a monthly panel
    panel = None
    for label in ("iip", "gsec10y", "rate3m", "policy_rate"):
        df = series[label]
        if not len(df):
            continue
        df = df.rename(columns={"value": label})
        df["date"] = pd.to_datetime(df["date"])
        df.to_csv(REF / f"{label}.csv", index=False)
        panel = df if panel is None else panel.merge(df, on="date", how="outer")
    fx = usdinr_yf()                 # USDINR from yfinance (month-end)
    if len(fx):
        fx.to_csv(REF / "usdinr.csv", index=False)
        log.info(f"  yfinance usdinr rows={len(fx)}  last={fx.iloc[-1].to_dict()}")
        panel = fx if panel is None else panel.merge(fx, on="date", how="outer")
    if len(cpi):
        panel = cpi[["date", "cpi_yoy"]].merge(panel, on="date", how="outer") if panel is not None else cpi[["date", "cpi_yoy"]]
    if panel is not None:
        panel.sort_values("date").to_csv(REF / "macro_monthly.csv", index=False)
        log.info(f"macro_monthly.csv: {len(panel)} months, cols={[c for c in panel.columns if c!='date']}")

    # World Bank annual (keyless)
    wb = {"gdp_growth_pct": "NY.GDP.MKTP.KD.ZG", "cpi_inflation_pct": "FP.CPI.TOTL.ZG",
          "real_interest_pct": "FR.INR.RINR", "current_account_gdp_pct": "BN.CAB.XOKA.GD.ZS"}
    arows = {}
    for label, code in wb.items():
        try:
            r = s.get(f"https://api.worldbank.org/v2/country/IND/indicator/{code}?format=json&per_page=70", timeout=30)
            for o in (r.json()[1] or []):
                if o["value"] is not None:
                    arows.setdefault(o["date"], {})[label] = round(o["value"], 2)
        except Exception as e:
            log.warning(f"  WB {label}: {e}")
    if arows:
        adf = pd.DataFrame([{"year": y, **v} for y, v in sorted(arows.items())])
        adf.to_csv(REF / "macro_annual.csv", index=False)
        log.info(f"macro_annual.csv: {len(adf)} years, cols={[c for c in adf.columns if c!='year']}")
    log.info(f"-> {REF}")


if __name__ == "__main__":
    main()
