"""Validation on real votes: Kraków civic budget (Budżet Obywatelski, data/raw/bo_krakow.json, src/fetch_bo_krakow.py).
For each district pool of an edition, the district's simulated residents (archetypes, see src/policy.py) see every project
(title + short description) and Jev judges, per project, whether this person would put it among their 3 district picks.
Predicted score = sum over residents (people-weighted) of P(pick). Compared with the real points (3/2/1 per voter) by
Spearman rank correlation within each pool, and by how many real winners are in the predicted top.
Baselines: gpt-5.4 ranking the same list without residents ("what ChatGPT would say"), and 'cheaper project wins'.
Known limit: real BO results depend on mobilisation (schools, councils, NGOs collecting votes), which no census-based model knows.
Usage: python3 src/validate_bo.py [--edition 2025] [--districts 4] [--no-llm]
"""
import asyncio, hashlib, json, os, sys
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_sim, event_agent
from jev_sim import person_state

BATCH = 20   # projects per Jev call
KEY = ["band", "sex", "status", "has_car", "kids", "inc"]   # archetype traits (no policy groups here)
CACHE = Path("out/bo"); CACHE.mkdir(parents=True, exist_ok=True)


def residents(district):
    """ASSUMPTION: voters = residents 16+ of the district (BO Kraków lets residents vote; age limit not modelled)."""
    P = pd.read_csv("out/personas.csv"); K = P[(P.age >= 16) & (P.district == district)].copy()
    K["band"] = pd.cut(K.age, [15, 24, 34, 49, 64, 74, 120]).astype(str)
    K["inc"] = pd.qcut(K.net_income.rank(method="first"), 3, labels=["lo", "mid", "hi"]).astype(str)
    K["kids"] = K.children.fillna(0) > 0
    K["arch"] = K.groupby(KEY, sort=False).ngroup()
    rng = np.random.default_rng(7); reps = K.groupby("arch").id.apply(lambda s: int(rng.choice(s.values)))
    K["rep"] = K.arch.map(reps)
    return K, reps


def proj_text(p):
    d = (p.get("short_description") or "").strip()
    return f"{p['title'].strip().capitalize()}. {d[:260].capitalize()}" + (f" Cost about {int(p['cost_pln'] / 1000)} thousand PLN." if p.get("cost_pln") else "")


async def jev_pool(K, reps, pool, district, edition):
    from typesafe_sdk import AsyncTypeSafeClient, Noul, TypeSafeError
    batches = [pool[i:i + BATCH] for i in range(0, len(pool), BATCH)]
    f = CACHE / f"jev_{edition}_{hashlib.sha1(district.encode()).hexdigest()[:6]}.jsonl"
    done = {}
    if f.exists():
        for l in f.read_text().splitlines(): x = json.loads(l); done[(x["rep"], x["b"])] = x["p"]
    T = {r.id: r for r in K.itertuples()}; sem = asyncio.Semaphore(20); new = [0]
    ctx = (f"Kraków civic budget vote {edition}. Residents of {district} district choose up to 3 projects for their district "
           "from the list below (and the city pays for the winners). Projects are proposed by residents.")
    async with AsyncTypeSafeClient(timeout=40.0) as c:
        with f.open("a") as out:
            async def one(rep, b, projects):
                if (rep, b) in done: return
                st = {"person": person_state(T[rep], "all"), "situation": ctx}
                qs = {f"p{i}": Noul(instructions=f"This person would put this project among their 3 picks: {proj_text(p)}",
                                    criteria={"true": "it would be one of their picks", "false": "they would pick other projects"}) for i, p in enumerate(projects)}
                async with sem:
                    try: a = await c.system_one(state=st, questions=qs)
                    except TypeSafeError: return
                done[(rep, b)] = [a.nouls[f"p{i}"].noul for i in range(len(projects))]; new[0] += 1
                out.write(json.dumps({"rep": int(rep), "b": b, "p": done[(rep, b)]}) + "\n")
            await asyncio.gather(*(one(int(rep), b, pr) for rep in reps.values for b, pr in enumerate(batches)))
    w = K.groupby("rep").size()   # members per representative (personas; same weight within a district)
    score = np.zeros(len(pool))
    for rep, n in w.items():
        ps = sum((done.get((rep, b), [np.nan] * len(pr)) for b, pr in enumerate(batches)), [])
        score += n * np.nan_to_num(np.array(ps, dtype=float))
    return score, new[0]


