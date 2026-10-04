"""Experiments on how to ask Jev: same personas, same event, different state / wording.
Reports the raw level, ranking stability (Spearman vs the first variant) and the SPREAD between groups after anchoring
every variant to the same total (ratio of probabilities, e.g. 25-34 vs 65+). Reference spread from GUS kultura 2024
(urban, non-classical concerts): 25-34 vs 65+ ≈ 2.5-3x.
Usage: python3 src/jev_experiment.py scenarios/concert.json [--n 300] [--anchor 15000]
"""
import asyncio, json, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "src")
import jev_sim
from schema import load_scenario

# name -> build() options. All use the strict default question from jev_sim.questions().
VARIANTS = {
    "B_raw_hobbies": dict(fit=False, leisure="all"),
    "D_fit_plus_hobbies": dict(fit=True, leisure="all"),
    "E_fit_only": dict(fit=True, leisure="none"),
    "F_composite": dict(fit=True, leisure="none"),   # E's state, 3 atomic questions multiplied in code
    "F2_composite": dict(fit=True, leisure="none"),  # F + neutral 'afford', habit inside person, no peer fact
}
# Jev docs, "Composite scoring": break a judgement into atomic questions, combine with weights in code.
# ASSUMPTION: the three factors are independent, P(go) = P(want) * P(afford) * P(free).
ATOMIC = {
    "want": ("This person would genuinely enjoy this specific event: the artist, the music and the setting.",
             "they would enjoy it", "it does not appeal to them"),
    "afford": ("This person can comfortably pay for the ticket without giving up something important.",
               "the ticket is easily affordable for them", "the ticket would be a real strain or someone else would have to pay"),
    "free": ("This person is free and has the energy to go out to this venue at this time, given work, family duties and travel time.",
             "they can realistically go that evening", "work, family, health or travel makes it hard"),
}


ATOMIC2 = {**ATOMIC, "afford": ("Paying for the ticket is comfortable for this person's budget (their own income or their household's).",
                                 "the ticket fits their budget comfortably", "the ticket would be a strain on their budget")}


def f2_state(state, r):
    """Move the concert habit into the person, drop the peer-rate fact, explain who pays for people without income."""
    st = json.loads(json.dumps(state)); ps = st["personal_situation"]; per = st["person"]
    habit = next((v for k, v in ps.items() if k.startswith("how_often_they_go")), None)
    for k in [k for k in ps if k.startswith("how_often_they_go") or k.startswith("among_people")]: ps.pop(k)
    if habit: per["goes_to_live_concerts"] = habit
    if r.net_income <= 0:
        hh = str(r.household or "")
        per["monthly_net_income_pln"] = ("no own income; lives on their partner's income" if "partner" in hh else
                                         "no own income; supported by parents" if r.age < 26 or "rodzic" in hh else "no own income; supported by family")
        ps["ticket_cost_for_this_person"] = "paid from the household budget"
    return st


def anchor(p, total, scale):
    lg = np.log(p.clip(1e-4, 1 - 1e-4) / (1 - p.clip(1e-4, 1 - 1e-4))); lo, hi = -15.0, 15.0
    for _ in range(60):
        c = (lo + hi) / 2; t = (1 / (1 + np.exp(-(lg + c)))).sum() * scale; lo, hi = (c, hi) if t < total else (lo, c)
    return 1 / (1 + np.exp(-(lg + c)))


def main():
    jev_sim.load_env(); args = sys.argv[1:]
    S = load_scenario(args[0]); n = int(args[args.index("--n") + 1]) if "--n" in args else 300
    total = int(args[args.index("--anchor") + 1]) if "--anchor" in args else (S.anchor_total or 15000)
    P = pd.read_csv("out/personas.csv"); DS = pd.read_csv("out/districts.csv", index_col="district")
    Q15 = P[P.age >= 15]; scale = DS.people.sum() / DS.personas.sum() * len(Q15) / n
    Q = Q15.sample(n, random_state=7); qs = jev_sim.questions(S); key = next(iter(qs))
    out = Path("out/jev/experiment"); out.mkdir(parents=True, exist_ok=True); res = {}
    for name, opts in VARIANTS.items():
        items, _ = jev_sim.build(Q, S, calibrate=False, **opts)
        cache = out / f"{Path(args[0]).stem}_{name}.jsonl"
        done = {json.loads(l)["id"] for l in cache.read_text().splitlines()} if cache.exists() else set()
        todo = [(i, s) for i, s in items if i not in done]
        if name == "F2_composite":
            from typesafe_sdk import Noul
            items = [(i, f2_state(s, Q.set_index("id").loc[i])) for i, s in items]
            todo = [(i, s) for i, s in items if i not in done]
            aq = {k: Noul(instructions=i, criteria={"true": t, "false": f}) for k, (i, t, f) in ATOMIC2.items()}
            if todo: print(name); asyncio.run(jev_sim.run(todo, aq, cache, 40))
            D = pd.read_json(cache, lines=True).drop_duplicates("id").set_index("id")
            res[name] = D.want * D.afford * D.free
            for k in ATOMIC2: res["F2_" + k] = D[k]
            continue
        if name == "F_composite":
            from typesafe_sdk import Noul
            aq = {k: Noul(instructions=i, criteria={"true": t, "false": f}) for k, (i, t, f) in ATOMIC.items()}
            if todo: print(name); asyncio.run(jev_sim.run(todo, aq, cache, 40))
            D = pd.read_json(cache, lines=True).drop_duplicates("id").set_index("id")
            res[name] = D.want * D.afford * D.free
            for k in ATOMIC: res["F_" + k] = D[k]
            continue
        if todo: print(name); asyncio.run(jev_sim.run(todo, {key: qs[key]}, cache, 40))
        res[name] = pd.read_json(cache, lines=True).drop_duplicates("id").set_index("id")[key]
    R = pd.DataFrame(res).join(Q.set_index("id"))
    first = next(iter(VARIANTS))
    groups = {"25-34 / 65+": (R.age.between(25, 34), R.age >= 65), "chodzą na koncerty / nie": (R.c_concert_other, ~R.c_concert_other),
              "lubi ten gatunek / nie": (R.fav_genre == S.genre, R.fav_genre != S.genre) if getattr(S, "genre", None) else None,
              "pracuje+student / bezrob.+niepr.": (R.status.isin(["pracuje", "student"]), R.status.isin(["bezrobotny", "niepracujący"]))}
    rows = []
    for v in list(VARIANTS) + ["F_" + k for k in ATOMIC] + ["F2_" + k for k in ATOMIC2]:
        a = anchor(R[v], total, scale); row = {"wariant": v, "surowe P": R[v].mean(), "Spearman vs " + first[:1]: R[v].corr(R[first], method="spearman")}
        for g, m in groups.items():
            if m is not None: row[g] = a[m[0]].mean() / a[m[1]].mean()
        rows.append(row)
    T = pd.DataFrame(rows).set_index("wariant")
    print(f"\nRozpiętość = iloraz P po zakotwiczeniu do {total:,} (GUS, koncerty, 25-34 / 65+ ≈ 2.5-3x):")
    print(T.round(2).to_string())
    R.to_csv(out / f"{Path(args[0]).stem}_variants.csv")


if __name__ == "__main__":
    main()
