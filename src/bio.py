"""Leisure bio of a persona sampled from GUS survey rates (data/raw/gus_leisure_rates.json):
culture 2024 (15+), sport 2025 (5+), household budgets 2025. Every rate comes from a GUS table;
the ways of combining one-way tables are ASSUMPTIONs and are marked below.

Kraków adjustment: GUS never crosses age with town size, so we shift odds (keeps p in 0..1, no overshoot):
  odds(p) = odds(rate(age, sex, urban)) x OR(500k+ vs all towns)                  ASSUMPTION: town-size effect is
                                                                                  the same in every age group
  and, when education is known, x OR(education vs total) ** 0.5                 ASSUMPTION: damped, age already
                                                                                  carries part of the education effect
"""
import json
import numpy as np

L = json.load(open("data/raw/gus_leisure_rates.json"))
CU, SA, ST = L["culture"], L["sport_any"], L["sport_types"]

# culture activities kept as persona traits: key -> label used in the persona description
CULTURE = {"concert_other": "koncerty (pop, rock, inne)", "concert_classical": "filharmonia, koncerty klasyczne", "theatre": "teatr",
           "cinema_monthly": "kino co miesiąc", "disco_club_dancing": "kluby, dyskoteki", "festival": "festiwale", "museum_domestic": "muzea",
           "stage_cabaret": "kabaret, stand-up", "books_read_any": "czyta książki", "hobby_photography": "fotografia",
           "hobby_music_making": "gra na instrumencie / śpiewa", "hobby_dance": "taniec", "hobby_diy_tinkering": "majsterkowanie",
           "hobby_collecting": "kolekcjonowanie"}
SPORT = {"jazda_na_rowerze": "rower", "plywanie": "pływanie", "aerobik_fitness_gimnastyka": "fitness", "sporty_silowe_i_kulturystyka": "siłownia",
         "jogging_nordic_walking": "bieganie / nordic walking", "gra_w_pilke_nozna": "piłka nożna", "joga": "joga", "taniec": "taniec",
         "jazda_na_nartach_snowboardzie": "narty / snowboard", "gra_w_siatkowke": "siatkówka",
         "gra_w_tenisa_ziemnego_badmintona_lub_squasha": "tenis / badminton / squash", "wspinaczka": "wspinaczka",
         "sporty_walki_zapasy_judo_karate_boks_i_inne": "sporty walki", "wedkarstwo": "wędkarstwo",
         "jazda_na_rolkach_deskorolce_hulajnodze_wrotkach_itp": "rolki / deskorolka"}
EDU_KEY = {"wyższe": "wyzsze", "średnie": "srednie_policealne", "policealne": "srednie_policealne",
           "zasadnicze zawodowe": "zasadnicze_zawodowe", "podstawowe": "podstawowe_gimnazjalne_brak"}


def OR(a, b):  # odds ratio of rate a vs rate b
    return (a / (1 - a)) / (b / (1 - b)) if a and b and a < 1 and b < 1 else 1.0


def shift(p, *ors):
    if p <= 0: return 0.0
    o = p / (1 - p) if p < 1 else 1e6
    for x in ors: o *= x
    return o / (1 + o)


def band5(a):  # culture age groups
    return "15-24" if a < 25 else "25-34" if a < 35 else "35-49" if a < 50 else "50-64" if a < 65 else "65+"


def band8(a):  # sport age groups
    for lo, b in ((60, "60+"), (50, "50-59"), (40, "40-49"), (30, "30-39"), (20, "20-29"), (15, "15-19"), (10, "10-14"), (5, "5-9")):
        if a >= lo: return b
    return None


def culture_rate(v, age, sex, edu=None):
    if age < 15: return 0.0
    b = band5(age)
    r = (v.get("urban_by_age_sex") or {}).get(sex, {}).get(b)
    if r is None:  # suppressed cell: age x sex nationally, scaled by the urban/national ratio of that age
        ua, na, nas = (v.get("urban_by_age") or {}).get(b), (v.get("by_age") or {}).get(b), (v.get("by_age_sex") or {}).get(sex, {}).get(b)
        r = nas * ua / na if None not in (ua, na, nas) and na else (ua or na or v["total"])
    res = v.get("by_residence") or {}
    e = (v.get("by_education") or {}).get(EDU_KEY.get(edu or "", ""))
    return float(shift(r, OR(res.get("500k+"), res.get("miasta_razem")), OR(e, v.get("total")) ** 0.5))


SA_TOTAL = SA["total_by_year"]["2025"] if isinstance(SA["total_by_year"], dict) else SA["total_by_year"]
SA_CITY = OR(SA["by_residence"]["500k+"], SA_TOTAL)


def sport_rate(age, sex, edu=None):
    b = band8(age)
    if b is None: return 0.0
    e = (SA.get("by_education") or {}).get(EDU_KEY.get(edu or "", ""))
    return float(shift(SA["by_age_sex"][sex][b], SA_CITY, OR(e, SA_TOTAL) ** 0.5))


def sport_type_cond(t, age, sex):
    """P(type | does sport): share of all people doing t / share doing any sport, same age & sex; city ratio of t vs any."""
    b = band8(age); v = ST[t]
    if b is None: return 0.0
    num = (v.get("by_age_sex") or {}).get(sex, {}).get(b); den = SA["by_age_sex"][sex][b]
    if num is None or not den: return 0.0
    # ASSUMPTION: big-city boost of this type relative to the boost of sport overall
    city = OR(v["by_residence"]["500k+"], v["total"]) / SA_CITY if v.get("total") else 1.0
    return float(shift(min(num / den, 0.95), city))


def sample(age, sex, edu, rng):
    """Returns dict of flags + a ';'-joined label list for the persona description."""
    out, labels = {}, []
    for k, lbl in CULTURE.items():
        hit = rng.random() < culture_rate(CU[k], age, sex, edu); out["c_" + k] = hit
        if hit: labels.append("potańcówki, dancingi" if k == "disco_club_dancing" and age >= 40 else lbl)   # GUS item covers both
    out["c_concert_freq"] = out["c_concert_other"] and rng.random() < (
        (CU["concert_other_more_than_4"]["total"] / CU["concert_other"]["total"]) if CU["concert_other"]["total"] else 0)
    sport = rng.random() < sport_rate(age, sex, edu); out["sport"] = sport; out["sport_types"] = []
    if sport:
        for t, lbl in SPORT.items():
            if rng.random() < sport_type_cond(t, age, sex): out["sport_types"].append(t); labels.append(lbl)
    out["hobbies"] = ";".join(dict.fromkeys(labels))  # dedupe "taniec" (culture + sport)
    return out


# ---------- spending on recreation & culture (household budgets 2025)
SP = L["spending"]
_Q = SP["income_quintile_upper_limits_pln_per_capita_monthly"]
_REC = SP["expenditure_per_capita_by_quintile"]["rekreacja_i_kultura"]


def culture_spend(net_income):
    """Monthly per-capita spending on recreation & culture of the persona's income quintile.
    ASSUMPTION: individual net income ~ household per-capita income (true for singles, overstated for parents)."""
    for q in ("Q1", "Q2", "Q3", "Q4"):
        if _Q[q] is not None and net_income <= _Q[q]: return float(_REC[q])
    return float(_REC["Q5"])
