"""Pull GUS BDL data for a city in 2 requests (IDs already known).
Usage: python3 src/fetch_bdl.py [gmina_id] [powiat_id] [year]
Defaults: Kraków (gmina 011212161011, powiat 011212161000), 2025.
Key: BDL_KEY in .env (free: https://bdl.stat.gov.pl/api/v1/client). Throttled to 1 req/1.2 s.
"""
import json, os, sys, time, requests
BASE = "https://bdl.stat.gov.pl/api/v1"
GROUPS = ['0-4','5-9','10-14','15-19','20-24','25-29','30-34','35-39','40-44','45-49','50-54','55-59','60-64','65-69','70-74','75-79','80-84','85+']
M = [72301,72302,72303,72304,47711,47736,47724,47712,47725,47728,47706,47715,47721,72243,76018,76019,76020,76021]
K = [72296,72297,72298,72299,47738,47696,47695,47716,47698,47727,47723,47702,47693,72241,76014,76015,76016,76017]
TOTAL, WAGE, UNEMP, CARS = 72305, 64428, 60270, 60533   # P2137 ogółem, P2497, P2392, P2420

def key():
    for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
        if line.startswith("BDL_KEY="): return line.split("=", 1)[1].strip()
_last = [0.0]
def get(path, params):
    time.sleep(max(0, 1.2 - (time.time() - _last[0]))); _last[0] = time.time()
    r = requests.get(BASE + path, params=params, headers={"X-ClientId": key()}, timeout=30)
    if r.status_code == 429: sys.exit("429 rate limited: " + r.text[:200])
    r.raise_for_status(); return {x["id"]: {v["year"]: v["val"] for v in x["values"]} for x in r.json()["results"]}

gmina, powiat, year = (sys.argv[1:4] + [None] * 3)[:3]
gmina, powiat, year = gmina or "011212161011", powiat or "011212161000", year or "2025"
a = get(f"/data/by-unit/{gmina}", [("var-id", v) for v in M + K + [TOTAL]] + [("year", year), ("page-size", 100), ("format", "json")])
b = get(f"/data/by-unit/{powiat}", [("var-id", v) for v in (WAGE, UNEMP, CARS)] + [("year", year), ("page-size", 20), ("format", "json")])
out = {"_source": f"GUS BDL API, gmina {gmina}, powiat {powiat}, rok {year}",
       "city": {"population": a[TOTAL][year], "avg_gross_wage": b[WAGE][year], "unemployment_rate": b[UNEMP][year], "cars_per_1000": b[CARS][year]},
       "units": {"Kraków": {"age_sex": {"M": dict(zip(GROUPS, (a[i][year] for i in M))), "K": dict(zip(GROUPS, (a[i][year] for i in K)))}}}}
json.dump(out, open("data/raw/krakow_demo.json", "w"), ensure_ascii=False, indent=1)
print("saved data/raw/krakow_demo.json, population", out["city"]["population"])
