"""Synthetic residents of Kraków (+ suburbs in the grid bbox) and non-resident students, built from GUS data.
Inputs (data/raw/):
  krakow_grid_nsp2021.json   NSP 2021 1 km grid: residents, sex, 3 age bands
  krakow_grid_extra.json     Census 2021 grid (Eurostat, GUS data): employed, place of birth, moved in last year
  krakow_demo.json           BDL 2025: 5-year age x sex, wage, unemployment, cars
  krakow_gus_extra.json      BDL: education x age, employment x age x sex, households, students, wages by sex...
  gus_leisure_rates.json     GUS surveys: culture 2024, sport 2025, household budgets 2025 (used in src/bio.py)
  ../geo/krakow_districts.geojson
Output: out/personas.csv, out/grid_cells.csv, out/districts.csv
Pipeline per persona: place, age, sex (grid + BDL) -> education (NSP 2021 by age, sex-tilted) -> employment
(NSP 2021 by age x sex, calibrated per 1 km cell to Census EMP) -> status -> income (BDL wage median/mean by sex)
-> household (NSP 2021 totals) -> car -> leisure & sport (GUS surveys, src/bio.py).
Every persona represents the same number of people. Validation table printed at the end.
"""
import json, sys, numpy as np, pandas as pd
from pyproj import Transformer
from shapely.geometry import shape
from shapely import contains_xy
sys.path.insert(0, "src"); import bio
rng = np.random.default_rng(42)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
OUTSIDE = "poza Krakowem"
G = json.load(open("data/raw/krakow_grid_nsp2021.json")); D = json.load(open("data/raw/krakow_demo.json"))
X = json.load(open("data/raw/krakow_gus_extra.json")); GX = json.load(open("data/raw/krakow_grid_extra.json"))
GEO = json.load(open("data/geo/krakow_districts.geojson"))["features"]
DIST = [f for f in GEO if f["properties"]["kind"] == "district"]; CITY = shape(next(f for f in GEO if f["properties"]["kind"] == "city")["geometry"])
val = lambda t, n: X[t][n]["value"]
g = pd.DataFrame(G["rows"], columns=G["columns"])
# GUS suppresses small values (statistical confidentiality): fill NaNs with the remainder split evenly
for cols in (["male", "female"], ["age_0_14", "age_15_64", "age_65p"]):
    rem = (g.res - g[cols].sum(axis=1, skipna=True)).clip(lower=0)
    nmiss = g[cols].isna().sum(axis=1).replace(0, 1)
    for c in cols: g[c] = g[c].fillna(rem / nmiss)
gx = pd.DataFrame(GX["rows"], columns=GX["columns"])[["E_km", "N_km", "employed", "born_pl", "res1y_moved_within_pl"]]
g = g.merge(gx, on=["E_km", "N_km"], how="left")
tr = Transformer.from_crs(3035, 4326, always_xy=True); tr_inv = Transformer.from_crs(4326, 3035, always_xy=True)
g["lon"], g["lat"] = tr.transform((g.E_km + 0.5) * 1000, (g.N_km + 0.5) * 1000)
g["cell"] = g.E_km.astype(int).astype(str) + "_" + g.N_km.astype(int).astype(str)
cell_of = {(int(e), int(n)): i for i, (e, n) in enumerate(zip(g.E_km, g.N_km))}
g["in_city"] = contains_xy(CITY, g.lon, g.lat)

def district_of(lon, lat):
    out = np.full(len(lon), OUTSIDE, dtype=object)
    for f in DIST: out[contains_xy(shape(f["geometry"]), lon, lat)] = f["properties"]["name"]
    return out

def shift(p, k):  # odds shift, vectorised
    p = np.clip(p, 1e-6, 1 - 1e-6); o = p / (1 - p) * k; return o / (1 + o)

