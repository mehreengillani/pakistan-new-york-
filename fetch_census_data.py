#!/usr/bin/env python3
"""
Fetch data for the Policy East post:
  1) US imports from Pakistan by HS product and customs district (Census trade API)
  2) Pakistani / Pakistan-born population in NYC (ACS 5-year API)

Setup
-----
    pip install requests pandas
    export CENSUS_API_KEY="your_key_here"      # Windows PowerShell: $env:CENSUS_API_KEY="..."

Usage
-----
    python fetch_census_data.py trade --start 2010 --end 2025
    python fetch_census_data.py acs --year 2024
    python fetch_census_data.py list-vars trade      # show variables the trade endpoint offers
    python fetch_census_data.py list-vars acs        # show B05006 / B02015 variable labels

Outputs go to ./data/ as CSV files.

Notes
-----
* Variable names and district names are checked against the API's own metadata at
  run time (variables.json). If the Census API renames something, the script tells
  you which field is missing instead of silently failing.
* Trade values are annual totals taken from the December year-to-date (_YR) fields.
  For the current, incomplete year the same call gives year-to-date up to the latest
  published month.
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

BASE = "https://api.census.gov/data"
TRADE_URL = f"{BASE}/timeseries/intltrade/imports/hs"
OUT_DIR = Path("data")

# Pakistan's Census country code is looked up by name at run time (see
# lookup_country_code). Pass --country-code to override it.
PAKISTAN_CODE = None

# NYC counties (state FIPS 36): Bronx, Kings, New York, Queens, Richmond
NYC_COUNTIES = "005,047,061,081,085"

# Rough grouping of HS 2-digit chapters into categories that read well in a post.
HS2_CATEGORY = {
    "61": "Apparel (knit)",
    "62": "Apparel (woven)",
    "63": "Home textiles & made-ups",
    "52": "Cotton, yarn & fabric",
    "41": "Leather & hides",
    "42": "Leather goods",
    "10": "Rice & cereals",
    "95": "Sports goods & toys",
    "90": "Medical & surgical instruments",
}

# Census API returns these as "null" annotations in some ACS cells.
ACS_NULL_CODES = {-666666666, -555555555, -333333333, -222222222, -999999999}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def get_key():
    key = os.environ.get("CENSUS_API_KEY", "").strip().strip("'\"")
    if not key:
        sys.exit("Set your key first:  export CENSUS_API_KEY='your_key_here'")
    return key


def api_get(url, params, retries=3):
    """GET with simple retry. Returns parsed JSON or raises with the API's message."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, timeout=60)
        except requests.RequestException as exc:
            last = str(exc)
            time.sleep(2 * attempt)
            continue
        if r.status_code == 200:
            if not r.text.strip():
                return []
            try:
                return r.json()
            except ValueError:
                # The Census API answers an unusable key (not yet activated, mistyped,
                # extra quotes/spaces) with a 200 and a short text/HTML message.
                shown = dict(params)
                if "key" in shown:
                    shown["key"] = "***"
                raise RuntimeError(
                    "The API replied with something that is not JSON.\n"
                    f"Request params: {shown}\n"
                    f"Response starts with: {r.text[:300]!r}\n"
                    "Most common cause: the API key is invalid or not activated yet. "
                    "Click the activation link in the email from the Census Bureau, "
                    "then re-run."
                )
        if r.status_code == 204:
            return []
        # 4xx responses carry a plain-text explanation; do not retry those.
        if 400 <= r.status_code < 500:
            safe_url = re.sub(r"key=[^&]+", "key=***", r.url)
            raise RuntimeError(f"HTTP {r.status_code} from {safe_url}\n{r.text[:500]}")
        last = f"HTTP {r.status_code}"
        time.sleep(2 * attempt)
    raise RuntimeError(f"Request failed after {retries} tries: {last}")


def to_frame(rows):
    """Census returns [header, row, row, ...]."""
    if not rows or len(rows) < 2:
        return pd.DataFrame()
    return pd.DataFrame(rows[1:], columns=rows[0])


def fetch_variable_names(url):
    meta = api_get(f"{url}/variables.json", {})
    return meta.get("variables", {})


# --------------------------------------------------------------------------- #
# Part 1: Trade
# --------------------------------------------------------------------------- #
def lookup_country_code(level_param, level, key, year, month, name="PAKISTAN"):
    """Find a country's Census code by name using one small query (apparel, HS 61)."""
    for y in (year, year - 1):
        params = {
            "get": "CTY_CODE,CTY_NAME",
            level_param: level,
            "I_COMMODITY": "61",
            "YEAR": str(y),
            "MONTH": month,
            "key": key,
        }
        try:
            df = to_frame(api_get(TRADE_URL, params))
        except RuntimeError as exc:
            print(f"[country lookup {y}] {exc}", file=sys.stderr)
            continue
        if df.empty:
            continue
        match = df[df["CTY_NAME"].str.upper().str.strip() == name]
        if match.empty:
            match = df[df["CTY_NAME"].str.upper().str.contains(name, na=False)]
        if not match.empty:
            code = match["CTY_CODE"].iloc[0]
            print(f"Country code for {name}: {code} ({match['CTY_NAME'].iloc[0]})")
            return code
    sys.exit(f"Could not find a country code for {name}. "
             "Look it up and pass --country-code CODE.")


