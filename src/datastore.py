"""One normalized catalog of everything the agent may use, with English names, enums, units, sources.
- residents: synthetic personas (out/personas.csv) as a normalized table; every persona carries a weight = residents it stands
  for (by district, from out/districts.csv), so counts are in PEOPLE. Fields: FIELDS below.
- tables: official numbers. city.* = GUS BDL for Kraków (data/raw/krakow_gus_extra.json); survey.* = national GUS surveys
  (culture participation, sport, household budgets, time use: data/raw/gus_leisure_rates.json); hetus.* = Eurostat HETUS 2020
  Poland (day of week, time of day); venues = event venues with capacity (viz/venues.json); districts = population per district.
- places: named venues, districts, neighbourhoods -> coordinates.
query() is the only way to read residents: typed filters, up to 2 group-by fields, measure count / share / mean / median.
"""
import json, os, sys
from functools import lru_cache
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUTSIDE = "poza Krakowem"
EN = {"status": {"pracuje": "employed", "student": "university_student", "uczeń": "school_pupil", "emeryt": "retired", "bezrobotny": "unemployed", "niepracujący": "inactive"},
      "education": {"wyższe": "higher", "średnie": "secondary", "zasadnicze zawodowe": "vocational", "podstawowe": "primary"},
      "household": {"mieszka sam(a)": "alone", "z partnerem/partnerką": "partner", "z partnerem i dziećmi": "partner_children", "samotny rodzic z dziećmi": "single_parent",
                    "z rodzicami": "with_parents", "ze współlokatorami / w akademiku": "flatmates", "z dalszą rodziną (wielopokoleniowo)": "extended_family",
                    "z dorosłymi dziećmi (bez partnera)": "adult_children"},
      "car_fuel": {"benzyna": "petrol", "diesel": "diesel", "LPG": "lpg", "hybryda/elektryk/inne": "hybrid_electric"},
      "activity": {"czyta książki": "reading", "muzea": "museums", "teatr": "theatre", "rower": "cycling", "koncerty (pop, rock, inne)": "concerts",
                   "kabaret, stand-up": "cabaret_standup", "pływanie": "swimming", "festiwale": "festivals", "kluby, dyskoteki": "clubbing",
                   "filharmonia, koncerty klasyczne": "classical_concerts", "taniec": "dancing", "fitness": "fitness", "majsterkowanie": "diy",
                   "kino co miesiąc": "cinema_monthly", "gra na instrumencie / śpiewa": "plays_music", "narty / snowboard": "skiing", "potańcówki, dancingi": "dances",
                   "siłownia": "gym", "bieganie / nordic walking": "running", "rolki / deskorolka": "skating", "fotografia": "photography",
                   "tenis / badminton / squash": "racket_sports", "piłka nożna": "football", "joga": "yoga", "wspinaczka": "climbing", "siatkówka": "volleyball",
                   "kolekcjonowanie": "collecting", "sporty walki": "martial_arts", "wędkarstwo": "fishing"}}
AGE_BANDS = [(0, 14, "0-14"), (15, 24, "15-24"), (25, 34, "25-34"), (35, 49, "35-49"), (50, 64, "50-64"), (65, 200, "65+")]
FIELDS = {   # name -> (type, description); 'list' fields hold several values (op 'in' = has any)
    "district": ("enum", "district of Kraków where they live, or 'outside_krakow' (suburbs)"),
    "lives_in_krakow": ("bool", "false for the suburban ring"),
    "age": ("number", "years"), "age_band": ("enum", "0-14 | 15-24 | 25-34 | 35-49 | 50-64 | 65+"),
    "sex": ("enum", "male | female"),
    "status": ("enum", " | ".join(EN["status"].values())),
    "studying": ("bool", "studies at a university; status university_student = studies and does not work, working students have status employed and studying true"),
    "student_from_outside": ("bool", "university student whose family home is elsewhere"),
    "life_stage": ("enum", "rule-based segment: uczniowie | studenci z Krakowa | studenci spoza Krakowa | seniorzy i emeryci | rodzice z dziećmi | niepracujący | młodzi pracujący bez dzieci | pracujący 35+ bez dzieci"),
    "education": ("enum", " | ".join(EN["education"].values()) + " (NSP 2021; empty under 13)"),
    "household": ("enum", " | ".join(EN["household"].values()) + " (NSP 2021)"),
    "children": ("number", "children living with them"),
    "born_abroad": ("bool", "census grid 2021"),
    "net_income": ("number", "monthly net money PLN: wages lognormal from GUS by sex, age, education; pensions from ZUS; ASSUMPTIONS: students without a job "
                             "get ~1800 PLN (parents / stipend), registered unemployed ~900 PLN benefit; 0 = no money of their own (pupils, inactive)"),
    "employed": ("bool", "works (NSP 2021 employment by age x sex, calibrated to the census grid); working students included"),
    "has_car": ("bool", "household car (CEPiK registry shares)"), "car_age": ("number", "years, only with a car"),
    "car_fuel": ("enum", " | ".join(EN["car_fuel"].values()) + " (lpg = petrol car with LPG)"),
    "uses_public_transport": ("bool", "no car: gets around by tram/bus"),
    "activity": ("list", "free-time activities sampled from GUS culture and sport surveys by age x sex x education: " + ", ".join(EN["activity"].values())),
    "favourite_music": ("enum", "pop | rock | hip-hop | elektronika | klasyka/jazz | disco polo (ASSUMPTION, no GUS data)"),
    "culture_spend": ("number", "monthly culture spending PLN (GUS household budget survey by income)"),
    "vote_2023": ("enum", "Sejm 2023 vote (adults in Kraków; src/attitudes.py: PKW precincts near home x IPSOS exit-poll odds ratios, raked to district results): PiS | KO | TD | Lewica | Konf | Inne"),
    "lat": ("number", "home, 1 km grid"), "lon": ("number", "home, 1 km grid"),
}
OPS = ["=", "!=", ">", "<", ">=", "<=", "in", "not_in", "within_km"]


