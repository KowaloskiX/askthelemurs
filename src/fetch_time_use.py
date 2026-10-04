"""Time use by day of week, time of day and month for Poland: Eurostat HETUS 2020 wave (data supplied by GUS).
-> data/raw/hetus_pl_2020.json
  week:   participation rate (%) by sex x day type (Mon-Thu, Fri, Sat, Sun) for 'Entertainment and culture' and
          'Socialising with others' (main activity, share of people doing it at all that day)
  hours:  participation rate (%) in leisure (social life, culture, sport, hobbies; excluding TV) by sex x 10-min slot
  months: participation rate (%) in 'Entertainment and culture' by sex x month
Three requests to the Eurostat dissemination API (no key needed).
"""
import json, requests

API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"


def get(ds, **f):
    r = requests.get(API + ds, params={"geo": "PL", "format": "JSON", "lang": "EN", **f}, timeout=120)
    r.raise_for_status(); return r.json()


def table(d, keep):
    """Flatten Eurostat JSON-stat into {(dim values...): value} for the dimensions in `keep`."""
    dims = d["id"]; sizes = d["size"]; idx = {k: {v: i for v, i in d["dimension"][k]["category"]["index"].items()} for k in dims}
    labels = {k: d["dimension"][k]["category"]["label"] for k in dims}
    out = {}
    for flat, val in d["value"].items():
        flat = int(flat); pos = {}
        for k, s in zip(reversed(dims), reversed(sizes)): pos[k] = flat % s; flat //= s
        key = tuple(next(c for c, i in idx[k].items() if i == pos[k]) for k in keep)
        out[key] = val
    return out, labels


week, wl = table(get("tus_20week", unit="PTP_RT"), ["sex", "daysweek", "acl18"])
acts = {code: lab for code, lab in wl["acl18"].items() if lab in ("Entertainment and culture", "Socialising with others (visits, celebrations and other)")}
days = {code: lab for code, lab in wl["daysweek"].items() if lab in ("Monday to Thursday", "Friday", "Saturday", "Sunday")}
hours, hl = table(get("tus_20startime"), ["sex", "startime", "acl18"])
leis = next(c for c, l in hl["acl18"].items() if l.startswith("Leisure, social"))
months, ml = table(get("tus_20period", unit="PTP_RT"), ["sex", "month", "acl18"])
ent = next(c for c, l in ml["acl18"].items() if l == "Entertainment and culture")
SEX = {"T": "total", "M": "M", "F": "K"}
out = {"_source": "Eurostat HETUS 2020 wave, Poland (data supplied by GUS): tus_20week, tus_20startime, tus_20period. Participation rate in %.",
       "week": {SEX[s]: {wl["daysweek"][d]: {wl["acl18"][a]: week.get((s, d, a)) for a in acts} for d in days} for s in SEX},
       "hours": {SEX[s]: {hl["startime"][t]: hours.get((s, t, leis)) for t in hl["startime"] if t != "TOTAL" and (s, t, leis) in hours} for s in SEX},
       "months": {SEX[s]: {ml["month"][m]: months.get((s, m, ent)) for m in ml["month"]} for s in SEX}}
json.dump(out, open("data/raw/hetus_pl_2020.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out["week"]["total"], indent=1, ensure_ascii=False))
h = out["hours"]["total"]; print({k: h[k] for k in list(h)[::12]})
print({k: v for k, v in out["months"]["total"].items() if "to" not in k})
