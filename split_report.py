#!/usr/bin/env python3
"""Split the full report into two stand-alone pages from the same template and data.

  Part 1  Where Pakistani goods land            (US imports, New York port)
  Part 2  Where Pakistani New Yorkers live      (Census community estimates)

The pages share report_template.html, report_data.json and every chart function with the full report,
so a number can only be wrong in one place. Missing sections are simply skipped by the page script.

Usage:
  python3 split_report.py --template report_template.html --data output/report_data.json --out-dir output \
      [--full-url URL --part1-url URL --part2-url URL]
"""
import argparse, json, re
from pathlib import Path

MARK = "/*__REPORT_DATA__*/null"


def section(base, sid):
    m = re.search(r'  <section class="block" id="%s">.*?</section>\n' % sid, base, re.S)
    assert m, sid
    return m.group(0)


def drop_lines(block, *needles):
    keep = [ln for ln in block.split("\n") if not any(n in ln for n in needles)]
    return "\n".join(keep)


def guard_script(script):
    """Make every chart/control optional so one script serves the full report and both parts."""
    def rep(a, b):
        nonlocal script
        assert script.count(a) == 1, a[:60]
        script = script.replace(a, b)
    rep("  const $ = id => document.getElementById(id);\n",
        "  const $ = id => document.getElementById(id);\n  const has = id => !!document.getElementById(id);\n")
    for fn, el in [("renderA", "plotA"), ("renderB", "plotB"), ("renderC", "plotC"), ("renderD", "plotD"),
                   ("renderE", "plotE"), ("renderBoroughs", "tblBoro")]:
        rep("function %s() {" % fn, "function %s() {\n    if (!has(\"%s\")) return;" % (fn, el))
    rep("(function sparkline() {\n", "(function sparkline() {\n    if (!has(\"spark\")) return;\n")
    rep('  seg("segA"', '  if (has("segA")) seg("segA"')
    rep('  seg("segE"', '  if (has("segE")) seg("segE"')
    rep('  const yb = $("yearB");', '  if (has("yearB")) {\n  const yb = $("yearB");')
    rep('yb.addEventListener("input", () => { state.yearB = Number(yb.value); renderB(); });',
        'yb.addEventListener("input", () => { state.yearB = Number(yb.value); renderB(); });\n  }')
    rep('  fillSelect("fromC"', '  if (has("fromC")) {\n  fillSelect("fromC"')
    rep('$("yearD").addEventListener("change", e => { state.yearD = Number(e.target.value); renderD(); });',
        '$("yearD").addEventListener("change", e => { state.yearD = Number(e.target.value); renderD(); });\n  }')
    rep('  const boroughs = [', '  if (has("boroughE")) {\n  const boroughs = [')
    rep('$("allE").addEventListener("change", e => { state.allE = e.target.checked; renderE(); });',
        '$("allE").addEventListener("change", e => { state.allE = e.target.checked; renderE(); });\n  }')
    return script


def set_tiles(script, js):
    new, n = re.subn(r"const tiles = \[.*?\n  \];", lambda m: js, script, count=1, flags=re.S)
    assert n == 1
    return new


TILES_1 = """const tiles = [
    { num: fmt.bn2(F.us_last_bn), lab: "US imports from Pakistan in " + F.last_year + ", " + fmt.sgn0(F.us_growth_pct) + " since " + F.first_year + " (current dollars)" },
    { num: fmt.pct0(F.ny_share_last_pct), lab: "entered through New York, the top port in every year of the data" },
    { num: fmt.pct0(F.core_share_pct), lab: "of imports are knit apparel, woven apparel and home textiles" },
    { num: fmt.pct0(F.top_lq_group_ny_share_pct), lab: "of US " + F.top_lq_group.toLowerCase() + " imports from Pakistan entered through New York" }
  ];"""

TILES_2 = """const bs = DATA.boroughs.slice().sort((p, q) => q.share_pct - p.share_pct);
  const bq = DATA.boroughs.filter(b => b.borough === "Brooklyn" || b.borough === "Queens").reduce((s, b) => s + b.share_pct, 0);
  document.querySelectorAll("[data-bq]").forEach(el => { el.textContent = Math.round(bq) + "%"; });
  const tiles = [
    { num: "~" + fmt.round1000(F.nyc_pakistani), lab: "New Yorkers identify as Pakistani (±" + fmt.int(F.nyc_pakistani_moe) + ")" },
    { num: "~" + fmt.round1000(F.nyc_pakistan_born), lab: "were born in Pakistan (±" + fmt.int(F.nyc_pakistan_born_moe) + ")" },
    { num: fmt.pct0(bs[0].share_pct), lab: "of them live in " + bs[0].borough + ", the largest borough share" },
    { num: fmt.pct0(F.south_brooklyn_share_pct), lab: "live in four south Brooklyn areas" }
  ];"""