def _segment(r):
    import planner
    return planner.segment(r)


@lru_cache(maxsize=1)
def residents():
    P = pd.read_csv("out/personas.csv"); DS = pd.read_csv("out/districts.csv", index_col="district")
    w = (DS.people / DS.personas).to_dict()
    R = pd.DataFrame({"id": P.id, "district": P.district.replace({OUTSIDE: "outside_krakow"}), "lives_in_krakow": P.district != OUTSIDE,
                      "age": P.age, "sex": P.sex.map({"M": "male", "K": "female"}), "status": P.status.map(EN["status"]),
                      "studying": ("bool", "studies at a university; status university_student = studies and does not work, working students have status employed and studying true"),
    "student_from_outside": P.origin == "student_spoza", "education": P.education.map(EN["education"]),
                      "household": P.household.map(EN["household"]), "children": P.children.fillna(0).astype(int),
                      "born_abroad": P.born_abroad.fillna(False).astype(bool), "net_income": P.net_income.round(), "employed": P.employed.fillna(False).astype(bool), "studying": (P.studying.fillna(False).astype(bool) & (P.age >= 18)) | (P.status == "student"),
                      "has_car": P.has_car.astype(bool), "car_age": P.car_age.where(P.has_car), "car_fuel": P.car_fuel.map(EN["car_fuel"]).where(P.has_car),
                      "uses_public_transport": ~P.has_car.astype(bool),
                      "activity": [[EN["activity"][x] for x in h.split(";") if x in EN["activity"]] if isinstance(h, str) and h else [] for h in P.hobbies],
                      "favourite_music": P.fav_genre, "culture_spend": P.culture_spend, "lat": P.lat, "lon": P.lon,
                      "weight": P.district.map(w)})
    att = pd.read_csv("out/attitudes.csv").set_index("id").vote if os.path.exists("out/attitudes.csv") else pd.Series(dtype=str)
    R["vote_2023"] = R.id.map(att)
    R["age_band"] = pd.cut(R.age, [b[0] - 1 for b in AGE_BANDS] + [200], labels=[b[2] for b in AGE_BANDS]).astype(str)
    R["life_stage"] = [_segment(r) for r in P.itertuples()]
    return R


def _km(lat, lon, la2, lo2):
    r = np.radians; h = np.sin(r(la2 - lat) / 2) ** 2 + np.cos(r(lat)) * np.cos(r(la2)) * np.sin(r(lo2 - lon) / 2) ** 2
    return 2 * 6371 * np.arcsin(np.sqrt(h))


