import pandas as pd, numpy as np, sys
D = sys.argv[1]
raw = pd.read_csv(f"{D}/pakistan_imports_all_districts.csv", dtype=str)
print("columns:", list(raw.columns))
raw = raw.loc[:, ~raw.columns.duplicated()]
for c in ["general_imports_usd","consumption_imports_usd","year","MONTH"]:
    raw[c] = pd.to_numeric(raw[c])
print("MONTH values:", sorted(raw.MONTH.unique()), "| years:", sorted(raw.year.unique()))
print("YEAR==year:", (pd.to_numeric(raw.YEAR)==raw.year).all(), "| countries:", raw.country.unique(), "| levels:", raw.COMM_LVL.unique())
print("null counts:", raw[["consumption_imports_usd","district_name","hs2"]].isna().sum().to_dict())
print("negative consumption rows:", (raw.consumption_imports_usd<0).sum())
tot = raw[raw.district_name.str.upper().str.startswith("TOTAL")]
dis = raw[~raw.district_name.str.upper().str.startswith("TOTAL")]
print("dup keys among districts:", dis.duplicated(["year","hs2","district_code"]).sum(), "| dup keys among total rows:", tot.duplicated(["year","hs2"]).sum())
# districts vs total row, by year x product
a = dis.groupby(["year","hs2"]).consumption_imports_usd.sum()
b = tot.set_index(["year","hs2"]).consumption_imports_usd
diff = (a.reindex(b.index).fillna(0) - b)
print("year x HS2 cells:", len(b), "| cells where districts != total row:", (diff!=0).sum(), "| max abs diff:", diff.abs().max())
us = dis.groupby("year").consumption_imports_usd.sum()/1e6
ustot = tot.groupby("year").consumption_imports_usd.sum()/1e6
print("\nUS imports for consumption ($m) districts-sum | total-row-sum")
print(pd.DataFrame({"districts":us.round(1),"totalrow":ustot.round(1)}).T.to_string())
# number of HS2 chapters per year
print("HS2 chapters per year:", dis.groupby("year").hs2.nunique().to_dict())
# category mapping check
m = {"61":"Apparel (knit)","62":"Apparel (woven)","63":"Home textiles & made-ups","52":"Cotton, yarn & fabric","41":"Leather & leather goods","42":"Leather & leather goods","10":"Rice & cereals","95":"Sports goods & toys","90":"Medical & surgical instruments"}
exp = dis.hs2.map(m).fillna("Other products")
print("category column matches my own mapping (file uses 'Other'?):", dis.category.unique())
mism = dis[(exp!=dis.category) & ~((exp=="Other products")&(dis.category.isin(["Other","Other products"])))]
print("category mismatches:", len(mism), mism[["hs2","category"]].drop_duplicates().head(10).to_dict("records"))