NAV = '<nav class="nav" aria-label="Sections"><div class="wrap">\n  <span class="brand">Policy East</span>\n  %s\n</div></nav>\n\n'


def pager(items):
    return '  <div class="pager">\n' + "\n".join(
        '    <a href="%s" target="_blank" rel="noopener"><span class="k">%s</span><span class="t">%s</span></a>' % i for i in items) + "\n  </div>\n"


def build(base, data_json, urls):
    head = base[:base.index("<nav")]
    script = guard_script(base[base.index("<script>\nconst DATA"):])
    footer = re.search(r"  <footer>.*?</footer>\n", base, re.S).group(0)
    sec = {k: section(base, k) for k in ["findings", "trade", "entry", "products", "community", "method"]}
    def finish(title, nav, body, tiles, spark):
        h = head.replace("<title>Pakistan and New York</title>", "<title>%s</title>" % title)
        s = set_tiles(script, tiles)
        html = h + nav + '<main class="wrap">\n' + body + footer + "</main>\n\n" + s
        return html.replace(MARK, data_json)

    # ---------------- Part 1 ----------------
    hero1 = """  <header class="hero">
    <div class="hero-top">
    <div style="display:flex;flex-direction:column;gap:18px;min-width:0">
    <p class="eyebrow">Policy East · Data analysis · Part 1 of 2</p>
    <h1>US-Pakistan Trade Analysis</h1>
    <p class="subhead">Where Pakistani Goods Land</p>
    <p class="lede">US imports from Pakistan reached <span data-f="us_last_bn" data-fmt="bn2"></span> in <span data-f="last_year" data-fmt="year"></span>. New York is the largest entry point for those goods. This part reads the Census trade data, shows what it says, and marks where it stops.</p>
    </div>
    <div class="spark" id="spark" aria-hidden="true"></div>
    </div>
    <div class="tiles" id="tiles"></div>
  </header>

  <aside class="keyfind" aria-label="Key finding">
    <p class="eyebrow">Key finding</p>
    <p class="big">New York handles <span class="hl" data-f="ny_share_last_pct" data-fmt="pct0"></span> of US imports from Pakistan, and ranked first in every year of the data.</p>
    <p class="why">That is <span data-f="ny_last_bn" data-fmt="bn2"></span> of <span data-f="us_last_bn" data-fmt="bn2"></span> in <span data-f="last_year" data-fmt="year"></span>. Its share is down from <span data-f="ny_share_first_pct" data-fmt="pct0"></span> in <span data-f="first_year" data-fmt="year"></span> because other ports grew faster, but it is still more than twice the size of the next port. The data records where goods entered, not who bought them.</p>
  </aside>

"""
    f1 = drop_lines(sec["findings"], "The community is concentrated.", "Many neighborhood estimates are weak.")
    limits1 = """  <section class="block" id="limits">
    <h2>What the data can and cannot say</h2>
    <div class="prose">
      <p>Customs district means port of entry, not final buyer. Goods can enter in New York and be sold anywhere. Figures are goods only and exclude services.</p>
      <p>Dollar values are nominal, in current dollars with no adjustment for inflation, so growth rates overstate real growth. The causes of the 2021–2022 jump and the 2023 fall are not analyzed. Tariff changes were not analyzed either.</p>
      <p>This part does not look at where Pakistani New Yorkers live, and nothing here shows that the community drives the trade that enters through the port. Part 2 covers the community.</p>
    </div>
  </section>
"""
    m1 = drop_lines(sec["method"], "<strong>Community.</strong>", "PUMA</dt>", "Margin of error</dt>",
                    'href="https://www.census.gov/data/developers/data-sets/acs-5year.html"')
    m1 = m1.replace("  </section>", '    <p class="small">Source: <a href="https://www.census.gov/data/developers/data-sets/international-trade.html" target="_blank" rel="noopener">Census International Trade API</a>.</p>\n  </section>')
    nav1 = NAV % ('<a href="#findings">Findings</a><a href="#trade">Trade over time</a><a href="#entry">Entry points</a>'
                  '<a href="#products">Products</a><a href="#limits">Limits</a><a href="#method">Method</a>')
    pg1 = pager([(urls["part2"], "Next · Part 2 of 2", "Where Pakistani New Yorkers live"), (urls["full"], "Both parts together", "Read the full report")])
    body1 = hero1 + f1 + sec["trade"] + sec["entry"] + sec["products"] + limits1 + m1 + pg1
    part1 = finish("Where Pakistani Goods Land", nav1, body1, TILES_1, True)

    # ---------------- Part 2 ----------------
    hero2 = """  <header class="hero">
    <div class="hero-top" style="grid-template-columns:minmax(0,1fr)">
    <div style="display:flex;flex-direction:column;gap:18px;min-width:0">
    <p class="eyebrow">Policy East · Data analysis · Part 2 of 2</p>
    <h1>US-Pakistan Trade Analysis</h1>
    <p class="subhead">Where Pakistani New Yorkers Live</p>
    <p class="lede">About <span data-f="nyc_pakistani" data-fmt="round1000"></span> New Yorkers identify as Pakistani. This part reads the Census American Community Survey, shows where they live, and marks how precise each estimate is.</p>
    </div>
    </div>
    <div class="tiles" id="tiles"></div>
  </header>

  <aside class="keyfind" aria-label="Key finding">
    <p class="eyebrow">Key finding</p>
    <p class="big">About <span class="hl" data-f="nyc_pakistani" data-fmt="round1000"></span> Pakistani New Yorkers live in the city, and <span class="hl" data-f="south_brooklyn_share_pct" data-fmt="pct0"></span> of them live in four south Brooklyn areas.</p>
    <p class="why">Brooklyn and Queens together hold <span data-bq></span> of the city's Pakistani residents. These are survey estimates with margins of error, and many neighborhood figures are weak, so read close rankings with care.</p>
  </aside>

"""
    f2 = """  <section class="block" id="findings">
    <h2>What we found</h2>
    <ol class="findings">
      <li><strong>The community is concentrated.</strong> About <span data-f="nyc_pakistani" data-fmt="int"></span> New Yorkers identify as Pakistani (margin of error ±<span data-f="nyc_pakistani_moe" data-fmt="int"></span>), which is <span data-f="pak_share_of_pop_pct" data-fmt="pct2"></span> of the city. <span data-f="south_brooklyn_share_pct" data-fmt="pct0"></span> of them live in four south Brooklyn areas.</li>
      <li><strong>Brooklyn and Queens hold most of them.</strong> Together they hold <span data-bq></span> of Pakistani New Yorkers. The borough table below gives each borough's share.</li>
      <li><strong>Many neighborhood estimates are weak.</strong> <span data-f="areas_low_reliability" data-fmt="int"></span> of <span data-f="areas_total" data-fmt="int"></span> areas are weak: <span data-f="areas_cv_ge30" data-fmt="int"></span> have a coefficient of variation of 30% or more and <span data-f="areas_zero_estimate" data-fmt="int"></span> show an estimate of zero.</li>
    </ol>
  </section>
"""
    limits2 = """  <section class="block" id="limits">
    <h2>What the data can and cannot say</h2>
    <div class="prose">
      <p>"Pakistani alone" excludes people who report more than one race or origin, so it is a floor for the community. Small-area estimates carry large margins of error, and ranks between close neighbors are approximate.</p>
      <p>The survey counts residents. It says nothing about trade, and nothing here shows that the community drives the goods that enter through the port. Part 1 covers the trade data.</p>
    </div>
  </section>
"""
    m2 = drop_lines(sec["method"], "<strong>Load and check.</strong>", "<strong>Trade.</strong>", "<strong>New York.</strong>",
                    "Imports for consumption</dt>", "HS chapter</dt>", "Location quotient</dt>",
                    'href="https://www.census.gov/data/developers/data-sets/international-trade.html"')
    m2 = m2.replace("    </dl>", "      <dt>Coefficient of variation</dt><dd>The standard error divided by the estimate. Under 30% is reasonably precise.</dd>\n    </dl>")
    m2 = m2.replace("  </section>", '    <p class="small">Source: <a href="https://www.census.gov/data/developers/data-sets/acs-5year.html" target="_blank" rel="noopener">American Community Survey 5-year data</a>, 2020–2024.</p>\n  </section>')
    nav2 = NAV % ('<a href="#findings">Findings</a><a href="#community">Community</a><a href="#limits">Limits</a><a href="#method">Method</a>')
    pg2 = pager([(urls["part1"], "Previous · Part 1 of 2", "Where Pakistani goods land"), (urls["full"], "Both parts together", "Read the full report")])
    body2 = hero2 + f2 + sec["community"] + limits2 + m2 + pg2
    part2 = finish("Where Pakistani New Yorkers Live", nav2, body2, TILES_2, False)
    return part1, part2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="report_template.html")
    ap.add_argument("--data", default="output/report_data.json")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--full-url", default="#"); ap.add_argument("--part1-url", default="#"); ap.add_argument("--part2-url", default="#")
    a = ap.parse_args()
    base = Path(a.template).read_text(encoding="utf-8")
    data = json.dumps(json.loads(Path(a.data).read_text(encoding="utf-8")), separators=(",", ":"))
    p1, p2 = build(base, data, {"full": a.full_url, "part1": a.part1_url, "part2": a.part2_url})
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    (out / "part1_where_goods_land.html").write_text(p1, encoding="utf-8")
    (out / "part2_where_pakistani_new_yorkers_live.html").write_text(p2, encoding="utf-8")
    print("[ok] wrote", out / "part1_where_goods_land.html", "and", out / "part2_where_pakistani_new_yorkers_live.html")


if __name__ == "__main__":
    main()