# ======================= 1. residents: place, age, sex
AS = D["units"]["Kraków"]["age_sex"]
def band(lbl): a = 85 if lbl == "85+" else int(lbl.split("-")[0]); return "0_14" if a < 15 else ("15_64" if a < 65 else "65p")
groups = list(AS["M"])
# students: total from BDL (137 784 in 2024/25, by seat of the university), see section 3
STUDENTS_TOTAL = val("students", "he_students_graduates")["students_total"]
COMMUTE_SHARE = 0.15   # ASSUMPTION: share of students living outside the grid bbox (daily commuters from Małopolska)
pop_res = g.res.sum()
# first pass: residents only; students are added after we know how many residents already study
n_res = int(round(N * pop_res / (pop_res + 0.6 * STUDENTS_TOTAL)))   # provisional split, corrected below
ci = rng.choice(len(g), size=n_res, p=g.res / g.res.sum())
lon, lat = tr.transform((g.E_km.values[ci] + rng.random(n_res)) * 1000, (g.N_km.values[ci] + rng.random(n_res)) * 1000)
rows = []
for k, i in enumerate(ci):
    c = g.iloc[i]
    b = rng.choice(["0_14", "15_64", "65p"], p=np.array([c.age_0_14, c.age_15_64, c.age_65p]) / max(1, c.age_0_14 + c.age_15_64 + c.age_65p))
    sex = "M" if rng.random() < c.male / max(1, c.male + c.female) else "K"
    cand = [gr for gr in groups if band(gr) == b]; w = np.array([AS[sex][gr] for gr in cand], float)
    gr = rng.choice(cand, p=w / w.sum()); lo, hi = (85, 95) if gr == "85+" else map(int, gr.split("-"))
    born_abroad_p = 1 - c.born_pl / c.res if c.born_pl == c.born_pl and c.res else 0.04
    rows.append(dict(ci=i, cell=c.cell, lat=round(lat[k], 5), lon=round(lon[k], 5), sex=sex, age=int(rng.integers(lo, hi + 1)),
                     origin="mieszkaniec", born_abroad=bool(rng.random() < born_abroad_p)))
P = pd.DataFrame(rows)

# ======================= 2. education (13+): NSP 2021 by age (powiat), tilted by sex (gmina)
E_AGE = val("education", "edu_by_age_13plus"); E_SEX = val("education", "edu_by_sex_13plus")
EDU = ["wyższe", "średnie", "zasadnicze zawodowe", "podstawowe"]
def edu_dist(d):
    v = np.array([d["higher"], d["secondary_postsec_total"], d["basic_vocational"], d["lower_secondary_or_primary"] + d["below_primary_or_none"]], float)
    return v / v.sum()   # 'unknown' dropped
def e_age(a):
    if a <= 19: return "<=19"
    if a >= 65: return "65+"
    lo = 20 + (a - 20) // 5 * 5; return f"{lo}-{lo + 4}"
# ASSUMPTION: sex changes only the odds of higher education, same factor in every age group
OR_SEX = {s: (E_SEX[k]["higher"] / (E_SEX[k]["total"] - E_SEX[k]["higher"])) / (E_SEX["total"]["higher"] / (E_SEX["total"]["total"] - E_SEX["total"]["higher"]))
          for s, k in (("M", "M"), ("K", "F"))}
def sample_edu(a, s):
    if a < 13: return None
    p = edu_dist(E_AGE[e_age(a)]); h = shift(p[0], OR_SEX[s]); rest = p[1:] / p[1:].sum() * (1 - h)
    return rng.choice(EDU, p=np.r_[h, rest] / (h + rest.sum()))
P["education"] = [sample_edu(a, s) for a, s in zip(P.age, P.sex)]

# ======================= 3. employment: NSP 2021 by age x sex, calibrated per cell to Census EMP
EMP = val("labour", "employed_by_age_sex")
pop_band = {}
for s, sk in (("M", "M"), ("K", "F")):
    for lbl in groups:
        a = 85 if lbl == "85+" else int(lbl.split("-")[0])
        if a < 15: continue
        b = "15-24" if a < 25 else "25-34" if a < 35 else "35-44" if a < 45 else "45-54" if a < 55 else "55-64" if a < 65 else "65+"
        pop_band[(s, b)] = pop_band.get((s, b), 0) + AS[s][lbl]
