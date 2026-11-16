import pandas as pd, numpy as np, json, sys, re
D, J = sys.argv[1], sys.argv[2]
R = json.load(open(J)); F = R["findings"]
c = pd.read_csv(f"{D}/acs2024_nyc_pakistani_county.csv", dtype={"county":str})
p = pd.read_csv(f"{D}/acs2024_nyc_pakistani_puma.csv", dtype={"public use microdata area":str})
Z=1.645
print(c[["NAME","county","B02015_025E","B02015_025M","B05006_064E","B05006_064M","B01003_001E"]].to_string())
print("counties:", len(c), "| PUMAs:", len(p), "| sentinel/negative values:", int((c.select_dtypes('number')<0).sum().sum()), int((p.select_dtypes('number')<0).sum().sum()))
pak, pakm = c.B02015_025E.sum(), np.sqrt((c.B02015_025M**2).sum())
born, bornm = c.B05006_064E.sum(), np.sqrt((c.B05006_064M**2).sum())
pop = c.B01003_001E.sum()
def chk(n, a, b, tol=0.51):
    print(("OK   " if abs(a-b)<=tol else "DIFF ")+f"{n:30s} mine={a:,.3f} report={b:,.3f}")
chk("nyc_pakistani", pak, F["nyc_pakistani"]); chk("nyc_pakistani_moe", pakm, F["nyc_pakistani_moe"])
chk("nyc_pakistan_born", born, F["nyc_pakistan_born"]); chk("nyc_pakistan_born_moe", bornm, F["nyc_pakistan_born_moe"])
chk("pak_share_of_pop_pct", pak/pop*100, F["pak_share_of_pop_pct"], 0.0006)
print("   NYC population (county sum):", f"{pop:,.0f}")
# PUMA vs county consistency
print("\nPUMA sums vs county sums:  pop", p.B01003_001E.sum(), c.B01003_001E.sum(), "| pakistani", p.B02015_025E.sum(), c.B02015_025E.sum(), "| born", p.B05006_064E.sum(), c.B05006_064E.sum())
def boro(n):
    for k,v in {"Bronx":"Bronx","Brooklyn":"Brooklyn","Manhattan":"Manhattan","Queens":"Queens","Staten Island":"Staten Island"}.items():
        if k in n: return v
p["boro"] = p.NAME.map(boro); print("borough assignment nulls:", p.boro.isna().sum(), p.boro.value_counts().to_dict())
cb = {"005":"Bronx","047":"Brooklyn","061":"Manhattan","081":"Queens","085":"Staten Island"}
print("PUMA borough sums vs county (Pakistani):", {b:(int(p[p.boro==b].B02015_025E.sum()), int(c[c.county==k].B02015_025E.sum())) for k,b in cb.items()})
# boroughs block
B = pd.DataFrame(R["boroughs"]).set_index("borough")
for k,b in cb.items():
    r = c[c.county==k].iloc[0]
    assert abs(B.loc[b,"pak"]-r.B02015_025E)<0.5 and abs(B.loc[b,"pak_moe"]-r.B02015_025M)<0.5 and abs(B.loc[b,"born"]-r.B05006_064E)<0.5 and B.loc[b,"pop"]==r.B01003_001E, b
    assert abs(B.loc[b,"share_pct"]-r.B02015_025E/pak*100)<0.06, (b, B.loc[b,"share_pct"], r.B02015_025E/pak*100)
print("boroughs block matches raw counties; shares:", {b: round(B.loc[b,'share_pct'],1) for b in B.index})
# south Brooklyn
sb = p[p.NAME.str.contains(r"Brooklyn Community District (?:12|13|14|15)--")]
print("\nSouth Brooklyn PUMAs:", [re.sub(r" PUMA.*","",n) for n in sb.NAME])
sbe, sbm = sb.B02015_025E.sum(), np.sqrt((sb.B02015_025M**2).sum())
chk("south_brooklyn", sbe, F["south_brooklyn"]); chk("south_brooklyn_moe", sbm, F["south_brooklyn_moe"])
chk("south_brooklyn_share_pct", sbe/pak*100, F["south_brooklyn_share_pct"], 0.06)
print("   Brooklyn total:", int(c[c.county=='047'].B02015_025E.iloc[0]), "| south Brooklyn share of Brooklyn:", round(sbe/c[c.county=='047'].B02015_025E.iloc[0]*100,1))
# reliability
cv = (p.B02015_025M/Z)/p.B02015_025E.replace(0, np.nan)
zero = int((p.B02015_025E==0).sum())
low_incl_zero = int(((cv>=0.3)|cv.isna()).sum()); low_strict = int((cv>=0.3).sum()); gt30 = int((cv>0.3).sum())
print(f"\nCV>=30%: {low_strict} | CV>30%: {gt30} | zero-estimate areas: {zero} | CV>=30 or undefined: {low_incl_zero} | report low: {F['areas_low_reliability']} of {F['areas_total']}")
print("MOE >= estimate:", int((p.B02015_025M>=p.B02015_025E).sum()), "| report:", F["areas_moe_ge_estimate"])
print("CV bands: high(<15)", int((cv<0.15).sum()), "medium(15-30)", int(((cv>=0.15)&(cv<0.3)).sum()), "low(>=30)", low_strict)
# pumas block
P = pd.DataFrame(R["pumas"])
print("pumas block rows:", len(P), "| pak sum:", P.pak.sum(), "| max cv diff:", None)
m = P.merge(p.assign(area=p.NAME.map(lambda n: None)), left_index=True, right_index=True, how="left") if False else None
rawsorted = p.sort_values("B02015_025E", ascending=False)
print("top 6 areas raw:", [(re.sub(r' PUMA.*','',n)[-45:], int(e), int(m_)) for n,e,m_ in zip(rawsorted.NAME, rawsorted.B02015_025E, rawsorted.B02015_025M)][:6])
print("top 6 areas report:", P.sort_values("pak", ascending=False).head(6)[["area","borough","pak","pak_moe"]].values.tolist())
# whole pumas json vs raw by sorted multiset
a = sorted(zip(P.pak, P.pak_moe, P.born, P.born_moe, P["pop"])); b = sorted(zip(p.B02015_025E, p.B02015_025M, p.B05006_064E, p.B05006_064M, p.B01003_001E))
print("pumas json == raw (multiset of rows):", all(np.allclose(x,y) for x,y in zip(a,b)), len(a)==len(b))
print("south_brooklyn flag count in json:", int(P.south_brooklyn.sum()), "| flagged areas:", P[P.south_brooklyn].area.tolist())
# overlapping interval claim: neighbours overlap
s = P.sort_values("pak", ascending=False).head(12).reset_index(drop=True)
ov = sum((s.pak[i]-s.pak_moe[i]) < (s.pak[i+1]+s.pak_moe[i+1]) for i in range(len(s)-1))
print("top-12: adjacent bars whose MOE intervals overlap:", ov, "of", len(s)-1)
# top 12 precision
cvs = (s.pak_moe/Z)/s.pak; print("top-12 CV% :", (cvs*100).round(0).tolist())
