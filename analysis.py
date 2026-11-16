#!/usr/bin/env python3
"""
Policy East analysis
    Where Pakistani goods enter the US, and where Pakistani New Yorkers live.

This script reproduces every number, chart and table in the report, from the five
CSV files that fetch_census_data.py writes. Read it top to bottom: each step says
WHY it is done, not only what it does.

    python analysis.py --data-dir data --out-dir output
    python analysis.py --data-dir data --out-dir output --template report_template.html
    python analysis.py --data-dir data --out-dir output --no-charts

Inputs (in --data-dir)
    pakistan_imports_all_districts.csv       US imports from Pakistan, HS 2-digit, by customs district
    pakistan_imports_new_york_district.csv   the same, New York district only
    acs2024_nyc_pakistani_county.csv         ACS 2020-2024, five boroughs
    acs2024_nyc_pakistani_puma.csv           ACS 2020-2024, 55 NYC neighborhood areas (PUMAs)

Outputs (in --out-dir)
    findings.md                 every headline number, written from the data
    report_data.json            the data the interactive report reads
    tables/*.csv                clean tables for reuse
    charts/*.png                five static charts for the post
    report.html                 only if --template is given

Requires: pandas, numpy, matplotlib (matplotlib only for charts)
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Settings, in one place so the choices are visible
# --------------------------------------------------------------------------- #
FILES = {
    "all": "pakistan_imports_all_districts.csv",
    "ny": "pakistan_imports_new_york_district.csv",
    "county": "acs2024_nyc_pakistani_county.csv",
    "puma": "acs2024_nyc_pakistani_puma.csv",
}

NY_DISTRICT = "NEW YORK CITY, NY"

# ACS variables. B02015 = Asian origin group, "alone". B05006 = place of birth, foreign-born.
PAK_E, PAK_M = "B02015_025E", "B02015_025M"    # identifies as Pakistani (alone)
BORN_E, BORN_M = "B05006_064E", "B05006_064M"  # born in Pakistan
POP_E, FB_E = "B01003_001E", "B05006_001E"     # total population, total foreign-born

# ACS margins of error are published at the 90% confidence level. Dividing by 1.645
# turns a margin of error into a standard error, which the coefficient of variation needs.
Z90 = 1.645

# Product groups. HS chapters are far too fine for a post, so we group the ones that matter
# for Pakistan and fold the rest into "Other products". Groups were chosen BEFORE looking at
# the New York results, so the grouping does not bend the story.
HS2_GROUP = {
    "61": "Apparel (knit)",
    "62": "Apparel (woven)",
    "63": "Home textiles & made-ups",
    "52": "Cotton, yarn & fabric",
    "41": "Leather & leather goods",
    "42": "Leather & leather goods",
    "10": "Rice & cereals",
    "95": "Sports goods & toys",
    "90": "Medical & surgical instruments",
}
OTHER = "Other products"

BOROUGH_BY_FIPS = {"005": "Bronx", "047": "Brooklyn", "061": "Manhattan",
                   "081": "Queens", "085": "Staten Island"}

# Brooklyn community districts 12-15 (Borough Park, Coney Island, Flatbush, Sheepshead Bay).
# They hold the largest Pakistani populations; reported as one block because the
# individual neighborhood estimates are noisy.
SOUTH_BROOKLYN = r"Brooklyn Community District (?:12|13|14|15)--"


def nice_district(name):
    """'HOUSTON-GALVESTON, TX' -> 'Houston-Galveston, TX'"""
    if ", " not in name:
        return name.title()
    city, state = name.rsplit(", ", 1)
    return f"{city.title()}, {state}"


# --------------------------------------------------------------------------- #
# Step 1. Load and validate
# --------------------------------------------------------------------------- #
def load(data_dir):
    """Load the files and stop early if something is off.

    WHY: a wrong total is worse than a crash. The first version of this project double
    counted every import because the API adds a 'TOTAL FOR ALL DISTRICTS' row to each
    product, and a naive sum added it to the districts. These checks would have caught it.
    """
    d = Path(data_dir)
    missing = [f for f in FILES.values() if not (d / f).exists()]
    if missing:
        sys.exit(f"Missing files in {d}: {missing}")

    str_cols = {"hs_code": str, "district_code": str}
    allp = pd.read_csv(d / FILES["all"], dtype=str_cols)
    ny = pd.read_csv(d / FILES["ny"], dtype=str_cols)
    county = pd.read_csv(d / FILES["county"], dtype={"county": str})
    puma = pd.read_csv(d / FILES["puma"], dtype={"public use microdata area": str})

    for df in (allp, ny):
        df["hs2"] = df["hs_code"].str[:2]

    # 1a. Separate the API's own total rows from real districts.
    is_total = allp["district_name"].str.upper().str.startswith("TOTAL")
    dist = allp[~is_total].copy()
    totals = allp[is_total]

    # 1b. Districts must add up to the API's total row (proves nothing was lost or doubled).
    by_dist = dist.groupby("year").consumption_imports_usd.sum()
    by_total = totals.groupby("year").consumption_imports_usd.sum()
    gap = (by_dist - by_total).abs().max()
    print(f"[check] districts vs API total row, largest gap: ${gap:,.0f}")
    if gap > 1_000_000:
        sys.exit("Districts do not add up to the API total row. Investigate before going on.")

    # 1c. No duplicate rows.
    dups = dist.duplicated(["year", "district_code", "hs_code"]).sum()
    print(f"[check] duplicate rows: {dups}")
    if dups:
        sys.exit("Duplicate (year, district, product) rows found.")

    # 1d. The New York file must match the New York rows of the all-districts file.
    ny_a = dist[dist.district_name == NY_DISTRICT].groupby("year").consumption_imports_usd.sum()
    ny_b = ny.groupby("year").consumption_imports_usd.sum()
    ny_gap = (ny_a - ny_b).abs().max()
    print(f"[check] New York file vs all-districts file, largest gap: ${ny_gap:,.0f}")
    if ny_gap > 1_000_000:
        sys.exit("New York file does not match the all-districts file.")

    # 1e. Pakistan only, and the years we expect.
    print(f"[check] countries: {sorted(dist.country.unique())}, years {dist.year.min()}-{dist.year.max()}")

    return dist, ny, county, puma


# --------------------------------------------------------------------------- #
# Step 2. Trade analysis
# --------------------------------------------------------------------------- #
def analyse_trade(dist, ny):
    """Totals, New York's share, rank, concentration and product mix.

    WHY 'imports for consumption': it excludes goods that go into bonded storage or are
    re-exported, so it is closer to what the US market actually uses than 'general imports'.
    The two are within about 1% of each other for Pakistan, so the choice does not matter much.
    """
    us = dist.groupby("year").consumption_imports_usd.sum()
    nyv = ny.groupby("year").consumption_imports_usd.sum()
    years = [int(y) for y in us.index]
    first, last = years[0], years[-1]

    yearly = pd.DataFrame({"us_usd": us, "ny_usd": nyv})
    yearly["ny_share_pct"] = yearly.ny_usd / yearly.us_usd * 100

    # Rank of New York among all districts each year.
    by_district = dist.groupby(["year", "district_name"]).consumption_imports_usd.sum().unstack(fill_value=0)
    yearly["ny_rank"] = by_district.rank(axis=1, ascending=False)[NY_DISTRICT].astype(int)

    # Herfindahl-Hirschman index of entry points: sum of squared percentage shares.
    # WHY: one number for "is trade flowing through a few ports or many?" (10,000 = one port).
    shares = by_district.div(by_district.sum(axis=1), axis=0) * 100
    yearly["entry_hhi"] = (shares ** 2).sum(axis=1)

    # Compound annual growth rate: smooths the 2021-22 spike so growth can be compared fairly.
    cagr = (us[last] / us[first]) ** (1 / (last - first)) - 1

    # Product groups, national and New York.
    def group(df):
        return df["hs2"].map(HS2_GROUP).fillna(OTHER)

    nat_g = (dist.assign(group=group(dist)).groupby(["year", "group"])
             .consumption_imports_usd.sum().unstack(fill_value=0))
    ny_g = (ny.assign(group=group(ny)).groupby(["year", "group"])
            .consumption_imports_usd.sum().unstack(fill_value=0)
            .reindex(index=nat_g.index, columns=nat_g.columns, fill_value=0))

    # Location quotient: New York's share of a product divided by New York's share of ALL imports.
    # WHY: raw shares mislead because New York is big everywhere. An LQ above 1 means New York
    # takes more of this product than its overall weight would predict.
    ny_overall = yearly.loc[last, "ny_share_pct"] / 100
    lq = (ny_g.loc[last] / nat_g.loc[last]) / ny_overall

    return {
        "years": years, "first": first, "last": last,
        "yearly": yearly, "by_district": by_district, "cagr": cagr,
        "nat_g": nat_g, "ny_g": ny_g, "lq": lq,
    }


# --------------------------------------------------------------------------- #
# Step 3. Community analysis
# --------------------------------------------------------------------------- #
def analyse_community(county, puma):
    """City totals, borough split and how much to trust each neighborhood.

    WHY margins of error matter: ACS numbers are survey estimates. For a small group like
    Pakistani New Yorkers, many neighborhood estimates are barely distinguishable from zero.
    Reporting them without the margin of error would overstate what the data can say.
    """
    def total(df, e, m):
        # Standard Census method for summing estimates: add the estimates, and combine the
        # margins of error as the square root of the sum of squares (assumes independence).
        return float(df[e].sum()), float(np.sqrt((df[m] ** 2).sum()))

    county = county.copy()
    county["borough"] = county["county"].map(BOROUGH_BY_FIPS)

    pak, pak_moe = total(county, PAK_E, PAK_M)
    born, born_moe = total(county, BORN_E, BORN_M)
    nyc_pop = float(county[POP_E].sum())

    puma = puma.copy()
    puma["borough"] = puma["NAME"].str.extract(r"NYC-(Manhattan|Bronx|Brooklyn|Queens|Staten Island)")[0]
    puma["area"] = puma["NAME"].str.split("--").str[1].str.replace(" PUMA; New York", "", regex=False)

    # Coefficient of variation: standard error divided by the estimate.
    # Rule of thumb used by Census users: under 15% reliable, 15-30% use with caution, over 30% weak.
    est = puma[PAK_E].replace(0, np.nan)
    puma["cv"] = (puma[PAK_M] / Z90) / est
    puma["reliability"] = np.select(
        [puma["cv"] < 0.15, puma["cv"] < 0.30], ["high", "medium"], default="low")

    south = puma[puma["NAME"].str.contains(SOUTH_BROOKLYN, regex=True)]
    south_est, south_moe = total(south, PAK_E, PAK_M)

    by_borough = county[["borough", PAK_E, PAK_M, BORN_E, BORN_M, POP_E]].copy()
    by_borough["share_of_nyc_pakistani_pct"] = by_borough[PAK_E] / pak * 100

    return {
        "county": county, "puma": puma, "by_borough": by_borough,
        "pak": pak, "pak_moe": pak_moe, "born": born, "born_moe": born_moe,
        "nyc_pop": nyc_pop, "pak_share_of_pop_pct": pak / nyc_pop * 100,
        "south": south, "south_est": south_est, "south_moe": south_moe,
        "south_share_pct": south_est / pak * 100,
        "n_low": int((puma["reliability"] == "low").sum()),
        "n_cv30": int((puma["cv"] >= 0.30).sum()),          # CV computable and 30% or more
        "n_zero": int((puma[PAK_E] == 0).sum()),            # estimate of zero: CV undefined, counted as weak
        "n_moe_ge_est": int((puma[PAK_M] >= puma[PAK_E]).sum()),
        "n_areas": len(puma),
    }


# --------------------------------------------------------------------------- #
# Step 4. Findings: every sentence in the report comes from here
# --------------------------------------------------------------------------- #
def build_findings(t, c):
    y, first, last = t["yearly"], t["first"], t["last"]
    us, nyv = y.us_usd, y.ny_usd
    peak_year = int(us.idxmax())
    top_d = t["by_district"].loc[last].sort_values(ascending=False)
    top_d_share = top_d / top_d.sum() * 100
    nat_last, nat_first = t["nat_g"].loc[last], t["nat_g"].loc[first]
    core = ["Apparel (knit)", "Apparel (woven)", "Home textiles & made-ups"]
    big = nat_last[nat_last >= 1e9].index
    growth = (nat_last / nat_first - 1) * 100
    lq = t["lq"].sort_values(ascending=False)

    f = {
        "first_year": first, "last_year": last,
        "us_first_bn": us[first] / 1e9, "us_last_bn": us[last] / 1e9,
        "us_growth_pct": (us[last] / us[first] - 1) * 100,
        "cagr_pct": t["cagr"] * 100,
        "peak_year": peak_year, "peak_bn": us[peak_year] / 1e9,
        "drop_after_peak_pct": (us[peak_year + 1] / us[peak_year] - 1) * 100 if peak_year < last else None,
        "ny_last_bn": nyv[last] / 1e9,
        "ny_share_last_pct": y.ny_share_pct[last], "ny_share_first_pct": y.ny_share_pct[first],
        "ny_share_min_pct": y.ny_share_pct.min(), "ny_share_min_year": int(y.ny_share_pct.idxmin()),
        "ny_share_max_pct": y.ny_share_pct.max(), "ny_share_max_year": int(y.ny_share_pct.idxmax()),
        "ny_growth_pct": (nyv[last] / nyv[first] - 1) * 100,
        "ny_rank_always_first": bool((y.ny_rank == 1).all()),
        "second_district": nice_district(top_d.index[1] if top_d.index[0] == NY_DISTRICT else top_d.index[0]),
        "second_district_share_pct": float(top_d_share.iloc[1]),
        "third_district": nice_district(top_d.index[2]), "third_district_share_pct": float(top_d_share.iloc[2]),
        "entry_hhi_first": y.entry_hhi[first], "entry_hhi_last": y.entry_hhi[last],
        "core_share_pct": float(nat_last[core].sum() / nat_last.sum() * 100),
        "fastest_big_group": growth[big].idxmax(), "fastest_big_growth_pct": float(growth[big].max()),
        "small_group_note": small_group_note(growth, big, nat_first, float(growth[big].max()), first),
        "top_lq_group": lq.index[0], "top_lq": float(lq.iloc[0]),
        "second_lq_group": lq.index[1], "second_lq": float(lq.iloc[1]),
        "top_lq_group_ny_share_pct": float(t["ny_g"].loc[last, lq.index[0]] / nat_last[lq.index[0]] * 100),
        "second_lq_group_ny_share_pct": float(t["ny_g"].loc[last, lq.index[1]] / nat_last[lq.index[1]] * 100),
        "nyc_pakistani": c["pak"], "nyc_pakistani_moe": c["pak_moe"],
        "nyc_pakistan_born": c["born"], "nyc_pakistan_born_moe": c["born_moe"],
        "pak_share_of_pop_pct": c["pak_share_of_pop_pct"],
        "south_brooklyn": c["south_est"], "south_brooklyn_moe": c["south_moe"],
        "south_brooklyn_share_pct": c["south_share_pct"],
        "areas_total": c["n_areas"], "areas_low_reliability": c["n_low"],
        "areas_cv_ge30": c["n_cv30"], "areas_zero_estimate": c["n_zero"],
        "areas_moe_ge_estimate": c["n_moe_ge_est"],
    }
    f = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in f.items()}
    return f


def small_group_note(growth, big, nat_first, big_growth, first):
    """One sentence on smaller groups (under $1bn) that grew faster than the fastest large group."""
    small = [g for g in growth.index if g not in set(big) and growth[g] > big_growth]
    if not small:
        return "No smaller group grew faster in percentage terms."
    parts = [f"{g} (+{growth[g]:.0f}%, from ${nat_first[g] / 1e6:,.0f}m in {first})" for g in small]
    lead = "One smaller group grew faster in percentage terms: " if len(small) == 1 else f"{len(small)} smaller groups grew faster in percentage terms: "
    return lead + "; ".join(parts) + ". Growth from such a small base is easy to inflate, so read it with care."


def findings_markdown(f):
    s = f
    return f"""# Findings

