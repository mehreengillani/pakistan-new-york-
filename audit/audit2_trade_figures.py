import pandas as pd, numpy as np, json, sys
D, J = sys.argv[1], sys.argv[2]
raw = pd.read_csv(f"{D}/pakistan_imports_all_districts.csv", dtype=str)
raw = raw.loc[:, ~raw.columns.duplicated()]
raw["v"] = pd.to_numeric(raw.consumption_imports_usd); raw["year"]=pd.to_numeric(raw.year)
d = raw[~raw.district_name.str.upper().str.startswith("TOTAL")].copy()
m = {"61":"Apparel (knit)","62":"Apparel (woven)","63":"Home textiles & made-ups","52":"Cotton, yarn & fabric","41":"Leather & leather goods","42":"Leather & leather goods","10":"Rice & cereals","95":"Sports goods & toys","90":"Medical & surgical instruments"}
d["g"] = d.hs2.map(m).fillna("Other products")
F = json.load(open(J))["findings"]; R = json.load(open(J))
bad = []
def chk(name, mine, rep, tol=0.0006):
    ok = abs(mine-rep) <= max(tol*abs(rep), 0.0006) if isinstance(rep,(int,float)) and not isinstance(rep,bool) else mine==rep
    print(("OK   " if ok else "DIFF ")+f"{name:34s} mine={mine!r:>14}  report={rep!r}")
    if not ok: bad.append(name)

us = d.groupby("year").v.sum()
NYN = "NEW YORK CITY, NY"
ny = d[d.district_name==NYN].groupby("year").v.sum().reindex(us.index).fillna(0)
f, l = us.index.min(), us.index.max()
chk("us_first_bn", us[f]/1e9, F["us_first_bn"]); chk("us_last_bn", us[l]/1e9, F["us_last_bn"])
chk("us_growth_pct", (us[l]/us[f]-1)*100, F["us_growth_pct"])
chk("cagr_pct", ((us[l]/us[f])**(1/(l-f))-1)*100, F["cagr_pct"])
pk = us.idxmax(); chk("peak_year", int(pk), F["peak_year"]); chk("peak_bn", us[pk]/1e9, F["peak_bn"])
chk("drop_after_peak_pct", (us[pk+1]/us[pk]-1)*100, F["drop_after_peak_pct"])
chk("ny_last_bn", ny[l]/1e9, F["ny_last_bn"])
sh = ny/us*100
chk("ny_share_last_pct", sh[l], F["ny_share_last_pct"]); chk("ny_share_first_pct", sh[f], F["ny_share_first_pct"])
chk("ny_share_min_pct", sh.min(), F["ny_share_min_pct"]); chk("ny_share_min_year", int(sh.idxmin()), F["ny_share_min_year"])
chk("ny_share_max_pct", sh.max(), F["ny_share_max_pct"]); chk("ny_share_max_year", int(sh.idxmax()), F["ny_share_max_year"])
chk("ny_growth_pct", (ny[l]/ny[f]-1)*100, F["ny_growth_pct"])
dy = d.groupby(["year","district_name"]).v.sum().unstack().fillna(0)
rank1 = (dy.idxmax(axis=1)==NYN)
chk("ny_rank_always_first", bool(rank1.all()), F["ny_rank_always_first"])
rk = dy.rank(axis=1, ascending=False, method="min")[NYN]
top = dy.loc[l].sort_values(ascending=False)
print("   2025 top 5 districts:", [(k, round(v/1e6), f"{v/us[l]*100:.1f}%") for k,v in top.head(5).items()])
chk("second_district_share_pct", top.iloc[1]/us[l]*100, F["second_district_share_pct"]); chk("third_district_share_pct", top.iloc[2]/us[l]*100, F["third_district_share_pct"])
print("   report names:", F["second_district"], "|", F["third_district"], " mine:", top.index[1], "|", top.index[2])
hhi = ((dy.div(dy.sum(axis=1), axis=0)*100)**2).sum(axis=1)
chk("entry_hhi_first", hhi[f], F["entry_hhi_first"]); chk("entry_hhi_last", hhi[l], F["entry_hhi_last"])
print("   HHI by year:", hhi.round(0).astype(int).to_dict())
gy = d.groupby(["year","g"]).v.sum().unstack().fillna(0)
core = gy.loc[l, ["Apparel (knit)","Apparel (woven)","Home textiles & made-ups"]].sum()/us[l]*100
chk("core_share_pct", core, F["core_share_pct"])
print("   group 2025 ($m) & growth since 2010:")
for g in gy.columns:
    print(f"     {g:32s} {gy.loc[l,g]/1e6:8.0f}  {((gy.loc[l,g]/gy.loc[f,g]-1)*100 if gy.loc[f,g]>0 else float('nan')):+7.0f}%   2010: {gy.loc[f,g]/1e6:.0f}")