def mask(R, filters):
    """filters: [{field, op, value}] (AND). value: string; numbers / true/false parsed; 'in' takes a comma list;
    within_km (field 'home'): 'lat,lon,km'."""
    m = pd.Series(True, index=R.index)
    for f in filters or []:
        fld, op, val = f["field"], f["op"], str(f["value"]).strip()
        if op == "within_km":
            la, lo, km = [float(x) for x in val.split(",")]; m &= _km(R.lat, R.lon, la, lo) <= km; continue
        if fld not in FIELDS: raise ValueError(f"unknown field {fld}; fields: {', '.join(FIELDS)}")
        col = R[fld]; kind = FIELDS[fld][0]
        vals = [x.strip() for x in val.split(",")]
        if kind == "list":
            hit = col.apply(lambda xs: bool(set(xs) & set(vals)))
            m &= ~hit if op in ("!=", "not_in") else hit; continue
        if kind == "bool": vals = [v.lower() in ("true", "1", "yes") for v in vals]
        elif kind == "number": vals = [float(v) for v in vals]
        if op in ("in", "="): m &= col.isin(vals)
        elif op in ("not_in", "!="): m &= ~col.isin(vals)
        else: m &= {">": col > vals[0], "<": col < vals[0], ">=": col >= vals[0], "<=": col <= vals[0]}[op].fillna(False)
    return m


def query(filters=None, group_by=None, measure="count", field=None, base_filters=None):
    """measure: count (people), share (of base_filters population, default everyone matching nothing extra = all residents
    in the group), mean / median of a numeric field. Returns rows + n_personas (reliability: < 30 is noisy)."""
    R = residents(); base = mask(R, base_filters); sel = base & mask(R, filters); group_by = [g for g in (group_by or []) if g]
    for g in group_by:
        if g not in FIELDS or FIELDS[g][0] == "list": raise ValueError(f"cannot group by {g}")
    if measure in ("mean", "median") and (field not in FIELDS or FIELDS[field][0] != "number"): raise ValueError("mean/median need a numeric field")
    def agg(G, B):
        out = {"n_personas": int(len(G)), "people": int(round(G.weight.sum(), -2))}
        if measure == "share": out["share"] = round(float(G.weight.sum() / max(B.weight.sum(), 1e-9)), 4)
        if measure == "mean": out["mean"] = round(float(np.average(G[field].dropna(), weights=G.weight[G[field].notna()])), 2) if G[field].notna().any() else None
        if measure == "median":
            g = G[[field, "weight"]].dropna().sort_values(field); c = g.weight.cumsum()
            out["median"] = float(g[field][c >= c.iloc[-1] / 2].iloc[0]) if len(g) else None
        return out
    if not group_by: return {"rows": [agg(R[sel], R[base])], "population": "residents" + (" (base filtered)" if base_filters else "")}
    rows = []
    for k, G in R[sel].groupby(group_by, dropna=True):
        k = k if isinstance(k, tuple) else (k,)
        B = R[base]
        for g, v in zip(group_by, k): B = B[B[g] == v]
        rows.append({**{g: (v.item() if hasattr(v, "item") else v) for g, v in zip(group_by, k)}, **agg(G, B)})
    return {"rows": rows}


