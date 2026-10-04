"""Archetypes test (idea from MIT Media Lab's AgentTorch / Large Population Models): group residents with the same
decision-relevant traits, ask Jev ONCE per archetype (one representative persona), give the answer to every member.
Compared here with asking every person individually (the 600-person SCT run) and with the CEM poll.
Representatives are drawn from OUTSIDE the evaluated sample, so a person is never scored with their own answer.
Usage: python3 src/archetypes.py [--n 600]   (size of the individually asked sample; runs it through policy.run if not cached)
"""
import asyncio, json, os, sys
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy, jev_sim

SCT = "Strefa Czystego Transportu w całym Krakowie: nie wjadą benzyniaki starsze niż 27 lat i diesle starsze niż 17 lat"
KEYS = {"6 cech": ["band", "sex", "status", "has_car", "aff", "inc"], "7 cech (+dzieci)": ["band", "sex", "status", "has_car", "aff", "inc", "kids"]}


def frame(sp): return policy.adults(sp)


async def ask(sp, rows, cache):
    """Jev answers for the given personas (cached by persona id: the same person + question = the same state)."""
    from typesafe_sdk import AsyncTypeSafeClient, TypeSafeError
    done = {json.loads(l)["id"]: json.loads(l) for l in cache.read_text().splitlines()} if cache.exists() else {}
    todo = [r for r in rows.itertuples() if r.id not in done]; qs = policy.questions(sp); sem = asyncio.Semaphore(20)
    async with AsyncTypeSafeClient(timeout=30.0) as c:
        with cache.open("a") as f:
            async def one(r):
                async with sem:
                    try: a = await c.system_one(state=policy.state_for(r, sp), questions=qs)
                    except TypeSafeError: return
                row = {"id": int(r.id), "support": a.nouls["support"].noul}; done[row["id"]] = row; f.write(json.dumps(row) + "\n")
            await asyncio.gather(*(one(r) for r in todo))
    print(f"  Jev: {len(todo)} nowych zapytań")
    return {i: x["support"] for i, x in done.items()}


def main():
    jev_sim.load_env(); sp = policy.spec(SCT); K = frame(sp)
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 600
    ind_file = Path(f"out/policy/{policy.spec_key(sp)}_{n}_7.jsonl")
    if not ind_file.exists() or sum(1 for _ in ind_file.open()) < n * 0.98: asyncio.run(policy.run(sp, n=n))
    IND = {json.loads(l)["id"]: json.loads(l)["support"] for l in ind_file.read_text().splitlines()}
    S = K[K.id.isin(IND)].copy(); S["ind"] = S.id.map(IND)
    print(f"Próba indywidualna: {len(S)} dorosłych, średnie P(popiera) {S.ind.mean():.1%} (sondaż CEM: 61%)\n")
    cache = Path(f"out/policy/{policy.spec_key(sp)}_archetypes.jsonl")
    rng = np.random.default_rng(7); out = {}
    for name, cols in KEYS.items():
        G = K.groupby(cols)
        reps, reps2 = {}, {}
        for k, g in G:
            pool = g[~g.id.isin(IND)]
            if len(pool): reps[k] = int(rng.choice(pool.id))
            if len(pool) >= 2: reps2[k] = int(rng.choice(pool.id[pool.id != reps[k]]))
        ids = set(reps.values()) | (set(reps2.values()) if name == "6 cech" else set())
        print(f"{name}: {G.ngroups} archetypów → {len(ids)} zapytań do Jev")
        A = asyncio.run(ask(sp, K[K.id.isin(ids)], cache))
        key = lambda r: tuple(getattr(r, c) for c in cols)
        S[name] = [A.get(reps.get(key(r)), np.nan) for r in S.itertuples()]
        ok = S[name].notna()
        K[name] = [A.get(reps.get(key(r)), np.nan) for r in K.itertuples()]
        drv = S[ok & S.has_car]
        out[name] = {
            "pokrycie próby": f"{ok.mean():.0%}",
            "średnie P: archetypy (próba)": f"{S.loc[ok, name].mean():.1%}",
            "średnie P: indywidualnie (te same osoby)": f"{S.loc[ok, 'ind'].mean():.1%}",
            "średnie P: archetypy, CAŁE miasto": f"{K[name].mean():.1%} (n={K[name].notna().sum()})",
            "korelacja osoba po osobie": f"{np.corrcoef(S.loc[ok, name], S.loc[ok, 'ind'])[0, 1]:.2f}",
            "średni błąd na osobę": f"{np.mean(abs(S.loc[ok, name] - S.loc[ok, 'ind'])):.3f}",
            "kobiety > mężczyźni": f"{S.loc[ok & (S.sex == 'K'), name].mean():.1%} vs {S.loc[ok & (S.sex == 'M'), name].mean():.1%}",
            "bez auta > z autem": f"{S.loc[ok & ~S.has_car, name].mean():.1%} vs {drv[name].mean():.1%}",
            "kierowcy za > przeciw": f"{(drv[name] > .5).mean():.0%} vs {(drv[name] < .5).mean():.0%}",
        }
        for g in sp["impacts"]:
            m = ok & S.aff.str.contains(g["label_pl"], regex=False)
            out[name][f"grupa: {g['label_pl']}"] = f"archetypy {S.loc[m, name].mean():.0%} vs indywidualnie {S.loc[m, 'ind'].mean():.0%} (n={m.sum()})"
        if name == "6 cech":   # heterogeneity inside an archetype: two different members, same key
            pairs = [(A[reps[k]], A[reps2[k]]) for k in reps2 if reps[k] in A and reps2[k] in A]
            a, b = np.array(pairs).T
            out[name]["dwóch członków tego samego archetypu: korelacja"] = f"{np.corrcoef(a, b)[0, 1]:.2f} (średnia różnica {np.mean(abs(a - b)):.3f}, par {len(pairs)})"
    # district map: noise of the 600-person sample vs the whole city through archetypes
    D = pd.DataFrame({f"indywidualnie ({n})": S.groupby("district").ind.mean(), "n": S.groupby("district").size(),
                      "archetypy, całe miasto": K.groupby("district")["7 cech (+dzieci)"].mean(), "N": K.groupby("district").size()})
    for name, o in out.items():
        print(f"\n== {name}"); [print(f"  {k:52s} {v}") for k, v in o.items()]
    print("\nDzielnice (średnie P):"); print(D.round(3).sort_values("archetypy, całe miasto").to_string())
    print(f"\nRozrzut między dzielnicami (odch. std): indywidualnie {D[f'indywidualnie ({n})'].std():.3f}, archetypy {D['archetypy, całe miasto'].std():.3f}")
    print(f"Korelacja map dzielnic: {D[f'indywidualnie ({n})'].corr(D['archetypy, całe miasto']):.2f}")


if __name__ == "__main__":
    main()
