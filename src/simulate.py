"""Heuristic model for EVENT scenarios: transparent utility -> probabilities, audience by home district,
and a ranking "where to hold it": the same event placed at the population centre of each district.
Usage: python3 src/simulate.py scenarios/concert.json
Other scenario kinds (transit, policy) have no heuristic; use src/agents.py (LLM layer).
"""
import json, sys, numpy as np, pandas as pd
rng = np.random.default_rng(7)
S = json.load(open(sys.argv[1]))
P = pd.read_csv("out/personas.csv"); DS = pd.read_csv("out/districts.csv", index_col="district")
OUTSIDE = "poza Krakowem"

def km(lat, lon, la2, lo2):
    r = np.radians; h = np.sin(r(la2 - lat) / 2) ** 2 + np.cos(r(lat)) * np.cos(r(la2)) * np.sin(r(lo2 - lon) / 2) ** 2
    return 2 * 6371 * np.arcsin(np.sqrt(h))

def utility(P, S, venue_latlon):
    """Vectorised over personas. JS port in viz/template.html - keep in sync."""
    adj = S.get("adjacent", {})
    u = np.full(len(P), -2.2)
    u += np.where(P.fav_genre == S["genre"], 2.0, np.where([S["genre"] in adj.get(f, []) for f in P.fav_genre], 0.4, -1.0))
    u += -np.abs(P.age - S["target_age"]) / 12
    budget = np.maximum(P.net_income, 300) * 0.04          # ASSUMPTION: ~4% of net income on one event
    # ASSUMPTION: penalty grows with log(price / budget), so a 3x-over-budget ticket hurts but does not rule people out
    u += -1.2 * np.log(np.maximum(S["price"], 1) / budget).clip(lower=0)
    u += -S.get("distance_decay", 0.06) * km(P.lat, P.lon, *venue_latlon) + np.where(P.has_car, 0.2, 0)
    if S["weekday"] in ("Fri", "Sat"): u += np.where(P.status.isin(["pracuje", "student"]), 0.4, 0)
    # ASSUMPTION: many out-of-town students go home for the weekend
    away = {"Fri": 0.3, "Sat": 0.8, "Sun": 0.8}.get(S["weekday"], 0)
    u -= np.where(P.origin == "student_spoza", away, 0)
    u -= np.where((P.age < 16) | (P.age > 80), 2.5, 0)
    return u

p_of = lambda latlon: 1 / (1 + np.exp(-utility(P, S, latlon)))
scale = DS.people.sum() / DS.personas.sum()            # people represented by one persona
P["p_attend"] = p_of(S["venue_latlon"])
P["attend"] = rng.random(len(P)) < P.p_attend
P["dist_km"] = km(P.lat, P.lon, *S["venue_latlon"])
P.to_csv("out/sim_heuristic.csv", index=False)
P.groupby("cell").agg(lat=("lat", "mean"), lon=("lon", "mean"), n=("id", "size"), p=("p_attend", "mean")).to_csv("out/sim_by_cell.csv")

print("Scenario:", S["name"])
print("p by distance to venue (km):\n" + P.groupby(pd.cut(P.dist_km, [0, 3, 6, 10, 20, 99]), observed=True).p_attend.mean().round(3).to_string())
print(P.groupby(pd.cut(P.age, [0, 17, 24, 34, 49, 64, 120]), observed=True).p_attend.mean().round(3).to_string())
print("by origin:\n" + P.groupby("origin").p_attend.mean().round(3).to_string())
total = P.p_attend.sum() * scale
print(f"Total expected (metro scale): {int(total // 100 * 100)} | venue capacity: {S.get('capacity')}")

# audience by home district: where do the interested people live?
home = P.groupby("district").agg(personas=("id", "size"), mean_p=("p_attend", "mean"))
home["interested"] = (home.personas * home.mean_p * scale).round(-2)
home["per_1000"] = (home.mean_p * 1000).round(1)
home["share_%"] = (home.interested / home.interested.sum() * 100).round(1)
print("\nAudience by home district (for the venue above):")
print(home.sort_values("interested", ascending=False)[["interested", "per_1000", "share_%"]].to_string())

# where to hold it: same event, venue at each district's population centre
rank = []
for d, r in DS.drop(OUTSIDE, errors="ignore").iterrows():
    p = p_of((r.lat, r.lon)); dk = km(P.lat, P.lon, r.lat, r.lon)
    rank.append(dict(district=d, lat=r.lat, lon=r.lon, interested=round(p.sum() * scale, -2),
                     within_5km_pct=round(p[dk < 5].sum() / p.sum() * 100, 1), local_pct=round(p[P.district == d].sum() / p.sum() * 100, 1)))
rank = pd.DataFrame(rank).sort_values("interested", ascending=False).reset_index(drop=True)
rank.index += 1
rank.to_csv("out/district_ranking.csv")
print("\nWhere to hold it (venue at district population centre):")
print(rank[["district", "interested", "within_5km_pct", "local_pct"]].to_string())
