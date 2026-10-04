"""Pull extra GUS BDL variables for Kraków (persona realism): NSP 2021 education/households/labour,
students, pensions, wages, culture, sport, transport, migration, housing, tourism, budget.
Usage: python3 src/fetch_bdl_extra.py   -> data/raw/krakow_gus_extra.json
IDs are hardcoded (found via /subjects + /variables). ~10 requests, throttled to 1 req / 1.2 s.
For every item we take the latest year in which at least half of its variables are filled (so a monthly
series picks the last complete year, not a year with only Q1; discontinued sub-series show as null).
Levels: gmina = Kraków 011212161011 (BDL level 6), powiat = m. Kraków 011212161000 (5),
podregion = PODREGION MIASTO KRAKÓW 011212100000 (4, same territory as the city),
region = REGION MAŁOPOLSKIE 011210000000 (3, = whole voivodeship; NOT Kraków-specific).
"""
import json, os, sys, time, requests
BASE = "https://bdl.stat.gov.pl/api/v1"
UNITS = {"gmina": "011212161011", "powiat": "011212161000", "podregion": "011212100000", "region": "011210000000"}

def key():
    for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
        if line.startswith("BDL_KEY="): return line.split("=", 1)[1].strip()
_last = [0.0]
def get(path, params):
    time.sleep(max(0, 1.2 - (time.time() - _last[0]))); _last[0] = time.time()
    r = requests.get(BASE + path, params=params, headers={"X-ClientId": key()}, timeout=60)
    if r.status_code == 429: sys.exit("429 rate limited: " + r.text[:200])
    r.raise_for_status(); return r.json()

def seq(start, labels, step=1):  # consecutive var ids -> {label: id}
    return {l: start + i * step for i, l in enumerate(labels)}

EDU10 = ["total", "higher", "secondary_postsec_total", "secondary_general", "secondary_vocational",
         "basic_vocational", "lower_secondary", "primary", "below_primary_or_none", "unknown"]
EDU9 = ["total", "higher", "secondary_postsec_total", "secondary_general", "secondary_vocational",
        "basic_vocational", "lower_secondary_or_primary", "below_primary_or_none", "unknown"]
AGE12 = ["total", "<=19", "20-24", "25-29", "30-34", "35-39", "40-44", "45-49", "50-54", "55-59", "60-64", "65+"]
HH_AGE = ["total", "<=24", "25-29", "30-34", "35-39", "40-44", "45-49", "50-54", "55-59", "60-64", "65-69", "70-74", "75+"]
SEX = ["total", "M", "F"]
MONTHS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
LAB9 = ["population_15plus", "active", "employed", "unemployed", "inactive", "status_unknown",
        "activity_rate_pct", "employment_rate_pct", "unemployment_rate_pct"]