def emp_band(a): return None if a < 15 else "15-24" if a < 25 else "25-34" if a < 35 else "35-44" if a < 45 else "45-54" if a < 55 else "55-64" if a < 65 else "65+"
# ASSUMPTION: BDL 2025 population as denominator for NSP 2021 employed (city grew ~2% since)
P["p_emp0"] = [0.0 if (b := emp_band(a)) is None else min(0.95, EMP["M" if s == "M" else "F"][b] / pop_band[(s, b)]) for a, s in zip(P.age, P.sex)]
# within the 15-24 band nobody under 18 works full-time and 18-19 rarely: ASSUMPTION
P.loc[P.age < 18, "p_emp0"] = 0.02; P.loc[P.age.between(18, 19), "p_emp0"] *= 0.5
# per-cell odds factor k so that sum(p) over the cell's personas = EMP_cell * personas / residents
per = n_res / pop_res
P["p_emp"] = P.p_emp0
for i, grp in P.groupby("ci"):
    target = g.employed[i] * per if g.employed[i] == g.employed[i] else None
    if target is None or len(grp) < 3: continue    # tiny samples: keep the city-wide age x sex rate
    lo_k, hi_k = 0.2, 5.0
    for _ in range(30):
        k = (lo_k * hi_k) ** 0.5; s = shift(grp.p_emp0.values, k).sum()
        lo_k, hi_k = (k, hi_k) if s < target else (lo_k, k)
    P.loc[grp.index, "p_emp"] = shift(grp.p_emp0.values, k)
P.loc[P.age < 15, "p_emp"] = 0
P["employed"] = rng.random(len(P)) < P.p_emp

# ======================= 4. students: residents who study + non-resident students to reach the BDL total
# ASSUMPTION: 90% of non-working 19-24 residents and 30% of working ones study; 20% of non-working 25-26 too
st_p = np.where(P.age.between(19, 24), np.where(P.employed, 0.3, 0.9), np.where(P.age.between(25, 26) & ~P.employed, 0.2, 0))
P["studying"] = rng.random(len(P)) < st_p
res_students = P.studying.sum() / per
EXTRA_STUDENTS = max(0, STUDENTS_TOTAL * (1 - COMMUTE_SHARE) - res_students)
n_stu = int(round(EXTRA_STUDENTS * per))
# ASSUMPTION: campuses / dorm clusters, rough weights by enrolment and dorm beds; (name, lat, lon, weight, spread km)
CAMPUSES = [("Miasteczko Studenckie AGH", 50.0688, 19.9037, 10, .5), ("AGH / UR, al. Mickiewicza", 50.0654, 19.9195, 8, 1.),
            ("UJ, Stare Miasto", 50.0614, 19.9330, 8, 1.2), ("Kampus 600-lecia UJ, Ruczaj", 50.0270, 19.9030, 10, 1.),
            ("UEK, Rakowicka", 50.0700, 19.9555, 8, .9), ("PK, Warszawska", 50.0715, 19.9415, 5, .8),
            ("Akademiki Piastowska / Bydgoska", 50.0790, 19.9050, 5, .6), ("PK Czyżyny + AWF", 50.0740, 20.0100, 5, 1.),
            ("UKEN, Podchorążych", 50.0785, 19.9145, 5, .8), ("Krakowska Akademia", 50.0375, 19.9270, 3, .8),
            ("UJ CM, Prokocim", 50.0115, 20.0055, 2, .8), ("UR, Balicka", 50.0830, 19.8520, 1.5, .7)]