## US imports from Pakistan
- Imports for consumption rose from ${s['us_first_bn']:.2f}bn in {s['first_year']} to ${s['us_last_bn']:.2f}bn in {s['last_year']} ({s['us_growth_pct']:+.0f}%, {s['cagr_pct']:.1f}% a year).
- The peak was ${s['peak_bn']:.2f}bn in {s['peak_year']}; the next year it fell {abs(s['drop_after_peak_pct']):.0f}%.
- Apparel and home textiles were {s['core_share_pct']:.0f}% of {s['last_year']} imports.
- {s['fastest_big_group']} grew fastest of the groups above $1bn ({s['fastest_big_growth_pct']:+.0f}% since {s['first_year']}).

## New York
- New York City district took ${s['ny_last_bn']:.2f}bn in {s['last_year']}, a {s['ny_share_last_pct']:.0f}% share; it ranked first among US customs districts in {'every year' if s['ny_rank_always_first'] else 'some years only'} of the data.
- Its share ranged from {s['ny_share_min_pct']:.0f}% ({s['ny_share_min_year']}) to {s['ny_share_max_pct']:.0f}% ({s['ny_share_max_year']}). New York's imports grew {s['ny_growth_pct']:+.0f}% against {s['us_growth_pct']:+.0f}% nationally, so its share was {s['ny_share_last_pct']:.0f}% in {s['last_year']}.
- Next largest: {s['second_district']} ({s['second_district_share_pct']:.0f}%) and {s['third_district']} ({s['third_district_share_pct']:.0f}%).
- New York over-indexes on {s['top_lq_group']} ({s['top_lq_group_ny_share_pct']:.0f}% of the national total, location quotient {s['top_lq']:.1f}) and {s['second_lq_group']} ({s['second_lq_group_ny_share_pct']:.0f}%, LQ {s['second_lq']:.1f}).
- Entry-point concentration (HHI) went from {s['entry_hhi_first']:.0f} to {s['entry_hhi_last']:.0f}.