# (topic, name, unit_key, measure, var_id | nested dict of var_ids, note)
SPEC = [
 # 1. Education, NSP 2021 (ludność rezydująca, 13+)
 ("education", "edu_by_sex_13plus", "gmina", "persons", {s: seq(1719968 + 10 * i, EDU10) for i, s in enumerate(SEX)}, "NSP 2021, P4318, resident population 13+"),
 ("education", "edu_by_age_13plus", "powiat", "persons", {a: seq(1701450 + 9 * i, EDU9) for i, a in enumerate(AGE12)}, "NSP 2021, P4320, resident 13+; '<=19' means 13-19; no sex split at this level"),
 # 2. Households & families, NSP 2021
 ("households", "households_by_size", "gmina", "households (people / avg for last two)",
  seq(1652549, ["total", "1p", "2p", "3p", "4p", "5p_plus", "persons_in_households", "avg_household_size"]), "NSP 2021, P4287"),
 ("households", "households_by_family_composition", "gmina", "households",
  seq(1641709, ["total", "one_family", "two_family", "three_plus_family", "nonfamily_total", "nonfamily_1person", "nonfamily_multiperson"]), "NSP 2021, P4225"),
 ("households", "families_by_children", "gmina", "families (persons / avg for last two)",
  seq(1641728, ["total", "no_children", "with_children", "1_child", "2_children", "3_children", "4plus_children", "children_total", "avg_children_per_family"]), "NSP 2021, P4227; 'children' = own children living in family, any age"),
 ("households", "families_by_type", "gmina", "families",
  seq(1641716, ["total", "married_total", "married_no_children", "married_with_children", "cohabiting_total", "cohabiting_no_children", "cohabiting_with_children", "single_mothers", "single_fathers"]), "NSP 2021, P4226"),
 ("households", "households_by_sex_age_of_reference_person", "gmina", "households",
  {s: seq(1734697 + 13 * i, HH_AGE) for i, s in enumerate(SEX)}, "NSP 2021, P4462; reference person (reprezentant) age/sex"),
 ("households", "marital_status_15plus", "gmina", "persons",
  {s: seq(1652557 + 6 * i, ["total", "never_married", "married", "widowed", "divorced", "unknown"]) for i, s in enumerate(SEX)}, "NSP 2021, P4288, resident 15+; legal status"),
 # 3. Higher education (by seat of HE institution / branch)
 ("students", "he_students_graduates", "gmina", "units / persons",
  {"institutions": 377826, "branches": 377821, "students_total": 377822, "students_F": 377825, "students_M": 377823,
   "graduates_total": 377819, "graduates_F": 377820, "graduates_M": 377824}, "P3226; year = start of academic year (2024 = 2024/25), stan na 31 XII"),
 ("students", "he_by_supervising_ministry", "podregion", "persons",
  {t: seq(s, ["students_total", "students_M", "students_F", "graduates_total", "graduates_M", "graduates_F"]) for t, s in
   [("all", 1618483), ("science_ministry", 1618489), ("health_ministry", 1618495), ("culture_ministry_arts", 1618507), ("defence_interior", 1618513), ("church", 1618519)]}, "P4023"),
 ("students", "students_to_pop_19_24_ratio", "podregion", "ratio", 1621850, "P4058; students / residents aged 19-24"),
 ("students", "foreign_students_pct_malopolskie", "region", "%", 453694, "P3449; region Małopolskie, not Kraków-only"),
 ("students", "students_per_10k_malopolskie", "region", "persons", {"per_10k_pop": 60194, "per_10k_pop_19_24": 60195}, "P2383; region level"),
 # 4. Pensions / income sources
 ("pensions", "pensioners_avg_count_malopolskie", "region", "persons",
  {"all_pensioners": 155055, "zus_total": 155056, "zus_old_age": 155051, "zus_disability": 155053, "zus_survivor": 155052, "krus_farmers": 155054}, "P2861; region (=voivodeship) only, no powiat data in BDL"),
 ("pensions", "avg_monthly_pension_gross_malopolskie", "region", "PLN",
  {"zus_total": 155057, "zus_old_age": 155058, "zus_disability": 155059, "zus_survivor": 155060, "krus_farmers": 155061, "zus_old_age_pct_of_avg_wage": 288075}, "P2860; region only"),
 ("pensions", "main_income_source_by_sex", "powiat", "persons",
  {s: seq(1735107 + 7 * i, ["total", "employment", "self_employment", "old_age_pension", "disability_pension", "other_sources", "dependent_or_unknown"]) for i, s in enumerate(SEX)}, "NSP 2021, P4488 (ludność, not resident-only)"),
 # 5. Wages & labour
 ("labour", "mean_gross_wage_by_residence_monthly", "powiat", "PLN",
  {s: {m: 1749925 + 6 * i + j for i, m in enumerate(MONTHS)} for j, s in enumerate(SEX)}, "P4609 'Rozkład wynagrodzeń' (by place of residence); employees, monthly; average the months for an annual figure"),
 ("labour", "median_gross_wage_by_residence_monthly", "powiat", "PLN",
  {s: {m: 1750141 + 6 * i + j for i, m in enumerate(MONTHS)} for j, s in enumerate(SEX)}, "P4610, as above, median"),
 ("labour", "registered_unemployed_by_age_sex", "powiat", "persons",
  {"total": {"total": 34041, "M": 34042, "F": 34043}, "<=24": {"total": 8813, "M": 33573, "F": 8814}, "25-34": {"total": 8815, "M": 33576, "F": 8816},
   "35-44": {"total": 8817, "M": 33579, "F": 8818}, "45-54": {"total": 8819, "M": 33582, "F": 8820}, "55+": {"total": 8821, "M": 33585, "F": 8822}}, "P1946, stan 31 XII"),
 ("labour", "registered_unemployed_by_education_sex", "powiat", "persons",
  {"total": {"total": 47058, "M": 47066, "F": 47052}, "higher": {"total": 47053, "M": 47067, "F": 47057},
   "postsec_secondary_vocational": {"total": 47062, "M": 47047, "F": 47048}, "secondary_general": {"total": 47060, "M": 47055, "F": 47051},
   "basic_vocational": {"total": 47063, "M": 47050, "F": 47054}, "lower_secondary_primary_or_less": {"total": 47049, "M": 47059, "F": 47064}}, "P1947, stan 31 XII"),
 ("labour", "economic_activity_15plus_by_sex", "gmina", "persons / %",
  {s: seq(1655473 + 9 * i, LAB9) for i, s in enumerate(SEX)}, "NSP 2021, P4305, resident; LFS-style definitions"),
 ("labour", "employed_by_age_sex", "gmina", "persons",
  {s: seq(1725076 + 7 * i, ["total", "15-24", "25-34", "35-44", "45-54", "55-64", "65+"]) for i, s in enumerate(SEX)}, "NSP 2021, P4409, resident"),
 ("labour", "employed_by_occupation_group", "powiat", "persons",
  seq(1735319, ["total", "managers", "professionals", "technicians", "clerical", "service_sales", "agriculture", "craft_trades", "operators", "elementary"]), "NSP 2021, P4504, resident, ISCO major groups"),
 ("labour", "employed_by_pkd_section", "powiat", "persons",
  seq(1735265, ["total", "A_agriculture", "BCDE_industry", "B_mining", "C_manufacturing", "D_energy", "E_water_waste", "F_construction", "G_trade",
                "H_transport", "I_accommodation_food", "J_ict", "K_finance", "L_real_estate", "M_professional", "N_admin_support", "O_public_admin",
                "P_education", "Q_health_social", "R_culture_recreation", "S_other_services", "T_households"]), "NSP 2021, P4502, resident, by place of residence"),
 # 6. Culture
 ("culture", "cinemas", "gmina", "objects / seats / shows / viewers",
  {"cinemas": 148551, "screens": 148553, "seats": 148555, "shows": 148557, "shows_polish_films": 148559, "viewers": 148561, "viewers_polish_films": 148563}, "P1687, fixed cinemas"),
 ("culture", "museums", "gmina", "objects / visitors", {"museums_incl_branches": 1241, "visitors": 1243}, "P1679; visitors include tourists"),
 ("culture", "theatres", "gmina", "units / performances / viewers",
  {k: seq(s, ["drama", "musical_entertainment_old", "opera_old", "puppet"], -3) | {"operetta_ballet_dance_other": s2, "opera": s2 + 1}
   for k, s, s2 in [("units", 194952, 1747502), ("performances", 194953, 1747504), ("viewers", 194954, 1747506)]},
  "P2874, fixed halls; '_old' categories are pre-2023 classification, replaced by the last two"),
 ("culture", "music_institutions", "gmina", "units / concerts / listeners",
  {k: {"choir": s + 6, "philharmonic": s + 3, "orchestra_symph_chamber": s, "orchestra": s2}
   for k, s, s2 in [("units", 194922, 1747499), ("concerts", 194923, 1747500), ("listeners", 194924, 1747501)]}, "P2875, fixed halls; 'orchestra' = new classification"),
 ("culture", "culture_centres", "gmina", "objects / events / persons",
  {"centres": 377288, "events": 58505, "event_participants": 58506, "participants_per_1000": 410643, "art_groups": 58511, "art_group_members": 58507,
   "clubs_sections": 58509, "club_members": 58510}, "P2284, centra/domy/ośrodki kultury, kluby, świetlice"),
 ("culture", "culture_centres_events_by_type", "gmina", "events / participants",
  {"events": {"total": 377328, "film_shows": 149720, "exhibitions": 149722, "festivals": 475971, "concerts": 475973, "amateur_shows": 149724,
              "professional_shows": 149726, "discos": 149728, "talks_lectures": 149730, "tourist_sport_recreation": 149732, "contests": 151875,
              "theatre_shows": 475975, "conferences": 475977, "interdisciplinary": 475979, "workshops": 475981, "other": 377332},
   "participants": {"total": 377329, "film_shows": 149719, "exhibitions": 149721, "festivals": 475970, "concerts": 475972, "amateur_shows": 149723,
                    "professional_shows": 149725, "discos": 149727, "talks_lectures": 149729, "tourist_sport_recreation": 149731, "contests": 151874,
                    "theatre_shows": 475974, "conferences": 475976, "interdisciplinary": 475978, "workshops": 475980, "other": 377333}}, "P2795"),
 ("culture", "mass_events_count", "gmina", "events",
  {"total": 410716, "artistic_entertainment": 567345, "concerts": 410713, "performances": 410710, "film_shows": 410707, "festivals": 410704,
   "cabarets": 410701, "circus": 410698, "historical_reenactments": 567346, "combined": 410695, "other_artistic": 410692, "interdisciplinary": 410689,
   "sport": 410686, "indoor_total": 410717, "outdoor_total": 410718}, "P3402, imprezy masowe (Ustawa o bezpieczeństwie imprez masowych)"),
 ("culture", "mass_events_participants", "gmina", "persons",
  {"total": 410728, "artistic_entertainment": 410725, "interdisciplinary": 410722, "sport": 410719, "sport_per_1000_pop": 1725016,
   "free_total": 410729, "free_artistic": 410726, "free_interdisciplinary": 410723, "free_sport": 410720,
   "paid_total": 410730, "paid_artistic": 410727, "paid_interdisciplinary": 410724, "paid_sport": 410721}, "P3403"),
 ("culture", "public_libraries", "gmina", "objects / persons / volumes",
  {"libraries_incl_branches": 1230, "readers": 1234, "loans": 1235, "readers_per_1000": 60191}, "P1688, P2381"),
 ("culture", "public_culture_institutions_events", "gmina", "events / persons",
  {"participants_public_total": 1725694, "events_public_total": 1725697}, "P4344; ASSUMPTION: first id of each triple = 'sektor publiczny ogółem' (label truncated in API listing)"),
 ("culture", "art_groups", "gmina", "groups / persons",
  {"groups": 271278, "members": 271279, "members_children_youth": 568135}, "P2792"),
 ("culture", "seniors_in_clubs_utw_share_60plus", "gmina", "%", 1609859, "P3904; members of senior clubs & Universities of Third Age / pop 60+"),
 # 7. Sport
 ("sport", "sports_clubs", "gmina", "clubs / persons",
  seq(1618742, ["clubs", "members", "exercising_total", "exercising_M", "exercising_F", "exercising_u18_total", "exercising_u18_boys",
                "exercising_u18_girls", "sections", "coaches", "instructors", "other_staff", "clubs_per_10k"])
  | {"exercising_per_1000": 1640935, "u18_exercising_per_1000_u18": 1640936}, "P4028 (estimated w/ imputation), biennial; 'ćwiczący' counts memberships in sections (a person in 2 sections counts twice)"),
 ("sport", "sports_facilities", "gmina", "objects",
  {"stadiums": 1601422, "football_pitches": 1601437, "basketball_courts": 1601438, "handball_courts": 1601439, "volleyball_courts": 1601440,
   "multipurpose_pitches": 1601441, "sports_halls_36x19": 1601448, "small_gyms_halls": 1601461, "tennis_courts": 1601474, "indoor_pools": 1601487,
   "shooting_ranges": 1601509, "ice_rinks": 1601510, "ski_jumps": 1601511, "skateparks": 1601512, "outdoor_gyms": 1601604}, "P3881, biennial"),
 # 8. Transport
 ("transport", "cars_by_fuel", "powiat", "vehicles",
  {"total": 1752922, "petrol": 475618, "diesel": 475614, "lpg": 475610, "other_incl_electric_hybrid": 475622}, "P3583, registered passenger cars"),
 ("transport", "cars_by_age", "powiat", "vehicles",
  seq(475696, ["total", "<=1", "2", "3", "4-5", "6-7", "8-9", "10-11", "12-15", "16-20", "21-25", "26-30", "31+"], -6), "P3584, registered passenger cars"),
 ("transport", "cycle_paths", "gmina", "km", {"total_km": 288080, "per_100km2": 1365541, "per_10k_pop": 288082}, "P3164"),
 ("transport", "public_bikes", "gmina", "units", seq(1646070, ["systems", "stations", "bikes_total", "bikes_regular", "bikes_electric", "tandems", "kids", "family", "cargo"]) |
  {"bikes_disabled": 1746955, "rentals": 1646079, "bikes_per_1000": 1646080, "rentals_per_1000": 1646081}, "P4262"),
 ("transport", "urban_transport_passengers_malopolskie", "region", "mixed",
  {"passengers_thousand": 1726304, "passengers_million": 498861, "trips_per_capita": 634994}, "P3603; region level (all urban transport in voivodeship, Kraków MPK dominates)"),
 ("transport", "urban_transport_fleet_use_malopolskie", "region", "mixed",
  {"bus_in_service_pct": 400355, "tram_in_service_pct": 400350, "bus_vehicle_km_thousand": 400356, "tram_vehicle_km_thousand": 400351,
   "bus_km_per_day": 400358, "tram_km_per_day": 400353}, "P3365; region level"),
 # 9. Migration (permanent registrations)
 ("migration", "permanent_migration_by_sex", "gmina", "persons",
  {k: {"total": t, "M": m, "F": f} for k, t, m, f in [
   ("in_total", 1365224, 1365225, 1365226), ("in_internal", 80121, 80115, 80109), ("in_from_abroad", 80122, 80116, 80110),
   ("out_total", 1365229, 1365230, 1365231), ("out_internal", 80123, 80117, 80111), ("out_abroad", 80124, 80118, 80112),
   ("net_total", 1365234, 1365235, 1365236), ("net_internal", 80125, 80119, 80113), ("net_foreign", 80108, 80120, 80114)]}
  | {"net_per_1000": {"total": 1365239}, "net_internal_per_1000": {"total": 498816}, "net_foreign_per_1000": {"total": 745534}}, "P1350; zameldowania na pobyt stały only (students/temporary residents not included)"),
 ("migration", "interpowiat_foreign_migration_by_econ_age", "powiat", "persons",
  {k: {"pre_working": s + 10, "working": s + 5, "post_working": s} for k, s in
   [("in_from_other_powiats", 396230), ("out_to_other_powiats", 396231), ("in_from_abroad", 396232), ("out_abroad", 396233), ("net", 396234)]}, "P3313"),
 # 10. Housing
 ("housing", "dwelling_stock_indicators", "gmina", "m2 / ratio",
  {"avg_floor_area_m2": 60572, "floor_area_per_person_m2": 60573, "dwellings_per_1000": 410600, "avg_rooms": 475702, "persons_per_dwelling": 475703, "persons_per_room": 475704}, "P2430, stan 31 XII"),
 ("housing", "dwelling_stock", "gmina", "dwellings / rooms / m2", {"dwellings": 60811, "rooms": 60808, "floor_area_m2": 60813}, "P2166"),
 ("housing", "new_dwellings_completed", "gmina", "dwellings / m2",
  {"dwellings": 748601, "rooms": 748602, "floor_area_m2": 748603, "individual": 748604, "for_sale_or_rent": 748610, "cooperative": 748607, "municipal": 748616, "social_rent": 748619}, "P3824"),
 ("housing", "occupied_dwellings_by_m2_per_person", "gmina", "dwellings / persons",
  {"dwellings": seq(1723504, ["total", "<7", "7-9.9", "10-14.9", "15-19.9", "20-29.9", "30+"]), "persons": seq(1723511, ["total", "<7", "7-9.9", "10-14.9", "15-19.9", "20-29.9", "30+"])}, "NSP 2021, P4385"),
 # 11. Other: budget, tourism
 ("other", "city_budget_revenue_per_capita", "gmina", "PLN",
  {"total_gminy_incl_cities": 76973, "own_revenue_gminy_incl_cities": 76976, "pit_share_gminy_incl_cities": 149128, "total_city_powiat": 76974, "pit_share_city_powiat": 149129},
  "P2627; PIT share per capita is a proxy for resident income; Kraków's 'city with powiat rights' variant may only be filled for the gmina part"),
 ("other", "tourist_accommodation", "powiat", "objects / beds / persons / nights",
  {"establishments_july": 40914, "establishments_yearround": 40920, "beds_july": 40936, "beds_yearround": 40944, "tourists": 40909, "tourists_foreign": 40928,
   "rooms_rented_hotels": 152455, "rooms_rented_hotels_foreign": 152490, "nights": 40921, "nights_foreign": 40905, "hotel_type_beds_july": 475886, "hotel_type_tourists": 475888},
  "P2017, establishments with 10+ beds; powiat level because the gmina series stops in 2014"),
 ("other", "tourism_indicators", "powiat", "per 1000 pop", {"beds_per_1000": 60300, "nights_per_1000": 60298, "tourists_per_1000": 60297}, "P2396"),
]