DIFFUSE_W = 20  # renting rooms: spread like people who moved within Poland in the last year (Census grid CHG_IN)
inside = g.index[g.in_city]
mv = g.res1y_moved_within_pl.fillna(0)[inside]; mv_p = (mv + 1) / (mv + 1).sum()
w = np.array([c[3] for c in CAMPUSES] + [DIFFUSE_W], float)
STU_AGE = {19: .12, 20: .17, 21: .17, 22: .16, 23: .14, 24: .11, 25: .07, 26: .06}  # ASSUMPTION
F_SHARE = val("students", "he_students_graduates")["students_F"] / STUDENTS_TOTAL
srows = []
while len(srows) < n_stu:
    a = rng.choice(len(w), p=w / w.sum())
    if a < len(CAMPUSES):
        _, la, lo, _, s = CAMPUSES[a]; la, lo = la + rng.normal(0, s / 111.3), lo + rng.normal(0, s / 71.5)
        e, n = tr_inv.transform(lo, la); i = cell_of.get((int(e // 1000), int(n // 1000)))
        if i is None: continue
    else:
        i = rng.choice(inside, p=mv_p); lo, la = tr.transform((g.E_km[i] + rng.random()) * 1000, (g.N_km[i] + rng.random()) * 1000)
    srows.append(dict(ci=i, cell=g.cell[i], lat=round(la, 5), lon=round(lo, 5), sex="K" if rng.random() < F_SHARE else "M",
                      age=int(rng.choice(list(STU_AGE), p=list(STU_AGE.values()))), origin="student_spoza", born_abroad=bool(rng.random() < 0.065),  # 6.5% foreign students (BDL, Małopolska)
                      education="średnie", employed=bool(rng.random() < 0.25), studying=True))   # ASSUMPTION: 25% work part-time
P = pd.concat([P, pd.DataFrame(srows)], ignore_index=True)
P["district"] = district_of(P.lon.values, P.lat.values)

# ======================= 5. status
# NSP 2021 main income source: old-age 144 053 + disability 35 219 pensioners. BDL 2025: registered unemployed by age x sex.
def base_status(r):
    if r.employed: return "pracuje"
    if r.studying: return "student"
    if r.age < 19: return "uczeń"
    if (r.sex == "K" and r.age >= 60) or (r.sex == "M" and r.age >= 65): return "emeryt"
    if r.age >= 50 and rng.random() < 0.75: return "emeryt"   # ASSUMPTION: early retirement / disability pension (tuned to NSP pensioner total)
    return "niepracujący"
P["status"] = P.apply(base_status, axis=1)
UN = val("labour", "registered_unemployed_by_age_sex")
ub = lambda a: "<=24" if a < 25 else "25-34" if a < 35 else "35-44" if a < 45 else "45-54" if a < 55 else "55+"
scale = None
for (b, s), grp in P[(P.status == "niepracujący") & (P.origin == "mieszkaniec") & (P.district != OUTSIDE)].groupby([P.age.map(ub), P.sex]):
    need = UN[b]["M" if s == "M" else "F"] * per   # registered unemployed are Kraków residents
    take = grp.sample(min(len(grp), int(round(need))), random_state=1).index if need >= 1 else (grp.sample(1, random_state=1).index if rng.random() < need else [])
    P.loc[take, "status"] = "bezrobotny"
P.loc[P.employed & P.studying, "status"] = "pracuje"  # working students: status pracuje, flag studying

# ======================= 6. income (net zł / month)
WM, WD = val("labour", "mean_gross_wage_by_residence_monthly"), val("labour", "median_gross_wage_by_residence_monthly")
avg = lambda d: float(np.mean(list(d.values())))
LOGN = {}
for s, k in (("M", "M"), ("K", "F")):
    mean, med = avg(WM[k]), avg(WD[k]); LOGN[s] = (np.log(med), np.sqrt(2 * np.log(mean / med)))   # lognormal from median & mean
AGE_F = lambda a: .6 if a < 25 else .85 if a < 30 else 1.1 if a < 45 else 1.05 if a < 60 else .95                 # ASSUMPTION
EDU_F = {"wyższe": 1.25, "średnie": .9, "zasadnicze zawodowe": .75, "podstawowe": .65, None: .9}                     # ASSUMPTION
NET = 0.71                                                                                                          # ASSUMPTION gross -> net
PENSION = val("pensions", "avg_monthly_pension_gross_malopolskie")
pension_gross = PENSION.get("zus_old_age") or next(v for k, v in PENSION.items() if "old" in k and isinstance(v, (int, float)))
work = P.status == "pracuje"
f = np.array([AGE_F(a) * EDU_F.get(e if isinstance(e, str) else None, .9) for a, e in zip(P.age, P.education)])
f_norm = {s: np.median(f[work & (P.sex == s)]) for s in ("M", "K")}   # keep the BDL median by sex
mu = np.array([LOGN[s][0] for s in P.sex]); sd = np.array([LOGN[s][1] for s in P.sex])
gross = np.exp(mu + sd * rng.standard_normal(len(P))) * f / np.array([f_norm[s] for s in P.sex])
gross = np.where(P.studying & work, gross * 0.45, gross)   # ASSUMPTION: working students part-time
inc = np.select([work, P.status == "emeryt", P.status == "student", P.status == "bezrobotny"],
                [gross * NET, pension_gross * 0.85 * rng.lognormal(0, .3, len(P)),     # ASSUMPTION pension net ~85% of gross
                 1800 * rng.lognormal(0, .35, len(P)), 900 * rng.lognormal(0, .4, len(P))], 0)   # ASSUMPTION student, benefit
P["net_income"] = np.round(inc, -1)

# ======================= 7. household (adults 20+): types raked to NSP 2021 totals
HS, FC, FT = val("households", "households_by_size"), val("households", "families_by_children"), val("households", "families_by_type")
HH = ["mieszka sam(a)", "z partnerem/partnerką", "z partnerem i dziećmi", "samotny rodzic z dziećmi", "z rodzicami", "ze współlokatorami / w akademiku"]
single_par = FT["single_mothers"] + FT["single_fathers"] if "single_mothers" in FT else 53_934
minors = sum(AS[s][lb] for s in "MK" for lb in groups[:4]) * 0.9     # 0-17 ≈ 90% of 0-19 (ASSUMPTION)
TARGET = np.array([HS["1p"], 2 * FC["no_children"], 2 * (FC["with_children"] - single_par), single_par,
                   max(0, FC["children_total"] - minors), 0.0])
TARGET[5] = max(0, HS["persons_in_households"] - minors - TARGET[:5].sum())   # remainder: flatmates & other multi-person
def hh_w(a, s):  # ASSUMPTION: relative propensities by age, raked to totals below
    return np.array([.2 + .02 * max(0, a - 40) + (.3 if a > 70 else 0), .1 if a < 25 else .5 if a < 35 else .35,
                     .02 if a < 25 else .6 if a < 50 else .25 if a < 60 else .03, (.02 if a < 25 else .12 if a < 55 else .04) * (2.5 if s == "K" else .5),
                     .9 if a < 25 else .35 if a < 30 else .1 if a < 40 else .03 if a < 45 else 0, .6 if a < 30 else .1 if a < 40 else .05])
adult = (P.age >= 20) & (P.origin == "mieszkaniec") & (P.district != OUTSIDE)
W = np.array([hh_w(a, s) for a, s in zip(P.age[adult], P.sex[adult])])
for _ in range(50):   # 1-D raking of type totals
    W = W / W.sum(1, keepdims=True); cur = W.sum(0) / per; W *= np.where(cur > 0, TARGET / np.maximum(cur, 1e-9), 1)
W = W / W.sum(1, keepdims=True)
P["household"] = None
P.loc[adult, "household"] = [HH[rng.choice(6, p=w)] for w in W]
out_adult = (P.age >= 20) & ~adult & (P.origin == "mieszkaniec")   # suburbs: same propensities, not raked (no data)
P.loc[out_adult, "household"] = [HH[rng.choice(6, p=hh_w(a, s) / hh_w(a, s).sum())] for a, s in zip(P.age[out_adult], P.sex[out_adult])]
P.loc[P.age < 20, "household"] = "z rodzicami"
# remainder category (multi-person non-family + multi-family households): flatmates when young, extended family otherwise
P.loc[(P.household == HH[5]) & (P.age >= 35), "household"] = "z dalszą rodziną (wielopokoleniowo)"
P.loc[(P.household == HH[3]) & (P.age >= 55), "household"] = "z dorosłymi dziećmi (bez partnera)"
P.loc[P.origin == "student_spoza", "household"] = [HH[5] if rng.random() < 0.85 else HH[1] for _ in range((P.origin == "student_spoza").sum())]
kids = np.array([FC["1_child"], FC["2_children"], FC["3_children"] + FC["4plus_children"]], float)
has_kids = P.household.isin([HH[2], HH[3], "z dorosłymi dziećmi (bez partnera)"])
P["children"] = 0; P.loc[has_kids, "children"] = rng.choice([1, 2, 3], size=has_kids.sum(), p=kids / kids.sum())

# ======================= 8. car (access) and transport
C = D["city"]; cars = C["cars_per_1000"] / 1000
def car_p(r):  # ASSUMPTION: age curve x income x family, from cars per 1000 (BDL)
    if r.age < 18: return 0
    if r.origin == "student_spoza": return cars * .3
    f = (1.2 if 30 <= r.age < 70 else .6) * (1.25 if r.children else 1) * (0.7 if r.net_income < 3000 else 1.15 if r.net_income > 9000 else 1)
    return min(.95, cars * f)
P["has_car"] = [bool(rng.random() < car_p(r)) for r in P.itertuples()]
# car age and fuel from registered cars in Kraków (BDL 2025). ASSUMPTION: fuel independent of age;
# richer people drive newer cars (quantile tilt). Registered stock includes company and dormant cars.
CA, CF = val("transport", "cars_by_age"), val("transport", "cars_by_fuel")
age_bands = [(k, (0, 1) if k == "<=1" else (31, 40) if k == "31+" else tuple(map(int, k.split("-"))) if "-" in k else (int(k), int(k)))
             for k in CA if k != "total"]
cum = np.cumsum([CA[k] for k, _ in age_bands]) / sum(CA[k] for k, _ in age_bands)
fuels = ["benzyna", "diesel", "LPG", "hybryda/elektryk/inne"]; fw = np.array([CF["petrol"], CF["diesel"], CF["lpg"], CF["other_incl_electric_hybrid"]], float)
# richer owners get newer cars (random score tilted by income), but ages are handed out by RANK so the owners' age mix
# matches the registry exactly (the tilt alone made cars ~5 pts too young: src/representativeness.py)
own = P.index[P.has_car]
score = rng.random(len(own)) * np.where(P.net_income[own] > 12000, 0.6, np.where(P.net_income[own] < 4000, 1.15, 0.9))
q = (pd.Series(score, index=own).rank(method="first") - 0.5) / len(own)
def band_age(u):
    lo, hi = age_bands[int(np.searchsorted(cum, u))][1]; return int(rng.integers(lo, hi + 1))
P["car_age"] = None; P.loc[own, "car_age"] = [band_age(u) for u in q]
P["car_fuel"] = [rng.choice(fuels, p=fw / fw.sum()) if c else None for c in P.has_car]

# ======================= 9. leisure & sport (GUS surveys) + music taste
B = [bio.sample(a, s, e if isinstance(e, str) else None, rng) for a, s, e in zip(P.age, P.sex, P.education)]
for k in ("c_concert_other", "c_concert_freq", "c_concert_classical", "c_theatre", "c_disco_club_dancing", "c_festival", "sport"):
    P[k] = [b[k] for b in B]
P["cycles"] = ["jazda_na_rowerze" in b["sport_types"] for b in B]
P["hobbies"] = [b["hobbies"] for b in B]
P["culture_spend"] = [bio.culture_spend(x) for x in P.net_income]
P["transport"] = pd.Series(np.where(P.has_car, "głównie auto", "komunikacja miejska"), index=P.index) + pd.Series(np.where(P.cycles, " + rower", ""), index=P.index)
P.loc[P.age < 15, "transport"] = None
T = ["pop", "rock", "hip-hop", "elektronika", "klasyka/jazz", "disco polo"]
def taste(a, classical):  # ASSUMPTION (no GUS data on genres); classical-concert goers lean to klasyka/jazz
    w = np.array([3, 2, 3 if a < 35 else .5, 2 if a < 40 else .5, (.5 if a < 40 else 2) * (4 if classical else 1), 1 if a < 30 else 1.5]); return rng.choice(T, p=w / w.sum())
P["fav_genre"] = [taste(a, c) for a, c in zip(P.age, P.c_concert_classical)]

# ======================= output
P = P.drop(columns=["ci", "p_emp0", "p_emp"]).sample(frac=1, random_state=1).reset_index(drop=True)
P.insert(0, "id", range(1, len(P) + 1))
P.to_csv("out/personas.csv", index=False)
g[["cell", "lat", "lon", "res", "age_0_14", "age_15_64", "age_65p"]].to_csv("out/grid_cells.csv", index=False)
scale = (pop_res + EXTRA_STUDENTS) / len(P)
dsum = P.groupby("district").agg(personas=("id", "size"), students_out=("origin", lambda s: (s == "student_spoza").sum()),
                                 mean_age=("age", "mean"), lat=("lat", "mean"), lon=("lon", "mean"))
dsum["people"] = (dsum.personas * scale).round(-2); dsum["students_out"] = (dsum.students_out * scale).round(-2)
dsum.round({"mean_age": 1, "lat": 5, "lon": 5}).sort_values("people", ascending=False).to_csv("out/districts.csv")

# ======================= validation: synthetic city vs GUS (Kraków residents only, unless noted)
K = P[(P.district != OUTSIDE) & (P.origin == "mieszkaniec")]; K15 = K[K.age >= 15]; Kp = lambda m: m.sum() * scale
ea = val("labour", "economic_activity_15plus_by_sex")["total"]; mis = val("pensions", "main_income_source_by_sex")["total"]
ed = E_SEX["total"]
checks = [("mieszkańcy w granicach miasta", Kp(np.ones(len(K), bool)), 800_653, "NSP 2021"),
          ("studenci razem (z dojeżdżającymi)", Kp(P.studying) / (1 - COMMUTE_SHARE), STUDENTS_TOTAL, "BDL 2024/25"),
          ("pracujący 15+", Kp(K.employed), ea["employed"], "NSP 2021"),
          ("wskaźnik zatrudnienia 15+ (cała pop.)", K15.employed.mean(), ea["employed"] / ea["population_15plus"], "NSP 2021"),
          ("bezrobotni zarejestrowani", Kp(K.status == "bezrobotny"), UN["total"]["total"], "BDL 2025"),
          ("emeryci i renciści", Kp(K.status == "emeryt"), mis["old_age_pension"] + mis["disability_pension"], "NSP 2021"),
          ("wyższe wykształcenie (13+)", (K[K.age >= 13].education == "wyższe").mean(), ed["higher"] / (ed["total"] - ed["unknown"]), "NSP 2021"),
          ("mieszka sam(a)", Kp(K.household == HH[0]), HS["1p"], "NSP 2021"),
          ("mediana brutto pracujących", np.median(P.net_income[(P.status == "pracuje") & ~P.studying] / NET), avg(WD["total"]), "BDL 2025"),
          ("koncerty (15+, Kraków)", K15.c_concert_other.mean(), bio.CU["concert_other"]["by_residence"]["500k+"], "GUS kultura 2024, 500k+"),
          ("sport (Kraków, 5+)", K[K.age >= 5].sport.mean(), bio.SA["by_residence"]["500k+"], "GUS sport 2025, 500k+")]
print(f"{len(P)} personas ({(P.origin == 'mieszkaniec').sum()} residents, {(P.origin == 'student_spoza').sum()} non-resident students = "
      f"{EXTRA_STUDENTS:,.0f} people); 1 persona ≈ {scale:.0f} people")
print(f"\n{'wskaźnik':38s} {'model':>12s} {'GUS':>12s}  źródło")
for n, m, t, src in checks:
    fm = (lambda x: f"{x:.1%}") if t < 1 else (lambda x: f"{x:,.0f}")
    print(f"{n:38s} {fm(m):>12s} {fm(t):>12s}  {src}")
print("\n" + dsum.sort_values("people", ascending=False)[["people", "students_out", "mean_age"]].round(1).to_string())
