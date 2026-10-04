"""Political leaning of every adult persona (Sejm 2023 vote), from three official sources:
1. local prior: PKW results of the polling precincts near the persona's home (data/raw/pkw_krakow.json, src/fetch_pkw_krakow.py),
   distance-weighted (ASSUMPTION: a precinct's station stands for its area; kernel 0.7 km, max 1.5 km, else the district);
2. individual traits: multinomial odds ratios vs PiS by sex, age, education, occupation from the 2023 IPSOS exit poll
   (N = 75,675; Cześnik, Szczupska, Sychowiec, Polish Sociological Review 2025, Table 1, Model 1; data/raw/exitpoll_2023_czesnik.pdf);
3. raking: within each district the personas' expected shares are scaled to the real PKW district result.
ASSUMPTIONS: non-voters (18% in Kraków) share the preferences of voters in their place and group; 'Inne' (BS, PJJ, ~2.5%)
gets odds ratio 1. Output: out/attitudes.csv (id, p_<party>, vote = one sampled party).
Usage: python3 src/attitudes.py
"""
import json
import numpy as np, pandas as pd

PARTIES = ["PiS", "KO", "TD", "Lewica", "Konf", "Inne"]
COMMITTEE = {"PiS": "PiS", "KO": "KO", "Trzecia Droga": "TD", "Lewica": "Lewica", "Konfederacja": "Konf",
             "Bezpartyjni Samorządowcy": "Inne", "Polska Jest Jedna": "Inne"}
# Table 1, Model 1 (sociodemographics only): odds ratio vs PiS for KO, TD, NL (Lewica), Konf
OR = {"sex": {"M": [0.98, 1.08, 0.78, 2.79], "K": [1, 1, 1, 1]},
      "age": {"18-29": [1, 1, 1, 1], "30-39": [0.55, 0.58, 0.35, 0.33], "40-49": [0.59, 0.47, 0.27, 0.13], "50-59": [0.53, 0.35, 0.17, 0.07], "60+": [0.48, 0.25, 0.14, 0.03]},
      "edu": {"primary": [1, 1, 1, 1], "vocational": [1.44, 1.35, 1.52, 1.26], "secondary": [2.74, 2.39, 2.41, 2.10], "higher": [4.36, 4.02, 4.04, 2.73]},
      "occ": {"professional": [1, 1, 1, 1], "service": [0.65, 0.73, 0.79, 0.78], "worker": [0.48, 0.57, 0.57, 0.82], "student": [1.33, 1.33, 1.86, 1.07],
              "unemployed": [0.43, 0.49, 0.57, 0.79], "pensioner": [0.53, 0.49, 0.68, 0.48], "other": [0.55, 0.65, 0.66, 0.84]}}
EDU = {"podstawowe": "primary", "zasadnicze zawodowe": "vocational", "średnie": "secondary", "wyższe": "higher"}


def occupation(r):
    """ASSUMPTION: no occupation in the personas; employed people are split by education (higher = professional,
    secondary = administrative/service, lower = worker)."""
    if r.status in ("student", "uczeń"): return "student"
    if r.status == "emeryt": return "pensioner"
    if r.status == "bezrobotny": return "unemployed"
    if r.status == "niepracujący": return "other"
    return {"wyższe": "professional", "średnie": "service"}.get(r.education, "worker")


def age_band(a): return "18-29" if a < 30 else "30-39" if a < 40 else "40-49" if a < 50 else "50-59" if a < 60 else "60+"


def km(lat, lon, la2, lo2):
    r = np.radians; h = np.sin(r(la2 - lat) / 2) ** 2 + np.cos(r(lat)) * np.cos(r(la2)) * np.sin(r(lo2 - lon) / 2) ** 2
    return 2 * 6371 * np.arcsin(np.sqrt(h))


def main():
    D = json.load(open("data/raw/pkw_krakow.json"))
    pr = [p for p in D["precincts"] if p["election"] == "sejm2023" and not p["special"] and p.get("lat")]
    plat = np.array([p["lat"] for p in pr]); plon = np.array([p["lon"] for p in pr])
    V = np.zeros((len(pr), len(PARTIES)))
    for i, p in enumerate(pr):
        for c, v in p["results"].items(): V[i, PARTIES.index(COMMITTEE[c])] += v
    dist = {d: np.array([sum(v for c, v in x["votes"].items() if COMMITTEE[c] == k) for k in PARTIES], float)
            for d, x in D["district_aggregates"]["sejm2023"]["by_district"].items()}
    dist = {d: v / v.sum() for d, v in dist.items()}

    P = pd.read_csv("out/personas.csv"); K = P[(P.age >= 18) & (P.district != "poza Krakowem")].copy()
    prior = np.zeros((len(K), len(PARTIES)))
    for j, r in enumerate(K.itertuples()):
        d = km(r.lat, r.lon, plat, plon); w = np.exp(-d / 0.7) * (d <= 1.5)
        prior[j] = (w @ V) / (w @ V).sum() if w.sum() > 0 else dist[r.district]
    odds = np.ones((len(K), len(PARTIES)))
    for j, r in enumerate(K.itertuples()):
        o = np.array(OR["sex"][r.sex]) * np.array(OR["age"][age_band(r.age)]) * np.array(OR["edu"][EDU.get(r.education, "secondary")]) * np.array(OR["occ"][occupation(r)])
        odds[j, 1:5] = o
    p = prior * odds; p /= p.sum(1, keepdims=True)
    for _ in range(50):   # rake to the PKW district result
        for d, tgt in dist.items():
            m = (K.district == d).values
            if m.any(): p[m] *= tgt / p[m].mean(0); p[m] /= p[m].sum(1, keepdims=True)
    rng = np.random.default_rng(7)
    out = pd.DataFrame(p.round(4), columns=[f"p_{k}" for k in PARTIES]); out.insert(0, "id", K.id.values)
    out["vote"] = [PARTIES[rng.choice(len(PARTIES), p=x / x.sum())] for x in p]
    out.to_csv("out/attitudes.csv", index=False)

    # checks: Kraków total vs PKW, and by age / education vs the exit poll big-city pattern (direction only)
    tot = np.array([sum(sum(v for c, v in x["votes"].items() if COMMITTEE[c] == k) for x in D["district_aggregates"]["sejm2023"]["by_district"].values()) for k in PARTIES], float)
    print("Kraków, udział (persony vs PKW):", "  ".join(f"{k} {p[:, i].mean():.1%}/{tot[i] / tot.sum():.1%}" for i, k in enumerate(PARTIES)))
    K = K.assign(**{k: p[:, i] for i, k in enumerate(PARTIES)}, band=K.age.map(age_band))
    print("\nWg wieku (oczekiwane udziały):"); print(K.groupby("band")[PARTIES].mean().round(3).to_string())
    print("\nWg wykształcenia:"); print(K.groupby("education")[PARTIES].mean().round(3).to_string())
    print(f"\nzapisano out/attitudes.csv ({len(out)} dorosłych)")


if __name__ == "__main__":
    main()
