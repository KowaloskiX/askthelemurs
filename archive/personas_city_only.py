"""Generate synthetic Kraków residents from GUS BDL aggregates.
Input:  data/krakow_demo.json  (built from BDL batches)
Output: out/personas.csv
Only marginals are real (age x sex per unit, city-level rates); joint structure
and interests are ASSUMPTIONS, clearly marked below.
"""
import json, numpy as np, pandas as pd, sys
rng = np.random.default_rng(42)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
D = json.load(open("data/krakow_demo.json"))

def parse_age(lbl):              # "25-29" -> (25,29), "85+" -> (85,95)
    lbl = lbl.replace(" ", "")
    if lbl.endswith("+"): a = int(lbl[:-1]); return a, a + 10
    a, b = lbl.split("-"); return int(a), int(b)

rows = []
for (unit, ag, sex), n in [((u, a, s), v) for u, d in D["units"].items()
                           for s in ("M", "K") for a, v in d["age_sex"][s].items()]:
    rows.append((unit, ag, sex, n))
cells = pd.DataFrame(rows, columns=["unit", "age_group", "sex", "n"])
cells = cells[cells.n > 0]
p = cells.n / cells.n.sum()
idx = rng.choice(len(cells), size=N, p=p)
P = cells.iloc[idx][["unit", "age_group", "sex"]].reset_index(drop=True)
P["age"] = [rng.integers(*parse_age(g)) + (1 if parse_age(g)[0]==parse_age(g)[1] else 0) for g in P.age_group]

C = D["city"]
avg_wage = C.get("avg_gross_wage", 9000)
unemp = C.get("unemployment_rate", 2.5) / 100
cars_pc = C.get("cars_per_1000", 600) / 1000

def status(age):
    if age < 19: return "uczeń"
    if age < 25: return "student" if rng.random() < 0.55 else ("pracuje" if rng.random() > unemp else "bezrobotny")
    if age < 65: return "pracuje" if rng.random() > unemp*1.0 else "bezrobotny"
    return "emeryt"
P["status"] = P.age.map(status)
# ASSUMPTION: lognormal income around GUS avg gross wage (net ~0.71), age curve
def income(r):
    if r.status in ("uczeń",): return 0
    base = {"student": 0.35, "pracuje": 1.0, "bezrobotny": 0.25, "emeryt": 0.55}[r.status]
    curve = 0.75 if r.age < 30 else (1.1 if r.age < 55 else 1.0)
    return round(avg_wage * 0.71 * base * curve * rng.lognormal(0, 0.35), -1)
P["net_income"] = P.apply(income, axis=1)
P["has_car"] = [(a >= 18) and (rng.random() < min(0.95, cars_pc * (1.2 if 30 <= a < 70 else 0.6))) for a in P.age]
# ASSUMPTION: music taste priors by age (placeholder until survey data)
TASTES = ["pop", "rock", "hip-hop", "elektronika", "klasyka/jazz", "disco polo"]
def taste(a):
    w = {"pop":3, "rock":2, "hip-hop":3 if a<35 else 0.5, "elektronika":2 if a<40 else 0.5,
         "klasyka/jazz":0.5 if a<40 else 2, "disco polo":1 if a<30 else 1.5}
    k = list(w); v = np.array([w[x] for x in k]); return rng.choice(k, p=v/v.sum())
P["fav_genre"] = P.age.map(taste)
P.insert(0, "id", range(1, len(P) + 1))
P.to_csv("out/personas.csv", index=False)
print(P.describe(include="all").T[["count","unique","top","mean"]].to_string())