def leaves(d):
    if isinstance(d, int): yield d
    else:
        for v in d.values(): yield from leaves(v)

# fetch: all years for every var, per unit, in chunks of 100 ids
need = {}
for _, _, u, _, ids, _ in SPEC: need.setdefault(u, set()).update(leaves(ids))
data, nreq = {}, 0
for u, ids in need.items():
    ids = sorted(ids)
    for i in range(0, len(ids), 100):
        j = get(f"/data/by-unit/{UNITS[u]}", [("var-id", v) for v in ids[i:i + 100]] + [("page-size", 100), ("format", "json")]); nreq += 1
        for x in j["results"]:
            data[(u, x["id"])] = {v["year"]: v["val"] for v in x["values"] if v["val"] is not None}

def pick(u, ids):
    years = {}
    for v in leaves(ids):
        for y in data.get((u, v), {}): years[y] = years.get(y, 0) + 1
    if not years: return None, None
    year = max(y for y in years if years[y] >= 0.5 * max(years.values()))  # latest year at least half-filled
    fill = lambda d: data.get((u, d), {}).get(year) if isinstance(d, int) else {k: fill(v) for k, v in d.items()}
    return year, fill(ids)

out = {"_source": "GUS BDL API (bdl.stat.gov.pl/api/v1), fetched by src/fetch_bdl_extra.py; units: " + json.dumps(UNITS),
       "_note": "year = latest year with >=50% of the item's variables filled (others null); NSP 2021 items are census (31 III 2021). region = Małopolskie, not Kraków."}
missing = []
for topic, name, u, measure, ids, note in SPEC:
    year, val = pick(u, ids)
    if year is None: missing.append(f"{topic}.{name}")
    out.setdefault(topic, {})[name] = {"var_id": ids, "unit": measure, "level": f"{u} {UNITS[u]}", "year": year, "value": val, "note": note}
json.dump(out, open("data/raw/krakow_gus_extra.json", "w"), ensure_ascii=False, indent=1)
print(f"saved data/raw/krakow_gus_extra.json ({nreq} requests); empty items: {missing or 'none'}")