def fetch_trade(args):
    key = get_key()
    wanted = [
        "I_COMMODITY", "I_COMMODITY_LDESC", "CTY_CODE", "CTY_NAME",
        "DISTRICT", "DIST_NAME", "GEN_VAL_YR", "CON_VAL_YR",
    ]

    available = fetch_variable_names(TRADE_URL)
    missing = [v for v in wanted if v not in available]
    if missing:
        sys.exit(
            f"These fields were not found on the trade endpoint: {missing}\n"
            "Run:  python fetch_census_data.py list-vars trade\n"
            "and edit the 'wanted' list in fetch_trade()."
        )

    # The name of the "commodity level" filter is read from the API's own metadata
    # (expected: COMM_LVL) so a wrong guess cannot break the call.
    level_param = args.level_param
    if not level_param:
        if "COMM_LVL" in available:
            level_param = "COMM_LVL"
        else:
            candidates = [n for n in available if "LVL" in n or "LEVEL" in n]
            level_param = candidates[0] if candidates else None
    if not level_param:
        sys.exit(
            "Could not find the commodity-level filter on the trade endpoint.\n"
            "Run:  python fetch_census_data.py list-vars trade\n"
            "and pass the right name with  --level-param NAME"
        )
    print(f"Using '{level_param}={args.level}' to select the HS level.")

    country_code = args.country_code or lookup_country_code(
        level_param, args.level, key, args.end, args.month
    )

    frames = []
    for year in range(args.start, args.end + 1):
        params = {
            "get": ",".join(wanted),
            level_param: args.level,            # HS2, HS4, HS6 or HS10
            "CTY_CODE": country_code,
            "YEAR": str(year),
            "MONTH": args.month,                # 12 = full year; use latest month for current year
            "key": key,
        }
        try:
            df = to_frame(api_get(TRADE_URL, params))
        except RuntimeError as exc:
            print(f"[{year}] skipped: {exc}", file=sys.stderr)
            continue
        if df.empty:
            print(f"[{year}] no rows returned")
            continue
        df["year"] = year
        frames.append(df)
        print(f"[{year}] {len(df):,} rows")

    if not frames:
        sys.exit("No trade data retrieved. Check the key, the years and --month.")

    data = pd.concat(frames, ignore_index=True)

    # Sanity check that the country code really is Pakistan.
    names = data["CTY_NAME"].str.upper().unique()
    if not any("PAKISTAN" in n for n in names):
        sys.exit(f"CTY_CODE {country_code} returned {names[:3]}, not Pakistan. "
                 "Look up the right code and pass --country-code.")

    for col in ("GEN_VAL_YR", "CON_VAL_YR"):
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.rename(columns={
        "I_COMMODITY": "hs_code",
        "I_COMMODITY_LDESC": "hs_description",
        "CTY_NAME": "country",
        "DIST_NAME": "district_name",
        "DISTRICT": "district_code",
        "GEN_VAL_YR": "general_imports_usd",
        "CON_VAL_YR": "consumption_imports_usd",
    })
    data["hs2"] = data["hs_code"].str[:2]
    data["category"] = data["hs2"].map(HS2_CATEGORY).fillna("Other")

    OUT_DIR.mkdir(exist_ok=True)
    all_path = OUT_DIR / "pakistan_imports_all_districts.csv"
    data.to_csv(all_path, index=False)

    # Customs district names contain "NEW YORK" for the NYC port complex.
    ny = data[data["district_name"].str.upper().str.contains("NEW YORK", na=False)]
    ny_path = OUT_DIR / "pakistan_imports_new_york_district.csv"
    ny.to_csv(ny_path, index=False)

    # The API returns a "TOTAL FOR ALL DISTRICTS" row for every product. Adding it to
    # the individual districts would count everything twice, so leave it out here.
    is_total = data["district_name"].str.upper().str.startswith("TOTAL")
    summary = (
        data[~is_total]
        .groupby(["year", "category"], as_index=False)["consumption_imports_usd"].sum()
    )
    summary_path = OUT_DIR / "pakistan_imports_by_year_category.csv"
    summary.to_csv(summary_path, index=False)

    print(f"\nSaved:\n  {all_path}\n  {ny_path}  ({len(ny):,} rows)\n  {summary_path}")
    print("\nDistrict names matched as 'New York':",
          sorted(ny['district_name'].unique()) or "none - check district_name values")


