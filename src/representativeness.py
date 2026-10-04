"""Is the synthetic population representative of Kraków? Read-only check of out/personas.csv (and out/attitudes.csv)
against GUS / PKW / CEPiK tables, plus the statistical precision that 20k personas give for the city, a district and a
small group. Prints a table; nothing is regenerated.
Usage: python3 src/representativeness.py
"""
import json
import numpy as np, pandas as pd

OUT = "poza Krakowem"
P = pd.read_csv("out/personas.csv"); DS = pd.read_csv("out/districts.csv", index_col="district")
P["w"] = P.district.map(DS.people / DS.personas)
K = P[(P.district != OUT) & (P.origin == "mieszkaniec")]          # residents of the city (census population)
X = json.load(open("data/raw/krakow_gus_extra.json")); D = json.load(open("data/raw/krakow_demo.json"))
v = lambda g, k: X[g][k]["value"]
share = lambda m, base=K: float(base.w[m].sum() / base.w.sum())
rows = []


def add(group, name, model, gus, src):
    rows.append({"grupa": group, "wskaźnik": name, "persony": model, "GUS": gus, "źródło": src})


# age x sex (BDL 2025)
AS = D["units"]["Kraków"]["age_sex"]; tot = sum(sum(x.values()) for x in AS.values())
bands = [(0, 14), (15, 24), (25, 34), (35, 49), (50, 64), (65, 120)]
def gus_band(lo, hi):
    s = 0
    for sex in AS:
        for k, n in AS[sex].items():
            a = int(k.split("-")[0].replace("+", "")); s += n if lo <= a <= hi else 0
    return s / tot
for lo, hi in bands:
    add("wiek", f"{lo}–{hi if hi < 120 else '+'}", share((K.age >= lo) & (K.age <= hi)), gus_band(lo, hi), "BDL 2025")
add("płeć", "kobiety", share(K.sex == "K"), sum(AS["K"].values()) / tot, "BDL 2025")
# education 13+ (NSP 2021)
ed = v("education", "edu_by_sex_13plus")["total"]; base = K[K.age >= 13]; known = ed["total"] - ed["unknown"]
add("wykształcenie 13+", "wyższe", share(base.education == "wyższe", base), ed["higher"] / known, "NSP 2021")
add("wykształcenie 13+", "średnie i policealne", share(base.education == "średnie", base), ed["secondary_postsec_total"] / known, "NSP 2021")
add("wykształcenie 13+", "zasadnicze zawodowe", share(base.education == "zasadnicze zawodowe", base), ed["basic_vocational"] / known, "NSP 2021")
# households by size (NSP 2021): people living alone / all people in households
hs = v("households", "households_by_size")
add("gospodarstwa", "mieszka sam(a), % osób", share(K.household == "mieszka sam(a)"), hs["1p"] / hs["persons_in_households"], "NSP 2021")
# employment (NSP 2021)
ea = v("labour", "economic_activity_15plus_by_sex")["total"]; b15 = K[K.age >= 15]
add("praca", "wskaźnik zatrudnienia 15+", share(b15.employed.fillna(False).astype(bool), b15), ea["employed"] / ea["population_15plus"], "NSP 2021")
# cars (CEPiK via BDL 2025): fuel mix and age of personas' cars
cf = v("transport", "cars_by_fuel"); C = P[P.has_car & (P.district != OUT)]
for k, pl in [("petrol", "benzyna"), ("diesel", "diesel"), ("lpg", "LPG")]:
    add("auta (paliwo)", pl, share(C.car_fuel == pl, C), cf[k] / cf["total"], "BDL/CEPiK 2025")
ca = v("transport", "cars_by_age"); old = sum(ca[k] for k in ("16-20", "21-25", "26-30", "31+"))
add("auta (wiek)", "starsze niż 15 lat", share(C.car_age > 15, C), old / ca["total"], "BDL/CEPiK 2025")
# students
st = v("students", "he_students_graduates")["students_total"]
add("studenci", "studiujący (osoby)", float((P.studying.fillna(False).astype(bool) | (P.status == "student")).mul(P.w).sum() / 0.85), st, "BDL 2024/25 (85% mieszka w modelu)")
# politics (PKW Sejm 2023), adults in Kraków
try:
    A = pd.read_csv("out/attitudes.csv").merge(P[["id", "w"]], on="id")
    pk = json.load(open("data/raw/pkw_krakow.json"))["district_aggregates"]["sejm2023"]["by_district"]
    tv = {}; [tv.__setitem__(c, tv.get(c, 0) + n) for d in pk.values() for c, n in d["votes"].items()]
    m = {"KO": "KO", "PiS": "PiS", "Trzecia Droga": "TD", "Lewica": "Lewica", "Konfederacja": "Konf"}
    for c, k in m.items(): add("poglądy (Sejm 2023)", k, float(A.w[A.vote == k].sum() / A.w.sum()), tv[c] / sum(tv.values()), "PKW 2023")
except FileNotFoundError: pass

R = pd.DataFrame(rows)
R["różnica"] = [(f"{(a - b) * 100:+.1f} pkt" if b < 1 else f"{(a / b - 1) * 100:+.0f}%") for a, b in zip(R.persony, R.GUS)]
fmt = lambda x: f"{x:.1%}" if x < 1 else f"{x:,.0f}".replace(",", " ")
print(R.assign(persony=R.persony.map(fmt), GUS=R.GUS.map(fmt)).to_string(index=False))

# precision: 95% margin for a yes-share near 50%, by sample (personas), with design effect ~1 (ASSUMPTION)
moe = lambda n: 1.96 * np.sqrt(0.25 / n) * 100
nd = K.groupby("district").size()
print(f"\nPrecyzja (95%, odpowiedź ~50%): miasto n={len(K)}: ±{moe(len(K)):.1f} pkt; dzielnica n={nd.min()}–{nd.max()}: ±{moe(nd.max()):.1f}–{moe(nd.min()):.1f} pkt; "
      f"grupa 4% dorosłych (np. stare diesle) n≈{int(0.04 * len(K))}: ±{moe(0.04 * len(K)):.1f} pkt")
print(f"Najmniejsze dzielnice (persony): " + ", ".join(f"{d} {n}" for d, n in nd.sort_values().head(3).items()))