LLM_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["shares"],
              "properties": {"shares": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "share_of_points"],
                                                                    "properties": {"id": {"type": "string"}, "share_of_points": {"type": "number"}}}}}}


def llm_pool(pool, district, edition):
    """gpt-5.4 without residents: predicts each project's share of the district's points."""
    event_agent.load_env()
    items = [{"id": p["id"], "project": proj_text(p)} for p in pool]
    f = CACHE / f"llm_{edition}_{hashlib.sha1((district + json.dumps(items)).encode()).hexdigest()[:8]}.json"
    if not f.exists():
        from openai import OpenAI
        r = OpenAI().responses.create(model="gpt-5.4", reasoning={"effort": "low"},
            instructions=("You predict the result of Kraków's civic budget vote before it happens. Voters in the district rank up to 3 projects (3/2/1 points). "
                          "Estimate each project's share of all points in this district pool (shares sum to 1). Do not use knowledge of the actual results. Answer only with the JSON."),
            input=json.dumps({"edition": edition, "district": district, "projects": items}, ensure_ascii=False),
            text={"format": {"type": "json_schema", "name": "bo", "schema": LLM_SCHEMA, "strict": True}})
        f.write_text(r.output_text)
    m = {x["id"]: x["share_of_points"] for x in json.loads(f.read_text())["shares"]}
    return np.array([m.get(p["id"], 0.0) for p in pool])


def main():
    jev_sim.load_env(); a = sys.argv[1:]; opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    edition = int(opt("--edition", 2025)); nd = int(opt("--districts", 18)); use_llm = "--no-llm" not in a
    from scipy.stats import spearmanr
    B = [p for p in json.load(open("data/raw/bo_krakow.json"))["projects"] if p["edition"] == edition and p["level"] == "district"]
    dists = sorted({p["district_norm"] for p in B})[:nd] if nd < 18 else sorted({p["district_norm"] for p in B})
    rows = []
    for d in dists:
        pool = sorted([p for p in B if p["district_norm"] == d], key=lambda p: p["id"])
        K, reps = residents(d)
        score, new = asyncio.run(jev_pool(K, reps, pool, d, edition))
        real = np.array([p["votes"] for p in pool], float); won = np.array([bool(p.get("won_by_budget_rule")) for p in pool])
        k = max(int(won.sum()), 1)
        def hits(pred): return int(won[np.argsort(-pred)[:k]].sum())
        row = {"district": d, "projects": len(pool), "winners": int(won.sum()), "archetypes": len(reps), "jev_calls_new": new,
               "rho_residents": spearmanr(score, real).correlation, "hits_residents": hits(score),
               "rho_cheaper": spearmanr(-np.array([p["cost_pln"] or 0 for p in pool]), real).correlation}
        if use_llm:
            llm = llm_pool(pool, d, edition); row["rho_llm"] = spearmanr(llm, real).correlation; row["hits_llm"] = hits(llm)
            row["rho_mix"] = spearmanr(pd.Series(score).rank() + pd.Series(llm).rank(), real).correlation
        rows.append(row); print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()})
    R = pd.DataFrame(rows); R.to_csv(CACHE / f"validate_{edition}.csv", index=False)
    print(f"\nEdycja {edition}, {len(R)} dzielnic, {R.projects.sum()} projektów, {R.winners.sum()} zwycięzców")
    for c, name in [("residents", "symulowani mieszkańcy (Jev)"), ("llm", "gpt-5.4 bez mieszkańców"), ("cheaper", "tańszy wygrywa"), ("mix", "mieszkańcy + gpt (średnia rang)")]:
        if f"rho_{c}" in R:
            h = f", trafieni zwycięzcy {R[f'hits_{c}'].sum()}/{R.winners.sum()}" if f"hits_{c}" in R else ""
            print(f"  {name:34s} Spearman mediana {R[f'rho_{c}'].median():.2f}, średnia {R[f'rho_{c}'].mean():.2f}{h}")
    print(f"  losowo: Spearman 0, trafieni zwycięzcy ok. {sum(w * w / p for w, p in zip(R.winners, R.projects)):.0f}/{R.winners.sum()}")


if __name__ == "__main__":
    main()
