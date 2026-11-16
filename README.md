# Pakistan and New York

Two questions, one Census-based analysis, for Policy East:

1. **Where do Pakistani goods land?** US imports from Pakistan by year, customs district and product, with a focus on the New York City district.
2. **Where do Pakistani New Yorkers live?** Census American Community Survey (ACS) estimates by borough and neighborhood area, with margins of error.

**Author:** Mehreen Ali Gillani, Data Scientist, City University of New York

**Read the report:** open `docs/index.html` (full report), `docs/part1_where_goods_land.html` (Part 1) or `docs/part2_where_pakistani_new_yorkers_live.html` (Part 2). With GitHub Pages turned on, they are served at `https://mehreengillani.github.io/pakistan-new-york-/`.

## Main findings

- US imports for consumption from Pakistan rose from $3.49bn (2010) to $5.47bn (2025), +57% in current dollars (not adjusted for inflation).
- New York is the largest entry point in every year: $1.54bn, 28% of 2025 imports. Its share is down from 32% in 2010.
- Knit apparel, woven apparel and home textiles are 73% of 2025 imports. Woven apparel grew fastest of the large groups (+164%).
- New York takes a larger share of rice and cereals (64%) and woven apparel (44%) than of imports overall (28%).
- About 49,138 New Yorkers (±3,593) identify as Pakistani alone. Brooklyn and Queens hold 79% of them, and four south Brooklyn areas hold 27%.
- 37 of 55 neighborhood areas are weak estimates, so close neighborhood rankings are not firm.

Customs district means where goods entered the US, not who bought them. Nothing here shows that the community drives the trade.

## Repository layout

```
fetch_census_data.py   pulls trade and ACS data from the Census APIs into data/
analysis.py            runs the checks and analysis, writes charts, tables, findings and report data
split_report.py        builds the two-part pages from the same template and data
report_template.html   page template for the interactive report
data/                  the Census CSVs used in the report
output/                charts (PNG), tables (CSV), findings.md, report_data.json
docs/                  the finished HTML pages (GitHub Pages)
audit/                 independent scripts that recompute the figures from the raw CSVs
```

## Reproduce it

```bash
pip install -r requirements.txt

# 1. Get a free key at https://api.census.gov/data/key_signup.html, then set it as an environment variable.
#    Never put the key in a file you commit.
export CENSUS_API_KEY="your_key_here"      # Windows PowerShell: $env:CENSUS_API_KEY="..."

# 2. Download the data (skip this step to use the CSVs already in data/)
python3 fetch_census_data.py trade
python3 fetch_census_data.py acs

# 3. Analyze, check and build the report
python3 analysis.py --data-dir data --out-dir output --template report_template.html

# 4. Build the two-part pages
python3 split_report.py --template report_template.html --data output/report_data.json --out-dir docs

# 5. Optional: independent audit of every figure
python3 audit/audit1_raw_integrity.py data
python3 audit/audit2_trade_figures.py data output/report_data.json
python3 audit/audit3_community_figures.py data output/report_data.json
```

`analysis.py` stops if district rows do not add up to the Census total rows. An early version double counted the "TOTAL FOR ALL DISTRICTS" rows, and this check exists to prevent that.

## Data

- **Trade:** U.S. Census Bureau, International Trade API, imports by HS 2-digit chapter and customs district, Pakistan (country code 5350), imports for consumption, December year-to-date for 2010–2025.
- **Community:** U.S. Census Bureau, American Community Survey 5-year estimates 2020–2024, tables B02015 (Pakistani alone), B05006 (place of birth) and B01003 (population), by county and by PUMA for the five NYC counties. Margins of error are at 90% confidence.
- Data was pulled in November 2026. The Census can revise figures, so rerun step 2 for current numbers.

This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau.