# --------------------------------------------------------------------------- #
# Part 2: ACS
# --------------------------------------------------------------------------- #
def find_acs_variables(base_url):
    """Locate the Pakistan / Pakistani estimate variables by label, not by hard-coded ID."""
    out = {}
    for group, token in (("B05006", "Pakistan"), ("B02015", "Pakistani")):
        meta = api_get(f"{base_url}/groups/{group}.json", {})
        hits = [
            (name, v["label"])
            for name, v in meta.get("variables", {}).items()
            if name.endswith("E") and token.lower() in v.get("label", "").lower()
        ]
        if not hits:
            sys.exit(f"Could not find a '{token}' line in {group}. "
                     f"Run: python fetch_census_data.py list-vars acs")
        out[group] = hits[0][0]
        print(f"{group}: using {hits[0][0]}  ->  {hits[0][1]}")
    return out


def clean_acs(df, numeric_cols):
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        df.loc[df[c].isin(ACS_NULL_CODES), c] = pd.NA
    return df


def fetch_acs(args):
    key = get_key()
    base = f"{BASE}/{args.year}/acs/acs5"
    ids = find_acs_variables(base)

    pak_born = ids["B05006"]
    pak_anc = ids["B02015"]
    est_vars = [
        "NAME",
        "B01003_001E",                      # total population
        "B05006_001E",                      # total foreign-born
        pak_born, pak_born[:-1] + "M",      # Pakistan-born estimate and margin of error
        pak_anc, pak_anc[:-1] + "M",        # Pakistani (Asian alone) estimate and margin
    ]
    # B01003 is a different table, so request it in the same call (allowed in ACS API).
    geos = {
        "county": {"for": f"county:{NYC_COUNTIES}", "in": "state:36"},
        "puma": {"for": "public use microdata area:*", "in": "state:36"},
    }

    OUT_DIR.mkdir(exist_ok=True)
    for geo_name, geo in geos.items():
        params = {"get": ",".join(est_vars), **geo, "key": key}
        df = to_frame(api_get(f"{base}", params))
        if df.empty:
            print(f"[{geo_name}] no rows")
            continue
        num_cols = [c for c in est_vars if c != "NAME"]
        df = clean_acs(df, num_cols)

        if geo_name == "puma":
            # Keep only NYC PUMAs (names start with "NYC-")
            df = df[df["NAME"].str.contains("NYC", na=False)].copy()

        df["pakistan_born_share_of_foreign_born"] = df[pak_born] / df["B05006_001E"]
        df["pakistani_share_of_population"] = df[pak_anc] / df["B01003_001E"]
        path = OUT_DIR / f"acs{args.year}_nyc_pakistani_{geo_name}.csv"
        df.to_csv(path, index=False)
        print(f"[{geo_name}] {len(df)} rows -> {path}")

    print("\nReminder: neighborhood-level estimates for small groups have large margins "
          "of error. Report the *M columns and consider grouping adjacent areas.")


# --------------------------------------------------------------------------- #
# Part 3: Discovery helper
# --------------------------------------------------------------------------- #
def list_vars(args):
    if args.which == "trade":
        vars_ = fetch_variable_names(TRADE_URL)
        for name, v in sorted(vars_.items()):
            print(f"{name:25s} {v.get('label', '')}")
    else:
        base = f"{BASE}/{args.year}/acs/acs5"
        for group in ("B05006", "B02015"):
            meta = api_get(f"{base}/groups/{group}.json", {})
            print(f"\n== {group} ==")
            for name, v in meta.get("variables", {}).items():
                if name.endswith("E"):
                    print(f"{name:15s} {v.get('label', '')}")


# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("trade", help="US imports from Pakistan by HS code and district")
    t.add_argument("--start", type=int, default=2010)
    t.add_argument("--end", type=int, default=2025)
    t.add_argument("--level", default="HS2", choices=["HS2", "HS4", "HS6", "HS10"])
    t.add_argument("--month", default="12", help="Month for year-to-date totals (12 = full year)")
    t.add_argument("--country-code", default=PAKISTAN_CODE)
    t.add_argument("--level-param", default=None,
                   help="Name of the HS-level filter (auto-detected; normally COMM_LVL)")
    t.set_defaults(func=fetch_trade)

    a = sub.add_parser("acs", help="ACS 5-year Pakistani / Pakistan-born tables for NYC")
    a.add_argument("--year", type=int, default=2024, help="ACS 5-year end year (2024 = 2020-2024)")
    a.set_defaults(func=fetch_acs)

    l = sub.add_parser("list-vars", help="Print available variables")
    l.add_argument("which", choices=["trade", "acs"])
    l.add_argument("--year", type=int, default=2024)
    l.set_defaults(func=list_vars)

    args = p.parse_args()
    try:
        args.func(args)
    except RuntimeError as exc:
        sys.exit(f"\nERROR: {exc}")


if __name__ == "__main__":
    main()