# ---------------- official tables
@lru_cache(maxsize=1)
def tables():
    T = {}
    raw = json.load(open("data/raw/krakow_gus_extra.json"))
    for g, items in raw.items():
        if g.startswith("_") or not isinstance(items, dict): continue
        for k, v in items.items():
            if isinstance(v, dict) and "value" in v:
                T[f"city.{g}.{k}"] = {"source": "GUS BDL, Kraków", **{x: v[x] for x in ("value", "unit", "year", "level", "note") if x in v}}
    S = json.load(open("data/raw/gus_leisure_rates.json"))
    for k, v in S.get("culture", {}).items(): T[f"survey.culture.{k}"] = {"source": "GUS survey: participation in culture 2024 (Poland)", "note": v.get("description"), "value": {x: v[x] for x in v if x.startswith("by_") or x == "total"}}
    for k in ("sport_any", "sport_regular", "sport_occasional", "sport_types", "spending", "time_use"):
        if k in S: T[f"survey.{k}"] = {"source": "GUS survey (Poland)", "note": S[k].get("description") if isinstance(S[k], dict) else None, "value": S[k]}
    H = json.load(open("data/raw/hetus_pl_2020.json"))
    T["hetus.week"] = {"source": "Eurostat HETUS 2020, Poland", "note": "share of people doing the activity on that day, %", "value": H["week"]}
    T["hetus.months"] = {"source": "Eurostat HETUS 2020, Poland", "note": "participation by month, %", "value": H.get("months")}
    T["hetus.hours"] = {"source": "Eurostat HETUS 2020, Poland", "note": "share of people in each activity per 10-minute slot, %", "value": H.get("hours")}
    if os.path.exists("data/raw/pkw_krakow.json"):   # election results (PKW), per district, and precinct points
        PK = json.load(open("data/raw/pkw_krakow.json"))
        for el, agg in PK["district_aggregates"].items():
            T[f"elections.{el}.by_district"] = {"source": "PKW, wyniki w obwodach, aggregated to districts (special precincts excluded)", "note": f"{el}: turnout and vote shares per district",
                                                "value": {d: {"turnout": x["turnout"], "shares": x["shares"]} for d, x in agg["by_district"].items()}}
    if os.path.exists("data/raw/bo_krakow.json"):   # civic budget: what residents actually voted for, by district
        BO = json.load(open("data/raw/bo_krakow.json"))["projects"]
        for ed in sorted({x["edition"] for x in BO})[-3:]:
            T[f"civic_budget.{ed}"] = {"source": "Budżet Obywatelski Krakowa (BIP, budzet.krakow.pl)", "note": f"edition {ed}: projects with points (3/2/1 per voter), cost, winner; top 15 per pool",
                                       "value": {pool: [{"title": x["title"][:90], "category": x.get("category"), "cost_pln": x["cost_pln"], "points": x["votes"], "won": x.get("won_by_budget_rule")}
                                                        for x in sorted([y for y in BO if y["edition"] == ed and (y["district_norm"] or "city") == pool], key=lambda y: -y["votes"])[:15]]
                                                 for pool in sorted({(y["district_norm"] or "city") for y in BO if y["edition"] == ed})}}
    if os.path.exists("data/raw/crime_krakow.json"):   # src/fetch_crime_krakow.py: police station areas (whole districts), city reports
        CR = json.load(open("data/raw/crime_krakow.json"))
        DS_ = pd.read_csv("out/districts.csv", index_col="district").people
        years = [y for y in CR["crimes_by_komisariat"] if "2016" <= y <= "2020"]
        rows = {}
        for kp, ds_ in CR["komisariat_to_districts"].items():
            pop = float(sum(DS_.get(d, 0) for d in ds_)); avg = sum(CR["crimes_by_komisariat"][y][f"KP {kp}"] for y in years) / len(years)
            rows[f"KP {kp}"] = {"districts": ds_, "crimes_per_year_2016_2020": round(avg), "residents": int(round(pop, -2)), "crimes_per_1000_residents": round(1000 * avg / pop, 1)}
        T["safety.crimes_by_police_area"] = {"source": "Raport o stanie Miasta Krakowa 2016-2020 (KMP/KWP data), police station areas = whole districts",
                                             "note": "crimes recorded where they HAPPENED (Stare Miasto inflated by tourists and nightlife: per-resident rates there mislead); no per-district data after 2020; excludes KMP/KWP-handled cases (~2-3k/yr)",
                                             "value": rows}
        T["safety.city_total_2021_2025"] = {"source": "Raport o stanie Gminy Kraków", "unit": "crimes recorded per year", "value": CR["city_total_2021_2025"]}
        T["safety.perceived_by_district"] = {"source": "Kraków city survey of residents (~100 adults per district, ±10 pts per district)", "note": "share who say their district is safe, etc.",
                                             "value": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in CR["perceived_safety_by_district"].items()}}
    if os.path.exists("data/raw/osm_poi_krakow.json"):
        OS = json.load(open("data/raw/osm_poi_krakow.json"))
        T["osm.categories"] = {"source": OS["_source"], "note": "point categories for count_places / nearby_places", "value": OS["categories"]}
    T["venues"] = {"source": "project list, approximate coordinates and capacities (ASSUMPTION)", "value": json.load(open("viz/venues.json"))}
    DS = pd.read_csv("out/districts.csv")
    T["districts"] = {"source": "GUS NSP 2021 grid + BDL 2025, aggregated to OSM districts", "unit": "persons",
                      "value": {r.district.replace(OUTSIDE, "outside_krakow"): {"people": int(r.people), "mean_age": float(r.mean_age), "lat": float(r.lat), "lon": float(r.lon)} for r in DS.itertuples()}}
    return T


def table_index():
    """Key -> one-line description (what the agent sees before fetching a table)."""
    out = {}
    for k, v in tables().items():
        d = v.get("note") or ""
        if isinstance(v["value"], dict) and not d: d = "fields: " + ", ".join(list(v["value"])[:8])
        out[k] = f"{d} [{v.get('year', '')} {v.get('unit', '')}]".replace(" []", "").strip()[:160]
    return out