## Pakistani New Yorkers (ACS 2020-2024)
- About {s['nyc_pakistani']:,.0f} residents (±{s['nyc_pakistani_moe']:,.0f}) identify as Pakistani alone, {s['pak_share_of_pop_pct']:.2f}% of the city.
- About {s['nyc_pakistan_born']:,.0f} (±{s['nyc_pakistan_born_moe']:,.0f}) were born in Pakistan.
- Four south Brooklyn areas hold about {s['south_brooklyn']:,.0f} (±{s['south_brooklyn_moe']:,.0f}), or {s['south_brooklyn_share_pct']:.0f}% of the city total.
- {s['areas_low_reliability']} of {s['areas_total']} neighborhood areas are weak estimates: {s['areas_cv_ge30']} have a coefficient of variation of 30% or more and {s['areas_zero_estimate']} have an estimate of zero. In {s['areas_moe_ge_estimate']} the margin of error is as large as the estimate.

## Limits
- Customs district = where goods entered the US, not who bought them.
- Dollar values are nominal (current dollars, not adjusted for inflation), so growth rates overstate real growth.
- "Pakistani alone" excludes multiracial residents, so it is a floor for the community.
- Neighborhood ranks between close areas are within the margin of error.
"""


# --------------------------------------------------------------------------- #
# Step 5. Export for the interactive report
# --------------------------------------------------------------------------- #
def export_report_data(t, c, f):
    years = t["years"]
    nat_g, ny_g = t["nat_g"], t["ny_g"]
    groups = list(nat_g.loc[t["last"]].sort_values(ascending=False).index)

    def musd(series):                       # dollars -> millions, 2 decimals
        return [round(float(v) / 1e6, 2) for v in series]

    districts = (t["by_district"].T
                 .assign(_last=lambda d: d[t["last"]]).sort_values("_last", ascending=False)
                 .drop(columns="_last"))

    data = {
        "meta": {"years": years, "first": t["first"], "last": t["last"], "ny_district": NY_DISTRICT},
        "findings": f,
        "yearly": [{"year": int(yr), "us_usd_m": round(r.us_usd / 1e6, 2), "ny_usd_m": round(r.ny_usd / 1e6, 2),
                    "ny_share_pct": round(r.ny_share_pct, 2), "ny_rank": int(r.ny_rank),
                    "entry_hhi": round(r.entry_hhi, 0)} for yr, r in t["yearly"].iterrows()],
        "districts": [{"name": nice_district(n), "is_ny": n == NY_DISTRICT, "usd_m": musd(row)}
                      for n, row in districts.iterrows()],
        "groups": groups,
        "group_national_usd_m": {g: musd(nat_g[g]) for g in groups},
        "group_ny_usd_m": {g: musd(ny_g[g]) for g in groups},
        "boroughs": [{"borough": r.borough, "pak": int(r[PAK_E]), "pak_moe": int(r[PAK_M]),
                      "born": int(r[BORN_E]), "born_moe": int(r[BORN_M]), "pop": int(r[POP_E]),
                      "share_pct": round(r.share_of_nyc_pakistani_pct, 1)}
                     for _, r in c["by_borough"].iterrows()],
        "pumas": [{"area": r.area, "borough": r.borough, "pak": int(r[PAK_E]), "pak_moe": int(r[PAK_M]),
                   "born": int(r[BORN_E]), "born_moe": int(r[BORN_M]), "pop": int(r[POP_E]),
                   "cv": None if pd.isna(r.cv) else round(float(r.cv), 3), "reliability": r.reliability,
                   "south_brooklyn": bool(re.search(SOUTH_BROOKLYN, r.NAME))}
                  for _, r in c["puma"].iterrows()],
    }
    return data


# --------------------------------------------------------------------------- #
# Step 6. Static charts (for the post). Colors pass the validate_palette.js checks.
# --------------------------------------------------------------------------- #
def make_charts(t, c, f, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    plt.rcParams.update({"font.family": ["Inter", "DejaVu Sans"], "axes.unicode_minus": False})

    SURF, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
    BLUE, ORANGE, GRAY, BLUE_L, BLUE_D = "#2a78d6", "#eb6834", "#c3c2b7", "#9ec5f4", "#256abf"
    out.mkdir(parents=True, exist_ok=True)

    def style(ax):
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color(AXIS)
        ax.tick_params(axis="both", length=0, colors=MUTED, labelsize=9)
        ax.set_facecolor(SURF)

    def header(fig, title, sub, src):
        fig.patch.set_facecolor(SURF)
        fig.text(0.04, 0.965, title, fontsize=15, fontweight="bold", color=INK, ha="left", va="top")
        fig.text(0.04, 0.905, sub, fontsize=9.5, color=INK2, ha="left", va="top")
        fig.text(0.04, 0.025, src, fontsize=7.5, color=MUTED, ha="left", va="bottom", linespacing=1.5)

    def save(fig, name):
        fig.savefig(out / name, dpi=150, facecolor=SURF)
        plt.close(fig)

    SRC_T = ("Source: U.S. Census Bureau, International Trade API. Imports for consumption, "
             "by customs district of entry. Policy East analysis.")
    y, first, last = t["yearly"], t["first"], t["last"]
    us, nyb = y.us_usd / 1e9, y.ny_usd / 1e9

    # Chart 1: two lines on ONE axis (same units), so no dual-axis problem.
    fig, ax = plt.subplots(figsize=(9, 5.4), dpi=150)
    fig.subplots_adjust(left=0.08, right=0.85, top=0.80, bottom=0.13)
    header(fig, f"US imports from Pakistan are up {f['us_growth_pct']:.0f}% since {first}; "
                f"New York takes about {f['ny_share_last_pct']:.0f}%",
           "Imports for consumption, $ billions per year. New York = New York City customs district "
           "(port of entry, not final buyer).", SRC_T)
    style(ax)
    for s, col in ((us, BLUE), (nyb, ORANGE)):
        ax.plot(s.index, s.values, color=col, lw=2, solid_capstyle="round", solid_joinstyle="round", zorder=3)
        ax.plot(s.index[-1], s.values[-1], "o", ms=8, color=col, mec=SURF, mew=2, zorder=5)
    ax.set_ylim(0, 6.6); ax.set_xlim(first - 0.4, last + 0.6)
    ax.set_yticks(range(0, 7))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: "$0" if v == 0 else f"${v:.0f}bn"))
    ax.set_xticks(list(range(first, last + 1, 3)))
    ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
    ax.annotate(f"US total\n${us.iloc[-1]:.2f}bn", (last, us.iloc[-1]), xytext=(10, 0),
                textcoords="offset points", va="center", fontsize=9.5, color=INK)
    ax.annotate(f"New York\n${nyb.iloc[-1]:.2f}bn", (last, nyb.iloc[-1]), xytext=(10, 0),
                textcoords="offset points", va="center", fontsize=9.5, color=INK)
    pk = f["peak_year"]
    ax.annotate(f"{pk} peak: ${us[pk]:.2f}bn", (pk, us[pk]), xytext=(0, 10), textcoords="offset points",
                ha="center", fontsize=9, color=INK2)
    ax.legend([Line2D([0], [0], color=BLUE, lw=2), Line2D([0], [0], color=ORANGE, lw=2)],
              ["All US customs districts", "New York City district"], loc="upper left", frameon=False,
              fontsize=9, labelcolor=INK2, handlelength=1.6)
    save(fig, "chart1_us_vs_new_york_imports.png")

    # Chart 2: ranked bars; emphasis (one accent, rest gray) because New York is the point.
    d_last = t["by_district"].loc[last].sort_values(ascending=False) / 1e9
    rows = [(nice_district(k), v) for k, v in d_last.head(8).items()] + [("All other districts", d_last.iloc[8:].sum())]
    names, vals = [r[0] for r in rows][::-1], [r[1] for r in rows][::-1]
    fig, ax = plt.subplots(figsize=(9, 5.4), dpi=150)
    fig.subplots_adjust(left=0.26, right=0.95, top=0.80, bottom=0.09)
    header(fig, "New York is the top US entry point for Pakistani goods",
           f"{last} imports for consumption by customs district, $ billions and share of the US total.", SRC_T)
    style(ax); ax.spines["bottom"].set_visible(False)
    ax.barh(range(len(vals)), vals, height=0.5, color=[ORANGE if n.startswith("New York") else GRAY for n in names])
    for i, (n, v) in enumerate(zip(names, vals)):
        ax.text(v + 0.03, i, f"${v:.2f}bn  ·  {v / d_last.sum() * 100:.0f}%", va="center", fontsize=9.5,
                color=INK, fontweight="bold" if n.startswith("New York") else "normal")
    ax.set_yticks(range(len(names))); ax.set_yticklabels(names, fontsize=10, color=INK2)
    ax.set_xticks([]); ax.set_xlim(0, max(vals) * 1.35)
    save(fig, "chart2_top_entry_districts.png")

    # Chart 3: dumbbell, before -> after per product group.
    nat = t["nat_g"] / 1e9
    c0, c1 = nat.loc[first], nat.loc[last]
    order = c1.sort_values().index
    fig, ax = plt.subplots(figsize=(9, 5.6), dpi=150)
    fig.subplots_adjust(left=0.30, right=0.88, top=0.80, bottom=0.12)
    header(fig, f"{f['fastest_big_group']} grew fastest of the large product groups",
           f"US imports from Pakistan by product group, {first} vs {last}, $ billions (imports for consumption).", SRC_T)
    style(ax)
    xmax = 1.75
    for i, g in enumerate(order):
        ax.plot([c0[g], c1[g]], [i, i], color=AXIS, lw=2, zorder=2, solid_capstyle="round")
        ax.plot(c0[g], i, "o", ms=8, color=BLUE_L, mec=SURF, mew=2, zorder=4)
        ax.plot(c1[g], i, "o", ms=8, color=BLUE_D, mec=SURF, mew=2, zorder=4)
        ax.text(xmax - 0.03, i, f"{(c1[g] / c0[g] - 1) * 100:+.0f}%", va="center", ha="right", fontsize=9.5, color=INK)
    ax.text(xmax - 0.03, len(order) - 0.35, "Change", ha="right", fontsize=8.5, color=MUTED)
    ax.set_yticks(range(len(order))); ax.set_yticklabels(order, fontsize=10, color=INK2)
    ax.set_xlim(0, xmax); ax.set_ylim(-0.6, len(order) - 0.1)
    ax.set_xticks([0, 0.5, 1.0, 1.5])
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: "$0" if v == 0 else f"${v:g}bn"))
    ax.grid(axis="x", color=GRID, lw=0.8); ax.set_axisbelow(True)
    ax.legend([Line2D([0], [0], marker="o", color="none", markerfacecolor=BLUE_L, markeredgecolor=BLUE_L, markersize=8),
               Line2D([0], [0], marker="o", color="none", markerfacecolor=BLUE_D, markeredgecolor=BLUE_D, markersize=8)],
              [str(first), str(last)], loc="lower left", bbox_to_anchor=(0.50, 0.10), frameon=False,
              fontsize=9.5, labelcolor=INK2)
    save(fig, "chart3_product_mix.png")

    # Chart 4: New York's share of each product group, against its overall share.
    natm, nym = t["nat_g"].loc[last] / 1e6, t["ny_g"].loc[last] / 1e6
    sh = (nym / natm * 100).sort_values()
    overall = f["ny_share_last_pct"]
    fig, ax = plt.subplots(figsize=(9, 5.6), dpi=150)
    fig.subplots_adjust(left=0.30, right=0.95, top=0.80, bottom=0.09)
    header(fig, f"New York's share is highest for {f['top_lq_group'].lower()} and {f['second_lq_group'].lower()}",
           f"Share of each product group's {last} US imports from Pakistan that entered through New York. "
           f"Orange = above the overall {overall:.0f}%.", SRC_T)
    style(ax); ax.spines["bottom"].set_visible(False)
    ax.axvline(overall, color=INK2, lw=1, zorder=1)
    ax.barh(range(len(sh)), sh.values, height=0.5, color=[ORANGE if v > overall else GRAY for v in sh.values], zorder=2)
    for i, g in enumerate(sh.index):
        ax.text(sh[g] + 1.2, i, f"{sh[g]:.0f}%   (\\${nym[g]:,.0f}m of \\${natm[g]:,.0f}m)", va="center", fontsize=9.5,
                color=INK, zorder=4, bbox=dict(facecolor=SURF, edgecolor="none", pad=1.5))
    ax.text(overall + 0.8, len(sh) - 0.42, f"All products: {overall:.0f}%", fontsize=8.5, color=INK2, va="bottom")
    ax.set_yticks(range(len(sh))); ax.set_yticklabels(sh.index, fontsize=10, color=INK2)
    ax.set_xlim(0, 115); ax.set_xticks([]); ax.set_ylim(-0.6, len(sh) - 0.1)
    save(fig, "chart4_new_york_share_by_product.png")

    # Chart 5: estimates WITH error bars, so readers see the uncertainty.
    # (Title is written for the 2020-2024 ACS data; re-read it if you change the input.)
    pu = c["puma"]
    top = pu.sort_values(PAK_E, ascending=False).head(12).iloc[::-1]
    labels = [f"{a}  ({b})" for a, b in zip(top.area, top.borough)]
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    fig.subplots_adjust(left=0.40, right=0.95, top=0.80, bottom=0.13)
    header(fig, "Pakistani New Yorkers are concentrated in south Brooklyn and parts of Queens",
           f"Residents identifying as Pakistani alone, 12 largest NYC neighborhood areas, with 90% margin of error. "
           f"NYC total: about {c['pak']:,.0f} (±{c['pak_moe']:,.0f}).",
           "Source: U.S. Census Bureau, ACS 2020–2024 5-year estimates, table B02015 (Pakistani alone). "
           "Areas are Census PUMAs. Policy East analysis.\nNeighboring bars overlap within their margins of "
           "error, so treat the ranking between close areas as approximate.")
    style(ax); ax.spines["bottom"].set_visible(False)
    ax.barh(range(len(top)), top[PAK_E], height=0.5, color=BLUE, zorder=2)
    ax.errorbar(top[PAK_E], range(len(top)), xerr=top[PAK_M], fmt="none", ecolor=INK2, elinewidth=1, capsize=3, zorder=3)
    for i, (e, m) in enumerate(zip(top[PAK_E], top[PAK_M])):
        ax.text(e + m + 90, i, f"{e:,.0f}", va="center", fontsize=9.5, color=INK)
    ax.set_yticks(range(len(top))); ax.set_yticklabels(labels, fontsize=9.5, color=INK2)
    ax.set_xlim(0, 6900); ax.set_xticks([0, 2000, 4000, 6000])
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.grid(axis="x", color=GRID, lw=0.8); ax.set_axisbelow(True)
    save(fig, "chart5_nyc_pakistani_population_by_area.png")


# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", default="data")
    p.add_argument("--out-dir", default="output")
    p.add_argument("--template", help="HTML template to fill with the report data (writes report.html)")
    p.add_argument("--no-charts", action="store_true", help="skip the PNG charts")
    a = p.parse_args()

    out = Path(a.out_dir)
    (out / "tables").mkdir(parents=True, exist_ok=True)

    dist, ny, county, puma = load(a.data_dir)
    t = analyse_trade(dist, ny)
    c = analyse_community(county, puma)
    f = build_findings(t, c)

    # Tables
    t["yearly"].round(4).to_csv(out / "tables" / "us_vs_new_york_by_year.csv")
    (t["nat_g"] / 1e6).round(2).to_csv(out / "tables" / "national_by_product_group_usd_m.csv")
    (t["ny_g"] / 1e6).round(2).to_csv(out / "tables" / "new_york_by_product_group_usd_m.csv")
    t["lq"].round(2).rename("location_quotient").to_csv(out / "tables" / "new_york_location_quotient.csv")
    c["puma"].drop(columns=["state", "public use microdata area"], errors="ignore").to_csv(
        out / "tables" / "nyc_areas_with_reliability.csv", index=False)
    c["by_borough"].to_csv(out / "tables" / "nyc_boroughs.csv", index=False)

    (out / "findings.md").write_text(findings_markdown(f), encoding="utf-8")
    data = export_report_data(t, c, f)
    (out / "report_data.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

    if a.template:
        html = Path(a.template).read_text(encoding="utf-8")
        marker = "/*__REPORT_DATA__*/null"
        if marker not in html:
            sys.exit(f"Template has no {marker} placeholder.")
        (out / "report.html").write_text(html.replace(marker, json.dumps(data, separators=(",", ":"))), encoding="utf-8")
        print(f"[ok] wrote {out / 'report.html'}")

    if not a.no_charts:
        make_charts(t, c, f, out / "charts")
        print(f"[ok] wrote charts to {out / 'charts'}")

    print(f"[ok] wrote {out / 'findings.md'} and report_data.json")
    print(findings_markdown(f))


if __name__ == "__main__":
    main()
