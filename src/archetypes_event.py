"""Archetypes for events (same test as src/archetypes.py for the SCT): individual answers of n residents vs the answer of one
representative per archetype (drawn outside the sample). Compares want / free / afford, p_base, groups and the venue ranking.
Usage: python3 src/archetypes_event.py [--n 1200]
"""
import asyncio, json, os, sys
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import planner, jev_sim

RAP = {"category": "koncert", "genre": "hip-hop", "price": 60.0, "age": "18-30", "scale": "club", "setting": "indoor", "day": "Fri", "time": "evening"}


async def ask(s, rows, cache):
    from typesafe_sdk import AsyncTypeSafeClient, TypeSafeError
    done = {json.loads(l)["id"]: json.loads(l) for l in cache.read_text().splitlines()} if cache.exists() else {}
    todo = [r for r in rows.itertuples() if r.id not in done]; qs = planner.questions(s); sem = asyncio.Semaphore(20)
    async with AsyncTypeSafeClient(timeout=30.0) as c:
        with cache.open("a") as f:
            async def one(r):
                async with sem:
                    try: a = await c.system_one(state=planner.state_for(r, s), questions=qs)
                    except TypeSafeError: return
                row = {"id": int(r.id), **{q: v.noul for q, v in a.nouls.items()}}; done[row["id"]] = row; f.write(json.dumps(row) + "\n")
            await asyncio.gather(*(one(r) for r in todo))
    print(f"  Jev: {len(todo)} nowych zapytań"); return done


def main():
    jev_sim.load_env(); n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 1200; s = RAP
    A = asyncio.run(planner.run(s, n=n))   # individual sample (cached)
    K = planner.arch_frame(s); g = K.groupby(planner.EVENT_KEY)
    print(f"Archetypów: {g.ngroups} (osób 15+: {len(K)}), próba indywidualna: {len(A)}")
    rng = np.random.default_rng(7); reps = {}
    for k, grp in g:
        pool = grp[~grp.id.isin(A.id)]
        if len(pool): reps[k] = int(rng.choice(pool.id))
    R = asyncio.run(ask(s, K[K.id.isin(set(reps.values()))], Path(f"out/planner/{planner.spec_key(s)}_archreps.jsonl")))
    S = K[K.id.isin(A.id)].merge(A[["id", "want", "free"] + (["afford"] if "afford" in A else []) + ["p_base"]], on="id")
    key = lambda r: tuple(getattr(r, c) for c in planner.EVENT_KEY)
    for q in ["want", "free", "afford"]:
        if q in S: S[f"{q}_a"] = [R.get(reps.get(key(r)), {}).get(q, np.nan) for r in S.itertuples()]
    S["p_a"] = S.want_a * S.free_a * (S.afford_a if "afford_a" in S else 1); ok = S.p_a.notna()
    print(f"pokrycie próby: {ok.mean():.0%}")
    for q in ["want", "free", "afford"]:
        if q in S: print(f"  {q:7s} średnio archetypy {S.loc[ok, q + '_a'].mean():.3f} vs indyw. {S.loc[ok, q].mean():.3f}, korelacja {np.corrcoef(S.loc[ok, q + '_a'], S.loc[ok, q])[0, 1]:.2f}")
    print(f"  p_base  średnio archetypy {S.loc[ok, 'p_a'].mean():.3f} vs indyw. {S.loc[ok, 'p_base'].mean():.3f}, korelacja {np.corrcoef(S.loc[ok, 'p_a'], S.loc[ok, 'p_base'])[0, 1]:.2f}")
    S["seg"] = [planner.segment(r) for r in S.itertuples()]
    print("\nWg segmentu (średnie p_base): archetypy vs indywidualnie")
    print(S[ok].groupby("seg")[["p_a", "p_base"]].mean().round(3).assign(n=S[ok].groupby("seg").size()).to_string())


if __name__ == "__main__":
    main()