def get_table(key, max_chars=6000):
    T = tables()
    if key not in T: raise ValueError(f"unknown table {key}")
    s = json.dumps(T[key], ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= max_chars else s[:max_chars] + f'..."(truncated, {len(s)} chars)"'


# ---------------- places
@lru_cache(maxsize=1)
def places():
    import planner
    DS = pd.read_csv("out/districts.csv")
    g = planner.gazetteer([{"name": r.district, "lat": r.lat, "lon": r.lon} for r in DS.itertuples() if r.district != OUTSIDE])
    return {v["name"]: {"lat": round(v["lat"], 4), "lon": round(v["lon"], 4), "kind": v["kind"]} for v in g.values()}


def find_place(name):
    n = name.lower(); P = places()
    hits = {k: v for k, v in P.items() if n in k.lower() or k.lower() in n}
    return hits or {"_note": "not in the list of known places; use your own approximate coordinates and say so"}


# ---------------- supply side: OpenStreetMap points (src/fetch_osm_poi.py)
@lru_cache(maxsize=1)
def poi():
    if not os.path.exists("data/raw/osm_poi_krakow.json"): return pd.DataFrame(columns=["cat", "name", "lat", "lon", "district"])
    D = pd.DataFrame(json.load(open("data/raw/osm_poi_krakow.json"))["poi"], columns=["cat", "name", "lat", "lon"])
    from shapely.geometry import Point, shape
    G = [(f["properties"]["name"], shape(f["geometry"])) for f in json.load(open("data/geo/krakow_districts.geojson"))["features"] if f["properties"]["kind"] == "district"]
    D["district"] = [next((n for n, g in G if g.contains(Point(lo, la))), None) for la, lo in zip(D.lat, D.lon)]
    return D


def count_places(category, by_district=True):
    """Number of places of a category per district, with residents per place (supply vs people)."""
    D = poi(); D = D[D.cat == category]
    if not by_district: return {"category": category, "count": int(len(D))}
    R = residents(); pop = R[R.lives_in_krakow].groupby("district").weight.sum()
    c = D.groupby("district").size()
    note = ("OpenStreetMap is incomplete for many service categories (e.g. gyms, beauty, doctors): a district with 0-2 places may simply be unmapped. "
            "Treat residents_per_place as a rough relative signal; say this in the answer when a conclusion rests on very small counts.")
    return {"category": category, "source": "OpenStreetMap (completeness varies)", "caveat": note, "rows": [{"district": d, "places": int(c.get(d, 0)), "residents": int(round(pop[d], -2)),
            "residents_per_place": int(round(pop[d] / c[d])) if c.get(d, 0) else None} for d in pop.index]}


def nearby_places(category, lat, lon, km=1.0):
    D = poi(); D = D[D.cat == category]; d = _km(D.lat.values, D.lon.values, lat, lon); m = d <= km
    return {"category": category, "within_km": km, "count": int(m.sum()),
            "nearest": [{"name": n, "km": round(float(x), 2)} for n, x in sorted(zip(D.name[m].fillna("(bez nazwy)"), d[m]), key=lambda z: z[1])[:8]]}


def travel_by_district(lat, lon, mode="transit"):
    """Median door-to-door minutes from residents' homes to a point, by district (R5 on the MPK timetable / OSM roads,
    src/travel.py; mode transit = everyone by tram/bus, car = everyone by car, as_they_travel = car owners by car)."""
    import travel
    R = residents(); R = R[R.lives_in_krakow].copy(); P = pd.read_csv("out/personas.csv", usecols=["id", "cell"]).set_index("id")
    R["cell"] = R.id.map(P.cell)
    if mode in ("transit", "car"): R["has_car"] = mode == "car"
    R["min"] = travel.minutes(R, lat, lon)
    g = R.groupby("district").apply(lambda G: float(np.average(G["min"], weights=G.weight)), include_groups=False)
    return {"to": [lat, lon], "mode": mode, "source": "R5 on ZTP Kraków GTFS (Friday 18:00, median over a 30-min window) and OSM roads (no congestion)",
            "rows": [{"district": d, "minutes": round(v, 1)} for d, v in g.sort_values().items()]}


def catalog():
    return {"residents": {"what": "synthetic residents of Kraków + suburban ring built from GUS census and surveys; counts are people (weighted)",
                          "total_people": int(round(residents().weight.sum(), -3)), "fields": {k: f"{t}: {d}" for k, (t, d) in FIELDS.items()}, "ops": OPS},
            "tables": table_index(), "places": "find_place(name) for venues, districts, neighbourhoods",
            "osm_categories": (json.load(open("data/raw/osm_poi_krakow.json"))["categories"] if os.path.exists("data/raw/osm_poi_krakow.json") else "not fetched yet")}