big = gy.columns[gy.loc[l]>1e9]
fg = ((gy.loc[l,big]/gy.loc[f,big]-1)*100).sort_values(ascending=False)
chk("fastest_big_growth_pct", fg.iloc[0], F["fastest_big_growth_pct"]); print("   fastest big group:", fg.index[0], "| report:", F["fastest_big_group"], "| groups >$1bn:", list(big))
ng = d[d.district_name==NYN].groupby(["year","g"]).v.sum().unstack().reindex(gy.index).fillna(0)
lq = (ng.loc[l]/gy.loc[l]) / (ny[l]/us[l])
lq = lq.sort_values(ascending=False)
print("   LQ 2025:", {k: round(v,2) for k,v in lq.items()})
chk("top_lq", lq.iloc[0], F["top_lq"]); chk("second_lq", lq.iloc[1], F["second_lq"])
print("   top two LQ groups:", lq.index[0], "|", lq.index[1], " report:", F["top_lq_group"], "|", F["second_lq_group"])
chk("top_lq_group_ny_share_pct", ng.loc[l,lq.index[0]]/gy.loc[l,lq.index[0]]*100, F["top_lq_group_ny_share_pct"])
chk("second_lq_group_ny_share_pct", ng.loc[l,lq.index[1]]/gy.loc[l,lq.index[1]]*100, F["second_lq_group_ny_share_pct"])
print("   rice: NY $m", ng.loc[l,'Rice & cereals']/1e6, " national $m", gy.loc[l,'Rice & cereals']/1e6)
# yearly table vs json
Y = pd.DataFrame(R["yearly"]).set_index("year")
print("\nyearly table max abs diffs: us", (Y.us_usd_m - us/1e6).abs().max(), "ny", (Y.ny_usd_m-ny/1e6).abs().max(), "share", (Y.ny_share_pct-sh).abs().max(), "hhi", (Y.entry_hhi-hhi).abs().max(), "rank", (Y.ny_rank-rk).abs().max())
# districts block and product blocks
Dj = {x["name"]:x["usd_m"] for x in R["districts"]}
mx = 0
for name, arr in Dj.items():
    # find matching district name (json may be title-cased)
    cand = [c for c in dy.columns if c.upper()==name.upper() or c.title().replace(", Ny",", NY")==name or c.lower()==name.lower()]
    if not cand: print("   no match for district", name); continue
    mx = max(mx, np.abs(np.array(arr) - dy[cand[0]].reindex(range(f,l+1)).values/1e6).max())
print("districts block: districts in json:", len(Dj), "| in raw:", dy.shape[1], "| max abs diff $m:", mx)
mx1 = max(np.abs(np.array(R["group_national_usd_m"][g]) - gy[g].values/1e6).max() for g in R["group_national_usd_m"])
mx2 = max(np.abs(np.array(R["group_ny_usd_m"][g]) - ng[g].values/1e6).max() for g in R["group_ny_usd_m"])
print("group national max diff $m:", mx1, "| group NY max diff $m:", mx2, "| groups:", sorted(R["group_national_usd_m"]))
print("\nNOT MATCHING:", bad or "none")
# claims in prose
print("\nProse checks:")
print("  2021 vs 2020, 2022 vs 2021, 2023 vs 2022, 2024 vs 2023, 2025 vs 2024 growth %:", {y: round((us[y]/us[y-1]-1)*100,1) for y in range(2021,2026)})
print("  2025 above 2023?", us[2025]>us[2023], "| 2025 below peak?", us[2025]<us[2022])
print("  NY 2025 vs 2024 growth:", round((ny[2025]/ny[2024]-1)*100,1))
sm = gy.loc[l]/gy.loc[f]; print("  groups with growth > woven apparel (any size):", [(g, round((v-1)*100)) for g,v in sm.items() if (v-1)*100 > fg.iloc[0] and gy.loc[l,g]<=1e9])
print("  NY share min/max by year:", sh.round(1).to_dict())
print("  hhi lower in last vs first:", hhi[l] < hhi[f])
print("  NY rank by year:", rk.astype(int).to_dict())
